"""Verify stopped-worker catch-up in distinct processes using real elapsed time."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from nanomuse.research.clock import iso  # noqa: E402
from nanomuse.research.contracts import ProjectSpec  # noqa: E402
from nanomuse.research.orchestrator import ResearchOrchestrator  # noqa: E402
from nanomuse.research.store import ResearchStore  # noqa: E402

REPOSITORIES = [
    "https://github.com/Anil-matcha/open-dots",
    "https://github.com/nano-muse/nanoMuse",
]


def save(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def runtime_digest() -> str:
    directory = ROOT / "nanomuse/research"
    paths = [Path(__file__)] + sorted(
        p for p in directory.rglob("*") if p.suffix in {".py", ".sql", ".json"}
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).replace("\\", "/").encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def drain(engine: ResearchOrchestrator) -> None:
    for _ in range(100):
        if not await engine.tick():
            return
    raise RuntimeError("Probe worker did not become idle")


async def prepare(args: argparse.Namespace) -> None:
    args.output.mkdir(parents=True, exist_ok=False)
    store = ResearchStore(args.output / "data")  # SystemClock, never FakeClock.
    try:
        projects = []
        engine = ResearchOrchestrator(store)
        for name in ("interval", "daily"):
            project = store.create_project(
                ProjectSpec(
                    title=f"Wall clock probe: {name}",
                    goal="Check unchanged snapshots after a stopped worker resumes.",
                    candidates=REPOSITORIES,
                    mode="fixture",
                )
            )
            store.enqueue(project["id"], request_key="probe-initial")
            await drain(engine)
            if store.runs(project["id"])[0]["status"] != "succeeded":
                raise RuntimeError("Initial fixture report failed")
            projects.append(project["id"])
        now = time.time()
        zone = ZoneInfo(args.timezone)
        local = datetime.fromtimestamp(now, zone)
        if args.overnight:
            hour, minute = map(int, args.daily_time.split(":"))
            daily = (local + timedelta(days=1)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            resume_after = daily.timestamp() + 2
            interval = 60
        else:
            daily = datetime.fromtimestamp((int(now) // 60 + 1) * 60, zone)
            resume_after = now + args.gap_seconds
            interval = 1
        for index, project_id in enumerate(projects):
            with store.transaction():
                project = store.get("project", project_id)
                project["tracking"].update(
                    enabled=True,
                    interval_minutes=interval,
                    daily_time=daily.strftime("%H:%M") if index == 1 else "",
                    timezone=args.timezone,
                    next_check_at=daily.timestamp() if index == 1 else now + interval * 60,
                )
                store._put("project", project)
        state = {
            "schema_version": 1,
            "mode": "overnight_wall_clock" if args.overnight else "short_wall_clock",
            "status": "waiting_for_elapsed_time",
            "prepare_pid": os.getpid(),
            "prepared_at": iso(now),
            "prepared_timestamp": now,
            "prepared_local_date": local.date().isoformat(),
            "resume_after": resume_after,
            "resume_after_iso": iso(resume_after),
            "timezone": args.timezone,
            "interval_minutes": interval,
            "project_ids": projects,
            "due_at": [store.get("project", p)["tracking"]["next_check_at"] for p in projects],
            "report_ids": [store.project(p)["current_report_id"] for p in projects],
            "model_calls": len(store.list("model_call")),
            "notifications": len(store.list("notification")),
            "runtime_sha256": runtime_digest(),
            "inputs": "saved_public_excerpts_and_scripted_MockLLM",
        }
        save(args.output / "prepared.json", state)
        print(f"Worker stopped. Resume no earlier than {state['resume_after_iso']}.", flush=True)
    finally:
        store.close()


async def resume(args: argparse.Namespace) -> None:
    state = json.loads((args.output / "prepared.json").read_text("utf-8"))
    if (args.output / "summary.json").exists():
        raise FileExistsError("This probe already has a result")
    now = time.time()
    if now < state["resume_after"]:
        raise ValueError("The real waiting interval has not elapsed")
    if runtime_digest() != state["runtime_sha256"]:
        raise ValueError("Probe runtime changed during the stopped-worker interval")
    if os.getpid() == state["prepare_pid"]:
        raise ValueError("Preparation and resumption must use distinct processes")
    local = datetime.fromtimestamp(now, ZoneInfo(state["timezone"]))
    crossed_day = local.date().isoformat() != state["prepared_local_date"]
    elapsed = now - state["prepared_timestamp"]
    if state["mode"] == "overnight_wall_clock" and (not crossed_day or elapsed < 6 * 3600):
        raise ValueError("Overnight acceptance requires a date change and six real hours")
    store = ResearchStore(args.output / "data")
    try:
        engine = ResearchOrchestrator(store)
        engine.schedule_due()
        engine.schedule_due()
        scheduled = []
        for index, project_id in enumerate(state["project_ids"]):
            checks = [run for run in store.runs(project_id) if run["kind"] == "check"]
            if len(checks) != 1 or checks[0]["missed_from"] != state["due_at"][index]:
                raise RuntimeError("Missed checks did not merge into one durable run")
            scheduled.append(checks[0]["id"])
        await drain(engine)
        engine.schedule_due()
        for run_id in scheduled:
            if store.run(run_id)["status"] != "succeeded":
                raise RuntimeError("The resumed source check failed")
        for index, project_id in enumerate(state["project_ids"]):
            if store.project(project_id)["current_report_id"] != state["report_ids"][index]:
                raise RuntimeError("Unchanged sources produced an unexpected report")
            if len([r for r in store.runs(project_id) if r["kind"] == "check"]) != 1:
                raise RuntimeError("Repeated scheduling produced another check")
        if len(store.list("model_call")) != state["model_calls"]:
            raise RuntimeError("Unchanged sources made unexpected model calls")
        if len(store.list("notification")) != state["notifications"]:
            raise RuntimeError("Unchanged sources produced an unexpected notification")
        if store.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Probe database integrity check failed")
        save(
            args.output / "summary.json",
            {
                "schema_version": 1,
                "mode": state["mode"],
                "status": "passed",
                "resumed_at": iso(now),
                "resume_pid": os.getpid(),
                "prepare_pid": state["prepare_pid"],
                "elapsed_seconds": elapsed,
                "crossed_local_date": crossed_day,
                "timezone": state["timezone"],
                "merged_runs_per_project": 1,
                "new_model_calls": 0,
                "new_reports": 0,
                "new_notifications": 0,
                "database_integrity": "ok",
                "runtime_sha256": state["runtime_sha256"],
                "inputs": state["inputs"],
                "runs": [store.run(identifier) for identifier in scheduled],
            },
        )
        print(f"{state['mode']} passed after {elapsed:.1f} real seconds.", flush=True)
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "resume", "watch"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overnight", action="store_true")
    parser.add_argument("--gap-seconds", type=int, default=130)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--daily-time", default="09:00")
    args = parser.parse_args()
    if args.gap_seconds < 125:
        parser.error("Use at least 125 real seconds to miss two minute intervals")
    args.output = args.output.expanduser().resolve()
    if args.phase == "watch":
        command = [sys.executable, str(Path(__file__).resolve()), "prepare"] + sys.argv[2:]
        subprocess.run(command, check=True, cwd=ROOT)
        state = json.loads((args.output / "prepared.json").read_text("utf-8"))
        # No ResearchStore or running worker exists in this timer process.
        while (remaining := state["resume_after"] - time.time()) > 0:
            time.sleep(min(30, remaining))
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "resume", "--output", str(args.output)],
            check=True,
            cwd=ROOT,
        )
    else:
        asyncio.run(prepare(args) if args.phase == "prepare" else resume(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
