"""Piano roll tools — file-based bridge (NO MIDI required).

Pipeline:
  1. MCP tool stages one or more actions into `fLMCP_request.json` in FL's
     Piano roll scripts folder.
  2. MCP tool sends Ctrl+Alt+Y to the FL Studio window (Win32 SendInput).
  3. FL fires `ComposeWithLLM.pyscript` which drains the request queue via
     `flpianoroll` and writes `fLMCP_state.json`.
  4. MCP tool reads state file and returns it.

This works in FL Studio's piano-roll sub-interpreter context (where daemon
threads are prohibited) because the pyscript finishes quickly and doesn't
try to spawn any threads.

Requirements on user side:
  * `ComposeWithLLM.pyscript` must be installed (done by install_windows.ps1).
  * The target pattern's piano roll must be available. Passing `channel` lets
    the controller bridge open it automatically. The host helper also selects
    `ComposeWithLLM` automatically on a fresh FL profile.
"""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from ..file_bridge import stage_and_run


def _bars_to_quarters(bars: float) -> float:
    return bars * 4.0


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def piano_roll_status() -> dict:
        """Report whether the file-based piano-roll bridge is installed/reachable.

        Use this to verify the pyscript is in place before issuing edits.
        """
        from ..file_bridge import PR_DIR, STATE_FILE, is_installed, read_state
        return {
            "installed": is_installed(),
            "pyscript_dir": str(PR_DIR),
            "last_state_file": str(STATE_FILE),
            "last_state": read_state(),
        }

    @mcp.tool()
    def piano_roll_add_notes(notes: list[dict],
                             clear_first: bool = False,
                             channel: int | None = None,
                             pattern: int | None = None) -> dict:
        """Add notes to a channel's piano roll (works WITHOUT MIDI).

        `notes`: list of {midi: int, time_bars: float, duration_bars: float,
                          velocity: 0..1, pan?: -1..1}.

        If `channel` is given and the file/MIDI bridge is online, that channel's
        piano roll is opened automatically. Otherwise, open the intended
        channel's piano roll first; ComposeWithLLM selection is automatic.
        """
        actions: list[dict] = []
        if clear_first:
            actions.append({"action": "clear"})
        pyscript_notes = []
        for n in notes:
            pyscript_notes.append({
                "midi": int(n["midi"]),
                "time": _bars_to_quarters(float(n.get("time_bars", n.get("time", 0)))),
                "duration": _bars_to_quarters(float(n.get("duration_bars", n.get("duration", 1.0)))),
                "velocity": float(n.get("velocity", 0.8)),
                **({"pan": float(n["pan"])} if "pan" in n else {}),
            })
        actions.append({"action": "add_notes", "notes": pyscript_notes})
        return stage_and_run(actions, channel=channel, pattern=pattern)

    @mcp.tool()
    def piano_roll_add_chord(midi_notes: list[int],
                             time_bars: float = 0.0,
                             duration_bars: float = 1.0,
                             velocity: float = 0.8,
                             channel: int | None = None,
                             pattern: int | None = None) -> dict:
        """Add a chord at a given bar position."""
        return stage_and_run([{
            "action": "add_chord",
            "time": _bars_to_quarters(time_bars),
            "duration": _bars_to_quarters(duration_bars),
            "notes": [{"midi": int(m), "velocity": velocity} for m in midi_notes],
        }], channel=channel, pattern=pattern)

    @mcp.tool()
    def piano_roll_add_arpeggio(midi_notes: list[int],
                                time_bars: float = 0.0,
                                step_bars: float = 0.25,
                                note_duration_bars: float = 0.25,
                                velocity: float = 0.8,
                                direction: Literal["up", "down", "updown", "random"] = "up",
                                repeats: int = 1,
                                channel: int | None = None,
                                pattern: int | None = None) -> dict:
        """Arpeggiate a chord into sequential notes."""
        import random
        seq = list(midi_notes)
        if direction == "down":
            seq.reverse()
        elif direction == "updown":
            seq = seq + seq[-2:0:-1]
        elif direction == "random":
            random.shuffle(seq)

        pyscript_notes = []
        total = len(seq) * max(1, int(repeats))
        for i in range(total):
            pyscript_notes.append({
                "midi": int(seq[i % len(seq)]),
                "time": _bars_to_quarters(time_bars + i * step_bars),
                "duration": _bars_to_quarters(note_duration_bars),
                "velocity": velocity,
            })
        return stage_and_run([{"action": "add_notes", "notes": pyscript_notes}],
                             channel=channel, pattern=pattern)

    @mcp.tool()
    def piano_roll_delete_notes(notes: list[dict],
                                channel: int | None = None,
                                pattern: int | None = None) -> dict:
        """Delete notes by {midi, time_bars} match."""
        converted = [{"midi": int(n["midi"]),
                      "time": _bars_to_quarters(float(n["time_bars"]))}
                     for n in notes]
        return stage_and_run([{"action": "delete_notes", "notes": converted}],
                             channel=channel, pattern=pattern)

    @mcp.tool()
    def piano_roll_clear(channel: int | None = None,
                         pattern: int | None = None) -> dict:
        """Remove every note in the (channel's) piano roll."""
        return stage_and_run([{"action": "clear"}], channel=channel, pattern=pattern)

    @mcp.tool()
    def piano_roll_read(channel: int | None = None,
                        pattern: int | None = None) -> dict:
        """Read back the current piano-roll state (returns all notes)."""
        return stage_and_run([{"action": "export_only"}], wait_sec=5.0,
                             channel=channel, pattern=pattern)

    @mcp.tool()
    def piano_roll_quantize(grid_bars: float = 0.25,
                            strength: float = 1.0,
                            channel: int | None = None,
                            pattern: int | None = None) -> dict:
        """Snap existing notes to a grid."""
        return stage_and_run([{
            "action": "quantize",
            "grid": _bars_to_quarters(grid_bars),
            "strength": strength,
        }], channel=channel, pattern=pattern)

    @mcp.tool()
    def piano_roll_transpose(semitones: int,
                             channel: int | None = None,
                             pattern: int | None = None) -> dict:
        """Shift every note by N semitones."""
        return stage_and_run([{"action": "transpose", "semitones": int(semitones)}],
                             channel=channel, pattern=pattern)

    @mcp.tool()
    def piano_roll_humanize(timing_jitter_bars: float = 0.02,
                            velocity_jitter: float = 0.1,
                            channel: int | None = None,
                            pattern: int | None = None) -> dict:
        """Add subtle timing+velocity randomisation."""
        return stage_and_run([{
            "action": "humanize",
            "timing_jitter": _bars_to_quarters(timing_jitter_bars),
            "velocity_jitter": velocity_jitter,
        }], channel=channel, pattern=pattern)

    @mcp.tool()
    def piano_roll_duplicate(source_time_bars: float,
                             length_bars: float,
                             dest_time_bars: float,
                             channel: int | None = None,
                             pattern: int | None = None) -> dict:
        """Copy a time-range of notes to another location."""
        return stage_and_run([{
            "action": "duplicate",
            "source_time": _bars_to_quarters(source_time_bars),
            "length": _bars_to_quarters(length_bars),
            "dest_time": _bars_to_quarters(dest_time_bars),
        }], channel=channel, pattern=pattern)
