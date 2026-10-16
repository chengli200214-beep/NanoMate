"""Verified workspace excerpts with durable publication receipts.

This adapter extracts bounded passages from explicitly supplied text files. It
does not ask a model to summarise them. The coordinator guards each execution
through the Sentinel before calling this module.
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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SOURCE_BYTES = 262_144
MAX_TOTAL_BYTES = 1_048_576
MAX_EXCERPT_CHARS = 4_000
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{index}" for prefix in ("COM", "LPT") for index in range(1, 10)
}


def _relative(value: str) -> str:
    """Use portable relative paths, excluding Windows drives and device paths."""
    value = value.replace("\\", "/")
    parts = value.split("/")
    if (
        not value
        or len(value) > 1024
        or PureWindowsPath(value).drive
        or value.startswith(("/", "~"))
        or any(char in value for char in '\x00\r\n:*?"<>|')
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.endswith((".", " ")) for part in parts)
        or any(part.split(".", 1)[0].upper() in _RESERVED_NAMES for part in parts)
    ):
        raise ValueError("Document paths must be ordinary relative workspace paths")
    return value


class DocumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_paths: list[str] = Field(min_length=1, max_length=10)
    output_name: str = Field(default="digest.md", min_length=1, max_length=100)
    notes: str = Field(default="", max_length=12_000)
    source_hashes: dict[str, str] = Field(default_factory=dict)

    @field_validator("source_paths")
    @classmethod
    def sources(cls, values: list[str]) -> list[str]:
        paths = [_relative(value) for value in values]
        if len({value.casefold() for value in paths}) != len(paths):
            raise ValueError("Document source paths must be unique")
        if any(Path(value).suffix.lower() not in {".md", ".txt"} for value in paths):
            raise ValueError("Document sources must be UTF-8 .md or .txt files")
        return paths

    @field_validator("output_name")
    @classmethod
    def name(cls, value: str) -> str:
        value = _relative(value)
        if "/" in value or Path(value).suffix.lower() != ".md":
            raise ValueError("Document output_name must be a .md basename")
        return value

    @field_validator("source_hashes")
    @classmethod
    def hashes(cls, values: dict[str, str]) -> dict[str, str]:
        if any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in values.values()):
            raise ValueError("Document source hashes must be SHA-256 hex values")
        return {_relative(key): value for key, value in values.items()}


def _redirect(path: Path) -> bool:
    details = path.lstat()
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
    )


def _root(workspace: Path) -> Path:
    root = workspace.absolute()
    if not root.is_dir() or _redirect(root):
        raise ValueError("Document workspace must be an ordinary directory")
    # System ancestors can have aliases, such as /var on macOS. Resolve those
    # once; the workspace itself and all paths beneath it must remain ordinary.
    return root.resolve(strict=True)


def _safe_path(root: Path, relative: str, *, missing: bool = False) -> Path:
    candidate = root / _relative(relative)
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        try:
            if _redirect(current):
                raise ValueError("Document paths cannot contain symbolic links or junctions")
        except FileNotFoundError:
            if not missing:
                raise ValueError("Document source file is unavailable") from None
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Document path is outside the workspace")
    return candidate


def _read_file(root: Path, relative: str, limit: int) -> bytes:
    path = _safe_path(root, relative)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Document file must be an ordinary file")
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
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError("Document file is not an ordinary file within the size limit")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        current = _safe_path(root, relative).stat()
        if (
            len(data) > limit
            or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
            or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        ):
            raise ValueError("Document file changed while it was being read")
        return data
    except OSError as exc:
        raise ValueError("Document file could not be read safely") from exc


def _sources(spec: DocumentSpec, root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    total = 0
    for relative in spec.source_paths:
        data = _read_file(root, relative, MAX_SOURCE_BYTES)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Document sources exceed the total size limit")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Document sources must contain UTF-8 text") from exc
        if "\x00" in text:
            raise ValueError("Document sources must contain text rather than binary data")
        result[relative] = data
    return result


def prepare(spec: DocumentSpec | dict[str, Any], workspace: Path) -> DocumentSpec:
    """Freeze source hashes at plan creation, without publishing an artifact."""
    parsed = DocumentSpec.model_validate(spec)
    sources = _sources(parsed, _root(workspace))
    hashes = {path: hashlib.sha256(data).hexdigest() for path, data in sources.items()}
    if parsed.source_hashes and parsed.source_hashes != hashes:
        raise ValueError("Document source contents changed; review a new plan")
    return parsed.model_copy(update={"source_hashes": hashes})


def _literal(text: str) -> str:
    fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", text)), default=3))
    return f"{fence}text\n{text}\n{fence}"


def _render(spec: DocumentSpec, sources: dict[str, bytes]) -> bytes:
    parts = [
        "# 资料摘录 / Extractive digest",
        "按来源顺序摘录各文件开头的文字，每个来源最多 4000 字符。"
        "这是原文摘录，不是语义总结。\n\n"
        "Passages are copied from the start of each source, in source order, up to 4000 "
        "characters per source. This is an extractive digest, not a semantic summary.",
    ]
    if spec.notes:
        parts.extend(["## 用户备注 / User notes", _literal(spec.notes)])
    for index, (path, data) in enumerate(sources.items(), 1):
        text = data.decode("utf-8-sig")
        parts.extend(
            [
                f"## 来源 / Source {index}",
                _literal(path),
                f"SHA-256: `{spec.source_hashes[path]}`",
                _literal(text[:MAX_EXCERPT_CHARS]),
            ]
        )
        if len(text) > MAX_EXCERPT_CHARS:
            parts.append("该来源的后续文字未收录。 / The remaining source text is not included.")
    return ("\n\n".join(parts) + "\n").encode("utf-8")


class DocumentArtifacts:
    def __init__(self, data_dir: Path, workspace: Path):
        self.workspace = _root(workspace)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "assistant-artifacts.sqlite"
        with self._transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("Unsupported document artifact schema")
            db.execute(
                "CREATE TABLE IF NOT EXISTS artifact ("
                "id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, "
                "payload TEXT NOT NULL, metadata TEXT NOT NULL, content BLOB NOT NULL, "
                "published INTEGER NOT NULL DEFAULT 0)"
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

    def execute(
        self,
        spec: DocumentSpec | dict[str, Any],
        request_key: str,
        task_id: str,
        revision: int,
        step_id: str,
    ) -> dict[str, Any]:
        parsed = DocumentSpec.model_validate(spec)
        if not request_key or len(request_key) > 512 or revision < 1 or not task_id or not step_id:
            raise ValueError("Document execution requires a request key and task revision")
        if set(parsed.source_hashes) != set(parsed.source_paths):
            raise ValueError("Document sources must be prepared before execution")
        artifact_id = hashlib.sha256(request_key.encode("utf-8")).hexdigest()[:32]
        payload = json.dumps(
            [parsed.model_dump(), task_id, revision, step_id], sort_keys=True, ensure_ascii=False
        )
        with self._transaction() as db:
            row = db.execute("SELECT * FROM artifact WHERE id=?", (artifact_id,)).fetchone()
            if row is not None:
                if row["request_key"] != request_key or row["payload"] != payload:
                    raise ValueError("Document request key was reused with different inputs")
            else:
                sources = _sources(parsed, self.workspace)
                hashes = {path: hashlib.sha256(data).hexdigest() for path, data in sources.items()}
                if hashes != parsed.source_hashes:
                    raise ValueError("Document source contents changed; review a new plan")
                content = _render(parsed, sources)
                metadata: dict[str, Any] = {
                    "id": artifact_id,
                    "path": f"assistant-artifacts/{artifact_id}/{parsed.output_name}",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "source_hashes": hashes,
                    "kind": "extractive_digest",
                    "task_id": task_id,
                    "revision": revision,
                    "step_id": step_id,
                }
                target = _safe_path(self.workspace, metadata["path"], missing=True)
                if target.exists():
                    raise ValueError("Document output already exists without a publication receipt")
                db.execute(
                    "INSERT INTO artifact(id,request_key,payload,metadata,content) VALUES(?,?,?,?,?)",
                    (artifact_id, request_key, payload, json.dumps(metadata), content),
                )
        # The intent is committed before publication. A restart can verify a file
        # published just before a crash, or publish the persisted bytes once.
        with self._transaction() as db:
            row = db.execute("SELECT * FROM artifact WHERE id=?", (artifact_id,)).fetchone()
            assert row is not None
            metadata = json.loads(row["metadata"])
            target = _safe_path(self.workspace, metadata["path"], missing=True)
            if row["published"]:
                return self._verified(metadata)
            if target.exists():
                self._verified(metadata)
            else:
                self._publish(metadata["path"], bytes(row["content"]))
                self._verified(metadata)
            self._mark_published(db, artifact_id)
            return {**metadata, "verified": True}

    def _publish(self, relative: str, content: bytes) -> None:
        target = _safe_path(self.workspace, relative, missing=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = _safe_path(self.workspace, relative, missing=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=".publish-", dir=target.parent)
        temporary = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _safe_path(self.workspace, relative, missing=True)
            # Hard-link publication is atomic and refuses to replace an existing
            # destination on both Windows and POSIX. No overwrite fallback is used.
            os.link(temporary, target, follow_symlinks=False)
            if os.name != "nt":
                directory = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError as exc:
            raise ValueError(
                "Document artifact could not be published without overwriting"
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _mark_published(self, db: sqlite3.Connection, artifact_id: str) -> None:
        db.execute("UPDATE artifact SET published=1 WHERE id=?", (artifact_id,))

    def _verified(self, metadata: dict[str, Any], *, content: bool = False) -> dict[str, Any]:
        data = _read_file(self.workspace, metadata["path"], MAX_TOTAL_BYTES)
        if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise ValueError("Document artifact contents changed; verification failed")
        result = {**metadata, "verified": True}
        if content:
            result["content"] = data.decode("utf-8")
        return result

    def read(self, artifact_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
            raise ValueError("Invalid document artifact ID")
        with self._transaction() as db:
            row = db.execute("SELECT * FROM artifact WHERE id=?", (artifact_id,)).fetchone()
            if row is None or not row["published"]:
                raise ValueError("Document artifact is not available")
            return self._verified(json.loads(row["metadata"]), content=True)
