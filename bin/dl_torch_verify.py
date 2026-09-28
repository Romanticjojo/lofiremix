"""dl_torch_verify.py — 分段并行下载 torch whl, 官方 sha256 校验, 坏段自动重下
策略: 每轮 16 线程 Range 下载到 seg_v{round}_XX; 组装后校验官方 sha256;
不匹配则与上一轮逐段比对, 只重下内容不一致的段; 最多 5 轮。
"""
import concurrent.futures as cf
import hashlib
import os
import urllib.request

URL = 'https://download.pytorch.org/whl/cu128/torch-2.9.1%2Bcu128-cp311-cp311-win_amd64.whl'
OFFICIAL = '633005a3700e81b5be0df2a7d3c1d48aced23ed927653797a3bd2b144a3aeeb6'
TOTAL = 2862053760
N = 16
SEG = (TOTAL + N - 1) // N
TMP = os.path.join(os.environ['LOCALAPPDATA'], 'Temp')
OUT = os.path.join(TMP, 'torch-2.9.1+cu128-cp311-cp311-win_amd64.whl')

proxy = urllib.request.ProxyHandler({'https': 'http://127.0.0.1:7897', 'http': 'http://127.0.0.1:7897'})


def fetch(seg_idx, round_id, start, end):
    """下载 [start,end] 到 seg_v{round_id}_{seg_idx}, 成功返回路径"""
    path = os.path.join(TMP, f'tv_{round_id}_{seg_idx:02d}')
    expect = end - start + 1
    for attempt in range(6):
        try:
            if os.path.exists(path) and os.path.getsize(path) == expect:
                return path
            req = urllib.request.Request(URL, headers={'Range': f'bytes={start}-{end}', 'User-Agent': 'Mozilla/5.0'})
            opener = urllib.request.build_opener(proxy)
            with opener.open(req, timeout=120) as r:
                data = r.read()
            if len(data) != expect:
                raise IOError(f'size {len(data)} != {expect}')
            with open(path + '.part', 'wb') as f:
                f.write(data)
            os.replace(path + '.part', path)
            return path
        except Exception as e:
            print(f'seg{seg_idx} attempt{attempt}: {e}', flush=True)
    raise IOError(f'seg{seg_idx} 多次失败')


def seg_path(round_id, i):
    return os.path.join(TMP, f'tv_{round_id}_{i:02d}')


ranges = []
for i in range(N):
    s = i * SEG
    e = min((i + 1) * SEG - 1, TOTAL - 1)
    ranges.append((i, s, e))

prev_round = None
for rnd in range(1, 6):
    # 下载 (或复用已完成正确尺寸的段)
    todo = list(ranges)
    if prev_round is not None:
        # 只重下与上一轮内容不一致/缺失的段 → 先全比对
        todo = []
        for i, s, e in ranges:
            p_new, p_old = seg_path(rnd, i), seg_path(prev_round, i)
            if os.path.exists(p_old) and os.path.getsize(p_old) == e - s + 1:
                # 复制上一轮作为候选
                import shutil
                shutil.copyfile(p_old, p_new)
                todo.append((i, s, e))  # 仍需重下以交叉验证? 不: 先组装试哈希
    # 简化: 每轮全量下
    todo = list(ranges)
    with cf.ThreadPoolExecutor(N) as ex:
        futs = {ex.submit(fetch, i, rnd, s, e): i for i, s, e in todo}
        for f in cf.as_completed(futs):
            f.result()
    # 交叉验证: 与上一轮不同的段标记为可疑, 重下一遍
    if prev_round is not None:
        for i, s, e in ranges:
            a, b = seg_path(prev_round, i), seg_path(rnd, i)
            ha = hashlib.sha256(open(a, 'rb').read()).hexdigest()
            hb = hashlib.sha256(open(b, 'rb').read()).hexdigest()
            if ha != hb:
                print(f'seg{i} 两轮不一致, 第三次下载仲裁', flush=True)
                arb = os.path.join(TMP, f'tv_arb_{i:02d}')
                if os.path.exists(arb):
                    os.remove(arb)
                fetch(i, 'arb', s, e)
                hc = hashlib.sha256(open(arb, 'rb').read()).hexdigest()
                win = rnd if hb == hc else prev_round
                if win == prev_round:
                    import shutil
                    shutil.copyfile(a, b)
                os.remove(arb)
    # 组装 + 哈希
    h = hashlib.sha256()
    with open(OUT, 'wb') as out:
        for i, s, e in ranges:
            with open(seg_path(rnd, i), 'rb') as f:
                while True:
                    chunk = f.read(1 << 22)
                    if not chunk:
                        break
                    h.update(chunk)
                    out.write(chunk)
    digest = h.hexdigest()
    print(f'round {rnd}: sha256={digest}', flush=True)
    if digest == OFFICIAL:
        print('SHA256 MATCH — wheel OK')
        # 清理分段
        for i, s, e in ranges:
            for r in (rnd, prev_round):
                if r:
                    p = seg_path(r, i)
                    if os.path.exists(p):
                        os.remove(p)
        break
    prev_round = rnd
else:
    raise SystemExit('5 轮仍未匹配官方哈希')
