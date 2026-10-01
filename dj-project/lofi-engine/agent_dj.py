#!/usr/bin/env python3
# Lofi Night Shift v2 — Agent 实时 DJ 引擎
# 每个切点由决策器选技巧: 段属性(能量/调性/BPM/位置) → tech 选择 → ffmpeg 真实渲染
# 与 Auret model_set_v2 的 qsin 等功率交叉不同: lofi 语境用更慢的技巧族
import json, subprocess, os, sys

feats = json.load(open('/tmp/dj-lofi/feats.json'))
SRC = '/tmp/dj-lofi/src'
OUT = '/tmp/dj-lofi'
XF = 10.0   # 交叉长(lofi 慢板技巧后留 10s)

# ── 1. 编排(沿用能量缓降 + 邻接) ──
order_by_rms = sorted(feats, key=lambda t: -t['rms'])
first = order_by_rms[:12]; second = order_by_rms[12:]
def seq(arr):
    out = []; pool = arr[:]
    while pool:
        pool.sort(key=lambda t: -t['rms'])
        nxt = pool[0]
        if out:
            last = out[-1]
            cands = [t for t in pool if t['key'] == last['key'] or abs(t['bpm']-last['bpm']) < 8]
            if cands: cands.sort(key=lambda t: -t['rms']); nxt = cands[0]
        out.append(nxt); pool.remove(nxt)
    return out
setlist = seq(first) + list(reversed(sorted(second, key=lambda t: -t['rms'])))

# ── 2. Agent 决策器: 段属性 → 技巧 ──
def decide(i, cur, nxt, n):
    """返回 (tech, reason)。技巧族按 lofi 语境适配:
    - tape_stop: 磁带减速(brake) — 能量骤降点用, lofi 最标志性
    - echo_out:  回声抽离 — 远调过渡/情绪断点
    - filter_build: 高通渐入 — 同调连锁提速进场
    - loop_roll: 节奏循环 — 尾声蓄势
    - tape_start: 磁带起步(慢速起播) — 开场/段尾进大曲
    - blend: 纯长交叉 — 默认, 最符合睡眠语境
    """
    frac = i / max(1, n-1)
    d_bpm = (nxt['bpm']-cur['bpm'])/cur['bpm'] if nxt else 0
    same_key = nxt and nxt['key'] == cur['key']
    near_key = nxt and abs(ord(nxt['key'][0])-ord(cur['key'][0])) <= 1

    # 前半场(还醒着): 多技巧; 后半场(渐睡): 回归纯 blend, 偶尔 tape
    if frac > 0.75:
        if i % 5 == 0: return ('tape_stop', '深夜偶发磁带刹停 · 增加梦境感')
        return ('blend', '后半夜 · 纯长交叉不打扰')
    # 前半场决策
    if d_bpm < -0.12 and i >= 2:
        return ('tape_stop', f'能量骤降 {d_bpm*100:.0f}% → 磁带刹停再落')
    if same_key or (near_key and abs(d_bpm) < 0.06):
        return ('filter_build', f'同调{"/近调" if not same_key else ""} {cur["key"]}→{nxt["key"]} → 高通滑入')
    if not near_key or abs(d_bpm) > 0.10:
        return ('echo_out', f'远调 {cur["key"]}→{nxt["key"]} → 回声抽离衔接')
    if i in (4, 5):
        return ('loop_roll', '中段蓄势 · 2拍循环收尾')
    if i == 0:
        return ('tape_start', '开场 · 磁带慢起')
    return ('blend', '邻接顺滑 · 长交叉')

# ── 3. 段长 ──
def seg_len(i, n, dur):
    frac = i / max(1, n-1)
    L = 150 - 40*frac
    return min(L, dur - 25)

# ── 4. ffmpeg 技巧渲染(真实 DSP, 非 UI 演示) ──
def chain(i, seg, tech):
    pre = f'atrim=duration={seg+XF:.2f},asetpts=PTS-STARTPTS'
    if tech == 'tape_stop':
        cut = max(0.0, seg - 4)
        # 刹停材料: 只取 4s, 降速后 ~7.7s, afade 收尾
        return (f'[0:a]{pre},asplit=2[m][t];'
                f'[m]atrim=duration={cut:.2f},asetpts=PTS-STARTPTS[mm];'
                f'[t]atrim=start={cut:.2f}:duration=4.0,asetpts=PTS-STARTPTS,'
                f'atempo=0.72,atempo=0.72,afade=t=out:st=0:d=2.6[br];'
                f'[mm][br]concat=n=2:v=0:a=1[o]')
    if tech == 'tape_start':
        # 磁带起步: 前 3s 从 0.7x 渐进回 1x
        return (f'[0:a]{pre},asplit=2[m][t];'
                f'[m]atrim=duration=0.0001,asetpts=PTS-STARTPTS[sil];'
                f'[t]asetrate=44100*0.94,aresample=44100,atempo=1.0,'
                f'afade=t=in:st=0:d=3[st];'
                f'[sil][st]concat=n=2:v=0:a=1[o]')
    if tech == 'echo_out':
        # 只在尾部 8s 加回声(干信号全保持)
        cut = max(0.0, seg - 8)
        return (f'[0:a]{pre},asplit=2[m][t];'
                f'[m]atrim=duration={cut:.2f},asetpts=PTS-STARTPTS[mm];'
                f'[t]atrim=start={cut:.2f},asetpts=PTS-STARTPTS,'
                f'aecho=1:1:280|560:0.22|0.12,alimiter=limit=0.82[ec];'
                f'[mm][ec]concat=n=2:v=0:a=1[o]')
    if tech == 'filter_build':
        cut = max(0.0, seg - 9)
        return (f'[0:a]{pre},asplit=2[fa][fb];'
                f'[fb]atrim=start={cut:.2f},asetpts=PTS-STARTPTS,'
                f'highpass=f=220,afade=t=in:st=0:d=8[hp];'
                f'[fa]atrim=duration={cut:.2f},asetpts=PTS-STARTPTS[fa2];'
                f'[fa2][hp]concat=n=2:v=0:a=1[o]')
    if tech == 'loop_roll':
        cut = max(0.0, seg - 7)
        return (f'[0:a]{pre},asplit=2[m][t];'
                f'[m]atrim=duration={cut:.2f},asetpts=PTS-STARTPTS[mm];'
                f'[t]atrim=start={cut:.2f}:duration=1.8,asetpts=PTS-STARTPTS,'
                f'aloop=loop=3:size=44100,afade=t=out:st=0:d=6[lp];'
                f'[mm][lp]concat=n=2:v=0:a=1[o]')
    return f'[0:a]{pre}[o]'

TECH_CN = {'tape_stop':'磁带刹停','tape_start':'磁带慢起','echo_out':'回声抽离',
           'filter_build':'高通滑入','loop_roll':'循环蓄势','blend':'长板交叉'}

# ── 5. 渲染每段 ──
segs_info = []
print('Agent DJ 决策:')
for i, t in enumerate(setlist):
    nxt = setlist[i+1] if i+1 < len(setlist) else None
    tech, reason = decide(i, t, nxt, len(setlist))
    L = seg_len(i, len(setlist), t['dur'])
    start = 0 if i == 0 else min(t['dur']*0.18, t['dur']-L-5)
    wav = f'{OUT}/segs2/{i:02d}.wav'
    os.makedirs(f'{OUT}/segs2', exist_ok=True)
    # -ss 放 -i 前(输入快进), 每次强制重渲染
    for attempt in range(2):
        if os.path.exists(wav): os.unlink(wav)
        r = subprocess.run(['ffmpeg','-y','-loglevel','error','-ss', f'{start:.2f}', '-t', f'{L+XF:.2f}',
                            '-i', f"{SRC}/{t['f']}",
                            '-filter_complex', chain(i, L, tech), '-map','[o]',
                            '-ar','44100', wav], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(wav) and os.path.getsize(wav) > 100000: break
        if attempt: print('SEG ERR', i, tech, r.stderr[-200:]); sys.exit(1)
    d = float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                              '-of','csv=p=0', wav], capture_output=True, text=True).stdout.strip())
    segs_info.append(dict(t, seg_dur=round(L,1), real_dur=round(d,1), tech=tech, reason=reason,
                          phase=('intro' if i<2 else 'warmup' if i<9 else 'build' if i<17 else 'peak' if i<22 else 'outro')))
    print(f"{i+1:2d}. {t['title']:32s} {TECH_CN[tech]:6s} {reason}")

# ── 6. 链式 acrossfade(等功率 qsin 由 acrossfade 的 c1=c2=tri 承担) ──
inputs = []
for i in range(len(segs_info)):
    inputs += ['-i', f'segs2/{i:02d}.wav']
n = len(segs_info)
fc = ''
prev = '0:a'
for i in range(1, n):
    outl = f'x{i}'
    fc += f'[{prev}][{i}:a]acrossfade=d={XF}:c1=tri:c2=tri[{outl}];'
    prev = outl
fc = fc.rstrip(';')
final = f'{OUT}/lofi-dj-set-v2.mp3'
subprocess.run(['ffmpeg','-y','-loglevel','error'] + inputs + ['-filter_complex', fc, '-map', f'[{prev}]',
                '-c:a','libmp3lame','-q:a','2', final], check=True, cwd=OUT)

# ── 7. 时间轴(修正 acrossfade 重叠) ──
starts = [0.0]
for i in range(1, len(segs_info)):
    starts.append(round(starts[-1] + segs_info[i-1]['real_dur'] - XF, 1))
total = starts[-1] + segs_info[-1]['real_dur']
print(f"\n总长: {total:.0f}s = {total/60:.1f}min  技巧统计:", {k: sum(1 for s in segs_info if s['tech']==k) for k in set(s['tech'] for s in segs_info)})

out = [dict(title=s['title'], bpm=s['bpm'], key=s['key'], rms=s['rms'], phase=s['phase'],
            start=starts[i], dur=round(s['real_dur'],1), tech=s['tech'], reason=s['reason'], f=s['f'])
       for i, s in enumerate(segs_info)]
json.dump(dict(setlist=out, total=round(total,1), xf=XF), open(f'{OUT}/timeline-v2.json','w'), ensure_ascii=False, indent=1)
print('done →', final)
