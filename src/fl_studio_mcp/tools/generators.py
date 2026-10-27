"""High-level compositional helpers: chord progressions, drum grooves, basslines, melodies, scales.

These are pure-Python (no bridge call) until the final emit, where they translate into
piano_roll / step sequencer operations via the bridge. Gives Claude a one-shot way to
drop in a full beat or progression without reasoning about MIDI numbers.
"""

from __future__ import annotations

import random
from typing import Literal

from mcp.server.fastmcp import FastMCP

from ..bridge_client import RPCError, get_client
from ..file_bridge import stage_and_run


def _emit_notes_to_piano_roll(notes_bars: list[dict], clear_first: bool = True,
                              channel: int | None = None,
                              pattern: int | None = None) -> dict:
    """Route notes (in bars) to the piano roll via the file bridge (no MIDI).

    When `channel` is given and the file/MIDI bridge is online, the target channel's
    piano roll is opened first so the notes land on it.
    """
    actions: list[dict] = []
    if clear_first:
        actions.append({"action": "clear"})
    qn = []
    for n in notes_bars:
        qn.append({
            "midi": int(n["midi"]),
            "time": float(n.get("time", n.get("time_bars", 0))) * 4,
            "duration": float(n.get("duration", n.get("duration_bars", 1.0))) * 4,
            "velocity": float(n.get("velocity", 0.8)),
        })
    actions.append({"action": "add_notes", "notes": qn})
    return stage_and_run(actions, channel=channel, pattern=pattern)

# ---------- music theory primitives ------------------------------------------

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

SCALES: dict[str, list[int]] = {
    "major":          [0, 2, 4, 5, 7, 9, 11],
    "minor":          [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor":  [0, 2, 3, 5, 7, 9, 11],
    "dorian":         [0, 2, 3, 5, 7, 9, 10],
    "phrygian":       [0, 1, 3, 5, 7, 8, 10],
    "lydian":         [0, 2, 4, 6, 7, 9, 11],
    "mixolydian":     [0, 2, 4, 5, 7, 9, 10],
    "locrian":        [0, 1, 3, 5, 6, 8, 10],
    "pentatonic":     [0, 2, 4, 7, 9],
    "minor_pent":     [0, 3, 5, 7, 10],
    "blues":          [0, 3, 5, 6, 7, 10],
    "chromatic":      list(range(12)),
}

# chord quality -> semitone intervals from root
CHORDS: dict[str, list[int]] = {
    "maj":   [0, 4, 7],
    "min":   [0, 3, 7],
    "dim":   [0, 3, 6],
    "aug":   [0, 4, 8],
    "sus2":  [0, 2, 7],
    "sus4":  [0, 5, 7],
    "maj7":  [0, 4, 7, 11],
    "min7":  [0, 3, 7, 10],
    "7":     [0, 4, 7, 10],
    "min9":  [0, 3, 7, 10, 14],
    "maj9":  [0, 4, 7, 11, 14],
    "add9":  [0, 4, 7, 14],
    "6":     [0, 4, 7, 9],
    "min6":  [0, 3, 7, 9],
}

# Roman-numeral progressions in a diatonic scale (0-indexed scale-degree + chord quality)
COMMON_PROGRESSIONS: dict[str, list[tuple[int, str]]] = {
    "I-V-vi-IV":       [(0, "maj"), (4, "maj"), (5, "min"), (3, "maj")],
    "vi-IV-I-V":       [(5, "min"), (3, "maj"), (0, "maj"), (4, "maj")],
    "ii-V-I":          [(1, "min7"), (4, "7"),  (0, "maj7")],
    "I-vi-IV-V":       [(0, "maj"), (5, "min"), (3, "maj"), (4, "maj")],
    "I-IV-V":          [(0, "maj"), (3, "maj"), (4, "maj")],
    "i-VI-III-VII":    [(0, "min"), (5, "maj"), (2, "maj"), (6, "maj")],
    "i-iv-v":          [(0, "min"), (3, "min"), (4, "min")],
    "i-VII-VI-V":      [(0, "min"), (6, "maj"), (5, "maj"), (4, "maj")],
    "12-bar-blues":    [(0, "7")] * 4 + [(3, "7")] * 2 + [(0, "7")] * 2 + [(4, "7"), (3, "7"), (0, "7"), (4, "7")],
}


def _parse_root(root: str) -> int:
    """Convert 'C4' or 'F#3' to MIDI number."""
    root = root.strip()
    if len(root) >= 2 and root[1] == "#":
        name, octave = root[:2], root[2:]
    elif len(root) >= 2 and root[1] == "b":
        name, octave = root[:2].replace("Db", "C#").replace("Eb", "D#").replace("Gb", "F#").replace("Ab", "G#").replace("Bb", "A#"), root[2:]
    else:
        name, octave = root[:1], root[1:]
    if name not in NOTE_NAMES:
        raise ValueError(f"bad root note: {root}")
    return NOTE_NAMES.index(name) + 12 * (int(octave) + 1)


def _chord_midi(root_midi: int, quality: str, inversion: int = 0) -> list[int]:
    iv = CHORDS[quality][:]
    for _ in range(inversion):
        iv.append(iv.pop(0) + 12)
    return [root_midi + x for x in iv]


# ---------- drum grooves (GM MIDI drum map, 9-channel-agnostic values) -------

# Standard FL Studio drum kit channel positions (0-based). Users can remap.
KICK, SNARE, CLHAT, OPHAT, CLAP, TOM, RIDE = 0, 1, 2, 3, 4, 5, 6

DRUM_PATTERNS: dict[str, dict[int, list[int]]] = {
    # 16-step grids
    "four_on_floor": {
        KICK:  [1,0,0,0, 1,0,0,0, 1,0,0,0, 1,0,0,0],
        SNARE: [0,0,0,0, 1,0,0,0, 0,0,0,0, 1,0,0,0],
        CLHAT: [0,0,1,0, 0,0,1,0, 0,0,1,0, 0,0,1,0],
    },
    "boom_bap": {
        KICK:  [1,0,0,0, 0,0,1,0, 0,0,1,0, 0,0,0,0],
        SNARE: [0,0,0,0, 1,0,0,0, 0,0,0,0, 1,0,0,0],
        CLHAT: [1,0,1,0, 1,0,1,0, 1,0,1,0, 1,0,1,0],
    },
    "trap": {
        KICK:  [1,0,0,0, 0,0,1,0, 0,0,0,0, 0,1,0,0],
        SNARE: [0,0,0,0, 1,0,0,0, 0,0,0,0, 1,0,0,0],
        CLHAT: [1,1,0,1, 1,0,1,1, 1,1,0,1, 1,1,1,1],
    },
    "drum_and_bass": {
        KICK:  [1,0,0,0, 0,0,0,0, 0,0,1,0, 0,0,0,0],
        SNARE: [0,0,0,0, 1,0,0,0, 0,0,0,0, 1,0,0,0],
        CLHAT: [1,0,1,0, 1,0,1,0, 1,0,1,0, 1,0,1,0],
    },
    "reggaeton": {
        KICK:  [1,0,0,0, 0,0,1,0, 1,0,0,0, 0,0,1,0],
        SNARE: [0,0,0,0, 1,0,0,0, 0,0,1,0, 1,0,0,0],
        CLHAT: [0,0,1,0, 0,0,1,0, 0,0,1,0, 0,0,1,0],
    },
    "house": {
        KICK:  [1,0,0,0, 1,0,0,0, 1,0,0,0, 1,0,0,0],
        SNARE: [0,0,0,0, 1,0,0,0, 0,0,0,0, 1,0,0,0],
        CLHAT: [0,0,1,0, 0,0,1,0, 0,0,1,0, 0,0,1,0],
        OPHAT: [0,0,0,0, 0,0,0,1, 0,0,0,0, 0,0,0,1],
    },
    "amen_break": {
        KICK:  [1,0,0,0, 0,0,1,0, 0,0,1,0, 0,0,0,0],
        SNARE: [0,0,0,0, 1,0,0,1, 0,1,0,0, 1,0,0,0],
        CLHAT: [1,0,1,0, 1,0,1,0, 1,0,1,0, 1,0,1,0],
        RIDE:  [0,0,0,1, 0,0,0,0, 0,0,0,1, 0,0,0,0],
    },
    "rock": {
        KICK:  [1,0,0,0, 0,0,0,0, 1,0,0,0, 0,0,0,0],
        SNARE: [0,0,0,0, 1,0,0,0, 0,0,0,0, 1,0,0,0],
        CLHAT: [1,0,1,0, 1,0,1,0, 1,0,1,0, 1,0,1,0],
    },
}


# ---------- registration -----------------------------------------------------

def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def gen_list_scales() -> dict:
        """Return all supported scale names."""
        return {"scales": list(SCALES.keys())}

    @mcp.tool()
    def gen_list_chord_qualities() -> dict:
        """Return all supported chord quality names."""
        return {"qualities": list(CHORDS.keys())}

    @mcp.tool()
    def gen_list_progressions() -> dict:
        """Return all built-in Roman-numeral progressions."""
        return {"progressions": list(COMMON_PROGRESSIONS.keys())}

    @mcp.tool()
    def gen_list_drum_patterns() -> dict:
        """Return all built-in drum patterns."""
        return {"patterns": list(DRUM_PATTERNS.keys())}

    @mcp.tool()
    def gen_chord_notes(root: str = "C4",
                       quality: str = "maj",
                       inversion: int = 0) -> dict:
        """Return MIDI notes for a single chord (no FL write)."""
        root_midi = _parse_root(root)
        return {"notes": _chord_midi(root_midi, quality, inversion)}

    @mcp.tool()
    def gen_scale_notes(root: str = "C4",
                       scale: str = "minor",
                       octaves: int = 1) -> dict:
        """Return MIDI notes for a scale run."""
        root_midi = _parse_root(root)
        iv = SCALES[scale]
        notes: list[int] = []
        for o in range(octaves):
            for s in iv:
                notes.append(root_midi + s + 12 * o)
        notes.append(root_midi + 12 * octaves)
        return {"notes": notes}

    @mcp.tool()
    def gen_emit_chord_progression(channel: int,
                                  progression: str = "I-V-vi-IV",
                                  root: str = "C4",
                                  scale: str = "major",
                                  chord_length_bars: float = 1.0,
                                  octave_shift: int = 0,
                                  pattern: int | None = None,
                                  clear_first: bool = True) -> dict:
        """Write a chord progression to a channel's piano roll."""
        if progression not in COMMON_PROGRESSIONS:
            raise ValueError(f"unknown progression: {progression}; see gen_list_progressions")
        if scale not in SCALES:
            raise ValueError(f"unknown scale: {scale}")
        scale_deg = SCALES[scale]
        root_midi = _parse_root(root) + 12 * octave_shift

        notes: list[dict] = []
        t = 0.0
        for degree, quality in COMMON_PROGRESSIONS[progression]:
            degree_offset = scale_deg[degree % len(scale_deg)] + 12 * (degree // len(scale_deg))
            chord_root = root_midi + degree_offset
            for n in _chord_midi(chord_root, quality):
                notes.append({"midi": n, "time": t, "duration": chord_length_bars, "velocity": 0.78})
            t += chord_length_bars

        return _emit_notes_to_piano_roll(notes, clear_first=clear_first,
                                         channel=channel, pattern=pattern)

    @mcp.tool()
    def gen_emit_melody(channel: int,
                        root: str = "C4",
                        scale: str = "minor",
                        length_bars: float = 4.0,
                        note_duration_bars: float = 0.25,
                        octave_range: int = 2,
                        seed: int | None = None,
                        pattern: int | None = None,
                        clear_first: bool = True) -> dict:
        """Generate a random melody that stays within the given scale and write it to the piano roll."""
        if scale not in SCALES:
            raise ValueError(f"unknown scale: {scale}")
        rnd = random.Random(seed)
        root_midi = _parse_root(root)
        scale_deg = SCALES[scale]
        pool = [root_midi + s + 12 * o for o in range(octave_range) for s in scale_deg]
        total_notes = max(1, int(length_bars / note_duration_bars))
        notes: list[dict] = []
        prev = rnd.choice(pool)
        for i in range(total_notes):
            # bias toward small steps
            candidates = sorted(pool, key=lambda p: abs(p - prev))[: max(5, len(pool) // 3)]
            prev = rnd.choice(candidates)
            notes.append({
                "midi": prev,
                "time": i * note_duration_bars,
                "duration": note_duration_bars * rnd.choice([0.8, 1.0, 1.0, 1.2]),
                "velocity": round(rnd.uniform(0.55, 0.95), 2),
            })
        return _emit_notes_to_piano_roll(notes, clear_first=clear_first,
                                         channel=channel, pattern=pattern)

    @mcp.tool()
    def gen_emit_bassline(channel: int,
                          progression: str = "i-VII-VI-V",
                          root: str = "C2",
                          scale: str = "minor",
                          bar_length: float = 1.0,
                          pattern_style: Literal["root", "octaves", "walking", "eighths"] = "octaves",
                          pattern: int | None = None,
                          clear_first: bool = True) -> dict:
        """Create a bassline that follows a chord progression root (various styles)."""
        if progression not in COMMON_PROGRESSIONS:
            raise ValueError(f"unknown progression: {progression}")
        scale_deg = SCALES[scale]
        root_midi = _parse_root(root)
        notes: list[dict] = []
        t = 0.0
        for degree, _ in COMMON_PROGRESSIONS[progression]:
            degree_offset = scale_deg[degree % len(scale_deg)] + 12 * (degree // len(scale_deg))
            r = root_midi + degree_offset
            if pattern_style == "root":
                notes.append({"midi": r, "time": t, "duration": bar_length, "velocity": 0.85})
            elif pattern_style == "octaves":
                for i in range(2):
                    notes.append({"midi": r + (12 if i else 0), "time": t + i * bar_length / 2,
                                  "duration": bar_length / 2, "velocity": 0.8})
            elif pattern_style == "walking":
                for i, s in enumerate([0, 2, 4, 5]):
                    notes.append({"midi": r + s, "time": t + i * bar_length / 4,
                                  "duration": bar_length / 4, "velocity": 0.78})
            elif pattern_style == "eighths":
                for i in range(8):
                    notes.append({"midi": r, "time": t + i * bar_length / 8,
                                  "duration": bar_length / 8, "velocity": 0.72 + 0.2 * (i % 2)})
            t += bar_length
        return _emit_notes_to_piano_roll(notes, clear_first=clear_first,
                                         channel=channel, pattern=pattern)

    @mcp.tool()
    def gen_emit_drum_pattern_notes(style: str = "boom_bap",
                                    midi_map: dict[str, int] | None = None,
                                    step_bars: float = 0.25,
                                    repeats: int = 1,
                                    clear_first: bool = True) -> dict:
        """Emit a drum groove to the currently-open piano roll as individual notes.

        Use this when you don't have a MIDI bridge (no step sequencer access).
        Open the drum channel's piano roll first.

        `midi_map`: which MIDI note number represents each drum voice.
            Default: kick=36, snare=38, clhat=42, ophat=46, clap=39, tom=45, ride=51.
        `style`: one of gen_list_drum_patterns names.
        """
        if style not in DRUM_PATTERNS:
            raise ValueError(f"unknown drum style: {style}; see gen_list_drum_patterns")
        default_map = {"kick": 36, "snare": 38, "clhat": 42, "ophat": 46,
                       "clap": 39, "tom": 45, "ride": 51}
        mm = {**default_map, **(midi_map or {})}
        sym = {"kick": KICK, "snare": SNARE, "clhat": CLHAT,
               "ophat": OPHAT, "clap": CLAP, "tom": TOM, "ride": RIDE}
        notes: list[dict] = []
        rep = max(1, int(repeats))
        for drum, pos_code in sym.items():
            hits = DRUM_PATTERNS[style].get(pos_code)
            if not hits:
                continue
            midi_num = mm.get(drum)
            if midi_num is None:
                continue
            for r in range(rep):
                for step_idx, bit in enumerate(hits):
                    if not bit:
                        continue
                    t_bars = (r * len(hits) + step_idx) * step_bars
                    notes.append({
                        "midi": midi_num,
                        "time": t_bars,
                        "duration": step_bars * 0.95,
                        "velocity": 0.9 if drum == "kick" else 0.78,
                    })
        return _emit_notes_to_piano_roll(notes, clear_first=clear_first)

    @mcp.tool()
    def gen_emit_drum_pattern_step_seq(channel_map: dict[str, int],
                                       style: str = "boom_bap",
                                       repeats: int = 1,
                                       pattern: int | None = None) -> dict:
        """Write a drum groove into the step sequencer (requires MIDI bridge).

        Use this when you DO have a MIDI bridge. `channel_map`: map each of
        {"kick","snare","clhat","ophat","clap","tom","ride"} to a channel index.
        """
        if style not in DRUM_PATTERNS:
            raise ValueError(f"unknown drum style: {style}; see gen_list_drum_patterns")
        sym = {"kick": KICK, "snare": SNARE, "clhat": CLHAT,
               "ophat": OPHAT, "clap": CLAP, "tom": TOM, "ride": RIDE}
        try:
            bridge = get_client()
            placed = 0
            for drum_name, channel in channel_map.items():
                dk = drum_name.lower()
                if dk not in sym:
                    continue
                hits = DRUM_PATTERNS[style].get(sym[dk])
                if not hits:
                    continue
                seq = hits * max(1, repeats)
                bridge.call("channels.setStepSequence", index=channel, steps=seq, pattern=pattern)
                placed += sum(seq)
            return {"placed_hits": placed, "style": style, "repeats": repeats, "via": "midi_bridge"}
        except RPCError as e:
            return {"ok": False, "error": str(e),
                    "hint": "MIDI bridge unavailable. Use gen_emit_drum_pattern_notes instead "
                            "(writes individual notes into the piano roll; no MIDI required)."}

    @mcp.tool()
    def gen_emit_arpeggio(channel: int,
                          root: str = "C4",
                          quality: str = "min",
                          direction: Literal["up", "down", "updown", "random"] = "up",
                          step_bars: float = 0.25,
                          length_bars: float = 4.0,
                          velocity: float = 0.8,
                          octaves: int = 1,
                          pattern: int | None = None,
                          clear_first: bool = True) -> dict:
        """Emit an arpeggio across N octaves for a given chord."""
        root_midi = _parse_root(root)
        chord = _chord_midi(root_midi, quality)
        notes_pool = []
        for o in range(octaves):
            for n in chord:
                notes_pool.append(n + 12 * o)
        if direction == "down":
            notes_pool.reverse()
        elif direction == "updown":
            notes_pool = notes_pool + notes_pool[-2:0:-1]
        elif direction == "random":
            random.shuffle(notes_pool)

        total_notes = max(1, int(length_bars / step_bars))
        notes: list[dict] = []
        for i in range(total_notes):
            notes.append({
                "midi": notes_pool[i % len(notes_pool)],
                "time": i * step_bars,
                "duration": step_bars,
                "velocity": velocity,
            })

        return _emit_notes_to_piano_roll(notes, clear_first=clear_first,
                                         channel=channel, pattern=pattern)
