"""Recovery, fencing and adapter ownership, with no provider or network calls."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nanomuse.assistant.api import install_assistant
from nanomuse.assistant.coordinator import Coordinator
from nanomuse.assistant.ledger import Conflict, Ledger, Plan
from nanomuse.config import SentinelSettings
from nanomuse.research.clock import FakeClock
from nanomuse.research.contracts import ProjectSpec
from nanomuse.research.orchestrator import ResearchOrchestrator
from nanomuse.research.store import ResearchStore
from nanomuse.sentinel import Sentinel
from nanomuse.sentinel.audit import AuditLog
from nanomuse.ui import HeadlessUI


def plan(*steps: dict[str, Any]) -> Plan:
    return Plan(
        title="Study",
        success_criteria="Review recorded evidence",
        steps=list(steps)
        or [
            {"adapter": "checkpoint", "title": "Read notes"},
            {"adapter": "checkpoint", "title": "Practice"},
        ],
    )


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "tasks.db", FakeClock().now)


def test_create_dedup_and_concurrent_claim(ledger: Ledger) -> None:
    task = ledger.create(plan(), "request")
    assert ledger.create(plan(), "request")["id"] == task["id"]
    with pytest.raises(Conflict):
        ledger.create(plan({"adapter": "checkpoint", "title": "Different"}), "request")
    with ThreadPoolExecutor(4) as pool:
        claims = list(pool.map(lambda _: Ledger(ledger.path, ledger.clock).claim(), range(4)))
    assert sum(s is not None for s in claims) == 1
    assert ledger.get(task["id"])["steps"][0]["attempt"] == 1


def test_expired_attempt_cannot_commit(tmp_path: Path) -> None:
    clock = FakeClock()
    ledger = Ledger(tmp_path / "tasks.db", clock.now)
    ledger.create(plan(), "first")
    first = ledger.claim()
    assert first
    clock.advance(31)
    assert not ledger.finish_claim(first, "waiting_user", {})
    second = Ledger(ledger.path, clock.now).claim()
    assert second and second["token"] != first["token"]
    assert not ledger.finish_claim(first, "waiting_user", {})
    assert ledger.finish_claim(second, "waiting_user", {})


def test_revision_retains_evidence_invalidates_old_confirmation(ledger: Ledger) -> None:
    task = ledger.create(plan(), "first")
    first = ledger.claim()
    assert first and ledger.finish_claim(first, "waiting_user", {})
    ledger.confirm(task["id"], first["id"], 1, "Notes reviewed")
    second = ledger.claim()
    assert second and ledger.finish_claim(second, "waiting_user", {})
    new = ledger.revise(
        task["id"],
        1,
        plan({"adapter": "checkpoint", "title": "New work"}),
        "User changed requirements",
    )
    assert new["revision"] == 2 and len(new["plans"]) == 2
    assert new["steps"][0]["result"]["user_evidence"] == "Notes reviewed"
    assert new["steps"][1]["status"] == "superseded"
    with pytest.raises(Conflict):
        ledger.confirm(task["id"], second["id"], 1, "Old answer")
    with pytest.raises(Conflict):
        ledger.control(task["id"], 1, "cancel")


def test_pause_resume_waiting_and_cancel(ledger: Ledger) -> None:
    task = ledger.create(plan(), "first")
    step = ledger.claim()
    assert step and ledger.finish_claim(step, "waiting_user", {})
    ledger.control(task["id"], 1, "pause")
    assert ledger.claim() is None
    ledger.control(task["id"], 1, "resume")
    ledger.settle()
    assert ledger.get(task["id"])["status"] == "waiting_user"
    ledger.control(task["id"], 1, "cancel")
    ledger.settle()
    assert ledger.get(task["id"])["status"] == "cancelled"


def test_due_merges_missed_and_does_not_overlap(tmp_path: Path) -> None:
    clock = FakeClock()
    ledger = Ledger(tmp_path / "tasks.db", clock.now)
    schedule = ledger.schedule(plan(), "timer", clock.now() + 60, 60)
    ledger.fire_due()
    assert ledger.tasks() == []
    clock.advance(3700)
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda _: Ledger(ledger.path, clock.now).fire_due(), range(3)))
    assert len(ledger.tasks()) == 1
    assert ledger.schedules()[0]["next_at"] > clock.now()
    clock.advance(600)
    ledger.fire_due()
    assert len(ledger.tasks()) == 1
    task = ledger.tasks()[0]
    ledger.control(task["id"], 1, "cancel")
    ledger.settle()
    ledger.fire_due()
    assert len(ledger.tasks()) == 2
    ledger.disable_schedule(schedule["id"])
    clock.advance(600)
    ledger.fire_due()
    assert len(ledger.tasks()) == 2


def research(store: ResearchStore) -> dict[str, Any]:
    return store.create_project(
        ProjectSpec(
            title="Comparison",
            goal="Compare deployment",
            candidates=[
                "https://github.com/Anil-matcha/open-dots",
                "https://github.com/nano-muse/nanoMuse",
            ],
            constraints=[],
            mode="fixture",
        )
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_crash_after_domain_enqueue_reuses_run(tmp_path: Path, cancel: bool) -> None:
    clock = FakeClock()
    ledger = Ledger(tmp_path / "tasks.db", clock.now)
    store = ResearchStore(tmp_path / "research", clock)
    try:
        project = research(store)
        task = ledger.create(
            plan({"adapter": "research", "project_id": project["id"], "title": "Compare"}), "first"
        )
        old = ledger.claim()
        assert old
        run = store.enqueue(project["id"], request_key=f"assistant:{old['id']}")
        # Simulate process loss after the other database commits, before the ledger binds it.
        if cancel:
            ledger.control(task["id"], 1, "cancel")
        clock.advance(31)
        reopened = Ledger(ledger.path, clock.now)
        worker = ResearchOrchestrator(
            store,
            sentinel=Sentinel(
                SentinelSettings(), AuditLog(tmp_path / "audit.jsonl"), HeadlessUI(approve=False)
            ),
        )
        coordinator = Coordinator(reopened, worker)
        coordinator.tick()
        assert len(store.runs(project["id"])) == 1
        assert not reopened.finish_claim(old, "waiting_user", {})
        if not cancel:
            for _ in range(20):
                if not await worker.tick():
                    break
        coordinator.tick()
        assert reopened.get(task["id"])["status"] == ("cancelled" if cancel else "completed")
        assert store.run(run["id"])["status"] == ("cancelled" if cancel else "succeeded")
    finally:
        store.close()


def test_schedule_ownership_and_frozen_research_inputs(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from nanomuse.research.api import install_research

    app = FastAPI()
    service = SimpleNamespace(data_dir=tmp_path)
    worker = install_research(app, service, [])
    install_assistant(app, service, [])
    try:
        project = research(worker.store)
        body = {
            "plan": plan(
                {"adapter": "research", "title": "Compare", "project_id": project["id"]}
            ).model_dump(),
            "request_id": "timer",
            "due_at": 1,
            "interval_seconds": 60,
        }
        path = f"/api/research/projects/{project['id']}/tracking"
        with TestClient(app) as client:
            assert client.put(path, json={"enabled": True}).status_code == 200
            assert client.post("/api/assistant/schedules", json=body).status_code == 409
            assert client.put(path, json={"enabled": False}).status_code == 200
            saved = client.post("/api/assistant/schedules", json=body)
            assert saved.status_code == 200
            assert client.put(path, json={"enabled": True}).status_code == 409
            assert (
                client.post(
                    "/api/assistant/schedules", json={**body, "request_id": "other"}
                ).status_code
                == 409
            )
            # Conditions changed while asleep. No fresh domain work may start from an old plan.
            old = worker.store.get("spec_revision", project["spec_id"])
            worker.store.revise(
                project["id"],
                ProjectSpec.model_validate(
                    {**{k: old[k] for k in ProjectSpec.model_fields}, "goal": "New goal"}
                ),
            )
            app.state.assistant.tick()
            task = app.state.assistant.ledger.tasks()[0]
            assert task["status"] == "needs_attention"
            assert not any(
                r["request_key"].startswith("assistant:") for r in worker.store.runs(project["id"])
            )
            assert (
                client.delete(f"/api/assistant/schedules/{saved.json()['id']}").status_code == 200
            )
            assert client.put(path, json={"enabled": True}).status_code == 200
    finally:
        worker.store.close()


async def test_main_agent_tool_uses_same_ledger_and_cannot_confirm_for_user(ledger: Ledger) -> None:
    import json

    from nanomuse.assistant.tool import AssistantTasks
    from nanomuse.schema import RiskLevel

    tool = AssistantTasks(ledger=ledger, validate_plan=lambda _: None)
    assert tool.assess({"action": "create"}).risk == RiskLevel.MODERATE
    result = await tool.execute(action="create", request_id="tool", plan=plan().model_dump())
    task = json.loads(result.output)
    assert ledger.tasks()[0]["id"] == task["id"]
    rejected = await tool.execute(action="confirm", task_id=task["id"], expected_revision=1)
    assert rejected.error


async def test_completed_domain_result_is_not_current_after_input_change(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "research", FakeClock())
    ledger = Ledger(tmp_path / "tasks.db")
    try:
        project = research(store)
        task = ledger.create(
            plan({"adapter": "research", "title": "Compare", "project_id": project["id"]}), "first"
        )
        worker = ResearchOrchestrator(
            store,
            sentinel=Sentinel(
                SentinelSettings(), AuditLog(tmp_path / "audit.jsonl"), HeadlessUI(approve=False)
            ),
        )
        coordinator = Coordinator(ledger, worker)
        coordinator.tick()
        for _ in range(20):
            if not await worker.tick():
                break
        run = store.runs(project["id"])[0]
        assert run["status"] == "succeeded"
        old = store.get("spec_revision", project["spec_id"])
        store.revise(
            project["id"],
            ProjectSpec.model_validate(
                {**{k: old[k] for k in ProjectSpec.model_fields}, "goal": "New goal"}
            ),
        )
        coordinator.tick()
        result = ledger.get(task["id"])
        assert result["status"] == "needs_attention"
        assert result["steps"][0]["result"]["inputs_current"] is False
    finally:
        store.close()


def test_active_plan_cannot_be_replaced(ledger: Ledger) -> None:
    task = ledger.create(plan(), "first")
    assert ledger.claim()
    ledger.control(task["id"], 1, "pause")
    with pytest.raises(Conflict):
        ledger.revise(task["id"], 1, plan(), "Changed")


def test_abrupt_process_exit_recovers_dispatch_and_cancellation(tmp_path: Path) -> None:
    import subprocess
    import sys

    code = """
import os, sys
from pathlib import Path
from nanomuse.assistant.ledger import Ledger, Plan
from nanomuse.research.store import ResearchStore
from nanomuse.research.clock import FakeClock
from nanomuse.research.contracts import ProjectSpec
root = Path(sys.argv[1])
ledger = Ledger(root / 'tasks.db', FakeClock().now)
store = ResearchStore(root / 'research', FakeClock())
project = store.create_project(ProjectSpec(title='Study', goal='Compare',
    candidates=['https://github.com/Anil-matcha/open-dots',
                'https://github.com/nano-muse/nanoMuse'], constraints=[], mode='fixture'))
task = ledger.create(Plan(title='Study', success_criteria='Report', steps=[
    {'adapter': 'research', 'title': 'Compare', 'project_id': project['id']}]), 'process')
step = ledger.claim()
store.enqueue(project['id'], request_key='assistant:' + step['id'])
os._exit(17)
"""
    child = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True)
    assert child.returncode == 17, child.stderr
    clock = FakeClock()
    clock.advance(31)
    ledger = Ledger(tmp_path / "tasks.db", clock.now)
    store = ResearchStore(tmp_path / "research", clock)
    try:
        task = ledger.tasks()[0]
        ledger.control(task["id"], 1, "cancel")
        coordinator = Coordinator(ledger, ResearchOrchestrator(store))
        coordinator.tick()
        coordinator.tick()
        assert ledger.get(task["id"])["status"] == "cancelled"
        runs = store.runs(store.list("project")[0]["id"])
        assert len(runs) == 1 and runs[0]["status"] == "cancelled"
        with ledger.transaction() as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        store.close()


def test_schema_downgrade_is_rejected_without_mutation(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        Ledger(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()


def test_mutation_receipts_survive_restart_and_reject_changed_payload(ledger: Ledger) -> None:
    task = ledger.create(plan(), "created")
    newer = plan({"adapter": "checkpoint", "title": "Updated"})
    ledger.revise(task["id"], 1, newer, "Changed", "revision-request")
    reopened = Ledger(ledger.path, ledger.clock)
    result = reopened.revise(task["id"], 1, newer, "Changed", "revision-request")
    assert result["revision"] == 2 and len(result["plans"]) == 2
    with pytest.raises(Conflict):
        reopened.revise(task["id"], 1, newer, "Other reason", "revision-request")
    step = reopened.claim()
    assert step and reopened.finish_claim(step, "waiting_user", {})
    reopened.confirm(task["id"], step["id"], 2, "Reviewed", "confirm-request")
    result = reopened.confirm(task["id"], step["id"], 2, "Reviewed", "confirm-request")
    assert result["status"] == "completed"
    assert sum(e["kind"] == "user_confirmed" for e in result["events"]) == 1


def test_api_validation_and_stale_revision(tmp_path: Path) -> None:
    from types import SimpleNamespace

    app = FastAPI()
    store = ResearchStore(tmp_path / "research")
    app.state.research = ResearchOrchestrator(store)
    install_assistant(app, SimpleNamespace(data_dir=tmp_path), [])
    try:
        with TestClient(app) as client:
            task = client.post(
                "/api/assistant/tasks", json={"plan": plan().model_dump(), "request_id": "one"}
            ).json()
            assert task["status"] == "queued"
            assert (
                client.post(
                    f"/api/assistant/tasks/{task['id']}/actions",
                    json={"expected_revision": 2, "action": "cancel", "request_id": "stale"},
                ).status_code
                == 409
            )
            assert client.get("/api/assistant/tasks/missing").status_code == 404
            assert (
                client.post(
                    "/api/assistant/tasks",
                    json={
                        "plan": {
                            "title": "Bad",
                            "success_criteria": "none",
                            "steps": [{"adapter": "shell", "title": "Execute"}],
                        },
                        "request_id": "bad",
                    },
                ).status_code
                == 422
            )
            assert (
                client.post(
                    "/api/assistant/schedules",
                    json={
                        "plan": plan().model_dump(),
                        "request_id": "timer",
                        "interval_seconds": 0,
                    },
                ).status_code
                == 422
            )
    finally:
        store.close()
