import numpy as np
import pytest

from dj_agent.models import Track


@pytest.fixture
def track_factory():
    def make(name="a", bpm=120., duration=180., energy=.5):
        beat = 60 / bpm
        return Track(
            id=name, path=f"{name}.wav", title=name, artist="test fixture", duration=duration,
            sample_rate=48000, bpm=bpm, beats=np.arange(0, duration, beat).tolist(),
            downbeats=np.arange(0, duration, beat * 4).tolist(), key="C major", energy=energy,
            segments=[{"time": float(t), "energy": energy, "vocal_proxy": .2, "novelty": .5}
                      for t in np.arange(0, duration, beat * 16)],
            analysis_backend="synthetic-test-only",
            beat_confidence="high",
        )
    return make
