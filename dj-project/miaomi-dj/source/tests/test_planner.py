import math

import pytest

from dj_agent.planner import plan_set


def test_rejects_empty_set():
    with pytest.raises(ValueError, match="至少"):
        plan_set([], 8, 25)


def test_unique_tracks_source_bounds_and_overlap_duration(track_factory):
    tracks = [track_factory(str(i), bpm=118+i*2, energy=.2+i*.1) for i in range(5)]
    plan = plan_set(tracks, 5, 10)
    assert len({p["track_id"] for p in plan["tracks"]}) == len(plan["tracks"])
    assert len(plan["transitions"]) == len(plan["tracks"]) - 1
    for item in plan["tracks"]:
        assert 0 <= item["source_start"] < item["source_end"] <= 180
        assert .92 <= item["rate"] <= 1.08
    expected = sum((p["source_end"]-p["source_start"])/p["rate"] for p in plan["tracks"])
    expected -= sum(t["overlap_seconds"] for t in plan["transitions"])
    assert plan["duration_seconds"] == pytest.approx(expected)
    for t in plan["transitions"]:
        assert t["overlap_seconds"] > 0
        assert t["output_start"] >= 0


def test_incompatible_tempos_use_conservative_handoff(track_factory):
    plan = plan_set([track_factory("slow", 80), track_factory("fast", 130)], 2, 10)
    assert plan["transitions"][0]["mode"] == "short_fade"
    assert plan["transitions"][0]["overlap_seconds"] <= 4
    assert all(p["rate"] == 1 for p in plan["tracks"])
    assert plan["warnings"]  # Not enough material for requested duration.


def test_rejects_nonfinite_or_too_short_tracks(track_factory):
    bad = track_factory("bad")
    bad.bpm = math.nan
    with pytest.raises(ValueError):
        plan_set([bad, track_factory("other")], 2, 25)
    with pytest.raises(ValueError):
        plan_set([track_factory("tiny", duration=2), track_factory("other")], 2, 25)


def test_transition_start_is_on_both_actual_beat_grids(track_factory):
    a = track_factory("a", 120, energy=.1)
    b = track_factory("b", 124, energy=.9)
    # Real audio commonly has a nonzero initial downbeat and detector tempo rounding.
    a.beats = [i*.503+.13 for i in range(357)]
    a.downbeats = a.beats[::4]
    for i, segment in enumerate(a.segments):
        segment["time"] = a.beats[min(i*16, len(a.beats)-1)]
    b.beats = [v+.27 for v in b.beats if v+.27 < b.duration]
    b.downbeats = b.beats[::4]
    for segment in b.segments:
        segment["time"] += .27
    plan = plan_set([a, b], 2, 10)
    left, right = plan["tracks"]
    transition = plan["transitions"][0]
    first_source_beat = left["source_end"]-transition["overlap_seconds"]*left["rate"]
    assert min(abs(first_source_beat-beat) for beat in a.beats) < .002
    assert min(abs(right["source_start"]-beat) for beat in b.beats) < .002
    assert transition["phase_aligned"] is True


def test_uncertain_downbeat_analysis_gets_short_handoff(track_factory):
    a, b = track_factory("a"), track_factory("b")
    b.beat_confidence = "estimated"
    plan = plan_set([a, b], 2, 10)
    assert plan["transitions"][0]["mode"] == "short_fade"
    assert plan["transitions"][0]["overlap_seconds"] <= 3


def test_local_tempo_correction_propagates_to_next_transition(track_factory):
    a = track_factory("a", 120, energy=.1)
    b = track_factory("b", 126, energy=.5)
    b.bpm = 124  # A rounded metadata estimate differs from the actual beat grid.
    c = track_factory("c", 118, energy=.9)
    plan = plan_set([a, b, c], 3, 10)
    assert [t["track_id"] for t in plan["tracks"]] == ["a", "b", "c"]
    assert all(t["phase_aligned"] for t in plan["transitions"])


def test_reference_model_is_optional_bounded_and_audited(track_factory):
    from dj_agent.reference_choice import fit_choice
    tracks = [track_factory(str(i), bpm=120, energy=.2) for i in range(5)]
    catalog = [{'id': str(i), 'title': str(i), 'local_track_ids': [str(i)]} for i in range(5)]
    rows = [{'context': ['0'], 'candidates': ['1', '2', '3', '4'], 'label': '3',
             'provenance': {'source_video_id': 'source'}}]
    model = fit_choice(catalog, rows, context_length=1)
    baseline = plan_set(tracks, 5, 10)
    guided = plan_set(tracks, 5, 10, choice_model=model)
    assert guided['planner'] == 'bounded-rules-reference-assist-v1'
    assert len(guided['selection_audit']) == 4
    for step in guided['selection_audit']:
        for row in step['candidates']:
            assert 0 <= row['reference_penalty'] <= .2
        selected = min(step['candidates'], key=lambda row: (row['total_cost'], row['track_id']))
        assert selected['track_id'] == step['selected_track_id']
    assert baseline['planner'] == 'bounded-rules-v1'
    assert all(.92 <= t['rate'] <= 1.08 for t in guided['tracks'])
