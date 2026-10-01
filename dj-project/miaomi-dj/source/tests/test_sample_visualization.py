import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest
import soundfile as sf


def test_timeline_uses_sample_accurate_intervals_and_real_fader_curves():
    from dj_agent.sample_visualization import timeline_data
    plan = {'tracks': [{'track_id': 'a', 'title': 'A', 'source_start': 0, 'source_end': 10, 'rate': 1,
                        'bpm': 120, 'key': 'C major', 'output_start': 0},
                       {'track_id': 'b', 'title': 'B', 'source_start': 2, 'source_end': 12, 'rate': 1,
                        'bpm': 120, 'key': 'C major', 'output_start': 8}],
            'transitions': [{'id': 'transition-01', 'output_start': 8, 'overlap_seconds': 2,
                             'mode': 'eq_blend', 'phase_aligned': True}], 'duration_seconds': 18}
    metrics = {'sample_rate': 48000, 'duration_seconds': 18, 'vocal_controls': [
        {'transition_id': 'transition-01', 'applied': True, 'fade_start_output_seconds': 8.5,
         'exit_output_seconds': 9.25}]}
    data = timeline_data(plan, metrics)
    assert [(t['start'], t['end']) for t in data['tracks']] == [(0, 10), (8, 18)]
    transition = data['transitions'][0]
    assert (transition['start'], transition['end']) == (8, 10)
    assert transition['automation'][0]['outgoing'] == 1
    assert transition['automation'][-1]['incoming'] == pytest.approx(1)
    midpoint = transition['automation'][60]
    assert midpoint['low_outgoing'] == pytest.approx(.5)
    assert midpoint['low_incoming'] == pytest.approx(.5)
    assert transition['automation'][-1]['vocal_outgoing'] == 0
    metrics['vocal_controls'][0].update(fade_start_output_seconds=7.1, exit_output_seconds=7.85)
    earlier = timeline_data(plan, metrics)['transitions'][0]
    assert earlier['visual_start'] == 7.1
    assert earlier['automation'][0]['vocal_outgoing'] == 1
    assert earlier['automation'][0]['outgoing'] == 1
    metrics['variant'] = 'baseline'
    metrics['vocal_controls'] = []
    baseline = timeline_data(plan, metrics)['transitions'][0]
    assert baseline['automation'][60]['low_outgoing'] == pytest.approx(2**-.5)
    assert not baseline['vocal_control']['detection_enabled']


def test_visualization_reads_exported_audio_and_preserves_it(tmp_path):
    from dj_agent.sample_visualization import export_visualization
    audio = tmp_path / 'audio'
    audio.mkdir()
    sr = 8000
    signal = np.full((sr*18, 2), .125, dtype=np.float32)
    sf.write(audio/'master.wav', signal, sr, subtype='PCM_24')
    (audio/'master.mp3').write_bytes(b'preview-fixture')
    plan = {'tracks': [{'track_id': str(i), 'title': f'Track {i}', 'source_start': 0, 'source_end': 10,
                        'rate': 1, 'bpm': 120, 'key': 'C major', 'output_start': i*8} for i in range(2)],
            'transitions': [{'id': 'transition-01', 'output_start': 8, 'overlap_seconds': 2,
                             'mode': 'short_fade', 'phase_aligned': False}], 'duration_seconds': 18}
    metrics = {'sample_rate': sr, 'duration_seconds': 18, 'clipped_samples': 0}
    (audio/'plan.json').write_text(json.dumps(plan))
    (audio/'metrics.json').write_text(json.dumps(metrics))
    original = (audio/'master.wav').read_bytes()
    (tmp_path/'covers').mkdir()
    (tmp_path/'covers/album.jpg').write_bytes(b'cover-fixture')
    (tmp_path/'artwork.json').write_text(json.dumps({'tracks': {'0': {
        'album': 'Verified album', 'cover': 'covers/album.jpg',
        'credit_url': 'https://music.apple.com/album/example'}}}))
    data = export_visualization(tmp_path)
    assert data['waveform']['source'] == 'decoded_master_wav'
    assert max(data['waveform']['peaks']) == pytest.approx(.125)
    assert (tmp_path/'index.html').is_file()
    assert (tmp_path/'visualization.json').is_file()
    assert (audio/'master.wav').read_bytes() == original
    assert data['tracks'][0]['album'] == 'Verified album'
    assert data['tracks'][0]['cover'] == 'covers/album.jpg'
    assert 'path' not in data['tracks'][0]
    assert data['tracks'][1]['cover'] is None


def test_sample_server_range_and_file_allowlist(tmp_path):
    from dj_agent.sample_server import create_server
    (tmp_path/'audio').mkdir()
    (tmp_path/'index.html').write_text('<h1>test</h1>')
    (tmp_path/'audio/master.mp3').write_bytes(b'0123456789')
    (tmp_path/'private.txt').write_text('not served')
    (tmp_path/'covers').mkdir()
    (tmp_path/'covers/album.jpg').write_bytes(b'cover-fixture')
    server = create_server(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        request = urllib.request.Request(base+'/audio/master.mp3', headers={'Range': 'bytes=3-5'})
        with urllib.request.urlopen(request) as response:
            assert response.status == 206
            assert response.read() == b'345'
            assert response.headers['Content-Range'] == 'bytes 3-5/10'
        with urllib.request.urlopen(base+'/covers/album.jpg') as response:
            assert response.read() == b'cover-fixture'
            assert response.headers['Content-Type'] == 'image/jpeg'
        for path in ['/private.txt', '/../private.txt', '/audio/', '/%2e%2e/private.txt']:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base+path)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
