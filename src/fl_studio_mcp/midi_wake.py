"""Optional Windows MIDI wake signal for the file transport."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

WAKE_MESSAGE = 0x01_77_BF  # CC 119, value 1, channel 16


def find_wake_sender() -> Callable[[], bool] | None:
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    class MidiOutCaps(ctypes.Structure):
        _fields_ = [
            ("wMid", wintypes.WORD),
            ("wPid", wintypes.WORD),
            ("vDriverVersion", wintypes.UINT),
            ("szPname", wintypes.WCHAR * 32),
            ("wTechnology", wintypes.WORD),
            ("wVoices", wintypes.WORD),
            ("wNotes", wintypes.WORD),
            ("wChannelMask", wintypes.WORD),
            ("dwSupport", wintypes.DWORD),
        ]

    try:
        winmm = ctypes.WinDLL("winmm")
    except OSError:
        return None

    winmm.midiOutGetNumDevs.argtypes = []
    winmm.midiOutGetNumDevs.restype = wintypes.UINT
    winmm.midiOutGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(MidiOutCaps), wintypes.UINT]
    winmm.midiOutOpen.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t,
        wintypes.DWORD,
    ]
    winmm.midiOutShortMsg.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    winmm.midiOutClose.argtypes = [wintypes.HANDLE]

    outputs = []
    for output_id in range(winmm.midiOutGetNumDevs()):
        caps = MidiOutCaps()
        if winmm.midiOutGetDevCapsW(output_id, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            outputs.append((output_id, caps.szPname.casefold()))
    configured = os.environ.get("FLMCP_MIDI_PORT", "").casefold()
    if configured:
        selected = next((output_id for output_id, name in outputs if name == configured), None)
    else:
        selected = next(
            (output_id for marker in ("flmcp", "loopmidi") for output_id, name in outputs
             if marker in name),
            None,
        )
    if selected is None:
        return None

    def send() -> bool:
        handle = wintypes.HANDLE()
        if winmm.midiOutOpen(ctypes.byref(handle), selected, 0, 0, 0) != 0:
            return False
        try:
            return winmm.midiOutShortMsg(handle, WAKE_MESSAGE) == 0
        finally:
            winmm.midiOutClose(handle)

    return send
