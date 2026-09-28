"""jeez_spectral.py — jeez 前145s vs 咱们成品的频谱对比
aspectralstats 版本不行, 改用 ffmpegshowspectrumpict + 手写 FFT (numpy)
"""
import subprocess

import numpy as np

JEEZ = r"D:\LLM_work\lofiremix\work\jeez_mix.mp4"
OURS_BL = r"D:\LLM_work\lofiremix\out\blinding_lights_lofi.mp3"
OURS_LTZ = r"D:\LLM_work\lofiremix\out\less_than_zero_lofi.mp3"
SRC_BL = r"D:\LLM_work\lofiremix\work\src\blinding_lights.flac"


def load_mono(f, ss, dur):
    """解码为 44.1k 单精度 mono numpy"""
    p = subprocess.run(
        ['ffmpeg', '-hide_banner', '-vn', '-ss', str(ss), '-t', str(dur), '-i', f,
         '-af', 'aformat=sample_rates=44100:channel_layouts=mono', '-f', 'f32le', '-'],
        capture_output=True, timeout=300)
    return np.frombuffer(p.stdout, dtype=np.float32)


def spectral_profile(y, sr=44100, window=4096):
    """分帧 FFT → 能量谱 → 质心/滚降/各频段能量占比 + 谐波噪声感"""
    n = len(y) // window * window
    frames = y[:n].reshape(-1, window) * np.hanning(window)
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    freqs = np.fft.rfftfreq(window, 1 / sr)
    med = np.median(spec, axis=0)
    total = med.sum()
    centroid = (freqs * med).sum() / total
    cum = np.cumsum(med) / total
    rolloff = freqs[np.searchsorted(cum, 0.85)]
    band = lambda lo, hi: med[(freqs >= lo) & (freqs < hi)].sum() / total
    return {
        'centroid': round(float(centroid)),
        'rolloff85': round(float(rolloff)),
        'sub100': round(float(band(20, 100)), 3),
        'low100_300': round(float(band(100, 300)), 3),
        'mid300_2k': round(float(band(300, 2000)), 3),
        'high2k_7k': round(float(band(2000, 7000)), 3),
        'air7k_16k': round(float(band(7000, 16000)), 3),
    }


def lufs(f, ss, dur):
    p = subprocess.run(
        ['ffmpeg', '-hide_banner', '-vn', '-ss', str(ss), '-t', str(dur), '-i', f,
         '-af', 'loudnorm=print_format=json', '-f', 'null', '-'],
        capture_output=True, timeout=300)
    out = (p.stdout + p.stderr).decode('utf-8', 'replace')
    import json, re
    m = re.search(r'\{[^{}]*"input_i"[^{}]*\}', out, re.S)
    d = json.loads(m.group(0)) if m else {}
    return f"I={d.get('input_i')} LRA={d.get('input_lra')} TP={d.get('input_tp')}"


samples = [
    ('jeez mix (可用段)', JEEZ, 10),
    ('咱们 blinding_lights', OURS_BL, 30),
    ('咱们 less_than_zero', OURS_LTZ, 30),
    ('(参照) 原版 blinding_lights', SRC_BL, 30),
]
for name, f, ss in samples:
    print(f'==== {name} ====')
    y = load_mono(f, ss, 60)
    print('  频谱:', spectral_profile(y))
    print('  响度:', lufs(f, ss, 60))
