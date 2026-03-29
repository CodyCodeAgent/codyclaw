#!/usr/bin/env bash
# stop.sh — Stop CodyClaw

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.codyclaw.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "No PID file found — CodyClaw may not be running"
  exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "CodyClaw stopped (pid $PID)"
else
  echo "Process $PID is not running"
fi

rm -f "$PID_FILE"
