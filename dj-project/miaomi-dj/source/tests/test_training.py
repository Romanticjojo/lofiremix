import json

import numpy as np
import pytest
from test_preferences import comparison_fixture

from dj_agent import preferences, training
from dj_agent.cli import main
from dj_agent.feedback import record_preference


def save_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def labeled_session(tmp_path):
    sessions = tmp_path / "sessions"
    session = sessions / "one"
    session.mkdir(parents=True)
    save_json(session / "comparison.json", comparison_fixture(session))
    record_preference(session, "transition-01", "A")
    identity = tmp_path / "identities.json"
    save_json(identity, {"schema_version": 1, "reviewed": True,
                         "songs": {"song-a": "canonical-a", "song-b": "canonical-b"}})
    return sessions, session, identity


def test_empty_real_dataset_does_not_write_a_model(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    out = tmp_path / "training"
    report = training.run_training(root, None, out)
    assert report["status"] == "needs_data"
    assert report["audit"]["accepted_pairs"] == 0
    assert not (out / "model.json").exists()
    assert (out / "report.json").exists()


def test_collects_only_audio_bound_human_labels_with_reviewed_identity(tmp_path):
    root, session, identity = labeled_session(tmp_path)
    rows, audit = training.collect_preferences(root, identity)
    assert len(rows) == audit["accepted_pairs"] == 1
    assert rows[0]["track_ids"] == ["canonical-a", "canonical-b"]
    manifest = json.loads((session / "comparison.json").read_text(encoding="utf-8"))
    preferred = rows[0]["a"] if rows[0]["target"] == 1 else rows[0]["b"]
    assert preferred == manifest["pairs"][0]["candidates"]["A"]["features"]
    record_preference(session, "transition-01", "A")
    repeated, audit = training.collect_preferences(root, identity)
    assert len(repeated) == 1  # repeated clicks must not increase a pair's training weight
    assert audit["feedback_records"] == 2
    record_preference(session, "transition-01", "B")
    conflicting, audit = training.collect_preferences(root, identity)
    assert not conflicting
    assert audit["rejected"]["conflicting_labels"] == 1


@pytest.mark.parametrize("mutation,reason", [
    ("audio", "changed_audio"), ("manifest", "changed_manifest"),
    ("synthetic", "non_real_audio"), ("unreviewed", "identity_not_reviewed"),
    ("missing_identity", "missing_identity"), ("same_song", "same_song"),
    ("neither", "neither"),
])
def test_bad_training_inputs_are_explained_not_silently_fitted(tmp_path, mutation, reason):
    root, session, identity = labeled_session(tmp_path)
    manifest_path = session / "comparison.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "audio":
        (session / manifest["pairs"][0]["candidates"]["A"]["audio"]).write_bytes(b"changed")
    elif mutation in ("manifest", "synthetic"):
        manifest["source_kind"] = "synthetic-engineering" if mutation == "synthetic" else "user-local-audio"
        save_json(manifest_path, manifest | {"changed": True})
        if mutation == "synthetic":
            (session / "feedback.jsonl").unlink()
            record_preference(session, "transition-01", "A")
    elif mutation in ("unreviewed", "missing_identity", "same_song"):
        mapping = json.loads(identity.read_text())
        if mutation == "unreviewed":
            mapping["reviewed"] = False
        elif mutation == "missing_identity":
            mapping["songs"].pop("song-b")
        else:
            mapping["songs"]["song-b"] = "canonical-a"
        save_json(identity, mapping)
    else:
        (session / "feedback.jsonl").unlink()
        record_preference(session, "transition-01", "neither")
    rows, audit = training.collect_preferences(root, identity)
    assert not rows
    assert audit["rejected"][reason] >= 1


def numeric_fixture(count=60):
    """Artificial preference rule, for numerical correctness only; never production data."""
    rows = []
    for i in range(count):
        a = dict.fromkeys(preferences.FEATURE_NAMES, 0.)
        b = a.copy()
        a["low_band_ratio"] = .1 if i % 2 else .9
        b["low_band_ratio"] = 1 - a["low_band_ratio"]
        rows.append({"example_id": f"fixture-{i}", "track_ids": [f"a-{i}", f"b-{i}"],
                     "a": a, "b": b, "target": float(i % 2),
                     "renderer_A": "baseline", "renderer_B": "enhanced"})
    return rows


def test_ranker_learns_controlled_signal_and_is_symmetric():
    rows = numeric_fixture()
    model = training.fit_ranker(rows[:40])
    for row in rows[40:]:
        p = training.predict_preference(model, row["a"], row["b"])
        reverse = training.predict_preference(model, row["b"], row["a"])
        assert p + reverse == pytest.approx(1)
        assert (p > .5) == bool(row["target"])
        assert abs(p - row["target"]) < .25
    assert training.predict_preference(model, rows[0]["a"], rows[0]["a"]) == pytest.approx(.5)


def test_train_only_scaling_and_song_disjoint_evaluation():
    rows = numeric_fixture()
    model, evaluation = training.evaluate_ranker(rows)
    train = evaluation["split"]["train_song_ids"]
    test = evaluation["split"]["test_song_ids"]
    assert not set(train) & set(test)
    assert evaluation["test"]["accuracy"] == 1.
    assert evaluation["test"]["log_loss"] < np.log(2)
    assert model["deployment_status"] == "experimental_not_enabled"
    assert evaluation["test"]["majority_baseline_accuracy"] <= .8
    assert len(model["weights"]) == len(preferences.FEATURE_NAMES)


def test_shared_songs_or_too_few_decisive_labels_block_model():
    rows = numeric_fixture()
    for row in rows:
        row["track_ids"][0] = "shared-song"
    with pytest.raises(ValueError, match="connected"):
        training.evaluate_ranker(rows)
    with pytest.raises(ValueError, match="insufficient"):
        training.evaluate_ranker(numeric_fixture(4))


def test_ranker_rejects_nonfinite_and_inactive_feature_extrapolation():
    rows = numeric_fixture()
    model = training.fit_ranker(rows)
    row = rows[0]
    altered = row["a"] | {"rms_dbfs": 1000000.}
    assert training.predict_preference(model, altered, row["b"]) == pytest.approx(
        training.predict_preference(model, row["a"], row["b"]))
    with pytest.raises(ValueError):
        training.predict_preference(model, row["a"] | {"rms_dbfs": float("nan")}, row["b"])


def test_train_cli_reports_shortage_and_never_overwrites(tmp_path, capsys):
    root = tmp_path / "sessions"
    root.mkdir()
    out = tmp_path / "run"
    assert main(["train", "--sessions", str(root), "--output", str(out)]) == 2
    assert json.loads((out / "report.json").read_text())["status"] == "needs_data"
    before = (out / "report.json").read_bytes()
    assert main(["train", "--sessions", str(root), "--output", str(out)]) == 2
    assert (out / "report.json").read_bytes() == before
    assert main(["train", "--sessions", str(root), "--output", str(tmp_path / "check"), "--check-only"]) == 0


def test_complete_pipeline_exports_reloadable_experimental_checkpoint(tmp_path):
    """Fictional listeners and tones confined to pytest temp files; no human training dataset."""
    from test_preferences import tone
    root = tmp_path / "sessions"
    songs = {}
    for index in range(60):
        session = root / str(index)
        for renderer, frequency in [("baseline", 90 + index), ("enhanced", 900 + index)]:
            (session / renderer).mkdir(parents=True)
            tone(session / renderer / "transition-01.wav", frequency)
        ids = [f"a-{index}", f"b-{index}"]
        songs.update({song: song for song in ids})
        manifest = preferences.build_comparison(session, {"source_kind": "user-local-audio", "transitions": [
            {"id": "transition-01", "from_id": ids[0], "to_id": ids[1]}]})
        save_json(session / "comparison.json", manifest)
        # Controlled artificial taste: prefer the treble tone, regardless of A/B position.
        chosen = next(label for label, candidate in manifest["pairs"][0]["candidates"].items()
                      if candidate["renderer"] == "enhanced")
        record_preference(session, "transition-01", chosen)
    identity = tmp_path / "identity.json"
    save_json(identity, {"schema_version": 1, "reviewed": True, "songs": songs})
    out = tmp_path / "model-test-only"
    report = training.run_training(root, identity, out)
    assert report["status"] == "trained_experimental"
    assert report["evaluation"]["test"]["accuracy"] == 1.
    model = json.loads((out / "model.json").read_text())
    rows = json.loads((out / "dataset.json").read_text())
    assert model["dataset_sha256"] == report["dataset_sha256"]
    assert model["deployment_status"] == "experimental_not_enabled"
    first = rows[0]
    assert (training.predict_preference(model, first["a"], first["b"]) > .5) == bool(first["target"])
