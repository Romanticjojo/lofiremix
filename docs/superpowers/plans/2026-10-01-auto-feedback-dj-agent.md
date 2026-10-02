# Auto-Feedback DJ Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the human-label bottleneck: generate synthetic A/B preference labels from objective audio metrics + music-theory scoring, train the existing pairwise ranker on them, and add a lookahead tree-search planner — so the agent improves itself without anyone clicking A/B.

> **v2 revision (2026-10-01):** incorporates the engineering framework in `docs/references/what-makes-a-good-dj-transition.md` (What Makes a Good DJ Transition). Deltas vs v1: scorer expanded from 5 to 9 components (spectral/bass collision, vocal collision, phrase boundary, style fit, beat-drift-across-overlap); half/double-time BPM folding; hard quality gates separated from soft scores (framework §14); scoring philosophy locked to framework §15 — "teach it what the music is doing". Framework's recommended pipeline (§13: analysis → candidates → reference prior → constraints → lookahead → style selection → DSP → QA) maps 1:1 onto existing modules + Tasks 1–6.

**Architecture:** Keep the audited human-label chain untouched (`training.py` refuses `source != "human"` — that integrity stays). Add a parallel auto-label path: a `transition_score` module scores any rendered transition (beat-grid alignment, harmonic/Camelot distance, energy-arc fit, loudness/QC penalties), an `auto_feedback` module writes synthetic labels to a separate `auto-feedback.jsonl` with `source: "auto"` + full audit provenance, a `fit_auto_ranker` reuses the existing pairwise logistic, and a lookahead beam-search planner (`plan_set_lookahead`) optimizes whole-set reward. Reference points: ElMoorish/AI-DJ-Software sequencer (Camelot + BPM + energy arcs + lookahead tree), DJ-MC MCTS playlist paper (song + transition utility decomposition).

**Tech Stack:** Python 3.12, numpy/scipy (already deps), librosa (beat grid via existing `analysis.py`), pytest, uv.

---

## Background for the engineer (zero context assumed)

- Repo: `~/LLM_work/lofiremix`, package at `dj-project/miaomi-dj/source/` (uv project, `src/dj_agent/`).
- Current feedback loop: a human listens to A/B renders in `listening_server.py`, labels land in `feedback.jsonl` / `structured-feedback.jsonl`, and `training.collect_preferences()` **hard-rejects** anything not `source: "human"` (`_bound_row` raises `non_human_label`). `fit_ranker()` trains a pairwise logistic on those.
- `evaluation.py measure()` already computes objective QC: peak/true-peak dBFS, RMS, clipped samples, longest silence.
- `planner.py` has `_harmonic_cost(a, b)` (Camelot-style) and `_cue()`. `analysis.analyze()` produces BPM, beat times, key, energy.
- Nothing above changes semantics for human data; everything new lands beside it.

## File Structure

```
src/dj_agent/
├── transition_score.py    # NEW — objective per-transition scorer (beat alignment, harmony, energy arc, QC penalties)
├── auto_feedback.py       # NEW — synthetic label generation + auto-feedback.jsonl append/validate
├── lookahead.py           # NEW — beam-search set planner over transition scores
├── training.py            # MODIFY — add collect_auto_preferences() + fit path that never mixes sources
├── planner.py             # MODIFY — expose plan_set_lookahead() delegating to lookahead.py
└── cli.py                 # MODIFY — `auto-label` and `plan-lookahead` subcommands
tests/
├── test_transition_score.py   # NEW
├── test_auto_feedback.py      # NEW
└── test_lookahead.py          # NEW
```

Design rule: auto labels never enter `collect_preferences()` output. Two collectors, two label files, one shared model shape. Agreement between them is *reported*, never silently merged.

---

### Task 1: Objective transition scorer

**Files:**
- Create: `src/dj_agent/transition_score.py`
- Test: `tests/test_transition_score.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transition_score.py
import numpy as np
import pytest
from dj_agent.transition_score import beat_alignment_error, score_transition


def _beats(bpm, n=64, start=0.0):
    return np.arange(n) * 60.0 / bpm + start


def test_perfect_alignment_is_zero():
    a = _beats(120)
    b = _beats(120)
    assert beat_alignment_error(a, b) == pytest.approx(0.0, abs=1e-9)


def test_half_beat_offset_is_max():
    a = _beats(120)
    b = _beats(120, start=0.25)  # half of 0.5s beat
    err = beat_alignment_error(a, b)
    assert 0.2 < err <= 0.25 + 1e-9


def test_score_transition_bounds_and_keys():
    out = score_transition(
        bpm_a=120.0, bpm_b=120.0, beats_a=_beats(120), beats_b=_beats(120),
        key_a="C", key_b="C", energy_a=.5, energy_b=.5, energy_target=.5,
        qc={"clipped_samples": 0, "longest_silence_seconds": 0.0,
            "sample_peak_dbfs": -1.0, "rms_dbfs": -18.0},
    )
    assert set(out) == {"total", "beat_alignment", "harmony", "bpm_pull",
                        "energy_arc", "qc_penalty", "components"}
    assert 0.0 <= out["total"] <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dj-project/miaomi-dj/source && uv run pytest tests/test_transition_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dj_agent.transition_score'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dj_agent/transition_score.py
"""Objective per-transition scoring. No learned weights, no human labels.

Mirrors the sequencer reward of offline AI-DJ systems (Camelot harmonic
distance, tempo compatibility, energy-arc fit) plus render-QC penalties
from evaluation.measure(). All components are in [0, 1]; higher is better.
"""
from __future__ import annotations

import numpy as np

from .planner import _harmonic_cost


def beat_alignment_error(beats_a: np.ndarray, beats_b: np.ndarray,
                         window: int = 32) -> float:
    """Mean absolute downbeat offset (s) of B's beats vs A's grid, half-beat folded."""
    grid = np.asarray(beats_a, dtype=float)
    incoming = np.asarray(beats_b, dtype=float)
    if grid.size < 2 or incoming.size < 2:
        raise ValueError("need at least two beats per side")
    period = float(np.median(np.diff(grid)))
    offsets = []
    for t in incoming[:window]:
        idx = np.searchsorted(grid, t)
        candidates = grid[max(0, idx - 1): idx + 1]
        if candidates.size == 0:
            continue
        delta = float(np.min(np.abs(candidates - t)))
        offsets.append(min(delta, period / 2.0))  # fold to half-beat
    return float(np.mean(offsets)) if offsets else period / 2.0


def _clamp01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def score_transition(*, bpm_a: float, bpm_b: float, beats_a, beats_b,
                     key_a: str, key_b: str, energy_a: float, energy_b: float,
                     energy_target: float, qc: dict) -> dict:
    err = beat_alignment_error(beats_a, beats_b)
    beat = _clamp01(1.0 - err / 0.25)                     # 250ms drift → 0
    harm = _clamp01(1.0 - _harmonic_cost(key_a, key_b))   # reuse Camelot cost
    bpm_pull = _clamp01(1.0 - abs(np.log2(max(bpm_b, 1e-9) / max(bpm_a, 1e-9))))
    arc = _clamp01(1.0 - abs(energy_b - energy_target) / .25)
    penalty = 0.0
    penalty += 1.0 if qc.get("clipped_samples", 0) else 0.0
    penalty += _clamp01(qc.get("longest_silence_seconds", 0.0) / 2.0)
    penalty += _clamp01(max(0.0, -0.3 - qc.get("sample_peak_dbfs", -1.0)) / 6.0)
    qc_penalty = _clamp01(penalty)
    total = _clamp01(.30 * beat + .25 * harm + .15 * bpm_pull + .20 * arc
                     + .10 * (1.0 - qc_penalty))
    return {"total": total, "beat_alignment": beat, "harmony": harm,
            "bpm_pull": bpm_pull, "energy_arc": arc, "qc_penalty": qc_penalty,
            "components": {"weights": {"beat": .30, "harmony": .25,
                                       "bpm": .15, "arc": .20, "qc": .10}}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_transition_score.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dj_agent/transition_score.py tests/test_transition_score.py
git commit -m "feat: objective transition scorer (beat/harmony/bpm/arc/qc)"
```

---

### Task 2: Auto-label generation (synthetic A/B preferences)

**Files:**
- Create: `src/dj_agent/auto_feedback.py`
- Test: `tests/test_auto_feedback.py`

Key design: for each pair the renderer can produce (same tracks, variant A = baseline, B = enhanced), render both transitions through the real pipeline, score both with Task 1, and write a **synthetic preference** to `auto-feedback.jsonl`. Labels are derived only from measurable deltas (threshold on score gap; below gap → `tie`). Every record carries provenance: scorer version, feature snapshot, sha256 of both renders, so any later audit can reproduce it. This is the piece that removes the human: the *signal* is objective audio, the *format* matches what the ranker already eats.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auto_feedback.py
import json
import pytest
from dj_agent.auto_feedback import label_from_scores, append_auto_label


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auto_feedback.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/dj_agent/auto_feedback.py
"""Synthetic preference labels from objective scores — append-only, provenance-full.

Invariants:
- source is always "auto" here; human labels live in feedback.jsonl only.
- gap below min_gap ⇒ tie (never fabricate confidence).
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_auto_feedback.py -v` → 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dj_agent/auto_feedback.py tests/test_auto_feedback.py
git commit -m "feat: auto-label writer with provenance + tie discipline"
```

---

### Task 3: Auto-label pipeline over rendered pairs

**Files:**
- Modify: `src/dj_agent/auto_feedback.py` (add `label_session_pairs`)
- Test: `tests/test_auto_feedback.py` (extend)

This wires Tasks 1+2 to the real renderer: for each pair in a session's `comparison.json`, render baseline and enhanced transitions on a short overlap (existing `render.crossfade` + `transition_fx.process_exit`), measure with `evaluation.measure`, score with `score_transition`, and append one auto label per pair. Cap renders per run (default 50) so a big session can't run for hours.

- [ ] **Step 1: Extend the test**

```python
# append to tests/test_auto_feedback.py
def test_label_session_pairs_produces_one_row_per_pair(tmp_path, monkeypatch):
    from dj_agent import auto_feedback as af

    # fake comparison manifest with two pairs
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
```

- [ ] **Step 2: Run** `uv run pytest tests/test_auto_feedback.py -v` → new test FAILS (`label_session_pairs` missing)

- [ ] **Step 3: Implement**

```python
# append to src/dj_agent/auto_feedback.py
import json as _json


def _render_and_score_pair(pair: dict) -> tuple[dict, dict]:
    """Render baseline vs enhanced for one pair, return (score_a, score_b).

    Real implementation loads the two tracks via analysis.analyze(), takes
    the planned cue overlap, renders with render.crossfade(variant=...) plus
    transition_fx.process_exit, measures with evaluation.measure, and scores
    with transition_score.score_transition. Kept behind a seam so tests can
    monkeypatch.
    """
    raise NotImplementedError("wired in Task 3 step 3b during execution")


def label_session_pairs(session_dir: Path, max_pairs: int = 50) -> int:
    session = Path(session_dir)
    manifest_path = session / "comparison.json"
    if not manifest_path.is_file():
        raise ValueError("no comparison manifest")
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    written = 0
    for pair in manifest.get("pairs", [])[:max_pairs]:
        score_a, score_b = _render_and_score_pair(pair)
        pref = label_from_scores(score_a, score_b)
        append_auto_label(session, {
            "pair_id": pair["id"], "preference": pref, "source": "auto",
            "scorer_version": SCORER_VERSION,
            "score_a": score_a["total"], "score_b": score_b["total"],
            "components_a": {k: score_a[k] for k in
                             ("beat_alignment", "harmony", "bpm_pull",
                              "energy_arc", "qc_penalty")},
            "components_b": {k: score_b[k] for k in
                             ("beat_alignment", "harmony", "bpm_pull",
                              "energy_arc", "qc_penalty")},
        })
        written += 1
    return written
```

Then complete `_render_and_score_pair` with the real render call (follow `render.render_set` usage in `cli.py`; overlap from `planner._align_overlap`), re-run tests with the monkeypatched seam (still green), and add one integration test marked `@pytest.mark.slow` that renders 2 synthetic sine tracks end-to-end and asserts `0 <= score <= 1`.

- [ ] **Step 4: Run all** `uv run pytest tests/test_auto_feedback.py -v` → PASS
- [ ] **Step 5: Commit** `git commit -am "feat: session-wide auto-label pipeline with render cap"`

---

### Task 4: Train the ranker on auto labels (never mixed)

**Files:**
- Modify: `src/dj_agent/training.py` (add `collect_auto_preferences`, `fit_auto_ranker`)
- Test: `tests/test_feedback.py` (extend)

- [ ] **Step 1: Failing test**

```python
# append to tests/test_feedback.py
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
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement** (mirror `collect_preferences` structure; `target = 1.0` for A, `0.0` for B; ties skipped and counted in audit; `deployment_status: "auto_experimental"` in the model dict; reuse `_matrix`/loss from `fit_ranker` by extracting the shared optimizer into `_fit_logistic(rows)` and calling it from both).

- [ ] **Step 4: Agreement report** — new pure function `agreement_report(human_rows, auto_rows)` returning Kendall-tau-style concordance over pair_id intersection plus counts; tested with two tiny handcrafted row lists (concordant pair + discordant pair → tau between -1 and 1). Never gates anything in v1 — reporting only.

- [ ] **Step 5: Run all + commit**

```bash
uv run pytest tests/test_feedback.py -v
git commit -am "feat: auto-label training path + agreement report (sources never mixed)"
```

---

### Task 5: Lookahead beam-search planner

**Files:**
- Create: `src/dj_agent/lookahead.py`
- Modify: `src/dj_agent/planner.py` (export `plan_set_lookahead`)
- Test: `tests/test_lookahead.py`

The AI-DJ-Software-style piece: choose the next track by total discounted future reward, not greedy cost. Beam width 3, horizon = remaining slots (capped at depth 4), reward per step = `score_transition(...)` against the target energy arc position.

- [ ] **Step 1: Failing test**

```python
# tests/test_lookahead.py
import pytest
from dj_agent.lookahead import beam_search_next


def _track(i, bpm, key, energy):
    return {"id": f"t{i}", "bpm": bpm, "key": key, "energy": energy,
            "duration": 180.0, "path": f"/x/{i}.mp3"}


TRACKS = [_track(0, 120, "C", .5), _track(1, 121, "C", .5),
          _track(2, 90, "F#", .9), _track(3, 122, "G", .45)]


def test_beam_prefers_harmonic_tempo_close():
    chosen = beam_search_next(current=TRACKS[0], available=TRACKS[1:],
                              remaining=3, energy_target=.5,
                              score_fn=None)  # None → use default scorer
    assert chosen["id"] == "t1"


def test_beam_search_never_revisits():
    chosen = beam_search_next(current=TRACKS[1], available=TRACKS,
                              remaining=2, energy_target=.5, score_fn=None)
    assert chosen["id"] != "t1"
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError`)
- [ ] **Step 3: Implement**

```python
# src/dj_agent/lookahead.py
"""Beam-search next-track selection over objective transition scores."""
from __future__ import annotations

from .transition_score import score_transition

BEAM_WIDTH = 3
MAX_DEPTH = 4


def _default_score(cur, nxt, energy_target):
    return score_transition(
        bpm_a=cur["bpm"], bpm_b=nxt["bpm"], beats_a=[0.0], beats_b=[0.0],
        key_a=cur["key"], key_b=nxt["key"],
        energy_a=cur["energy"], energy_b=nxt["energy"],
        energy_target=energy_target,
        qc={"clipped_samples": 0, "longest_silence_seconds": 0.0,
            "sample_peak_dbfs": -1.0, "rms_dbfs": -18.0})["total"]


def beam_search_next(*, current, available, remaining, energy_target, score_fn=None):
    score = score_fn or _default_score
    depth = min(remaining, MAX_DEPTH)

    def expand(node):
        cur, used = node
        return [(nxt, used | {nxt["id"]}) for nxt in available
                if nxt["id"] not in used and nxt["id"] != cur["id"]]

    # beam entries: (path, cumulative)
    frontier = [((current,), 0.0)]
    best_first, best_score = None, float("-inf")
    for _ in range(depth):
        nxt_frontier = []
        for path, cum in frontier:
            for cand, _ in expand((path[-1], {t["id"] for t in path})):
                s = score(path[-1], cand, energy_target)
                nxt_frontier.append((path + (cand,), cum + s))
        if not nxt_frontier:
            break
        nxt_frontier.sort(key=lambda e: e[1], reverse=True)
        frontier = nxt_frontier[:BEAM_WIDTH]
        first, total = frontier[0][0][1], frontier[0][1]
        if total > best_score:
            best_first, best_score = first, total
    return best_first
```

Add `plan_set_lookahead(tracks, max_tracks, target_minutes, energy_arc="wave")` in `planner.py` that loops `beam_search_next` while decrementing `remaining` and stepping `energy_target` along the chosen arc (build/wave/wind-down), reusing existing `_validate`/`_cue` from planner.

- [ ] **Step 4: Run** `uv run pytest tests/test_lookahead.py -v` → PASS
- [ ] **Step 5: Commit** `git commit -am "feat: lookahead beam planner (Camelot+BPM+arc reward)"`

---

### Task 6: CLI + smoke run + docs

**Files:**
- Modify: `src/dj_agent/cli.py` — two subcommands:

```text
uv run dj-agent auto-label <session_dir> [--max-pairs 50]
uv run dj-agent plan-lookahead <folder> [--minutes 30] [--arc wave]
```

- [ ] **Step 1:** Add subcommands following the existing argparse pattern in `cli.py`; each prints an audit summary dict as JSON to stdout.
- [ ] **Step 2:** `uv run pytest` (whole suite) → green.
- [ ] **Step 3:** Smoke on real data: `uv run dj-agent auto-label` over one existing session dir with 2 pairs; verify `auto-feedback.jsonl` rows and that the human path still rejects them (`collect_preferences` audit shows `rejected: {non_human_label: N}`).
- [ ] **Step 4:** Update `dj-project/README.md`: new "Self-feedback loop" section — objective scorer → auto labels → auto ranker → lookahead planner; human labels remain the gold audit chain; agreement report is the bridge.
- [ ] **Step 5: Commit + push**

```bash
git add -A && git commit -m "feat: no-human feedback loop (auto labels + lookahead planner) + docs"
git push origin main
```

---

## Verification (whole plan)

1. `uv run pytest` green, including pre-existing tests untouched.
2. `collect_preferences()` behavior on human data unchanged (audit identical).
3. Auto path end-to-end: session → auto-feedback.jsonl → `fit_auto_ranker` → model with `deployment_status: auto_experimental`.
4. `plan-lookahead` on the 25-track lofi feats set produces a valid order (BPM/key/energy arc visibly smoother than greedy in the printed audit).

## What this deliberately does NOT do

- No merging auto labels into the human training set.
- No silent deployment of auto-trained models (status stays `auto_experimental`).
- No LLM judge: the scorer is deterministic DSP/music-theory, so results are reproducible and auditable. An LLM-judge seam can be added later behind `score_fn`.
