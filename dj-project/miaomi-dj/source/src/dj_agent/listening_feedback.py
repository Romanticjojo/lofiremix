"""Audio-bound structured listening feedback with explicit, append-only revisions."""

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .feedback_lock import session_lock
from .preferences import file_sha256, session_file

RATINGS = ('cue', 'blend', 'vocals')
VALUES = (None, 'good', 'mixed', 'bad')
FIELDS = {'preference', 'song_fit', 'ratings', 'note'}


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_records(path):
    if path.is_symlink():
        raise ValueError('反馈文件不能是链接')
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('反馈记录损坏，请保留原文件并检查')
    return rows


def validate_values(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('反馈字段不完整')
    if value['preference'] not in ('A', 'B', 'tie', 'neither'):
        raise ValueError('请先选择更喜欢的版本，或选择差不多／都不满意')
    if value['song_fit'] not in VALUES or not isinstance(value['note'], str) or len(value['note']) > 2000:
        raise ValueError('评价选项无效或备注超过 2000 字')
    ratings = value['ratings']
    if not isinstance(ratings, dict) or set(ratings) != {'A', 'B'}:
        raise ValueError('每个版本需要独立的评价字段')
    for row in ratings.values():
        if (not isinstance(row, dict) or set(row) != set(RATINGS)
                or any(v not in VALUES for v in row.values())):
            raise ValueError('版本评价选项无效')


def latest_structured(session):
    """Validate the revision chain, preserving old rows rather than overwriting votes."""
    with session_lock(session):
        return _latest_structured(session)


def _latest_structured(session):
    latest = {}
    for row in read_records(Path(session)/'structured-feedback.jsonl'):
        required = {'feedback', 'item_id', 'record_id', 'kind', 'binding', 'pair_id', 'request_id'}
        if (row.get('schema_version') != 1 or row.get('source') != 'human' or not required <= row.keys()
                or not isinstance(row['item_id'], str) or not isinstance(row['record_id'], str)):
            raise ValueError('未知结构化反馈格式')
        validate_values(row['feedback'])
        key = row['item_id']
        previous = latest.get(key)
        if row.get('supersedes') != (previous['record_id'] if previous else None):
            raise ValueError('反馈修改记录不连续')
        latest[key] = row
    return latest


def training_records(session, legacy, manifest, digest):
    """Only the latest explicit overall A/B choice enters the existing training audit."""
    replacements = {}
    for row in latest_structured(session).values():
        if row['kind'] != 'comparison':
            continue  # Stem revisions are archived, not silently treated as old renderers.
        pair = next((p for p in manifest['pairs'] if p['id'] == row['pair_id']), None)
        if pair is None or row['binding'] != _binding(digest, pair, 'comparison'):
            raise ValueError('结构化反馈绑定的音频或清单已变化')
        replacements[row['pair_id']] = {
            'source': 'human', 'pair_id': row['pair_id'], 'track_ids': sorted(pair['track_ids']),
            'comparison_sha256': digest, 'preference': row['feedback']['preference'],
        }
    return [r for r in legacy if r.get('pair_id') not in replacements] + list(replacements.values())


def _binding(digest, pair, kind):
    return _digest({'comparison': digest, 'pair': pair, 'kind': kind})


class FeedbackStore:
    def __init__(self, session):
        self.session = Path(session).resolve()
        with session_lock(self.session):
            self._items()  # Fail before starting the server for missing or changed audio.

    def _items(self):
        manifest_path = session_file(self.session, 'comparison.json')
        manifest, digest = _json(manifest_path), file_sha256(manifest_path)
        if manifest.get('schema_version') != 2:
            raise ValueError('按钮试听需要带音频绑定的新版比较清单')
        plan_path = self.session/'baseline/plan.json'
        plan = _json(plan_path) if plan_path.is_file() else {}
        titles = {t['track_id']: t.get('title', t['track_id']) for t in plan.get('tracks', [])}
        legacy = read_records(self.session/'feedback.jsonl')
        notes_path = self.session/'listening-notes-01.json'
        notes = _json(notes_path) if notes_path.is_file() else {}
        notes = notes.get('transitions', []) if notes.get('comparison_sha256') == digest else []
        items = []

        def make(pair, kind, item_id, heading, page, directory=None):
            candidates = {}
            binding = _binding(digest, pair, kind)
            for label, c in pair['candidates'].items():
                path = session_file(directory or self.session, c['audio'])
                if file_sha256(path) != c['sha256']:
                    raise ValueError('音频内容已变化，请重新生成试听比较')
                candidates[label] = {'label': ('原版' if label == 'A' else '新版')
                                     if kind == 'vocal_revision' else f'版本 {label}',
                                     'url': f'/media/{binding}/{label}.wav', '_path': path,
                                     'sha256': c['sha256']}
            if set(candidates) != {'A', 'B'}:
                raise ValueError('试听需要两个候选版本')
            return {'id': item_id, 'pair_id': pair['id'], 'kind': kind, 'heading': heading,
                    'songs': ' → '.join(titles.get(t, t) for t in pair['track_ids']),
                    'track_ids': pair['track_ids'], 'page': page, 'candidates': candidates,
                    'binding': binding, 'comparison_sha256': digest,
                    'previous_note': '', '_prior': []}

        for pair in manifest['pairs']:
            item = make(pair, 'comparison', pair['id'], pair['id'], '/comparison.html')
            item['_prior'] = [r for r in legacy if r.get('pair_id') == pair['id'] and
                              r.get('comparison_sha256') == digest and r.get('source') == 'human']
            item['previous_note'] = next((r.get('verbatim', '') for r in notes if r['id'] == pair['id']), '')
            items.append(item)
        for folder in sorted((self.session/'revisions').glob('*')):
            if not folder.is_dir() or folder.is_symlink() or not (folder/'revision.json').is_file():
                continue
            if not folder.resolve().is_relative_to(self.session):
                raise ValueError('修订目录超出试听目录')
            revision = _json(folder/'revision.json')
            original = next(p for p in manifest['pairs'] if p['id'] == revision['source_pair'])
            pair = {'id': original['id'], 'track_ids': original['track_ids'], 'candidates': {
                'A': {'audio': 'original.wav', 'sha256': revision['original_sha256']},
                'B': {'audio': 'revised.wav', 'sha256': revision['revised_sha256']}}}
            item = make(pair, 'vocal_revision', f'revision:{folder.name}',
                        f'{original["id"]} · 人声修订', f'/revisions/{folder.name}/review.html', folder)
            for r in read_records(folder/'human-feedback.jsonl'):
                if (r.get('source') == 'human' and
                        all(r.get('candidates', {}).get(k, {}).get('sha256') == pair['candidates'][v]['sha256']
                            for k, v in [('original', 'A'), ('revised', 'B')])):
                    preference = {'original': 'A', 'revised': 'B', 'tie': 'tie', 'neither': 'neither'}.get(r['preference'])
                    if preference:
                        item['_prior'].append({'preference': preference})
                        item['previous_note'] = r.get('verbatim', '新版更好' if preference == 'B' else '')
            items.append(item)
        latest = latest_structured(self.session)
        for item in items:
            item['feedback'] = {'preference': None, 'song_fit': None,
                                'ratings': {k: dict.fromkeys(RATINGS) for k in ('A', 'B')}, 'note': ''}
            prior = item.pop('_prior')
            item['version'] = _digest(prior)
            item['saved_via'] = None
            if prior:
                item['feedback']['preference'] = prior[-1]['preference']
                item['saved_via'] = 'conversation'
            row = latest.get(item['id'])
            if row:
                if row['binding'] != item['binding']:
                    raise ValueError('已保存评价的音频或清单变化，请使用新会话')
                item.update(feedback=row['feedback'], version=row['record_id'], saved_via='buttons')
        if not items or len({i['id'] for i in items}) != len(items):
            raise ValueError('试听比较为空或重复')
        return items, manifest.get('source_kind', 'unknown')

    def state(self):
        with session_lock(self.session):
            items, source_kind = self._items()
            for item in items:
                for candidate in item['candidates'].values():
                    candidate.pop('_path')
            return {'items': items, 'source_kind': source_kind}

    def media(self, binding, label):
        with session_lock(self.session):
            items, _ = self._items()
            item = next((i for i in items if i['binding'] == binding), None)
            if item is None:
                raise ValueError('试听内容已变化，请刷新页面')
            return item['candidates'][label]['_path']

    def submit(self, payload):
        expected = FIELDS | {'item_id', 'binding', 'base_version', 'request_id'}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError('提交字段无效')
        values = {key: payload[key] for key in FIELDS}
        validate_values(values)
        rid = payload['request_id']
        if not isinstance(rid, str) or not 8 <= len(rid) <= 100 or not rid.isascii():
            raise ValueError('保存请求标识无效')
        with session_lock(self.session):
            items, _ = self._items()
            item = next((i for i in items if i['id'] == payload['item_id']), None)
            if item is None or payload['binding'] != item['binding']:
                raise ValueError('试听内容变化，请刷新后重新评价')
            log = self.session/'structured-feedback.jsonl'
            history = read_records(log)
            repeated = next((r for r in history if r['request_id'] == rid), None)
            if repeated:
                if repeated['request_digest'] != _digest(payload):
                    raise ValueError('重复请求内容不同')
                return {'saved': True, 'version': repeated['record_id'], 'feedback': repeated['feedback']}
            if payload['base_version'] != item['version']:
                raise ValueError('反馈已在另一页面更新，请刷新后再保存')
            if values == item['feedback']:
                return {'saved': True, 'version': item['version'], 'feedback': values}
            previous = latest_structured(self.session).get(item['id'])
            row = {'schema_version': 1, 'record_id': str(uuid.uuid4()), 'source': 'human',
                   'timestamp_utc': datetime.now(UTC).isoformat(), 'item_id': item['id'],
                   'pair_id': item['pair_id'], 'kind': item['kind'], 'track_ids': item['track_ids'],
                   'binding': item['binding'], 'comparison_sha256': item['comparison_sha256'],
                   'candidates': {k: {'sha256': c['sha256']} for k, c in item['candidates'].items()},
                   'request_id': rid, 'request_digest': _digest(payload),
                   'supersedes': previous['record_id'] if previous else None, 'feedback': values}
            line = json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n'
            with log.open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            return {'saved': True, 'version': row['record_id'], 'feedback': values}
