"""Bounded public-link downloads with non-overwriting, recoverable publication.

The browser adapter verifies the observed link and obtains Sentinel approval first.
This layer never runs page scripts, sends browser cookies or resumes an interrupted
network request. Recovery acknowledges only the exact file identity and bytes that
were recorded before publication.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from nanomuse.assistant.file_actions import (
    MAX_FILE_BYTES,
    OutcomeUnknown,
    _identity,
    _missing,
    _path,
    _read,
    _root,
)
from nanomuse.research.sources.dns import CloudflareResolver
from nanomuse.research.sources.errors import SourceError
from nanomuse.research.sources.security import PublicTransport, Resolver, validate_url
from nanomuse.tools.web import MAX_REDIRECTS, USER_AGENT

MAX_DOWNLOAD_BYTES = MAX_FILE_BYTES
MAX_DOWNLOAD_SECONDS = 120.0


def _origin(url: str) -> tuple[str, str, int]:
    try:
        parsed = validate_url(url)
    except SourceError as exc:
        raise ValueError(f"Download URL is unsafe: {exc}") from exc
    return (
        parsed.scheme,
        parsed.host,
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def configured_download_resolver() -> Resolver | None:
    """Select DNS only through an operator setting, never website or model content.

    The default is system DNS, with no fallback. Explicit cloudflare opt-in sends
    requested download hostnames to Cloudflare's fixed HTTPS DNS endpoint. The
    research module's DNS setting deliberately has no effect on this adapter.
    """
    mode = os.environ.get("NANOMUSE_ASSISTANT_DNS", "system").strip().lower()
    if mode == "system":
        return None
    if mode == "cloudflare":
        return CloudflareResolver()
    raise ValueError("NANOMUSE_ASSISTANT_DNS must be system or cloudflare.")


class BrowserDownloads:
    """A durable download intent, separate from the ledger's scheduling receipt."""

    def __init__(self, data_dir: Path, workspace: Path, *, resolver: Resolver | None = None):
        self._resolver = resolver if resolver is not None else configured_download_resolver()
        self.workspace = _root(workspace)
        self.workspace_identity = _identity(self.workspace.stat())
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "assistant-downloads.sqlite3"
        with self._transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("Unsupported browser download schema")
            db.execute(
                "CREATE TABLE IF NOT EXISTS download ("
                "request_key TEXT PRIMARY KEY,payload TEXT NOT NULL,url TEXT NOT NULL,"
                "state TEXT NOT NULL,temp_path TEXT NOT NULL DEFAULT '',"
                "identity TEXT NOT NULL DEFAULT '[]',sha256 TEXT NOT NULL DEFAULT '',"
                "size_bytes INTEGER NOT NULL DEFAULT 0,final_url TEXT NOT NULL DEFAULT '',"
                "content_type TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '{}')"
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
            raise OutcomeUnknown("Download workspace changed; human review is required")
        return root

    @staticmethod
    def _payload(
        spec: dict[str, Any], request_key: str, task_id: str, revision: int, step_id: str
    ) -> str:
        if not request_key or len(request_key) > 512 or not task_id or revision < 1 or not step_id:
            raise ValueError("Download requires a request key and task revision")
        if not spec.get("target_absent") or not spec.get("path"):
            raise ValueError("Download destination must be prepared before execution")
        return json.dumps([spec, task_id, revision, step_id], ensure_ascii=False, sort_keys=True)

    def _row(self, request_key: str) -> dict[str, Any] | None:
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM download WHERE request_key=?", (request_key,)
            ).fetchone()
        return dict(row) if row else None

    def recover(
        self, spec: dict[str, Any], request_key: str, task_id: str, revision: int, step_id: str
    ) -> dict[str, Any] | None:
        """Read existing evidence, or return None if no download intent exists.

        An unfinished request is never fetched again. A publication interrupted after
        linking can be recovered only when the destination's identity and bytes match.
        """
        payload = self._payload(spec, request_key, task_id, revision, step_id)
        row = self._row(request_key)
        if row is None:
            return None
        if row["payload"] != payload:
            raise ValueError("Download request key was reused with different inputs")
        if row["state"] not in {"publishing", "completed"}:
            raise OutcomeUnknown("Download was interrupted before publication; review a new plan")
        return self._complete(row)

    async def execute(
        self,
        spec: dict[str, Any],
        url: str,
        request_key: str,
        task_id: str,
        revision: int,
        step_id: str,
        check_running: Callable[[], None],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Download through pinned public DNS, including when transport is injected.

        Injected transports belong to their caller and are not closed here. Resolver
        and transport injection are internal test hooks, absent from every task schema.
        """
        payload = self._payload(spec, request_key, task_id, revision, step_id)
        recovered = self.recover(spec, request_key, task_id, revision, step_id)
        if recovered is not None:
            return recovered
        _origin(url)
        check_running()
        root = self._workspace()
        target = _missing(root, spec["path"])
        # Store the intent before sending any request. A second executor cannot create
        # another request while the first one is waiting for the network.
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO download(request_key,payload,url,state) VALUES(?,?,?,'fetching')",
                (request_key, payload, url),
            )
            if cursor.rowcount != 1:
                raise OutcomeUnknown("Another executor owns this download")
        staged: Path | None = None
        identity: list[int] = []
        try:
            descriptor, name = tempfile.mkstemp(prefix=".assistant-download-", dir=target.parent)
            staged = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                identity = _identity(os.fstat(stream.fileno()))
                with self._transaction() as db:
                    db.execute(
                        "UPDATE download SET temp_path=?,identity=? WHERE request_key=?",
                        (staged.relative_to(root).as_posix(), json.dumps(identity), request_key),
                    )
                async with asyncio.timeout(min(MAX_DOWNLOAD_SECONDS, max(1.0, timeout))):
                    size, digest, final_url, content_type = await self._fetch(
                        url, stream.write, check_running, timeout=timeout, transport=transport
                    )
                stream.flush()
                os.fsync(stream.fileno())
                if _identity(os.fstat(stream.fileno())) != identity:
                    raise OutcomeUnknown("Download temporary file identity changed")
            check_running()
            staged_relative = staged.relative_to(root).as_posix()
            data, current_identity = _read(self._workspace(), staged_relative)
            if current_identity != identity or hashlib.sha256(data).hexdigest() != digest:
                raise OutcomeUnknown("Download temporary file changed before publication")
            with self._transaction() as db:
                db.execute(
                    "UPDATE download SET state='publishing',sha256=?,size_bytes=?,final_url=?,"
                    "content_type=? WHERE request_key=? AND state='fetching'",
                    (digest, size, final_url, content_type, request_key),
                )
            check_running()
            target = _missing(self._workspace(), spec["path"])
            staged = _path(self._workspace(), staged_relative)
            os.link(staged, target)
            row = self._row(request_key)
            assert row is not None
            return self._complete(row)
        except (ValueError, OSError, httpx.HTTPError, SourceError) as exc:
            row = self._row(request_key)
            if row is not None and row["state"] in {"publishing", "completed"}:
                try:
                    return self._complete(row)
                except OutcomeUnknown:
                    raise OutcomeUnknown(
                        "Download publication is unverified; inspect the destination before revising"
                    ) from exc
            with self._transaction() as db:
                db.execute(
                    "UPDATE download SET state='failed' WHERE request_key=? AND state='fetching'",
                    (request_key,),
                )
            raise ValueError(f"Download stopped before publication: {exc}") from exc
        finally:
            # Only remove the exact temporary inode we created. Never remove a target,
            # a replacement file, a junction or a path outside this workspace.
            if staged is not None and identity:
                try:
                    safe = _path(self._workspace(), staged.relative_to(root).as_posix())
                    if _identity(safe.stat()) == identity:
                        safe.unlink()
                except (OSError, ValueError):
                    pass

    async def _fetch(
        self,
        url: str,
        write: Callable[[bytes], Any],
        check_running: Callable[[], None],
        *,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None,
    ) -> tuple[int, str, str, str]:
        origin = _origin(url)
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        # No browser cookies, ambient proxy credentials or caller supplied headers.
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout,
            headers=headers,
            transport=PublicTransport(transport, self._resolver),
            trust_env=False,
        ) as client:
            for hop in range(MAX_REDIRECTS + 1):
                check_running()
                if _origin(url) != origin:
                    raise ValueError("Download redirects to another origin require a new preview")
                # A redirect can set cookies too. Public downloads never inherit even
                # those cookies on the next hop.
                client.cookies.clear()
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location or hop == MAX_REDIRECTS:
                            raise ValueError("Download redirect limit exceeded or location missing")
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError("Download requires a complete HTTP 200 response")
                    length = response.headers.get("content-length")
                    expected_size = int(length) if length is not None else None
                    if expected_size is not None and not 0 <= expected_size <= MAX_DOWNLOAD_BYTES:
                        raise ValueError("Download exceeds the 16 MiB size limit")
                    encoding = response.headers.get("content-encoding", "identity").lower()
                    if encoding not in {"", "identity"}:
                        raise ValueError("Compressed download responses are not supported")
                    size = 0
                    digest = hashlib.sha256()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        check_running()
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise ValueError("Download exceeds the 16 MiB size limit")
                        write(chunk)
                        digest.update(chunk)
                    if expected_size is not None and expected_size != size:
                        raise ValueError("Download response length did not match its header")
                    check_running()
                    return (
                        size,
                        digest.hexdigest(),
                        str(response.url),
                        response.headers.get("content-type", "")[:200],
                    )
        raise ValueError("Download redirect limit exceeded")

    def _evidence(self, row: dict[str, Any]) -> dict[str, Any]:
        spec, task_id, revision, step_id = json.loads(row["payload"])
        try:
            data, identity = _read(self._workspace(), spec["path"])
            if (
                identity != json.loads(row["identity"])
                or len(data) != row["size_bytes"]
                or hashlib.sha256(data).hexdigest() != row["sha256"]
            ):
                raise ValueError("Download target changed")
        except (OSError, ValueError) as exc:
            raise OutcomeUnknown("Download target is unavailable or changed") from exc
        return {
            "action": "download",
            "effective_action": "public_get",
            "operation": {
                "path": spec["path"],
                "url": row["url"],
                "final_url": row["final_url"],
                "request_key": row["request_key"],
                "task_id": task_id,
                "revision": revision,
                "step_id": step_id,
            },
            "verification": {
                "kind": "file_sha256",
                "path": spec["path"],
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "content_type": row["content_type"],
            },
            "explicit_submit": False,
            "remote_effects": "read_only_get_requested",
            "cookies_sent": False,
        }

    def _complete(self, row: dict[str, Any]) -> dict[str, Any]:
        evidence = self._evidence(row)
        with self._transaction() as db:
            db.execute(
                "UPDATE download SET state='completed',result=? WHERE request_key=?",
                (json.dumps(evidence, ensure_ascii=False, sort_keys=True), row["request_key"]),
            )
        return evidence

    def cancel_evidence(self, request_key: str) -> tuple[str, dict[str, Any]]:
        """Inspect local effects without fetching or publishing anything."""
        row = self._row(request_key)
        if row is None or row["state"] in {"fetching", "failed"}:
            return "cancelled", {"cancelled_before_publication": True}
        try:
            return "completed", self._evidence(row)
        except OutcomeUnknown:
            return "outcome_unknown", {
                "outcome_unknown": True,
                "request_key": request_key,
                "reason": "Download publication requires inspection; no request was replayed",
            }
