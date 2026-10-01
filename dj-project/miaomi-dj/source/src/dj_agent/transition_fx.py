"""Subtle outgoing-deck effects. Functional approximations, not proprietary emulation."""
import math

import numpy as np
from scipy.signal import butter, sosfiltfilt


def process_exit(audio, sr, seconds, bpm, kind):
    if kind not in {'none', 'filter', 'echo', 'reverb'}:
        raise ValueError('unknown transition FX')
    if (audio.ndim != 2 or not np.isfinite(audio).all() or sr < 4000
            or not math.isfinite(seconds) or seconds <= 0 or not 35 <= bpm <= 240):
        raise ValueError('invalid transition FX input')
    control = {'kind': kind, 'wet_peak': 0., 'duration_seconds': seconds,
               'basis': 'local DSP approximation, not original Pioneer/rekordbox algorithm'}
    if kind == 'none':
        return audio.copy(), control
    result = audio.copy()
    n = min(len(audio), round(sr*seconds))
    dry = audio[-n:]
    phase = np.linspace(0, 1, n, dtype=np.float32)
    ramp = phase*phase*(3-2*phase)
    wet = np.zeros_like(dry)
    peak = {'filter': .65, 'echo': .2, 'reverb': .12}[kind]
    if kind == 'filter':
        wet = sosfiltfilt(butter(2, 650, fs=sr, btype='highpass', output='sos'), dry, axis=0).astype(np.float32)
        control['highpass_hz'] = 650
    else:
        delays = [60/bpm*.5*i for i in range(1, 5)] if kind == 'echo' else [.029, .043, .067, .101, .149, .211, .307, .433]
        weights = np.array([.55**i for i in range(len(delays))])
        weights /= weights.sum()
        for delay, weight in zip(delays, weights, strict=True):
            offset = round(delay*sr)
            if 0 < offset < n:
                wet[offset:] += dry[:-offset]*float(weight)
        control['delay_seconds'] = delays
        control['delay_weights'] = weights.tolist()
    mix = (peak*ramp)[:, None]
    result[-n:] = dry*(1-mix)+wet*mix
    control['wet_peak'] = peak
    return result, control
