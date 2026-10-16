"""Personal-assistant acceptance through the real chat, task API and domain stores.

MockLLM supplies a declared action script. These tests verify orchestration and
observable effects, not a provider's ability to interpret the user's language.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nanomuse.assistant.preferences import PreferenceAction, PreferenceStore
from nanomuse.config import Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.memory.store import MemoryStore
from nanomuse.schema import Function, LLMResponse, Message, Role, ToolCall
from nanomuse.server import create_app
from nanomuse.server.service import MuseService


@pytest.fixture
def acceptance_app(
    settings: Settings,
) -> Iterator[tuple[TestClient, MuseService, FastAPI, MockLLM]]:
    settings.server.token = "test-acceptance-session"
    settings.hub.enabled = False
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    settings.sentinel.mode = "ask"
    llm = MockLLM([])
    service = MuseService(settings, llm=llm)
    service.profile.proactivity = "off"
    app = create_app(settings, service)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-acceptance-session"
        yield client, service, app, llm


def wait_for(predicate: Callable[[], Any]) -> Any:
    until = time.monotonic() + 10
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    pytest.fail("Acceptance condition did not settle")


def task_state(client: TestClient, task_id: str, status: str) -> dict[str, Any]:
    def ready() -> dict[str, Any] | None:
        response = client.get(f"/api/assistant/tasks/{task_id}")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] not in {"failed", "cancelled"} or result["status"] == status, result
        return result if result["status"] == status else None

    return wait_for(ready)


def tool_call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(function=Function(name=name, arguments=json.dumps(arguments)))


def chat(
    client: TestClient,
    service: MuseService,
    llm: MockLLM,
    text: str,
    script: list[LLMResponse | Callable[[list[Message]], LLMResponse]],
) -> None:
    expected = len(llm.calls) + len(script)
    llm.script.extend(script)
    response = client.post("/api/threads/main/send", json={"text": text})
    assert response.status_code == 200, response.text
    wait_for(
        lambda: (
            len(llm.calls) >= expected
            and not service.threads["main"].busy
            and service.threads["main"].inbox.empty()
        )
    )
    assert len(llm.calls) == expected


def one_step(adapter: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "Acceptance task",
        "success_criteria": "Check the authoritative store before reporting completion",
        "steps": [{"adapter": adapter, "title": "Apply requested action", adapter: payload}],
    }


def create_task(client: TestClient, plan: dict[str, Any], key: str) -> dict[str, Any]:
    response = client.post("/api/assistant/tasks", json={"request_id": key, "plan": plan})
    assert response.status_code == 200, response.text
    return response.json()


def test_a1_ordinary_chat_has_no_task_or_personal_side_effect(
    acceptance_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, llm = acceptance_app
    chat(
        client,
        service,
        llm,
        "Explain what a SQLite transaction does. Do not create a task.",
        [LLMResponse(content="A transaction groups changes into one commit or rollback.")],
    )
    assert client.get("/api/assistant/tasks").json() == []
    assert client.get("/api/assistant/preferences").json() == []
    assert client.get("/api/research/projects").json() == []
    assert service.app.reminders.list(None) == []
    assert service.app.memory is not None and service.app.memory.count() == 0


@pytest.mark.parametrize("repeating", [False, True], ids=["one-off", "daily"])
def test_a2_to_a5_chat_reminder_edit_and_cancel_keep_one_domain_record(
    acceptance_app: tuple[TestClient, MuseService, FastAPI, MockLLM], repeating: bool
) -> None:
    client, service, _, llm = acceptance_app

    def submit(payload: dict[str, Any], key: str) -> dict[str, Any]:
        previous_ids = {item["id"] for item in client.get("/api/assistant/tasks").json()}
        chat(
            client,
            service,
            llm,
            "Apply the requested reminder change and keep a task record.",
            [
                LLMResponse(
                    tool_calls=[
                        tool_call(
                            "assistant_tasks",
                            action="create",
                            request_id=key,
                            plan=one_step("reminder", payload),
                        )
                    ]
                ),
                LLMResponse(content="The reminder change is queued in the task ledger."),
            ],
        )
        tasks = client.get("/api/assistant/tasks").json()
        added = [item for item in tasks if item["id"] not in previous_ids]
        assert len(added) == 1
        task = added[0]
        return task_state(client, task["id"], "completed")["steps"][0]["result"]["reminder"]

    schedule = {"repeat": "daily 08:00"} if repeating else {"at": "2099-01-01T09:00:00+08:00"}
    created = submit({"action": "create", "text": "Review study notes", **schedule}, "create")
    edited = submit(
        {
            "action": "update",
            "reminder_id": created["id"],
            "expected_revision": created["revision"],
            "at": "2099-01-02T10:00:00+08:00",
            "text": "Review revised study notes",
        },
        "edit",
    )
    assert edited["id"] == created["id"] and edited["revision"] == 2
    assert edited["next_at"] != created["next_at"] and not edited["repeat"]
    # A stale occurrence cannot deliver after its time or cadence was replaced.
    assert (
        service.app.reminders.mark_fired(
            created["id"], expected_revision=1, expected_next_at=created["next_at"]
        )
        is None
    )
    cancelled = submit(
        {"action": "cancel", "reminder_id": created["id"], "expected_revision": 2}, "cancel"
    )
    assert cancelled["status"] == "cancelled" and cancelled["revision"] == 3
    assert len(service.app.reminders.list(None)) == 1
    assert service.app.reminders.due(datetime.fromisoformat("2099-01-03T00:00:00+00:00")) == []
    with pytest.raises(ValueError, match="cancelled"):
        service.fire_reminder(created["id"])
    assert len(client.get("/api/assistant/tasks").json()) == 3
    assert len(llm.calls) == 6


def test_b1_b3_b4_progress_evidence_and_changed_requirements_share_task_history(
    acceptance_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, llm = acceptance_app
    initial = {
        "title": "Study transaction recovery",
        "success_criteria": "Record the user's review and exercise evidence",
        "steps": [
            {"adapter": "checkpoint", "title": "Read transaction notes"},
            {"adapter": "checkpoint", "title": "Practice rollback"},
        ],
    }
    chat(
        client,
        service,
        llm,
        "Break this study plan into reading and practice steps.",
        [
            LLMResponse(
                tool_calls=[
                    tool_call("assistant_tasks", action="create", request_id="study", plan=initial)
                ]
            ),
            LLMResponse(content="The two study steps are waiting for your progress evidence."),
        ],
    )
    task = client.get("/api/assistant/tasks").json()[0]
    waiting = task_state(client, task["id"], "waiting_user")
    first, second = waiting["steps"]
    evidence = "Read the transaction notes and wrote down the rollback invariant."
    confirmed = client.post(
        f"/api/assistant/tasks/{task['id']}/steps/{first['id']}/confirm",
        json={"request_id": "reading-feedback", "expected_revision": 1, "evidence": evidence},
    )
    assert confirmed.status_code == 200, confirmed.text
    wait_for(
        lambda: (
            client.get(f"/api/assistant/tasks/{task['id']}").json()["steps"][1]["status"]
            == "waiting_user"
        )
    )
    revised_plan = {
        "title": initial["title"],
        "success_criteria": "Record a crash recovery exercise",
        "steps": [{"adapter": "checkpoint", "title": "Practice crash recovery"}],
    }
    chat(
        client,
        service,
        llm,
        "Keep the reading evidence, replace the remaining exercise with crash recovery.",
        [
            LLMResponse(
                tool_calls=[
                    tool_call(
                        "assistant_tasks",
                        action="revise",
                        task_id=task["id"],
                        request_id="new-exercise",
                        expected_revision=1,
                        reason="The user requested a crash recovery exercise.",
                        plan=revised_plan,
                    )
                ]
            ),
            LLMResponse(content="The remaining exercise now has a new plan revision."),
        ],
    )
    changed = task_state(client, task["id"], "waiting_user")
    assert changed["revision"] == 2 and len(changed["plans"]) == 2
    assert len(client.get("/api/assistant/tasks").json()) == 1
    by_id = {step["id"]: step for step in changed["steps"]}
    assert by_id[first["id"]]["result"]["user_evidence"] == evidence
    assert by_id[second["id"]]["status"] == "superseded"
    current = next(step for step in changed["steps"] if step["revision"] == 2)
    assert current["status"] == "waiting_user" and not current["result"].get("user_evidence")
    rejected = client.post(
        f"/api/assistant/tasks/{task['id']}/steps/{second['id']}/confirm",
        json={"request_id": "stale-feedback", "expected_revision": 1, "evidence": "Done"},
    )
    assert rejected.status_code == 409


def test_b5_b6_user_confirmed_preference_is_recalled_then_deleted_from_next_prompt(
    acceptance_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, llm = acceptance_app
    value = "For SQLite study, explain transaction invariants before examples."
    task = create_task(
        client,
        one_step(
            "preference",
            {
                "action": "set",
                "key": "sqlite_study",
                "value": value,
                "purpose": "SQLite transactions",
                "expected_revision": 0,
            },
        ),
        "preference",
    )
    waiting = task_state(client, task["id"], "waiting_user")
    step_id = waiting["steps"][0]["id"]
    assert service.app.memory is not None and service.app.memory.count() == 0
    approved = client.post(
        f"/api/assistant/tasks/{task['id']}/steps/{step_id}/authorize-preference",
        json={"request_id": "user-preference-consent", "expected_revision": 1},
    )
    assert approved.status_code == 200, approved.text
    task_state(client, task["id"], "completed")
    chat(
        client,
        service,
        llm,
        "Explain a SQLite transaction.",
        [LLMResponse(content="A transaction preserves its invariants through commit or rollback.")],
    )
    system = next(m.content or "" for m in llm.calls[-1]["messages"] if m.role == Role.SYSTEM)
    assert value in system
    chat(
        client,
        service,
        llm,
        "Plan a weekend trip to the mountains.",
        [LLMResponse(content="Choose a trail and check its opening hours before leaving.")],
    )
    unrelated_system = next(
        m.content or "" for m in llm.calls[-1]["messages"] if m.role == Role.SYSTEM
    )
    assert value not in unrelated_system
    deletion = create_task(
        client,
        one_step("preference", {"action": "delete", "key": "sqlite_study", "expected_revision": 1}),
        "delete-preference",
    )
    delete_waiting = task_state(client, deletion["id"], "waiting_user")
    delete_step = delete_waiting["steps"][0]["id"]
    approved = client.post(
        f"/api/assistant/tasks/{deletion['id']}/steps/{delete_step}/authorize-preference",
        json={"request_id": "user-deletion-consent", "expected_revision": 1},
    )
    assert approved.status_code == 200, approved.text
    task_state(client, deletion["id"], "completed")
    chat(
        client,
        service,
        llm,
        "Explain SQLite transaction recovery.",
        [LLMResponse(content="Recovery uses durable state to decide what was committed.")],
    )
    system = next(m.content or "" for m in llm.calls[-1]["messages"] if m.role == Role.SYSTEM)
    assert value not in system and service.app.memory.count() == 0
    tombstone = client.get("/api/assistant/preferences").json()[0]
    assert tombstone["status"] == "deleted" and tombstone["value"] == ""


def test_a6_summary_tool_context_contains_only_actual_inventory(
    acceptance_app: tuple[TestClient, MuseService, FastAPI, MockLLM],
) -> None:
    client, service, _, llm = acceptance_app
    actual = create_task(
        client,
        {
            "title": "Review recorded notes",
            "success_criteria": "User supplies review evidence",
            "steps": [{"adapter": "checkpoint", "title": "Read the notes"}],
        },
        "inventory-task",
    )
    task_state(client, actual["id"], "waiting_user")
    reminder = service.app.reminders.create("Water the study plant", at="2099-01-01T09:00:00+08:00")

    def summarize(messages: list[Message]) -> LLMResponse:
        results = {m.name: m.content or "" for m in messages if m.role == Role.TOOL}
        tasks = json.loads(results["assistant_tasks"])
        assert [item["id"] for item in tasks] == [actual["id"]]
        assert tasks[0]["title"] == "Review recorded notes"
        assert reminder.id in results["reminders"]
        assert "Water the study plant" in results["reminders"]
        return LLMResponse(content="Recorded: review the notes; the plant reminder is scheduled.")

    chat(
        client,
        service,
        llm,
        "Summarize my recorded tasks and reminders. Do not invent appointments.",
        [
            LLMResponse(
                tool_calls=[
                    tool_call("assistant_tasks", action="list"),
                    tool_call("reminders", action="list"),
                ]
            ),
            summarize,
        ],
    )
    assert len(client.get("/api/assistant/tasks").json()) == 1
    assert len(service.app.reminders.list(None)) == 1


def test_b6_scoped_projection_upgrade_preserves_global_and_ordinary_memories(
    tmp_path: Path,
) -> None:
    memory = MemoryStore(tmp_path / "memory.db")
    ordinary = memory.add("The user has hiking boots.", source="user")
    preferences = PreferenceStore(tmp_path / "preferences.db", memory)
    scoped = preferences.execute(
        PreferenceAction(
            action="set",
            key="sql_study",
            value="Explain transaction invariants first.",
            purpose="SQL transactions",
            expected_revision=0,
        ),
        "scope-create",
        "task",
        1,
        "step",
    )
    global_preference = preferences.execute(
        PreferenceAction(action="set", key="reply_style", value="Be concise.", expected_revision=0),
        "global-create",
        "task-two",
        1,
        "step-two",
    )
    projection = memory.get(scoped["memory_id"])
    assert projection is not None
    legacy_metadata = json.loads(projection.source.removeprefix("assistant_preference:"))
    legacy_metadata.pop("purpose")
    memory.set_owned_projection(
        projection.id,
        projection.content,
        projection.category,
        "assistant_preference:" + json.dumps(legacy_metadata),
    )
    # An old projection fails closed until its authoritative store adds scope metadata.
    assert projection.id not in {item.id for item in memory.relevant("SQL transactions")}
    preferences.recover()
    assert projection.id in {item.id for item in memory.relevant("Explain SQL transactions")}
    unrelated = {item.id for item in memory.relevant("Plan a mountain holiday")}
    assert projection.id not in unrelated
    assert {ordinary.id, global_preference["memory_id"]} <= unrelated


@pytest.mark.parametrize(
    "metadata",
    [
        "not-json",
        "[]",
        '{"key":"sql_study"}',
        '{"key":"sql_study","purpose":null}',
        '{"key":null,"purpose":"SQL"}',
        '{"key":"sql_study","purpose":"SQL","expires_at":true}',
        '{"key":"sql_study","purpose":"SQL","expires_at":1}',
        '{"key":"sql_study","purpose":"SQL","expires_at":NaN}',
        '{"key":"sql_study","purpose":"SQL","expires_at":' + "9" * 500 + "}",
        "[" * 2000 + "]" * 2000,
    ],
)
def test_b6_malformed_or_expired_owned_metadata_is_not_injected(
    tmp_path: Path, metadata: str
) -> None:
    memory = MemoryStore(tmp_path / "memory.db")
    item = memory.set_owned_projection(
        "m_pref_scope", "Scoped SQL rule", "preference", "assistant_preference:" + metadata
    )
    assert item.id not in {record.id for record in memory.relevant("SQL transactions")}


@pytest.mark.parametrize("delete_during_search", [False, True])
async def test_b6_embedding_results_cannot_reinsert_out_of_scope_or_deleted_preferences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delete_during_search: bool
) -> None:
    memory = MemoryStore(tmp_path / "memory.db")
    first = memory.add("First ordinary memory", source="user")
    memory.add("Second ordinary memory", source="user")
    scoped = memory.set_owned_projection(
        "m_pref_scope",
        "Explain SQL transaction invariants first.",
        "preference",
        'assistant_preference:{"key":"sql_study","purpose":"SQL transactions"}',
    )
    query = "SQL transactions" if delete_during_search else "Mountain holiday itinerary"

    async def ranked(context: str, limit: int) -> list[Any]:
        assert context == query and limit == 1
        if delete_during_search:
            memory.forget(scoped.id)
        # A stale or semantically ranked result is untrusted for scope and deletion.
        return [scoped, first]

    monkeypatch.setattr(
        memory, "index", SimpleNamespace(search=ranked, invalidate_cache=lambda: None)
    )
    recalled = await memory.relevant_async(query, limit=1)
    assert [item.id for item in recalled] == [first.id]
