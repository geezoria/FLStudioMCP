"""Server-side handling of FL's modal dialogs (no FL or real windows needed)."""

from __future__ import annotations

from fl_studio_mcp import bridge_client, fl_windows
from fl_studio_mcp.tools import automation, transport


class Registrar:
    """Collects tool functions the way ToolRegistrar would, without an MCP app."""

    def __init__(self):
        self.tools = {}

    def _decorator(self, *_args, **_kwargs):
        def wrap(fn):
            self.tools[fn.__name__] = fn
            return fn
        return wrap

    read = write = _decorator


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def call(self, action, **params):
        self.calls.append((action, params))
        return dict(self.result)


def _fake_prompt(monkeypatch, buttons):
    """A record prompt whose TQuickFocusBtn children sit at the given tops."""
    clicked = []
    prompt = 100
    children = [(200 + i, fl_windows.BUTTON_CLASS, "", 1) for i in range(len(buttons))]
    tops = {200 + i: top for i, top in enumerate(buttons)}
    monkeypatch.setattr(fl_windows, "_user32", lambda: object())
    monkeypatch.setattr(fl_windows, "_fl_dialogs",
                        lambda _u: [(prompt, fl_windows.RECORD_PROMPT_TITLE)])
    monkeypatch.setattr(fl_windows, "_windows", lambda _u, parent=None: children)
    monkeypatch.setattr(fl_windows, "_top", lambda _u, hwnd: tops[hwnd])
    monkeypatch.setattr(fl_windows, "_click", lambda _u, hwnd: clicked.append(hwnd))
    return clicked


def test_answer_record_prompt_clicks_button_by_vertical_order(monkeypatch):
    # Enumeration order differs from screen order; the third from the top wins.
    clicked = _fake_prompt(monkeypatch, [300, 100, 500, 200, 400])
    assert fl_windows.answer_record_prompt(wait=0) == "Notes and automation"
    assert clicked == [200]   # tops sorted: 100, 200, 300 (hwnd 200), 400, 500


def test_answer_record_prompt_refuses_unexpected_layout(monkeypatch):
    clicked = _fake_prompt(monkeypatch, [100, 200, 300])
    assert fl_windows.answer_record_prompt(wait=0) is None
    assert clicked == []


def test_answer_record_prompt_without_prompt(monkeypatch):
    monkeypatch.setattr(fl_windows, "_user32", lambda: object())
    monkeypatch.setattr(fl_windows, "_fl_dialogs", lambda _u: [(1, "Confirm")])
    assert fl_windows.answer_record_prompt(wait=0) is None


def test_dialog_helpers_are_inert_off_windows(monkeypatch):
    monkeypatch.setattr(fl_windows.sys, "platform", "darwin")
    assert fl_windows.open_dialogs() == []
    assert fl_windows.answer_record_prompt(wait=0) is None


def test_unsafe_error_names_open_dialog(monkeypatch):
    monkeypatch.setattr(fl_windows, "open_dialogs", lambda: ["Recording"])
    message = bridge_client._explain("Operation unsafe at current time")
    assert "'Recording'" in message
    assert bridge_client._explain("bad channel") == "bad channel"


def test_unsafe_error_without_dialog_says_busy(monkeypatch):
    monkeypatch.setattr(fl_windows, "open_dialogs", lambda: [])
    assert "busy" in bridge_client._explain("Operation unsafe at current time")


def test_automation_answers_record_prompt(monkeypatch):
    client = FakeClient({"ok": True, "mode": "async"})
    monkeypatch.setattr(automation, "get_client", lambda: client)
    choices = []
    monkeypatch.setattr(fl_windows, "answer_record_prompt",
                        lambda choice: choices.append(choice) or fl_windows.RECORD_PROMPT_CHOICES[choice])
    result = automation._record("automation.recordMixerVolume", track=1, points=[])
    assert client.calls == [("automation.recordMixerVolume", {"track": 1, "points": []})]
    assert choices == [fl_windows.NOTES_AND_AUTOMATION]
    assert "Notes and automation" in result["record_prompt"]


def test_automation_leaves_prompt_alone_on_bridge_error(monkeypatch):
    monkeypatch.setattr(automation, "get_client", lambda: FakeClient({"ok": False, "error": "no points"}))
    monkeypatch.setattr(fl_windows, "answer_record_prompt",
                        lambda choice: (_ for _ in ()).throw(AssertionError("must not answer")))
    assert "record_prompt" not in automation._record("automation.recordTempo", points=[])


def test_automation_without_prompt_adds_nothing(monkeypatch):
    monkeypatch.setattr(automation, "get_client", lambda: FakeClient({"ok": True}))
    monkeypatch.setattr(fl_windows, "answer_record_prompt", lambda choice: None)
    assert automation._record("automation.recordTempo", points=[]) == {"ok": True}


def test_transport_record_reports_open_prompt(monkeypatch):
    registrar = Registrar()
    transport.register(registrar)
    monkeypatch.setattr(transport, "get_client", lambda: FakeClient({"ok": True}))
    monkeypatch.setattr(transport, "_dialogs_soon", lambda wait=0.5: ["Recording"])
    result = registrar.tools["transport_record"]()
    assert result["record_prompt_open"] is True and "hint" in result

    monkeypatch.setattr(transport, "_dialogs_soon", lambda wait=0.5: [])
    assert registrar.tools["transport_record"]() == {"ok": True}
