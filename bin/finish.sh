#!/bin/bash
# finish.sh — lofi 后期: 分轨独立处理再回混 (preset 化)
# 用法: bin/finish.sh <work_dir> [preset]
#   preset 省略 = v1 经典档; presets/lofi-v2-sleep.txt = v2 睡眠档
# 时长自动探测, 输出名 = <work_dir 目录名>_lofi.wav
set -e
WD="$1"
PRESET="$2"
[ -d "$WD/stems" ] || { echo "缺 $WD/stems (先跑 bin/separate.sh)"; exit 1; }
VOC=$(find "$WD/stems" -name vocals.wav | head -1)
[ -n "$VOC" ] || { echo "找不到 vocals.wav 于 $WD/stems"; exit 1; }
S=$(dirname "$VOC")

# ---- 默认参数 (v1 经典档) ----
SPEED=0.92; VOC_LP=8000; ACC_LP=7000; ACC_LOW_GAIN=3; ACC_MID_CUT=-2
SUB_HIGHPASS=0; TREMOLO_F=0.8; TREMOLO_D=0.12; VINYL_AMP=0.045; VINYL_MIX=0.55; LUFS=-16
BUS_LP=9500; BUS_HPF=55

# ---- preset 覆盖 (只收 KEY=数字 行, 兼容 CRLF) ----
if [ -n "$PRESET" ]; then
  PF="$(dirname "$0")/../presets/$(basename "$PRESET")"
  [ -f "$PF" ] || PF="$PRESET"
  [ -f "$PF" ] || { echo "缺 preset: $PRESET"; exit 1; }
  eval "$(sed -nE 's/^([A-Z_]+)=([-0-9.]+).*/\1=\2/p' "$PF" | tr -d '\r')"
  echo "preset: $(basename "$PF")"
fi

# 成品时长 = stem 时长 / 降速倍率
SRC_DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$S/vocals.wav")
OUT_DUR=$(awk "BEGIN{printf \"%.2f\", $SRC_DUR/$SPEED}")
FADE_OUT_ST=$(awk "BEGIN{printf \"%.2f\", $OUT_DUR-6}")
VINYL_FADE_ST=$(awk "BEGIN{printf \"%.2f\", $OUT_DUR-3}")

# vinyl 噪声床: 粉噪 + 带通
ffmpeg -y -loglevel error -f lavfi \
  -i "anoisesrc=color=pink:amplitude=$VINYL_AMP:duration=$OUT_DUR:seed=42" \
  -af "highpass=f=200,lowpass=f=7000,afade=t=in:d=2,afade=t=out:st=$VINYL_FADE_ST:d=3" \
  "$WD/vinyl.wav"

# 分轨处理链
for stem in vocals no_vocals; do
  if [ $stem = vocals ]; then
    CHAIN="lowpass=f=$VOC_LP,acompressor=threshold=-18dB:ratio=2.5:attack=8:release=120"
  else
    CHAIN="lowpass=f=$ACC_LP"
    [ "$ACC_LOW_GAIN" != "0" ] && CHAIN="$CHAIN,equalizer=f=120:t=q:w=1:g=$ACC_LOW_GAIN"
    CHAIN="$CHAIN,equalizer=f=3000:t=q:w=1.4:g=$ACC_MID_CUT"
    [ "$SUB_HIGHPASS" != "0" ] && CHAIN="$CHAIN,highpass=f=$SUB_HIGHPASS"
  fi
  ffmpeg -y -loglevel error -i "$S/$stem.wav" -af "\
asetrate=44100*$SPEED,aresample=44100,\
$CHAIN,\
tremolo=f=$TREMOLO_F:d=$TREMOLO_D,\
afade=t=in:d=1.5" \
    "$WD/${stem}_lofi.wav"
done

# 回混 + 响度
ffmpeg -y -loglevel error \
  -i "$WD/vocals_lofi.wav" -i "$WD/no_vocals_lofi.wav" -i "$WD/vinyl.wav" \
  -filter_complex "\
[0:a]volume=1.0[v];\
[1:a]volume=0.92[m];\
[2:a]volume=$VINYL_MIX[n];\
[v][m][n]amix=inputs=3:duration=first:normalize=0,\
lowpass=f=$BUS_LP,highpass=f=$BUS_HPF,\
equalizer=f=200:t=q:w=0.8:g=1.5,\
alimiter=limit=0.9,loudnorm=I=$LUFS:TP=-1.5:LRA=11,\
afade=t=out:st=$FADE_OUT_ST:d=6" \
  -t "$OUT_DUR" "$WD/lofi_mix.wav"

OUT_NAME="$(basename "$WD")"
mkdir -p out
cp "$WD/lofi_mix.wav" "out/${OUT_NAME}_lofi.wav"
echo "DONE → out/${OUT_NAME}_lofi.wav (src ${SRC_DUR}s → out ${OUT_DUR}s, LUFS=$LUFS)"
