"""Durable coordinator state. Domain stores remain authoritative for their results."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Conflict(ValueError):
    """A stale revision or incompatible state needs a fresh user decision."""


class StepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    adapter: str = Field(pattern="^(checkpoint|research)$")
    title: str = Field(min_length=1, max_length=500)
    project_id: str = Field(default="", max_length=100)
    kind: str = Field(default="research", pattern="^(research|check)$")
    spec_id: str = Field(default="", max_length=100)
    input_version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def project_required(self) -> StepSpec:
        if self.adapter == "research" and not self.project_id:
            raise ValueError("Research steps require a project")
        if self.adapter == "checkpoint" and self.project_id:
            raise ValueError("Checkpoints cannot bind research projects")
        return self


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=500)
    success_criteria: str = Field(min_length=1, max_length=4000)
    steps: list[StepSpec] = Field(min_length=1, max_length=30)


def uid() -> str:
    return uuid.uuid4().hex


class Ledger:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.clock = clock
        with self.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("Unsupported assistant ledger schema")
            for statement in SCHEMA:
                db.execute(statement)
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _task(self, db: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM task WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    def _event(self, db: sqlite3.Connection, task_id: str, kind: str, data: Any) -> None:
        db.execute(
            "INSERT INTO event(task_id,kind,data,at) VALUES(?,?,?,?)",
            (task_id, kind, json.dumps(data), self.clock()),
        )

    def _create(
        self,
        db: sqlite3.Connection,
        plan: Plan,
        request_id: str,
        due_at: float,
        schedule_id: str = "",
    ) -> str:
        payload = plan.model_dump_json()
        previous = db.execute("SELECT * FROM task WHERE request_id=?", (request_id,)).fetchone()
        if previous:
            if previous["request_payload"] != json.dumps([payload, due_at, schedule_id]):
                raise Conflict("Request ID already used with different inputs")
            return str(previous["id"])
        task_id = uid()
        db.execute(
            "INSERT INTO task VALUES(?,?,?,1,'queued',?,?,?,?)",
            (
                task_id,
                request_id,
                json.dumps([payload, due_at, schedule_id]),
                plan.title,
                due_at,
                schedule_id,
                self.clock(),
            ),
        )
        self._plan(db, task_id, 1, plan, "Initial plan")
        return task_id

    def create(self, plan: Plan, request_id: str, due_at: float = 0) -> dict[str, Any]:
        with self.transaction() as db:
            task_id = self._create(db, plan, request_id, due_at)
        return self.get(task_id)

    def _plan(
        self, db: sqlite3.Connection, task_id: str, revision: int, plan: Plan, reason: str
    ) -> None:
        db.execute(
            "INSERT INTO plan VALUES(?,?,?,?,?)",
            (task_id, revision, plan.model_dump_json(), reason, self.clock()),
        )
        for index, step in enumerate(plan.steps):
            db.execute(
                "INSERT INTO step(id,task_id,revision,position,spec) VALUES(?,?,?,?,?)",
                (uid(), task_id, revision, index, step.model_dump_json()),
            )
        self._event(db, task_id, "plan_created", {"revision": revision, "reason": reason})

    def tasks(self) -> list[dict[str, Any]]:
        with self.transaction() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,title,revision,status,due_at,schedule_id,created_at FROM task "
                    "ORDER BY created_at DESC,rowid DESC LIMIT 500"
                )
            ]

    def get(self, task_id: str) -> dict[str, Any]:
        with self.transaction() as db:
            return self._detail(db, task_id)

    def _detail(self, db: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        task = self._task(db, task_id)
        task.pop("request_payload")
        task["plans"] = [
            {**dict(r), "spec": json.loads(r["spec"])}
            for r in db.execute("SELECT * FROM plan WHERE task_id=? ORDER BY revision", (task_id,))
        ]
        task["steps"] = [
            {**dict(r), "spec": json.loads(r["spec"]), "result": json.loads(r["result"])}
            for r in db.execute(
                "SELECT * FROM step WHERE task_id=? ORDER BY revision,position", (task_id,)
            )
        ]
        task["events"] = [
            {**dict(r), "data": json.loads(r["data"])}
            for r in db.execute("SELECT * FROM event WHERE task_id=? ORDER BY id", (task_id,))
        ]
        return task

    def revise(
        self, task_id: str, expected: int, plan: Plan, reason: str, request_id: str = ""
    ) -> dict[str, Any]:
        with self.transaction() as db:
            if self._mutation(
                db, request_id, [task_id, "revise", expected, plan.model_dump(), reason]
            ):
                return self._detail(db, task_id)
            task = self._task(db, task_id)
            self._expected(task, expected)
            if (
                task["status"] in {"completed", "cancelled", "cancelling"}
                or db.execute(
                    "SELECT 1 FROM step WHERE task_id=? AND status IN ('running','bound')",
                    (task_id,),
                ).fetchone()
            ):
                raise Conflict("Wait for active work to settle before replacing the plan")
            # Retain completed evidence. Only unfinished work becomes superseded.
            db.execute(
                "UPDATE step SET status='superseded',token='' WHERE task_id=? "
                "AND status NOT IN ('completed','superseded')",
                (task_id,),
            )
            db.execute(
                "UPDATE task SET revision=revision+1,title=?,status='queued' WHERE id=?",
                (plan.title, task_id),
            )
            self._plan(db, task_id, expected + 1, plan, reason)
        return self.get(task_id)

    def _mutation(self, db: sqlite3.Connection, request_id: str, payload: Any) -> bool:
        if not request_id:
            return False  # Internal callers rely on revision and state fencing.
        encoded = json.dumps(payload, sort_keys=True)
        old = db.execute("SELECT payload FROM mutation WHERE id=?", (request_id,)).fetchone()
        if old:
            if old["payload"] != encoded:
                raise Conflict("Mutation request ID already used with different inputs")
            return True
        db.execute("INSERT INTO mutation VALUES(?,?)", (request_id, encoded))
        return False

    @staticmethod
    def _expected(task: dict[str, Any], expected: int) -> None:
        if task["revision"] != expected:
            raise Conflict("Plan changed; reload before applying this action")

    def control(
        self, task_id: str, expected: int, action: str, request_id: str = ""
    ) -> dict[str, Any]:
        with self.transaction() as db:
            if self._mutation(db, request_id, [task_id, action, expected]):
                return self._detail(db, task_id)
            task = self._task(db, task_id)
            self._expected(task, expected)
            target = {"pause": "paused", "resume": "queued", "cancel": "cancelling"}
            if action not in target or task["status"] in {"completed", "cancelled", "cancelling"}:
                raise Conflict("Action unavailable")
            if action == "resume" and task["status"] != "paused":
                raise Conflict("Only paused tasks may resume")
            db.execute("UPDATE task SET status=? WHERE id=?", (target[action], task_id))
            self._event(db, task_id, action, {"revision": expected})
        return self.get(task_id)

    def confirm(
        self, task_id: str, step_id: str, expected: int, evidence: str, request_id: str = ""
    ) -> dict[str, Any]:
        with self.transaction() as db:
            if self._mutation(db, request_id, [task_id, "confirm", expected, step_id, evidence]):
                return self._detail(db, task_id)
            task = self._task(db, task_id)
            self._expected(task, expected)
            if task["status"] != "waiting_user":
                raise Conflict("Task is not waiting for confirmation")
            changed = db.execute(
                "UPDATE step SET status='completed',result=? WHERE id=? "
                "AND task_id=? AND revision=? AND status='waiting_user'",
                (json.dumps({"user_evidence": evidence}), step_id, task_id, expected),
            )
            if changed.rowcount != 1:
                raise Conflict("Checkpoint is no longer current")
            db.execute("UPDATE task SET status='queued' WHERE id=?", (task_id,))
            self._event(db, task_id, "user_confirmed", {"step_id": step_id, "evidence": evidence})
            self._settle(db, task_id)
        return self.get(task_id)

    def claim(self, lease_seconds: float = 30) -> dict[str, Any] | None:
        now = self.clock()
        with self.transaction() as db:
            row = db.execute(
                "SELECT s.* FROM step s JOIN task t ON t.id=s.task_id "
                "WHERE s.revision=t.revision AND t.due_at<=? AND "
                "((s.status='pending' AND t.status IN ('queued','running')) OR "
                "(s.status='running' AND s.lease_until<=? AND t.status IN "
                "('queued','running','paused','cancelling'))) AND NOT EXISTS "
                "(SELECT 1 FROM step p WHERE p.task_id=s.task_id AND p.revision=s.revision "
                "AND p.position<s.position AND p.status!='completed') "
                "ORDER BY t.created_at,s.position LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            token = uid()
            db.execute(
                "UPDATE step SET status='running',token=?,lease_until=?,attempt=attempt+1 "
                "WHERE id=?",
                (token, now + lease_seconds, row["id"]),
            )
            db.execute(
                "UPDATE task SET status='running' WHERE id=? AND status='queued'", (row["task_id"],)
            )
            self._event(
                db,
                row["task_id"],
                "attempt_started",
                {"step_id": row["id"], "attempt": row["attempt"] + 1},
            )
            return {**dict(row), "token": token, "spec": json.loads(row["spec"])}

    def finish_claim(self, step: dict[str, Any], status: str, result: dict[str, Any]) -> bool:
        if status not in {"bound", "waiting_user", "needs_attention"}:
            raise ValueError("Unsupported claim result")
        with self.transaction() as db:
            updated = db.execute(
                "UPDATE step SET status=?,result=?,token='',lease_until=0 "
                "WHERE id=? AND token=? AND status='running' AND lease_until>? "
                "AND revision=(SELECT revision FROM task WHERE id=step.task_id)",
                (status, json.dumps(result), step["id"], step["token"], self.clock()),
            )
            if not updated.rowcount:
                return False
            if status != "bound":
                db.execute(
                    "UPDATE task SET status=? WHERE id=? AND status='running'",
                    (status, step["task_id"]),
                )
            self._event(
                db,
                step["task_id"],
                "attempt_result",
                {"step_id": step["id"], "status": status, "result": result},
            )
            return True

    def bound(self) -> list[dict[str, Any]]:
        with self.transaction() as db:
            return [
                {**dict(r), "result": json.loads(r["result"])}
                for r in db.execute(
                    "SELECT s.*,t.status AS task_status FROM step s JOIN task t ON t.id=s.task_id "
                    "WHERE s.status='bound'"
                )
            ]

    def reconcile(self, step_id: str, status: str, result: dict[str, Any]) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM step WHERE id=? AND status='bound'", (step_id,)
            ).fetchone()
            if row is None:
                return
            db.execute(
                "UPDATE step SET status=?,result=? WHERE id=?",
                (status, json.dumps(result), step_id),
            )
            self._event(
                db,
                row["task_id"],
                "module_result",
                {"step_id": step_id, "status": status, "result": result},
            )
            if status == "needs_attention":
                db.execute(
                    "UPDATE task SET status='needs_attention' WHERE id=? "
                    "AND status NOT IN ('paused','cancelling')",
                    (row["task_id"],),
                )
            self._settle(db, row["task_id"])

    def _settle(self, db: sqlite3.Connection, task_id: str) -> None:
        task = self._task(db, task_id)
        pending = db.execute(
            "SELECT 1 FROM step WHERE task_id=? AND revision=? AND status!='completed'",
            (task_id, task["revision"]),
        ).fetchone()
        if not pending and task["status"] not in {"paused", "cancelling", "cancelled"}:
            db.execute("UPDATE task SET status='completed' WHERE id=?", (task_id,))
            self._event(db, task_id, "completed", {"revision": task["revision"]})
        elif task["status"] == "queued":
            first = db.execute(
                "SELECT status FROM step WHERE task_id=? AND revision=? "
                "AND status!='completed' ORDER BY position LIMIT 1",
                (task_id, task["revision"]),
            ).fetchone()
            if first and first["status"] in {"waiting_user", "needs_attention"}:
                db.execute("UPDATE task SET status=? WHERE id=?", (first["status"], task_id))

    def settle(self) -> None:
        with self.transaction() as db:
            for row in db.execute(
                "SELECT id,status FROM task WHERE status IN ('cancelling','queued')"
            ).fetchall():
                if row["status"] == "queued":
                    self._settle(db, row["id"])
                elif not db.execute(
                    "SELECT 1 FROM step WHERE task_id=? AND "
                    "(status='bound' OR (status='running' AND lease_until>?))",
                    (row["id"], self.clock()),
                ).fetchone():
                    # An expired research dispatch may already have reached the domain store.
                    # Recover that intent before acknowledging cancellation.
                    unknown = db.execute(
                        "SELECT 1 FROM step WHERE task_id=? AND status='running'", (row["id"],)
                    ).fetchone()
                    if unknown:
                        continue
                    db.execute(
                        "UPDATE step SET status='cancelled' WHERE task_id=? AND "
                        "status NOT IN ('completed','superseded')",
                        (row["id"],),
                    )
                    db.execute("UPDATE task SET status='cancelled' WHERE id=?", (row["id"],))
                    self._event(db, row["id"], "cancelled", {})

    def schedules(self) -> list[dict[str, Any]]:
        with self.transaction() as db:
            return [
                {**dict(r), "payload": json.loads(r["payload"])}
                for r in db.execute("SELECT * FROM schedule ORDER BY next_at")
            ]

    def schedule(
        self, plan: Plan, request_id: str, next_at: float, interval_seconds: int
    ) -> dict[str, Any]:
        # Only replay-safe research/checkpoint adapters are admitted by Plan.
        payload = json.dumps(
            {"plan": plan.model_dump(), "first_at": next_at, "interval_seconds": interval_seconds},
            sort_keys=True,
        )
        with self.transaction() as db:
            old = db.execute("SELECT * FROM schedule WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["payload"] != payload:
                    raise Conflict("Schedule request ID already used")
                schedule_id = old["id"]
            else:
                schedule_id = uid()
                db.execute(
                    "INSERT INTO schedule VALUES(?,?,?,?,?,1)",
                    (schedule_id, request_id, payload, next_at, interval_seconds),
                )
        return next(s for s in self.schedules() if s["id"] == schedule_id)

    def disable_schedule(self, schedule_id: str) -> None:
        with self.transaction() as db:
            if not db.execute("UPDATE schedule SET enabled=0 WHERE id=?", (schedule_id,)).rowcount:
                raise KeyError(schedule_id)

    def fire_due(self) -> None:
        now = self.clock()
        with self.transaction() as db:
            for row in db.execute(
                "SELECT * FROM schedule WHERE enabled=1 AND next_at<=?", (now,)
            ).fetchall():
                # Do not accumulate overlapping occurrences behind an unfinished task.
                if db.execute(
                    "SELECT 1 FROM task WHERE schedule_id=? AND status NOT IN "
                    "('completed','cancelled')",
                    (row["id"],),
                ).fetchone():
                    continue
                plan = Plan.model_validate(json.loads(row["payload"])["plan"])
                task_id = self._create(
                    db, plan, f"schedule:{row['id']}:{row['next_at']}", row["next_at"], row["id"]
                )
                interval = row["interval_seconds"]
                next_at = row["next_at"] + (int((now - row["next_at"]) // interval) + 1) * interval
                db.execute("UPDATE schedule SET next_at=? WHERE id=?", (next_at, row["id"]))
                self._event(
                    db,
                    task_id,
                    "schedule_catch_up",
                    {
                        "from": row["next_at"],
                        "through": now,
                        "owner": "assistant",
                        "policy": "merge",
                    },
                )


SCHEMA = [
    "CREATE TABLE IF NOT EXISTS mutation(id TEXT PRIMARY KEY,payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS task(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,"
    "request_payload TEXT NOT NULL,revision INTEGER NOT NULL,status TEXT NOT NULL,"
    "title TEXT NOT NULL,due_at REAL NOT NULL,schedule_id TEXT NOT NULL,created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS plan(task_id TEXT REFERENCES task(id),revision INTEGER,"
    "spec TEXT NOT NULL,reason TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(task_id,revision))",
    "CREATE TABLE IF NOT EXISTS step(id TEXT PRIMARY KEY,task_id TEXT REFERENCES task(id),"
    "revision INTEGER,position INTEGER,spec TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',"
    "token TEXT NOT NULL DEFAULT '',lease_until REAL NOT NULL DEFAULT 0,attempt INTEGER NOT NULL "
    "DEFAULT 0,result TEXT NOT NULL DEFAULT '{}',UNIQUE(task_id,revision,position))",
    "CREATE TABLE IF NOT EXISTS event(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT "
    "REFERENCES task(id),kind TEXT NOT NULL,data TEXT NOT NULL,at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS schedule(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,"
    "payload TEXT NOT NULL,next_at REAL NOT NULL,interval_seconds INTEGER NOT NULL,"
    "enabled INTEGER NOT NULL DEFAULT 1)",
]
