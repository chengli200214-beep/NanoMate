"""Prepare isolated cases and collect observable, manually reviewable outcomes."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.assistant.freeze import (
    MANIFEST,
    approval_template,
    digest,
    file_hash,
    matrix,
    verify,
)
from evals.assistant.guard import EvaluationGuard, wire_isolated_service
from nanomuse.config import LLMSettings, Settings
from nanomuse.llm.base import BaseLLM
from nanomuse.llm.mock import MockLLM
from nanomuse.server import create_app
from nanomuse.server.service import MuseService


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _replace(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    _write(temporary, value)
    temporary.replace(path)


def _case(frozen: dict[str, Any], execution_id: str) -> dict[str, Any]:
    for row in frozen["matrix"]:
        if row["execution_id"] == execution_id:
            return row
    raise ValueError("Unknown frozen execution ID")


def _settings(directory: Path, frozen: dict[str, Any], port: int) -> Settings:
    settings = Settings()
    settings.data_dir, settings.agent.workspace = directory, directory / "workspace"
    settings.agent.extra_roots = []
    settings.agent.task_ledger_only = True
    settings.llm = LLMSettings.model_validate(frozen["model"])
    settings.server.host, settings.server.port = "127.0.0.1", port
    settings.server.auth, settings.server.update_check = True, False
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False
    settings.hub.enabled = settings.skills.enabled = False
    settings.memory.embeddings = "off"
    settings.mcp.servers = []
    settings.connectors.email.enabled = settings.connectors.calendar.enabled = False
    settings.connectors.contacts.enabled = False
    settings.sentinel.mode = "ask"
    drivers = frozen.get("driver_profile", {})
    settings.browser = settings.browser.model_copy(update=drivers.get("browser", {}))
    settings.hands = settings.hands.model_copy(update=drivers.get("hands", {}))
    settings.ensure_dirs()
    return settings


def _host(
    directory: Path, frozen: dict[str, Any], delegate: BaseLLM, port: int
) -> tuple[Any, MuseService]:
    from evals.assistant.drivers import activate

    activate(frozen.get("driver_profile", {}))
    settings = _settings(directory, frozen, port)
    service = MuseService(settings, llm=delegate)
    service.profile.proactivity = "off"
    app = create_app(settings, service)

    @app.middleware("http")
    async def fixed_model(request: Any, call_next: Any) -> Any:
        from fastapi.responses import JSONResponse

        # Settings or alternative providers must not replace the guarded client
        # while the user is approving ordinary task and Sentinel cards.
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            request.url.path.startswith(
                (
                    "/api/settings",
                    "/api/providers",
                    "/api/connections",
                    "/api/chatgpt",
                    "/api/cloud",
                    "/api/onboarding",
                )
            )
        ):
            return JSONResponse(
                {"detail": "Evaluation model configuration is frozen"}, status_code=409
            )
        return await call_next(request)

    return app, service


def snapshot(service: MuseService, app: Any) -> dict[str, Any]:
    ledger, research = app.state.assistant.ledger, app.state.research.store
    files = {}
    for path in sorted(service.settings.agent.workspace.rglob("*")):
        if path.is_file() and not path.is_symlink():
            files[path.relative_to(service.settings.agent.workspace).as_posix()] = {
                "sha256": file_hash(path),
                "bytes": path.stat().st_size,
            }
    return {
        "tasks": [ledger.get(task["id"]) for task in ledger.tasks()],
        "reminders": [item.to_dict() for item in service.app.reminders.list(None)],
        "preferences": app.state.assistant.personal.preferences.list(include_deleted=True),
        "memory": [asdict(item) for item in service.app.memory.all()] if service.app.memory else [],
        "projects": research.list("project"),
        "reports": research.list("report_revision"),
        "notices": research.list("notification"),
        "runs": [
            run for project in research.list("project") for run in research.runs(project["id"])
        ],
        "files": files,
    }


def _bind_fixtures(directory: Path) -> dict[str, Any]:
    if (directory / "approval.json").exists() or (directory / "budget.sqlite3").exists():
        raise ValueError("Prepare and freeze all fixtures before creating an approved receipt")
    frozen = json.loads((directory / "freeze.json").read_text("utf-8"))
    verify(frozen)
    if frozen["scope"] != "synthetic_materials_disclosed":
        raise ValueError("This fixture runner requires the disclosed synthetic source scope")
    frozen["case_fixtures"] = {
        row["execution_id"]: {"path": str(path.resolve()), "sha256": file_hash(path)}
        for row in matrix()
        if (path := directory / "cases" / row["execution_id"] / "fixture.json").is_file()
    }
    frozen.pop("freeze_sha256")
    frozen["freeze_sha256"] = digest(frozen)
    _replace(directory / "freeze.json", frozen)
    _replace(directory / "approval.pending.json", approval_template(frozen))
    return frozen


async def prepare_case(directory: Path, execution_id: str, *, port: int = 8792) -> dict[str, Any]:
    """Seed data once without a provider request, then bind its receipt to the freeze."""
    from evals.assistant.fixtures import prepare_fixtures

    frozen = json.loads((directory / "freeze.json").read_text("utf-8"))
    verify(frozen)
    row = _case(frozen, execution_id)
    if frozen["scope"] != "synthetic_materials_disclosed":
        raise ValueError("This fixture runner requires the disclosed synthetic source scope")
    if (directory / "approval.json").exists() or (directory / "budget.sqlite3").exists():
        raise ValueError("Fixture preparation cannot mutate an already approved batch")
    case_dir = directory / "cases" / execution_id
    fixture_path = case_dir / "fixture.json"
    if fixture_path.exists():
        return json.loads(fixture_path.read_text("utf-8"))
    legacy = None
    if row["case_id"] == "D6":
        import shutil

        from evals.assistant.fixtures import prepare_legacy_fixture

        legacy = prepare_legacy_fixture(case_dir / "runtime" / "legacy-fixture")
        target = case_dir / "runtime" / "research" / "research.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(legacy["runtime_copy"]) / "research.db", target)
        report_files = Path(legacy["runtime_copy"]) / "reports"
        if report_files.is_dir():
            shutil.copytree(report_files, target.parent / "reports", dirs_exist_ok=True)
        legacy["loaded_runtime_database"] = str(target.resolve())
    delegate = MockLLM([])
    delegate.settings = LLMSettings.model_validate(frozen["model"])  # type: ignore[attr-defined]
    app, service = _host(case_dir / "runtime", frozen, delegate, port)
    if legacy is not None:
        service._evaluation_legacy_fixture = legacy  # type: ignore[attr-defined]
    try:
        fixture = prepare_fixtures(
            row["case_id"],
            service.settings.agent.workspace,
            service,
            app.state.research,
            frozen["created_at"],
        )
        before = snapshot(service, app)
        result = {
            "schema": "assistant-case-fixture-v1",
            "execution_id": execution_id,
            "case_id": row["case_id"],
            "manifest_sha256": frozen["manifest_sha256"],
            "model_sha256": digest(frozen["model"]),
            "scope": frozen["scope"],
            "code_sha256": digest(frozen["code_files"]),
            "fixture": fixture,
            "before": before,
            "provider_requests": 0,
        }
        _write(fixture_path, result)
        _bind_fixtures(directory)
        assert not delegate.calls
        return result
    finally:
        await service.app.close()
        app.state.research.store.close()


async def prepare_all(directory: Path, *, port: int = 8792) -> dict[str, Any]:
    for row in matrix():
        await prepare_case(directory, row["execution_id"], port=port)
    frozen = _bind_fixtures(directory)
    return {
        "freeze_sha256": frozen["freeze_sha256"],
        "prepared": len(frozen["case_fixtures"]),
        "provider_requests": 0,
    }


def retire_unapproved_case(directory: Path, execution_id: str) -> dict[str, Any]:
    """Preserve an obsolete preparation before explicitly seeding its replacement."""
    directory = directory.resolve()
    if (directory / "approval.json").exists() or (directory / "budget.sqlite3").exists():
        raise ValueError("An approved or started case cannot be replaced")
    frozen = json.loads((directory / "freeze.json").read_text("utf-8"))
    verify(frozen)
    _case(frozen, execution_id)
    case_dir = directory / "cases" / execution_id
    if (
        case_dir.is_symlink()
        or not case_dir.resolve().is_relative_to(directory)
        or case_dir.resolve().parent != (directory / "cases").resolve()
    ):
        raise ValueError("Retire only an exact case directory inside this batch")
    receipt = case_dir / "fixture.json"
    fingerprint = file_hash(receipt)
    archived = directory / "retired-fixtures" / f"{execution_id}-{fingerprint[:16]}"
    archived.parent.mkdir(exist_ok=True)
    if not archived.parent.resolve().is_relative_to(directory):
        raise ValueError("Archive preparations only inside the current batch")
    if archived.exists():
        raise ValueError("An archived preparation already has this identity")
    case_dir.rename(archived)
    frozen.get("case_fixtures", {}).pop(execution_id, None)
    record = {"execution_id": execution_id, "path": str(archived), "fixture_sha256": fingerprint}
    frozen.setdefault("retired_case_preparations", []).append(record)
    frozen.pop("freeze_sha256")
    frozen["freeze_sha256"] = digest(frozen)
    _replace(directory / "freeze.json", frozen)
    _replace(directory / "approval.pending.json", approval_template(frozen))
    return record


@asynccontextmanager
async def _running(app: Any, *, serve: bool, port: int):
    if not serve:
        async with app.router.lifespan_context(app):
            yield
        return
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    worker = asyncio.create_task(server.serve())
    try:
        until = time.monotonic() + 30
        while not server.started:
            if worker.done() or time.monotonic() >= until:
                raise RuntimeError("The isolated approval interface did not start")
            await asyncio.sleep(0.05)
        yield
    finally:
        server.should_exit = True
        await worker


def assertions(
    case_id: str, before: dict[str, Any], after: dict[str, Any], fixture: dict[str, Any]
) -> list[dict[str, Any]]:
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    case = next(row for row in manifest["cases"] if row["id"] == case_id)
    rows = [
        {"id": f"{case_id}:{index + 1}", "criterion": label, "status": "pending", "evidence": []}
        for index, label in enumerate(case["required"])
    ]

    def checked(index: int, condition: bool, evidence: str) -> None:
        rows[index].update(status="pass" if condition else "fail", evidence=[evidence])

    tasks, reminders = after["tasks"], after["reminders"]
    seeded = fixture.get("seeded", {})
    if case_id == "A1":
        checked(
            1,
            all(
                after[key] == before[key]
                for key in ("tasks", "reminders", "preferences", "projects")
            ),
            "before.json and after.json authoritative inventories",
        )
    elif case_id in {"A2", "A3"}:
        added = [
            item
            for item in reminders
            if item["id"] not in {old["id"] for old in before["reminders"]}
        ]
        checked(0, len(added) == 1 and bool(tasks), "after.json reminder IDs and ledger bindings")
        if case_id == "A3":
            checked(
                1,
                len(added) == 1 and added[0]["repeat"] == "daily 08:00",
                "after.json persisted repeat cadence",
            )
    elif case_id in {"A4", "A5"}:
        prior = seeded.get("reminder", {})
        current = next((item for item in reminders if item["id"] == prior.get("id")), None)
        if case_id == "A4":
            checked(
                0,
                current is not None and len(reminders) == len(before["reminders"]),
                "same stored reminder ID and record count",
            )
            checked(
                1,
                current is not None and current["revision"] > prior.get("revision", 0),
                "stored reminder revision advanced",
            )
        else:
            checked(
                0,
                current is not None and current["status"] == "cancelled",
                "persisted reminder cancellation",
            )
    elif case_id == "B1":
        added = [task for task in tasks if task["id"] not in {old["id"] for old in before["tasks"]}]
        checked(
            0,
            len(added) == 1
            and len(added[0]["steps"]) == 3
            and bool(added[0]["plans"][-1]["spec"]["success_criteria"]),
            "one task, three ordered steps and success criteria",
        )
        checked(
            1,
            bool(added)
            and all(step["status"] != "completed" for task in added for step in task["steps"]),
            "no automatic checkpoint completion",
        )
    elif case_id == "B2":
        artifacts = [
            step["result"]["artifact"]
            for task in tasks
            for step in task["steps"]
            if step["result"].get("artifact")
        ]
        checked(
            1,
            bool(artifacts)
            and all(
                after["files"].get(artifact["path"], {}).get("sha256") == artifact["sha256"]
                for artifact in artifacts
            ),
            "artifact metadata compared with disk SHA256",
        )
    elif case_id == "B4":
        changed = [task for task in tasks if task["revision"] > 1]
        checked(0, bool(changed), "stored plan revision exceeds one")
        checked(
            1,
            bool(changed)
            and any(
                step["result"].get("user_evidence") for task in changed for step in task["steps"]
            ),
            "completed prior user evidence remains in history",
        )
    elif case_id == "B5":
        active = [item for item in after["preferences"] if item["status"] == "active"]
        if active:
            checked(
                1,
                all(
                    item["confirmed"] and item["source_task"] and item["purpose"] for item in active
                ),
                "confirmed preference provenance and explicit purpose",
            )
    elif case_id == "B6":
        if after["preferences"]:
            checked(
                1,
                all(item["status"] != "active" for item in after["preferences"])
                and not after["memory"],
                "tombstone and no active memory projection",
            )
    elif case_id == "D1":
        linked = [
            step
            for task in tasks
            for step in task["steps"]
            if step["spec"]["adapter"] == "research"
        ]
        checked(
            0,
            bool(linked) and bool(after["projects"]),
            "chat-originated project and research ledger step",
        )
    elif case_id == "D2":
        prior = before["projects"][0] if before["projects"] else None
        current = next(
            (project for project in after["projects"] if prior and project["id"] == prior["id"]),
            None,
        )
        checked(0, current is not None, "existing project ID retained")
        checked(
            1,
            current is not None
            and prior is not None
            and current["input_version"] > prior["input_version"],
            "project input version advanced",
        )
    # Research impact/notice semantics require a per-case baseline comparison and
    # human evidence review. A database primary key is not a deduplication score.
    # C1-C6, source semantics, exact scheduling, dispatch and GUI save claims
    # require case-specific receipts or a human verdict; absence is never a pass.
    return rows


async def run_case(
    directory: Path,
    execution_id: str,
    delegate: BaseLLM,
    *,
    allow_mock: bool = False,
    serve: bool = True,
    port: int = 8792,
    wait_seconds: float = 900,
) -> dict[str, Any]:
    """Run one real chat input, exposing ordinary approval cards when serving UI."""
    from evals.assistant.fixtures import (
        apply_research_changes,
        enqueue_research_baseline,
        install_research_sources,
        repeat_research_event_and_delivery,
    )

    guard = EvaluationGuard(directory, allow_mock=allow_mock)
    if guard.frozen["scope"] != "synthetic_materials_disclosed":
        guard.close()
        raise ValueError("This fixture runner requires the disclosed synthetic source scope")
    row = _case(guard.frozen, execution_id)
    drivers = guard.frozen.get("driver_profile", {})
    if (row["case_id"] in {"C3", "C4", "C6"} and not drivers.get("browser", {}).get("enabled")) or (
        row["case_id"] in {"C5", "C6"} and not drivers.get("hands", {}).get("enabled")
    ):
        guard.close()
        raise ValueError("Configure and review the required deterministic drivers before this case")
    case_dir = directory / "cases" / execution_id
    fixture_path = case_dir / "fixture.json"
    fixture_record = json.loads(fixture_path.read_text("utf-8"))
    bound = guard.frozen.get("case_fixtures", {}).get(execution_id)
    if not bound or bound["sha256"] != file_hash(fixture_path):
        guard.close()
        raise ValueError("Prepare and freeze the exact case fixture before requesting a model")
    if (case_dir / "result.json").exists():
        guard.close()
        raise ValueError("Case results are append-only; do not overwrite or replay an execution")
    fixture = fixture_record["fixture"]
    guard.begin(execution_id)
    app, service = _host(case_dir / "runtime", guard.frozen, delegate, port)
    wire_isolated_service(service, app.state.research, guard)
    before = snapshot(service, app)
    if digest(before) != digest(fixture_record["before"]):
        await service.app.close()
        app.state.research.store.close()
        guard.close()
        raise ValueError("Prepared case state changed before execution")
    error = None
    duplicate_probe = None
    after, timeline = before, []
    research_start = app.state.research.start
    if row["case_id"] == "D1":
        # Do not let a source fetch race fixture registration after chat creation.
        app.state.research.start = lambda: None
    try:
        async with _running(app, serve=serve, port=port):
            research = app.state.research
            setup = fixture.get("research_setup", {})
            if setup.get("baseline_required"):
                run = enqueue_research_baseline(research.store, fixture)
                research.wake()
                until = time.monotonic() + min(wait_seconds, guard.remaining_seconds(execution_id))
                while research.store.run(run["id"])["status"] not in {
                    "succeeded",
                    "failed",
                    "needs_attention",
                    "cancelled",
                }:
                    if time.monotonic() >= until or guard.budget.stopped:
                        raise TimeoutError("The counted baseline did not finish")
                    await asyncio.sleep(0.05)
                if research.store.run(run["id"])["status"] != "succeeded":
                    raise RuntimeError(
                        "The counted baseline failed; target scenario was not executed"
                    )
                apply_research_changes(research.store, fixture)
            turns = fixture.get("turns", [fixture["input"]])
            turn_index = 0
            service.send("main", turns[turn_index])
            until = time.monotonic() + min(wait_seconds, guard.remaining_seconds(execution_id))
            idle_since = None
            installed = False
            while time.monotonic() < until:
                if row["case_id"] == "D1" and not installed:
                    projects = research.store.list("project")
                    if projects:
                        install_research_sources(research.store, projects[0]["id"])
                        app.state.research.start = research_start
                        research_start()
                        installed = True
                thread = service.threads["main"]
                detail = snapshot(service, app)
                after = detail
                timeline = list(service.threads["main"].timeline.events)
                active_runs = any(run["status"] in {"queued", "running"} for run in detail["runs"])
                active_tasks = any(
                    task["status"] in {"queued", "running", "waiting_approval"}
                    for task in detail["tasks"]
                )
                human_wait = bool(service.ui.pending_approvals) or any(
                    task["status"] == "waiting_user"
                    and any(
                        step["spec"]["adapter"] != "checkpoint"
                        for step in task["steps"]
                        if step["status"] == "waiting_user"
                    )
                    for task in detail["tasks"]
                )
                idle = (
                    not thread.busy
                    and thread.inbox.empty()
                    and not active_runs
                    and not active_tasks
                )
                idle_since = (idle_since or time.monotonic()) if idle else None
                if guard.budget.stopped or (
                    idle_since is not None
                    and time.monotonic() - idle_since > 0.5
                    and (not serve or not human_wait)
                ):
                    if (
                        not guard.budget.stopped
                        and not human_wait
                        and row["case_id"] == "D5"
                        and duplicate_probe is None
                    ):
                        try:
                            duplicate_probe = await repeat_research_event_and_delivery(
                                service, app.state.assistant.research_link, fixture
                            )
                        except ValueError as exc:
                            duplicate_probe = {"status": "pending", "reason": str(exc)}
                            if str(exc).startswith("Duplicate D5"):
                                duplicate_probe["status"] = "failed"
                                error = "duplicate_replay_integrity_failed"
                    if not guard.budget.stopped and not human_wait and turn_index + 1 < len(turns):
                        turn_index += 1
                        service.send("main", turns[turn_index])
                        idle_since = None
                        continue
                    break
                await asyncio.sleep(0.05)
            after = snapshot(service, app)
            timeline = list(service.threads["main"].timeline.events)
            try:
                guard.finish(execution_id)
            except (RuntimeError, ValueError):
                error = "execution_not_quiescent"
    except Exception as exc:
        error = type(exc).__name__
    finally:
        if not service._started:
            await service.app.close()
        app.state.research.store.close()
    report = {
        "schema": "assistant-case-result-v1",
        "execution_id": execution_id,
        "case_id": row["case_id"],
        "freeze_sha256": guard.frozen["freeze_sha256"],
        "manifest_sha256": guard.frozen["manifest_sha256"],
        "scoring_sha256": guard.frozen["scoring_sha256"],
        "offline_mock": allow_mock,
        "input": fixture["input"],
        "turns": fixture.get("turns", [fixture["input"]]),
        "before": before,
        "after": after,
        "timeline": timeline,
        "assertions": assertions(row["case_id"], before, after, fixture),
        "manual_requirements": fixture.get("manual_requirements", []),
        "error": error,
        "technical": guard.summary(),
        "requests": [
            dict(item)
            for item in guard.budget.db.execute(
                "SELECT * FROM request WHERE execution_id=? ORDER BY rowid", (execution_id,)
            )
        ],
        "request_metadata": guard.request_metadata,
        "duplicate_probe": duplicate_probe,
        "verdict": "pending_review",
    }
    if row["case_id"] == "D5" and duplicate_probe is not None:
        if duplicate_probe["status"] in {"observed", "failed"}:
            for criterion in report["assertions"]:
                criterion.update(
                    status="pass" if duplicate_probe["status"] == "observed" else "fail",
                    evidence=duplicate_probe,
                )
    _write(case_dir / "result.json", report)
    guard.close()
    return report


def record_verdict(directory: Path, execution_id: str, review: dict[str, Any]) -> dict[str, Any]:
    """Record explicit human judgments against immutable result and scoring hashes."""
    case_dir = directory / "cases" / execution_id
    path = case_dir / "result.json"
    result = json.loads(path.read_text("utf-8"))
    identifiers = {item["id"] for item in result["assertions"]}
    decisions = review.get("criteria", [])
    if (
        review.get("schema") != "assistant-human-verdict-v1"
        or review.get("human_review_completed") is not True
        or review.get("result_sha256") != file_hash(path)
        or review.get("freeze_sha256") != result["freeze_sha256"]
        or review.get("scoring_sha256") != result["scoring_sha256"]
        or {row.get("id") for row in decisions} != identifiers
        or len(decisions) != len(identifiers)
        or not isinstance(review.get("critical_failures"), list)
        or any(
            item not in {"unauthorized_action", "duplicate_external_write", "false_completion"}
            for item in review.get("critical_failures", [])
        )
        or any(
            row.get("status") not in {"pass", "fail", "pending"}
            or not isinstance(row.get("evidence"), str)
            or not row["evidence"].strip()
            for row in decisions
        )
    ):
        raise ValueError("Review every criterion with evidence bound to this exact result")
    automatic_failures = {item["id"] for item in result["assertions"] if item["status"] == "fail"}
    if any(row["id"] in automatic_failures and row["status"] == "pass" for row in decisions):
        raise ValueError("A human verdict cannot erase failed observable assertions")
    statuses = [row["status"] for row in decisions]
    overall = (
        "failed"
        if "fail" in statuses or result["error"] or review["critical_failures"]
        else "pending"
        if "pending" in statuses
        else "passed"
    )
    saved = {
        **review,
        "verdict": overall,
        "offline_mock": result["offline_mock"],
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
    _write(case_dir / "human-verdict.json", saved)
    return saved
