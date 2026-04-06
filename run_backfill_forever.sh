#!/bin/zsh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
RUN_LOG="$LOG_DIR/backfill_runner.log"
APP_LOG="$LOG_DIR/backfill_work_info.log"
STATE_FILE="$LOG_DIR/backfill_work_info_state.json"
RESTART_DELAY="${RESTART_DELAY:-5}"
PYTHON_BIN="${PYTHON_BIN:-/Users/tailab/miniconda3/envs/tiktok/bin/python}"

mkdir -p "$LOG_DIR"
cd "$SCRIPT_DIR" || exit 1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] backfill supervisor started" | tee -a "$RUN_LOG"

while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] launching backfill" | tee -a "$RUN_LOG"
  "$PYTHON_BIN" -u backfill_work_info.py \
    --save-every 20 \
    --sleep 0.2 \
    --state-file "$STATE_FILE" >> "$APP_LOG" 2>&1
  EXIT_CODE=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] exited with code $EXIT_CODE" | tee -a "$RUN_LOG"

  # 如果已经完成，退出守护循环
  if [ -f "$STATE_FILE" ]; then
    if grep -q '"done":[[:space:]]*true' "$STATE_FILE"; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] state marked done=true, supervisor exit" | tee -a "$RUN_LOG"
      break
    fi
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] restart in ${RESTART_DELAY}s" | tee -a "$RUN_LOG"
  sleep "$RESTART_DELAY"
done

