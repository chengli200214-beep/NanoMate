"""Execution ownership and recovery through the real ledger and Sentinel, offline."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from nanomuse.assistant.coordinator import Coordinator
from nanomuse.assistant.execution import ExecutionJournal
from nanomuse.assistant.ledger import Conflict, Ledger, Plan
from nanomuse.assistant.personal import PersonalRuntime, PersonalTool
from nanomuse.computer.link import ComputerLink
from nanomuse.config import GUISettings, HandsSettings, Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.phone.screen import Screen
from nanomuse.research.contracts import ProjectSpec
from nanomuse.schema import Function, RiskLevel, ToolCall, ToolResult
from nanomuse.server import create_app
from nanomuse.server.service import MuseService
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.browser import Browser
from nanomuse.tools.computer import ComputerAct
from tests.test_assistant_browser import Page


@pytest.fixture
def execution_host(settings: Settings) -> tuple[MuseService, FastAPI, Coordinator, MockLLM]:
    settings.server.token = "test-execution-session"
    settings.hub.enabled = False
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    settings.sentinel.mode = "auto"
    llm = MockLLM([])
    service = MuseService(settings, llm=llm)
    service.profile.proactivity = "off"
    app = create_app(settings, service)
    return service, app, app.state.assistant, llm


def plan(*steps: dict[str, Any]) -> Plan:
    return Plan(title="Verified execution", success_criteria="Inspect actual results", steps=steps)


def file_step(path: str = "saved.txt", **fields: Any) -> dict[str, Any]:
    return {
        "adapter": "file",
        "title": "Save local text",
        "file": {"action": "write", "path": path, "content": "saved", **fields},
    }


def surface_step(adapter: str) -> dict[str, Any]:
    return {
        "adapter": adapter,
        "title": "Inspect source",
        adapter: {"action": "extract"} if adapter == "browser" else {"action": "observe"},
    }


def waiting(coordinator: Coordinator, value: Plan, request: str = "create") -> dict[str, Any]:
    coordinator.personal.validate(value)
    task = coordinator.ledger.create(value, request)
    coordinator.tick()
    current = coordinator.ledger.get(task["id"])
    assert current["status"] == "waiting_user"
    assert current["steps"][0]["result"]["requires_execution_confirmation"]
    return current


def approve_and_bind(coordinator: Coordinator, task: dict[str, Any]) -> dict[str, Any]:
    step = next(step for step in task["steps"] if step["status"] == "waiting_user")
    coordinator.ledger.authorize_action(
        task["id"], step["id"], task["revision"], f"yes:{step['id']}"
    )
    coordinator.tick()
    bound = next(item for item in coordinator.ledger.bound() if item["id"] == step["id"])
    coordinator.personal._step_id = bound["id"]
    return bound


class OfflineSurface:
    """An observable side-effect counter, not a replacement for GUI acceptance."""

    def __init__(self, result: dict[str, Any] | None = None):
        self.calls: list[Any] = []
        self.invalidations = 0
        self.result = result or {"action": "observe", "text": "Browser source text"}

    async def execute(self, value: Any) -> dict[str, Any]:
        self.calls.append(value)
        return self.result

    def invalidate(self) -> None:
        self.invalidations += 1

    def assess(self, value: Any) -> CallAssessment:
        return CallAssessment(risk=RiskLevel.SAFE, reads_private_data=True)


async def test_execution_preview_is_user_only_and_checkpoint_cannot_fake_it(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
) -> None:
    service, app, coordinator, llm = execution_host
    tool = service.app.tools.get("assistant_tasks")
    assert tool is not None
    assert "authorize_action" not in tool.parameters["properties"]["action"]["enum"]
    created = await tool.execute(
        action="create",
        request_id="model-request",
        plan=plan(file_step()).model_dump(),
        confirm_execution=True,
        confirm_preferences=True,
    )
    assert not created.error
    task = json.loads(created.output)
    coordinator.tick()
    step = coordinator.ledger.get(task["id"])["steps"][0]
    rejected = await tool.execute(
        action="authorize_action",
        task_id=task["id"],
        expected_revision=1,
        request_id="model-approval",
        step_id=step["id"],
    )
    assert rejected.error
    with pytest.raises(Conflict):
        coordinator.ledger.confirm(task["id"], step["id"], 1, "Already done", "fake-complete")
    assert not coordinator.ledger.preference_authorized(step["id"])
    assert not (service.settings.agent.workspace / "saved.txt").exists()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        url = f"/api/assistant/tasks/{task['id']}/steps/{step['id']}/authorize-action"
        assert (
            await client.post(url, json={"request_id": "unauth", "expected_revision": 1})
        ).status_code == 401
        client.headers["Authorization"] = "Bearer test-execution-session"
        assert (
            await client.post(url, json={"request_id": "human", "expected_revision": 1})
        ).status_code == 200
    coordinator.tick()
    bound = coordinator.ledger.bound()[0]
    coordinator.personal._step_id = bound["id"]
    await coordinator.personal._run(bound)
    assert coordinator.ledger.get(task["id"])["status"] == "completed"
    assert (service.settings.agent.workspace / "saved.txt").read_text("utf-8") == "saved"
    assert not llm.calls


async def test_pausing_a_pending_sentinel_approval_rejects_its_late_answer(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
) -> None:
    service, _, coordinator, _ = execution_host
    service.app.sentinel.settings.mode = "strict"
    task = waiting(coordinator, plan(file_step()))
    step = approve_and_bind(coordinator, task)
    runtime = coordinator.personal
    runtime._worker = asyncio.create_task(runtime._run(step))
    for _ in range(100):
        if service.ui.pending_approvals:
            break
        await asyncio.sleep(0.01)
    assert service.ui.pending_approvals
    approval_id = next(iter(service.ui.pending_approvals))
    coordinator.ledger.control(task["id"], 1, "pause", "pause-before-input")
    with pytest.raises(asyncio.CancelledError):
        await runtime._worker
    assert not service.ui.resolve_approval(approval_id, True)
    assert not (service.settings.agent.workspace / "saved.txt").exists()
    assert runtime.journal.records(task["id"]) == []
    assert coordinator.ledger.get(task["id"])["status"] == "paused"
    assert not (await PersonalTool(name="assistant_file", runtime=runtime, step=step).execute()).ok


@pytest.mark.parametrize("adapter", ["browser", "native"])
async def test_started_input_after_restart_is_unknown_and_never_replayed(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
    adapter: str,
) -> None:
    service, _, coordinator, llm = execution_host
    task = waiting(coordinator, plan(surface_step(adapter)))
    step = approve_and_bind(coordinator, task)
    coordinator.personal.journal.start(step, step["spec"][adapter])
    reopened = PersonalRuntime(Ledger(coordinator.ledger.path), service)
    surface = OfflineSurface()
    setattr(reopened, adapter, surface)
    reopened._step_id = step["id"]
    await reopened._run(step)
    detail = reopened.ledger.get(task["id"])
    assert detail["status"] == "needs_attention"
    assert detail["steps"][0]["status"] == "outcome_unknown"
    assert surface.calls == []
    assert reopened.journal.get(step["id"])["status"] == "started"
    assert not llm.calls


@pytest.mark.parametrize("adapter", ["browser", "native"])
async def test_completed_receipt_after_restart_advances_ledger_without_second_input(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
    adapter: str,
) -> None:
    service, _, coordinator, _ = execution_host
    task = waiting(coordinator, plan(surface_step(adapter)))
    step = approve_and_bind(coordinator, task)
    receipt = {"adapter": adapter, "action": "observe", "text": "Verified at execution time"}
    coordinator.personal.journal.start(step, step["spec"][adapter])
    coordinator.personal.journal.finish(step["id"], receipt)
    reopened = PersonalRuntime(Ledger(coordinator.ledger.path), service)
    surface = OfflineSurface()
    setattr(reopened, adapter, surface)
    reopened._step_id = step["id"]
    await reopened._run(step)
    detail = reopened.ledger.get(task["id"])
    assert detail["status"] == "completed"
    recovered = detail["steps"][0]["result"]
    assert {key: recovered[key] for key in receipt} == receipt
    assert recovered["recovered_receipt"] is True
    assert surface.calls == []


async def test_browser_extraction_flows_to_file_bytes_with_shared_task_evidence(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
) -> None:
    service, _, coordinator, llm = execution_host
    value = plan(surface_step("browser"), file_step(content="", content_from_step=0))
    task = waiting(coordinator, value)
    runtime = coordinator.personal
    runtime.browser = OfflineSurface({"action": "extract", "text": "摘录内容\n"})
    first = approve_and_bind(coordinator, task)
    await runtime._run(first)
    coordinator.tick()
    second_waiting = coordinator.ledger.get(task["id"])
    assert second_waiting["status"] == "waiting_user"
    second = approve_and_bind(coordinator, second_waiting)
    await runtime._run(second)
    final = coordinator.ledger.get(task["id"])
    assert final["status"] == "completed"
    assert (service.settings.agent.workspace / "saved.txt").read_text("utf-8") == "摘录内容\n"
    actions = ExecutionJournal(runtime.journal.path).records(task["id"])
    assert len(actions) == 2
    file_action = next(action for action in actions if action["adapter"] == "file")
    assert file_action["intent"]["content"] == "摘录内容\n"
    assert file_action["intent"]["content_from_step"] is None
    assert final["steps"][1]["result"]["verification"]["kind"] == "file_sha256"
    assert not llm.calls


async def test_missing_extraction_text_cannot_silently_publish_an_empty_file(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
) -> None:
    service, _, coordinator, _ = execution_host
    task = waiting(
        coordinator, plan(surface_step("browser"), file_step(content="", content_from_step=0))
    )
    first = approve_and_bind(coordinator, task)
    coordinator.ledger.reconcile(
        first["id"], "completed", {"adapter": "browser", "action": "extract"}
    )
    coordinator.tick()
    second = approve_and_bind(coordinator, coordinator.ledger.get(task["id"]))
    await coordinator.personal._run(second)
    assert coordinator.ledger.get(task["id"])["status"] == "needs_attention"
    assert not (service.settings.agent.workspace / "saved.txt").exists()


async def test_cancelling_an_interrupted_partial_move_keeps_unknown_outcome_visible(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, coordinator, _ = execution_host
    source = service.settings.agent.workspace / "source.txt"
    source.write_bytes(b"move contents")
    task = waiting(
        coordinator,
        plan(
            {
                "adapter": "file",
                "title": "Move source",
                "file": {"action": "move", "source_path": "source.txt", "path": "target.txt"},
            }
        ),
    )
    step = approve_and_bind(coordinator, task)
    original_link = os.link

    def link_then_crash(old: Path, new: Path) -> None:
        original_link(old, new)
        raise KeyboardInterrupt("simulated process crash after linking move target")

    with monkeypatch.context() as context:
        context.setattr(os, "link", link_then_crash)
        with pytest.raises(KeyboardInterrupt):
            await coordinator.personal.apply_execution(step)
    coordinator.ledger.control(task["id"], 1, "cancel", "cancel-partial-move")
    coordinator.personal.pulse()
    coordinator.ledger.settle()
    detail = coordinator.ledger.get(task["id"])
    assert detail["status"] == "needs_attention"
    assert detail["steps"][0]["status"] == "outcome_unknown"
    assert source.read_bytes() == b"move contents"
    assert (service.settings.agent.workspace / "target.txt").read_bytes() == b"move contents"
    coordinator.ledger.control(task["id"], 1, "cancel", "cancel-uncertain-again")
    coordinator.ledger.settle()
    repeated = coordinator.ledger.get(task["id"])
    assert repeated["status"] == "needs_attention"
    assert repeated["steps"][0]["status"] == "outcome_unknown"
    coordinator.ledger.control(task["id"], 1, "pause", "pause-uncertain")
    coordinator.ledger.control(task["id"], 1, "resume", "resume-uncertain")
    coordinator.ledger.settle()
    assert coordinator.ledger.get(task["id"])["status"] == "needs_attention"


class InputProbe(BaseTool):
    name: str = "computer_act"
    description: str = "Offline probe for session ownership"
    risk: RiskLevel = RiskLevel.SAFE
    calls: list[str]

    async def execute(self, **_: Any) -> ToolResult:
        self.calls.append("input")
        return ToolResult(output="done")


async def test_takeover_fences_main_chat_and_survives_another_pause_and_restart(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
) -> None:
    service, _, coordinator, _ = execution_host
    task = waiting(coordinator, plan(surface_step("browser")))
    coordinator.ledger.handoff(task["id"], 1, "take-over")
    coordinator.personal.take_over(task["id"])
    probe = InputProbe(calls=[])
    result = await service.app.sentinel.guard(ToolCall(function=Function(name=probe.name)), probe)
    assert result.error and probe.calls == []
    coordinator.ledger.control(task["id"], 1, "pause", "pause-again")
    restarted_app = create_app(service.settings, service)
    restored = restarted_app.state.assistant.personal
    result = await service.app.sentinel.guard(ToolCall(function=Function(name=probe.name)), probe)
    assert result.error and probe.calls == []
    tool = service.app.tools.get("assistant_tasks")
    assert tool is not None
    for action in ("resume", "cancel"):
        rejected = await tool.execute(
            action=action,
            task_id=task["id"],
            expected_revision=1,
            request_id=f"model-{action}",
            allow_human_return=True,
        )
        assert rejected.error
        assert restored.ledger.get(task["id"])["status"] == "paused"
        assert restored.ledger.manual_owner() == task["id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted_app), base_url="http://testserver"
    ) as client:
        body = {"request_id": "human-finished", "expected_revision": 1, "action": "resume"}
        endpoint = f"/api/assistant/tasks/{task['id']}/actions"
        assert (await client.post(endpoint, json=body)).status_code == 401
        client.headers["Authorization"] = "Bearer test-execution-session"
        assert (await client.post(endpoint, json=body)).status_code == 200
    assert restored.ledger.manual_owner() == ""
    result = await service.app.sentinel.guard(ToolCall(function=Function(name=probe.name)), probe)
    assert result.ok and probe.calls == ["input"]


def test_takeover_request_is_idempotent(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
) -> None:
    _, _, coordinator, _ = execution_host
    task = waiting(coordinator, plan(surface_step("browser")))
    coordinator.ledger.handoff(task["id"], 1, "take-over")
    coordinator.ledger.handoff(task["id"], 1, "take-over")
    events = coordinator.ledger.get(task["id"])["events"]
    assert sum(event["kind"] == "human_takeover" for event in events) == 1


async def test_legacy_computer_input_waiting_for_app_permission_stops_on_takeover(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, coordinator, _ = execution_host
    task = waiting(coordinator, plan(surface_step("browser")))
    entered, release = asyncio.Event(), asyncio.Event()
    inputs: list[dict[str, Any]] = []

    async def app_gate(app: str, label: str) -> bool:
        entered.set()
        await release.wait()
        return True

    async def fake_input(
        self: ComputerLink, params: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        inputs.append(params)
        return {"looked": False}

    monkeypatch.setattr(ComputerLink, "act", fake_input)
    link = ComputerLink(
        HandsSettings(enabled=True), shots_dir=service.settings.agent.workspace / "screenshots"
    )
    link.last_screen = Screen(app="notepad", app_name="Notepad")
    tool = ComputerAct(link=link, gui=GUISettings(), app_gate=app_gate)
    action = asyncio.create_task(
        service.app.sentinel.guard(
            ToolCall(
                function=Function(
                    name="computer_act",
                    arguments=json.dumps({"action": "type", "text": "do not enter"}),
                )
            ),
            tool,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    coordinator.ledger.handoff(task["id"], 1, "human-takes-session")
    release.set()
    result = await asyncio.wait_for(action, timeout=2)
    assert result.error
    assert inputs == []


@pytest.mark.parametrize("adapter", ["file", "browser", "native"])
async def test_takeover_quiesces_bound_execution_for_direct_observe_and_revision(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
    adapter: str,
) -> None:
    service, app, coordinator, llm = execution_host
    runtime = coordinator.personal
    entered = asyncio.Event()

    class ControlledSurface(OfflineSurface):
        async def execute(self, value: Any) -> dict[str, Any]:
            self.calls.append(value)
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("An interrupted action must never be resumed")

        async def observe(self) -> dict[str, Any]:
            return {
                "text": "Read-only state after human takeover",
                "observation_id": "a" * 64,
                "screenshot": str(service.settings.agent.workspace / "screenshots" / "manual.png"),
            }

    browser, native = ControlledSurface(), ControlledSurface()
    runtime.browser, runtime.native = browser, native
    task = waiting(coordinator, plan(file_step() if adapter == "file" else surface_step(adapter)))
    step = approve_and_bind(coordinator, task)
    if adapter == "file":
        service.app.sentinel.settings.mode = "strict"
    runtime._worker = asyncio.create_task(runtime._run(step))
    if adapter == "file":
        for _ in range(100):
            if service.ui.pending_approvals:
                break
            await asyncio.sleep(0.01)
        assert service.ui.pending_approvals
        assert runtime.journal.get(step["id"]) is None
    else:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert runtime.journal.get(step["id"])["status"] == "started"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": "Bearer test-execution-session"},
    ) as client:
        handed = await client.post(
            f"/api/assistant/tasks/{task['id']}/take-over",
            json={
                "request_id": "quiesce-takeover",
                "expected_revision": 1,
            },
        )
        assert handed.status_code == 200
        with pytest.raises(asyncio.CancelledError):
            await runtime._worker
        assert not service.ui.pending_approvals
        calls_before = len(browser.calls) + len(native.calls)
        runtime.pulse()
        coordinator.ledger.settle()
        quiet = coordinator.ledger.get(task["id"])
        assert quiet["status"] == "paused"
        assert quiet["steps"][0]["status"] in {"needs_attention", "outcome_unknown"}
        assert coordinator.ledger.manual_owner() == task["id"]
        assert not coordinator.ledger.bound()
        assert len(browser.calls) + len(native.calls) == calls_before
        observed = await client.post(
            "/api/assistant/execution/observe",
            json={
                "surface": "native" if adapter == "native" else "browser",
            },
        )
        assert observed.status_code == 200, observed.text
        assert observed.json()["text"] == "Read-only state after human takeover"
        revised = await client.post(
            f"/api/assistant/tasks/{task['id']}/plan-revisions",
            json={
                "request_id": "replace-after-observing",
                "expected_revision": 1,
                "reason": "Human inspected the actual state and supplied a new action",
                "plan": plan(file_step("reviewed.txt", content="reviewed")).model_dump(),
            },
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["revision"] == 2 and revised.json()["status"] == "paused"
        coordinator.tick()
        runtime.pulse()
        assert coordinator.ledger.manual_owner() == task["id"]
        assert not (service.settings.agent.workspace / "reviewed.txt").exists()
        probe = InputProbe(calls=[])
        blocked = await service.app.sentinel.guard(
            ToolCall(function=Function(name=probe.name)), probe
        )
        assert blocked.error and probe.calls == []
        task_tool = service.app.tools.get("assistant_tasks")
        assert task_tool is not None
        assert (
            await task_tool.execute(
                action="resume",
                task_id=task["id"],
                expected_revision=2,
                request_id="model-cannot-return-human-session",
            )
        ).error
        returned = await client.post(
            f"/api/assistant/tasks/{task['id']}/actions",
            json={
                "request_id": "human-returns-session",
                "expected_revision": 2,
                "action": "resume",
            },
        )
        assert returned.status_code == 200
    assert coordinator.ledger.manual_owner() == ""
    coordinator.tick()
    resumed = coordinator.ledger.get(task["id"])
    assert resumed["status"] == "waiting_user"
    assert len(browser.calls) + len(native.calls) == calls_before
    assert not (service.settings.agent.workspace / "saved.txt").exists()
    assert not (service.settings.agent.workspace / "reviewed.txt").exists()
    assert not llm.calls


async def test_browser_observed_during_takeover_runs_revised_fill_after_user_return(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
) -> None:
    service, app, coordinator, llm = execution_host
    runtime = coordinator.personal
    page = Page()
    browser = Browser(workspace=service.settings.agent.workspace)
    browser._backend = page
    service.app.tools.add(browser)
    native = OfflineSurface()
    runtime.native = native
    task = waiting(
        coordinator,
        plan(
            {
                "adapter": "browser",
                "title": "Old uncertain navigation",
                "browser": {"action": "navigate", "url": "https://old.example/"},
            }
        ),
    )
    old = approve_and_bind(coordinator, task)
    old_observation = await runtime.observe("browser")
    runtime.journal.start(old, old["spec"]["browser"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": "Bearer test-execution-session"},
    ) as client:
        assert (
            await client.post(
                f"/api/assistant/tasks/{task['id']}/take-over",
                json={
                    "request_id": "take-over-browser",
                    "expected_revision": 1,
                },
            )
        ).status_code == 200
        runtime.pulse()
        assert coordinator.ledger.get(task["id"])["status"] == "paused"
        observed_response = await client.post(
            "/api/assistant/execution/observe",
            json={
                "surface": "browser",
            },
        )
        assert observed_response.status_code == 200, observed_response.text
        observed = observed_response.json()
        assert observed["observation_hash"] != old_observation["observation_hash"]
        revised = await client.post(
            f"/api/assistant/tasks/{task['id']}/plan-revisions",
            json={
                "request_id": "observed-fill-plan",
                "expected_revision": 1,
                "reason": "User inspected the page and requested only a draft fill",
                "plan": plan(
                    {
                        "adapter": "browser",
                        "title": "Fill inspected ordinary draft",
                        "browser": {
                            "action": "fill",
                            "index": 1,
                            "text": "Reviewed draft",
                            "expected_url": observed["url"],
                            "observation_hash": observed["observation_hash"],
                        },
                    }
                ).model_dump(),
            },
        )
        assert revised.status_code == 200 and revised.json()["status"] == "paused"
        invalidations_before_return = native.invalidations
        returned = await client.post(
            f"/api/assistant/tasks/{task['id']}/actions",
            json={
                "request_id": "user-returns-browser",
                "expected_revision": 2,
                "action": "resume",
            },
        )
        assert returned.status_code == 200
        assert native.invalidations == invalidations_before_return
        coordinator.tick()
        pending = coordinator.ledger.get(task["id"])
        step = next(item for item in pending["steps"] if item["revision"] == 2)
        assert pending["status"] == "waiting_user" and page.actions == []
        approved = await client.post(
            f"/api/assistant/tasks/{task['id']}/steps/{step['id']}/authorize-action",
            json={
                "request_id": "approve-revised-fill",
                "expected_revision": 2,
            },
        )
        assert approved.status_code == 200
        coordinator.tick()
        bound = next(item for item in coordinator.ledger.bound() if item["id"] == step["id"])
        runtime._step_id = bound["id"]
        runtime._worker = asyncio.create_task(runtime._run(bound))
        for _ in range(100):
            if service.ui.pending_approvals:
                break
            if runtime._worker.done():
                break
            await asyncio.sleep(0.01)
        assert service.ui.pending_approvals
        approval_id = next(iter(service.ui.pending_approvals))
        assert (
            await client.post(f"/api/approvals/{approval_id}", json={"approved": True})
        ).status_code == 200
        await asyncio.wait_for(runtime._worker, timeout=3)
    final = coordinator.ledger.get(task["id"])
    assert final["status"] == "completed", final["steps"]
    assert final["steps"][0]["status"] == "superseded"
    assert final["steps"][-1]["result"]["verification"] == "browser_fresh_observation"
    assert page.state["elements"][1]["value"] == "Reviewed draft"
    assert page.actions == [("fill", (1, "Reviewed draft"))]
    assert not llm.calls


async def test_execution_completion_keeps_research_adapter_working(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM],
) -> None:
    service, _, coordinator, llm = execution_host
    project = coordinator.research.store.create_project(
        ProjectSpec(
            title="Research module",
            goal="Compare fixture records",
            mode="fixture",
            candidates=[
                "https://github.com/Anil-matcha/open-dots",
                "https://github.com/nano-muse/nanoMuse",
            ],
        )
    )
    task = waiting(
        coordinator,
        plan(
            file_step(),
            {
                "adapter": "research",
                "title": "Compare sources",
                "project_id": project["id"],
            },
        ),
    )
    step = approve_and_bind(coordinator, task)
    await coordinator.personal._run(step)
    coordinator.tick()
    for _ in range(20):
        if not await coordinator.research.tick():
            break
    coordinator.tick()
    detail = coordinator.ledger.get(task["id"])
    assert detail["status"] == "completed"
    assert detail["steps"][1]["result"]["domain_status"] == "succeeded"
    assert (service.settings.agent.workspace / "saved.txt").exists()
    assert not llm.calls
