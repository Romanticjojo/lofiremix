"""Objective per-transition scoring — no learned weights, no human labels.

Implements the component table of "What Makes a Good DJ Transition?"
(§11): beat alignment, tempo (with half/double folding), harmony, energy
trajectory, spectral/bass interaction, vocal collision, phrase boundaries,
transition-style fit, plus render-QC penalties.  Every component is in
[0, 1]; higher is better.  Hard quality gates (framework §14) live in
:func:`quality_gates`, deliberately separate from soft scores.

Philosophy (§15): teach the system what the music is doing, not what one
person likes.  All inputs are measurable audio/analysis quantities.
"""

from __future__ import annotations

import math

import numpy as np

from .planner import _harmonic_cost, _rate

# Component weights for the soft total (sum = 1).  Beat and harmony carry the
# most weight because they are the most audible failure modes; style and qc
# are tie-breakers.  These are explicit engineering constants, not fitted.
WEIGHTS = {
    "beat": .24,
    "harmony": .18,
    "bpm": .10,
    "arc": .14,
    "spectral": .10,
    "vocal": .08,
    "phrase": .08,
    "style": .04,
    "qc": .04,
}

# Stretch beyond this is a hard gate failure (framework §14.2), mirroring
# planner._rate's ±8% feasibility band.
STRETCH_LIMIT = 1.08


def _clamp01(x: float) -> float:
    if not math.isfinite(x):
        return 0.0
    return float(min(1.0, max(0.0, x)))


def beat_alignment_error(beats_a: np.ndarray, beats_b: np.ndarray,
                         window: int = 32) -> float:
    """Mean absolute beat offset (s) of B's beats vs A's grid, half-beat folded.

    Compares actual beat grids rather than rounded BPM values because small
    tempo errors accumulate into audible drift (framework §3).
    """
    grid = np.asarray(beats_a, dtype=float)
    incoming = np.asarray(beats_b, dtype=float)
    if grid.size < 2 or incoming.size < 2:
        raise ValueError("need at least two beats per side")
    period = float(np.median(np.diff(grid)))
    if period <= 0:
        raise ValueError("non-monotonic beat grid")
    offsets = []
    for t in incoming[:window]:
        idx = np.searchsorted(grid, t)
        candidates = grid[max(0, idx - 1): idx + 1]
        if candidates.size == 0:
            continue
        delta = float(np.min(np.abs(candidates - t)))
        offsets.append(min(delta, period / 2.0))  # fold to half-beat
    return float(np.mean(offsets)) if offsets else period / 2.0


def beat_drift_across_overlap(beats_a: np.ndarray, beats_b: np.ndarray,
                              window: int = 64) -> float:
    """Max |offset| across the overlap window — catches accumulating drift."""
    grid = np.asarray(beats_a, dtype=float)
    incoming = np.asarray(beats_b, dtype=float)
    if grid.size < 2 or incoming.size < 2:
        raise ValueError("need at least two beats per side")
    period = float(np.median(np.diff(grid)))
    worst = 0.0
    for i, t in enumerate(incoming[:window]):
        idx = np.searchsorted(grid, t)
        candidates = grid[max(0, idx - 1): idx + 1]
        if candidates.size == 0:
            continue
        worst = max(worst, min(float(np.min(np.abs(candidates - t))), period / 2.0))
    return worst


def _folded_bpm_pull(bpm_a: float, bpm_b: float) -> float:
    """BPM compatibility with half/double-time folding (framework §4)."""
    rate, factor = _rate(bpm_a, bpm_b)
    # B heard at bpm*rate; factor says how the grid re-interprets (half/double)
    effective = bpm_b * rate * factor
    mismatch = abs(math.log(effective / bpm_a)) if bpm_a > 0 and bpm_b > 0 else 1.0
    return _clamp01(1.0 - mismatch / 0.05)


def _phrase_score(transition_start: float | None, downbeats_a, beats_a=None) -> float:
    """Bonus when the transition lands near a 4-bar phrase boundary (§9)."""
    if transition_start is None:
        return .5  # unknown — neutral
    import numpy as _np
    if downbeats_a is not None:
        db = _np.asarray(downbeats_a, dtype=float)
    elif beats_a is not None and len(beats_a) >= 8:
        db = _np.asarray(beats_a, dtype=float)[::4]
    else:
        return .5
    if db.size < 2:
        return .5
    bar = float(_np.median(_np.diff(db))) * 4  # four bars = one phrase
    if bar <= 0:
        return .5
    distance = abs((transition_start - float(db[0])) % bar)
    distance = min(distance, bar - distance)
    return _clamp01(1.0 - distance / (bar / 2.0))


def _style_fit(style: str | None, bpm_a: float, bpm_b: float,
               harmonic: float, energy_delta: float) -> float:
    """Does the chosen technique suit the musical context? (§10 table)"""
    if style is None:
        return .5
    table = {
        # style: (needs_harmony, max_energy_delta)
        "long_blend": (.8, .15),
        "short_xfade": (.3, .25),
        "bass_handoff": (.6, .20),
        "filter": (.4, .45),
        "echo_out": (.3, .50),
        "breakdown_drop": (.2, .80),
        "vocal_handoff": (.6, .30),
        "cut": (.0, .90),
    }
    needs_h, max_ed = table.get(style, (.5, .30))
    harmony_ok = 1.0 if harmonic >= needs_h else harmonic / max(needs_h, 1e-9)
    energy_ok = 1.0 if abs(energy_delta) <= max_ed else max_ed / max(abs(energy_delta), 1e-9)
    return _clamp01(.6 * harmony_ok + .4 * energy_ok)


def score_transition(*, bpm_a: float, bpm_b: float, beats_a, beats_b,
                     key_a: str, key_b: str,
                     energy_a: float, energy_b: float, energy_target: float,
                     qc: dict,
                     bass_energy_a: float | None = None,
                     bass_energy_b: float | None = None,
                     vocal_a: float | None = None,
                     vocal_b: float | None = None,
                     transition_start: float | None = None,
                     downbeats_a=None,
                     style: str | None = None,
                     stretch_ratio: float | None = None) -> dict:
    """Score one transition; every component in [0, 1], ``total`` weighted."""
    beats_a = np.asarray(beats_a, dtype=float)
    beats_b = np.asarray(beats_b, dtype=float)

    err = beat_alignment_error(beats_a, beats_b)
    drift = beat_drift_across_overlap(beats_a, beats_b)
    beat = _clamp01((1 - err / .10) * .5 + (1 - drift / .15) * .5)  # 100ms/150ms rails

    harm = _clamp01(1.0 - _harmonic_cost(key_a, key_b))
    bpm = _folded_bpm_pull(bpm_a, bpm_b)
    arc = _clamp01(1.0 - abs(energy_b - energy_target) / .25)

    # Spectral: low-end collision (framework §7).  Unknown → neutral.
    if bass_energy_a is None or bass_energy_b is None:
        spectral = .5
    else:
        collision = min(bass_energy_a, bass_energy_b)  # both low ends active
        spectral = _clamp01(1.0 - collision * 1.1)

    # Vocal collision (§8): two prominent vocals at once is a failure.
    if vocal_a is None or vocal_b is None:
        vocal = .5
    else:
        vocal = _clamp01(1.0 - min(vocal_a, vocal_b) * 1.2)

    phrase = _phrase_score(transition_start, downbeats_a, beats_a)
    style_fit = _style_fit(style, bpm_a, bpm_b, harm, energy_b - energy_a)

    penalty = 0.0
    penalty += 1.0 if qc.get("clipped_samples", 0) else 0.0
    penalty += _clamp01(qc.get("longest_silence_seconds", 0.0) / 2.0)
    penalty += _clamp01(max(0.0, -0.3 - qc.get("sample_peak_dbfs", -1.0)) / 6.0)
    if stretch_ratio is not None:
        penalty += _clamp01(abs(math.log(max(stretch_ratio, 1e-9)))
                            / abs(math.log(STRETCH_LIMIT)) * .5)
    qc_penalty = _clamp01(penalty)

    total = _clamp01(
        WEIGHTS["beat"] * beat + WEIGHTS["harmony"] * harm
        + WEIGHTS["bpm"] * bpm + WEIGHTS["arc"] * arc
        + WEIGHTS["spectral"] * spectral + WEIGHTS["vocal"] * vocal
        + WEIGHTS["phrase"] * phrase + WEIGHTS["style"] * style_fit
        + WEIGHTS["qc"] * (1.0 - qc_penalty)
    )
    return {"total": total, "beat_alignment": beat, "harmony": harm,
            "bpm_pull": bpm, "energy_arc": arc, "spectral": spectral,
            "vocal": vocal, "phrase": phrase, "style": style_fit,
            "qc_penalty": qc_penalty,
            "components": {"weights": dict(WEIGHTS),
                           "beat_offset_seconds": err,
                           "beat_drift_seconds": drift}}


def quality_gates(*, qc: dict, stretch_ratio: float | None = None,
                  beat_drift_seconds: float | None = None) -> dict:
    """Hard pass/fail gates (framework §14).  Separate from soft scores:
    a transition can score .6 and still be unusable because it clips."""
    clipped = int(qc.get("clipped_samples", 0) or 0)
    silence = float(qc.get("longest_silence_seconds", 0.0) or 0.0)
    peak = float(qc.get("sample_peak_dbfs", -1.0))
    gates = {
        "no_clipping": clipped == 0,
        "no_unexpected_silence": silence < 2.0,
        "true_peak_below_zero": peak < 0.0,
        "stretch_within_limit": (stretch_ratio is None
                                 or abs(math.log(max(stretch_ratio, 1e-9)))
                                 <= abs(math.log(STRETCH_LIMIT)) + 1e-9),
        "beat_drift_below_threshold": (beat_drift_seconds is None
                                       or beat_drift_seconds < .15),
    }
    return {"pass": all(gates.values()), "gates": gates}
