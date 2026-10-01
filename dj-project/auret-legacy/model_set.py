#!/usr/bin/env python3
"""
Reference-Model Set Builder — 用笔记本训练的三个模型给 17 首混合曲库排 set
=========================================================================
模型来源: /var/lib/miaomi-dj/20260925-v4/weeknd-reference-model-20260925.zip
  - acoustic-model.json  声学条件选曲 (6 特征线性打分)
  - cue-model.json       出歌点回归 (ridge, 输出 source 退出秒数比例)
  - choice-model.json    曲序 softmax (650 参数, 依赖 source_total_weights 的 8 个参考源,
                          与当前曲库无交集 → 不用, 决策权交给 acoustic)
曲库: /tmp/fw-check/audio 17 首 (Lauv/JVKE/Metro Boomin 等混合, 非 Weeknd)
要求:
  1) 第一首 intro 从歌曲开头进入 (start=0)
  2) 中间切歌加 DJ 技巧: loop roll / spinback filter / echo out / reverb cut
渲染: ffmpeg filter_complex — 每个 transition 有专属音效处理
"""
import json, subprocess, os, sys, glob, math, random

random.seed(42)

MODEL = os.path.expanduser('~/Downloads/hermes_work/miaomi-dj/model/weeknd-model')
FULLS = '/tmp/dj-compare/Auret/data/fulls'   # 17 首 mp3 全曲
SEG, XF = 84, 12                             # 每首 84s(>70), 交叉 12s

# ── 1. 曲库特征 (librosa 重提, 对齐模型特征语义) ──
def load_feats():
    cache = '/tmp/fw17_feats.json'
    if os.path.exists(cache):
        return json.load(open(cache))
    import librosa
    feats = {}
    for f in sorted(glob.glob(f'{FULLS}/*.mp3')):
        base = os.path.basename(f)[:-4]
        y, sr = librosa.load(f, duration=90, mono=True)
        tempo_arr, beats = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(tempo_arr.item() if hasattr(tempo_arr, 'item') else tempo_arr)
        while tempo > 190: tempo /= 2   # fold
        while tempo < 65: tempo *= 2
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
        rms = float(librosa.feature.rms(y=y).mean())
        feats[base] = {'bpm': float(tempo), 'chroma': [float(x) for x in chroma],
                       'energy': rms, 'duration': float(librosa.get_duration(path=f))}
        print(f'  {base[:40]:42s} {tempo:7.2f} E={rms:.3f}', file=sys.stderr)
    json.dump(feats, open(cache, 'w'))
    return feats

# ── 2. Camelot (chroma → key) ──
CAMELOT = {'C':'8B','C#/Db':'3B','D':'10B','D#/Eb':'5B','E':'12B','F':'7B','F#/Gb':'2B','G':'9B','G#/Ab':'4B','A':'11B','A#/Bb':'6B','B':'1B'}
CAMELOT_M = {'C':'5A','C#/Db':'12A','D':'7A','D#/Eb':'2A','E':'9A','F':'4A','F#/Gb':'11A','G':'6A','G#/Ab':'1A','A':'8A','A#/Bb':'3A','B':'10A'}
MAJ = [0,2,4,5,7,9,11]; MIN = [0,2,3,5,7,8,10]
NOTES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']

def est_key(chroma):
    # Krumhansl 简化: 相关性最高的调
    best = (None, -2)
    for root in range(12):
        for mode, prof in [('major', MAJ), ('minor', MIN)]:
            score = sum(chroma[(root+i) % 12] for i in prof) - sum(chroma[(root+i) % 12] for i in range(12) if i not in prof)
            if score > best[1]: best = (f'{NOTES[root]} {"maj" if mode=="major" else "min"}', score)
    return best[0]

def camelot_of(keystr, chroma):
    # major/minor 判定结合 chroma 三度音程更稳, 这里简化用 keystr
    root, mode = keystr.split()
    table = CAMELOT if mode == 'maj' else CAMELOT_M
    for k, v in table.items():
        if k.split('/')[0] == root: return v
    return '?'

def cam_dist(c1, c2):
    if c1 == '?' or c2 == '?': return 2.5
    n1, m1 = int(c1[:-1]), c1[-1]; n2, m2 = int(c2[:-1]), c2[-1]
    d = min((n1-n2) % 12, (n2-n1) % 12)
    if m1 == m2: return 0.0 if d == 0 else (1.0 if d == 1 else 2.0)
    # 相对大小调 (同数字)
    if n1 == n2: return 0.5
    # 大调→上五度小调 (B↔A same letter): 同字母不同 mode 距 1
    return min(1.5 + d*0.5, 3.0)

# ── 3. acoustic model 打分 (对齐 6 特征) ──
ac = json.load(open(f'{MODEL}/acoustic-model.json'))
W = dict(zip(ac['features'], ac['weights']))

def fold_tempo(b): return min(b, 200 - b) if b > 100 else b  # folded: 镜像折返
def folded_gap(b1, b2):
    cands = []
    for mul in (1, 2, 0.5):
        cands.append(abs(b1*mul - b2))
    return min(cands)

def score(prev, cand, phase_pos):
    bpm_p, bpm_c = prev['bpm'], cand['bpm']
    ftg = folded_gap(bpm_p, bpm_c)
    hd = cam_dist(prev['cam'], cand['cam'])
    ed = abs(prev['energy'] - cand['energy'])
    f = {
        'folded_tempo_gap': ftg,
        'harmonic_distance': hd,
        'energy_distance': ed,
        'candidate_energy': cand['energy'],
        'candidate_duration': cand['duration'],
        'candidate_folded_tempo': min(bpm_c, 200-bpm_c) if bpm_c > 100 else bpm_c,
    }
    s = sum(W[k] * v for k, v in f.items())
    # 能量弧相位奖励 (非模型, 编排层): 前段升能量, 尾段降
    n_tracks = 17
    pos = phase_pos / n_tracks
    target_e = 0.20 + 0.16 * math.sin(math.pi * min(pos*1.15, 1.0))  # 弧线
    s += -abs(cand['energy'] - target_e) * 0.3
    return s

# ── 4. cue model 出歌点 ──
cu = json.load(open(f'{MODEL}/cue-model.json'))
def exit_fraction(src, nxt):
    x = [(src['bpm']-cu['mean'][0])/cu['scale'][0],
         (src['duration']-cu['mean'][1])/cu['scale'][1],
         (src['energy']-cu['mean'][2])/cu['scale'][2],
         (nxt['bpm']-cu['mean'][3])/cu['scale'][3],
         (nxt['duration']-cu['mean'][4])/cu['scale'][4],
         (nxt['energy']-cu['mean'][5])/cu['scale'][5]]
    c = cu['coefficients']
    frac = c[0]*x[0]+c[1]*x[1]+c[2]*x[2]+c[3]*x[3]+c[4]*x[4]+c[5]*x[5]+c[6]
    return min(max(frac, 0.30), 0.88)

# ── 5. 编排: 贪心 + acoustic 打分 ──
def build(feats):
    tracks = []
    for base, f in feats.items():
        key = est_key(f['chroma'])
        tracks.append({'id': base, **f, 'key': key,
                       'cam': camelot_of(key, f['chroma']), 'used': False})
    # intro: 最安静(energy 最低)的歌从 0 开始 —— 深夜慢板开场
    intro = min(tracks, key=lambda t: t['energy'])
    intro['used'] = True
    order = [intro]
    cur = intro
    while len(order) < len(tracks):
        cands = [t for t in tracks if not t['used']]
        best = max(cands, key=lambda t: score(cur, t, len(order)))
        best['used'] = True
        order.append(best)
        cur = best
    return order

# ── 6. DJ 技巧分配 ──
TECHNIQUES = ['loop_roll', 'spinback', 'echo_out', 'filter_build']
def pick_tech(i, src, nxt, n):
    ftg = folded_gap(src['bpm'], nxt['bpm'])
    hd = cam_dist(src['cam'], nxt['cam'])
    # 和声近+节奏近 → loop roll (节拍循环蓄势)
    if ftg < 8 and hd <= 1.0: return 'loop_roll'
    # BPM 大跳 → spinback (倒带转盘遮丑)
    if ftg > 30: return 'spinback'
    # 能量升级处 → filter_build (滤波上扬)
    if nxt['energy'] > src['energy'] + 0.01: return 'filter_build'
    # 默认 → echo out (回声抽离)
    return 'echo_out'

# ── 7. 渲染 ──
def render(order, out='data/set-model.mp3'):
    n = len(order)
    inputs, filt = [], []
    clip_specs = []
    t0 = 0.0
    starts = []
    for i, tr in enumerate(order):
        if i == 0:
            start = 0.0; seg = 90          # intro 从歌曲开头
        else:
            ef = exit_fraction(order[i-1], tr)
            src = order[i-1]
            start = min(src['duration'] * ef, max(0, src['duration'] - SEG - XF - 8))
            seg = SEG
        starts.append(t0)
        t0 += (seg - XF) if i > 0 else seg
        clip_specs.append((start, seg))
    for tr, (start, seg) in zip(order, clip_specs):
        inputs += ['-ss', f'{start:.2f}', '-t', f'{seg + XF:.2f}', '-i', f"{FULLS}/{tr['id']}.mp3"]
    # 每轨独立 chain → c{i}
    for i, (tr, (start, seg)) in enumerate(zip(order, clip_specs)):
        tech = tr.get('tech_out', 'xfade')
        pre = f'atrim=duration={seg+XF:.2f},asetpts=PTS-STARTPTS'
        if tech == 'loop_roll':
            cut = max(0.0, seg - 8)
            filt.append(
                f'[{i}:a]{pre},asplit=2[m{i}][t{i}];'
                f'[m{i}]atrim=duration={cut:.2f},asetpts=PTS-STARTPTS[mm{i}];'
                f'[t{i}]atrim=start={cut:.2f}:duration=2,asetpts=PTS-STARTPTS,'
                f'aloop=loop=3:size=88200,afade=t=out:st=0:d=8[lp{i}];'
                f'[mm{i}][lp{i}]concat=n=2:v=0:a=1,c{0 if False else ""}');  # noqa
            # label
            filt[-1] = filt[-1].replace(',c', '') + f'[c{i}];'
        elif tech == 'spinback':
            rev_start = max(0.0, seg + XF - 1.5)
            filt.append(
                f'[{i}:a]{pre},asplit=2[n{i}][r{i}];'
                f'[r{i}]atrim=start={rev_start:.2f},asetpts=PTS-STARTPTS,areverse,'
                f'atempo=1.8,afade=t=out:st=0:d=1.3[rv{i}];'
                f'[n{i}]atrim=duration={rev_start:.2f},asetpts=PTS-STARTPTS[nm{i}];'
                f'[nm{i}][rv{i}]concat=n=2:v=0:a=1[c{i}];')
        elif tech == 'echo_out':
            filt.append(f'[{i}:a]{pre},aecho=0.8:0.88:250|500:0.3|0.18[c{i}];')
        elif tech == 'filter_build':
            cut = max(0.0, seg - 10)
            filt.append(
                f'[{i}:a]{pre},asplit=2[f{i}a][f{i}b];'
                f'[f{i}b]atrim=start={cut:.2f},asetpts=PTS-STARTPTS,'
                f'highpass=f=300,afade=t=in:st=0:d=9[hp{i}];'
                f'[f{i}a]atrim=duration={cut:.2f},asetpts=PTS-STARTPTS[fa{i}];'
                f'[fa{i}][hp{i}]concat=n=2:v=0:a=1[c{i}];')
        else:
            filt.append(f'[{i}:a]{pre}[c{i}];')
    prev = 'c0'
    for i in range(1, n):
        nx = f'cf{i}'
        curve = 'tri' if order[i-1].get('tech_out') in ('loop_roll', 'spinback') else 'qsin'
        filt.append(f'[{prev}][c{i}]acrossfade=d={XF}:c1={curve}:c2=tri[{nx}];')
        prev = nx
    filt_s = chr(10).join(filt).rstrip(';')
    cmd = ['ffmpeg', '-y', '-loglevel', 'error'] + inputs + \
          ['-filter_complex', filt_s, '-map', f'[{prev}]', '-q:a', '4', out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("RENDER FAIL:", r.stderr[-1500:], file=sys.stderr); sys.exit(1)
    return starts, out

def main():
    feats = load_feats()
    order = build(feats)
    # 技巧分配
    for i in range(len(order)-1):
        order[i]['tech_out'] = pick_tech(i, order[i], order[i+1], len(order))
    order[-1]['tech_out'] = 'xfade'
    print("SETLIST:", file=sys.stderr)
    for i, t in enumerate(order):
        print(f"  {i+1:2d}. {t['key']:8s} {t['cam']:4s} {t['bpm']:7.2f} E={t['energy']:.3f}  →{t.get('tech_out','')}  {t['id'][:38]}", file=sys.stderr)
    starts, out = render(order)
    # setlist JSON
    json.dump({'starts': starts, 'order': [
        {'title': t['id'], 'bpm': t['bpm'], 'key': t['key'], 'cam': t['cam'],
         'energy': t['energy'], 'tech': t.get('tech_out','xfade'), 'start': st, 'dur': SEG}
        for t, st in zip(order, starts)
    ]}, open('/tmp/setlist-model.json','w'), ensure_ascii=False, indent=1)
    rr = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',out],capture_output=True,text=True)
    print(f"DONE {out} {rr.stdout.strip()}s", file=sys.stderr)

if __name__ == '__main__':
    main()
