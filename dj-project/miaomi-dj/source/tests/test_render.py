import numpy as np
import pytest
import soundfile as sf

from dj_agent.render import crossfade, render_set


def test_crossfade_keeps_stereo_and_duration():
    a = np.full((48000, 2), .1, dtype=np.float32)
    b = np.full((48000, 2), .1, dtype=np.float32)
    mixed = crossfade(a, b, 12000, "baseline", 48000)
    assert mixed.shape == (84000, 2)
    assert np.max(np.abs(mixed)) < .15
    np.testing.assert_array_equal(mixed[:30000], a[:30000])
    np.testing.assert_array_equal(mixed[-30000:], b[-30000:])


@pytest.mark.parametrize('variant', ['baseline', 'enhanced'])
def test_vocal_handoff_removes_weighted_outgoing_stem_only(monkeypatch, variant):
    from dj_agent import render, vocals
    sr = 8000
    a = np.full((sr*8, 2), .1, dtype=np.float32)
    b = np.zeros_like(a)
    b[sr:] = .2
    n = sr*2
    mixed = crossfade(a, b, n, variant, sr)
    original = mixed.copy()
    # Synthetic signals consist only of known vocals; replace expensive neural inference only.
    monkeypatch.setattr(vocals, 'extract_vocals', lambda audio, sample_rate: audio.copy())
    control = render.apply_transition_vocal_handoff(mixed, len(a), a, b, n, sr, variant)
    assert control['applied'] is True
    assert control['exit_output_seconds'] == pytest.approx(6.85, abs=.05)
    incoming_only = crossfade(np.zeros_like(a), b, n, variant, sr)
    np.testing.assert_allclose(mixed[7*sr:8*sr], incoming_only[7*sr:8*sr], atol=2e-7)
    np.testing.assert_array_equal(mixed[:6*sr], original[:6*sr])
    np.testing.assert_array_equal(mixed[8*sr:], original[8*sr:])


def test_vocal_handoff_skips_when_no_incoming_voice(monkeypatch):
    from dj_agent import render, vocals
    sr = 8000
    a = np.full((sr*6, 2), .1, dtype=np.float32)
    mixed = crossfade(a, a, sr, 'baseline', sr)
    original = mixed.copy()
    monkeypatch.setattr(vocals, 'extract_vocals', lambda audio, sample_rate: np.zeros_like(audio))
    control = render.apply_transition_vocal_handoff(mixed, len(a), a, a, sr, sr, 'baseline')
    assert control['applied'] is False
    np.testing.assert_array_equal(mixed, original)


def test_enhanced_avoids_doubling_identical_bass():
    sr = 48000
    t = np.arange(sr*2) / sr
    bass = np.column_stack([.2*np.sin(2*np.pi*60*t)]*2).astype(np.float32)
    output = crossfade(bass, bass, sr, "enhanced", sr)
    assert output.shape == (sr*3, 2)
    assert np.max(np.abs(output)) < .24
    rms = np.sqrt(np.mean(output[sr:2*sr]**2))
    assert .08 < rms < .17


@pytest.mark.parametrize("overlap", [0, -1, 101])
def test_invalid_overlap_rejected(overlap):
    with pytest.raises(ValueError):
        crossfade(np.zeros((100, 2)), np.zeros((100, 2)), overlap)


def test_render_real_files_stretch_and_report(tmp_path):
    sr = 24000
    t = np.arange(sr*10)/sr
    paths = []
    for i in range(2):
        path = tmp_path / f"source-{i}.wav"
        # Different L/R frequencies catch mono conversion and incorrect pitch shift.
        signal = np.column_stack([.1*np.sin(2*np.pi*440*t), .1*np.sin(2*np.pi*660*t)])
        sf.write(path, signal, sr)
        paths.append(path)
    plan = {"schema_version": 1, "requested_minutes": .25, "source_kind": "synthetic-test-only",
            "tracks": [{"track_id": str(i), "path": str(path), "title": str(i), "artist": "fixture",
                        "source_start": 0., "source_end": 10., "rate": 1.05, "bpm": 126.,
                        "output_start": 0 if i == 0 else 10/1.05-2} for i, path in enumerate(paths)],
            "transitions": [{"id": "transition-01", "from_id": "0", "to_id": "1",
                             "mode": "eq_blend", "overlap_seconds": 2., "output_start": 10/1.05-2}],
            "duration_seconds": 20/1.05-2, "warnings": []}
    result = render_set(plan, tmp_path / "output")
    audio, sr_out = sf.read(result["master_path"])
    assert sr_out == 48000
    assert audio.shape[1] == 2
    assert len(audio)/sr_out == pytest.approx(20/1.05-2, abs=.01)
    assert np.max(np.abs(audio)) < .9
    assert result["metrics"]["clipped_samples"] == 0
    assert result["metrics"]["longest_silence_seconds"] < .1
    spectrum = np.abs(np.fft.rfft(audio[48000:144000, 0]))
    assert np.argmax(spectrum)*48000/96000 == pytest.approx(440, abs=2)
    assert (tmp_path / "output" / "report.html").exists()
    assert (tmp_path / "output" / "plan.json").exists()


def test_render_rejects_source_overrun(tmp_path):
    path = tmp_path / "short.wav"
    sf.write(path, np.ones((48000, 2))*.1, 48000)
    plan = {"tracks": [{"path": str(path), "source_start": 0, "source_end": 10, "rate": 1}],
            "transitions": []}
    with pytest.raises(ValueError, match="越界|至少"):
        render_set(plan, tmp_path / "out")


def test_never_overwrites_a_source_in_output_directory(tmp_path):
    source = tmp_path / "transition-01.wav"
    sf.write(source, np.ones((48000*12, 2))*.1, 48000)
    original = source.read_bytes()
    tracks = [{"track_id": str(i), "path": str(source), "title": "fixture", "source_start": 0.,
               "source_end": 12., "rate": 1., "bpm": 120., "output_start": 0 if i == 0 else 10}
              for i in range(2)]
    plan = {"tracks": tracks, "transitions": [{"id": "transition-01", "output_start": 10.,
                                               "overlap_seconds": 2., "mode": "eq_blend"}],
            "duration_seconds": 22., "warnings": []}
    with pytest.raises(ValueError, match="空|已有"):
        render_set(plan, tmp_path)
    assert source.read_bytes() == original


def test_transition_names_cannot_escape_or_replace_master(tmp_path):
    for unsafe in ("../source", "master", "transition-01/../../source"):
        plan = {"tracks": [{}, {}], "transitions": [{"id": unsafe}]}
        with pytest.raises(ValueError, match="标识"):
            render_set(plan, tmp_path / "out")


def test_nonfinite_plan_rejected_before_creating_any_files(tmp_path):
    for field in ("output_start", "duration_seconds"):
        plan = {"tracks": [{}, {}], "transitions": [{"id": "transition-01", "output_start": 1.,
                                                     "overlap_seconds": 2.}], "duration_seconds": 10.}
        if field == "output_start":
            plan["transitions"][0][field] = float("nan")
        else:
            plan[field] = float("nan")
        with pytest.raises(ValueError, match="时间"):
            render_set(plan, tmp_path / "out")
        assert not (tmp_path / "out").exists()
