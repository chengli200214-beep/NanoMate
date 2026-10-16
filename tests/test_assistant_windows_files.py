"""Windows exclusive file handles, using disposable files and no GUI automation."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any

import pytest

from nanomuse.assistant.file_actions import FileActions, FileSpec, OutcomeUnknown, prepare

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing modes")


@contextmanager
def exclusively_locked(path: Path) -> Iterator[None]:
    """Hold an existing test file with FILE_SHARE_NONE, including delete denial."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ | GENERIC_WRITE, no sharing, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL.
    handle = create(str(path), 0xC0000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not close(handle):
            raise ctypes.WinError(ctypes.get_last_error())


@pytest.fixture
def files(tmp_path: Path) -> FileActions:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return FileActions(tmp_path / "state", workspace)


def run(files: FileActions, spec: FileSpec, revision: int = 1) -> dict[str, Any]:
    return files.execute(spec, f"request-{revision}", "task-1", revision, f"step-{revision}")


@pytest.mark.parametrize("action", ["copy", "move"])
def test_exclusive_source_lock_rejects_execution_without_deleting_or_copying(
    files: FileActions, action: str
) -> None:
    source = files.workspace / "source.bin"
    source.write_bytes(b"exclusive source bytes\x00\xff")
    value = prepare(
        {"action": action, "source_path": "source.bin", "path": "target.bin"}, files.workspace
    )
    with exclusively_locked(source):
        # Verify that this is a real kernel sharing violation, rather than a stubbed
        # Python permission error or an attribute that the adapter does not use.
        with pytest.raises(PermissionError):
            source.read_bytes()
        with pytest.raises(PermissionError):
            source.unlink()
        with pytest.raises(ValueError, match="could not be read safely"):
            run(files, value)
        assert source.exists()
        assert not (files.workspace / "target.bin").exists()
        assert files._row("request-1") is None
        assert files.cancel_evidence("request-1")[0] == "cancelled"
    assert source.read_bytes() == b"exclusive source bytes\x00\xff"
    revised = prepare(value.model_copy(update={"path": "revised.bin"}), files.workspace)
    receipt = run(files, revised, revision=2)
    assert receipt["status"] == "completed"
    assert receipt["verification"]["kind"] == "file_sha256"
    assert (files.workspace / "revised.bin").read_bytes() == b"exclusive source bytes\x00\xff"
    assert source.exists() is (action == "copy")
    assert not (files.workspace / "target.bin").exists()


@pytest.mark.parametrize("action", ["write", "copy", "move"])
def test_exclusive_target_lock_never_overwrites_and_new_destination_succeeds(
    files: FileActions, action: str
) -> None:
    source = files.workspace / "source.bin"
    source.write_bytes(b"planned source")
    raw: dict[str, Any] = {"action": action, "path": "target.bin"}
    if action == "write":
        raw["content"] = "planned source"
    else:
        raw["source_path"] = "source.bin"
    value = prepare(raw, files.workspace)
    target = files.workspace / "target.bin"
    target.write_bytes(b"user target appeared after preview")
    before = target.stat().st_ino
    with exclusively_locked(target):
        with pytest.raises(PermissionError):
            target.read_bytes()
        with pytest.raises(ValueError, match="target already exists"):
            run(files, value)
        assert target.stat().st_ino == before
        assert source.read_bytes() == b"planned source"
        assert files._row("request-1") is None
    assert target.read_bytes() == b"user target appeared after preview"
    assert target.stat().st_ino == before
    revised = prepare(value.model_copy(update={"path": "revised.bin"}), files.workspace)
    receipt = run(files, revised, revision=2)
    assert receipt["status"] == "completed"
    assert (files.workspace / "revised.bin").read_bytes() == b"planned source"
    assert target.read_bytes() == b"user target appeared after preview"
    assert target.stat().st_ino == before
    assert source.exists() is (action != "move")


def test_exclusive_lock_after_move_link_keeps_both_files_and_never_replays_deletion(
    files: FileActions, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = files.workspace / "source.bin"
    target = files.workspace / "target.bin"
    source.write_bytes(b"move publication bytes")
    value = prepare(
        {"action": "move", "source_path": "source.bin", "path": "target.bin"}, files.workspace
    )
    verify = files._verify_target
    acquired = False
    with ExitStack() as locks:

        def verify_then_lock(row: dict[str, Any]) -> dict[str, Any]:
            nonlocal acquired
            result = verify(row)
            if not acquired:
                locks.enter_context(exclusively_locked(source))
                acquired = True
            return result

        with monkeypatch.context() as context:
            context.setattr(files, "_verify_target", verify_then_lock)
            with pytest.raises(OutcomeUnknown, match="outcome is unverified"):
                run(files, value)
        assert acquired
        assert source.exists() and target.exists()
        assert files._row("request-1")["state"] == "linked"  # type: ignore[index]
    # The lock ended, but the first request remains ambiguous. Replaying it must not
    # delete the original just because both hard links can now be read.
    assert source.read_bytes() == target.read_bytes() == b"move publication bytes"
    assert source.stat().st_ino == target.stat().st_ino
    assert files.cancel_evidence("request-1")[0] == "needs_attention"
    with pytest.raises(OutcomeUnknown, match="source removal is unverified"):
        run(files, value)
    assert source.exists() and target.exists()
    revised = prepare(value.model_copy(update={"path": "revised.bin"}), files.workspace)
    receipt = run(files, revised, revision=2)
    assert receipt["status"] == "completed"
    assert receipt["verification"]["source_absent"] is True
    assert not source.exists()
    assert (files.workspace / "revised.bin").read_bytes() == b"move publication bytes"
    # Human review of the old ambiguous action is still possible. A later revision
    # never silently deletes the old partially published target.
    assert target.read_bytes() == b"move publication bytes"
