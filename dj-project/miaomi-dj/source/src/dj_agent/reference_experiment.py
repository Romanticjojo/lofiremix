"""Reproducible CPU reference-choice experiments and offline acceptance artifacts."""

import importlib.metadata
import json
import platform
import shutil
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .preferences import file_sha256
from .reference_choice import fit_choice, rank_choices, rank_metrics, sample_weights
from .reference_data import UNKNOWN_LABELS, prepare_dataset, source_folds


def _dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in rows), encoding='utf8')


def dataset_schema(catalog):
    track = {'type': 'string', 'enum': [c['id'] for c in catalog]}
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'DJ reference choice v1',
            'type': 'object', 'additionalProperties': False,
            'required': ['schema_version', 'id', 'task', 'context', 'candidates', 'label', 'supervision',
                         'candidate_semantics', 'provenance', 'unknown_labels'],
            'properties': {
                'schema_version': {'const': 1}, 'id': {'type': 'string'},
                'task': {'const': 'next_track_choice'},
                'context': {'type': 'array', 'items': track, 'minItems': 1, 'maxItems': 3},
                'candidates': {'type': 'array', 'items': track, 'minItems': 1, 'uniqueItems': True},
                'label': track, 'supervision': {'const': 'weak_sequence_demonstration'},
                'candidate_semantics': {'const': 'unobserved_alternatives_not_bad_music'},
                'provenance': {'type': 'object', 'additionalProperties': False,
                               'required': ['source_video_id', 'source_url', 'label_source',
                                            'source_record_id', 'sources_sha256', 'reference_rows_sha256'],
                               'properties': {k: {'type': 'string'} for k in [
                                   'source_video_id', 'source_url', 'label_source', 'source_record_id',
                                   'sources_sha256', 'reference_rows_sha256']}},
                'unknown_labels': {'type': 'object', 'required': list(UNKNOWN_LABELS),
                                   'additionalProperties': False,
                                   'properties': {k: {'type': 'null'} for k in UNKNOWN_LABELS}}}}


def _messages(rows, catalog):
    lookup = {c['id']: {'id': c['id'], 'title': c['title']} for c in catalog}
    return [{'messages': [
        {'role': 'system', 'content': 'Choose the next track to imitate a reference playlist sequence. '
         'Return JSON with next_track_id from candidates. This task does not judge audio mixing quality.'},
        {'role': 'user', 'content': json.dumps({'context': [lookup[t] for t in r['context']],
                                               'candidates': [lookup[t] for t in r['candidates']]})},
        {'role': 'assistant', 'content': json.dumps({'next_track_id': r['label']})}]} for r in rows]


def _mean_metrics(items):
    return {key: float(np.mean([m[key] for m in items])) for key in ('hit_at_1', 'hit_at_3', 'mrr', 'nll')}


def _evaluate(catalog, rows, folds, staging):
    by_id = {r['id']: r for r in rows}
    results, predictions = [], []
    methods = ['model', 'popularity', 'uniform']
    for fold in folds:
        train = [by_id[i] for i in fold['train_ids']]
        test = [by_id[i] for i in fold['test_ids']]
        model = fit_choice(catalog, train)
        root = staging / 'folds' / fold['held_out_video']
        _dump(root / 'model.json', model)
        _jsonl(root / 'train.jsonl', train)
        _jsonl(root / 'test.jsonl', test)
        _jsonl(root / 'train-sft.jsonl', _messages(train, catalog))
        _jsonl(root / 'test-sft.jsonl', _messages(test, catalog))
        popularity = Counter()
        for row, weight in zip(train, sample_weights(train), strict=True):
            popularity[row['label']] += weight
        seen = {t for row in train for t in [*row['context'], row['label']]}
        metrics = {m: [] for m in methods}
        covered = 0
        for row in test:
            ranked = rank_choices(model, row['context'], row['candidates'])
            model_p = {r['track_id']: r['probability'] for r in ranked}
            pop = {c: popularity[c] + .001 for c in row['candidates']}
            pop_total = sum(pop.values())
            distributions = {'model': model_p, 'popularity': {c: p/pop_total for c, p in pop.items()},
                             'uniform': dict.fromkeys(row['candidates'], 1/len(row['candidates']))}
            item_metrics = {m: rank_metrics(distributions[m], row['label']) for m in methods}
            for method in methods:
                metrics[method].append(item_metrics[method])
            known = row['context'][-1] in seen and row['label'] in seen
            covered += known
            predictions.append({'id': row['id'], 'held_out_video': fold['held_out_video'],
                                'context': row['context'], 'label': row['label'],
                                'current_and_target_seen_in_train': known,
                                'candidate_count': len(row['candidates']), 'model_top5': ranked[:5],
                                'label_probability': model_p[row['label']], 'metrics': item_metrics})
        results.append({**fold, 'train_count': len(train), 'test_count': len(test),
                        'purged_count': len(fold['purged_ids']), 'seen_pair_endpoints': covered,
                        'metrics': {m: _mean_metrics(metrics[m]) for m in methods}})
    macro = {m: _mean_metrics([f['metrics'][m] for f in results]) for m in methods}
    micro = {m: _mean_metrics([p['metrics'][m] for p in predictions]) for m in methods}
    return {'method': 'leave_one_video_out_with_directed_pair_purge', 'folds': results,
            'macro_by_video': macro, 'micro_by_observation': micro,
            'known_vocabulary_shared': True, 'heldout_hyperparameter_tuning': False,
            'tie_handling': 'expected_metric_over_uniform_random_tie_breaks',
            'beats_both_baselines_macro_mrr': macro['model']['mrr'] > max(
                macro['uniform']['mrr'], macro['popularity']['mrr']),
            'independent_song_generalization_claimed': False}, predictions


def run_reference_training(reference_dir: Path, output: Path):
    reference_dir, output = Path(reference_dir), Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('training output already exists; choose a new directory')
    started = time.perf_counter()
    catalog, rows = prepare_dataset(reference_dir)
    folds = source_folds(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.reference-training-', dir=output.parent) as scratch:
        staging = Path(scratch) / 'result'
        staging.mkdir()
        _jsonl(staging / 'choice-dataset.jsonl', rows)
        _jsonl(staging / 'sft-messages.jsonl', _messages(rows, catalog))
        _jsonl(staging / 'sft-index.jsonl', [{'line_1_based': i+1, 'id': r['id'],
                                             'provenance': r['provenance']} for i, r in enumerate(rows)])
        _dump(staging / 'dataset.schema.json', dataset_schema(catalog))
        _dump(staging / 'catalog.json', catalog)
        _dump(staging / 'splits.json', folds)
        evaluation, predictions = _evaluate(catalog, rows, folds, staging)
        _jsonl(staging / 'heldout-predictions.jsonl', predictions)
        model = fit_choice(catalog, rows)
        model['dataset_sha256'] = file_sha256(staging / 'choice-dataset.jsonl')
        model['refit_on_all_observations'] = True
        _dump(staging / 'model.json', model)
        for _ in range(10):
            rank_choices(model, rows[0]['context'])
        durations = []
        for i in range(200):
            before = time.perf_counter()
            rank_choices(model, rows[i % len(rows)]['context'])
            durations.append((time.perf_counter() - before) * 1000)
        report = {'schema_version': 1, 'created_utc': datetime.now(UTC).isoformat(),
                  'status': 'trained_reference_choice_experiment', 'model_written': True,
                  'device': 'CPU', 'observations': len(rows), 'unique_tracks': len(catalog),
                  'unique_directed_pairs': len({(r['context'][-1], r['label']) for r in rows}),
                  'source_videos': len(folds), 'human_quality_labels_created': 0,
                  'parameter_count': model['parameter_count'], 'evaluation': evaluation,
                  'final_model_fitted_on_all_data': True,
                  'acceptance_status': 'requires_listening_and_more_independent_data',
                  'deployment_status': 'experimental_not_enabled',
                  'model_bytes': (staging / 'model.json').stat().st_size,
                  'dataset_sha256': model['dataset_sha256'],
                  'inference_latency_ms': {'repeats': 200, 'includes_json_weight_conversion': True,
                                           'median': float(np.median(durations)),
                                           'p95': float(np.percentile(durations, 95))},
                  'elapsed_seconds': time.perf_counter() - started}
        _dump(staging / 'report.json', report)
        environment = {'python': platform.python_version(), 'platform': platform.platform(),
                       'packages': {n: importlib.metadata.version(n) for n in ['numpy', 'scipy']},
                       'hyperparameters_selected_before_evaluation': True,
                       'training_randomness': 'none; zero initialization and deterministic L-BFGS-B',
                       'reproduce': 'python -m dj_agent train-reference REFERENCE_DIRECTORY --output NEW_DIRECTORY',
                       'implementation_sha256': {name: file_sha256(Path(__file__).with_name(name)) for name in
                                                 ['reference_data.py', 'reference_choice.py',
                                                  'reference_experiment.py', 'reference_review.html']}}
        _dump(staging / 'environment.json', environment)
        (staging / 'provenance').mkdir()
        for name in ['sources.json', 'reference-transitions.jsonl', 'local-candidates.json', 'summary.json']:
            if (reference_dir / name).exists():
                shutil.copyfile(reference_dir / name, staging / 'provenance' / name)
        template = Path(__file__).with_name('reference_review.html').read_text(encoding='utf8')
        embedded = json.dumps({'model': model, 'report': report, 'rows': rows}, ensure_ascii=False,
                              allow_nan=False).replace('<', '\\u003c').replace('&', '\\u0026')
        (staging / 'acceptance.html').write_text(template.replace('__REFERENCE_BUNDLE__', embedded),
                                                encoding='utf8')
        card = f'''# Reference Choice v1 — 模型验收说明

已在本地 CPU 训练，权重为 `model.json`，共 {model['parameter_count']} 个参数。
数据：{len(folds)} 段视频章节曲序，{len(rows)} 条示范，{len(catalog)} 首按标题归并的歌曲。

## 模型负责什么

输入最多三首前序歌曲，在给定歌单中预测参考曲序中的下一首。模型是带 L2 正则的条件 softmax，
特征为当前歌曲独热编码、衰减的两首历史歌曲及截距。每个来源总训练权重相同。
这不是 LLM / LoRA 微调，也没有学习音频、切点、人声退出、EQ 或混响。

## 如何验收

打开 `acceptance.html` 切换前序歌曲，查看实际权重计算出的排序。页面可离线运行。
其中模型用全部数据重新拟合；页面输出属于拟合数据演示，不能当独立测试成绩。
独立于每折训练来源的成绩见 `report.json` 和 `heldout-predictions.jsonl`。

评测轮流留出整段视频，再从训练集清除相同有向相邻曲对；不拆随机行制造泄漏。
词表在折间共享，歌曲也可重复，故只检验已知目录中的新曲序，不能证明新歌泛化。
每折的原始训练/测试数据和模型均保存在 `folds`。参数固定，没有据此测试集调参。

## 标签与边界

`label` 表示页面章节展示的下一首，不表示最优搭配。未选候选不是人工差评。
章节时间只是视频位置；分组章节里多首歌的准确边界未知。原素材未下载、未做音频分析。
标准 choice JSONL 和 JSON Schema 在本目录；chat messages 格式另存 `sft-messages.jsonl`，
逐行来源索引在 `sft-index.jsonl`。保留这些格式方便后续实验，不表示本次训练了大语言模型。
{len(rows)} 条记录来自仅 {len(folds)} 个视频，重复曲对并非独立样本。标题归并不能确认音频版本或艺人身份。
权重维持 experimental_not_enabled，不自动接管现有播放规划；真人 A/B 数据仍走单独的训练审计。

`checksums.json` 可核验全部导出文件。运行相同命令并指定新目录可复现数据划分和权重。
'''
        (staging / 'model-card.md').write_text(card, encoding='utf8')
        _dump(staging / 'checksums.json', {p.relative_to(staging).as_posix(): file_sha256(p)
                                         for p in sorted(staging.rglob('*')) if p.is_file()})
        staging.rename(output)
    return report
