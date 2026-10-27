"""Tests for the file-based piano-roll bridge fallbacks (no FL / no MIDI)."""

from __future__ import annotations

from fl_studio_mcp import file_bridge


def test_open_piano_roll_without_channel_is_noop():
    r = file_bridge.open_piano_roll(channel=None)
    assert r["opened"] is False
    assert "channel" in r["reason"].lower()


def test_stage_and_run_reports_missing_pyscript(monkeypatch, tmp_path):
    # Point the piano-roll dir at an empty temp dir so is_installed() is False.
    monkeypatch.setattr(file_bridge, "PR_DIR", tmp_path)
    r = file_bridge.stage_and_run([{"action": "clear"}])
    assert r["ok"] is False
    assert "not installed" in r["error"].lower()


def test_clear_request_queue_writes_empty_list(monkeypatch, tmp_path):
    req = tmp_path / "fLMCP_request.json"
    monkeypatch.setattr(file_bridge, "REQUEST_FILE", req)
    file_bridge.clear_request_queue()
    assert req.read_text(encoding="utf-8").strip() == "[]"


def test_append_request_accumulates(monkeypatch, tmp_path):
    import json
    req = tmp_path / "fLMCP_request.json"
    monkeypatch.setattr(file_bridge, "REQUEST_FILE", req)
    file_bridge.clear_request_queue()
    file_bridge._append_request({"action": "clear"})
    file_bridge._append_request({"action": "add_notes", "notes": []})
    data = json.loads(req.read_text(encoding="utf-8"))
    assert [a["action"] for a in data] == ["clear", "add_notes"]


def test_stage_and_run_bootstraps_compose_when_hotkey_has_no_state(monkeypatch, tmp_path):
    from fl_studio_mcp import keystroke

    script = tmp_path / "ComposeWithLLM.pyscript"
    script.write_text("# fixture", encoding="utf-8")
    monkeypatch.setattr(file_bridge, "PR_DIR", tmp_path)
    monkeypatch.setattr(file_bridge, "REQUEST_FILE", tmp_path / "fLMCP_request.json")
    monkeypatch.setattr(file_bridge, "STATE_FILE", tmp_path / "fLMCP_state.json")
    monkeypatch.setattr(file_bridge.sys, "platform", "win32")
    monkeypatch.setattr(keystroke, "send_hotkey_windows", lambda: False)
    monkeypatch.setattr(file_bridge, "wait_for_state", lambda _timeout: {"noteCount": 0})
    monkeypatch.setattr(keystroke, "select_compose_script_windows", lambda: True)

    result = file_bridge.stage_and_run([{"action": "export_only"}], wait_sec=1)

    assert result["ok"] is True
    assert result["hotkey_sent"] is False
    assert result["script_bootstrapped"] is True


def test_stage_and_run_skips_bootstrap_when_hotkey_returns_state(monkeypatch, tmp_path):
    from fl_studio_mcp import keystroke

    script = tmp_path / "ComposeWithLLM.pyscript"
    script.write_text("# fixture", encoding="utf-8")
    monkeypatch.setattr(file_bridge, "PR_DIR", tmp_path)
    monkeypatch.setattr(file_bridge, "REQUEST_FILE", tmp_path / "fLMCP_request.json")
    monkeypatch.setattr(file_bridge, "STATE_FILE", tmp_path / "fLMCP_state.json")
    monkeypatch.setattr(keystroke, "send_hotkey_windows", lambda: True)
    monkeypatch.setattr(file_bridge, "wait_for_state", lambda _timeout: {"noteCount": 0})
    monkeypatch.setattr(
        keystroke,
        "select_compose_script_windows",
        lambda: (_ for _ in ()).throw(AssertionError("bootstrap should not run")),
    )

    result = file_bridge.stage_and_run([{"action": "export_only"}], wait_sec=1)

    assert result["ok"] is True
    assert result["script_bootstrapped"] is False


def test_keystroke_delegates_to_file_bridge():
    # keystroke.py should no longer own its own copy of the paths.
    from fl_studio_mcp import keystroke
    assert keystroke.state_file() == file_bridge.STATE_FILE
    assert keystroke.request_file() == file_bridge.REQUEST_FILE
