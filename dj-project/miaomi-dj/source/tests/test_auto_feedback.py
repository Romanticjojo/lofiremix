import json

import pytest

from dj_agent.auto_feedback import append_auto_label, label_from_scores


def test_label_from_scores_gap():
    a = {"total": .9}; b = {"total": .5}
    assert label_from_scores(a, b, min_gap=.1) == "A"
    assert label_from_scores(b, a, min_gap=.1) == "B"
    assert label_from_scores(a, dict(total=.88), min_gap=.1) == "tie"


def test_append_auto_label_roundtrip(tmp_path):
    rec = {"pair_id": "p1", "preference": "A", "source": "auto",
           "scorer_version": 1, "score_a": .9, "score_b": .5}
    path = append_auto_label(tmp_path, rec)
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert rows == [rec]


def test_append_rejects_human_source(tmp_path):
    with pytest.raises(ValueError):
        append_auto_label(tmp_path, {"pair_id": "p1", "preference": "A",
                                     "source": "human"})


def test_append_rejects_bad_preference(tmp_path):
    with pytest.raises(ValueError):
        append_auto_label(tmp_path, {"pair_id": "p1", "preference": "X",
                                     "source": "auto"})


def test_label_session_pairs_produces_one_row_per_pair(tmp_path, monkeypatch):
    from dj_agent import auto_feedback as af

    (tmp_path / "comparison.json").write_text(json.dumps({
        "schema_version": 2,
        "pairs": [{"id": "p1", "tracks": ["tA", "tB"]},
                  {"id": "p2", "tracks": ["tC", "tD"]}]}))
    calls = []

    def fake_render_score(pair):
        calls.append(pair)
        sa = {"total": .9, "beat_alignment": 1, "harmony": 1, "bpm_pull": 1,
              "energy_arc": 1, "qc_penalty": 0}
        sb = dict(sa, total=.4)
        return sa, sb

    monkeypatch.setattr(af, "_render_and_score_pair", fake_render_score)
    n = af.label_session_pairs(tmp_path, max_pairs=10)
    assert n == 2 and len(calls) == 2
    rows = [json.loads(l) for l in (tmp_path / "auto-feedback.jsonl").read_text().splitlines() if l.strip()]
    assert [r["preference"] for r in rows] == ["A", "A"]
    assert all(r["source"] == "auto" for r in rows)


def test_label_session_pairs_respects_cap(tmp_path, monkeypatch):
    from dj_agent import auto_feedback as af

    (tmp_path / "comparison.json").write_text(json.dumps({
        "pairs": [{"id": f"p{i}", "tracks": ["a", "b"]} for i in range(10)]}))
    monkeypatch.setattr(af, "_render_and_score_pair",
                        lambda p: ({"total": .9}, {"total": .1}))
    assert af.label_session_pairs(tmp_path, max_pairs=3) == 3


def test_label_session_pairs_requires_manifest(tmp_path):
    from dj_agent.auto_feedback import label_session_pairs
    with pytest.raises(ValueError):
        label_session_pairs(tmp_path)
