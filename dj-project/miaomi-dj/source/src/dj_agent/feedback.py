"""Human A/B labels and song-disjoint evaluation splits."""

import json
import os
import random
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

_PAIR_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_PREFERENCES = frozenset({"A", "B", "tie", "neither"})


def _track_ids(value: object) -> list[str]:
    if (not isinstance(value, list) or len(value) != 2
            or any(not isinstance(item, str) or not item.strip() for item in value)):
        raise ValueError("pair must contain two nonempty track IDs")
    if value[0] == value[1]:
        raise ValueError("pair must contain two distinct tracks")
    return sorted(value)


def record_preference(session_dir: Path, pair_id: str, preference: str) -> Path:
    """Append one explicit human label for a pair in comparison.json."""
    from .feedback_lock import session_lock
    with session_lock(session_dir):
        return _record_preference(session_dir, pair_id, preference)


def _record_preference(session_dir: Path, pair_id: str, preference: str) -> Path:
    if not isinstance(preference, str) or preference not in _PREFERENCES:
        raise ValueError("preference must be A, B, tie, or neither")
    if not isinstance(pair_id, str) or _PAIR_ID.fullmatch(pair_id) is None:
        raise ValueError("invalid pair ID")

    session = Path(session_dir)
    if not session.is_dir() or session.is_symlink():
        raise ValueError("session directory must be an existing real directory")
    manifest_path = session / "comparison.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("comparison manifest is missing or unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid comparison manifest") from exc
    pairs = manifest.get("pairs") if isinstance(manifest, dict) else None
    if not isinstance(pairs, list):
        raise ValueError("comparison manifest has no pairs")
    matches = [pair for pair in pairs if isinstance(pair, dict) and pair.get("id") == pair_id]
    if len(matches) != 1:
        raise ValueError("pair ID must identify exactly one comparison")
    track_ids = _track_ids(matches[0].get("track_ids"))

    # Once a session uses structured buttons, CLI/chat updates must join that same
    # explicit revision history instead of writing a newer vote to an ignored file.
    if (session / 'structured-feedback.jsonl').exists():
        import uuid

        from .listening_feedback import FeedbackStore
        store = FeedbackStore(session)
        item = next(i for i in store.state()['items'] if i['id'] == pair_id)
        store.submit({'item_id': pair_id, 'binding': item['binding'], 'base_version': item['version'],
                      'request_id': str(uuid.uuid4()), **item['feedback'], 'preference': preference})
        return session / 'structured-feedback.jsonl'

    output = session / "feedback.jsonl"
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise ValueError("feedback destination is unsafe")
    record = {
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "source": "human",
        "pair_id": pair_id,
        "track_ids": track_ids,
        "group_key": json.dumps(track_ids, ensure_ascii=False, separators=(",", ":")),
        "preference": preference,
    }
    if manifest.get("schema_version") == 2:
        from .preferences import file_sha256
        record["comparison_sha256"] = file_sha256(manifest_path)
    line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    fd = os.open(output, flags, 0o600)
    try:
        if os.write(fd, line) != len(line):
            raise OSError("short feedback write")
        os.fsync(fd)
    finally:
        os.close(fd)
    return output


def split_by_song_identity(
    rows: Sequence[Mapping[str, object]], test_fraction: float = 0.2, seed: int = 0
) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
    """Split connected groups so no track ID appears in both folds.

    Raises ValueError when all pairs are connected through shared songs.
    """
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between zero and one")
    if not rows:
        raise ValueError("independent split requires comparison rows")

    parent: dict[str, str] = {}

    def find(song: str) -> str:
        parent.setdefault(song, song)
        if parent[song] != song:
            parent[song] = find(parent[song])
        return parent[song]

    song_pairs = [_track_ids(row.get("track_ids")) for row in rows]
    for a, b in song_pairs:
        parent[find(b)] = find(a)
    groups: dict[str, list[int]] = {}
    for index, (a, _) in enumerate(song_pairs):
        groups.setdefault(find(a), []).append(index)
    if len(groups) < 2:
        raise ValueError("independent split impossible: all songs are connected")

    components = sorted(groups.values(), key=lambda indices: tuple(song_pairs[indices[0]]))
    random.Random(seed).shuffle(components)
    target = max(1, min(len(rows) - 1, round(len(rows) * test_fraction)))
    selected: set[int] = set()
    for component in components:
        if selected and len(selected) >= target:
            break
        if len(selected) + len(component) == len(rows):
            continue
        selected.update(component)
    if not selected:
        selected.update(components[0])
    train = [row for i, row in enumerate(rows) if i not in selected]
    test = [row for i, row in enumerate(rows) if i in selected]
    return train, test
