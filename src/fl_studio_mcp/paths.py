"""Locations under FL Studio's user data folder and its install folder."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SHARED_PATHS_KEY = r"Software\Image-Line\Shared\Paths"


def _registry_path(name: str) -> str | None:
    """A value under HKCU\\Software\\Image-Line\\Shared\\Paths (Windows only)."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SHARED_PATHS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if isinstance(value, str) and value.strip():
        return os.path.expandvars(value.strip())
    return None


def documents_dir() -> Path:
    """The user's Documents folder, following a OneDrive or custom redirect on Windows."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        from uuid import UUID

        # FOLDERID_Documents
        guid = (ctypes.c_byte * 16).from_buffer_copy(UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}").bytes_le)
        out = wintypes.LPWSTR()
        try:
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                try:
                    if out.value:
                        return Path(out.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(out)
        except (AttributeError, OSError):
            pass
    return Path.home() / "Documents"


def user_data_dir() -> Path:
    """FL's user data folder (`Documents/Image-Line` by default).

    `FLMCP_USER_DATA_DIR` overrides it, for a folder set in FL's Options > File
    settings that the registry doesn't show (or on macOS).
    """
    override = os.environ.get("FLMCP_USER_DATA_DIR")
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    shared_data = _registry_path("Shared data")
    if shared_data:
        return Path(shared_data)
    return documents_dir() / "Image-Line"


def settings_dir() -> Path:
    return user_data_dir() / "FL Studio" / "Settings"


def bridge_dir() -> Path:
    override = os.environ.get("FLMCP_BRIDGE_DIR")
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return settings_dir() / "Hardware" / "fLMCP Bridge"


def piano_roll_dir() -> Path:
    return settings_dir() / "Piano roll scripts"


def plugin_database_dir() -> Path:
    """FL's Plugin database: one .fst preset per plugin, kept by the Plugin Manager."""
    return user_data_dir() / "FL Studio" / "Presets" / "Plugin database"


def fl_install_dir() -> Path | None:
    """FL Studio's install folder (the one with Data/ and Shared/), if the registry knows it."""
    install = _registry_path("Install path")
    return Path(install) if install else None
