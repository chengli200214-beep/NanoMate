"""Runtime results resume original planning without impersonating a user or approving input."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nanomuse.agent import Incoming
from nanomuse.assistant.continuations import Continuations, ContinuationStopped, make_origin
from nanomuse.assistant.delivery import deliver_task
from nanomuse.assistant.errors import Conflict
from nanomuse.assistant.ledger import Ledger, Plan
from nanomuse.config import Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.schema import Function, LLMResponse, Message, Role, ToolCall
from nanomuse.server import create_app
from nanomuse.server.events import Timeline
from nanomuse.server.service import MuseService


def call(**arguments: Any) -> ToolCall:
    return ToolCall(function=Function(name="assistant_tasks", arguments=json.dumps(arguments)))


def complete_phase(
    ledger: Ledger, origin: dict[str, Any], *, resume: bool = True
) -> dict[str, Any]:
    task = ledger.create(
        Plan.model_validate(
            {
                "title": "Observe",
                "success_criteria": "Fresh observation is available",
                "steps": [
                    {"adapter": "native", "title": "Observe", "native": {"action": "observe"}}
                ],
            }
        ),
        "observe",
        origin=origin,
        continue_after_result=resume,
    )
    step = ledger.claim()
    assert step is not None
    assert ledger.finish_claim(step, "bound", {})
    ledger.reconcile(step["id"], "completed", {"observation": {"observation_id": "a" * 64}})
    return task


def scope_service(tmp_path: Path, settings: Settings) -> tuple[Continuations, Any, dict[str, Any]]:
    llm = MockLLM()
    ledger = Ledger(tmp_path / "tasks.sqlite3")
    store = Continuations(ledger)
    timeline = Timeline("main", tmp_path / "timeline.json")
    timeline.add({"id": "user-request", "type": "user", "text": "Observe, then type and save"})
    thread = SimpleNamespace(
        timeline=timeline,
        busy=False,
        inbox=asyncio.Queue(),
        agent=SimpleNamespace(ui_language="en"),
    )
    service = SimpleNamespace(
        app=SimpleNamespace(llm=llm),
        threads={"main": thread},
        settings=settings,
        timeline=lambda _: timeline,
        bus=SimpleNamespace(publish=lambda _: None),
    )
    origin = make_origin("main", "user-request", "Observe, then type and save", llm, settings)
    return store, service, origin


async def test_completion_notice_and_continuation_are_deduplicated_and_restart_bounded(
    tmp_path: Path, settings: Settings
) -> None:
    store, service, origin = scope_service(tmp_path, settings)
    origin["maximum"] = 1
    complete_phase(store.ledger, origin)
    store.project()
    store.project()
    assert len(store.records()) == 1
    notice = store.pending_notices()[0]
    await deliver_task(service, notice)
    await deliver_task(service, notice)
    store.noticed(notice["id"])
    assert (
        len([e for e in service.timeline("main").events if e.get("source") == "assistant_task"])
        == 1
    )
    assert store.claim(notice, service)
    assert not store.rejection(notice, service, claimed=True)
    # A crash while a planning request might be in flight never repeats the call.
    restarted = Continuations(store.ledger)
    assert restarted.records()[0]["state"] == "blocked"
    assert restarted.records()[0]["reason"] == "interrupted_planning"
    assert not restarted.pending()
    assert not service.app.llm.calls


async def test_notices_do_not_opt_in_or_override_new_user_request(
    tmp_path: Path, settings: Settings
) -> None:
    store, service, origin = scope_service(tmp_path, settings)
    complete_phase(store.ledger, origin, resume=False)
    store.project()
    item = store.pending_notices()[0]
    await deliver_task(service, item)
    store.noticed(item["id"])
    assert not store.pending()
    assert not service.app.llm.calls
    assert store.describe(store.ledger.get(item["task"]["id"]))["approval_required"] is False
    service.timeline("main").add({"id": "new-user", "type": "user", "text": "Stop that goal"})
    assert store.rejection(item, service) == "origin_request_changed"


@pytest.mark.parametrize("adapter", ["reminder", "document"])
def test_personal_completion_corrects_stale_chat_snapshot_without_another_model_call(
    settings: Settings, adapter: str
) -> None:
    settings.agent.task_ledger_only = settings.assistant_usage.enabled = True
    settings.agent.language = "en"
    settings.hub.enabled = False
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    settings.sentinel.mode = "ask"
    llm = MockLLM()
    service = MuseService(settings, llm=llm)
    service.profile.proactivity = "off"
    old = service.app.reminders.create("Synthetic review", at="2999-01-01T09:00:00+00:00")
    source = settings.agent.workspace / "notes.md"
    source.write_text("Synthetic source for a local digest.", "utf-8")
    payload = (
        {
            "action": "update",
            "reminder_id": old.id,
            "expected_revision": 1,
            "at": "2999-01-01T10:00:00+00:00",
        }
        if adapter == "reminder"
        else {"source_paths": ["notes.md"], "output_name": "digest.md"}
    )
    llm.script.extend(
        [
            LLMResponse(
                tool_calls=[
                    call(
                        action="create",
                        request_id="personal-phase",
                        plan={
                            "title": "Synthetic personal task",
                            "success_criteria": "Verify the authoritative store",
                            "steps": [
                                {"adapter": adapter, "title": "Apply change", adapter: payload}
                            ],
                        },
                    )
                ]
            ),
            LLMResponse(content="The task is queued."),
        ]
    )
    app = create_app(settings, service)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + service.token
        assert (
            client.post(
                "/api/threads/main/send",
                json={"text": "Apply the requested synthetic personal change."},
            ).status_code
            == 200
        )
        notices: list[dict[str, Any]] = []
        until = time.monotonic() + 12
        while time.monotonic() < until:
            notices = [
                event
                for event in service.timeline("main").events
                if event.get("source") == "assistant_task"
            ]
            if notices and not service.threads["main"].busy:
                break
            time.sleep(0.02)
        assert len(notices) == 1
        assert notices[0]["assistant_task"]["status"] == "completed"
        assert "Task completed" in notices[0]["text"]
        tasks = app.state.assistant.ledger.tasks()
        assert len(tasks) == 1
        task = client.get(f"/api/assistant/tasks/{tasks[0]['id']}").json()
        assert task["status"] == "completed" and not task["approval_required"]
        if adapter == "reminder":
            changed = service.app.reminders.get(old.id)
            assert changed.revision == 2 and changed.next_at != old.next_at
            assert len(service.app.reminders.list()) == 1
        else:
            artifact = task["steps"][0]["result"]["artifact"]
            output = settings.agent.workspace / artifact["path"]
            assert hashlib.sha256(output.read_bytes()).hexdigest() == artifact["sha256"]
        assert len(llm.calls) == 2
        assert service.app.assistant_usage.status()["requests"] == 0
        assert len([e for e in service.timeline("main").events if e.get("type") == "user"]) == 1


@pytest.mark.parametrize("resume", [False, True])
def test_identical_creation_retry_keeps_committed_continuation_choice(
    tmp_path: Path, settings: Settings, resume: bool
) -> None:
    store, _, origin = scope_service(tmp_path, settings)
    plan = Plan.model_validate(
        {
            "title": "Observe",
            "success_criteria": "Read current state",
            "steps": [{"adapter": "native", "title": "Observe", "native": {"action": "observe"}}],
        }
    )
    first = store.ledger.create(plan, "stable-request", origin=origin, continue_after_result=resume)
    assert (
        store.ledger.replay_creation(plan, "stable-request", continue_after_result=resume)["id"]
        == first["id"]
    )
    with pytest.raises(Conflict, match="continuation choice"):
        store.ledger.replay_creation(plan, "stable-request", continue_after_result=not resume)
    with pytest.raises(Conflict, match="continuation choice"):
        store.ledger.create(plan, "stable-request", origin=origin, continue_after_result=not resume)
    with store.ledger.transaction() as db:
        assert db.execute("SELECT count(*) FROM request_scope").fetchone()[0] == 1
        assert (
            bool(db.execute("SELECT continue_after_result FROM task_origin").fetchone()[0])
            == resume
        )
    assert len(store.ledger.tasks()) == 1


def test_legacy_creation_retry_cannot_enable_automatic_planning(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "tasks.sqlite3")
    plan = Plan.model_validate(
        {
            "title": "Legacy task",
            "success_criteria": "Retain existing work",
            "steps": [{"adapter": "checkpoint", "title": "User review"}],
        }
    )
    original = ledger.create(plan, "legacy-request")
    with pytest.raises(Conflict, match="continuation choice"):
        ledger.replay_creation(plan, "legacy-request", continue_after_result=True)
    assert (
        ledger.replay_creation(plan, "legacy-request", continue_after_result=False)["id"]
        == original["id"]
    )


async def test_result_delivery_never_recreates_a_deleted_chat(
    tmp_path: Path, settings: Settings
) -> None:
    store, service, origin = scope_service(tmp_path, settings)
    complete_phase(store.ledger, origin)
    store.project()
    item = store.pending_notices()[0]
    service.threads.clear()

    def must_not_open(_: str) -> None:
        pytest.fail("Delivery opened a deleted thread")

    service.timeline = must_not_open
    with pytest.raises(ValueError, match="no longer exists"):
        await deliver_task(service, item)
    assert not store.claim(item, service)
    assert store.records()[0]["reason"] == "origin_thread_unavailable"


def test_main_chat_continues_observation_then_proposes_input_and_save_with_new_approvals(
    settings: Settings,
) -> None:
    settings.agent.task_ledger_only = True
    settings.assistant_usage.enabled = True
    settings.hub.enabled = False
    settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    settings.sentinel.mode = "auto"
    text = "Synthetic saved text"
    digest = hashlib.sha256(text.encode()).hexdigest()
    opening = {
        "title": "Open and observe",
        "success_criteria": "The editor observation is available; typing and saving remain",
        "steps": [
            {
                "adapter": "native",
                "title": "Open editor",
                "native": {"action": "open_app", "path": "note.txt"},
            },
            {"adapter": "native", "title": "Observe editor", "native": {"action": "observe"}},
        ],
    }

    def inspect(messages: list[Message]) -> LLMResponse:
        system = messages[0].content or ""
        assert "not a new user instruction or action approval" in system
        task_id = system.split('task_id="')[1].split('"')[0]
        return LLMResponse(tool_calls=[call(action="get", task_id=task_id)])

    def next_phase(action: str) -> Any:
        def respond(messages: list[Message]) -> LLMResponse:
            result = json.loads(
                next(m.content for m in reversed(messages) if m.role == Role.TOOL) or "{}"
            )
            observation = result["steps"][-1]["result"]["observation"]["observation_id"]
            native: dict[str, Any] = {"action": action, "expected_observation": observation}
            if action == "type":
                native["text"] = text
            elif action == "key":
                native["keys"] = ["ctrl", "s"]
            else:
                native.update(saved_file="note.txt", expected_sha256=digest)
            return LLMResponse(
                tool_calls=[
                    call(
                        action="create",
                        request_id=f"phase-{action}",
                        continue_after_result=action != "verify_saved_file",
                        plan={
                            "title": action,
                            "success_criteria": "This approved phase is verified; preserve the original save goal",
                            "steps": [{"adapter": "native", "title": action, "native": native}],
                        },
                    )
                ]
            )

        return respond

    llm = MockLLM(
        [
            LLMResponse(
                tool_calls=[
                    call(
                        action="create",
                        request_id="opening",
                        plan=opening,
                        continue_after_result=True,
                    )
                ]
            ),
            LLMResponse(content="Opening phase queued; input and save remain."),
            inspect,
            next_phase("type"),
            LLMResponse(content="Review the exact input preview."),
            inspect,
            next_phase("key"),
            LLMResponse(content="Review the exact save preview."),
            inspect,
            next_phase("verify_saved_file"),
            LLMResponse(content="Review save verification."),
        ]
    )
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)
    personal = app.state.assistant.personal
    observed = 0
    inputs: list[str] = []

    async def fake_native(spec: Any) -> dict[str, Any]:
        nonlocal observed
        observed += 1
        if spec.action == "type":
            inputs.append("type")
        elif spec.action == "key":
            assert inputs == ["type"]
            inputs.append("save")
            (settings.agent.workspace / "note.txt").write_text(text, "utf-8")
        elif spec.action == "verify_saved_file":
            assert (
                hashlib.sha256((settings.agent.workspace / "note.txt").read_bytes()).hexdigest()
                == spec.expected_sha256
            )
            return {
                "verification": "native_save_file_sha256",
                "sha256": digest,
                "gui_save_dispatched": True,
            }
        return {
            "observation": {"observation_id": f"{observed:064x}"},
            "verification": "fake_native_observation",
        }

    personal.native.execute = fake_native
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + service.token
        response = client.post(
            "/api/threads/main/send",
            json={
                "text": f"Open note.txt, enter exactly {text!r}, save through the editor, then verify the bytes."
            },
        )
        assert response.status_code == 200
        authorized: set[str] = set()
        seen_followups: set[str] = set()
        until = time.monotonic() + 25
        while time.monotonic() < until:
            for summary in app.state.assistant.ledger.tasks():
                task = app.state.assistant.ledger.get(summary["id"])
                if task["title"] in {"type", "key"} and task["id"] not in seen_followups:
                    seen_followups.add(task["id"])
                    assert inputs == ([] if task["title"] == "type" else ["type"])
                    assert task["status"] != "completed"
                for step in task["steps"]:
                    if step["status"] == "waiting_user" and step["id"] not in authorized:
                        approved = client.post(
                            f"/api/assistant/tasks/{task['id']}/steps/{step['id']}/authorize-action",
                            json={
                                "request_id": "approve-" + step["id"],
                                "expected_revision": task["revision"],
                            },
                        )
                        assert approved.status_code == 200, approved.text
                        authorized.add(step["id"])
            tasks = app.state.assistant.ledger.tasks()
            if (
                len(tasks) == 4
                and all(t["status"] == "completed" for t in tasks)
                and not service.threads["main"].busy
            ):
                break
            time.sleep(0.02)
        assert len(tasks) == 4 and all(t["status"] == "completed" for t in tasks), tasks
        assert inputs == ["type", "save"]
        assert len(authorized) == 5
        assert len(seen_followups) == 2
        assert len(llm.calls) == 11
        assert len([e for e in service.timeline("main").events if e.get("type") == "user"]) == 1
        assert len([m for m in service.threads["main"].agent.messages if m.role == Role.USER]) == 1
        records = app.state.assistant.continuations.records()
        assert len(records) >= 3
        assert {r["scope_id"] for r in records} == {records[0]["scope_id"]}
        with app.state.assistant.ledger.transaction() as db:
            origins = list(db.execute("SELECT parent_event_id FROM task_origin ORDER BY rowid"))
        assert origins[0]["parent_event_id"] == ""
        assert all(row["parent_event_id"] for row in origins[1:])
        roots = {app.state.assistant.continuations.request_root(task["id"]) for task in tasks}
        assert roots == {records[0]["scope_id"]}


async def test_strict_profile_keeps_new_user_request_out_of_old_scope(settings: Settings) -> None:
    settings.agent.task_ledger_only = True
    service = MuseService(settings, llm=MockLLM())
    agent = service.app.agent
    agent.messages = [Message.user("Original goal")]
    agent.inbox = asyncio.Queue()
    incoming = Incoming("New independent goal", origin_event_id="new-user-event")
    agent.inbox.put_nowait(incoming)
    assert agent._drain_inbox() == 0
    assert agent.inbox.get_nowait() is incoming
    assert [m.content for m in agent.messages] == ["Original goal"]
    await service.app.close()


async def test_replacement_request_during_model_call_prevents_old_continuation_mutation(
    settings: Settings,
) -> None:
    settings.agent.task_ledger_only = True
    changed = False

    def replacement(_: list[Message]) -> LLMResponse:
        nonlocal changed
        changed = True
        return LLMResponse(
            tool_calls=[
                call(
                    action="create",
                    request_id="must-not-create",
                    plan={
                        "title": "Old goal mutation",
                        "success_criteria": "Must not execute",
                        "steps": [{"adapter": "checkpoint", "title": "No authority"}],
                    },
                )
            ]
        )

    llm = MockLLM([replacement])
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)
    agent = service.app.agent
    agent.messages = [Message.user("Original user request")]
    origin = make_origin("main", "original-user", "Original user request", llm, settings)
    with pytest.raises(ContinuationStopped):
        await agent.run(
            origin["goal"],
            origin=origin,
            runtime_event={"id": "phase", "task": {"id": "observed"}},
            runtime_validator=lambda: "origin_request_changed" if changed else "",
        )
    assert len(llm.calls) == 1
    assert app.state.assistant.ledger.tasks() == []
    assert len([m for m in agent.messages if m.role == Role.USER]) == 1
    await service.app.close()
    app.state.research.store.close()


async def test_scope_deadline_and_closed_execution_stop_without_calls(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, service, origin = scope_service(tmp_path, settings)
    complete_phase(store.ledger, origin)
    store.project()
    item = store.pending_notices()[0]
    service.app.llm.continuation_allowed = lambda owner: False
    assert store.rejection(item, service) == "model_execution_closed"
    assert not store.claim(item, service)
    assert not service.app.llm.calls
    monkeypatch.setattr(
        "nanomuse.assistant.continuations.time.time", lambda: origin["deadline"] + 1
    )
    assert store.rejection(item, service) == "continuation_deadline"
