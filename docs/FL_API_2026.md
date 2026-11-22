# FL Studio 2025/2026 scripting API — what changed since the 37.0.1 stubs

The official `fl-studio-api-stubs` package stops at API **37** (v37.0.1) and the
IL-Group repo was marked unmaintained on 2026-05-13. This file collects what
newer FL releases added, so bridge handlers can target it. Sources: the FL
online manual (MIDI scripting + piano roll scripting pages), `WhatsNew.rtf` of
FL Studio 2026, the scripting name/docstring tables in `FLEngine_x64.dll`
(2026 vs 2025.2.5) and a diff of the bundled `Shared/Python/Lib/midi.py`.

Run the `meta.introspect` bridge action (`fl_introspect` tool) inside FL to get
the ground truth for *your* build — `general.getVersion()` is the API version.

Bundled Python: **3.12.1** in both FL 2025 and FL 2026. Highest documented API
version: **45** (FL 26.1.4).

## Sandbox (FL 2025+)

Controller scripts run in a sandboxed sub-interpreter:

* `threading` cannot start threads (daemon → `RuntimeError`, non-daemon →
  `start_new_thread returned NULL`);
* `_socket` is unusable, so no TCP/UDP servers;
* `os.remove` / `os.replace` / `os.stat` / `os.mkdir` are unreliable;
* plain `open()` read / write / append works.

This is why fLMCP ≥ 0.4 talks to FL through JSON files polled from `OnIdle`
and `OnMidiMsg` (see [ARCHITECTURE.md](ARCHITECTURE.md)).

## Functions added after the stubs

"–" = the manual does not list it (names/docstrings come from the binary, so
the exact argument list is a best guess — verify with `meta.introspect`).

| function | module | signature | API | FL release / notes |
|---|---|---|---|---|
| getPluginMixLevel | mixer | `(index, plugIndex) -> float` | 37 | 24.2; 0..1 since 24.2.1 |
| setPluginMixLevel | mixer | `(index, plugIndex, value, pickupMode=PIM_None)` | 37 | 24.2 |
| getTrackCount | mixer | `() -> int` | 38 | 2025.1; excludes Master and "Current" |
| getProjectTitle / getProjectAuthor / getProjectGenre | general | `() -> str` | 38 | 2025.1 |
| setCurrentTempo | mixer | `(tempo: float, asInt=0)` | 38 | WhatsNew: 25.2.4 (float tempo) |
| setRecPPQ / setNumerator / setDenominator | general | `(int)` | 38 | WhatsNew: 25.2.4 |
| movePattern | patterns | `(direction=1)` | 39 | 25.2.2; acts on selected patterns |
| setPatternLength | patterns | `(index, length_steps)` | 39 | 26.1 (fixed in b4). **Verified on 26.1.7: the unit is steps (16 = one 4/4 bar)**, matching `getPatternLength`, whose stub docs wrongly say beats |
| incrementPatternLength | patterns | `(index, offset)` | 39 | 26.1; presumably steps like `setPatternLength` (unverified) |
| rerollLoopStarterLoop | channels | `(index, useGlobalIndex=True)` | 39 | -1 = all loops |
| selectTool | playlist | `(index)` | 40 | 25.2.4; `EETool_*` constants (Pencil=0, Brush=1, Del=2, IP=3, Sel=4, Zoom=5, Cut=6, Play=7, Slip=8, Mute=9, Step=10) |
| showPicker | ui | `(mode, category=-1)` | 41 | 26.1; mode 0 plugin picker, 1 project picker |
| getSwing / setSwing | channels | `() -> float` / `(value)` | 42 | 26.1; the manual calls them getShuffle/setShuffle, the binary exports getSwing/setSwing. Verified: Channel rack global swing, no index, clamped to 0..1 |
| clonePattern | patterns | `(index=-1, destIndex=-1)` | 43 | `destIndex` added in 26.1.1. Verified: the copy goes to the next free pattern (or `destIndex`) and becomes the selected pattern |
| clearPattern | patterns | `(index=-1)` | 44 | 26.1.3. An earlier check saw it open a modal Confirm dialog that blocks the script (and FL's UI thread) until the user answers. On 26.1.7 it cleared notes and steps at once, with no dialog |
| duplicatePatternData | patterns | `(index)` | 45 | 26.1.4 |
| getPluginMuteState / setPluginMuteState | mixer | `(index, plugIndex)` / `(index, plugIndex, value)` | – | 2025.2; new `REC_Plug_Mute`. Arity verified; value semantics not yet (empty slots report 1, and setting 0 sticks) |
| releaseCC | device | ? | – | 2025.2; lets automation control a CC again |
| flrMidiNote / flrMidiCC | device | ? | – | 2025.2; FL Studio Remote |
| fillEachNSteps | channels | `(index, interval, …)` | – | 2025.2 |
| rerollLoopStarterSteps / rerollLoopStarterSample | channels | `(index…)` | – | -1 = all |
| setLoopStarterRootNote | patterns | ? | – | |
| selectionSet / selectionClear / selectionIsActive | arrangement | `selectionSet(selStart, …, selectNotes, updateSongLoop, scroll)` | – | 2025.1 |
| OnInitAll | event | `()` | – | 2025.1 |
| setTimelineSelection | flpianoroll | mirrors `getTimelineSelection` | – | 2025.1 |

## New constants (2026 `midi.py`)

* `HW_Dirty_Undo = 131072` (API 42), `HW_Dirty_ChannelRack = 262144`,
  `HW_Dirty_GraphEditor = 524288` (API 44)
* `widPluginPicker = 8`
* `PAD_Count / PAD_Semitone / PAD_Color / PAD_Empty / PAD_Muted` = 0..4 — used
  with `plugins.getPadInfo(index, slotIndex, PAD_*, padIndex)` (FPC)
* already in 2025 but missing from the stubs: `EETool_*`, `SBN_*`,
  `SS_Stop / SS_StartStep / SS_Start`, `pRepeat = 8`, `widPlugin = 5`

## Behaviour changes worth knowing

* The mixer API was reworked for the dynamic mixer (24.2.99).
* `getStepParam` gained note repeat via `pRepeat`.
* DisplayRect durations are capped at 2 s (26.1.5).
* Several crash fixes for scripts calling FL functions "at the wrong time"
  (26.1.3, 26.1.7) — the bridge only ever calls FL from FL's own callbacks.
* **No background tick for this script (26.1.7, verified).** FL never calls
  `OnIdle`. `OnUpdateMeters` doesn't fire either, not even during playback
  with `device.setHasMeters()` called in `OnInit`. The bridge runs only from
  `OnMidiMsg`, which makes the MIDI wake port mandatory.
* **Record prompt (26.1.7, verified).** Arming Record while stopped or playing
  opens the modal "Recording" dialog ("What would you like to record?") unless
  it was turned off. While it is open, `ui.getFocusedFormCaption()` is
  `"Recording"`, `ui.getFocusedFormID()` is -1, `transport.isRecording()` is 1,
  and edits raise "Operation unsafe at current time". `transport.start()`
  closes it and cancels the arm. `record()` followed by `start()` in one
  callback also cancels the arm. Disarming shows no prompt. No API reads or sets
  the recording filter.

## Still not scriptable

Listing or placing playlist clips, multiple arrangements, marker editing
beyond jump/add, mixer routing sends beyond what `mixer.setRouteTo` offers.
