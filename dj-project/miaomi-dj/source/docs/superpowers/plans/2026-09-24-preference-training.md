# Preference training implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Turn explicit listening choices into a reproducible experimental local ranker, and report missing data without manufacturing a trained DJ model.

**Architecture:** Export audio-bound, randomly ordered A/B candidates; collect only bound human feedback from real local audio. Fit a regularized pairwise logistic model on numerical clip features, holding out connected canonical song groups before fitting scaling or weights. Keep the result experimental and separate from automatic audio execution.

**Tech Stack:** Existing Python 3.11, NumPy, SciPy, SoundFile; CPU only, no added dependency.

**Spec:** `docs/design.md` and `docs/training.md`; user explicitly authorized starting training on 2026-09-24.

## Global constraints

- Work directly in `D:\DJ_agent`, preserve music and existing results.
- No invented human preferences; synthetic examples only in automated tests.
- No GLM/Open-Jev fine-tuning without task data and evidence of benefit.
- No claim of Weeknd evaluation, live crowd optimization, or automatic model promotion.
- Current input inventory: zero Weeknd files and zero human feedback records.

## Task 1: Bound candidate features and labels

Files: create `src/dj_agent/preferences.py`, `tests/test_preferences.py`; modify `cli.py`, `feedback.py`.

Interfaces: `clip_features(path) -> dict[str, float]`; `build_comparison(session, plan) -> dict` exports schema 2 with A/B clip paths, hashes and fixed-version features. `record_preference` binds new records to manifest SHA-256.

- [x] Write tests: `assert features['low_band_ratio'] > .9` for a 100 Hz stereo tone, `< .01` for 1 kHz; reject nonfinite/empty clips; verify bound feedback hash changes when manifest changes.
- [x] Run focused pytest and observe absent feature behavior.
- [x] Implement numerical features from decoded clips: level, crest, low-band fraction, RMS spread/step, side energy, silence fraction. Never use artist, filename, variant name or A/B letter as learned features.
- [x] Validate candidate paths stay in session, hashes match, source marks remain distinct; use random A/B assignment and actual manifest paths in HTML.
- [x] Run focused tests and preserve v1 feedback compatibility (legacy labels are stored but not silently trained).

## Task 2: Data audit, fitting and independent evaluation

Files: create `src/dj_agent/training.py`, `tests/test_training.py`; modify `cli.py`.

Interfaces: `collect_preferences(sessions, identity_map) -> (rows, audit)`; `fit_ranker(rows) -> dict`; `predict_preference(model, a, b) -> float`; `run_training(sessions, identity_map, output, check_only=False) -> dict`.

- [x] Write tests: empty data yields `needs_data` with no checkpoint; altered candidate/manifest and synthetic source are excluded; identity mapping is required; conflicting repeats excluded; same-song leakage blocks training.
- [x] Test real numerical fitting against a controlled synthetic preference function in temporary test directories: held-out predictions must beat chance; swapping A/B complements probability; nonfinite inputs rejected. These fixtures do not become human data.
- [x] Fit `sigmoid(w @ ((features_A-features_B)/train_scale))` with zero intercept, L2=.1 and L-BFGS-B. Training scale is RMS difference from training rows only; inactive dimensions have zero weight. Fail on optimizer/nonfinite errors.
- [x] Reserve connected song groups with seed 17 before fitting. Require at least 20 decisive training pairs, 10 decisive test pairs, 3 training groups and 2 test groups. These are engineering floors, not evidence of musical generalization.
- [x] Export counts, rejected reasons, split membership, log loss, decisive accuracy, training-majority baseline and always-enhanced baseline. Output experimental JSON checkpoint only after valid fit. No automatic use by planner.
- [x] Expose `train --sessions outputs --identity-map data/song-identities.json --output ... [--check-only]`; collision fails without overwriting prior results. Data-shortage actual train exits 2; check-only exits 0 with explicit status.

## Task 3: Local verification and honest delivery

Files: update `docs/training.md`, `docs/status.md`, `docs/verification.md`, `README.md`.

- [x] Run full pytest, Ruff and existing Mixxx harness. Request independent read-only review of training integrity while updating docs.
- [x] Execute actual training command on current workspace; save readiness report. Expect missing songs/labels and no learned checkpoint.
- [x] Record exact evidence and explain public annotation datasets versus personal preference labels, practical collection steps, CPU use and lack of a trained preference model today.
- [x] Keep changes local, commit verified work on current branch.
