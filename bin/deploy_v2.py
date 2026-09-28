"""deploy_v2.py — v2 双版本部署
1. out/<id>_lofi_v2.mp3 → 服务器 lofi-<id>-v2-<TOKEN>.mp3
2. 页面 TRACKS 每首歌加 v2 条目 (带风格/场景标签)
3. nginx location 追加 (server 块内)
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'out')
REMOTE = 'root@47.95.167.143'
WEB = '/var/www/syrinx'
TOKEN = 'cde18313c9f2'  # 现有页面 token

SONGS = {}
for line in open(os.path.join(ROOT, 'work', 'songs.tsv'), encoding='utf-8'):
    if line.strip():
        sid, artist, title = line.strip().split('|')
        SONGS[sid] = (artist, title)

def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print('CMD FAIL:', cmd, r.stderr[:300])
    return r.stdout

def dur_str(sec):
    m, s = divmod(int(round(sec)), 60)
    return f'{m}:{s:02d}'

# 1. 上传 v2 mp3
uploaded = []
for sid in SONGS:
    mp3 = os.path.join(OUT, f'{sid}_lofi_v2.mp3')
    if not os.path.exists(mp3):
        print('缺', mp3); continue
    sh(['scp', '-q', mp3, f'{REMOTE}:{WEB}/lofi-{sid}-v2-{TOKEN}.mp3'])
    d = sh(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', mp3]).strip()
    uploaded.append((sid, float(d)))
print(f'上传 {len(uploaded)} 首 v2')

# 2. 页面: 现有 html + 每首 v1 条目后插 v2 条目
html = sh(['ssh', REMOTE, f'cat {WEB}/lofi-room-{TOKEN}.html'])
assert 'TRACKS' in html

v2_tags = '["v2 睡眠档","lowpass 4-4.5k","−19 LUFS 助眠","深夜/学习","温柔人声"]'
v1_tags = '["v1 经典档","33rpm 0.92x","vinyl 底噪","−16 LUFS","夜间驾车/氛围"]'

for sid, dur in uploaded:
    artist, title = SONGS[sid]
    # v1 条目: 改名加 (v1) 标识 + 换 tags
    pat_v1 = re.compile(
        r'(\{id:"' + sid + r'", name:"' + re.escape(title) + r'", artist:"[^"]*", src:"/lofi-' + sid + r'-' + TOKEN + r'\.mp3",\n\s*cover:"[^"]*", dur:"[^"]*", tags:)\[[^\]]*\](\},)')
    v2_entry = (f'{{id:"{sid}_v2", name:"{title} (Sleep V2)", artist:"{artist} · lofi remix", '
                f'src:"/lofi-{sid}-v2-{TOKEN}.mp3",\n   cover:"/covers-au/lofi-{sid}.jpg", '
                f'dur:"{dur_str(dur)}", tags:{v2_tags}}},')
    html, n = pat_v1.subn(lambda m: m.group(1) + v1_tags + m.group(2) + '\n  ' + v2_entry, html)
    if n != 1:
        print(f'!! v1 条目匹配异常: {sid} (n={n})')

open(os.path.join(ROOT, 'work', 'lofi-room-v2.html'), 'w', encoding='utf-8').write(html)
n_tracks = len(re.findall(r'\{id:"', html))
print(f'页面共 {n_tracks} 首 → work/lofi-room-v2.html')
sh(['scp', '-q', os.path.join(ROOT, 'work', 'lofi-room-v2.html'),
    f'{REMOTE}:{WEB}/lofi-room-{TOKEN}.html'])

# 3. nginx location (服务器端 awk 插入 server 块内, 与上次同法)
conf = '# === lofi v2 batch 20260928b ===\n'
for sid, dur in uploaded:
    conf += (f'location = /lofi-{sid}-v2-{TOKEN}.mp3 {{\n'
             '    add_header Cache-Control "no-cache";\n'
             '    add_header Accept-Ranges bytes;\n'
             f'    try_files /lofi-{sid}-v2-{TOKEN}.mp3 =404;\n}}\n')
open(os.path.join(ROOT, 'work', 'nginx_v2.conf'), 'w', encoding='utf-8').write(conf)
sh(['scp', '-q', os.path.join(ROOT, 'work', 'nginx_v2.conf'), f'{REMOTE}:/tmp/lofi_v2.conf'])
sh(['ssh', REMOTE, f'''bash -c '
if ! grep -q "lofi v2 batch 20260928b" /etc/nginx/sites-enabled/romanticjojo; then
  cp /etc/nginx/sites-enabled/romanticjojo /etc/nginx/sites-enabled/romanticjojo.bak2
  awk "BEGIN{{while((getline l < \\"/tmp/lofi_v2.conf\\")>0) buf=buf l \\"\\n\\"}} /location = /lofi-wake_me_up-{TOKEN}\\.mp3 {{/{{printf \\"%s\\", buf}} {{print}}" /etc/nginx/sites-enabled/romanticjojo > /tmp/rj2.new
  cp /tmp/rj2.new /etc/nginx/sites-enabled/romanticjojo
  nginx -t && systemctl reload nginx && echo NGINX_RELOADED
else
  echo ALREADY
fi' '''])
print('DEPLOY V2 DONE')
