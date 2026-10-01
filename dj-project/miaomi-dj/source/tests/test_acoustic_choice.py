import numpy as np

from dj_agent import acoustic_choice as ac


def test_contextual_features_generalize_energy_compatibility():
    catalog = [{'id': c, 'title': c} for c in 'abcde']
    library = {c: {'bpm': 120., 'key': 'C major', 'duration': 200., 'energy': energy}
               for c, energy in zip('abcde', [.08, .1, .3, .33, .35], strict=True)}
    rows = [{'context': ['a'], 'label': 'b', 'candidates': ['b', 'c', 'd', 'e'],
             'provenance': {'source_video_id': '1'}},
            {'context': ['c'], 'label': 'd', 'candidates': ['a', 'b', 'd', 'e'],
             'provenance': {'source_video_id': '2'}}]
    model = ac.fit_acoustic(catalog, library, rows)
    ranked = ac.rank_acoustic(model, ['e'], ['a', 'd'])
    assert ranked[0]['track_id'] == 'd'
    assert model['optimization']['final_loss'] < model['optimization']['initial_loss']
    assert len(model['weights']) == len(ac.FEATURES)
    assert np.isclose(sum(r['probability'] for r in ranked), 1.)
