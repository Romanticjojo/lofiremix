import pytest

from dj_agent.lookahead import beam_search_next, plan_set_lookahead, TrackLike


def _track(i, bpm, key, energy):
    return TrackLike(id=f"t{i}", bpm=bpm, key=key, energy=energy,
                     duration=180.0, title=f"T{i}")


TRACKS = [_track(0, 120, "C major", .5), _track(1, 121, "C major", .5),
          _track(2, 90, "F# minor", .9), _track(3, 122, "G major", .45)]


def test_beam_prefers_harmonic_tempo_close():
    chosen = beam_search_next(current=TRACKS[0], available=TRACKS[1:],
                              remaining=3, energy_target=.5)
    assert chosen.id == "t1"


def test_beam_search_never_revisits():
    chosen = beam_search_next(current=TRACKS[1], available=TRACKS,
                              remaining=2, energy_target=.5)
    assert chosen.id != "t1"


def test_beam_respects_custom_score_fn():
    # Inverted scorer: harmony closeness is punished → picks distant key
    def bad(cur, nxt, target):
        from dj_agent.planner import _harmonic_cost
        return _harmonic_cost(cur.key, nxt.key)
    chosen = beam_search_next(current=TRACKS[0], available=TRACKS[1:],
                              remaining=2, energy_target=.5, score_fn=bad)
    assert chosen.id == "t2"


def test_plan_set_lookahead_returns_full_plan():
    plan = plan_set_lookahead(TRACKS + [_track(4, 120, "C major", .55),
                                        _track(5, 119, "C major", .48)],
                              max_tracks=6, target_minutes=15)
    ids = [t["track_id"] for t in plan["tracks"]]
    assert len(ids) == 6 and len(set(ids)) == 6
    assert all("score" in t or "selection" in t for t in plan["tracks"])
