import json
from pathlib import Path

import pytest

from dj_agent.feedback import record_preference, split_by_song_identity


def make_session(tmp_path: Path) -> Path:
    session = tmp_path / "session"
    session.mkdir()
    (session / "comparison.json").write_text(
        json.dumps({"pairs": [{"id": "pair_01", "track_ids": ["song-b", "song-a"],
                               "A": "baseline/report.html", "B": "enhanced/report.html"}]}),
        encoding="utf-8",
    )
    return session


def test_records_repeated_human_preference_without_replacing_first(tmp_path):
    session = make_session(tmp_path)
    feedback_path = record_preference(session, "pair_01", "A")
    assert feedback_path == session / "feedback.jsonl"
    first = feedback_path.read_text(encoding="utf-8")
    assert first.endswith("\n")
    record_preference(session, "pair_01", "tie")
    lines = feedback_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == first.strip()
    assert len(lines) == 2
    first_record, second_record = (json.loads(line) for line in lines)
    assert first_record["pair_id"] == "pair_01"
    assert first_record["preference"] == "A"
    assert second_record["preference"] == "tie"
    assert first_record["source"] == second_record["source"] == "human"
    assert first_record["track_ids"] == ["song-a", "song-b"]
    assert first_record["group_key"] == second_record["group_key"]
    assert first_record["timestamp_utc"].endswith("Z")


@pytest.mark.parametrize("preference", ["", "a", "C", "both", "A\n{}", "../A", None])
def test_invalid_label_does_not_change_prior_feedback(tmp_path, preference):
    session = make_session(tmp_path)
    path = record_preference(session, "pair_01", "B")
    prior = path.read_bytes()
    with pytest.raises(ValueError):
        record_preference(session, "pair_01", preference)
    assert path.read_bytes() == prior


@pytest.mark.parametrize("pair_id", ["missing", "../pair_01", "pair_01/else", "pair_01\n{}"])
def test_unknown_or_malicious_pair_does_not_change_prior_feedback(tmp_path, pair_id):
    session = make_session(tmp_path)
    path = record_preference(session, "pair_01", "B")
    prior = path.read_bytes()
    with pytest.raises(ValueError):
        record_preference(session, pair_id, "A")
    assert path.read_bytes() == prior


def test_split_keeps_every_song_in_one_fold_even_across_pairs():
    rows = [
        {"pair_id": "ab", "track_ids": ["a", "b"]},
        {"pair_id": "bc", "track_ids": ["b", "c"]},
        {"pair_id": "de", "track_ids": ["d", "e"]},
    ]
    train, test = split_by_song_identity(rows, test_fraction=0.34, seed=7)
    assert len(train) + len(test) == len(rows)
    assert {id(row) for row in train + test} == {id(row) for row in rows}
    train_songs = {song for row in train for song in row["track_ids"]}
    test_songs = {song for row in test for song in row["track_ids"]}
    assert not train_songs.intersection(test_songs)
    assert bool(train) and bool(test)


def test_split_all_interconnected_pairs_cannot_make_independent_test_set():
    rows = [{"track_ids": ["a", "b"]}, {"track_ids": ["b", "c"]}]
    with pytest.raises(ValueError, match="independent"):
        split_by_song_identity(rows)


def test_collect_auto_preferences_reads_only_auto_file(tmp_path):
    from dj_agent.training import collect_auto_preferences
    (tmp_path / "auto-feedback.jsonl").write_text(
        '{"pair_id":"p1","preference":"A","source":"auto",'
        '"scorer_version":1,"score_a":0.9,"score_b":0.4}\n'
        '{"pair_id":"p2","preference":"tie","source":"auto",'
        '"scorer_version":1,"score_a":0.5,"score_b":0.5}\n')
    rows, audit = collect_auto_preferences(tmp_path)
    assert len(rows) == 1 and rows[0]["target"] == 1.0
    assert audit["source"] == "auto" and audit["ties_skipped"] == 1


def test_collect_auto_rejects_human_rows(tmp_path):
    from dj_agent.training import collect_auto_preferences
    (tmp_path / "auto-feedback.jsonl").write_text(
        '{"pair_id":"p1","preference":"A","source":"human"}\n')
    import pytest
    with pytest.raises(ValueError):
        collect_auto_preferences(tmp_path)


def test_agreement_report_concordance():
    from dj_agent.training import agreement_report
    human = [{"pair_id": "p1", "target": 1.0}, {"pair_id": "p2", "target": 0.0}]
    auto = [{"pair_id": "p1", "target": 1.0}, {"pair_id": "p2", "target": 1.0}]
    rep = agreement_report(human, auto)
    assert rep["common_pairs"] == 2
    assert rep["concordant"] == 1 and rep["discordant"] == 1
    assert -1.0 <= rep["tau"] <= 1.0
