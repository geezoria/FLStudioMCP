"""Exercise the real FL controller and MCP client over protocol 2 offline."""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from fl_studio_mcp import bridge_client, midi_wake, paths, protocol
from fl_studio_mcp.bridge_client import BridgeClient, RPCError

DEVICE_SCRIPT = Path(__file__).resolve().parents[1] / "fl_bridge" / "device_FLStudioMCP.py"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _event(**fields):
    return SimpleNamespace(**({"status": 0xBF, "data1": 119, "data2": 1, "handled": False} | fields))


@pytest.fixture
def controller(monkeypatch, tmp_path):
    pytest.importorskip("channels", reason="fl-studio-api-stubs not installed")
    installed_script = tmp_path / "Settings" / "Hardware" / "fLMCP Bridge" / DEVICE_SCRIPT.name
    installed_script.parent.mkdir(parents=True)
    installed_script.write_bytes(DEVICE_SCRIPT.read_bytes())
    spec = importlib.util.spec_from_file_location("flmcp_transport_under_test", installed_script)
    device = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(device)
    assert device.SCRIPT_DIR == installed_script.parent

    def forbidden(*_args, **_kwargs):
        pytest.fail("the FL controller must use open() for filesystem I/O")

    monkeypatch.setattr(device, "os", SimpleNamespace(
        getpid=os.getpid, stat=forbidden, mkdir=forbidden,
        replace=forbidden, remove=forbidden, listdir=forbidden,
        path=SimpleNamespace(exists=forbidden),
    ))
    monkeypatch.setattr(device.general, "getVersion", lambda: 45)
    monkeypatch.setattr(device.ui, "getVersion", lambda: "FL Studio 26.1.6")
    monkeypatch.setitem(device._HANDLERS, "test.echo", lambda params: params)
    monkeypatch.setenv("FLMCP_BRIDGE_DIR", str(device.SCRIPT_DIR))
    monkeypatch.setattr(bridge_client, "find_wake_sender", lambda: None)
    yield device
    device.OnDeInit()


@pytest.fixture
def idle(controller):
    controller.OnInit()
    stop = threading.Event()

    def run():
        while not stop.is_set():
            controller.OnIdle()
            stop.wait(0.004)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        yield controller
    finally:
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.fixture
def wake_only(controller, monkeypatch):
    controller.OnInit()
    events = queue.Queue()

    def send():
        events.put(_event())
        return True

    def run():
        while (event := events.get()) is not None:
            controller.OnMidiMsg(event)
            assert event.handled

    monkeypatch.setattr(bridge_client, "find_wake_sender", lambda: send)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        yield controller
    finally:
        events.put(None)
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_idle_ping_echo_and_heartbeat(idle):
    client = BridgeClient()
    assert client.is_connected()
    ping = client.ping()
    assert ping["bridge_version"] == "0.4.0"
    assert ping["protocol"] == 2
    assert ping["api_version"] == 45
    assert ping["fl_version"] == "FL Studio 26.1.6"
    assert ping["idle_calls"] > 0
    assert ping["midi_wakes"] == 0

    assert client.call("test.echo", value="áudio") == {"value": "áudio"}
    req = _read(idle.COMMAND_FILE)
    resp = _read(idle.RESPONSE_FILE)
    assert req == {"protocol": 2, "id": req["id"], "action": "test.echo", "params": {"value": "áudio"}}
    assert resp == {"id": req["id"], "ok": True, "result": {"value": "áudio"},
                    "elapsed_ms": resp["elapsed_ms"]}
    assert resp["elapsed_ms"] >= 0
    assert not list(idle.SCRIPT_DIR.glob("*.tmp"))
    heartbeat = _read(idle.HEARTBEAT_FILE)
    assert {"bridge_version", "protocol", "api_version", "fl_version", "pid",
            "last_id", "idle_calls", "midi_wakes", "automation", "ts"} == heartbeat.keys()
    assert heartbeat["pid"] == os.getpid()
    assert abs(heartbeat["ts"] - time.time()) < 2


def test_batch_order_and_per_action_errors(idle, monkeypatch):
    seen = []

    def mark(params):
        seen.append(params["value"])
        return len(seen)

    monkeypatch.setitem(idle._HANDLERS, "test.mark", mark)
    client = BridgeClient()
    assert client.call_batch([("test.mark", {"value": 1}), ("unknown", {}),
                              ("test.mark", {"value": 2})]) == [
        {"ok": True, "result": 1},
        {"ok": False, "error": "ValueError: unknown action: unknown"},
        {"ok": True, "result": 2},
    ]
    assert seen == [1, 2]
    with pytest.raises(RPCError, match="at most 32"):
        client.call_batch([("test.mark", {"value": 3})] * 33)


def test_single_error_has_traceback(idle):
    with pytest.raises(RPCError, match="unknown action: unknown"):
        BridgeClient().call("unknown")
    assert "ValueError" in _read(idle.RESPONSE_FILE)["traceback"]


def test_ids_advance_after_clock_rollback_and_client_restart(idle, monkeypatch):
    BridgeClient().ping()
    first = _read(idle.COMMAND_FILE)["id"]
    monkeypatch.setattr(bridge_client.time, "time_ns", lambda: first - 1)
    BridgeClient().ping()
    assert _read(idle.COMMAND_FILE)["id"] == first + 1


def test_restarted_controller_ignores_leftover_command(controller, monkeypatch):
    count = []
    monkeypatch.setitem(controller._HANDLERS, "test.mark", lambda _: count.append(True))
    controller.COMMAND_FILE.write_bytes(protocol.encode_command(100, {"action": "test.mark"}))
    controller.OnInit()
    controller.OnIdle()
    controller.OnMidiMsg(_event())
    assert count == []
    assert _read(controller.HEARTBEAT_FILE)["last_id"] == 100

    controller.COMMAND_FILE.write_bytes(protocol.encode_command(101, {"action": "test.mark"}))
    controller.OnIdle()
    controller.OnMidiMsg(_event())
    controller.OnIdle()
    assert count == [True]


def test_reentrant_tick_and_failed_response_write_do_not_repeat_handler(controller, monkeypatch):
    calls = []
    write_failures = []

    def mark(_):
        calls.append(True)
        controller.OnMidiMsg(_event())
        return "done"

    def flaky_open(path, *args, **kwargs):
        if path == controller.RESPONSE_FILE and not write_failures:
            write_failures.append(True)
            raise PermissionError("response temporarily open in another process")
        return open(path, *args, **kwargs)

    monkeypatch.setitem(controller._HANDLERS, "test.mark", mark)
    monkeypatch.setattr(controller, "open", flaky_open, raising=False)
    controller.OnInit()
    controller.COMMAND_FILE.write_bytes(protocol.encode_command(100, {"action": "test.mark"}))
    controller.OnIdle()
    assert not controller.RESPONSE_FILE.exists()
    controller.OnIdle()
    assert _read(controller.RESPONSE_FILE)["result"] == "done"
    assert calls == write_failures == [True]


@pytest.mark.parametrize("heartbeat", [None, {"protocol": 2, "ts": 1}])
def test_missing_or_stale_heartbeat_fails_before_dispatch(controller, heartbeat):
    if heartbeat:
        controller.HEARTBEAT_FILE.write_text(json.dumps(heartbeat), encoding="utf-8")
    client = BridgeClient(timeout=3)
    assert not client.is_connected()
    start = time.monotonic()
    with pytest.raises(RPCError, match="bridge unavailable: FL Studio not running"):
        client.ping()
    assert time.monotonic() - start < 1
    assert not controller.COMMAND_FILE.exists()


def test_deinit_marks_heartbeat_stopped(controller):
    controller.OnInit()
    assert BridgeClient().is_connected()
    controller.OnDeInit()
    assert not BridgeClient().is_connected()


def test_partial_response_is_retried(controller, monkeypatch):
    partial_read = threading.Event()
    actual_read = bridge_client._read_object
    actual_open = open

    def observed_read(path):
        data = actual_read(path)
        if path == controller.RESPONSE_FILE and not data:
            partial_read.set()
        return data

    class PartialWrite:
        def __enter__(self):
            self.file = actual_open(controller.RESPONSE_FILE, "w", encoding="utf-8")
            return self

        def __exit__(self, *_):
            self.file.close()

        def write(self, body):
            # Re-open in separate writes to reproduce a reader seeing an
            # incomplete response even on Windows installations with file sharing.
            partial_read.clear()
            self.file.write(body[:7])
            self.file.flush()
            assert partial_read.wait(timeout=1)
            self.file.write(body[7:])

    def open_response(path, *args, **kwargs):
        if path == controller.RESPONSE_FILE:
            return PartialWrite()
        return actual_open(path, *args, **kwargs)

    monkeypatch.setattr(bridge_client, "_read_object", observed_read)
    monkeypatch.setattr(controller, "open", open_response, raising=False)
    controller.OnInit()
    with ThreadPoolExecutor(max_workers=1) as executor:
        call = executor.submit(BridgeClient().call, "test.echo", value="ú")
        end = time.monotonic() + 1
        while not controller.COMMAND_FILE.exists() and time.monotonic() < end:
            time.sleep(0.005)
        controller.OnIdle()
        assert call.result(timeout=1) == {"value": "ú"}


def test_permission_error_during_replace_is_retried(idle, monkeypatch):
    replace = os.replace
    attempts = []

    def flaky_replace(*args):
        attempts.append(args)
        if len(attempts) < 3:
            raise PermissionError("sharing violation")
        replace(*args)

    monkeypatch.setattr(bridge_client.os, "replace", flaky_replace)
    assert BridgeClient().ping()["ok"]
    assert len(attempts) == 3
    assert not list(idle.SCRIPT_DIR.glob("*.tmp"))


def test_timed_out_command_cannot_run_later(controller, monkeypatch):
    seen = []
    monkeypatch.setitem(controller._HANDLERS, "test.mark", lambda _: seen.append(True))
    controller.OnInit()
    with pytest.raises(RPCError, match="execution status unknown"):
        BridgeClient(timeout=0.08).call("test.mark")
    controller.OnIdle()
    assert seen == []
    assert _read(controller.COMMAND_FILE)["batch"] == []


def test_live_wake_only_controller_recovers_stale_heartbeat(wake_only):
    # Age the on-disk heartbeat without waiting five seconds.
    wake_only.HEARTBEAT_FILE.write_text('{"protocol":2,"ts":1}', encoding="utf-8")
    wake_only._last_heartbeat = time.monotonic() - 6
    client = BridgeClient()
    assert not client.is_connected()
    ping = client.ping()
    assert ping["idle_calls"] == 0
    assert ping["midi_wakes"] >= 2  # liveness probe followed by dispatch
    assert client.call_batch([("test.echo", {"wake": True})]) == [
        {"ok": True, "result": {"wake": True}},
    ]


@pytest.mark.parametrize("fields", [
    {"status": 0xB0}, {"status": 0x9F}, {"data1": 118}, {"data2": 0}, {"data2": 128},
])
def test_non_wake_midi_is_left_alone(controller, fields):
    other = _event(**fields)
    controller.OnMidiMsg(other)
    assert not other.handled
    assert controller._midi_wakes == 0


def test_wake_events_accept_any_nonzero_cc_value(controller):
    controller.OnInit()
    wake = _event(data2=127)
    controller.OnMidiMsg(wake)
    assert wake.handled
    assert controller._midi_wakes == 1


def test_async_batch_wakes_advance_real_scheduler(wake_only, monkeypatch):
    applied = threading.Event()
    monkeypatch.setattr(wake_only.mixer, "getCurrentTempo", lambda: 120_000)
    monkeypatch.setattr(wake_only, "_rec_tempo", lambda _: applied.set())
    state = {"play": 0, "rec": 0}
    monkeypatch.setattr(wake_only.transport, "isPlaying", lambda: state["play"])
    monkeypatch.setattr(wake_only.transport, "isRecording", lambda: state["rec"])
    monkeypatch.setattr(wake_only.transport, "record", lambda: state.update(rec=1 - state["rec"]))
    monkeypatch.setattr(wake_only.transport, "start", lambda: state.update(play=1 - state["play"]))
    monkeypatch.setattr(wake_only.transport, "stop", lambda: state.update(play=0))
    client = BridgeClient()
    [response] = client.call_batch([
        ("automation.recordTempo", {"points": [{"time_bars": 0.035, "bpm": 110}]}),
    ])
    assert response["result"]["mode"] == "async"
    assert applied.wait(timeout=1)
    try:
        assert wake_only._midi_wakes > 1
        assert wake_only._idle_calls == 0
    finally:
        # Keep the MIDI receiver alive until the short test job's pump exits.
        deadline = time.monotonic() + 1
        while client._pumping and time.monotonic() < deadline:
            time.sleep(0.01)


def test_concurrent_clients_do_not_overwrite_commands(idle):
    with ThreadPoolExecutor(max_workers=5) as executor:
        requests = [executor.submit(BridgeClient().call, "test.echo", value=i) for i in range(12)]
        assert [call.result(timeout=3)["value"] for call in requests] == list(range(12))


def test_cross_process_lock_times_out_then_allows_call(idle):
    ready = idle.SCRIPT_DIR / "lock-ready"
    release = idle.SCRIPT_DIR / "lock-release"
    lock_file = idle.SCRIPT_DIR / protocol.LOCK_NAME
    child_code = """
import sys
import time
from pathlib import Path
from fl_studio_mcp.bridge_client import _process_lock

lock, ready, release = map(Path, sys.argv[1:])
deadline = time.monotonic() + 10
with _process_lock(lock, deadline):
    ready.touch()
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
"""
    env = os.environ | {"PYTHONPATH": str(DEVICE_SCRIPT.parents[1] / "src")}
    with subprocess.Popen([sys.executable, "-c", child_code, lock_file, ready, release], env=env) as child:
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            with pytest.raises(RPCError, match="timed out waiting for the bridge lock"):
                BridgeClient(timeout=0.1).ping()
            assert not idle.COMMAND_FILE.exists()
        finally:
            release.touch()
        assert child.wait(timeout=2) == 0
    assert BridgeClient().ping()["ok"]


def test_introspection_reports_functions_docs_constants_and_counts(idle):
    client = BridgeClient()
    result = client.call("meta.introspect", modules=["general", "midi", "not-a-module"])
    assert result["api_version"] == 45
    assert result["python_version"] == sys.version
    assert result["idle_calls"] > 0
    assert result["midi_wakes"] == 0
    assert result["modules"].keys() == {"general", "midi"}
    docs = {item["name"]: item["doc"] for item in result["modules"]["general"]["callables"]}
    assert 0 < len(docs["processRECEvent"]) <= 400
    assert result["modules"]["midi"]["constants"]["REC_Tempo"] == idle.midi.REC_Tempo


def test_piano_roll_files_work_with_open_only(controller):
    controller.PR_REQUEST.parent.mkdir()
    controller._stage_piano_roll_request({"action": "clear"})
    controller._stage_piano_roll_request({"action": "add_notes", "notes": []})
    assert [item["action"] for item in _read(controller.PR_REQUEST)] == ["clear", "add_notes"]
    assert controller._read_piano_roll_state() is None
    controller.PR_STATE.write_text('{"noteCount":0}', encoding="utf-8")
    assert controller._read_piano_roll_state() == {"noteCount": 0}


def test_log_appends_and_resets_at_cap(controller, monkeypatch):
    monkeypatch.setattr(controller, "MAX_LOG_BYTES", 40)
    controller._log("first")
    controller._log("second")
    assert controller.LOG_FILE.read_text(encoding="utf-8") == "[fLMCP] first\n[fLMCP] second\n"
    controller._log("third")
    assert controller.LOG_FILE.read_text(encoding="utf-8") == "[fLMCP] third\n"


def test_script_directory_falls_back_without_dunder_file(controller, monkeypatch, tmp_path):
    monkeypatch.delattr(controller, "__file__")
    monkeypatch.setattr(controller.Path, "home", lambda: tmp_path)
    expected = tmp_path / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    assert controller._script_dir() == expected / "Hardware" / "fLMCP Bridge"


def test_timeout_and_directory_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLMCP_BRIDGE_DIR", str(tmp_path))
    monkeypatch.setenv("FLMCP_TIMEOUT", "2.5")
    assert paths.bridge_dir() == tmp_path
    assert BridgeClient().timeout == 2.5


def test_default_paths_follow_windows_registry(monkeypatch, tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows registry path")
    import winreg

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    def open_key(root, subkey):
        assert root == winreg.HKEY_CURRENT_USER
        assert subkey == r"Software\Image-Line\Shared\Paths"
        return FakeKey()

    def query_value(_key, name):
        assert name == "Shared data"
        return str(tmp_path), winreg.REG_SZ

    monkeypatch.delenv("FLMCP_BRIDGE_DIR", raising=False)
    monkeypatch.setattr(winreg, "OpenKey", open_key)
    monkeypatch.setattr(winreg, "QueryValueEx", query_value)
    settings = tmp_path / "FL Studio" / "Settings"
    assert paths.bridge_dir() == settings / "Hardware" / "fLMCP Bridge"
    assert paths.piano_roll_dir() == settings / "Piano roll scripts"


@pytest.mark.parametrize(("override", "selected"), [
    (None, 1), ("loopMIDI Port", 0), ("missing output", None),
])
def test_winmm_port_selection_and_wake_packet(monkeypatch, override, selected):
    if sys.platform != "win32":
        pytest.skip("Windows winmm")
    import ctypes

    output_names = ["loopMIDI Port", "fLMCP Bridge"]
    calls = []

    def get_caps(output_id, caps, _size):
        caps._obj.szPname = output_names[output_id]
        return 0

    def open_port(handle, output_id, *_):
        calls.append(("open", output_id))
        return 0

    def send_msg(_handle, data):
        calls.append(("send", data))
        return 0

    def close_port(_handle):
        calls.append(("close",))
        return 0

    library = SimpleNamespace(
        midiOutGetNumDevs=lambda: len(output_names), midiOutGetDevCapsW=get_caps,
        midiOutOpen=open_port, midiOutShortMsg=send_msg, midiOutClose=close_port,
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda _: library)
    if override is None:
        monkeypatch.delenv("FLMCP_MIDI_PORT", raising=False)
    else:
        monkeypatch.setenv("FLMCP_MIDI_PORT", override)
    sender = midi_wake.find_wake_sender()
    if selected is None:
        assert sender is None
        assert not calls
    else:
        assert sender()
        assert calls == [("open", selected), ("send", 0x01_77_BF), ("close",)]
