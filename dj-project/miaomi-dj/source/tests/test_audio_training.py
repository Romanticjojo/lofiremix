import json

import numpy as np
import pytest

from dj_agent import audio_training as at


def test_cue_split_purges_test_song_even_in_another_video():
    def row(i, source, track):
        return {'id': i, 'context': [track], 'provenance': {'source_video_id': source}}
    rows = [row('1', 'v1', 'a'), row('2', 'v2', 'a'), row('3', 'v2', 'b'),
            row('4', 'v3', 'c')]
    split = at.cue_splits(rows)[0]
    assert split['test_ids'] == ['1']
    assert split['train_ids'] == ['3', '4']
    assert split['purged_ids'] == ['2']


def test_ridge_regularizes_and_serialized_prediction_is_bounded():
    x = np.array([[0., 0.], [1., 0.], [2., 0.], [3., 0.]])
    y = np.array([.1, .3, .5, .7])
    model = at.fit_cue(x, y)
    predictions = at.predict_cue(model, x)
    assert np.mean(abs(predictions-y)) < np.mean(abs(y-np.median(y)))
    assert at.predict_cue(model, np.array([[100., 0.]]))[0] <= 1.
    assert at.predict_cue(model, np.array([[-100., 0.]]))[0] >= 0.


def test_cue_fit_rejects_nonfinite_targets():
    with pytest.raises(ValueError):
        at.fit_cue(np.array([[1.], [2.]]), np.array([.5, np.nan]))


@pytest.mark.parametrize('cue_count', [0, 2])
def test_choice_report_survives_insufficient_cue_evaluation(tmp_path, cue_count):
    dataset = tmp_path/'data'
    dataset.mkdir()
    catalog = [{'id': t, 'title': t} for t in 'abcd']
    library = [{'id': t, 'bpm': 120., 'duration': 200., 'energy': .2, 'key': 'C major'} for t in 'abcd']
    (dataset/'catalog.json').write_text(json.dumps(catalog))
    (dataset/'library.json').write_text(json.dumps(library))
    (dataset/'protocol.json').write_text('{}')
    rows = []
    for i, t in enumerate('abcd'):
        rows.append({'id': str(i), 'context': [t], 'label': 'bcda'[i],
                     'candidates': [c for c in 'abcd' if c != t], 'supervision': 'weak_audio_alignment',
                     'source_exit_seconds_estimate': 80., 'source_exit_cue_eligible': i < cue_count,
                     'provenance': {'source_video_id': f'video{i}'}})
    (dataset/'choice-dataset.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    report = at.run_audio_training(dataset, tmp_path/'result')
    assert report['observations'] == 4
    assert report['cue_macro_mae_seconds'] is None
    assert report['cue_test_count'] == 0
    assert report['cue_status'] == ('trained' if cue_count == 2 else 'insufficient_training_data')
    assert (tmp_path/'result'/'choice-model.json').is_file()
