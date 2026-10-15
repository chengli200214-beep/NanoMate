"""Kill a real worker process at durable boundaries, then recover its SQLite state."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import nanomuse
from nanomuse.research.clock import FakeClock
from nanomuse.research.contracts import ProjectSpec
from nanomuse.research.orchestrator import ResearchOrchestrator
from nanomuse.research.store import ResearchStore, fingerprint

RUNTIME_ROOT = Path(nanomuse.__file__).resolve().parent.parent
REPOSITORIES = [
    "https://github.com/Anil-matcha/open-dots",
    "https://github.com/nano-muse/nanoMuse",
]


def _child_worker(directory: Path, run_id: str) -> None:
    """Executed as a separate interpreter; the named hook must call os._exit(86)."""
    store = ResearchStore(directory, FakeClock())
    assert store.run(run_id)["status"] in ("queued", "running")
    worker = ResearchOrchestrator(store)

    async def tick_once() -> None:
        assert await worker.tick()

    asyncio.run(tick_once())
    raise AssertionError("The requested crash hook did not terminate the worker")


def _crash_worker(directory: Path, run_id: str, point: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["NANOMUSE_RESEARCH_CRASH"] = point
    # Use the same runtime as the parent even when this test lives in another worktree.
    environment["PYTHONPATH"] = str(RUNTIME_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--crash-worker", str(directory), run_id],
        cwd=RUNTIME_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "point", ["after_claim", "before_step_commit", "after_step_commit", "after_report_write"]
)
async def test_real_process_exit_recovers_without_duplicate_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    clock = FakeClock()
    directory = tmp_path / "research"
    store = ResearchStore(directory, clock)
    project = store.create_project(
        ProjectSpec(
            title="Process recovery",
            goal="Compare the registered deployment documentation",
            candidates=REPOSITORIES,
            constraints=[{"id": "local", "text": "Local deployment", "hard": True}],
            mode="fixture",
        )
    )
    project_id = project["id"]
    run = store.enqueue(project_id, request_key="one-logical-run")
    worker = ResearchOrchestrator(store)
    # Leave at least one committed step before every crash. For the report hook,
    # finish collection, extraction and comparison, but do not start publication.
    prefix_count = len(run["steps"]) - 1 if point == "after_report_write" else 1
    for _ in range(prefix_count):
        assert await worker.tick()
    before = store.run(run["id"])
    committed_before = {
        step["id"]: step for step in before["steps"] if step["status"] == "succeeded"
    }
    assert len(committed_before) == prefix_count
    target = next(step for step in before["steps"] if step["status"] == "pending")
    assert store.project(project_id)["current_report_id"] is None
    store.close()

    process = await asyncio.to_thread(_crash_worker, directory, run["id"], point)
    assert process.returncode == 86, (
        f"{point} did not exit through os._exit(86): {process.returncode}\n"
        f"stdout: {process.stdout}\nstderr: {process.stderr}"
    )

    # Reopening after os._exit exercises SQLite's actual process recovery, not a
    # graceful worker shutdown. No stop() call releases the dead worker's lease.
    reopened = ResearchStore(directory, clock)
    try:
        interrupted = reopened.run(run["id"])
        dead_step = next(step for step in interrupted["steps"] if step["id"] == target["id"])
        assert dead_step["attempt"] == 1
        committed_at_crash = {
            step["id"]: step for step in interrupted["steps"] if step["status"] == "succeeded"
        }
        if point == "after_step_commit":
            assert dead_step["status"] == "succeeded"
            assert dead_step["output"]
            assert len(committed_at_crash) == len(committed_before) + 1
        else:
            assert dead_step["status"] == "running"
            assert dead_step["output"] == {}
            assert len(committed_at_crash) == len(committed_before)
            assert dead_step["lease_until"] > clock.now()

        assert reopened.project(project_id)["current_report_id"] is None
        assert reopened.list("report_revision", project_id) == []
        assert reopened.list("notification", project_id) == []
        report_directory = directory / "reports" / project_id
        if point == "after_report_write":
            assert dead_step["stage"] == "publish"
            assert len(list(report_directory.glob("*.tmp"))) == 1
            assert list(report_directory.glob("*.md")) == []

        clock.advance(61)
        resumed = ResearchOrchestrator(reopened)
        original_claim = reopened.claim
        fenced_attempts: list[int] = []

        def claim_and_probe_fencing(owner: str, lease_seconds: float = 60) -> dict[str, Any] | None:
            fresh = original_claim(owner, lease_seconds)
            if fresh and fresh["id"] == dead_step["id"]:
                assert fresh["attempt"] == dead_step["attempt"] + 1
                # Reusing the new owner's identity with an old attempt specifically
                # checks attempt fencing, in addition to checking dead-worker fencing.
                old_attempt_with_new_owner = {**fresh, "attempt": dead_step["attempt"]}
                for stale in (dead_step, old_attempt_with_new_owner):
                    assert reopened.complete(stale, {"must_not_commit": True}) is False
                    assert reopened.renew(stale) is False
                    reopened.fail(stale, "stale worker must not fail the current attempt")
                current = next(
                    step for step in reopened.run(run["id"])["steps"] if step["id"] == fresh["id"]
                )
                assert current["status"] == "running"
                assert current["error"] == ""
                assert current["attempt"] == fresh["attempt"]
                fenced_attempts.append(fresh["attempt"])
            return fresh

        monkeypatch.setattr(reopened, "claim", claim_and_probe_fencing)
        # A step committed immediately before death is also immutable to its old worker.
        if point == "after_step_commit":
            assert reopened.complete(dead_step, {"must_not_commit": True}) is False
            assert reopened.renew(dead_step) is False

        for _ in range(len(run["steps"]) + 2):
            if not await resumed.tick():
                break
        else:
            pytest.fail("The recovered run did not become idle")

        finished = reopened.run(run["id"])
        assert finished["status"] == "succeeded", finished
        assert all(
            step["status"] == "succeeded" and not step["error"] for step in finished["steps"]
        )
        for step in finished["steps"]:
            if step["id"] in committed_at_crash:
                previous = committed_at_crash[step["id"]]
                assert step["attempt"] == previous["attempt"]
                assert step["output"] == previous["output"]
        recovered_target = next(step for step in finished["steps"] if step["id"] == target["id"])
        if point == "after_step_commit":
            assert recovered_target["attempt"] == 1
            assert fenced_attempts == []
        else:
            assert recovered_target["attempt"] == 2
            assert fenced_attempts == [2]

        reports = reopened.list("report_revision", project_id)
        notices = reopened.list("notification", project_id)
        assert len(reports) == len(notices) == 1
        current_report = reopened.project(project_id)["current_report"]
        assert current_report["id"] == reports[0]["id"]
        assert current_report["run_id"] == run["id"]
        assert current_report["hash"] == fingerprint(current_report["markdown"])
        assert (
            Path(current_report["path"]).read_text(encoding="utf-8") == current_report["markdown"]
        )
        assert len(list(report_directory.glob("*.md"))) == 1
        assert reopened.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

        # Reopening or polling an idle worker cannot publish the same logical run again.
        assert not await resumed.tick()
        assert not await resumed.tick()
        assert len(reopened.list("report_revision", project_id)) == 1
        assert len(reopened.list("notification", project_id)) == 1
    finally:
        reopened.close()


async def test_crash_after_report_write_keeps_previous_report_readable(tmp_path: Path) -> None:
    clock = FakeClock()
    directory = tmp_path / "research"
    store = ResearchStore(directory, clock)
    project = store.create_project(
        ProjectSpec(
            title="Report replacement",
            goal="Compare the registered deployment documentation",
            candidates=REPOSITORIES,
            mode="fixture",
        )
    )
    project_id = project["id"]
    baseline_run = store.enqueue(project_id, request_key="baseline")
    worker = ResearchOrchestrator(store)
    for _ in range(len(baseline_run["steps"])):
        assert await worker.tick()
    baseline_report = store.project(project_id)["current_report"]
    assert baseline_report is not None
    new_run = store.enqueue(project_id, request_key="replacement")
    for _ in range(len(new_run["steps"]) - 1):
        assert await worker.tick()
    store.close()

    process = await asyncio.to_thread(_crash_worker, directory, new_run["id"], "after_report_write")
    assert process.returncode == 86, process.stderr
    reopened = ResearchStore(directory, clock)
    try:
        # The temporary replacement is on disk, but readers still get the last
        # committed version and its original notification until publication commits.
        current = reopened.project(project_id)["current_report"]
        assert current["id"] == baseline_report["id"]
        assert Path(current["path"]).read_text(encoding="utf-8") == current["markdown"]
        assert len(reopened.list("report_revision", project_id)) == 1
        assert len(reopened.list("notification", project_id)) == 1
        publish = next(
            step for step in reopened.run(new_run["id"])["steps"] if step["stage"] == "publish"
        )
        assert publish["status"] == "running" and publish["attempt"] == 1
        assert list((directory / "reports" / project_id).glob("*.tmp"))

        clock.advance(61)
        resumed = ResearchOrchestrator(reopened)
        assert await resumed.tick()
        assert not await resumed.tick()
        current = reopened.project(project_id)["current_report"]
        assert current["id"] != baseline_report["id"]
        assert current["run_id"] == new_run["id"]
        assert len(reopened.list("report_revision", project_id)) == 2
        assert len(reopened.list("notification", project_id)) == 2
        assert (
            len(
                [
                    report
                    for report in reopened.list("report_revision", project_id)
                    if report["run_id"] == new_run["id"]
                ]
            )
            == 1
        )
        assert reopened.run(new_run["id"])["status"] == "succeeded"
    finally:
        reopened.close()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--crash-worker":
        raise SystemExit("This test runner requires --crash-worker, a data directory and a run ID")
    _child_worker(Path(sys.argv[2]), sys.argv[3])
