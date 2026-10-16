"""Research links recover across two stores without broadening module permissions."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nanomuse.assistant.coordinator import Coordinator
from nanomuse.assistant.errors import Conflict
from nanomuse.assistant.ledger import Ledger, Plan
from nanomuse.assistant.research_link import ResearchLink
from nanomuse.assistant.tool import AssistantTasks
from nanomuse.config import SentinelSettings, Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.research.clock import FakeClock
from nanomuse.research.contracts import ProjectSpec
from nanomuse.research.impact import mute_scope
from nanomuse.research.orchestrator import ResearchOrchestrator
from nanomuse.research.store import ResearchStore
from nanomuse.schema import Function, LLMResponse, RiskLevel, ToolCall
from nanomuse.sentinel import Sentinel
from nanomuse.sentinel.audit import AuditLog
from nanomuse.server import create_app
from nanomuse.server.service import MuseService
from nanomuse.ui import HeadlessUI


def spec(**changes: Any) -> ProjectSpec:
    return ProjectSpec.model_validate(
        {
            "title": "Agent comparison",
            "goal": "Compare local deployment",
            "candidates": [
                "https://github.com/Anil-matcha/open-dots",
                "https://github.com/nano-muse/nanoMuse",
            ],
            "mode": "fixture",
            **changes,
        }
    )


@pytest.fixture
def link(tmp_path: Path) -> Iterator[ResearchLink]:
    clock = FakeClock()
    store = ResearchStore(tmp_path / "research", clock)
    ledger = Ledger(tmp_path / "tasks.db", clock.now)
    yield ResearchLink(ledger, store)
    assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    store.close()


def requests(link: ResearchLink) -> list[dict[str, Any]]:
    return [
        json.loads(row[0])
        for row in link.store.db.execute("SELECT data FROM assistant_research_request")
    ]


def test_identical_request_reuses_project_run_and_task(link: ResearchLink) -> None:
    first = link.create(spec(), "request")
    again = link.create(spec(), "request")
    assert first == again
    assert len(link.store.list("project")) == len(link.ledger.tasks()) == 1
    assert len(link.store.runs(first["project_id"])) == 1
    assert first["task"]["steps"][0]["spec"]["spec_id"] == first["spec_id"]
    assert link.run_for_step(first["task"]["steps"][0])["id"] == first["run_id"]
    with pytest.raises(Conflict):
        link.create(spec(goal="Different condition"), "request")
    assert len(link.store.list("project")) == 1


def test_crash_after_project_commit_repairs_missing_ledger_link(
    link: ResearchLink, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = link.ledger.create
    with monkeypatch.context() as patch:
        patch.setattr(
            link.ledger, "create", lambda *a, **k: (_ for _ in ()).throw(OSError("crash"))
        )
        with pytest.raises(OSError, match="crash"):
            link.create(spec(), "interrupted")
    assert len(link.store.list("project")) == 1 and link.ledger.tasks() == []
    record = requests(link)[0]
    assert len(link.store.runs(record["project_id"])) == 1
    reopened = ResearchLink(Ledger(link.ledger.path, link.ledger.clock), link.store)
    reopened.recover()
    reopened.recover()
    result = reopened.create(spec(), "interrupted")
    assert len(link.ledger.tasks()) == 1
    assert (
        create(Plan.model_validate(record["plan"]), record["request_id"])["id"]
        == (result["task"]["id"])
    )
    assert len(link.store.runs(result["project_id"])) == 1


def test_crash_after_ledger_commit_reuses_task(link: ResearchLink) -> None:
    original = link._link
    link._link = lambda record: link.ledger.create(  # type: ignore[method-assign]
        Plan.model_validate(record["plan"]), record["request_id"]
    )
    task = link.create(spec(), "before-link-commit")
    assert not requests(link)[0]["task_id"]
    link._link = original  # type: ignore[method-assign]
    link.recover()
    assert len(link.ledger.tasks()) == 1
    assert requests(link)[0]["task_id"] == task["id"]


def test_revision_retains_project_id_and_history_without_duplicate_run(link: ResearchLink) -> None:
    first = link.create(spec(), "first")
    source = link.store.project(first["project_id"])["sources"][0]
    with link.store.transaction():
        source["enabled"] = False
        link.store._put("source", source)
    revised = link.revise(
        first["project_id"], spec(goal="Compare with an added condition"), first["spec_id"], "edit"
    )
    assert revised["project_id"] == first["project_id"]
    assert revised["spec_id"] != first["spec_id"]
    assert revised["previous_task_ids"] == [first["task"]["id"]]
    assert revised["task"]["id"] != first["task"]["id"]
    assert "conditions updated" in revised["task"]["title"]
    assert len(link.store.runs(first["project_id"])) == 2
    assert link.store.run(first["run_id"])["status"] == "cancelled"
    assert link.run_for_step(revised["task"]["steps"][0])["id"] == revised["run_id"]
    assert not link.store.get("source", source["id"])["enabled"]
    assert link.ledger.get(first["task"]["id"])["plans"][0]["revision"] == 1
    assert (
        link.revise(
            first["project_id"],
            spec(goal="Compare with an added condition"),
            first["spec_id"],
            "edit",
        )["task"]["id"]
        == revised["task"]["id"]
    )


@pytest.mark.parametrize("mode", ["fixture", "live"])
def test_stale_revision_or_mode_change_rolls_back(link: ResearchLink, mode: str) -> None:
    first = link.create(spec(), "first")
    with pytest.raises((Conflict, ValueError)):
        link.revise(first["project_id"], spec(mode=mode), "stale-spec", "stale")
    if mode == "live":
        with pytest.raises(ValueError, match="mode"):
            link.revise(first["project_id"], spec(mode=mode), first["spec_id"], "mode-change")
    assert len(link.store.list("spec_revision")) == 1
    assert len(requests(link)) == len(link.ledger.tasks()) == 1


def notice(link: ResearchLink, project_id: str, key: str = "event") -> dict[str, Any]:
    with link.store.transaction():
        project = link.store.get("project", project_id)
        event = {
            "id": key,
            "project_id": project_id,
            "source_id": "source",
            "spec_id": project["spec_id"],
            "impact_level": "recommendation",
            "affected_constraints": [],
            "affected_decisions": [],
            "change_summary": "A hard requirement changed",
        }
        link.store._put("impact_event", event)
        link.store._notify(project_id, key, event["change_summary"], key)
    return event


def test_notice_delivery_retries_stable_id_until_ack_and_not_after_restart(
    link: ResearchLink,
) -> None:
    first = link.create(spec(), "first")
    notice(link, first["project_id"])
    pending = link.pending_deliveries()
    assert len(pending) == 1 and pending[0]["task_id"] == first["task"]["id"]
    assert pending[0]["type"] == "research_change"
    assert link.pending_deliveries() == pending
    # Simulate a sink commit followed by loss before the outbox acknowledgement.
    reopened = ResearchLink(link.ledger, link.store)
    assert reopened.pending_deliveries() == pending
    reopened.delivered(pending[0]["id"])
    assert ResearchLink(link.ledger, link.store).pending_deliveries() == []
    with pytest.raises(KeyError):
        reopened.delivered("absent")


@pytest.mark.parametrize("feedback", ["read", "useless", "muted"])
def test_read_useless_or_now_muted_notices_do_not_escape(link: ResearchLink, feedback: str) -> None:
    first = link.create(spec(), "first")
    event = notice(link, first["project_id"])
    assert len(link.pending_deliveries()) == 1
    with link.store.transaction():
        if feedback == "muted":
            link.store._put(
                "mute_rule",
                {
                    "id": "muted",
                    "project_id": first["project_id"],
                    "scope": mute_scope(event),
                    "enabled": True,
                },
            )
        else:
            row = link.store.list("notification")[0]
            row["feedback"] = feedback
            link.store.db.execute(
                "UPDATE notification SET data=? WHERE id=?", (json.dumps(row), row["id"])
            )
    assert link.pending_deliveries() == []


def test_legacy_metadata_backup_and_notice_baseline_preserve_original_records(
    tmp_path: Path,
) -> None:
    store = ResearchStore(tmp_path / "research", FakeClock())
    legacy = store.create_project(spec())
    with store.transaction():
        store._notify(legacy["id"], "historical", "Old report", "old-report")
    ledger = Ledger(tmp_path / "tasks.db")
    linked = ResearchLink(ledger, store)
    assert linked.pending_deliveries() == [] and ledger.tasks() == []
    assert store.get("project", legacy["id"]) == legacy
    backups = list(store.directory.glob("research.pre-assistant-link.*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert json.loads(db.execute("SELECT data FROM project").fetchone()[0]) == legacy
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='assistant_research_request'"
        ).fetchone()
    ResearchLink(ledger, store)
    assert len(list(store.directory.glob("research.pre-assistant-link.*.db"))) == 1
    notice(linked, legacy["id"], "new-important-change")
    assert linked.pending_deliveries()[0]["project_id"] == legacy["id"]
    assert linked.pending_deliveries()[0]["task_id"] == ""
    store.close()


async def test_fixture_report_is_returned_once_and_original_executor_owns_run(
    link: ResearchLink, tmp_path: Path
) -> None:
    first = link.create(spec(), "fixture")
    worker = ResearchOrchestrator(
        link.store,
        sentinel=Sentinel(
            SentinelSettings(), AuditLog(tmp_path / "audit.jsonl"), HeadlessUI(approve=False)
        ),
    )
    coordinator = Coordinator(link.ledger, worker)
    coordinator.research_link = link
    coordinator.tick()
    coordinator.tick()
    for _ in range(20):
        if not await worker.tick():
            break
    coordinator.tick()
    assert len(link.store.runs(first["project_id"])) == 1
    assert link.ledger.get(first["task"]["id"])["status"] == "completed"
    pending = link.pending_deliveries()
    assert len(pending) == 1 and pending[0]["type"] == "research_report"
    project = link.project(first["project_id"])
    assert project["report"]["id"] == pending[0]["report_id"]
    assert project["report"]["markdown"] and not project["report"]["stale"]
    assert project["project"]["tracking"]["max_calls"] == 40
    assert link.projects()[0]["current_report_id"] == pending[0]["report_id"]
    link.delivered(pending[0]["id"])
    assert link.pending_deliveries() == []


async def test_tool_requires_explicit_mode_and_research_reads_do_not_create_tasks(
    link: ResearchLink,
) -> None:
    tool = AssistantTasks(ledger=link.ledger, validate_plan=lambda plan: None, research=link)
    assert tool.assess({"action": "research_list"}).risk == RiskLevel.SAFE
    assert tool.assess({"action": "research_create"}).risk == RiskLevel.MODERATE
    assert (await tool.execute(action="research_list")).output == "[]"
    value = spec().model_dump(exclude={"mode"})
    assert (
        await tool.execute(action="research_create", request_id="missing", research_spec=value)
    ).error
    assert link.store.list("project") == []
    created = await tool.execute(
        action="research_create", request_id="yes", research_spec=spec().model_dump()
    )
    assert not created.error
    result = json.loads(created.output)
    read = await tool.execute(action="research_get", project_id=result["project_id"])
    assert json.loads(read.output)["spec"]["goal"] == spec().goal
    assert len(link.ledger.tasks()) == 1


@pytest.mark.parametrize("action", ["pause", "cancel", "revise"])
async def test_prebound_user_control_never_leaves_an_executable_orphan(
    link: ResearchLink, action: str
) -> None:
    first = link.create(spec(), "prebound")
    assert link.store.run(first["run_id"])["status"] == "paused"
    worker = ResearchOrchestrator(link.store)
    assert not await worker.tick()
    task_id = first["task"]["id"]
    if action == "revise":
        link.ledger.revise(
            task_id,
            1,
            Plan(
                title="New personal work",
                success_criteria="User reviews the replacement plan",
                steps=[{"adapter": "checkpoint", "title": "Review"}],
            ),
            "User replaced research with a personal task",
            "replace",
        )
    else:
        link.ledger.control(task_id, 1, action, f"control-{action}")
    link.recover()
    assert link.store.run(first["run_id"])["status"] == (
        "paused" if action == "pause" else "cancelled"
    )
    assert not await worker.tick()


def test_main_chat_creates_and_revises_research_with_original_history(settings: Settings) -> None:
    settings.agent.task_ledger_only = True
    settings.server.token = "test-assistant-research-link"
    settings.hub.enabled = False
    settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    settings.sentinel.mode = "auto"

    def response(**arguments: Any) -> LLMResponse:
        return LLMResponse(
            tool_calls=[
                ToolCall(function=Function(name="assistant_tasks", arguments=json.dumps(arguments)))
            ]
        )

    llm = MockLLM(
        [
            response(
                action="research_create",
                request_id="chat-create",
                research_spec=spec().model_dump(),
            ),
            LLMResponse(content="The research task is queued."),
        ]
    )
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)

    def wait_reports(client: TestClient, count: int) -> None:
        until = time.monotonic() + 25
        while time.monotonic() < until:
            projects = client.get("/api/research/projects").json()
            notices = [
                event
                for event in service.timeline("main").events
                if event.get("source") == "research"
            ]
            if projects and len(notices) == count and not service.threads["main"].busy:
                return
            time.sleep(0.03)
        pytest.fail("The linked report did not reach the main conversation")

    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-assistant-research-link"
        assert (
            client.post(
                "/api/threads/main/send", json={"text": "Compare two agents in fixture mode."}
            ).status_code
            == 200
        )
        wait_reports(client, 1)
        project = client.get("/api/research/projects").json()[0]
        original_report = project["current_report_id"]
        original_task = client.get("/api/assistant/tasks").json()[0]
        assert original_task["status"] == "completed"
        assert len(llm.calls) == 2
        advertised = {tool["function"]["name"] for tool in llm.calls[0]["tools"]}
        assert "assistant_tasks" in advertised and "research_shell" not in advertised
        revised_spec = spec(goal="Compare with a revised condition").model_dump()
        llm.script.extend(
            [
                response(
                    action="research_revise",
                    request_id="chat-revise",
                    project_id=project["id"],
                    expected_spec_id=project["spec_id"],
                    research_spec=revised_spec,
                ),
                LLMResponse(content="A follow-up research task is queued."),
            ]
        )
        assert (
            client.post(
                "/api/threads/main/send", json={"text": "Update the condition and research again."}
            ).status_code
            == 200
        )
        wait_reports(client, 2)
        projects = client.get("/api/research/projects").json()
        assert len(projects) == 1 and projects[0]["id"] == project["id"]
        assert projects[0]["spec_id"] != project["spec_id"]
        assert client.get(f"/api/research/reports/{original_report}/export").status_code == 200
        assert (
            client.get(f"/api/assistant/tasks/{original_task['id']}").json()["status"]
            == "completed"
        )
        assert len(client.get("/api/assistant/tasks").json()) == 2
        assert len(app.state.research.store.runs(project["id"])) == 2
        assert len(llm.calls) == 4
