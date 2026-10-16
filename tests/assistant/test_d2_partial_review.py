"""A rejected research mutation cannot partially apply its requested revision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanomuse.assistant.ledger import Ledger
from nanomuse.assistant.research_link import ResearchLink
from nanomuse.assistant.tool import AssistantTasks
from nanomuse.research.clock import FakeClock
from nanomuse.research.contracts import ProjectSpec
from nanomuse.research.store import ResearchStore


async def test_missing_request_id_research_revision_is_atomic_and_repairable(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = ResearchStore(tmp_path / "research", clock)
    ledger = Ledger(tmp_path / "tasks.sqlite3", clock.now)
    spec = ProjectSpec(
        title="Synthetic offline assistant comparison",
        goal="Compare only the supplied synthetic documents",
        candidates=["Synthetic candidate one", "Synthetic candidate two"],
        constraints=[
            {"id": "offline", "text": "Offline operation is required", "hard": True},
            {"id": "license", "text": "Apache-2.0 or GPL-3.0-or-later", "hard": True},
        ],
        mode="fixture",
    )
    try:
        # A preserved legacy project starts without an assistant task or link.
        project = store.create_project(spec)
        with store.transaction():
            source = store.list("source", project["id"])[0]
            source["enabled"] = False
            store._put("source", source)
        link = ResearchLink(ledger, store)
        tool = AssistantTasks(ledger=ledger, validate_plan=lambda plan: None, research=link)
        revised = spec.model_dump()
        revised["constraints"][1]["text"] = "GPL-3.0-or-later only"

        def state() -> dict[str, Any]:
            return {
                "project": store.get("project", project["id"]),
                "specs": store.list("spec_revision", project["id"]),
                "sources": store.list("source", project["id"]),
                "runs": store.runs(project["id"]),
                "tasks": ledger.tasks(),
                "requests": [
                    tuple(row)
                    for row in store.db.execute("SELECT * FROM assistant_research_request")
                ],
            }

        before = state()
        for expected_revision in (0, 0, 1):
            rejected = await tool.execute(
                action="research_revise",
                project_id=project["id"],
                expected_spec_id=project["spec_id"],
                expected_revision=expected_revision,
                research_spec=revised,
            )
            assert rejected.error
            assert "request_id" in rejected.error
            assert "No task or research state changed" in rejected.error
            assert state() == before

        arguments = {
            "action": "research_revise",
            "project_id": project["id"],
            "expected_spec_id": project["spec_id"],
            "research_spec": revised,
            "request_id": "synthetic-license-revision",
        }
        first = await tool.execute(**arguments)
        assert first.ok
        receipt = json.loads(first.output)
        after = state()
        assert receipt["project_id"] == project["id"]
        assert after["project"]["spec_id"] != project["spec_id"]
        assert after["project"]["input_version"] == project["input_version"] + 1
        assert after["sources"] == before["sources"]
        assert len(after["specs"]) == 2
        assert len(after["runs"]) == len(after["tasks"]) == len(after["requests"]) == 1
        assert receipt["task"]["status"] == "queued"
        assert after["runs"][0]["status"] == "paused"

        replay = await tool.execute(**arguments)
        assert replay.ok and json.loads(replay.output) == receipt
        assert state() == after
    finally:
        store.close()
