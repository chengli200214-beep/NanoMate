"""Bounded Windows GUI actions for the task ledger.

The host guards ``assess`` through its existing Sentinel. Inputs use the host's
ComputerAct, including its per-application gate. No model or shell is started.
Observations are single-use and intentionally do not survive a restart or takeover.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageChops, ImageDraw, ImageGrab
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nanomuse.assistant.documents import _read_file, _relative, _root, _safe_path
from nanomuse.computer.link import INPUT_CONTEXT_FENCE
from nanomuse.computer.screen import active_window
from nanomuse.phone.screen import Screen
from nanomuse.schema import RiskLevel
from nanomuse.tools.base import CallAssessment
from nanomuse.tools.computer import ComputerAct, ComputerScreen

_KEYS = {
    ("ctrl", "a"),
    ("ctrl", "s"),
    ("ctrl", "home"),
    ("ctrl", "end"),
    *((key,) for key in ("tab", "esc", "up", "down", "left", "right", "home", "end")),
    ("backspace",),
    ("delete",),
}
_EXPLORER_KEYS = {("alt", "left"), ("alt", "right"), ("alt", "up")}


class NativeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["observe", "open_app", "type", "key", "click", "verify_saved_file"]
    app: Literal["notepad", "explorer"] = "notepad"
    path: str = Field(default="", max_length=1024)
    expected_observation: str = ""
    text: str = Field(default="", max_length=4000)
    keys: list[str] = Field(default_factory=list, max_length=3)
    x: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    y: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    label: str = Field(default="", max_length=120)
    saved_file: str = Field(default="", max_length=1024)
    expected_sha256: str = ""

    @field_validator("path", "saved_file")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return _relative(value) if value else value

    @field_validator("expected_observation", "expected_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        if value and (len(value) != 64 or any(c not in "0123456789abcdef" for c in value)):
            raise ValueError("Expected a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def shape(self) -> NativeSpec:
        if self.action not in {"observe", "open_app"} and not self.expected_observation:
            raise ValueError("Native actions need a fresh single-use observation")
        if self.action == "open_app" and self.app == "explorer" and not self.path:
            raise ValueError("File Explorer needs an explicit relative workspace directory")
        if self.action == "type" and (not self.text or self.app != "notepad"):
            raise ValueError("Typing is supported only in an opened Notepad document")
        if self.action == "key":
            allowed = (
                _KEYS
                if self.app == "notepad"
                else _EXPLORER_KEYS
                | {("tab",), ("esc",), ("up",), ("down",), ("left",), ("right",)}
            )
            if tuple(self.keys) not in allowed:
                raise ValueError("This key combination is outside the native adapter's scope")
        if self.action == "click" and (self.x is None or self.y is None or not self.label):
            raise ValueError("A native click needs picture coordinates and the visible label")
        if self.action == "verify_saved_file" and (
            not self.saved_file or not self.expected_sha256 or self.app != "notepad"
        ):
            raise ValueError("Save verification needs a Notepad workspace file and its hash")
        if self.path and self.action != "open_app":
            raise ValueError("path is only supported for opening an application")
        if self.text and self.action != "type":
            raise ValueError("text is only supported for typing")
        if self.keys and self.action != "key":
            raise ValueError("keys are only supported for key actions")
        if (self.x is not None or self.y is not None or self.label) and self.action != "click":
            raise ValueError("Coordinates and label are only supported for clicks")
        if (self.saved_file or self.expected_sha256) and self.action != "verify_saved_file":
            raise ValueError("Save evidence is only supported for verification")
        return self


def _editor_state(hwnd: int) -> dict[str, Any] | None:
    """Read bounded text and logical selection using system-marshalled messages only.

    WM_GETTEXT and EM_GETSEL are below WM_USER. Private RichEdit messages such as
    EM_EXGETSEL must never receive this process's pointers in a different process.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM

    def message(code: int, wparam: int = 0, lparam: int = 0) -> int:
        if not user32.SendMessageTimeoutW(
            hwnd, code, wparam, lparam, 0x23, 250, ctypes.byref(result)
        ):
            raise ValueError("The editor did not return verifiable state")
        return int(result.value)

    try:
        length = message(0x000E)  # WM_GETTEXTLENGTH
        if length > 65_535:
            return None  # WM_GETTEXT is not a reliable large-RichEdit transport.
        buffer = ctypes.create_unicode_buffer(length + 1)
        copied = message(0x000D, length + 1, ctypes.addressof(buffer))  # WM_GETTEXT
        text = buffer.value
        utf16_length = len(text.encode("utf-16-le")) // 2
        if copied != utf16_length:
            return None
        start, end = wintypes.DWORD(), wintypes.DWORD()
        message(0x00B0, ctypes.addressof(start), ctypes.addressof(end))  # EM_GETSEL
        if not 0 <= start.value <= end.value <= utf16_length:
            return None
        canonical = text.replace("\r\n", "\n").replace("\r", "\n")
        return {
            "text_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "utf16_length": utf16_length,
            "selection": [int(start.value), int(end.value)],
        }
    except (ValueError, OSError, UnicodeError):
        return None


def _cursor_rect(dpi: int) -> list[int] | None:
    """Read the visible OS pointer's exact bitmap extent and release copied GDI objects."""
    import ctypes
    from ctypes import wintypes

    class CursorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hCursor", wintypes.HANDLE),
            ("ptScreenPos", wintypes.POINT),
        ]

    class IconInfo(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    class Bitmap(ctypes.Structure):
        _fields_ = [
            ("bmType", wintypes.LONG),
            ("bmWidth", wintypes.LONG),
            ("bmHeight", wintypes.LONG),
            ("bmWidthBytes", wintypes.LONG),
            ("bmPlanes", wintypes.WORD),
            ("bmBitsPixel", wintypes.WORD),
            ("bmBits", wintypes.LPVOID),
        ]

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32  # type: ignore[attr-defined]
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CursorInfo)]
    user32.GetCursorInfo.restype = wintypes.BOOL
    user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(IconInfo)]
    user32.GetIconInfo.restype = wintypes.BOOL
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID]
    gdi32.GetObjectW.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL
    cursor, icon = CursorInfo(), IconInfo()
    cursor.cbSize = ctypes.sizeof(cursor)
    if not user32.GetCursorInfo(ctypes.byref(cursor)) or not cursor.flags & 1 or not cursor.hCursor:
        return None
    try:
        if not user32.GetIconInfo(cursor.hCursor, ctypes.byref(icon)) or icon.fIcon:
            return None
        bitmap = Bitmap()
        handle = icon.hbmColor or icon.hbmMask
        if not handle or gdi32.GetObjectW(handle, ctypes.sizeof(bitmap), ctypes.byref(bitmap)) != (
            ctypes.sizeof(bitmap)
        ):
            return None
        width, height = int(bitmap.bmWidth), int(bitmap.bmHeight)
        # A monochrome cursor packs its AND and XOR planes vertically in hbmMask.
        if not icon.hbmColor:
            if height % 2:
                return None
            height //= 2
        limit = min(128.0, 64 * max(1.0, dpi / 96))
        if not (
            0 < width <= limit
            and 0 < height <= limit
            and icon.xHotspot < width
            and icon.yHotspot < height
        ):
            return None
        left = int(cursor.ptScreenPos.x - icon.xHotspot)
        top = int(cursor.ptScreenPos.y - icon.yHotspot)
        return [left, top, left + width, top + height]
    except (OSError, ValueError):
        return None
    finally:
        # GetIconInfo allocates copies even though the global HCURSOR is borrowed.
        for handle in {icon.hbmMask, icon.hbmColor} - {None, 0}:
            gdi32.DeleteObject(handle)


def _window_identity() -> dict[str, Any]:
    """Read Windows focus, HWND, rectangle and scale without changing the desktop."""
    if sys.platform != "win32":
        raise ValueError("Native task actions currently require Windows")
    import ctypes
    from ctypes import wintypes

    class GUIThreadInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    if hasattr(user32, "GetDpiForWindow"):
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise ValueError("No foreground window; unlock the desktop and observe again")
    pid = wintypes.DWORD()
    thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    rect, info = wintypes.RECT(), GUIThreadInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)) or not user32.GetGUIThreadInfo(
        thread, ctypes.byref(info)
    ):
        raise ValueError("The foreground window or its keyboard focus cannot be verified")
    name = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, name, len(name))
    title = ctypes.create_unicode_buffer(4096)
    user32.GetWindowTextW(hwnd, title, len(title))
    dpi = user32.GetDpiForWindow(hwnd) if hasattr(user32, "GetDpiForWindow") else 0
    client = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        raise ValueError("The application client area cannot be verified")
    top_left, bottom_right = (
        wintypes.POINT(client.left, client.top),
        wintypes.POINT(client.right, client.bottom),
    )
    if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)) or not user32.ClientToScreen(
        hwnd, ctypes.byref(bottom_right)
    ):
        raise ValueError("The application client position cannot be verified")
    caret: list[int] | None = None
    focus_class = ctypes.create_unicode_buffer(256)
    focus_rect: list[int] | None = None
    if info.hwndFocus:
        user32.GetClassNameW(info.hwndFocus, focus_class, len(focus_class))
        focus_client = wintypes.RECT()
        if user32.GetClientRect(info.hwndFocus, ctypes.byref(focus_client)):
            first = wintypes.POINT(focus_client.left, focus_client.top)
            last = wintypes.POINT(focus_client.right, focus_client.bottom)
            if user32.ClientToScreen(info.hwndFocus, ctypes.byref(first)) and user32.ClientToScreen(
                info.hwndFocus, ctypes.byref(last)
            ):
                focus_rect = [first.x, first.y, last.x, last.y]
    if info.hwndCaret and info.rcCaret.right > info.rcCaret.left:
        first = wintypes.POINT(info.rcCaret.left, info.rcCaret.top)
        last = wintypes.POINT(info.rcCaret.right, info.rcCaret.bottom)
        if user32.ClientToScreen(info.hwndCaret, ctypes.byref(first)) and user32.ClientToScreen(
            info.hwndCaret, ctypes.byref(last)
        ):
            caret = [first.x, first.y, last.x, last.y]
    return {
        "foreground_window": int(hwnd),
        "process_id": int(pid.value),
        "focus_window": int(info.hwndFocus or 0),
        "focus_class": focus_class.value,
        "focus_rect": focus_rect,
        "editor_state": _editor_state(int(info.hwndFocus))
        if focus_class.value.casefold() == "edit"
        or focus_class.value.casefold().startswith("richedit")
        else None,
        "class_name": name.value,
        "window_title": title.value[:160],
        "window_rect": [rect.left, rect.top, rect.right, rect.bottom],
        "client_rect": [top_left.x, top_left.y, bottom_right.x, bottom_right.y],
        "caret_rect": caret,
        "cursor_rect": _cursor_rect(int(dpi)),
        "dpi": int(dpi),
    }


def _restore_window(expected: dict[str, Any]) -> None:
    """Reactivate only the exact application window the approved observation named."""
    import ctypes
    from ctypes import wintypes

    if str(expected["app"]).lower().removesuffix(".exe") not in {"notepad", "explorer"}:
        raise ValueError("Only an observed Notepad or File Explorer window can be restored")
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    hwnd = int(expected["foreground_window"])
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        raise ValueError("The observed window was closed or minimized; observe again")
    pid, rect = wintypes.DWORD(), wintypes.RECT()
    target_thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    name, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(4096)
    user32.GetClassNameW(hwnd, name, len(name))
    user32.GetWindowTextW(hwnd, title, len(title))
    if (
        pid.value != expected["process_id"]
        or name.value != expected["class_name"]
        or title.value[:160] != expected["window_title"]
        or [rect.left, rect.top, rect.right, rect.bottom] != expected["window_rect"]
    ):
        raise ValueError("The observed application window changed; observe again")
    if int(user32.GetForegroundWindow() or 0) == hwnd:
        return

    def wait_foreground(timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                return True
            if time.monotonic() >= deadline or not user32.IsWindow(hwnd):
                return False
            time.sleep(0.02)

    # Across input queues activation is asynchronous. A return value, whether
    # true or false, never substitutes for observing the exact foreground HWND.
    user32.SetForegroundWindow(hwnd)
    if wait_foreground(0.35):
        return
    # A browser approval can own Windows' foreground lock. Join its input queue
    # only for activation of this verified HWND, then detach before any input.
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM
    current_thread = kernel32.GetCurrentThreadId()
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    message = wintypes.MSG()
    user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
    # Joining an unresponsive input queue can hang the caller. WM_NULL carries
    # no input and checks both participants before any short-lived attachment.
    response = ctypes.c_size_t()
    for window in {int(foreground or 0), hwnd} - {0}:
        if not user32.SendMessageTimeoutW(window, 0, 0, 0, 0x23, 150, ctypes.byref(response)):
            raise ValueError("The approved window or current desktop is not responding")
    attached: list[int] = []
    try:
        for thread in dict.fromkeys((foreground_thread, target_thread)):
            if (
                thread
                and thread != current_thread
                and user32.AttachThreadInput(current_thread, thread, True)
            ):
                attached.append(thread)
        user32.SetForegroundWindow(hwnd)
    finally:
        for thread in reversed(attached):
            user32.AttachThreadInput(current_thread, thread, False)
    if not wait_foreground(0.75):
        raise ValueError("Windows could not reactivate the approved window; observe again")


def _capture_client(rect: list[int]) -> bytes:
    """Capture lossless client pixels so caret changes cannot spread through JPEG blocks."""
    image = ImageGrab.grab(bbox=(rect[0], rect[1], rect[2], rect[3]), all_screens=True)
    if image.size != (rect[2] - rect[0], rect[3] - rect[1]):
        raise ValueError("The application client capture has an unexpected size")
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _click_target_matches(state: dict[str, Any], x: float, y: float) -> bool:
    """Check the physical input target, including windows inside masked pointer pixels."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.IsChild.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    hit = int(user32.WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0)
    if not hit:
        return False
    process = wintypes.DWORD()
    if (
        not user32.GetWindowThreadProcessId(hit, ctypes.byref(process))
        or process.value != state["process_id"]
    ):
        return False
    if (
        str(state["app"]).lower().removesuffix(".exe") == "notepad"
        and state.get("fingerprint_scope") == "focused_editor"
    ):
        return hit == state.get("focus_window")
    main = int(state["foreground_window"])
    # GA_ROOT walks parents, not owners, so a separately owned popup cannot pass.
    return int(user32.GetAncestor(hit, 2) or 0) == main and (
        hit == main or bool(user32.IsChild(main, hit))
    )


def _changed_fields(expected: dict[str, Any], current: dict[str, Any]) -> str:
    omitted = {
        "observation_id",
        "expires_at",
        "screenshot",
        "screenshot_sha256",
        "state_sha256",
        "client_screenshot",
        "client_screenshot_sha256",
    }
    return (
        ", ".join(
            sorted(
                key
                for key in expected.keys() | current.keys()
                if key not in omitted and expected.get(key) != current.get(key)
            )
        )
        or "control_generation"
    )


def _window_pixels(data: bytes, state: dict[str, Any]) -> str:
    """Hash the client pixels, masking only the OS-reported caret at its exact position."""
    with Image.open(io.BytesIO(data)) as original:
        image = original.convert("RGB")
    width, height = image.size
    sw, sh = state["screen_width"], state["screen_height"]
    rect = state.get("client_rect") or state["window_rect"]
    box = (
        max(0, round(rect[0] * width / sw)),
        max(0, round(rect[1] * height / sh)),
        min(width, round(rect[2] * width / sw)),
        min(height, round(rect[3] * height / sh)),
    )
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("The application client is outside the observed display")
    caret = state.get("caret_rect")
    if caret:
        scale = max(1.0, state.get("dpi", 96) / 96)
        if not (0 < caret[2] - caret[0] <= 16 * scale and 0 < caret[3] - caret[1] <= 96 * scale):
            raise ValueError("The OS-reported caret is too large to mask safely")
        # Production fingerprints use lossless client captures, so only the exact
        # OS-reported caret needs masking. Its location still belongs to state.
        caret_box = tuple(
            round(value * (width / sw if index % 2 == 0 else height / sh))
            - (1 if index >= 2 else 0)
            for index, value in enumerate(caret)
        )
        ImageDraw.Draw(image).rectangle(caret_box, fill=(0, 0, 0))
    cropped = image.crop(box)
    return hashlib.sha256(cropped.tobytes()).hexdigest()


def _launch(app: str, path: Path | None) -> None:
    """Launch a fixed Windows binary with one literal path argument, never via a shell."""
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer)):  # type: ignore[attr-defined]
        raise ValueError("Windows system directory could not be located")
    windows = Path(buffer.value)
    executable = (
        windows / "System32" / "notepad.exe" if app == "notepad" else windows / "explorer.exe"
    )
    if not executable.is_file():
        raise ValueError("The requested Windows application is not installed")
    argv = [str(executable), *([str(path)] if path is not None else [])]
    subprocess.Popen(argv, shell=False, close_fds=True)  # noqa: S603


class NativeActions:
    def __init__(self, service: Any, workspace: Path):
        self.service = service
        self.workspace = _root(workspace)
        self._observation: dict[str, Any] | None = None
        self._observation_expires = 0.0
        self._opened_path = ""
        self._save_dispatched = False
        self._generation = 0
        self._lock = asyncio.Lock()
        self.can_execute: Callable[[], bool] | None = None

    def invalidate(self) -> None:
        """A pause, takeover or restart never grants permission to reuse old coordinates."""
        self._generation += 1
        self._observation = None
        self._observation_expires = 0.0
        self._opened_path = ""
        self._save_dispatched = False

    def _tools(self, *, observation: bool = False) -> tuple[ComputerAct, ComputerScreen]:
        if sys.platform != "win32":
            raise ValueError("Native task actions currently require Windows")
        app = self.service.app
        act, look = app.tools.get("computer_act"), app.tools.get("computer_screen")
        if not isinstance(act, ComputerAct) or not isinstance(look, ComputerScreen):
            raise ValueError("Enable computer use in Settings before running native task actions")
        if not act.link.backend_available():
            raise ValueError("Windows input backend is unavailable; install the hands dependency")
        if act.link.task_active:
            raise ValueError("Another computer task owns this desktop")
        holds = getattr(app, "holds", None)
        if not observation and holds is not None and holds.active():
            if any(hold.tool == "computer" for hold in holds.active()):
                self.invalidate()
                raise ValueError("The desktop is under human control; observe after handback")
        return act, look

    async def _snapshot(self) -> dict[str, Any]:
        act, _ = self._tools(observation=True)
        before = _window_identity()
        # A human may inspect the current frame while retaining control. This read
        # bypasses the tool's input hold wait, never the host's Sentinel guard.
        result = await ComputerScreen(link=act.link, holds=None).execute()
        if result.error:
            raise ValueError(result.error or result.output)
        screen: Screen | None = act.link.last_screen
        if screen is None or not screen.image_path or not screen.width or not screen.height:
            raise ValueError("A verified screenshot is required; the desktop may be locked")
        client_rect = before.get("client_rect") or before["window_rect"]
        fingerprint_rect = client_rect
        focus_rect = before.get("focus_rect")
        editor = str(before.get("focus_class") or "").casefold()
        if (
            screen.app.lower().removesuffix(".exe") == "notepad"
            and (editor == "edit" or editor.startswith("richedit"))
            and focus_rect
            and client_rect[0] <= focus_rect[0] < focus_rect[2] <= client_rect[2]
            and client_rect[1] <= focus_rect[1] < focus_rect[3] <= client_rect[3]
        ):
            fingerprint_rect = focus_rect
        client_pixels = _capture_client(fingerprint_rect)
        after = _window_identity()
        if before != after:
            raise ValueError("Window focus changed during capture; observe again")
        data = Path(screen.image_path).read_bytes()
        if not data:
            raise ValueError("The desktop screenshot is empty")
        mapping = act.link.mapping
        state = {
            **after,
            "app": screen.app,
            "window_title": after.get("window_title") or screen.app_name,
            "width": screen.width,
            "height": screen.height,
            "screen_width": mapping.screen_w if mapping else screen.width,
            "screen_height": mapping.screen_h if mapping else screen.height,
            "fingerprint_rect": fingerprint_rect,
            "fingerprint_scope": "focused_editor" if fingerprint_rect != client_rect else "client",
        }
        width = fingerprint_rect[2] - fingerprint_rect[0]
        height = fingerprint_rect[3] - fingerprint_rect[1]
        caret = after.get("caret_rect")
        local = {
            "window_rect": [0, 0, width, height],
            "screen_width": width,
            "screen_height": height,
            "dpi": after.get("dpi", 96),
            "caret_rect": [value - fingerprint_rect[index % 2] for index, value in enumerate(caret)]
            if caret
            else None,
        }
        state["pixel_sha256"] = _window_pixels(client_pixels, local)
        relative = f"screenshots/native-client-{uuid.uuid4().hex}.png"
        screenshot = _safe_path(self.workspace, relative, missing=True)
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot = _safe_path(self.workspace, relative, missing=True)
        with screenshot.open("xb") as stream:
            stream.write(client_pixels)
        return {
            **state,
            "state_sha256": hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest(),
            "screenshot": screen.image_path,
            "screenshot_sha256": hashlib.sha256(data).hexdigest(),
            "client_screenshot": str(screenshot),
            "client_screenshot_sha256": hashlib.sha256(client_pixels).hexdigest(),
        }

    def _same_observation(self, expected: dict[str, Any], current: dict[str, Any]) -> bool:
        if expected["state_sha256"] == current["state_sha256"]:
            return True
        changed = set(_changed_fields(expected, current).split(", "))
        if (
            not changed <= {"caret_rect", "cursor_rect", "pixel_sha256"}
            or expected.get("app", "").lower().removesuffix(".exe") != "notepad"
            or expected.get("fingerprint_scope") != "focused_editor"
            or not expected.get("editor_state")
            or expected["editor_state"] != current.get("editor_state")
        ):
            return False
        # Focus restoration can repaint the caret, and remote desktops can draw
        # the OS mouse pointer into captures. Exact text and selection must still
        # match, and no pixel outside their bounded OS-reported extents may differ.
        try:
            frames = []
            for observation in (expected, current):
                relative = Path(observation["client_screenshot"]).relative_to(self.workspace)
                data = _safe_path(self.workspace, relative.as_posix()).read_bytes()
                if hashlib.sha256(data).hexdigest() != observation["client_screenshot_sha256"]:
                    return False
                with Image.open(io.BytesIO(data)) as source:
                    frames.append(source.convert("RGB"))
            if frames[0].size != frames[1].size:
                return False
            rect = expected["fingerprint_rect"]
            for observation in (expected, current):
                scale = max(1.0, observation["dpi"] / 96)
                for field, max_width, max_height in (
                    ("caret_rect", 16 * scale, 96 * scale),
                    ("cursor_rect", min(128.0, 64 * scale), min(128.0, 64 * scale)),
                ):
                    overlay = observation.get(field)
                    if not overlay:
                        continue
                    if not (
                        0 < overlay[2] - overlay[0] <= max_width
                        and 0 < overlay[3] - overlay[1] <= max_height
                    ):
                        return False
                    box = tuple(
                        value - rect[index % 2] - (1 if index >= 2 else 0)
                        for index, value in enumerate(overlay)
                    )
                    for image in frames:
                        ImageDraw.Draw(image).rectangle(box, fill=(0, 0, 0))
            return ImageChops.difference(frames[0], frames[1]).getbbox() is None
        except (KeyError, ValueError, OSError):
            return False

    async def observe(self) -> dict[str, Any]:
        async with self._lock:
            return await self._issue_observation()

    async def _issue_observation(self) -> dict[str, Any]:
        generation = self._generation
        self._observation = None
        previous = await self._snapshot()
        for _attempt in range(4):
            await asyncio.sleep(0.15)
            observed = await self._snapshot()
            if self._same_observation(previous, observed):
                break
            previous = observed
        else:
            raise ValueError("The native view is still changing; observe once it settles")
        if generation != self._generation:
            raise ValueError("Native control changed while observing; observe again")
        observed["observation_id"] = hashlib.sha256(
            f"{self._generation}:{os.urandom(24).hex()}:{observed['state_sha256']}".encode()
        ).hexdigest()
        self._observation_expires = time.monotonic() + 300
        observed["expires_at"] = time.time() + 300
        self._observation = observed
        return dict(observed)

    async def _after_input(self, spec: NativeSpec, generation: int) -> dict[str, Any]:
        for _attempt in range(20 if spec.action == "open_app" else 1):
            if spec.action == "open_app":
                await asyncio.sleep(0.25)
                application, _ = await asyncio.to_thread(active_window)
                if application.lower().removesuffix(".exe") != spec.app:
                    continue
            self._permitted(generation)
            try:
                observed = await self._issue_observation()
            except (ValueError, OSError):
                continue
            if str(observed["app"]).lower().removesuffix(".exe") == spec.app and (
                not spec.path
                or spec.app != "notepad"
                or Path(spec.path).name.casefold() in observed["window_title"].casefold()
            ):
                return {"observation": observed}
            self._observation = None
        return {"requires_fresh_observation": True, "observation_unavailable": True}

    def assess(self, spec: NativeSpec) -> CallAssessment:
        if spec.action in {"observe", "verify_saved_file"}:
            return CallAssessment(
                risk=RiskLevel.SAFE,
                reads_private_data=True,
                target=spec.app,
                summary=f"native: {spec.action} in {spec.app}",
            )
        act = self.service.app.tools.get("computer_act")
        if isinstance(act, ComputerAct):
            result = act.assess(self._arguments(spec))
        else:
            result = CallAssessment(risk=RiskLevel.MODERATE, reads_private_data=True)
        result.target = spec.app
        result.summary = f"native: {spec.action} in {spec.app}" + (
            f" ({spec.path})" if spec.path else ""
        )
        # A declared label cannot establish what a generic native button actually does.
        if spec.action in {"click", "type", "key"}:
            result.risk = RiskLevel.SENSITIVE
        return result

    @staticmethod
    def _arguments(spec: NativeSpec) -> dict[str, Any]:
        if spec.action == "open_app":
            return {"action": "open_app", "app": spec.app}
        if spec.action == "type":
            return {"action": "type", "text": spec.text, "clear": False, "submit": False}
        if spec.action == "key":
            return {"action": "key", "keys": spec.keys}
        return {"action": "click", "x": spec.x, "y": spec.y, "label": spec.label}

    def _path(self, spec: NativeSpec) -> Path | None:
        if not spec.path:
            return None
        path = _safe_path(self.workspace, spec.path)
        if spec.app == "notepad":
            if not path.is_file() or path.suffix.lower() not in {".txt", ".md"}:
                raise ValueError("Notepad can open ordinary workspace .txt or .md files")
        elif not path.is_dir():
            raise ValueError("File Explorer can open ordinary workspace directories")
        return path

    def _permitted(self, generation: int) -> None:
        if generation != self._generation or (
            self.can_execute is not None and not self.can_execute()
        ):
            raise ValueError("Task paused or handed over before native input")
        self._tools()

    async def _gate(
        self, act: ComputerAct, current: dict[str, Any], generation: int, opening: str = ""
    ) -> None:
        refused = await act._app_allowed(opening)
        if refused is not None:
            raise ValueError(refused.error or refused.output)
        self._permitted(generation)
        if opening:
            return  # Starting a fixed app has no coordinates or focused-field dependency.
        _restore_window(current)
        await asyncio.sleep(0.15)
        self._permitted(generation)
        after_approval = await self._snapshot()
        if not self._same_observation(current, after_approval):
            raise ValueError(
                "Desktop changed during approval; observe again. Changed fields: "
                + _changed_fields(current, after_approval)
            )
        self._permitted(generation)

    async def execute(self, spec: NativeSpec | dict[str, Any]) -> dict[str, Any]:
        parsed = NativeSpec.model_validate(spec)
        if parsed.action == "observe":
            return {"observation": await self.observe(), "verification": "native_observation"}
        async with self._lock:
            expected, self._observation = self._observation, None
            if parsed.action == "open_app" and not parsed.expected_observation:
                return await self._open_application(parsed, self._generation)
            if (
                expected is None
                or expected["observation_id"] != parsed.expected_observation
                or time.monotonic() >= self._observation_expires
            ):
                raise ValueError("Native observation expired; observe again before acting")
            generation = self._generation
            self._permitted(generation)
            if parsed.action == "open_app":
                return await self._open_application(parsed, generation)
            if parsed.action != "open_app":
                if str(expected["app"]).lower().removesuffix(".exe") != parsed.app:
                    raise ValueError("The expected application does not own the foreground window")
                _restore_window(expected)
                await asyncio.sleep(0.15)
                self._permitted(generation)
            current = await self._snapshot()
            if parsed.action != "open_app" and (
                generation != self._generation or not self._same_observation(expected, current)
            ):
                raise ValueError(
                    "Native screen, focus or display scale changed; observe again. Changed fields: "
                    + _changed_fields(expected, current)
                )
            act, _ = self._tools()
            if str(current["app"]).lower().removesuffix(".exe") != parsed.app:
                raise ValueError("The expected application does not own the foreground window")
            if current["class_name"] == "#32770":
                raise ValueError(
                    "A native dialog needs human control; automatic dialog input is disabled"
                )
            if parsed.action == "verify_saved_file":
                if not self._save_dispatched or parsed.saved_file != self._opened_path:
                    raise ValueError("No GUI save was dispatched for this workspace file")
                actual = hashlib.sha256(
                    _read_file(self.workspace, parsed.saved_file, 1_048_576)
                ).hexdigest()
                if actual != parsed.expected_sha256:
                    raise ValueError("The saved file's contents do not match the expected hash")
                return {
                    "saved_file": parsed.saved_file,
                    "sha256": actual,
                    "verification": "native_save_file_sha256",
                    "gui_save_dispatched": True,
                }
            if parsed.action in {"type", "key"} and parsed.app == "notepad":
                if (
                    not self._opened_path
                    or Path(self._opened_path).name.casefold()
                    not in str(current["window_title"]).casefold()
                ):
                    raise ValueError(
                        "Reopen the workspace file in Notepad before editing or saving"
                    )
            if parsed.action == "click" and (
                parsed.x >= current["width"] or parsed.y >= current["height"]
            ):
                raise ValueError("Click coordinates are outside the observed picture")
            click_point: tuple[float, float] | None = None
            if parsed.action == "click":
                rect = current["fingerprint_rect"]
                physical_x = parsed.x * current["screen_width"] / current["width"]
                physical_y = parsed.y * current["screen_height"] / current["height"]
                if not (rect[0] <= physical_x < rect[2] and rect[1] <= physical_y < rect[3]):
                    raise ValueError("Clicks must stay inside the observed application client")
                click_point = (round(physical_x, 2), round(physical_y, 2))
            await self._gate(act, current, generation)
            args = self._arguments(parsed)
            if parsed.action == "click" and act.link.settings.coords == "norm1000":
                args["x"] = parsed.x / current["width"] * 1000
                args["y"] = parsed.y / current["height"] * 1000
            # The original per-app gate has completed and focus was checked afterwards.
            # Do not introduce another approval/hold wait between that check and input.
            guarded_act = ComputerAct(link=act.link, gui=act.gui, holds=None, app_gate=None)
            interrupted = threading.Event()

            def input_fence() -> str | None:
                if interrupted.is_set() or generation != self._generation:
                    return "Native input was paused or cancelled"
                try:
                    actual = _window_identity()
                except (ValueError, OSError):
                    return "Native window focus could not be verified"
                fields = (
                    "foreground_window",
                    "process_id",
                    "focus_window",
                    "class_name",
                    "window_rect",
                    "client_rect",
                    "focus_rect",
                    "dpi",
                )
                if any(actual.get(key) != current.get(key) for key in fields):
                    return "The user changed the native window or keyboard focus"
                if click_point is not None:
                    try:
                        if not _click_target_matches(current, *click_point):
                            return "The native click target is covered or no longer the observed window"
                    except (ValueError, OSError):
                        return "The native click target could not be verified"
                return None

            self._save_dispatched = False
            fence_token = INPUT_CONTEXT_FENCE.set(input_fence)
            try:
                result = await guarded_act.execute(**args)
            except asyncio.CancelledError:
                self.invalidate()
                raise
            finally:
                # to_thread retains its copied context after this task is cancelled.
                # Stop its next bounded input chunk even after resetting our context.
                interrupted.set()
                INPUT_CONTEXT_FENCE.reset(fence_token)
            if result.error:
                raise ValueError(result.error or result.output)
            if parsed.action == "key" and parsed.keys == ["ctrl", "s"]:
                self._save_dispatched = True
            return {
                "app": parsed.app,
                "action": parsed.action,
                "verification": "native_input_dispatched",
                **await self._after_input(parsed, generation),
            }

    async def _open_application(self, spec: NativeSpec, generation: int) -> dict[str, Any]:
        """A fixed application launch needs no coordinates or unrelated desktop capture."""
        self._permitted(generation)
        act, _ = self._tools()
        path = self._path(spec)
        refused = await act._app_allowed(spec.app)
        if refused is not None:
            raise ValueError(refused.error or refused.output)
        self._permitted(generation)
        _launch(spec.app, path)
        self._opened_path = spec.path if spec.app == "notepad" else ""
        self._save_dispatched = False
        return {
            "app": spec.app,
            "path": spec.path,
            "verification": "launch_dispatched",
            **await self._after_input(spec, generation),
        }
