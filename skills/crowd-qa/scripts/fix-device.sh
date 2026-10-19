#!/usr/bin/env bash
# The fix stage's devices, for a mobile product (an area of kind mobile, or FIX_DRIVER=device).
#   fix-device.sh <run> claim <job>              prints `export DEVICE=...` lines: a free device, or the job's own
#   fix-device.sh <run> build <job> <checkout>   builds the checkout and installs it on the job's device
#   fix-device.sh <run> release <job>            puts the base build back if this job installed one, frees the device
# config.env:
#   FIX_DEVICES="<udid> emulator-5570"   devices the fix stage may use (default DEVICE_POOL; wait for testers to end)
#   FIX_DEVICE_PLATFORM="ios"            ios | android | any: which kind claim hands out
#   DEVICE_HOSTS="<id>=<ssh host> ..."   where each device lives (empty = this machine)
#   FIX_BUILD_HOST="build-mac"           where builds run (empty = this machine); the checkout is copied there
#   FIX_BUILD_DIR="/tmp/crowd-fix"       on FIX_BUILD_HOST, one folder per area, kept for incremental builds
#   FIX_BUILD_CMD_ios='...'              run in the copied checkout, e.g. flutter build ios --simulator ...
#   FIX_BUILD_CMD_android='...'
#   FIX_APP_ios="build/ios/iphonesimulator/Runner.app"   built app, relative to the checkout
#   FIX_APP_android="build/app/outputs/flutter-apk/app-dev-debug.apk"
#   APP_PATH / ANDROID_APP_PATH          the base build on the device's host, reinstalled at release
set -uo pipefail
RUN="$1"; CMD="$2"; JOB="$3"
. "$RUN/config.env"
D="$RUN/fix/devices"; mkdir -p "$D" "$RUN/logs"
LOG="$RUN/logs/fix-device-$JOB.log"

host_of() { for kv in ${DEVICE_HOSTS:-}; do [ "${kv%%=*}" = "$1" ] && { echo "${kv#*=}"; return; }; done; }
platform_of() { case "$1" in emulator-*|*:*) echo android;; *) echo ios;; esac; }
on() {  # on <host> <command>: runs locally when host is empty
  local h="$1"; shift
  if [ -z "$h" ]; then bash -c "$*"; else ssh -o ServerAliveInterval=15 "$h" "export PATH=/opt/homebrew/bin:/usr/local/bin:\$HOME/.local/bin:\$HOME/Library/Android/sdk/platform-tools:\$HOME/Android/Sdk/platform-tools:\$PATH; $*"; fi
}
install_on() {  # install_on <device> <path on the device's host>
  local dev="$1" p="$2" h; h=$(host_of "$1")
  if [ "$(platform_of "$dev")" = ios ]; then
    on "$h" "xcrun simctl terminate $dev ${APP_ID:-} >/dev/null 2>&1; xcrun simctl install $dev $(printf %q "$p")"
  else
    on "$h" "adb -s $dev install -r -g $(printf %q "$p")"
  fi
}
mine() { grep -lx "$JOB" "$D"/* 2>/dev/null | grep -v '\.built$' | head -1 | xargs -n1 basename 2>/dev/null; }
exports() { echo "export DEVICE=$1 CROWD_DEVICE_HOST=$(host_of "$1") AGENT_DEVICE_SESSION=fix-$JOB"; }

case "$CMD" in
  claim)
    st=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],{}).get('status',''))" "$RUN/state/jobs.json" "$JOB" 2>/dev/null)
    case "$st" in done|failed) echo "$JOB is $st: it may not take a device" >&2; exit 1;; esac
    dev=$(mine); [ -n "$dev" ] && { exports "$dev"; exit 0; }
    want="${FIX_DEVICE_PLATFORM:-any}"
    for i in $(seq 1 $(( ${FIX_DEVICE_WAIT_S:-3600} / 20 ))); do
      for u in ${FIX_DEVICES:-$DEVICE_POOL}; do
        [ "$want" = any ] || [ "$(platform_of "$u")" = "$want" ] || continue
        if ( set -o noclobber; echo "$JOB" > "$D/$u" ) 2>/dev/null; then
          if [ -f "$D/$u.built" ]; then      # an earlier job's build is still on it: base build first
            base=$( [ "$(platform_of "$u")" = ios ] && echo "${APP_PATH:-}" || echo "${ANDROID_APP_PATH:-}" )
            [ -n "$base" ] && install_on "$u" "$base" >> "$LOG" 2>&1 && rm -f "$D/$u.built"
          fi
          exports "$u"; exit 0
        fi
      done
      sleep 20
    done
    echo "no free device after ${FIX_DEVICE_WAIT_S:-3600}s (held: $(cat "$D"/* 2>/dev/null | tr '\n' ' '))" >&2; exit 1 ;;
  build)
    CO="$4"; dev=$(mine); [ -n "$dev" ] || { echo "claim a device first" >&2; exit 1; }
    pf=$(platform_of "$dev"); bcmd_var="FIX_BUILD_CMD_$pf"; app_var="FIX_APP_$pf"
    [ -n "${!bcmd_var:-}" ] && [ -n "${!app_var:-}" ] || { echo "$bcmd_var or $app_var is not set in config.env" >&2; exit 1; }
    area=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],{}).get('area',''))" "$RUN/state/jobs.json" "$JOB" 2>/dev/null)
    BH="${FIX_BUILD_HOST:-}"; BD="${FIX_BUILD_DIR:-/tmp/crowd-fix}/${area:-$JOB}"
    echo "$(date +%H:%M:%S) build $CO for $dev ($pf) on ${BH:-local}:$BD" >> "$LOG"
    on "$BH" "mkdir -p $BD" >> "$LOG" 2>&1
    rsync -a --delete --exclude .git --exclude build --exclude .dart_tool --exclude ios/Pods --exclude node_modules \
      "$CO/" "${BH:+$BH:}$BD/" >> "$LOG" 2>&1 || { echo "copying the checkout failed: tail $LOG" >&2; exit 1; }
    on "$BH" "cd $BD && ${!bcmd_var}" >> "$LOG" 2>&1 || { echo "build failed: tail -50 $LOG" >&2; exit 1; }
    built="$BD/${!app_var}"; DH=$(host_of "$dev")
    if [ "$DH" != "$BH" ]; then          # the device lives elsewhere: carry the build over
      dst="/tmp/crowd-fix-$JOB.$( [ "$pf" = ios ] && echo app || echo apk )"
      on "$DH" "rm -rf $dst" >> "$LOG" 2>&1
      if [ -n "$BH" ] && [ -n "$DH" ]; then scp -3 -r -q "$BH:$built" "$DH:$dst"
      elif [ -n "$BH" ]; then scp -r -q "$BH:$built" "$dst"
      else scp -r -q "$built" "$DH:$dst"; fi >> "$LOG" 2>&1 || { echo "copying the build failed: tail $LOG" >&2; exit 1; }
      built="$dst"
    fi
    install_on "$dev" "$built" >> "$LOG" 2>&1 || { echo "install on $dev failed: tail $LOG" >&2; exit 1; }
    echo "$JOB" > "$D/$dev.built"
    echo "installed on $dev; open the app again (agent-device open ${APP_ID:-<app id>} --relaunch)" ;;
  release)
    dev=$(mine); [ -n "$dev" ] || exit 0
    # close the job's agent-device session: a session left open keeps holding the device after the lock is gone
    on "$(host_of "$dev")" "agent-device close --session fix-$JOB" >> "$LOG" 2>&1 || true
    if [ -f "$D/$dev.built" ]; then
      base=$( [ "$(platform_of "$dev")" = ios ] && echo "${APP_PATH:-}" || echo "${ANDROID_APP_PATH:-}" )
      if [ -n "$base" ] && install_on "$dev" "$base" >> "$LOG" 2>&1; then rm -f "$D/$dev.built"
      else echo "could not put the base build back on $dev; it stays marked so the next claim knows" >&2; fi
    fi
    rm -f "$D/$dev" ;;
  *) echo "usage: fix-device.sh <run> claim|build|release <job> [checkout]" >&2; exit 2 ;;
esac
