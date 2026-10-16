"""Download recovery through the actual task ledger and Sentinel, without network."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from nanomuse.assistant.ledger import Ledger
from nanomuse.assistant.personal import PersonalRuntime, PersonalTool
from nanomuse.tools.browser import Browser
from tests.test_assistant_browser import Page
from tests.test_assistant_downloads import public_resolver
from tests.test_assistant_execution import approve_and_bind, plan, waiting
from tests.test_assistant_execution import execution_host as execution_host


@pytest.fixture
def download_runtime(execution_host: Any, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    service, _, coordinator, llm = execution_host
    browser = Browser(workspace=service.settings.agent.workspace, holds=service.app.holds)
    page = Page()
    browser._backend = page
    service.app.tools.add(browser)
    runtime = coordinator.personal
    runtime.wire_fences()
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"\x00fixture document\xff")

    assert runtime.browser.downloads is not None
    original_fetch = runtime.browser.downloads._fetch

    async def fetch(*args: Any, **kwargs: Any) -> Any:
        return await original_fetch(*args, **{**kwargs, "transport": httpx.MockTransport(respond)})

    monkeypatch.setattr(runtime.browser.downloads, "_fetch", fetch)
    runtime.browser.downloads._resolver = public_resolver
    return SimpleNamespace(
        service=service,
        coordinator=coordinator,
        llm=llm,
        runtime=runtime,
        page=page,
        requests=requests,
        workspace=service.settings.agent.workspace,
    )


async def download_waiting(host: SimpleNamespace) -> dict[str, Any]:
    observation = await host.runtime.browser.observe()
    return waiting(
        host.coordinator,
        plan(
            {
                "adapter": "browser",
                "title": "Download observed document",
                "browser": {
                    "action": "download",
                    "index": 0,
                    "expected_url": observation["url"],
                    "observation_hash": observation["observation_hash"],
                    "path": "document.bin",
                },
            }
        ),
    )


async def approved_call(host: SimpleNamespace, action: Any) -> Any:
    future = asyncio.create_task(action)
    for _ in range(500):
        if future.done():
            break
        for approval_id in tuple(host.service.ui.pending_approvals):
            host.service.ui.resolve_approval(approval_id, True)
        await asyncio.sleep(0.01)
    return await asyncio.wait_for(future, timeout=2)


def restarted(host: SimpleNamespace, step: dict[str, Any]) -> PersonalRuntime:
    runtime = PersonalRuntime(Ledger(host.coordinator.ledger.path), host.service)
    runtime._step_id = step["id"]
    return runtime


async def apply_with_task_context(host: SimpleNamespace, step: dict[str, Any]) -> dict[str, Any]:
    sentinel = host.service.app.sentinel
    token = sentinel.begin_task(
        purpose=step["spec"]["title"],
        task_id=f"{step['task_id']}:{step['revision']}",
        conversation=f"assistant:{step['task_id']}",
    )
    try:
        return await host.runtime.apply_execution(step)
    finally:
        sentinel.end_task(token)


async def test_download_preview_is_user_only_and_cannot_be_skipped(
    download_runtime: SimpleNamespace,
) -> None:
    h = download_runtime
    task = await download_waiting(h)
    step = task["steps"][0]
    assert step["spec"]["browser"]["target_absent"] is True
    result = await PersonalTool(name="assistant_browser", runtime=h.runtime, step=step).execute()
    assert result.error
    assert not h.requests
    assert not (h.workspace / "document.bin").exists()
    assert not h.llm.calls


async def test_download_pipeline_has_file_receipt_and_one_network_get(
    download_runtime: SimpleNamespace,
) -> None:
    h = download_runtime
    task = await download_waiting(h)
    step = approve_and_bind(h.coordinator, task)
    await approved_call(h, h.runtime._run(step))
    final = h.coordinator.ledger.get(task["id"])
    assert final["status"] == "completed"
    result = final["steps"][0]["result"]
    assert result["adapter"] == "browser"
    assert result["action"] == "download"
    assert result["verification"]["kind"] == "file_sha256"
    assert result["verification"]["size_bytes"] == len(b"\x00fixture document\xff")
    assert (h.workspace / "document.bin").read_bytes() == b"\x00fixture document\xff"
    assert len(h.requests) == 1 and not h.page.actions
    entries = h.service.app.audit.tail(100)
    assert any(entry["tool"] == "browser" and entry["risk"] == "sensitive" for entry in entries)
    assert not h.llm.calls


async def test_file_publication_before_journal_commit_recovers_without_second_get(
    download_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = download_runtime
    task = await download_waiting(h)
    step = approve_and_bind(h.coordinator, task)

    def crash(*_: Any) -> None:
        raise RuntimeError("simulated crash before journal receipt commit")

    monkeypatch.setattr(h.runtime.journal, "finish", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await approved_call(h, apply_with_task_context(h, step))
    assert h.runtime.journal.get(step["id"])["status"] == "started"
    assert len(h.requests) == 1
    reopened = restarted(h, step)
    await reopened._run(step)
    final = reopened.ledger.get(task["id"])
    assert final["status"] == "completed"
    assert final["steps"][0]["result"]["verification"]["kind"] == "file_sha256"
    assert reopened.journal.get(step["id"])["status"] == "completed"
    assert len(h.requests) == 1
    assert not h.llm.calls


async def test_cancel_after_published_download_records_existing_effect_without_another_get(
    download_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = download_runtime
    task = await download_waiting(h)
    step = approve_and_bind(h.coordinator, task)

    def crash(*_: Any) -> None:
        raise RuntimeError("simulated journal failure")

    with monkeypatch.context() as context:
        context.setattr(h.runtime.journal, "finish", crash)
        with pytest.raises(RuntimeError, match="simulated journal failure"):
            await approved_call(h, apply_with_task_context(h, step))
    h.coordinator.ledger.control(task["id"], 1, "cancel", "cancel-published-download")
    h.runtime.pulse()
    h.coordinator.ledger.settle()
    final = h.coordinator.ledger.get(task["id"])
    assert final["status"] == "cancelled"
    assert final["steps"][0]["status"] == "completed"
    assert final["steps"][0]["result"]["verification"]["kind"] == "file_sha256"
    assert (h.workspace / "document.bin").read_bytes() == b"\x00fixture document\xff"
    assert len(h.requests) == 1


@pytest.mark.parametrize("mutation", ["change", "delete"])
async def test_completed_download_journal_does_not_hide_changed_disk_on_recovery(
    download_runtime: SimpleNamespace, mutation: str
) -> None:
    h = download_runtime
    task = await download_waiting(h)
    step = approve_and_bind(h.coordinator, task)
    await approved_call(h, apply_with_task_context(h, step))
    assert h.runtime.journal.get(step["id"])["status"] == "completed"
    path = h.workspace / "document.bin"
    if mutation == "delete":
        path.unlink()
    else:
        path.write_bytes(b"user changed bytes")
    reopened = restarted(h, step)
    await reopened._run(step)
    final = reopened.ledger.get(task["id"])
    assert final["status"] == "needs_attention"
    assert final["steps"][0]["status"] == "outcome_unknown"
    assert len(h.requests) == 1
    if mutation == "change":
        assert path.read_bytes() == b"user changed bytes"
    else:
        assert not path.exists()


async def test_crash_before_download_store_requires_fresh_preview_without_get(
    download_runtime: SimpleNamespace,
) -> None:
    h = download_runtime
    task = await download_waiting(h)
    step = approve_and_bind(h.coordinator, task)
    h.runtime.journal.start(step, step["spec"]["browser"])
    reopened = restarted(h, step)
    await reopened._run(step)
    final = reopened.ledger.get(task["id"])
    assert final["status"] == "needs_attention"
    assert final["steps"][0]["result"]["requires_new_preview"] is True
    assert not (h.workspace / "document.bin").exists()
    assert not h.requests
    assert not h.llm.calls
