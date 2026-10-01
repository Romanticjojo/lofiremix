"""Objective diagnostics, deliberately separate from human musical-quality judgments."""
import numpy as np
from scipy.signal import resample_poly


def measure(audio: np.ndarray, sample_rate: int) -> dict:
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio.astype(np.float64)**2)))
    window = max(1, int(sample_rate*.02))
    mono_power = np.mean(audio*audio, axis=1)
    usable = len(mono_power)//window*window
    frame_rms = np.sqrt(mono_power[:usable].reshape(-1, window).mean(axis=1))
    silence = frame_rms < 10**(-60/20)
    longest = current = 0
    for value in silence:
        current = current+1 if value else 0
        longest = max(longest, current)
    true_peak = peak
    for start in range(0, len(audio), sample_rate*5):
        chunk = audio[max(0, start-64):min(len(audio), start+sample_rate*5+64)]
        true_peak = max(true_peak, float(np.max(np.abs(resample_poly(chunk, 4, 1, axis=0)))))
    return {"duration_seconds": len(audio)/sample_rate, "sample_rate": sample_rate,
            "channels": audio.shape[1], "sample_peak_dbfs": float(20*np.log10(max(peak, 1e-12))),
            "estimated_true_peak_dbfs": float(20*np.log10(max(true_peak, 1e-12))),
            "rms_dbfs": float(20*np.log10(max(rms, 1e-12))),
            "clipped_samples": int(np.count_nonzero(np.abs(audio) >= 1.)),
            "longest_silence_seconds": longest*window/sample_rate,
            "silence_threshold_dbfs": -60, "silence_window_ms": 20,
            "human_listening": "pending", "true_peak_method": "4x polyphase estimate"}
