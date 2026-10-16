"""The assistant entry coexists with research and never adopts a foreign database."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanomuse.assistant.profile import assistant_settings, own_directory
from nanomuse.config import Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.schema import Function, LLMResponse, ToolCall
from nanomuse.server import create_app
from nanomuse.server.service import MuseService


def test_foreign_directory_is_untouched(tmp_path: Path) -> None:
    old = tmp_path / "research.db"
    old.write_bytes(b"retained-test-data")
    with pytest.raises(ValueError, match="existing data is preserved"):
        with own_directory(tmp_path):
            pytest.fail("adopted foreign data")
    assert list(tmp_path.iterdir()) == [old]
    assert old.read_bytes() == b"retained-test-data"


def test_single_instance_is_released_after_owner_exits(tmp_path: Path) -> None:
    directory = tmp_path / "assistant"
    code = (
        "from pathlib import Path; from nanomuse.assistant.profile import own_directory; "
        "import sys\nwith own_directory(Path(sys.argv[1])): print('owned')"
    )
    with own_directory(directory):
        other = subprocess.run(
            [sys.executable, "-c", code, str(directory)], capture_output=True, text=True
        )
        assert other.returncode != 0
        assert "Another assistant owns" in other.stderr
    other = subprocess.run(
        [sys.executable, "-c", code, str(directory)], capture_output=True, text=True
    )
    assert other.returncode == 0, other.stderr


def test_assistant_profile_keeps_auth_and_local_tools(tmp_path: Path) -> None:
    settings = assistant_settings(tmp_path, 8788)
    assert settings.data_dir == tmp_path
    assert settings.agent.workspace == tmp_path / "workspace"
    assert settings.agent.extra_roots == []
    assert settings.server.auth and settings.server.host == "127.0.0.1"
    assert not settings.cloud.required and not settings.hub.enabled
    assert settings.sentinel.mode == "ask"
    assert settings.memory.enabled


def call(name: str, **arguments: str) -> ToolCall:
    return ToolCall(function=Function(name=name, arguments=json.dumps(arguments)))


def wait_idle(service: MuseService) -> None:
    until = time.monotonic() + 10
    while time.monotonic() < until:
        thread = service.threads["main"]
        if not thread.busy and thread.inbox.empty():
            return
        time.sleep(0.02)
    pytest.fail("chat did not settle")


def test_chat_tools_research_and_authenticated_inventory_coexist(settings: Settings) -> None:
    settings.server.token = "test-assistant-session"
    settings.hub.enabled = False
    settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    llm = MockLLM(
        [
            LLMResponse(
                tool_calls=[call("files", action="write", path="notes.md", content="Notes")]
            ),
            LLMResponse(
                tool_calls=[
                    call(
                        "reminders",
                        action="create",
                        kind="remind",
                        text="Review notes",
                        at="2099-01-01T09:00:00+08:00",
                    )
                ]
            ),
            LLMResponse(content="Saved the notes and reminder."),
        ]
    )
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)
    with TestClient(app) as client:
        assert client.get("/api/assistant/capabilities").status_code == 401
        assert client.get("/api/assistant/tasks").status_code == 401
        assert client.post("/api/assistant/tasks", json={}).status_code == 401
        client.headers["Authorization"] = "Bearer test-assistant-session"
        inventory = client.get("/api/assistant/capabilities").json()
        assert inventory["mode"] == "offline"
        assert inventory["runtime_execution_verified"] is False
        assert "test-assistant-session" not in json.dumps(inventory)
        assert not llm.calls
        assert {row["id"] for row in inventory["checks"]} >= {"files", "research", "reminders"}
        assert client.get("/api/research/projects").status_code == 200
        response = client.post("/api/threads/main/send", json={"text": "Save notes and remind me."})
        assert response.status_code == 200
        wait_idle(service)
        assert (settings.agent.workspace / "notes.md").read_text("utf-8") == "Notes"
        assert len(service.app.reminders.list()) == 1
        bindings = client.get("/api/assistant/module-bindings").json()
        assert len(bindings) == 1 and bindings[0]["owner"] == "MuseService"
        assert bindings[0]["module"] == "reminders"
        assert len(llm.calls) == 3
        assert app.state.research.store.list("project") == []
