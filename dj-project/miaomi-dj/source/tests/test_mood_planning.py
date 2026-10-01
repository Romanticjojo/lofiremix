import json
from pathlib import Path


def test_mood_catalog_is_sourced_and_distinguishes_inference():
    path = Path(__file__).parents[1]/'resources/weeknd-moods.json'
    data = json.loads(path.read_text(encoding='utf8'))
    assert len(data['tracks']) == 25
    for entry in data['tracks'].values():
        assert entry['evidence_level'] in {'song_review', 'album_context_inference'}
        assert entry['source_ids']
        assert 0 <= entry['party_energy'] <= 1
        assert entry['score_origin'] == 'editorial_heuristic_not_human_training_label'
        assert all(data['sources'][s]['url'].startswith('https://') for s in entry['source_ids'])


def test_party_curve_has_peak_and_recovery():
    from dj_agent.mood import party_target
    values = [party_target(i, 10)[0] for i in range(10)]
    assert values[3] > values[0]
    assert values[4] < values[3]
    assert values[7] > values[4]
    assert values[-1] < values[7]
