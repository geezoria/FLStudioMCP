# fLMCP — Tool reference

Every tool is a standard MCP tool callable over JSON-RPC. Parameter names below
match the Python signatures.

## Meta (4)

| Tool | Purpose |
| --- | --- |
| `fl_ping` | Reach the bridge; returns version + uptime |
| `fl_reconnect` | Reset cached status and run a fresh file/MIDI ping |
| `fl_bridge_info` | FL version, script dir, available API modules |
| `fl_call_raw(action, params)` | Escape hatch — invoke any bridge action directly |

## Transport (18)

`transport_play`, `transport_stop`, `transport_record`, `transport_status`,
`transport_set_position(position, unit)`, `transport_song_length`,
`transport_set_loop_mode(mode)`, `transport_set_playback_speed(speed)`,
`transport_set_tempo(bpm)`, `transport_tap_tempo`,
`transport_set_time_signature(numerator, denominator)`,
`transport_toggle_metronome`, `transport_toggle_countdown_before_recording`,
`transport_jog(steps)`, `transport_toggle_punch`,
`transport_toggle_overdub`, `transport_toggle_loop_recording`,
`transport_jump_marker(direction)`

## Patterns (13)

`pattern_count`, `pattern_current`, `pattern_list`, `pattern_select(index)`,
`pattern_create(name)`, `pattern_rename(index, name)`,
`pattern_set_color(index, color)`, `pattern_clone(index, new_name)`,
`pattern_delete(index)`, `pattern_set_length(index, bars)`,
`pattern_find_by_name(name)`, `pattern_jump_to_next`, `pattern_jump_to_previous`

## Channels (20)

`channel_count`, `channel_info(index)`, `channel_all`, `channel_selected`,
`channel_select(index, exclusive)`, `channel_set_volume(index, volume)`,
`channel_set_pan(index, pan)`, `channel_set_pitch(index, semitones)`,
`channel_mute(index, muted)`, `channel_solo(index, solo)`,
`channel_set_name(index, name)`, `channel_set_color(index, color)`,
`channel_route_to_mixer(index, mixer_track)`,
`channel_trigger_note(index, note, velocity, duration_ms)`,
`channel_get_grid_bit(index, position)`, `channel_set_grid_bit(index, position, value)`,
`channel_get_step_sequence(index, pattern)`, `channel_set_step_sequence(index, steps, pattern)`,
`channel_clear_step_sequence(index, pattern)`, `channel_quick_quantize(index)`

## Mixer (19)

`mixer_count`, `mixer_track_info(track)`, `mixer_get_peaks(track)`,
`mixer_all_tracks(include_empty)`,
`mixer_set_volume(track, volume)`, `mixer_set_pan(track, pan)`,
`mixer_mute(track, muted)`, `mixer_solo(track, solo)`, `mixer_arm(track, armed)`,
`mixer_set_name(track, name)`, `mixer_set_color(track, color)`,
`mixer_set_stereo_separation(track, separation)`,
`mixer_set_send_level(src_track, dst_track, level)`,
`mixer_route(src_track, dst_track, enabled)`, `mixer_fx_slots(track)`,
`mixer_select(track)`, `mixer_get_eq(track)`,
`mixer_set_eq_band(track, band, gain, frequency)`,
`mixer_link_to_channel(channel, track, mode)`

## Plugins (13)

`plugin_is_valid(index, slot, location)`, `plugin_name(...)`,
`plugin_param_count(...)`, `plugin_params(...)`,
`plugin_get_param(index, param, slot, location)`,
`plugin_set_param(index, param, value, slot, location)`,
`plugin_find_param(index, name_contains, slot, location)`,
`plugin_preset_count(...)`, `plugin_next_preset(...)`, `plugin_prev_preset(...)`,
`plugin_set_preset(index, preset, slot, location)`,
`plugin_show_editor(...)`, `plugin_list_mixer_track(track)`

## Piano roll (10)

`piano_roll_add_notes(notes, clear_first, channel?, pattern?)` — notes are
dicts `{midi, time_bars, duration_bars, velocity (0..1), pan?}`. If `channel`
is given and the controller bridge is online, that channel's piano roll is opened
automatically before the edit (so the notes route to the right channel);
otherwise the edit targets whatever piano roll is currently open. All the
piano-roll tools below accept the same optional `channel` / `pattern`.
`piano_roll_add_chord(channel, midi_notes, time_bars, duration_bars, velocity, pattern)`
`piano_roll_add_arpeggio(channel, midi_notes, time_bars, step_bars, note_duration_bars, velocity, direction, repeats, pattern)`
`piano_roll_delete_notes(channel, notes, pattern)`
`piano_roll_clear(channel, pattern)`
`piano_roll_read(channel, pattern)`
`piano_roll_quantize(channel, grid_bars, strength, pattern)`
`piano_roll_transpose(channel, semitones, pattern)`
`piano_roll_humanize(channel, timing_jitter_bars, velocity_jitter, pattern)`
`piano_roll_duplicate(channel, source_time_bars, length_bars, dest_time_bars, pattern)`

## Playlist (14)

`playlist_track_count`, `playlist_track_info(track)`,
`playlist_all_tracks(include_empty)`,
`playlist_set_track_name(track, name)`, `playlist_set_track_color(track, color)`,
`playlist_mute_track(track, muted)`, `playlist_solo_track(track, solo)`,
`playlist_list_clips(track)`,
`playlist_place_pattern(track, pattern, position_bars, length_bars)`  ⚠ limited (see LIMITATIONS.md),
`playlist_delete_clip(track, position_bars)`  ⚠ limited,
`playlist_refresh`, `playlist_list_markers`,
`playlist_add_marker(position_bars, name)`, `playlist_delete_marker(index)`

## Arrangement (5)

`arrangement_current`, `arrangement_list`, `arrangement_select(index)`,
`arrangement_jump_marker(direction)`, `arrangement_play_time`

## Automation (5)

All accept `points = [{time_bars, value}]` (tempo uses `bpm` instead of `value`)
and record the automation live. Recording is **asynchronous / non-blocking**:
the tool returns immediately (`{"mode": "async", "scheduled": N}`) and the clip
finishes a few seconds later without freezing FL's UI.

`automation_record_tempo(points)`, `automation_record_channel_volume(channel, points)`,
`automation_record_channel_pan(channel, points)`,
`automation_record_mixer_volume(track, points)`,
`automation_record_plugin_param(channel, param, points, slot, location)`

## Project (11)

`project_metadata`, `project_new(template)`, `project_open(path)`,
`project_save`, `project_save_as(path)`, `project_undo`, `project_redo`,
`project_undo_history`, `project_save_undo(name, flags)`,
`project_render(path, format, mode)`, `project_version`

## UI (8)

`ui_focused_window`, `ui_window_status(name)`,
`ui_show_window(name, focus)`, `ui_hide_window(name)`,
`ui_hint(message)`,
`ui_open_piano_roll_for_channel(channel, pattern)`,
`ui_selected_channel`, `ui_scroll_to_channel(channel)`

## Generators (high-level, ≈ 14)

`gen_list_scales`, `gen_list_chord_qualities`, `gen_list_progressions`, `gen_list_drum_patterns`
`gen_chord_notes(root, quality, inversion)`, `gen_scale_notes(root, scale, octaves)`
`gen_emit_chord_progression(channel, progression, root, scale, chord_length_bars, octave_shift, pattern, clear_first)`
`gen_emit_melody(channel, root, scale, length_bars, note_duration_bars, octave_range, seed, pattern, clear_first)`
`gen_emit_bassline(channel, progression, root, scale, bar_length, pattern_style, pattern, clear_first)`
`gen_emit_drum_pattern(channel_map, style, repeats, pattern, clear_first)` —
`channel_map` = `{"kick": 0, "snare": 1, "clhat": 2, ...}`, `style` = built-in
(four_on_floor / boom_bap / trap / drum_and_bass / reggaeton / house /
amen_break / rock)
`gen_emit_arpeggio(channel, root, quality, direction, step_bars, length_bars, velocity, octaves, pattern, clear_first)`

## Voice-to-MIDI (5)

All transcription tools take `polyphonic: bool = False`. Default is the
lightweight monophonic pyin engine (one note at a time — humming). Set
`polyphonic=True` to use Spotify Basic Pitch (chords / strums / full-mix
content, overlapping notes preserved) — requires the `polyphonic` extra; tools
return a structured install hint if it's missing.

`voice_open_gui` — launch the Dear PyGui window (live waveform + pitch, send to FL)
`voice_list_devices` — enumerate microphones
`voice_record_and_transcribe(duration_sec, device, min_note_sec, fmin_hz, fmax_hz, polyphonic)` — record + transcribe, returns raw notes + wav path
`voice_transcribe_file(audio_path, min_note_sec, fmin_hz, fmax_hz, polyphonic)` — transcribe an existing audio file
`voice_to_piano_roll(duration_sec, bpm, device, scale_root, scale, transpose_semitones, quantize_grid_sec, min_confidence, min_note_sec, polyphonic, clear_first)` — one-shot: capture + transcribe + scale-snap + quantize + write into the piano roll
`voice_notes_to_piano_roll(notes, bpm, scale_root, scale, transpose_semitones, quantize_grid_sec, clear_first)` — push a pre-transcribed note list (from `voice_record_and_transcribe`) with optional scale-snap / transpose / quantize. Pure-Python: does **not** require the audio extras.

## Audio analysis + DnB (5)

`audio_analyze(path, extract_melody, polyphonic)` — tempo, key, onsets, loudness, optional transcription (mono melody, or polyphonic notes with `polyphonic=True`); works on WAV / FLAC / MP3 / OGG / AIFF
`audio_slice(path, output_dir, max_slices)` — chop audio at onsets into individual WAVs (drag straight into the channel rack)
`audio_melody_to_piano_roll(path, bpm, snap_to_detected_key, transpose_semitones, min_confidence, polyphonic, clear_first)` — extract notes (dominant melody, or polyphonic chords with `polyphonic=True`) and push into FL
`gen_list_dnb_styles` — list DnB drum presets (`amen`, `think`, `modern`, `halftime`)
`gen_emit_dnb_groove(style, repeats, clear_first)` — emit a 2-bar DnB drum pattern (MIDI: 36 kick, 38 snare, 42 clhat, 46 ophat, 51 ride)
`song_to_dnb_flip(audio_path, target_bpm, dnb_style, dnb_bars, include_melody, include_bass, polyphonic, clear_first)` — one-shot: MP3 → DnB drum loop + sub-bass on detected root + quantized melody (`polyphonic=True` keeps chords)

## MCP resources (7)

- `fl://status` — bridge + transport snapshot
- `fl://project` — full project metadata
- `fl://transport` — live transport state
- `fl://channels` — entire channel rack
- `fl://mixer` — all mixer tracks
- `fl://patterns` — every pattern
- `fl://playlist` — playlist tracks, clips, markers

Claude can read these any time without a tool call — great for getting
oriented ("what does the project currently look like?") before issuing edits.
