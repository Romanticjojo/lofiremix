"""Six-parameter conditional choice baseline using audio-derived pair features."""

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp, softmax

from .planner import _harmonic_cost
from .reference_choice import sample_weights

FEATURES = ('folded_tempo_gap', 'harmonic_distance', 'energy_distance',
            'candidate_energy', 'candidate_duration', 'candidate_folded_tempo')


def _tempo(bpm):
    while bpm < 80:
        bpm *= 2
    while bpm >= 160:
        bpm /= 2
    return bpm


def pair_features(a, b):
    av = np.array([a[k] for k in ('bpm', 'energy', 'duration')], dtype=float)
    bv = np.array([b[k] for k in ('bpm', 'energy', 'duration')], dtype=float)
    if not np.isfinite([av, bv]).all() or min(av[0], bv[0], av[2], bv[2]) <= 0:
        raise ValueError('invalid acoustic features')
    ratio = b['bpm']/a['bpm']
    gap = min(abs(np.log2(ratio*factor)) for factor in (.5, 1., 2.))
    return np.array([gap, _harmonic_cost(a['key'], b['key']),
                     abs(b['energy']-a['energy'])/.25, b['energy']/.25,
                     b['duration']/300, _tempo(b['bpm'])/120], dtype=float)


def fit_acoustic(catalog, library, rows, *, regularization=1.):
    if not np.isfinite(regularization) or regularization <= 0:
        raise ValueError('invalid regularization')
    observed, targets = [], []
    for row in rows:
        candidates = row['candidates']
        if (not row['context'] or row['label'] not in candidates
                or len(set(candidates)) != len(candidates) or set(candidates) & set(row['context'])):
            raise ValueError('invalid acoustic choice row')
        observed.append(np.stack([pair_features(library[row['context'][-1]], library[c]) for c in candidates]))
        targets.append(candidates.index(row['label']))
    row_weights = sample_weights(rows)

    def loss(w):
        value, gradient = regularization/2*np.sum(w*w), regularization*w
        for x, target, importance in zip(observed, targets, row_weights, strict=True):
            logits = x @ w
            value += importance*(logsumexp(logits)-logits[target])
            p = softmax(logits)
            p[target] -= 1
            gradient = gradient + importance*(x.T @ p)
        return float(value), gradient

    initial = np.zeros(len(FEATURES))
    optimized = minimize(loss, initial, jac=True, method='L-BFGS-B', options={'gtol': 1e-8, 'maxiter': 500})
    if not optimized.success or not np.isfinite(optimized.x).all():
        raise RuntimeError('acoustic model optimization failed')
    return {'schema_version': 1, 'model_type': 'acoustic_conditional_choice', 'features': list(FEATURES),
            'weights': optimized.x.tolist(), 'parameter_count': len(FEATURES),
            'catalog': catalog, 'library': {tid: {k: library[tid][k] for k in ('bpm', 'key', 'duration', 'energy')}
                                           for tid in (c['id'] for c in catalog)},
            'regularization': regularization, 'training_observations': len(rows),
            'optimization': {'initial_loss': loss(initial)[0], 'final_loss': float(optimized.fun)},
            'deployment_status': 'experimental_not_enabled'}


def rank_acoustic(model, context, available):
    if model.get('model_type') != 'acoustic_conditional_choice' or model.get('features') != list(FEATURES):
        raise ValueError('incompatible acoustic model')
    if not context or context[-1] not in model['library']:
        raise ValueError('unknown context')
    selected = set(available)-set(context)
    if not selected or not selected <= set(model['library']):
        raise ValueError('empty or unknown candidates')
    catalog = [c for c in model['catalog'] if c['id'] in selected]
    weights = np.asarray(model['weights'], dtype=float)
    if weights.shape != (len(FEATURES),) or not np.isfinite(weights).all():
        raise ValueError('invalid acoustic weights')
    x = np.stack([pair_features(model['library'][context[-1]], model['library'][c['id']]) for c in catalog])
    probabilities = softmax(x @ weights)
    return sorted([{'track_id': c['id'], 'title': c['title'], 'probability': float(p)}
                   for c, p in zip(catalog, probabilities, strict=True)],
                  key=lambda r: (-r['probability'], r['track_id']))
