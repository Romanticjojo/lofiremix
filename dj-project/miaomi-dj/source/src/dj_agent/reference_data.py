"""Provenance-checked weak sequence data, separate from human listening preferences."""

import hashlib
import json
import unicodedata
from pathlib import Path

from .preferences import file_sha256

UNKNOWN_LABELS = ('transition_quality', 'human_preference', 'precise_mix_cue_seconds',
                  'source_song_cue_seconds', 'eq_fader_automation')


def title_key(title):
    if not isinstance(title, str) or not title.strip():
        raise ValueError('empty track title')
    value = ' '.join(unicodedata.normalize('NFKC', title).casefold().split())
    aliases = {'heatrless': 'heartless', 'ls therssomeone else?': 'is there someone else?',
               'isthere someone else?': 'is there someone else?', 'creepin‘': 'creepin',
               'creepin’': 'creepin', "creepin'": 'creepin'}
    return aliases.get(value, value)


def prepare_dataset(reference_dir: Path):
    root = Path(reference_dir)
    source_path, row_path = root / 'sources.json', root / 'reference-transitions.jsonl'
    sources = json.loads(source_path.read_text(encoding='utf8'))
    raw = [json.loads(line) for line in row_path.read_text(encoding='utf8').splitlines() if line.strip()]
    if not isinstance(sources, list) or not sources or not raw:
        raise ValueError('empty reference data')
    source_map, expected = {}, {}
    for source in sources:
        sid = source['video_id']
        if (not isinstance(sid, str) or not sid.isdigit() or sid in source_map
                or source['url'] != f'https://www.douyin.com/video/{sid}'
                or source['evidence'] != 'public_page_visible_chapters'):
            raise ValueError('invalid or duplicate reference source')
        source_map[sid] = source
        sequence, chapter_starts = [], []
        for timestamp, titles in source['chapters']:
            parts = [int(p) for p in timestamp.split(':')]
            if len(parts) != 2 or parts[0] < 0 or not 0 <= parts[1] < 60 or not titles:
                raise ValueError('invalid chapter timestamp or tracks')
            chapter_starts.append(parts[0] * 60 + parts[1])
            sequence.extend(titles)
        if (chapter_starts != sorted(set(chapter_starts)) or chapter_starts[0] != 0
                or chapter_starts[-1] >= source['duration_seconds']
                or len(sequence) != source['track_count'] or len(sequence) < 2):
            raise ValueError('invalid chapter sequence')
        for index in range(len(sequence) - 1):
            expected[f'{sid}-{index+1:02d}'] = (
                sid, [title_key(t) for t in sequence[max(0, index-2):index+1]], title_key(sequence[index+1]))
    if len({r['id'] for r in raw}) != len(raw) or {r['id'] for r in raw} != set(expected):
        raise ValueError('missing, duplicate or unexpected reference observations')
    catalog_by_key = {}
    for row in raw:
        sid, context, target = expected[row['id']]
        if (row['schema_version'] != 1 or row['source_video_id'] != sid
                or row['source_url'] != source_map[sid]['url']
                or row['label_source'] != 'public_page_chapter_order'
                or [title_key(t) for t in row['context_tracks']] != context
                or title_key(row['observed_next_track']) != target
                or title_key(row['from']['normalized_title']) != context[-1]
                or title_key(row['to']['normalized_title']) != target
                or any(row.get(k) is not None for k in UNKNOWN_LABELS)
                or row.get('audio_analyzed') is not False or row.get('personal_ranker_eligible') is not False
                or target in context):
            raise ValueError('source mismatch or unsupported reference label')
        for title in [*row['context_tracks'], row['observed_next_track']]:
            key = title_key(title)
            if key not in catalog_by_key:
                catalog_by_key[key] = {'id': hashlib.sha256(key.encode()).hexdigest()[:16],
                                       'title': title, 'title_key': key, 'aliases': [],
                                       'local_track_ids': [], 'identity_reviewed': False}
            entry = catalog_by_key[key]
            if title not in entry['aliases']:
                entry['aliases'].append(title)
        for end in ('from', 'to'):
            entry = catalog_by_key[title_key(row[end]['normalized_title'])]
            local_id = row[end].get('local_track_id')
            if local_id and local_id not in entry['local_track_ids']:
                entry['local_track_ids'].append(local_id)
    catalog = sorted(catalog_by_key.values(), key=lambda c: c['title_key'])
    keys = {c['title_key']: c['id'] for c in catalog}
    all_ids = [c['id'] for c in catalog]
    source_hash, row_hash = file_sha256(source_path), file_sha256(row_path)
    rows = []
    for original in raw:
        context = [keys[title_key(t)] for t in original['context_tracks']]
        rows.append({'schema_version': 1, 'id': original['id'], 'task': 'next_track_choice',
                     'context': context, 'candidates': [c for c in all_ids if c not in context],
                     'label': keys[title_key(original['observed_next_track'])],
                     'supervision': 'weak_sequence_demonstration',
                     'candidate_semantics': 'unobserved_alternatives_not_bad_music',
                     'provenance': {'source_video_id': original['source_video_id'],
                                    'source_url': original['source_url'],
                                    'label_source': original['label_source'],
                                    'source_record_id': original['id'],
                                    'sources_sha256': source_hash, 'reference_rows_sha256': row_hash},
                     'unknown_labels': dict.fromkeys(UNKNOWN_LABELS)})
    return catalog, rows


def source_folds(rows):
    sources = sorted({r['provenance']['source_video_id'] for r in rows})
    if len(sources) < 3:
        raise ValueError('evaluation requires at least three sources')
    folds = []
    for sid in sources:
        test = [r for r in rows if r['provenance']['source_video_id'] == sid]
        pairs = {(r['context'][-1], r['label']) for r in test}
        train, purged = [], []
        for row in rows:
            if row['provenance']['source_video_id'] == sid:
                continue
            (purged if (row['context'][-1], row['label']) in pairs else train).append(row['id'])
        if not train:
            raise ValueError('no training observations after pair purging')
        folds.append({'held_out_video': sid, 'train_ids': train, 'test_ids': [r['id'] for r in test],
                      'purged_ids': purged})
    return folds
