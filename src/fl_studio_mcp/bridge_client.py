"""Synchronous file client for the fLMCP controller."""

from __future__ import annotations

import errno
import json
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from . import paths
from .midi_wake import find_wake_sender
from .protocol import (
    COMMAND_NAME,
    HEARTBEAT_NAME,
    LOCK_NAME,
    MAX_BATCH_ACTIONS,
    PROTOCOL,
    RESPONSE_NAME,
    RPCError,
    encode_command,
)

log = logging.getLogger("fl_studio_mcp.bridge")

POLL_INTERVAL = 0.008
HEARTBEAT_MAX_AGE = 5.0
WAKE_PROBE_TIMEOUT = 0.20
WAKE_RETRY_INTERVAL = 0.10
AUTOMATION_WAKE_INTERVAL = 0.02
AUTOMATION_START_GRACE = 1.0  # arming settle time plus one heartbeat interval
IDLE_OBSERVATION_WINDOW = 1.25
UNAVAILABLE = (
    "bridge unavailable: FL Studio not running or fLMCP Bridge script not enabled"
    " — see fl_ping / docs"
)

_request_lock = threading.Lock()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _explain(error: str) -> str:
    """Add the likely cause to FL's terse "Operation unsafe at current time"."""
    if "unsafe" not in error.lower():
        return error
    from .fl_windows import open_dialogs

    dialogs = open_dialogs()
    if dialogs:
        names = ", ".join(f"'{title}'" for title in dialogs)
        return f"{error} (FL is showing a dialog: {names}. Answer or close it in FL, then retry.)"
    return f"{error} (FL is busy, e.g. a dialog is open or a plugin is loading. Retry shortly.)"


def _finite_non_negative(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _sleep_until(deadline: float, interval: float = POLL_INTERVAL) -> None:
    time.sleep(max(0.0, min(interval, deadline - time.monotonic())))


@contextmanager
def _process_lock(path: Path, deadline: float) -> Iterator[None]:
    # Never delete the lock file: waiters must lock the same file even if a
    # holder exits. Windows permits byte-range locking past end of file.
    with path.open("a+b") as lock_file:
        fd = lock_file.fileno()
        if sys.platform == "win32":
            import msvcrt

            def acquire() -> None:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

            def release() -> None:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire() -> None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release() -> None:
                fcntl.flock(fd, fcntl.LOCK_UN)

        while True:
            try:
                acquire()
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for the bridge lock") from error
                _sleep_until(deadline)
        try:
            yield
        finally:
            release()


def _try_wake(sender: Callable[[], bool] | None) -> None:
    if sender is not None:
        try:
            sender()
        except OSError:
            log.debug("MIDI wake failed", exc_info=True)


class BridgeClient:
    def __init__(self, timeout: float | None = None, *, bridge_dir: str | Path | None = None):
        self.timeout = float(os.environ.get("FLMCP_TIMEOUT", "10") if timeout is None else timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("FLMCP_TIMEOUT must be a positive, finite number of seconds")
        self.bridge_dir = Path(bridge_dir) if bridge_dir is not None else paths.bridge_dir()
        self.command_file = self.bridge_dir / COMMAND_NAME
        self.response_file = self.bridge_dir / RESPONSE_NAME
        self.heartbeat_file = self.bridge_dir / HEARTBEAT_NAME
        self._last_id = 0
        self._pump_lock = threading.Lock()
        self._pump_until = 0.0
        self._pump_limit = 0.0
        self._pumping = False

    @cached_property
    def _wake_sender(self) -> Callable[[], bool] | None:
        return find_wake_sender()

    def close(self) -> None:
        """Reset MIDI output discovery. The file transport has no connection to close."""
        with _request_lock:
            self.__dict__.pop("_wake_sender", None)

    def _fresh_heartbeat(self) -> dict[str, Any]:
        heartbeat = _read_object(self.heartbeat_file)
        ts = heartbeat.get("ts")
        now = time.time()
        if (
            heartbeat.get("protocol") == PROTOCOL
            and type(ts) in (int, float)
            and ts > 0
            and now - HEARTBEAT_MAX_AGE <= ts <= now + HEARTBEAT_MAX_AGE
        ):
            return heartbeat
        return {}

    def is_connected(self) -> bool:
        return bool(self._fresh_heartbeat())

    def _require_heartbeat(self, action: str, deadline: float) -> dict[str, Any]:
        heartbeat = self._fresh_heartbeat()
        if heartbeat:
            return heartbeat

        # A wake-only controller cannot refresh its heartbeat between calls.
        # Probe before writing anything so a dead controller leaves no queued edit.
        sender = self._wake_sender
        if sender is not None:
            probe_deadline = min(deadline, time.monotonic() + WAKE_PROBE_TIMEOUT)
            next_wake = 0.0
            while time.monotonic() < probe_deadline:
                now = time.monotonic()
                if now >= next_wake:
                    _try_wake(sender)
                    next_wake = now + WAKE_RETRY_INTERVAL
                heartbeat = self._fresh_heartbeat()
                if heartbeat:
                    return heartbeat
                _sleep_until(probe_deadline)
        raise RPCError(action, UNAVAILABLE)

    def _next_id(self, heartbeat: dict[str, Any]) -> int:
        # Read the shared high-water marks under the cross-process lock. This
        # also handles clock rollback between calls or MCP process restarts.
        seen_ids = (
            heartbeat.get("last_id"),
            _read_object(self.command_file).get("id"),
            _read_object(self.response_file).get("id"),
            self._last_id,
        )
        self._last_id = max(time.time_ns(), max(i for i in seen_ids if type(i) is int) + 1)
        return self._last_id

    def _write_command(self, body: bytes, deadline: float) -> None:
        deadline = min(deadline, time.monotonic() + 0.25)
        temporary = None
        try:
            with NamedTemporaryFile(dir=self.bridge_dir, prefix="mcp_command.", suffix=".tmp",
                                    delete=False) as output:
                temporary = Path(output.name)
                output.write(body)
            while True:
                try:
                    os.replace(temporary, self.command_file)
                    return
                except PermissionError:
                    # Windows can deny replace while the controller has the file open.
                    if time.monotonic() >= deadline:
                        raise
                    _sleep_until(deadline)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    log.debug("could not remove bridge temporary file", exc_info=True)

    def _wait_for_response(self, action: str, request_id: int, deadline: float) -> dict[str, Any]:
        sender = self._wake_sender
        next_wake = 0.0
        next_health_check = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_wake:
                _try_wake(sender)
                next_wake = now + WAKE_RETRY_INTERVAL
            response = _read_object(self.response_file)
            response_id = response.get("id")
            if type(response_id) is int and response_id == request_id and type(response.get("ok")) is bool:
                return response
            if now >= next_health_check:
                try:
                    self._require_heartbeat(action, deadline)
                except RPCError as error:
                    raise RPCError(action, f"{error.message}; execution status unknown") from error
                next_health_check = now + 0.25
            _sleep_until(deadline)
        raise RPCError(action, f"no response within {self.timeout:g}s; execution status unknown")

    def _start_async_wake_pump(self, results: list[Any], idle_calls: int) -> None:
        sender = self._wake_sender
        if sender is None:
            return
        durations, limits = [], []
        for result in results:
            if not isinstance(result, dict) or result.get("mode") != "async":
                continue
            duration = result.get("approx_duration_sec")
            if _finite_non_negative(duration):
                durations.append(duration)
                start_timeout = result.get("start_timeout_sec")
                limits.append(duration + (start_timeout if _finite_non_negative(start_timeout) else 0))
        if not durations:
            return

        with self._pump_lock:
            now = time.monotonic()
            self._pump_until = max(self._pump_until, now + max(durations) + AUTOMATION_START_GRACE)
            # Keep waking past that while the bridge reports a recording in progress
            # (e.g. waiting for FL's record prompt), up to the bridge's start timeout.
            self._pump_limit = max(self._pump_limit, now + max(limits) + AUTOMATION_START_GRACE)
            if self._pumping:
                return
            self._pumping = True
            pump = threading.Thread(target=self._pump_wakes, args=(sender, idle_calls),
                                    name="flmcp-automation-wake", daemon=True)
            try:
                pump.start()
            except RuntimeError:
                self._pumping = False
                log.warning("could not start automation wake pump", exc_info=True)

    def _pump_wakes(self, sender: Callable[[], bool], idle_calls: int) -> None:
        idle_active_until = 0.0
        while True:
            now = time.monotonic()
            heartbeat = self._fresh_heartbeat()
            with self._pump_lock:
                recording = bool(heartbeat.get("automation")) and now < self._pump_limit
                if now >= self._pump_until and not recording:
                    self._pumping = False
                    return
            observed = heartbeat.get("idle_calls", idle_calls)
            if type(observed) is int and observed != idle_calls:
                if observed > idle_calls:
                    idle_active_until = now + IDLE_OBSERVATION_WINDOW
                else:
                    idle_active_until = 0.0  # Controller reloaded since the previous sample.
                idle_calls = observed
            if now >= idle_active_until:
                _try_wake(sender)
            time.sleep(AUTOMATION_WAKE_INTERVAL)

    def _exchange(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        if not _request_lock.acquire(timeout=self.timeout):
            raise RPCError(action, "timed out waiting for the in-process bridge lock")
        try:
            self._require_heartbeat(action, deadline)
            with _process_lock(self.bridge_dir / LOCK_NAME, deadline):
                heartbeat = self._require_heartbeat(action, deadline)
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out before dispatch")
                request_id = self._next_id(heartbeat)
                self._write_command(encode_command(request_id, payload), deadline)
                try:
                    response = self._wait_for_response(action, request_id, deadline)
                except RPCError:
                    # Replace any unread command with a no-op while preserving the ID.
                    # A handler that already started may still finish.
                    try:
                        self._write_command(encode_command(request_id, {"batch": []}),
                                            time.monotonic() + 0.05)
                    except OSError:
                        log.warning("could not clear timed-out bridge command %s", request_id)
                    raise
                if not response["ok"]:
                    raise RPCError(action, _explain(str(response.get("error") or "unknown error")))

                if "batch" in payload:
                    records = response.get("results")
                    if (
                        not isinstance(records, list)
                        or len(records) != len(payload["batch"])
                        or any(not isinstance(item, dict) for item in records)
                    ):
                        raise RPCError(action, "invalid batch response")
                    results = [item.get("result") for item in records if item.get("ok")]
                else:
                    if "result" not in response:
                        raise RPCError(action, "missing result in response")
                    results = [response["result"]]

                idle_calls = heartbeat.get("idle_calls")
                self._start_async_wake_pump(results, idle_calls if type(idle_calls) is int else 0)
                return response
        except TimeoutError as error:
            raise RPCError(action, str(error)) from error
        except OSError as error:
            raise RPCError(action, f"{UNAVAILABLE} ({error})") from error
        finally:
            _request_lock.release()

    def call(self, action: str, **params: Any) -> Any:
        return self._exchange(action, {"action": action, "params": params}).get("result")

    def call_batch(self, actions: list[tuple[str, dict]]) -> list[dict[str, Any]]:
        """Run up to 32 actions in order, returning one success/error record per action."""
        if len(actions) > MAX_BATCH_ACTIONS:
            raise RPCError("batch", f"expected at most {MAX_BATCH_ACTIONS} actions")
        payload = {"batch": [{"action": action, "params": params} for action, params in actions]}
        return self._exchange("batch", payload)["results"]

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
