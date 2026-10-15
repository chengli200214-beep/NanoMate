from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from evals.research.runner import (
    DEFAULT_MANIFEST,
    EvaluationReasoner,
    SyntheticFetcher,
    _usage,
    build_matrix,
    execute_one,
    freeze_inputs,
    load_manifest,
    parser,
    run_suite,
    verify_frozen,
    write_json,
)
from nanomuse.research.contracts import ImpactJudgment


def test_first_round_has_exactly_the_frozen_32_executions() -> None:
    manifest = load_manifest(DEFAULT_MANIFEST)
    matrix = build_matrix(manifest, include_reruns=False)
    assert len(matrix) == 32
    assert Counter(item["variant"] for item in matrix) == {
        "baseline": 12,
        "full": 12,
        "no_version_invalidation": 4,
        "no_impact_filter": 4,
    }
    assert all(item["phase"] == "first_round" for item in matrix)


def test_reruns_are_preregistered_and_pilots_are_fixed() -> None:
    manifest = load_manifest(DEFAULT_MANIFEST)
    matrix = build_matrix(manifest)
    reruns = [item for item in matrix if item["phase"] == "preregistered_rerun"]
    assert len(matrix) == 36 and len(reruns) == 4
    assert Counter(item["scenario_id"] for item in reruns) == {
        "constraints-no-sandbox": 2,
        "update-dependency": 2,
    }
    pilots = build_matrix(manifest, pilot=True)
    assert len(pilots) == 2 and all(item["variant"] == "full" for item in pilots)


def test_input_annotations_are_explicitly_unreviewed_synthetic_drafts() -> None:
    manifest = load_manifest(DEFAULT_MANIFEST)
    assert manifest["dataset_origin"] == "synthetic"
    assert manifest["annotation_status"] == "agent_draft_pending_human_review"
    assert manifest["independent_annotation_review"] is False
    assert all(".example/" in item["url"] for item in manifest["sources"].values())


def test_changed_inputs_fail_frozen_hash_verification(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(DEFAULT_MANIFEST.read_bytes())
    freeze = freeze_inputs(manifest_path, tmp_path / "run", [])
    verify_frozen(freeze)
    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        verify_frozen(freeze)


async def test_default_dry_run_makes_no_execution_or_quality_results(tmp_path: Path) -> None:
    output = tmp_path / "dry-run"
    args = parser().parse_args(["--output", str(output)])
    assert args.mode == "dry-run"
    summary = await run_suite(args)
    assert summary["planned_executions"] == 36 and summary["executed"] == 0
    assert summary["model_quality_metrics"] is None
    assert summary["improvement_claim"] is None
    assert (output / "freeze.json").exists()
    assert not (output / "executions").exists()


def test_missing_usage_is_never_replaced_with_synthetic_token_estimates() -> None:
    assert _usage([{"input_tokens": None, "output_tokens": None}])["input_tokens"] is None
    assert _usage([{"input_tokens": 10}, {"input_tokens": None}])["input_tokens"] is None
    assert _usage([{"input_tokens": 10}, {"input_tokens": 5}])["input_tokens"] == 15
    assert _usage([])["input_tokens"] == 0
    assert _usage([{}])["monetary_cost"] is None


def test_artifact_overwrite_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    write_json(path, {"status": "failed"})
    with pytest.raises(FileExistsError):
        write_json(path, {"status": "succeeded"})
    assert json.loads(path.read_text("utf-8"))["status"] == "failed"


async def test_synthetic_fetcher_reads_only_manifest_sources() -> None:
    item = {"url": "https://alpha.example/docs", "content": "Synthetic text.", "version": "s-v1"}
    fetcher = SyntheticFetcher([item])
    result = await fetcher.fetch({"url": item["url"]})
    assert result.content == item["content"] and not result.not_modified
    second = await fetcher.fetch({"url": item["url"]}, item)
    assert second.not_modified
    with pytest.raises(ValueError, match="unregistered"):
        await fetcher.fetch({"url": "https://unregistered.example"})


async def test_fixture_really_executes_orchestrator_and_keeps_artifacts(tmp_path: Path) -> None:
    args = parser().parse_args(
        [
            "--mode",
            "fixture",
            "--workers",
            "3",
            "--scenario",
            "initial-portable",
            "--first-round-only",
            "--output",
            str(tmp_path / "actual-fixture"),
        ]
    )
    summary = await run_suite(args)
    assert summary["executed"] == 2
    assert summary["workers"] == 3
    assert summary["execution_statuses"] == {"succeeded": 2}
    assert summary["execution_mode"] == "offline_scripted_workflow_verification"
    assert summary["usage"]["input_tokens"] is None
    assert summary["model_quality_metrics"] is None
    files = list(args.output.glob("executions/*/result.json"))
    assert len(files) == 2
    result = json.loads(files[0].read_text("utf-8"))
    assert result["freeze_sha256"] == summary["freeze_sha256"]
    assert result["target_run"]["status"] == "succeeded"
    assert result["all_calls"] and result["observations"]["report_sections"] == 6
    assert (files[0].parent / "data/research.db").exists()
    assert list((files[0].parent / "data/reports").glob("*/*.md"))


async def test_actual_failure_is_retained_and_not_rewritten_as_a_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nanomuse.research.reasoning as reasoning

    class FailedFixture:
        calls: list[dict[str, Any]] = []

        async def extract(self, _: Any) -> Any:
            raise ValueError("Deliberate workflow failure")

    monkeypatch.setattr(reasoning, "FixtureReasoner", FailedFixture)
    manifest = load_manifest(DEFAULT_MANIFEST)
    item = build_matrix(manifest, scenario_ids={"initial-portable"}, include_reruns=False)[0]
    result = await execute_one(manifest, item, tmp_path, "fixture")
    assert result["status"] == "failed"
    assert "Deliberate workflow failure" in result["error"]
    assert result["quality_score"] is None
    retained = json.loads(
        (tmp_path / "executions" / item["execution_id"] / "result.json").read_text("utf-8")
    )
    assert retained["status"] == "failed" and retained["target_run"]["status"] == "failed"


async def test_revision_ablation_is_identified_and_only_changes_previous_decision_statuses(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(DEFAULT_MANIFEST)
    items = build_matrix(manifest, scenario_ids={"constraints-no-sandbox"}, include_reruns=False)
    full = next(item for item in items if item["variant"] == "full")
    ablation = next(item for item in items if item["variant"] == "no_version_invalidation")
    complete = await execute_one(manifest, full, tmp_path, "fixture")
    changed = await execute_one(manifest, ablation, tmp_path, "fixture")
    assert complete["status"] == changed["status"] == "succeeded"
    assert complete["observations"]["old_decisions_invalidated"] is True
    assert changed["observations"]["old_decisions_invalidated"] is False
    assert "intervention" in changed
    assert complete["setup_usage"]["model_calls"] > 0
    assert complete["usage"]["model_calls"] < len(complete["all_calls"])


async def test_impact_intervention_is_separate_from_the_original_model_judgment() -> None:
    class Delegate:
        calls = [{"stage": "impact", "input_tokens": None}]

        async def impact(self, _: Any) -> ImpactJudgment:
            return ImpactJudgment(change_summary="No material change", impact_level="irrelevant")

    delegate = Delegate()
    wrapped = EvaluationReasoner(delegate, "no_impact_filter")
    actual = await delegate.impact({})
    intervened = await wrapped.impact({})
    assert actual.impact_level == "irrelevant"
    assert intervened.impact_level == "recommendation"
    assert wrapped.calls is delegate.calls
    assert wrapped.interventions[0]["original_model_judgment"]["impact_level"] == "irrelevant"


async def test_live_mode_without_a_ready_model_records_no_fictional_executions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nanomuse.llm.factory as factory

    monkeypatch.setattr(factory, "llm_ready", lambda *args, **kwargs: False)
    args = parser().parse_args(
        ["--mode", "live", "--pilot", "--output", str(tmp_path / "no-model")]
    )
    summary = await run_suite(args)
    assert summary["executed"] == 0
    assert "configuration_error" in summary
    assert not (args.output / "executions").exists()


async def test_model_initialization_failure_is_retained_before_any_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nanomuse.llm.factory as factory
    from nanomuse.config import Settings

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("Deliberate model initialization failure")

    monkeypatch.setattr(factory, "create_llm", fail)
    manifest = load_manifest(DEFAULT_MANIFEST)
    item = build_matrix(manifest, scenario_ids={"initial-portable"}, include_reruns=False)[0]
    settings = Settings()
    settings.llm.api_key = "synthetic-test-value"
    result = await execute_one(manifest, item, tmp_path, "live", settings)
    assert result["status"] == "failed" and result["target_run"] is None
    assert result["target_duration_ms"] is None
    assert result["usage"]["model_calls"] == 0
    retained = tmp_path / "executions" / item["execution_id"] / "result.json"
    assert "model initialization failure" in retained.read_text("utf-8")


async def test_freeze_abort_preserves_a_summary_without_inventing_unexecuted_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import evals.research.runner as runner

    def changed(_: Any) -> None:
        raise RuntimeError("Frozen inputs changed")

    monkeypatch.setattr(runner, "verify_frozen", changed)
    args = parser().parse_args(
        ["--mode", "fixture", "--pilot", "--output", str(tmp_path / "abort")]
    )
    summary = await run_suite(args)
    assert summary["executed"] == 0 and "Frozen inputs changed" in summary["aborted"]
    assert (args.output / "summary.json").exists()
    assert not (args.output / "executions").exists()
