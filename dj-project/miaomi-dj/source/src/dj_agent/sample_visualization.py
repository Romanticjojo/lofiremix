"""Timeline and control envelopes from the actual rendered set, with measured waveform."""

import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from .preferences import file_sha256


def timeline_data(plan, metrics):
    sr = metrics['sample_rate']
    tracks, transitions, cursor = [], [], 0
    voices = {r['transition_id']: r for r in metrics.get('vocal_controls', [])}
    for i, item in enumerate(plan['tracks']):
        frames = round((item['source_end']-item['source_start'])/item['rate']*sr)
        start = cursor
        if i:
            transition = plan['transitions'][i-1]
            start -= round(transition['overlap_seconds']*sr)
            if abs(start/sr-transition['output_start']) > .02:
                raise ValueError('visual transition timing differs from rendered samples')
            voice = voices.get(transition['id'], {'applied': False})
            visual_start = min(start/sr, voice['fade_start_output_seconds']) if voice.get('applied') else start/sr
            automation = []
            for when in np.linspace(visual_start, cursor/sr, 121):
                t = float(np.clip((when-start/sr)/((cursor-start)/sr), 0, 1))
                outgoing, incoming = float(np.cos(t*np.pi/2)), float(np.sin(t*np.pi/2))
                handoff = float(np.clip((t-.25)/.5, 0, 1))
                handoff = handoff*handoff*(3-2*handoff)
                vocal = 1.
                if voice.get('applied'):
                    phase = float(np.clip((when-voice['fade_start_output_seconds']) /
                                          max(voice['exit_output_seconds']-voice['fade_start_output_seconds'],
                                              1/sr), 0, 1))
                    vocal = 1-phase*phase*(3-2*phase)
                enhanced = transition['mode'] == 'eq_blend' and metrics.get('variant', 'enhanced') == 'enhanced'
                automation.append({'time': when, 'outgoing': outgoing, 'incoming': incoming,
                                   'low_outgoing': 1-handoff if enhanced else outgoing,
                                   'low_incoming': handoff if enhanced else incoming,
                                   'vocal_outgoing': vocal})
            transitions.append({**transition, 'start': start/sr, 'end': cursor/sr,
                                'mode': transition['mode'] if enhanced else 'short_fade',
                                'visual_start': visual_start,
                                'from_title': plan['tracks'][i-1]['title'], 'to_title': item['title'],
                                'vocal_control': {**voice, 'detection_enabled': bool(metrics.get('vocal_handoff_enabled', bool(voices)))},
                                'automation': automation})
        tracks.append({'id': item['track_id'], 'title': item['title'], 'start': start/sr,
                       'end': (start+frames)/sr, 'source_start': item['source_start'],
                       'source_end': item['source_end'], 'rate': item['rate'],
                       'bpm': item['bpm'], 'key': item.get('key', 'unknown')})
        tracks[-1].update(mood=item.get('mood', {}), party_stage=item.get('party_stage'),
                          party_target=item.get('party_target'))
        cursor = start+frames
    if abs(cursor/sr-metrics['duration_seconds']) > .02:
        raise ValueError('visual duration differs from exported master')
    return {'schema_version': 1, 'duration': cursor/sr, 'tracks': tracks, 'transitions': transitions,
            'metrics': metrics, 'planner': plan.get('planner'),
            'reference_configuration': plan.get('reference_configuration'),
            'crowd_observed': False, 'party_preset': plan.get('party_preset'),
            'envelope_basis': 'actual renderer control curves, not measured separate-deck loudness'}


def export_visualization(directory):
    root = Path(directory)
    audio = root/'audio'
    plan = json.loads((audio/'plan.json').read_text(encoding='utf8'))
    metrics = json.loads((audio/'metrics.json').read_text(encoding='utf8'))
    data = timeline_data(plan, metrics)
    catalog_file = root/'mood-catalog.json'
    data['mood_sources'] = json.loads(catalog_file.read_text(encoding='utf8'))['sources'] if catalog_file.exists() else {}
    artwork_file = root/'artwork.json'
    artwork = json.loads(artwork_file.read_text(encoding='utf8')).get('tracks', {}) if artwork_file.exists() else {}
    for track in data['tracks']:
        entry = artwork.get(track['id'], {})
        cover = entry.get('cover')
        if cover and (not re.fullmatch(r'covers/[a-z0-9-]+\.(jpg|png|webp)', cover)
                      or not (root/cover).is_file() or not (root/cover).resolve().is_relative_to(root.resolve())):
            raise ValueError('cover must be an existing local image inside covers/')
        track.update(album=entry.get('album', ''), cover=cover,
                     artist=entry.get('artist', 'The Weeknd'), credit_url=entry.get('credit_url'))
    info = sf.info(audio/'master.wav')
    if info.samplerate != metrics['sample_rate'] or abs(info.duration-data['duration']) > .02:
        raise ValueError('master waveform differs from declared metrics')
    peaks = [round(float(np.max(np.abs(block))), 5) for block in
             sf.blocks(audio/'master.wav', blocksize=round(info.samplerate*.1), dtype='float32', always_2d=True)]
    data['waveform'] = {'source': 'decoded_master_wav', 'step_seconds': .1, 'peaks': peaks}
    data['master_sha256'] = file_sha256(audio/'master.wav')
    data['preview_sha256'] = file_sha256(audio/'master.mp3')
    serialized = json.dumps(data, ensure_ascii=False, allow_nan=False)
    (root/'visualization.json').write_text(serialized, encoding='utf8')
    template = Path(__file__).with_name('sample_room.html').read_text(encoding='utf8')
    safe_data = serialized.replace('<', '\\u003c').replace('&', '\\u0026')
    (root/'index.html').write_text(template.replace('__SAMPLE_DATA__', safe_data), encoding='utf8')
    return data
