"""Execute frozen synthetic scenarios and retain artifacts without inferring quality."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import time
import uuid
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanomuse.research.contracts import FetchedSource, ImpactJudgment, ProjectSpec, SourceInput

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "manifest.json"
VARIANTS = ("baseline", "full", "no_version_invalidation", "no_impact_filter")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Results are append-only by run directory. A rerun must choose another directory.
    if path.exists():
        raise FileExistsError(f"Refusing to replace evaluation artifact: {path.name}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text("utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("dataset_origin") != "synthetic":
        raise ValueError("Expected the version 1 synthetic evaluation manifest")
    scenarios = manifest.get("scenarios", [])
    ids = [item["id"] for item in scenarios]
    if len(set(ids)) != 12 or len(ids) != 12:
        raise ValueError("The frozen matrix must contain 12 distinct scenarios")
    if Counter(item["group"] for item in scenarios) != {
        "initial": 4,
        "constraint_change": 4,
        "source_update": 4,
    }:
        raise ValueError("Expected four scenarios in each of the three groups")
    for scenario in scenarios:
        if scenario["initial_spec"] not in manifest["specs"]:
            raise ValueError("Unknown initial specification")
        for field in ("initial_sources", "target_sources"):
            if field in scenario and scenario[field] not in manifest["source_sets"]:
                raise ValueError("Unknown source set")
        annotation = scenario["annotations"]
        if (
            not {
                "must_identify",
                "allowed_selection",
                "invalidated_constraints",
                "notify",
                "rationale",
            }
            <= annotation.keys()
        ):
            raise ValueError("A scenario is missing preregistered annotations")
    return manifest


def build_matrix(
    manifest: dict[str, Any],
    *,
    include_reruns: bool = True,
    pilot: bool = False,
    scenario_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    matrix: list[dict[str, Any]] = []
    for variant in VARIANTS:
        for scenario in manifest["scenarios"]:
            if scenario_ids and scenario["id"] not in scenario_ids:
                continue
            if pilot and (variant != "full" or scenario["id"] not in manifest["pilot_runs"]):
                continue
            if variant == "no_version_invalidation" and scenario["group"] != "constraint_change":
                continue
            if variant == "no_impact_filter" and scenario["group"] != "source_update":
                continue
            matrix.append(
                {
                    "scenario_id": scenario["id"],
                    "variant": variant,
                    "phase": "pilot" if pilot else "first_round",
                    "repetition": 0,
                }
            )
    if include_reruns and not pilot:
        for item in manifest["preregistered_reruns"]:
            if scenario_ids and item["scenario_id"] not in scenario_ids:
                continue
            for repeat in range(1, item["repetitions"] + 1):
                matrix.append(
                    {
                        "scenario_id": item["scenario_id"],
                        "variant": item["variant"],
                        "phase": "preregistered_rerun",
                        "repetition": repeat,
                    }
                )
    return [
        {
            "execution_id": f"{index:02d}-{item['scenario_id']}-{item['variant']}-r{item['repetition']}",
            **item,
        }
        for index, item in enumerate(matrix, 1)
    ]


def _git_info(directory: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=directory, check=True, capture_output=True, text=True
        ).stdout.strip()
        changed = subprocess.run(
            ["git", "status", "--porcelain", "--", "nanomuse/research"],
            cwd=directory,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return {"commit": commit, "research_tree_dirty": bool(changed.strip())}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "research_tree_dirty": None}


def freeze_inputs(
    manifest_path: Path,
    output: Path,
    matrix: list[dict[str, Any]],
    *,
    approval: dict[str, Any] | None = None,
    review_files: list[Path] | None = None,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import nanomuse.research

    runtime_dir = Path(nanomuse.research.__file__).resolve().parent
    files = [
        manifest_path,
        HERE.parents[1] / "scripts/evaluate_research.py",
    ]
    files += sorted(HERE.glob("*.py"))
    root = HERE.parents[1]
    files += [root / "nanomuse/llm" / name for name in ("base.py", "factory.py", "openai_chat.py")]
    files += [root / "nanomuse" / name for name in ("schema.py", "config.py")]
    files += review_files or []
    files += sorted(runtime_dir.rglob("*.py")) + sorted(runtime_dir.rglob("*.sql"))
    files += sorted(runtime_dir.rglob("*.json"))
    records = []
    for index, path in enumerate(files):
        destination = output / "frozen-inputs" / f"{index:02d}-{path.name}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
        records.append(
            {
                "input_path": str(path),
                "frozen_path": str(destination.relative_to(output)),
                "sha256": sha256(path),
                "logical_path": str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
                if path.resolve().is_relative_to(root.resolve())
                else f"external/{index:02d}-{path.name}",
            }
        )
    freeze = {
        "schema_version": 1,
        "frozen_at": datetime.now(UTC).isoformat(),
        "manifest_sha256": sha256(manifest_path),
        "files": records,
        "runtime_code": _git_info(runtime_dir.parents[1]),
        "matrix": matrix,
        "dataset_origin": "synthetic",
        "annotation_status": approval["status"] if approval else "agent_draft_pending_human_review",
        "owner_approval": approval,
        "evaluation_configuration": configuration,
        "content_sha256": hashlib.sha256(
            json.dumps(
                {
                    "files": {row["logical_path"]: row["sha256"] for row in records},
                    "matrix": matrix,
                    "configuration": configuration,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    }
    freeze["freeze_sha256"] = hashlib.sha256(
        json.dumps(freeze, sort_keys=True).encode("utf-8")
    ).hexdigest()
    write_json(output / "freeze.json", freeze)
    return freeze


def verify_frozen(freeze: dict[str, Any]) -> None:
    for item in freeze["files"]:
        if sha256(Path(item["input_path"])) != item["sha256"]:
            raise RuntimeError("Frozen evaluation inputs or runtime code changed during execution")


class SyntheticFetcher:
    """Return only manifest snapshots. Even live-model evaluations do not fetch these hosts."""

    def __init__(self, sources: list[dict[str, Any]]) -> None:
        self.set_sources(sources)

    def set_sources(self, sources: list[dict[str, Any]]) -> None:
        self.sources = {item["url"]: item for item in sources}

    async def fetch(
        self, source: dict[str, Any], previous: dict[str, Any] | None = None
    ) -> FetchedSource:
        item = self.sources.get(source["url"])
        if item is None:
            raise ValueError("Synthetic fetcher cannot read an unregistered manifest source")
        unchanged = previous is not None and previous["content"] == item["content"]
        return FetchedSource(
            url=item["url"],
            title="Synthetic fixture",
            content=item["content"],
            version=item["version"],
            not_modified=unchanged,
        )


class EvaluationReasoner:
    """A narrow experimental intervention. Expected annotations never reach the model."""

    def __init__(self, delegate: Any, variant: str) -> None:
        self.delegate = delegate
        self.variant = variant
        self.interventions: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = delegate.calls
        self.previous_context: dict[str, Any] | None = None
        self.comparison_inputs: list[dict[str, Any]] = []

    def _context(self, context: dict[str, Any]) -> dict[str, Any]:
        if self.variant != "no_version_invalidation" or self.previous_context is None:
            return context
        combined = deepcopy(context)
        for name in ("decisions", "evidence", "snapshots"):
            by_id = {row["id"]: row for row in combined[name]}
            for row in self.previous_context[name]:
                if row["id"] not in by_id:
                    by_id[row["id"]] = deepcopy(row)
            combined[name] = list(by_id.values())
        return combined

    def set_call_budget(self, remaining: int) -> None:
        if hasattr(self.delegate, "set_call_budget"):
            self.delegate.set_call_budget(remaining)

    def set_call_observer(self, callback: Any) -> None:
        if hasattr(self.delegate, "set_call_observer"):
            self.delegate.set_call_observer(callback)

    async def extract(self, context: dict[str, Any]) -> Any:
        return await self.delegate.extract(self._context(context))

    async def compare(self, context: dict[str, Any]) -> Any:
        from nanomuse.research.context import build_context

        selected_context = self._context(context)
        selected = build_context(
            "compare", selected_context, getattr(self.delegate, "context_budget_chars", 48_000)
        )
        previous_ids = {row["id"] for row in (self.previous_context or {}).get("decisions", [])}
        self.comparison_inputs.append(
            {
                "decision_ids": [row["id"] for row in selected["decisions"]],
                "historical_decision_ids": [
                    row["id"] for row in selected["decisions"] if row["id"] in previous_ids
                ],
                "evidence_ids": [row["id"] for row in selected["evidence"]],
            }
        )
        return await self.delegate.compare(selected_context)

    async def impact(self, context: dict[str, Any]) -> ImpactJudgment:
        result = await self.delegate.impact(self._context(context))
        if self.variant == "no_impact_filter":
            # The experiment disables the final impact gate, not the model call. The
            # current store notifies at recommendation level; this forced level is an
            # intervention label and is not a claimed model judgment.
            self.interventions.append(
                {
                    "kind": "force_notification",
                    "original_model_judgment": result.model_dump(),
                    "applied_impact_level": "recommendation",
                    "change_ids": [item.get("id") for item in context.get("changes", [])],
                }
            )
            return result.model_copy(
                update={
                    "impact_level": "recommendation",
                    "recommended_action": "Evaluation intervention: notify on every source change.",
                }
            )
        return result


def _spec(manifest: dict[str, Any], name: str, mode: str) -> ProjectSpec:
    data = deepcopy(manifest["specs"][name])
    data.update(
        candidates=list(manifest["candidates"].values()),
        mode="fixture" if mode == "fixture" else "live",
    )
    return ProjectSpec.model_validate(data)


def _sources(manifest: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [
        {
            **manifest["sources"][key],
            "candidate": manifest["candidates"][manifest["sources"][key]["candidate"]],
        }
        for key in manifest["source_sets"][name]
    ]


def _configure_sources(store: Any, project_id: str, sources: list[dict[str, Any]]) -> None:
    by_url = {item["url"]: item for item in store.list("source", project_id)}
    desired = {item["url"] for item in sources}
    with store.transaction():
        for source in by_url.values():
            source["enabled"] = source["url"] in desired
            store._put("source", source)
    for item in sources:
        if item["url"] not in by_url:
            store.add_source(
                project_id,
                SourceInput(
                    candidate=item["candidate"],
                    kind="manual",
                    url=item["url"],
                    content=item["content"],
                    title="Synthetic evaluation source",
                ),
            )


async def _drain(engine: Any, store: Any, run_id: str, clock: Any) -> dict[str, Any]:
    for _ in range(150):
        current = store.run(run_id)
        if current["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
            return current
        if not await engine.tick():
            clock.advance(60)
    raise RuntimeError("Evaluation step limit reached before the run became terminal")


def _usage(calls: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"model_calls": len(calls)}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        values = [item.get(key) for item in calls]
        result[key] = sum(values) if all(isinstance(value, int) for value in values) else None
    result["model_duration_ms"] = sum(float(item.get("duration_ms") or 0) for item in calls)
    result["monetary_cost"] = None
    return result


def _safe_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {str(exc)[:1000]}"
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GH_TOKEN"):
        if value := os.environ.get(name):
            message = message.replace(value, "[redacted]")
    return message


def _observations(
    store: Any,
    project_id: str,
    run: dict[str, Any],
    scenario: dict[str, Any],
    manifest: dict[str, Any],
    old_decisions: list[str],
    notifications_before: int,
) -> dict[str, Any]:
    project = store.project(project_id)
    report = project["current_report"]
    evidence = store.list("evidence", project_id)
    by_id = {item["id"]: item for item in evidence}
    decisions = [
        item
        for item in store.list("decision_revision", project_id)
        if item.get("run_id") == run["id"]
    ]
    cited = {ref for item in decisions for ref in item.get("evidence_refs", [])}
    snapshots = {item["id"]: item for item in store.snapshots(project_id)}
    invalid = [
        ref
        for ref in cited
        if ref not in by_id
        or by_id[ref].get("status") != "active"
        or by_id[ref]["snapshot_id"] not in snapshots
    ]
    facts = []
    for item in scenario["annotations"]["must_identify"]:
        facts.append(
            {
                **item,
                "verbatim_evidence_present": any(
                    evidence_item["candidate"] == manifest["candidates"][item["candidate"]]
                    and evidence_item.get("status") == "active"
                    and item["quote"] in evidence_item.get("quote", "")
                    for evidence_item in evidence
                ),
                "semantic_support_review": "pending",
            }
        )
    old_statuses = {
        identifier: store.get("decision_revision", identifier)["status"]
        for identifier in old_decisions
    }
    new_notifications = store.list("notification", project_id)[notifications_before:]
    event_ids = {item["id"] for item in store.list("impact_event", project_id)}
    return {
        "target_run_status": run["status"],
        "report_present": report is not None,
        "report_sections": report["markdown"].count("\n## ") if report else 0,
        "obsolete_or_unbound_evidence_refs": invalid,
        "stale_reference_misuse_count": len(set(invalid)),
        "stale_fact_semantic_misuse": None,
        "must_identify_observations": facts,
        "old_decision_statuses": old_statuses,
        "old_decisions_invalidated": bool(old_statuses)
        and all(value != "active" for value in old_statuses.values()),
        "new_notifications": len(new_notifications),
        "new_source_change_notifications": sum(
            item.get("event_id") in event_ids for item in new_notifications
        ),
        "registered_conflicts": len(store.list("evidence_conflict", project_id)),
        "allowed_selection_review": "pending",
        "model_quality_score": None,
    }


async def execute_one(
    manifest: dict[str, Any],
    item: dict[str, Any],
    output: Path,
    mode: str,
    settings: Any = None,
    budget: Any = None,
    frozen: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from nanomuse.research.clock import FakeClock
    from nanomuse.research.model import create_research_llm
    from nanomuse.research.orchestrator import ResearchOrchestrator
    from nanomuse.research.reasoning import FixtureReasoner, ResearchReasoner
    from nanomuse.research.store import ResearchStore

    scenario = next(row for row in manifest["scenarios"] if row["id"] == item["scenario_id"])
    directory = output / "executions" / item["execution_id"]
    directory.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {
        **item,
        "execution_mode": "offline_scripted_workflow_verification"
        if mode == "fixture"
        else "live_model_synthetic_inputs",
        "dataset_origin": "synthetic",
        "status": "running",
        "quality_score": None,
        "annotation_status": manifest["annotation_status"],
        "annotations": scenario["annotations"],
        "target_run": None,
        "setup_run": None,
        "replays": [],
        "error": None,
        "prompt_version": manifest["prompt_version"],
        "model_configuration": (
            {
                "provider": settings.llm.provider,
                "model": settings.llm.model,
                "temperature": settings.llm.temperature,
                "max_tokens": settings.llm.max_tokens,
                "tool_mode": settings.llm.tool_mode,
                "transport_retries": settings.llm.max_retries,
            }
            if mode == "live"
            else {
                "provider": "mock",
                "model": "mock",
                "temperature": None,
                "max_tokens": None,
                "tool_mode": "scripted",
            }
        ),
    }
    clock = FakeClock(1_792_022_400.0)
    store = ResearchStore(directory / "data", clock)
    llm = None
    delegate: Any = FixtureReasoner()
    reasoner = EvaluationReasoner(delegate, item["variant"])
    target_sources = _sources(manifest, scenario.get("target_sources", scenario["initial_sources"]))
    target_spec = _spec(manifest, scenario.get("target_spec", scenario["initial_spec"]), mode)
    initial_sources = _sources(manifest, scenario["initial_sources"])
    fetcher = SyntheticFetcher(initial_sources)
    engine = ResearchOrchestrator(store, reasoner=reasoner, fetcher=fetcher)
    project_id: str | None = None
    target_run_id: str | None = None
    started = time.perf_counter()
    target_started = started
    target_calls_start = 0
    try:
        if mode == "live":
            llm = create_research_llm(settings)
            if budget is not None:
                from evals.research.budget import BudgetedLLM

                llm = BudgetedLLM(
                    llm,
                    budget,
                    item.get("budget_execution_id", item["execution_id"]),
                    before_request=(lambda: verify_frozen(frozen)) if frozen else None,
                )
            reasoner = EvaluationReasoner(ResearchReasoner(llm), item["variant"])
            engine.reasoner = reasoner
        needs_setup = scenario["group"] != "initial" and item["variant"] != "baseline"
        project = store.create_project(
            _spec(manifest, scenario["initial_spec"], mode) if needs_setup else target_spec
        )
        project_id = project["id"]
        _configure_sources(store, project_id, initial_sources if needs_setup else target_sources)
        old_decisions: list[str] = []
        if needs_setup:
            setup = store.enqueue(project_id, "research", "evaluation-setup")
            result["setup_run"] = await _drain(engine, store, setup["id"], clock)
            result["setup_usage"] = _usage(reasoner.calls)
            if result["setup_run"]["status"] != "succeeded":
                raise RuntimeError("Initial setup run failed; target workflow was not executed")
            old_decisions = [row["id"] for row in store.list("decision_revision", project_id)]
        target_calls_start = len(reasoner.calls)
        target_started = time.perf_counter()
        notifications_before = len(store.list("notification", project_id))
        fetcher.set_sources(target_sources)
        _configure_sources(store, project_id, target_sources)
        clock.advance(60)
        if needs_setup and scenario["group"] == "constraint_change":
            previous_spec = store.project(project_id)["spec"]
            previous_context = engine._context(
                project_id, {key: previous_spec[key] for key in ProjectSpec.model_fields}
            )
            store.revise(project_id, target_spec)
            target = store.runs(project_id)[0]
            if item["variant"] == "no_version_invalidation":
                # Disable semantic version invalidation at the evaluation boundary.
                # The real execution fence and current requirements stay enabled.
                with store.transaction():
                    for identifier in old_decisions:
                        previous = store.get("decision_revision", identifier)
                        previous["status"] = "active"
                        store._put("decision_revision", previous)
                reasoner.previous_context = previous_context
                result["intervention"] = (
                    "Reused previous-version decisions and evidence in the model context; "
                    "retained current requirements and the execution fence"
                )
        else:
            kind = "check" if needs_setup and scenario["group"] == "source_update" else "research"
            target = store.enqueue(project_id, kind, "evaluation-target")
        target_run_id = target["id"]
        result["target_run"] = await _drain(engine, store, target_run_id, clock)
        result["observations"] = _observations(
            store,
            project_id,
            result["target_run"],
            scenario,
            manifest,
            old_decisions,
            notifications_before,
        )
        result["status"] = result["target_run"]["status"]
        if result["status"] != "succeeded":
            result["error"] = result["target_run"].get("error")
        for index in range(scenario.get("replay_count", 0)):
            before_reports = len(store.list("report_revision", project_id))
            before_notifications = len(store.list("notification", project_id))
            before_calls = len(reasoner.calls)
            replay = store.enqueue(project_id, "check", f"evaluation-replay-{index}")
            completed = await _drain(engine, store, replay["id"], clock)
            result["replays"].append(
                {
                    "run": completed,
                    "additional_reports": len(store.list("report_revision", project_id))
                    - before_reports,
                    "additional_notifications": len(store.list("notification", project_id))
                    - before_notifications,
                    "additional_model_calls": len(reasoner.calls) - before_calls,
                }
            )
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = _safe_error(exc)
        result["error_type"] = getattr(exc, "code", type(exc).__name__)
        if target_run_id:
            result["target_run"] = store.run(target_run_id)
    finally:
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result["target_duration_ms"] = (
            round((time.perf_counter() - target_started) * 1000, 3) if target_run_id else None
        )
        if not target_run_id:
            target_calls_start = len(reasoner.calls)
        result["usage"] = _usage(reasoner.calls[target_calls_start:])
        result["all_calls"] = reasoner.calls
        result["target_calls"] = reasoner.calls[target_calls_start:]
        result["interventions"] = reasoner.interventions
        result["comparison_inputs"] = reasoner.comparison_inputs
        result["usage_scope"] = (
            "Target workflow and all following replay attempts, including failures"
        )
        if project_id:
            write_json(
                directory / "state.json",
                {
                    name: store.list(name, project_id)
                    for name in (
                        "project",
                        "spec_revision",
                        "source",
                        "source_snapshot",
                        "evidence",
                        "evidence_conflict",
                        "decision_revision",
                        "report_revision",
                        "impact_event",
                        "notification",
                        "model_call",
                    )
                },
            )
        write_json(directory / "result.json", result)
        await engine.stop()
        store.close()
        if llm:
            await llm.close()
    return result


def summarize(results: list[dict[str, Any]], mode: str, planned: int) -> dict[str, Any]:
    durations = [
        row["target_duration_ms"] for row in results if row["target_duration_ms"] is not None
    ]
    return {
        "execution_mode": "dry_run"
        if mode == "dry-run"
        else "offline_scripted_workflow_verification"
        if mode == "fixture"
        else "live_model_synthetic_inputs",
        "dataset_origin": "synthetic",
        "planned_executions": planned,
        "executed": len(results),
        "execution_statuses": dict(Counter(row["status"] for row in results)),
        "phase_counts": dict(Counter(row["phase"] for row in results)),
        "usage": _usage([call for row in results for call in row["target_calls"]]),
        "median_target_duration_ms": statistics.median(durations) if durations else None,
        "model_quality_metrics": None,
        "improvement_claim": None,
        "manual_review_status": "pending",
        "interpretation": (
            "Offline results verify scripted workflow execution only. No model-quality, accuracy, "
            "or improvement claim is derived from fixture responses. Live-model runs still use "
            "synthetic inputs. Annotation approval and semantic output review are separate."
        ),
    }


async def run_suite(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_manifest(args.manifest)
    selected = set(args.scenario) if args.scenario else None
    if selected and not selected <= {row["id"] for row in manifest["scenarios"]}:
        raise ValueError("Unknown scenario selection")
    matrix = build_matrix(
        manifest, include_reruns=not args.first_round_only, pilot=args.pilot, scenario_ids=selected
    )
    if args.variant:
        matrix = [row for row in matrix if row["variant"] in args.variant]
    settings = None
    ready = True
    if args.mode == "live":
        from nanomuse.research.model import load_research_settings, research_model_ready

        settings = load_research_settings(args.config, args.data_dir)
        ready = research_model_ready(settings)
        settings = settings.model_copy(deep=True)
        settings.llm.tool_mode = "native"
        settings.llm.max_retries = 0
        settings.llm.vision = "off"
    approval = policy = None
    review_files = []
    if args.review_receipt or args.scoring_rules:
        from evals.research.review import load_approval

        if not args.review_receipt or not args.scoring_rules:
            raise ValueError("Supply both review receipt and scoring rules")
        approval, policy = load_approval(args.review_receipt, args.scoring_rules, args.manifest)
        review_files = [args.review_receipt, args.scoring_rules]
        manifest["annotation_status"] = approval["status"]
    if args.mode == "live" and ready and not approval:
        raise ValueError("A live batch requires approved annotations, scoring and a spending limit")
    if args.budget_ledger and not args.budget_ledger.is_file():
        raise ValueError("A shared batch ledger must already exist")
    configuration = {
        "mode": args.mode,
        "workers": args.workers,
        "budget_policy": policy,
        "shared_budget_ledger": bool(args.budget_ledger),
    }
    if settings is not None:
        configuration["model"] = {
            key: getattr(settings.llm, key)
            for key in (
                "provider",
                "model",
                "temperature",
                "max_tokens",
                "tool_mode",
                "max_retries",
                "stream",
                "vision",
            )
        }
        configuration["extra_body_sha256"] = hashlib.sha256(
            json.dumps(settings.llm.extra_body, sort_keys=True).encode()
        ).hexdigest()
    args.output.mkdir(parents=True, exist_ok=False)
    freeze = freeze_inputs(
        args.manifest,
        args.output,
        matrix,
        approval=approval,
        review_files=review_files,
        configuration=configuration,
    )
    if args.mode == "live":
        if not ready:
            summary = summarize([], args.mode, len(matrix))
            summary["configuration_error"] = (
                "No configured model is ready. No model calls were made."
            )
            write_json(args.output / "summary.json", summary)
            return summary
    results = []
    aborted = None
    budget = None
    if args.mode == "live" and policy:
        from evals.research.budget import BatchBudget

        budget = BatchBudget(args.budget_ledger or args.output / "batch-budget.db", policy)
    budget_before = budget.summary() if budget else None
    if budget_before:
        write_json(args.output / "budget-before.json", budget_before)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    for item in matrix:
        queue.put_nowait(item)

    async def worker() -> None:
        nonlocal aborted
        while not queue.empty() and not aborted and not (budget and budget.stopped):
            item = queue.get_nowait()
            try:
                verify_frozen(freeze)
                row = await execute_one(
                    manifest,
                    {
                        **item,
                        "freeze_sha256": freeze["freeze_sha256"],
                        "budget_execution_id": f"{args.output.name}/{item['execution_id']}",
                    },
                    args.output,
                    args.mode,
                    settings,
                    budget,
                    freeze,
                )
                results.append(row)
                print(
                    f"{item['execution_id']}: {row['status']}, "
                    f"{row['usage']['model_calls']} target model calls",
                    flush=True,
                )
            except Exception as exc:
                aborted = _safe_error(exc)

    if args.mode != "dry-run":
        await asyncio.gather(*(worker() for _ in range(args.workers)))
        try:
            verify_frozen(freeze)
        except Exception as exc:
            aborted = _safe_error(exc)
    results.sort(key=lambda row: row["execution_id"])
    summary = summarize(results, args.mode, len(matrix))
    summary["freeze_sha256"] = freeze["freeze_sha256"]
    summary["annotation_source_status"] = manifest["annotation_status"]
    summary["annotation_status"] = approval["status"] if approval else manifest["annotation_status"]
    summary["owner_approval"] = approval
    summary["workers"] = args.workers
    if budget:
        summary["batch_budget"] = budget.summary()
        summary["budget_before"] = budget_before
        if budget.stopped:
            aborted = "Batch stopped: inspect the local currency ledger before further requests"
        budget.close()
    if aborted:
        summary["aborted"] = aborted
    write_json(args.output / "summary.json", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--mode", choices=("dry-run", "fixture", "live"), default="dry-run")
    result.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    result.add_argument("--output", type=Path, default=HERE / "runs" / suffix)
    result.add_argument("--config", type=Path, default=None)
    result.add_argument("--data-dir", type=Path, default=None)
    result.add_argument("--scenario", action="append", default=[])
    result.add_argument("--first-round-only", action="store_true")
    result.add_argument("--review-receipt", type=Path, default=None)
    result.add_argument("--scoring-rules", type=Path, default=None)
    result.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    result.add_argument("--budget-ledger", type=Path, default=None)
    result.add_argument("--variant", action="append", choices=VARIANTS, default=[])
    result.add_argument(
        "--pilot", action="store_true", help="Run the two preregistered full-version pilots"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        summary = asyncio.run(run_suite(args))
    except Exception as exc:
        print(_safe_error(exc))
        return 2
    print(json.dumps({"output": str(args.output), **summary}, ensure_ascii=False, indent=2))
    return (
        0
        if args.mode == "dry-run"
        or (
            not summary.get("configuration_error")
            and not summary.get("aborted")
            and summary["execution_statuses"].get("succeeded", 0) == summary["executed"]
            and summary["executed"] == summary["planned_executions"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
