"""Previewed, non-overwriting workspace operations with durable evidence.

The coordinator obtains Sentinel approval before executing this adapter. A
receipt proves the resulting file bytes, rather than merely recording that a
system call returned. Ambiguous interrupted moves require human review.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_WRITE_BYTES = 1024 * 1024
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"} | {
    f"{prefix}{index}" for prefix in ("COM", "LPT") for index in "123456789¹²³"
}


class OutcomeUnknown(ValueError):
    """An effect cannot be proved or safely replayed from the durable intent."""


def _relative(value: str) -> str:
    value = value.replace("\\", "/")
    parts = value.split("/")
    if (
        not value
        or len(value) > 1024
        or PureWindowsPath(value).drive
        or value.startswith(("/", "~"))
        or any(ord(char) < 32 or char in ':*?"<>|' for char in value)
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.endswith((".", " ")) for part in parts)
        or any(part.split(".", 1)[0].upper() in _RESERVED for part in parts)
    ):
        raise ValueError("File paths must be ordinary relative workspace paths")
    return value


class FileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["write", "copy", "move", "mkdir"]
    path: str = Field(min_length=1, max_length=1024)
    source_path: str = Field(default="", max_length=1024)
    content: str = Field(default="", max_length=MAX_WRITE_BYTES)
    content_from_step: int | None = Field(default=None, ge=0, le=29)
    source_sha256: str = ""
    target_absent: bool = False

    @field_validator("path")
    @classmethod
    def target_path(cls, value: str) -> str:
        return _relative(value)

    @field_validator("source_path")
    @classmethod
    def source(cls, value: str) -> str:
        return _relative(value) if value else value

    @field_validator("source_sha256")
    @classmethod
    def source_hash(cls, value: str) -> str:
        if value and not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("File source_sha256 must be a SHA-256 hex value")
        return value

    @model_validator(mode="after")
    def arguments(self) -> FileSpec:
        if len(self.content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ValueError("File text exceeds the write size limit")
        if self.action in {"copy", "move"}:
            if not self.source_path or self.content or self.content_from_step is not None:
                raise ValueError("Copy and move require only a source_path and target path")
            if self.path.casefold() == self.source_path.casefold():
                raise ValueError("File source and target must differ")
        elif self.source_path or self.source_sha256:
            raise ValueError("Write and mkdir cannot specify a source file")
        if self.action != "write" and (self.content or self.content_from_step is not None):
            raise ValueError("Only write accepts content")
        if self.content_from_step is not None and self.content:
            raise ValueError("A file write accepts content or a previous step reference")
        return self


def _redirect(path: Path) -> bool:
    details = path.lstat()
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def _identity(details: os.stat_result) -> list[int]:
    return [details.st_dev, details.st_ino]


def _root(workspace: Path) -> Path:
    root = workspace.absolute()
    if not root.is_dir() or _redirect(root):
        raise ValueError("File workspace must be an ordinary directory")
    return root.resolve(strict=True)


def _path(root: Path, relative: str, *, absent: bool = False) -> Path:
    candidate = root / _relative(relative)
    current = root
    for index, part in enumerate(candidate.relative_to(root).parts):
        current /= part
        try:
            if _redirect(current):
                raise ValueError("File paths cannot contain symbolic links or junctions")
            if current != candidate and not current.is_dir():
                raise ValueError("File parent must be an ordinary directory")
        except FileNotFoundError:
            if current != candidate or not absent:
                raise ValueError("File path or parent is unavailable") from None
        if index > 64:
            raise ValueError("File path is too deeply nested")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("File path is outside the workspace")
    return candidate


def _read(root: Path, relative: str) -> tuple[bytes, list[int]]:
    path = _path(root, relative)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                raise ValueError("File source must be an ordinary file within the size limit")
            data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = _path(root, relative).stat()
        if (
            len(data) > MAX_FILE_BYTES
            or _identity(before) != _identity(current)
            or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        ):
            raise ValueError("File source changed while it was being read")
        return data, _identity(current)
    except OSError as exc:
        raise ValueError("File source could not be read safely") from exc


def _missing(root: Path, relative: str) -> Path:
    path = _path(root, relative, absent=True)
    if path.exists():
        raise ValueError("File target already exists; review a different target")
    return path


def prepare(spec: FileSpec | dict[str, Any], workspace: Path) -> FileSpec:
    """Validate the preview and freeze source bytes without changing files."""
    parsed = FileSpec.model_validate(spec)
    root = _root(workspace)
    _missing(root, parsed.path)
    source_hash = ""
    if parsed.source_path:
        data, _ = _read(root, parsed.source_path)
        source_hash = hashlib.sha256(data).hexdigest()
        if parsed.source_sha256 and parsed.source_sha256 != source_hash:
            raise ValueError("File source changed; review a new plan")
    return parsed.model_copy(update={"source_sha256": source_hash, "target_absent": True})


class FileActions:
    def __init__(self, data_dir: Path, workspace: Path):
        self.workspace = _root(workspace)
        self.workspace_identity = _identity(self.workspace.stat())
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "assistant-files.sqlite3"
        with self._transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("Unsupported file action schema")
            db.execute(
                "CREATE TABLE IF NOT EXISTS file_operation ("
                "request_key TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL, "
                "identity TEXT NOT NULL DEFAULT '[]', temp_path TEXT NOT NULL DEFAULT '', "
                "sha256 TEXT NOT NULL DEFAULT '', size_bytes INTEGER NOT NULL DEFAULT 0, "
                "result TEXT NOT NULL DEFAULT '{}')"
            )
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _workspace(self) -> Path:
        root = _root(self.workspace)
        if _identity(root.stat()) != self.workspace_identity:
            raise OutcomeUnknown("File workspace changed; human review is required")
        return root

    def _row(self, request_key: str) -> dict[str, Any] | None:
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM file_operation WHERE request_key=?", (request_key,)
            ).fetchone()
        return dict(row) if row else None

    def execute(
        self,
        spec: FileSpec | dict[str, Any],
        request_key: str,
        task_id: str,
        revision: int,
        step_id: str,
    ) -> dict[str, Any]:
        parsed = FileSpec.model_validate(spec)
        if not request_key or len(request_key) > 512 or revision < 1 or not task_id or not step_id:
            raise ValueError("File execution requires a request key and task revision")
        if not parsed.target_absent or parsed.content_from_step is not None:
            raise ValueError("File operation must be prepared and content references resolved")
        if parsed.source_path and not parsed.source_sha256:
            raise ValueError("File source must be prepared before execution")
        payload = json.dumps(
            [parsed.model_dump(), task_id, revision, step_id], sort_keys=True, ensure_ascii=False
        )
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM file_operation WHERE request_key=?", (request_key,)
            ).fetchone()
            if row:
                if row["payload"] != payload:
                    raise ValueError("File request key was reused with different inputs")
                fresh = False
            else:
                prepare(parsed, self._workspace())
                db.execute(
                    "INSERT INTO file_operation(request_key,payload,state) VALUES(?,?,'staging')",
                    (request_key, payload),
                )
                fresh = True
        if not fresh:
            return self._recover(request_key)
        try:
            return self._perform(parsed, request_key)
        except Exception as exc:
            current = self._row(request_key)
            if current is not None and current["state"] in {"attempting", "linked", "completed"}:
                try:
                    return self._recover(request_key)
                except OutcomeUnknown:
                    raise OutcomeUnknown(
                        "File operation outcome is unverified; human review is required"
                    ) from exc
            with self._transaction() as db:
                db.execute(
                    "UPDATE file_operation SET state='rejected' "
                    "WHERE request_key=? AND state='staging'",
                    (request_key,),
                )
            raise

    def _perform(self, spec: FileSpec, request_key: str) -> dict[str, Any]:
        root = self._workspace()
        target = _missing(root, spec.path)
        if spec.action == "mkdir":
            self._attempt(request_key, [], "", "", 0)
            target = _missing(self._workspace(), spec.path)
            target.mkdir()
            details = _path(root, spec.path).stat()
            if not stat.S_ISDIR(details.st_mode):
                raise OutcomeUnknown("Created directory could not be verified")
            with self._transaction() as db:
                db.execute(
                    "UPDATE file_operation SET identity=? WHERE request_key=?",
                    (json.dumps(_identity(details)), request_key),
                )
            return self._complete(request_key)
        if spec.source_path:
            data, source_identity = _read(root, spec.source_path)
            if hashlib.sha256(data).hexdigest() != spec.source_sha256:
                raise ValueError("File source changed; review a new plan")
        else:
            data, source_identity = spec.content.encode("utf-8"), []
        sha256 = hashlib.sha256(data).hexdigest()
        if spec.action == "move":
            self._attempt(request_key, source_identity, "", sha256, len(data))
            source = _path(self._workspace(), spec.source_path)
            current_data, current_identity = _read(root, spec.source_path)
            if (
                current_identity != source_identity
                or hashlib.sha256(current_data).hexdigest() != sha256
            ):
                raise OutcomeUnknown("Move source changed before publication")
            target = _missing(self._workspace(), spec.path)
            os.link(source, target)
            self._verify_target(self._row_required(request_key))
            with self._transaction() as db:
                db.execute(
                    "UPDATE file_operation SET state='linked' WHERE request_key=?", (request_key,)
                )
            # Unlink only this verified source, never an interrupted move's replacement.
            current_data, current_identity = _read(root, spec.source_path)
            if (
                current_identity != source_identity
                or hashlib.sha256(current_data).hexdigest() != sha256
            ):
                raise OutcomeUnknown("Move source changed before removal")
            _path(self._workspace(), spec.source_path).unlink()
            return self._complete(request_key)
        descriptor, staged_name = tempfile.mkstemp(prefix=".assistant-file-", dir=target.parent)
        staged = Path(staged_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            staged_identity = _identity(os.fstat(stream.fileno()))
        staged_relative = staged.relative_to(root).as_posix()
        self._attempt(request_key, staged_identity, staged_relative, sha256, len(data))
        staged = _path(self._workspace(), staged_relative)
        target = _missing(self._workspace(), spec.path)
        os.link(staged, target)
        result = self._complete(request_key)
        self._cleanup(request_key)
        return result

    def _attempt(
        self, request_key: str, identity: list[int], temp_path: str, sha256: str, size_bytes: int
    ) -> None:
        with self._transaction() as db:
            result = db.execute(
                "UPDATE file_operation SET state='attempting',identity=?,temp_path=?,"
                "sha256=?,size_bytes=? WHERE request_key=? AND state='staging'",
                (json.dumps(identity), temp_path, sha256, size_bytes, request_key),
            )
            if result.rowcount != 1:
                raise OutcomeUnknown("Another executor owns this file operation")

    def _row_required(self, request_key: str) -> dict[str, Any]:
        row = self._row(request_key)
        if row is None:
            raise OutcomeUnknown("File operation intent is unavailable")
        return row

    def _verify_target(self, row: dict[str, Any]) -> dict[str, Any]:
        spec = FileSpec.model_validate(json.loads(row["payload"])[0])
        expected_identity = json.loads(row["identity"])
        try:
            root = self._workspace()
            if spec.action == "mkdir":
                path = _path(root, spec.path)
                details = path.stat()
                if not stat.S_ISDIR(details.st_mode) or _identity(details) != expected_identity:
                    raise ValueError("Directory identity changed")
                return {"kind": "directory_identity", "path": spec.path}
            data, identity = _read(root, spec.path)
            if (
                identity != expected_identity
                or hashlib.sha256(data).hexdigest() != row["sha256"]
                or len(data) != row["size_bytes"]
            ):
                raise ValueError("File target identity or bytes changed")
            return {
                "kind": "file_sha256",
                "path": spec.path,
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
            }
        except (OSError, ValueError) as exc:
            raise OutcomeUnknown("File target is unavailable or changed") from exc

    def _evidence(self, row: dict[str, Any]) -> dict[str, Any]:
        spec_data, task_id, revision, step_id = json.loads(row["payload"])
        spec = FileSpec.model_validate(spec_data)
        verification = self._verify_target(row)
        if spec.action == "move":
            try:
                source = _path(self._workspace(), spec.source_path, absent=True)
                if source.exists():
                    raise ValueError("Move source still exists")
            except (OSError, ValueError) as exc:
                raise OutcomeUnknown("Move source removal is unverified") from exc
            verification["source_absent"] = True
        return {
            "status": "completed",
            "operation": {
                "action": spec.action,
                "path": spec.path,
                "source_path": spec.source_path,
                "request_key": row["request_key"],
                "task_id": task_id,
                "revision": revision,
                "step_id": step_id,
            },
            "verification": verification,
        }

    def _complete(self, request_key: str) -> dict[str, Any]:
        evidence = self._evidence(self._row_required(request_key))
        with self._transaction() as db:
            db.execute(
                "UPDATE file_operation SET state='completed',result=? WHERE request_key=?",
                (json.dumps(evidence, ensure_ascii=False, sort_keys=True), request_key),
            )
        return evidence

    def _recover(self, request_key: str) -> dict[str, Any]:
        row = self._row_required(request_key)
        if row["state"] in {"staging", "rejected"}:
            raise ValueError("File operation did not publish a target; review a new plan")
        evidence = self._evidence(row)
        # Only acknowledge an already verified effect. Never publish or unlink on replay.
        if row["state"] != "completed":
            with self._transaction() as db:
                db.execute(
                    "UPDATE file_operation SET state='completed',result=? WHERE request_key=?",
                    (json.dumps(evidence, ensure_ascii=False, sort_keys=True), request_key),
                )
        return evidence

    def _cleanup(self, request_key: str) -> None:
        row = self._row_required(request_key)
        if not row["temp_path"]:
            return
        try:
            staged = _path(self._workspace(), row["temp_path"])
            if _identity(staged.stat()) == json.loads(row["identity"]):
                staged.unlink()
        except (OSError, ValueError):
            # The published receipt remains valid if housekeeping is unavailable.
            pass

    def cancel_evidence(self, request_key: str) -> tuple[str, dict[str, Any]]:
        """Inspect effects without completing an interrupted write or move."""
        row = self._row(request_key)
        if row is None or row["state"] in {"staging", "rejected"}:
            return "cancelled", {"cancelled_before_execution": True}
        try:
            return "completed", self._evidence(row)
        except OutcomeUnknown:
            return "needs_attention", {
                "outcome_unknown": True,
                "request_key": request_key,
                "reason": "File operation requires inspection; no action was replayed",
            }
