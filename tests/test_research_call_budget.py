from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from nanomuse.llm.mock import MockLLM
from nanomuse.research.reasoning import (
    FixtureReasoner,
    ResearchCallLimitError,
    ResearchModelError,
    ResearchReasoner,
)
from nanomuse.schema import Function, LLMResponse, ToolCall


def context() -> dict[str, Any]:
    return {
        "spec": {
            "title": "Call budget",
            "goal": "Bound provider requests",
            "candidates": ["https://alpha.example", "https://beta.example"],
        },
        "snapshots": [],
        "evidence": [],
        "decisions": [],
        "changes": [],
    }


def comparison_response() -> LLMResponse:
    return LLMResponse(
        content=json.dumps(
            {
                "summary": "No evidence",
                "recommendation": "Defer",
                "decisions": [],
                "unknowns": ["No sources inspected"],
            }
        )
    )


def impact_response() -> LLMResponse:
    return LLMResponse(
        content=json.dumps(
            {"change_summary": "No verified material change", "impact_level": "uncertain"}
        )
    )


async def test_shared_budget_covers_extract_compare_and_multiple_publish_impacts() -> None:
    llm = MockLLM(
        [LLMResponse(content="[]"), comparison_response(), impact_response(), impact_response()]
    )
    reasoner = ResearchReasoner(llm)
    reasoner.set_call_budget(3)
    await reasoner.extract(context())
    await reasoner.compare(context())
    await reasoner.impact(context())
    with pytest.raises(ResearchCallLimitError, match="shared research run") as caught:
        await reasoner.impact(context())
    assert caught.value.code == "model_budget"
    assert [item["stage"] for item in reasoner.calls] == ["extract", "compare", "impact"]
    assert len(llm.calls) == 3 and len(llm.script) == 1


async def test_budget_can_be_reset_from_persisted_remaining_allowance() -> None:
    llm = MockLLM([LLMResponse(content="[]"), comparison_response()])
    reasoner = ResearchReasoner(llm)
    reasoner.set_call_budget(1)
    await reasoner.extract(context())
    with pytest.raises(ResearchCallLimitError):
        await reasoner.compare(context())
    reasoner.set_call_budget(1)
    result = await reasoner.compare(context())
    assert result.recommendation == "Defer" and len(llm.calls) == 2


async def test_zero_budget_never_calls_provider_or_logs_an_invented_request() -> None:
    llm = MockLLM([LLMResponse(content="[]")])
    reasoner = ResearchReasoner(llm)
    reasoner.set_call_budget(0)
    with pytest.raises(ResearchCallLimitError):
        await reasoner.extract(context())
    assert llm.calls == [] and reasoner.calls == [] and len(llm.script) == 1


async def test_json_repairs_consume_shared_allowance() -> None:
    llm = MockLLM([LLMResponse(content="not JSON"), LLMResponse(content="[]")])
    reasoner = ResearchReasoner(llm)
    reasoner.set_call_budget(1)
    with pytest.raises(ResearchCallLimitError):
        await reasoner.extract(context())
    assert len(llm.calls) == len(reasoner.calls) == 1
    assert reasoner.calls[0]["status"] == "invalid_output"


async def test_failed_provider_request_also_consumes_shared_allowance() -> None:
    def fail(_: Any) -> LLMResponse:
        raise TimeoutError("Scripted provider timeout")

    llm = MockLLM([fail, LLMResponse(content="[]")])
    reasoner = ResearchReasoner(llm)
    reasoner.set_call_budget(1)
    with pytest.raises(ResearchModelError):
        await reasoner.extract(context())
    with pytest.raises(ResearchCallLimitError):
        await reasoner.extract(context())
    assert len(llm.calls) == len(reasoner.calls) == 1
    assert reasoner.calls[0]["status"] == "failed"


async def test_tool_turns_consume_budget_and_stage_ceiling_remains_independent() -> None:
    tool = ToolCall(function=Function(name="query_evidence", arguments="{}"))
    llm = MockLLM([LLMResponse(tool_calls=[tool]), comparison_response()])
    reasoner = ResearchReasoner(llm, max_calls=1)
    reasoner.set_call_budget(3)
    with pytest.raises(ResearchCallLimitError, match="extract stage"):
        await reasoner.extract(context())
    result = await reasoner.compare(context())
    assert result.summary == "No evidence" and len(llm.calls) == 2


async def test_fixture_does_not_leave_a_stale_queued_response_after_budget_exhaustion() -> None:
    fixture = FixtureReasoner()
    ctx = context()
    ctx["spec"]["mode"] = "fixture"
    fixture.set_call_budget(1)
    await fixture.extract(ctx)
    with pytest.raises(ResearchCallLimitError):
        await fixture.compare(ctx)
    assert isinstance(fixture.llm, MockLLM) and fixture.llm.script == []
    fixture.set_call_budget(1)
    result = await fixture.compare(ctx)
    assert "Offline scripted fixture" in result.summary
    assert len(fixture.calls) == 2


@pytest.mark.parametrize("remaining", [-1, True, 1.5, "2"])
def test_invalid_budget_is_rejected(remaining: Any) -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        ResearchReasoner(MockLLM()).set_call_budget(remaining)


async def test_observer_reserves_before_provider_and_updates_the_same_unique_id() -> None:
    events: list[dict[str, Any]] = []

    def answer(_: Any) -> LLMResponse:
        assert len(events) == 1 and events[0]["status"] == "running"
        assert events[0]["input_tokens"] is None
        events.append({"provider_called": True})
        return LLMResponse(content="[]", usage={"prompt_tokens": 7, "completion_tokens": 3})

    reasoner = ResearchReasoner(MockLLM([answer]))
    reasoner.set_call_observer(lambda entry: events.append(entry))
    await reasoner.extract(context())
    assert len(events) == 3 and events[1] == {"provider_called": True}
    assert events[2]["status"] == "succeeded"
    assert events[0]["id"] == events[2]["id"] == reasoner.calls[0]["id"]
    assert uuid.UUID(reasoner.calls[0]["id"].removeprefix("call_"))
    assert events[2]["input_tokens"] == 7 and len(reasoner.calls) == 1


async def test_observer_reservation_failure_prevents_provider_request() -> None:
    reserved: list[dict[str, Any]] = []

    def unavailable(entry: dict[str, Any]) -> None:
        reserved.append(entry)
        raise OSError("Reservation storage unavailable")

    llm = MockLLM([LLMResponse(content="[]")])
    reasoner = ResearchReasoner(llm)
    reasoner.set_call_budget(1)
    reasoner.set_call_observer(unavailable)
    with pytest.raises(OSError, match="Reservation storage"):
        await reasoner.extract(context())
    assert llm.calls == [] and reasoner.calls == []
    assert reserved[0]["status"] == "running"
    reasoner.set_call_observer(None)
    with pytest.raises(ResearchCallLimitError):
        await reasoner.extract(context())


async def test_observer_final_storage_failure_propagates_with_actual_call_retained() -> None:
    events: list[dict[str, Any]] = []

    def observer(entry: dict[str, Any]) -> None:
        events.append(entry)
        if entry["status"] != "running":
            raise OSError("Final call update unavailable")

    llm = MockLLM([LLMResponse(content="[]")])
    reasoner = ResearchReasoner(llm)
    reasoner.set_call_observer(observer)
    with pytest.raises(OSError, match="Final call update"):
        await reasoner.extract(context())
    assert len(llm.calls) == len(reasoner.calls) == 1
    assert reasoner.calls[0]["status"] == "succeeded"
    assert events[0]["id"] == events[1]["id"]


async def test_observer_keeps_failed_provider_requests_under_the_reserved_id() -> None:
    events: list[dict[str, Any]] = []

    def fail(_: Any) -> LLMResponse:
        raise TimeoutError("Scripted provider timeout")

    reasoner = ResearchReasoner(MockLLM([fail]))
    reasoner.set_call_observer(lambda entry: events.append(entry))
    with pytest.raises(ResearchModelError):
        await reasoner.extract(context())
    assert [item["status"] for item in events] == ["running", "failed"]
    assert events[0]["id"] == events[1]["id"] == reasoner.calls[0]["id"]


async def test_interrupted_provider_request_finishes_as_unknown_not_a_forgotten_call() -> None:
    events: list[dict[str, Any]] = []

    def interrupted(_: Any) -> LLMResponse:
        raise asyncio.CancelledError()

    reasoner = ResearchReasoner(MockLLM([interrupted]))
    reasoner.set_call_observer(lambda entry: events.append(entry))
    with pytest.raises(asyncio.CancelledError):
        await reasoner.extract(context())
    assert [item["status"] for item in events] == ["running", "unknown"]
    assert events[-1]["error_type"] == "interrupted"
    assert len(reasoner.calls) == 1


async def test_budget_rejection_does_not_create_an_observer_reservation() -> None:
    events: list[dict[str, Any]] = []
    reasoner = ResearchReasoner(MockLLM())
    reasoner.set_call_budget(0)
    reasoner.set_call_observer(lambda entry: events.append(entry))
    with pytest.raises(ResearchCallLimitError):
        await reasoner.extract(context())
    assert events == [] and reasoner.calls == []


async def test_each_request_gets_its_own_id_and_observer_can_be_cleared() -> None:
    events: list[dict[str, Any]] = []
    reasoner = ResearchReasoner(MockLLM([LLMResponse(content="[]"), comparison_response()]))
    reasoner.set_call_observer(lambda entry: events.append(entry))
    await reasoner.extract(context())
    reasoner.set_call_observer(None)
    await reasoner.compare(context())
    assert len(events) == 2 and len(reasoner.calls) == 2
    assert reasoner.calls[0]["id"] != reasoner.calls[1]["id"]
