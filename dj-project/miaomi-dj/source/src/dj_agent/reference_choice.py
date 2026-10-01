"""Small regularized conditional choice model; learns chapter sequence imitation only."""

from collections import Counter

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp, softmax


def _features(context, index, context_length=3):
    if not context or any(t not in index for t in context):
        raise ValueError('empty or unknown context track')
    n = len(index)
    vector = np.zeros(1 + n*(1 + (context_length > 1)))
    vector[0] = 1
    vector[1 + index[context[-1]]] = 1
    for lag, track in enumerate(reversed(context[-context_length:-1]), 1):
        vector[1 + n + index[track]] += .5**lag
    return vector


def sample_weights(rows):
    counts = Counter(r['provenance']['source_video_id'] for r in rows)
    if not counts:
        raise ValueError('empty training data')
    return np.array([1 / (len(counts) * counts[r['provenance']['source_video_id']]) for r in rows])


def fit_choice(catalog, rows, *, regularization=.05, context_length=3):
    if (isinstance(regularization, bool) or not isinstance(regularization, (int, float))
            or not np.isfinite(regularization) or regularization <= 0
            or type(context_length) is not int or context_length not in (1, 2, 3)):
        raise ValueError('invalid choice hyperparameters')
    ids = [c['id'] for c in catalog]
    if len(ids) < 3 or len(set(ids)) != len(ids):
        raise ValueError('invalid catalog')
    index = {t: i for i, t in enumerate(ids)}
    weights = sample_weights(rows)
    x = np.stack([_features(r['context'], index, context_length) for r in rows])
    mask = np.zeros((len(rows), len(ids)), dtype=bool)
    targets = []
    for i, row in enumerate(rows):
        candidates = row['candidates']
        if (not candidates or len(candidates) != len(set(candidates))
                or any(c not in index for c in candidates) or row['label'] not in candidates
                or set(candidates) & set(row['context'])):
            raise ValueError('invalid training candidates')
        mask[i, [index[t] for t in candidates]] = True
        targets.append(index[row['label']])
    targets = np.array(targets)
    shape = (x.shape[1], len(ids))

    def loss(flat):
        w = flat.reshape(shape)
        logits = np.where(mask, x @ w, -np.inf)
        log_norm = logsumexp(logits, axis=1)
        objective = weights @ (log_norm - logits[np.arange(len(rows)), targets])
        probabilities = np.exp(logits - log_norm[:, None])
        probabilities[np.arange(len(rows)), targets] -= 1
        gradient = x.T @ (probabilities * weights[:, None]) + regularization*w
        return float(objective + regularization/2 * np.sum(w*w)), gradient.ravel()

    initial = np.zeros(np.prod(shape))
    initial_loss = loss(initial)[0]
    fit = minimize(loss, initial, jac=True, method='L-BFGS-B', options={'maxiter': 500, 'gtol': 1e-8})
    if not fit.success or not np.isfinite(fit.x).all():
        raise RuntimeError(f'choice optimization failed: {fit.message}')
    counts = Counter(r['provenance']['source_video_id'] for r in rows)
    return {'schema_version': 1, 'model_type': 'context_softmax_choice', 'feature_version': 2,
            'catalog': catalog, 'weights': fit.x.reshape(shape).tolist(), 'regularization': regularization,
            'context_length': context_length,
            'parameter_count': int(np.prod(shape)), 'training_observations': len(rows),
            'source_total_weights': {sid: 1/len(counts) for sid in sorted(counts)},
            'optimization': {'initial_loss': initial_loss, 'final_loss': float(fit.fun),
                             'iterations': int(fit.nit)},
            'deployment_status': 'experimental_not_enabled',
            'objective': 'imitate_reference_chapter_next_track_not_audio_quality'}


def rank_choices(model, context, available=None):
    if (model.get('schema_version') != 1 or model.get('feature_version') not in (1, 2)
            or model.get('model_type') != 'context_softmax_choice'):
        raise ValueError('incompatible choice model')
    catalog = model['catalog']
    index = {c['id']: i for i, c in enumerate(catalog)}
    if len(index) != len(catalog):
        raise ValueError('duplicate model catalog IDs')
    context_length = 3 if model['feature_version'] == 1 else model.get('context_length')
    if type(context_length) is not int or context_length not in (1, 2, 3):
        raise ValueError('invalid model context length')
    w = np.array(model['weights'], dtype=float)
    expected_shape = (1 + len(index)*(1 + (context_length > 1)), len(index))
    if w.shape != expected_shape or not np.isfinite(w).all():
        raise ValueError('invalid choice weights')
    x = _features(context, index, context_length)
    if available is not None and any(t not in index for t in available):
        raise ValueError('unknown available track')
    allowed = set(index if available is None else available) - set(context)
    candidates = [c for c in catalog if c['id'] in allowed]
    if not candidates:
        raise ValueError('no available candidates')
    probabilities = softmax((x @ w)[[index[c['id']] for c in candidates]])
    rows = [{'track_id': c['id'], 'title': c['title'], 'probability': float(p)}
            for c, p in zip(candidates, probabilities, strict=True)]
    return sorted(rows, key=lambda r: (-r['probability'], r['track_id']))


def rank_metrics(probabilities, label):
    """Expected ranks/hits under random tie breaking, avoiding alphabetical lucky hits."""
    p = probabilities[label]
    values = np.array(list(probabilities.values()))
    ties = np.isclose(values, p, rtol=1e-10, atol=1e-12)
    better = int(np.sum((values > p) & ~ties))
    tied = int(ties.sum())
    return {'hit_at_1': min(max(1 - better, 0), tied)/tied,
            'hit_at_3': min(max(3 - better, 0), tied)/tied,
            'mrr': float(np.mean(1/np.arange(better+1, better+tied+1))),
            'nll': float(-np.log(max(p, 1e-15)))}
