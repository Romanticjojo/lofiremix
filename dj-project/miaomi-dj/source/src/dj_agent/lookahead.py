"""Beam-search next-track selection over objective transition scores.

Framework §12: a DJ set is a sequence. Track B may be a slightly weaker
immediate match than track C, yet create a much better path toward the
desired musical direction over the next 20 minutes.  This module optimizes
whole-set reward instead of greedy per-step cost (the piece AI-DJ-Software's
sequencer and the DJ-MC MCTS paper both advocate).
"""

from __future__ import annotations

from dataclasses import dataclass

from .transition_score import score_transition

BEAM_WIDTH = 3
MAX_DEPTH = 4


@dataclass(frozen=True)
class TrackLike:
    """Minimal track view the planner needs (duck-typed: any object with
    these attribute names works — analysis.Track included)."""
    id: str
    bpm: float
    key: str
    energy: float
    duration: float
    title: str = ""


def default_score(cur, nxt, energy_target: float) -> float:
    """Objective score between two adjacent tracks (no audio render yet —
    beat grids assumed aligned at plan time, verified later at render time)."""
    return score_transition(
        bpm_a=cur.bpm, bpm_b=nxt.bpm, beats_a=[0.0, 0.5], beats_b=[0.0, 0.5],
        key_a=cur.key, key_b=nxt.key,
        energy_a=cur.energy, energy_b=nxt.energy,
        energy_target=energy_target,
        qc={"clipped_samples": 0, "longest_silence_seconds": 0.0,
            "sample_peak_dbfs": -1.0, "rms_dbfs": -18.0})["total"]


def beam_search_next(*, current, available, remaining: int, energy_target: float,
                     score_fn=None):
    """Pick the next track maximizing cumulative discounted future reward."""
    score = score_fn or default_score
    depth = max(1, min(remaining, MAX_DEPTH))

    frontier = [((current,), 0.0)]
    best_first, best_score = None, float("-inf")
    for _ in range(depth):
        nxt_frontier = []
        for path, cum in frontier:
            used = {t.id for t in path}
            for cand in available:
                if cand.id in used or cand.id == path[-1].id:
                    continue
                s = score(path[-1], cand, energy_target)
                nxt_frontier.append((path + (cand,), cum + s))
        if not nxt_frontier:
            break
        nxt_frontier.sort(key=lambda e: (-e[1], e[0][1].id))  # deterministic tie-break
        frontier = nxt_frontier[:BEAM_WIDTH]
        first, total = frontier[0][0][1], frontier[0][1]
        if total > best_score:
            best_first, best_score = first, total
    return best_first


def plan_set_lookahead(tracks, max_tracks: int = 10, target_minutes: float = 25,
                       *, beam_width: int = BEAM_WIDTH,
                       energy_arc=None, score_fn=None) -> dict:
    """Plan a whole set with beam search; returns a render-compatible plan dict.

    ``energy_arc`` maps position p∈[0,1] → target energy (default: the same
    .2→.9 rise the greedy planner uses, so results are directly comparable).
    """
    if len(tracks) < 2:
        raise ValueError("need at least two tracks")
    tracks = list({t.id: t for t in tracks}.values())
    count = min(max_tracks, len(tracks))

    if energy_arc is None:
        def _default_arc(p):
            return .2 + .7 * p
        energy_arc = _default_arc

    ranked = sorted(tracks, key=lambda t: (t.energy, t.id))
    opener = ranked[min(len(ranked) - 1, max(0, int(.2 * (len(ranked) - 1))))]
    ordered = [opener]
    audit = []
    for i in range(1, count):
        p = i / max(count - 1, 1)
        remaining = count - i
        chosen = beam_search_next(current=ordered[-1],
                                  available=[t for t in tracks if t.id not in
                                             {o.id for o in ordered}],
                                  remaining=remaining,
                                  energy_target=energy_arc(p),
                                  score_fn=score_fn)
        if chosen is None:
            break
        audit.append({"position": i, "energy_target": energy_arc(p),
                      "selected": chosen.id,
                      "score": default_score(ordered[-1], chosen, energy_arc(p))})
        ordered.append(chosen)

    import math
    per_track = target_minutes * 60 / max(len(ordered), 1)
    plan_tracks = []
    for i, t in enumerate(ordered):
        plan_tracks.append({
            "track_id": t.id, "path": getattr(t, "path", f"{t.id}.wav"),
            "title": getattr(t, "title", t.id), "artist": getattr(t, "artist", ""),
            "source_start": 0.0, "source_end": min(getattr(t, "duration", per_track), per_track),
            "rate": 1.0, "bpm": t.bpm, "key": t.key, "energy": t.energy,
            "selection": {"method": "lookahead_beam",
                          "depth": min(count - i, MAX_DEPTH), "beam": beam_width},
        })
    return {"schema_version": 1, "requested_minutes": target_minutes,
            "source_kind": "lookahead-plan",
            "tracks": plan_tracks, "lookahead_audit": audit,
            "planner": "beam_search", "beam_width": beam_width,
            "total_score": round(sum(a["score"] for a in audit), 4)
            if audit else 0.0}
