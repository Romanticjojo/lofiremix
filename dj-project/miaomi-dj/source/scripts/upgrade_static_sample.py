"""Run on the DJ host: verify an immutable static release, then switch its symlink.

Usage: python3 upgrade_static_sample.py /var/tmp/miaomi-dj-20260925-v4
No nginx configuration or other site's release pointer is changed.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def upgrade(stage):
    stage = Path(stage).resolve(strict=True)
    info = json.loads((stage / 'release.json').read_text())
    name = info['release']
    if not re.fullmatch(r'\d{8}-v\d+', name):
        raise ValueError('invalid release name')
    root = Path('/var/www/miaomi-dj')
    current = root / 'current'
    release = root / 'releases' / name
    private = Path('/var/lib/miaomi-dj') / name
    if stage != Path('/var/tmp') / ('miaomi-dj-' + name) or root.resolve() != root:
        raise ValueError('unexpected deployment location')
    if not current.is_symlink() or os.readlink(current) != info['expected_current']:
        raise ValueError('current release changed; inspect before retrying')
    if release.exists() or release.is_symlink() or private.exists():
        raise ValueError('immutable release already exists')
    protected = {name: digest(name) for name in info['protected_sha256']}
    if protected != info['protected_sha256']:
        raise ValueError('protected site files changed since preflight')
    if digest(stage / 'project.tar.gz') != info['private_archive_sha256']:
        raise ValueError('private archive checksum mismatch')
    manifest = json.loads((stage / 'public-manifest.json').read_text())
    def allowed(value):
        return value in {'index.html', 'audio/master.mp3'} or bool(
            re.fullmatch(r'covers/[a-z0-9-]+\.(jpg|png|webp)', value))

    if not {'index.html', 'audio/master.mp3'} <= manifest.keys() or not all(map(allowed, manifest)):
        raise ValueError('public manifest contains unexpected files')
    with tarfile.open(stage / 'public.tar.gz') as archive:
        members = archive.getmembers()
        if (len(members) != len(manifest) or {m.name for m in members} != manifest.keys()
                or any(not m.isfile() for m in members)):
            raise ValueError('archive does not exactly match the static allowlist')
        release.mkdir(parents=True)
        for member in members:
            path = release / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, path.open('xb') as target:
                shutil.copyfileobj(source, target)
            expected = manifest[member.name]
            if path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']:
                raise ValueError('public file checksum mismatch: ' + member.name)
            path.chmod(0o644)
    for path in [release, *release.rglob('*')]:
        if path.is_dir():
            path.chmod(0o755)
    private.mkdir(parents=True, mode=0o700)
    for name in ['release.json', 'public-manifest.json', 'project.tar.gz']:
        shutil.copy2(stage / name, private / name)
        (private / name).chmod(0o600)
    rollback = {'previous_current': info['expected_current'], 'new_current': str(release),
                'protected_sha256': protected}
    (private / 'rollback.json').write_text(json.dumps(rollback, indent=2))
    (private / 'rollback.json').chmod(0o600)
    # Check again immediately before the one mutation that changes the live site.
    if os.readlink(current) != info['expected_current']:
        raise ValueError('current release changed during staging')
    temporary = root / ('current.' + info['release'] + '.tmp')
    temporary.symlink_to(release)
    try:
        os.replace(temporary, current)
        if {name: digest(name) for name in protected} != protected:
            raise ValueError('protected site files changed during switch')
    except Exception:
        if temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(info['expected_current'])
        os.replace(temporary, current)
        raise
    result = {'release': str(release), 'previous_current': info['expected_current'],
              'verified_files': len(manifest), 'protected_sha256': protected,
              'rollback_record': str(private / 'rollback.json')}
    (private / 'deployment-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    upgrade(sys.argv[1])
