from __future__ import annotations

import pytest

from fl_studio_mcp import paths, plugin_database


@pytest.fixture
def database(tmp_path):
    for rel in [
        "Effects/Dynamics/Fruity Limiter.fst",
        "Effects/Delay reverb/Fruity Reeverb 2.fst",
        "Effects/Delay reverb/Fruity Delay 3.fst",
        "Installed/Effects/VST3/FabFilter Pro-Q 3_x64.fst",
        "Installed/Effects/Fruity/Fruity Limiter.fst",
        "Generators/Synth classic/3x Osc.fst",
    ]:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")
    return tmp_path


def test_list_plugins_dedupes_and_cleans_names(database):
    names = [(e.kind, e.name, e.category) for e in plugin_database.list_plugins(database)]
    assert names == [
        ("effect", "FabFilter Pro-Q 3", "VST3"),
        ("effect", "Fruity Delay 3", "Delay reverb"),
        ("effect", "Fruity Limiter", "Dynamics"),
        ("effect", "Fruity Reeverb 2", "Delay reverb"),
        ("generator", "3x Osc", "Synth classic"),
    ]


def test_search_matches_every_word_in_name_or_category(database):
    assert [e.name for e in plugin_database.search("reverb", root=database)] == [
        "Fruity Delay 3", "Fruity Reeverb 2"]  # the "Delay reverb" category matches too
    assert [e.name for e in plugin_database.search("fruity delay", root=database)] == [
        "Fruity Delay 3", "Fruity Reeverb 2"]
    assert plugin_database.search("osc", "effect", root=database) == []


def test_resolve_prefers_exact_then_unique_matches(database):
    assert plugin_database.resolve("fruity limiter", root=database).category == "Dynamics"
    assert plugin_database.resolve("pro-q", root=database).name == "FabFilter Pro-Q 3"
    with pytest.raises(ValueError, match="matches 3 effects"):
        plugin_database.resolve("fruity", root=database)
    with pytest.raises(ValueError, match="no effect named"):
        plugin_database.resolve("ozone", root=database)
    # Names only: "Fruity Reeverb 2" sits in the "Delay reverb" category but isn't a delay.
    assert plugin_database.resolve("fruity delay", root=database).name == "Fruity Delay 3"
    preset = database / "Effects" / "Dynamics" / "Fruity Limiter.fst"
    assert plugin_database.resolve(str(preset), root=database).path == preset


def test_user_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FLMCP_USER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FLMCP_BRIDGE_DIR", raising=False)
    assert paths.settings_dir() == tmp_path / "FL Studio" / "Settings"
    assert paths.plugin_database_dir() == tmp_path / "FL Studio" / "Presets" / "Plugin database"
    assert paths.bridge_dir() == tmp_path / "FL Studio" / "Settings" / "Hardware" / "fLMCP Bridge"


def test_documents_dir_is_absolute():
    assert paths.documents_dir().is_absolute()


def test_display_name_only_strips_an_x64_suffix():
    assert plugin_database._display_name("Fruity Reeverb 2_x64") == "Fruity Reeverb 2"
    assert plugin_database._display_name("Foo (x64)") == "Foo"
    assert plugin_database._display_name("Max64") == "Max64"
    assert plugin_database._display_name("Box64") == "Box64"


def test_resolve_makes_relative_presets_absolute(database, monkeypatch):
    monkeypatch.chdir(database)
    entry = plugin_database.resolve("Effects/Dynamics/Fruity Limiter.fst", root=database)
    assert entry.path.is_absolute()
