"""Recovery checks exercise durable outputs, not model claims of completion."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from nanomuse.config import SentinelSettings
from nanomuse.research.api import install_research, next_check
from nanomuse.research.clock import FakeClock
from nanomuse.research.contracts import ProjectSpec, SourceInput
from nanomuse.research.orchestrator import ResearchOrchestrator
from nanomuse.research.reasoning import FixtureReasoner
from nanomuse.research.sources import FixtureFetcher
from nanomuse.research.store import ResearchStore, fingerprint
from nanomuse.sentinel import Sentinel
from nanomuse.sentinel.audit import AuditLog
from nanomuse.ui import HeadlessUI

REPOS = ["https://github.com/Anil-matcha/open-dots", "https://github.com/nano-muse/nanoMuse"]


def spec(**changes: Any) -> ProjectSpec:
    return ProjectSpec.model_validate(
        {
            "title": "Agent research",
            "goal": "Compare deployment",
            "candidates": REPOS,
            "constraints": [{"id": "local", "text": "Local deployment", "hard": True}],
            "mode": "fixture",
            **changes,
        }
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ResearchStore]:
    current = ResearchStore(tmp_path, FakeClock())
    yield current
    # Inspect a reopened database, including tests that closed their original handle.
    inspected = ResearchStore(tmp_path, current.clock)
    try:
        assert inspected.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for report in inspected.list("report_revision"):
            assert report["hash"] == fingerprint(report["markdown"])
            for ref in report["decision_refs"]:
                decision = inspected.get("decision_revision", ref)
                assert decision["project_id"] == report["project_id"]
                for evidence_ref in decision["evidence_refs"]:
                    evidence = inspected.get("evidence", evidence_ref)
                    snapshot = inspected.get("source_snapshot", evidence["snapshot_id"])
                    assert evidence["project_id"] == snapshot["project_id"] == report["project_id"]
                    assert evidence["quote"] in snapshot["content"]
                    assert evidence["version"] == snapshot["version"]
        for call in inspected.list("model_call"):
            assert call["stage"] in {"extract", "compare", "impact"}
            assert inspected.run(call["run_id"])["project_id"] == call["project_id"]
        if audit_path := next(tmp_path.glob("audit.jsonl"), None):
            records = [json.loads(line) for line in audit_path.read_text("utf-8").splitlines()]
            assert records and all(row.get("event") for row in records)
            assert any(row.get("tool") == "read_source" for row in records)
    finally:
        inspected.close()


def attach_audit(worker: ResearchOrchestrator) -> None:
    if worker.sentinel is None:
        worker.sentinel = Sentinel(
            SentinelSettings(),
            AuditLog(worker.store.directory / "audit.jsonl"),
            HeadlessUI(approve=False),
        )


async def drain(worker: ResearchOrchestrator, limit: int = 30) -> None:
    attach_audit(worker)
    for _ in range(limit):
        if not await worker.tick():
            return
    raise AssertionError("Research did not become idle")


@pytest.mark.parametrize("repeat", range(3))
async def test_timeout_is_bounded(store: ResearchStore, repeat: int) -> None:
    p = store.create_project(spec())
    run = store.enqueue(p["id"])
    for attempt in range(4):
        step = store.claim(f"worker-{repeat}")
        assert step is not None and step["attempt"] == attempt + 1
        store.fail(step, "temporary timeout", retryable=True)
        store.clock.advance(5)  # type: ignore[attr-defined]
    result = store.run(run["id"])
    assert result["steps"][0]["attempt"] == 4
    assert result["steps"][0]["error"] == "temporary timeout"
    assert result["steps"][0]["status"] == "succeeded"  # source gap, not fabricated data
    assert store.list("source_snapshot", p["id"]) == []


@pytest.mark.parametrize("repeat", range(3))
async def test_unavailable_source_is_a_visible_gap(store: ResearchStore, repeat: int) -> None:
    p = store.create_project(spec())
    store.add_source(
        p["id"], SourceInput(candidate=REPOS[0], kind="document", url="https://example.com/missing")
    )
    run = store.enqueue(p["id"])
    await drain(ResearchOrchestrator(store))
    assert store.run(run["id"])["status"] == "succeeded"
    assert any(s.get("error") for s in store.project(p["id"])["sources"])
    assert len(store.snapshots(p["id"])) == 2


@pytest.mark.parametrize("repeat", range(3))
def test_killed_before_commit_can_be_reclaimed(store: ResearchStore, repeat: int) -> None:
    p = store.create_project(spec())
    store.enqueue(p["id"])
    first = store.claim("first")
    assert first is not None
    directory = store.directory
    clock = store.clock
    store.close()
    clock.advance(61)  # type: ignore[attr-defined]
    reopened = ResearchStore(directory, clock)
    second = reopened.claim("second")
    assert second and second["id"] == first["id"] and second["attempt"] == 2
    assert not reopened.complete(first, {"should_not": "commit"})


@pytest.mark.parametrize("repeat", range(3))
async def test_committed_source_is_not_repeated_after_restart(
    store: ResearchStore, repeat: int
) -> None:
    p = store.create_project(spec())
    run = store.enqueue(p["id"])
    assert await ResearchOrchestrator(store).tick()
    first = store.run(run["id"])["steps"][0]
    directory, clock = store.directory, store.clock
    store.close()
    reopened = ResearchStore(directory, clock)
    await drain(ResearchOrchestrator(reopened))
    after = reopened.run(run["id"])
    assert after["status"] == "succeeded"
    assert after["steps"][0]["attempt"] == first["attempt"] == 1
    assert len(reopened.list("report_revision", p["id"])) == 1
    reopened.close()


@pytest.mark.parametrize("repeat", range(3))
async def test_uncommitted_report_does_not_replace_current(
    store: ResearchStore, repeat: int
) -> None:
    p = store.create_project(spec())
    store.enqueue(p["id"])
    await drain(ResearchOrchestrator(store))
    original = store.project(p["id"])["current_report"]
    run = store.enqueue(p["id"])
    worker = ResearchOrchestrator(store)
    for _ in range(4):
        await worker.tick()
    step = store.claim("interrupted")
    assert step and step["stage"] == "publish"
    assert store.project(p["id"])["current_report"]["id"] == original["id"]
    assert store.run(run["id"])["status"] == "running"


@pytest.mark.parametrize("repeat", range(3))
async def test_report_and_notice_are_atomic_and_idempotent(
    store: ResearchStore, repeat: int
) -> None:
    p = store.create_project(spec())
    run = store.enqueue(p["id"], request_key="same")
    assert store.enqueue(p["id"], request_key="same")["id"] == run["id"]
    await drain(ResearchOrchestrator(store))
    assert len(store.list("report_revision", p["id"])) == 1
    assert len(store.list("notification", p["id"])) == 1
    assert (
        store.project(p["id"])["current_report"]["id"]
        == store.list("report_revision", p["id"])[0]["id"]
    )


@pytest.mark.parametrize("repeat", range(3))
async def test_duplicate_unchanged_event_has_no_model_calls(
    store: ResearchStore, repeat: int
) -> None:
    p = store.create_project(spec())
    worker = ResearchOrchestrator(store)
    store.enqueue(p["id"])
    await drain(worker)
    calls = len(store.list("model_call", p["id"]))
    run = store.enqueue(p["id"], "check", "same-event")
    await drain(worker)
    assert store.run(run["id"])["status"] == "succeeded"
    assert len(store.list("report_revision", p["id"])) == 1
    assert len(store.list("notification", p["id"])) == 1
    assert len(store.list("model_call", p["id"])) == calls


@pytest.mark.parametrize("repeat", range(3))
async def test_old_conditions_cannot_publish(store: ResearchStore, repeat: int) -> None:
    p = store.create_project(spec())
    old = store.enqueue(p["id"])
    stale_worker = store.claim("old")
    revision = store.revise(p["id"], spec(goal="No external sandbox"))
    assert stale_worker and not store.complete(stale_worker, {"bad": "output"})
    assert store.run(old["id"])["status"] == "cancelled"
    await drain(ResearchOrchestrator(store))
    assert store.project(p["id"])["current_report"]["spec_id"] == revision["id"]


def test_requirement_revision_preserves_disabled_sources(store: ResearchStore) -> None:
    project = store.create_project(spec())
    source = store.list("source", project["id"])[0]
    source["enabled"] = False
    store._put("source", source)
    store.revise(project["id"], spec(goal="Updated requirement"))
    assert store.get("source", source["id"])["enabled"] is False
    run = store.run(store.runs(project["id"])[0]["id"])
    assert source["id"] not in {step["source_id"] for step in run["steps"]}


def test_readded_candidate_restores_its_sources(store: ResearchStore) -> None:
    project = store.create_project(spec())
    source = next(s for s in store.list("source", project["id"]) if s["candidate"] == REPOS[1])
    store.revise(project["id"], spec(candidates=[REPOS[0], "https://example.com/third"]))
    assert store.get("source", source["id"])["enabled"] is False
    store.revise(project["id"], spec())
    assert store.get("source", source["id"])["enabled"] is True


async def test_changed_requirement_reextracts_unchanged_snapshots(store: ResearchStore) -> None:
    class ScopedFixture(FixtureReasoner):
        dimensions: list[str] = []

        async def extract(self, context: dict[str, Any]) -> Any:
            dimension = context["spec"]["constraints"][0]["id"]
            self.dimensions.append(dimension)
            return [
                row.model_copy(update={"dimension": dimension})
                for row in await super().extract(context)
            ]

    project = store.create_project(spec())
    reasoner = ScopedFixture()
    engine = ResearchOrchestrator(store, reasoner=reasoner)
    store.enqueue(project["id"])
    await drain(engine)
    original = store.snapshots(project["id"])
    old_evidence = store.list("evidence", project["id"])
    store.revise(
        project["id"],
        spec(constraints=[{"id": "sandbox", "text": "No external sandbox", "hard": True}]),
    )
    await drain(engine)
    assert reasoner.dimensions == ["local", "sandbox"]
    assert store.snapshots(project["id"]) == original
    assert all(store.get("evidence", row["id"])["status"] == "superseded" for row in old_evidence)
    assert {
        e["dimension"]
        for e in engine._context(project["id"], store.project(project["id"])["spec"])["evidence"]
    } == {"sandbox"}
    store.enqueue(project["id"], "check")
    await drain(engine)
    assert reasoner.dimensions == ["local", "sandbox"]


@pytest.mark.parametrize("repeat", range(3))
async def test_correction_keeps_source_quote_and_uncertainty(
    store: ResearchStore, repeat: int
) -> None:
    p = store.create_project(spec())
    store.enqueue(p["id"])
    await drain(ResearchOrchestrator(store))
    evidence = store.list("evidence", p["id"])[0]
    correction = store.correction(
        evidence["id"], "User disputes this claim", "Needs independent check", evidence["quote"]
    )
    assert correction["verification"] == "user_correction"
    assert store.project(p["id"])["current_report"]["stale"]
    await drain(ResearchOrchestrator(store))
    assert store.project(p["id"])["current_report"]["stale"] is False
    assert store.get("evidence", evidence["id"])["status"] == "superseded"


@pytest.mark.parametrize("repeat", range(3))
def test_expired_worker_cannot_override_new_attempt(store: ResearchStore, repeat: int) -> None:
    p = store.create_project(spec())
    store.enqueue(p["id"])
    old = store.claim("first")
    store.clock.advance(61)  # type: ignore[attr-defined]
    fresh = store.claim("second")
    assert old and fresh and fresh["id"] == old["id"]
    assert not store.renew(old)
    assert not store.complete(old, {"stale": True})
    assert store.complete(fresh, {"ok": True})


async def test_pause_cancel_and_project_isolation(store: ResearchStore) -> None:
    p = store.create_project(spec())
    other = store.create_project(spec(title="Separate project"))
    store.enqueue(other["id"])
    await drain(ResearchOrchestrator(store))
    other_evidence = store.list("evidence", other["id"])
    assert other_evidence
    run = store.enqueue(p["id"])
    store.control(run["id"], "pause")
    assert store.claim("test") is None
    store.control(run["id"], "resume")
    await drain(ResearchOrchestrator(store))
    assert store.list("evidence", other["id"]) == other_evidence
    assert not {row["id"] for row in other_evidence} & {
        row["id"] for row in store.list("evidence", p["id"])
    }
    run2 = store.enqueue(p["id"])
    claimed = store.claim("test")
    store.control(run2["id"], "cancel")
    assert claimed and not store.complete(claimed, {"late": True})


def test_missed_checks_are_merged_once(store: ResearchStore) -> None:
    p = store.create_project(spec())
    with store.transaction():
        p["tracking"].update(
            enabled=True, next_check_at=store.clock.now() - 36000, interval_minutes=60
        )
        store._put("project", p)
    worker = ResearchOrchestrator(store)
    worker.schedule_due()
    worker.schedule_due()
    runs = store.runs(p["id"])
    assert len(runs) == 1
    assert runs[0]["missed_to"] - runs[0]["missed_from"] == 36000


def test_daily_check_respects_timezone() -> None:
    now = 1800000000.0
    due = next_check(
        now, {"timezone": "Asia/Shanghai", "daily_time": "09:00", "interval_minutes": 1440}
    )
    assert 0 < due - now <= 86400


async def test_reverify_one_fact_when_snapshot_has_other_active_facts(store: ResearchStore) -> None:
    p = store.create_project(spec())
    store.enqueue(p["id"])
    await drain(ResearchOrchestrator(store))
    item = store.list("evidence", p["id"])[0]
    with store.transaction():
        store._put("evidence", {**item, "id": "another", "dimension": "another"})
        item["status"] = "stale"
        store._put("evidence", item)
    run = store.enqueue(p["id"])
    await drain(ResearchOrchestrator(store))
    assert store.run(run["id"])["status"] == "succeeded"
    assert store.get("evidence", item["id"])["status"] == "active"


async def test_invalid_quote_rolls_back_the_step(store: ResearchStore) -> None:
    p = store.create_project(spec())
    run = store.enqueue(p["id"])
    worker = ResearchOrchestrator(store)
    await worker.tick()
    await worker.tick()
    step = store.claim("test")
    snapshot = store.snapshots(p["id"])[0]
    assert step and step["stage"] == "extract"
    with pytest.raises(ValueError, match="quote"):
        store.complete(
            step,
            {
                "evidence": [
                    {
                        "snapshot_id": snapshot["id"],
                        "quote": "invented",
                        "fact": "invented",
                        "dimension": "local",
                    }
                ]
            },
        )
    assert store.list("evidence", p["id"]) == []
    assert store.run(run["id"])["steps"][2]["status"] == "running"


def test_research_api_auth_and_validation(tmp_path: Path) -> None:
    class Service:
        data_dir = tmp_path

    def auth(request: Request) -> None:
        if request.headers.get("Authorization") != "Bearer test":
            raise HTTPException(401)

    app = FastAPI()
    install_research(app, Service(), [Depends(auth)])
    client = TestClient(app)
    assert client.get("/api/research/projects").status_code == 401
    client.headers["Authorization"] = "Bearer test"
    created = client.post("/api/research/projects", json=spec().model_dump())
    assert created.status_code == 200
    project_id = created.json()["id"]
    assert (
        client.get(f"/api/research/projects/{project_id}").json()["spec"]["title"] == spec().title
    )
    assert client.get("/api/research/runs/missing").status_code == 404
    assert (
        client.post(
            f"/api/research/projects/{project_id}/sources",
            json={"kind": "manual", "content": "data"},
        ).status_code
        == 400
    )


async def test_fixture_does_not_make_network_calls(
    store: ResearchStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    def reject(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Fixture tried to access the network")

    monkeypatch.setattr(socket, "getaddrinfo", reject)
    p = store.create_project(spec())
    run = store.enqueue(p["id"])
    await drain(ResearchOrchestrator(store, reasoner=FixtureReasoner(), fetcher=FixtureFetcher()))
    assert store.run(run["id"])["status"] == "succeeded"
