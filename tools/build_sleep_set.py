#!/usr/bin/env python3
# Lofi Sleep DJ: 编排 25 首 v2 → 长串烧(每段 95-160s 按时长自适应, 12s 长交叉淡入淡出)
import json, subprocess, os

feats = json.load(open('/tmp/dj-lofi/feats.json'))
F = {t['title']: t for t in feats}

# ---- 编排: 能量从稍高缓缓沉下去(入睡前略有力→越来最轻), 同调性优先相邻 ----
# rms 排序: 开场 rms 中高(还醒着), 尾声 rms 最低(深睡)
order_by_rms = sorted(feats, key=lambda t: -t['rms'])

# 开场: rms 中上的 12 首(醒着听), 后半: rms 低的 13 首(渐睡)
# 前半内部按能量缓降+避免同 BPM 连续, 后半同理
def seq(arr):
    out = []
    pool = arr[:]
    # 起点取 pool 中 rms 最高的
    while pool:
        pool.sort(key=lambda t: -t['rms'])
        nxt = pool[0]
        if out:
            last = out[-1]
            # 优先选调性相近(同 key 或 bpm 差<8)且能量略低的
            cands = [t for t in pool if t['key'] == last['key'] or abs(t['bpm']-last['bpm']) < 8]
            if cands:
                cands.sort(key=lambda t: -t['rms'])
                nxt = cands[0]
        out.append(nxt)
        pool.remove(nxt)
    return out

first = order_by_rms[:12]
second = order_by_rms[12:]
setlist = seq(first) + list(reversed(sorted(second, key=lambda t: -t['rms'])))

# 每段时长: 开场长(150s, 完整听), 越往后越短(110s), 最长曲不超原长
XF = 12.0  # 交叉长度
segs = []
for i, t in enumerate(setlist):
    frac = i / (len(setlist)-1)
    dur = 155 - 45*frac          # 155s → 110s
    dur = min(dur, t['dur'] - 20)
    phase = 'intro' if i < 2 else ('warmup' if i < 9 else ('build' if i < 17 else ('peak' if i < 22 else 'outro')))
    segs.append(dict(t, seg_dur=round(dur,1), phase=phase))

print("编排顺序:")
for i, s in enumerate(segs):
    print(f"{i+1:2d}. {s['title']:32s} bpm={s['bpm']:6.1f} key={s['key']:2s} rms={s['rms']:.4f} 段长={s['seg_dur']}")

# ---- 渲染: 每段 trim 后 acrossfade ----
os.makedirs('/tmp/dj-lofi/segs', exist_ok=True)
prev = None
for i, s in enumerate(segs):
    seg_file = f"/tmp/dj-lofi/segs/{i:02d}.mp3"
    if not os.path.exists(seg_file):
        # 取曲子中段最佳听区: 跳过前奏 25%(intro 保留原曲开头)
        start = 0 if i == 0 else min(s['dur']*0.18, s['dur']-s['seg_dur']-5)
        cmd = ['ffmpeg','-y','-loglevel','error','-i', f"src/{s['f']}",
               '-ss', str(round(start,1)), '-t', str(s['seg_dur']+XF),
               '-c','copy', seg_file.replace('.m4a','.mp3')]
        subprocess.run(cmd, check=True, cwd='/tmp/dj-lofi')
    print('seg', i, 'ok')

# 链式 acrossfade
inputs = []
for i in range(len(segs)):
    inputs += ['-i', f'segs/{i:02d}.mp3']
n = len(segs)
fc = ''
for i in range(n-1):
    fc += f"[{i}:a]"
fc += f"acrossfade=d={XF}:c1=tri:c2=tri[a1];"
# 依次再叠
last = 'a1'
for i in range(1, n-1):
    pass
# acrossfade 只支持两路, 逐步合并: 用 filter_complex 链式写法
fc = ''
prev_label = '0:a'
for i in range(1, n):
    out_label = f'x{i}'
    fc += f"[{prev_label}][{i}:a]acrossfade=d={XF}:c1=tri:c2=tri[{out_label}];"
    prev_label = out_label
fc = fc.rstrip(';')
cmd = ['ffmpeg','-y','-loglevel','error'] + inputs + ['-filter_complex', fc, '-map', f'[{prev_label}]',
       '-c:a','libmp3lame','-q:a','2','/tmp/dj-lofi/lofi-dj-set.mp3']
print('rendering chain xfade...')
subprocess.run(cmd, check=True, cwd='/tmp/dj-lofi')

# 时间轴: 段 i 起点 = sum(前面段长) - i*XF
starts = []
acc = 0.0
for i, s in enumerate(segs):
    if i > 0: acc -= XF
    starts.append(round(acc,1))
    acc += s['seg_dur'] + XF
total = acc - XF
print('总长:', round(total,1), 's =', round(total/60,1), 'min')

out = [dict(title=s['title'], bpm=s['bpm'], key=s['key'], rms=s['rms'], phase=s['phase'],
            start=starts[i], dur=round(s['seg_dur'],1), f=s['f']) for i, s in enumerate(segs)]
json.dump(dict(setlist=out, total=round(total,1), xf=XF), open('/tmp/dj-lofi/timeline.json','w'), ensure_ascii=False, indent=1)
print('done')
