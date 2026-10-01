"""Known-origin transformed audio and missing-song controls; not a listening test."""
# ruff: noqa: E402 -- Set numerical thread limits before NumPy initialization.
import os

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '4'

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

from dj_agent.preferences import file_sha256
from dj_agent.reference_audio import FEATURE_VERSION, POSITION_MARGIN, WINDOW, extract_features, match_windows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--offset-base', type=int, default=33)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    tracks = json.loads((args.dataset/'library.json').read_text(encoding='utf8'))
    features = {t['id']: np.load(Path('data/reference_audio_features')/FEATURE_VERSION/(t['source_hash']+'.npy'))
                for t in tracks}
    controls, queries = [], []
    for index, track in enumerate(tracks):
        speed = (.96, 1., 1.04)[index % 3]
        pitch = (-1, 0, 1)[index % 3]
        start = args.offset_base + index % 5*9
        audio = args.output/f'positive-{index+1:02}.flac'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(start), '-i', track['path'],
                        '-t', '38', '-af', f'rubberband=tempo={speed}:pitch={2**(pitch/12)},highpass=f=100,volume=0.6',
                        '-ar', '11025', '-ac', '1', str(audio)], check=True)
        data = extract_features(audio, args.output/f'positive-{index+1:02}.npy')
        queries.append(data[:WINDOW])
        controls.append({'id': index+1, 'true_track': track['id'], 'true_title': track['title'],
                         'true_source_start': start, 'true_speed': speed, 'true_pitch': pitch,
                         'transformed_audio_sha256': file_sha256(audio)})
    matched = match_windows(features, np.stack(queries))
    negatives = []
    for track in tracks:
        q = features[track['id']][40:40+WINDOW]
        absent = {k: v for k, v in features.items() if k != track['id']}
        row = match_windows(absent, q[None, ...], speeds=(1.,))[0]
        negatives.append({'absent_track': track['title'], **row})
    for control, row in zip(controls, matched, strict=True):
        control.update(prediction=row, correct_identity=row['track_id'] == control['true_track'],
                       offset_error_seconds=abs(row['source_start']-control['true_source_start']))
    correct = [r for r in controls if r['correct_identity'] and r['prediction']['accepted']]
    cue_accepted = [r for r in correct if r['prediction']['position_margin'] >= POSITION_MARGIN]
    report = {'protocol': '25 transformed known-origin clips; 25 missing-library song controls',
              'song_identity_thresholds_changed_after_controls': False,
              'source_position_gate_added_after_v1_controls': True,
              'position_margin_threshold': POSITION_MARGIN, 'offset_base_seconds': args.offset_base,
              'controls': controls, 'missing_song_controls': negatives,
              'positive_accepted_correct': len(correct), 'positive_count': len(controls),
              'positive_wrong_identity_accepted': sum(r['prediction']['accepted'] and not r['correct_identity'] for r in controls),
              'accepted_offset_error_median_seconds': float(np.median([r['offset_error_seconds'] for r in correct])),
              'accepted_offset_error_max_seconds': float(max(r['offset_error_seconds'] for r in correct)),
              'position_gate_accepted_count': len(cue_accepted),
              'position_gate_offset_errors_over_3_seconds': sum(r['offset_error_seconds'] > 3 for r in cue_accepted),
              'missing_song_false_accepts': sum(r['accepted'] for r in negatives),
              'missing_song_count': len(negatives),
              'limitation': 'Small local recording controls; does not establish precision on unseen remixes or real mixed windows.'}
    (args.output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('controls', 'missing_song_controls')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
