"""Offline discovery and invocation through the MCP 2 client."""
from __future__ import annotations

import json
from importlib.metadata import version
from threading import Lock, get_ident
from time import sleep

import pytest
from anyio import create_task_group, run
from mcp.client import Client

from fl_studio_mcp import __version__
from fl_studio_mcp.resources import project as project_resources
from fl_studio_mcp.server import build_app
from fl_studio_mcp.tools import piano_roll


@pytest.fixture(autouse=True)
def default_toolsets(monkeypatch):
    monkeypatch.delenv("FLMCP_TOOLSETS", raising=False)
    monkeypatch.delenv("FLMCP_DISABLE_TOOLS", raising=False)


def test_build_app():
    app = build_app()
    tools = run(app.list_tools)
    assert len(tools) == 160
    names = {t.name for t in tools}
    # Spot-check coverage across every module
    for required in [
        "fl_ping",
        "transport_play", "transport_set_tempo", "transport_set_time_signature",
        "pattern_create", "pattern_clone",
        "channel_set_step_sequence", "channel_quick_quantize",
        "mixer_set_volume", "mixer_set_eq_band", "mixer_route",
        "plugin_find_param", "plugin_list_mixer_track",
        "piano_roll_add_notes", "piano_roll_add_arpeggio", "piano_roll_humanize",
        "piano_roll_clear", "piano_roll_read",
        "playlist_add_marker",
        "arrangement_select",
        "automation_record_tempo", "automation_record_plugin_param",
        "project_save", "project_undo", "project_render",
        "ui_show_window", "ui_hint",
        "gen_emit_chord_progression",
        "gen_emit_drum_pattern_notes", "gen_emit_drum_pattern_step_seq",
        "gen_emit_bassline", "gen_emit_melody", "gen_emit_arpeggio",
        "piano_roll_status",
        # voice + audio tools (registered without their heavy deps installed)
        "voice_to_piano_roll", "voice_notes_to_piano_roll", "voice_list_devices",
        "audio_analyze", "audio_slice", "audio_melody_to_piano_roll",
        "gen_emit_dnb_groove", "song_to_dnb_flip", "gen_list_dnb_styles",
    ]:
        assert required in names, f"missing tool: {required}"


def test_piano_roll_tools_accept_channel_and_pattern():
    """The piano-roll tools document channel/pattern routing — verify the params
    actually exist on the registered tool schemas."""
    app = build_app()
    tools = {t.name: t for t in run(app.list_tools)}
    for name in ("piano_roll_add_notes", "piano_roll_clear", "piano_roll_quantize"):
        props = tools[name].input_schema.get("properties", {})
        assert "channel" in props, f"{name} missing channel param"
        assert "pattern" in props, f"{name} missing pattern param"


def test_mcp_discovery_and_pure_tool():
    async def exercise():
        async with Client(build_app(), mode="legacy") as client:
            assert client.server_info is not None
            assert client.server_info.title == "fLMCP — FL Studio MCP"
            assert client.server_info.version == __version__ == version("fl-studio-mcp")
            assert client.server_info.website_url == "https://github.com/geezoria/FLStudioMCP"

            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert len(tools) == 160
            for tool in tools.values():
                assert tool.title and tool.title.strip()
                assert tool.description and tool.description.strip()
                assert tool.annotations is not None
                assert tool.annotations.open_world_hint is False
                assert tool.annotations.read_only_hint is not None
                assert tool.annotations.destructive_hint is not None
                assert tool.annotations.idempotent_hint is not None

            assert tools["fl_ping"].annotations.read_only_hint is True
            assert tools["mixer_set_volume"].annotations.idempotent_hint is True
            assert tools["mixer_set_volume"].annotations.read_only_hint is False
            for name in ("transport_play", "mixer_mute", "plugin_show_editor"):
                assert tools[name].annotations.idempotent_hint is False
            for name in ("fl_call_raw", "project_save_as", "piano_roll_clear", "channel_set_step_sequence"):
                assert tools[name].annotations.destructive_hint is True

            resources = (await client.list_resources()).resources
            assert {resource.uri for resource in resources} == {
                "fl://status", "fl://project", "fl://transport", "fl://channels",
                "fl://mixer", "fl://patterns", "fl://playlist",
            }
            assert all(resource.title and resource.mime_type == "application/json" for resource in resources)

            result = await client.call_tool("gen_chord_notes", {"root": "C4", "quality": "maj"})
            assert not result.is_error
            assert result.structured_content == {"notes": [60, 64, 67]}

    run(exercise)


def test_mcp_prompts_list_and_render():
    workflows = {
        "make_beat": (
            {"genre": "house", "bpm": "124", "bars": "8"},
            ("house", "124 BPM", "8 bars", "gen_emit_drum_pattern_step_seq", "channel_get_step_sequence"),
        ),
        "write_chord_progression": (
            {"key": "F# minor", "mood": "dreamy"},
            ("F# minor", "dreamy", "gen_emit_chord_progression", "piano_roll_read"),
        ),
        "hum_to_melody": ({}, ("voice_record_and_transcribe", "voice_notes_to_piano_roll", "piano_roll_read")),
        "flip_song_to_dnb": (
            {"path": r"C:\Music\Example track.wav"},
            (r"C:\Music\Example track.wav", "audio_analyze", "song_to_dnb_flip", "piano_roll_read"),
        ),
        "mix_check": ({}, ("read-only", "mixer_get_eq", "agreement before changing")),
        "project_overview": ({}, ("read-only", "project_metadata", "transport_status")),
    }

    async def exercise():
        async with Client(build_app()) as client:
            prompts = {prompt.name: prompt for prompt in (await client.list_prompts()).prompts}
            assert prompts.keys() == workflows.keys()
            for name, (arguments, expected_text) in workflows.items():
                prompt = prompts[name]
                assert prompt.title and prompt.description
                assert {arg.name for arg in (prompt.arguments or []) if arg.required} == arguments.keys()
                result = await client.get_prompt(name, arguments)
                (message,) = result.messages
                assert message.role == "user"
                assert message.content.text.startswith("Start with fl_ping.")
                assert all(text in message.content.text for text in expected_text)

    run(exercise)


@pytest.mark.parametrize(
    ("selection", "disabled", "count", "present", "absent"),
    [
        ("lean", "", 41, {"gen_emit_bassline", "piano_roll_read", "mixer_set_volume"},
         {"fl_call_raw", "voice_to_piano_roll", "mixer_set_eq_band", "project_save"}),
        ("core", "", 24, {"transport_tap_tempo", "project_undo", "project_save"},
         {"project_render", "channel_all"}),
        (" patterns, MIXER ", "", 33, {"pattern_list", "mixer_get_eq"},
         {"fl_bridge_info", "channel_all", "transport_play"}),
        ("core, patterns", "project_save, pattern_list", 35, {"pattern_create", "transport_play"},
         {"project_save", "pattern_list"}),
        ("all", "fl_ping, gen_chord_notes", 158, {"channel_all"}, {"fl_ping", "gen_chord_notes"}),
    ],
)
def test_toolset_selection(monkeypatch, selection, disabled, count, present, absent):
    monkeypatch.setenv("FLMCP_TOOLSETS", selection)
    monkeypatch.setenv("FLMCP_DISABLE_TOOLS", disabled)

    async def exercise():
        async with Client(build_app()) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert len(names) == count
            assert present | {"fl_list_toolsets"} <= names
            assert names.isdisjoint(absent)
            result = await client.call_tool("fl_list_toolsets")
            assert not result.is_error
            details = result.structured_content
            assert details["enabled_tool_count"] == count
            assert {name for group in details["groups"].values()
                    for name in group["enabled_tools"]} == names
            assert details["disabled_tools"] == sorted(
                name.strip() for name in disabled.split(",") if name.strip()
            )

    run(exercise)


def test_playlist_resource_preserves_partial_state(monkeypatch):
    class PartialBridge:
        def call(self, action, **params):
            if action == "playlist.allTracks":
                assert params == {"include_empty": False}
                return {"tracks": [{"index": 1, "name": "Drums"}]}
            if action == "playlist.listClips":
                assert params == {"track": None}
                raise RuntimeError("Clip enumeration unavailable")
            assert action == "playlist.listMarkers"
            return {"markers": [], "note": "Marker enumeration unavailable"}

    monkeypatch.setattr(project_resources, "get_client", PartialBridge)

    async def exercise():
        async with Client(build_app()) as client:
            result = await client.read_resource("fl://playlist")
            (content,) = result.contents
            assert content.mime_type == "application/json"
            assert json.loads(content.text) == {
                "tracks": {"tracks": [{"index": 1, "name": "Drums"}]},
                "clips": {"ok": False, "error": "Clip enumeration unavailable"},
                "markers": {"markers": [], "note": "Marker enumeration unavailable"},
            }

    run(exercise)


def test_piano_roll_calls_serialize_off_the_event_loop(monkeypatch):
    event_loop_thread = get_ident()
    in_flight = Lock()
    edited_channels = []

    def fake_stage_and_run(actions, *, channel, pattern):
        assert get_ident() != event_loop_thread
        assert in_flight.acquire(blocking=False), "overlapping piano-roll commands"
        try:
            sleep(0.025)
            edited_channels.append(channel)
            return {"ok": True}
        finally:
            in_flight.release()

    monkeypatch.setattr(piano_roll, "stage_and_run", fake_stage_and_run)

    async def exercise():
        async with Client(build_app()) as client:
            await client.list_tools()

            async def append_note(channel):
                result = await client.call_tool(
                    "piano_roll_add_notes", {"notes": [{"midi": 60}], "channel": channel},
                )
                assert not result.is_error
                assert result.structured_content == {"ok": True}

            async with create_task_group() as tasks:
                tasks.start_soon(append_note, 0)
                tasks.start_soon(append_note, 1)

    run(exercise)
    assert sorted(edited_channels) == [0, 1]
