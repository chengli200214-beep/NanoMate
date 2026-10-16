"""Confirmed user preferences, projected into the existing long-term memory store.

This module accepts approved intent only. Callers must obtain user confirmation
through the Sentinel before executing a ledger step, never infer it from a source.
The preference database owns revisions and tombstones; MemoryStore owns retrieval.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nanomuse.assistant.errors import Conflict
from nanomuse.memory.store import MemoryStore


class PreferenceAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    action: Literal["set", "delete"]
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    value: str | None = Field(default=None, min_length=1, max_length=2000)
    expected_revision: int = Field(ge=0, le=2**31 - 1)
    purpose: str = Field(default="", max_length=1000)
    expires_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_action(self) -> PreferenceAction:
        if self.action == "set" and self.value is None:
            raise ValueError("Setting a preference requires a value")
        if self.action == "delete" and (
            self.value is not None or self.purpose or self.expires_at is not None
        ):
            raise ValueError("Deleting a preference cannot supply a value, purpose or expiry")
        return self


class PreferenceStore:
    """A durable desired-state outbox with idempotent MemoryStore projection.

    A request commits its revision and a value-free receipt before touching memory.
    Recovery always projects the latest revision, so replaying an old request cannot
    restore a replaced or deleted value. Holding the preference write lock during
    projection also prevents an older worker from publishing after a newer delete.
    """

    def __init__(
        self,
        path: Path,
        memory: MemoryStore | None,
        clock: Callable[[], float] = time.time,
    ):
        self.path = path
        self.memory = memory
        self.clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("Unsupported assistant preference schema")
            db.execute(
                "CREATE TABLE IF NOT EXISTS preference ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL, purpose TEXT NOT NULL, "
                "revision INTEGER NOT NULL, status TEXT NOT NULL, expires_at REAL, "
                "source_task TEXT NOT NULL, source_plan INTEGER NOT NULL, source_step TEXT NOT NULL, "
                "confirmed_at REAL NOT NULL, updated_at REAL NOT NULL, "
                "projected_revision INTEGER NOT NULL DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS preference_receipt ("
                "request_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result TEXT NOT NULL)"
            )
            db.execute("PRAGMA user_version=1")
        self.recover()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def memory_id(key: str) -> str:
        return "m_pref_" + hashlib.sha256(key.encode()).hexdigest()[:32]

    def execute(
        self,
        spec: PreferenceAction,
        request_key: str,
        task_id: str,
        revision: int,
        step_id: str,
    ) -> dict[str, Any]:
        if self.memory is None:
            raise ValueError("Long-term memory must be enabled for personal preferences")
        if not all(0 < len(s) <= 200 for s in (request_key, task_id, step_id)):
            raise ValueError("Preference actions require bounded request, task and step IDs")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("Preference actions require a positive plan revision")
        payload = json.dumps(
            [spec.model_dump(mode="json"), task_id, revision, step_id],
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self._transaction() as db:
            previous = db.execute(
                "SELECT * FROM preference_receipt WHERE request_key=?", (request_key,)
            ).fetchone()
            if previous:
                if previous["payload_hash"] != digest:
                    raise Conflict("Preference request key already used with different inputs")
                result: dict[str, Any] = json.loads(previous["result"])
            else:
                self._expire(db)
                self._observe_removed(db)
                current = db.execute("SELECT * FROM preference WHERE key=?", (spec.key,)).fetchone()
                current_revision = current["revision"] if current else 0
                if spec.expected_revision != current_revision:
                    raise Conflict("Preference changed, review its current revision")
                if spec.action == "delete" and current is None:
                    raise Conflict("Preference does not exist")
                if spec.expires_at is not None and spec.expires_at <= self.clock():
                    raise ValueError("Preference expiry must be in the future")
                next_revision = current_revision + 1
                status = "active" if spec.action == "set" else "deleted"
                now = self.clock()
                db.execute(
                    "INSERT INTO preference(key,value,purpose,revision,status,expires_at,"
                    "source_task,source_plan,source_step,confirmed_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                    "value=excluded.value,purpose=excluded.purpose,revision=excluded.revision,"
                    "status=excluded.status,expires_at=excluded.expires_at,"
                    "source_task=excluded.source_task,source_plan=excluded.source_plan,"
                    "source_step=excluded.source_step,confirmed_at=excluded.confirmed_at,"
                    "updated_at=excluded.updated_at",
                    (
                        spec.key,
                        spec.value or "",
                        spec.purpose,
                        next_revision,
                        status,
                        spec.expires_at,
                        task_id,
                        revision,
                        step_id,
                        now,
                        now,
                    ),
                )
                # No value or purpose in receipts, including receipts from earlier sets.
                result = {
                    "key": spec.key,
                    "revision": next_revision,
                    "status": status,
                    "memory_id": self.memory_id(spec.key),
                    "source_task": task_id,
                    "source_plan": revision,
                    "source_step": step_id,
                    "confirmed": True,
                }
                db.execute(
                    "INSERT INTO preference_receipt VALUES(?,?,?)",
                    (request_key, digest, json.dumps(result)),
                )
        self.recover()
        return result

    def _expire(self, db: sqlite3.Connection) -> None:
        db.execute(
            "UPDATE preference SET value='',purpose='',revision=revision+1,status='expired',"
            "updated_at=? WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?",
            (self.clock(), self.clock()),
        )

    def _observe_removed(self, db: sqlite3.Connection) -> None:
        if self.memory is None:
            return
        for row in db.execute(
            "SELECT * FROM preference WHERE status='active' AND projected_revision=revision"
        ).fetchall():
            if self.memory.get(self.memory_id(row["key"])) is None:
                db.execute(
                    "UPDATE preference SET value='',purpose='',revision=revision+1,"
                    "status='deleted',updated_at=? WHERE key=?",
                    (self.clock(), row["key"]),
                )

    def recover(self) -> None:
        """Reconcile after restart and remove expired preferences before later retrieval."""
        with self._transaction() as db:
            self._expire(db)
            self._observe_removed(db)
            if self.memory is None:
                return
            for row in db.execute("SELECT * FROM preference").fetchall():
                memory_id = self.memory_id(row["key"])
                if row["status"] == "active":
                    provenance = {
                        "key": row["key"],
                        "revision": row["revision"],
                        "source_task": row["source_task"],
                        "source_plan": row["source_plan"],
                        "source_step": row["source_step"],
                        "confirmed_at": row["confirmed_at"],
                        "expires_at": row["expires_at"],
                    }
                    source = "assistant_preference:" + json.dumps(provenance, sort_keys=True)
                    content = f"Confirmed preference {row['key']}: {row['value']}"
                    if row["purpose"]:
                        content += f"\nPurpose: {row['purpose']}"
                    content += "\nApply when relevant. The user's current request takes priority."
                    self.memory.set_owned_projection(memory_id, content, "preference", source)
                else:
                    self.memory.remove_owned_projection(memory_id)
                db.execute(
                    "UPDATE preference SET projected_revision=? WHERE key=?",
                    (row["revision"], row["key"]),
                )

    def list(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        self.recover()
        with self._transaction() as db:
            rows = db.execute(
                "SELECT * FROM preference "
                + ("" if include_deleted else "WHERE status='active' ")
                + "ORDER BY key"
            ).fetchall()
        return [
            {
                **dict(row),
                "memory_id": self.memory_id(row["key"]),
                "confirmed": True,
            }
            for row in rows
        ]

    def active_context(self) -> builtins.list[dict[str, Any]]:
        """Return confirmed active metadata; actual agent retrieval uses MemoryStore."""
        return self.list()
