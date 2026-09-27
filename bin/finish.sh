#!/bin/bash
# finish.sh — lofi 后期: 分轨独立处理再回混
# 用法: ./bin/finish.sh <work_dir>
# 处理链:
#   vocals:   降 2 半音感(asetro 0.94x) + 高频柔化(lowpass 8k) + 轻微磁带抖动
#   no_vocals: 降速同倍 + lowpass 7k(暖) + 侧链压缩人声让位
#   合成:     + vinyl 黑胶噪声(粉噪+爆点) + 低频提升 + 整体响度 -16 LUFS(lofi 审美比 -14 更轻)
set -e
WD="$1"
S="$WD/stems/htdemucs/src"

# vinyl 噪声床: 粉噪 44dB + 偶发爆点
# 简化噪声床: 粉噪 + crackle 模拟(anullsrc 上叠随机脉冲太复杂, 用高频粉噪+门限)
ffmpeg -y -loglevel error -f lavfi \
  -i "anoisesrc=color=pink:amplitude=0.045:duration=211.9:seed=42" \
  -af "highpass=f=200,lowpass=f=7000,afade=t=in:d=2,afade=t=out:st=209:d=3" \
  "$WD/vinyl.wav"

# 主链: 两轨同参数降速 (atempo 会变调, 用 asetrate+aresample 保持时长近似→直接变调降速)
# lofi 经典: 33rpm 感 → 速度 0.92x, 音调随降 (asetrate 44100*0.92)
for stem in vocals no_vocals; do
  ffmpeg -y -loglevel error -i "$S/$stem.wav" -af "\
asetrate=44100*0.92,aresample=44100,\
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
afade=t=out:st=225:d=6" \
  -t 231 "$WD/lofi_mix.wav"

cp "$WD/lofi_mix.wav" out/less_than_zero_lofi.wav
echo "DONE → out/less_than_zero_lofi.wav"
