"""A restricted model view; the task executor keeps the original driver registry."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from nanomuse.schema import ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment, ToolCollection

# New connectors and arbitrary code execution are never implicitly exposed by the
# isolated assistant profile. Computer effects are expressed as approved ledger steps.
MODEL_TOOLS = frozenset(
    {
        "terminate",
        "ask_user",
        "files",
        "web_search",
        "web_fetch",
        "assistant_tasks",
        "goals",
        "reminders",
        "triggers",
        "recall",
    }
)
READ_ACTIONS = frozenset({"read", "list", "search"})


class ReadOnlyFiles(BaseTool):
    name: str = "files"
    description: str = (
        "Read, list and search workspace files. Mutations must be created as file steps "
        "through assistant_tasks and approved in the task screen."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["read", "list", "search"]},
            "path": {"type": "string"},
            "pattern": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    original: BaseTool
    reads_private_data: bool = True

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        if args.get("action") not in READ_ACTIONS:
            raise ValueError("File mutations require an approved task ledger step")
        assessment = self.original.assess(args)
        assessment.reads_private_data = True
        return assessment

    async def execute(self, action: str = "", **kwargs: Any) -> ToolResult:
        if action not in READ_ACTIONS or set(kwargs) - {"path", "pattern"}:
            return ToolResult.fail("File mutations require an approved task ledger step")
        return await self.original.execute(action=action, **kwargs)


class ReadOnlyReminders(BaseTool):
    name: str = "reminders"
    description: str = (
        "List current reminders. Create, update and cancel reminders through "
        "assistant_tasks reminder steps so each change has a durable task record."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["list"]}},
        "required": ["action"],
        "additionalProperties": False,
    }
    original: BaseTool
    reads_private_data: bool = True

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        if args.get("action") != "list":
            raise ValueError("Reminder changes require a task ledger step")
        assessment = self.original.assess(args)
        assessment.reads_private_data = True
        return assessment

    async def execute(self, action: str = "", **kwargs: Any) -> ToolResult:
        if action != "list" or kwargs:
            return ToolResult.fail("Reminder changes require a task ledger step")
        return await self.original.execute(action="list")


class LedgerModelTools(ToolCollection):
    """Live view of safe chat tools, never a replacement for host executor tools."""

    def __init__(self, source: ToolCollection):
        super().__init__()
        self.source = source
        self._files: ReadOnlyFiles | None = None
        self._reminders: ReadOnlyReminders | None = None

    def get(self, name: str) -> BaseTool | None:
        if name not in MODEL_TOOLS:
            return None
        tool = self.source.get(name)
        if name == "files" and tool is not None:
            if self._files is None or self._files.original is not tool:
                self._files = ReadOnlyFiles(original=tool)
            return self._files
        if name == "reminders" and tool is not None:
            if self._reminders is None or self._reminders.original is not tool:
                self._reminders = ReadOnlyReminders(original=tool)
            return self._reminders
        return tool

    def __iter__(self) -> Iterator[BaseTool]:
        return (selected for tool in self.source if (selected := self.get(tool.name)) is not None)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def to_params(self) -> list[dict[str, Any]]:
        return [tool.to_param() for tool in self]

    async def cleanup(self) -> None:
        # The host owns every underlying tool and cleans up its registry once.
        return None


def install_boundary(service: Any) -> None:
    """Refresh already-created agents; future agents wrap the same live registry."""
    if not getattr(service.settings.agent, "task_ledger_only", False):
        return
    agents = [service.app.agent, *(thread.agent for thread in service.threads.values())]
    for agent in agents:
        if not isinstance(agent.tools, LedgerModelTools):
            agent.tools = LedgerModelTools(service.app.tools)
