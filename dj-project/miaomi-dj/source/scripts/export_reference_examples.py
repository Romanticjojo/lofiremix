"""Export reversible reference excerpts for auditing automatic labels."""

import argparse
import json
import subprocess
from pathlib import Path

from dj_agent.audio import probe
from dj_agent.preferences import file_sha256


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    args = parser.parse_args()
    root = args.dataset
    sources = {s['id']: s for s in json.loads((root/'sources.json').read_text(encoding='utf8'))}
    rows = [json.loads(line) for line in (root/'choice-dataset.jsonl').read_text(encoding='utf8').splitlines()]
    folder = root/'review-clips'
    folder.mkdir(exist_ok=False)
    manifest, index = [], ['# 参考转场音频核对', '', '这些是参考视频里的真实音轨片段，不是模型生成音频。标签尚未人工验收。', '']
    for row in rows:
        source = sources[row['provenance']['source_video_id']]
        start = max(0., row['boundary_interval_seconds'][0]-12)
        end = min(source['duration_seconds'], row['boundary_interval_seconds'][1]+12)
        output = folder/(row['id']+'.flac')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(start), '-i', source['audio_path'],
                        '-t', str(end-start), '-c:a', 'flac', str(output)], check=True)
        meta = probe(output)
        if abs(meta['duration']-(end-start)) > .05:
            raise RuntimeError('reference clip duration mismatch')
        manifest.append({'id': row['id'], 'audio': output.relative_to(root).as_posix(),
                         'sha256': file_sha256(output), 'source_audio_sha256': source['audio_sha256'],
                         'source_start_seconds': start, 'source_end_seconds': end,
                         'duration_seconds': meta['duration'], 'human_reviewed': False})
        index.append(f"- [{row['from_title']} → {row['to_title']}](review-clips/{output.name})："
                     f"视频 {start:.0f}–{end:.0f} 秒；估计交接区间 {row['boundary_interval_seconds']}。")
    (root/'audio-examples.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in manifest), encoding='utf8')
    (root/'REVIEW.md').write_text('\n'.join(index)+'\n', encoding='utf8')
    print(f'Exported and duration-verified {len(manifest)} FLAC excerpts')


if __name__ == '__main__':
    main()
