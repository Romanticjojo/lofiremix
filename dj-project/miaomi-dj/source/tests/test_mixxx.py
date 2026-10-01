from __future__ import annotations

import subprocess
from collections import deque
from pathlib import Path

import pytest

import dj_agent.mixxx as mixxx_module
from dj_agent.mixxx import (
    MixxxBridge,
    MixxxError,
    build_mixxx_command,
    decode_frame,
    diagnose,
    encode_frame,
    prepare_playlist,
)


class ScriptedPort:
    def __init__(self, replies: list[bytes]):
        self.replies = deque(replies)
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def receive(self, timeout: float) -> bytes | None:
        return self.replies.popleft() if self.replies else None

    def close(self) -> None:
        pass


def reply(seq: int, value: str = "1", status: str = "OK") -> bytes:
    return encode_frame(f"DJ1|{seq}|{status}|{value}")


def test_frame_rejects_foreign_and_malformed_sysex():
    assert decode_frame(encode_frame("DJ1|1|HELLO|0|")) == "DJ1|1|HELLO|0|"
    with pytest.raises(MixxxError):
        decode_frame(bytes([0xF0, 0x01, 0x02, 0xF7]))
    with pytest.raises(MixxxError):
        encode_frame("DJ1|1|PLAY|1|\u6b4c")


def test_bridge_rejects_write_until_hello_acknowledged():
    port = ScriptedPort([])
    bridge = MixxxBridge(port, timeout=0.01)
    with pytest.raises(MixxxError, match="not ready"):
        bridge.play(1)
    assert port.sent == []


def test_bridge_ignores_stale_reply_and_reads_actual_state():
    port = ScriptedPort([reply(99), reply(1), reply(2, "0.25")])
    bridge = MixxxBridge(port, timeout=0.1)
    assert bridge.connect() == {"ready": True, "protocol": "DJ1"}
    assert bridge.read(1, "volume") == 0.25
    assert decode_frame(port.sent[1]) == "DJ1|2|READ|1|volume"


def test_sequence_does_not_wrap_after_127_commands():
    class EchoPort(ScriptedPort):
        def send(self, data: bytes) -> None:
            super().send(data)
            seq = int(decode_frame(data).split("|")[1])
            self.replies.append(reply(seq, "1"))

    port = EchoPort([])
    bridge = MixxxBridge(port, timeout=0.1)
    bridge.connect()
    for _ in range(128):
        assert bridge.read(1, "play") == 1
    assert decode_frame(port.sent[-1]).startswith("DJ1|129|READ|")


def test_timeout_marks_bridge_unready():
    port = ScriptedPort([reply(1)])
    bridge = MixxxBridge(port, timeout=0.01)
    bridge.connect()
    with pytest.raises(MixxxError, match="timeout"):
        bridge.read(1, "play")
    with pytest.raises(MixxxError, match="not ready"):
        bridge.stop(1)


def test_manual_takeover_blocks_writes_and_requires_release():
    port = ScriptedPort([reply(1), reply(2, "MANUAL"), reply(3, "AUTO"), reply(4, "1")])
    bridge = MixxxBridge(port, timeout=0.1)
    bridge.connect()
    assert bridge.take(1) == "MANUAL"
    with pytest.raises(MixxxError, match="manual"):
        bridge.play(1)
    assert bridge.release(1) == "AUTO"
    assert bridge.play(1) is True


def test_load_selected_reports_no_loaded_track():
    port = ScriptedPort([reply(1), reply(2, "0"), reply(3, "requested"), reply(4, "0"), reply(5, "0")])
    bridge = MixxxBridge(port, timeout=0.1)
    bridge.connect()
    with pytest.raises(MixxxError, match="no_track"):
        bridge.load_selected(1)


def test_load_selected_waits_for_track_loaded_feedback():
    port = ScriptedPort([reply(1), reply(2, "0"), reply(3, "requested"), reply(4, "0"), reply(5, "1")])
    bridge = MixxxBridge(port, timeout=0.1, load_timeout=1.0)
    bridge.connect()
    assert bridge.load_selected(1) is True


def test_playlist_contains_ordered_absolute_paths_and_rejects_missing(tmp_path):
    first, second = tmp_path / "one.wav", tmp_path / "two.wav"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    result = prepare_playlist([first, second], tmp_path / "set.m3u8")
    assert result.read_text(encoding="utf-8-sig").splitlines() == ["#EXTM3U", str(first), str(second)]
    with pytest.raises(FileNotFoundError):
        prepare_playlist([first, tmp_path / "missing.wav"], tmp_path / "bad.m3u8")
    assert not (tmp_path / "bad.m3u8").exists()


def test_playlist_refuses_to_overwrite_source_or_existing_output(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"original audio")
    with pytest.raises(FileExistsError):
        prepare_playlist([source], source)
    assert source.read_bytes() == b"original audio"
    existing = tmp_path / "set.m3u8"
    existing.write_bytes(b"existing playlist")
    with pytest.raises(FileExistsError):
        prepare_playlist([source], existing)
    assert existing.read_bytes() == b"existing playlist"


def test_take_from_autodj_marks_both_decks_manual():
    port = ScriptedPort([reply(1), reply(2, "1"), reply(3, "MANUAL_BOTH")])
    bridge = MixxxBridge(port, timeout=0.1)
    bridge.connect()
    assert bridge.autodj_enable() is True
    assert bridge.take(1) == "MANUAL_BOTH"
    with pytest.raises(MixxxError, match="manual"):
        bridge.play(2)
    with pytest.raises(MixxxError, match="manual"):
        bridge.crossfade(0)
    assert len(port.sent) == 3


def test_launch_command_uses_isolated_settings_and_at_most_two_tracks(tmp_path):
    exe = tmp_path / "mixxx.exe"
    exe.write_bytes(b"stub")
    tracks = [tmp_path / "a.wav", tmp_path / "b.wav"]
    for track in tracks:
        track.write_bytes(b"audio")
    settings = tmp_path / "settings"
    assert build_mixxx_command(exe, settings, tracks) == [
        str(exe), "--settingsPath", str(settings), str(tracks[0]), str(tracks[1]),
    ]
    with pytest.raises(ValueError, match="two"):
        build_mixxx_command(exe, settings, tracks + [tracks[0]])


def test_diagnose_finds_project_install_from_another_working_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    executable = project / "tools" / "mixxx" / "Mixxx" / "mixxx.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"stub")
    monkeypatch.setattr(mixxx_module, "__file__", str(project / "src" / "dj_agent" / "mixxx.py"))
    monkeypatch.chdir(tmp_path)
    result = diagnose()
    assert result["mixxx_found"] is True
    assert result["mixxx_exe"] == str(executable)


def test_autodj_requires_ready_bridge_and_confirms_enabled():
    port = ScriptedPort([reply(1), reply(2, "1"), reply(3, "1")])
    bridge = MixxxBridge(port, timeout=0.1)
    with pytest.raises(MixxxError, match="not ready"):
        bridge.autodj_enable()
    bridge.connect()
    assert bridge.autodj_enable() is True
    with pytest.raises(MixxxError, match="Auto DJ"):
        bridge.level(1, 0.4)
    assert bridge.autodj_fade_now() is True
    assert decode_frame(port.sent[1]) == "DJ1|2|AUTODJ_ENABLE|0|"


def test_rejects_invalid_deck_and_nonfinite_level_before_sending():
    port = ScriptedPort([reply(1)])
    bridge = MixxxBridge(port, timeout=0.1)
    bridge.connect()
    with pytest.raises(ValueError):
        bridge.play(3)
    with pytest.raises(ValueError):
        bridge.level(1, float("nan"))
    assert len(port.sent) == 1


def test_js_mapping_handles_commands_and_manual_change():
    harness = Path(__file__).resolve().parents[1] / "integrations" / "mixxx" / "bridge_harness.js"
    result = subprocess.run(["node", str(harness)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
