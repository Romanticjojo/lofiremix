"""Nested source-held-out parameter study; preserves prior models and human labels."""

import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .preferences import file_sha256
from .reference_choice import fit_choice, rank_choices, rank_metrics, sample_weights
from .reference_data import prepare_dataset, source_folds
from .reference_experiment import _dump, _jsonl, _mean_metrics

GRID = tuple({'regularization': reg, 'context_length': length}
             for length in (1, 2, 3) for reg in (.005, .02, .05, .2, 1.))
DEFAULT = {'regularization': .05, 'context_length': 3}


def nested_splits(rows):
    by_id = {r['id']: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError('duplicate observation IDs')
    folds = source_folds(rows)
    for outer in folds:
        subset = [by_id[i] for i in outer['train_ids']]
        inner = []
        for sid in sorted({r['provenance']['source_video_id'] for r in subset}):
            validation = [r for r in subset if r['provenance']['source_video_id'] == sid]
            pairs = {(r['context'][-1], r['label']) for r in validation}
            eligible = [r for r in subset if r['provenance']['source_video_id'] != sid]
            purged = [r['id'] for r in eligible if (r['context'][-1], r['label']) in pairs]
            train = [r['id'] for r in eligible if r['id'] not in purged]
            if not train:
                raise ValueError('empty inner training split after pair purge')
            inner.append({'held_out_video': sid, 'train_ids': train,
                          'test_ids': [r['id'] for r in validation], 'purged_ids': purged})
        if len(inner) < 2:
            raise ValueError('need two inner source folds')
        outer['inner_folds'] = inner
    return folds


def _predictions(model, rows):
    result = []
    for row in rows:
        ranked = rank_choices(model, row['context'], row['candidates'])
        probabilities = {p['track_id']: p['probability'] for p in ranked}
        result.append({'id': row['id'], 'metrics': rank_metrics(probabilities, row['label']),
                       'top5': ranked[:5], 'label_probability': probabilities[row['label']]})
    return result


def _select_config(catalog, rows, inner_folds):
    by_id = {r['id']: r for r in rows}
    search = []
    for config in GRID:
        scores = []
        for split in inner_folds:
            train = [by_id[i] for i in split['train_ids']]
            valid = [by_id[i] for i in split['test_ids']]
            model = fit_choice(catalog, train, **config)
            metrics = _mean_metrics([p['metrics'] for p in _predictions(model, valid)])
            scores.append({'validation_video': split['held_out_video'], 'metrics': metrics,
                           'train_count': len(train), 'validation_count': len(valid)})
        search.append({'config': dict(config), 'inner_folds': scores,
                       'macro': _mean_metrics([s['metrics'] for s in scores])})
    ordered = sorted(search, key=lambda r: (-r['macro']['mrr'], r['macro']['nll'],
                                           r['config']['context_length'], -r['config']['regularization']))
    for position, row in enumerate(ordered, 1):
        row['selection_rank'] = position
    return dict(ordered[0]['config']), search


def run_parameter_study(reference_dir, output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('parameter study output exists; choose a new directory')
    started = time.perf_counter()
    catalog, rows = prepare_dataset(reference_dir)
    by_id = {r['id']: r for r in rows}
    splits = nested_splits(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.reference-tuning-', dir=output.parent) as scratch:
        staging = Path(scratch) / 'result'
        staging.mkdir()
        _dump(staging / 'protocol.json', {
            'parameter_grid': list(GRID), 'selection_metric': 'inner_macro_MRR',
            'tie_break': 'lower inner NLL, shorter context, stronger regularization',
            'outer_test_used_for_parameter_selection': False,
            'candidate_vote_rule': 'mode of inner-selected configurations; ties prefer shorter then stronger',
            'splitting': 'nested leave-one-video-out; directed target pairs purged at both levels',
            'known_vocabulary_shared': True, 'prior_dataset_already_inspected': True,
            'independent_new_external_test_set': False})
        _dump(staging / 'splits.json', splits)
        _dump(staging / 'catalog.json', catalog)
        _jsonl(staging / 'choice-dataset.jsonl', rows)
        outer_results, all_predictions, winners = [], [], []
        methods = ('tuned', 'original_parameters', 'popularity', 'uniform')
        for fold in splits:
            train = [by_id[i] for i in fold['train_ids']]
            test = [by_id[i] for i in fold['test_ids']]
            selected, search = _select_config(catalog, train, fold['inner_folds'])
            winners.append(selected)
            models = {'tuned': fit_choice(catalog, train, **selected),
                      'original_parameters': fit_choice(catalog, train, **DEFAULT)}
            predictions = {name: _predictions(model, test) for name, model in models.items()}
            metrics = {name: [] for name in methods}
            popularity = Counter()
            for row, weight in zip(train, sample_weights(train), strict=True):
                popularity[row['label']] += weight
            seen = {t for r in train for t in [*r['context'], r['label']]}
            for i, row in enumerate(test):
                pop = {c: popularity[c]+.001 for c in row['candidates']}
                total = sum(pop.values())
                item = {'id': row['id'], 'test_video': fold['held_out_video'], 'context': row['context'],
                        'label': row['label'], 'candidate_count': len(row['candidates']),
                        'current_and_target_seen': row['context'][-1] in seen and row['label'] in seen,
                        'predictions': {name: predictions[name][i] for name in models}}
                item['metrics'] = {name: predictions[name][i]['metrics'] for name in models}
                item['metrics']['popularity'] = rank_metrics({c: p/total for c, p in pop.items()}, row['label'])
                item['metrics']['uniform'] = rank_metrics(
                    dict.fromkeys(row['candidates'], 1/len(row['candidates'])), row['label'])
                for name in methods:
                    metrics[name].append(item['metrics'][name])
                all_predictions.append(item)
            for name, model in models.items():
                _dump(staging / 'folds' / fold['held_out_video'] / f'{name}.json', model)
            outer_results.append({**fold, 'train_count': len(train), 'test_count': len(test),
                                  'selected_config': selected, 'inner_search': search,
                                  'metrics': {name: _mean_metrics(values) for name, values in metrics.items()}})
        counts = Counter((c['context_length'], c['regularization']) for c in winners)
        length, reg = min(counts, key=lambda pair: (-counts[pair], pair[0], -pair[1]))
        selected = {'context_length': length, 'regularization': reg}
        final_model = fit_choice(catalog, rows, **selected)
        final_model['dataset_sha256'] = file_sha256(staging / 'choice-dataset.jsonl')
        final_model['refit_on_all_observations'] = True
        _dump(staging / 'candidate-model.json', final_model)
        _jsonl(staging / 'outer-predictions.jsonl', all_predictions)
        grid_summary = []
        for config in GRID:
            observations = [s['macro'] for f in outer_results for s in f['inner_search'] if s['config'] == config]
            grid_summary.append({'config': dict(config), 'inner_macro_average': _mean_metrics(observations),
                                 'selected_outer_folds': sum(c == config for c in winners)})
        _dump(staging / 'parameter-grid.json', grid_summary)
        macro = {m: _mean_metrics([f['metrics'][m] for f in outer_results]) for m in methods}
        micro = {m: _mean_metrics([p['metrics'][m] for p in all_predictions]) for m in methods}
        fitted_metrics = _mean_metrics([p['metrics'] for p in _predictions(final_model, rows)])
        report = {'schema_version': 1, 'created_utc': datetime.now(UTC).isoformat(),
                  'configuration_count': len(GRID), 'observations': len(rows), 'unique_tracks': len(catalog),
                  'outer_folds': outer_results, 'macro_by_video': macro, 'micro_by_observation': micro,
                  'original_config': DEFAULT, 'candidate_config': selected,
                  'candidate_parameter_count': final_model['parameter_count'],
                  'selection_agreement': max(counts.values())/len(winners),
                  'final_selection_uses_outer_test_scores': False,
                  'fitted_data_metrics_not_test': fitted_metrics,
                  'known_endpoints_test_rows': sum(p['current_and_target_seen'] for p in all_predictions),
                  'deployment_status': 'experimental_not_enabled',
                  'elapsed_seconds': time.perf_counter()-started,
                  'limitation': 'Only three previously inspected chapter sequences; exploratory nested CV, '
                  'not a new external test and not a mixing quality evaluation.'}
        _dump(staging / 'report.json', report)
        summary = ['# 参考曲序模型参数测试', '',
                   f"测试 {len(GRID)} 组参数，{len(rows)} 条示范、{len(catalog)} 首标题。", '',
                   '每轮最外层留出整段视频；只用其余视频的内层验证挑参数。两层均剔除相同有向曲对。',
                   '这是已观察过的同一小数据集上的探索性重测，不是新采集的独立外部验收。', '',
                   '| 按视频平均 | Top 1 | Top 3 | MRR | 概率损失（越低越好） |',
                   '|---|---:|---:|---:|---:|']
        names = {'tuned': '内层选参流程', 'original_parameters': '原参数',
                 'popularity': '常见下一首', 'uniform': '均匀随机'}
        for key in methods:
            m = macro[key]
            summary.append(f"| {names[key]} | {m['hit_at_1']:.2%} | {m['hit_at_3']:.2%} | {m['mrr']:.4f} | {m['nll']:.4f} |")
        summary.extend(['', '## 各轮选择', '', '| 留出视频 | 训练/测试条数 | 上下文首数 | 正则强度 | 测试 Top 3 |',
                        '|---|---:|---:|---:|---:|'])
        for f in outer_results:
            c = f['selected_config']
            summary.append(f"| {f['held_out_video']} | {f['train_count']}/{f['test_count']} | {c['context_length']} | {c['regularization']} | {f['metrics']['tuned']['hit_at_3']:.2%} |")
        summary.extend(['', '## 暂定候选参数', '',
                        f"上下文 {length} 首；L2 正则 {reg}；{final_model['parameter_count']} 个参数。",
                        f"选择一致率 {report['selection_agreement']:.0%}。由三轮内层选择投票得到；并列时取更短上下文、更强正则。",
                        '候选在全量数据重新拟合后保存为 candidate-model.json，原模型不覆盖，也不自动接管播放。',
                        f"候选全量拟合 Top 3 为 {fitted_metrics['hit_at_3']:.2%}，该数字只反映记忆训练数据，不能当测试成绩。",
                        f"61 条并非独立样本；本次实际 {len(rows)} 条测试记录中，训练同时见过当前歌和目标歌的有 {report['known_endpoints_test_rows']} 条。",
                        '不同参考对同一首歌可能有不同下一首；参考答案不是唯一音乐上合理的选择。',
                        '切点、人声和效果器标签仍未知；本报告不能用于判断混音听感。', '',
                        '## 证据', '', '完整网格：parameter-grid.json；划分：splits.json；逐例预测：outer-predictions.jsonl；',
                        '各轮权重：folds/；文件校验：checksums.json。'])
        (staging/'summary.md').write_text('\n'.join(summary)+'\n', encoding='utf8')
        _dump(staging/'implementation.json', {name: file_sha256(Path(__file__).with_name(name)) for name in
                                              ['reference_choice.py', 'reference_data.py', 'reference_tuning.py']})
        _dump(staging/'checksums.json', {p.relative_to(staging).as_posix(): file_sha256(p)
                                       for p in sorted(staging.rglob('*')) if p.is_file()})
        staging.rename(output)
    return report
