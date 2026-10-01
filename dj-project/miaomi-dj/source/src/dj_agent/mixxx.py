"""Bounded Mixxx 2.5 MIDI controller bridge.

The wire format is private to this project's controller mapping. Mixxx itself
does not expose a general HTTP or OSC service in the stable release.
"""

from __future__ import annotations

import math
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

PREFIX = bytes((0xF0, 0x7D, 0x44, 0x4A, 0x01))
SUFFIX = bytes((0xF7,))
READ_KEYS = frozenset(("track_loaded", "play", "volume", "crossfader", "owner"))


class MixxxError(RuntimeError):
    """A command was rejected or the controller bridge is unavailable."""


class Port(Protocol):
    def send(self, data: bytes) -> None: ...

    def receive(self, timeout: float) -> bytes | None: ...

    def close(self) -> None: ...


def encode_frame(payload: str) -> bytes:
    try:
        raw = payload.encode("ascii")
    except UnicodeEncodeError as exc:
        raise MixxxError("wire payload must be ASCII") from exc
    if len(raw) > 96 or any(byte < 32 or byte > 126 for byte in raw):
        raise MixxxError("wire payload outside printable ASCII or too long")
    return PREFIX + raw + SUFFIX


def decode_frame(frame: bytes) -> str:
    if not frame.startswith(PREFIX) or not frame.endswith(SUFFIX):
        raise MixxxError("foreign or malformed MIDI SysEx frame")
    payload = frame[len(PREFIX) : -1]
    if len(payload) > 96 or any(byte < 32 or byte > 126 for byte in payload):
        raise MixxxError("invalid MIDI SysEx payload")
    return payload.decode("ascii")


class MidoPort:
    """RtMidi-backed port. The MIDI extra is imported only when used."""

    def __init__(self, input_name: str, output_name: str):
        try:
            import mido
        except ImportError as exc:
            raise MixxxError("install the optional midi dependencies") from exc
        self._input = mido.open_input(input_name)
        try:
            self._output = mido.open_output(output_name)
        except BaseException:
            self._input.close()
            raise
        self._mido = mido

    def send(self, data: bytes) -> None:
        self._output.send(self._mido.Message.from_bytes(data))

    def receive(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in self._input.iter_pending():
                if message.type == "sysex":
                    return bytes(message.bytes())
            time.sleep(min(0.005, max(0.0, deadline - time.monotonic())))
        return None

    def close(self) -> None:
        self._output.close()
        self._input.close()


class MixxxBridge:
    def __init__(self, port: Port, timeout: float = 0.5, load_timeout: float = 5.0):
        if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(load_timeout) or load_timeout <= 0:
            raise ValueError("timeouts must be finite and positive")
        self.port = port
        self.timeout = timeout
        self.load_timeout = load_timeout
        self.ready = False
        self._seq = 0
        self._manual: set[int] = set()
        self._mix_owned: set[int] = set()

    def _exchange(self, op: str, deck: int, value: str = "") -> str:
        self._seq = self._seq % 2_147_483_647 + 1
        seq = self._seq
        self.port.send(encode_frame(f"DJ1|{seq}|{op}|{deck}|{value}"))
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.ready = False
                raise MixxxError(f"Mixxx response timeout for {op}")
            frame = self.port.receive(remaining)
            if frame is None:
                self.ready = False
                raise MixxxError(f"Mixxx response timeout for {op}")
            try:
                fields = decode_frame(frame).split("|", 3)
            except MixxxError:
                continue
            if len(fields) != 4 or fields[0] != "DJ1" or fields[1] != str(seq):
                continue
            if fields[2] == "ERR":
                raise MixxxError(f"Mixxx rejected {op}: {fields[3]}")
            if fields[2] != "OK":
                continue
            return fields[3]

    def connect(self) -> dict:
        if self._exchange("HELLO", 0) != "1":
            raise MixxxError("Mixxx bridge protocol mismatch")
        self.ready = True
        return {"ready": True, "protocol": "DJ1"}

    def _check_deck(self, deck: int) -> None:
        if type(deck) is not int or deck not in (1, 2):
            raise ValueError("deck must be 1 or 2")

    def _check_write(self, deck: int | None = None) -> None:
        if not self.ready:
            raise MixxxError("Mixxx bridge not ready")
        if deck is not None and deck in self._manual:
            raise MixxxError("deck is under manual ownership")
        if deck is None and self._manual:
            raise MixxxError("crossfader is under manual ownership")
        if deck is not None and deck in self._mix_owned:
            raise MixxxError("deck is owned by Mixxx Auto DJ")
        if deck is None and self._mix_owned:
            raise MixxxError("crossfader is owned by Mixxx Auto DJ")

    def read(self, deck: int, key: str) -> float | str:
        self._check_deck(deck)
        if key not in READ_KEYS:
            raise ValueError("unsupported Mixxx read key")
        if not self.ready:
            raise MixxxError("Mixxx bridge not ready")
        value = self._exchange("READ", deck, key)
        if key == "owner":
            if value == "MANUAL":
                self._manual.add(deck)
                self._mix_owned.discard(deck)
            elif value == "AUTO":
                self._manual.discard(deck)
                self._mix_owned.discard(deck)
            elif value == "MIX":
                self._manual.discard(deck)
                self._mix_owned.add(deck)
            else:
                raise MixxxError("invalid owner response")
            return value
        try:
            result = float(value)
        except ValueError as exc:
            raise MixxxError("invalid numeric response") from exc
        if not math.isfinite(result):
            raise MixxxError("non-finite numeric response")
        return result

    def load_selected(self, deck: int) -> bool:
        self._check_deck(deck)
        self._check_write(deck)
        if self.read(deck, "track_loaded") != 0:
            raise MixxxError("deck must be empty before load_selected")
        if self._exchange("LOAD_SELECTED", deck) != "requested":
            raise MixxxError("Mixxx did not acknowledge load request")
        deadline = time.monotonic() + self.load_timeout
        while time.monotonic() < deadline:
            try:
                if self.read(deck, "track_loaded") == 1:
                    return True
            except MixxxError as exc:
                if "timeout" not in str(exc):
                    raise
                break
            time.sleep(0.02)
        raise MixxxError("no_track: Mixxx did not load the selected track")

    def play(self, deck: int) -> bool:
        self._check_deck(deck)
        self._check_write(deck)
        return self._exchange("PLAY", deck) == "1"

    def stop(self, deck: int) -> bool:
        self._check_deck(deck)
        self._check_write(deck)
        return self._exchange("STOP", deck) == "0"

    def level(self, deck: int, value: float) -> float:
        self._check_deck(deck)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("level must be finite and between 0 and 1")
        self._check_write(deck)
        return float(self._exchange("LEVEL", deck, format(value, ".6f")))

    def crossfade(self, value: float) -> float:
        if not math.isfinite(value) or not -1 <= value <= 1:
            raise ValueError("crossfade must be finite and between -1 and 1")
        self._check_write()
        return float(self._exchange("CROSSFADE", 0, format(value, ".6f")))

    def autodj_enable(self) -> bool:
        self._check_write()
        enabled = self._exchange("AUTODJ_ENABLE", 0) == "1"
        if enabled:
            self._mix_owned.update((1, 2))
        return enabled

    def autodj_fade_now(self) -> bool:
        if not self.ready:
            raise MixxxError("Mixxx bridge not ready")
        if self._manual:
            raise MixxxError("deck is under manual ownership")
        return self._exchange("AUTODJ_FADE", 0) == "1"

    def take(self, deck: int) -> str:
        self._check_deck(deck)
        if not self.ready:
            raise MixxxError("Mixxx bridge not ready")
        value = self._exchange("TAKE", deck)
        if value not in ("MANUAL", "MANUAL_BOTH"):
            raise MixxxError("manual ownership not confirmed")
        if value == "MANUAL_BOTH":
            self._manual.update((1, 2))
            self._mix_owned.clear()
        else:
            self._manual.add(deck)
            self._mix_owned.discard(deck)
        return value

    def release(self, deck: int) -> str:
        self._check_deck(deck)
        if not self.ready:
            raise MixxxError("Mixxx bridge not ready")
        value = self._exchange("RELEASE", deck)
        if value not in ("AUTO", "MIX"):
            raise MixxxError("automatic ownership not confirmed")
        self._manual.discard(deck)
        if value == "MIX":
            self._mix_owned.add(deck)
        else:
            self._mix_owned.discard(deck)
        return value

    def close(self) -> None:
        self.ready = False
        self.port.close()


def diagnose(mixxx_exe: Path | None = None) -> dict:
    """Read-only local availability; never reports hardware as tested."""
    executable = mixxx_exe or Path(__file__).resolve().parents[2] / "tools" / "mixxx" / "Mixxx" / "mixxx.exe"
    result: dict = {"mixxx_exe": str(executable), "mixxx_found": executable.is_file(), "midi": False}
    try:
        import mido

        result["midi"] = True
        result["midi_inputs"] = mido.get_input_names()
        result["midi_outputs"] = mido.get_output_names()
    except (ImportError, OSError) as exc:
        result["midi_error"] = str(exc)
    return result


def prepare_playlist(paths: Sequence[Path], output: Path) -> Path:
    """Write an ordered M3U8 for import into the isolated Mixxx library."""
    if not paths:
        raise ValueError("playlist requires at least one track")
    resolved = [Path(path).resolve(strict=True) for path in paths]
    if any(not path.is_file() for path in resolved):
        raise ValueError("every playlist entry must be a file")
    if any("\n" in str(path) or "\r" in str(path) for path in resolved):
        raise ValueError("playlist paths cannot contain newlines")
    output = Path(output)
    if output.resolve() in resolved:
        raise FileExistsError("playlist output cannot replace a source track")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8-sig") as stream:
        stream.write("#EXTM3U\n" + "\n".join(map(str, resolved)) + "\n")
    return output


def build_mixxx_command(executable: Path, settings_dir: Path, initial_tracks: Sequence[Path] = ()) -> list[str]:
    """Use only Mixxx's documented settings and positional track arguments."""
    if len(initial_tracks) > 2:
        raise ValueError("Mixxx startup accepts at most two initial tracks in this bridge")
    executable = Path(executable).resolve(strict=True)
    if not executable.is_file():
        raise ValueError("Mixxx executable must be a file")
    tracks = [Path(path).resolve(strict=True) for path in initial_tracks]
    if any(not track.is_file() for track in tracks):
        raise ValueError("initial track must be a file")
    settings_dir = Path(settings_dir).resolve()
    return [str(executable), "--settingsPath", str(settings_dir), *map(str, tracks)]


def launch_mixxx(executable: Path, settings_dir: Path, initial_tracks: Sequence[Path] = ()) -> subprocess.Popen:
    """Launch one isolated Mixxx instance; caller owns the returned process."""
    command = build_mixxx_command(executable, settings_dir, initial_tracks)
    settings_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(command, cwd=str(Path(command[0]).parent))
