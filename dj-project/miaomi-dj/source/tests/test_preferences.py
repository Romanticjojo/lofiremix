import hashlib
import json

import numpy as np
import pytest
import soundfile as sf

from dj_agent import preferences
from dj_agent.feedback import record_preference


def tone(path, frequency=100, gain=.1):
    time = np.arange(16000) / 8000
    mono = gain * np.sin(2 * np.pi * frequency * time)
    sf.write(path, np.column_stack([mono, mono]), 8000, subtype="FLOAT")


def test_features_measure_bass_without_using_file_name(tmp_path):
    bass, treble = tmp_path / "a.wav", tmp_path / "b.wav"
    tone(bass)
    tone(treble, 1000)
    a, b = preferences.clip_features(bass), preferences.clip_features(treble)
    assert a["low_band_ratio"] > .9
    assert b["low_band_ratio"] < .01
    assert a["rms_dbfs"] == pytest.approx(-23.0103, abs=.01)
    assert a["crest_db"] == pytest.approx(3.0103, abs=.01)
    assert a["side_energy_ratio"] == pytest.approx(0)
    assert a["silence_fraction"] == 0


@pytest.mark.parametrize("audio", [np.zeros((0, 2)), np.full((16000, 2), np.nan)])
def test_invalid_clip_cannot_become_training_features(tmp_path, audio):
    path = tmp_path / "bad.wav"
    sf.write(path, audio, 8000, subtype="FLOAT")
    with pytest.raises(ValueError):
        preferences.clip_features(path)


def comparison_fixture(tmp_path):
    for folder, hz in [("baseline", 100), ("enhanced", 1000)]:
        (tmp_path / folder).mkdir()
        tone(tmp_path / folder / "transition-01.wav", hz)
    plan = {"source_kind": "user-local-audio", "transitions": [
        {"id": "transition-01", "from_id": "song-a", "to_id": "song-b"}]}
    return preferences.build_comparison(tmp_path, plan)


def test_comparison_binds_actual_audio_and_feedback(tmp_path):
    manifest = comparison_fixture(tmp_path)
    assert manifest["schema_version"] == 2
    pair = manifest["pairs"][0]
    assert {pair["candidates"][v]["renderer"] for v in ("A", "B")} == {"baseline", "enhanced"}
    for label in ("A", "B"):
        candidate = pair["candidates"][label]
        clip = tmp_path / candidate["audio"]
        assert candidate["sha256"] == hashlib.sha256(clip.read_bytes()).hexdigest()
        assert candidate["features"] == preferences.clip_features(clip)
        assert pair[label] == candidate["renderer"] + "/report.html"
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    feedback = record_preference(tmp_path, "transition-01", "A")
    record = json.loads(feedback.read_text(encoding="utf-8"))
    assert record["comparison_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_comparison_cannot_read_outside_session(tmp_path):
    plan = {"transitions": [{"id": "../outside", "from_id": "a", "to_id": "b"}]}
    with pytest.raises(ValueError):
        preferences.build_comparison(tmp_path, plan)
