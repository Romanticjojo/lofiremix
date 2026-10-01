import numpy as np
import pytest

from dj_agent import vocals


def test_vocal_exit_keeps_accompaniment_and_removes_only_outgoing_voice():
    sr = 8000
    t = np.arange(sr * 4) / sr
    bed = np.column_stack([.1*np.sin(2*np.pi*100*t)]*2)
    voice = np.column_stack([.05*np.sin(2*np.pi*1000*t)]*2)
    mix = bed + voice
    result, gain = vocals.remove_outgoing_vocal(mix, voice, sr, exit_at=2., fade_seconds=.5)
    np.testing.assert_array_equal(result[:sr], mix[:sr])
    np.testing.assert_allclose(result[2*sr:], bed[2*sr:], atol=1e-8)
    assert gain[0] == 1 and gain[-1] == 0
    assert np.max(np.abs(np.diff(gain))) < .001


def test_vocal_entry_needs_sustained_voice_not_short_leakage():
    sr = 8000
    mixture = np.ones((sr * 4, 2))*.1
    voice = np.zeros_like(mixture)
    voice[100:150] = .1
    voice[2*sr:3*sr] = .05
    assert vocals.detect_vocal_entry(voice, mixture, sr) == pytest.approx(2., abs=.05)
    assert vocals.detect_vocal_entry(np.zeros_like(voice), mixture, sr) is None


@pytest.mark.parametrize('bad', [float('nan'), -1., 10.])
def test_vocal_exit_rejects_impossible_timing(bad):
    with pytest.raises(ValueError):
        vocals.remove_outgoing_vocal(np.zeros((16000, 2)), np.zeros((16000, 2)), 8000, bad)


def test_vocal_edit_rejects_mismatched_stem_length_and_nonfinite_samples():
    mixture = np.zeros((16000, 2))
    with pytest.raises(ValueError):
        vocals.remove_outgoing_vocal(mixture, mixture[:100], 8000, 1.)
    mixture[0, 0] = np.nan
    with pytest.raises(ValueError):
        vocals.detect_vocal_entry(mixture, np.zeros_like(mixture), 8000)


def test_separation_rejects_invalid_input_before_loading_a_model():
    with pytest.raises(ValueError):
        vocals.extract_vocals(np.zeros((0, 2)), 48000)
    with pytest.raises(ValueError):
        vocals.extract_vocals(np.zeros((1000, 2)), 0)


def test_audio_player_guard_pauses_other_players_via_real_javascript(tmp_path):
    import re
    import subprocess

    from dj_agent.cli import _comparison_page
    manifest = {'schema_version': 2, 'pairs': [{'id': 'transition-01'}]}
    _comparison_page(tmp_path, manifest)
    page = (tmp_path/'comparison.html').read_text(encoding='utf-8')
    scripts = re.findall(r'<script>(.*?)</script>', page, re.S)
    harness = '''
const assert = require('node:assert/strict');
let listener;
const first = { tagName: 'AUDIO', paused: false, pause() { this.paused = true; } };
const second = { tagName: 'AUDIO', paused: false, pause() { this.paused = true; } };
const document = {
  addEventListener(event, callback, capture) { assert.equal(event, 'play'); assert.equal(capture, true); listener=callback; },
  querySelectorAll(selector) { assert.equal(selector, 'audio'); return [first, second]; }
};
''' + '\n'.join(scripts) + '''
assert.equal(typeof listener, 'function');
listener({target: second});
assert.equal(first.paused, true);
assert.equal(second.paused, false);
'''
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
