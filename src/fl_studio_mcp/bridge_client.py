"""Synchronous file transport with a MIDI wake-up for FL Studio 2025.

The MCP process writes a JSON request, sends a dedicated Control Change through
loopMIDI, and polls for the response written by ``OnMidiMsg`` on FL's main
thread. The public client API remains compatible with the former TCP client.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .protocol import HOST, PORT, RPCError

log = logging.getLogger("fl_studio_mcp.bridge")

POLL_INTERVAL = 0.01
WAKE_RETRY_INTERVAL = 0.10
AUTOMATION_WAKE_INTERVAL = 0.02
DEFAULT_MIDI_PORT = "loopMIDI Port"
WAKE_MESSAGE = 0x01_77_BF  # status=0xBF, controller=119, value=1


def _bridge_dir() -> Path:
    if sys.platform == "win32":
        home = Path(os.path.expandvars("%USERPROFILE%"))
    else:
        home = Path.home()
    return home / "Documents" / "Image-Line" / "FL Studio" / "Settings" / "Hardware" / "fLMCP Bridge"


def send_midi_wakeup(port_name: str = DEFAULT_MIDI_PORT) -> None:
    """Send the bridge wake-up CC through a Windows MIDI output device."""
    if sys.platform != "win32":
        raise OSError("MIDI wake-up transport requires Windows")

    import ctypes
    from ctypes import wintypes

    class MIDIOUTCAPSW(ctypes.Structure):
        _fields_ = [
            ("wMid", wintypes.WORD),
            ("wPid", wintypes.WORD),
            ("vDriverVersion", wintypes.UINT),
            ("szPname", wintypes.WCHAR * 32),
            ("wTechnology", wintypes.WORD),
            ("wVoices", wintypes.WORD),
            ("wNotes", wintypes.WORD),
            ("wChannelMask", wintypes.WORD),
            ("dwSupport", wintypes.DWORD),
        ]

    winmm = ctypes.WinDLL("winmm")
    winmm.midiOutGetNumDevs.restype = wintypes.UINT
    winmm.midiOutGetDevCapsW.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(MIDIOUTCAPSW),
        wintypes.UINT,
    ]
    winmm.midiOutOpen.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    winmm.midiOutShortMsg.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    winmm.midiOutClose.argtypes = [wintypes.HANDLE]

    devices: list[tuple[int, str]] = []
    for device_id in range(winmm.midiOutGetNumDevs()):
        caps = MIDIOUTCAPSW()
        result = winmm.midiOutGetDevCapsW(device_id, ctypes.byref(caps), ctypes.sizeof(caps))
        if result == 0:
            devices.append((device_id, caps.szPname))

    matches = [device_id for device_id, name in devices if name == port_name]
    if len(matches) != 1:
        names = ", ".join(name for _, name in devices) or "none"
        raise OSError(f"expected one MIDI output named {port_name!r}; found {matches}; outputs: {names}")

    handle = wintypes.HANDLE()
    result = winmm.midiOutOpen(ctypes.byref(handle), matches[0], None, None, 0)
    if result != 0:
        raise OSError(f"midiOutOpen failed with MMRESULT {result}")
    try:
        result = winmm.midiOutShortMsg(handle, WAKE_MESSAGE)
        if result != 0:
            raise OSError(f"midiOutShortMsg failed with MMRESULT {result}")
    finally:
        winmm.midiOutClose(handle)


class BridgeClient:
    def __init__(
        self,
        host: str = HOST,
        port: int = PORT,
        timeout: float = 10.0,
        *,
        bridge_dir: str | Path | None = None,
        midi_port: str | None = None,
        wake_sender: Callable[[], None] | None = None,
    ):
        # host and port remain compatibility attributes for callers using the
        # old constructor. The active transport is file + MIDI.
        self.host = host
        self.port = port
        self.timeout = timeout
        self.bridge_dir = Path(bridge_dir) if bridge_dir is not None else _bridge_dir()
        self.command_file = self.bridge_dir / "mcp_command.json"
        self.response_file = self.bridge_dir / "mcp_response.json"
        self.midi_port = midi_port or os.environ.get("FL_STUDIO_MCP_MIDI_PORT", DEFAULT_MIDI_PORT)
        self._wake_sender = wake_sender or (lambda: send_midi_wakeup(self.midi_port))
        self._lock = threading.Lock()
        self._ids = itertools.count(time.time_ns())
        self._last_success: float | None = None

    def close(self) -> None:
        """Keep API compatibility with the former persistent TCP client."""
        self._last_success = None

    def is_connected(self) -> bool:
        return self._last_success is not None and (time.monotonic() - self._last_success) < 15.0

    def _write_command(self, envelope: dict[str, Any]) -> None:
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        request_id = envelope.get("id", "request")
        temporary = self.bridge_dir / (
            f"mcp_command.{os.getpid()}.{threading.get_ident()}.{request_id}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            deadline = time.monotonic() + min(1.0, max(0.1, self.timeout / 2))
            while True:
                try:
                    os.replace(temporary, self.command_file)
                    return
                except PermissionError:
                    # FL may still have the previous command open for a few
                    # milliseconds. Retrying is required on Windows, where an
                    # open destination cannot be replaced atomically.
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(POLL_INTERVAL)
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    def _read_response(self, request_id: int) -> dict[str, Any] | None:
        try:
            raw = self.response_file.read_text(encoding="utf-8")
            response = json.loads(raw)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(response, dict) or response.get("id") != request_id:
            return None
        try:
            self.response_file.unlink()
        except OSError:
            pass
        return response

    def _start_async_wake_pump(self, result: Any) -> None:
        """Keep asynchronous FL-side jobs moving when OnIdle is unavailable.

        FL Studio 2025 delivers ``OnMidiMsg`` but may never call ``OnIdle`` for
        this controller. The MCP process can use threads safely, so it sends
        wake bytes until the last scheduled automation point has been applied.
        """
        if not isinstance(result, dict) or result.get("mode") != "async":
            return
        try:
            duration = max(0.0, float(result.get("approx_duration_sec", 0.0)))
        except (TypeError, ValueError):
            return

        def pump() -> None:
            deadline = time.monotonic() + duration + 0.25
            while time.monotonic() <= deadline:
                try:
                    self._wake_sender()
                except OSError as error:
                    log.warning("automation MIDI wake-up failed: %s", error)
                    return
                time.sleep(AUTOMATION_WAKE_INTERVAL)

        threading.Thread(target=pump, name="flmcp-automation-wake", daemon=True).start()

    def call(self, action: str, **params: Any) -> Any:
        request_id = next(self._ids)
        envelope = {"id": request_id, "action": action, "params": params}

        with self._lock:
            try:
                self.response_file.unlink()
            except OSError:
                pass

            try:
                self._write_command(envelope)
                self._wake_sender()
            except OSError as error:
                raise RPCError(action=action, message=f"file/MIDI bridge unavailable: {error}") from error

            deadline = time.monotonic() + self.timeout
            next_wake = time.monotonic() + WAKE_RETRY_INTERVAL
            while time.monotonic() < deadline:
                response = self._read_response(request_id)
                if response is not None:
                    self._last_success = time.monotonic()
                    if response.get("ok"):
                        result = response.get("result")
                        self._start_async_wake_pump(result)
                        return result
                    raise RPCError(action=action, message=str(response.get("error") or "unknown error"))

                now = time.monotonic()
                if now >= next_wake:
                    try:
                        self._wake_sender()
                    except OSError as error:
                        log.warning("MIDI wake-up retry failed for %s: %s", action, error)
                    next_wake = now + WAKE_RETRY_INTERVAL
                time.sleep(POLL_INTERVAL)

            try:
                self.command_file.unlink()
            except OSError:
                pass
            raise RPCError(
                action=action,
                message=(
                    f"no response within {self.timeout}s; ensure loopMIDI output {self.midi_port!r} "
                    "exists and the matching FL Studio input is enabled with Controller type "
                    "'fLMCP Bridge'"
                ),
            )

    def ping(self) -> dict:
        return self.call("meta.ping")


_singleton: BridgeClient | None = None
_singleton_lock = threading.Lock()


def get_client() -> BridgeClient:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = BridgeClient()
        return _singleton
