import numpy as np

from dj_agent import reference_audio as ra


def features(seed, frames=180):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(frames, 12)).astype('float32')
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def test_match_finds_recording_offset_and_pitch_and_rejects_unknown():
    a, b = features(1), features(2)
    query = np.roll(a[50:78], 1, axis=1)
    result = ra.match_windows({'a': a, 'b': b}, np.stack([query, features(4, 28)]),
                              speeds=(1.,), shifts=(-1, 0, 1))
    assert result[0]['track_id'] == 'a'
    assert result[0]['source_start'] == 50
    assert result[0]['pitch_shift'] == 1
    assert result[0]['score'] > .99
    assert result[0]['accepted']
    assert not result[1]['accepted']


def test_silent_and_duplicate_tracks_are_not_confident():
    a = features(1)
    result = ra.match_windows({'a': a, 'b': a.copy()}, np.stack([a[:28], np.zeros((28, 12))]),
                              speeds=(1.,), shifts=(0,))
    assert not any(row['accepted'] for row in result)


def rows(track, centers, offset=20):
    return [dict(track_id=track, center=float(t), source_start=float(t - 14 + offset),
                 score=.95, margin=.2, position_margin=.2, speed=1., pitch_shift=0, accepted=True)
            for t in centers]


def test_spans_preserve_unknown_gaps_and_cues_are_intervals():
    matches = rows('a', [20, 26, 32, 38]) + rows('b', [56, 62, 68, 74]) + rows('c', [150, 156, 162])
    spans = ra.build_spans(matches)
    transitions = ra.transition_rows('v', spans, ['a', 'b', 'c'])
    assert len(transitions) == 1
    r = transitions[0]
    assert r['context'] == ['a'] and r['label'] == 'b'
    assert r['boundary_interval_seconds'] == [38., 56.]
    assert r['source_exit_seconds_estimate'] == 67.
    assert r['supervision'] == 'weak_audio_alignment'
    assert r['human_preference'] is None and r['effects'] is None


def test_source_position_jumps_do_not_form_a_coherent_span():
    matches = rows('a', [20, 26, 32])
    matches[1]['source_start'] += 60
    assert ra.build_spans(matches) == []


def test_repeated_passage_keeps_song_identity_but_rejects_source_cue():
    a, b = features(1), features(2)
    a[90:118] = a[30:58]
    result = ra.match_windows({'a': a, 'b': b}, a[30:58][None, ...], speeds=(1.,), shifts=(0,))[0]
    assert result['accepted']
    assert result['position_margin'] < .001
    matches = rows('a', [20, 26, 32])
    for r in matches:
        r['position_margin'] = .001
    span = ra.build_spans(matches)[0]
    assert not span['exit_position_confident']
