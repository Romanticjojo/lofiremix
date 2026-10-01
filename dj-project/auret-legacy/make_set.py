#!/usr/bin/env python3
"""
Auret 决策引擎 → 30 分钟 DJ 串烧编排器
=====================================
用 Auret 的特征数据(track-features.json) + 打分规则(getSuggestions 的原版逻辑)
+ 补上它缺的 Camelot 和声链 + 能量弧约束，输出一个 30 分钟 set 的编排方案
（每首歌的进入顺序、BPM 路线、切歌类型、混音提示），并可直接渲染成串烧音频。

用法:
  python3 make_set.py                # 决策 + 打印编排
  python3 make_set.py --render out.mp3   # 决策 + ffmpeg 渲染真串烧
"""
import json, math, random, subprocess, sys, os
from pathlib import Path

ROOT = Path(__file__).parent
FEATS = ROOT / "data" / "track-features.json"
PREVIEWS = ROOT / "data" / "previews"
SET_MINUTES = 30
SONG_SECONDS = 45          # 每首预览 45s（全曲太长，串烧用片段）
TRANSITION = 8             # 交叉淡化秒数
SEED = 42

# ── Auret 原版: Camelot 映射（dj-treta 的表, Auret 没有, 我们补上）──
KEY_TO_CAMELOT = {
    'C':'8B','C#':'3B','Db':'3B','D':'10B','D#':'5B','Eb':'5B','E':'12B','F':'7B',
    'F#':'2B','G':'9B','G#':'4B','Ab':'4B','A':'11B','A#':'6B','Bb':'6B','B':'1B',
    'Cm':'5A','C#m':'12A','Dm':'7A','D#m':'2A','Ebm':'2A','Em':'9A','Fm':'4A',
    'F#m':'11A','Gm':'6A','G#m':'1A','Am':'8A','A#m':'3A','Bbm':'3A','Bm':'10A',
}

def camelot(key: str) -> str | None:
    k = key.strip()
    if k in KEY_TO_CAMELOT: return KEY_TO_CAMELOT[k]
    # 兼容 "A#m" 之外的写法
    return KEY_TO_CAMELOT.get(k.replace('b','m').replace('#','#'))

def compatible(a: str, b: str) -> bool:
    """Camelot: 同码 / ±1 / 同号大小调互换"""
    if not a or not b: return True   # 未知调性不设限
    if a == b: return True
    na, nb = int(a[:-1]), int(b[:-1])
    sa, sb = a[-1], b[-1]
    if na == nb: return True                     # 大小调互换
    if sa == sb and abs(na - nb) in (1, 11): return True  # 相邻
    return False

def harmonic_distance(a: str, b: str) -> int:
    """0=完美 1=可接受 2=勉强"""
    if not a or not b: return 1
    if a == b: return 0
    na, nb = int(a[:-1]), int(b[:-1])
    sa, sb = a[-1], b[-1]
    if na == nb: return 0
    if sa == sb and abs(na - nb) in (1, 11): return 1
    if sa != sb and abs(na - nb) in (1, 11): return 2
    return 3

# ── Auret 原版场景打分规则（此处用 'hype' 当 DJ set 的能量档）──
RULES = {
    'warmup':  {'bpmMin': 75,  'bpmMax': 100, 'energyMin': 0.05, 'energyMax': 0.18},
    'build':   {'bpmMin': 95,  'bpmMax': 130, 'energyMin': 0.12, 'energyMax': 0.25},
    'peak':    {'bpmMin': 125, 'bpmMax': 175, 'energyMin': 0.15, 'energyMax': 1.0},
    'outro':   {'bpmMin': 70,  'bpmMax': 110, 'energyMin': 0.03, 'energyMax': 0.15},
}

PHASES = [('warmup', .15), ('build', .25), ('peak', .40), ('outro', .20)]  # 30min 的能量弧

def phase_for_index(i, n):
    """能量弧: warmup→build→peak(最长)→outro"""
    frac = i / max(n - 1, 1)
    acc = 0
    for name, w in PHASES:
        acc += w
        if frac < acc: return name
    return 'outro'

def score_track(t, rule, prev_key, prev_bpm, played):
    """Auret 式打分 + 和声/节拍衔接分"""
    if t['track_id'] in played: return -999
    bpm = t.get('bpm') or 120
    energy = t.get('energy_mean') or 0
    s = 0.0
    # Auret 原版三段
    if rule['bpmMin'] <= bpm <= rule['bpmMax']: s += 3
    elif rule['bpmMin'] - 15 <= bpm <= rule['bpmMax'] + 15: s += 1
    if rule['energyMin'] <= energy <= rule['energyMax']: s += 3
    elif rule['energyMin'] - 0.05 <= energy <= rule['energyMax'] + 0.05: s += 1
    # 和声衔接（Auret 缺的）
    hd = harmonic_distance(prev_key, camelot(t.get('estimated_key','')))
    s += (3 - min(hd, 3))
    # BPM 连续性（避免跳变）
    if prev_bpm: s -= abs(bpm - prev_bpm) / 25
    # Auret 的随机因子（避免每次同一套）
    s += random.uniform(0, 1.5)
    return s

def plan_set():
    random.seed(SEED)
    tracks = json.load(open(FEATS))
    # 预计算 camelot
    for t in tracks:
        t['_cam'] = camelot(t.get('estimated_key',''))
    n = max(SET_MINUTES * 60 // SONG_SECONDS, 4)  # 30min ≈ 40 首? 45s 太碎——
    # 实际: 30min 串烧, 每首 ~2.5min 全曲。但我们只有 45s 预览。
    # 方案: 用预览排 40 首×45s=30min 的 "快闪串烧"（每首只出现 45s）
    n = SET_MINUTES * 60 // SONG_SECONDS
    # 17 首循环使用但避免相邻重复
    set_list, played_recent = [], []
    prev_key, prev_bpm = None, None
    for i in range(n):
        phase = phase_for_index(i, n)
        rule = RULES[phase]
        cands = [t for t in tracks if t['track_id'] not in played_recent[-6:]]
        scored = sorted((score_track(t, rule, prev_key, prev_bpm, played_recent[-6:]) for t in cands), reverse=True)
        # scored 丢了对像——重写
        scored = sorted(((score_track(t, rule, prev_key, prev_bpm, played_recent[-6:]), t) for t in cands), key=lambda x: -x[0])
        best = scored[0][1]
        set_list.append({**best, '_phase': phase})
        played_recent.append(best['track_id'])
        prev_key = best['_cam']
        prev_bpm = best.get('bpm')
    return set_list

def transition_type(a, b):
    """dj-treta 三式: 按 BPM 差与和声距离选"""
    hd = harmonic_distance(a.get('_cam'), b.get('_cam'))
    dbpm = abs((b.get('bpm') or 120) - (a.get('bpm') or 120))
    if hd == 0 and dbpm <= 6: return 'blend', 'S曲线长混 16s（和声完美,节拍近）'
    if dbpm <= 6: return 'bass_swap', '低频EQ交换 12s（和声有距离,节拍稳）'
    if dbpm <= 20: return 'filter_sweep', '滤波揭示 8s（BPM跳变,用效果器遮）'
    return 'cut', '硬切 2s（大跳变,短语边界切）'

def render(set_list, out_path):
    """ffmpeg 交叉淡化串烧: 每首 45s, 相邻 8s crossfade"""
    inputs, filt = [], []
    for i, t in enumerate(set_list):
        f = PREVIEWS / t.get('file', t['track_id'])
        inputs += ['-i', str(f)]
    n = len(set_list)
    # acrossfade 链
    if n == 1: filt_chain = '[0:a]'
    else:
        fc = f'[0:a][1:a]acrossfade=d={TRANSITION}:c1=tri:c2=tri[a1]'
        for i in range(2, n):
            fc += f';[a{i-1}][{i}:a]acrossfade=d={TRANSITION}:c1=tri:c2=tri[a{i}]'
        filt_chain = fc.split(';')[-1].split(']')[-1] if False else f'a{n-1}'
        # 直接构造完整链
        parts = []
        prev = '[0:a]'
        for i in range(1, n):
            out = f'[a{i}]'
            parts.append(f'{prev}[{i}:a]acrossfade=d={TRANSITION}:c1=tri:c2=tri{out}')
            prev = out
        filt_chain = ';'.join(parts)
    cmd = ['ffmpeg','-y','-loglevel','error'] + inputs + [
        '-filter_complex', ';'.join(filt_chain) if isinstance(filt_chain, str) else filt_chain,
        '-c:a', 'libmp3lame','-q:a','2', str(out_path)]
    # 修正: filter_complex 用 parts 列表拼的字符串
    # filt_chain 是 parts 列表 join 后的字符串, 最后一个输出标签
    parts = []
    prev = '[0:a]'
    n_in = len(set_list)
    for i in range(1, n_in):
        out = f'[a{i}]'
        parts.append(f'{prev}[{i}:a]acrossfade=d={TRANSITION}:c1=tri:c2=tri{out}')
        prev = out
    fc = ';'.join(parts) if parts else '[0:a]anull[a0]'
    if not parts: prev = '[a0]'
    cmd = ['ffmpeg','-y','-loglevel','error'] + inputs + [
        '-filter_complex', fc, '-map', prev,
        '-c:a','libmp3lame','-q:a','2', str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FFMPEG ERR:", r.stderr[-800:]); sys.exit(1)
    print(f"rendered: {out_path}")

def main():
    sl = plan_set()
    print(f"╔══ 30 分钟 DJ Set 编排（Auret 特征 + Camelot 和声链 + 能量弧）══╗")
    t0 = 0
    for i, t in enumerate(sl):
        mm, ss = divmod(int(t0), 60)
        tec, note = ('—','首曲') if i == 0 else transition_type(sl[i-1], t)
        print(f"  {mm:02d}:{ss:02d} [{t['_phase']:<6}] {t.get('bpm',0):5.1f}BPM {t.get('estimated_key','?'):>3}"
              f"→{t['_cam'] or '?':>4} | {tec:<13}| {t['title'][:38]}")
        t0 += SONG_SECONDS - (TRANSITION if i else 0)
    print(f"  总时长 ≈ {t0//60} 分钟, {len(sl)} 首")
    if '--render' in sys.argv:
        out = ROOT / 'data' / 'set30.mp3'
        render(sl, out)

if __name__ == '__main__':
    main()
