"""Measured candidate features for local listening comparisons."""

import hashlib
import re
import secrets
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import welch

FEATURE_VERSION = "clip-v1"
FEATURE_NAMES = (
    "rms_dbfs", "crest_db", "low_band_ratio", "rms_spread_db",
    "rms_step_p95_db", "side_energy_ratio", "silence_fraction",
)


def file_sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def session_file(session: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("candidate path must be relative to its session")
    root = Path(session).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise ValueError("candidate file is missing or outside its session")
    return path


def clip_features(path: Path) -> dict[str, float]:
    """Numerical measurements, not musical quality scores or vocal detection."""
    info = sf.info(path)
    if not .5 <= info.duration <= 300 or info.channels not in (1, 2) or not 8000 <= info.samplerate <= 192000:
        raise ValueError("training clips must be 0.5–300 seconds, mono/stereo, 8–192 kHz")
    audio, sr = sf.read(path, dtype="float64", always_2d=True)
    if not np.isfinite(audio).all() or np.max(np.abs(audio)) > 8:
        raise ValueError("invalid numerical audio values")
    power = float(np.mean(audio**2))
    rms = np.sqrt(power)
    db = lambda value: 20 * np.log10(np.maximum(value, 1e-6))  # noqa: E731
    window = round(sr * .1)
    frame_power = np.mean(audio**2, axis=1)[:len(audio) // window * window].reshape(-1, window).mean(axis=1)
    levels = db(np.sqrt(frame_power))
    frequencies, spectrum = welch(audio, sr, nperseg=min(4096, len(audio)), axis=0)
    spectrum = spectrum.mean(axis=1)
    low = spectrum[(frequencies >= 20) & (frequencies <= 180)].sum() / max(spectrum.sum(), 1e-12)
    side = 0. if audio.shape[1] == 1 else float(np.mean(((audio[:, 0] - audio[:, 1]) / 2)**2))
    return dict(zip(FEATURE_NAMES, map(float, (
        db(rms), db(np.max(np.abs(audio))) - db(rms), low,
        np.percentile(levels, 90) - np.percentile(levels, 10),
        np.percentile(np.abs(np.diff(levels)), 95),
        side / max(power, 1e-12), np.mean(frame_power < 1e-6),
    )), strict=True))


def build_comparison(session: Path, plan: dict) -> dict:
    """Bind random A/B presentation to decoded candidate features and audio bytes."""
    pairs = []
    for transition in plan["transitions"]:
        pair_id = transition["id"]
        if not isinstance(pair_id, str) or not re.fullmatch(r"transition-[0-9]{2,4}", pair_id):
            raise ValueError("invalid transition ID")
        order = ["baseline", "enhanced"]
        if secrets.randbits(1):
            order.reverse()
        candidates = {}
        for label, renderer in zip(("A", "B"), order, strict=True):
            relative = f"{renderer}/{pair_id}.wav"
            path = session_file(session, relative)
            candidates[label] = {"audio": relative, "sha256": file_sha256(path),
                                 "renderer": renderer, "features": clip_features(path)}
        pairs.append({"id": pair_id, "track_ids": [transition["from_id"], transition["to_id"]],
                      "A": f"{order[0]}/report.html", "B": f"{order[1]}/report.html",
                      "candidates": candidates})
    return {"schema_version": 2, "feature_version": FEATURE_VERSION,
            "source_kind": plan.get("source_kind", "unverified-local-audio"), "pairs": pairs}
