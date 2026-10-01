#!/usr/bin/env python3
"""v2: 节拍网格切歌 + 响度归一 + 尾部回声。选歌顺序沿用 v1 模型编排。"""
import json, subprocess, os, glob, statistics, sys

D = '/tmp/dj-compare/Auret'
NORM = f'{D}/data/norm'
os.makedirs(NORM, exist_ok=True)
XF = 12
order = json.load(open('/tmp/setlist-model.json'))['order']

# ── 1. 响度归一 (EBU R128 → -14 LUFS, 真峰值 -1.5dB) ──
ids = [t['title'] for t in order]
for tid in ids:
    src = f'{D}/data/fulls/{tid}.mp3'
    dst = f'{NORM}/{tid}.mp3'
    if not os.path.exists(dst):
        r = subprocess.run(['ffmpeg','-y','-loglevel','error','-i',src,
            '-af','loudnorm=I=-14:TP=-1.5:LRA=11','-q:a','4',dst], capture_output=True, text=True)
        if r.returncode: print('NORM ERR', tid[:30], r.stderr[-100:]); sys.exit(1)
print('normalized', len(ids))

# ── 2. 节拍网格 (librosa 全曲 beat → 4 拍小节) ──
BEATS = '/tmp/beats17.json'
if os.path.exists(BEATS):
    beats = json.load(open(BEATS))
else:
    import librosa
    beats = {}
    for tid in ids:
        y, sr = librosa.load(f'{NORM}/{tid}.mp3', sr=22050, mono=True)
        _, bf = librosa.beat.beat_track(y=y, sr=sr)
        bt = librosa.frames_to_time(bf, sr=sr).tolist()
        dur = float(librosa.get_duration(y=y, sr=sr))
        beats[tid] = {'beats': bt, 'duration': dur}
        print(f'  beats {len(bt):3d}  {tid[:36]}', file=sys.stderr)
    json.dump(beats, open(BEATS,'w'))

def bars_of(tid):
    b = beats[tid]
    return [t for t in b['beats'][::4]]  # 4/4 小节起点

def snap(tid, want, seg_min):
    """want 附近选小节起点, 保证后面装得下"""
    dur = beats[tid]['duration']
    cands = [x for x in bars_of(tid) if x + seg_min + XF + 2 <= dur]
    if not cands: return want
    return min(cands, key=lambda x: abs(x - want))

def seg_bars(tid, target, seg_min=72):
    """段长取整小节数, >=seg_min"""
    b = beats[tid]['beats']
    if len(b) < 8: return target
    bar_len = statistics.median([b[i+1]-b[i] for i in range(len(b)-1)]) * 4
    n = max(round(target / bar_len), -(-seg_min // max(bar_len,1)) if False else 1)
    while n * bar_len < seg_min: n += 1
    return n * bar_len

# ── 3. clip specs: intro 从 0, 其余小节对齐 ──
import math
cu = json.load(open(os.path.expanduser('~/Downloads/hermes_work/miaomi-dj/model/weeknd-model/cue-model.json')))
c = cu['coefficients']
feats = json.load(open('/tmp/fw17_feats.json'))

specs = []
for i, tr in enumerate(order):
    tid = tr['title']
    if i == 0:
        s = 0.0; L = seg_bars(tid, 90, 72)
    else:
        src = order[i-1]; stid = src['title']
        sdur = feats[stid]['duration']
        x = [(src['bpm']-cu['mean'][0])/cu['scale'][0], (sdur-cu['mean'][1])/cu['scale'][1],
             (src['energy']-cu['mean'][2])/cu['scale'][2], (tr['bpm']-cu['mean'][3])/cu['scale'][3],
             (feats[tid]['duration']-cu['mean'][4])/cu['scale'][4], (tr['energy']-cu['mean'][5])/cu['scale'][5]]
        ef = min(max(sum(c[j]*x[j] for j in range(6))+c[6], 0.30), 0.88)
        want = min(sdur * ef, max(0, sdur - 84 - XF - 8))
        L = seg_bars(tid, 84, 72)
        s = snap(tid, want, L)
    specs.append((s, L))

print('specs:', [(round(s), round(L)) for s, L in specs], file=sys.stderr)

# ── 4. 渲染: echo 只加尾部+limiter, 全部 qsin 等功率交叉 ──
def chain(i, seg, tech):
    pre = f'atrim=duration={seg+XF:.2f},asetpts=PTS-STARTPTS'
    if tech == 'loop_roll':
        cut = max(0.0, seg - 8)
        return (f'[0:a]{pre},asplit=2[m][t];'
                f'[m]atrim=duration={cut:.2f},asetpts=PTS-STARTPTS[mm];'
                f'[t]atrim=start={cut:.2f}:duration=2,asetpts=PTS-STARTPTS,'
                f'aloop=loop=3:size=88200,afade=t=out:st=0:d=8[lp];'
                f'[mm][lp]concat=n=2:v=0:a=1[o]')
    if tech == 'spinback':
        rs = max(0.0, seg + XF - 1.5)
        return (f'[0:a]{pre},asplit=2[n][r];'
                f'[r]atrim=start={rs:.2f},asetpts=PTS-STARTPTS,areverse,'
                f'atempo=1.8,afade=t=out:st=0:d=1.3[rv];'
                f'[n]atrim=duration={rs:.2f},asetpts=PTS-STARTPTS[nm];'
                f'[nm][rv]concat=n=2:v=0:a=1[o]')
    if tech == 'echo_out':
        # 干信号保持 1.0 (之前整段 0.8 → 忽小), 只加轻回声 + limiter 防削波
        return f'[0:a]{pre},aecho=1:1:250|500:0.16|0.09,alimiter=limit=0.84[o]'
    if tech == 'filter_build':
        cut = max(0.0, seg - 10)
        return (f'[0:a]{pre},asplit=2[fa][fb];'
                f'[fb]atrim=start={cut:.2f},asetpts=PTS-STARTPTS,'
                f'highpass=f=300,afade=t=in:st=0:d=9[hp];'
                f'[fa]atrim=duration={cut:.2f},asetpts=PTS-STARTPTS[fa2];'
                f'[fa2][hp]concat=n=2:v=0:a=1[o]')
    return f'[0:a]{pre}[o]'

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r

# 逐段量长
lens = []
for i, (tr, (s, L)) in enumerate(zip(order, specs)):
    wav = f'/tmp/v2seg_{i}.wav'
    r = run(['ffmpeg','-y','-loglevel','error','-ss',f'{s:.2f}','-t',f'{L+XF:.2f}','-i',f'{NORM}/{tr["title"]}.mp3',
             '-filter_complex', chain(0, L, tr.get('tech','xfade')),'-map','[o]',wav])
    if r.returncode: print('SEG ERR', i, r.stderr[-150:], file=sys.stderr); sys.exit(1)
    rr = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',wav],capture_output=True,text=True)
    lens.append(float(rr.stdout.strip())); os.unlink(wav)

starts = [0.0]
for i in range(1, len(lens)): starts.append(starts[-1] + lens[i-1] - XF)
print('lens:', [round(l,1) for l in lens], file=sys.stderr)
print('starts:', [round(x,1) for x in starts], file=sys.stderr)
print('total:', round(starts[-1]+lens[-1],1), file=sys.stderr)

# 全量渲染
inputs = []
for tr, (s, L) in zip(order, specs):
    inputs += ['-ss', f'{s:.2f}', '-t', f'{L+XF:.2f}', '-i', f'{NORM}/{tr["title"]}.mp3']
filt = []
for i, (tr, (s, L)) in enumerate(zip(order, specs)):
    fc = chain(i, L, tr.get('tech','xfade')).replace('[0:a]', f'[{i}:a]').replace('[o]', f'[c{i}]')
    filt.append(fc + ';')
prev = 'c0'
for i in range(1, len(order)):
    nx = f'cf{i}'
    filt.append(f'[{prev}][c{i}]acrossfade=d={XF}:c1=qsin:c2=qsin[{nx}];')
    prev = nx
fs = chr(10).join(filt).rstrip(';')
out = f'{D}/data/set-model-v2.mp3'
r = run(['ffmpeg','-y','-loglevel','error'] + inputs + ['-filter_complex', fs, '-map', f'[{prev}]', '-q:a','4', out])
if r.returncode: print('RENDER FAIL:', r.stderr[-800:], file=sys.stderr); sys.exit(1)
rr = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',out],capture_output=True,text=True)
print('MP3', out, rr.stdout.strip()+'s', file=sys.stderr)

json.dump({'lens': lens, 'starts': starts, 'specs': specs,
           'order': [{'title': t['title'], 'tech': t.get('tech','xfade')} for t in order]},
          open('/tmp/v2-meta.json','w'), ensure_ascii=False)
print('DONE')
