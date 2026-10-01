"""Conservative audio-derived weak labels, never synthetic listener preferences."""

from pathlib import Path

import librosa
import numpy as np

from .audio import decode

FEATURE_VERSION = 'chroma-1hz-v1'
WINDOW = 28
STEP = 6
POSITION_MARGIN = .08


def extract_features(path, cache_path):
    """One centered, normalized chroma vector per second, in bounded chunks."""
    cache_path = Path(cache_path)
    if cache_path.exists():
        return np.load(cache_path, allow_pickle=False)
    sr, hop, fft = 11025, 2048, 4096
    audio = decode(Path(path), sr, 1)[:, 0]
    bank = librosa.filters.chroma(sr=sr, n_fft=fft, tuning=0)
    seconds = len(audio) // sr
    result = np.zeros((seconds, 12), dtype=np.float32)
    for start in range(0, seconds, 60):
        length = min(60, seconds-start)
        segment = audio[start*sr:(start+length)*sr]
        power = np.abs(librosa.stft(segment, n_fft=fft, hop_length=hop))**2
        chroma = np.sqrt(np.maximum(bank @ power, 0))
        frame_seconds = np.minimum((np.arange(chroma.shape[1])*hop/sr).astype(int), length-1)
        for second in range(length):
            vector = chroma[:, frame_seconds == second].mean(axis=1)
            vector -= vector.mean()
            result[start+second] = vector / max(float(np.linalg.norm(vector)), 1e-9)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache_path, result, allow_pickle=False)
    return result


def match_windows(library, queries, *, speeds=(.92, .96, 1., 1.04, 1.08),
                  shifts=(-2, -1, 0, 1, 2), threshold=.72, margin_threshold=.10):
    """Search recording, offset, speed, pitch; confidence scores are not probabilities."""
    queries = np.asarray(queries, dtype=np.float32)
    if (queries.ndim != 3 or queries.shape[2] != 12 or not np.isfinite(queries).all()
            or not library or not speeds or any(s <= 0 for s in speeds)):
        raise ValueError('invalid matching inputs')
    q = queries.reshape(len(queries), -1)
    norms = np.linalg.norm(q, axis=1, keepdims=True)
    q = q / np.maximum(norms, 1e-9)
    ids = list(library)
    scores = np.full((len(q), len(ids)), -1., dtype=np.float32)
    locations = np.zeros_like(scores)
    rates = np.ones_like(scores)
    pitches = np.zeros_like(scores, dtype=int)
    position_margins = np.zeros_like(scores)
    for col, track_id in enumerate(ids):
        values = np.asarray(library[track_id], dtype=np.float32)
        if values.ndim != 2 or values.shape[1] != 12 or not np.isfinite(values).all():
            raise ValueError('invalid library features')
        position_scores = np.full((len(q), len(values)), -1., dtype=np.float32)
        for speed in speeds:
            starts = np.arange(max(0, int(len(values)-(queries.shape[1]-1)*speed)-1))
            if not len(starts):
                continue
            times = starts[:, None] + np.arange(queries.shape[1])[None, :]*speed
            low = times.astype(int)
            fraction = (times-low).astype(np.float32)[..., None]
            base = values[low]*(1-fraction) + values[low+1]*fraction
            for shift in shifts:
                templates = np.roll(base, shift, axis=2).reshape(len(starts), -1)
                templates /= np.maximum(np.linalg.norm(templates, axis=1, keepdims=True), 1e-9)
                for lo in range(0, len(q), 256):
                    hi = min(lo+256, len(q))
                    similarities = q[lo:hi] @ templates.T
                    position_scores[lo:hi, :len(starts)] = np.maximum(
                        position_scores[lo:hi, :len(starts)], similarities)
                    indices = similarities.argmax(axis=1)
                    best = similarities[np.arange(hi-lo), indices]
                    improve = best > scores[lo:hi, col]
                    selected = np.flatnonzero(improve)+lo
                    scores[selected, col] = best[improve]
                    locations[selected, col] = starts[indices[improve]]
                    rates[selected, col] = speed
                    pitches[selected, col] = shift
        # A second similar chorus in the SAME recording must not become a precise cue label.
        alternative = np.where(abs(np.arange(len(values))[None, :]-locations[:, col, None]) > 12,
                               position_scores, -1.).max(axis=1)
        position_margins[:, col] = scores[:, col]-alternative
    result = []
    for i in range(len(q)):
        order = np.argsort(-scores[i], kind='stable')
        best = order[0]
        score = float(scores[i, best])
        margin = score - float(scores[i, order[1]]) if len(order) > 1 else 0.
        result.append({'track_id': ids[best], 'source_start': float(locations[i, best]),
                       'score': score, 'margin': margin, 'speed': float(rates[i, best]),
                       'pitch_shift': int(pitches[i, best]),
                       'position_margin': float(position_margins[i, best]),
                       'accepted': bool(score >= threshold and margin >= margin_threshold and norms[i, 0] > 0)})
    return result


def build_spans(matches, *, step=STEP, window=WINDOW, min_windows=3):
    """Never bridge unknown windows, repeated-song position jumps or long gaps."""
    groups, current = [], []
    for row in matches:
        compatible = bool(current and row['accepted'] and row['track_id'] == current[-1]['track_id'])
        if compatible:
            previous = current[-1]
            delta = row['center']-previous['center']
            predicted = delta*(row['speed']+previous['speed'])/2
            residual = abs(row['source_start']-previous['source_start']-predicted)
            compatible = (0 < delta <= step*1.1 and residual <= 3.
                          and row['pitch_shift'] == previous['pitch_shift'])
        if not compatible and current:
            groups.append(current)
            current = []
        if row['accepted']:
            current.append(row)
    if current:
        groups.append(current)
    result = []
    for group in groups:
        if len(group) < min_windows:
            continue
        speed = float(np.median([r['speed'] for r in group]))
        offset = float(np.median([r['source_start']-speed*(r['center']-window/2) for r in group]))
        result.append({'track_id': group[0]['track_id'], 'first_center': group[0]['center'],
                       'last_center': group[-1]['center'], 'window_count': len(group),
                       'speed_estimate': speed, 'source_offset_estimate': offset,
                       'score_min': min(r['score'] for r in group),
                       'margin_min': min(r['margin'] for r in group),
                       'entry_position_confident': all(r.get('position_margin', 0) >= POSITION_MARGIN
                                                        for r in group[:3]),
                       'exit_position_confident': all(r.get('position_margin', 0) >= POSITION_MARGIN
                                                       for r in group[-3:]),
                       'position_margin_min': min(r.get('position_margin', 0) for r in group),
                       'pitch_shift': group[0]['pitch_shift']})
    return result


def transition_rows(source_id, spans, catalog_ids, *, max_gap=36.):
    result = []
    for a, b in zip(spans, spans[1:], strict=False):
        gap = b['first_center']-a['last_center']
        if a['track_id'] == b['track_id'] or not 0 < gap <= max_gap:
            continue
        middle = (a['last_center']+b['first_center'])/2
        result.append({'schema_version': 1, 'id': f'{source_id}-audio-{len(result)+1:03}',
                       'task': 'next_track_choice', 'context': [a['track_id']],
                       'candidates': [tid for tid in catalog_ids if tid != a['track_id']],
                       'label': b['track_id'], 'supervision': 'weak_audio_alignment',
                       'candidate_semantics': 'unobserved_alternatives_not_bad_music',
                       'boundary_interval_seconds': [a['last_center'], b['first_center']],
                       'boundary_estimate_seconds': middle,
                       'source_exit_seconds_estimate': middle*a['speed_estimate']+a['source_offset_estimate'],
                       'source_entry_seconds_estimate': middle*b['speed_estimate']+b['source_offset_estimate'],
                       'boundary_uncertainty_seconds': gap/2,
                       'alignment_min_score': min(a['score_min'], b['score_min']),
                       'alignment_min_margin': min(a['margin_min'], b['margin_min']),
                       'source_exit_position_confident': a['exit_position_confident'],
                       'source_entry_position_confident': b['entry_position_confident'],
                       'human_preference': None, 'effects': None, 'vocal_overlap': None,
                       'precise_mix_cue_seconds': None,
                       'provenance': {'source_video_id': source_id,
                                      'label_source': 'automatic_recording_alignment_unreviewed'}})
    return result
