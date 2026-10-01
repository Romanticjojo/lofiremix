"""Resume content-addressed features and conservative reference-audio alignment."""
# ruff: noqa: E402 -- Numerical thread limits must be set before importing NumPy.

import os

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '4'

import argparse
import json
import time
from pathlib import Path

import numpy as np

from dj_agent.preferences import file_sha256
from dj_agent.reference_audio import (
    FEATURE_VERSION,
    POSITION_MARGIN,
    STEP,
    WINDOW,
    build_spans,
    extract_features,
    match_windows,
    transition_rows,
)


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    library = json.loads(args.library.read_text(encoding='utf8'))
    sources = json.loads(args.manifest.read_text(encoding='utf8'))
    if len({s['audio_sha256'] for s in sources}) != len(sources):
        raise ValueError('duplicate reference audio; deduplicate before evaluation')
    config = {'feature_version': FEATURE_VERSION, 'window_seconds': WINDOW, 'step_seconds': STEP,
              'threshold': .72, 'margin_threshold': .10, 'minimum_coherent_windows': 3,
              'source_position_margin': POSITION_MARGIN, 'position_competitor_separation_seconds': 12,
              'speed_search': [.92, .96, 1., 1.04, 1.08], 'pitch_search': [-2, -1, 0, 1, 2],
              'library_sha256': file_sha256(args.library), 'manifest_sha256': file_sha256(args.manifest),
              'implementation_sha256': file_sha256(Path('src/dj_agent/reference_audio.py'))}
    if (root/'protocol.json').exists():
        if json.loads((root/'protocol.json').read_text(encoding='utf8')) != config:
            raise ValueError('resume configuration mismatch; use a new output directory')
    dump(root/'protocol.json', config)
    dump(root/'library.json', library)
    dump(root/'sources.json', sources)
    catalog = [{'id': r['id'], 'title': r['title'], 'local_track_ids': [r['id']],
                'source_hash': r['source_hash']} for r in library]
    dump(root/'catalog.json', catalog)
    lookup = {r['id']: r for r in library}
    features = {}
    cache = Path('data/reference_audio_features')/FEATURE_VERSION
    for track in library:
        if file_sha256(Path(track['path'])) != track['source_hash']:
            raise ValueError('library content changed since analysis')
        features[track['id']] = extract_features(track['path'], cache/(track['source_hash']+'.npy'))
        print('FEATURE', track['title'], len(features[track['id']]), flush=True)
    summaries, all_rows = [], []
    for source in sources:
        started = time.perf_counter()
        sid = source['id']
        destination = root/'alignments'/sid
        print('SOURCE', sid, source['original_filename'][:50], flush=True)
        if file_sha256(Path(source['audio_path'])) != source['audio_sha256']:
            raise ValueError('reference audio content changed')
        if (destination/'matches.json').exists():
            matches = json.loads((destination/'matches.json').read_text(encoding='utf8'))
        else:
            reference = extract_features(source['audio_path'], cache/(source['audio_sha256']+'.npy'))
            starts = np.arange(0, len(reference)-WINDOW, STEP)
            queries = np.stack([reference[start:start+WINDOW] for start in starts])
            matches = match_windows(features, queries)
            for row, start in zip(matches, starts, strict=True):
                row.update(center=float(start+WINDOW/2), title=lookup[row['track_id']]['title'])
            dump(destination/'matches.json', matches)
        spans = build_spans(matches)
        for span in spans:
            span['title'] = lookup[span['track_id']]['title']
        rows = transition_rows(sid, spans, list(features))
        for row in rows:
            row['provenance'].update(audio_sha256=source['audio_sha256'], video_sha256=source['video_sha256'])
            row['from_title'] = lookup[row['context'][-1]]['title']
            row['to_title'] = lookup[row['label']]['title']
            row['source_cue_within_recordings'] = bool(
                0 <= row['source_entry_seconds_estimate'] < lookup[row['label']]['duration']
                and 0 <= row['source_exit_seconds_estimate'] < lookup[row['context'][-1]]['duration'])
            row['source_exit_cue_eligible'] = bool(row['source_exit_position_confident'] and
                0 <= row['source_exit_seconds_estimate'] < lookup[row['context'][-1]]['duration'])
        dump(destination/'spans.json', spans)
        dump(destination/'transitions.json', rows)
        all_rows.extend(rows)
        summary = {'source_id': sid, 'title': source['original_filename'],
                   'duration_seconds': source['duration_seconds'], 'query_windows': len(matches),
                   'accepted_windows': sum(r['accepted'] for r in matches), 'spans': len(spans),
                   'transition_rows': len(rows), 'elapsed_seconds': time.perf_counter()-started}
        summaries.append(summary)
        dump(root/'summary.json', summaries)
        (root/'choice-dataset.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in all_rows),
                                                 encoding='utf8')
        print('ALIGNED', json.dumps(summary, ensure_ascii=False), flush=True)
    dump(root/'schema.json', {
        '$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'Audio aligned weak DJ choice',
        'type': 'object', 'required': ['schema_version', 'id', 'context', 'candidates', 'label',
                                     'supervision', 'boundary_interval_seconds', 'provenance'],
        'properties': {'schema_version': {'const': 1}, 'id': {'type': 'string'},
                       'context': {'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {'type': 'string'}},
                       'candidates': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string'}},
                       'label': {'type': 'string'}, 'supervision': {'const': 'weak_audio_alignment'},
                       'human_preference': {'type': 'null'}, 'effects': {'type': 'null'},
                       'boundary_interval_seconds': {'type': 'array', 'minItems': 2, 'maxItems': 2,
                                                     'items': {'type': 'number', 'minimum': 0}},
                       'provenance': {'type': 'object', 'required': ['source_video_id', 'audio_sha256']}}})
    print('COMPLETE', len(sources), 'sources', len(all_rows), 'weak transitions', flush=True)


if __name__ == '__main__':
    main()
