# ruff: noqa: E402 -- Numerical thread limits must be set before importing NumPy.
import os

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '4'

import argparse
import json

from dj_agent.audio_training import run_audio_training

parser = argparse.ArgumentParser()
parser.add_argument('--dataset', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
print(json.dumps(run_audio_training(args.dataset, args.output), ensure_ascii=False, indent=2))
