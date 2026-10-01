import json

import numpy as np
import pytest

from dj_agent.reference_choice import fit_choice, rank_choices, rank_metrics
from dj_agent.reference_data import prepare_dataset, source_folds


def write_references(root, sequences):
    sources, rows = [], []
    for sid, titles in sequences.items():
        url = f'https://www.douyin.com/video/{sid}'
        sources.append({'video_id': sid, 'url': url, 'evidence': 'public_page_visible_chapters',
                        'duration_seconds': len(titles) * 60, 'track_count': len(titles),
                        'chapters': [[f'{i:02d}:00', [t]] for i, t in enumerate(titles)]})
        for i in range(len(titles) - 1):
            rows.append({'schema_version': 1, 'id': f'{sid}-{i+1:02d}', 'source_video_id': sid,
                         'source_url': url, 'label_source': 'public_page_chapter_order',
                         'context_tracks': titles[max(0, i-2):i+1], 'observed_next_track': titles[i+1],
                         'from': {'normalized_title': titles[i], 'local_track_id': None},
                         'to': {'normalized_title': titles[i+1], 'local_track_id': None},
                         'human_preference': None, 'precise_mix_cue_seconds': None,
                         'source_song_cue_seconds': None, 'eq_fader_automation': None,
                         'audio_analyzed': False, 'personal_ranker_eligible': False})
    root.mkdir(exist_ok=True)
    (root / 'sources.json').write_text(json.dumps(sources), encoding='utf8')
    (root / 'reference-transitions.jsonl').write_text(
        '\n'.join(json.dumps(r) for r in rows), encoding='utf8')


def test_dataset_keeps_observed_labels_and_merges_case_variants(tmp_path):
    write_references(tmp_path, {'101': ['Alpha', 'Beta', 'Gamma'], '102': ['alpha', 'gamma', 'Delta']})
    catalog, rows = prepare_dataset(tmp_path)
    assert len(catalog) == 4
    assert len(rows) == 4
    titles = {r['id']: r['title'] for r in catalog}
    assert titles[rows[0]['label']] == 'Beta'
    assert rows[0]['context'] == rows[2]['context']
    assert rows[0]['label'] in rows[0]['candidates']
    assert not set(rows[0]['context']) & set(rows[0]['candidates'])
    assert rows[0]['supervision'] == 'weak_sequence_demonstration'
    assert all(v is None for v in rows[0]['unknown_labels'].values())
    assert rows[0]['provenance']['source_video_id'] == '101'


@pytest.mark.parametrize('mutation', ['duplicate', 'invented_quality', 'wrong_target', 'missing', 'wrong_url'])
def test_dataset_rejects_inconsistent_or_overclaimed_source(tmp_path, mutation):
    write_references(tmp_path, {'101': ['Alpha', 'Beta', 'Gamma']})
    path = tmp_path / 'reference-transitions.jsonl'
    rows = [json.loads(s) for s in path.read_text().splitlines()]
    if mutation == 'duplicate':
        rows.append(rows[0])
    elif mutation == 'invented_quality':
        rows[0]['human_preference'] = 'A'
    elif mutation == 'wrong_target':
        rows[0]['observed_next_track'] = 'Gamma'
    elif mutation == 'missing':
        rows.pop()
    else:
        rows[0]['source_url'] = 'https://www.douyin.com/video/999'
    path.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf8')
    with pytest.raises(ValueError):
        prepare_dataset(tmp_path)


def test_folds_purge_shared_directed_pairs_without_splitting_video(tmp_path):
    write_references(tmp_path, {'101': ['A', 'B', 'C'], '102': ['A', 'B', 'D'],
                                '103': ['C', 'D', 'A']})
    _, rows = prepare_dataset(tmp_path)
    folds = source_folds(rows)
    first = next(f for f in folds if f['held_out_video'] == '101')
    assert first['test_ids'] == ['101-01', '101-02']
    assert first['purged_ids'] == ['102-01']
    assert set(first['train_ids']) == {'102-02', '103-01', '103-02'}


def test_choice_learns_context_and_reload_excludes_played_tracks(tmp_path):
    write_references(tmp_path, {'101': ['A', 'B'], '102': ['C', 'D'], '103': ['E', 'F']})
    catalog, rows = prepare_dataset(tmp_path)
    model = fit_choice(catalog, rows)
    assert model['optimization']['final_loss'] < model['optimization']['initial_loss']
    restored = json.loads(json.dumps(model))
    for row in rows:
        prediction = rank_choices(restored, row['context'])
        assert prediction[0]['track_id'] == row['label']
        assert not set(row['context']) & {r['track_id'] for r in prediction}
        assert sum(r['probability'] for r in prediction) == pytest.approx(1)
        assert prediction == rank_choices(model, row['context'])
    with pytest.raises(ValueError, match='unknown'):
        rank_choices(model, ['missing-track'])
    model['weights'][0][0] = float('nan')
    with pytest.raises(ValueError):
        rank_choices(model, rows[0]['context'])


def test_choice_respects_available_library_and_does_not_invent_fallback(tmp_path):
    write_references(tmp_path, {'101': ['A', 'B', 'C'], '102': ['D', 'E', 'F']})
    catalog, rows = prepare_dataset(tmp_path)
    model = fit_choice(catalog, rows)
    wanted = rows[0]['label']
    result = rank_choices(model, rows[0]['context'], available=[wanted])
    assert result == [{'track_id': wanted, 'title': 'B', 'probability': 1.0}]
    with pytest.raises(ValueError, match='candidates'):
        rank_choices(model, rows[0]['context'], available=rows[0]['context'])


def test_equal_source_weight_is_recorded_and_deterministic(tmp_path):
    write_references(tmp_path, {'101': ['A', 'B', 'C', 'D'], '102': ['D', 'A']})
    catalog, rows = prepare_dataset(tmp_path)
    model = fit_choice(catalog, rows)
    assert model['source_total_weights'] == {'101': .5, '102': .5}
    assert np.allclose(model['weights'], fit_choice(catalog, rows)['weights'], rtol=0, atol=1e-12)


def test_evaluation_does_not_get_lucky_hits_from_alphabetical_ties():
    metrics = rank_metrics({'a': .4, 'b': .4, 'c': .2}, 'b')
    assert metrics['hit_at_1'] == .5
    assert metrics['hit_at_3'] == 1
    assert metrics['mrr'] == .75
    assert metrics['nll'] == pytest.approx(-np.log(.4))


def test_experiment_exports_reproducible_evidence_and_refuses_overwrite(tmp_path):
    from dj_agent.reference_experiment import run_reference_training
    refs, out = tmp_path / 'refs', tmp_path / 'model'
    write_references(refs, {'101': ['A', 'B', 'C'], '102': ['C', 'D', 'A'], '103': ['B', 'D', 'C']})
    report = run_reference_training(refs, out)
    assert report['observations'] == 6
    assert report['model_written'] is True
    assert len(report['evaluation']['folds']) == 3
    model = json.loads((out / 'model.json').read_text())
    assert model['deployment_status'] == 'experimental_not_enabled'
    predictions = [json.loads(s) for s in (out / 'heldout-predictions.jsonl').read_text().splitlines()]
    assert {p['id'] for p in predictions} == {'101-01', '101-02', '102-01', '102-02', '103-01', '103-02'}
    import hashlib
    checksums = json.loads((out / 'checksums.json').read_text())
    for filename, digest in checksums.items():
        assert hashlib.sha256((out / filename).read_bytes()).hexdigest() == digest
    with pytest.raises(ValueError, match='exists'):
        run_reference_training(refs, out)
    assert (out / 'acceptance.html').is_file()
    assert (out / 'dataset.schema.json').is_file()


def test_invalid_experiment_does_not_leave_partial_model(tmp_path):
    from dj_agent.reference_experiment import run_reference_training
    refs, out = tmp_path / 'refs', tmp_path / 'model'
    write_references(refs, {'101': ['A', 'B', 'C']})
    with pytest.raises(ValueError, match='sources'):
        run_reference_training(refs, out)
    assert not out.exists()
