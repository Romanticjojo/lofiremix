import hashlib
import importlib.util
import json
from functools import lru_cache
from pathlib import Path

import librosa
import numpy as np

from .audio import decode, probe
from .models import Track

ANALYSIS_VERSION = "6"
SUPPORTED = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".aiff", ".aif"}


@lru_cache(maxsize=1)
def _beat_predictor():
    import torch
    from beat_this.inference import Audio2Beats
    # CPU preprocessing runs before playback; cap threads to keep the desktop responsive.
    torch.set_num_threads(min(8, torch.get_num_threads()))
    checkpoint = Path(__file__).resolve().parents[2] / "data" / "models" / "final0.ckpt"
    return Audio2Beats(checkpoint_path=str(checkpoint) if checkpoint.exists() else "final0",
                       device="cpu", dbn=False)


def source_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _tempo_from_beats(beats):
    beats = np.asarray(beats, dtype=float)
    span = min(32, len(beats)-1)
    return float(60*span/np.median(beats[span:]-beats[:-span]))


def _key(chroma):
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    vector = np.mean(chroma, axis=1)
    vector -= vector.mean()
    scores = []
    for mode, profile in [("major", major), ("minor", minor)]:
        profile = (profile-profile.mean()) / np.linalg.norm(profile-profile.mean())
        for root in range(12):
            scores.append((float(vector @ np.roll(profile, root)), root, mode))
    scores.sort(reverse=True)
    notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{notes[scores[0][1]]} {scores[0][2]}"


def analyze(path: Path, cache_dir: Path, backend: str = "auto") -> Track:
    path, cache_dir = Path(path).resolve(), Path(cache_dir)
    if backend not in {"auto", "librosa", "beat-this"}:
        raise ValueError("未知节拍分析后端")
    meta = probe(path)
    if meta["duration"] < 8:
        raise ValueError("音频不足 8 秒，无法可靠规划 DJ 过渡")
    fingerprint = source_hash(path)
    learned_available = importlib.util.find_spec("beat_this") is not None
    selected = "beat-this" if backend == "auto" and learned_available else backend
    if selected == "auto":
        selected = "librosa"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{fingerprint}-{selected}-v{ANALYSIS_VERSION}.json"
    if cache_path.exists():
        result = Track(**json.loads(cache_path.read_text(encoding="utf-8")))
        result.path = str(path)
        return result
    sr, hop = 22050, 512
    y = decode(path, sr, 1)[:, 0]
    if np.sqrt(np.mean(y*y)) < 1e-5:
        raise ValueError("音频接近静音，无法分析")
    warnings = []
    if selected == "beat-this":
        try:
            beats, downbeats = _beat_predictor()(y, sr)
        except Exception as error:
            if backend == "beat-this":
                raise RuntimeError(f"Beat This! 分析失败：{error}") from error
            warnings.append(f"预训练节拍分析不可用，回退传统分析：{type(error).__name__}")
            selected = "librosa"
    if selected == "librosa":
        onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        _, beat_frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop,
                                               trim=False)
        beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
        downbeats = beats[::4]
        warnings.append("使用传统节拍检测；小节起点按四拍推断，需要试听确认。")
    beats = np.asarray(beats, dtype=float)
    beats = beats[(beats >= 0) & (beats < meta["duration"])]
    if len(beats) < 8 or np.any(np.diff(beats) <= 0):
        raise ValueError("检测到的节拍不足或异常，无法规划")
    bpm = _tempo_from_beats(beats)
    if not 35 <= bpm <= 240:
        raise ValueError(f"节拍速度异常：{bpm:.1f} BPM")
    intervals = np.diff(beats)
    regularity = float(np.percentile(abs(intervals-np.median(intervals)), 90) / np.median(intervals))
    confidence = "estimated" if selected == "librosa" else ("high" if regularity <= .12 else "low")
    if confidence == "low":
        warnings.append("节拍间距变化较大，采用保守短过渡；该置信等级是启发式指标。")
    spectrum = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    chroma = librosa.feature.chroma_stft(S=spectrum**2, sr=sr, n_fft=2048, hop_length=hop)
    rms = librosa.feature.rms(S=spectrum, frame_length=2048, hop_length=hop)[0]
    frequencies = librosa.fft_frequencies(sr=sr, n_fft=2048)
    # This is spectral crowding, NOT a learned vocal detector.
    mid = spectrum[(frequencies > 300) & (frequencies < 3500)].sum(axis=0)
    density = mid / (spectrum.sum(axis=0)+1e-9)
    downbeats = np.asarray(downbeats, dtype=float)
    downbeats = downbeats[(downbeats >= 0) & (downbeats < meta["duration"])]
    segment_times = downbeats[::4]  # Four-bar candidate grid; structural novelty is a separate score.
    segments = []
    for time in segment_times:
        frame = int(time * sr / hop)
        width = max(1, int(8 * 60 / bpm * sr / hop))
        lo, hi = max(0, frame-width), min(len(rms), frame+width)
        before = chroma[:, lo: max(lo+1, frame)].mean(axis=1)
        after = chroma[:, frame: max(frame+1, hi)].mean(axis=1)
        novelty = float(np.linalg.norm(after-before) / (np.linalg.norm(before)+1e-6))
        segments.append({"time": float(time), "energy": float(np.mean(rms[frame:hi])),
                         "vocal_proxy": float(np.mean(density[frame:hi])), "novelty": novelty})
    warnings.append("段落为候选边界；人声指标仅为中频拥挤度代理，未进行人声识别。")
    tags = meta["tags"]
    track = Track(id=fingerprint[:16], path=str(path), title=tags.get("title", path.stem),
                  artist=tags.get("artist", ""), duration=meta["duration"], sample_rate=meta["sample_rate"],
                  bpm=bpm, beats=beats.tolist(), downbeats=downbeats.tolist(), key=_key(chroma),
                  energy=float(np.percentile(rms, 75)), segments=segments,
                  analysis_backend=selected, warnings=warnings, source_hash=fingerprint,
                  beat_confidence=confidence)
    # A failed learned attempt must not poison its successful backend cache.
    cache_path = cache_dir / f"{fingerprint}-{selected}-v{ANALYSIS_VERSION}.json"
    cache_path.write_text(json.dumps(track.to_dict(), ensure_ascii=False, indent=2, allow_nan=False),
                          encoding="utf-8")
    return track


def discover(folder: Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"找不到曲库文件夹：{folder}")
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)
