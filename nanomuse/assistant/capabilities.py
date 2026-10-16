"""Read-only capability inventory. Registration is not successful execution."""

from __future__ import annotations

import importlib.util
import platform
from typing import TYPE_CHECKING, Any

from nanomuse.llm.mock import MockLLM
from nanomuse.research.model import research_model_ready

if TYPE_CHECKING:
    from nanomuse.server.service import MuseService


def capabilities(service: MuseService) -> dict[str, Any]:
    settings = service.settings
    tools = service.app.tools
    offline = isinstance(service.app.llm, MockLLM)
    model_configured = research_model_ready(settings)
    browser_installed = importlib.util.find_spec("playwright") is not None

    def registered(name: str) -> str:
        return "registered" if name in tools else "disabled"

    browser = registered("browser")
    if browser == "registered" and settings.browser.backend != "device" and not browser_installed:
        browser = "missing_dependency"
    return {
        "contract": "assistant-capabilities-v1",
        "mode": "offline" if offline else "live",
        "platform": platform.system(),
        "checks": [
            {
                "id": "chat",
                "status": "offline"
                if offline
                else ("configured" if model_configured else "needs_configuration"),
            },
            {"id": "goals", "status": registered("goals")},
            {"id": "reminders", "status": registered("reminders")},
            {"id": "memory", "status": registered("remember")},
            {"id": "files", "status": registered("files")},
            {"id": "browser", "status": browser},
            {"id": "computer", "status": registered("computer_task")},
            {"id": "research", "status": "registered"},
        ],
        # Never claim a connected model, working Chromium, or an interactive desktop here.
        "runtime_execution_verified": False,
        "vision_execution_verified": False,
        "unified_task_execution": True,
        "task_adapters": ["checkpoint", "research"],
        "legacy_schedule_owners": {"goals": "MuseService", "reminders": "MuseService"},
    }
