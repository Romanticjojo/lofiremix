#!/bin/bash
# finish.sh — lofi 后期: 分轨独立处理再回混
# 用法: ./bin/finish.sh <work_dir>
# 时长自动探测 (ffprobe 读 vocals stem), 输出名 = <work_dir 目录名>_lofi.wav
# 处理链:
#   vocals:   降速变调(asetrate 0.92x) + 高频柔化(lowpass 8k) + 轻微磁带抖动
#   no_vocals: 降速同倍 + lowpass 7k(暖) + 120Hz 暖化 + 3kHz 收
#   合成:     + vinyl 黑胶噪声床 + 低频提升 + 整体响度 -16 LUFS(lofi 审美比 -14 更轻)
set -e
WD="$1"
S="$WD/stems/htdemucs/src"
SPEED=0.92

[ -f "$S/vocals.wav" ] || { echo "缺 $S/vocals.wav (先跑 bin/separate.sh)"; exit 1; }

# 成品时长 = stem 时长 / 降速倍率 (asetrate 降速会拉长音频)
SRC_DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$S/vocals.wav")
OUT_DUR=$(awk "BEGIN{printf \"%.2f\", $SRC_DUR/$SPEED}")
FADE_OUT_ST=$(awk "BEGIN{printf \"%.2f\", $OUT_DUR-6}")
VINYL_FADE_ST=$(awk "BEGIN{printf \"%.2f\", $OUT_DUR-3}")

# vinyl 噪声床: 粉噪 + 带通, 时长随歌
ffmpeg -y -loglevel error -f lavfi \
  -i "anoisesrc=color=pink:amplitude=0.045:duration=$OUT_DUR:seed=42" \
  -af "highpass=f=200,lowpass=f=7000,afade=t=in:d=2,afade=t=out:st=$VINYL_FADE_ST:d=3" \
  "$WD/vinyl.wav"

# 主链: 两轨同参数降速 (asetrate+aresample → 变调降速, 33rpm 感)
for stem in vocals no_vocals; do
  ffmpeg -y -loglevel error -i "$S/$stem.wav" -af "\
asetrate=44100*$SPEED,aresample=44100,\
$([ $stem = vocals ] && echo 'lowpass=f=8000,acompressor=threshold=-18dB:ratio=2.5:attack=8:release=120' || echo 'lowpass=f=7000,equalizer=f=120:t=q:w=1:g=3,equalizer=f=3000:t=q:w=1.4:g=-2'),\
tremolo=f=0.8:d=0.12,\
afade=t=in:d=1.5" \
    "$WD/${stem}_lofi.wav"
done

# 回混 + vinyl + 响度
ffmpeg -y -loglevel error \
  -i "$WD/vocals_lofi.wav" -i "$WD/no_vocals_lofi.wav" -i "$WD/vinyl.wav" \
  -filter_complex "\
[0:a]volume=1.0[v];\
[1:a]volume=0.92[m];\
[2:a]volume=0.55[n];\
[v][m][n]amix=inputs=3:duration=first:normalize=0,\
lowpass=f=9500,highpass=f=55,\
equalizer=f=200:t=q:w=0.8:g=1.5,\
alimiter=limit=0.9,loudnorm=I=-16:TP=-1.5:LRA=11,\
afade=t=out:st=$FADE_OUT_ST:d=6" \
  -t "$OUT_DUR" "$WD/lofi_mix.wav"

mkdir -p out
OUT_NAME="$(basename "$WD")_lofi"
cp "$WD/lofi_mix.wav" "out/${OUT_NAME}.wav"
echo "DONE → out/${OUT_NAME}.wav (src ${SRC_DUR}s → out ${OUT_DUR}s)"
