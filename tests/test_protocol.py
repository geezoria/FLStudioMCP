"""BridgeClient tests for the file transport and MIDI wake-up."""

from __future__ import annotations

import json
import threading
import time

import pytest

import fl_studio_mcp.bridge_client as bridge_client_module
from fl_studio_mcp.bridge_client import BridgeClient


class FakeWakeBridge:
    def __init__(self, bridge_dir):
        self.command_file = bridge_dir / "mcp_command.json"
        self.response_file = bridge_dir / "mcp_response.json"
        self.wake_count = 0

    def wake(self):
        self.wake_count += 1
        request = json.loads(self.command_file.read_text(encoding="utf-8"))
        action = request["action"]
        if action == "raise.me":
            response = {"id": request["id"], "ok": False, "error": "boom"}
        elif action == "meta.ping":
            response = {
                "id": request["id"],
                "ok": True,
                "result": {"ok": True, "bridge_version": "fake-file-midi"},
            }
        else:
            response = {
                "id": request["id"],
                "ok": True,
                "result": {"echo": request.get("params", {})},
            }
        self.response_file.write_text(json.dumps(response), encoding="utf-8")


@pytest.fixture
def client_and_bridge(tmp_path):
    bridge = FakeWakeBridge(tmp_path)
    client = BridgeClient(bridge_dir=tmp_path, wake_sender=bridge.wake, timeout=0.5)
    return client, bridge


def test_ping_roundtrip(client_and_bridge):
    client, bridge = client_and_bridge
    result = client.ping()
    assert result == {"ok": True, "bridge_version": "fake-file-midi"}
    assert bridge.wake_count == 1
    assert client.is_connected() is True


def test_echo_params(client_and_bridge):
    client, _ = client_and_bridge
    result = client.call("anything.else", foo=1, bar="baz")
    assert result == {"echo": {"foo": 1, "bar": "baz"}}


def test_error_propagated(client_and_bridge):
    client, _ = client_and_bridge
    with pytest.raises(Exception) as exc:
        client.call("raise.me")
    assert "boom" in str(exc.value)


def test_request_write_is_complete_before_wakeup(client_and_bridge):
    client, bridge = client_and_bridge
    client.call("anything.else", value="áudio")
    request = json.loads(bridge.command_file.read_text(encoding="utf-8"))
    assert request["params"] == {"value": "áudio"}
    assert not (bridge.command_file.parent / "mcp_command.json.tmp").exists()


def test_command_replace_retries_windows_sharing_violation(tmp_path, monkeypatch):
    bridge = FakeWakeBridge(tmp_path)
    real_replace = bridge_client_module.os.replace
    attempts = 0

    def flaky_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("destination is briefly open in FL")
        return real_replace(source, destination)

    monkeypatch.setattr(bridge_client_module.os, "replace", flaky_replace)
    client = BridgeClient(bridge_dir=tmp_path, wake_sender=bridge.wake, timeout=0.5)
    assert client.call("retry.replace") == {"echo": {}}
    assert attempts == 3
    assert not list(tmp_path.glob("mcp_command.*.tmp"))


def test_retries_wakeup_until_response(tmp_path):
    wake_count = 0

    def delayed_wake():
        nonlocal wake_count
        wake_count += 1
        if wake_count < 2:
            return
        request = json.loads((tmp_path / "mcp_command.json").read_text(encoding="utf-8"))
        (tmp_path / "mcp_response.json").write_text(
            json.dumps({"id": request["id"], "ok": True, "result": {"retry": True}}),
            encoding="utf-8",
        )

    client = BridgeClient(bridge_dir=tmp_path, wake_sender=delayed_wake, timeout=0.5)
    assert client.call("retry") == {"retry": True}
    assert wake_count >= 2


def test_serializes_concurrent_calls(tmp_path):
    bridge = FakeWakeBridge(tmp_path)
    client = BridgeClient(bridge_dir=tmp_path, wake_sender=bridge.wake, timeout=0.5)
    results = []

    def invoke(value):
        results.append(client.call("echo", value=value))

    threads = [threading.Thread(target=invoke, args=(value,)) for value in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(result["echo"]["value"] for result in results) == [0, 1, 2, 3]


def test_timeout_removes_pending_command(tmp_path):
    client = BridgeClient(bridge_dir=tmp_path, wake_sender=lambda: None, timeout=0.05)
    with pytest.raises(Exception) as exc:
        client.ping()
    assert "no response" in str(exc.value).lower()
    assert not (tmp_path / "mcp_command.json").exists()


def test_close_resets_connection_state(client_and_bridge):
    client, _ = client_and_bridge
    client.ping()
    client.close()
    assert client.is_connected() is False


def test_async_automation_keeps_sending_wakeups(tmp_path):
    command_file = tmp_path / "mcp_command.json"
    response_file = tmp_path / "mcp_response.json"
    wake_count = 0

    def wake():
        nonlocal wake_count
        wake_count += 1
        request = json.loads(command_file.read_text(encoding="utf-8"))
        if wake_count == 1:
            response_file.write_text(
                json.dumps({
                    "id": request["id"],
                    "ok": True,
                    "result": {"mode": "async", "approx_duration_sec": 0.03},
                }),
                encoding="utf-8",
            )

    client = BridgeClient(bridge_dir=tmp_path, wake_sender=wake, timeout=0.5)
    assert client.call("automation.recordTempo")["mode"] == "async"
    time.sleep(0.08)
    assert wake_count >= 2
