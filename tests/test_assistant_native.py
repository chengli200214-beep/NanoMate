"""Native task actions use fresh, single-use focus evidence and actual GUI dispatch."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from nanomuse.agent.holds import Holds
from nanomuse.assistant import native_actions as native
from nanomuse.assistant.native_actions import NativeActions, NativeSpec
from nanomuse.assistant.native_actions import _click_target_matches as verified_click_target
from nanomuse.assistant.native_actions import _restore_window as restore_native_window
from nanomuse.computer.link import ComputerLink
from nanomuse.config import GUISettings, HandsSettings
from nanomuse.schema import RiskLevel
from nanomuse.tools.base import ToolCollection
from nanomuse.tools.computer import ComputerAct, ComputerScreen
from tests.test_phone import png


class FakeHands:
    name = "fake"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.calls: list[tuple[Any, ...]] = []
        self.buffer = "initial"

    def size(self) -> tuple[int, int]:
        return 640, 480

    def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        self.calls.append(("click", x, y, button, clicks))

    def type(self, text: str) -> None:
        self.calls.append(("type", text))
        self.buffer += text

    def key(self, keys: list[str]) -> None:
        self.calls.append(("key", *keys))
        if keys == ["ctrl", "s"]:
            # Only the fake GUI backend simulates a save. The adapter cannot write this file.
            (self.workspace / "draft.txt").write_text(self.buffer, encoding="utf-8")


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "draft.txt").write_text("initial", encoding="utf-8")
    hands = FakeHands(workspace)
    raw: dict[str, Any] = {
        "app": "firefox",
        "app_name": "Local fixture",
        "width": 640,
        "height": 480,
        "screenshot": png(64, 48),
        "keyboard": False,
    }
    identity = {
        "foreground_window": 3,
        "process_id": 4,
        "focus_window": 5,
        "class_name": "Notepad",
        "window_rect": [0, 0, 640, 480],
        "dpi": 96,
    }
    launches: list[tuple[str, Path | None]] = []

    def launch(app: str, path: Path | None) -> None:
        launches.append((app, path))
        raw.update(app=app, app_name=f"{path.name if path else 'Untitled'} - {app}")
        identity["foreground_window"] += 1

    def capture_client(rect: list[int]) -> bytes:
        with Image.open(io.BytesIO(base64.b64decode(raw["screenshot"]))) as original:
            image = original.convert("RGB").resize((640, 480)).crop(tuple(rect))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(native, "_window_identity", lambda: dict(identity))
    monkeypatch.setattr(native, "active_window", lambda: (raw["app"], raw["app_name"]))
    monkeypatch.setattr(native, "_restore_window", lambda _expected: None)
    monkeypatch.setattr(native, "_click_target_matches", lambda _state, _x, _y: True)
    monkeypatch.setattr(native, "_capture_client", capture_client)
    monkeypatch.setattr(native, "_launch", launch)
    monkeypatch.setattr("nanomuse.computer.link.screen_size", lambda: (640, 480))
    monkeypatch.setattr("nanomuse.computer.link.capture", lambda _width: dict(raw))
    monkeypatch.setattr(
        "nanomuse.computer.link.active_window", lambda: (raw["app"], raw["app_name"])
    )
    link = ComputerLink(
        HandsSettings(enabled=True, settle_s=0), shots_dir=tmp_path / "shots", backend=hands
    )
    holds = Holds()
    act = ComputerAct(link=link, gui=GUISettings(), holds=holds)
    look = ComputerScreen(link=link, holds=holds)
    service = SimpleNamespace(app=SimpleNamespace(tools=ToolCollection(act, look), holds=holds))
    actions = NativeActions(service, workspace)
    return SimpleNamespace(
        actions=actions,
        act=act,
        look=look,
        link=link,
        hands=hands,
        raw=raw,
        identity=identity,
        launches=launches,
        workspace=workspace,
        holds=holds,
        service=service,
    )


async def run(h: SimpleNamespace, action: str, **kwargs: Any) -> dict[str, Any]:
    observation = await h.actions.observe()
    return await h.actions.execute(
        NativeSpec(action=action, expected_observation=observation["observation_id"], **kwargs)
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "type", "text": ""},
        {"action": "open_app", "app": "powershell"},
        {"action": "open_app", "app": "explorer"},
        {"action": "open_app", "path": "../outside.txt"},
        {"action": "open_app", "path": "AUX.txt"},
        {"action": "key", "keys": ["win", "r"]},
        {"action": "key", "app": "explorer", "keys": ["enter"]},
        {"action": "type", "app": "explorer", "text": "powershell"},
        {"action": "click", "x": float("inf"), "y": 1, "label": "Document"},
        {"action": "observe", "text": "unexpected"},
    ],
)
def test_strict_bounded_spec(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NativeSpec.model_validate({"expected_observation": "a" * 64, **payload})


def test_native_inputs_require_observation() -> None:
    with pytest.raises(ValidationError, match="fresh single-use"):
        NativeSpec(action="type", text="hello")


async def test_observation_is_private_metadata_and_single_use(harness: SimpleNamespace) -> None:
    h = harness
    observation = await h.actions.observe()
    assert len(observation["observation_id"]) == 64
    assert observation["window_title"] == "Local fixture"
    assert "base64" not in observation and "screenshot" in observation
    spec = NativeSpec(
        action="open_app", path="draft.txt", expected_observation=observation["observation_id"]
    )
    result = await h.actions.execute(spec)
    assert result["verification"] == "launch_dispatched"
    assert h.launches == [("notepad", h.workspace / "draft.txt")]
    with pytest.raises(ValueError, match="expired"):
        await h.actions.execute(spec)


async def test_expired_native_observation_is_not_replayed(harness: SimpleNamespace) -> None:
    h = harness
    observation = await h.actions.observe()
    h.actions._observation_expires = 0
    with pytest.raises(ValueError, match="expired"):
        await h.actions.execute(
            NativeSpec(action="open_app", expected_observation=observation["observation_id"])
        )
    assert h.launches == []


async def test_post_input_frame_supplies_next_single_use_token(harness: SimpleNamespace) -> None:
    h = harness
    launched = await run(h, "open_app", path="draft.txt")
    assert launched["observation"]["app"] == "notepad"
    typed = await h.actions.execute(
        NativeSpec(
            action="type",
            text=" edited",
            expected_observation=launched["observation"]["observation_id"],
        )
    )
    assert typed["observation"]["observation_id"] != launched["observation"]["observation_id"]
    assert h.hands.calls == [("type", " edited")]


async def test_allowlisted_launch_does_not_capture_unrelated_private_window(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    identity = native._window_identity

    def private_window() -> dict[str, Any]:
        if h.raw["app"] != "notepad":
            raise AssertionError("Unrelated foreground window must not be captured")
        return identity()

    monkeypatch.setattr(native, "_window_identity", private_window)
    opened = await h.actions.execute(NativeSpec(action="open_app", path="draft.txt"))
    assert h.launches == [("notepad", h.workspace / "draft.txt")]
    assert opened["observation"]["app"] == "notepad"


async def test_approval_can_return_to_only_bound_target_window(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    launched = await run(h, "open_app", path="draft.txt")
    observed = launched["observation"]
    target = dict(h.identity)
    restore_calls = []

    def restore(expected: dict[str, Any]) -> None:
        restore_calls.append((expected["foreground_window"], expected["process_id"]))
        h.identity.update(target)
        h.raw.update(app="notepad", app_name="draft.txt - notepad")

    async def gate(_app: str, _label: str) -> bool:
        h.raw.update(app="firefox", app_name="Approval")
        h.identity["foreground_window"] = 91
        return True

    monkeypatch.setattr(native, "_restore_window", restore)
    h.act.app_gate = gate
    h.raw.update(app="firefox", app_name="Approval")
    h.identity["foreground_window"] = 91
    await h.actions.execute(
        NativeSpec(action="type", text=" edited", expected_observation=observed["observation_id"])
    )
    assert restore_calls == [(target["foreground_window"], target["process_id"])] * 2
    assert h.hands.calls == [("type", " edited")]


def test_window_fingerprint_ignores_only_external_pixels_and_os_caret() -> None:
    state = {
        "screen_width": 200,
        "screen_height": 100,
        "window_rect": [10, 10, 190, 95],
        "client_rect": [20, 20, 180, 90],
        "caret_rect": [40, 40, 41, 55],
        "dpi": 96,
    }

    def pixels(*, caret: bool = False, external: bool = False, content: bool = False) -> bytes:
        image = Image.new("RGB", (200, 100), "white")
        draw = ImageDraw.Draw(image)
        if caret:
            draw.rectangle((40, 40, 40, 54), fill="black")
        if external:
            draw.rectangle((0, 95, 199, 99), fill="black")
        if content:
            draw.rectangle((100, 50, 120, 60), fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    baseline = native._window_pixels(pixels(), state)
    assert native._window_pixels(pixels(caret=True, external=True), state) == baseline
    assert native._window_pixels(pixels(content=True), state) != baseline
    unsafe = {**state, "caret_rect": [20, 20, 180, 90]}
    with pytest.raises(ValueError, match="too large"):
        native._window_pixels(pixels(), unsafe)


def caret_frame(
    h: SimpleNamespace,
    caret: list[int] | None,
    *,
    selection: list[int] | None = None,
    text: str = "note",
    changed_pixel: bool = False,
    cursor: list[int] | None = None,
) -> dict[str, Any]:
    image = Image.new("RGB", (100, 60), "white")
    draw = ImageDraw.Draw(image)
    if caret:
        draw.rectangle((caret[0], caret[1], caret[2] - 1, caret[3] - 1), fill="black")
    if changed_pixel:
        draw.rectangle((70, 20, 80, 30), fill="black")
    if cursor:
        draw.rectangle((cursor[0], cursor[1], cursor[2] - 1, cursor[3] - 1), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    path = h.workspace / "screenshots" / f"native-client-{uuid.uuid4().hex}.png"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    state = {
        "app": "notepad",
        "fingerprint_scope": "focused_editor",
        "fingerprint_rect": [0, 0, 100, 60],
        "screen_width": 100,
        "screen_height": 60,
        "window_rect": [0, 0, 100, 60],
        "dpi": 96,
        "caret_rect": caret,
        "cursor_rect": cursor,
        "editor_state": {
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "selection": selection or [4, 4],
        },
    }
    state["pixel_sha256"] = native._window_pixels(data, state)
    return {
        **state,
        "state_sha256": hashlib.sha256(repr(state).encode()).hexdigest(),
        "client_screenshot": str(path),
        "client_screenshot_sha256": hashlib.sha256(data).hexdigest(),
    }


@pytest.mark.parametrize("after", [None, [21, 10, 22, 30]])
def test_caret_repaint_requires_same_text_and_logical_selection(
    harness: SimpleNamespace, after: list[int] | None
) -> None:
    before = caret_frame(harness, [20, 10, 21, 30])
    current = caret_frame(harness, after)
    assert harness.actions._same_observation(before, current)


@pytest.mark.parametrize(
    "change", ["text", "selection", "outside_pixel", "missing_semantics", "tampered_png"]
)
def test_caret_exception_never_hides_content_or_selection_changes(
    harness: SimpleNamespace, change: str
) -> None:
    before = caret_frame(harness, [20, 10, 21, 30])
    kwargs: dict[str, Any] = {}
    if change == "text":
        kwargs["text"] = "edited note"
    elif change == "selection":
        kwargs["selection"] = [0, 0]
    elif change == "outside_pixel":
        kwargs["changed_pixel"] = True
    current = caret_frame(harness, None, **kwargs)
    if change == "missing_semantics":
        before["editor_state"] = current["editor_state"] = None
    elif change == "tampered_png":
        Path(before["client_screenshot"]).write_bytes(
            Path(current["client_screenshot"]).read_bytes()
        )
    assert not harness.actions._same_observation(before, current)


@pytest.mark.parametrize("after", [None, [24, 5, 38, 30]])
def test_remote_mouse_pointer_repaint_requires_exact_editor_state(
    harness: SimpleNamespace, after: list[int] | None
) -> None:
    before = caret_frame(harness, [20, 10, 21, 30], cursor=[2, 5, 16, 30])
    current = caret_frame(harness, None, cursor=after)
    assert harness.actions._same_observation(before, current)


@pytest.mark.parametrize(
    "change",
    ["text", "selection", "outside_pixel", "unknown_cursor", "large_cursor", "other_app"],
)
def test_pointer_exception_never_hides_content_or_unverified_regions(
    harness: SimpleNamespace, change: str
) -> None:
    before = caret_frame(harness, None, cursor=[2, 5, 16, 30])
    kwargs: dict[str, Any] = {"cursor": [24, 5, 38, 30]}
    if change == "text":
        kwargs["text"] = "edited note"
    elif change == "selection":
        kwargs["selection"] = [0, 0]
    elif change == "outside_pixel":
        kwargs["changed_pixel"] = True
    elif change == "large_cursor":
        kwargs["cursor"] = [0, 0, 65, 60]
    current = caret_frame(harness, None, **kwargs)
    if change == "unknown_cursor":
        current["cursor_rect"] = None
    elif change == "other_app":
        before["app"] = current["app"] = "explorer"
    assert not harness.actions._same_observation(before, current)


@pytest.mark.parametrize("kind", ["color", "monochrome", "hidden", "oversized", "unreadable"])
def test_os_cursor_uses_bitmap_hotspot_and_always_releases_owned_bitmaps(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import ctypes

    class Function:
        def __init__(self, callback: Any):
            self.callback = callback

        def __call__(self, *args: Any) -> Any:
            return self.callback(*args)

    deleted: list[int] = []
    objects: list[int] = []

    def cursor_info(pointer: Any) -> int:
        info = pointer._obj
        assert info.cbSize == ctypes.sizeof(info)
        info.flags, info.hCursor = (0 if kind == "hidden" else 1), 9
        info.ptScreenPos.x, info.ptScreenPos.y = 300, 200
        return 1

    def icon_info(handle: int, pointer: Any) -> int:
        assert handle == 9
        info = pointer._obj
        info.fIcon, info.xHotspot, info.yHotspot = 0, 7, 11
        info.hbmMask, info.hbmColor = 10, 0 if kind == "monochrome" else 20
        return 1

    def bitmap(handle: int, size: int, pointer: Any) -> int:
        objects.append(handle)
        if kind == "unreadable":
            return 0
        info = pointer._obj
        info.bmWidth = 129 if kind == "oversized" else 32
        info.bmHeight = 64 if kind == "monochrome" else 32
        return size

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(
            user32=SimpleNamespace(
                GetCursorInfo=Function(cursor_info), GetIconInfo=Function(icon_info)
            ),
            gdi32=SimpleNamespace(
                GetObjectW=Function(bitmap),
                DeleteObject=Function(lambda handle: deleted.append(handle) or 1),
            ),
        ),
        raising=False,
    )
    rect = native._cursor_rect(96)
    assert rect == ([293, 189, 325, 221] if kind in {"color", "monochrome"} else None)
    assert set(deleted) == (
        set() if kind == "hidden" else {10} if kind == "monochrome" else {10, 20}
    )
    assert len(deleted) == len(set(deleted))
    assert objects == ([] if kind == "hidden" else [10] if kind == "monochrome" else [20])


async def test_takeover_observation_requires_reopening_workspace_document(
    harness: SimpleNamespace,
) -> None:
    h = harness
    await h.actions.execute(NativeSpec(action="open_app", path="draft.txt"))
    h.actions.invalidate()  # Pause and takeover clear the document binding.
    after_handoff = await h.actions.observe()
    with pytest.raises(ValueError, match="Reopen the workspace file"):
        await h.actions.execute(
            NativeSpec(
                action="type",
                text="should not run",
                expected_observation=after_handoff["observation_id"],
            )
        )
    assert h.hands.calls == []
    reopened = await h.actions.execute(NativeSpec(action="open_app", path="draft.txt"))
    await h.actions.execute(
        NativeSpec(
            action="type",
            text=" edited",
            expected_observation=reopened["observation"]["observation_id"],
        )
    )
    assert h.hands.calls == [("type", " edited")]


@pytest.mark.parametrize("change", ["focus", "window", "dpi", "resolution", "pixels"])
async def test_changed_desktop_requires_new_observation(
    harness: SimpleNamespace, change: str
) -> None:
    h = harness
    await run(h, "open_app", path="draft.txt")
    observation = await h.actions.observe()
    if change == "focus":
        h.identity["focus_window"] = 99
    elif change == "window":
        h.identity["foreground_window"] = 99
    elif change == "dpi":
        h.identity["dpi"] = 144
    elif change == "resolution":
        h.raw["width"] = 800
    else:
        image, buffer = Image.new("RGB", (64, 48), "black"), io.BytesIO()
        image.save(buffer, format="PNG")
        h.raw["screenshot"] = base64.b64encode(buffer.getvalue()).decode()
    spec = NativeSpec(
        action="type", text="edit", expected_observation=observation["observation_id"]
    )
    with pytest.raises(ValueError, match="changed"):
        await h.actions.execute(spec)
    assert h.hands.calls == []


async def test_pause_and_takeover_invalidate_previous_coordinates(harness: SimpleNamespace) -> None:
    h = harness
    observation = await h.actions.observe()
    h.actions.invalidate()
    with pytest.raises(ValueError, match="expired"):
        await h.actions.execute(
            NativeSpec(action="open_app", expected_observation=observation["observation_id"])
        )
    hold = h.holds.open("main", "computer")
    observation = (
        await h.actions.observe()
    )  # Read-only inspection remains possible during takeover.
    with pytest.raises(ValueError, match="human control"):
        await h.actions.execute(
            NativeSpec(action="open_app", expected_observation=observation["observation_id"])
        )
    h.holds.done(hold.id)
    assert h.launches == []


async def test_final_task_state_checked_after_per_app_approval(harness: SimpleNamespace) -> None:
    h = harness
    allowed = True

    async def gate(_app: str, _label: str) -> bool:
        nonlocal allowed
        allowed = False
        return True

    h.act.app_gate = gate
    h.actions.can_execute = lambda: allowed
    with pytest.raises(ValueError, match="paused"):
        await run(h, "open_app", path="draft.txt")
    assert h.launches == []


async def test_per_app_approval_cannot_change_focus_and_then_input(
    harness: SimpleNamespace,
) -> None:
    h = harness
    await run(h, "open_app", path="draft.txt")

    async def gate(_app: str, _label: str) -> bool:
        h.identity["focus_window"] = 99
        return True

    h.act.app_gate = gate
    with pytest.raises(ValueError, match="changed during approval"):
        await run(h, "type", text="edit")
    assert h.hands.calls == []


async def test_gui_typing_save_and_separate_file_verification(harness: SimpleNamespace) -> None:
    h = harness
    await run(h, "open_app", path="draft.txt")
    await run(h, "type", text=" edited")
    assert (h.workspace / "draft.txt").read_text(encoding="utf-8") == "initial"
    expected = hashlib.sha256(b"initial edited").hexdigest()
    with pytest.raises(ValueError, match="No GUI save"):
        await run(h, "verify_saved_file", saved_file="draft.txt", expected_sha256=expected)
    await run(h, "key", keys=["ctrl", "s"])
    result = await run(h, "verify_saved_file", saved_file="draft.txt", expected_sha256=expected)
    assert result["verification"] == "native_save_file_sha256"
    assert result["gui_save_dispatched"] is True
    assert h.hands.calls == [("type", " edited"), ("key", "ctrl", "s")]
    (h.workspace / "draft.txt").write_text("external edit", encoding="utf-8")
    with pytest.raises(ValueError, match="do not match"):
        await run(h, "verify_saved_file", saved_file="draft.txt", expected_sha256=expected)


async def test_notepad_editor_only_and_no_automatic_dialog_input(harness: SimpleNamespace) -> None:
    h = harness
    with pytest.raises(ValueError, match="foreground"):
        await run(h, "type", text="do not type into a browser")
    await run(h, "open_app", path="draft.txt")
    h.identity["class_name"] = "#32770"
    with pytest.raises(ValueError, match="dialog"):
        await run(h, "type", text="outside.txt")
    assert h.hands.calls == []


async def test_file_explorer_can_open_only_workspace_directories(harness: SimpleNamespace) -> None:
    h = harness
    with pytest.raises(ValueError, match="directories"):
        await run(h, "open_app", app="explorer", path="draft.txt")
    (h.workspace / "materials").mkdir()
    await run(h, "open_app", app="explorer", path="materials")
    assert h.launches == [("explorer", h.workspace / "materials")]
    await run(h, "key", app="explorer", keys=["alt", "left"])
    assert h.hands.calls == [("key", "alt", "left")]


async def test_native_input_uses_pixel_coordinates_with_normalized_host(
    harness: SimpleNamespace,
) -> None:
    h = harness
    await run(h, "open_app", path="draft.txt")
    h.link.settings.coords = "norm1000"
    await run(h, "click", x=320, y=240, label="Document body")
    assert h.hands.calls == [("click", 320.0, 240.0, "left", 1)]
    with pytest.raises(ValueError, match="outside"):
        await run(h, "click", x=640, y=240, label="Outside picture")


async def test_click_hits_physical_point_after_approval_and_before_real_input(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    await run(h, "open_app", path="draft.txt")
    h.raw.update(width=320, height=240)
    h.link.settings.coords = "norm1000"
    checked: list[tuple[float, float]] = []

    def covered(_state: dict[str, Any], x: float, y: float) -> bool:
        checked.append((x, y))
        return False

    monkeypatch.setattr(native, "_click_target_matches", covered)
    with pytest.raises(ValueError, match="click target is covered"):
        await run(h, "click", x=160, y=120, label="Document body")
    assert checked == [(320.0, 240.0)]
    assert h.hands.calls == []


@pytest.mark.parametrize(
    "scope, hit, process, root, child, permitted",
    [
        ("focused_editor", 5, 4, 3, True, True),
        ("focused_editor", 6, 4, 3, True, False),
        ("focused_editor", 5, 99, 3, True, False),
        ("focused_editor", 90, 4, 90, False, False),
        ("client", 5, 4, 3, True, True),
        ("client", 3, 4, 3, False, True),
        ("client", 5, 4, 3, False, False),
        ("client", 90, 4, 90, False, False),
        ("client", 5, 99, 3, True, False),
        ("client", 0, 4, 3, True, False),
    ],
)
def test_click_hit_test_rejects_overlays_other_controls_and_other_processes(
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    hit: int,
    process: int,
    root: int,
    child: bool,
    permitted: bool,
) -> None:
    import ctypes

    class Function:
        def __init__(self, callback: Any):
            self.callback = callback

        def __call__(self, *args: Any) -> Any:
            return self.callback(*args)

    checked: list[tuple[int, int]] = []

    def at(point: Any) -> int:
        checked.append((point.x, point.y))
        return hit

    def thread(_hwnd: int, pointer: Any) -> int:
        pointer._obj.value = process
        return 20

    def ancestor(hwnd: int, flags: int) -> int:
        assert (hwnd, flags) == (hit, 2)
        return root

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(
            user32=SimpleNamespace(
                WindowFromPoint=Function(at),
                GetWindowThreadProcessId=Function(thread),
                GetAncestor=Function(ancestor),
                IsChild=Function(lambda main, hwnd: main == 3 and hwnd == hit and child),
            )
        ),
        raising=False,
    )
    state = {
        "app": "notepad" if scope == "focused_editor" else "explorer",
        "fingerprint_scope": scope,
        "foreground_window": 3,
        "focus_window": 5,
        "process_id": 4,
    }
    assert verified_click_target(state, 320.25, 240.75) is permitted
    assert checked == [(320, 240)]


async def test_typing_stops_after_current_chunk_when_focus_changes(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    opened = await run(h, "open_app", path="draft.txt")
    calls = []

    def type_and_switch(text: str) -> None:
        calls.append(text)
        h.identity["foreground_window"] = 99

    monkeypatch.setattr(h.hands, "type", type_and_switch)
    with pytest.raises(ValueError, match="nanomuse:stop"):
        await h.actions.execute(
            NativeSpec(
                action="type",
                text="abcdefghijklmnop",
                expected_observation=opened["observation"]["observation_id"],
            )
        )
    assert calls == ["abcdefgh"]


async def test_cancelled_typing_thread_cannot_continue_after_manual_takeover(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    opened = await run(h, "open_app", path="draft.txt")
    started, release, stopped = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def blocked_type(text: str) -> None:
        calls.append(text)
        started.set()
        release.wait(timeout=2)
        stopped.set()

    monkeypatch.setattr(h.hands, "type", blocked_type)
    worker = asyncio.create_task(
        h.actions.execute(
            NativeSpec(
                action="type",
                text="abcdefghijklmnop",
                expected_observation=opened["observation"]["observation_id"],
            )
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    release.set()
    assert await asyncio.to_thread(stopped.wait, 2)
    await asyncio.sleep(0.05)
    assert calls == ["abcdefgh"]
    assert h.actions._observation is None


async def test_disabled_or_missing_native_backend_is_honest(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="linux"))
    with pytest.raises(ValueError, match="require Windows"):
        await h.actions.observe()
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(h.link, "backend_available", lambda: False)
    with pytest.raises(ValueError, match="unavailable"):
        await h.actions.observe()
    h.service.app.tools.remove("computer_act")
    with pytest.raises(ValueError, match="Enable computer use"):
        await h.actions.observe()


def test_native_input_risk_is_sensitive_and_reads_remain_safe(harness: SimpleNamespace) -> None:
    h = harness
    assert h.actions.assess(NativeSpec(action="observe")).risk == RiskLevel.SAFE
    for payload in (
        {"action": "type", "text": "edit"},
        {"action": "key", "keys": ["ctrl", "s"]},
        {"action": "click", "x": 1, "y": 1, "label": "Save"},
    ):
        assessment = h.actions.assess(NativeSpec(expected_observation="a" * 64, **payload))
        assert assessment.risk == RiskLevel.SENSITIVE
        assert assessment.reads_private_data


@pytest.mark.parametrize(
    ("activation", "reported", "succeeds"),
    [
        ("delayed", True, True),
        ("delayed", False, True),
        ("attached", False, True),
        ("never", True, False),
    ],
)
def test_activation_waits_for_exact_foreground_and_detaches_both_queues(
    monkeypatch: pytest.MonkeyPatch, activation: str, reported: bool, succeeds: bool
) -> None:
    import ctypes

    class Function:
        def __init__(self, callback: Any):
            self.callback = callback

        def __call__(self, *args: Any) -> Any:
            return self.callback(*args)

    clock = SimpleNamespace(now=0.0)
    calls: list[tuple[Any, ...]] = []
    attached: set[int] = set()
    activates_at: list[float] = []

    def foreground() -> int:
        return 3 if activates_at and clock.now >= activates_at[0] else 99

    def activate(hwnd: int) -> int:
        calls.append(("activate", hwnd))
        if activation == "delayed" or (activation == "attached" and attached == {20, 30}):
            activates_at[:] = [clock.now + 0.1]
        return int(reported)

    def thread(hwnd: int, process: Any) -> int:
        if process is not None:
            process._obj.value = 7
        return 20 if hwnd == 3 else 30

    def rectangle(_hwnd: int, rect: Any) -> int:
        rect._obj.left, rect._obj.top, rect._obj.right, rect._obj.bottom = 0, 0, 640, 480
        return 1

    def string(value: str, buffer: Any) -> int:
        buffer.value = value
        return len(value)

    def attach(_current: int, other: int, enabled: bool) -> int:
        calls.append(("attach", other, enabled))
        attached.add(other) if enabled else attached.discard(other)
        return 1

    user32 = SimpleNamespace(
        IsWindow=Function(lambda _hwnd: 1),
        IsIconic=Function(lambda _hwnd: 0),
        GetWindowThreadProcessId=Function(thread),
        GetWindowRect=Function(rectangle),
        GetClassNameW=Function(lambda _hwnd, buffer, _size: string("Notepad", buffer)),
        GetWindowTextW=Function(lambda _hwnd, buffer, _size: string("draft.txt - Notepad", buffer)),
        SetForegroundWindow=Function(activate),
        GetForegroundWindow=Function(foreground),
        AttachThreadInput=Function(attach),
        PeekMessageW=Function(lambda *_args: 0),
        SendMessageTimeoutW=Function(lambda *_args: 1),
    )
    kernel32 = SimpleNamespace(GetCurrentThreadId=Function(lambda: 10))
    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(user32=user32, kernel32=kernel32), raising=False
    )
    monkeypatch.setattr(
        native,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.now,
            sleep=lambda seconds: setattr(clock, "now", clock.now + seconds),
        ),
    )
    expected = {
        "app": "notepad",
        "foreground_window": 3,
        "process_id": 7,
        "class_name": "Notepad",
        "window_title": "draft.txt - Notepad",
        "window_rect": [0, 0, 640, 480],
    }
    if succeeds:
        restore_native_window(expected)
        assert foreground() == 3
        assert clock.now >= 0.1  # Even a true API result must wait for activation.
    else:
        with pytest.raises(ValueError, match="could not reactivate"):
            restore_native_window(expected)
        assert foreground() == 99
    if activation == "delayed":
        assert not any(call[0] == "attach" for call in calls)
    else:
        assert [call for call in calls if call[0] == "attach"] == [
            ("attach", 30, True),
            ("attach", 20, True),
            ("attach", 20, False),
            ("attach", 30, False),
        ]
    assert attached == set()
    assert clock.now < 1.2
