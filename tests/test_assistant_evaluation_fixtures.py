"""Preparation is disclosed test data, never a provider result or GUI approval."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evals.assistant.fixtures import (
    CANDIDATES,
    RESEARCH_CONTENT,
    SOURCE_POLICY,
    UNICODE_TEXT,
    apply_research_changes,
    enqueue_research_baseline,
    install_research_sources,
    prepare_fixtures,
    prepare_legacy_fixture,
    repeat_research_event_and_delivery,
    research_spec,
)
from evals.assistant.freeze import CASE_IDS
from nanomuse.assistant.ledger import Ledger
from nanomuse.assistant.preferences import PreferenceStore
from nanomuse.assistant.research_link import ResearchLink
from nanomuse.config import Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.memory.store import MemoryStore
from nanomuse.reminders import ReminderStore
from nanomuse.research.orchestrator import ResearchOrchestrator
from nanomuse.research.reasoning import FixtureReasoner, ResearchReasoner
from nanomuse.research.sources.http import HttpSourceFetcher
from nanomuse.research.store import ResearchStore
from nanomuse.schema import LLMResponse
from nanomuse.server import create_app
from nanomuse.server.events import EventBus, Timeline
from nanomuse.server.service import MuseService

ANCHOR = "2099-01-03T12:00:00+08:00"


@pytest.fixture
def environment(tmp_path: Path) -> tuple[Path, Any, ResearchOrchestrator, MockLLM]:
    root = tmp_path / ".local" / "acceptance"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    ledger = Ledger(root / "assistant" / "tasks.sqlite3")
    tool = SimpleNamespace(ledger=ledger)
    llm = MockLLM([])
    service = SimpleNamespace(
        data_dir=root,
        settings=SimpleNamespace(agent=SimpleNamespace(workspace=workspace)),
        app=SimpleNamespace(
            tools=SimpleNamespace(get=lambda name: tool if name == "assistant_tasks" else None),
            reminders=ReminderStore(root / "reminders.db"),
            memory=MemoryStore(root / "memory.db"),
        ),
        threads={},
    )
    research = ResearchOrchestrator(ResearchStore(root / "research"), llm=lambda: llm)
    return workspace, service, research, llm


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_all_cases_prepare_json_without_model_gui_or_implicit_approval(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
    case_id: str,
) -> None:
    workspace, service, research, llm = environment
    result = prepare_fixtures(case_id, workspace, service, research, ANCHOR)
    assert json.loads(json.dumps(result))["case_id"] == case_id
    assert result["source_policy"] == SOURCE_POLICY
    assert result["preparation_is_assistant_execution"] is False
    assert "synthetic" in result["input"].lower()
    assert llm.calls == [] and research.task is None and research._reasoners == {}
    assert not any("approved" in key for key in result)
    assert not list(workspace.rglob(".prepared-*.json"))
    assert (workspace.parent / f".prepared-{case_id}.json").is_file()
    for source in result["sources"]:
        raw = (workspace / source["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source["sha256"]
        assert len(raw) == source["size"]
    with service.app.tools.get("assistant_tasks").ledger.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM step_approval").fetchone()[0] == 0


def test_reminder_seed_uses_authoritative_id_and_exact_offset(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, _ = environment
    result = prepare_fixtures("A4", workspace, service, research, ANCHOR)
    expected = result["expected"]
    actual = service.app.reminders.get(expected["reminder_id"])
    assert actual is not None and actual.revision == expected["old_revision"] == 1
    assert actual.next_at == "2099-01-04T01:00:00+00:00"
    assert datetime.fromisoformat(expected["at"]).isoformat() == "2099-01-04T10:00:00+08:00"
    assert actual.id in result["input"]
    again = prepare_fixtures("A4", workspace, service, research, ANCHOR)
    assert again["expected"]["reminder_id"] == actual.id
    assert len(service.app.reminders.list(None)) == 1


def test_completed_study_fixture_retains_user_evidence_but_no_practice_result(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, _ = environment
    result = prepare_fixtures("B4", workspace, service, research, ANCHOR)
    task = service.app.tools.get("assistant_tasks").ledger.get(result["expected"]["task_id"])
    assert task["revision"] == 1 and task["steps"][0]["status"] == "completed"
    assert task["steps"][0]["result"]["user_evidence"].startswith("Synthetic prior user evidence")
    assert all(step["status"] == "pending" and step["result"] == {} for step in task["steps"][1:])


def test_prior_preference_is_marked_preparation_and_remains_scoped(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, _ = environment
    result = prepare_fixtures("B6", workspace, service, research, ANCHOR)
    records = PreferenceStore(
        service.data_dir / "assistant" / "preferences.sqlite3", service.app.memory
    ).list()
    assert len(records) == 1 and records[0]["purpose"] == "SQL study"
    memory_id = result["expected"]["memory_id"]
    assert memory_id in {item.id for item in service.app.memory.relevant("SQL study")}
    assert memory_id not in {
        item.id for item in service.app.memory.relevant("Plan a mountain trip")
    }
    assert result["turns"][0] == result["input"]
    assert "sql" not in result["turns"][0].casefold()
    assert "sql_study" in result["turns"][1] and "SQL study" in result["turns"][2]


def test_collision_and_locked_file_are_preparation_not_fake_failure(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, _ = environment
    result = prepare_fixtures("C2", workspace, service, research, ANCHOR)
    assert len(result["sources"]) == 6
    collision = result["seeded"]["collision"]
    assert (workspace / collision["path"]).read_bytes() == b"Preserve colliding target.\n"
    assert (workspace / result["expected"]["locked_source"]).exists()
    assert any("exclusive lock" in item for item in result["manual_requirements"])
    assert not service.app.tools.get("assistant_tasks").ledger.tasks()


def test_native_preparation_does_not_write_desired_saved_text(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, _ = environment
    result = prepare_fixtures("C5", workspace, service, research, ANCHOR)
    assert (workspace / result["expected"]["output_path"]).read_text("utf-8") != UNICODE_TEXT
    assert result["expected"]["gui_save_required"] is True
    assert any("approvals by user" in item for item in result["manual_requirements"])


@pytest.mark.parametrize("case_id", ["D2", "D3", "D4", "D5"])
async def test_synthetic_live_sources_use_real_reasoner_path_without_network(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
    case_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, service, research, llm = environment
    result = prepare_fixtures(case_id, workspace, service, research, ANCHOR)
    project_id = result["expected"]["project_id"]
    project = research.store.project(project_id)
    assert project["mode"] == "live" and project["spec"]["candidates"] == list(CANDIDATES)
    assert result["research_setup"]["baseline_required"] is True
    assert project["current_report"] is None and research.store.snapshots(project_id) == []
    assert not research.store.runs(project_id)

    def no_network(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("A controlled manual research source attempted a network request")

    monkeypatch.setattr("nanomuse.research.sources.http.httpx.AsyncClient", no_network)
    enabled = [source for source in project["sources"] if source["enabled"]]
    assert len(enabled) == 2 and all(
        source["kind"] == "manual" and not source["url"] for source in enabled
    )
    for source, content in zip(enabled, RESEARCH_CONTENT, strict=True):
        fetched = await HttpSourceFetcher(github_token="").fetch(source)
        assert fetched.content == content.strip()
    reasoner = research._get_reasoner(project, "synthetic-test-run")
    assert type(reasoner) is ResearchReasoner and not isinstance(reasoner, FixtureReasoner)
    assert llm.calls == []
    if case_id == "D4":
        assert set(result["research_setup"]["pending_source_changes"][0]) == {"source_id", "title"}
        assert result["expected"]["changed_snapshot_count"] == 0


def test_d1_source_install_keeps_chat_created_project_task_binding_valid(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    _, service, research, _ = environment
    ledger = service.app.tools.get("assistant_tasks").ledger
    linked = ResearchLink(ledger, research.store).create(research_spec(), "synthetic-chat-request")
    before = research.store.project(linked["project_id"])
    install_research_sources(research.store, linked["project_id"])
    after = research.store.project(linked["project_id"])
    assert (
        before["spec_id"] == after["spec_id"] and before["input_version"] == after["input_version"]
    )
    assert research.store.run(linked["run_id"])["status"] == "paused"
    assert all(source["kind"] == "manual" and not source["url"] for source in after["sources"])


def test_research_fixture_preparation_retry_does_not_create_another_project(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, _ = environment
    first = prepare_fixtures("D2", workspace, service, research, ANCHOR)
    second = prepare_fixtures("D2", workspace, service, research, ANCHOR)
    assert first == second
    assert len(research.store.list("project")) == 1
    with pytest.raises(ValueError, match="frozen time anchor"):
        prepare_fixtures("D2", workspace, service, research, "2099-01-04T12:00:00+08:00")


async def test_change_helpers_wait_for_counted_live_baseline_and_only_modify_registered_source(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, llm = environment
    fixtures = prepare_fixtures("D3", workspace, service, research, ANCHOR)
    with pytest.raises(ValueError, match="baseline"):
        apply_research_changes(research.store, fixtures)
    baseline = enqueue_research_baseline(research.store, fixtures)
    assert enqueue_research_baseline(research.store, fixtures)["id"] == baseline["id"]
    assert llm.calls == []
    # This is a declared MockLLM baseline, not a scored provider answer. The
    # production ResearchReasoner and report publisher still do all the work.
    llm.script.extend(
        [
            LLMResponse(content="[]"),
            LLMResponse(
                content=json.dumps(
                    {
                        "summary": "Controlled test baseline source collection completed.",
                        "recommendation": "The scripted baseline deliberately makes no ranking.",
                        "recommended_candidate": None,
                        "decisions": [],
                        "unknowns": ["This scripted unit-test baseline does not judge candidates."],
                        "next_steps": [],
                    }
                )
            ),
        ]
    )
    for _ in range(8):
        if not await research.tick():
            break
    assert research.store.run(baseline["id"])["status"] == "succeeded"
    assert len(llm.calls) == 2
    before = research.store.project(fixtures["expected"]["project_id"])
    receipts = apply_research_changes(research.store, fixtures)
    after = research.store.project(before["id"])
    assert (
        before["spec_id"] == after["spec_id"] and before["input_version"] == after["input_version"]
    )
    assert len(receipts) == 1 and receipts[0]["changed"]
    assert receipts[0]["baseline_snapshot_id"]
    assert research.store.list("impact_event", before["id"]) == []
    assert apply_research_changes(research.store, fixtures)[0]["changed"] is False
    assert len(llm.calls) == 2


def test_legacy_copy_requires_real_migration_backup_and_keeps_original_unchanged(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, _ = environment
    result = prepare_fixtures("D6", workspace, service, research, ANCHOR)
    legacy = result["seeded"]["legacy"]
    original = Path(legacy["original_database"])
    with sqlite3.connect(original) as db:
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='assistant_research_request'"
            ).fetchone()
            is None
        )
    copy = ResearchStore(Path(legacy["runtime_copy"]))
    ResearchLink(service.app.tools.get("assistant_tasks").ledger, copy)
    backups = list(Path(legacy["runtime_copy"]).glob("research.pre-assistant-link.*.db"))
    assert len(backups) == 1
    expected = legacy["expected"]
    project = copy.project(expected["project_id"])
    assert project["id"] == expected["project_id"]
    assert project["runs"][0]["id"] == expected["historical_run_id"]
    assert project["runs"][0]["status"] == "succeeded"
    report = project["current_report"]
    assert report["id"] == expected["historical_report_id"]
    assert report["hash"] == expected["historical_report_hash"]
    assert (
        hashlib.sha256(report["markdown"].encode()).hexdigest()
        == expected["historical_report_sha256"]
    )
    assert "Synthetic historical report" in report["markdown"]
    assert report["fixture_provenance"] == {"synthetic_history": True, "model_evaluated": False}
    assert all(row["verdict"] == "unknown" for row in copy.list("decision_revision", project["id"]))
    assert copy.list("model_call", project["id"]) == []
    assert {source["id"]: source["enabled"] for source in project["sources"]} == expected[
        "source_states"
    ]
    assert hashlib.sha256(original.read_bytes()).hexdigest() == legacy["original_sha256"]
    assert (
        Path(legacy["original_report_path"]).read_bytes()
        == Path(legacy["copied_report_path"]).read_bytes()
    )


async def test_real_assistant_install_preserves_historical_report_and_original_fixture(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".local" / "legacy-integration"
    legacy = prepare_legacy_fixture(root / "fixture")
    runtime = root / "runtime"
    runtime.mkdir()
    shutil.copytree(legacy["runtime_copy"], runtime / "research")
    settings = Settings()
    settings.data_dir, settings.agent.workspace = runtime, runtime / "workspace"
    settings.hub.enabled = settings.skills.enabled = False
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False
    settings.memory.embeddings = "off"
    settings.agent.task_ledger_only = True
    delegate = MockLLM([])
    service = MuseService(settings, llm=delegate)
    app = create_app(settings, service)
    try:
        store = app.state.research.store
        expected = legacy["expected"]
        project = store.project(expected["project_id"])
        report = project["current_report"]
        assert report["id"] == expected["historical_report_id"]
        assert report["run_id"] == expected["historical_run_id"]
        assert report["hash"] == expected["historical_report_hash"] and report["stale"] is False
        assert (
            hashlib.sha256(report["markdown"].encode()).hexdigest()
            == expected["historical_report_sha256"]
        )
        assert Path(legacy["original_report_path"]).read_text("utf-8") == report["markdown"]
        assert {item["id"]: item["enabled"] for item in project["sources"]} == expected[
            "source_states"
        ]
        assert len(list((runtime / "research").glob("research.pre-assistant-link.*.db"))) == 1
        assert app.state.assistant.research_link.pending_deliveries() == []
        original = Path(legacy["original_database"])
        assert hashlib.sha256(original.read_bytes()).hexdigest() == legacy["original_sha256"]
        assert not delegate.calls
    finally:
        await service.app.close()
        app.state.research.store.close()


async def test_d5_replays_actual_change_request_and_delivery_without_models_or_duplicate_state(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, llm = environment
    fixtures = prepare_fixtures("D5", workspace, service, research, ANCHOR)
    project_id = fixtures["expected"]["project_id"]
    link = ResearchLink(service.app.tools.get("assistant_tasks").ledger, research.store)
    baseline = enqueue_research_baseline(research.store, fixtures)
    comparison = {
        "summary": "Scripted synthetic unit-test comparison with unresolved verdicts.",
        "recommendation": "No candidate selected in this unit-test script.",
        "recommended_candidate": None,
        "decisions": [],
        "unknowns": ["Runtime capabilities have not been verified."],
        "next_steps": [],
    }
    llm.script.extend([LLMResponse(content="[]"), LLMResponse(content=json.dumps(comparison))])
    for _ in range(8):
        if not await research.tick():
            break
    assert research.store.run(baseline["id"])["status"] == "succeeded"
    apply_research_changes(research.store, fixtures)

    def extraction(messages: Any) -> LLMResponse:
        snapshot = next(
            item
            for item in research.store.snapshots(project_id)
            if item["source_id"] == fixtures["expected"]["changed_source_id"]
        )
        quote = "Version 2 requires an authenticated network connection for document ingestion and search."
        return LLMResponse(
            content=json.dumps(
                [
                    {
                        "snapshot_id": snapshot["id"],
                        "candidate": snapshot["candidate"],
                        "dimension": "offline",
                        "fact": "The supplied synthetic version 2 document states that a network connection is required.",
                        "quote": quote,
                        "verification": "documentation",
                        "locator": "line 4",
                    }
                ]
            )
        )

    def impact(messages: Any) -> LLMResponse:
        evidence = next(
            item
            for item in research.store.list("evidence", project_id)
            if item["status"] == "active"
        )
        return LLMResponse(
            content=json.dumps(
                {
                    "change_summary": "The synthetic source now documents a required network connection.",
                    "affected_constraints": ["offline"],
                    "affected_decisions": [],
                    "evidence_refs": [evidence["id"]],
                    "impact_level": "recommendation",
                    "recommended_action": "Review offline compatibility before choosing a candidate.",
                }
            )
        )

    llm.script.extend([extraction, LLMResponse(content=json.dumps(comparison)), impact])
    changed = research.store.enqueue(project_id, "check", "test-D5-actual-change")
    for _ in range(8):
        if not await research.tick():
            break
    assert research.store.run(changed["id"])["status"] == "succeeded"
    assert len(llm.calls) == 5
    timeline = Timeline("main", service.data_dir / "duplicate-delivery-timeline.json")
    service.timeline = lambda thread: timeline
    service.settings.agent.language = "en"
    service.bus = EventBus()
    before_report_count = len(research.store.list("report_revision", project_id))
    receipt = await repeat_research_event_and_delivery(service, link, fixtures)
    assert receipt["run_id"] == changed["id"] and receipt["repetitions"] == 2
    assert receipt["domain_inventory_unchanged"] and receipt["timeline_notice_count"] == 1
    assert receipt["model_calls_added"] == 0 and len(llm.calls) == 5
    assert len(research.store.list("report_revision", project_id)) == before_report_count
    assert sum(item["id"] == receipt["delivery_id"] for item in timeline.events) == 1
    reloaded = Timeline("main", timeline.path)
    assert reloaded.get(receipt["delivery_id"]) == timeline.get(receipt["delivery_id"])


def test_legacy_fixture_accepts_runner_preloaded_copy_without_creating_another_database(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, service, research, _ = environment
    legacy = prepare_legacy_fixture(workspace.parent / "legacy-fixture")
    service._evaluation_legacy_fixture = legacy

    def no_duplicate(directory: Path) -> dict[str, Any]:
        pytest.fail("The runner already prepared the legacy database before host construction")

    monkeypatch.setattr("evals.assistant.fixtures.prepare_legacy_fixture", no_duplicate)
    result = prepare_fixtures("D6", workspace, service, research, ANCHOR)
    assert result["seeded"]["legacy"] == legacy
    assert result["expected"]["project_id"] == legacy["expected"]["project_id"]


@pytest.mark.parametrize("change", ["naive", "outside", "busy", "research_running", "case"])
def test_fixture_preparation_rejects_unsafe_or_ambiguous_context(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
    change: str,
) -> None:
    workspace, service, research, _ = environment
    anchor, case_id = ANCHOR, "A1"
    if change == "naive":
        anchor = "2099-01-03T12:00:00"
    elif change == "outside":
        workspace = workspace.parent.parent.parent / "actual-workspace"
        service.settings.agent.workspace = workspace
    elif change == "busy":
        service.threads["main"] = SimpleNamespace(busy=True)
    elif change == "research_running":
        research.task = object()  # type: ignore[assignment]
    else:
        case_id = "D7"
    with pytest.raises(ValueError):
        prepare_fixtures(case_id, workspace, service, research, anchor)


def test_changed_source_fixture_is_not_overwritten_on_retry(
    environment: tuple[Path, Any, ResearchOrchestrator, MockLLM],
) -> None:
    workspace, service, research, _ = environment
    result = prepare_fixtures("B2", workspace, service, research, ANCHOR)
    source = workspace / result["sources"][0]["path"]
    source.write_text("User changed the fixture", "utf-8")
    with pytest.raises(ValueError, match="different material"):
        prepare_fixtures("B2", workspace, service, research, ANCHOR)
    assert source.read_text("utf-8") == "User changed the fixture"
