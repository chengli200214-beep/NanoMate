"""Strict-profile limits reach factories, settings tests and real ledger dispatch."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from nanomuse.app import NanoMuseApp
from nanomuse.assistant.capabilities import capabilities
from nanomuse.assistant.continuations import make_origin
from nanomuse.assistant.coordinator import Coordinator
from nanomuse.assistant.usage_boundary import install_usage_boundary
from nanomuse.assistant.usage_limits import (
    BlockedUsageLLM,
    UsageLimits,
    UsageStopped,
    current_usage_owner,
)
from nanomuse.config import AssistantUsageSettings, Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.schema import LLMResponse
from nanomuse.server.service import MuseService
from nanomuse.ui import HeadlessUI
from tests.test_assistant_execution import (
    OfflineSurface,
    approve_and_bind,
    plan,
    surface_step,
)
from tests.test_assistant_execution import (
    execution_host as execution_host,
)


async def test_zero_allowance_keeps_app_available_without_provider_factory(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.assistant_usage.enabled = True
    settings.memory.embeddings = "off"
    calls: list[Any] = []

    def forbidden(*args: Any, **kwargs: Any) -> None:
        calls.append(args)
        raise AssertionError("A blocked configuration must not construct a provider")

    monkeypatch.setattr("nanomuse.app.create_llm", forbidden)
    app = NanoMuseApp(settings, HeadlessUI())
    assert isinstance(app.llm, BlockedUsageLLM)
    with pytest.raises(UsageStopped):
        await app.llm.ask([])
    assert isinstance(app.make_slot_llm(settings.llm), BlockedUsageLLM)
    with pytest.raises(UsageStopped, match="visual models"):
        await app.make_gui_llm().ask([])
    assert not calls
    assert app.assistant_usage is not None
    assert app.assistant_usage.status()["requests"] == 0
    await app.close()


async def test_connection_probe_and_model_swap_share_strict_runtime_limits(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.assistant_usage.enabled = True
    settings.memory.embeddings = "off"
    settings.hub.enabled = False
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False

    class Probe(MockLLM):
        async def ask(self, *args: Any, **kwargs: Any) -> LLMResponse:
            owner = current_usage_owner()
            assert owner is not None and owner.turn_id.startswith("connection-test:")
            return await super().ask(*args, **kwargs)

    service = MuseService(settings, llm=Probe([LLMResponse(content="OK")]))
    inventory = capabilities(service)
    assert inventory["mode"] == "offline"
    assert inventory["usage_limits"]["configuration_required"]
    result = await service.connections.test_llm()
    assert result["ok"] and result["reply"] == "OK"
    assert current_usage_owner() is None
    assert service.app.assistant_usage.status()["requests"] == 0
    with pytest.raises(ValueError, match="Embeddings"):
        service.connections.set_embeddings({"mode": "on"})
    assert not (await service.connections.test_embeddings())["ok"]
    assert not (await service.connections.test_gui())["ok"]
    monkeypatch.setattr(
        "nanomuse.app.create_llm", lambda *a, **k: pytest.fail("unmetered replacement")
    )
    old_limits = service.app.assistant_usage
    service.connections._swap_llm()
    assert isinstance(service.app.llm, BlockedUsageLLM)
    assert service.app.assistant_usage is old_limits
    await asyncio.sleep(0)
    await service.app.close()


async def test_followup_task_cannot_reset_gui_allowance_before_input_intent(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM], tmp_path: Path
) -> None:
    service, _, coordinator, llm = execution_host
    limits = UsageLimits(
        tmp_path / "limits.sqlite3", AssistantUsageSettings(enabled=True, gui_actions=1)
    )
    service.app.assistant_usage = limits
    runtime = coordinator.personal
    browser = OfflineSurface()
    runtime.browser = browser
    origin = make_origin("main", "original-request", "Inspect twice", llm, service.settings)
    first = coordinator.ledger.create(plan(surface_step("browser")), "first", origin=origin)
    coordinator.tick()
    step = approve_and_bind(coordinator, coordinator.ledger.get(first["id"]))
    await runtime._run(step)
    assert coordinator.ledger.get(first["id"])["status"] == "completed"
    followup = coordinator.ledger.create(plan(surface_step("browser")), "next", origin=origin)
    coordinator.tick()
    next_step = approve_and_bind(coordinator, coordinator.ledger.get(followup["id"]))
    await runtime._run(next_step)
    assert coordinator.ledger.get(followup["id"])["status"] == "needs_attention"
    assert runtime.journal.get(next_step["id"]) is None
    assert len(browser.calls) == 1
    assert coordinator.continuations.request_root(first["id"]) == (
        coordinator.continuations.request_root(followup["id"])
    )


async def test_gui_deadline_stops_next_input_chunk_and_keeps_unknown_receipt(
    execution_host: tuple[MuseService, FastAPI, Coordinator, MockLLM], tmp_path: Path
) -> None:
    service, _, coordinator, _ = execution_host
    now = [1000.0]
    service.app.assistant_usage = UsageLimits(
        tmp_path / "limits.sqlite3",
        AssistantUsageSettings(enabled=True, gui_actions=5, gui_seconds=10),
        clock=lambda: now[0],
    )
    runtime = coordinator.personal

    class ExpiringSurface(OfflineSurface):
        async def execute(self, value: Any) -> dict[str, Any]:
            self.calls.append(value)
            assert runtime.can_execute()
            now[0] += 11
            assert not runtime.can_execute()
            raise ValueError("Stopped before the next input chunk")

    surface = ExpiringSurface()
    runtime.browser = surface
    task = coordinator.ledger.create(plan(surface_step("browser")), "expiring")
    coordinator.tick()
    step = approve_and_bind(coordinator, coordinator.ledger.get(task["id"]))
    await runtime._run(step)
    assert coordinator.ledger.get(task["id"])["steps"][0]["status"] == "outcome_unknown"
    assert runtime.journal.get(step["id"])["status"] == "started"
    assert len(surface.calls) == 1


async def test_unpriced_model_routes_preserve_auth_and_never_reach_handler() -> None:
    app = FastAPI()
    calls: list[str] = []

    @app.post("/api/avatar/start")
    async def unmetered() -> dict[str, bool]:
        calls.append("provider")
        return {"ok": True}

    def authorize(request: Request) -> None:
        if request.headers.get("Authorization") != "Bearer fixture":
            raise HTTPException(401, "Authentication required")

    install_usage_boundary(
        app,
        SimpleNamespace(
            settings=SimpleNamespace(assistant_usage=AssistantUsageSettings(enabled=True))
        ),
        authorize,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        assert (await client.post("/api/avatar/start")).status_code == 401
        response = await client.post(
            "/api/avatar/start", headers={"Authorization": "Bearer fixture"}
        )
        assert response.status_code == 409
        assert response.json()["code"] == "assistant_usage_stopped"
    assert calls == []
