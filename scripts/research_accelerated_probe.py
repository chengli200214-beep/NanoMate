"""Exercise missed-day scheduling in distinct processes without changing the system clock."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from nanomuse.research.clock import FakeClock  # noqa: E402
from nanomuse.research.contracts import ProjectSpec  # noqa: E402
from nanomuse.research.orchestrator import ResearchOrchestrator  # noqa: E402
from nanomuse.research.schedule import next_check  # noqa: E402
from nanomuse.research.store import ResearchStore  # noqa: E402
from scripts.research_wall_clock_probe import drain, runtime_digest  # noqa: E402

CASES = {
    "cross_midnight": ("2026-10-16T15:30:00+00:00", 12 * 3600, "Asia/Shanghai"),
    "week_offline": ("2026-10-16T15:30:00+00:00", 7 * 86400, "Asia/Shanghai"),
    "spring_dst": ("2026-03-08T04:30:00+00:00", 12 * 3600, "America/New_York"),
    "fall_dst": ("2026-11-01T03:30:00+00:00", 12 * 3600, "America/New_York"),
}


def save(path: Path, data: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


async def child(phase: str, directory: Path, case: str) -> None:
    start, gap, timezone = CASES[case]
    timestamp = datetime.fromisoformat(start).timestamp()
    clock = FakeClock(timestamp + (gap if phase == "resume" else 0))
    store = ResearchStore(directory / "data", clock)
    try:
        engine = ResearchOrchestrator(store)
        if phase == "prepare":
            ids = []
            for mode in ("interval", "daily"):
                project = store.create_project(
                    ProjectSpec(
                        title=f"Accelerated recovery: {case} / {mode}",
                        goal="Verify unchanged source catch-up with a controlled clock.",
                        candidates=[
                            "https://github.com/Anil-matcha/open-dots",
                            "https://github.com/nano-muse/nanoMuse",
                        ],
                        mode="fixture",
                    )
                )
                store.enqueue(project["id"])
                await drain(engine)
                assert store.project(project["id"])["current_report_id"]
                with store.transaction():
                    project = store.get("project", project["id"])
                    project["tracking"].update(
                        enabled=True,
                        interval_minutes=60,
                        timezone=timezone,
                        daily_time="09:00" if mode == "daily" else "",
                    )
                    project["tracking"]["next_check_at"] = next_check(
                        timestamp, project["tracking"]
                    )
                    store._put("project", project)
                ids.append(project["id"])
            save(
                directory / "prepared.json",
                {
                    "pid": os.getpid(),
                    "project_ids": ids,
                    "runtime_sha256": runtime_digest(),
                    "calls": len(store.list("model_call")),
                    "reports": len(store.list("report_revision")),
                    "notifications": len(store.list("notification")),
                    "due_at": [store.get("project", p)["tracking"]["next_check_at"] for p in ids],
                },
            )
        else:
            before = json.loads((directory / "prepared.json").read_text("utf-8"))
            assert before["pid"] != os.getpid()
            assert before["runtime_sha256"] == runtime_digest()
            engine.schedule_due()
            engine.schedule_due()
            for index, project_id in enumerate(before["project_ids"]):
                checks = [r for r in store.runs(project_id) if r["kind"] == "check"]
                assert len(checks) == 1
                assert checks[0]["missed_from"] == before["due_at"][index]
                assert checks[0]["missed_to"] == clock.now()
            await drain(engine)
            engine.schedule_due()
            for project_id in before["project_ids"]:
                checks = [r for r in store.runs(project_id) if r["kind"] == "check"]
                assert len(checks) == 1 and checks[0]["status"] == "succeeded"
                assert store.get("project", project_id)["tracking"]["next_check_at"] > clock.now()
            for key, table in (
                ("calls", "model_call"),
                ("reports", "report_revision"),
                ("notifications", "notification"),
            ):
                assert len(store.list(table)) == before[key]
            assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            save(
                directory / "summary.json",
                {
                    "case": case,
                    "status": "passed",
                    "clock": "controlled_not_system",
                    "simulated_gap_seconds": gap,
                    "timezone": timezone,
                    "prepare_pid": before["pid"],
                    "resume_pid": os.getpid(),
                    "new_model_calls": 0,
                    "new_reports": 0,
                    "new_notifications": 0,
                    "merged_checks_per_project": 1,
                    "runtime_sha256": runtime_digest(),
                    "real_overnight_verified": False,
                },
            )
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("prepare", "resume"))
    parser.add_argument("--case", choices=tuple(CASES))
    args = parser.parse_args()
    if args.phase:
        if not args.case:
            parser.error("Child phase requires --case")
        asyncio.run(child(args.phase, args.output, args.case))
        return 0
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summaries = []
    for case in CASES:
        directory = args.output / case
        directory.mkdir()
        for phase in ("prepare", "resume"):
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--output",
                    str(directory),
                    "--phase",
                    phase,
                    "--case",
                    case,
                ],
                cwd=ROOT,
                check=True,
                timeout=60,
            )
        summaries.append(json.loads((directory / "summary.json").read_text("utf-8")))
    save(
        args.output / "summary.json",
        {
            "status": "passed",
            "mode": "accelerated_process_recovery",
            "cases": summaries,
            "real_elapsed_seconds": time.monotonic() - started,
            "real_overnight_verified": False,
            "limits": "Tests application scheduling and persisted recovery, not OS sleep, network longevity or physical overnight elapsed time.",
        },
    )
    print(f"Passed {len(summaries)} controlled-clock recovery cases in distinct processes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
