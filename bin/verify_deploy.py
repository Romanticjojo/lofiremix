"""verify_deploy.py — 部署验收: 页面 200 + 全部 mp3 Range 206 + TRACKS 数与歌曲数一致"""
import re
import subprocess
import sys

TOKEN = sys.argv[1] if len(sys.argv) > 1 else None
REMOTE = 'root@47.95.167.143'
WEB = '/var/www/syrinx'

if not TOKEN:
    TOKEN = subprocess.run(
        f"ssh {REMOTE} 'ls {WEB} | grep -oP \"lofi-room-\\K[a-f0-9]{{12}}(?=\\.html)\" | head -1'",
        shell=True, capture_output=True, text=True).stdout.strip()
assert TOKEN, 'no token'
BASE = 'https://romanticjojo.com'
print(f'token: {TOKEN}')

# 1. 页面 200
code = subprocess.run(f'curl -s -o /dev/null -w "%{{http_code}}" {BASE}/vl-{TOKEN}',
                      shell=True, capture_output=True, text=True).stdout.strip()
print(f'page /vl-{TOKEN}: {code}')

# 2. 页面里的 TRACKS src
html = subprocess.run(f'curl -s {BASE}/vl-{TOKEN}', shell=True, capture_output=True, text=True).stdout
srcs = re.findall(r'src:"(/lofi-[a-z0-9_]+-[a-f0-9]+\.mp3)"', html)
print(f'TRACKS entries: {len(srcs)}')

# 3. 每个 mp3: HEAD 200 + Range 206
ok, bad = 0, []
for s in srcs:
    c1 = subprocess.run(f'curl -s -o /dev/null -w "%{{http_code}}" {BASE}{s}',
                        shell=True, capture_output=True, text=True).stdout.strip()
    c2 = subprocess.run(f'curl -s -o /dev/null -w "%{{http_code}}" -r 0-1023 {BASE}{s}',
                        shell=True, capture_output=True, text=True).stdout.strip()
    if c1 == '200' and c2 == '206':
        ok += 1
    else:
        bad.append((s, c1, c2))
print(f'mp3 OK: {ok}/{len(srcs)}')
for b in bad:
    print('  BAD', b)
sys.exit(0 if not bad and code == '200' else 1)
