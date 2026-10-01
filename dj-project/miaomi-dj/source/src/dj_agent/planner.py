"""Bounded, deterministic phrase-aware planning; no claims of learned musical taste."""
import math

import numpy as np

from .models import Track


def _validate(track):
    if (not all(math.isfinite(v) for v in [track.duration, track.bpm, track.energy])
            or track.duration < 12 or not 35 <= track.bpm <= 240):
        raise ValueError(f"曲目时长或速度不合格：{track.title}")
    if len(track.beats) < 8 or any(not math.isfinite(b) for b in track.beats):
        raise ValueError(f"曲目节拍无效：{track.title}")
    if any(b <= a for a, b in zip(track.beats, track.beats[1:], strict=False)):
        raise ValueError("节拍必须严格递增")
    for segment in track.segments:
        if any(not math.isfinite(float(segment[k])) for k in ("time", "energy", "vocal_proxy", "novelty")):
            raise ValueError("段落特征包含非有限值")


def _harmonic_cost(a, b):
    notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    try:
        na, ma = a.split()
        nb, mb = b.split()
        # Relative major is three semitones above minor tonic.
        pa = (notes.index(na) + (3 if ma == "minor" else 0)) % 12
        pb = (notes.index(nb) + (3 if mb == "minor" else 0)) % 12
        delta = abs((pa * 7) % 12 - (pb * 7) % 12)
        return min(delta, 12-delta)/6
    except ValueError:
        return .5


def _rate(target, bpm):
    choices = [(target/(bpm*factor), factor) for factor in (.5, 1., 2.)]
    feasible = [(r, f) for r, f in choices if .92 <= r <= 1.08]
    return min(feasible, key=lambda rf: abs(math.log(rf[0]))) if feasible else (1., 1.)


def _cue(track, desired, low, high):
    candidates = [s for s in track.segments if low <= s["time"] <= high]
    if not candidates:
        candidates = [{"time": b, "vocal_proxy": .5, "novelty": 0}
                      for b in track.downbeats if low <= b <= high]
    if not candidates:
        return float(np.clip(desired, low, high))
    return float(min(candidates, key=lambda s: abs(s["time"]-desired)/max(high-low, 1)
                     + .2*s["vocal_proxy"] - .1*min(s["novelty"], 2))["time"])


def _align_overlap(prev, item, source_a, source_b):
    """Match actual beat-window durations, not rounded metadata BPM."""
    a = np.asarray(source_a.beats)
    b = np.asarray(source_b.beats)
    end_index = int(np.argmin(abs(a-prev["source_end"])))
    start_index = int(np.argmin(abs(b-item["source_start"])))
    crowding = np.mean([s["vocal_proxy"] for t in (source_a, source_b) for s in t.segments] or [.5])
    preferred = 16 if crowding > .55 else 32
    # Four/eight/sixteen/thirty-two-bar options, prioritizing a restrained duration.
    candidates = sorted((16, 32, 64, 128), key=lambda n: abs(math.log2(n/preferred)))
    for virtual_beats in candidates:
        n_a, n_b = round(virtual_beats/prev["beat_factor"]), round(virtual_beats/item["beat_factor"])
        if end_index < n_a or start_index+n_b >= len(b):
            continue
        source_a_start, source_a_end = a[end_index-n_a], a[end_index]
        seconds = (source_a_end-source_a_start)/prev["rate"]
        new_rate = (b[start_index+n_b]-b[start_index])/seconds
        if not .92 <= new_rate <= 1.08:
            continue
        if (seconds > (source_a_end-prev["source_start"])/prev["rate"]*.35 or
                seconds > (item["source_end"]-b[start_index])/new_rate*.35):
            continue
        ta = (a[end_index-n_a:end_index+1]-source_a_start)/prev["rate"]
        tb = (b[start_index:start_index+n_b+1]-b[start_index])/new_rate
        grid = np.linspace(0, 1, virtual_beats+1)
        drift = np.abs(np.interp(grid, np.linspace(0, 1, len(ta)), ta)
                       - np.interp(grid, np.linspace(0, 1, len(tb)), tb))
        error = float(np.percentile(drift, 95))
        if error > .075:
            continue
        prev["source_end"] = float(source_a_end)
        item["source_start"] = float(b[start_index])
        item["rate"] = float(new_rate)
        item["bpm"] = float(item["source_bpm"]*new_rate*item["beat_factor"])
        return float(seconds), error
    return None


def plan_set(tracks: list[Track], max_tracks: int = 10, target_minutes: float = 25,
             *, choice_model: dict | None = None, mood_catalog: dict | None = None) -> dict:
    tracks = list({t.id: t for t in tracks}.values())
    if len(tracks) < 2:
        raise ValueError("至少需要两首不同的音频文件")
    if not isinstance(max_tracks, int) or max_tracks < 2 or not math.isfinite(target_minutes) or target_minutes <= 0:
        raise ValueError("曲目数至少为 2，目标分钟数必须为正数")
    for track in tracks:
        _validate(track)
    count = min(max_tracks, len(tracks))
    ranked_energy = {t.id: rank/max(len(tracks)-1, 1)
                     for rank, t in enumerate(sorted(tracks, key=lambda t: (t.energy, t.id)))}
    ordered = [min(tracks, key=lambda t: (abs(ranked_energy[t.id]-.2), t.id))]
    remaining = [t for t in tracks if t.id != ordered[0].id]
    rates, factors = [1.], [1.]
    reference_ids = ({file_id: entry['id'] for entry in choice_model['catalog']
                      for file_id in entry.get('local_track_ids', [])} if choice_model else {})
    selection_audit = []
    from .mood import party_target
    for index in range(1, count):
        target = ordered[-1].bpm*rates[-1]*factors[-1]
        desired_energy = .2 + .7 * index/max(count-1, 1)
        mood_target, mood_stage = party_target(index, count)
        def cost(candidate, target=target, desired_energy=desired_energy):
            r, f = _rate(target, candidate.bpm)
            mismatch = abs(math.log(target/(candidate.bpm*r*f)))
            return (mismatch*5 + abs(math.log(r))*2 + .5*_harmonic_cost(ordered[-1].key, candidate.key)
                    + abs(ranked_energy[candidate.id]-desired_energy)*.8, candidate.id)
        penalties, probabilities = {}, {}
        context = []
        for previous in reversed(ordered[-3:]):
            if previous.id not in reference_ids:
                break
            context.insert(0, reference_ids[previous.id])
        available = {reference_ids[t.id] for t in remaining if t.id in reference_ids} - set(context)
        if choice_model and context and len(available) >= 2:
            from .reference_choice import rank_choices
            ranked = rank_choices(choice_model, context, available)
            penalties = {r['track_id']: .2*(i/max(len(ranked)-1, 1)) for i, r in enumerate(ranked)}
            probabilities = {r['track_id']: r['probability'] for r in ranked}
        scored = []
        for candidate in remaining:
            rule_cost = cost(candidate)[0]
            model_id = reference_ids.get(candidate.id)
            penalty = penalties.get(model_id, .1) if penalties else 0.
            mood = (mood_catalog or {}).get(candidate.title)
            mood_cost = 1.2*abs(mood['party_energy']-mood_target) if mood else 0.
            scored.append({'track_id': candidate.id, 'rule_cost': rule_cost,
                           'reference_probability': probabilities.get(model_id),
                           'reference_penalty': penalty, 'mood_cost': mood_cost,
                           'total_cost': rule_cost + penalty + mood_cost})
        selected = min(scored, key=lambda r: (r['total_cost'], r['track_id']))
        next_track = next(t for t in remaining if t.id == selected['track_id'])
        selection_audit.append({'selected_track_id': next_track.id,
                                'reference_context': context, 'candidates': scored,
                                'mood_target': mood_target if mood_catalog else None,
                                'mood_stage': mood_stage if mood_catalog else None})
        r, f = _rate(target, next_track.bpm)
        ordered.append(next_track)
        rates.append(r)
        factors.append(f)
        remaining.remove(next_track)
    desired_length = (target_minutes*60+(count-1)*12)/count
    items = []
    for index, (track, rate, factor) in enumerate(zip(ordered, rates, factors, strict=True)):
        start = 0. if index == 0 else _cue(track, 0., 0., min(20., track.duration*.1))
        max_end = track.duration
        desired_end = min(max_end, start+desired_length*rate)
        end = max_end if index == count-1 and desired_end >= max_end-8 else _cue(
            track, desired_end, max(start+8., desired_end-8.), min(max_end, desired_end+8.))
        end = min(end, max_end)
        items.append({"track_id": track.id, "path": track.path, "title": track.title,
                      "artist": track.artist, "key": track.key, "source_duration": track.duration,
                      "source_hash": track.source_hash, "source_start": start, "source_end": end,
                      "rate": rate, "beat_factor": factor, "source_bpm": track.bpm,
                      "bpm": track.bpm*rate*factor, "output_start": 0.,
                      "analysis_backend": track.analysis_backend})
    transitions = []
    for index in range(1, count):
        prev, item = items[index-1], items[index]
        # A previous overlap can correct the entire incoming deck's rate.
        # Recompute this candidate against the corrected playing deck, not the old preplan.
        rate, factor = _rate(prev["bpm"], item["source_bpm"])
        item.update(rate=rate, beat_factor=factor, bpm=item["source_bpm"]*rate*factor)
        a_len = (prev["source_end"]-prev["source_start"])/prev["rate"]
        b_len = (item["source_end"]-item["source_start"])/item["rate"]
        compatible = (abs(prev["bpm"]-item["bpm"])/prev["bpm"] < .015 and
                      ordered[index-1].beat_confidence == "high" and ordered[index].beat_confidence == "high")
        aligned = _align_overlap(prev, item, ordered[index-1], ordered[index]) if compatible else None
        compatible = aligned is not None
        overlap = aligned[0] if compatible else min(3., a_len*.2, b_len*.2)
        cursor = prev["output_start"] + (prev["source_end"]-prev["source_start"])/prev["rate"]
        item["output_start"] = cursor-overlap
        transitions.append({"id": f"transition-{index:02d}", "from_id": prev["track_id"],
                            "to_id": item["track_id"], "output_start": cursor-overlap,
                            "overlap_seconds": overlap, "mode": "eq_blend" if compatible else "short_fade",
                            "reason": "检测节拍窗口对齐、乐句候选与低频交接" if compatible else "速度或节拍置信度不适合长叠加，使用短过渡",
                            "phase_aligned": compatible,
                            "predicted_phase_error_p95_ms": aligned[1]*1000 if compatible else None})
    cursor = items[-1]["output_start"] + (items[-1]["source_end"]-items[-1]["source_start"])/items[-1]["rate"]
    warnings = ["曲序来自可解释规则；尚未进行个人偏好训练。",
                "小节/段落检测可能出错，需通过转场片段听评；相位对齐状态由渲染检查。"]
    if cursor < target_minutes*60*.9:
        warnings.append(f"素材/裁剪仅支持 {cursor/60:.1f} 分钟，未达到请求的 {target_minutes:g} 分钟；没有重复填充。")
    if choice_model:
        warnings[0] = "实验参考曲序模型提供小幅排序建议；速度/调性/能量规则约束；尚未通过听感验收。"
    result = {"schema_version": 1, "planner": "bounded-rules-v1", "requested_minutes": target_minutes,
            "tracks": items, "transitions": transitions, "duration_seconds": cursor,
            "warnings": warnings, "source_kind": "user-local-audio"}
    if choice_model:
        result.update(planner='bounded-rules-reference-assist-v1', selection_audit=selection_audit,
                      reference_configuration={'regularization': choice_model['regularization'],
                                               'context_length': choice_model.get('context_length', 3),
                                               'max_cost_adjustment': .2})
    if mood_catalog:
        result['party_preset'] = 'warmup-rise-breathe-peak-release-v1'
        result['crowd_observed'] = False
        result['selection_audit'] = selection_audit
        for index, item in enumerate(items):
            item['mood'] = mood_catalog.get(item['title'], {})
            item['party_target'], item['party_stage'] = party_target(index, count)
    return result
