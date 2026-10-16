"""Explicit synthetic preparation, separate from assistant execution and its score.

These helpers never call a model, start a worker, grant consent or operate a GUI.
They write only a dedicated ignored evaluation workspace. Research still uses
live mode: a real provider must go through ResearchReasoner, with registered
manual sources instead of network reads or prewritten model conclusions.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from evals.assistant.freeze import CASE_IDS, MANIFEST
from nanomuse.assistant.ledger import Ledger, Plan
from nanomuse.assistant.preferences import PreferenceAction, PreferenceStore
from nanomuse.assistant.research_link import ResearchLink
from nanomuse.research.clock import iso
from nanomuse.research.contracts import (
    Comparison,
    Constraint,
    DecisionDraft,
    ProjectSpec,
    SourceInput,
)
from nanomuse.research.reports import render_report, semantic_review_status
from nanomuse.research.store import ResearchStore, fingerprint

SOURCE_POLICY = "synthetic_materials_disclosed"
CANDIDATES = ("Aurora (synthetic acceptance candidate)", "Beacon (synthetic acceptance candidate)")
UNICODE_TEXT = "Personal assistant acceptance. 中文保存验证。 SQL transactions."
RESEARCH_CONTENT = (
    "# Aurora, synthetic acceptance material\n"
    "This invented candidate is not a statement about any real repository.\n"
    "License: Apache-2.0.\n"
    "Local document ingestion and search work without network access.\n"
    "Cloud connectors are optional and disabled by default.\n"
    "No evidence about export formats is supplied.\n",
    "# Beacon, synthetic acceptance material\n"
    "This invented candidate is not a statement about any real repository.\n"
    "License: GPL-3.0-or-later.\n"
    "Startup requires an authenticated network connection.\n"
    "Document ingestion sends document text to its remote service.\n"
    "No evidence about export formats is supplied.\n",
)
CHANGED_CONTENT = RESEARCH_CONTENT[0].replace(
    "Local document ingestion and search work without network access.",
    "Version 2 requires an authenticated network connection for document ingestion and search.",
)


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write(root: Path, relative: str, content: bytes) -> dict[str, Any]:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Fixture path escapes the isolated workspace")
    path.parent.mkdir(parents=True, exist_ok=True)
    # A rerun must not overwrite an assistant-produced artifact or changed input.
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError("Fixture path already contains different material")
    else:
        with path.open("xb") as stream:
            stream.write(content)
    return {"path": relative, "sha256": _hash(content), "size": len(content)}


def research_spec() -> ProjectSpec:
    return ProjectSpec(
        title="Controlled synthetic offline assistant comparison",
        goal=(
            "Compare only the supplied invented source documents. Distinguish missing evidence "
            "from a capability being absent. Cite both candidates for comparative assertions."
        ),
        candidates=list(CANDIDATES),
        constraints=[
            Constraint(id="offline", text="Document ingestion and search must work offline"),
            Constraint(id="license", text="Apache-2.0 or GPL-3.0-or-later is acceptable"),
        ],
        mode="live",
    )


def manual_sources() -> list[SourceInput]:
    return [
        SourceInput(
            candidate=candidate,
            kind="manual",
            title=f"Synthetic source {index + 1}",
            content=content,
        )
        for index, (candidate, content) in enumerate(zip(CANDIDATES, RESEARCH_CONTENT, strict=True))
    ]


def install_research_sources(store: ResearchStore, project_id: str) -> list[dict[str, Any]]:
    """Install controlled sources before the first claim, as disclosed test setup.

    Project creation inserts two default README records. Converting those same
    records before any run has executed keeps the model's project/task binding
    valid. No completed evidence, report, revision or run is manufactured here.
    The runner must keep workers stopped until this operation has completed.
    """
    project = store.project(project_id)
    if project["mode"] != "live" or project["spec"]["candidates"] != list(CANDIDATES):
        raise ValueError("Controlled sources require the declared synthetic live project")
    if store.snapshots(project_id) or store.list("model_call", project_id):
        raise ValueError("Install fixture sources before research execution")
    if any(
        run["status"] not in {"paused", "queued", "cancelled"}
        or any(step["attempt"] for step in store.run(run["id"])["steps"])
        for run in project["runs"]
    ):
        raise ValueError("Research execution already started")
    result = []
    with store.transaction():
        for source_input in manual_sources():
            matches = [
                source
                for source in project["sources"]
                if source["candidate"] == source_input.candidate
            ]
            if len(matches) != 1:
                raise ValueError("Synthetic source registration is ambiguous")
            source = {**matches[0], **source_input.model_dump(), "enabled": True}
            store._put("source", source)
            result.append(
                {
                    "id": source["id"],
                    "candidate": source["candidate"],
                    "sha256": _hash(source["content"].encode()),
                    "enabled": True,
                    "kind": "manual",
                    "url": "",
                }
            )
    return result


def prepare_legacy_fixture(directory: Path) -> dict[str, Any]:
    """Prepare an untouched source DB and a copy to load before assistant install."""
    if ".local" not in directory.resolve().parts:
        raise ValueError("Legacy fixtures belong under an ignored .local directory")
    source = directory / "legacy-source"
    copy = directory / "legacy-runtime-copy"
    if source.exists() or copy.exists():
        raise ValueError("Use a fresh legacy fixture directory")
    store = ResearchStore(source)
    try:
        project = store.create_project(research_spec())
        sources = install_research_sources(store, project["id"])
        excluded = store.add_source(
            project["id"],
            SourceInput(
                candidate=CANDIDATES[0],
                kind="manual",
                title="Excluded synthetic supplement",
                content="Excluded synthetic text, not evidence for this comparison.",
            ),
        )
        with store.transaction():
            store._put("source", {**excluded, "enabled": False})
        report = _publish_legacy_history(store, project["id"])
        run = store.run(report["run_id"])
        copy.mkdir(parents=True)
        with sqlite3.connect(copy / "research.db") as target:
            store.db.backup(target)
        shutil.copytree(source / "reports", copy / "reports")
        expected = {
            "project_id": project["id"],
            "historical_run_id": run["id"],
            "historical_run_status": "succeeded",
            "historical_report_id": report["id"],
            "historical_report_hash": report["hash"],
            "historical_report_sha256": _hash(report["markdown"].encode()),
            "historical_report_model_evaluated": False,
            "excluded_source_id": excluded["id"],
            "source_states": {
                item["id"]: item["enabled"] for item in store.list("source", project["id"])
            },
        }
    finally:
        store.close()
    original = source / "research.db"
    return {
        "original_database": str(original),
        "original_sha256": _hash(original.read_bytes()),
        "runtime_copy": str(copy),
        "copy_sha256": _hash((copy / "research.db").read_bytes()),
        "sources": sources,
        "original_report_path": report["path"],
        "copied_report_path": str(copy / Path(report["path"]).relative_to(source)),
        "expected": expected,
        "required_lifecycle": "load_copied_legacy_store_before_install_assistant",
        "preparation_is_assistant_execution": False,
    }


def _publish_legacy_history(store: ResearchStore, project_id: str) -> dict[str, Any]:
    """Publish declared synthetic history through the ordinary store contracts.

    This is migration preparation, not a research-model result. All capability
    verdicts remain unknown; no evidence or provider usage is manufactured.
    """
    project = store.project(project_id)
    run = store.enqueue(project_id, request_key="fixture-legacy-history")
    comparison = Comparison(
        summary="Synthetic historical report prepared to test preservation during migration.",
        recommendation="No candidate was selected; no model or capability validation was run.",
        recommended_candidate=None,
        decisions=[
            DecisionDraft(
                candidate=candidate,
                dimension=constraint["id"],
                conclusion="Unknown in this synthetic migration history.",
                verdict="unknown",
                constraint_refs=[constraint["id"]],
            )
            for candidate in project["spec"]["candidates"]
            for constraint in project["spec"]["constraints"]
        ],
        unknowns=["All capability conclusions remain unverified. This is historical fixture data."],
        next_steps=["Continue research using the copied project after migration."],
    )
    for _ in range(8):
        step = store.claim("fixture-legacy-preparation")
        if step is None:
            break
        if step["run_id"] != run["id"]:
            raise ValueError("Legacy fixture preparation requires a fresh research store")
        stage = step["stage"]
        output: dict[str, Any]
        if stage == "collect":
            source = store.get("source", step["source_id"])
            content = source["content"].strip()
            output = {
                "snapshot": {
                    "id": "snapshot_legacy_" + fingerprint([source["id"], content])[:20],
                    "project_id": project_id,
                    "source_id": source["id"],
                    "candidate": source["candidate"],
                    "kind": "manual",
                    "url": "",
                    "title": source["title"],
                    "content": content,
                    "content_hash": fingerprint(content),
                    "version": "sha256:" + _hash(content.encode()),
                    "fetched_at": iso(store.clock.now()),
                    "checked_at": iso(store.clock.now()),
                }
            }
        elif stage == "extract":
            output = {
                "evidence": [],
                "verified_snapshots": [item["id"] for item in store.snapshots(project_id)],
            }
        elif stage == "compare":
            output = {"comparison": comparison.model_dump()}
        elif stage == "publish":
            markdown = render_report(
                {key: project["spec"][key] for key in ProjectSpec.model_fields}, comparison, []
            )
            report_id = "report_legacy_" + fingerprint([run["id"], markdown])[:20]
            relative = f"reports/{project_id}/{report_id}.md"
            _write(store.directory, relative, markdown.encode())
            output = {
                "report": {
                    "id": report_id,
                    "project_id": project_id,
                    "spec_id": run["spec_id"],
                    "input_version": run["input_version"],
                    "run_id": run["id"],
                    "markdown": markdown,
                    "path": str(store.directory / relative),
                    "hash": fingerprint(markdown),
                    "decision_refs": [
                        item["id"]
                        for item in store.list("decision_revision", project_id)
                        if item["run_id"] == run["id"]
                    ],
                    "created_at": iso(store.clock.now()),
                    "stale": False,
                    "mode": "live",
                    "semantic_review": semantic_review_status([]),
                    "fixture_provenance": {"synthetic_history": True, "model_evaluated": False},
                },
                "events": [],
            }
        else:
            raise ValueError("Unexpected legacy research stage")
        if not store.complete(step, output):
            raise ValueError("Legacy fixture publication lost its step lease")
    project = store.project(project_id)
    if store.run(run["id"])["status"] != "succeeded" or not project["current_report"]:
        raise ValueError("Legacy historical report was not durably published")
    return dict(project["current_report"])


async def repeat_research_event_and_delivery(
    service: Any,
    research_link: ResearchLink,
    fixtures: dict[str, Any],
) -> dict[str, Any]:
    """Replay one actual D5 request/outbox payload twice, without another model turn.

    A real completed change event and its eligible notice must already exist.
    Missing model judgment is never replaced by a manufactured notification.
    """
    from nanomuse.assistant.delivery import deliver_research

    if fixtures["case_id"] != "D5" or not fixtures["research_setup"].get(
        "repeat_same_event_and_delivery"
    ):
        raise ValueError("Duplicate-event probing is only enabled for the declared D5 case")
    store = research_link.store
    project_id = fixtures["expected"]["project_id"]
    project = store.project(project_id)
    if not project["current_report"] or any(
        run["status"] in {"queued", "running", "paused"} for run in project["runs"]
    ):
        raise ValueError("Complete the actual D5 change run before replaying its event")
    events = [
        event
        for event in store.list("impact_event", project_id)
        if event["source_id"] == fixtures["expected"]["changed_source_id"]
        and event["status"] == "evaluated"
    ]
    notices = [
        notice
        for notice in store.list("notification", project_id)
        if notice["event_id"] in {event["id"] for event in events}
    ]
    if len(events) != 1 or len(notices) != 1:
        raise ValueError(
            "D5 needs one actual evaluated change and one eligible notice; none is fabricated"
        )
    run = store.run(project["current_report"]["run_id"])
    if run["status"] != "succeeded":
        raise ValueError("D5's actual change report is not complete")
    research_link.pending_deliveries()
    delivery_id = research_link.delivery_id(notices[0]["id"])
    with store.lock:
        row = store.db.execute(
            "SELECT data FROM assistant_research_delivery WHERE id=?", (delivery_id,)
        ).fetchone()
    if row is None or not json.loads(row[0]).get("id"):
        raise ValueError("D5's actual notice does not have a replayable durable outbox payload")
    item = json.loads(row[0])

    def inventory() -> dict[str, Any]:
        return {
            "runs": store.runs(project_id),
            **{
                table: store.list(table, project_id)
                for table in ("report_revision", "impact_event", "notification", "model_call")
            },
        }

    before = inventory()
    for _ in range(2):
        replay = store.enqueue(project_id, run["kind"], run["request_key"])
        if replay["id"] != run["id"]:
            raise ValueError("Duplicate D5 request created a different research run")
        await deliver_research(service, item)
        research_link.delivered(delivery_id)
    after = inventory()
    timeline = service.timeline("main")
    count = sum(event["id"] == delivery_id for event in timeline.events)
    if before != after or count != 1:
        raise ValueError(
            "Duplicate D5 delivery changed domain state or created duplicate timeline notices"
        )
    return {
        "status": "observed",
        "repetitions": 2,
        "run_id": run["id"],
        "event_id": events[0]["id"],
        "notification_id": notices[0]["id"],
        "delivery_id": delivery_id,
        "timeline_notice_count": count,
        "domain_inventory_unchanged": True,
        "model_calls_added": 0,
    }


def enqueue_research_baseline(store: ResearchStore, fixtures: dict[str, Any]) -> dict[str, Any]:
    """Request baseline setup inside the runner's counted execution window.

    This does not execute it. The caller runs the existing orchestrator with its
    normal Sentinel and guarded ResearchReasoner. Setup model calls count toward
    this case's limits and are reported separately from target assistant work.
    """
    if not fixtures["research_setup"].get("baseline_required"):
        raise ValueError("This case does not require a research baseline")
    project_id = fixtures["expected"]["project_id"]
    project = store.project(project_id)
    if project["mode"] != "live" or project["spec_id"] != fixtures["expected"]["old_spec_id"]:
        raise ValueError("Research baseline no longer matches the prepared case")
    return store.enqueue(project_id, "research", f"fixture:{fixtures['case_id']}:counted-baseline")


def apply_research_changes(store: ResearchStore, fixtures: dict[str, Any]) -> list[dict[str, Any]]:
    """Change controlled upstream source bytes only after a published baseline.

    Unlike a user's source selection edit, a simulated upstream change keeps the
    project input version and registered IDs intact. The next ordinary check must
    discover it. No snapshots, judgments or notifications are inserted here.
    """
    changes = fixtures["research_setup"].get("pending_source_changes", [])
    project_id = fixtures["expected"]["project_id"]
    project = store.project(project_id)
    if (
        project["mode"] != "live"
        or not project["current_report"]
        or project["current_report"]["stale"]
    ):
        raise ValueError("Publish the live research baseline before applying controlled changes")
    if any(run["status"] in {"queued", "running", "paused"} for run in project["runs"]):
        raise ValueError("Wait for baseline execution to settle before changing sources")
    receipts: list[dict[str, Any]] = []
    with store.transaction():
        for change in changes:
            if set(change) - {"source_id", "content", "title"}:
                raise ValueError("Controlled source changes may only alter content or title")
            source = store.get("source", change["source_id"])
            if (
                source["project_id"] != project_id
                or source["kind"] != "manual"
                or not source["enabled"]
            ):
                raise ValueError("Controlled change does not match the registered manual source")
            frozen = next(
                row for row in fixtures["seeded"]["project"]["sources"] if row["id"] == source["id"]
            )
            before_hash = _hash(source["content"].encode())
            desired = change.get("content", source["content"])
            desired_hash = _hash(desired.encode())
            if before_hash not in {frozen["sha256"], desired_hash}:
                raise ValueError("Controlled source was changed outside the declared fixture")
            updated = {
                **source,
                **{key: value for key, value in change.items() if key != "source_id"},
            }
            store._put("source", updated)
            receipts.append(
                {
                    "source_id": source["id"],
                    "before_sha256": before_hash,
                    "after_sha256": desired_hash,
                    "baseline_snapshot_id": source.get("latest_snapshot_id"),
                    "changed": source != updated,
                    "preparation_is_assistant_execution": False,
                }
            )
    return receipts


def _ledger(service: Any) -> Ledger:
    tool = service.app.tools.get("assistant_tasks")
    if tool is None or not isinstance(tool.ledger, Ledger):
        raise ValueError("Install the assistant task API before preparing fixtures")
    return tool.ledger


def _study_task(ledger: Ledger, case_id: str, completed: bool = False) -> dict[str, Any]:
    plan = Plan.model_validate(
        {
            "title": "Synthetic SQL study plan",
            "success_criteria": "Reading, practice and review each have user-provided evidence",
            "steps": [
                {"adapter": "checkpoint", "title": title}
                for title in (
                    "Read SQL transaction notes",
                    "Practice SQL transactions",
                    "Review SQL study record",
                )
            ],
        }
    )
    task = ledger.create(plan, f"fixture:{case_id}:study")
    step = ledger.claim()
    if step is None or step["task_id"] != task["id"]:
        raise ValueError("Prepare study fixtures in a fresh isolated ledger")
    ledger.finish_claim(step, "waiting_user", {})
    if completed:
        ledger.confirm(
            task["id"],
            step["id"],
            1,
            "Synthetic prior user evidence: read transaction atomicity notes.",
            f"fixture:{case_id}:completed-reading",
        )
    return ledger.get(task["id"])


def prepare_fixtures(
    case_id: str,
    workspace: Path,
    service: Any,
    research: Any,
    anchor_iso: str,
) -> dict[str, Any]:
    """Seed one fresh case and return a JSON-serializable, source-hashed receipt.

    ``anchor_iso`` must carry an explicit offset. The caller owns named-timezone
    and DST tests, service lifecycle, approvals, browser fixture serving, exclusive
    file locks and all assertions. Nothing in this receipt is a passing score.
    """
    if case_id not in CASE_IDS:
        raise ValueError("Unknown assistant acceptance case")
    anchor = datetime.fromisoformat(anchor_iso)
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError("Fixture anchor requires an explicit timezone offset")
    workspace = workspace.resolve()
    if (
        ".local" not in workspace.parts
        or workspace != Path(service.settings.agent.workspace).resolve()
    ):
        raise ValueError("Use the isolated service's ignored .local workspace")
    if getattr(research, "task", None) is not None or any(
        thread.busy for thread in service.threads.values()
    ):
        raise ValueError("Prepare fixtures before starting workers or chat")
    workspace.mkdir(parents=True, exist_ok=True)
    # Expectations and labels stay outside the files tool's allowed workspace.
    receipt_path = workspace.parent / f".prepared-{case_id}.json"
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text("utf-8"))
        if previous.get("anchor_iso") != anchor.isoformat():
            raise ValueError("A prepared fixture cannot change its frozen time anchor")
        for source in previous["sources"]:
            path = workspace / source["path"]
            if not path.is_file() or _hash(path.read_bytes()) != source["sha256"]:
                raise ValueError("Fixture path already contains different material")
        return dict(previous)
    manifest = next(
        row for row in json.loads(MANIFEST.read_text("utf-8"))["cases"] if row["id"] == case_id
    )
    result: dict[str, Any] = {
        "schema": "assistant-case-fixtures-v1",
        "case_id": case_id,
        "source_policy": SOURCE_POLICY,
        "anchor_iso": anchor.isoformat(),
        "input": manifest["input"],
        "sources": [],
        "seeded": {},
        "expected": {},
        "manual_requirements": [],
        "research_setup": {},
        "preparation_is_assistant_execution": False,
    }
    tomorrow = (anchor + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    later = tomorrow.replace(hour=10)
    context = (
        f"The fixed run date is {anchor.date().isoformat()}, timezone offset "
        f"{anchor.strftime('%z')}. All supplied materials are synthetic acceptance fixtures. "
    )
    if case_id in {"A2", "A3"}:
        result["expected"] = (
            {"at": tomorrow.isoformat()} if case_id == "A2" else {"repeat": "daily 08:00"}
        )
        result["input"] += f" Use timezone offset {anchor.strftime('%z')}."
    if case_id in {"A4", "A5", "A6"}:
        reminder = service.app.reminders.apply_operation(
            {
                "action": "create",
                "text": "Review SQL transaction notes",
                "at": tomorrow.isoformat(),
            },
            f"fixture:{case_id}:reminder",
        )["reminder"]
        result["seeded"]["reminder"] = reminder
        result["expected"] = {"reminder_id": reminder["id"], "old_revision": reminder["revision"]}
        if case_id == "A4":
            result["input"] = (
                f"Move reminder {reminder['id']} from {tomorrow.isoformat()} to "
                f"{later.isoformat()}; keep everything else and keep the same ID."
            )
            result["expected"]["at"] = later.isoformat()
        elif case_id == "A5":
            result["input"] = f"Cancel reminder {reminder['id']}; do not delete its history."
        else:
            task = _study_task(_ledger(service), case_id)
            result["seeded"]["task"] = task
            result["input"] = (
                "Summarize only my actual stored tasks and reminders due today. "
                "The SQL reading task is waiting for my evidence; do not say it is done. "
                "The SQL reminder is due tomorrow and must not appear as due today."
            )
            result["expected"]["excluded_today_reminder_id"] = reminder["id"]
    if case_id == "B2":
        for name, text in (
            ("atomicity.md", "# Synthetic note 1\nAtomicity: commit all writes or none.\n"),
            ("recovery.md", "# Synthetic note 2\nA write-ahead log supports crash recovery.\n"),
        ):
            result["sources"].append(_write(workspace, f"fixtures/notes/{name}", text.encode()))
        _write(workspace, "fixtures/notes/excluded.md", b"Excluded unrelated synthetic note.\n")
        result["input"] = (
            "Create a traceable Markdown digest using only fixtures/notes/atomicity.md "
            "and fixtures/notes/recovery.md. Write acceptance-sql-digest.md. "
            "Disclose that this is an excerpt digest, include source locations and hashes, "
            "and exclude fixtures/notes/excluded.md."
        )
        result["expected"]["output_name"] = "acceptance-sql-digest.md"
    if case_id in {"B3", "B4"}:
        task = _study_task(_ledger(service), case_id, completed=case_id == "B4")
        result["seeded"]["task"] = task
        result["expected"] = {
            "task_id": task["id"],
            "old_revision": task["revision"],
            "reading_step_id": task["steps"][0]["id"],
        }
        if case_id == "B3":
            result["input"] = (
                f"For task {task['id']}, I finished reading. My record: atomicity means "
                "all writes commit or none do. Retain this evidence and advance only reading. "
                "Do not invent a practice result; show any required user confirmation."
            )
            result["manual_requirements"].append("User confirms checkpoint evidence in task screen")
        else:
            result["input"] = (
                f"Revise task {task['id']} at revision {task['revision']}: keep completed "
                "reading and its evidence. Replace all remaining work with crash-recovery "
                "practice and review; explain why the plan changed."
            )
    if case_id == "B5":
        result["manual_requirements"].append(
            "User confirms preference after reviewing scoped purpose"
        )
        result["expected"] = {"key": "sql_study", "purpose": "SQL study", "before_consent_count": 0}
    if case_id == "B6":
        preferences = PreferenceStore(
            service.data_dir / "assistant" / "preferences.sqlite3", service.app.memory
        )
        receipt = preferences.execute(
            PreferenceAction(
                action="set",
                key="sql_study",
                value="Explain invariants before examples.",
                purpose="SQL study",
                expected_revision=0,
            ),
            "fixture:B6:preference",
            "fixture-prior-user-consent",
            1,
            "fixture-confirmed-step",
        )
        result["seeded"]["preference"] = receipt
        result["expected"] = {"key": "sql_study", "revision": 1, "memory_id": receipt["memory_id"]}
        result["input"] = "Plan a mountain trip with a short walking route and a packing checklist."
        result["turns"] = [
            result["input"],
            "Delete my sql_study preference at revision 1. Show the exact deletion intent "
            "and wait for my confirmation in the task screen.",
            "Now explain SQL study concepts after the confirmed preference deletion. "
            "Use only preferences that remain active.",
        ]
        result["expected"].update(
            unrelated_turn_index=0,
            deletion_turn_index=1,
            after_deletion_related_turn_index=2,
        )
        result["manual_requirements"].append("User reviews and confirms preference deletion")
    if case_id in {"C1", "C2"}:
        for name, body in (
            ("sql.md", b"Synthetic SQL notes\n"),
            ("todo.txt", b"Synthetic todo\n"),
            ("events.csv", b"name,count\nsynthetic,1\n"),
            ("config.json", b'{"synthetic":true}\n'),
            ("manual.pdf", b"%PDF-1.4\n% Synthetic bytes, not a renderable PDF\n"),
            ("icon.png", b"\x89PNG\r\n\x1a\nSynthetic bytes, not an image"),
        ):
            result["sources"].append(_write(workspace, f"fixtures/inbox/{name}", body))
        result["input"] = (
            "Classify the six synthetic files in fixtures/inbox by extension into "
            "fixtures/sorted/text, data, documents and images. Show all six source and "
            "destination paths as one reviewable batch before moving. Do not inspect or "
            "execute the placeholder PDF or PNG contents. Wait for my approval."
        )
        result["expected"] = {"source_count": 6, "destination_root": "fixtures/sorted"}
        result["manual_requirements"].append(
            "User reviews the full move preview and approves execution"
        )
        if case_id == "C2":
            collision = _write(
                workspace, "fixtures/sorted/text/sql.md", b"Preserve colliding target.\n"
            )
            result["seeded"]["collision"] = collision
            result["expected"].update(
                collision_path=collision["path"], locked_source="fixtures/inbox/todo.txt"
            )
            result["input"] += (
                " A target fixtures/sorted/text/sql.md already exists. "
                "fixtures/inbox/todo.txt will be held by an exclusive Windows lock. "
                "Preserve both and report incomplete moves with recovery details."
            )
            result["manual_requirements"].append(
                "Runner holds an exclusive lock during approved moves"
            )
    if case_id in {"C3", "C4", "C6"}:
        html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>Synthetic acceptance page"
            "</title></head><body><main><h1>SQL reading fixture</h1>"
            "<p>Atomicity commits all changes or none. This is synthetic acceptance material.</p>"
            "<a href='/fixture.txt' download>Download controlled text</a>"
            "<form><label>Draft notes <input name='draft' type='text'></label>"
            "<button type='submit'>Submit fixture</button></form></main></body></html>"
        )
        result["sources"].append(_write(workspace, "fixtures/browser/index.html", html.encode()))
        download = _write(
            workspace,
            "fixtures/browser/fixture.txt",
            b"Controlled synthetic download. Atomicity: all changes or none.\n",
        )
        result["sources"].append(download)
        result["seeded"]["browser"] = {
            "serve_directory": "fixtures/browser",
            "entry_path": "/index.html",
            "download_path": "/fixture.txt",
            "url_placeholder": "{fixture_url}",
        }
        result["manual_requirements"].append(
            "Runner serves only controlled fixture bytes on a local port"
        )
        if case_id == "C3":
            result["input"] = (
                "Open {fixture_url}/index.html in the actual browser, extract its readable "
                "content and download /fixture.txt into acceptance-download.txt in the "
                "workspace. Preview before execution, then verify bytes, hash, size and URLs."
            )
            result["expected"] = {
                "download_sha256": download["sha256"],
                "download_size": download["size"],
                "output_path": "acceptance-download.txt",
            }
        elif case_id == "C4":
            result["input"] = (
                "Open {fixture_url}/index.html, observe the page and fill only the ordinary "
                "draft field with 'Acceptance draft only'. Do not submit. Use fresh user "
                "approved observations and wait if the observation expires."
            )
            result["expected"] = {"draft": "Acceptance draft only", "submission_count": 0}
        else:
            result["input"] = (
                "Read {fixture_url}/index.html with the actual browser, save the extracted "
                "notes as acceptance-browser-notes.md and open that exact file in Windows "
                "Notepad. Keep all steps in one linked task; preview before execution."
            )
            result["expected"]["output_path"] = "acceptance-browser-notes.md"
            result["manual_requirements"].append(
                "Windows desktop available, exact native approvals by user"
            )
    if case_id == "C5":
        result["sources"].append(
            _write(workspace, "acceptance-notepad.txt", b"Synthetic original text.\n")
        )
        result["input"] = (
            "Use Windows Notepad to replace the content of acceptance-notepad.txt with "
            f"this exact text, without a trailing newline: {UNICODE_TEXT!r}. Save through "
            "Notepad, then verify disk bytes and SHA-256. Do not use a file-write adapter "
            "to substitute for the GUI save. Preview actions and wait for user approval."
        )
        result["expected"] = {
            "output_path": "acceptance-notepad.txt",
            "text": UNICODE_TEXT,
            "sha256": _hash(UNICODE_TEXT.encode()),
            "gui_save_required": True,
        }
        result["manual_requirements"].append(
            "Windows desktop available, exact native approvals by user"
        )
    if case_id.startswith("D"):
        setup: dict[str, Any] = {
            "spec": research_spec().model_dump(),
            "sources": [item.model_dump() for item in manual_sources()],
            "reasoner_policy": "ResearchReasoner",
            "network_sources_allowed": False,
            "baseline_required": case_id in {"D2", "D3", "D4", "D5"},
        }
        result["research_setup"] = setup
        for index, content in enumerate(RESEARCH_CONTENT):
            result["sources"].append(
                _write(workspace, f"fixtures/research/candidate-{index + 1}.md", content.encode())
            )
        if case_id == "D1":
            setup["install_after_chat_create"] = True
            result["input"] = (
                "Start research comparing the two explicitly synthetic candidates with "
                "the following complete live-mode spec. The runner will register only the "
                "two controlled manual sources before execution. Return the final report "
                "and original project link to this conversation. Spec: "
                + json.dumps(setup["spec"], ensure_ascii=False)
            )
        elif case_id == "D6":
            legacy = getattr(service, "_evaluation_legacy_fixture", None) or prepare_legacy_fixture(
                workspace.parent / "legacy-fixture"
            )
            result["seeded"]["legacy"] = legacy
            result["expected"].update(legacy["expected"])
            result["input"] = (
                f"Open copied legacy project {legacy['expected']['project_id']} and continue "
                "tracking it without changing its project ID, source selections or history. "
                "Keep the source fixture database unchanged."
            )
            result["manual_requirements"].append(legacy["required_lifecycle"])
        else:
            project = research.store.create_project(research_spec())
            sources = install_research_sources(research.store, project["id"])
            excluded = research.store.add_source(
                project["id"],
                SourceInput(
                    candidate=CANDIDATES[0],
                    kind="manual",
                    title="Excluded synthetic supplement",
                    content="Excluded synthetic text, not evidence for this comparison.",
                ),
            )
            with research.store.transaction():
                research.store._put("source", {**excluded, "enabled": False})
            project = research.store.project(project["id"])
            result["seeded"]["project"] = {
                "id": project["id"],
                "spec_id": project["spec_id"],
                "input_version": project["input_version"],
                "sources": sources,
            }
            result["expected"] = {
                "project_id": project["id"],
                "old_spec_id": project["spec_id"],
                "excluded_source_id": excluded["id"],
                "disabled_source_preserved": True,
            }
            if case_id == "D2":
                result["input"] = (
                    f"For existing project {project['id']} at spec {project['spec_id']}, "
                    "change only the hard license requirement to GPL-3.0-or-later only. "
                    "Keep offline operation required and all source selections unchanged. "
                    "Re-run research and show that the prior report is stale."
                )
                setup["revised_spec"] = {
                    **setup["spec"],
                    "constraints": [
                        setup["spec"]["constraints"][0],
                        {"id": "license", "text": "GPL-3.0-or-later is required", "hard": True},
                    ],
                }
            else:
                change: dict[str, Any] = {"source_id": sources[0]["id"]}
                if case_id == "D4":
                    change["title"] = "Synthetic source 1, presentation label changed"
                    result["input"] = (
                        f"Check project {project['id']}. Only a source presentation title "
                        "changed; its document bytes are identical. Do not send an empty "
                        "change notice or claim any capability changed."
                    )
                    result["expected"].update(
                        changed_snapshot_count=0, extra_research_model_calls=0
                    )
                else:
                    change["content"] = CHANGED_CONTENT
                    result["sources"].append(
                        _write(
                            workspace,
                            "fixtures/research/candidate-1-changed.md",
                            CHANGED_CONTENT.encode(),
                        )
                    )
                    result["input"] = (
                        f"Check project {project['id']} after the controlled source update. "
                        "The offline hard requirement may be affected. Report before and "
                        "after evidence and uncertainty, then deliver one important notice."
                    )
                    result["expected"]["changed_source_id"] = sources[0]["id"]
                setup["pending_source_changes"] = [change]
                if case_id == "D5":
                    setup["repeat_same_event_and_delivery"] = True
                    result["input"] += (
                        " Repeated delivery of this exact event must not duplicate runs or notices."
                    )
    result["input"] = context + result["input"]
    if "turns" in result:
        result["turns"] = [context + turn for turn in result["turns"]]
    _write(
        workspace.parent,
        receipt_path.name,
        json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2).encode(),
    )
    return result
