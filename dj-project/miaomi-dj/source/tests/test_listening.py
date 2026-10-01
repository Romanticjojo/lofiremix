import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection

import numpy as np
import pytest
import soundfile as sf

from dj_agent import listening_feedback as feedback
from dj_agent import listening_server
from dj_agent.feedback import record_preference
from dj_agent.preferences import build_comparison, file_sha256
from dj_agent.training import collect_preferences


def make_session(root):
    root.mkdir(parents=True, exist_ok=True)
    for renderer, hz in [('baseline', 200), ('enhanced', 400)]:
        folder = root / renderer
        folder.mkdir()
        t = np.arange(8000) / 8000
        sf.write(folder / 'transition-01.wav', np.column_stack([.1*np.sin(t*hz*2*np.pi)]*2), 8000)
    plan = {'source_kind': 'user-local-audio', 'transitions': [
        {'id': 'transition-01', 'from_id': 'track-a', 'to_id': 'track-b'}],
        'tracks': [{'track_id': 'track-a', 'title': '<Track A>'},
                   {'track_id': 'track-b', 'title': 'Track B'}]}
    manifest = build_comparison(root, plan)
    (root / 'comparison.json').write_text(json.dumps(manifest), encoding='utf-8')
    (root / 'baseline' / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    return root


def submission(item, request_id='test-request-0001'):
    return {'item_id': item['id'], 'binding': item['binding'], 'base_version': item['version'],
            'request_id': request_id, 'preference': 'A', 'song_fit': 'good',
            'ratings': {'A': {'cue': 'good', 'blend': 'good', 'vocals': 'mixed'},
                        'B': {'cue': None, 'blend': None, 'vocals': None}}, 'note': '人声仍有一点重叠'}


def test_empty_feedback_is_unselected_and_legacy_choice_is_preserved(tmp_path):
    session = make_session(tmp_path / 'listen')
    store = feedback.FeedbackStore(session)
    item = store.state()['items'][0]
    assert item['feedback']['preference'] is None
    assert item['feedback']['song_fit'] is None
    record_preference(session, 'transition-01', 'B')
    item = store.state()['items'][0]
    assert item['feedback']['preference'] == 'B'
    assert item['feedback']['ratings']['A']['cue'] is None
    assert item['saved_via'] == 'conversation'


def test_save_reload_idempotence_and_explicit_revision(tmp_path):
    session = make_session(tmp_path / 'listen')
    record_preference(session, 'transition-01', 'B')
    legacy = (session / 'feedback.jsonl').read_bytes()
    store = feedback.FeedbackStore(session)
    item = store.state()['items'][0]
    payload = submission(item)
    first = store.submit(payload)
    assert store.submit(payload)['version'] == first['version']
    reloaded = feedback.FeedbackStore(session).state()['items'][0]
    assert reloaded['feedback']['ratings']['A']['vocals'] == 'mixed'
    with pytest.raises(ValueError, match='更新'):
        store.submit({**payload, 'request_id': 'test-request-0002', 'preference': 'B'})
    changed = submission(reloaded, 'test-request-0003')
    changed['preference'] = 'tie'
    store.submit(changed)
    rows = [json.loads(s) for s in (session/'structured-feedback.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 2 and rows[-1]['supersedes'] == rows[0]['record_id']
    assert (session/'feedback.jsonl').read_bytes() == legacy
    assert feedback.FeedbackStore(session).state()['items'][0]['feedback']['preference'] == 'tie'


@pytest.mark.parametrize('change', [
    {'preference': None}, {'song_fit': 'invented'}, {'ratings': {}},
    {'note': 'x'*2001}, {'item_id': '../../escape'}, {'source': 'human'},
])
def test_invalid_submission_never_writes(tmp_path, change):
    session = make_session(tmp_path / 'listen')
    store = feedback.FeedbackStore(session)
    with pytest.raises(ValueError):
        store.submit({**submission(store.state()['items'][0]), **change})
    assert not (session/'structured-feedback.jsonl').exists()


def test_changed_audio_or_manifest_rejects_stale_button_submission(tmp_path):
    session = make_session(tmp_path / 'listen')
    store = feedback.FeedbackStore(session)
    payload = submission(store.state()['items'][0])
    with (session/'baseline/transition-01.wav').open('ab') as f:
        f.write(b'changed')
    with pytest.raises(ValueError, match='音频'):
        store.submit(payload)
    assert not (session/'structured-feedback.jsonl').exists()


def test_training_uses_latest_structured_choice_once_not_dimension_scores(tmp_path):
    session = make_session(tmp_path / 'listen')
    record_preference(session, 'transition-01', 'B')
    store = feedback.FeedbackStore(session)
    payload = submission(store.state()['items'][0])
    payload['preference'] = 'tie'
    store.submit(payload)
    identity = tmp_path/'identities.json'
    identity.write_text(json.dumps({'schema_version': 1, 'reviewed': True,
                                    'songs': {'track-a': 'song-a', 'track-b': 'song-b'}}))
    rows, audit = collect_preferences(tmp_path, identity)
    assert len(rows) == 1 and rows[0]['target'] == .5
    assert not audit['rejected']


def test_later_cli_feedback_joins_the_same_revision_history(tmp_path):
    session = make_session(tmp_path / 'listen')
    store = feedback.FeedbackStore(session)
    store.submit(submission(store.state()['items'][0]))
    record_preference(session, 'transition-01', 'B')
    item = store.state()['items'][0]
    assert item['feedback']['preference'] == 'B'
    assert item['feedback']['ratings']['A']['vocals'] == 'mixed'
    history = feedback.read_records(session/'structured-feedback.jsonl')
    assert len(history) == 2 and history[-1]['supersedes'] == history[0]['record_id']


def test_separate_stores_recheck_version_inside_shared_write_lock(tmp_path):
    session = make_session(tmp_path / 'listen')
    stores = [feedback.FeedbackStore(session), feedback.FeedbackStore(session)]
    item = stores[0].state()['items'][0]
    barrier = threading.Barrier(2)

    def submit(index):
        payload = submission(item, f'parallel-request-{index}')
        payload['preference'] = ['A', 'B'][index]
        barrier.wait()
        try:
            return stores[index].submit(payload)
        except ValueError:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, [0, 1]))
    assert sum(r is not None for r in results) == 1
    assert len(feedback.latest_structured(session)) == 1


def test_session_lock_coordinates_another_python_process(tmp_path):
    from dj_agent.feedback_lock import session_lock
    code = ('import sys\nfrom pathlib import Path\nfrom dj_agent.feedback_lock import session_lock\n'
            'print("ready", flush=True)\nwith session_lock(Path(sys.argv[1])):\n'
            ' print("acquired", flush=True)\n')
    with session_lock(tmp_path):
        process = subprocess.Popen([sys.executable, '-u', '-c', code, str(tmp_path)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert process.stdout.readline().strip() == 'ready'
        with pytest.raises(subprocess.TimeoutExpired):
            process.communicate(timeout=.2)
    output, error = process.communicate(timeout=10)
    assert process.returncode == 0 and output.strip() == 'acquired', error


def test_revision_feedback_loads_original_human_choice_and_binds_audio(tmp_path):
    session = make_session(tmp_path / 'listen')
    folder = session/'revisions/transition-01-vocal-exit'
    folder.mkdir(parents=True)
    original = (session/'baseline/transition-01.wav').read_bytes()
    revised = (session/'enhanced/transition-01.wav').read_bytes()
    (folder/'original.wav').write_bytes(original)
    (folder/'revised.wav').write_bytes(revised)
    report = {'source_pair': 'transition-01', 'original_sha256': file_sha256(folder/'original.wav'),
              'revised_sha256': file_sha256(folder/'revised.wav')}
    (folder/'revision.json').write_text(json.dumps(report))
    (folder/'human-feedback.jsonl').write_text(json.dumps({
        'source': 'human', 'preference': 'revised', 'candidates': {
            'original': {'sha256': report['original_sha256']},
            'revised': {'sha256': report['revised_sha256']}}})+'\n')
    item = feedback.FeedbackStore(session).state()['items'][1]
    assert item['feedback']['preference'] == 'B'
    assert item['candidates']['B']['label'] == '新版'
    assert item['kind'] == 'vocal_revision'


def test_media_address_is_bound_to_audio_not_catalog_position(tmp_path):
    session = make_session(tmp_path / 'listen')
    store = feedback.FeedbackStore(session)
    item = store.state()['items'][0]
    assert f"/media/{item['binding']}/" in item['candidates']['A']['url']
    assert file_sha256(store.media(item['binding'], 'A')) == item['candidates']['A']['sha256']


def test_http_saves_only_same_origin_and_serves_seekable_allowlisted_audio(tmp_path):
    session = make_session(tmp_path / 'listen')
    server = listening_server.create_server(session, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    client = HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        client.request('GET', '/api/state')
        state = json.loads(client.getresponse().read())
        payload = submission(state['items'][0])
        headers = {'Content-Type': 'application/json', 'X-DJ-Token': state['token'],
                   'Origin': 'https://unrelated.example'}
        client.request('POST', '/api/feedback', json.dumps(payload), headers)
        response = client.getresponse()
        assert response.status == 403
        response.read()
        assert not (session/'structured-feedback.jsonl').exists()
        headers['Origin'] = f'http://127.0.0.1:{port}'
        client.request('POST', '/api/feedback', json.dumps(payload), headers)
        response = client.getresponse()
        assert response.status == 200
        assert json.loads(response.read())['saved'] is True
        media = state['items'][0]['candidates']['A']['url']
        client.request('GET', media, headers={'Range': 'bytes=2-10'})
        response = client.getresponse()
        assert response.status == 206 and len(response.read()) == 9
        client.request('GET', '/../../pyproject.toml')
        response = client.getresponse()
        assert response.status == 404
        response.read()
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
