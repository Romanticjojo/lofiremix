"""Train and audit small reference imitation models without deploying them."""

import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .acoustic_choice import fit_acoustic, rank_acoustic
from .preferences import file_sha256
from .reference_choice import fit_choice, rank_choices, rank_metrics, sample_weights
from .reference_data import source_folds
from .reference_experiment import _dump, _jsonl, _mean_metrics

REGULARIZATIONS = (.02, .05, .2, 1.)
CHOICE_METHODS = ('model', 'acoustic', 'popularity', 'uniform')
CUE_FEATURES = ('source_bpm', 'source_duration', 'source_energy', 'next_bpm',
                'next_duration', 'next_energy')


def cue_splits(rows):
    splits = []
    for source in sorted({r['provenance']['source_video_id'] for r in rows}):
        test = [r for r in rows if r['provenance']['source_video_id'] == source]
        songs = {r['context'][-1] for r in test}
        eligible = [r for r in rows if r['provenance']['source_video_id'] != source]
        splits.append({'held_out_video': source, 'test_ids': [r['id'] for r in test],
                       'train_ids': [r['id'] for r in eligible if r['context'][-1] not in songs],
                       'purged_ids': [r['id'] for r in eligible if r['context'][-1] in songs]})
    return splits


def fit_cue(x, y, weights=None, regularization=1.):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if (x.ndim != 2 or y.shape != (len(x),) or len(x) < 2
            or not np.isfinite(x).all() or not np.isfinite(y).all()
            or np.any((y < 0) | (y > 1)) or regularization <= 0):
        raise ValueError('invalid cue training data')
    weights = np.ones(len(x))/len(x) if weights is None else np.asarray(weights, dtype=float)
    if weights.shape != y.shape or not np.isfinite(weights).all() or np.any(weights <= 0):
        raise ValueError('invalid cue weights')
    weights = weights/weights.sum()
    mean = weights @ x
    scale = np.sqrt(weights @ ((x-mean)**2))
    scale[scale < 1e-8] = 1.
    z = np.column_stack([np.ones(len(x)), (x-mean)/scale])
    penalty = np.eye(z.shape[1])*regularization
    penalty[0, 0] = 0
    coefficients = np.linalg.solve(z.T @ (z*weights[:, None])+penalty, z.T @ (y*weights))
    return {'model_type': 'ridge_source_exit_fraction', 'schema_version': 1,
            'mean': mean.tolist(), 'scale': scale.tolist(), 'coefficients': coefficients.tolist(),
            'regularization': regularization, 'training_observations': len(x),
            'parameter_count': len(coefficients), 'deployment_status': 'experimental_not_enabled'}


def predict_cue(model, x):
    x = np.asarray(x, dtype=float)
    z = np.column_stack([np.ones(len(x)), (x-np.array(model['mean']))/np.array(model['scale'])])
    return np.clip(z @ np.array(model['coefficients']), 0., 1.)


def _cue_data(rows, library):
    x, y = [], []
    for r in rows:
        a, b = library[r['context'][-1]], library[r['label']]
        x.append([a['bpm'], a['duration'], a['energy'], b['bpm'], b['duration'], b['energy']])
        y.append(r['source_exit_seconds_estimate']/a['duration'])
    return np.asarray(x), np.asarray(y)


def _choice_metrics(model, rows):
    return _mean_metrics([rank_metrics({p['track_id']: p['probability'] for p in
                                        rank_choices(model, r['context'], r['candidates'])}, r['label'])
                          for r in rows])


def _select_regularization(catalog, train):
    index = {r['id']: r for r in train}
    folds = source_folds(train)
    grid = []
    for reg in REGULARIZATIONS:
        metrics = []
        for split in folds:
            fitted = fit_choice(catalog, [index[i] for i in split['train_ids']],
                                regularization=reg, context_length=1)
            metrics.append(_choice_metrics(fitted, [index[i] for i in split['test_ids']]))
        grid.append({'regularization': reg, 'macro': _mean_metrics(metrics)})
    selected = min(grid, key=lambda r: (-r['macro']['mrr'], r['macro']['nll'], -r['regularization']))
    return selected['regularization'], grid


def run_audio_training(dataset, output):
    dataset, output = Path(dataset), Path(output)
    if output.exists():
        raise ValueError('training output already exists; choose a new version')
    catalog = json.loads((dataset/'catalog.json').read_text(encoding='utf8'))
    rows = [json.loads(line) for line in (dataset/'choice-dataset.jsonl').read_text(encoding='utf8').splitlines()]
    library = {r['id']: r for r in json.loads((dataset/'library.json').read_text(encoding='utf8'))}
    ids = {r['id'] for r in catalog}
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('empty or duplicate observations')
    for row in rows:
        if (row['supervision'] != 'weak_audio_alignment' or len(row['context']) != 1
                or row['label'] not in ids or not set(row['context']) <= ids
                or any(row.get(key) is not None for key in ('human_preference', 'effects', 'vocal_overlap'))):
            raise ValueError('unsupported or invented labels')
    splits = source_folds(rows)
    output.mkdir(parents=True)
    _dump(output/'protocol.json', {
        'created_utc': datetime.now(UTC).isoformat(), 'choice_regularization_grid': REGULARIZATIONS,
        'choice_context_length': 1, 'selection': 'inner_video_heldout_MRR_then_NLL_then_stronger_L2',
        'evaluation': 'outer_video_heldout_directed_pair_purged',
        'cue_evaluation': 'video_heldout_also_purge_outgoing_song_identity',
        'cue_regularization': 1., 'labels': 'unreviewed_weak_audio_alignment',
        'acoustic_regularization': 1., 'acoustic_parameters': 6,
        'acoustic_comparison': 'additional fixed-configuration experiment added after inspecting identity-model v2; exploratory',
        'independent_human_ground_truth': False, 'test_scores_used_to_select_final_model': False,
        'dataset_sha256': file_sha256(dataset/'choice-dataset.jsonl'),
        'library_sha256': file_sha256(dataset/'library.json'),
        'alignment_protocol_sha256': file_sha256(dataset/'protocol.json'),
        'limitations': ['Small related recordings; residual shared performance excerpts possible.',
                        'Local closed 25-track vocabulary; missing-library songs remain unknown.',
                        'Alternatives are not negative quality labels; this is imitation, not preference.',
                        'No ground-truth mixer actions, exact overlap, effects or human ratings.']})
    _jsonl(output/'train-all.jsonl', rows)
    _dump(output/'library.json', list(library.values()))
    _dump(output/'alignment-protocol.json', json.loads((dataset/'protocol.json').read_text(encoding='utf8')))
    _dump(output/'catalog.json', catalog)
    by_id = {r['id']: r for r in rows}
    results, predictions, selected_regs = [], [], []
    for fold in splits:
        train, test = [by_id[i] for i in fold['train_ids']], [by_id[i] for i in fold['test_ids']]
        reg, search = _select_regularization(catalog, train)
        selected_regs.append(reg)
        model = fit_choice(catalog, train, regularization=reg, context_length=1)
        acoustic = fit_acoustic(catalog, library, train, regularization=1.)
        popularity = Counter()
        for r, weight in zip(train, sample_weights(train), strict=True):
            popularity[r['label']] += weight
        fold_predictions = []
        for row in test:
            ranked = rank_choices(model, row['context'], row['candidates'])
            acoustic_ranked = rank_acoustic(acoustic, row['context'], row['candidates'])
            pop = {c: popularity[c]+.001 for c in row['candidates']}
            total = sum(pop.values())
            distributions = {'model': {p['track_id']: p['probability'] for p in ranked},
                             'acoustic': {p['track_id']: p['probability'] for p in acoustic_ranked},
                             'popularity': {c: p/total for c, p in pop.items()},
                             'uniform': dict.fromkeys(row['candidates'], 1/len(row['candidates']))}
            item = {'id': row['id'], 'held_out_video': fold['held_out_video'], 'top5': ranked[:5],
                    'acoustic_top5': acoustic_ranked[:5],
                    'metrics': {m: rank_metrics(p, row['label']) for m, p in distributions.items()}}
            predictions.append(item)
            fold_predictions.append(item)
        result = {**fold, 'selected_regularization': reg, 'inner_search': search,
                  'metrics': {m: _mean_metrics([p['metrics'][m] for p in fold_predictions])
                              for m in CHOICE_METHODS}}
        results.append(result)
        _dump(output/'folds'/fold['held_out_video']/'choice-model.json', model)
        _dump(output/'folds'/fold['held_out_video']/'acoustic-model.json', acoustic)
        _jsonl(output/'folds'/fold['held_out_video']/'train.jsonl', train)
        _jsonl(output/'folds'/fold['held_out_video']/'test.jsonl', test)
    votes = Counter(selected_regs)
    chosen = min(votes, key=lambda r: (-votes[r], -r))
    model = fit_choice(catalog, rows, regularization=chosen, context_length=1)
    model.update(objective='imitate_audio_aligned_next_track_not_mixing_quality',
                 dataset_sha256=file_sha256(output/'train-all.jsonl'), refit_on_all_observations=True)
    _dump(output/'choice-model.json', model)
    acoustic = fit_acoustic(catalog, library, rows, regularization=1.)
    acoustic['dataset_sha256'] = file_sha256(output/'train-all.jsonl')
    _dump(output/'acoustic-model.json', acoustic)
    _jsonl(output/'choice-heldout-predictions.jsonl', predictions)
    _dump(output/'choice-splits.json', results)
    # Cue estimates outside their source files cannot be cue regression labels.
    cue_rows = [r for r in rows if r.get('source_exit_cue_eligible', False)]
    cue_index = {r['id']: r for r in cue_rows}
    cue_folds, cue_predictions = [], []
    for fold in cue_splits(cue_rows):
        train = [cue_index[i] for i in fold['train_ids']]
        test = [cue_index[i] for i in fold['test_ids']]
        if len(train) < 3:
            cue_folds.append({**fold, 'status': 'insufficient_training_data'})
            continue
        x, y = _cue_data(train, library)
        fitted = fit_cue(x, y, sample_weights(train))
        xt, yt = _cue_data(test, library)
        estimated = predict_cue(fitted, xt)
        baseline = float(np.median(y))
        fp = []
        for row, pred, truth in zip(test, estimated, yt, strict=True):
            duration = library[row['context'][-1]]['duration']
            fp.append({'id': row['id'], 'estimate_seconds': float(pred*duration),
                       'weak_label_seconds': float(truth*duration),
                       'absolute_error_seconds': float(abs(pred-truth)*duration),
                       'baseline_absolute_error_seconds': float(abs(baseline-truth)*duration)})
        cue_predictions.extend(fp)
        cue_folds.append({**fold, 'status': 'evaluated',
                          'mae_seconds': float(np.mean([r['absolute_error_seconds'] for r in fp])),
                          'baseline_mae_seconds': float(np.mean([r['baseline_absolute_error_seconds'] for r in fp]))})
        _dump(output/'folds'/fold['held_out_video']/'cue-model.json', fitted)
    if len(cue_rows) >= 2:
        x, y = _cue_data(cue_rows, library)
        cue_model = fit_cue(x, y, sample_weights(cue_rows))
        cue_model.update(features=CUE_FEATURES, target='estimated_source_exit_seconds / source_duration',
                         dataset_sha256=file_sha256(output/'train-all.jsonl'), status='trained')
    else:
        cue_model = {'status': 'insufficient_training_data', 'training_observations': len(cue_rows),
                     'deployment_status': 'not_trainable', 'weights': None}
    _dump(output/'cue-model.json', cue_model)
    _dump(output/'cue-splits.json', cue_folds)
    _jsonl(output/'cue-heldout-predictions.jsonl', cue_predictions)
    _jsonl(output/'cue-train-all.jsonl', cue_rows)
    # Compatible conversation-format export, with a line index for provenance; no LLM fine-tuning here.
    _jsonl(output/'sft-messages.jsonl', [{'messages': [
        {'role': 'system', 'content': 'Imitate the next track in a reference DJ sequence. Return next_track_id as JSON. This is weak demonstration, not quality judgment.'},
        {'role': 'user', 'content': json.dumps({'context': r['context'], 'candidates': r['candidates']})},
        {'role': 'assistant', 'content': json.dumps({'next_track_id': r['label']})}]} for r in rows])
    _jsonl(output/'sft-index.jsonl', [{'line_1_based': i+1, 'id': r['id'], 'provenance': r['provenance']}
                                    for i, r in enumerate(rows)])
    timings = []
    for i in range(220):
        before = time.perf_counter()
        rank_choices(model, rows[i % len(rows)]['context'])
        if i >= 20:
            timings.append((time.perf_counter()-before)*1000)
    macro = {m: _mean_metrics([f['metrics'][m] for f in results]) for m in CHOICE_METHODS}
    evaluated_cues = [f for f in cue_folds if f['status'] == 'evaluated']
    report = {'schema_version': 1, 'source_count': len(splits), 'observations': len(rows),
              'unique_directed_pairs': len({(r['context'][-1], r['label']) for r in rows}),
              'catalog_size': len(catalog), 'model_parameters': model['parameter_count'],
              'selected_regularization': chosen, 'context_length': 1, 'inner_winner_votes': dict(votes),
              'choice_macro_by_video': macro, 'fitted_metrics_not_test': _choice_metrics(model, rows),
              'cue_observations': len(cue_rows), 'cue_test_count': len(cue_predictions),
              'cue_status': cue_model['status'],
              'cue_macro_mae_seconds': (float(np.mean([f['mae_seconds'] for f in evaluated_cues]))
                                        if evaluated_cues else None),
              'cue_baseline_macro_mae_seconds': (float(np.mean([f['baseline_mae_seconds'] for f in evaluated_cues]))
                                                 if evaluated_cues else None),
              'decision_latency_ms_median': float(np.median(timings)),
              'decision_latency_ms_p95': float(np.percentile(timings, 95)),
              'beats_both_choice_baselines_mrr': macro['model']['mrr'] > max(macro['popularity']['mrr'], macro['uniform']['mrr']),
              'deployment_status': 'experimental_not_enabled',
              'claim': 'Models trained; scores are agreement with weak automatic labels, not listening quality.'}
    _dump(output/'report.json', report)
    _dump(output/'implementation.json', {name: file_sha256(Path(__file__).with_name(name)) for name in
                                        ('audio_training.py', 'reference_audio.py', 'reference_choice.py', 'acoustic_choice.py')})
    _dump(output/'checksums.json', {p.relative_to(output).as_posix(): file_sha256(p)
                                   for p in sorted(output.rglob('*')) if p.is_file()})
    return report
