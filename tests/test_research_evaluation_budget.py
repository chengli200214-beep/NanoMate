from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from evals.research.budget import BatchBudget, BatchBudgetStopped, BudgetedLLM
from evals.research.review import load_approval
from evals.research.runner import (
    DEFAULT_MANIFEST,
    build_matrix,
    freeze_inputs,
    load_manifest,
    parser,
    run_suite,
    verify_frozen,
)
from nanomuse.config import LLMSettings
from nanomuse.llm.mock import MockLLM
from nanomuse.schema import LLMResponse, Message


def policy(**changes: Any) -> dict[str, Any]:
    return {
        "model": "deepseek-flash",
        "limit_cny": "10",
        "stop_reserve_cny": "1",
        "context_token_ceiling": 1_048_576,
        "input_peak_micro_cny": 2,
        "output_peak_micro_cny": 8,
        **changes,
    }


def model(script: list[Any]) -> MockLLM:
    mock = MockLLM(script)
    mock.settings = LLMSettings(tool_mode="native", max_retries=0, vision="off")  # type: ignore[attr-defined]
    return mock


def test_shared_ledger_reserves_before_spending_and_persists_stops(tmp_path: Path) -> None:
    path = tmp_path / "budget.db"
    settings = policy(limit_cny="3", stop_reserve_cny="0.5")
    first, second = BatchBudget(path, settings), BatchBudget(path, settings)
    identifier = first.reserve("setup", 4096)
    with pytest.raises(BatchBudgetStopped, match="threshold"):
        second.reserve("target", 4096)
    first.settle(identifier, {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
    assert first.summary()["known_peak_cost_upper_cny"] == 0.00036
    first.close()
    second.close()
    reopened = BatchBudget(path, settings)
    with pytest.raises(BatchBudgetStopped):
        reopened.reserve("repair", 4096)
    assert reopened.summary()["requests"] == 1
    reopened.close()


def test_interrupted_process_reservations_prevent_automatic_resumption(tmp_path: Path) -> None:
    path = tmp_path / "budget.db"
    budget = BatchBudget(path, policy())
    budget.reserve("interrupted", 4096)
    budget.close()
    reopened = BatchBudget(path, policy())
    assert reopened.stopped
    assert reopened.summary()["unsettled_requests"] == 1
    with pytest.raises(BatchBudgetStopped):
        reopened.reserve("retry", 4096)
    reopened.close()


async def test_all_setup_target_and_repair_attempts_share_one_ledger(tmp_path: Path) -> None:
    budget = BatchBudget(tmp_path / "budget.db", policy())
    response = LLMResponse(
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    )
    for name in ("setup", "target", "repair"):
        wrapped = BudgetedLLM(model([response]), budget, name)
        await wrapped.ask([Message.user("Synthetic request")])
    assert budget.summary()["settled_requests"] == 3
    assert budget.summary()["known_peak_cost_upper_cny"] == 0.00108
    budget.close()


@pytest.mark.parametrize(
    "usage", [{}, {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 999}]
)
async def test_unknown_usage_blocks_the_next_paid_request(
    tmp_path: Path, usage: dict[str, Any]
) -> None:
    budget = BatchBudget(tmp_path / "budget.db", policy())
    delegate = model([LLMResponse(usage=usage), LLMResponse()])
    wrapped = BudgetedLLM(delegate, budget, "target")
    with pytest.raises(BatchBudgetStopped, match="usage"):
        await wrapped.ask([Message.user("Synthetic request")])
    with pytest.raises(BatchBudgetStopped):
        await wrapped.ask([Message.user("Must not reach provider")])
    assert len(delegate.calls) == 1
    assert budget.summary()["unsettled_requests"] == 1
    budget.close()


async def test_transport_failure_is_counted_and_not_retried_silently(tmp_path: Path) -> None:
    budget = BatchBudget(tmp_path / "budget.db", policy())

    def fail(_: Any) -> LLMResponse:
        raise TimeoutError("Synthetic transport failure")

    delegate = model([fail])
    wrapped = BudgetedLLM(delegate, budget, "target")
    with pytest.raises(TimeoutError):
        await wrapped.ask([Message.user("Synthetic request")])
    assert len(delegate.calls) == 1 and budget.stopped
    budget.close()


@pytest.mark.parametrize(
    "setting,value",
    [("max_retries", 1), ("tool_mode", "auto"), ("base_url", "https://other.example")],
)
def test_hidden_retries_and_unpriced_endpoints_are_refused(
    tmp_path: Path, setting: str, value: Any
) -> None:
    budget = BatchBudget(tmp_path / "budget.db", policy())
    delegate = model([])
    setattr(delegate.settings, setting, value)  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="configuration"):
        BudgetedLLM(delegate, budget, "target")
    assert not delegate.calls
    budget.close()


def approved_files(tmp_path: Path) -> tuple[Path, Path]:
    manifest = load_manifest(DEFAULT_MANIFEST)
    manifest_hash = hashlib.sha256(DEFAULT_MANIFEST.read_bytes()).hexdigest()
    scoring = tmp_path / "scoring.json"
    scoring.write_text(
        json.dumps(
            {
                "source_manifest_sha256": manifest_hash,
                "cases": [{"scenario_id": row["id"]} for row in manifest["scenarios"]],
            }
        ),
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "status": "approved",
                "human_review_completed": True,
                "reviewed_at": "2026-10-16",
                "independent_review": False,
                "source_manifest_sha256": manifest_hash,
                "scope": {
                    "selected": "two_synthetic_candidates_plus_separate_real_repository_smoke"
                },
                "scenarios": [
                    {"id": row["id"], "decision": "approve"} for row in manifest["scenarios"]
                ],
                "scoring_rules": {
                    "decision": "approve",
                    "source_sha256": hashlib.sha256(scoring.read_bytes()).hexdigest(),
                },
                "budget": {
                    "status": "approved",
                    "additional_limit_cny": 100,
                    "automatic_topup_allowed": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return receipt, scoring


def test_approval_freezes_exact_scoring_and_changes_invalidate_the_freeze(tmp_path: Path) -> None:
    receipt, scoring = approved_files(tmp_path)
    approval, budget = load_approval(receipt, scoring, DEFAULT_MANIFEST)
    assert budget["limit_cny"] == "100" and approval["semantic_output_review"] == "pending"
    freeze = freeze_inputs(
        DEFAULT_MANIFEST,
        tmp_path / "run",
        build_matrix(load_manifest(DEFAULT_MANIFEST)),
        approval=approval,
        review_files=[receipt, scoring],
    )
    assert freeze["annotation_status"] == "owner_approved_agent_authored_not_independent"
    assert len(freeze["matrix"]) == 36
    scoring.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        verify_frozen(freeze)
    with pytest.raises(ValueError, match="approval"):
        load_approval(receipt, scoring, DEFAULT_MANIFEST)


def test_partial_annotation_approval_prevents_live_evaluation(tmp_path: Path) -> None:
    receipt, scoring = approved_files(tmp_path)
    content = json.loads(receipt.read_text("utf-8"))
    content["scenarios"][0]["decision"] = None
    receipt.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match="approval"):
        load_approval(receipt, scoring, DEFAULT_MANIFEST)


async def test_ready_live_model_cannot_bypass_owner_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nanomuse.research.model as clients

    monkeypatch.setattr(clients, "research_model_ready", lambda _: True)
    args = parser().parse_args(["--mode", "live", "--output", str(tmp_path / "unapproved")])
    with pytest.raises(ValueError, match="requires approved"):
        await run_suite(args)
    assert not args.output.exists()


def test_existing_ledger_refuses_a_different_spending_policy(tmp_path: Path) -> None:
    path = tmp_path / "budget.db"
    budget = BatchBudget(path, policy())
    budget.close()
    with pytest.raises(ValueError, match="cannot change"):
        BatchBudget(path, policy(limit_cny="100"))


def test_a_repair_reuses_prior_settled_spending_instead_of_resetting_the_cap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "budget.db"
    approved = policy(limit_cny="3", stop_reserve_cny="0.5")
    original = BatchBudget(path, approved)
    identifier = original.reserve("original-batch", 4096)
    original.settle(
        identifier, {"prompt_tokens": 500_000, "completion_tokens": 0, "total_tokens": 500_000}
    )
    original.close()
    repair = BatchBudget(path, approved)
    with pytest.raises(BatchBudgetStopped, match="threshold"):
        repair.reserve("repair-batch", 4096)
    assert repair.summary()["known_peak_cost_upper_cny"] == 1
    assert repair.summary()["requests"] == 1
    repair.close()


async def test_a_targeted_repair_keeps_its_own_matrix_separate(tmp_path: Path) -> None:
    args = parser().parse_args(
        [
            "--mode",
            "fixture",
            "--variant",
            "full",
            "--scenario",
            "update-conflict",
            "--first-round-only",
            "--output",
            str(tmp_path / "targeted"),
        ]
    )
    summary = await run_suite(args)
    assert summary["executed"] == summary["planned_executions"] == 1
    assert summary["execution_statuses"] == {"succeeded": 1}
    frozen = json.loads((args.output / "freeze.json").read_text("utf-8"))
    assert frozen["matrix"][0]["variant"] == "full"
