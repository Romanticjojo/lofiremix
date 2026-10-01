"""Reproduce an offline sample from a cached analysis and optional choice model."""
import argparse
import json
import subprocess
import time
from pathlib import Path

from dj_agent.models import Track
from dj_agent.planner import plan_set
from dj_agent.preferences import file_sha256
from dj_agent.render import render_set


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--exclude', action='append', default=[])
    parser.add_argument('--tracks', type=int, default=8)
    parser.add_argument('--minutes', type=float, default=20)
    parser.add_argument('--moods', type=Path)
    parser.add_argument('--effects', action='store_true')
    args = parser.parse_args()
    excluded = {title.casefold().strip() for title in args.exclude}
    library = json.loads(args.library.read_text(encoding='utf8'))
    tracks = [Track(**row) for row in library if row['beat_confidence'] == 'high'
              and row['title'].casefold().strip() not in excluded]
    model = json.loads(args.model.read_text(encoding='utf8'))
    mood_data = json.loads(args.moods.read_text(encoding='utf8')) if args.moods else None
    plan = plan_set(tracks, args.tracks, args.minutes, choice_model=model,
                    mood_catalog=mood_data['tracks'] if mood_data else None)
    if args.effects:
        for index, transition in enumerate(plan['transitions']):
            stage = plan['tracks'][index+1].get('party_stage')
            transition['fx'] = ('echo' if transition['mode'] == 'short_fade' else
                                'reverb' if stage in {'呼吸', '余韵'} else 'filter')
    rules = plan_set(tracks, args.tracks, args.minutes)
    plan['sample_provenance'] = {
        'library_sha256': file_sha256(args.library), 'model_sha256': file_sha256(args.model),
        'excluded_titles': args.exclude, 'eligible_tracks': len(tracks),
        'filter': 'beat_confidence == high', 'mode': 'experimental offline assist',
        'same_order_as_rules_only': [t['track_id'] for t in plan['tracks']] ==
                                    [t['track_id'] for t in rules['tracks']],
        'human_request': 'Outgoing vocal exits before incoming vocal; requested exclusions recorded above.'}
    args.output.mkdir(parents=True, exist_ok=False)
    if mood_data:
        (args.output/'mood-catalog.json').write_text(json.dumps(mood_data, ensure_ascii=False, indent=2), encoding='utf8')
    (args.output/'planned-set.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'duration': plan['duration_seconds'], 'tracks': [t['title'] for t in plan['tracks']]}),
          flush=True)
    started = time.perf_counter()
    render_set(plan, args.output/'audio', 'enhanced', vocal_handoff=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(args.output/'audio/master.wav'),
                    '-codec:a', 'libmp3lame', '-b:a', '256k', str(args.output/'audio/master.mp3')], check=True)
    report = {'render_seconds': time.perf_counter()-started,
              'master_sha256': file_sha256(args.output/'audio/master.wav'),
              'preview_sha256': file_sha256(args.output/'audio/master.mp3'),
              'metrics': json.loads((args.output/'audio/metrics.json').read_text(encoding='utf8'))}
    (args.output/'sample-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
