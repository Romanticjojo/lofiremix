#!/usr/bin/env python3
# v4 长途飞行版: 25 曲全上, 段长 200-240s(接近整曲), 12s 长交叉
# 编排: 3 幕式 (起飞巡航→平流层→夜降), 能量波形: 中开→缓升→高原平台→缓慢滑降
import json

feats = json.load(open('feats.json'))
XF = 12.0

ranked = sorted(feats, key=lambda x: x['rms'])  # 低→高
# 手动编排: 按能量分带
band_low  = ranked[:8]     # rms 最低 8
band_mid  = ranked[8:17]
band_high = ranked[17:]    # 8 首

def pick(band, n): return [band.pop(0) for _ in range(n)] if band else []

order = []
# 幕1 起飞巡航 (8 曲): 中低能量开场缓慢爬升
m1 = [band_mid.pop(0)] + band_low[:4] + [band_mid.pop(0), band_mid.pop(0), band_mid.pop(0)]   # 8
m2 = band_mid + band_high[:3]              # 9 平流层
m3 = band_high[3:] + list(reversed(band_low[4:]))  # 8 夜降
order = m1 + m2 + m3
assert len(m1) + len(m2) + len(m3) == 25, (len(m1), len(m2), len(m3))
order = [t for t in order if t]
assert len(order) == 25, len(order)

# 段长: 慢板 215s, 中 225s, 高能 235s (原曲 239-400s, 全部容纳主歌+副歌+桥段)
def seg_len(t):
    return 235.0 if t['rms'] > 0.082 else 225.0 if t['rms'] > 0.075 else 215.0

ACT = []
total = 0
for i, t in enumerate(order):
    d = seg_len(t)
    act = 'ACT I · 起飞巡航' if i < 8 else 'ACT II · 平流层' if i < 16 else 'ACT III · 夜降'
    ACT.append(act)
    timeline_seg = dict(f=t['f'], title=t['title'], bpm=t['bpm'], key=t['key'], rms=t['rms'], dur=round(d,1), start=round(total,1), act=act)
    globals().setdefault('segs', []).append(timeline_seg)
    total += d - XF

segs = globals()['segs']
for i, seg in enumerate(segs):
    seg['real_start'] = 0.0 if i == 0 else round(seg['start'] - XF, 1)

print(f'v4: {len(segs)} 曲, 总长 {total:.0f}s = {total/60:.1f}min')
for i, s in enumerate(segs):
    print(f"{i+1:2d}. [{s['act'][:6]}] {s['title'][:26]:26s} {s['bpm']:5.1f} {s['key']:2s} rms={s['rms']:.3f} {s['dur']:.0f}s @ {s['real_start']:.0f}")

json.dump(dict(xf=XF, total=round(total,1), setlist=segs), open('timeline-v4.json','w'), ensure_ascii=False, indent=1)
