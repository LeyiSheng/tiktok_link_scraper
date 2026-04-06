#!/bin/zsh
set -u

echo "[$(date '+%Y-%m-%d %H:%M:%S')] rsync_then_restart started"
rsync -az /Users/tailab/Desktop/DouYin_Spider/datas leyi@10.120.17.96:/data_sde/leyi/douyin_data/
rc=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] rsync exit code: $rc"

if [ "$rc" -eq 0 ]; then
  cd /Users/tailab/Desktop/tiktok_link_scraper || exit 1
  nohup env PYTHON_BIN=/Users/tailab/miniconda3/envs/tiktok/bin/python ./run_forever.sh > run_forever.log 2>&1 &
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] started run_forever pid=$!"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] rsync failed, skip restart"
fi
