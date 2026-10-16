"""Offline production admission, with no provider client construction or paid requests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from nanomuse.assistant.profile import assistant_settings
from nanomuse.assistant.usage_limits import (
    CONTEXT_TOKENS,
    PRICE_POLICY,
    UsageGuardLLM,
    UsageLimits,
    UsageOwner,
    UsageStopped,
    current_usage_owner,
    guard_llm,
    make_guarded_llm,
    strict_llm_settings,
    usage_owner,
)
from nanomuse.config import AssistantUsageSettings, LLMSettings, Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.llm.openai_chat import OpenAIChatLLM
from nanomuse.schema import LLMResponse, Message


def policy(**changes: Any) -> AssistantUsageSettings:
    return AssistantUsageSettings(
        enabled=True,
        price_policy=PRICE_POLICY,
        daily_cny=100,
        task_cny=100,
        **changes,
    )


def client(
    limits: UsageLimits,
    answer: Callable[[], Awaitable[LLMResponse]] | None = None,
    owner: UsageOwner | None = None,
    slot: LLMSettings | None = None,
) -> tuple[UsageGuardLLM, list[dict[str, Any]]]:
    """Allocate an adapter without its constructor, SDK client or network transport."""
    slot = slot or strict_llm_settings(LLMSettings(), limits.policy)
    delegate = object.__new__(OpenAIChatLLM)
    delegate.settings = slot
    delegate.client = SimpleNamespace(max_retries=0)  # type: ignore[assignment]
    delegate.session_header = ""
    delegate.vision_available = False
    calls: list[dict[str, Any]] = []

    async def send(parameters: dict[str, Any], on_delta: Any) -> LLMResponse:
        calls.append(parameters)
        if answer is not None:
            return await answer()
        return LLMResponse(content="done", usage={"prompt_tokens": 100, "completion_tokens": 10})

    delegate._send = send  # type: ignore[method-assign]
    return UsageGuardLLM(delegate, slot, limits, owner), calls


def test_config_limits_are_bounded_and_only_isolated_profile_enables_meter(tmp_path: Path) -> None:
    assert not Settings().assistant_usage.enabled
    assert Settings().assistant_usage.daily_cny == Settings().assistant_usage.task_cny == 0
    assert Settings().agent.max_task_continuations == 8
    assert Settings().agent.task_continuation_seconds == 900
    assert assistant_settings(tmp_path, 8798).assistant_usage.enabled
    for value in (0, 32769):
        with pytest.raises(ValueError):
            AssistantUsageSettings(request_output_tokens=value)


async def test_zero_budget_and_unknown_policy_stop_locally_without_model_summary(
    tmp_path: Path,
) -> None:
    limits = UsageLimits(tmp_path / "usage.db", AssistantUsageSettings(enabled=True))
    model, calls = client(limits, owner=UsageOwner("turn"))
    with pytest.raises(UsageStopped, match="configure assistant_usage"):
        await model.ask([Message.user("hello")])
    assert calls == [] and limits.status()["requests"] == 0
    limits.update_policy(policy().model_copy(update={"price_policy": ""}))
    with pytest.raises(UsageStopped, match="price_policy"):
        await model.ask([Message.user("hello")])
    assert not model.continuation_allowed("turn") and calls == []


async def test_mock_uses_no_money_reservation_even_without_scope(tmp_path: Path) -> None:
    limits = UsageLimits(tmp_path / "usage.db", AssistantUsageSettings(enabled=True))
    mock = MockLLM([LLMResponse(content="offline")])
    guarded = guard_llm(mock, LLMSettings(), limits)
    assert (await guarded.ask([Message.user("hello")])).content == "offline"
    assert len(mock.calls) == 1 and limits.status()["requests"] == 0


async def test_blocked_configuration_never_constructs_a_provider(tmp_path: Path) -> None:
    limits = UsageLimits(tmp_path / "usage.db", AssistantUsageSettings(enabled=True))
    calls: list[LLMSettings] = []

    def factory(slot: LLMSettings) -> MockLLM:
        calls.append(slot)
        return MockLLM([LLMResponse(content="offline")])

    blocked = make_guarded_llm(LLMSettings(), limits, factory)
    with pytest.raises(UsageStopped, match="configure assistant_usage"):
        await blocked.ask([Message.user("hello")])
    limits.update_policy(policy())
    unsupported = make_guarded_llm(LLMSettings(model="unreviewed-synthetic-model"), limits, factory)
    with pytest.raises(UsageStopped, match="no reviewed"):
        await unsupported.ask([Message.user("hello")])
    assert calls == [] and limits.status()["requests"] == 0
    allowed = make_guarded_llm(LLMSettings(), limits, factory)
    await allowed.ask([Message.user("offline")])
    assert len(calls) == 1
    assert calls[0].max_retries == 0 and calls[0].tool_mode == "native"
    assert calls[0].vision == "off" and not calls[0].stream


async def test_chat_and_research_share_atomic_daily_request_admission(tmp_path: Path) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy(daily_requests=1))
    release = asyncio.Event()

    async def wait() -> LLMResponse:
        await release.wait()
        return LLMResponse(usage={"prompt_tokens": 100, "completion_tokens": 10})

    main, calls = client(limits, answer=wait, owner=UsageOwner("main"))
    research, research_calls = client(limits, owner=UsageOwner("research", run_id="run"))
    task = asyncio.create_task(main.ask([Message.user("hello")]))
    while not calls:
        await asyncio.sleep(0)
    with pytest.raises(UsageStopped, match="request allowance"):
        await research.ask([Message.user("compare")])
    assert research_calls == [] and limits.status()["pending_requests"] == 1
    release.set()
    await task
    assert limits.status()["requests"] == 1


async def test_full_vendor_context_is_reserved_not_prompt_byte_guess(tmp_path: Path) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy(task_tokens=CONTEXT_TOKENS))
    model, calls = client(limits, owner=UsageOwner("turn"))
    with pytest.raises(UsageStopped, match="token allowance"):
        await model.ask([Message.user("x")], max_tokens=1)
    assert calls == []


async def test_later_turn_and_policy_swap_do_not_reset_existing_task_spend(tmp_path: Path) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy(task_requests=1))
    first, calls = client(limits, owner=UsageOwner("turn-one", task_id="same-task"))
    await first.ask([Message.user("first")])
    limits.update_policy(policy(task_requests=1))
    second, second_calls = client(limits, owner=UsageOwner("turn-two", task_id="same-task"))
    with pytest.raises(UsageStopped, match="request allowance"):
        await second.ask([Message.user("continue")])
    assert len(calls) == 1 and second_calls == []


async def test_usage_unknown_survives_midnight_restart_and_policy_increase(tmp_path: Path) -> None:
    now = [1792195199.0]
    path = tmp_path / "usage.db"
    limits = UsageLimits(path, policy(), clock=lambda: now[0])
    slot = strict_llm_settings(LLMSettings(), limits.policy)
    old_id = limits.reserve(UsageOwner("original", task_id="task-a"), slot, 100)
    original_day = limits.requests()[0]["day"]
    now[0] += 2
    reopened = UsageLimits(path, policy(), clock=lambda: now[0])
    reopened.update_policy(policy().model_copy(update={"daily_cny": 1000, "task_cny": 1000}))
    model, calls = client(reopened, owner=UsageOwner("new", task_id="task-b"))
    with pytest.raises(UsageStopped, match="unknown model usage"):
        await model.ask([Message.user("new task")])
    assert calls == [] and reopened.status()["unknown_requests"] == 1
    # A late receipt reconciles only the original immutable owner, day and price.
    reopened.settle(old_id, {"prompt_tokens": 30, "completion_tokens": 20})
    row = reopened.requests()[0]
    assert row["owner"]["task_id"] == "task-a"
    assert row["day"] == original_day
    assert row["charged_cost"] == 30 * 2 + 20 * 8
    await model.ask([Message.user("new task")])
    assert reopened.requests()[-1]["owner"]["task_id"] == "task-b"


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"prompt_tokens": 1},
        {"prompt_tokens": -1, "completion_tokens": 2},
        {"prompt_tokens": True, "completion_tokens": 2},
    ],
)
async def test_incomplete_usage_blocks_next_request(tmp_path: Path, usage: dict[str, Any]) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy())

    async def incomplete() -> LLMResponse:
        return LLMResponse(content="answer", usage=usage)

    model, calls = client(limits, answer=incomplete, owner=UsageOwner("turn"))
    with pytest.raises(UsageStopped, match="incomplete or invalid usage"):
        await model.ask([Message.user("hello")])
    with pytest.raises(UsageStopped, match="unknown model usage"):
        await model.ask([Message.user("hello")])
    assert len(calls) == 1 and limits.status()["unknown_requests"] == 1


async def test_timeout_has_one_attempt_and_stops_retries(tmp_path: Path) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy(request_seconds=1))

    async def timeout() -> LLMResponse:
        raise TimeoutError("synthetic timeout")

    model, calls = client(limits, answer=timeout, owner=UsageOwner("turn"))
    for expected in ("outcome is unknown", "unknown model usage"):
        with pytest.raises(UsageStopped, match=expected):
            await model.ask([Message.user("hello")])
    assert len(calls) == 1


async def test_cancellation_preserves_unknown_reservation_and_shutdown_signal(
    tmp_path: Path,
) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy())
    entered = asyncio.Event()

    async def wait() -> LLMResponse:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    model, calls = client(limits, answer=wait, owner=UsageOwner("turn"))
    task = asyncio.create_task(model.ask([Message.user("hello")]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1 and limits.status()["unknown_requests"] == 1


async def test_real_adapter_retry_loop_makes_exactly_one_synthetic_sdk_attempt(
    tmp_path: Path,
) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy())
    model, _ = client(limits, owner=UsageOwner("turn"))
    delegate = model.delegate
    attempts = 0

    async def fail(**_: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise openai.APIConnectionError(
            request=httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        )

    delegate.client = SimpleNamespace(  # type: ignore[attr-defined]
        max_retries=0, chat=SimpleNamespace(completions=SimpleNamespace(create=fail))
    )
    delegate._send = OpenAIChatLLM._send.__get__(delegate, OpenAIChatLLM)  # type: ignore[attr-defined]
    with pytest.raises(UsageStopped, match="outcome is unknown"):
        await model.ask([Message.user("hello")])
    assert attempts == 1
    assert limits.status()["requests"] == limits.status()["unknown_requests"] == 1


async def test_native_adapter_settings_changes_and_visual_requests_fail_closed(
    tmp_path: Path,
) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy())
    model, calls = client(limits, owner=UsageOwner("turn"))
    with pytest.raises(UsageStopped, match="visual"):
        await model.ask([Message.user("look", images=["unread-fixture.png"])])
    model.delegate.settings.max_retries = 2  # type: ignore[attr-defined]
    with pytest.raises(UsageStopped, match="strict native"):
        await model.ask([Message.user("hello")])
    assert calls == [] and limits.status()["requests"] == 0


async def test_bound_reasoner_keeps_owner_after_context_changes(tmp_path: Path) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy())
    unbound, calls = client(limits)
    stale_reasoner = unbound.with_owner(
        UsageOwner("old-turn", task_id="old-task", run_id="old-run")
    )
    with usage_owner(UsageOwner("new-turn", task_id="new-task")):
        assert current_usage_owner().turn_id == "new-turn"
        await stale_reasoner.ask([Message.user("late work")])
    assert current_usage_owner() is None and len(calls) == 1
    assert limits.requests()[0]["owner"]["task_id"] == "old-task"
    assert limits.requests()[0]["owner"]["run_id"] == "old-run"


async def test_each_ask_complete_retry_counts_and_roomier_output_is_bounded(tmp_path: Path) -> None:
    limits = UsageLimits(tmp_path / "usage.db", policy())

    async def cut() -> LLMResponse:
        return LLMResponse(
            finish_reason="length", usage={"prompt_tokens": 10, "completion_tokens": 2}
        )

    model, calls = client(limits, answer=cut, owner=UsageOwner("turn"))
    with pytest.raises(UsageStopped, match="output token limit"):
        await model.ask_complete([Message.user("hello")])
    assert len(calls) == 1 and limits.status()["requests"] == 1


def test_gui_actions_and_deadline_survive_revision_restart_policy_swap(tmp_path: Path) -> None:
    now = [10.0]
    path = tmp_path / "usage.db"
    limits = UsageLimits(path, policy(gui_actions=2, gui_seconds=10), lambda: now[0])
    first = limits.reserve_gui("root-task", "revision-1:step-a")
    assert first["actions"] == 1
    assert limits.reserve_gui("root-task", "revision-1:step-a")["replayed"]
    reopened = UsageLimits(path, policy(gui_actions=100, gui_seconds=1000), lambda: now[0])
    second = reopened.reserve_gui("root-task", "revision-2:step-b")
    assert second["actions"] == 2 and second["deadline"] == 20
    with pytest.raises(UsageStopped, match="action allowance"):
        reopened.reserve_gui("root-task", "revision-3:step-c")
    now[0] = 21
    with pytest.raises(UsageStopped, match="deadline"):
        reopened.check_gui("root-task")
