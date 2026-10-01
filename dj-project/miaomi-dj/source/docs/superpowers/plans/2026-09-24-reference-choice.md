# Reference choice training implementation plan

> Execution: implement locally in the existing feature branch. User authorized data preparation and training on 2026-09-24; no further approval gate is needed.

**Goal:** Save a reproducible, provenance-bound reference sequence dataset and train an inspectable CPU next-song choice model, with honest held-out evaluation and a local acceptance page.

**Architecture:** Three visible Douyin chapter lists provide weak sequence demonstrations. They do not supply audio quality, human preference, exact cues or effect automation. A separate regularized multinomial choice model learns known-title continuation from up to three preceding tracks. Existing human A/B training retains its own stricter eligibility checks.

**Tech stack:** Python 3.11, NumPy/SciPy already installed, JSON/JSONL, self-contained HTML. No cloud services or new dependencies.

## Constraints and dataset contract

- Preserve original media and four explicit human decisions. No fabricated human labels.
- Versioned choice JSONL: `id`, `context`, `candidates`, `label`, `provenance`, `supervision`, `unknown_labels`.
- Candidate alternatives are unobserved choices, not negative musical-quality judgments; exclude context songs from candidates.
- Label source is `public_page_chapter_order`; source song cue, transition quality and FX remain null.
- Evaluation is leave-one-video-out, purging every held-out directed adjacent pair from training. Known song vocabulary is shared and declared. This is not unseen-song generalization.
- Equal total training weight per source video; fixed regularization, no held-out tuning. Compare uniform and train-only popularity baselines.
- Save final model, per-fold models, split IDs, held-out predictions, hashes and model card. All final-data inference is explicitly a fitted-data demo.
- Model stays experimental and does not replace the playback planner automatically.

## Task 1 — Dataset preparation

Status: complete. Source consistency, label boundaries and title merging verified on actual references.

Files: `src/dj_agent/reference_data.py`, `tests/test_reference_choice.py`.

- [x] Write tests for provenance, candidate/label consistency, malformed inputs, duplicate IDs, source row mismatch, no invented precise labels, and held-out pair purging.
- [x] Run tests and confirm missing implementation fails.
- [x] Implement `prepare_dataset(reference_dir)` returning catalog and choice rows, and `source_folds(rows)` returning traceable train/test IDs plus purge IDs.
- [x] Run the focused tests.

## Task 2 — Training and inference

Status: complete. Deterministic fit/reload and held-out predictions verified; model remains experimental.

Files: `src/dj_agent/reference_choice.py`, same test file.

- [x] Write learning/reload tests with hand-checked toy choices; reject unknown context and nonfinite model parameters, exclude already-played tracks.
- [x] Implement deterministic masked multinomial cross-entropy with L2 regularization using SciPy L-BFGS-B. Store feature version and weights as portable JSON.
- [x] Implement rank predictions and metrics with fractional tie handling; compare uniform and train-only popularity on full candidate sets.
- [x] Run tests; verify optimization reduces objective and exports reload identically.

## Task 3 — Experiment, artifacts and acceptance

Status: artifacts and training complete; full suite 112 passed. Local HTML browser inspection was blocked by file URL policy; no workaround attempted. Embedded model equality, JavaScript syntax and Python/HTML feature parity checked. Real feedback page interaction verified separately in Chrome. Independent review found no unresolved P1/P2.

Files: `src/dj_agent/reference_experiment.py`, `src/dj_agent/reference_review.html`, CLI, docs, launcher.

- [x] Test artifact integrity, deterministic splits and refusal to overwrite an existing experiment.
- [x] Implement `train-reference` command, dataset/schema/folds/checkpoint/report/prediction/checksum export, plus a local HTML model explorer.
- [x] Run on the three actual reference manifests; inspect evaluation without selecting a favorable split or tuning to the holdout.
- [x] Verify real inference and original feedback page; run full tests and Ruff. Record blocked local-file browser inspection and verify HTML/model parity separately.
- [x] Request a bounded independent review, address concrete findings, document measured results and commit locally.
