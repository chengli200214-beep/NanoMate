"""Real ledger and Sentinel around the native driver, using an offline GUI backend."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from nanomuse.assistant.native_actions import NativeActions
from nanomuse.assistant.personal import PersonalTool
from nanomuse.schema import RiskLevel
from tests.test_assistant_execution import (
    approve_and_bind,
    plan,
    waiting,
)
from tests.test_assistant_execution import (
    execution_host as execution_host,
)
from tests.test_assistant_native import harness as harness


@pytest.fixture
def native_runtime(execution_host: Any, harness: SimpleNamespace) -> SimpleNamespace:
    service, _, coordinator, llm = execution_host
    h = harness
    workspace = service.settings.agent.workspace
    (workspace / "draft.txt").write_text("initial", encoding="utf-8")
    h.hands.workspace = workspace
    h.link.shots_dir = workspace / "screenshots"
    service.app.tools.add(h.act, h.look)
    runtime = coordinator.personal
    runtime.native = NativeActions(service, workspace)
    runtime.native.can_execute = runtime.can_execute

    async def app_gate(app: str, label: str) -> bool:
        return bool(await service.app.sentinel.allow_app(app, label))

    h.act.app_gate = app_gate
    return SimpleNamespace(
        service=service,
        coordinator=coordinator,
        llm=llm,
        runtime=runtime,
        gui=h,
        workspace=workspace,
    )


async def execute_step(host: SimpleNamespace, payload: dict[str, Any]) -> dict[str, Any]:
    task = waiting(
        host.coordinator,
        plan(
            {
                "adapter": "native",
                "title": "Native document action",
                "native": payload,
            }
        ),
        request=f"native-{payload['action']}",
    )
    step = approve_and_bind(host.coordinator, task)
    runtime = host.runtime
    runtime._worker = asyncio.create_task(runtime._run(step))
    for _ in range(500):
        if runtime._worker.done():
            break
        for approval_id in tuple(host.service.ui.pending_approvals):
            host.service.ui.resolve_approval(approval_id, True)
        await asyncio.sleep(0.01)
    await asyncio.wait_for(runtime._worker, timeout=2)
    detail = runtime.ledger.get(task["id"])
    assert detail["status"] == "completed", host.service.app.audit.tail(1)
    result = detail["steps"][0]["result"]
    assert runtime.journal.get(step["id"])["result"] == result
    return result


async def test_native_task_uses_gui_save_before_reporting_verified_file(
    native_runtime: SimpleNamespace,
) -> None:
    h = native_runtime
    observed = await h.runtime.native.observe()
    opened = await execute_step(
        h,
        {
            "action": "open_app",
            "path": "draft.txt",
            "expected_observation": observed["observation_id"],
        },
    )
    assert opened["verification"] == "launch_dispatched"
    typed = await execute_step(
        h,
        {
            "action": "type",
            "text": " edited",
            "expected_observation": opened["observation"]["observation_id"],
        },
    )
    assert typed["verification"] == "native_input_dispatched"
    assert (h.workspace / "draft.txt").read_text("utf-8") == "initial"
    saved = await execute_step(
        h,
        {
            "action": "key",
            "keys": ["ctrl", "s"],
            "expected_observation": typed["observation"]["observation_id"],
        },
    )
    verified = await execute_step(
        h,
        {
            "action": "verify_saved_file",
            "saved_file": "draft.txt",
            "expected_sha256": hashlib.sha256(b"initial edited").hexdigest(),
            "expected_observation": saved["observation"]["observation_id"],
        },
    )
    assert verified["verification"] == "native_save_file_sha256"
    assert verified["gui_save_dispatched"]
    assert h.gui.hands.calls == [("type", " edited"), ("key", "ctrl", "s")]
    native_calls = [
        entry for entry in h.service.app.audit.tail(100) if entry.get("tool") == "assistant_native"
    ]
    assert len(native_calls) == 4
    assert any(entry["risk"] == "sensitive" and entry["approved"] for entry in native_calls)
    assert not h.llm.calls


async def test_runtime_native_observation_is_audited_and_works_under_human_control(
    native_runtime: SimpleNamespace,
) -> None:
    h = native_runtime
    task = waiting(
        h.coordinator,
        plan({"adapter": "native", "title": "Inspect", "native": {"action": "observe"}}),
    )
    h.runtime.ledger.handoff(task["id"], 1, "manual-native")
    observed = await h.runtime.observe("native")
    assert observed["app"] == "firefox"
    entries = h.service.app.audit.tail(20)
    entry = next(entry for entry in entries if entry.get("tool") == "assistant_observe_native")
    assert entry["ok"] and entry["risk"] == "safe"
    assert h.runtime.ledger.manual_owner() == task["id"]
    assert h.gui.hands.calls == []


def test_native_personal_tool_preserves_save_risk(native_runtime: SimpleNamespace) -> None:
    h = native_runtime
    task = waiting(
        h.coordinator,
        plan(
            {
                "adapter": "native",
                "title": "Save editor changes",
                "native": {
                    "action": "key",
                    "keys": ["ctrl", "s"],
                    "expected_observation": "a" * 64,
                },
            }
        ),
    )
    step = approve_and_bind(h.coordinator, task)
    assessment = PersonalTool(name="assistant_native", runtime=h.runtime, step=step).assess({})
    assert assessment.risk == RiskLevel.SENSITIVE
    assert assessment.warnings and assessment.target == "notepad"


@pytest.mark.parametrize("failure", ["shared_input_fence", "native_focus_change"])
async def test_native_step_preserves_desktop_interruption_reason(
    native_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    h = native_runtime
    opened = await execute_step(h, {"action": "open_app", "path": "draft.txt"})
    if failure == "shared_input_fence":
        reason = "Window focus changed before physical desktop input"
        h.gui.link.input_fence = lambda: reason
        payload = {"action": "key", "keys": ["ctrl", "s"]}
    else:
        reason = "The user changed the native window or keyboard focus"
        original_type = h.gui.hands.type

        def interrupted_type(text: str) -> None:
            original_type(text)
            h.gui.identity["focus_window"] = 99

        monkeypatch.setattr(h.gui.hands, "type", interrupted_type)
        payload = {"action": "type", "text": "abcdefghijklmnop"}
    payload["expected_observation"] = opened["observation"]["observation_id"]
    task = waiting(
        h.coordinator,
        plan(
            {
                "adapter": "native",
                "title": "Protected native input",
                "native": payload,
            }
        ),
        request=f"native-interruption-{failure}",
    )
    step = approve_and_bind(h.coordinator, task)
    responses = []
    sentinel = h.service.app.sentinel
    original_guard = sentinel.guard

    async def record_response(call: Any, tool: Any) -> Any:
        response = await original_guard(call, tool)
        if tool.name == "assistant_native":
            responses.append(response)
        return response

    monkeypatch.setattr(sentinel, "guard", record_response)
    h.runtime._worker = asyncio.create_task(h.runtime._run(step))
    for _ in range(500):
        if h.runtime._worker.done():
            break
        for approval_id in tuple(h.service.ui.pending_approvals):
            h.service.ui.resolve_approval(approval_id, True)
        await asyncio.sleep(0.01)
    await asyncio.wait_for(h.runtime._worker, timeout=2)
    assert len(responses) == 1 and responses[0].error
    assert reason in responses[0].error
    assert "phone" not in responses[0].error.lower()
    assert "pressed Stop" not in responses[0].error
    audit = next(
        entry
        for entry in reversed(h.service.app.audit.tail(100))
        if entry.get("tool") == "assistant_native"
    )
    assert not audit["ok"] and reason in audit["error"]
    detail = h.runtime.ledger.get(task["id"])
    assert detail["steps"][0]["status"] == "outcome_unknown"
    assert detail["status"] == "needs_attention"
    assert h.runtime.journal.get(step["id"])["status"] == "started"
    assert (h.workspace / "draft.txt").read_text("utf-8") == "initial"
    assert h.gui.hands.calls == ([] if failure == "shared_input_fence" else [("type", "abcdefgh")])
    assert not h.llm.calls
