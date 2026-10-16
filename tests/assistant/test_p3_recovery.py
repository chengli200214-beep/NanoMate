"""Task and domain receipts retain their meaning across personal-adapter recovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nanomuse.assistant.api import install_assistant
from nanomuse.assistant.ledger import Ledger, Plan, StepSpec
from nanomuse.assistant.personal import PersonalRuntime, PersonalTool
from nanomuse.memory.store import MemoryStore
from nanomuse.reminders import ReminderStore
from nanomuse.tools.base import ToolCollection


def reminder_plan(**command: Any) -> Plan:
    return Plan(
        title="Personal reminder",
        success_criteria="Reminder configuration is recorded",
        steps=[
            {
                "adapter": "reminder",
                "title": "Configure reminder",
                "reminder": {
                    "action": "create",
                    "text": "Read notes",
                    "at": "2999-01-01 10:00",
                    **command,
                },
            }
        ],
    )


def host(tmp_path: Path) -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SimpleNamespace(
        data_dir=tmp_path,
        settings=SimpleNamespace(agent=SimpleNamespace(workspace=workspace)),
        app=SimpleNamespace(
            reminders=ReminderStore(tmp_path / "reminders.db"),
            memory=MemoryStore(tmp_path / "memory.db"),
            tools=ToolCollection(),
        ),
        bus=SimpleNamespace(publish=lambda event: None),
    )


def bind(
    ledger: Ledger, plan: Plan, request: str = "create"
) -> tuple[dict[str, Any], dict[str, Any]]:
    task = ledger.create(plan, request)
    step = ledger.claim()
    assert step and ledger.finish_claim(step, "bound", {"adapter": step["spec"]["adapter"]})
    return task, ledger.bound()[0]


def test_p2_creation_receipt_remains_valid_after_optional_step_fields_added(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "tasks.db")
    plan = Plan(
        title="Existing plan",
        success_criteria="Review",
        steps=[
            {
                "adapter": "checkpoint",
                "title": "Review notes",
            }
        ],
    )
    task = ledger.create(plan, "p2-request")
    legacy = plan.model_dump()
    for step in legacy["steps"]:
        for key in ("reminder", "document", "preference"):
            step.pop(key)
    with ledger.transaction() as db:
        db.execute(
            "UPDATE task SET request_payload=? WHERE id=?",
            (json.dumps([json.dumps(legacy, separators=(",", ":")), 0, ""]), task["id"]),
        )
        db.execute("UPDATE plan SET spec=?", (json.dumps(legacy),))
        db.execute("UPDATE step SET spec=?", (json.dumps(legacy["steps"][0]),))
    reopened = Ledger(ledger.path)
    assert reopened.create(plan, "p2-request")["id"] == task["id"]
    assert len(reopened.tasks()) == 1
    with reopened.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "reminder" not in reopened.get(task["id"])["plans"][0]["spec"]["steps"][0]


def test_domain_commit_before_ledger_result_replays_without_resurrection(tmp_path: Path) -> None:
    service = host(tmp_path)
    ledger = Ledger(tmp_path / "tasks.db")
    runtime = PersonalRuntime(ledger, service)
    task, step = bind(ledger, reminder_plan())
    committed = runtime.apply(step)
    reminder = committed["reminder"]
    service.app.reminders.cancel(reminder["id"], expected_revision=reminder["revision"])
    # A new runtime finds the same bound intent after the process died before reconcile.
    recovered = PersonalRuntime(Ledger(ledger.path), service)
    assert recovered.apply(step) == committed
    assert len(service.app.reminders.list(None)) == 1
    current = service.app.reminders.get(reminder["id"])
    assert current.status == "cancelled" and current.revision == 2
    recovered.ledger.reconcile(step["id"], "completed", committed)
    assert recovered.ledger.get(task["id"])["status"] == "completed"


def test_paused_and_replaced_personal_intent_cannot_execute(tmp_path: Path) -> None:
    service = host(tmp_path)
    ledger = Ledger(tmp_path / "tasks.db")
    runtime = PersonalRuntime(ledger, service)
    plan = reminder_plan()
    plan.steps.append(StepSpec(adapter="checkpoint", title="Review configuration"))
    task, step = bind(ledger, plan)
    tool = PersonalTool(name="assistant_reminder", runtime=runtime, step=step)
    ledger.control(task["id"], 1, "pause")
    assert not asyncio.run(tool.execute()).ok
    assert service.app.reminders.list() == []
    ledger.control(task["id"], 1, "resume")
    result = asyncio.run(tool.execute())
    assert result.ok
    ledger.reconcile(step["id"], "completed", json.loads(result.output))
    ledger.control(task["id"], 1, "pause")
    replacement = Plan(
        title="Changed intent",
        success_criteria="Reviewed",
        steps=[
            {
                "adapter": "checkpoint",
                "title": "Review only",
            }
        ],
    )
    ledger.revise(task["id"], 1, replacement, "Reminder already configured")
    assert not asyncio.run(tool.execute()).ok
    assert len(service.app.reminders.list()) == 1


@pytest.mark.parametrize("adapter", ["reminder", "document", "preference"])
def test_recurring_personal_changes_are_rejected(tmp_path: Path, adapter: str) -> None:
    payload: dict[str, Any] = {"adapter": adapter, "title": "Personal action"}
    payload[adapter] = {
        "reminder": {"action": "create", "text": "Read notes", "at": "2999-01-01 10:00"},
        "document": {"source_paths": ["notes.md"]},
        "preference": {"action": "set", "key": "style", "value": "Brief", "expected_revision": 0},
    }[adapter]
    plan = Plan(title="Personal", success_criteria="Saved", steps=[payload])
    ledger = Ledger(tmp_path / "tasks.db")
    with pytest.raises(ValueError, match="one-off"):
        ledger.schedule(plan, "routine", 0, 60)
    assert ledger.schedules() == [] and ledger.tasks() == []


def test_identical_task_creation_retry_after_reminder_update_returns_original_task(
    tmp_path: Path,
) -> None:
    service = host(tmp_path)
    reminder = service.app.reminders.create("Read notes", at="2999-01-01 10:00")
    plan = reminder_plan()
    plan.steps[0].reminder = plan.steps[0].reminder.model_validate(
        {
            "action": "update",
            "reminder_id": reminder.id,
            "expected_revision": 1,
            "text": "Review notes",
        }
    )
    app = FastAPI()
    app.state.research = SimpleNamespace()
    install_assistant(app, service, [])
    with TestClient(app) as client:
        body = {"request_id": "retry-update-task", "plan": plan.model_dump()}
        first = client.post("/api/assistant/tasks", json=body)
        assert first.status_code == 200
        coordinator = app.state.assistant
        claim = coordinator.ledger.claim()
        assert claim and coordinator.ledger.finish_claim(claim, "bound", {"adapter": "reminder"})
        step = coordinator.ledger.bound()[0]
        result = coordinator.personal.apply(step)
        coordinator.ledger.reconcile(step["id"], "completed", result)
        retry = client.post("/api/assistant/tasks", json=body)
        assert retry.status_code == 200, retry.text
        assert retry.json()["id"] == first.json()["id"]
        assert service.app.reminders.get(reminder.id).revision == 2


def test_cancellation_after_domain_commit_keeps_effect_evidence(tmp_path: Path) -> None:
    service = host(tmp_path)
    ledger = Ledger(tmp_path / "tasks.db")
    runtime = PersonalRuntime(ledger, service)
    task, step = bind(ledger, reminder_plan())
    committed = runtime.apply(step)
    ledger.control(task["id"], 1, "cancel")
    recovered = PersonalRuntime(Ledger(ledger.path), service)
    recovered.pulse()
    detail = recovered.ledger.get(task["id"])
    result = detail["steps"][0]["result"]
    assert result.get("cancelled_before_execution") is not True
    assert result.get("operation", {}).get("request_key") == committed["operation"]["request_key"]
    assert len(service.app.reminders.list()) == 1


def test_creation_retry_never_authorizes_preferences_added_by_revision(tmp_path: Path) -> None:
    service = host(tmp_path)
    app = FastAPI()
    app.state.research = SimpleNamespace()
    install_assistant(app, service, [])
    plan = Plan(
        title="Response preference",
        success_criteria="Confirmed preference recorded",
        steps=[
            {
                "adapter": "preference",
                "title": "Store response style",
                "preference": {
                    "action": "set",
                    "key": "reply_style",
                    "value": "Brief",
                    "expected_revision": 0,
                },
            }
        ],
    )
    with TestClient(app) as client:
        body = {
            "request_id": "confirmed-creation",
            "plan": plan.model_dump(),
            "confirm_preferences": True,
        }
        first = client.post("/api/assistant/tasks", json=body)
        assert first.status_code == 200
        ledger = app.state.assistant.ledger
        assert ledger.preference_authorized(first.json()["steps"][0]["id"])
        changed = plan.model_copy(deep=True)
        changed.steps[0].preference.value = "Explain every detail"
        revised = ledger.revise(first.json()["id"], 1, changed, "New preference needs confirmation")
        current = next(step for step in revised["steps"] if step["revision"] == 2)
        assert not ledger.preference_authorized(current["id"])
        replay = client.post("/api/assistant/tasks", json=body)
        assert replay.status_code == 200, replay.text
        assert not ledger.preference_authorized(current["id"])


def test_document_creation_retry_keeps_receipt_when_source_was_removed(tmp_path: Path) -> None:
    service = host(tmp_path)
    source = Path(service.settings.agent.workspace) / "notes.md"
    source.write_text("Original notes", encoding="utf-8")
    app = FastAPI()
    app.state.research = SimpleNamespace()
    install_assistant(app, service, [])
    plan = Plan(
        title="Digest",
        success_criteria="Digest saved",
        steps=[
            {
                "adapter": "document",
                "title": "Extract notes",
                "document": {"source_paths": ["notes.md"]},
            }
        ],
    )
    with TestClient(app) as client:
        body = {"request_id": "document-creation", "plan": plan.model_dump()}
        first = client.post("/api/assistant/tasks", json=body)
        assert first.status_code == 200
        source.unlink()
        replay = client.post("/api/assistant/tasks", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == first.json()["id"]
