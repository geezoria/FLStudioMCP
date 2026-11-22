import json
import time

from fl_studio_mcp import cli


def test_bundled_bridge_scripts_found():
    d = cli.bundled_bridge_dir()
    assert (d / cli.BRIDGE_SCRIPT).is_file()
    assert (d / "piano_roll" / cli.PIANO_ROLL_SCRIPT).is_file()
    assert cli.script_version(d / cli.BRIDGE_SCRIPT) == cli.__version__


def test_server_command_modes():
    cmd, args = cli.server_command("python")
    assert args == ["-m", "fl_studio_mcp"]
    assert cli.server_command("uvx") == ("uvx", ["flmcp"])


def test_write_client_config_merges_and_replaces_legacy(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    path.write_text(json.dumps({
        "theme": "dark",
        "mcpServers": {"other": {"command": "x"}, "fl-studio-mcp": {"command": "old"}},
    }))
    client = cli.Client("claude-desktop", "Claude Desktop", path, "mcpServers")
    cli.write_client_config(client, "py", ["-m", "fl_studio_mcp"])
    data = json.loads(path.read_text())
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert "fl-studio-mcp" not in data["mcpServers"]
    assert data["mcpServers"]["flmcp"] == {"command": "py", "args": ["-m", "fl_studio_mcp"]}
    assert (tmp_path / "claude_desktop_config.json.flmcp.bak").exists()

    assert "removed flmcp" in cli.remove_client_config(client)
    assert "flmcp" not in json.loads(path.read_text())["mcpServers"]


def test_vscode_entry_uses_servers_and_stdio(tmp_path):
    path = tmp_path / "mcp.json"
    client = cli.Client("vscode", "VS Code", path, "servers")
    cli.write_client_config(client, "uvx", ["flmcp"])
    assert json.loads(path.read_text())["servers"]["flmcp"]["type"] == "stdio"


def test_invalid_client_json_is_left_alone(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text("{not json")
    client = cli.Client("cursor", "Cursor", path, "mcpServers")
    assert "not valid JSON" in cli.write_client_config(client, "uvx", ["flmcp"])
    assert path.read_text() == "{not json"


def test_config_snippets_cover_clients():
    text = cli.config_snippets("uvx", ["flmcp"])
    assert "claude mcp add --scope user flmcp -- uvx flmcp" in text
    assert "[mcp_servers.flmcp]" in text


def test_doctor_reports_missing_and_stale(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLMCP_BRIDGE_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "midi_output_names", lambda: [])
    monkeypatch.setattr(cli, "fl_midi_inputs", lambda: [])
    monkeypatch.setattr(cli, "fl_is_running", lambda: False)
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "bridge script missing" in out and "no heartbeat" in out

    (tmp_path / cli.BRIDGE_SCRIPT).write_bytes(
        (cli.bundled_bridge_dir() / cli.BRIDGE_SCRIPT).read_bytes())
    (tmp_path / "mcp_heartbeat.json").write_text(json.dumps({"ts": time.time() - 60}))
    from fl_studio_mcp import bridge_client
    monkeypatch.setattr(bridge_client, "BridgeClient",
                        lambda **kw: _FakeBridge(error=RuntimeError("bridge unavailable")))
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "[ok]   bridge script" in out and "old" in out


class _FakeBridge:
    def __init__(self, result=None, error=None, **_):
        self.result, self.error = result, error

    def ping(self):
        if self.error:
            raise self.error
        return self.result


def test_doctor_accepts_wake_only_bridge_with_stale_heartbeat(tmp_path, monkeypatch, capsys):
    """FL 2026 never calls OnIdle, so the heartbeat ages between calls; ping decides."""
    from fl_studio_mcp import __version__, bridge_client

    monkeypatch.setenv("FLMCP_BRIDGE_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "midi_output_names", lambda: ["fLMCP"])
    monkeypatch.setattr(cli, "fl_midi_inputs", lambda: [
        {"fl": "FL Studio 26", "port": "fLMCP", "ScriptFolder": "fLMCP Bridge", "Enabled": "1"}])
    monkeypatch.setattr(cli, "fl_is_running", lambda: True)
    monkeypatch.setattr(cli, "fl_instance_count", lambda: 1)
    (tmp_path / cli.BRIDGE_SCRIPT).write_bytes(
        (cli.bundled_bridge_dir() / cli.BRIDGE_SCRIPT).read_bytes())
    (tmp_path / "mcp_heartbeat.json").write_text(json.dumps(
        {"ts": time.time() - 600, "idle_calls": 0, "fl_version": "26.1.7", "api_version": 45}))
    monkeypatch.setattr(bridge_client, "BridgeClient",
                        lambda **kw: _FakeBridge({"bridge_version": __version__}))
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "answered in" in out and "MIDI wakes only" in out and "All good." in out

    monkeypatch.setattr(bridge_client, "BridgeClient",
                        lambda **kw: _FakeBridge(error=RuntimeError("bridge unavailable")))
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "ping failed" in out and "600s old" in out


def test_doctor_flags_two_fl_instances(tmp_path, monkeypatch, capsys):
    """Two FL processes share the bridge folder and race for every command."""
    from fl_studio_mcp import __version__, bridge_client

    monkeypatch.setenv("FLMCP_BRIDGE_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "midi_output_names", lambda: ["fLMCP"])
    monkeypatch.setattr(cli, "fl_midi_inputs", lambda: [
        {"fl": "FL Studio 26", "port": "fLMCP", "ScriptFolder": "fLMCP Bridge", "Enabled": "1"}])
    monkeypatch.setattr(cli, "fl_is_running", lambda: True)
    monkeypatch.setattr(cli, "fl_instance_count", lambda: 2)
    (tmp_path / cli.BRIDGE_SCRIPT).write_bytes(
        (cli.bundled_bridge_dir() / cli.BRIDGE_SCRIPT).read_bytes())
    (tmp_path / "mcp_heartbeat.json").write_text(json.dumps(
        {"ts": time.time(), "idle_calls": 0, "fl_version": "26.1.7", "api_version": 45}))
    monkeypatch.setattr(bridge_client, "BridgeClient",
                        lambda **kw: _FakeBridge({"bridge_version": __version__}))
    assert cli.main(["doctor"]) == 1
    assert "2 FL Studio instances are running" in capsys.readouterr().out
