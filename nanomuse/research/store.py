"""Transactional steps, fenced leases and immutable research revisions."""

from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from nanomuse.research.clock import SystemClock, iso
from nanomuse.research.contracts import SCHEMA_VERSION, Clock, ProjectSpec, SourceInput

TABLES = {
    "project",
    "spec_revision",
    "source",
    "source_snapshot",
    "evidence",
    "evidence_conflict",
    "decision_revision",
    "report_revision",
    "impact_event",
    "mute_rule",
    "model_call",
}


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class ResearchStore:
    def __init__(self, directory: Path, clock: Clock | None = None) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(
            directory / "research.db", check_same_thread=False, isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Research database is newer than this application")
        self.db.executescript(Path(__file__).with_name("schema.sql").read_text("utf-8"))
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(research_run)")}
        if "input_version" not in columns:
            self.db.execute("ALTER TABLE research_run ADD COLUMN input_version INTEGER DEFAULT 1")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.lock:
            nested = self.db.in_transaction
            savepoint = uid("tx")
            self.db.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute(f"RELEASE {savepoint}" if nested else "COMMIT")
            except BaseException:
                if nested:
                    self.db.execute(f"ROLLBACK TO {savepoint}")
                    self.db.execute(f"RELEASE {savepoint}")
                else:
                    self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self.db.close()

    def get(self, table: str, record_id: str) -> dict[str, Any]:
        if table not in TABLES and table != "notification":
            raise ValueError("Unknown research table")
        with self.lock:
            row = self.db.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        return dict(json.loads(row[0]))

    def list(self, table: str, project_id: str | None = None) -> builtins.list[dict[str, Any]]:
        if table not in TABLES and table != "notification":
            raise ValueError("Unknown research table")
        with self.lock:
            rows = self.db.execute(
                f"SELECT data FROM {table}" + (" WHERE project_id=?" if project_id else ""),
                (project_id,) if project_id else (),
            ).fetchall()
        return [dict(json.loads(row[0])) for row in rows]

    def _put(self, table: str, value: dict[str, Any]) -> dict[str, Any]:
        if table not in TABLES:
            raise ValueError("Unknown research table")
        if table in {"spec_revision", "source_snapshot", "report_revision"}:
            row = self.db.execute(f"SELECT data FROM {table} WHERE id=?", (value["id"],)).fetchone()
            if row is not None:
                previous = dict(json.loads(row[0]))
                volatile = {"fetched_at", "checked_at", "etag", "not_modified"}
                before = {k: v for k, v in previous.items() if k not in volatile}
                after = {k: v for k, v in value.items() if k not in volatile}
                if before != after:
                    raise ValueError("An immutable research revision cannot be overwritten")
                return previous
        self.db.execute(
            f"INSERT INTO {table}(id,project_id,data) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (value["id"], value["project_id"], json.dumps(value, ensure_ascii=False)),
        )
        return value

    def create_project(self, spec: ProjectSpec) -> dict[str, Any]:
        project_id, spec_id = uid("project"), uid("spec")
        with self.transaction():
            self._put(
                "spec_revision",
                {
                    **spec.model_dump(),
                    "id": spec_id,
                    "project_id": project_id,
                    "created_at": iso(self.clock.now()),
                    "revision": 1,
                },
            )
            project = self._put(
                "project",
                {
                    "id": project_id,
                    "project_id": project_id,
                    "title": spec.title,
                    "spec_id": spec_id,
                    "current_spec_id": spec_id,
                    "current_report_id": None,
                    "mode": spec.mode,
                    "input_version": 1,
                    "created_at": iso(self.clock.now()),
                    "tracking": {
                        "enabled": False,
                        "interval_minutes": 1440,
                        "timezone": "UTC",
                        "daily_time": "",
                        "max_calls": 40,
                        "next_check_at": 0,
                    },
                },
            )
            for candidate in spec.candidates:
                self._put(
                    "source",
                    {
                        "id": uid("source"),
                        "project_id": project_id,
                        "candidate": candidate,
                        "kind": "readme",
                        "url": candidate,
                        "content": "",
                        "title": candidate,
                        "enabled": True,
                    },
                )
        return project

    def revise(self, project_id: str, spec: ProjectSpec) -> dict[str, Any]:
        with self.transaction():
            project = self.get("project", project_id)
            previous = self.get("spec_revision", project["spec_id"])
            if spec.mode != previous["mode"]:
                raise ValueError("Create a new project to change live or fixture mode")
            revision = self._put(
                "spec_revision",
                {
                    **spec.model_dump(),
                    "id": uid("spec"),
                    "project_id": project_id,
                    "revision": previous["revision"] + 1,
                    "created_at": iso(self.clock.now()),
                },
            )
            project.update(spec_id=revision["id"], current_spec_id=revision["id"], title=spec.title)
            self._put("project", project)
            for source in self.list("source", project_id):
                source["enabled"] = (
                    not source["candidate"] or source["candidate"] in spec.candidates
                )
                self._put("source", source)
            known = {s["candidate"] for s in self.list("source", project_id)}
            for candidate in spec.candidates:
                if candidate not in known:
                    self._put(
                        "source",
                        {
                            "id": uid("source"),
                            "project_id": project_id,
                            "candidate": candidate,
                            "kind": "readme",
                            "url": candidate,
                            "content": "",
                            "title": candidate,
                            "enabled": True,
                        },
                    )
            for decision in self.list("decision_revision", project_id):
                decision["status"] = "stale"
                self._put("decision_revision", decision)
            self._invalidate_inputs(project_id, "Conditions changed")
            self.enqueue(project_id, "research", f"revision:{revision['id']}")
        return revision

    def _invalidate_inputs(self, project_id: str, reason: str) -> None:
        """Fence every in-flight result after an owner changes the research inputs."""
        project = self.get("project", project_id)
        project["input_version"] = project.get("input_version", 1) + 1
        self._put("project", project)
        self.db.execute(
            "UPDATE research_step SET status='superseded' WHERE project_id=? "
            "AND status IN ('pending','running','failed') AND run_id IN "
            "(SELECT id FROM research_run WHERE status IN ('queued','running','paused','needs_attention','failed'))",
            (project_id,),
        )
        self.db.execute(
            "UPDATE research_run SET status='cancelled',error=? "
            "WHERE project_id=? AND status IN ('queued','running','paused','needs_attention')",
            (reason, project_id),
        )

    def add_source(self, project_id: str, source: SourceInput) -> dict[str, Any]:
        project = self.get("project", project_id)
        spec = self.get("spec_revision", project["spec_id"])
        if source.candidate not in spec["candidates"]:
            raise ValueError("Select a candidate repository for this source")
        if source.kind != "manual" and not source.url:
            raise ValueError("A public source URL is required")
        if source.kind == "manual" and not source.content.strip():
            raise ValueError("Manual source content is empty")
        with self.transaction():
            restart = bool(project["current_report_id"]) or any(
                run["status"] in {"queued", "running", "paused", "needs_attention"}
                for run in self.runs(project_id)
            )
            value = self._put(
                "source",
                {
                    **source.model_dump(),
                    "id": uid("source"),
                    "project_id": project_id,
                    "enabled": True,
                },
            )
            self._invalidate_inputs(project_id, "A source was added")
            if restart:
                self.enqueue(project_id, request_key=f"source:{value['id']}")
            return value

    def project(self, project_id: str) -> dict[str, Any]:
        project = self.get("project", project_id)
        report = (
            self.get("report_revision", project["current_report_id"])
            if project["current_report_id"]
            else None
        )
        if report:
            report["stale"] = (
                report["spec_id"] != project["spec_id"]
                or report.get("input_version", 1) != project.get("input_version", 1)
            ) or any(
                d["status"] == "stale" and d["id"] in report.get("decision_refs", [])
                for d in self.list("decision_revision", project_id)
            )
        return {
            **project,
            "spec": self.get("spec_revision", project["spec_id"]),
            "sources": self.list("source", project_id),
            "runs": self.runs(project_id),
            "current_report": report,
        }

    def runs(self, project_id: str) -> builtins.list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM research_run WHERE project_id=? ORDER BY created_at DESC,rowid DESC",
                (project_id,),
            ).fetchall()
        return [{**dict(row), "created_at": iso(row["created_at"])} for row in rows]

    def run(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute("SELECT * FROM research_run WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            steps = self.db.execute(
                "SELECT * FROM research_step WHERE run_id=? ORDER BY position", (run_id,)
            ).fetchall()
        return {
            **dict(row),
            "created_at": iso(row["created_at"]),
            "steps": [{**dict(s), "output": json.loads(s["output"])} for s in steps],
        }

    def enqueue(
        self,
        project_id: str,
        kind: str = "research",
        request_key: str | None = None,
        missed_from: float | None = None,
        missed_to: float | None = None,
    ) -> dict[str, Any]:
        if kind not in {"research", "check"}:
            raise ValueError("Unknown run kind")
        request_key = request_key or uid("request")
        with self.transaction():
            project = self.get("project", project_id)
            previous = self.db.execute(
                "SELECT id FROM research_run WHERE project_id=? AND request_key=?",
                (project_id, request_key),
            ).fetchone()
            if previous:
                return self.run(previous[0])
            run_id = uid("run")
            self.db.execute(
                "INSERT INTO research_run(id,project_id,spec_id,kind,status,request_key,created_at,missed_from,missed_to,input_version) "
                "VALUES(?,?,?,?,'queued',?,?,?,?,?)",
                (
                    run_id,
                    project_id,
                    project["spec_id"],
                    kind,
                    request_key,
                    self.clock.now(),
                    missed_from,
                    missed_to,
                    project.get("input_version", 1),
                ),
            )
            stages = [("collect", s["id"]) for s in self.list("source", project_id) if s["enabled"]]
            stages += [(stage, "") for stage in ("extract", "compare", "publish")]
            for index, (stage, source_id) in enumerate(stages):
                self.db.execute(
                    "INSERT INTO research_step(id,project_id,run_id,position,stage,source_id) VALUES(?,?,?,?,?,?)",
                    (uid("step"), project_id, run_id, index, stage, source_id),
                )
        return self.run(run_id)

    def claim(self, owner: str, lease_seconds: float = 60) -> dict[str, Any] | None:
        now = self.clock.now()
        with self.transaction():
            expired = {
                row[0]
                for row in self.db.execute(
                    "SELECT id FROM research_step WHERE status='running' AND lease_until<=?",
                    (now,),
                )
            }
            if expired:
                for call in self.list("model_call"):
                    if call.get("step_id") in expired and call["status"] == "running":
                        call.update(status="unknown", error_type="interrupted")
                        self._put("model_call", call)
            self.db.execute(
                "UPDATE research_step SET status='pending',lease_owner='' WHERE status='running' AND lease_until<=?",
                (now,),
            )
            if self.db.execute(
                "SELECT 1 FROM research_step WHERE status='running' AND lease_until>?", (now,)
            ).fetchone():
                return None
            row = self.db.execute(
                "SELECT s.* FROM research_step s JOIN research_run r ON r.id=s.run_id "
                "WHERE s.status='pending' AND s.retry_at<=? AND r.status IN ('queued','running') "
                "AND NOT EXISTS(SELECT 1 FROM research_step p WHERE p.run_id=s.run_id AND p.position<s.position AND p.status!='succeeded') "
                "ORDER BY r.created_at,r.rowid,s.position LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            step = dict(row)
            run = self.run(step["run_id"])
            input_hash = fingerprint(
                [run["spec_id"], run["input_version"], step["stage"], step["source_id"]]
            )
            self.db.execute(
                "UPDATE research_step SET status='running',attempt=attempt+1,lease_owner=?,lease_until=?,input_hash=? WHERE id=?",
                (owner, now + lease_seconds, input_hash, step["id"]),
            )
            self.db.execute(
                "UPDATE research_run SET status='running' WHERE id=?", (step["run_id"],)
            )
            step.update(
                attempt=step["attempt"] + 1,
                lease_owner=owner,
                lease_until=now + lease_seconds,
                input_hash=input_hash,
                status="running",
            )
            return step

    def _owns(self, step: dict[str, Any]) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM research_step s JOIN research_run r ON r.id=s.run_id "
                "JOIN project p ON p.id=r.project_id WHERE s.id=? AND s.status='running' "
                "AND s.attempt=? AND s.lease_owner=? AND s.lease_until>? "
                "AND s.input_hash=? AND r.input_version=coalesce(json_extract(p.data,'$.input_version'),1) "
                "AND r.status IN ('running','paused') AND r.spec_id=json_extract(p.data,'$.spec_id')",
                (
                    step["id"],
                    step["attempt"],
                    step["lease_owner"],
                    self.clock.now(),
                    step.get("input_hash", ""),
                ),
            ).fetchone()
            is not None
        )

    def renew(self, step: dict[str, Any], seconds: float = 60) -> bool:
        with self.transaction():
            if not self._owns(step):
                return False
            self.db.execute(
                "UPDATE research_step SET lease_until=? WHERE id=?",
                (self.clock.now() + seconds, step["id"]),
            )
            return True

    def complete(self, step: dict[str, Any], output: dict[str, Any]) -> bool:
        with self.transaction():
            if not self._owns(step):
                return False
            project_id = step["project_id"]
            run = self.run(step["run_id"])
            if output.get("no_changes"):
                self.db.execute(
                    "UPDATE research_step SET status='succeeded',output='{}',lease_until=0 WHERE run_id=? AND status IN ('pending','running')",
                    (run["id"],),
                )
                self.db.execute(
                    "UPDATE research_run SET status='succeeded' WHERE id=?", (run["id"],)
                )
                return True
            if step["stage"] == "collect" and "snapshot" in output:
                snapshot = output["snapshot"]
                self._put("source_snapshot", snapshot)
                source = self.get("source", step["source_id"])
                source.update(
                    latest_snapshot_id=snapshot["id"],
                    error="",
                    checked_at=iso(self.clock.now()),
                    etag=snapshot.get("etag", ""),
                )
                self._put("source", source)
                previous = output.get("previous_snapshot")
                if previous and previous != snapshot["id"]:
                    for evidence in self.list("evidence", project_id):
                        if evidence["snapshot_id"] == previous:
                            evidence["status"] = "stale"
                            self._put("evidence", evidence)
                            for decision in self.list("decision_revision", project_id):
                                if evidence["id"] in decision["evidence_refs"]:
                                    decision["status"] = "stale"
                                    self._put("decision_revision", decision)
                    event_id = (
                        "change_"
                        + fingerprint([step["source_id"], snapshot["id"], run["spec_id"]])[:20]
                    )
                    if not any(e["id"] == event_id for e in self.list("impact_event", project_id)):
                        self._put(
                            "impact_event",
                            {
                                "id": event_id,
                                "project_id": project_id,
                                "source_id": step["source_id"],
                                "before_snapshot": previous,
                                "after_snapshot": snapshot["id"],
                                "impact_level": "uncertain",
                                "status": "pending",
                                "change_summary": "Source content changed",
                                "spec_id": run["spec_id"],
                                "created_at": iso(self.clock.now()),
                            },
                        )
                output = {"snapshot_id": snapshot["id"], "changed": previous != snapshot["id"]}
            elif step["stage"] == "extract":
                from nanomuse.research.context import validate_evidence
                from nanomuse.research.contracts import EvidenceDraft

                spec_record = self.get("spec_revision", run["spec_id"])
                for evidence in output.get("evidence", []):
                    snapshot = self.get("source_snapshot", evidence["snapshot_id"])
                    if (
                        snapshot["project_id"] != project_id
                        or evidence["quote"] not in snapshot["content"]
                    ):
                        raise ValueError("Evidence quote must occur in this project's snapshot")
                    validate_evidence(
                        EvidenceDraft.model_validate(evidence),
                        {
                            "project_id": project_id,
                            "spec": {k: spec_record[k] for k in ProjectSpec.model_fields},
                            "snapshots": self.snapshots(project_id),
                        },
                    )
                    evidence = {
                        **evidence,
                        "id": "evidence_"
                        + fingerprint([snapshot["id"], evidence["fact"], evidence["dimension"]])[
                            :20
                        ],
                        "project_id": project_id,
                        "status": "active",
                        "source_url": snapshot["url"],
                        "version": snapshot["version"],
                        "fetched_at": snapshot["fetched_at"],
                    }
                    previous = self.db.execute(
                        "SELECT data FROM evidence WHERE id=?", (evidence["id"],)
                    ).fetchone()
                    if previous and json.loads(previous[0])["status"] == "superseded":
                        continue
                    self._put("evidence", evidence)
                verified = set(output.get("verified_snapshots", []))
                for old in self.list("evidence", project_id):
                    if old["snapshot_id"] in verified and old["status"] == "stale":
                        old["status"] = "superseded"
                        self._put("evidence", old)
            elif step["stage"] == "compare":
                from nanomuse.research.context import validate_comparison
                from nanomuse.research.contracts import Comparison

                spec_record = self.get("spec_revision", run["spec_id"])
                comparison = validate_comparison(
                    Comparison.model_validate(output["comparison"]),
                    {
                        "project_id": project_id,
                        "spec": {k: spec_record[k] for k in ProjectSpec.model_fields},
                        "snapshots": self.snapshots(project_id),
                        "evidence": self.list("evidence", project_id),
                        "conflicts": [
                            c
                            for c in self.list("evidence_conflict", project_id)
                            if c["status"] == "open"
                            and all(
                                self.get("evidence", ref)["status"] == "active"
                                for ref in c["evidence_refs"]
                            )
                        ],
                    },
                )
                output["comparison"] = comparison.model_dump()
                for old in self.list("evidence_conflict", project_id):
                    if old["status"] == "open":
                        old["status"] = "superseded"
                        self._put("evidence_conflict", old)
                for index, conflict in enumerate(comparison.conflicts):
                    self._put(
                        "evidence_conflict",
                        {
                            **conflict.model_dump(),
                            "id": f"conflict_{run['id']}_{index}",
                            "project_id": project_id,
                            "spec_id": run["spec_id"],
                            "run_id": run["id"],
                            "status": "open",
                            "created_at": iso(self.clock.now()),
                        },
                    )
                for index, decision in enumerate(output["comparison"]["decisions"]):
                    self._put(
                        "decision_revision",
                        {
                            **decision,
                            "id": f"decision_{run['id']}_{index}",
                            "project_id": project_id,
                            "spec_id": run["spec_id"],
                            "run_id": run["id"],
                            "status": "active",
                        },
                    )
            elif step["stage"] == "publish":
                report = output["report"]
                self._put("report_revision", report)
                project = self.get("project", project_id)
                project["current_report_id"] = report["id"]
                self._put("project", project)
                for event in output.get("events", []):
                    self._put("impact_event", event)
                    muted = any(
                        m.get("enabled", True)
                        and m["source_id"] == event["source_id"]
                        and m["impact_level"] == event["impact_level"]
                        for m in self.list("mute_rule", project_id)
                    )
                    if event["impact_level"] == "recommendation" and not muted:
                        self._notify(project_id, event["id"], event["change_summary"], event["id"])
                if run["kind"] == "research":
                    self._notify(project_id, report["id"], "Research report is ready", report["id"])
                self.db.execute(
                    "UPDATE research_run SET status='succeeded',error='' WHERE id=?", (run["id"],)
                )
            self.db.execute(
                "UPDATE research_step SET status='succeeded',output=?,error='',lease_until=0 WHERE id=?",
                (json.dumps(output, ensure_ascii=False), step["id"]),
            )
            return True

    def _notify(self, project_id: str, dedupe_key: str, content: str, event_id: str) -> None:
        value = {
            "id": uid("notification"),
            "project_id": project_id,
            "content": content,
            "event_id": event_id,
            "feedback": "",
            "created_at": iso(self.clock.now()),
        }
        self.db.execute(
            "INSERT OR IGNORE INTO notification VALUES(?,?,?,?)",
            (value["id"], project_id, dedupe_key, json.dumps(value, ensure_ascii=False)),
        )

    def fail(
        self,
        step: dict[str, Any],
        error: str,
        retryable: bool = False,
        needs_attention: bool = False,
        retry_after: float = 1,
    ) -> None:
        with self.transaction():
            if not self._owns(step):
                return
            retry = retryable and step["attempt"] < 3
            if not retry and step["stage"] == "collect" and not needs_attention:
                source = self.get("source", step["source_id"])
                source["error"] = error[:1000]
                self._put("source", source)
                self.db.execute(
                    "UPDATE research_step SET status='succeeded',error=?,output=?,lease_until=0 WHERE id=?",
                    (error[:1000], json.dumps({"source_error": error[:1000]}), step["id"]),
                )
                return
            self.db.execute(
                "UPDATE research_step SET status=?,error=?,retry_at=?,lease_until=0 WHERE id=?",
                (
                    "pending" if retry else "failed",
                    error[:1000],
                    self.clock.now() + retry_after,
                    step["id"],
                ),
            )
            self.db.execute(
                "UPDATE research_run SET status=?,error=? WHERE id=?",
                (
                    "running" if retry else "needs_attention" if needs_attention else "failed",
                    error[:1000],
                    step["run_id"],
                ),
            )

    def control(self, run_id: str, action: str) -> dict[str, Any]:
        with self.transaction():
            run = self.run(run_id)
            status = run["status"]
            if action == "pause" and status in {"queued", "running"}:
                status = "paused"
            elif action in {"resume", "retry"} and status in {
                "paused",
                "failed",
                "needs_attention",
            }:
                project = self.get("project", run["project_id"])
                if (
                    project["spec_id"] != run["spec_id"]
                    or project.get("input_version", 1) != run["input_version"]
                ):
                    raise ValueError("This run belongs to previous conditions")
                status = "queued"
                self.db.execute(
                    "UPDATE research_step SET status='pending',retry_at=0 WHERE run_id=? AND status='failed'",
                    (run_id,),
                )
            elif action == "cancel" and status not in {"succeeded", "cancelled"}:
                status = "cancelled"
                self.db.execute(
                    "UPDATE research_step SET status='cancelled' WHERE run_id=? AND status IN ('pending','running')",
                    (run_id,),
                )
            else:
                raise ValueError("Action is not available for this run")
            self.db.execute(
                "UPDATE research_run SET status=?,error='' WHERE id=?", (status, run_id)
            )
        return self.run(run_id)

    def snapshots(self, project_id: str) -> builtins.list[dict[str, Any]]:
        sources = [s for s in self.list("source", project_id) if s["enabled"]]
        if all(s.get("latest_snapshot_id") for s in sources):
            return [self.get("source_snapshot", s["latest_snapshot_id"]) for s in sources]
        latest: dict[str, dict[str, Any]] = {}
        for snapshot in self.list("source_snapshot", project_id):
            old = latest.get(snapshot["source_id"])
            if old is None or snapshot["fetched_at"] >= old["fetched_at"]:
                latest[snapshot["source_id"]] = snapshot
        enabled = {s["id"] for s in self.list("source", project_id) if s["enabled"]}
        return [s for k, s in latest.items() if k in enabled]

    def correction(
        self, evidence_id: str, fact: str, reason: str, quote: str | None = None
    ) -> dict[str, Any]:
        if not fact.strip() or not reason.strip():
            raise ValueError("A correction and its reason are required")
        with self.transaction():
            old = self.get("evidence", evidence_id)
            if quote is not None and (
                not quote.strip()
                or quote not in self.get("source_snapshot", old["snapshot_id"])["content"]
            ):
                raise ValueError("The supporting quote must occur in the original snapshot")
            old["status"] = "superseded"
            self._put("evidence", old)
            new = self._put(
                "evidence",
                {
                    **old,
                    "id": uid("evidence"),
                    "fact": fact,
                    "status": "active",
                    "verification": "user_correction",
                    "quote": quote if quote is not None else old["quote"],
                    "reason": reason,
                    "supersedes": old["id"],
                },
            )
            for decision in self.list("decision_revision", old["project_id"]):
                if old["id"] in decision["evidence_refs"]:
                    decision["status"] = "stale"
                    self._put("decision_revision", decision)
            self._invalidate_inputs(old["project_id"], "Evidence was corrected")
            self.enqueue(old["project_id"], request_key=f"correction:{new['id']}")
        return new

    def reverify(self, evidence_id: str) -> dict[str, Any]:
        with self.transaction():
            item = self.get("evidence", evidence_id)
            item["status"] = "stale"
            self._put("evidence", item)
            for decision in self.list("decision_revision", item["project_id"]):
                if item["id"] in decision["evidence_refs"]:
                    decision["status"] = "stale"
                    self._put("decision_revision", decision)
            self._invalidate_inputs(item["project_id"], "Evidence needs verification")
            return self.enqueue(item["project_id"])

    def record_calls(
        self, project_id: str, run_id: str, step_id: str, calls: builtins.list[dict[str, Any]]
    ) -> None:
        with self.transaction():
            for call in calls:
                self._put(
                    "model_call",
                    {
                        **call,
                        "id": call.get("id") or uid("call"),
                        "project_id": project_id,
                        "run_id": run_id,
                        "step_id": step_id,
                    },
                )
