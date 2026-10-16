from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nanomuse.assistant.file_actions import FileActions, FileSpec, OutcomeUnknown, prepare


@pytest.fixture
def files(tmp_path: Path) -> FileActions:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return FileActions(tmp_path / "state", workspace)


def execute(files: FileActions, spec: FileSpec, key: str = "step-1") -> dict[str, Any]:
    return files.execute(spec, key, "task-1", 1, "step-1")


def test_prepare_is_a_read_only_preview(files: FileActions) -> None:
    (files.workspace / "source.txt").write_bytes(b"source")
    spec = prepare(
        {"action": "copy", "source_path": "source.txt", "path": "target.txt"}, files.workspace
    )
    assert spec.target_absent
    assert spec.source_sha256 == hashlib.sha256(b"source").hexdigest()
    assert not (files.workspace / "target.txt").exists()
    assert files._row("step-1") is None


def test_write_receipt_survives_restart_and_revalidates_actual_bytes(files: FileActions) -> None:
    spec = prepare({"action": "write", "path": "new.txt", "content": "待办\n"}, files.workspace)
    result = execute(files, spec)
    assert (files.workspace / "new.txt").read_text(encoding="utf-8") == "待办\n"
    assert result["verification"]["sha256"] == hashlib.sha256("待办\n".encode()).hexdigest()
    restarted = FileActions(files.path.parent, files.workspace)
    assert execute(restarted, spec) == result
    (files.workspace / "new.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(OutcomeUnknown):
        execute(restarted, spec)
    assert restarted.cancel_evidence("step-1")[0] == "needs_attention"


def test_copy_uses_frozen_source_and_independent_target_inode(files: FileActions) -> None:
    source = files.workspace / "source.bin"
    source.write_bytes(b"\x00source\xff")
    spec = prepare(
        {"action": "copy", "source_path": "source.bin", "path": "target.bin"}, files.workspace
    )
    result = execute(files, spec)
    assert result["verification"]["size_bytes"] == 8
    assert source.stat().st_ino != (files.workspace / "target.bin").stat().st_ino
    source.write_bytes(b"later")
    assert execute(files, spec) == result
    assert (files.workspace / "target.bin").read_bytes() == b"\x00source\xff"


def test_changed_source_is_rejected_before_intent(files: FileActions) -> None:
    source = files.workspace / "source.txt"
    source.write_text("before", encoding="utf-8")
    spec = prepare(
        {"action": "move", "source_path": "source.txt", "path": "target.txt"}, files.workspace
    )
    source.write_text("after", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        execute(files, spec)
    assert not (files.workspace / "target.txt").exists()
    assert files._row("step-1") is None


@pytest.mark.parametrize("action", ["write", "copy", "move", "mkdir"])
def test_target_created_after_preview_is_never_overwritten(files: FileActions, action: str) -> None:
    (files.workspace / "source").write_bytes(b"source")
    raw: dict[str, Any] = {"action": action, "path": "target"}
    if action in {"copy", "move"}:
        raw["source_path"] = "source"
    spec = prepare(raw, files.workspace)
    (files.workspace / "target").write_bytes(b"existing")
    with pytest.raises(ValueError, match="target already exists"):
        execute(files, spec)
    assert (files.workspace / "target").read_bytes() == b"existing"
    assert (files.workspace / "source").read_bytes() == b"source"


def test_move_verifies_both_target_hash_and_source_absence(files: FileActions) -> None:
    (files.workspace / "source").write_bytes(b"move me")
    spec = prepare({"action": "move", "source_path": "source", "path": "target"}, files.workspace)
    result = execute(files, spec)
    assert result["verification"]["source_absent"]
    assert not (files.workspace / "source").exists()
    assert (files.workspace / "target").read_bytes() == b"move me"
    assert files.cancel_evidence("step-1") == ("completed", result)
    (files.workspace / "source").write_bytes(b"replacement")
    with pytest.raises(OutcomeUnknown, match="source removal"):
        execute(files, spec)
    assert (files.workspace / "source").read_bytes() == b"replacement"


def test_interrupted_partial_move_never_removes_source_on_replay(
    files: FileActions, monkeypatch: pytest.MonkeyPatch
) -> None:
    (files.workspace / "source").write_bytes(b"move me")
    spec = prepare({"action": "move", "source_path": "source", "path": "target"}, files.workspace)
    real_unlink = Path.unlink

    def crash(path: Path, *args: Any, **kwargs: Any) -> None:
        if path.name == "source":
            raise KeyboardInterrupt("simulated process crash")
        real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", crash)
        with pytest.raises(KeyboardInterrupt):
            execute(files, spec)
    restarted = FileActions(files.path.parent, files.workspace)
    assert restarted.cancel_evidence("step-1")[0] == "needs_attention"
    with pytest.raises(OutcomeUnknown):
        execute(restarted, spec)
    assert (files.workspace / "source").read_bytes() == b"move me"
    assert (files.workspace / "target").read_bytes() == b"move me"


def test_interrupted_write_can_acknowledge_published_inode_without_rewriting(
    files: FileActions, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = prepare({"action": "write", "path": "target", "content": "saved"}, files.workspace)
    with monkeypatch.context() as context:
        context.setattr(files, "_complete", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            execute(files, spec)
    target = files.workspace / "target"
    before = target.stat()
    restarted = FileActions(files.path.parent, files.workspace)
    result = execute(restarted, spec)
    assert result["status"] == "completed"
    assert target.stat().st_ino == before.st_ino
    assert target.stat().st_mtime_ns == before.st_mtime_ns


def test_attempt_with_missing_target_is_unknown_and_is_not_replayed(
    files: FileActions, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = prepare({"action": "write", "path": "target", "content": "saved"}, files.workspace)
    with monkeypatch.context() as context:
        context.setattr(os, "link", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            execute(files, spec)
    with pytest.raises(OutcomeUnknown):
        execute(FileActions(files.path.parent, files.workspace), spec)
    assert not (files.workspace / "target").exists()


def test_target_collision_during_publication_never_overwrites(
    files: FileActions, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = prepare({"action": "write", "path": "target", "content": "planned"}, files.workspace)
    original_link = os.link

    def collision(source: Path, target: Path) -> None:
        target.write_bytes(b"concurrent user file")
        original_link(source, target)

    monkeypatch.setattr(os, "link", collision)
    with pytest.raises(OutcomeUnknown):
        execute(files, spec)
    assert (files.workspace / "target").read_bytes() == b"concurrent user file"


def test_interrupted_completed_move_is_acknowledged_without_repeating_input(
    files: FileActions, monkeypatch: pytest.MonkeyPatch
) -> None:
    (files.workspace / "source").write_bytes(b"move me")
    spec = prepare({"action": "move", "source_path": "source", "path": "target"}, files.workspace)
    with monkeypatch.context() as context:
        context.setattr(files, "_complete", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            execute(files, spec)
    before = (files.workspace / "target").stat()
    result = execute(FileActions(files.path.parent, files.workspace), spec)
    assert result["verification"]["source_absent"]
    assert (files.workspace / "target").stat().st_mtime_ns == before.st_mtime_ns
    assert not (files.workspace / "source").exists()


def test_cancel_is_read_only_even_if_publication_needs_acknowledgement(
    files: FileActions, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = prepare({"action": "write", "path": "target", "content": "saved"}, files.workspace)
    with monkeypatch.context() as context:
        context.setattr(files, "_complete", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            execute(files, spec)
    before = files.path.read_bytes()
    workspace_before = {p.name: p.stat().st_mtime_ns for p in files.workspace.iterdir()}
    assert files.cancel_evidence("step-1")[0] == "completed"
    assert files.path.read_bytes() == before
    assert {p.name: p.stat().st_mtime_ns for p in files.workspace.iterdir()} == workspace_before


def test_mkdir_replay_checks_directory_identity(files: FileActions) -> None:
    spec = prepare({"action": "mkdir", "path": "folder"}, files.workspace)
    result = execute(files, spec)
    assert result["verification"]["kind"] == "directory_identity"
    (files.workspace / "folder" / "later.txt").write_bytes(b"later")
    assert execute(files, spec) == result
    (files.workspace / "folder").rename(files.workspace / "old-folder")
    (files.workspace / "folder").mkdir()
    with pytest.raises(OutcomeUnknown):
        execute(files, spec)


def test_mkdir_crash_before_identity_receipt_requires_review(
    files: FileActions, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = prepare({"action": "mkdir", "path": "folder"}, files.workspace)
    original = Path.mkdir

    def create_then_crash(path: Path, *args: Any, **kwargs: Any) -> None:
        original(path, *args, **kwargs)
        if path.name == "folder":
            raise KeyboardInterrupt("simulated process crash")

    with monkeypatch.context() as context:
        context.setattr(Path, "mkdir", create_then_crash)
        with pytest.raises(KeyboardInterrupt):
            execute(files, spec)
    assert (files.workspace / "folder").is_dir()
    assert files.cancel_evidence("step-1")[0] == "needs_attention"


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/outside",
        "C:\\outside",
        "\\\\host\\share",
        "a/../b",
        "a//b",
        "a/./b",
        "a/",
        "NUL.txt",
        "COM1",
        "lpt¹.txt",
        "a:stream",
        "a.",
        "a ",
        "\\\\?\\C:\\outside",
        "a\nfile",
        "CONIN$",
        "~home/a",
    ],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValidationError):
        FileSpec(action="write", path=path)


def test_missing_parent_must_be_a_separate_planned_mkdir(files: FileActions) -> None:
    with pytest.raises(ValueError, match="parent is unavailable"):
        prepare({"action": "write", "path": "missing/new.txt"}, files.workspace)
    assert not (files.workspace / "missing").exists()


def test_invalid_mixed_arguments_and_unprepared_execution(files: FileActions) -> None:
    with pytest.raises(ValidationError):
        FileSpec(action="copy", path="target", source_path="source", content="text")
    with pytest.raises(ValidationError):
        FileSpec(action="write", path="target", content="text", content_from_step=0)
    with pytest.raises(ValueError, match="must be prepared"):
        execute(files, FileSpec(action="write", path="target"))
    pending = prepare(
        {"action": "write", "path": "target", "content_from_step": 0}, files.workspace
    )
    with pytest.raises(ValueError, match="references resolved"):
        execute(files, pending)


def test_deduplication_key_cannot_be_reused_with_different_inputs(files: FileActions) -> None:
    first = prepare({"action": "write", "path": "target", "content": "first"}, files.workspace)
    execute(files, first)
    changed = first.model_copy(update={"content": "different"})
    with pytest.raises(ValueError, match="reused"):
        execute(files, changed)
    assert (files.workspace / "target").read_text(encoding="utf-8") == "first"


def test_same_bytes_from_a_replacement_inode_do_not_prove_publication(files: FileActions) -> None:
    spec = prepare({"action": "write", "path": "target", "content": "saved"}, files.workspace)
    execute(files, spec)
    target = files.workspace / "target"
    target.rename(files.workspace / "original")
    target.write_text("saved", encoding="utf-8")
    with pytest.raises(OutcomeUnknown):
        execute(files, spec)


def test_unknown_schema_is_rejected(files: FileActions) -> None:
    with sqlite3.connect(files.path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        FileActions(files.path.parent, files.workspace)


def test_symlink_parent_or_source_is_rejected(files: FileActions, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_bytes(b"private")
    link = files.workspace / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symbolic links requires privileges on this Windows host")
    with pytest.raises(ValueError, match="symbolic links"):
        prepare({"action": "write", "path": "link/new"}, files.workspace)
    with pytest.raises(ValueError, match="symbolic links"):
        prepare({"action": "copy", "source_path": "link/file", "path": "new"}, files.workspace)
    assert not (outside / "new").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction safety")
def test_windows_junction_is_rejected(files: FileActions, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = files.workspace / "link"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    try:
        with pytest.raises(ValueError, match="symbolic links or junctions"):
            prepare({"action": "write", "path": "link/new"}, files.workspace)
        assert not (outside / "new").exists()
    finally:
        link.rmdir()
