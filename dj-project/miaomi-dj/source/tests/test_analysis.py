import numpy as np
import pytest
import soundfile as sf

from dj_agent.analysis import _tempo_from_beats, analyze


def test_corrupt_audio_reports_actionable_error(tmp_path):
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"this is not audio")
    with pytest.raises(ValueError, match="解码|音频"):
        analyze(path, tmp_path / "cache", backend="librosa")


def test_silence_is_not_accepted_as_dj_track(tmp_path):
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(22050 * 12), 22050)
    with pytest.raises(ValueError, match="静音"):
        analyze(path, tmp_path / "cache", backend="librosa")


def test_detects_known_pulse_and_invalidates_changed_source(tmp_path):
    sr = 22050
    y = np.zeros(sr * 32)
    for start in np.arange(.5, 31.5, .5):
        n = int(start * sr)
        pulse = np.sin(2 * np.pi * 1000 * np.arange(500) / sr) * np.exp(-np.arange(500) / 90)
        y[n:n + len(pulse)] += pulse * .5
    path = tmp_path / "节拍 120.wav"
    sf.write(path, y, sr)
    a = analyze(path, tmp_path / "cache", backend="librosa")
    assert 117 < a.bpm < 123
    assert len(a.beats) > 50
    assert a.duration == pytest.approx(32, abs=.02)
    assert a.path == str(path.resolve())
    assert a.analysis_backend == "librosa"
    assert a.warnings  # Traditional analysis must not claim learned downbeat certainty.
    y[100] = .3
    sf.write(path, y, sr)
    b = analyze(path, tmp_path / "cache", backend="librosa")
    assert a.source_hash != b.source_hash
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2


def test_neural_frame_quantization_does_not_round_118_bpm_to_120():
    beats = np.round(np.arange(100)*60/118/.02)*.02
    assert _tempo_from_beats(beats) == pytest.approx(118, abs=.2)
