"""Durable personal-task notices and bounded, opt-in planner continuations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextvars import ContextVar, Token
from typing import Any

from nanomuse.assistant.ledger import EXECUTION_ADAPTERS, Ledger

_origin: ContextVar[dict[str, Any] | None] = ContextVar("assistant_request_origin", default=None)


class ContinuationStopped(RuntimeError):
    code = "assistant_continuation_stopped"

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("Further planning stopped; review the original request and task state.")


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS request_scope (id TEXT PRIMARY KEY, origin TEXT NOT NULL, "
    "count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active')",
    "CREATE TABLE IF NOT EXISTS task_origin (task_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, "
    "continue_after_result INTEGER NOT NULL, parent_event_id TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS task_result_outbox (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
    "scope_id TEXT NOT NULL, payload TEXT NOT NULL, noticed INTEGER NOT NULL DEFAULT 0, "
    "state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', blocked_noticed INTEGER NOT NULL DEFAULT 0)",
)


def enter_origin(origin: dict[str, Any] | None) -> Token[dict[str, Any] | None]:
    return _origin.set(dict(origin) if origin else None)


def leave_origin(token: Token[dict[str, Any] | None]) -> None:
    _origin.reset(token)


def current_origin() -> dict[str, Any] | None:
    origin = _origin.get()
    return dict(origin) if origin else None


def model_identity(llm: Any) -> str:
    settings = getattr(llm, "settings", None)
    value = {"name": llm.name}
    for key in ("provider", "model", "base_url", "tool_mode"):
        value[key] = getattr(settings, key, "")
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def make_origin(thread: str, event_id: str, goal: str, llm: Any, settings: Any) -> dict[str, Any]:
    now = time.time()
    scope_id = hashlib.sha256(f"{thread}:{event_id}".encode()).hexdigest()
    owner = getattr(llm, "continuation_owner", lambda: None)()
    from nanomuse.assistant.usage_limits import UsageGuardLLM

    if isinstance(llm, UsageGuardLLM):
        owner = scope_id
    return {
        "scope_id": scope_id,
        "thread": thread,
        "user_event_id": event_id,
        "goal": goal,
        "model_identity": model_identity(llm),
        "execution_owner": owner,
        "created_at": now,
        "deadline": now + getattr(settings.agent, "task_continuation_seconds", 900),
        "maximum": getattr(settings.agent, "max_task_continuations", 8),
    }


def record_origin(
    db: sqlite3.Connection, task_id: str, origin: dict[str, Any], continue_after_result: bool
) -> None:
    """Called in the task-creation transaction, never after a visible task commit."""
    for statement in SCHEMA:
        db.execute(statement)
    if check_replay(db, task_id, continue_after_result):
        return
    saved = {key: value for key, value in origin.items() if key != "parent_event_id"}
    db.execute(
        "INSERT OR IGNORE INTO request_scope(id,origin) VALUES(?,?)",
        (saved["scope_id"], json.dumps(saved, sort_keys=True)),
    )
    db.execute(
        "INSERT OR IGNORE INTO task_origin VALUES(?,?,?,?)",
        (task_id, saved["scope_id"], int(continue_after_result), origin.get("parent_event_id", "")),
    )


def check_replay(db: sqlite3.Connection, task_id: str, resume: bool) -> bool:
    """A repeated task ID cannot change its already committed planning permission."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_origin'").fetchone():
        return False
    row = db.execute(
        "SELECT continue_after_result FROM task_origin WHERE task_id=?", (task_id,)
    ).fetchone()
    if row is not None and bool(row[0]) != resume:
        from nanomuse.assistant.errors import Conflict

        raise Conflict("Request ID already used with a different continuation choice")
    return row is not None


class Continuations:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        with ledger.transaction() as db:
            for statement in SCHEMA:
                db.execute(statement)
            if "blocked_noticed" not in {
                r[1] for r in db.execute("PRAGMA table_info(task_result_outbox)")
            }:
                db.execute(
                    "ALTER TABLE task_result_outbox ADD COLUMN blocked_noticed INTEGER NOT NULL DEFAULT 0"
                )
            # An interrupted planning request may already have reached its provider.
            # Keep its count and block it rather than issuing another request.
            db.execute(
                "UPDATE task_result_outbox SET state='blocked',reason='interrupted_planning' "
                "WHERE state='running'"
            )

    def describe(self, task: dict[str, Any]) -> dict[str, Any]:
        steps = [s for s in task["steps"] if s["revision"] == task["revision"]]
        required = any(
            s["status"] not in {"completed", "superseded"}
            and s["spec"]["adapter"] in EXECUTION_ADAPTERS | {"preference"}
            and not self.ledger.preference_authorized(s["id"])
            for s in steps
        )
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM task_origin WHERE task_id=?", (task["id"],)).fetchone()
        return {
            **task,
            "approval_required": required,
            "status_is_snapshot": True,
            "request_scope": dict(row) if row else None,
        }

    def project(self) -> None:
        """Derive outbox intents from retained terminal events, repairing a crash gap."""
        with self.ledger.transaction() as db:
            rows = db.execute(
                "SELECT t.id,t.revision,o.scope_id,o.continue_after_result,s.origin "
                "FROM task t JOIN task_origin o ON o.task_id=t.id "
                "JOIN request_scope s ON s.id=o.scope_id WHERE t.status='completed'"
            ).fetchall()
            for row in rows:
                event = db.execute(
                    "SELECT id FROM event WHERE task_id=? AND kind='completed' "
                    "ORDER BY id DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                if event is None:
                    continue
                key = f"assistant-task:{row['id']}:{row['revision']}:{event['id']}"
                if db.execute("SELECT 1 FROM task_result_outbox WHERE id=?", (key,)).fetchone():
                    continue
                task = self.ledger._detail(db, row["id"])
                origin = json.loads(row["origin"])
                steps = [s for s in task["steps"] if s["revision"] == row["revision"]]
                observed = any(
                    isinstance(s["result"].get("observation"), dict)
                    or bool(s["result"].get("observation_hash"))
                    for s in steps
                )
                resume = bool(row["continue_after_result"] and observed)
                payload = {"id": key, "task": task, "origin": origin, "continue": resume}
                db.execute(
                    "INSERT INTO task_result_outbox(id,task_id,scope_id,payload,state) "
                    "VALUES(?,?,?,?,?)",
                    (
                        key,
                        row["id"],
                        row["scope_id"],
                        json.dumps(payload),
                        "pending" if resume else "not_requested",
                    ),
                )

    def pending_notices(self) -> list[dict[str, Any]]:
        with self.ledger.transaction() as db:
            return [
                json.loads(row["payload"])
                for row in db.execute(
                    "SELECT payload FROM task_result_outbox WHERE noticed=0 ORDER BY rowid"
                )
            ]

    def noticed(self, identifier: str) -> None:
        with self.ledger.transaction() as db:
            db.execute("UPDATE task_result_outbox SET noticed=1 WHERE id=?", (identifier,))

    def pending(self) -> list[dict[str, Any]]:
        with self.ledger.transaction() as db:
            return [
                json.loads(row["payload"])
                for row in db.execute(
                    "SELECT payload FROM task_result_outbox WHERE state='pending' AND noticed=1 "
                    "ORDER BY rowid"
                )
            ]

    def rejection(self, item: dict[str, Any], service: Any, *, claimed: bool = False) -> str:
        origin = item["origin"]
        if self.ledger.manual_owner():
            return "manual_control_active"
        thread = service.threads.get(origin["thread"])
        if thread is None:
            return "origin_thread_unavailable"
        latest: dict[str, Any] = next(
            (e for e in reversed(thread.timeline.events) if e.get("type") == "user"), {}
        )
        if latest.get("id") != origin["user_event_id"]:
            return "origin_request_changed"
        if time.time() >= origin["deadline"]:
            return "continuation_deadline"
        llm = service.app.llm
        if model_identity(llm) != origin["model_identity"]:
            return "model_configuration_changed"
        allowed = getattr(llm, "continuation_allowed", None)
        if allowed is not None:
            try:
                if not allowed(origin["execution_owner"]):
                    return "model_execution_closed"
            except Exception:  # Provider admission failures grant no continuation.
                return "model_execution_closed"
        elif origin["execution_owner"] is not None:
            return "model_execution_unavailable"
        with self.ledger.transaction() as db:
            scope = db.execute(
                "SELECT * FROM request_scope WHERE id=?", (origin["scope_id"],)
            ).fetchone()
            if scope is None or scope["status"] != "active":
                return "request_scope_stopped"
            if not claimed and scope["count"] >= origin["maximum"]:
                return "continuation_limit"
        return ""

    def claim(self, item: dict[str, Any], service: Any) -> bool:
        thread = service.threads.get(item["origin"]["thread"])
        if thread is not None and (thread.busy or not thread.inbox.empty()):
            return False
        reason = self.rejection(item, service)
        if reason:
            self.finished(item["id"], reason)
            return False
        with self.ledger.transaction() as db:
            changed = db.execute(
                "UPDATE task_result_outbox SET state='running' WHERE id=? AND state='pending'",
                (item["id"],),
            )
            if changed.rowcount != 1:
                return False
            changed = db.execute(
                "UPDATE request_scope SET count=count+1 WHERE id=? AND status='active' AND count<?",
                (item["origin"]["scope_id"], item["origin"]["maximum"]),
            )
            if changed.rowcount != 1:
                db.execute(
                    "UPDATE task_result_outbox SET state='blocked',reason='continuation_limit' WHERE id=?",
                    (item["id"],),
                )
                return False
        return True

    def finished(self, identifier: str, reason: str = "") -> None:
        with self.ledger.transaction() as db:
            db.execute(
                "UPDATE task_result_outbox SET state=?,reason=? WHERE id=? AND state IN ('pending','running')",
                ("blocked" if reason else "completed", reason, identifier),
            )

    def pending_blocks(self) -> list[dict[str, Any]]:
        with self.ledger.transaction() as db:
            return [
                {
                    **json.loads(row["payload"]),
                    "id": row["id"] + ":blocked",
                    "blocked_reason": row["reason"],
                }
                for row in db.execute(
                    "SELECT * FROM task_result_outbox WHERE state='blocked' AND blocked_noticed=0"
                )
            ]

    def block_noticed(self, identifier: str) -> None:
        with self.ledger.transaction() as db:
            db.execute(
                "UPDATE task_result_outbox SET blocked_noticed=1 WHERE id=?",
                (identifier.removesuffix(":blocked"),),
            )

    def block_thread(self, thread_id: str) -> None:
        with self.ledger.transaction() as db:
            for row in db.execute("SELECT id,origin FROM request_scope").fetchall():
                if json.loads(row["origin"])["thread"] == thread_id:
                    db.execute("UPDATE request_scope SET status='stopped' WHERE id=?", (row["id"],))

    def records(self) -> list[dict[str, Any]]:
        with self.ledger.transaction() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM task_result_outbox ORDER BY rowid")
            ]

    def validate_current(self, service: Any) -> str:
        origin = current_origin()
        if origin is None or not origin.get("parent_event_id"):
            return ""
        with self.ledger.transaction() as db:
            row = db.execute(
                "SELECT payload,state FROM task_result_outbox WHERE id=?",
                (origin["parent_event_id"],),
            ).fetchone()
        if row is None or row["state"] != "running":
            return "continuation_not_active"
        return self.rejection(json.loads(row["payload"]), service, claimed=True)

    def request_root(self, task_id: str) -> str:
        """The original request's quota survives follow-up tasks and plan revisions."""
        with self.ledger.transaction() as db:
            row = db.execute(
                "SELECT scope_id FROM task_origin WHERE task_id=?", (task_id,)
            ).fetchone()
            return str(row["scope_id"]) if row else task_id

    def owner_for_task(self, task_id: str, *, run_id: str = "", background: bool = True) -> Any:
        from nanomuse.assistant.usage_limits import UsageOwner

        root = self.request_root(task_id)
        return UsageOwner(
            turn_id=root, task_id=task_id, run_id=run_id, background=background, root_id=root
        )
