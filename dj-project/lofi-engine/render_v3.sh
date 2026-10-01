#!/bin/bash
# v3 渲染: 12 段 mp3 -c copy 切段 + acrossfade 8s 链 + libmp3lame
set -e
mkdir -p segs3
python3 - <<'PY'
import json, subprocess
tl = json.load(open('timeline-v3.json'))
for i, seg in enumerate(tl['setlist']):
    out = f"segs3/{i:02d}.wav"
    subprocess.run(['ffmpeg','-y','-loglevel','error','-i',f"src/{seg['f']}",
                    '-ss','0','-t',str(seg['dur']),'-ar','44100','-ac','2',out.replace('.mp3','.wav')], check=True)
print('分段完成', len(tl['setlist']))
PY
# acrossfade 链
N=12; XF=8
FILTER=""; LAST="0:a"
for i in $(seq 1 $((N-1))); do
  FILTER+="[$LAST][${i}:a]acrossfade=d=$XF:c1=tri:c2=tri[x${i}];"
  LAST="x${i}"
done
ffmpeg -y -loglevel error $(for i in $(seq 0 $((N-1))); do echo -n "-i segs3/$(printf %02d $i).wav "; done) \
  -filter_complex "${FILTER%;}" -map "[${LAST}]" -c:a libmp3lame -q:a 2 lofi-dj-set-v3.mp3
ffprobe -v error -show_entries format=duration -of csv=p=0 lofi-dj-set-v3.mp3
