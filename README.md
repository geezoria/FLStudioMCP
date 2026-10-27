# fLMCP — The most powerful FL Studio MCP server

**Model Context Protocol server that gives Claude (or any MCP client) end-to-end
control of FL Studio — transport, patterns, channels, mixer, plugins, piano
roll, playlist, arrangement, automation, rendering, high-level generators for
beats / chords / basslines / arpeggios / melodies, plus voice-to-MIDI humming
and full audio-file analysis (MP3 → FL piano roll).**

Built from scratch to improve on three earlier community attempts
([karl-andres](https://github.com/karl-andres/fl-studio-mcp) ·
[ohhalim](https://github.com/ohhalim/flstudio-mcp) ·
[veenastudio](https://github.com/veenastudio/flstudio-mcp)):

| Feature | karl-andres | ohhalim | veenastudio | **fLMCP** |
| --- | :-: | :-: | :-: | :-: |
| Transport controls | ✅ | ❌ | ◐ | ✅ |
| Tempo write (undoable) | ❌ | ❌ | ✅ | ✅ |
| Time signature | ❌ | ❌ | ❌ | ✅ |
| Patterns (create / rename / clone) | ❌ | ❌ | ❌ | ✅ |
| Full channel rack | ✅ | ❌ | ❌ | ✅ |
| Step sequencer get / set | ✅ | ❌ | ❌ | ✅ |
| Mixer (vol / pan / mute / solo / arm / routes / EQ / FX) | ◐ | ❌ | ❌ | ✅ |
| Plugin params (get / set / search / presets) | ◐ | ❌ | ❌ | ✅ |
| Piano roll (add / edit / clear / read / quantize / transpose / humanize / duplicate) | ◐ | ❌ | ◐ | ✅ |
| Playlist tracks / markers | ❌ | ❌ | ❌ | ✅ |
| Arrangement switching | ❌ | ❌ | ❌ | ✅ |
| Parameter automation via REC events | ❌ | ❌ | ◐ | ✅ |
| Undo / redo / save undo | ❌ | ❌ | ❌ | ✅ |
| MCP resources (`fl://project`, `fl://mixer`, …) | ◐ | ❌ | ❌ | ✅ |
| High-level music generators (beats / chords / arps / basslines / melodies) | ❌ | ❌ | ❌ | ✅ |
| **Voice-to-MIDI** (hum → piano roll) | ❌ | ❌ | ❌ | ✅ |
| **Polyphonic transcription** (chords, via Basic Pitch) | ❌ | ❌ | ❌ | ✅ |
| **Audio-file analysis** (tempo / key / onsets / melody) | ❌ | ❌ | ❌ | ✅ |
| **MP3 → DnB flip** one-shot | ❌ | ❌ | ❌ | ✅ |
| **Dear PyGui voice GUI** | ❌ | ❌ | ❌ | ✅ |
| Transport: atomic JSON + MIDI wake | ❌ (MIDI + JSON-poll) | ❌ (MIDI) | ❌ (MIDI) | ✅ |
| FL 2025 sub-interpreter compatible (no background threads) | ❌ | ❌ | ❌ | ✅ |
| PowerShell installer for Windows | ❌ | ❌ | ❌ | ✅ |
| Tool count | ≈ 60 | ≈ 14 | ≈ 5 | **165** |

## Highlights

- **FL Studio 2025 compatible transport.** FL 2025 runs controller scripts in
  a Python 3.12 sub-interpreter where starting threads fails. fLMCP therefore
  exchanges full JSON payloads through atomic files and uses one reserved
  loopMIDI CC only to wake `OnMidiMsg`; all FL API calls remain on FL's main
  thread and no payload is squeezed through MIDI.
- **Piano-roll editing that actually works.** FL's `flpianoroll` module is
  sandboxed to piano-roll scripts; fLMCP stages edits to a JSON file, then
  fires the companion pyscript via a synthesised `Ctrl+Alt+Y` so the notes
  are inserted on FL's own main thread.
- **High-level music generators.** `gen_emit_chord_progression`,
  `gen_emit_bassline`, `gen_emit_drum_pattern` (boom-bap / trap / DnB /
  reggaeton / house / amen-break / rock …), `gen_emit_arpeggio`,
  `gen_emit_melody` — write full musical ideas with one tool call.
- **Voice-to-MIDI.** Hum a melody, get it in the piano roll. Optional scale
  snap, quantize, transpose, confidence filtering. Standalone Dear PyGui
  window with live waveform + pitch readout for interactive edits. Pass
  `polyphonic=True` to transcribe chords / strums / full-mix content with
  Spotify Basic Pitch instead of the default monophonic engine.
- **Audio-file analysis.** Drop any MP3 / WAV / FLAC / OGG on it — get back
  tempo, detected key, onsets, and (optionally) the dominant melody
  transcribed into FL's piano roll.
- **Live production feedback.** Read per-track left/right mixer peaks, inspect
  channel types and window visibility/focus, and control punch, overdub, loop
  recording and marker jumps without leaving the MCP session.

## Architecture

```
+---------------------+      stdio       +----------------------+
|  Claude / MCP host  | <--------------- |  fl-studio-mcp       |
+---------------------+                  |  Python server       |
                                         +-----------+----------+
                                                     |
                                         atomic request/response JSON
                                         + loopMIDI wake CC 119 / ch. 16
                                                     |
  +------------------------------------  FL Studio 2025  -----------------+
  |                                                                      |
  |  [ Hardware/fLMCP Bridge/device_FLStudioMCP.py ]                      |
  |    OnInit()     -> records the last request id (no threads)            |
  |    OnMidiMsg()  -> reads request, executes FL API, writes response     |
  |    OnIdle()     -> optional polling fallback on versions that emit it  |
  |                                                                      |
  |  [ Piano roll scripts/ComposeWithLLM.pyscript ]                       |
  |    Triggered by Ctrl+Alt+Y (SendInput from MCP server):               |
  |    reads fLMCP_request.json, edits piano roll via flpianoroll,        |
  |    writes fLMCP_state.json back.                                      |
  +-----------------------------------------------------------------------+
```

Deeper dive in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Why files plus a MIDI wake-up

FL Studio 2025's Python 3.12 controller-script sub-interpreter rejects both
daemon and non-daemon threads, so a background TCP listener cannot run there.
The MCP process atomically replaces `mcp_command.json`, then sends CC 119 with
value 1 on MIDI channel 16 to `loopMIDI Port`. FL delivers that byte to
`OnMidiMsg`, which reads the full JSON, executes the existing handler on FL's
main thread, and writes `mcp_response.json`. `OnIdle` remains a fallback for FL
versions that emit it. MIDI carries only the wake-up, never the RPC payload.

### Why a separate piano-roll pyscript

The `flpianoroll` module (the only way to insert / delete notes) is **only**
available inside piano-roll scripts. Device / controller scripts can't
`import` it. fLMCP stages the requested edits into a JSON file, then
synthesises `Ctrl+Alt+Y` in the FL Studio window via Win32 `SendInput`, which
fires the companion pyscript to apply the edits and write the new state back.

## Install (Windows)

Requirements:

- Windows 10 / 11
- FL Studio 2025 (Producer Edition or higher — needs MIDI scripting)
- Python 3.10+ on `PATH` (used once to create the bundled venv)
- [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) with a port
  named `loopMIDI Port`

```powershell
git clone https://github.com/marcostolosa/FLStudioMCP.git fLMCP
cd fLMCP
./scripts/install_windows.ps1
```

What the installer does:

1. Copies `fl_bridge/device_FLStudioMCP.py` to
   `%USERPROFILE%\Documents\Image-Line\FL Studio\Settings\Hardware\fLMCP Bridge\`
2. Copies `fl_bridge/piano_roll/ComposeWithLLM.pyscript` to
   `...\Settings\Piano roll scripts\`
3. Creates `.venv\` and installs the MCP package editable, **with the
   `[audio,gui]` extras** (voice-to-MIDI + audio analysis). Pass `-SkipAudio`
   to install the core only.
4. Adds an `fl-studio-mcp` entry to `%APPDATA%\Claude\claude_desktop_config.json`
   (and to Claude Code's config if present).

### FL Studio-side activation

1. Launch FL Studio 2025.
2. In loopMIDI, create a port named **`loopMIDI Port`**.
3. `Options > MIDI Settings > Input`: select `loopMIDI Port`, set
   **Controller type** = `fLMCP Bridge`, and click *Enable*.
4. Open FL's script output (`Options > MIDI > View script output`) and look
   for `[fLMCP] file+MIDI bridge ready`.
5. No manual piano-roll script selection is required. On the first piano-roll
   call, fLMCP tries `Ctrl+Alt+Y` and, if FL has no active script yet,
   automatically selects and runs **ComposeWithLLM** through the piano-roll
   menu. FL remembers that choice for later calls.
6. Restart Claude Desktop / Claude Code so it picks up the new MCP entry.

### Optional extras

Voice-to-MIDI and audio-file analysis bring in heavier dependencies
(`sounddevice`, `librosa`, `soundfile`, `numpy`, `scipy`, `dearpygui`). The
Windows installer already installs them; on other platforms:

```bash
pip install "fl-studio-mcp[audio]"
```

**Polyphonic transcription** (the `polyphonic=True` flag — chords, strums,
full-mix content via Spotify Basic Pitch) is a separate, heavier extra. Because
`basic-pitch` pins a TensorFlow build that doesn't exist on Python 3.12, install
Basic Pitch with `--no-deps` (it runs on the ONNX runtime — no TensorFlow):

```bash
pip install "fl-studio-mcp[polyphonic]"
pip install basic-pitch --no-deps
```

On Windows, `./scripts/install_windows.ps1 -Polyphonic` does both steps for you.
Without it, the `polyphonic=True` tools return a clear install hint and the
default monophonic engine keeps working.

## Quick check

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

Expected output: project metadata, transport status, the first few channels /
mixer tracks / patterns, and a tempo ramp demonstration.

## Example conversations with Claude

> *"Open FL Studio, create a new pattern called 'Verse Beat', make a boom-bap
> drum groove on channels 0–2, a minor-key bassline in C2 on channel 3, and a
> i–VII–VI–V chord progression on channel 4."*

```python
pattern_create(name="Verse Beat")
gen_emit_drum_pattern(channel_map={"kick": 0, "snare": 1, "clhat": 2},
                     style="boom_bap", repeats=1)
gen_emit_bassline(channel=3, root="C2", scale="minor",
                  progression="i-VII-VI-V", pattern_style="octaves")
gen_emit_chord_progression(channel=4, progression="i-VII-VI-V",
                           root="C4", scale="minor")
transport_play()
```

> *"Take this 90s breakbeat, detect the key and tempo, and flip it into
> a 174 BPM DnB loop — amen-break drums, sub-bass on the detected root,
> and the track's own melody on top."*

```python
song_to_dnb_flip(audio_path="C:/samples/break.wav",
                 target_bpm=174, dnb_style="amen",
                 include_melody=True, include_bass=True)
```

> *"I'll hum a riff for 8 seconds — snap it to C minor, quantize to 1/16
> at 120 BPM, and write it to the open piano roll."*

```python
voice_to_piano_roll(duration_sec=8, bpm=120,
                    scale_root="C", scale="minor",
                    quantize_grid_sec=0.125)
```

Or the interactive route — Claude opens the GUI:

```python
voice_open_gui()   # Dear PyGui window with live waveform + pitch readout
```

## Tool catalogue (165 tools + 7 resources)

Full reference with signatures: [`docs/TOOLS.md`](docs/TOOLS.md).

| Area | Count | Highlights |
| --- | :-: | --- |
| Meta | 4 | ping, reconnect, bridge info, raw escape hatch |
| Transport | 18 | play / stop / record, tempo, punch, overdub, loop record, marker jump |
| Patterns | 13 | create, rename, clone, color, length, find-by-name |
| Channels | 20 | full rack, step sequencer get / set, routing, trigger |
| Mixer | 19 | vol / pan / mute / solo / arm, live peaks, sends, EQ, FX slots, routing |
| Plugins | 13 | get / set / search params, preset navigation, editor |
| Piano roll | 10 | add, read, clear, quantize, transpose, humanize, duplicate |
| Playlist | 14 | tracks, clips (limited), markers |
| Arrangement | 5 | current, list, select, jump marker |
| Automation | 5 | tempo, channel vol / pan, mixer vol, plugin params |
| Project | 11 | metadata, save, save-as, undo / redo, render |
| UI | 8 | show / hide windows, visibility, hints, focus + form caption |
| Generators | 14 | scales, chords, progressions, arpeggios, basslines, drums |
| Voice-to-MIDI | 5 | record + transcribe + scale-snap + send to FL, GUI |
| Audio | 5 | analyze MP3 / WAV, slice at onsets, melody → piano roll, DnB flip |
| MCP resources | 7 | `fl://project`, `fl://mixer`, `fl://patterns`, … |

## Resources Claude can read without a tool call

- `fl://status` — bridge + transport snapshot
- `fl://project` — full project metadata
- `fl://transport` — live transport state
- `fl://channels` — entire channel rack
- `fl://mixer` — all mixer tracks
- `fl://patterns` — every pattern
- `fl://playlist` — playlist tracks, clips, markers

Great for getting oriented ("what does the project look like right now?")
before issuing edits.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `bridge unavailable` from tool calls | FL is not running, the script is not loaded, or the wake port name differs. Enable `loopMIDI Port` with Controller type = `fLMCP Bridge`, or set `FL_STUDIO_MCP_MIDI_PORT` to the actual port name. |
| Updated controller code still behaves like the old version | FL keeps the imported Python module in memory when **Enable** is merely toggled. In MIDI Settings, temporarily change **Controller type** to `(generic controller)`, then select `fLMCP Bridge` again to force a real module reload. |
| `loopMIDI Port` not found | Start loopMIDI and create/rename the port to `loopMIDI Port`; then refresh FL's MIDI device list. |
| Piano-roll edits don't apply | fLMCP focuses FL Studio, tries `Ctrl+Alt+Y`, then automatically selects **ComposeWithLLM** from the piano-roll menu if no state comes back. Check that a piano roll can open and that `ComposeWithLLM.pyscript` is installed. |
| Hotkey fires the wrong script | Open any piano roll → right-click the script dropdown → set `ComposeWithLLM` as the default. |
| `playlist_place_pattern` returns a structured error | FL's public Python API does not yet expose clip placement. Documented in [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md). |
| `voice_*` tools error with `no module named sounddevice` | Install the audio extras: `pip install "fl-studio-mcp[audio]"` (or re-run `install_windows.ps1`). |
| `voice_to_piano_roll` returns 0 notes | Hum louder, check `voice_list_devices()` for the right mic, or lower `min_confidence`. |
| `audio_analyze` can't open MP3 | Install `ffmpeg` and make sure it's on `PATH` (librosa falls back to it for compressed formats). |

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — transport, threading, request lifecycle.
- [`docs/TOOLS.md`](docs/TOOLS.md) — complete tool reference with signatures.
- [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) — what FL's public Python API does / doesn't expose, with workarounds.

## Development

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -e ".[dev]"       # core + pytest (add [audio,gui] for the full stack)
pytest
```

Tests in `tests/` run entirely offline — the file/MIDI bridge is faked, the FL-side
script is imported with its FL-only modules stubbed out, and the core server
builds without the audio extras. You don't need FL Studio (or numpy/librosa)
running to execute the suite.

See [`CHANGELOG.md`](CHANGELOG.md) for what changed between releases.

## Contributing

Issues and PRs welcome at <https://github.com/marcostolosa/FLStudioMCP>. Please
run `pytest` before opening a PR. Bug reports are much easier to act on if
they include the FL build number (`Help > About`) and the snippet of `MIDI
script output` around the failure.

## License

MIT. See [`LICENSE`](LICENSE) if present, or the `license` field in
[`pyproject.toml`](pyproject.toml).
