import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dj_agent.cli import main


def test_empty_weeknd_directory_is_not_a_successful_mix(tmp_path, capsys):
    folder = tmp_path / "weeknd"
    folder.mkdir()
    assert main(["mix", str(folder), "--output", str(tmp_path / "out")]) == 2
    assert "至少" in capsys.readouterr().err
    assert not (tmp_path / "out" / "master.wav").exists()


def test_doctor_writes_honest_machine_report(tmp_path, capsys):
    destination = tmp_path / "doctor.json"
    assert main(["doctor", "--output", str(destination)]) == 0
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["ffmpeg_available"] is True
    assert report["real_weeknd_audio_test"] == "not_run"
    assert "hardware" in report


def test_analyze_bad_file_does_not_silently_succeed(tmp_path, capsys):
    (tmp_path / "broken.mp3").write_bytes(b"not music")
    assert main(["analyze", str(tmp_path), "--backend", "librosa"]) == 2
    assert "解码" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows launcher")
def test_windows_powershell_launcher_reaches_empty_library_validation(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             str(root / "scripts" / "run_dj.ps1"), "-MusicFolder", str(tmp_path)],
                            capture_output=True, timeout=30, check=False)
    assert result.returncode == 2
    assert b"ParserError" not in result.stderr
