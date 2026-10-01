"""Offline vocal handoff using independently separated source tracks."""

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly


def _validate_pair(a, b, sample_rate):
    if (not isinstance(sample_rate, int) or not 8000 <= sample_rate <= 192000
            or a.ndim != 2 or a.shape[1] != 2 or a.shape != b.shape
            or len(a) < sample_rate // 4 or not np.isfinite(a).all() or not np.isfinite(b).all()):
        raise ValueError("vocal edit requires finite, aligned stereo audio at 8–192 kHz")


def remove_outgoing_vocal(mixture, vocal_contribution, sample_rate, exit_at, fade_seconds=.6):
    """Subtract an already weighted outgoing vocal estimate; other audio is untouched.

    exit_at is seconds from the beginning of the supplied clip, not source-song time.
    Estimated stems may retain leakage; this does not guarantee lyric-free audio.
    """
    _validate_pair(mixture, vocal_contribution, sample_rate)
    if (not math.isfinite(exit_at) or not 0 < exit_at <= len(mixture)/sample_rate
            or not math.isfinite(fade_seconds) or fade_seconds <= 0):
        raise ValueError("invalid vocal exit time or fade duration")
    end = round(exit_at * sample_rate)
    start = max(0, end - round(fade_seconds * sample_rate))
    if start == end:
        raise ValueError("vocal fade is shorter than one sample")
    gain = np.ones(len(mixture), dtype=np.float64)
    t = np.linspace(0., 1., end - start)
    gain[start:end] = 1 - t*t*(3 - 2*t)
    gain[end:] = 0
    output = mixture - vocal_contribution * (1 - gain[:, None])
    if not np.isfinite(output).all():
        raise ValueError("vocal edit produced nonfinite audio")
    return output, gain


def detect_vocal_entry(vocals, mixture, sample_rate):
    """First sustained energy in an estimated vocal stem; not a lyric/phoneme detector."""
    _validate_pair(vocals, mixture, sample_rate)
    window = round(sample_rate * .05)
    usable = len(vocals) // window * window

    def envelope(audio):
        return np.sqrt(np.mean(audio[:usable].reshape(-1, window, 2)**2, axis=(1, 2)))

    voice, mix = envelope(vocals), envelope(mixture)
    threshold = max(.003, float(np.percentile(voice, 95)) * .18)
    active = (voice >= threshold) & (voice >= mix * .15)
    # Four consecutive 50 ms frames reject brief instrumental leakage.
    run = 0
    for index, value in enumerate(active):
        run = run + 1 if value else 0
        if run >= 4:
            return (index - 3) * window / sample_rate
    return None


@lru_cache(maxsize=1)
def _separator():
    try:
        import torch
        from demucs.pretrained import get_model
    except ImportError as exc:
        raise RuntimeError("vocal separation requires the optional stems dependency group") from exc
    torch.set_num_threads(min(8, torch.get_num_threads()))
    cache = Path(__file__).resolve().parents[2] / "data" / "models" / "demucs"
    previous = torch.hub.get_dir()
    torch.hub.set_dir(str(cache))
    try:
        model = get_model("htdemucs").cpu().eval()
    finally:
        torch.hub.set_dir(previous)
    return model


def extract_vocals(audio, sample_rate):
    """Run the pinned pretrained model locally on a bounded, already time-stretched crop.

    Preserve input timing/level; do not normalize individual stems or upload audio.
    """
    _validate_pair(audio, audio, sample_rate)
    if len(audio) / sample_rate > 120:
        raise ValueError("separate transition crops of at most 120 seconds")
    model = _separator()
    import torch
    from demucs.apply import apply_model

    factor = math.gcd(sample_rate, model.samplerate)
    resampled = resample_poly(audio, model.samplerate // factor, sample_rate // factor, axis=0)
    wave = torch.from_numpy(np.ascontiguousarray(resampled.T, dtype=np.float32))
    reference = wave.mean(0)
    center = reference.mean()
    deviation = reference.std()
    if deviation < 1e-7:
        deviation = wave.std()
    if deviation < 1e-7:
        return np.zeros_like(audio)
    with torch.inference_mode():
        stems = apply_model(model, ((wave - center) / deviation)[None], device="cpu", shifts=0,
                            split=True, overlap=.25, progress=False, num_workers=0)[0]
        voice = (stems[model.sources.index("vocals")] * deviation + center).cpu().numpy().T
    restored = resample_poly(voice, sample_rate // factor, model.samplerate // factor, axis=0)
    restored = restored[:len(audio)]
    if len(restored) < len(audio):
        restored = np.pad(restored, ((0, len(audio)-len(restored)), (0, 0)))
    _validate_pair(audio, restored, sample_rate)
    return restored.astype(np.float32)
