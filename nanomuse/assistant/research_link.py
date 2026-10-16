"""Recoverable links to the existing research module, without another executor."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from nanomuse.assistant.errors import Conflict
from nanomuse.assistant.ledger import Ledger, Plan
from nanomuse.research.contracts import ProjectSpec
from nanomuse.research.impact import mute_scope
from nanomuse.research.store import ResearchStore, fingerprint


class ResearchLink:
    """Journal domain mutations atomically, then repair the independent ledger link.

    Only additive metadata is stored alongside research. Projects, source settings,
    reports, budgets and run execution remain owned by ResearchStore/Orchestrator.
    """

    def __init__(self, ledger: Ledger, store: ResearchStore):
        self.ledger = ledger
        self.store = store
        with store.lock:
            initialized = store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assistant_research_request'"
            ).fetchone()
            if not initialized and store.db.execute("SELECT 1 FROM project LIMIT 1").fetchone():
                backup = store.directory / f"research.pre-assistant-link.{uuid.uuid4().hex}.db"
                with sqlite3.connect(backup) as target:
                    store.db.backup(target)
            with store.transaction():
                store.db.execute(
                    "CREATE TABLE IF NOT EXISTS assistant_research_request ("
                    "id TEXT PRIMARY KEY,payload TEXT NOT NULL,data TEXT NOT NULL)"
                )
                store.db.execute(
                    "CREATE TABLE IF NOT EXISTS assistant_research_delivery ("
                    "id TEXT PRIMARY KEY,data TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0)"
                )
                if not initialized:
                    # Existing notices stay in the research UI. Migration must not flood chat.
                    for notice in store.list("notification"):
                        store.db.execute(
                            "INSERT OR IGNORE INTO assistant_research_delivery VALUES(?,?,1)",
                            (self.delivery_id(notice["id"]), "{}"),
                        )

    @staticmethod
    def delivery_id(notice_id: str) -> str:
        return f"research:{notice_id}"

    @staticmethod
    def _request_id(request_id: str) -> str:
        if not request_id.strip() or len(request_id) > 150:
            raise ValueError("A stable request_id of 1 to 150 characters is required")
        return f"research-link:{fingerprint(request_id)}"

    def _old(self, request_id: str, payload: str) -> dict[str, Any] | None:
        row = self.store.db.execute(
            "SELECT payload,data FROM assistant_research_request WHERE id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        if row["payload"] != payload:
            raise Conflict("Research request ID already used with different inputs")
        return dict(json.loads(row["data"]))

    def create(self, spec: ProjectSpec, request_id: str) -> dict[str, Any]:
        return self._mutate("create", spec, request_id)

    def revise(
        self, project_id: str, spec: ProjectSpec, expected_spec_id: str, request_id: str
    ) -> dict[str, Any]:
        if not expected_spec_id:
            raise ValueError("Reload the research project before revising its conditions")
        return self._mutate("revise", spec, request_id, project_id, expected_spec_id)

    def _mutate(
        self,
        action: str,
        spec: ProjectSpec,
        request_id: str,
        project_id: str = "",
        expected_spec_id: str = "",
    ) -> dict[str, Any]:
        key = self._request_id(request_id)
        payload = json.dumps(
            [action, spec.model_dump(), project_id, expected_spec_id], sort_keys=True
        )
        with self.store.transaction():
            record = self._old(key, payload)
            if record is None:
                if action == "create":
                    project = self.store.create_project(spec)
                    run = self.store.enqueue(project["id"], "research", key)
                else:
                    project = self.store.project(project_id)
                    if project["spec_id"] != expected_spec_id:
                        raise Conflict("Research conditions changed; reload the project")
                    revision = self.store.revise(project_id, spec)
                    project = self.store.project(project_id)
                    run = next(
                        r
                        for r in self.store.runs(project_id)
                        if r["request_key"] == f"revision:{revision['id']}"
                    )
                # Nothing can execute before the task ledger claims this intent. A crash
                # or user pause between the two database commits must leave the run held.
                run = self.store.control(run["id"], "pause")
                plan = Plan.model_validate(
                    {
                        "title": spec.title
                        if action == "create"
                        else f"{spec.title}: conditions updated / 条件已更新",
                        "success_criteria": (
                            "A version-bound research report is published. / "
                            "产出与本次条件版本绑定的调研报告。"
                        ),
                        "steps": [
                            {
                                "adapter": "research",
                                "title": spec.title,
                                "project_id": project["id"],
                                "spec_id": project["spec_id"],
                                "input_version": project["input_version"],
                            }
                        ],
                    }
                )
                record = {
                    "request_id": key,
                    "project_id": project["id"],
                    "run_id": run["id"],
                    "spec_id": project["spec_id"],
                    "input_version": project["input_version"],
                    "action": action,
                    "plan": plan.model_dump(),
                    "task_id": "",
                    "step_id": "",
                    "previous_task_ids": [
                        old["task_id"]
                        for row in self.store.db.execute(
                            "SELECT data FROM assistant_research_request"
                        )
                        if (old := json.loads(row[0]))["project_id"] == project["id"]
                        and old["task_id"]
                    ],
                }
                self.store.db.execute(
                    "INSERT INTO assistant_research_request VALUES(?,?,?)",
                    (key, payload, json.dumps(record, ensure_ascii=False)),
                )
        return self._link(record)

    def _link(self, record: dict[str, Any]) -> dict[str, Any]:
        task = self.ledger.create(Plan.model_validate(record["plan"]), record["request_id"])
        record.update(task_id=task["id"], step_id=task["steps"][0]["id"])
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE assistant_research_request SET data=? WHERE id=?",
                (json.dumps(record, ensure_ascii=False), record["request_id"]),
            )
        return {
            "project_id": record["project_id"],
            "spec_id": record["spec_id"],
            "run_id": record["run_id"],
            "previous_task_ids": record["previous_task_ids"],
            "task": task,
        }

    def recover(self) -> None:
        """Finish links lost between research and task commits, including after restart."""
        with self.store.lock:
            records = [
                json.loads(row[0])
                for row in self.store.db.execute("SELECT data FROM assistant_research_request")
            ]
        for record in records:
            if not record["task_id"]:
                self._link(record)
            task = self.ledger.get(record["task_id"])
            step = next(item for item in task["steps"] if item["id"] == record["step_id"])
            run = self.store.run(record["run_id"])
            if (
                task["status"] in {"cancelling", "cancelled"} or step["status"] == "superseded"
            ) and run["status"] not in {"succeeded", "cancelled"}:
                self.store.control(run["id"], "cancel")
            elif task["status"] == "paused" and run["status"] in {"queued", "running"}:
                self.store.control(run["id"], "pause")

    def run_for_step(self, step: dict[str, Any]) -> dict[str, Any] | None:
        self.recover()
        with self.store.lock:
            rows = self.store.db.execute("SELECT data FROM assistant_research_request").fetchall()
        for row in rows:
            record = json.loads(row[0])
            if record["step_id"] == step["id"] and record["task_id"] == step["task_id"]:
                run = self.store.run(record["run_id"])
                if (
                    run["project_id"] != step["spec"]["project_id"]
                    or run["spec_id"] != step["spec"]["spec_id"]
                    or run["input_version"] != step["spec"]["input_version"]
                ):
                    raise Conflict("Research run does not match the immutable task inputs")
                return run
        return None

    def projects(self) -> list[dict[str, Any]]:
        """Read existing projects without adopting or re-creating them."""
        return [
            {
                key: value.get(key)
                for key in ("id", "title", "mode", "spec_id", "input_version", "current_report_id")
            }
            for value in self.store.list("project")
        ]

    def project(self, project_id: str) -> dict[str, Any]:
        value = self.store.get("project", project_id)
        report_id = value.get("current_report_id")
        report = self.store.get("report_revision", report_id) if report_id else None
        if report:
            from nanomuse.research.reports import semantic_review_status

            markdown = str(report.get("markdown", ""))
            report = {
                **report,
                "markdown": markdown[:40000],
                "truncated": len(markdown) > 40000,
                "stale": report["spec_id"] != value["spec_id"]
                or report.get("input_version", 1) != value.get("input_version", 1),
                "semantic_review": report.get("semantic_review")
                or semantic_review_status(self.store.list("evidence", project_id)),
            }
        return {
            "project": value,
            "spec": self.store.get("spec_revision", value["spec_id"]),
            "sources": [
                {key: source.get(key) for key in ("id", "candidate", "kind", "title", "enabled")}
                for source in self.store.list("source", project_id)
            ],
            "report": report,
            "runs": [
                {key: run.get(key) for key in ("id", "spec_id", "kind", "status", "input_version")}
                for run in self.store.runs(project_id)[:20]
            ],
            "source_content_is_untrusted": True,
        }

    def _delivery(self, notice: dict[str, Any]) -> dict[str, Any]:
        project = self.store.get("project", notice["project_id"])
        report = next(
            (
                item
                for item in self.store.list("report_revision", project["id"])
                if item["id"] == notice["event_id"]
            ),
            None,
        )
        task_id = ""
        event = next(
            (
                item
                for item in self.store.list("impact_event", project["id"])
                if item["id"] == notice["event_id"]
            ),
            None,
        )
        with self.store.lock:
            for row in self.store.db.execute("SELECT data FROM assistant_research_request"):
                record = json.loads(row[0])
                if record["project_id"] == project["id"] and (
                    (report is not None and record["run_id"] == report["run_id"])
                    or (event is not None and record["spec_id"] == event["spec_id"])
                ):
                    task_id = record["task_id"]
        return {
            "id": self.delivery_id(notice["id"]),
            "type": "research_report" if report else "research_change",
            "project_id": project["id"],
            "task_id": task_id,
            "report_id": report["id"] if report else "",
            "event_id": notice["event_id"],
            "title": project["title"],
            "content": notice["content"],
            "created_at": notice["created_at"],
            "source_content_is_untrusted": True,
        }

    def _suppressed(self, notice: dict[str, Any]) -> bool:
        if notice.get("feedback") in {"read", "useless"}:
            return True
        event = next(
            (
                item
                for item in self.store.list("impact_event", notice["project_id"])
                if item["id"] == notice["event_id"]
            ),
            None,
        )
        return bool(
            event
            and any(
                rule.get("enabled", True) and rule.get("scope") == mute_scope(event)
                for rule in self.store.list("mute_rule", notice["project_id"])
            )
        )

    def pending_deliveries(self) -> list[dict[str, Any]]:
        """Source notifications are the sole authority for important change eligibility."""
        self.recover()
        with self.store.transaction():
            for notice in self.store.list("notification"):
                delivery_id = self.delivery_id(notice["id"])
                existing = self.store.db.execute(
                    "SELECT delivered FROM assistant_research_delivery WHERE id=?", (delivery_id,)
                ).fetchone()
                if existing and existing["delivered"]:
                    continue
                suppressed = self._suppressed(notice)
                if existing is None:
                    self.store.db.execute(
                        "INSERT INTO assistant_research_delivery VALUES(?,?,?)",
                        (
                            delivery_id,
                            json.dumps(self._delivery(notice), ensure_ascii=False),
                            int(suppressed),
                        ),
                    )
                if suppressed:
                    self.store.db.execute(
                        "UPDATE assistant_research_delivery SET delivered=1 WHERE id=?",
                        (delivery_id,),
                    )
            rows = self.store.db.execute(
                "SELECT data FROM assistant_research_delivery WHERE delivered=0 ORDER BY rowid"
            ).fetchall()
        return [dict(json.loads(row[0])) for row in rows]

    def delivered(self, delivery_id: str) -> None:
        """Called only after the host has durably inserted the stable main timeline event."""
        with self.store.transaction():
            cursor = self.store.db.execute(
                "UPDATE assistant_research_delivery SET delivered=1 WHERE id=?", (delivery_id,)
            )
            if not cursor.rowcount:
                raise KeyError(delivery_id)
