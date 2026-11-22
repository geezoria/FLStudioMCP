"""Locations under FL Studio's user data folder."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def settings_dir() -> Path:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Image-Line\Shared\Paths") as key:
                shared_data, _ = winreg.QueryValueEx(key, "Shared data")
            if isinstance(shared_data, str) and shared_data.strip():
                return Path(os.path.expandvars(shared_data)) / "FL Studio" / "Settings"
        except OSError:
            pass
    return Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"


def bridge_dir() -> Path:
    override = os.environ.get("FLMCP_BRIDGE_DIR")
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return settings_dir() / "Hardware" / "fLMCP Bridge"


def piano_roll_dir() -> Path:
    return settings_dir() / "Piano roll scripts"
