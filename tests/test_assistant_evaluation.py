"""The assistant preparation tools never imply approval or paid acceptance."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from evals.assistant.freeze import (
    CASE_IDS,
    MANIFEST,
    PILOTS,
    SCORING,
    approval,
    approval_template,
    matrix,
    model_config,
    prepare,
    refresh_sources,
    verify,
)
from evals.assistant.guard import EvaluationGuard, wire_isolated_service
from evals.research.budget import BatchBudgetStopped
from nanomuse.config import LLMSettings, Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.schema import LLMResponse, Message


def slot() -> LLMSettings:
    return LLMSettings(max_retries=0, tool_mode="native", vision="off", stream=False, timeout=120)


def response(**changes: Any) -> LLMResponse:
    return LLMResponse(
        content="Observed test response",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
        **changes,
    )


def mock(script: list[Any]) -> MockLLM:
    delegate = MockLLM(script)
    delegate.settings = slot()  # type: ignore[attr-defined]
    return delegate


@pytest.fixture
def bundle(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "code"
    (root / "nanomuse").mkdir(parents=True)
    (root / "nanomuse" / "runtime.py").write_text("# Frozen synthetic runtime\n", "utf-8")
    directory = tmp_path / ".local" / "review"
    frozen = prepare(root, directory, slot(), "synthetic_materials_disclosed")
    receipt = approval_template(frozen)
    receipt.update(status="approved", human_review_completed=True)
    receipt["annotations"] = [{"id": case, "approved": True} for case in CASE_IDS]
    # Synthetic test policy, not user approval or a published fee prediction.
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
    return directory, frozen


def test_public_inputs_have_24_cases_and_32_windows_with_four_included_pilots() -> None:
    rows = matrix()
    manifest, scoring = (
        json.loads(MANIFEST.read_text("utf-8")),
        json.loads(SCORING.read_text("utf-8")),
    )
    assert [case["id"] for case in manifest["cases"]] == list(CASE_IDS)
    assert [case["id"] for case in scoring["cases"]] == list(CASE_IDS)
    assert len(rows) == len({row["execution_id"] for row in rows}) == 32
    assert [row["case_id"] for row in rows[:4]] == list(PILOTS)
    assert sum(row["pilot"] for row in rows) == 4
    assert all(
        sum(row["case_id"] == case for row in rows)
        == (3 if case in {"A4", "C1", "C5", "D2"} else 1)
        for case in CASE_IDS
    )
    text = MANIFEST.read_text("utf-8").lower()
    assert all(word not in text for word in ("limit_cny", "budget_policy", "api_key"))


@pytest.mark.parametrize(
    "field",
    ["schema", "status", "freeze_sha256", "model_sha256", "scope", "scoring_sha256", "annotations"],
)
def test_new_approval_binds_all_inputs_and_cannot_reuse_old_research_receipt(
    bundle: tuple[Path, dict[str, Any]], field: str
) -> None:
    directory, frozen = bundle
    receipt = json.loads((directory / "approval.json").read_text("utf-8"))
    receipt[field] = "old-research-value"
    with pytest.raises(ValueError, match="approval"):
        approval(frozen, receipt)


def test_pending_review_form_never_approves_itself(bundle: tuple[Path, dict[str, Any]]) -> None:
    _, frozen = bundle
    pending = approval_template(frozen)
    assert pending["status"] == "pending" and pending["budget_policy"] is None
    with pytest.raises(ValueError, match="approval"):
        approval(frozen, pending)


def test_code_change_or_new_runtime_file_invalidates_freeze(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    _, frozen = bundle
    root = Path(frozen["code_root"])
    verify(frozen)
    (root / "nanomuse" / "added.py").write_text("# New execution path\n", "utf-8")
    with pytest.raises(ValueError, match="changed"):
        verify(frozen)


def test_refresh_sources_preserves_unapproved_fixture_receipts(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, frozen = bundle
    (directory / "approval.json").unlink()
    receipt = directory / "cases" / "A4-r1" / "fixture.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"provider_requests": 0}', "utf-8")
    from evals.assistant.freeze import digest, file_hash

    frozen["case_fixtures"] = {"A4-r1": {"path": str(receipt), "sha256": file_hash(receipt)}}
    frozen.pop("freeze_sha256")
    frozen["freeze_sha256"] = digest(frozen)
    (directory / "freeze.json").write_text(json.dumps(frozen), "utf-8")
    (Path(frozen["code_root"]) / "nanomuse" / "runtime.py").write_text("# Corrected\n", "utf-8")
    refreshed = refresh_sources(directory)
    verify(refreshed)
    assert refreshed["freeze_sha256"] != frozen["freeze_sha256"]
    assert refreshed["case_fixtures"] == frozen["case_fixtures"]
    pending = json.loads((directory / "approval.pending.json").read_text("utf-8"))
    assert pending["status"] == "pending" and pending["budget_policy"] is None
    receipt.write_text('{"provider_requests": 1}', "utf-8")
    with pytest.raises(ValueError, match="receipts changed"):
        refresh_sources(directory)


def test_refresh_sources_cannot_reset_a_started_or_approved_batch(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    with pytest.raises(ValueError, match="approved or started"):
        refresh_sources(directory)
    (directory / "approval.json").unlink()
    (directory / "budget.sqlite3").touch()
    with pytest.raises(ValueError, match="approved or started"):
        refresh_sources(directory)


@pytest.mark.parametrize(
    "key,value",
    [
        ("max_retries", 1),
        ("tool_mode", "auto"),
        ("vision", "auto"),
        ("base_url", "https://other.example"),
        ("extra_body", {"max_tokens": 9000}),
        ("max_tokens", 4097),
        ("stream", True),
    ],
)
def test_unpriced_or_hidden_provider_paths_rejected_before_any_request(
    key: str, value: Any
) -> None:
    candidate = slot()
    setattr(candidate, key, value)
    with pytest.raises(ValueError, match="reviewed"):
        model_config(candidate)


async def test_pilot_phase_and_request_cap_reject_before_provider(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    delegate = mock([response() for _ in range(13)])
    wrapped = guard.wrap(delegate)
    with pytest.raises(BatchBudgetStopped, match="phase"):
        guard.begin("C1-r1")
    guard.begin("A4-r1")
    for _ in range(12):
        await wrapped.ask([Message.user("Synthetic reminder request")])
    with pytest.raises(BatchBudgetStopped, match="allowance"):
        await wrapped.ask([Message.user("Thirteenth request must not be sent")])
    assert len(delegate.calls) == guard.budget.summary()["requests"] == 12
    assert guard.budget.stopped
    guard.close()


async def test_currency_reservation_is_durable_before_delegate_is_called(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("A4-r1")

    def inspected(_: list[Message]) -> LLMResponse:
        row = guard.budget.db.execute("SELECT * FROM request").fetchone()
        assert row["status"] == "reserved" and row["reserved"] > 0
        assert row["execution_id"] == "A4-r1"
        return response()

    delegate = mock([inspected])
    await guard.wrap(delegate).ask([Message.user("Inspect the pre-request reservation")])
    assert guard.budget.summary()["settled_requests"] == 1
    guard.finish("A4-r1")
    with pytest.raises(BatchBudgetStopped, match="replayed"):
        guard.begin("A4-r1")
    guard.close()


async def test_unknown_usage_stops_main_and_research_sharing_same_guard(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("D2-r1")
    delegate = mock([LLMResponse(content="Missing usage"), response()])
    main, research = guard.wrap(delegate), guard.wrap(delegate)
    with pytest.raises(BatchBudgetStopped, match="usage"):
        await research.ask([Message.user("Synthetic research stage")])
    with pytest.raises(BatchBudgetStopped):
        await main.ask([Message.user("Cannot continue from another route")])
    assert len(delegate.calls) == 1 and guard.budget.summary()["unsettled_requests"] == 1
    guard.close()


def test_restart_keeps_unresolved_reservations_and_execution_windows_stopped(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    first = EvaluationGuard(directory, allow_mock=True)
    first.begin("A4-r1")
    first.budget.reserve("A4-r1", 4096)
    first.close()
    recovered = EvaluationGuard(directory, allow_mock=True)
    assert recovered.budget.stopped
    assert recovered.budget.summary()["unsettled_requests"] == 1
    with pytest.raises(BatchBudgetStopped):
        recovered.begin("B2-r1")
    recovered.close()


async def test_timeout_retains_unknown_usage_without_retry_or_second_attempt(
    bundle: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import evals.assistant.guard as module

    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("C5-r1")
    delegate = mock([])
    attempts = 0

    async def stall(*_: Any, **__: Any) -> LLMResponse:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(10)
        return response()

    monkeypatch.setattr(module, "REQUEST_SECONDS", 0.01)
    monkeypatch.setattr(delegate, "ask", stall)
    with pytest.raises(TimeoutError):
        await guard.wrap(delegate).ask([Message.user("Timeout test")])
    assert attempts == 1 and guard.budget.stopped
    assert guard.budget.summary()["unsettled_requests"] == 1
    guard.close()


async def test_length_completion_does_not_trigger_hidden_roomier_retry(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("B2-r1")
    delegate = mock([response(finish_reason="length"), response()])
    result = await guard.wrap(delegate).ask_complete([Message.user("Bounded completion")])
    assert result.finish_reason == "length" and len(delegate.calls) == 1
    guard.close()


@pytest.mark.parametrize("kind", ["images", "payload", "output", "deadline"])
async def test_rejections_have_no_provider_request_or_reservation(
    bundle: tuple[Path, dict[str, Any]], kind: str
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("A4-r1")
    delegate = mock([response()])
    wrapped = guard.wrap(delegate)
    messages, kwargs = [Message.user("Small text")], {}
    if kind == "images":
        messages[0].images = ["data:image/png;base64,AA=="]
    elif kind == "payload":
        messages[0].content = "x" * 262_145
    elif kind == "output":
        kwargs["max_tokens"] = 4097
    else:
        guard.budget.db.execute("UPDATE assistant_execution SET deadline=0")
    with pytest.raises((ValueError, BatchBudgetStopped)):
        await wrapped.ask(messages, **kwargs)
    assert not delegate.calls and guard.budget.summary()["requests"] == 0
    guard.close()


async def test_real_service_chat_and_research_factory_share_wrapper_without_loading_keys(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    from nanomuse.server import create_app
    from nanomuse.server.service import MuseService

    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    settings = Settings()
    settings.data_dir = directory / "runtime"
    settings.agent.workspace = settings.data_dir / "workspace"
    settings.server.port = 8792
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False
    settings.hub.enabled = settings.skills.enabled = False
    settings.memory.embeddings = "off"
    settings.mcp.servers = []
    settings.llm = slot()
    settings.ensure_dirs()
    delegate = mock([response(), response()])
    service = MuseService(settings, llm=delegate)
    app = create_app(settings, service)
    research = app.state.research
    wrapped = wire_isolated_service(service, research, guard)
    assert service.app.agent.llm is service.threads["main"].agent.llm is wrapped
    guard.begin("D2-r1")
    reasoner = research._get_reasoner({"mode": "live"}, "synthetic-run")
    assert reasoner.llm.guard is guard and reasoner.llm.delegate is delegate
    assert "computer_task" not in service.app.tools and "assistant_tasks" in service.app.tools
    await service.threads["main"].agent.run("Give a short plain answer without tools.")
    await reasoner.llm.ask([Message.user("Synthetic research request")])
    assert len(delegate.calls) == 2 and guard.budget.summary()["settled_requests"] == 2
    guard.finish("D2-r1")
    assert guard.summary()["domain_acceptance"] == "not_scored_by_technical_guard"
    await service.app.close()
    research.store.close()
    guard.close()


async def test_overall_request_cap_is_checked_inside_reservation_transaction(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("A4-r1")
    with guard.budget.transaction():
        guard.budget.db.executemany(
            "INSERT INTO request VALUES(?,?,'settled',0,0,0,0,NULL)",
            [(f"recorded-{number}", "earlier-execution") for number in range(384)],
        )
    delegate = mock([response()])
    with pytest.raises(BatchBudgetStopped, match="allowance"):
        await guard.wrap(delegate).ask([Message.user("Cannot exceed the overall request cap")])
    assert not delegate.calls and guard.budget.summary()["requests"] == 384
    guard.close()


def test_full_matrix_has_32_nonreplayable_execution_windows(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, frozen = bundle
    receipt_path = directory / "approval.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["phase"] = "complete"
    receipt_path.write_text(json.dumps(receipt), "utf-8")
    guard = EvaluationGuard(directory, allow_mock=True)
    for row in frozen["matrix"]:
        guard.begin(row["execution_id"])
        guard.finish(row["execution_id"])
    with pytest.raises(BatchBudgetStopped):
        guard.begin("A4-r4")
    with pytest.raises(BatchBudgetStopped, match="replayed"):
        guard.begin("A4-r1")
    assert len(guard.summary()["executions"]) == 32
    assert guard.summary()["domain_acceptance"] == "not_scored_by_technical_guard"
    guard.close()


async def test_insufficient_currency_refuses_before_provider(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    receipt_path = directory / "approval.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["budget_policy"].update(limit_cny="2", stop_reserve_cny="1")
    receipt_path.write_text(json.dumps(receipt), "utf-8")
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("A4-r1")
    delegate = mock([response()])
    with pytest.raises(BatchBudgetStopped, match="threshold"):
        await guard.wrap(delegate).ask([Message.user("Full vendor reservation cannot fit")])
    assert not delegate.calls and guard.budget.summary()["requests"] == 0
    guard.close()


async def test_reported_input_excess_stops_after_settling_actual_known_usage(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("A4-r1")
    delegate = mock(
        [
            LLMResponse(
                usage={"prompt_tokens": 32_001, "completion_tokens": 20, "total_tokens": 32_021}
            )
        ]
    )
    with pytest.raises(BatchBudgetStopped, match="tokens"):
        await guard.wrap(delegate).ask([Message.user("Token accounting check")])
    summary = guard.budget.summary()
    assert summary["settled_requests"] == 1 and summary["stopped"]
    assert "assistant_token_cap_exceeded" in summary["stop_reasons"]
    guard.close()


async def test_mutating_configuration_or_code_after_wrapping_prevents_spending(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, frozen = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("A4-r1")
    delegate = mock([response()])
    wrapped = guard.wrap(delegate)
    delegate.settings.model = "deepseek-v4-pro"  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="configuration"):
        await wrapped.ask([Message.user("Changed model")])
    delegate.settings.model = "deepseek-flash"  # type: ignore[attr-defined]
    (Path(frozen["code_root"]) / "nanomuse" / "runtime.py").write_text("# Changed\n", "utf-8")
    with pytest.raises(ValueError, match="changed"):
        await wrapped.ask([Message.user("Changed source")])
    assert not delegate.calls and guard.budget.summary()["requests"] == 0
    guard.close()


async def test_concurrent_research_and_main_requests_cannot_take_two_slots(
    bundle: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("D2-r1")
    delegate = mock([])
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending(*_: Any, **__: Any) -> LLMResponse:
        entered.set()
        await release.wait()
        return response()

    monkeypatch.setattr(delegate, "ask", pending)
    main, research = guard.wrap(delegate), guard.wrap(delegate)
    first = asyncio.create_task(main.ask([Message.user("Main chat")]))
    await entered.wait()
    with pytest.raises(BatchBudgetStopped, match="serial"):
        await research.ask([Message.user("Research must wait")])
    release.set()
    await first
    assert guard.budget.summary()["requests"] == 1
    guard.close()


async def test_revoked_approval_blocks_a_previously_wrapped_client(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.begin("A4-r1")
    delegate = mock([response()])
    wrapped = guard.wrap(delegate)
    receipt_path = directory / "approval.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["status"] = "revoked"
    receipt_path.write_text(json.dumps(receipt), "utf-8")
    with pytest.raises(ValueError, match="revoked"):
        await wrapped.ask([Message.user("The old wrapper must respect revocation")])
    assert not delegate.calls and guard.budget.summary()["requests"] == 0
    guard.close()


async def test_late_research_reasoner_cannot_charge_another_execution(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    delegate = mock([response()])
    main = guard.wrap(delegate)
    guard.begin("D2-r1")
    old_reasoner = main.bind_current()
    guard.finish("D2-r1")
    guard.begin("A4-r1")
    with pytest.raises(BatchBudgetStopped, match="previous execution"):
        await old_reasoner.ask([Message.user("A late old research request")])
    assert not delegate.calls and guard.budget.summary()["requests"] == 0
    guard.close()


async def test_new_phase_approval_keeps_pilot_charges_and_ids_in_one_ledger(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    from evals.assistant.amend import amend

    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    delegate = mock([response() for _ in PILOTS])
    wrapped = guard.wrap(delegate)
    for case in PILOTS:
        guard.begin(f"{case}-r1")
        await wrapped.ask([Message.user("Synthetic pilot request")])
        guard.finish(f"{case}-r1")
    earlier = [dict(row) for row in guard.budget.db.execute("SELECT * FROM request ORDER BY rowid")]
    guard.close()
    receipt = json.loads((directory / "approval.json").read_text("utf-8"))
    receipt["phase"] = "complete"
    receipt["budget_policy"]["limit_cny"] = "20"
    result = amend(directory, receipt)
    assert not result["recovered_receipt_publication"]
    continued = EvaluationGuard(directory, allow_mock=True)
    assert [
        dict(row) for row in continued.budget.db.execute("SELECT * FROM request ORDER BY rowid")
    ] == earlier
    assert len(continued.summary()["executions"]) == 4
    assert continued.budget.summary()["settled_requests"] == 4
    continued.begin("C1-r1")
    continued.finish("C1-r1")
    with pytest.raises(BatchBudgetStopped, match="replayed"):
        continued.begin("A4-r1")
    continued.close()


async def test_amendment_publication_crash_repairs_receipt_without_resetting_history(
    bundle: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import evals.assistant.amend as module

    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    for case in PILOTS:
        guard.begin(f"{case}-r1")
        guard.finish(f"{case}-r1")
    guard.close()
    receipt = json.loads((directory / "approval.json").read_text("utf-8"))
    receipt["phase"] = "complete"
    publish = module._publish
    with monkeypatch.context() as patch:
        patch.setattr(
            module, "_publish", lambda *args: (_ for _ in ()).throw(OSError("publication crash"))
        )
        with pytest.raises(OSError):
            module.amend(directory, receipt)
    with pytest.raises(ValueError, match="policy"):
        EvaluationGuard(directory, allow_mock=True)
    monkeypatch.setattr(module, "_publish", publish)
    assert module.amend(directory, receipt)["recovered_receipt_publication"]
    recovered = EvaluationGuard(directory, allow_mock=True)
    assert len(recovered.summary()["executions"]) == 4
    assert recovered.budget.summary()["requests"] == 0
    recovered.close()


def test_phase_amendment_requires_all_four_pilots_without_outstanding_usage(
    bundle: tuple[Path, dict[str, Any]],
) -> None:
    from evals.assistant.amend import amend

    directory, _ = bundle
    guard = EvaluationGuard(directory, allow_mock=True)
    guard.close()
    receipt = json.loads((directory / "approval.json").read_text("utf-8"))
    receipt["phase"] = "complete"
    with pytest.raises(ValueError, match="four pilot"):
        amend(directory, receipt)
