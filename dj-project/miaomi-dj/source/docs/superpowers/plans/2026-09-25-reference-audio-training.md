# Local reference audio training

User authorization: organize the newly supplied songs and Downloads MP4s and begin training (2026-09-25). This extends the existing reference-choice experiment; publishing a new model or renaming the live site is not part of this run.

## Design

Preserve originals, import only Weeknd reference MP4s into content-addressed folders, and export their complete audio as FLAC. Analyze all 25 local tracks with the existing cached Beat This pipeline, refreshing renamed paths by content hash. Audio matching uses normalized chroma sequences over windows with a small tempo and pitch search. Matching is evidence of recording identity, not a quality rating. Low-confidence, inconsistent and missing-library regions remain unknown.

Consecutive coherent matches form song spans. Only adjacent confident spans separated by a bounded uncertainty interval produce weak next-track labels. Retain the time bracket and estimated source-song cue, never call these exact fader timings. No inferred EQ, echo, reverb, vocal or human-preference labels. Train the existing regularized choice head using audio-derived rows. Save video-held-out, directed-pair-purged evaluation and a final experimental checkpoint separately from production. Also evaluate a small regularized source-cue regressor against training-only medians, with source-song purging to test generalization.

## Implementation and verification

- [x] Import eight videos with full SHA-256 manifests; decode full audio; analyze two new songs and refresh the whole library.
- [x] Test matching on time/pitch-transformed sequences, reject unrelated/silent/ambiguous queries; implement `reference_audio.py` and a resumable command under `scripts/`.
- [x] Test continuity, uncertainty brackets and unknown-gap exclusion before creating weak labels; export standard JSONL, schema and human-auditable spans.
- [x] Fit choice and cue models; evaluate source-held-out splits, purge repeated directed pairs or cue target songs, save splits, predictions, baseline metrics, parameters and provenance.
- [x] Verify full media integrity, sample the alignment against available chapter evidence, run tests, document results and limitations. Preserve previous models and listening feedback.
- [x] Offer product/session naming pairs for the user to choose; do not change the deployed name without a choice.

## Resource limits and reproducibility

Use existing Python/NumPy/SciPy/librosa/FFmpeg; CPU with bounded numerical threads. Cache feature extraction by audio hash and feature version. Match in bounded batches. Store raw video/audio, derived arrays and training artifacts outside Git; commit code, tests and instructions. Raw media stays local. Seeds, thresholds, library hashes and implementation hashes travel with the experiment. Filename metadata does not prove a platform URL, and a playlist is not automatically a live DJ performance.

## Completion ledger

Final dataset v2: 8 sources, 75 coherent spans, 30 choices / 22 directed pairs, 12 cue-eligible rows. Final model run v3 adds a six-parameter acoustic comparison; it is explicitly exploratory. Known-origin repeated-chorus failure found by control tests is rejected by the new position gate. New nonoverlapping control clips pass. Empty/insufficient cue data does not abort choice training. Independent review fixes verified. 147 tests and Ruff pass.

Parallel authorized online update completed: v4, 15 tracks, 1178.935 seconds; excluded Earned It and A Lonely Night; original apex URL verified. www CDN retains v3 and needs an authenticated ESA purge; no credentials or login bypass used. No new training weights deployed. Names offered in docs/naming-options.md, deployed title neutral.
