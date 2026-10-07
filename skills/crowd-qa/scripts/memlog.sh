#!/usr/bin/env bash
# usage: memlog.sh <run-dir>     (start detached with nohup)
# Appends load, free memory, swap and process counts to memlog.txt every 30s, synced to disk,
# so the last lines before a crash or reboot show whether memory was the cause.
RUN="$1"
while :; do
  if [ "$(uname)" = Darwin ]; then
    load=$(sysctl -n vm.loadavg | awk '{print $2}')
    free=$(memory_pressure 2>/dev/null | awk '/free percentage/{print $5}')
    swap=$(sysctl -n vm.swapusage | awk '{print $6}')
  else
    load=$(awk '{print $1}' /proc/loadavg)
    free=$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{print int(a*100/t)"%"}' /proc/meminfo)
    swap=$(awk '/SwapTotal/{t=$2} /SwapFree/{f=$2} END{print int((t-f)/1024)"M"}' /proc/meminfo)
  fi
  browsers=$(pgrep -f "Chrome for Testing|chrome-headless|Chromium" | wc -l | tr -d ' ')
  testers=$(pgrep -f "codex exec|claude -p|opencode run" | wc -l | tr -d ' ')
  emulators=$(pgrep -f "qemu-system|Simulator.app" | wc -l | tr -d ' ')
  echo "$(date +%H:%M:%S) load=$load free=$free swap=$swap browsers=$browsers testers=$testers emulators=$emulators" >> "$RUN/memlog.txt"
  sync
  sleep 30
done
