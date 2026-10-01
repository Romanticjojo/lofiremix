import hashlib
import json

import numpy as np
import pytest
from test_reference_choice import write_references

from dj_agent.reference_choice import fit_choice, rank_choices
from dj_agent.reference_data import prepare_dataset


def fixture_data(path):
    write_references(path, {'101': ['A', 'B', 'C', 'D'], '102': ['B', 'A', 'D', 'C'],
                            '103': ['C', 'A', 'B', 'D']})
    return prepare_dataset(path)


def test_context_length_controls_predictions_and_parameter_count(tmp_path):
    catalog, rows = fixture_data(tmp_path)
    one = fit_choice(catalog, rows, regularization=.2, context_length=1)
    two = fit_choice(catalog, rows, regularization=.2, context_length=2)
    ids = {c['title']: c['id'] for c in catalog}
    assert one['parameter_count'] == 20
    assert two['parameter_count'] == 36
    # Keep candidate availability equal, so this isolates history dependence.
    available = [ids['C'], ids['D']]
    assert rank_choices(one, [ids['A'], ids['B']], available) == rank_choices(one, [ids['B']], available)
    assert rank_choices(two, [ids['A'], ids['B']], available) != rank_choices(two, [ids['B']], available)
    restored = json.loads(json.dumps(one))
    assert rank_choices(restored, [ids['B']]) == rank_choices(one, [ids['B']])


def test_stronger_regularization_reduces_weight_norm(tmp_path):
    catalog, rows = fixture_data(tmp_path)
    weak = fit_choice(catalog, rows, regularization=.005)
    strong = fit_choice(catalog, rows, regularization=1)
    assert np.linalg.norm(weak['weights']) > np.linalg.norm(strong['weights'])


@pytest.mark.parametrize('kwargs', [{'regularization': 0}, {'regularization': float('nan')},
                                   {'regularization': True}, {'context_length': 0},
                                   {'context_length': 4}, {'context_length': True}])
def test_invalid_hyperparameters_rejected(tmp_path, kwargs):
    catalog, rows = fixture_data(tmp_path)
    with pytest.raises(ValueError):
        fit_choice(catalog, rows, **kwargs)


def test_legacy_v1_models_still_rank_identically(tmp_path):
    catalog, rows = fixture_data(tmp_path)
    model = fit_choice(catalog, rows)
    old = json.loads(json.dumps(model))
    old['feature_version'] = 1
    old.pop('context_length', None)
    assert rank_choices(old, rows[0]['context']) == rank_choices(model, rows[0]['context'])


def test_nested_splits_keep_outer_video_out_of_every_tuning_step(tmp_path):
    from dj_agent.reference_tuning import nested_splits
    _, rows = fixture_data(tmp_path)
    folds = nested_splits(rows)
    by_id = {r['id']: r for r in rows}
    for fold in folds:
        outer_train, outer_test = set(fold['train_ids']), set(fold['test_ids'])
        assert not outer_train & outer_test
        outer_pairs = {(by_id[i]['context'][-1], by_id[i]['label']) for i in outer_test}
        assert not outer_pairs & {(by_id[i]['context'][-1], by_id[i]['label']) for i in outer_train}
        for inner in fold['inner_folds']:
            assert set(inner['train_ids']) <= outer_train
            assert set(inner['test_ids']) <= outer_train
            assert not set(inner['train_ids']) & set(inner['test_ids'])
            validation_pairs = {(by_id[i]['context'][-1], by_id[i]['label']) for i in inner['test_ids']}
            assert not validation_pairs & {(by_id[i]['context'][-1], by_id[i]['label'])
                                           for i in inner['train_ids']}
            assert {by_id[i]['provenance']['source_video_id'] for i in inner['test_ids']} == {
                inner['held_out_video']}


def test_tuning_exports_complete_disjoint_evidence_without_replacing_prior(tmp_path):
    from dj_agent.reference_tuning import run_parameter_study
    refs, output = tmp_path / 'refs', tmp_path / 'results'
    fixture_data(refs)
    prior = tmp_path / 'original-model.json'
    prior.write_text('{"untouched":true}', encoding='utf8')
    report = run_parameter_study(refs, output)
    assert report['configuration_count'] == 15
    assert len(report['outer_folds']) == 3
    assert sum(f['test_count'] for f in report['outer_folds']) == 9
    assert report['final_selection_uses_outer_test_scores'] is False
    assert report['deployment_status'] == 'experimental_not_enabled'
    assert prior.read_text() == '{"untouched":true}'
    for fold in report['outer_folds']:
        best = min(fold['inner_search'], key=lambda c: c['selection_rank'])
        assert fold['selected_config'] == best['config']
    checksums = json.loads((output / 'checksums.json').read_text())
    for name, sha in checksums.items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == sha
    predictions = [json.loads(s) for s in (output / 'outer-predictions.jsonl').read_text().splitlines()]
    assert len(predictions) == 9
    assert len({p['id'] for p in predictions}) == 9
    with pytest.raises(ValueError, match='exists'):
        run_parameter_study(refs, output)
