"""Every place that states the release version must agree with ``__version__``."""
import json
import re
from pathlib import Path

from fl_studio_mcp import __version__

ROOT = Path(__file__).resolve().parents[1]


def _toml_version(path: Path) -> str:
    return re.search(r'(?m)^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8")).group(1)


def test_pyproject_version():
    assert _toml_version(ROOT / "pyproject.toml") == __version__


def test_server_json_versions():
    data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert data["version"] == __version__
    assert {p["version"] for p in data["packages"]} == {__version__}


def test_mcpb_bundle_matches_release():
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == __version__
    assert (ROOT / "mcpb" / manifest["icon"]).is_file()
    assert (ROOT / "mcpb" / manifest["server"]["entry_point"]).is_file()
    pyproject = (ROOT / "mcpb" / "pyproject.toml").read_text(encoding="utf-8")
    assert _toml_version(ROOT / "mcpb" / "pyproject.toml") == __version__
    assert f'"flmcp-studio=={__version__}"' in pyproject
