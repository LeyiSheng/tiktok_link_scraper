#!/bin/zsh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
RUN_LOG="$LOG_DIR/runner_youtube.log"
APP_LOG="$LOG_DIR/app_youtube.log"
RESTART_DELAY="${RESTART_DELAY:-15}"
PYTHON_BIN="${PYTHON_BIN:-/Users/tailab/miniconda3/envs/tiktok/bin/python}"

YOUTUBE_MAX="${YOUTUBE_MAX:-50}"
YTDLP_OUTPUT_DIR="${YTDLP_OUTPUT_DIR:-downloads/shorts}"
YTDLP_FORMAT="${YTDLP_FORMAT:-b}"
YTDLP_ARCHIVE_FILE="${YTDLP_ARCHIVE_FILE:-logs/yt_dlp_downloaded.txt}"
YTDLP_COOKIES_FILE="${YTDLP_COOKIES_FILE:-$SCRIPT_DIR/cookies.txt}"

mkdir -p "$LOG_DIR"

cd "$SCRIPT_DIR" || exit 1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] youtube supervisor started" | tee -a "$RUN_LOG"

while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] launching: $PYTHON_BIN main.py --platform youtube --max $YOUTUBE_MAX --download-ytdlp --ytdlp-output-dir $YTDLP_OUTPUT_DIR --ytdlp-format $YTDLP_FORMAT --ytdlp-archive-file $YTDLP_ARCHIVE_FILE --ytdlp-cookies-file $YTDLP_COOKIES_FILE" | tee -a "$RUN_LOG"
  "$PYTHON_BIN" main.py \
    --platform youtube \
    --headless \
    --max "$YOUTUBE_MAX" \
    --download-ytdlp \
    --ytdlp-output-dir "$YTDLP_OUTPUT_DIR" \
    --ytdlp-format "$YTDLP_FORMAT" \
    --ytdlp-archive-file "$YTDLP_ARCHIVE_FILE" \
    --ytdlp-cookies-file "$YTDLP_COOKIES_FILE" >> "$APP_LOG" 2>&1
  EXIT_CODE=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] process exited with code $EXIT_CODE, restart in ${RESTART_DELAY}s" | tee -a "$RUN_LOG"
  sleep "$RESTART_DELAY"
done
