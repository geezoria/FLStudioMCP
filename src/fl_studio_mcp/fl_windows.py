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


# --- Running a piano-roll script -------------------------------------------
#
# FL's API can't run a piano-roll script, and "Run last script again"
# (Ctrl+Alt+Y) only works after the user ran the script once in this session.
# FL keeps no "last script" across restarts. So we open the piano roll's Tools
# menu (the wrench on its caption bar) with a posted click and pick the script
# with posted arrow keys. FL's popup menu follows the real mouse cursor and
# ignores the coordinates of posted mouse messages, but it reads posted keys.
# Nothing here moves the cursor or brings FL to the foreground.
#
# The menu is one list in two columns: the built-in tools, then the Scripts
# column, which ends with every .pyscript from FL's own folder and the user's,
# sorted by name. Up from no selection wraps to the last item, so a script's
# position counted from the end selects it no matter how long the built-in
# part is, or whether "Run last script again" is enabled.

PIANO_ROLL_FORM_CLASS = "TEventEditForm"
PIANO_ROLL_TITLE = "Piano roll"
CAPTION_CLASS = "TNewCaption"
POPUP_MENU_CLASS = "TQuickPopupMenuWindow"
# The Tools icon on the piano roll's caption bar, and the bar's height, at 100 %
# display scaling (FL 2026).
TOOLS_ICON = (33, 11)
CAPTION_HEIGHT = 23
SYSTEM_SCRIPTS = ("System", "Config", "Piano roll scripts")
WM_MOUSEMOVE = 0x0200
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_UP = 0x26


def script_steps_from_end(script: str, names) -> int | None:
    """Up presses that select `script` in the Tools menu, given every script name
    FL lists (without the .pyscript extension). None when it isn't listed."""
    ordered = sorted({name.casefold() for name in names})
    key = script.casefold()
    if key not in ordered:
        return None
    return len(ordered) - ordered.index(key)


def piano_roll_scripts(*folders) -> list[str]:
    """Names of the .pyscript files in `folders`, as FL's Tools menu lists them."""
    from pathlib import Path

    names = []
    for folder in folders:
        try:
            names += [p.stem for p in Path(folder).glob("*.pyscript") if p.is_file()]
        except OSError:
            continue
    return names


def _process_dir(pid: int):
    import ctypes
    from ctypes import wintypes
    from pathlib import Path

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return Path(buf.value).parent
    finally:
        kernel32.CloseHandle(handle)


def _rect(user32, hwnd: int) -> tuple[int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _post_keys(user32, hwnd: int, keys) -> None:
    for vk in keys:
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0x00000001)
        user32.PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000001)


def _popup_menus(user32, pids) -> set[int]:
    return {hwnd for hwnd, cls, _, pid in _windows(user32)
            if cls == POPUP_MENU_CLASS and pid in pids}


def _find_piano_roll(user32, mains) -> int | None:
    for main in mains:
        for hwnd, cls, title, _ in _windows(user32, main):
            if (cls == PIANO_ROLL_FORM_CLASS and title.startswith(PIANO_ROLL_TITLE)
                    and user32.IsWindowVisible(hwnd)):
                return hwnd
    return None


def run_piano_roll_script(script: str, user_folder, wait: float = 1.5) -> dict:
    """Run a piano-roll script from the open piano roll's Tools menu.

    Returns {"ok": True, "steps": n} once Enter was posted on the script's menu
    item, or {"ok": False, "reason": ...}. It doesn't wait for the script to run.
    """
    user32 = _user32()
    if user32 is None:
        return {"ok": False, "reason": "only available on Windows"}
    try:
        windows = _windows(user32)
        mains = [(hwnd, pid) for hwnd, cls, _, pid in windows if cls == MAIN_FORM_CLASS]
        if not mains:
            return {"ok": False, "reason": "FL Studio's window was not found"}
        pids = {pid for _, pid in mains}
        deadline = time.monotonic() + wait
        form = _find_piano_roll(user32, [hwnd for hwnd, _ in mains])
        while form is None and time.monotonic() < deadline:
            time.sleep(0.05)
            form = _find_piano_roll(user32, [hwnd for hwnd, _ in mains])
        if form is None:
            return {"ok": False, "reason": "no piano roll is open"}

        exe_dir = _process_dir(mains[0][1])
        folders = [user_folder] + ([exe_dir.joinpath(*SYSTEM_SCRIPTS)] if exe_dir else [])
        steps = script_steps_from_end(script, piano_roll_scripts(*folders))
        if steps is None:
            return {"ok": False, "reason": f"{script}.pyscript is not in {folders}"}

        captions = sorted(
            (_rect(user32, hwnd)[1], hwnd) for hwnd, cls, _, _ in _windows(user32, form)
            if cls == CAPTION_CLASS and user32.GetParent(hwnd) == form
        )
        if not captions:
            return {"ok": False, "reason": "the piano roll's caption bar was not found"}
        caption = captions[0][1]
        left, top, _, bottom = _rect(user32, caption)
        scale = (bottom - top) / CAPTION_HEIGHT or 1.0
        x, y = round(TOOLS_ICON[0] * scale), round(TOOLS_ICON[1] * scale)
        lparam = (y << 16) | x

        for menu in _popup_menus(user32, pids):  # a menu left open would take the keys
            _post_keys(user32, menu, [VK_ESCAPE])
        before = _popup_menus(user32, pids)
        user32.PostMessageW(caption, WM_MOUSEMOVE, 0, lparam)
        user32.PostMessageW(caption, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        user32.PostMessageW(caption, WM_LBUTTONUP, 0, lparam)
        menu = None
        menu_deadline = time.monotonic() + 1.0
        while menu is None and time.monotonic() < menu_deadline:
            opened = _popup_menus(user32, pids) - before
            menu = next(iter(opened)) if opened else None
            if menu is None:
                time.sleep(0.02)
        if menu is None:
            return {"ok": False, "reason": "the piano roll's Tools menu did not open"}
        _post_keys(user32, menu, [VK_UP] * steps + [VK_RETURN])
        return {"ok": True, "steps": steps}
    except Exception as exc:
        log.debug("could not run %s from the piano roll", script, exc_info=True)
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
