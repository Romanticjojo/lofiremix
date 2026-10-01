"""Audio I/O through FFmpeg; never modifies source files."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np


def probe(path: Path) -> dict:
    if not Path(path).is_file():
        raise ValueError(f"找不到音频文件：{path}")
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, check=True)
        data = json.loads(result.stdout)
        stream = next(s for s in data["streams"] if s.get("codec_type") == "audio")
        duration = float(stream.get("duration") or data["format"]["duration"])
        if not np.isfinite(duration) or duration <= 0:
            raise ValueError("invalid duration")
        tags = {k.lower(): v for k, v in data.get("format", {}).get("tags", {}).items()}
        return {"duration": duration, "sample_rate": int(stream["sample_rate"]),
                "channels": int(stream["channels"]), "tags": tags}
    except (subprocess.SubprocessError, KeyError, StopIteration, ValueError) as error:
        raise ValueError(f"无法解码音频：{path.name}") from error


def decode(path: Path, sample_rate=48000, channels=2, start=0., duration=None) -> np.ndarray:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("找不到 FFmpeg，请先安装并加入 PATH。")
    command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path)]
    if start:
        command += ["-ss", f"{start:.8f}"]
    if duration is not None:
        command += ["-t", f"{duration:.8f}"]
    command += ["-map", "0:a:0", "-ac", str(channels), "-ar", str(sample_rate),
                "-f", "f32le", "pipe:1"]
    result = subprocess.run(command, capture_output=True, timeout=300, check=False)
    if result.returncode or not result.stdout:
        raise ValueError(f"无法解码音频：{Path(path).name}")
    audio = np.frombuffer(result.stdout, dtype="<f4").reshape(-1, channels).copy()
    if not np.isfinite(audio).all():
        raise ValueError("音频包含非有限数值")
    return audio
