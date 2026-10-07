#!/usr/bin/env bash
# watchdog.sh <run>: keeps the stack up and its data safe. Run it every few minutes (a launchd/systemd timer or
# cron; recipes in references/hooks.md) and api-watch.sh also runs it the moment the stack goes down.
#
# Hooks from config.env (each may be a local command or an `ssh host '...'` one):
#   HEALTH_CHECKS   the same checks api-watch.sh runs; all must pass for "up"
#   STACK_UP_CMD    brings the stack back up (idempotent)
#   DB_COUNT_CMD    prints one number that only grows during a run (users, rows): the wipe detector
#   DB_BACKUP_CMD   prints a full dump to stdout
#   DB_RESTORE_CMD  reads a dump on stdin and replaces the database
#
#   stack up      -> back up, but only when the count is at least the last good backup's, so a freshly wiped
#                    database never overwrites a good backup
#   stack down    -> STACK_UP_CMD, wait for the checks, then restore if the count dropped
#   count dropped -> restore the latest backup (a reboot or a container restart wiped non-durable storage)
RUN="$1"; . "$RUN/config.env"
BK="$RUN/state/db"; LOG="$RUN/logs/watchdog.log"; mkdir -p "$BK" "$RUN/logs"
exec 9>"$RUN/.watchdog.lock"
if command -v flock >/dev/null; then flock -n 9 || exit 0
else mkdir "$RUN/.watchdog.lockdir" 2>/dev/null || exit 0; trap 'rmdir "$RUN/.watchdog.lockdir"' EXIT; fi
say() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
healthy() {
  local IFS=';' item cmd
  for item in ${HEALTH_CHECKS:-}; do cmd="${item#*|}"; [ -n "$cmd" ] && { bash -c "$cmd" >/dev/null 2>&1 || return 1; }; done
  return 0
}
count() { [ -n "${DB_COUNT_CMD:-}" ] && bash -c "$DB_COUNT_CMD" 2>/dev/null | tr -dc '0-9'; }
last_good() { cat "$BK/last-count" 2>/dev/null || echo 0; }

if ! healthy; then
  [ -z "${STACK_UP_CMD:-}" ] && { say "stack down and no STACK_UP_CMD set"; exit 1; }
  say "stack down -> STACK_UP_CMD"
  bash -c "$STACK_UP_CMD" >> "$LOG" 2>&1
  for _ in $(seq 1 60); do healthy && break; sleep 5; done
  healthy || { say "stack still down after STACK_UP_CMD"; exit 1; }
  say "stack up again"
fi
[ -z "${DB_BACKUP_CMD:-}" ] && exit 0

n=$(count); g=$(last_good)
if [ -n "$n" ] && [ -s "$BK/db-latest.gz" ] && [ "$n" -lt "$g" ] && [ -n "${DB_RESTORE_CMD:-}" ]; then
  say "data wiped? count $n < backup's $g -> restoring db-latest.gz"
  gunzip -c "$BK/db-latest.gz" | bash -c "$DB_RESTORE_CMD" >> "$LOG" 2>&1
  [ -n "${STACK_UP_CMD:-}" ] && bash -c "$STACK_UP_CMD" >> "$LOG" 2>&1   # restart so caches match the data
  say "restored: count now $(count)"
  exit 0
fi
if [ -z "$n" ] || [ "$n" -ge "$g" ]; then
  if bash -c "$DB_BACKUP_CMD" 2>>"$LOG" | gzip > "$BK/db.tmp.gz" && [ -s "$BK/db.tmp.gz" ]; then
    mv "$BK/db.tmp.gz" "$BK/db-latest.gz"; [ -n "$n" ] && echo "$n" > "$BK/last-count"
    cp "$BK/db-latest.gz" "$BK/db-$(date +%H%M).gz"
    ls -t "$BK"/db-[0-9]*.gz 2>/dev/null | tail -n +13 | xargs rm -f 2>/dev/null
    echo "ok count=${n:-?} backup=$(du -h "$BK/db-latest.gz" | cut -f1)"
  else say "backup failed"; fi
fi
