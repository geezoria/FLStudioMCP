"""
File-based client that talks to the FL Studio bridge controller script.

FL Studio 2025 runs controller scripts in a Python sub-interpreter where
``threading`` and ``_socket`` are disabled (``start_new_thread`` /
``_socket.socket`` return NULL).  A TCP server therefore cannot run inside
FL.  Instead we exchange JSON files with the controller script:

    MCP server  -> writes  <bridge dir>/mcp_command.json   (atomic .tmp+replace)
    FL OnIdle   -> reads, deletes it, runs the action on FL's main thread,
                   then writes <bridge dir>/mcp_response.json (atomic)
    MCP server  -> polls for mcp_response.json, reads & deletes it

The bridge dir is FL Studio's
``Documents/Image-Line/FL Studio/Settings/Hardware/fLMCP Bridge``.

Calls are serialized through a lock (the controller handles one command per
OnIdle tick, and the MCP server's async tools run in worker threads under
``anyio.to_thread.run_sync``).
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
from typing import Any

from .protocol import RPCError

log = logging.getLogger("fl_studio_mcp.bridge")

POLL_INTERVAL = 0.02  # seconds between response-file checks


def _fl_settings_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.path.expandvars("%USERPROFILE%")) / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    elif sys.platform == "darwin":
        base = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    else:
        base = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    return base


def _bridge_dir() -> Path:
    return _fl_settings_dir() / "Hardware" / "fLMCP Bridge"


class BridgeClient:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._lock = threading.Lock()
        # timestamp-based, strictly increasing even across MCP-server restarts,
        # so a freshly-restarted server's ids are still > FL's last-seen id.
        self._ids = itertools.count(int(time.time() * 1000))
        self._dir = _bridge_dir()
        self._cmd = self._dir / "mcp_command.json"
        self._resp = self._dir / "mcp_response.json"
        self._heartbeat = self._dir / "mcp_heartbeat.txt"

    # ---- status ---------------------------------------------------------------
    def is_connected(self) -> bool:
        """Best-effort liveness check: the bridge dir exists and the controller's
        heartbeat file was touched recently (OnIdle writes it ~every 60 ticks)."""
        try:
            if not self._dir.is_dir():
                return False
            if not self._heartbeat.exists():
                # heartbeat appears a few seconds after FL loads the script;
                # fall back to "dir exists" so a fresh install isn't reported dead.
                return True
            age = time.time() - self._heartbeat.stat().st_mtime
            return age < 15.0
        except OSError:
            return False

    def close(self) -> None:  # API compat with the old TCP client
        pass

    # ---- request/response -----------------------------------------------------
    def call(self, action: str, **params: Any) -> Any:
        req_id = next(self._ids)
        with self._lock:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise RPCError(action=action, message=f"bridge dir unavailable: {e}")

            # drop any stale response
            try:
                self._resp.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

            payload = json.dumps({"id": req_id, "action": action, "params": params},
                                 ensure_ascii=False)
            tmp = self._dir / "mcp_command.json.tmp"
            last_err: OSError | None = None
            for _ in range(20):  # FL may briefly hold the file open while reading
                try:
                    tmp.write_text(payload, encoding="utf-8")
                    os.replace(tmp, self._cmd)
                    last_err = None
                    break
                except OSError as e:
                    last_err = e
                    time.sleep(0.01)
            if last_err is not None:
                raise RPCError(action=action, message=f"failed to write command file: {last_err}")

            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if self._resp.exists():
                    try:
                        text = self._resp.read_text(encoding="utf-8")
                        resp = json.loads(text)
                    except (OSError, json.JSONDecodeError):
                        time.sleep(POLL_INTERVAL)
                        continue
                    try:
                        self._resp.unlink()
                    except OSError:
                        pass
                    if resp.get("id") != req_id:
                        # leftover from an earlier call — keep waiting
                        time.sleep(POLL_INTERVAL)
                        continue
                    if resp.get("ok"):
                        return resp.get("result")
                    raise RPCError(action=action,
                                   message=str(resp.get("error") or "unknown error"))
                time.sleep(POLL_INTERVAL)

            # timed out — clean up the command file so it isn't run later
            try:
                self._cmd.unlink()
            except OSError:
                pass
            raise RPCError(
                action=action,
                message=(
                    f"no response from FL bridge for action={action} within {self.timeout}s. "
                    "Make sure FL Studio is running with the 'fLMCP Bridge' controller "
                    "enabled in Options > MIDI > Input."
                ),
            )

    # ---- convenience ----------------------------------------------------------
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
