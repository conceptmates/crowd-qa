#!/usr/bin/env bash
# Hooks for the Tinybox demo app (examples/demo-app), used by the dry run and as a worked example.
#   hooks.sh up                 start the app if /health does not answer (pid in data/server.pid)
#   hooks.sh connect <email> <name>   fake third-party connect (prints the channel number and secret)
#   hooks.sh mail <email>       that person's inbox
#   hooks.sh outbox             what the product sent to customers, newest last
#   hooks.sh count | backup | restore   database hooks for watchdog.sh
APP="$(cd "$(dirname "$0")/../demo-app" && pwd)"; DB="$APP/data/tinybox.db"; PORT="${PORT:-4100}"
case "$1" in
  up)
    curl -sf -m3 "http://localhost:$PORT/health" >/dev/null && exit 0
    mkdir -p "$APP/data"
    (cd "$APP" && PORT=$PORT nohup node server.js >> "$APP/data/server.log" 2>&1 & echo $! > "$APP/data/server.pid")
    for _ in $(seq 1 20); do curl -sf -m2 "http://localhost:$PORT/health" >/dev/null && exit 0; sleep 0.5; done; exit 1 ;;
  connect) shift; cd "$APP" && node scripts/connect-fake.js "$@" ;;
  mail) grep -F "\"$2\"" "$APP/data/outbox-mail.jsonl" 2>/dev/null || echo "inbox empty" ;;
  outbox) tail -n 30 "$APP/data/outbox-messages.jsonl" 2>/dev/null || echo "nothing sent yet" ;;
  count) sqlite3 "$DB" 'select count(*) from users' ;;
  backup) sqlite3 "$DB" .dump ;;
  restore)
    f=$(mktemp); cat > "$f"
    [ -f "$APP/data/server.pid" ] && kill "$(cat "$APP/data/server.pid")" 2>/dev/null; sleep 1
    rm -f "$DB" "$DB-wal" "$DB-shm"; sqlite3 "$DB" < "$f"; rm -f "$f"; "$0" up ;;
  *) echo "usage: hooks.sh up|connect|mail|outbox|count|backup|restore" >&2; exit 2 ;;
esac
