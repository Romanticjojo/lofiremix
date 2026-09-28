"""deploy_tracks.py — Lofi House 批量部署
1. out/<id>_lofi.mp3 → 服务器 /var/www/syrinx/lofi-<id>-<TOKEN>.mp3 (同 token 复用现有页面)
2. 封面 work/covers/lofi-<id>.jpg → /var/www/syrinx/covers-au/
3. deploy/lofi-room.html TRACKS 数组替换为现有3首+新25首 → 上传覆盖
4. 服务器端生成 nginx location 追加片段并重载
用法: python bin/deploy_tracks.py [--token XXXX] [--dry]
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'out')
COVERS = os.path.join(ROOT, 'work', 'covers')
HTML = os.path.join(ROOT, 'deploy', 'lofi-room.html')
REMOTE = 'root@47.95.167.143'
WEB = '/var/www/syrinx'

DRY = '--dry' in sys.argv
TOKEN = None
for a in sys.argv:
    if a.startswith('--token='):
        TOKEN = a.split('=', 1)[1]

# songs.tsv: id|artist|title
SONGS = {}
for line in open(os.path.join(ROOT, 'work', 'songs.tsv'), encoding='utf-8'):
    if line.strip():
        sid, artist, title = line.strip().split('|')
        SONGS[sid] = (artist, title)


def sh(cmd, **kw):
    print('$', cmd if isinstance(cmd, str) else ' '.join(cmd))
    if DRY:
        return ''
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw).stdout


def dur_str(sec):
    m, s = divmod(int(round(sec)), 60)
    return f'{m}:{s:02d}'


# ---- 0. 现有 token ----
if not TOKEN:
    r = sh(f"ssh {REMOTE} 'ls {WEB} | grep -oP \"lofi-room-\\K[a-f0-9]{{12}}(?=\\.html)\" | head -1'")
    TOKEN = r.strip()
    if not TOKEN:
        print('服务器无现有页面, 需 --token 生成新页面'); sys.exit(1)
print(f'页面 token: {TOKEN}')

# ---- 1. 上传 mp3 + 封面 ----
uploaded = []
for sid in SONGS:
    mp3 = os.path.join(OUT, f'{sid}_lofi.mp3')
    if not os.path.exists(mp3):
        print(f'!! 缺 {mp3}, 跳过'); continue
    sh(['scp', '-q', mp3, f'{REMOTE}:{WEB}/lofi-{sid}-{TOKEN}.mp3'])
    cov = os.path.join(COVERS, f'lofi-{sid}.jpg')
    if os.path.exists(cov):
        sh(['scp', '-q', cov, f'{REMOTE}:{WEB}/covers-au/lofi-{sid}.jpg'])
    # 时长 (本地 mp3)
    d = sh(f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{mp3}"').strip()
    uploaded.append((sid, float(d)))
    print(f'  up {sid} ({dur_str(float(d))})')

print(f'上传 {len(uploaded)} 首')

# ---- 2. 页面 TRACKS: 现有 3 首 (从线上 html 抓) + 新 25 首 ----
live_html = sh(f"ssh {REMOTE} 'cat {WEB}/lofi-room-{TOKEN}.html'")
assert 'TRACKS' in live_html, '线上页面抓取失败'
html = live_html

new_tracks = []
for sid, dur in uploaded:
    artist, title = SONGS[sid]
    new_tracks.append(
        f'  {{id:"{sid}", name:"{title}", artist:"{artist} · lofi remix", '
        f'src:"/lofi-{sid}-{TOKEN}.mp3",\n'
        f'   cover:"/covers-au/lofi-{sid}.jpg", dur:"{dur_str(dur)}", '
        f'tags:["33rpm 0.92x","lowpass 7-8k","tremolo 磁带抖动","vinyl 底噪","−16 LUFS"]}},'
    )

# 在 TRACKS 数组末尾 (]; 之前) 插入新曲目
block = '\n'.join(new_tracks)
html = re.sub(r'(const TRACKS = \[.*?)(\n\];)', lambda m: m.group(1) + '\n' + block + m.group(2), html, flags=re.S)
open(os.path.join(ROOT, 'work', 'lofi-room-new.html'), 'w', encoding='utf-8').write(html)
n = len(re.findall(r'\{id:"', html))
print(f'新页面共 {n} 首 → work/lofi-room-new.html')

# ---- 3. 上传页面 + nginx location 批量 ----
sh(['scp', '-q', os.path.join(ROOT, 'work', 'lofi-room-new.html'), f'{REMOTE}:{WEB}/lofi-room-{TOKEN}.html'])
# 防重复: 标记已存在则不再追加 nginx 配置
mark = sh(f"ssh {REMOTE} 'grep -c \"lofi batch 20260928\" /etc/nginx/sites-enabled/romanticjojo'").strip()
if mark and mark != '0':
    print('nginx 批量 location 已存在, 跳过追加')
else:
    conf = '# === lofi batch 20260928 ===\n'
    for sid, dur in uploaded:
        conf += f'''location = /lofi-{sid}-{TOKEN}.mp3 {{
    add_header Cache-Control "no-cache";
    add_header Accept-Ranges bytes;
    try_files /lofi-{sid}-{TOKEN}.mp3 =404;
}}
'''
    sh(f"ssh {REMOTE} 'cp /etc/nginx/sites-enabled/romanticjojo /etc/nginx/sites-enabled/romanticjojo.bak-20260928 && cat >> /etc/nginx/sites-enabled/romanticjojo <<\"NGINXEOF\"\n{conf}NGINXEOF\nnginx -t && systemctl reload nginx && echo NGINX_RELOADED'")
print('DEPLOY DONE')
