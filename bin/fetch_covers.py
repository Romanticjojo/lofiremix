"""fetch_covers.py — iTunes Search API 批量拉专辑封面 (600x600 jpg)
用法: python fetch_covers.py <songs_file> <out_dir>
songs_file 每行: id|artist|title   (如 blinding_lights|The Weeknd|Blinding Lights)
"""
import json
import os
import sys
import time
import urllib.request

songs_file, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)
proxy = urllib.request.ProxyHandler({'https': 'http://127.0.0.1:7897', 'http': 'http://127.0.0.1:7897'})
opener = urllib.request.build_opener(proxy)
opener.addheaders = [('User-Agent', 'Mozilla/5.0')]

ok, miss = [], []
for line in open(songs_file, encoding='utf-8'):
    line = line.strip()
    if not line:
        continue
    sid, artist, title = line.split('|')
    out = os.path.join(out_dir, f'lofi-{sid}.jpg')
    if os.path.exists(out) and os.path.getsize(out) > 5000:
        ok.append((sid, 'cached')); continue
    q = urllib.request.quote(f'{title} {artist}')
    url = f'https://itunes.apple.com/search?term={q}&entity=song&limit=5'
    try:
        with opener.open(url, timeout=15) as r:
            d = json.loads(r.read())
        art = None
        # 优先同名 track, 否则取第一张有 artwork 的
        for it in d.get('results', []):
            if it.get('artworkUrl100'):
                if it.get('trackName', '').lower().startswith(title.lower()[:12]) or art is None:
                    art = it['artworkUrl100']
                if it.get('trackName', '').lower().startswith(title.lower()[:12]):
                    break
        if art:
            big = art.replace('100x100', '600x600')
            with opener.open(big, timeout=20) as r:
                data = r.read()
            with open(out, 'wb') as f:
                f.write(data)
            ok.append((sid, f'{len(data)}B'))
        else:
            miss.append(sid)
    except Exception as e:
        miss.append(f'{sid} ({e})')
    time.sleep(0.4)

print(f'OK {len(ok)} / MISS {len(miss)}')
for sid, info in ok:
    print(f'  ok  {sid} {info}')
for m in miss:
    print(f'  MISS {m}')
