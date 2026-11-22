"""Find and answer FL Studio's modal dialogs (Windows only).

FL's Python API can't see or close modal dialogs, yet while one is open every
edit fails with "Operation unsafe at current time". The one the MCP itself
triggers is the record prompt: arming Record asks "What would you like to
record?" unless the user ticked "Don't show this in the future". This module
lets the automation tools answer it and lets errors name the dialog in the way.

Everything here is best effort and returns empty results on other platforms.
"""

from __future__ import annotations

import logging
import sys
import time

log = logging.getLogger("fl_studio_mcp.fl_windows")

MAIN_FORM_CLASS = "TFruityLoopsMainForm"
DIALOG_CLASS = "TMsgForm"
BUTTON_CLASS = "TQuickFocusBtn"
RECORD_PROMPT_TITLE = "Recording"
# Buttons of the record prompt, top to bottom (FL 2024-2026). The buttons carry
# no window text, so the order is how they are told apart.
RECORD_PROMPT_CHOICES = (
    "Audio, into the Edison audio editor/recorder",
    "Audio, into the playlist as an audio clip",
    "Notes and automation",
    "Everything",
    "Show me some more help",
)
NOTES_AND_AUTOMATION = 2

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001


def _user32():
    if sys.platform != "win32":
        return None
    import ctypes

    return ctypes.windll.user32


def _windows(user32, parent=None) -> list[tuple[int, str, str, int]]:
    """(hwnd, class, title, pid) of visible top-level windows, or of `parent`'s children."""
    import ctypes
    from ctypes import wintypes

    found: list[tuple[int, str, str, int]] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _):
        if parent is None and not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(128)
        title = ctypes.create_unicode_buffer(256)
        pid = wintypes.DWORD()
        user32.GetClassNameW(hwnd, cls, 128)
        user32.GetWindowTextW(hwnd, title, 256)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((int(hwnd or 0), cls.value, title.value, pid.value))
        return True

    callback = proto(collect)
    if parent is None:
        user32.EnumWindows(callback, 0)
    else:
        user32.EnumChildWindows(parent, callback, 0)
    return found


def _fl_dialogs(user32) -> list[tuple[int, str]]:
    windows = _windows(user32)
    fl_pids = {pid for _, cls, _, pid in windows if cls == MAIN_FORM_CLASS}
    return [(hwnd, title) for hwnd, cls, title, pid in windows
            if cls == DIALOG_CLASS and pid in fl_pids]


def open_dialogs() -> list[str]:
    """Titles of the modal dialogs FL Studio is showing right now."""
    user32 = _user32()
    if user32 is None:
        return []
    try:
        return [title for _, title in _fl_dialogs(user32)]
    except Exception:
        log.debug("could not list FL dialogs", exc_info=True)
        return []


def _click(user32, hwnd: int) -> None:
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    x, y = (rect.right - rect.left) // 2, (rect.bottom - rect.top) // 2
    lparam = (y << 16) | x
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)


def answer_record_prompt(choice: int = NOTES_AND_AUTOMATION, wait: float = 1.0) -> str | None:
    """Answer FL's "What would you like to record?" prompt if it appears within `wait` s.

    Returns the label of the button clicked, or None when no prompt showed up."""
    user32 = _user32()
    if user32 is None:
        return None
    deadline = time.monotonic() + wait
    try:
        while True:
            for hwnd, title in _fl_dialogs(user32):
                if title != RECORD_PROMPT_TITLE:
                    continue
                buttons = sorted(
                    (_top(user32, child), child)
                    for child, cls, _, _ in _windows(user32, hwnd) if cls == BUTTON_CLASS
                )
                if len(buttons) != len(RECORD_PROMPT_CHOICES):
                    log.warning("FL's record prompt has %d buttons, expected %d; not answering",
                                len(buttons), len(RECORD_PROMPT_CHOICES))
                    return None
                _click(user32, buttons[choice][1])
                return RECORD_PROMPT_CHOICES[choice]
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)
    except Exception:
        log.debug("could not answer FL's record prompt", exc_info=True)
        return None


def _top(user32, hwnd: int) -> int:
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.top
