#!/usr/bin/env bash
# The running product for fix agents: a frontend dev server per worktree, and a queue for the shared backend.
#   fix-stack.sh <run> up <area> <dir> <port>    FE_PREP_CMD, FE_INSTALL_CMD, then FE_DEV_CMD in <dir> on <port>
#   fix-stack.sh <run> down <area>               stops that dev server and everything it started
#   fix-stack.sh <run> list
#   fix-stack.sh <run> be-take <job> <branch>    waits for the backend, deploys <branch> with BE_DEPLOY_CMD
#   fix-stack.sh <run> be-give <job>             BE_RESET_CMD (back to the base branch), frees the backend
# Frontend fixes run side by side, each on its own port, all against the one shared backend. A backend fix takes
# the backend for itself: one at a time, and the stack goes back to the base branch before the next one.
# Lessons from the 2026-10-13 fix run: the dev server is started in its own process group and stopped as a group
# (next dev left its next-server child running on the port), and FE_PREP_CMD runs on every start, so a tool
# config the install needs is never lost (a git reset on pull wiped it and installs failed).
set -u
RUN="$1"; CMD="$2"
. "$RUN/config.env"
D="$RUN/fix/stack"; mkdir -p "$D" "$RUN/logs"
case "$CMD" in
  up)
    A="$3"; DIR="$4"; PORT="$5"; LOG="$RUN/logs/fe-$A.log"
    [ -d "$DIR" ] || { echo "no checkout at $DIR" >&2; exit 1; }
    bash "$0" "$RUN" down "$A" >/dev/null 2>&1
    cd "$DIR" || exit 1
    if [ -n "${FE_PREP_CMD:-}" ]; then bash -c "$FE_PREP_CMD" > "$RUN/logs/fe-$A-prep.log" 2>&1 || { echo "FE_PREP_CMD failed: tail $RUN/logs/fe-$A-prep.log" >&2; exit 1; }; fi
    if [ -n "${FE_INSTALL_CMD:-}" ]; then bash -c "$FE_INSTALL_CMD" > "$RUN/logs/fe-$A-install.log" 2>&1 || { echo "FE_INSTALL_CMD failed: tail $RUN/logs/fe-$A-install.log" >&2; exit 1; }; fi
    [ -n "${FE_DEV_CMD:-}" ] || { echo "FE_DEV_CMD is not set in config.env" >&2; exit 1; }
    # its own process group, so down can stop the dev server and every child it started
    PORT="$PORT" nohup python3 -c 'import os,sys; os.setsid(); os.execvp("bash", ["bash", "-c", sys.argv[1]])' \
      "$FE_DEV_CMD" > "$LOG" 2>&1 < /dev/null &
    echo $! > "$D/$A.pid"; echo "$PORT" > "$D/$A.port"; echo "$DIR" > "$D/$A.dir"
    for _ in $(seq 1 "${FE_UP_WAIT_S:-180}"); do
      curl -sf -m3 -o /dev/null "http://localhost:$PORT/" && { echo "up $A on http://localhost:$PORT"; exit 0; }
      kill -0 "$(cat "$D/$A.pid")" 2>/dev/null || { echo "dev server exited: tail $LOG" >&2; exit 1; }
      sleep 1
    done
    echo "dev server did not answer on $PORT: tail $LOG" >&2; exit 1 ;;
  down)
    A="$3"; P=$(cat "$D/$A.pid" 2>/dev/null); PORT=$(cat "$D/$A.port" 2>/dev/null)
    if [ -n "$P" ]; then
      G=$(ps -o pgid= -p "$P" 2>/dev/null | tr -d ' ')
      [ -n "$G" ] && kill -- "-$G" 2>/dev/null
      pkill -P "$P" 2>/dev/null; kill "$P" 2>/dev/null
    fi
    if [ -n "$PORT" ]; then
      for pid in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null); do kill "$pid" 2>/dev/null; done
    fi
    rm -f "$D/$A.pid"; echo "down $A" ;;
  list)
    for f in "$D"/*.pid; do
      [ -f "$f" ] || continue; a=$(basename "$f" .pid)
      echo "$a port=$(cat "$D/$a.port") $(kill -0 "$(cat "$f")" 2>/dev/null && echo up || echo dead) $(cat "$D/$a.dir")"
    done
    [ -d "$D/be.lock" ] && echo "backend held by $(cat "$D/be.lock/job" 2>/dev/null)" ;;
  be-take)
    J="$3"; BR="$4"
    [ -n "${BE_DEPLOY_CMD:-}" ] || { echo "BE_DEPLOY_CMD is not set in config.env; backend fixes cannot be re-tested" >&2; exit 1; }
    until mkdir "$D/be.lock" 2>/dev/null; do
      [ "$(cat "$D/be.lock/job" 2>/dev/null)" = "$J" ] && break
      sleep 15
    done
    echo "$J" > "$D/be.lock/job"
    BRANCH="$BR" bash -c "$BE_DEPLOY_CMD" > "$RUN/logs/be-$J.log" 2>&1 || { echo "BE_DEPLOY_CMD failed: tail $RUN/logs/be-$J.log" >&2; exit 1; }
    echo "backend now runs $BR (held by $J)" ;;
  be-give)
    J="$3"
    [ "$(cat "$D/be.lock/job" 2>/dev/null)" = "$J" ] || { echo "backend is not held by $J" >&2; exit 0; }
    if [ -n "${BE_RESET_CMD:-}" ]; then bash -c "$BE_RESET_CMD" >> "$RUN/logs/be-$J.log" 2>&1 || echo "BE_RESET_CMD failed: tail $RUN/logs/be-$J.log" >&2; fi
    rm -rf "$D/be.lock"; echo "backend released by $J" ;;
  *) sed -n 2,7p "$0"; exit 2 ;;
esac
