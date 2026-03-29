#!/usr/bin/env bash
# start.sh — Start CodyClaw in the background

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.codyclaw.pid"
LOG_FILE="$SCRIPT_DIR/.codyclaw.log"

if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE")
  if kill -0 "$PID" 2>/dev/null; then
    echo "CodyClaw is already running (pid $PID)"
    exit 1
  else
    rm -f "$PID_FILE"
  fi
fi

nohup codyclaw >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "CodyClaw started (pid $(cat "$PID_FILE")), logs: $LOG_FILE"
