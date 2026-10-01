import numpy as np
import pytest


@pytest.mark.parametrize('kind', ['filter', 'echo', 'reverb'])
def test_fx_is_bounded_finite_and_does_not_touch_earlier_audio(kind):
    from dj_agent.transition_fx import process_exit
    sr = 8000
    t = np.arange(sr*5)/sr
    signal = np.column_stack([.1*np.sin(2*np.pi*443.3*t)]*2).astype(np.float32)
    original = signal.copy()
    result, control = process_exit(signal, sr, 2., 120, kind)
    assert np.array_equal(signal, original)
    assert np.array_equal(result[:-2*sr], signal[:-2*sr])
    assert result.shape == signal.shape
    assert np.isfinite(result).all()
    assert np.max(abs(result)) < .2
    assert not np.allclose(result[-sr:], signal[-sr:])
    assert 0 < control['wet_peak'] <= .65


def test_no_fx_is_exact_identity_and_unknown_fx_fails():
    from dj_agent.transition_fx import process_exit
    signal = np.zeros((4000, 2), dtype=np.float32)
    result, control = process_exit(signal, 8000, .3, 120, 'none')
    assert np.array_equal(signal, result)
    assert control['kind'] == 'none'
    with pytest.raises(ValueError):
        process_exit(signal, 8000, .3, 120, 'scratch')
