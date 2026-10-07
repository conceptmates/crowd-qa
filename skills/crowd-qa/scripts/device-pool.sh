#!/usr/bin/env bash
# usage: device-pool.sh <run-dir> acquire <lane> <round> <holder-pid>   -> prints the UDID
#        device-pool.sh <run-dir> release <lane>
#        device-pool.sh <run-dir> list
# One simulator per live character. Prefers the device the character used last round (its app state
# and sign-in survive), then the card's preferred model, then any free device in DEVICE_POOL.
# Round 1, or a device change, gets a clean install: uninstall, keychain reset, install APP_PATH.
set -u
RUN="$1"; MODE="$2"; LANE="${3:-}"; ROUND="${4:-1}"; HOLDER="${5:-$PPID}"
. "$RUN/config.env"
D="$RUN/.devices"; mkdir -p "$D"
LD="$RUN/lanes/$LANE"

free() {  # free <udid>: no lock, or the lock's holder is dead
  local f="$D/$1"; [ -f "$f" ] || return 0
  kill -0 "$(awk '{print $2}' "$f")" 2>/dev/null && return 1
  rm -f "$f"; return 0
}
card_field() { sed -n "s/^- \*\*$1\*\*: *//p" "$LD/card.md" 2>/dev/null | head -1; }

case "$MODE" in
list)
  for u in $DEVICE_POOL; do printf '%s %s\n' "$u" "$(cat "$D/$u" 2>/dev/null || echo free)"; done; exit 0 ;;
release)
  for f in "$D"/*; do [ -f "$f" ] && [ "$(awk '{print $1}' "$f")" = "$LANE" ] && {
    u=$(basename "$f"); xcrun simctl ui "$u" content_size medium >/dev/null 2>&1; rm -f "$f"; }; done
  exit 0 ;;
acquire) ;;
*) echo "unknown mode $MODE" >&2; exit 2 ;;
esac

pinned=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('device',''))" "$LD/state.json" 2>/dev/null)
model=$(card_field Device)
while :; do
  pick=""
  [ -n "$pinned" ] && free "$pinned" && pick="$pinned"
  if [ -z "$pick" ] && [ -n "$model" ]; then
    for u in $DEVICE_POOL; do
      n=$(xcrun simctl list devices -j | python3 -c "import json,sys;d=json.load(sys.stdin)['devices'];print(next((x['name'] for v in d.values() for x in v if x['udid']=='$u'),''))")
      [ "$n" = "$model" ] && free "$u" && { pick="$u"; break; }
    done
  fi
  if [ -z "$pick" ]; then for u in $DEVICE_POOL; do free "$u" && { pick="$u"; break; }; done; fi
  if [ -n "$pick" ]; then
    # mkdir is atomic: two lanes racing for one device cannot both win
    if mkdir "$D/.lock-$pick" 2>/dev/null; then
      if free "$pick"; then echo "$LANE $HOLDER $(date +%s)" > "$D/$pick"; rmdir "$D/.lock-$pick"; break; fi
      rmdir "$D/.lock-$pick"
    fi
  fi
  sleep 20
done

xcrun simctl boot "$pick" >/dev/null 2>&1
xcrun simctl bootstatus "$pick" -b >/dev/null 2>&1
# Never quit Simulator.app here: on this Xcode quitting the window shuts every booted device down,
# which kills a character mid-test (seen 2026-10-07). `simctl boot` itself does not open the window.
xcrun simctl ui "$pick" appearance light >/dev/null 2>&1
size=$(card_field "Text size"); xcrun simctl ui "$pick" content_size "${size:-medium}" >/dev/null 2>&1
if [ "$ROUND" = 1 ] || [ "$pinned" != "$pick" ]; then
  xcrun simctl terminate "$pick" "$APP_ID" >/dev/null 2>&1
  xcrun simctl uninstall "$pick" "$APP_ID" >/dev/null 2>&1
  xcrun simctl keychain "$pick" reset >/dev/null 2>&1
  xcrun simctl install "$pick" "$APP_PATH" || { rm -f "$D/$pick"; echo "install failed on $pick" >&2; exit 1; }
fi
echo "$pick"
