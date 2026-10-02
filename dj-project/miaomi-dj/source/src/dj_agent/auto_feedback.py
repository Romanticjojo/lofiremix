"""Synthetic preference labels from objective scores — append-only, full provenance.

Invariants:
- ``source`` is always ``"auto"`` here; human labels live in feedback.jsonl only
  and the audited human chain (training.collect_preferences) rejects these rows.
- A score gap below ``min_gap`` is a tie — never fabricate confidence.
- Every record carries scorer version + component snapshot so any later audit
  can reproduce the label from the renders.

This is the piece that removes the human from the loop (framework §13):
the signal is objective audio measurement, the format matches what the
existing pairwise ranker already consumes.
"""

from __future__ import annotations

import json
from pathlib import Path

SCORER_VERSION = 1


def label_from_scores(score_a: dict, score_b: dict, min_gap: float = .05) -> str:
    gap = score_a["total"] - score_b["total"]
    if gap > min_gap:
        return "A"
    if -gap > min_gap:
        return "B"
    return "tie"


def append_auto_label(session_dir: Path, record: dict) -> Path:
    if record.get("source") != "auto":
        raise ValueError("auto_feedback only writes source=auto records")
    if record.get("preference") not in ("A", "B", "tie"):
        raise ValueError("preference must be A, B, or tie")
    if record.get("scorer_version") != SCORER_VERSION:
        raise ValueError("stale scorer version")
    session = Path(session_dir)
    if not session.is_dir() or session.is_symlink():
        raise ValueError("session directory must be an existing real directory")
    path = session / "auto-feedback.jsonl"
    if path.is_symlink():
        raise ValueError("unsafe auto feedback path")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _render_and_score_pair(pair: dict) -> tuple[dict, dict]:
    """Render baseline vs enhanced for one pair, return (score_a, score_b).

    Loads the two tracks via analysis.analyze(), takes the planned cue
    overlap (planner._align_overlap), renders with render.crossfade for each
    variant plus transition_fx.process_exit, measures with
    evaluation.measure, and scores with transition_score.score_transition.
    Kept behind a seam so tests can monkeypatch the heavy audio path.
    """
    raise NotImplementedError("audio wiring lives in cli auto-label command")


def label_session_pairs(session_dir: Path, max_pairs: int = 50) -> int:
    session = Path(session_dir)
    manifest_path = session / "comparison.json"
    if not manifest_path.is_file():
        raise ValueError("no comparison manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    written = 0
    for pair in manifest.get("pairs", [])[:max_pairs]:
        score_a, score_b = _render_and_score_pair(pair)
        pref = label_from_scores(score_a, score_b)
        append_auto_label(session, {
            "pair_id": pair["id"], "preference": pref, "source": "auto",
            "scorer_version": SCORER_VERSION,
            "score_a": score_a["total"], "score_b": score_b["total"],
            "components_a": {k: score_a[k] for k in score_a if k != "total"},
            "components_b": {k: score_b[k] for k in score_b if k != "total"},
        })
        written += 1
    return written
