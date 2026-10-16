"""Production app lifecycle and authenticated personal task adapter integration."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nanomuse.config import Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.schema import Function, LLMResponse, ToolCall
from nanomuse.server import create_app
from nanomuse.server.service import MuseService


@pytest.fixture
def personal_app(settings: Settings) -> Iterator[tuple[TestClient, MuseService, FastAPI, MockLLM]]:
    settings.server.token = "test-personal-session"
    settings.hub.enabled = False
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    settings.sentinel.mode = "ask"
    llm = MockLLM([])
    service = MuseService(settings, llm=llm)
    service.profile.proactivity = "off"
    app = create_app(settings, service)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-personal-session"
        yield client, service, app, llm


def plan(adapter: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "Personal task",
        "success_criteria": "Verify the requested change in its authoritative store",
        "steps": [{"adapter": adapter, "title": "Apply requested change", adapter: payload}],
    }


def create(
    client: TestClient,
    adapter: str,
    payload: dict[str, Any],
    request_id: str,
    **extra: Any,
) -> dict[str, Any]:
    response = client.post(
        "/api/assistant/tasks",
        json={"request_id": request_id, "plan": plan(adapter, payload), **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def wait_for(predicate: Callable[[], Any], *, timeout: float = 8) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.03)
    pytest.fail("Personal task did not reach its expected state")


def wait_task(client: TestClient, task_id: str, status: str) -> dict[str, Any]:
    def ready() -> dict[str, Any] | None:
        result = client.get(f"/api/assistant/tasks/{task_id}").json()
        assert result["status"] not in {"failed", "cancelled"} or result["status"] == status, result
        return result if result["status"] == status else None

    return wait_for(ready)


def test_ledger_reminder_update_uses_same_record_and_new_due_time(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, llm = personal_app
    first = create(
        client,
        "reminder",
        {"action": "create", "text": "Review notes", "at": "2099-01-01T09:00:00+08:00"},
        "reminder-create",
    )
    completed = wait_task(client, first["id"], "completed")
    record = completed["steps"][0]["result"]["reminder"]
    assert record["revision"] == 1
    edited = create(
        client,
        "reminder",
        {
            "action": "update",
            "reminder_id": record["id"],
            "expected_revision": 1,
            "at": "2099-01-01T10:00:00+08:00",
            "text": "Review updated notes",
        },
        "reminder-update",
    )
    final = wait_task(client, edited["id"], "completed")
    changed = final["steps"][0]["result"]["reminder"]
    assert changed["id"] == record["id"] and changed["revision"] == 2
    assert changed["next_at"] != record["next_at"]
    assert changed["text"] == "Review updated notes"
    current = service.app.reminders.get(record["id"])
    assert current is not None and current.next_at == changed["next_at"]
    old_due = datetime.fromisoformat(record["next_at"]).astimezone(UTC)
    assert service.app.reminders.due(old_due) == []
    assert len(service.app.reminders.list()) == 1
    assert client.get("/api/research/projects").status_code == 200
    assert not llm.calls


def test_document_completion_has_verified_artifact_and_detects_later_tampering(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, llm = personal_app
    (service.settings.agent.workspace / "notes.md").write_text("Source notes for review.", "utf-8")
    first = create(
        client, "document", {"source_paths": ["notes.md"], "output_name": "review.md"}, "document"
    )
    completed = wait_task(client, first["id"], "completed")
    result = completed["steps"][0]["result"]
    assert result["verification"] == "file_sha256"
    artifact = client.get(f"/api/assistant/artifacts/{result['artifact']['id']}").json()
    assert artifact["verified"]
    assert hashlib.sha256(artifact["content"].encode()).hexdigest() == artifact["sha256"]
    assert "Source notes for review." in artifact["content"]
    assert (
        artifact["source_hashes"]["notes.md"]
        == hashlib.sha256(b"Source notes for review.").hexdigest()
    )
    (service.settings.agent.workspace / artifact["path"]).write_text("Changed", "utf-8")
    assert client.get(f"/api/assistant/artifacts/{artifact['id']}").status_code == 400
    listed = client.get(f"/api/assistant/tasks/{first['id']}/artifacts").json()
    assert listed[0]["verified"] is False
    assert not llm.calls


def test_unconfirmed_preference_requires_distinct_user_consent_and_cannot_fake_checkpoint(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, llm = personal_app
    task = create(
        client,
        "preference",
        {"action": "set", "key": "reply_style", "value": "Concise replies", "expected_revision": 0},
        "preference",
    )
    waiting = wait_task(client, task["id"], "waiting_user")
    step_id = waiting["steps"][0]["id"]
    assert client.get("/api/assistant/preferences").json() == []
    assert service.app.memory is not None and service.app.memory.count() == 0
    false_completion = client.post(
        f"/api/assistant/tasks/{task['id']}/steps/{step_id}/confirm",
        json={"request_id": "fake-checkpoint", "expected_revision": 1, "evidence": "Done"},
    )
    assert false_completion.status_code == 409
    assert (
        client.post(
            f"/api/assistant/tasks/{task['id']}/steps/{step_id}/authorize-preference",
            json={"request_id": "stale-consent", "expected_revision": 2},
        ).status_code
        == 409
    )
    approved = client.post(
        f"/api/assistant/tasks/{task['id']}/steps/{step_id}/authorize-preference",
        json={"request_id": "user-consent", "expected_revision": 1},
    )
    assert approved.status_code == 200
    wait_task(client, task["id"], "completed")
    preferences = client.get("/api/assistant/preferences").json()
    assert len(preferences) == 1 and preferences[0]["confirmed"]
    assert preferences[0]["source_task"] == task["id"]
    assert preferences[0]["memory_id"] in {
        item.id for item in service.app.memory.relevant("reply style")
    }
    service.app.memory.forget(preferences[0]["memory_id"])
    tombstones = client.get("/api/assistant/preferences").json()
    assert tombstones[0]["status"] == "deleted" and tombstones[0]["value"] == ""
    assert service.app.memory.count() == 0
    assert not llm.calls


def test_model_task_tool_cannot_authorize_a_preference(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, _ = personal_app
    tool = service.app.tools.get("assistant_tasks")
    assert tool is not None
    assert "confirm_preferences" not in tool.parameters["properties"]

    async def invoke() -> Any:
        return await tool.execute(
            action="create",
            request_id="model-created",
            plan=plan(
                "preference",
                {
                    "action": "set",
                    "key": "answer_language",
                    "value": "Chinese",
                    "expected_revision": 0,
                },
            ),
            confirm_preferences=True,
        )

    assert client.portal is not None
    response = client.portal.call(invoke)
    assert not response.error
    task = json.loads(response.output)
    wait_task(client, task["id"], "waiting_user")

    async def unauthorized_action() -> Any:
        return await tool.execute(
            action="authorize_preference",
            task_id=task["id"],
            request_id="model-consent-attempt",
            expected_revision=1,
        )

    assert client.portal.call(unauthorized_action).error
    assert client.get("/api/assistant/preferences").json() == []
    assert service.app.memory is not None and service.app.memory.count() == 0


def test_ui_form_consent_and_authenticated_read_contract(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, _ = personal_app
    task = create(
        client,
        "preference",
        {"action": "set", "key": "reply_style", "value": "Concise replies", "expected_revision": 0},
        "form-consent",
        confirm_preferences=True,
    )
    wait_task(client, task["id"], "completed")
    assert service.app.memory is not None and service.app.memory.count() == 1
    client.headers.pop("Authorization")
    assert client.get("/api/assistant/preferences").status_code == 401
    assert client.get("/api/assistant/artifacts/" + "a" * 32).status_code == 401


def test_cancelling_during_strict_sentinel_approval_prevents_reminder_effect(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, _ = personal_app
    service.app.sentinel.settings.mode = "strict"
    task = create(
        client,
        "reminder",
        {"action": "create", "text": "Review notes", "at": "2099-01-01T09:00:00+08:00"},
        "strict-cancel",
    )
    wait_for(lambda: list(service.ui.pending_approvals))
    wait_task(client, task["id"], "waiting_approval")
    cancelled = client.post(
        f"/api/assistant/tasks/{task['id']}/actions",
        json={"request_id": "cancel", "expected_revision": 1, "action": "cancel"},
    )
    assert cancelled.status_code == 200
    wait_task(client, task["id"], "cancelled")
    assert service.app.reminders.list() == []
    wait_for(lambda: not service.ui.pending_approvals)


def test_document_changed_while_awaiting_approval_does_not_complete(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, _ = personal_app
    service.app.sentinel.settings.mode = "strict"
    source = service.settings.agent.workspace / "notes.md"
    source.write_text("Initial source", "utf-8")
    task = create(client, "document", {"source_paths": ["notes.md"]}, "changing-document")
    approval_id = wait_for(lambda: list(service.ui.pending_approvals))[0]
    source.write_text("Changed source", "utf-8")
    approved = client.post(f"/api/approvals/{approval_id}", json={"approved": True})
    assert approved.status_code == 200
    attention = wait_task(client, task["id"], "needs_attention")
    assert "artifact" not in attention["steps"][0]["result"]
    assert client.get(f"/api/assistant/tasks/{task['id']}/artifacts").json() == []


def test_paused_strict_approval_is_replaced_before_resuming_the_same_step(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, _ = personal_app
    service.app.sentinel.settings.mode = "strict"
    task = create(
        client,
        "reminder",
        {"action": "create", "text": "Review notes", "at": "2099-01-01T09:00:00+08:00"},
        "strict-pause",
    )
    first_approval = wait_for(lambda: list(service.ui.pending_approvals))[0]
    assert (
        client.post(
            f"/api/assistant/tasks/{task['id']}/actions",
            json={"request_id": "pause", "expected_revision": 1, "action": "pause"},
        ).status_code
        == 200
    )
    wait_for(lambda: not service.ui.pending_approvals)
    assert service.app.reminders.list() == []
    assert (
        client.post(
            f"/api/assistant/tasks/{task['id']}/actions",
            json={"request_id": "resume", "expected_revision": 1, "action": "resume"},
        ).status_code
        == 200
    )
    second_approval = wait_for(lambda: list(service.ui.pending_approvals))[0]
    assert first_approval != second_approval
    assert (
        client.post(f"/api/approvals/{first_approval}", json={"approved": True}).status_code == 404
    )
    assert (
        client.post(f"/api/approvals/{second_approval}", json={"approved": True}).status_code == 200
    )
    wait_task(client, task["id"], "completed")
    assert len(service.app.reminders.list()) == 1


def test_preference_plan_revision_does_not_reuse_earlier_consent(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, app, _ = personal_app
    task = create(
        client,
        "preference",
        {"action": "set", "key": "reply_style", "value": "Concise replies", "expected_revision": 0},
        "old-form-consent",
        confirm_preferences=True,
        due_at=time.time() + 3600,
    )
    old_step = task["steps"][0]["id"]
    assert app.state.assistant.ledger.preference_authorized(old_step)
    revised = client.post(
        f"/api/assistant/tasks/{task['id']}/plan-revisions",
        json={
            "request_id": "new-preference-plan",
            "expected_revision": 1,
            "reason": "User changed the desired style",
            "plan": plan(
                "preference",
                {
                    "action": "set",
                    "key": "reply_style",
                    "value": "Detailed replies",
                    "expected_revision": 0,
                },
            ),
        },
    )
    assert revised.status_code == 200
    new_step = next(step for step in revised.json()["steps"] if step["revision"] == 2)
    assert not app.state.assistant.ledger.preference_authorized(new_step["id"])
    assert (
        client.post(
            f"/api/assistant/tasks/{task['id']}/steps/{new_step['id']}/authorize-preference",
            json={"request_id": "stale-new-consent", "expected_revision": 1},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/assistant/tasks/{task['id']}/steps/{old_step}/authorize-preference",
            json={"request_id": "old-step-consent", "expected_revision": 2},
        ).status_code
        == 409
    )
    assert service.app.memory is not None and service.app.memory.count() == 0


def test_main_chat_creates_shared_personal_ledger_without_another_model_loop(
    personal_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, llm = personal_app
    source_text = "Study notes: practice SQLite transactions and durable task recovery."
    task_plan = {
        "title": "Prepare a study review",
        "success_criteria": "Publish sourced notes, schedule a reminder and review a style preference",
        "steps": [
            {
                "adapter": "document",
                "title": "Extract the study notes",
                "document": {"source_paths": ["study.md"], "output_name": "study-review.md"},
            },
            {
                "adapter": "reminder",
                "title": "Schedule a review",
                "reminder": {
                    "action": "create",
                    "text": "Review the study notes",
                    "at": "2099-01-01T09:00:00+08:00",
                },
            },
            {
                "adapter": "preference",
                "title": "Ask whether concise study explanations should be remembered",
                "preference": {
                    "action": "set",
                    "key": "study_style",
                    "value": "Use concise study explanations",
                    "expected_revision": 0,
                },
            },
        ],
    }

    def call(name: str, arguments: dict[str, Any]) -> ToolCall:
        return ToolCall(function=Function(name=name, arguments=json.dumps(arguments)))

    llm.script.extend(
        [
            LLMResponse(
                tool_calls=[
                    call("files", {"action": "write", "path": "study.md", "content": source_text})
                ]
            ),
            LLMResponse(
                tool_calls=[
                    call(
                        "assistant_tasks",
                        {"action": "create", "request_id": "main-chat-study", "plan": task_plan},
                    )
                ]
            ),
            LLMResponse(
                content="The study task is queued. Please confirm the proposed preference."
            ),
        ]
    )
    response = client.post(
        "/api/threads/main/send",
        json={
            "text": "Save my study notes, prepare a review, remind me and propose a study preference."
        },
    )
    assert response.status_code == 200
    wait_for(
        lambda: (
            len(llm.calls) >= 3
            and not service.threads["main"].busy
            and service.threads["main"].inbox.empty()
        )
    )
    advertised = {tool["function"]["name"]: tool["function"] for tool in llm.calls[1]["tools"]}
    assert "assistant_tasks" in advertised
    assert "plan" in advertised["assistant_tasks"]["parameters"]["properties"]
    tasks = client.get("/api/assistant/tasks").json()
    assert len(tasks) == 1 and tasks[0]["title"] == task_plan["title"]
    task = wait_task(client, tasks[0]["id"], "waiting_user")
    assert [step["status"] for step in task["steps"]] == ["completed", "completed", "waiting_user"]
    assert (service.settings.agent.workspace / "study.md").read_text("utf-8") == source_text
    artifact = task["steps"][0]["result"]["artifact"]
    published = client.get(f"/api/assistant/artifacts/{artifact['id']}").json()
    assert published["verified"] and source_text in published["content"]
    assert hashlib.sha256(published["content"].encode()).hexdigest() == artifact["sha256"]
    reminder = task["steps"][1]["result"]["reminder"]
    existing = service.app.reminders.list()
    assert len(existing) == 1 and existing[0].id == reminder["id"]
    assert service.app.memory is not None and service.app.memory.count() == 0
    assert client.get("/api/assistant/preferences").json() == []
    preference_step = task["steps"][2]
    assert (
        client.post(
            f"/api/assistant/tasks/{task['id']}/steps/{preference_step['id']}/authorize-preference",
            json={"request_id": "main-chat-user-consent", "expected_revision": 1},
        ).status_code
        == 200
    )
    completed = wait_task(client, task["id"], "completed")
    assert all(step["status"] == "completed" for step in completed["steps"])
    assert service.app.memory.count() == 1
    assert len(service.app.reminders.list()) == 1
    assert len(llm.calls) == 3
