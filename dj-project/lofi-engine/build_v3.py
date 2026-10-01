#!/usr/bin/env python3
# v3 精华版: 30 分钟高能精选, 节奏编排 = 蓄势→峰值→双峰→落地
# 与 v1(55min 能量缓降)/v2(53min 技巧版) 并列的第三种编排哲学
import json, subprocess, os

feats = json.load(open('feats.json'))
XF = 8.0  # 快节奏交叉更短

# 选曲: 14 首, 能量+听感平衡(去掉最沉的几首, 保留 2 首慢板做呼吸点)
ranked = sorted(feats, key=lambda x: -x['rms'])
slow_breathers = ranked[-3:-1]  # 能量最低 2 首做呼吸点(相对)
high = [t for t in ranked if t not in slow_breathers][:12]
pool = slow_breathers + high

# 编排: 开场中能→爬升→峰值群→呼吸→二次峰→慢板落地
def energy_tier(t): return t['rms']
pool.sort(key=energy_tier, reverse=True)
peaks, mids = pool[:6], pool[6:]
order = []
order.append(mids.pop(0))            # 开场: 中能
order += peaks[:3]                    # 爬升
order.append(slow_breathers[0])       # 呼吸点1
order += peaks[3:]                    # 峰值群
order += mids[:3]                     # 二次峰区
order.append(slow_breathers[-1])      # 收尾慢板

# 段长: 快曲 75-95s, 慢板呼吸 60s
PHASES = ['IGNITION', 'LIFT', 'BREATHE', 'SUMMIT', 'SECOND WIND', 'LANDING']
def phase_of(i, n):
    r = i / n
    return PHASES[0] if r < 0.1 else PHASES[1] if r < 0.3 else PHASES[2] if r == 0.25 or i == 3 else PHASES[3] if r < 0.65 else PHASES[4] if r < 0.85 else PHASES[5]

# 简化: 手动分
def seg_len(t):
    return 62.0 if t in slow_breathers else 75.0 + min(20.0, t['rms'] * 160)

total = 0; timeline = []
for i, t in enumerate(order):
    d = seg_len(t)
    timeline.append(dict(f=t['f'], title=t['title'], bpm=t['bpm'], key=t['key'], rms=t['rms'], dur=round(d,1), start=round(total,1)))
    total += d - XF
# real starts (进场均提前 XF): render with acrossfade → 音频内 start = 标记 - XF (i>0)
for i, seg in enumerate(timeline):
    if i > 0: seg['real_start'] = round(seg['start'] - XF, 1)
    else: seg['real_start'] = 0.0

print(f'v3: {len(timeline)} 曲, 总长 {total:.0f}s = {total/60:.1f}min')
for i, s in enumerate(timeline):
    print(f"{i+1:2d}. {s['title'][:28]:28s} {s['bpm']:5.1f} {s['key']:2s} rms={s['rms']:.3f} {s['dur']:.0f}s @ {s['real_start']:.0f}")

json.dump(dict(xf=XF, total=round(total,1), setlist=timeline), open('timeline-v3.json','w'), ensure_ascii=False, indent=1)
