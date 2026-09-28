#!/bin/bash
# batch_v2.sh — v2 睡眠档批量: 复用 work/<id>/stems (不重分轨), 输出 out/<id>_lofi_v2.mp3
set -e
cd "$(dirname "$0")/.."
for d in work/*/; do
  id=$(basename "$d")
  [ -d "$d/stems" ] || continue
  if [ -f "out/${id}_lofi_v2.mp3" ]; then echo "SKIP $id"; continue; fi
  echo "===== $id (v2) ====="
  bash bin/finish.sh "work/${id}" lofi-v2-sleep.txt
  ffmpeg -y -loglevel error -i "out/${id}_lofi.wav" -c:a libmp3lame -q:a 3 "out/${id}_lofi_v2.mp3"
  rm -f "out/${id}_lofi.wav"
done
echo "V2 BATCH DONE: $(ls out/*_lofi_v2.mp3 | wc -l) 首"
