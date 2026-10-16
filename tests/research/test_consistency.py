"""Integration assertions for input changes, conflict records and bounded requests."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.research.clock import FakeClock
from nanomuse.research.contracts import (
    Comparison,
    ConflictDraft,
    DecisionDraft,
    EvidenceDraft,
    FetchedSource,
    ImpactJudgment,
    ProjectSpec,
    SourceInput,
)
from nanomuse.research.impact import mute_scope
from nanomuse.research.orchestrator import ResearchOrchestrator
from nanomuse.research.reasoning import FixtureReasoner, ResearchReasoner
from nanomuse.research.sources import FixtureFetcher
from nanomuse.research.store import ResearchStore
from nanomuse.server import create_app
from nanomuse.server.service import MuseService

REPOS = ["https://github.com/Anil-matcha/open-dots", "https://github.com/nano-muse/nanoMuse"]


def project_spec() -> ProjectSpec:
    return ProjectSpec.model_validate(
        {
            "title": "Research consistency",
            "goal": "Compare documented local support",
            "candidates": REPOS,
            "constraints": [{"id": "local", "text": "Local deployment", "hard": True}],
            "mode": "fixture",
        }
    )


async def finish(worker: ResearchOrchestrator) -> None:
    for _ in range(25):
        if not await worker.tick():
            return
    raise AssertionError("Workflow did not settle")


def test_v1_database_keeps_existing_runs_during_schema_upgrade(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "research.db") as old:
        old.executescript("""
            CREATE TABLE research_run (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, spec_id TEXT NOT NULL,
                kind TEXT NOT NULL, status TEXT NOT NULL, request_key TEXT NOT NULL,
                created_at REAL NOT NULL, error TEXT NOT NULL DEFAULT '',
                missed_from REAL, missed_to REAL, UNIQUE(project_id,request_key)
            );
            INSERT INTO research_run(id,project_id,spec_id,kind,status,request_key,created_at)
            VALUES('old-run','old-project','old-spec','research','succeeded','old-request',0);
            PRAGMA user_version=1;
        """)
    store = ResearchStore(tmp_path, FakeClock())
    assert store.run("old-run")["status"] == "succeeded"
    assert store.run("old-run")["input_version"] == 1
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
    store.close()


async def test_correction_fences_an_inflight_comparison(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path, FakeClock())
    project = store.create_project(project_spec())
    run = store.enqueue(project["id"])
    worker = ResearchOrchestrator(store)
    for _ in range(3):
        await worker.tick()
    old = store.claim("old-worker")
    assert old and old["stage"] == "compare"
    fact = store.list("evidence", project["id"])[0]
    corrected = store.correction(fact["id"], "Owner disputes this interpretation", "Manual review")
    assert store.run(run["id"])["status"] == "cancelled"
    assert not store.complete(old, {"comparison": {"forged": True}})
    assert not store.renew(old)
    with pytest.raises(ValueError):
        store.control(run["id"], "resume")
    await finish(worker)
    assert store.project(project["id"])["current_report"]
    assert store.get("evidence", corrected["id"])["verification"] == "user_correction"
    store.close()


async def test_unchanged_fetch_preserves_snapshot_and_refreshes_source(tmp_path: Path) -> None:
    clock = FakeClock()
    store = ResearchStore(tmp_path, clock)
    project = store.create_project(project_spec())
    worker = ResearchOrchestrator(store)
    store.enqueue(project["id"])
    await finish(worker)
    snapshots = store.list("source_snapshot", project["id"])
    report_id = store.project(project["id"])["current_report_id"]
    calls = len(store.list("model_call", project["id"]))
    checked = store.project(project["id"])["sources"][0]["checked_at"]
    clock.advance(3600)
    store.enqueue(project["id"], "check")
    await finish(worker)
    assert store.list("source_snapshot", project["id"]) == snapshots
    assert store.project(project["id"])["sources"][0]["checked_at"] != checked
    assert store.project(project["id"])["current_report_id"] == report_id
    assert len(store.list("model_call", project["id"])) == calls
    with pytest.raises(ValueError, match="immutable"):
        with store.transaction():
            store._put("source_snapshot", {**snapshots[0], "content": "overwritten"})
    store.close()


async def test_reverification_replaces_old_wording_without_reextracting_forever(
    tmp_path: Path,
) -> None:
    class RephrasingFixture(FixtureReasoner):
        reviewed = False

        async def extract(self, context: dict[str, Any]) -> list[EvidenceDraft]:
            drafts = await super().extract(context)
            return [
                draft.model_copy(update={"fact": "Reviewed observation: " + draft.fact})
                if self.reviewed
                else draft
                for draft in drafts
            ]

    store = ResearchStore(tmp_path, FakeClock())
    project = store.create_project(project_spec())
    reasoner = RephrasingFixture()
    worker = ResearchOrchestrator(store, reasoner=reasoner)
    store.enqueue(project["id"])
    await finish(worker)
    old = store.list("evidence", project["id"])[0]
    reasoner.reviewed = True
    store.reverify(old["id"])
    await finish(worker)
    assert store.get("evidence", old["id"])["status"] == "superseded"
    assert any(
        e["status"] == "active" and e["fact"].startswith("Reviewed observation:")
        for e in store.list("evidence", project["id"])
    )
    extraction_calls = sum(call["stage"] == "extract" for call in reasoner.calls)
    store.enqueue(project["id"])
    await finish(worker)
    assert sum(call["stage"] == "extract" for call in reasoner.calls) == extraction_calls
    store.close()


async def test_call_budget_pauses_analysis_but_allows_zero_call_publication(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path, FakeClock())
    project = store.create_project(project_spec())
    project["tracking"]["max_calls"] = 1
    with store.transaction():
        store._put("project", project)
    run = store.enqueue(project["id"])
    worker = ResearchOrchestrator(store)
    await finish(worker)
    assert store.run(run["id"])["status"] == "needs_attention"
    assert len(store.list("model_call", project["id"])) == 1
    project["tracking"]["max_calls"] = 2
    with store.transaction():
        store._put("project", project)
    store.control(run["id"], "retry")
    await finish(worker)
    assert store.run(run["id"])["status"] == "succeeded"
    assert len(store.list("model_call", project["id"])) == 2
    assert store.project(project["id"])["current_report"]
    store.close()


class ScriptedConflictReasoner(FixtureReasoner):
    """An explicit model script, validating conflict persistence rather than detection quality."""

    async def compare(self, context: dict[str, Any]) -> Comparison:
        snapshots = {s["id"]: s for s in context["snapshots"]}
        refs = [
            e["id"] for e in context["evidence"] if snapshots[e["snapshot_id"]]["kind"] == "manual"
        ]
        self._queue(
            Comparison(
                summary="Two registered notes disagree.",
                recommendation="Verify the discrepancy before selection.",
                decisions=[
                    DecisionDraft(
                        candidate=REPOS[0],
                        dimension="local",
                        conclusion="Conflicting notes.",
                        verdict="unknown",
                        constraint_refs=["local"],
                        evidence_refs=refs,
                    )
                ],
                conflicts=[
                    ConflictDraft(
                        candidate=REPOS[0],
                        dimension="local",
                        evidence_refs=refs,
                        reason="The two supplied notes make opposing local deployment claims.",
                    )
                ],
            )
        )
        return await ResearchReasoner.compare(self, context)


@pytest.mark.parametrize("repeat", range(3))
async def test_conflict_retains_both_source_versions_and_cannot_settle_requirement(
    tmp_path: Path,
    repeat: int,
) -> None:
    store = ResearchStore(tmp_path, FakeClock())
    project = store.create_project(project_spec())
    for content in ("Local deployment is supported.", "Local deployment is not supported."):
        store.add_source(
            project["id"],
            SourceInput(
                candidate=REPOS[0],
                kind="manual",
                content=content,
            ),
        )
    run = store.enqueue(project["id"])
    await finish(ResearchOrchestrator(store, reasoner=ScriptedConflictReasoner()))
    assert store.run(run["id"])["status"] == "succeeded"
    conflicts = store.list("evidence_conflict", project["id"])
    assert len(conflicts) == 1 and conflicts[0]["status"] == "open"
    assert len(set(conflicts[0]["evidence_refs"])) == 2
    assert conflicts[0]["version_scope"]
    report = store.project(project["id"])["current_report"]["markdown"]
    assert "Local deployment is supported." in report
    assert "Local deployment is not supported." in report
    assert all(d["verdict"] == "unknown" for d in store.list("decision_revision", project["id"]))
    store.close()


def test_interrupted_request_keeps_its_budget_reservation(tmp_path: Path) -> None:
    clock = FakeClock()
    store = ResearchStore(tmp_path, clock)
    project = store.create_project(project_spec())
    run = store.enqueue(project["id"])
    old = store.claim("lost-worker")
    assert old
    store.record_calls(
        project["id"],
        run["id"],
        old["id"],
        [
            {"id": "call_reserved", "status": "running", "total_tokens": None},
        ],
    )
    clock.advance(61)
    assert store.claim("resumed-worker")
    calls = store.list("model_call", project["id"])
    assert len(calls) == 1 and calls[0]["status"] == "unknown"
    assert calls[0]["total_tokens"] is None
    store.close()


class ChangingSource:
    version = "v1"

    async def fetch(
        self,
        source: dict[str, Any],
        previous: dict[str, Any] | None = None,
    ) -> FetchedSource:
        version = self.version if source["candidate"] == REPOS[0] else "unchanged"
        return FetchedSource(
            url=source["url"],
            content=f"Saved documentation for {version}.",
            version=version,
            not_modified=bool(previous and previous["version"] == version),
        )


class ScriptedMeaningfulChange(FixtureReasoner):
    async def impact(self, context: dict[str, Any]) -> ImpactJudgment:
        refs = [e["id"] for e in context["evidence"] if e["candidate"] == REPOS[0]]
        self._queue(
            ImpactJudgment(
                change_summary="Scripted meaningful source update, awaiting owner review.",
                affected_constraints=["local"],
                evidence_refs=refs,
                impact_level="recommendation",
                recommended_action="Review the cited source update.",
            )
        )
        return await ResearchReasoner.impact(self, context)


async def test_meaningful_change_is_deduplicated_and_muting_is_scoped(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path, FakeClock())
    project = store.create_project(project_spec())
    fetcher = ChangingSource()
    worker = ResearchOrchestrator(store, fetcher=fetcher, reasoner=ScriptedMeaningfulChange())
    store.enqueue(project["id"])
    await finish(worker)
    fetcher.version = "v2"
    store.enqueue(project["id"], "check")
    await finish(worker)
    events = store.list("impact_event", project["id"])
    assert len(events) == 1 and events[0]["impact_level"] == "recommendation"
    notices = store.list("notification", project["id"])
    assert len([n for n in notices if n["event_id"] == events[0]["id"]]) == 1
    reports = len(store.list("report_revision", project["id"]))
    calls = len(store.list("model_call", project["id"]))
    store.enqueue(project["id"], "check")
    await finish(worker)
    assert len(store.list("report_revision", project["id"])) == reports
    assert len(store.list("model_call", project["id"])) == calls
    assert len(store.list("notification", project["id"])) == len(notices)
    with store.transaction():
        store._put(
            "mute_rule",
            {
                "id": "mute_test",
                "scope": mute_scope(events[0]),
                "project_id": project["id"],
                "source_id": events[0]["source_id"],
                "impact_level": "recommendation",
                "enabled": True,
            },
        )
    fetcher.version = "v3"
    store.enqueue(project["id"], "check")
    await finish(worker)
    assert len(store.list("impact_event", project["id"])) == 2
    assert len(store.list("notification", project["id"])) == len(notices)
    assert len(store.list("report_revision", project["id"])) == reports + 1
    store.close()


class UncertainConflictReasoner(ScriptedConflictReasoner):
    async def compare(self, context: dict[str, Any]) -> Comparison:
        if not any("not supported" in row["quote"] for row in context["evidence"]):
            return await FixtureReasoner.compare(self, context)
        return await super().compare(context)

    async def impact(self, context: dict[str, Any]) -> ImpactJudgment:
        self._queue(
            ImpactJudgment(
                change_summary="Current local-deployment claims disagree, verification is needed.",
                affected_constraints=["local"],
                evidence_refs=[
                    row["id"] for row in context["evidence"] if row["candidate"] == REPOS[0]
                ],
                impact_level="uncertain",
                recommended_action="Keep the verdict unknown and verify the current sources.",
            )
        )
        return await ResearchReasoner.impact(self, context)


class ConflictUpdateFetcher(FixtureFetcher):
    version = "v1"

    async def fetch(
        self, source: dict[str, Any], previous: dict[str, Any] | None = None
    ) -> FetchedSource:
        if source["kind"] != "manual":
            return await super().fetch(source, previous)
        changed = source["title"] == "Changing deployment page"
        version = self.version if changed else "unchanged"
        content = "Local deployment is supported."
        if changed and self.version != "v1":
            content = "Local deployment is not supported."
        return FetchedSource(
            url=source["url"],
            content=content,
            version=version,
            not_modified=bool(previous and previous["version"] == version),
        )


@pytest.mark.parametrize(
    "hard,muted,expected", [(True, False, 1), (False, False, 0), (True, True, 0)]
)
async def test_uncertain_hard_conflict_notifies_once_with_original_judgment_and_scoped_mute(
    tmp_path: Path, hard: bool, muted: bool, expected: int
) -> None:
    store = ResearchStore(tmp_path, FakeClock())
    spec = project_spec().model_dump()
    spec["constraints"][0]["hard"] = hard
    project = store.create_project(ProjectSpec.model_validate(spec))
    changing = None
    for title in ("Stable deployment page", "Changing deployment page"):
        source = store.add_source(
            project["id"],
            SourceInput(
                candidate=REPOS[0],
                kind="manual",
                title=title,
                content="Local deployment is supported.",
            ),
        )
        if title == "Changing deployment page":
            changing = source
    assert changing
    fetcher = ConflictUpdateFetcher()
    worker = ResearchOrchestrator(store, fetcher=fetcher, reasoner=UncertainConflictReasoner())
    store.enqueue(project["id"])
    await finish(worker)
    assert store.list("evidence_conflict", project["id"]) == []
    if muted:
        with store.transaction():
            store._put(
                "mute_rule",
                {
                    "id": "mute_conflict",
                    "scope": mute_scope(
                        {
                            "source_id": changing["id"],
                            "impact_level": "recommendation",
                            "spec_id": project["spec_id"],
                            "affected_constraints": ["local"],
                        }
                    ),
                    "project_id": project["id"],
                    "source_id": changing["id"],
                    "impact_level": "recommendation",
                    "enabled": True,
                },
            )
    before = len(store.list("notification", project["id"]))
    fetcher.version = "v2"
    store.enqueue(project["id"], "check")
    await finish(worker)
    event = store.list("impact_event", project["id"])[0]
    assert len(store.list("notification", project["id"])) - before == expected
    assert event["impact_level"] == ("recommendation" if hard else "uncertain")
    if hard:
        assert event["model_judgment"]["impact_level"] == "uncertain"
        assert event["notification_policy"]["constraint_refs"] == ["local"]
    current_run = store.project(project["id"])["current_report"]["run_id"]
    current_decisions = [
        row
        for row in store.list("decision_revision", project["id"])
        if row["run_id"] == current_run
    ]
    assert current_decisions and all(row["verdict"] == "unknown" for row in current_decisions)
    reports = len(store.list("report_revision", project["id"]))
    calls = len(store.list("model_call", project["id"]))
    notices = len(store.list("notification", project["id"]))
    store.enqueue(project["id"], "check")
    await finish(worker)
    assert len(store.list("report_revision", project["id"])) == reports
    assert len(store.list("model_call", project["id"])) == calls
    assert len(store.list("notification", project["id"])) == notices
    store.close()


def test_hosted_api_runs_fixture_through_sentinel_without_model_key(settings: Settings) -> None:
    settings.server.token = "research-local-test"
    settings.llm.api_key = ""
    settings.cloud.required = settings.cloud.sync = settings.cloud.models = False
    settings.hub.enabled = False
    service = MuseService(settings, llm=MockLLM([]))
    app = create_app(settings, service)
    with TestClient(app) as client:
        assert client.get("/api/research/projects").status_code == 401
        client.headers["Authorization"] = "Bearer research-local-test"
        project = client.post("/api/research/projects", json=project_spec().model_dump()).json()
        project_id = project["id"]
        response = client.post(f"/api/research/projects/{project_id}/runs", json={})
        assert response.status_code == 200
        run_id = response.json()["id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            run = client.get(f"/api/research/runs/{run_id}").json()
            if run["status"] in {"succeeded", "failed", "needs_attention"}:
                break
            time.sleep(0.02)
        assert run["status"] == "succeeded", run
        detail = client.get(f"/api/research/projects/{project_id}").json()
        assert detail["current_report"]["mode"] == "fixture"
        assert client.get(f"/api/research/projects/{project_id}/conflicts").json() == []
        exported = client.get(f"/api/research/reports/{detail['current_report_id']}/export")
        assert exported.status_code == 200 and "离线脚本回放" in exported.text
        audit = service.app.sentinel.audit.tail(100)
        assert any(item.get("tool") == "read_source" for item in audit)
        # A separate live project cannot silently inherit the fixture model.
        app.state.research.fetcher = FixtureFetcher()
        live_spec = {**project_spec().model_dump(), "mode": "live"}
        live = client.post("/api/research/projects", json=live_spec).json()
        live_run = client.post(f"/api/research/projects/{live['id']}/runs", json={}).json()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            pending = client.get(f"/api/research/runs/{live_run['id']}").json()
            if pending["status"] == "needs_attention":
                break
            time.sleep(0.02)
        assert pending["status"] == "needs_attention"
        assert "Configure a model" in pending["error"]
        assert client.get(f"/api/research/projects/{live['id']}").json()["current_report"] is None
