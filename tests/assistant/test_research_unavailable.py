"""Personal task APIs remain usable when no research store has been installed."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nanomuse.assistant.api import install_assistant
from nanomuse.config import Settings
from nanomuse.memory.store import MemoryStore
from nanomuse.reminders import ReminderStore
from nanomuse.tools.base import ToolCollection


def test_personal_only_host_rejects_unavailable_research_without_server_error(
    settings: Settings,
) -> None:
    service = SimpleNamespace(
        data_dir=settings.data_dir,
        settings=settings,
        app=SimpleNamespace(
            memory=MemoryStore(settings.data_dir / "memory.db"),
            reminders=ReminderStore(settings.data_dir / "reminders.db"),
            tools=ToolCollection(),
        ),
        bus=SimpleNamespace(publish=lambda event: None),
    )
    app = FastAPI()
    app.state.research = SimpleNamespace()
    install_assistant(app, service, [])
    assert app.state.assistant.research_link is None
    with TestClient(app) as client:
        plan = {
            "title": "Personal review",
            "success_criteria": "User confirms reviewing notes",
            "steps": [{"adapter": "checkpoint", "title": "Review notes"}],
        }
        assert (
            client.post(
                "/api/assistant/tasks", json={"request_id": "personal", "plan": plan}
            ).status_code
            == 200
        )
        plan["steps"] = [{"adapter": "research", "title": "Research", "project_id": "project"}]
        rejected = client.post(
            "/api/assistant/tasks", json={"request_id": "research", "plan": plan}
        )
        assert rejected.status_code == 400
        assert rejected.json()["detail"] == "The research module is unavailable"
        assert len(client.get("/api/assistant/tasks").json()) == 1
