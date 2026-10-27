"""Project operations implemented by host-side Windows UI automation."""

from __future__ import annotations

def test_project_save_as_delegates_to_host_helper(monkeypatch, tmp_path):
    from fl_studio_mcp import keystroke
    from fl_studio_mcp.tools import project

    target = tmp_path / "copy.flp"
    seen = []
    registered = {}

    class FakeMCP:
        def tool(self):
            def decorator(function):
                registered[function.__name__] = function
                return function
            return decorator

    def fake_save(path):
        seen.append(path)
        return {"ok": True, "path": path, "verified": True}

    monkeypatch.setattr(keystroke, "save_project_as_windows", fake_save)
    project.register(FakeMCP())
    result = registered["project_save_as"](str(target))
    assert result["ok"] is True
    assert result["verified"] is True
    assert seen == [str(target)]


def test_project_save_as_rejects_wrong_extension(tmp_path):
    from fl_studio_mcp.keystroke import save_project_as_windows

    result = save_project_as_windows(str(tmp_path / "copy.txt"))
    assert result["ok"] is False
    assert ".flp" in result["error"]


def test_project_save_as_refuses_existing_target(tmp_path):
    from fl_studio_mcp.keystroke import save_project_as_windows

    target = tmp_path / "copy.flp"
    target.write_bytes(b"existing")
    result = save_project_as_windows(str(target))
    assert result["ok"] is False
    assert "refusing to overwrite" in result["error"]
