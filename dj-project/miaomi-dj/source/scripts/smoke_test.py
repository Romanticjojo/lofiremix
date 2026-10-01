"""Generate explicitly synthetic engineering fixtures and run the real audio pipeline.

These signals are not The Weeknd music and never count as real-song listening tests.
"""
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from dj_agent.analysis import analyze
from dj_agent.cli import _comparison_page
from dj_agent.planner import plan_set
from dj_agent.render import render_set


def main():
    root = Path(__file__).resolve().parents[1]
    session = root / "outputs" / ("engineering-smoke-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))
    fixtures = session / "synthetic-fixtures"
    fixtures.mkdir(parents=True)
    sr, duration = 22050, 48
    rng = np.random.default_rng(123)
    tracks, timings = [], []
    for index, bpm in enumerate((120, 124, 118)):
        t = np.arange(sr*duration)/sr
        notes = [261.63, 329.63, 392.00] if index < 2 else [196., 246.94, 293.66]
        y = sum(.015*np.sin(2*np.pi*f*t) for f in notes)
        for beat_index, onset in enumerate(np.arange(.25, duration-.4, 60/bpm)):
            pos = round(onset*sr)
            tick_t = np.arange(round(.22*sr))/sr
            kick = .3*np.sin(2*np.pi*(60*tick_t+3*(1-np.exp(-tick_t*30))))*np.exp(-tick_t*22)
            y[pos:pos+len(kick)] += kick
            if beat_index % 2:
                noise = rng.normal(0, .12, len(tick_t))*np.exp(-tick_t*40)
                y[pos:pos+len(noise)] += noise
            hat = rng.normal(0, .035, round(.04*sr))*np.exp(-np.arange(round(.04*sr))/120)
            y[pos:pos+len(hat)] += hat
        stereo = np.column_stack([y, y + .005*np.sin(2*np.pi*523.25*t)])
        path = fixtures / f"synthetic-drums-{bpm}.wav"
        sf.write(path, stereo, sr, subtype="PCM_24")
        start = time.perf_counter()
        track = analyze(path, root / "data" / "analysis", backend="beat-this")
        track.artist = "SYNTHETIC ENGINEERING FIXTURE"
        tracks.append(track)
        timings.append({"track": path.name, "seconds": time.perf_counter()-start,
                        "detected_bpm": track.bpm, "backend": track.analysis_backend,
                        "confidence_hint": track.beat_confidence})
    plan = plan_set(tracks, max_tracks=3, target_minutes=2)
    plan["source_kind"] = "synthetic-test-only — NOT The Weeknd"
    plan["warnings"].insert(0, "本页全部是合成工程信号，不属于真实歌曲或听感验收。")
    outputs = {}
    for variant in ("baseline", "enhanced"):
        start = time.perf_counter()
        outputs[variant] = render_set(plan, session / variant, variant)
        outputs[variant]["render_seconds"] = time.perf_counter()-start
    manifest = {"schema_version": 1, "source_kind": plan["source_kind"], "pairs": [
        {"id": t["id"], "track_ids": [t["from_id"], t["to_id"]],
         "A": "baseline/report.html", "B": "enhanced/report.html"} for t in plan["transitions"]]}
    (session / "comparison.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _comparison_page(session, manifest)
    result = {"source_kind": plan["source_kind"], "analysis": timings, "outputs": outputs,
              "real_weeknd_test": "blocked_missing_audio", "human_listening": "not_performed"}
    (session / "engineering-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"session": str(session), "analysis": timings,
                      "duration": outputs["enhanced"]["metrics"]["duration_seconds"],
                      "true_peak": outputs["enhanced"]["metrics"]["estimated_true_peak_dbfs"],
                      "clipped_samples": outputs["enhanced"]["metrics"]["clipped_samples"]}, indent=2))


if __name__ == "__main__":
    main()
