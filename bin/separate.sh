#!/bin/bash
# separate.sh — demucs 两轨分轨封装
# 用法: bin/separate.sh <work_dir>   (work_dir/src.mp3 必须存在)
set -e
WD="$1"
[ -f "$WD/src.mp3" ] || { echo "缺 $WD/src.mp3"; exit 1; }
source "$(dirname "$0")/../.venv/bin/activate"
demucs -n htdemucs --two-stems=vocals -o "$WD/stems" "$WD/src.mp3"
echo "分轨完成 → $WD/stems/htdemucs/src/{vocals,no_vocals}.wav"
