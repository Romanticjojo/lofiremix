import numpy as np
import pytest

from dj_agent.transition_score import (
    beat_alignment_error,
    beat_drift_across_overlap,
    quality_gates,
    score_transition,
)


def _beats(bpm, n=64, start=0.0):
    return np.arange(n) * 60.0 / bpm + start


def test_perfect_alignment_is_zero():
    a = _beats(120)
    b = _beats(120)
    assert beat_alignment_error(a, b) == pytest.approx(0.0, abs=1e-9)


def test_half_beat_offset_is_max():
    a = _beats(120)
    b = _beats(120, start=0.25)  # half of 0.5s beat
    err = beat_alignment_error(a, b)
    assert 0.2 < err <= 0.25 + 1e-9


def test_beat_drift_accumulates():
    # B slightly slower: drift grows across the overlap window
    a = _beats(120, n=64)
    b = _beats(119.4, n=64)
    drift = beat_drift_across_overlap(a, b)
    assert drift > beat_alignment_error(a, b)


def _qc(**over):
    qc = {"clipped_samples": 0, "longest_silence_seconds": 0.0,
          "sample_peak_dbfs": -1.0, "rms_dbfs": -18.0}
    qc.update(over)
    return qc


def test_score_transition_bounds_and_keys():
    out = score_transition(
        bpm_a=120.0, bpm_b=120.0, beats_a=_beats(120), beats_b=_beats(120),
        key_a="C major", key_b="C major",
        energy_a=.5, energy_b=.5, energy_target=.5,
        qc=_qc(),
    )
    assert set(out) >= {"total", "beat_alignment", "harmony", "bpm_pull",
                        "energy_arc", "spectral", "vocal", "phrase", "style",
                        "qc_penalty", "components"}
    assert 0.0 <= out["total"] <= 1.0
    assert out["harmony"] == pytest.approx(1.0)


def test_half_double_time_folding():
    # 120 vs 60 BPM: same beat grid when folded — bpm_pull should be high
    out = score_transition(
        bpm_a=120.0, bpm_b=60.0, beats_a=_beats(120), beats_b=_beats(60),
        key_a="C major", key_b="C major",
        energy_a=.5, energy_b=.5, energy_target=.5,
        qc=_qc(),
    )
    assert out["bpm_pull"] > 0.9


def test_bass_collision_lowers_spectral():
    base = dict(bpm_a=124.0, bpm_b=124.0, beats_a=_beats(124), beats_b=_beats(124),
                key_a="A minor", key_b="A minor",
                energy_a=.6, energy_b=.6, energy_target=.6, qc=_qc())
    clean = score_transition(**base, bass_energy_a=.2, bass_energy_b=.3)
    clash = score_transition(**base, bass_energy_a=.9, bass_energy_b=.9)
    assert clash["spectral"] < clean["spectral"]
    assert clash["total"] < clean["total"]


def test_vocal_collision_penalized():
    base = dict(bpm_a=124.0, bpm_b=124.0, beats_a=_beats(124), beats_b=_beats(124),
                key_a="A minor", key_b="A minor",
                energy_a=.6, energy_b=.6, energy_target=.6, qc=_qc())
    quiet = score_transition(**base, vocal_a=.1, vocal_b=.1)
    colliding = score_transition(**base, vocal_a=.9, vocal_b=.9)
    assert colliding["vocal"] < quiet["vocal"]


def test_phrase_boundary_bonus():
    base = dict(bpm_a=124.0, bpm_b=124.0, beats_a=_beats(124), beats_b=_beats(124),
                key_a="A minor", key_b="A minor",
                energy_a=.6, energy_b=.6, energy_target=.6, qc=_qc())
    on_grid = score_transition(**base, transition_start=16 * 60 / 124)
    off_grid = score_transition(**base, transition_start=16 * 60 / 124 + .31)
    assert on_grid["phrase"] > off_grid["phrase"]


def test_quality_gates_hard_fail():
    gates = quality_gates(qc=_qc(clipped_samples=500))
    assert gates["pass"] is False
    assert any(not v for v in gates["gates"].values())

    gates_ok = quality_gates(qc=_qc())
    assert gates_ok["pass"] is True


def test_quality_gates_stretch_limit():
    gates = quality_gates(qc=_qc(), stretch_ratio=1.09)
    assert gates["pass"] is False
    assert gates["gates"]["stretch_within_limit"] is False
