#!/bin/zsh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
RUN_LOG="$LOG_DIR/runner.log"
APP_LOG="$LOG_DIR/app.log"
RESTART_DELAY="${RESTART_DELAY:-10}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$LOG_DIR"

cd "$SCRIPT_DIR" || exit 1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] supervisor started" | tee -a "$RUN_LOG"

while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] launching: $PYTHON_BIN main.py --platform douyin --download-douyin --headless" | tee -a "$RUN_LOG"
  "$PYTHON_BIN" main.py --platform douyin --download-douyin --headless >> "$APP_LOG" 2>&1
  EXIT_CODE=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] process exited with code $EXIT_CODE, restart in ${RESTART_DELAY}s" | tee -a "$RUN_LOG"
  sleep "$RESTART_DELAY"
done
