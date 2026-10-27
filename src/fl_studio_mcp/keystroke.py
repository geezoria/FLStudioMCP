"""Small Win32 input helpers for FL Studio host-side UI operations.

The piano-roll bridge uses Ctrl+Alt+Y. Project Save As is implemented here
because FL's controller API exposes only ``FPT_SaveNew`` (which ignores the
requested path). These helpers use only :mod:`ctypes`; pywin32 is not required.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

log = logging.getLogger("fl_studio_mcp.keystroke")

HOTKEY_NAME = "Ctrl+Alt+Y (Win) / Cmd+Opt+Y (Mac)"

# FL Studio's piano-roll Script menu is keyboard navigable.  The sequence is
# deliberately expressed in virtual-key codes so it is independent of the UI
# language: F3 opens the piano-roll menu, Home selects File, two Downs select
# Tools, Right enters its scripts column, Down selects ComposeWithLLM, and
# Enter runs it (and makes it the target of Ctrl+Alt+Y from then on).
_COMPOSE_MENU_KEYS = (0x72, 0x24, 0x28, 0x28, 0x27, 0x28, 0x0D)


def request_file():
    from .file_bridge import REQUEST_FILE
    return REQUEST_FILE


def state_file():
    from .file_bridge import STATE_FILE
    return STATE_FILE


def clear_state() -> None:
    from .file_bridge import clear_state as _clear_state
    _clear_state()


def wait_for_state(deadline_sec: float = 3.0) -> dict | None:
    """Poll for the state file produced by the piano-roll pyscript."""
    from .file_bridge import wait_for_state as _wait_for_state
    return _wait_for_state(deadline_sec)


class _Win32Input:
    """Minimal SendInput wrapper, created lazily on Windows."""

    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    INPUT_KEYBOARD = 1
    GW_OWNER = 4
    GA_ROOTOWNER = 3
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_CHAR = 0x0102
    BM_CLICK = 0x00F5
    EM_SETSEL = 0x00B1

    def __init__(self):
        import ctypes
        import ctypes.wintypes as w

        self.ctypes = ctypes
        self.w = w
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                        ("time", w.DWORD), ("dwExtraInfo", ctypes.POINTER(w.ULONG))]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                        ("dwFlags", w.DWORD), ("time", w.DWORD),
                        ("dwExtraInfo", ctypes.POINTER(w.ULONG))]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]

        class _UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", w.DWORD), ("u", _UNION)]

        self.KEYBDINPUT = KEYBDINPUT
        self.INPUT = INPUT
        self.user32.SendInput.argtypes = (w.UINT, ctypes.POINTER(INPUT), ctypes.c_int)

    def key(self, vk: int, *, up: bool = False, scan: int = 0, unicode: bool = False):
        item = self.INPUT()
        item.type = self.INPUT_KEYBOARD
        flags = (self.KEYEVENTF_KEYUP if up else 0) | (self.KEYEVENTF_UNICODE if unicode else 0)
        item.u.ki = self.KEYBDINPUT(vk, scan, flags, 0, None)
        return item

    def send(self, items) -> bool:
        seq = list(items)
        array = (self.INPUT * len(seq))(*seq)
        sent = self.user32.SendInput(len(seq), array, self.ctypes.sizeof(self.INPUT))
        return sent == len(seq)

    def combo(self, modifiers: list[int], key: int) -> bool:
        return self.send(
            [self.key(vk) for vk in modifiers] + [self.key(key), self.key(key, up=True)]
            + [self.key(vk, up=True) for vk in reversed(modifiers)]
        )

    def press(self, key: int) -> bool:
        """Press and release one virtual key."""
        return self.send([self.key(key), self.key(key, up=True)])

    def text(self, value: str) -> bool:
        units = value.encode("utf-16-le")
        items = []
        for offset in range(0, len(units), 2):
            unit = int.from_bytes(units[offset:offset + 2], "little")
            items.extend([self.key(0, scan=unit, unicode=True),
                          self.key(0, up=True, scan=unit, unicode=True)])
        return self.send(items)

    def find_fl_window(self) -> int:
        target = [0]
        callback_type = self.ctypes.WINFUNCTYPE(self.ctypes.c_int, self.w.HWND, self.w.LPARAM)

        def callback(hwnd, _):
            length = self.user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return 1
            buf = self.ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(hwnd, buf, length + 1)
            if "FL Studio" in buf.value and self.user32.IsWindowVisible(hwnd):
                target[0] = hwnd
                return 0
            return 1

        self.user32.EnumWindows(callback_type(callback), 0)
        return target[0]

    def focus_window(self, hwnd: int) -> bool:
        """Reliably foreground a window from a background MCP process.

        ``SetForegroundWindow`` alone is advisory on modern Windows and often
        fails when the MCP host owns the foreground input queue. Temporarily
        attaching the current and target input queues lets the subsequent
        SendInput shortcut reach FL Studio deterministically.
        """
        kernel32 = self.ctypes.WinDLL("kernel32", use_last_error=True)
        current_thread = int(kernel32.GetCurrentThreadId())
        target_thread = int(self.user32.GetWindowThreadProcessId(hwnd, None))
        foreground = int(self.user32.GetForegroundWindow() or 0)
        foreground_thread = (
            int(self.user32.GetWindowThreadProcessId(foreground, None))
            if foreground else 0
        )
        attached: list[int] = []
        try:
            for thread_id in {target_thread, foreground_thread}:
                if thread_id and thread_id != current_thread:
                    if self.user32.AttachThreadInput(current_thread, thread_id, True):
                        attached.append(thread_id)
            self.user32.ShowWindow(hwnd, 9)
            self.user32.BringWindowToTop(hwnd)
            self.user32.SetForegroundWindow(hwnd)
            self.user32.SetActiveWindow(hwnd)
            self.user32.SetFocus(hwnd)
        finally:
            for thread_id in attached:
                self.user32.AttachThreadInput(current_thread, thread_id, False)
        foreground = int(self.user32.GetForegroundWindow() or 0)
        root_owner = int(self.user32.GetAncestor(foreground, self.GA_ROOTOWNER) or 0)
        return foreground == int(hwnd) or root_owner == int(hwnd)

    def window_text(self, hwnd: int) -> str:
        length = self.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = self.ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def window_class(self, hwnd: int) -> str:
        buf = self.ctypes.create_unicode_buffer(128)
        self.user32.GetClassNameW(hwnd, buf, len(buf))
        return buf.value

    def window_pid(self, hwnd: int) -> int:
        pid = self.w.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return int(pid.value)

    def find_save_dialog(self, fl_hwnd: int) -> int:
        """Find FL's classic or shell-hosted Save As modal.

        Windows can host the common file dialog outside FL64.exe, so matching
        only the FL process ID misses a visible dialog on some Windows 11/FL
        Studio builds. An owned/root-owned dialog or the foreground Save As
        dialog is sufficiently tied to the requested operation.
        """
        target = [0]
        fl_pid = self.window_pid(fl_hwnd)
        foreground = int(self.user32.GetForegroundWindow() or 0)
        callback_type = self.ctypes.WINFUNCTYPE(self.ctypes.c_int, self.w.HWND, self.w.LPARAM)

        def callback(candidate, _):
            if not self.user32.IsWindowVisible(candidate):
                return 1
            title = self.window_text(candidate).strip().lower()
            cls = self.window_class(candidate)
            looks_like_save = cls == "#32770" or title in {
                "save as", "save project as", "salvar como", "salvar projeto como",
            }
            if not looks_like_save:
                return 1
            owner = int(self.user32.GetWindow(candidate, self.GW_OWNER) or 0)
            root_owner = int(self.user32.GetAncestor(candidate, self.GA_ROOTOWNER) or 0)
            related = (
                self.window_pid(candidate) == fl_pid
                or owner == fl_hwnd
                or root_owner == fl_hwnd
                or int(candidate) == foreground
            )
            if related:
                target[0] = int(candidate)
                return 0
            return 1

        self.user32.EnumWindows(callback_type(callback), 0)
        return target[0]

    def find_descendant(self, root: int, *, class_name: str, control_id: int) -> int:
        target = [0]
        callback_type = self.ctypes.WINFUNCTYPE(self.ctypes.c_int, self.w.HWND, self.w.LPARAM)

        def callback(candidate, _):
            if (self.window_class(candidate) == class_name
                    and int(self.user32.GetDlgCtrlID(candidate)) == control_id):
                target[0] = int(candidate)
                return 0
            return 1

        self.user32.EnumChildWindows(root, callback_type(callback), 0)
        return target[0]

    def edit_text(self, edit: int, value: str) -> None:
        """Replace an Edit control's value through its normal message path."""
        self.user32.SetFocus(edit)
        self.user32.SendMessageW(edit, self.EM_SETSEL, 0, -1)
        for char in value:
            self.user32.SendMessageW(edit, self.WM_CHAR, ord(char), 0)

    def set_save_dialog_target(self, dialog: int, target: Path) -> bool:
        """Set directory/name through stable control IDs and submit the dialog."""
        self.focus_window(dialog)
        time.sleep(0.05)

        # Alt+D exposes the standard address edit. Its numeric control ID is
        # 41477 on the Windows 11 common file dialog in both en-US and pt-BR.
        self.combo([0x12], 0x44)
        time.sleep(0.05)
        address = self.find_descendant(dialog, class_name="Edit", control_id=41477)
        filename = self.find_descendant(dialog, class_name="Edit", control_id=1001)
        save_button = int(self.user32.GetDlgItem(dialog, 1) or 0)
        if not address or not filename or not save_button:
            return False

        self.edit_text(address, str(target.parent))
        self.user32.SendMessageW(address, self.WM_KEYDOWN, 0x0D, 0)
        self.user32.SendMessageW(address, self.WM_KEYUP, 0x0D, 0)
        time.sleep(0.50)

        # FL supplies the .flp extension from the selected file type.
        filename = self.find_descendant(dialog, class_name="Edit", control_id=1001)
        if not filename:
            return False
        self.edit_text(filename, target.stem)
        self.user32.SendMessageW(save_button, self.BM_CLICK, 0, 0)
        return True


def send_hotkey_windows() -> bool:
    """Find FL Studio and send Ctrl+Alt+Y. Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        api = _Win32Input()
        hwnd = api.find_fl_window()
        if not hwnd:
            log.warning("FL Studio window not found for keystroke")
            return False
        api.focus_window(hwnd)
        time.sleep(0.08)
        return api.combo([0x11, 0x12], 0x59)  # Ctrl+Alt+Y
    except Exception as exc:
        log.warning("keystroke failed: %s", exc)
        return False


def select_compose_script_windows() -> bool:
    """Select and run ComposeWithLLM from the active piano-roll Script menu.

    This is the bootstrap path for a fresh FL Studio profile, where
    Ctrl+Alt+Y cannot work yet because no piano-roll script has been selected.
    Running the script here also drains the already-staged request queue.
    """
    if sys.platform != "win32":
        return False
    try:
        api = _Win32Input()
        hwnd = api.find_fl_window()
        if not hwnd:
            log.warning("FL Studio window not found for piano-roll script bootstrap")
            return False
        api.focus_window(hwnd)
        time.sleep(0.10)
        for index, key in enumerate(_COMPOSE_MENU_KEYS):
            if not api.press(key):
                return False
            # FL builds the two custom popup columns after F3/Right.  A short
            # pause at those boundaries avoids racing the menu construction.
            time.sleep(0.12 if index in {0, 4} else 0.035)
        return True
    except Exception as exc:
        log.warning("piano-roll script bootstrap failed: %s", exc)
        return False


def save_project_as_windows(path: str, timeout_sec: float = 15.0) -> dict:
    """Save the active FL project to an explicit, previously-unused ``.flp`` path."""
    target = Path(path).expanduser()
    if target.suffix.lower() != ".flp":
        return {"ok": False, "error": "project_save_as path must end in .flp"}
    try:
        target = target.resolve(strict=False)
    except OSError as exc:
        return {"ok": False, "error": f"invalid project path: {exc}"}
    if not target.parent.is_dir():
        return {"ok": False, "error": f"parent directory does not exist: {target.parent}"}
    if target.exists():
        return {"ok": False, "error": f"target already exists; refusing to overwrite: {target}"}
    if sys.platform != "win32":
        return {"ok": False, "error": "project_save_as UI automation is available on Windows only"}

    try:
        api = _Win32Input()
        hwnd = api.find_fl_window()
        if not hwnd:
            return {"ok": False, "error": "FL Studio window not found"}
        api.focus_window(hwnd)
        time.sleep(0.12)
        dialog = [api.find_save_dialog(hwnd)]
        if not dialog[0]:
            if not api.combo([0x11, 0x10], 0x53):  # Ctrl+Shift+S
                return {"ok": False, "error": "failed to send Ctrl+Shift+S to FL Studio"}

        deadline = time.monotonic() + min(max(timeout_sec, 1.0), 30.0)
        while time.monotonic() < deadline and not dialog[0]:
            dialog[0] = api.find_save_dialog(hwnd)
            if not dialog[0]:
                time.sleep(0.05)
        if not dialog[0]:
            return {"ok": False, "error": "FL Studio Save As dialog did not appear"}

        if not api.set_save_dialog_target(dialog[0], target):
            return {"ok": False, "error": "failed to fill the Save As dialog controls"}

        while time.monotonic() < deadline:
            if target.is_file() and target.stat().st_size > 0:
                return {"ok": True, "path": str(target), "bytes": target.stat().st_size,
                        "verified": True}
            time.sleep(0.05)
        return {"ok": False, "error": f"Save As completed without creating {target}"}
    except Exception as exc:
        log.warning("project Save As failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
