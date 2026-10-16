"""Durable coordinator state. Domain stores remain authoritative for their results."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nanomuse.assistant.browser_actions import BrowserSpec
from nanomuse.assistant.documents import DocumentSpec
from nanomuse.assistant.errors import Conflict
from nanomuse.assistant.file_actions import FileSpec
from nanomuse.assistant.native_actions import NativeSpec
from nanomuse.assistant.preferences import PreferenceAction
from nanomuse.assistant.reminders import ReminderAction

EXECUTION_ADAPTERS = {"file", "browser", "native"}


class StepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    adapter: str = Field(
        pattern="^(checkpoint|research|reminder|document|preference|file|browser|native)$"
    )
    title: str = Field(min_length=1, max_length=500)
    project_id: str = Field(default="", max_length=100)
    kind: str = Field(default="research", pattern="^(research|check)$")
    spec_id: str = Field(default="", max_length=100)
    input_version: int = Field(default=0, ge=0)
    reminder: ReminderAction | None = None
    document: DocumentSpec | None = None
    preference: PreferenceAction | None = None
    file: FileSpec | None = None
    browser: BrowserSpec | None = None
    native: NativeSpec | None = None

    @model_validator(mode="after")
    def project_required(self) -> StepSpec:
        if self.adapter == "research" and not self.project_id:
            raise ValueError("Research steps require a project")
        if self.adapter != "research" and self.project_id:
            raise ValueError("Only research steps can bind research projects")
        for adapter in ("reminder", "document", "preference", "file", "browser", "native"):
            payload = getattr(self, adapter)
            if (self.adapter == adapter) != (payload is not None):
                raise ValueError(f"Only {adapter} steps require a {adapter} payload")
        return self


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=500)
    success_criteria: str = Field(min_length=1, max_length=4000)
    steps: list[StepSpec] = Field(min_length=1, max_length=30)


def uid() -> str:
    return uuid.uuid4().hex


def request_plan(plan: Plan | dict[str, Any]) -> dict[str, Any]:
    """Compare user intent across additive fields and frozen input metadata."""
    value = Plan.model_validate(plan).model_dump()
    for step in value["steps"]:
        step.pop("spec_id", None)
        step.pop("input_version", None)
        if step.get("document"):
            step["document"].pop("source_hashes", None)
        if step.get("file"):
            step["file"].pop("source_sha256", None)
            step["file"].pop("target_absent", None)
        if step.get("browser"):
            step["browser"].pop("target_absent", None)
    return value


def mutation_payload(payload: Any) -> str:
    if isinstance(payload, list) and len(payload) > 3 and payload[1] == "revise":
        payload = [*payload]
        payload[3] = request_plan(payload[3])
    return json.dumps(payload, sort_keys=True)


class Ledger:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.clock = clock
        self.control_hook: Callable[[str, str], None] | None = None
        with self.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("Unsupported assistant ledger schema")
            for statement in SCHEMA:
                db.execute(statement)
            db.execute(
                "CREATE TABLE IF NOT EXISTS manual_session (id INTEGER PRIMARY KEY CHECK(id=1), task_id TEXT NOT NULL)"
            )
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
        confirm_preferences: bool = False,
    ) -> str:
        payload = plan.model_dump_json()
        previous = db.execute("SELECT * FROM task WHERE request_id=?", (request_id,)).fetchone()
        if previous:
            self._creation_matches(
                previous["request_payload"], plan, due_at, schedule_id, confirm_preferences
            )
            return str(previous["id"])
        task_id = uid()
        db.execute(
            "INSERT INTO task VALUES(?,?,?,1,'queued',?,?,?,?)",
            (
                task_id,
                request_id,
                json.dumps([payload, due_at, schedule_id, confirm_preferences]),
                plan.title,
                due_at,
                schedule_id,
                self.clock(),
            ),
        )
        self._plan(db, task_id, 1, plan, "Initial plan")
        if confirm_preferences:
            for step in db.execute("SELECT * FROM step WHERE task_id=?", (task_id,)).fetchall():
                if json.loads(step["spec"])["adapter"] == "preference":
                    db.execute(
                        "INSERT INTO step_approval VALUES(?,?,?)",
                        (
                            step["id"],
                            hashlib.sha256(step["spec"].encode()).hexdigest(),
                            self.clock(),
                        ),
                    )
                    self._event(
                        db, task_id, "preference_confirmed", {"step_id": step["id"], "revision": 1}
                    )
        return task_id

    @staticmethod
    def _creation_matches(
        payload: str, plan: Plan, due_at: float, schedule_id: str, confirm_preferences: bool
    ) -> None:
        previous = json.loads(payload)
        approved = previous[3] if len(previous) > 3 else False
        if (
            request_plan(json.loads(previous[0])) != request_plan(plan)
            or previous[1:3] != [due_at, schedule_id]
            or approved != confirm_preferences
        ):
            raise Conflict("Request ID already used with different inputs")

    def replay_creation(
        self, plan: Plan, request_id: str, due_at: float = 0, confirm_preferences: bool = False
    ) -> dict[str, Any] | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM task WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                return None
            self._creation_matches(row["request_payload"], plan, due_at, "", confirm_preferences)
            return self._detail(db, row["id"])

    def create(
        self, plan: Plan, request_id: str, due_at: float = 0, confirm_preferences: bool = False
    ) -> dict[str, Any]:
        with self.transaction() as db:
            task_id = self._create(
                db, plan, request_id, due_at, confirm_preferences=confirm_preferences
            )
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
                "UPDATE task SET revision=revision+1,title=?,status=? WHERE id=?",
                (plan.title, "paused" if task["status"] == "paused" else "queued", task_id),
            )
            self._plan(db, task_id, expected + 1, plan, reason)
        return self.get(task_id)

    def _mutation(self, db: sqlite3.Connection, request_id: str, payload: Any) -> bool:
        if not request_id:
            return False  # Internal callers rely on revision and state fencing.
        encoded = mutation_payload(payload)
        old = db.execute("SELECT payload FROM mutation WHERE id=?", (request_id,)).fetchone()
        if old:
            if mutation_payload(json.loads(old["payload"])) != encoded:
                raise Conflict("Mutation request ID already used with different inputs")
            return True
        db.execute("INSERT INTO mutation VALUES(?,?)", (request_id, encoded))
        return False

    def replay_revision(
        self, task_id: str, expected: int, plan: Plan, reason: str, request_id: str
    ) -> dict[str, Any] | None:
        with self.transaction() as db:
            row = db.execute("SELECT payload FROM mutation WHERE id=?", (request_id,)).fetchone()
            if row is None:
                return None
            wanted = [task_id, "revise", expected, plan.model_dump(), reason]
            if mutation_payload(json.loads(row["payload"])) != mutation_payload(wanted):
                raise Conflict("Mutation request ID already used with different inputs")
            return self._detail(db, task_id)

    @staticmethod
    def _expected(task: dict[str, Any], expected: int) -> None:
        if task["revision"] != expected:
            raise Conflict("Plan changed; reload before applying this action")

    def control(
        self,
        task_id: str,
        expected: int,
        action: str,
        request_id: str = "",
        *,
        allow_human_return: bool = False,
    ) -> dict[str, Any]:
        with self.transaction() as db:
            if self._mutation(db, request_id, [task_id, action, expected]):
                return self._detail(db, task_id)
            task = self._task(db, task_id)
            self._expected(task, expected)
            owner = db.execute(
                "SELECT task_id FROM manual_session WHERE task_id=?", (task_id,)
            ).fetchone()
            if owner is not None and action in {"resume", "cancel"} and not allow_human_return:
                raise Conflict("Return manual control in the task screen before automatic work")
            target = {"pause": "paused", "resume": "queued", "cancel": "cancelling"}
            if action not in target or task["status"] in {"completed", "cancelled", "cancelling"}:
                raise Conflict("Action unavailable")
            if action == "resume" and task["status"] != "paused":
                raise Conflict("Only paused tasks may resume")
            db.execute("UPDATE task SET status=? WHERE id=?", (target[action], task_id))
            if action in {"resume", "cancel"}:
                db.execute("DELETE FROM manual_session WHERE task_id=?", (task_id,))
            self._event(db, task_id, action, {"revision": expected})
        if self.control_hook is not None:
            self.control_hook(task_id, action)
        return self.get(task_id)

    def authorize_action(
        self, task_id: str, step_id: str, expected: int, request_id: str
    ) -> dict[str, Any]:
        """User-only approval of the exact immutable execution preview."""
        with self.transaction() as db:
            if self._mutation(db, request_id, [task_id, "authorize_action", expected, step_id]):
                return self._detail(db, task_id)
            task = self._task(db, task_id)
            self._expected(task, expected)
            if task["status"] in {"paused", "cancelling", "cancelled", "completed"}:
                raise Conflict("Task cannot accept execution approval now")
            row = db.execute(
                "SELECT * FROM step WHERE id=? AND task_id=? AND revision=?",
                (step_id, task_id, expected),
            ).fetchone()
            if row is None or json.loads(row["spec"])["adapter"] not in EXECUTION_ADAPTERS:
                raise Conflict("This is not a current execution step")
            first = db.execute(
                "SELECT id FROM step WHERE task_id=? AND revision=? AND status!='completed' "
                "ORDER BY position LIMIT 1",
                (task_id, expected),
            ).fetchone()
            if row["status"] != "waiting_user" or first is None or first[0] != step_id:
                raise Conflict("Review the next execution step before approving it")
            db.execute(
                "INSERT OR REPLACE INTO step_approval VALUES(?,?,?)",
                (
                    step_id,
                    hashlib.sha256(row["spec"].encode()).hexdigest(),
                    self.clock(),
                ),
            )
            db.execute("UPDATE step SET status='pending' WHERE id=?", (step_id,))
            db.execute("UPDATE task SET status='queued' WHERE id=?", (task_id,))
            self._event(
                db, task_id, "execution_approved", {"step_id": step_id, "revision": expected}
            )
        return self.get(task_id)

    def handoff(self, task_id: str, expected: int, request_id: str) -> dict[str, Any]:
        with self.transaction() as db:
            if self._mutation(db, request_id, [task_id, "take_over", expected]):
                return self._detail(db, task_id)
            task = self._task(db, task_id)
            self._expected(task, expected)
            if task["status"] in {"completed", "cancelled", "cancelling"}:
                raise Conflict("Task cannot be handed over now")
            owner = db.execute("SELECT task_id FROM manual_session").fetchone()
            if owner is not None and owner[0] != task_id:
                raise Conflict("Another task is under human control; return it first")
            db.execute("UPDATE task SET status='paused' WHERE id=?", (task_id,))
            db.execute("INSERT OR REPLACE INTO manual_session VALUES(1,?)", (task_id,))
            self._event(db, task_id, "human_takeover", {"revision": expected})
        if self.control_hook is not None:
            self.control_hook(task_id, "take_over")
        return self.get(task_id)

    def manual_owner(self) -> str:
        with self.transaction() as db:
            row = db.execute("SELECT task_id FROM manual_session").fetchone()
            return str(row[0]) if row else ""

    def task_status(self, task_id: str) -> str:
        with sqlite3.connect(self.path, timeout=10) as db:
            row = db.execute("SELECT status FROM task WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            return str(row[0])

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
            current = db.execute(
                "SELECT spec FROM step WHERE id=? AND task_id=? AND revision=?",
                (step_id, task_id, expected),
            ).fetchone()
            if current is None or json.loads(current["spec"])["adapter"] != "checkpoint":
                raise Conflict("Only checkpoints accept completion evidence")
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

    def authorize_preference(
        self, task_id: str, step_id: str, expected: int, request_id: str
    ) -> dict[str, Any]:
        """An authenticated user confirms exactly one immutable preference intent."""
        with self.transaction() as db:
            if self._mutation(db, request_id, [task_id, "authorize_preference", expected, step_id]):
                return self._detail(db, task_id)
            task = self._task(db, task_id)
            self._expected(task, expected)
            if task["status"] in {"completed", "cancelled", "cancelling", "paused"}:
                raise Conflict("Task cannot accept confirmation now")
            step = db.execute(
                "SELECT * FROM step WHERE id=? AND task_id=? AND revision=?",
                (step_id, task_id, expected),
            ).fetchone()
            if step is None or json.loads(step["spec"])["adapter"] != "preference":
                raise Conflict("This is not a current preference step")
            if step["status"] not in {"pending", "waiting_user"}:
                raise Conflict("Preference confirmation is no longer available")
            db.execute(
                "INSERT OR REPLACE INTO step_approval VALUES(?,?,?)",
                (step_id, hashlib.sha256(step["spec"].encode()).hexdigest(), self.clock()),
            )
            db.execute("UPDATE step SET status='pending' WHERE id=?", (step_id,))
            if step["status"] == "waiting_user":
                db.execute("UPDATE task SET status='queued' WHERE id=?", (task_id,))
            self._event(
                db, task_id, "preference_confirmed", {"step_id": step_id, "revision": expected}
            )
        return self.get(task_id)

    def preference_authorized(self, step_id: str) -> bool:
        with self.transaction() as db:
            row = db.execute(
                "SELECT s.spec,a.payload_hash FROM step s JOIN step_approval a "
                "ON a.step_id=s.id WHERE s.id=?",
                (step_id,),
            ).fetchone()
            return bool(
                row and row["payload_hash"] == hashlib.sha256(row["spec"].encode()).hexdigest()
            )

    def personal_state(self, step_id: str, *, waiting: bool = False) -> bool:
        with self.transaction() as db:
            row = db.execute(
                "SELECT s.*,t.status AS task_status,t.revision AS current_revision "
                "FROM step s JOIN task t ON t.id=s.task_id WHERE s.id=?",
                (step_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "bound"
                or row["revision"] != row["current_revision"]
            ):
                return False
            if row["task_status"] not in {"running", "queued", "waiting_approval"}:
                return False
            db.execute(
                "UPDATE task SET status=? WHERE id=?",
                ("waiting_approval" if waiting else "running", row["task_id"]),
            )
            return True

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
                {**dict(r), "result": json.loads(r["result"]), "spec": json.loads(r["spec"])}
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
            elif status == "outcome_unknown":
                db.execute(
                    "UPDATE task SET status='needs_attention' WHERE id=? AND status!='paused'",
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
            elif first and first["status"] == "outcome_unknown":
                db.execute("UPDATE task SET status='needs_attention' WHERE id=?", (task_id,))

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
                    if db.execute(
                        "SELECT 1 FROM step WHERE task_id=? AND status='outcome_unknown'",
                        (row["id"],),
                    ).fetchone():
                        db.execute(
                            "UPDATE task SET status='needs_attention' WHERE id=?", (row["id"],)
                        )
                        self._event(db, row["id"], "cancellation_needs_verification", {})
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
        if any(step.adapter not in {"research", "checkpoint"} for step in plan.steps):
            raise ValueError("Personal changes use one-off tasks; reminders own their repeat rules")
        payload = json.dumps(
            {"plan": plan.model_dump(), "first_at": next_at, "interval_seconds": interval_seconds},
            sort_keys=True,
        )
        with self.transaction() as db:
            old = db.execute("SELECT * FROM schedule WHERE request_id=?", (request_id,)).fetchone()
            if old:
                saved = json.loads(old["payload"])
                if (
                    request_plan(saved["plan"]) != request_plan(plan)
                    or saved["first_at"] != next_at
                    or saved["interval_seconds"] != interval_seconds
                ):
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
    "CREATE TABLE IF NOT EXISTS step_approval(step_id TEXT PRIMARY KEY REFERENCES step(id),"
    "payload_hash TEXT NOT NULL,confirmed_at REAL NOT NULL)",
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
