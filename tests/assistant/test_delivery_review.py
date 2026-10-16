"""Research notice retries keep an immutable bubble and observable live delivery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanomuse.assistant.delivery import deliver_research
from nanomuse.server.events import Timeline


def fixture_service(tmp_path: Path) -> tuple[SimpleNamespace, Timeline, list[dict[str, Any]]]:
    timeline = Timeline("main", tmp_path / "main.json")
    published: list[dict[str, Any]] = []
    service = SimpleNamespace(
        settings=SimpleNamespace(agent=SimpleNamespace(language="en")),
        threads={"main": SimpleNamespace(agent=SimpleNamespace(ui_language="en"))},
        timeline=lambda _: timeline,
        bus=SimpleNamespace(publish=published.append),
    )
    return service, timeline, published


def item() -> dict[str, Any]:
    return {
        "id": "research:notice",
        "type": "research_report",
        "project_id": "project",
        "task_id": "task",
        "report_id": "report",
        "event_id": "report",
        "title": "Comparison",
        "content": "A report is ready",
    }


async def test_delivery_retry_after_language_change_keeps_original_event(tmp_path: Path) -> None:
    service, timeline, published = fixture_service(tmp_path)
    await deliver_research(service, item())
    original = dict(timeline.get(item()["id"]) or {})
    service.threads["main"].agent.ui_language = "zh-CN"
    # A process loss before outbox acknowledgement can retry after a locale change.
    await deliver_research(service, item())
    assert timeline.get(item()["id"]) == original
    assert len(timeline.events) == len(published) == 1


async def test_successful_retry_after_disk_failure_publishes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, timeline, published = fixture_service(tmp_path)
    original = timeline._write_confirmed
    failed = False

    def fail_once(payload: str) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("Simulated full disk")
        original(payload)

    monkeypatch.setattr(timeline, "_write_confirmed", fail_once)
    with pytest.raises(OSError, match="Simulated full disk"):
        await deliver_research(service, item())
    assert not published
    await deliver_research(service, item())
    assert len(published) == 1
    await deliver_research(service, item())
    assert len(timeline.events) == len(published) == 1
