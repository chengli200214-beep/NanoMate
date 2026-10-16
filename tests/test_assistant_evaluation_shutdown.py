"""Offline evidence survives leaked Windows close waiters and partial task work."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import evals.assistant.guard as guard_module
import evals.assistant.runner as runner
from evals.assistant.freeze import CASE_IDS, approval_template, digest, file_hash, prepare
from nanomuse.config import LLMSettings
from nanomuse.llm.mock import MockLLM
from nanomuse.schema import LLMResponse, Message


def batch(tmp_path: Path, case: str) -> tuple[Path, MockLLM, dict[str, Any]]:
    source = tmp_path / "synthetic-source"
    source.mkdir()
    directory = tmp_path / ".local" / "batch"
    slot = LLMSettings(max_retries=0, tool_mode="native", vision="off", stream=False, timeout=120)
    frozen = prepare(source, directory, slot, "synthetic_materials_disclosed")
    before = {
        key: []
        for key in (
            "tasks",
            "reminders",
            "preferences",
            "memory",
            "projects",
            "reports",
            "notices",
            "runs",
        )
    }
    before["files"] = {}
    fixture = {"input": "Synthetic bounded request", "expected": {}, "research_setup": {}}
    if case == "B2":
        fixture["expected"] = {"output_name": "required.md"}
    record = {"fixture": fixture, "before": before}
    path = directory / "cases" / f"{case}-r1" / "fixture.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record), "utf-8")
    frozen["case_fixtures"] = {f"{case}-r1": {"path": str(path), "sha256": file_hash(path)}}
    frozen.pop("freeze_sha256")
    frozen["freeze_sha256"] = digest(frozen)
    (directory / "freeze.json").write_text(json.dumps(frozen), "utf-8")
    receipt = approval_template(frozen)
    receipt.update(status="approved", human_review_completed=True, phase="complete")
    receipt["annotations"] = [{"id": item, "approved": True} for item in CASE_IDS]
    receipt["budget_policy"] = {
        "model": "deepseek-flash",
        "limit_cny": "10",
        "stop_reserve_cny": "1",
        "context_token_ceiling": 1048576,
        "input_peak_micro_cny": 2,
        "output_peak_micro_cny": 8,
        "pricing_verified_at": "2026-10-16",
        "price_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    }
    (directory / "approval.json").write_text(json.dumps(receipt), "utf-8")
    response = LLMResponse(
        content="Synthetic reply",
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    )
    model = MockLLM([response, response])
    model.settings = slot  # type: ignore[attr-defined]
    return directory, model, before


def install_host(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
    state: dict[str, Any],
    *,
    calls: int = 1,
    pending: bool = False,
) -> Any:
    app = SimpleNamespace()
    store = SimpleNamespace(close=lambda: None)
    service = SimpleNamespace(_started=False, ui=SimpleNamespace(pending_approvals=[]))
    thread = SimpleNamespace(busy=False, inbox=asyncio.Queue(), timeline=SimpleNamespace(events=[]))
    service.threads = {"main": thread}

    async def close() -> None:
        service._started = False

    service.app = SimpleNamespace(close=close)
    app.state = SimpleNamespace(
        research=SimpleNamespace(store=store, start=lambda: None), assistant=SimpleNamespace()
    )

    def wire(_: Any, __: Any, guard: Any, **kwargs: Any) -> None:
        service.app.llm = guard.wrap(model)
        guard.quiescent = lambda: not thread.busy

    def send(*_: Any) -> None:
        thread.busy = True

        async def work() -> None:
            try:
                for _ in range(calls):
                    await service.app.llm.ask([Message.user("Synthetic isolated host request")])
            except Exception:
                # The runtime can surface a provider/tool error as a timeline item.
                # The runner must still notice the durable stop independently.
                pass
            finally:
                if pending:
                    state["tasks"] = [
                        {
                            "id": "task-pending",
                            "status": "waiting_user",
                            "steps": [
                                {
                                    "status": "waiting_user",
                                    "spec": {"adapter": "document"},
                                    "result": {},
                                }
                            ],
                        }
                    ]
                thread.busy = False

        asyncio.create_task(work())

    service.send = send
    model: MockLLM

    def host(_: Any, __: Any, delegate: MockLLM, ___: Any) -> tuple[Any, Any]:
        nonlocal model
        model = delegate
        return app, service

    monkeypatch.setattr(runner, "_host", host)
    monkeypatch.setattr(runner, "snapshot", lambda *_: deepcopy(state))
    monkeypatch.setattr(runner, "wire_isolated_service", wire)
    monkeypatch.setattr(runner, "SHUTDOWN_SECONDS", 0.03)
    monkeypatch.setattr(runner, "CANCEL_SECONDS", 0.03)
    service.checkpoint = directory / "cases"
    return app, service


def install_transport(
    monkeypatch: pytest.MonkeyPatch, app: Any, service: Any, *, hang: bool
) -> list[str]:
    actions: list[str] = []

    class Server:
        def __init__(self, config: Any):
            self.started, self.should_exit = False, False
            self.lifespan = SimpleNamespace(shutdown_event=asyncio.Event(), shutdown=self.close)

        async def close(self) -> None:
            actions.append("lifespan_shutdown")
            service._started = False
            self.lifespan.shutdown_event.set()

        async def serve(self) -> None:
            service._started, self.started = True, True
            while not self.should_exit:
                await asyncio.sleep(0)
            checkpoints = list(service.checkpoint.rglob("execution-evidence.json"))
            assert len(checkpoints) == 1
            actions.append("checkpoint_precedes_cleanup")
            if hang:
                # Mirrors Proactor's leaked Server.wait_closed waiter. Cancellation
                # is cooperative, but the waiter never completes on its own.
                await asyncio.Event().wait()
            else:
                asyncio.get_running_loop().call_exception_handler(
                    {
                        "message": "synthetic Windows callback failure",
                        "exception": ConnectionResetError("synthetic reset"),
                    }
                )

    monkeypatch.setattr("uvicorn.Server", Server)
    return actions


@pytest.mark.parametrize("hang", [True, False])
async def test_transport_failure_has_durable_precleanup_evidence_and_canonical_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hang: bool
) -> None:
    directory, model, state = batch(tmp_path, "A1")
    app, service = install_host(monkeypatch, directory, state)
    actions = install_transport(monkeypatch, app, service, hang=hang)
    result = await runner.run_case(
        directory, "A1-r1", model, allow_mock=True, serve=True, wait_seconds=2
    )
    assert actions == ["checkpoint_precedes_cleanup", "lifespan_shutdown"]
    assert result["error"] == "cleanup_failed"
    assert result["cleanup"]["status"] == "failed"
    assert any(
        ("timeout" if hang else "ConnectionResetError") in item
        for item in result["cleanup"]["issues"]
    )
    path = directory / "cases" / "A1-r1"
    assert json.loads((path / "result.json").read_text("utf-8")) == result
    checkpoint = json.loads((path / "execution-evidence.json").read_text("utf-8"))
    assert checkpoint["cleanup"]["status"] == "pending"
    assert result["execution_evidence_sha256"] == file_hash(path / "execution-evidence.json")
    assert result["technical"]["budget"]["settled_requests"] == 1
    assert result["technical"]["budget"]["unsettled_requests"] == 0
    assert result["technical"]["executions"][0]["status"] == "finished"


async def test_unfinished_action_and_swallowed_request_cap_are_not_error_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, model, state = batch(tmp_path, "B2")
    app, service = install_host(monkeypatch, directory, state, calls=2, pending=True)
    install_transport(monkeypatch, app, service, hang=True)
    monkeypatch.setattr(guard_module, "REQUESTS_PER_EXECUTION", 1)
    monkeypatch.setattr(runner, "REQUESTS_PER_EXECUTION", 1)
    result = await runner.run_case(
        directory, "B2-r1", model, allow_mock=True, serve=True, wait_seconds=2
    )
    assert "execution_request_limit_reached" in result["errors"]
    assert "domain_tasks_incomplete" in result["errors"]
    assert result["unfinished_tasks"] == [{"id": "task-pending", "status": "waiting_user"}]
    assert result["verdict"] == "pending_review" and result["error"] is not None
    with sqlite3.connect(directory / "budget.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM request").fetchone()[0] == 1
        assert db.execute("SELECT status FROM request").fetchone()[0] == "settled"
        assert db.execute("SELECT COUNT(*) FROM stop").fetchone()[0] > 0
        assert db.execute("SELECT COUNT(*) FROM assistant_execution").fetchone()[0] == 1
