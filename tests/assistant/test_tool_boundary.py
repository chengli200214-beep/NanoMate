"""The isolated assistant cannot bypass task previews with raw execution tools."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import Field

from nanomuse.assistant.profile import assistant_settings
from nanomuse.assistant.tool_boundary import LedgerModelTools, ReadOnlyFiles
from nanomuse.config import Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.reminders import ReminderStore
from nanomuse.schema import Function, LLMResponse, ToolCall, ToolResult
from nanomuse.server import create_app
from nanomuse.server.service import MuseService
from nanomuse.tools.base import BaseTool, ToolCollection
from nanomuse.tools.files import Files
from nanomuse.tools.reminder_tools import Reminders

RAW = (
    "shell",
    "python_execute",
    "browser",
    "computer_act",
    "computer_task",
    "computer_screen",
    "remember",
    "forget",
)


class SpyTool(BaseTool):
    description: str = "A test execution tool."
    calls: list[dict[str, Any]] = Field(default_factory=list)

    async def execute(self, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(output="Unexpected raw execution")


async def test_live_model_view_keeps_reads_and_original_registry(tmp_path: Path) -> None:
    (tmp_path / "source.md").write_text("Source text", "utf-8")
    original = Files(workspace=tmp_path)
    source = ToolCollection(original, *(SpyTool(name=name) for name in RAW))
    view = LedgerModelTools(source)
    assert all(view.get(name) is None for name in RAW)
    assert len(view) == 1
    read = view.get("files")
    assert isinstance(read, ReadOnlyFiles)
    assert (await read.execute(action="read", path="source.md")).output == "Source text"
    assert (await read.execute(action="list", path=".")).output == "source.md"
    assert (await read.execute(action="search", pattern="*.md")).ok
    assert (await read.execute(action="write", path="source.md", content="Changed")).error
    with pytest.raises(ValueError, match="ledger"):
        read.assess({"action": "append", "path": "source.md", "content": "Changed"})
    assert (tmp_path / "source.md").read_text("utf-8") == "Source text"
    assert source.get("files") is original and source.get("browser") is not None
    source.add(SpyTool(name="assistant_tasks"), SpyTool(name="remote__execute"))
    assert "assistant_tasks" in view and "remote__execute" not in view
    assert read.assess({"action": "read", "path": "source.md"}).reads_private_data
    schema = next(t["function"] for t in view.to_params() if t["function"]["name"] == "files")
    assert schema["parameters"]["properties"]["action"]["enum"] == ["read", "list", "search"]


def test_only_assistant_profile_enables_restricted_tools(tmp_path: Path) -> None:
    assert not Settings().agent.task_ledger_only
    assert assistant_settings(tmp_path / "assistant", 8788).agent.task_ledger_only


async def test_reminder_mutations_need_ledger_but_listing_and_original_owner_remain(
    tmp_path: Path,
) -> None:
    store = ReminderStore(tmp_path / "reminders.db")
    scheduled = store.create("Review notes", at="2999-01-01 09:00")
    original = Reminders(store=store)
    registry = ToolCollection(original)
    view = LedgerModelTools(registry)
    tool = view.get("reminders")
    assert tool is not None and tool is not original
    assert "Review notes" in (await tool.execute(action="list")).output
    for action in ("create", "update", "cancel"):
        assert (await tool.execute(action=action, reminder_id=scheduled.id)).error
        with pytest.raises(ValueError, match="ledger"):
            tool.assess({"action": action})
    assert store.get(scheduled.id).status == "active"
    assert registry.get("reminders") is original
    store.close()


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(function=Function(name=name, arguments=json.dumps(arguments)))


def test_mock_model_cannot_invoke_hidden_drivers_or_write_files(settings: Settings) -> None:
    settings.agent.task_ledger_only = True
    settings.server.token = "test-profile-tool-boundary"
    settings.hub.enabled = False
    settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    settings.sentinel.mode = "auto"
    (settings.agent.workspace / "source.md").write_text("Original text", "utf-8")
    llm = MockLLM(
        [
            LLMResponse(
                tool_calls=[
                    call("files", action="write", path="source.md", content="Changed"),
                    *(call(name, action="probe") for name in RAW),
                ]
            ),
            LLMResponse(tool_calls=[call("files", action="read", path="source.md")]),
            LLMResponse(content="File changes require a task preview."),
        ]
    )
    service = MuseService(settings, llm=llm)
    spies = [SpyTool(name=name) for name in RAW]
    service.app.tools.add(*spies)
    app = create_app(settings, service)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-profile-tool-boundary"
        assert (
            client.post(
                "/api/threads/main/send", json={"text": "Inspect the execution boundary."}
            ).status_code
            == 200
        )
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if len(llm.calls) == 3 and not service.threads["main"].busy:
                break
            time.sleep(0.02)
        assert len(llm.calls) == 3
        assert not any(spy.calls for spy in spies)
        assert (settings.agent.workspace / "source.md").read_text("utf-8") == "Original text"
        assert client.get("/api/assistant/tasks").json() == []
        advertised = {tool["function"]["name"] for tool in llm.calls[0]["tools"]}
        assert not advertised.intersection(RAW)
        assert "assistant_tasks" in advertised and "files" in advertised
        tool_messages = [m for m in llm.calls[-1]["messages"] if m.role == "tool"]
        assert any("Original text" in (m.content or "") for m in tool_messages)
        assert all(service.app.tools.get(name) is spy for name, spy in zip(RAW, spies, strict=True))
        later = service._make_thread("later", "Later")
        assert isinstance(later.agent.tools, LedgerModelTools)
        assert all(later.agent.tools.get(name) is None for name in RAW)


async def test_research_source_notices_do_not_become_proactive_user_context(
    settings: Settings,
) -> None:
    settings.hub.enabled = False
    settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    llm = MockLLM([LLMResponse(content="[]")])
    service = MuseService(settings, llm=llm)
    timeline = service.timeline("main")
    timeline.add({"type": "user", "text": "Review my study plan"})
    marker = "untrusted-source-instruction-marker"
    timeline.add(
        {
            "type": "assistant",
            "source": "research",
            "text": marker,
            "source_content_is_untrusted": True,
        }
    )
    assert marker not in "\n".join(service._feed_context())
    assert "Review my study plan" in "\n".join(service._feed_context())
    await service.refresh_ideas(1)
    assert marker not in "\n".join(str(message.content) for message in llm.calls[0]["messages"])
    timeline.flush()
