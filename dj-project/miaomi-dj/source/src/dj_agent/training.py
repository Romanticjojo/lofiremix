"""Audited human-preference experiments; never substitutes technical rules for labels."""

import hashlib
import json
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from .feedback import split_by_song_identity
from .preferences import FEATURE_NAMES, FEATURE_VERSION, clip_features, file_sha256, session_file


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _vector(features):
    if not isinstance(features, dict) or set(features) != set(FEATURE_NAMES):
        raise ValueError("feature_schema_mismatch")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in features.values()):
        raise ValueError("invalid_features")
    vector = np.array([features[name] for name in FEATURE_NAMES], dtype=np.float64)
    if not np.isfinite(vector).all() or np.max(np.abs(vector)) > 1e8:
        raise ValueError("invalid_features")
    return vector


def _candidate(session, candidate):
    path = session_file(session, candidate["audio"])
    if file_sha256(path) != candidate["sha256"]:
        raise ValueError("changed_audio")
    measured = clip_features(path)
    if not np.allclose(_vector(candidate["features"]), _vector(measured), atol=1e-8, rtol=1e-8):
        raise ValueError("changed_features")
    if candidate["renderer"] not in ("baseline", "enhanced"):
        raise ValueError("unknown_renderer")
    return measured


def _bound_row(session, manifest, digest, record, songs, reviewed):
    if manifest.get("schema_version") != 2 or manifest.get("feature_version") != FEATURE_VERSION:
        raise ValueError("legacy_or_unknown_schema")
    if record.get("comparison_sha256") != digest:
        raise ValueError("changed_manifest")
    if manifest.get("source_kind") != "user-local-audio":
        raise ValueError("non_real_audio")
    if record.get("source") != "human":
        raise ValueError("non_human_label")
    if not reviewed:
        raise ValueError("identity_not_reviewed")
    pairs = [p for p in manifest["pairs"] if p["id"] == record["pair_id"]]
    if len(pairs) != 1:
        raise ValueError("unknown_or_duplicate_pair")
    pair = pairs[0]
    ids = pair["track_ids"]
    if (not isinstance(ids, list) or len(ids) != 2 or any(not isinstance(t, str) for t in ids)
            or sorted(ids) != record["track_ids"]):
        raise ValueError("track_identity_mismatch")
    if any(t not in songs for t in ids):
        raise ValueError("missing_identity")
    canonical = sorted(songs[t] for t in ids)
    if canonical[0] == canonical[1]:
        raise ValueError("same_song")
    label = record["preference"]
    if label not in ("A", "B", "tie", "neither"):
        raise ValueError("invalid_label")
    a, b = pair["candidates"]["A"], pair["candidates"]["B"]
    features_a, features_b = _candidate(session, a), _candidate(session, b)
    if a["sha256"] == b["sha256"]:
        raise ValueError("identical_audio")
    target = {"A": 1., "B": 0., "tie": .5, "neither": None}[label]
    # Normalize orientation so copied sessions or A/B reversals cannot inflate sample count.
    if a["sha256"] > b["sha256"]:
        a, b = b, a
        features_a, features_b = features_b, features_a
        if target is not None:
            target = 1 - target
    identity = [canonical, a["sha256"], b["sha256"]]
    example_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    return {"example_id": example_id, "track_ids": canonical,
            "a": features_a, "b": features_b, "target": target,
            "renderer_A": a["renderer"], "renderer_B": b["renderer"]}


def collect_preferences(sessions: Path, identity_map: Path | None) -> tuple[list[dict], dict]:
    root = Path(sessions)
    if not root.is_dir():
        raise ValueError("sessions directory does not exist")
    songs, reviewed = {}, False
    if identity_map is not None and Path(identity_map).is_file():
        mapping = _json(identity_map)
        if not isinstance(mapping, dict) or mapping.get("schema_version") != 1:
            raise ValueError("invalid identity map schema")
        songs = mapping.get("songs", {})
        if not isinstance(songs, dict) or any(
            not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
            for k, v in songs.items()
        ):
            raise ValueError("identity map must associate file IDs with canonical song IDs")
        reviewed = mapping.get("reviewed") is True
    audit = {"feedback_records": 0, "accepted_pairs": 0, "identity_reviewed": reviewed,
             "identity_map_sha256": file_sha256(identity_map) if identity_map and Path(identity_map).is_file() else None}
    rejected, examples, votes = Counter(), {}, {}
    sessions_with_feedback = {p.parent for name in ('feedback.jsonl', 'structured-feedback.jsonl')
                              for p in root.rglob(name)}
    for session in sorted(sessions_with_feedback):
        feedback = session / 'feedback.jsonl'
        try:
            if feedback.is_symlink() or not feedback.resolve().is_relative_to(root.resolve()):
                raise ValueError("unsafe_feedback")
            manifest_path = session_file(session, "comparison.json")
            manifest, digest = _json(manifest_path), file_sha256(manifest_path)
            lines = feedback.read_text(encoding="utf-8").splitlines() if feedback.exists() else []
            if (session / 'structured-feedback.jsonl').exists():
                from .listening_feedback import training_records
                effective = training_records(session, [json.loads(s) for s in lines if s.strip()], manifest, digest)
                lines = [json.dumps(row) for row in effective]
        except (OSError, ValueError, UnicodeError) as exc:
            rejected[f"unreadable_session:{type(exc).__name__}"] += 1
            continue
        for line in lines:
            if not line.strip():
                continue
            audit["feedback_records"] += 1
            try:
                record = json.loads(line)
                row = _bound_row(session, manifest, digest, record, songs, reviewed)
                key = row["example_id"]
                examples[key] = row
                votes.setdefault(key, set()).add(row["target"])
            except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
                reason = str(exc) if type(exc) is ValueError else f"invalid_record:{type(exc).__name__}"
                rejected[reason] += 1
    rows = []
    for key in sorted(examples):
        if len(votes[key]) != 1:
            rejected["conflicting_labels"] += 1
        elif votes[key] == {None}:
            rejected["neither"] += 1
        else:
            rows.append(examples[key])
    audit.update({"accepted_pairs": len(rows), "rejected": dict(rejected),
                  "decisive_pairs": sum(row["target"] != .5 for row in rows)})
    return rows, audit


def _matrix(rows):
    if not rows:
        raise ValueError("training requires comparison rows")
    x = np.stack([_vector(row["a"]) - _vector(row["b"]) for row in rows])
    y = np.array([row["target"] for row in rows], dtype=np.float64)
    if not np.isfinite(y).all() or not np.isin(y, [0., .5, 1.]).all():
        raise ValueError("invalid target")
    return x, y


def fit_ranker(rows: list[dict]) -> dict:
    x, y = _matrix(rows)
    scale = np.sqrt(np.mean(x*x, axis=0))
    active = scale > 1e-8
    if not active.any():
        raise ValueError("no distinguishable candidate features")
    scale[~active] = 1.
    z = x / scale
    z[:, ~active] = 0
    regularization = .1

    def loss(w):
        logits = z @ w
        objective = np.mean(np.logaddexp(0, logits) - y*logits) + regularization/2 * (w @ w)
        gradient = z.T @ (expit(logits) - y) / len(y) + regularization*w
        return float(objective), gradient

    fitted = minimize(loss, np.zeros(x.shape[1]), jac=True, method="L-BFGS-B", options={"maxiter": 500})
    if not fitted.success or not np.isfinite(fitted.x).all():
        raise RuntimeError(f"preference optimizer failed: {fitted.message}")
    return {"schema_version": 1, "model_type": "pairwise_logistic", "feature_version": FEATURE_VERSION,
            "feature_names": list(FEATURE_NAMES), "weights": fitted.x.tolist(), "scale": scale.tolist(),
            "active": active.tolist(), "regularization": regularization,
            "optimizer_iterations": int(fitted.nit), "deployment_status": "experimental_not_enabled"}


def predict_preference(model: dict, a: dict, b: dict) -> float:
    if model.get("feature_version") != FEATURE_VERSION or model.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("incompatible model features")
    weights, scale = np.array(model["weights"]), np.array(model["scale"])
    active = np.array(model["active"], dtype=bool)
    if (weights.shape != (len(FEATURE_NAMES),) or scale.shape != weights.shape or active.shape != weights.shape
            or not np.isfinite(weights).all() or not np.isfinite(scale).all() or (scale <= 0).any()):
        raise ValueError("invalid model parameters")
    delta = (_vector(a) - _vector(b)) / scale
    delta[~active] = 0
    return float(expit(delta @ weights))


def _component_count(rows):
    groups = []
    for row in rows:
        joined = set(row["track_ids"])
        untouched = []
        for group in groups:
            if group & joined:
                joined.update(group)
            else:
                untouched.append(group)
        groups = [*untouched, joined]
    return len(groups)


def _partition(rows):
    if not rows:
        raise ValueError("insufficient data: no accepted human comparisons")
    train, test = split_by_song_identity(rows, test_fraction=.2, seed=17)
    if (sum(r["target"] != .5 for r in train) < 20 or sum(r["target"] != .5 for r in test) < 10
            or _component_count(train) < 3 or _component_count(test) < 2):
        raise ValueError("insufficient data: need 20/10 decisive train/test pairs and 3/2 independent song groups")
    return train, test


def evaluate_ranker(rows: list[dict]) -> tuple[dict, dict]:
    train, test = _partition(rows)
    model = fit_ranker(train)
    decisive_train = [r for r in train if r["target"] != .5]
    majority = float(np.mean([r["target"] for r in decisive_train]) >= .5)

    def metrics(part):
        probabilities = np.array([predict_preference(model, r["a"], r["b"]) for r in part])
        targets = np.array([r["target"] for r in part])
        decisive = targets != .5
        p = np.clip(probabilities, 1e-12, 1 - 1e-12)
        hits = np.where(np.isclose(probabilities, .5, atol=1e-10, rtol=0), .5,
                        (probabilities > .5) == targets)
        enhanced = np.array([r["renderer_A"] == "enhanced" for r in part], dtype=float)
        return {"pairs": len(part), "decisive_pairs": int(decisive.sum()),
                "log_loss": float(-np.mean(targets * np.log(p) + (1-targets)*np.log1p(-p))),
                "accuracy": float(hits[decisive].mean()),
                "majority_baseline_accuracy": float(np.mean(targets[decisive] == majority)),
                "always_enhanced_accuracy": float(np.mean(targets[decisive] == enhanced[decisive]))}

    split = {"seed": 17, "method": "canonical-song connected components"}
    for name, part in [("train", train), ("test", test)]:
        split[f"{name}_song_ids"] = sorted({song for row in part for song in row["track_ids"]})
        split[f"{name}_example_ids"] = [r["example_id"] for r in part]
        split[f"{name}_groups"] = _component_count(part)
    evaluation = {"split": split, "train": metrics(train), "test": metrics(test),
                  "limitations": "Small experimental holdout; no musical-quality or deployment approval."}
    return model, evaluation


def run_training(sessions: Path, identity_map: Path | None, output: Path, check_only=False) -> dict:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("training output already exists; choose a new directory")
    started = time.perf_counter()
    rows, audit = collect_preferences(sessions, identity_map)
    report = {"schema_version": 1, "created_utc": datetime.now(UTC).isoformat(), "audit": audit,
              "status": "needs_data", "model_written": False,
              "dataset_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, allow_nan=False).encode()).hexdigest(),
              "device": "CPU", "check_only": check_only}
    model = None
    try:
        _partition(rows)
    except ValueError as exc:
        report["reason"] = str(exc)
    else:
        if check_only:
            report["status"] = "ready_for_training"
        else:
            model, report["evaluation"] = evaluate_ranker(rows)
            model["dataset_sha256"] = report["dataset_sha256"]
            report.update(status="trained_experimental", model_written=True)
    report["elapsed_seconds"] = time.perf_counter() - started
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dj-training-", dir=output.parent) as scratch:
        staging = Path(scratch) / "result"
        staging.mkdir()
        artifacts = {"report.json": report}
        if model is not None:
            artifacts.update({"model.json": model, "dataset.json": rows})
        for filename, data in artifacts.items():
            (staging / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False),
                                          encoding="utf-8")
        staging.rename(output)
    return report
