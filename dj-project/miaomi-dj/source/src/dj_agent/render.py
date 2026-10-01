"""Offline reference renderer. Live Mixxx behavior has its own verification boundary."""
import json
import math
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

from .analysis import source_hash
from .audio import probe
from .evaluation import measure
from .report import write_report


def crossfade(a, b, overlap_samples, variant="enhanced", sample_rate=48000):
    n = overlap_samples
    if (not isinstance(n, int) or not 0 < n <= min(len(a), len(b)) or
            a.ndim != 2 or b.ndim != 2 or a.shape[1] != b.shape[1]):
        raise ValueError("过渡长度或音频声道不合法")
    if variant not in {"baseline", "enhanced"}:
        raise ValueError("未知混音版本")
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("音频包含非有限值")
    t = np.linspace(0, 1, n, dtype=np.float32)[:, None]
    fade_out, fade_in = np.cos(t*np.pi/2), np.sin(t*np.pi/2)
    left, right = a[-n:], b[:n]
    if variant == "enhanced" and n > 64:
        filt = butter(4, 180, fs=sample_rate, output="sos")
        low_a = sosfiltfilt(filt, left, axis=0).astype(np.float32)
        low_b = sosfiltfilt(filt, right, axis=0).astype(np.float32)
        handoff = np.clip((t-.25)/.5, 0, 1)
        handoff = handoff*handoff*(3-2*handoff)
        blend = ((left-low_a)*fade_out + (right-low_b)*fade_in
                 + low_a*(1-handoff) + low_b*handoff)
    else:
        blend = left*fade_out + right*fade_in
    return np.concatenate((a[:-n], blend, b[n:])).astype(np.float32, copy=False)


def _render_track(item, sample_rate):
    path = Path(item["path"])
    metadata = probe(path)
    start, end, rate = float(item["source_start"]), float(item["source_end"]), float(item["rate"])
    if (not all(math.isfinite(x) for x in [start, end, rate]) or
            not 0 <= start < end <= metadata["duration"]+.02 or not .92 <= rate <= 1.08):
        raise ValueError(f"曲目裁剪或变速越界：{path.name}")
    if item.get("source_hash") and source_hash(path) != item["source_hash"]:
        raise ValueError(f"音频内容已变化，请重新分析：{path.name}")
    filters = [f"rubberband=tempo={rate:.9f}:pitch=1:transients=crisp:detector=compound:phase=laminar"]
    # Trim must occur before tempo processing. atrim prevents output -t being interpreted after stretch.
    command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-af",
               f"atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS,{filters[0]}",
               "-ac", "2", "-ar", str(sample_rate), "-f", "f32le", "pipe:1"]
    result = subprocess.run(command, capture_output=True, timeout=600, check=False)
    if result.returncode:
        raise RuntimeError(f"音频伸缩失败（需要 FFmpeg rubberband）：{result.stderr.decode('utf-8', 'replace')[-400:]}")
    audio = np.frombuffer(result.stdout, dtype="<f4").reshape(-1, 2).copy()
    expected = round((end-start)/rate*sample_rate)
    if abs(len(audio)-expected) > sample_rate*.1:
        raise RuntimeError("时间伸缩输出时长与计划不符")
    audio = audio[:expected]
    if len(audio) < expected:
        audio = np.pad(audio, ((0, expected-len(audio)), (0, 0)))
    if not np.isfinite(audio).all() or not len(audio):
        raise ValueError("音频输出为空或含非有限值")
    rms = float(np.sqrt(np.mean(audio.astype(np.float64)**2)))
    if rms < 1e-6:
        raise ValueError("音频裁剪区间接近静音")
    # Fixed RMS reference with bounded boost and generous per-deck peak headroom.
    gain = min(.11/rms, 2., .60/max(float(np.max(np.abs(audio))), 1e-9))
    return audio * gain, float(gain)


def apply_transition_vocal_handoff(master, old_length, previous_audio, incoming_audio, n, sr, variant):
    """Edit only the outgoing separated contribution; return actual automation times."""
    from .vocals import detect_vocal_entry, extract_vocals, remove_outgoing_vocal
    incoming_crop = incoming_audio[:min(len(incoming_audio), n+3*sr)]
    incoming_voice = extract_vocals(incoming_crop, sr)
    entry = detect_vocal_entry(incoming_voice, incoming_crop, sr)
    transition_start = (old_length-n)/sr
    control = {'method': 'pretrained_htdemucs_sustained_energy', 'applied': False,
               'incoming_vocal_output_seconds': transition_start+entry if entry is not None else None}
    if entry is None or entry >= n/sr:
        return control
    crop = previous_audio[-min(len(previous_audio), n+3*sr):]
    voice = extract_vocals(crop, sr)
    contribution = crossfade(voice, np.zeros((n, 2), dtype=np.float32), n, variant, sr)
    start = old_length-len(crop)
    exit_at = min(len(crop)/sr, max(.001, (len(crop)-n)/sr + entry-.15))
    revised, _ = remove_outgoing_vocal(master[start:old_length], contribution, sr, exit_at, .75)
    master[start:old_length] = revised
    control.update(applied=True, exit_output_seconds=start/sr+exit_at,
                   fade_start_output_seconds=start/sr+max(0, exit_at-.75),
                   fade_seconds=min(.75, exit_at), stem_residual_possible=True)
    return control


def render_set(plan: dict, output_dir: Path, variant: str = "enhanced", *, vocal_handoff=False) -> dict:
    if len(plan.get("tracks", [])) < 2:
        raise ValueError("至少需要两首曲目")
    if len(plan.get("transitions", [])) != len(plan["tracks"])-1:
        raise ValueError("过渡数量与曲目数量不一致")
    if variant not in {"baseline", "enhanced"}:
        raise ValueError("未知混音版本")
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("输出目录必须为空；已有文件不会被覆盖，请使用新的目录")
    identifiers = [t.get("id", "") for t in plan["transitions"]]
    if (len(set(identifiers)) != len(identifiers) or
            any(not isinstance(value, str) or not re.fullmatch(r"transition-[0-9]{2,4}", value)
                for value in identifiers)):
        raise ValueError("转场标识不合法或重复")
    times = [plan.get("duration_seconds", float("nan"))]
    for transition in plan["transitions"]:
        times.extend([transition.get("output_start", float("nan")),
                      transition.get("overlap_seconds", float("nan"))])
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in times):
        raise ValueError("混音计划包含无效时间")
    if plan["duration_seconds"] <= 0:
        raise ValueError("混音计划时长必须为正数")
    # Reject unserializable/nonfinite metadata before decoding or creating deliverables.
    try:
        json.dumps(plan, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValueError("混音计划包含无效元数据") from error
    sr = 48000
    master = None
    gains = []
    voice_controls, fx_controls, previous_audio = [], [], None
    for index, item in enumerate(plan["tracks"]):
        print(f"渲染 {index+1}/{len(plan['tracks'])}：{item.get('title', 'track')}", flush=True)
        audio, gain = _render_track(item, sr)
        gains.append(gain)
        if master is None:
            master = audio
        else:
            transition = plan["transitions"][index-1]
            seconds = float(transition["overlap_seconds"])
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("过渡秒数不合法")
            expected_start = len(master)/sr-seconds
            if abs(transition["output_start"]-expected_start) > .02:
                raise ValueError("过渡时间与音频计划不一致")
            selected = variant if transition["mode"] == "eq_blend" else "baseline"
            old_length = len(master)
            fx_kind = transition.get('fx', 'none')
            if fx_kind != 'none':
                from .transition_fx import process_exit
                previous_audio, fx = process_exit(previous_audio, sr, seconds,
                                                 plan['tracks'][index-1]['bpm'], fx_kind)
                n = round(seconds*sr)
                master[-n:] = previous_audio[-n:]
                fx_controls.append({'transition_id': transition['id'], 'start': (old_length-n)/sr,
                                    'end': old_length/sr, **fx})
            master = crossfade(master, audio, round(seconds*sr), selected, sr)
            if vocal_handoff:
                control = apply_transition_vocal_handoff(
                    master, old_length, previous_audio, audio, round(seconds*sr), sr, selected)
                voice_controls.append({'transition_id': transition['id'], **control})
        previous_audio = audio
    edge = min(int(sr*.03), len(master)//2)
    master[:edge] *= np.linspace(0, 1, edge)[:, None]
    master[-edge:] *= np.linspace(1, 0, edge)[:, None]
    metrics = measure(master, sr)
    # Shared conservative ceiling. Scale rather than clip, preserving dynamics.
    if metrics["estimated_true_peak_dbfs"] > -1.5:
        master *= 10**((-1.5-metrics["estimated_true_peak_dbfs"])/20)
        metrics = measure(master, sr)
    if abs(metrics["duration_seconds"]-plan["duration_seconds"]) > .02:
        raise ValueError("整场录音时长与计划不符")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    # TemporaryDirectory owns only its freshly created child of this exact output parent.
    # Publish the complete result by rename; an error leaves no half-finished final session.
    with tempfile.TemporaryDirectory(prefix=".dj-render-", dir=output_dir.parent) as scratch:
        staging = Path(scratch) / "result"
        staging.mkdir()
        sf.write(staging / "master.wav", master, sr, subtype="PCM_24")
        decoded, exported_sr = sf.read(staging / "master.wav", dtype="float32", always_2d=True)
        metrics = measure(decoded, exported_sr)
        clips = []
        for transition in plan["transitions"]:
            start = max(0, round((transition["output_start"]-6)*sr))
            end = min(len(decoded), round((transition["output_start"]+transition["overlap_seconds"]+6)*sr))
            filename = transition["id"]+".wav"
            sf.write(staging / filename, decoded[start:end], sr, subtype="PCM_24")
            clips.append({"id": transition["id"], "path": filename,
                          "description": transition.get("reason", transition["mode"])})
        metrics.update({"variant": variant, "per_track_gain": gains, "level_method": "bounded RMS + peak ceiling",
                        "vocal_handoff_enabled": bool(vocal_handoff), "vocal_controls": voice_controls,
                        "fx_controls": fx_controls,
                        "source_kind": plan.get("source_kind", "unverified-local-audio"),
                        "measured_from": "decoded exported PCM_24 WAV"})
        for filename, data in [("plan.json", plan), ("metrics.json", metrics)]:
            (staging / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        write_report(plan, metrics, staging, variant, clips)
        if output_dir.exists():
            # rmdir refuses nonempty directories; never recursively remove user content.
            output_dir.rmdir()
        staging.rename(output_dir)
    return {"master_path": str(output_dir / "master.wav"), "report_path": str(output_dir / "report.html"), "metrics": metrics}
