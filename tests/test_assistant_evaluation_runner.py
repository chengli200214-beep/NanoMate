"""Offline end-to-end checks of preparation, guarded execution and human review.

Every approval below is fabricated test policy in a temporary directory. These
tests never represent the user's approval or claim that a provider was evaluated.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from evals.assistant.fixtures import SOURCE_POLICY
from evals.assistant.freeze import CASE_IDS, approval_template, digest, file_hash, prepare
from evals.assistant.runner import _host, prepare_case, record_verdict, run_case, snapshot
from nanomuse.config import LLMSettings
from nanomuse.llm.mock import MockLLM
from nanomuse.schema import Function, LLMResponse, ToolCall


def slot() -> LLMSettings:
    return LLMSettings(
        provider="openai",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        max_retries=0,
        tool_mode="native",
        vision="off",
        stream=False,
        timeout=120,
    )


@pytest.fixture
def batch(tmp_path: Path) -> Path:
    # A synthetic code root makes this unit test independent of another agent's
    # concurrent edits. Production preparation freezes the actual checkout.
    source = tmp_path / "synthetic-code-root"
    (source / "nanomuse").mkdir(parents=True)
    (source / "nanomuse" / "runtime.py").write_text(
        "# Synthetic frozen unit-test source\n", "utf-8"
    )
    directory = tmp_path / ".local" / "assistant-review"
    prepare(source, directory, slot(), SOURCE_POLICY)
    return directory


def approve_test_batch(directory: Path) -> dict[str, Any]:
    frozen = json.loads((directory / "freeze.json").read_text("utf-8"))
    receipt = approval_template(frozen)
    receipt.update(status="approved", human_review_completed=True, phase="complete")
    receipt["annotations"] = [{"id": case, "approved": True} for case in CASE_IDS]
    receipt["budget_policy"] = {
        "model": "deepseek-flash",
        "limit_cny": "10",
        "stop_reserve_cny": "1",
        "context_token_ceiling": 1_048_576,
        "input_peak_micro_cny": 2,
        "output_peak_micro_cny": 8,
        "pricing_verified_at": "2026-10-16",
        "price_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    }
    (directory / "approval.json").write_text(json.dumps(receipt), "utf-8")
    return receipt


def response(content: str = "", **kwargs: Any) -> LLMResponse:
    return LLMResponse(
        content=content,
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        **kwargs,
    )


def model(script: list[LLMResponse]) -> MockLLM:
    delegate = MockLLM(script)
    delegate.settings = slot()  # type: ignore[attr-defined]
    return delegate


def task_call(plan: dict[str, Any], request_id: str) -> ToolCall:
    return ToolCall(
        function=Function(
            name="assistant_tasks",
            arguments=json.dumps(
                {
                    "action": "create",
                    "request_id": request_id,
                    "plan": plan,
                }
            ),
        )
    )


def human_review(directory: Path, execution_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "assistant-human-verdict-v1",
        "human_review_completed": True,
        "critical_failures": [],
        "result_sha256": file_hash(directory / "cases" / execution_id / "result.json"),
        "freeze_sha256": result["freeze_sha256"],
        "scoring_sha256": result["scoring_sha256"],
        "criteria": [
            {
                "id": row["id"],
                "status": "pass",
                "evidence": "Synthetic unit-test reviewer inspected the response and authoritative stores.",
            }
            for row in result["assertions"]
        ],
    }


async def test_a1_prepare_execute_and_explicit_review_never_self_scores(batch: Path) -> None:
    prepared = await prepare_case(batch, "A1-r1")
    assert prepared["provider_requests"] == 0 and prepared["before"]["tasks"] == []
    approve_test_batch(batch)
    delegate = model(
        [response("SQLite transactions commit all changes together or roll them back.")]
    )
    result = await run_case(batch, "A1-r1", delegate, allow_mock=True, serve=False, wait_seconds=3)
    assert result["offline_mock"] is True and result["error"] is None
    assert len(delegate.calls) == 1 and result["before"] == result["after"]
    assert result["verdict"] == "pending_review"
    assert [row["status"] for row in result["assertions"]] == ["pending", "pass"]
    review = record_verdict(batch, "A1-r1", human_review(batch, "A1-r1", result))
    assert review["verdict"] == "passed" and review["offline_mock"] is True
    # The append-only outcome retains its pre-review status and exact bytes.
    saved = json.loads((batch / "cases" / "A1-r1" / "result.json").read_text("utf-8"))
    assert saved == result and saved["verdict"] == "pending_review"


@pytest.mark.parametrize("case_id", ["A2", "B1"])
async def test_chat_task_plan_reaches_authoritative_domain_store(batch: Path, case_id: str) -> None:
    prepared = await prepare_case(batch, f"{case_id}-r1")
    if case_id == "A2":
        steps = [
            {
                "adapter": "reminder",
                "title": "Review SQL tomorrow",
                "reminder": {
                    "action": "create",
                    "text": "Review SQL",
                    "at": prepared["fixture"]["expected"]["at"],
                },
            }
        ]
    else:
        steps = [
            {"adapter": "checkpoint", "title": title}
            for title in ("Read SQL notes", "Practice SQL", "Review study record")
        ]
    plan = {
        "title": "SQL study",
        "success_criteria": "Each requested study step has recorded evidence",
        "steps": steps,
    }
    approve_test_batch(batch)
    delegate = model(
        [
            response(tool_calls=[task_call(plan, f"offline-{case_id}-create")]),
            response("The requested plan is stored. Inspect the task and its actual state."),
        ]
    )
    result = await run_case(
        batch, f"{case_id}-r1", delegate, allow_mock=True, serve=False, wait_seconds=3
    )
    assert result["error"] is None and len(delegate.calls) == 2
    assert len(result["after"]["tasks"]) == 1
    task = result["after"]["tasks"][0]
    if case_id == "A2":
        reminders = result["after"]["reminders"]
        assert len(reminders) == 1 and reminders[0]["text"] == "Review SQL"
        assert (
            task["status"] == "completed"
            and task["steps"][0]["result"]["reminder"]["id"] == reminders[0]["id"]
        )
        assert result["assertions"][0]["status"] == "pass"
    else:
        assert len(task["steps"]) == 3 and task["status"] == "waiting_user"
        assert all(step["status"] != "completed" for step in task["steps"])
        assert all(row["status"] == "pass" for row in result["assertions"])
    review = record_verdict(batch, f"{case_id}-r1", human_review(batch, f"{case_id}-r1", result))
    assert review["verdict"] == "passed" and result["verdict"] == "pending_review"


async def test_b5_waits_for_explicit_user_preference_confirmation(batch: Path) -> None:
    await prepare_case(batch, "B5-r1")
    approve_test_batch(batch)
    plan = {
        "title": "Confirm SQL study preference",
        "success_criteria": "Save only after user consent",
        "steps": [
            {
                "adapter": "preference",
                "title": "Explain invariants first for SQL study",
                "preference": {
                    "action": "set",
                    "key": "sql_study",
                    "expected_revision": 0,
                    "purpose": "SQL study",
                    "value": "Explain invariants before examples.",
                },
            }
        ],
    }
    delegate = model(
        [
            response(tool_calls=[task_call(plan, "offline-B5-create")]),
            response("Please inspect and confirm the scoped preference in the task screen."),
        ]
    )
    result = await run_case(batch, "B5-r1", delegate, allow_mock=True, serve=False, wait_seconds=3)
    assert result["error"] is None and len(delegate.calls) == 2
    assert result["after"]["preferences"] == [] and result["after"]["memory"] == []
    task = result["after"]["tasks"][0]
    assert task["status"] == "waiting_user" and task["steps"][0]["status"] == "waiting_user"
    assert not any(event["kind"] == "preference_confirmed" for event in task["events"])
    assert result["verdict"] == "pending_review"
    review = human_review(batch, "B5-r1", result)
    review["criteria"][1]["status"] = "pending"
    review["criteria"][2]["status"] = "pending"
    assert record_verdict(batch, "B5-r1", review)["verdict"] == "pending"


async def test_prepare_case_is_idempotent_and_keeps_receipt_outside_model_workspace(
    batch: Path,
) -> None:
    first = await prepare_case(batch, "A4-r1")
    frozen_path = batch / "freeze.json"
    original_hash = file_hash(frozen_path)
    second = await prepare_case(batch, "A4-r1")
    assert first == second and file_hash(frozen_path) == original_hash
    runtime = batch / "cases" / "A4-r1" / "runtime"
    assert (runtime / ".prepared-A4.json").is_file()
    assert not list((runtime / "workspace").rglob(".prepared-*.json"))
    assert len(first["before"]["reminders"]) == 1


@pytest.mark.parametrize("tamper", ["receipt", "source", "store"])
async def test_prepared_fixture_tampering_is_rejected_before_any_model_request(
    batch: Path, tamper: str
) -> None:
    case_id = "B2" if tamper == "source" else "A1"
    await prepare_case(batch, f"{case_id}-r1")
    approve_test_batch(batch)
    case_dir = batch / "cases" / f"{case_id}-r1"
    if tamper == "receipt":
        path = case_dir / "fixture.json"
        record = json.loads(path.read_text("utf-8"))
        record["fixture"]["input"] = "Tampered unreviewed input"
        path.write_text(json.dumps(record), "utf-8")
    elif tamper == "source":
        (case_dir / "runtime" / "workspace" / "fixtures" / "notes" / "atomicity.md").write_text(
            "Unreviewed replacement", "utf-8"
        )
    else:
        (case_dir / "runtime" / "workspace" / "unexpected.txt").write_text(
            "Unexpected initial artifact", "utf-8"
        )
    delegate = model([response("This model must not be called.")])
    with pytest.raises(ValueError, match="fixture|state|inputs"):
        await run_case(
            batch, f"{case_id}-r1", delegate, allow_mock=True, serve=False, wait_seconds=3
        )
    assert delegate.calls == [] and not (case_dir / "result.json").exists()


async def test_completed_result_and_verdict_are_append_only_and_hash_bound(batch: Path) -> None:
    await prepare_case(batch, "A1-r1")
    approve_test_batch(batch)
    result = await run_case(
        batch,
        "A1-r1",
        model([response("A transaction is atomic.")]),
        allow_mock=True,
        serve=False,
        wait_seconds=3,
    )
    review = human_review(batch, "A1-r1", result)
    for field in ("result_sha256", "freeze_sha256", "scoring_sha256"):
        modified = deepcopy(review)
        modified[field] = "0" * 64
        with pytest.raises(ValueError, match="exact result"):
            record_verdict(batch, "A1-r1", modified)
    incomplete = deepcopy(review)
    incomplete["criteria"].pop()
    with pytest.raises(ValueError):
        record_verdict(batch, "A1-r1", incomplete)
    record_verdict(batch, "A1-r1", review)
    with pytest.raises(FileExistsError):
        record_verdict(batch, "A1-r1", review)
    replay = model([response("This replay must never reach the model.")])
    with pytest.raises(ValueError, match="append-only"):
        await run_case(batch, "A1-r1", replay, allow_mock=True, serve=False, wait_seconds=3)
    assert replay.calls == []
    with pytest.raises(ValueError, match="approved"):
        await prepare_case(batch, "A1-r1")


async def test_noop_reopen_preserves_snapshot_timestamps_and_domain_state(batch: Path) -> None:
    prepared = await prepare_case(batch, "B6-r1")
    frozen = json.loads((batch / "freeze.json").read_text("utf-8"))
    app, service = _host(batch / "cases" / "B6-r1" / "runtime", frozen, model([]), 8792)
    try:
        reopened = snapshot(service, app)
        assert digest(prepared["before"]) == digest(reopened)
        assert reopened["memory"] and reopened["preferences"][0]["purpose"] == "SQL study"
    finally:
        await service.app.close()
        app.state.research.store.close()
