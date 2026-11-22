"""`flmcp` command line: run the server, install the FL Studio side, diagnose.

    flmcp                 run the MCP server over stdio (what MCP clients launch)
    flmcp install         copy the bridge scripts into FL Studio, enable the
                          controller and register the server with MCP clients
    flmcp doctor          check every link of the chain and say what to fix
    flmcp config          print config snippets for MCP clients
    flmcp uninstall       remove the FL Studio scripts (and client entries)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from . import paths

SERVER_NAME = "flmcp"
LEGACY_SERVER_NAMES = ("fl-studio-mcp",)
BRIDGE_SCRIPT = "device_FLStudioMCP.py"
PIANO_ROLL_SCRIPT = "ComposeWithLLM.pyscript"
DEFAULT_MIDI_PORT = "fLMCP"


# ---------------------------------------------------------------------------
# Locating the FL-side files shipped with the package
# ---------------------------------------------------------------------------

def bundled_bridge_dir() -> Path:
    """Directory holding the FL-side scripts (wheel: package data, dev: repo)."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "fl_bridge", here.parents[1] / "fl_bridge"):
        if (candidate / BRIDGE_SCRIPT).is_file():
            return candidate
    raise FileNotFoundError("FL bridge scripts not found next to the package")


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def script_version(path: Path) -> str | None:
    try:
        match = re.search(r'^BRIDGE_VERSION\s*=\s*"([^"]+)"', path.read_text("utf-8"), re.M)
    except OSError:
        return None
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# FL Studio MIDI settings (Windows registry)
# ---------------------------------------------------------------------------

def _fl_registry_roots() -> list[str]:
    """`Software\\Image-Line\\FL Studio NN` keys, newest first."""
    if sys.platform != "win32":
        return []
    import winreg

    roots = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Image-Line") as key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                m = re.fullmatch(r"FL Studio (\d+)", name)
                if m:
                    roots.append((int(m.group(1)), "Software\\Image-Line\\" + name))
    except OSError:
        return []
    return [r for _, r in sorted(roots, reverse=True)]


def fl_midi_inputs() -> list[dict[str, Any]]:
    """MIDI input rows FL has seen, with the controller script assigned to each."""
    if sys.platform != "win32":
        return []
    import winreg

    rows = []
    for root in _fl_registry_roots():
        base = root + r"\Devices\MIDI input"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as key:
                i = 0
                while True:
                    try:
                        port = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    values: dict[str, Any] = {}
                    with winreg.OpenKey(key, port) as sub:
                        j = 0
                        while True:
                            try:
                                n, v, _ = winreg.EnumValue(sub, j)
                            except OSError:
                                break
                            j += 1
                            values[n] = v
                    rows.append({"fl": root.rsplit("\\", 1)[-1], "port": port, **values})
        except OSError:
            continue
    return rows


def fl_instance_count() -> int:
    """Number of running FL Studio processes (0 when it can't be determined)."""
    if sys.platform == "win32":
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout
            return sum(1 for line in out.splitlines() if line.startswith('"FL64.exe"'))
        except Exception:
            return 0
    try:
        out = subprocess.run(["pgrep", "-x", "FL Studio"], capture_output=True, text=True,
                             timeout=10).stdout
        return len(out.split())
    except Exception:
        return 0


def fl_is_running() -> bool:
    return fl_instance_count() > 0


def enable_fl_controller(port: str = DEFAULT_MIDI_PORT, folder: str = "fLMCP Bridge") -> list[str]:
    """Assign the bridge script to MIDI input `port` in every FL version's settings.

    FL keeps these values as REG_SZ strings and rewrites them when it exits, so
    this only sticks while FL is closed. Returns the registry keys changed.
    """
    if sys.platform != "win32":
        return []
    import winreg

    changed = []
    for root in _fl_registry_roots():
        key_path = root + "\\Devices\\MIDI input\\" + port
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, "ScriptFolder", 0, winreg.REG_SZ, folder)
                winreg.SetValueEx(key, "Enabled", 0, winreg.REG_SZ, "1")
            changed.append(key_path)
        except OSError:
            continue
    return changed


# ---------------------------------------------------------------------------
# MIDI ports
# ---------------------------------------------------------------------------

def midi_output_names() -> list[str]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class Caps(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                    ("vDriverVersion", wintypes.UINT), ("szPname", wintypes.WCHAR * 32),
                    ("wTechnology", wintypes.WORD), ("wVoices", wintypes.WORD),
                    ("wNotes", wintypes.WORD), ("wChannelMask", wintypes.WORD),
                    ("dwSupport", wintypes.DWORD)]

    try:
        winmm = ctypes.WinDLL("winmm")
    except OSError:
        return []
    names = []
    for i in range(winmm.midiOutGetNumDevs()):
        caps = Caps()
        if winmm.midiOutGetDevCapsW(ctypes.c_size_t(i), ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


def loopmidi_exe() -> Path | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            exe = Path(base) / "Tobias Erichsen" / "loopMIDI" / "loopMIDI.exe"
            if exe.is_file():
                return exe
    return None


def ensure_loopmidi_port(port: str = DEFAULT_MIDI_PORT) -> str:
    """Make loopMIDI create `port` on its next start. Returns a status line."""
    if sys.platform != "win32":
        return "not needed on this OS"
    if any(port.lower() in n.lower() for n in midi_output_names()):
        return f"MIDI port '{port}' exists"
    exe = loopmidi_exe()
    if exe is None:
        return ("loopMIDI not installed - get it from https://www.tobias-erichsen.de/"
                "software/loopmidi.html and create a port named 'fLMCP'")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Tobias Erichsen\loopMIDI\Ports") as k:
        winreg.SetValueEx(k, port, 0, winreg.REG_DWORD, 1)
    try:
        subprocess.Popen([str(exe)], close_fds=True)
    except OSError:
        pass
    return f"registered loopMIDI port '{port}' and started loopMIDI"


# ---------------------------------------------------------------------------
# MCP client configuration
# ---------------------------------------------------------------------------

def server_command(mode: str = "auto") -> tuple[str, list[str]]:
    """How MCP clients should launch this server."""
    exe = Path(sys.executable)
    ephemeral = any(part in str(exe).lower() for part in ("archive-v0", "\\uv\\cache", "/uv/cache"))
    if mode == "uvx" or (mode == "auto" and ephemeral):
        return "uvx", ["flmcp"]
    return str(exe), ["-m", "fl_studio_mcp"]


@dataclass
class Client:
    key: str
    label: str
    path: Path
    root: str  # top-level key holding servers


def known_clients() -> list[Client]:
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    out = []
    if sys.platform == "win32":
        out.append(Client("claude-desktop", "Claude Desktop",
                          appdata / "Claude" / "claude_desktop_config.json", "mcpServers"))
        pkgs = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
        for pkg in sorted(pkgs.glob("Claude_*")) if pkgs.is_dir() else []:
            out.append(Client("claude-desktop", "Claude Desktop (Store)",
                              pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json",
                              "mcpServers"))
        vscode = appdata / "Code" / "User" / "mcp.json"
    elif sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        out.append(Client("claude-desktop", "Claude Desktop",
                          support / "Claude" / "claude_desktop_config.json", "mcpServers"))
        vscode = support / "Code" / "User" / "mcp.json"
    else:
        vscode = home / ".config" / "Code" / "User" / "mcp.json"
    out.append(Client("cursor", "Cursor", home / ".cursor" / "mcp.json", "mcpServers"))
    out.append(Client("vscode", "VS Code", vscode, "servers"))
    return out


def _entry(command: str, args: list[str], client: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"command": command, "args": args}
    if client == "vscode":
        entry = {"type": "stdio", **entry}
    return entry


def write_client_config(client: Client, command: str, args: list[str]) -> str:
    data: dict[str, Any] = {}
    if client.path.exists():
        raw = client.path.read_text("utf-8").strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return f"{client.label}: {client.path} is not valid JSON - skipped"
        backup = client.path.with_name(client.path.name + ".flmcp.bak")
        if not backup.exists():
            shutil.copy2(client.path, backup)
    servers = data.setdefault(client.root, {})
    for legacy in LEGACY_SERVER_NAMES:
        servers.pop(legacy, None)
    servers[SERVER_NAME] = _entry(command, args, client.key)
    client.path.parent.mkdir(parents=True, exist_ok=True)
    client.path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return f"{client.label}: registered in {client.path}"


def remove_client_config(client: Client) -> str | None:
    try:
        data = json.loads(client.path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    servers = data.get(client.root) or {}
    removed = [n for n in (SERVER_NAME, *LEGACY_SERVER_NAMES) if servers.pop(n, None) is not None]
    if not removed:
        return None
    client.path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return f"{client.label}: removed {', '.join(removed)}"


def register_claude_code(command: str, args: list[str]) -> str:
    claude = shutil.which("claude")
    if not claude:
        return "Claude Code: `claude` CLI not found - skipped"
    subprocess.run([claude, "mcp", "remove", "--scope", "user", SERVER_NAME],
                   capture_output=True, text=True)
    res = subprocess.run([claude, "mcp", "add", "--scope", "user", SERVER_NAME, "--", command, *args],
                         capture_output=True, text=True)
    if res.returncode == 0:
        return "Claude Code: registered (user scope)"
    return f"Claude Code: failed ({(res.stderr or res.stdout).strip()[:200]})"


def config_snippets(command: str, args: list[str]) -> str:
    generic = json.dumps({"mcpServers": {SERVER_NAME: {"command": command, "args": args}}}, indent=2)
    codex = f'[mcp_servers.{SERVER_NAME}]\ncommand = {json.dumps(command)}\nargs = {json.dumps(args)}'
    claude = " ".join(["claude mcp add --scope user", SERVER_NAME, "--", command, *args])
    return (f"# Claude Desktop / Cursor / Windsurf / most clients\n{generic}\n\n"
            f"# Claude Code\n{claude}\n\n# Codex (~/.codex/config.toml)\n{codex}\n")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _ok(msg: str) -> None:
    print(f"  [ok]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [!!]   {msg}")


def _info(msg: str) -> None:
    print(f"  [..]   {msg}")


def install_fl_files() -> list[str]:
    src = bundled_bridge_dir()
    bdir = paths.bridge_dir()
    prdir = paths.piano_roll_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    prdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / BRIDGE_SCRIPT, bdir / BRIDGE_SCRIPT)
    shutil.copy2(src / "piano_roll" / PIANO_ROLL_SCRIPT, prdir / PIANO_ROLL_SCRIPT)
    return [str(bdir / BRIDGE_SCRIPT), str(prdir / PIANO_ROLL_SCRIPT)]


def cmd_install(a: argparse.Namespace) -> int:
    print(f"fLMCP {__version__} - installing\n")
    settings = paths.settings_dir()
    if not settings.is_dir():
        _warn(f"FL Studio user data folder not found: {settings}")
        _info("Start FL Studio once (it creates the folder), or set FLMCP_BRIDGE_DIR.")
        return 1
    print("FL Studio scripts")
    for p in install_fl_files():
        _ok(p)

    if sys.platform == "win32" and not a.no_midi:
        print("\nMIDI")
        _info(ensure_loopmidi_port(a.port))
        if fl_is_running():
            _warn("FL Studio is running - close it and re-run `flmcp install` to enable the "
                  "controller automatically, or enable it by hand (see below).")
        else:
            keys = enable_fl_controller(a.port)
            if keys:
                _ok(f"assigned 'fLMCP Bridge' to MIDI input '{a.port}' ({len(keys)} FL version(s))")
            else:
                _info("FL has not seen any MIDI settings yet - enable the controller by hand.")

    if not a.no_clients:
        print("\nMCP clients")
        command, args = server_command(a.command)
        targets = set(a.client or ["claude-desktop", "claude-code", "cursor", "vscode"])
        for client in known_clients():
            if client.key in targets and (client.path.parent.exists() or a.client):
                _ok(write_client_config(client, command, args))
        if "claude-code" in targets:
            _info(register_claude_code(command, args))

    print("\nNext steps")
    print("  1. Start FL Studio. If `fl_ping` says the bridge is offline: Options > MIDI Settings >")
    print(f"     Input: select '{a.port}' (or any MIDI input), Controller type = 'fLMCP Bridge', Enable.")
    print("  2. For piano-roll writing: open a piano roll, Scripts menu > ComposeWithLLM (once).")
    print("  3. Restart your AI app and ask it: \"ping FL Studio\".  Diagnose with `flmcp doctor`.")
    return 0


def cmd_doctor(a: argparse.Namespace) -> int:
    problems = 0
    print(f"fLMCP {__version__} doctor  (Python {sys.version.split()[0]}, {sys.platform})\n")

    print("Files")
    bdir = paths.bridge_dir()
    installed = bdir / BRIDGE_SCRIPT
    try:
        bundled = bundled_bridge_dir() / BRIDGE_SCRIPT
    except FileNotFoundError:
        bundled = None
    if not installed.is_file():
        problems += 1
        _warn(f"bridge script missing: {installed}  -> run `flmcp install`")
    elif bundled and _sha(installed) != _sha(bundled):
        problems += 1
        _warn(f"bridge script is outdated ({script_version(installed)} vs "
              f"{script_version(bundled)}) -> run `flmcp install`, then restart FL")
    else:
        _ok(f"bridge script {script_version(installed)} at {bdir}")
    pr = paths.piano_roll_dir() / PIANO_ROLL_SCRIPT
    (_ok if pr.is_file() else _warn)(f"piano-roll script {'found' if pr.is_file() else 'missing'}: {pr}")

    if sys.platform == "win32":
        print("\nMIDI / FL settings")
        outs = midi_output_names()
        wake = [n for n in outs if "flmcp" in n.lower() or "loopmidi" in n.lower()]
        if wake:
            _ok(f"wake port available: {wake[0]}")
        else:
            _warn("no loopMIDI/fLMCP MIDI port - the server cannot wake the bridge. FL 2026 "
                  "does not poll scripts on idle, so calls will time out. Create a loopMIDI "
                  "port named 'fLMCP'.")
        rows = [r for r in fl_midi_inputs() if str(r.get("ScriptFolder", "")) == "fLMCP Bridge"]
        if rows:
            for r in rows:
                state = "enabled" if str(r.get("Enabled", "")) == "1" else "assigned but DISABLED"
                (_ok if "enabled" == state else _warn)(f"{r['fl']}: input '{r['port']}' {state}")
        else:
            _warn("no MIDI input has controller type 'fLMCP Bridge' yet (FL saves this on exit)")
        running = fl_is_running()
        (_ok if running else _info)(f"FL Studio {'is' if running else 'is not'} running")
        instances = fl_instance_count() if running else 0
        if instances > 1:
            problems += 1
            _warn(f"{instances} FL Studio instances are running. They share the bridge folder, "
                  "so they race for commands and one call can run in both. Close all but one.")

    print("\nBridge")
    hb_path = bdir / "mcp_heartbeat.json"
    hb = _read_heartbeat(hb_path) or None
    age = time.time() - float(hb.get("ts") or 0) if hb else None
    if hb is None:
        problems += 1
        _warn("no heartbeat - the controller script has never run. Enable it in MIDI Settings.")
    elif not hb.get("ts"):
        problems += 1
        _warn("bridge stopped cleanly (FL closed or script disabled)")
    else:
        # FL 2026 may never call OnIdle for this script, so the heartbeat only
        # refreshes when a MIDI wake arrives. A stale heartbeat is therefore not
        # a failure by itself: ping (which wakes the bridge) decides.
        from .bridge_client import BridgeClient

        try:
            client = BridgeClient(timeout=5)
            t0 = time.perf_counter()
            info = client.ping()
            rtt = (time.perf_counter() - t0) * 1000
        except Exception as e:  # noqa: BLE001
            problems += 1
            _warn(f"ping failed: {e}")
            if age is not None and age > 5:
                _info(f"heartbeat is {age:.0f}s old - FL closed, frozen, showing a dialog, "
                      "or script disabled")
        else:
            info = info if isinstance(info, dict) else {}
            version = info.get("bridge_version") or hb.get("bridge_version")
            _ok(f"bridge {version} answered in {rtt:.0f} ms: FL {hb.get('fl_version')}, "
                f"API {hb.get('api_version')}")
            fresh = _read_heartbeat(hb_path) or hb
            if not fresh.get("idle_calls"):
                _info("FL does not call OnIdle for this script; it runs on MIDI wakes only "
                      "(normal on FL 2026, needs the loopMIDI port)")
            if version != __version__:
                problems += 1
                _warn(f"FL is running bridge {version} but the server is {__version__} - "
                      "run `flmcp install`, then restart FL or reload the script")

    print()
    if problems:
        print(f"{problems} problem(s) found.")
        return 1
    print("All good.")
    return 0


def _read_heartbeat(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cmd_config(a: argparse.Namespace) -> int:
    command, args = server_command(a.command)
    print(config_snippets(command, args))
    return 0


def cmd_uninstall(a: argparse.Namespace) -> int:
    for p in (paths.bridge_dir() / BRIDGE_SCRIPT, paths.piano_roll_dir() / PIANO_ROLL_SCRIPT):
        if p.exists():
            p.unlink()
            _ok(f"removed {p}")
    if not a.keep_clients:
        for client in known_clients():
            msg = remove_client_config(client)
            if msg:
                _ok(msg)
        claude = shutil.which("claude")
        if claude:
            subprocess.run([claude, "mcp", "remove", "--scope", "user", SERVER_NAME],
                           capture_output=True)
    return 0


def main(argv: list[str] | None = None) -> int | None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] == "serve":
        from .server import main as serve

        serve()
        return 0

    parser = argparse.ArgumentParser(prog="flmcp", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"flmcp {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("install", help="install FL scripts and register MCP clients")
    p.add_argument("--client", action="append",
                   choices=["claude-desktop", "claude-code", "cursor", "vscode"],
                   help="only configure these clients (repeatable)")
    p.add_argument("--no-clients", action="store_true", help="don't touch MCP client configs")
    p.add_argument("--no-midi", action="store_true", help="don't set up loopMIDI / FL MIDI settings")
    p.add_argument("--port", default=DEFAULT_MIDI_PORT, help="MIDI port name (default: fLMCP)")
    p.add_argument("--command", choices=["auto", "python", "uvx"], default="auto",
                   help="how clients launch the server")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("doctor", help="diagnose the setup")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("config", help="print MCP client config snippets")
    p.add_argument("--command", choices=["auto", "python", "uvx"], default="auto")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("uninstall", help="remove FL scripts and client registrations")
    p.add_argument("--keep-clients", action="store_true")
    p.set_defaults(func=cmd_uninstall)

    a = parser.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
