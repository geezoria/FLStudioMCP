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


def test_keystroke_delegates_to_file_bridge():
    # keystroke.py should no longer own its own copy of the paths.
    from fl_studio_mcp import keystroke
    assert keystroke.state_file() == file_bridge.STATE_FILE
    assert keystroke.request_file() == file_bridge.REQUEST_FILE


def _staged(monkeypatch, tmp_path, *, menu_ok, hotkey, state):
    import json
    (tmp_path / "ComposeWithLLM.pyscript").write_text("")
    for name, value in (("PR_DIR", tmp_path), ("REQUEST_FILE", tmp_path / "fLMCP_request.json"),
                        ("STATE_FILE", tmp_path / "fLMCP_state.json")):
        monkeypatch.setattr(file_bridge, name, value)
    from fl_studio_mcp import fl_windows, keystroke
    monkeypatch.setattr(fl_windows, "run_piano_roll_script",
                        lambda script, folder: {"ok": True, "steps": 5} if menu_ok
                        else {"ok": False, "reason": "no piano roll is open"})
    monkeypatch.setattr(keystroke, "send_hotkey_windows", lambda: hotkey)
    monkeypatch.setattr(file_bridge, "wait_for_state", lambda wait: state)
    result = file_bridge.stage_and_run([{"action": "clear"}], wait_sec=0.1)
    return result, json.loads((tmp_path / "fLMCP_request.json").read_text(encoding="utf-8"))


def test_stage_and_run_prefers_the_tools_menu(monkeypatch, tmp_path):
    result, _ = _staged(monkeypatch, tmp_path, menu_ok=True, hotkey=True, state={"noteCount": 0})
    assert result["ok"] is True
    assert result["trigger"] == "tools_menu"
    assert result["hotkey_sent"] is False


def test_stage_and_run_empties_the_queue_when_the_script_did_not_answer(monkeypatch, tmp_path):
    result, queue = _staged(monkeypatch, tmp_path, menu_ok=False, hotkey=True, state=None)
    assert result["ok"] is False
    assert result["trigger"] == "hotkey"
    assert queue == []
    assert "nothing was changed" in result["note"]


def test_stage_and_run_keeps_the_queue_for_a_manual_run(monkeypatch, tmp_path):
    result, queue = _staged(monkeypatch, tmp_path, menu_ok=False, hotkey=False, state=None)
    assert result["ok"] is False
    assert result["trigger"] is None
    assert queue == [{"action": "clear"}]
    assert "no piano roll is open" in result["note"]
