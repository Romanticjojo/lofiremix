#!/bin/bash
# batch.sh — 批量: work/src/<id>.flac → out/<id>_lofi.mp3
# 每首: demucs GPU 分轨 → finish.sh lofi 后期 → mp3 压制
# 可断点续跑 (已存在的 out/*.mp3 跳过)
set -e
cd "$(dirname "$0")/.."
DEMUCS=".venv/Scripts/demucs.exe"
[ -x "$DEMUCS" ] || DEMUCS="demucs"

for f in work/src/*.flac; do
  id=$(basename "$f" .flac)
  if [ -f "out/${id}_lofi.mp3" ]; then echo "SKIP $id (已有)"; continue; fi
  echo "===== $id ====="
  rm -rf "work/${id}"
  mkdir -p "work/${id}"
  # 分轨 (GPU, 输出 work/<id>/stems/htdemucs/<id>/{vocals,no_vocals}.wav)
  "$DEMUCS" -n htdemucs --two-stems=vocals -o "work/${id}/stems" "$f" 2>&1 | grep -v '^\s*[0-9]*%' | tail -1
  # lofi 后期 (自动定位 vocals.wav)
  bash bin/finish.sh "work/${id}"
  ffmpeg -y -loglevel error -i "out/${id}_lofi.wav" -c:a libmp3lame -q:a 3 "out/${id}_lofi.mp3"
  rm -f "out/${id}_lofi.wav"
done
echo "BATCH DONE: $(ls out/*_lofi.mp3 | wc -l) 首 in out/"
