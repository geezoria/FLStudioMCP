"""Client-selectable workflows for composing and inspecting FL Studio projects."""

from __future__ import annotations

from textwrap import dedent

from mcp.server.mcpserver import MCPServer

_START = (
    "Start with fl_ping. If a required tool is unavailable, use fl_list_toolsets "
    "to identify the tool group to enable. Follow the workflow using the available "
    "tools and report any step you could not verify.\n\n"
)


def register(mcp: MCPServer) -> None:
    @mcp.prompt(title="Make a Beat")
    def make_beat(genre: str, bpm: str, bars: str) -> str:
        """Build and verify a drum groove for a genre, tempo and loop length."""
        return _START + dedent(f"""\
            Make a {genre} beat at {bpm} BPM for {bars} bars.

            1. Read transport_status and call gen_list_drum_patterns. Choose the closest
               style and use channel_all to identify the kick, snare and hats by name.
               Ask for the intended drum channels if their mapping is ambiguous.
            2. If the controller bridge works, use pattern_create for a fresh named
               pattern and pattern_select with its returned index. Set the requested
               tempo with transport_set_tempo if appropriate for the current project.
               Use gen_emit_drum_pattern_step_seq with the actual channel_map, the
               chosen style, one repeat per bar in 4/4, and the new pattern index.
            3. Use channel_get_step_sequence for each written channel and
               transport_status to verify the steps and tempo. If using piano roll
               instead, open the piano roll of an empty drum instrument with a known
               MIDI map (ui_open_piano_roll_for_channel). Use gen_emit_drum_pattern_notes
               with step_bars=0.0625 for 16 steps per bar and clear_first=false.
               Read back with piano_roll_read and verify pitches and loop length.
            4. Report the pattern, style, tempo and verified drum parts. If the
               groove is ready to audition, offer to switch to Pattern mode.
            """)

    @mcp.prompt(title="Write a Chord Progression")
    def write_chord_progression(key: str, mood: str) -> str:
        """Choose, voice and verify a chord progression in the requested key and mood."""
        return _START + dedent(f"""\
            Write a chord progression in {key} with a {mood} mood.

            1. Use gen_list_scales and gen_list_progressions to pick a scale and
               progression that fit. Use a root with an octave, such as C4. If the
               preset voicing suits the idea, use gen_emit_chord_progression. For
               custom voice leading, use gen_list_chord_qualities and gen_chord_notes
               to preview each voicing, then lay out a single piano_roll_add_notes call.
            2. Before writing, use channel_all and pattern_current to resolve the
               intended instrument and pattern where available, and pass them as
               channel and pattern. Read the target with piano_roll_read and use an empty destination or agree on an append
               position. Keep clear_first=false unless the user requests replacement.
            3. Write the selected chords in bars. Read back with piano_roll_read,
               checking actual pitches, starts and durations. Summarize the chords
               and voicings, including the target channel and pattern.
            """)

    @mcp.prompt(title="Hum to Melody")
    def hum_to_melody() -> str:
        """Record a hummed phrase, review its transcription and write it to the piano roll."""
        return _START + dedent("""\
            Turn my hummed phrase into editable piano-roll notes.

            1. Check piano_roll_status and transport_status. Use voice_list_devices
               to confirm the microphone and agree on duration and a start cue.
            2. Once I'm ready, call voice_record_and_transcribe in monophonic mode.
               Review note count, timing and confidence. If the capture is unusable,
               report the issue and ask for another take before editing.
            3. Open the intended piano roll (ui_open_piano_roll_for_channel) and
               check it with piano_roll_read. Use voice_notes_to_piano_roll with the current project BPM,
               the retained notes, and clear_first=false in an empty destination.
               Apply optional scale snapping or quantization only as agreed.
            4. Read piano_roll_read again. Compare note count, pitches and timing
               against the transcription and report the verified result.
            """)

    @mcp.prompt(title="Flip Audio to Drum & Bass")
    def flip_song_to_dnb(path: str) -> str:
        """Analyze an audio file and make a short Drum & Bass sketch in FL Studio."""
        return _START + dedent(f"""\
            Make a four-bar Drum & Bass sketch using this source audio file: `{path}`.

            1. Call audio_analyze on the source for tempo and key. Call
               gen_list_dnb_styles and choose a style that suits the source.
               Use 174 BPM unless I specify another target.
            2. Open a fresh piano roll (ui_open_piano_roll_for_channel on an unused
               pattern). Call piano_roll_read before editing. Call song_to_dnb_flip
               with the source as audio_path, the target BPM, style, dnb_bars=4 and
               clear_first=false. The tool writes drums, bass and melody to a single
               piano roll. Explain the layer layout before treating it as a mix.
            3. Use piano_roll_read to verify note count and timing. Read
               transport_status because the flip tool only uses BPM for timing
               conversion. If I want the whole project at that tempo, call
               transport_set_tempo and verify it with transport_status again.
            4. Report source key and BPM, target tempo, style, and the layers verified.
            """)

    @mcp.prompt(title="Review a Mix")
    def mix_check() -> str:
        """Inspect faders, routing and EQ before proposing any mix changes."""
        return _START + dedent("""\
            Review the current mix. Start with a read-only pass.

            1. Call mixer_all_tracks with include_empty=true and channel_all to
               associate instruments with mixer inserts. Start with Master (track
               0) and the named or routed inserts.
            2. Use mixer_track_info and mixer_get_eq on those tracks. If available,
               use plugin_list_mixer_track to identify loaded effects. Base gain
               staging and EQ suggestions on the observed controls, and ask about
               audible issues where listening evidence is needed.
            3. Present prioritized suggestions with exact tracks and values and get
               my agreement before changing anything. If approved, use
               mixer_set_volume or mixer_set_eq_band as appropriate. Verify the same
               tracks with mixer_track_info or mixer_get_eq after the changes.
            """)

    @mcp.prompt(title="Project Overview")
    def project_overview() -> str:
        """Summarize FL Studio's current project, routing and selection without edits."""
        return _START + dedent("""\
            Give me a read-only overview of the open FL Studio project.

            1. Use project_metadata for the project snapshot, then transport_status
               for playback position, loop mode and live BPM.
            2. Call pattern_list, channel_all and mixer_all_tracks. Correlate
               channel-to-insert routing and identify the selected pattern and
               channel. Read fl://playlist for track and marker availability.
               If tools are filtered, use the corresponding fl:// resources.
            3. Cross-check counts and current selection where the reads overlap.
               Report tempo, project size, active selection, key mixer routes and
               the available playlist details. Flag unavailable fields and any
               state that changed while reading.
            """)
