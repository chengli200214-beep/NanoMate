from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanomuse.config import SentinelSettings
from nanomuse.llm.mock import MockLLM
from nanomuse.research.context import (
    ContextBudgetError,
    ResearchValidationError,
    build_context,
    normalize_context,
    validate_comparison,
    validate_evidence,
)
from nanomuse.research.contracts import Comparison, EvidenceDraft
from nanomuse.research.reasoning import (
    FixtureReasoner,
    ResearchCallLimitError,
    ResearchFormatError,
    ResearchModelError,
    ResearchReasoner,
    ResearchToolError,
)
from nanomuse.research.reports import diff_report, render_report
from nanomuse.research.tools import ResearchToolState, build_research_tools
from nanomuse.schema import Function, LLMResponse, ToolCall
from nanomuse.sentinel import Sentinel
from nanomuse.sentinel.audit import AuditLog
from nanomuse.ui import HeadlessUI

ALPHA = "https://code.example/alpha"
BETA = "https://code.example/beta"


def context() -> dict[str, Any]:
    return {
        "spec": {
            "title": "Desktop assistant",
            "goal": "Compare support for a Windows desktop",
            "candidates": [ALPHA, BETA],
            "constraints": [{"id": "windows", "text": "Must support Windows", "hard": True}],
        },
        "snapshots": [
            {
                "id": "sa",
                "url": ALPHA + "/README.md",
                "candidate": ALPHA,
                "content": "# Alpha\nSupports Windows.\nRequires a public relay.",
                "version": "commit-a",
            },
            {
                "id": "sb",
                "url": BETA + "/README.md",
                "candidate": BETA,
                "content": "# Beta\nOnly Linux is supported.",
                "version": "commit-b",
            },
        ],
        "evidence": [
            {
                "id": "ea",
                "snapshot_id": "sa",
                "candidate": ALPHA,
                "dimension": "windows",
                "fact": "The README declares Windows support",
                "quote": "Supports Windows.",
                "verification": "documentation",
                "status": "active",
                "source_url": ALPHA + "/README.md",
                "version": "commit-a",
                "fetched_at": "2026-10-15T00:00:00Z",
            },
            {
                "id": "eb",
                "snapshot_id": "sb",
                "candidate": BETA,
                "dimension": "windows",
                "fact": "The README restricts support to Linux",
                "quote": "Only Linux is supported.",
                "verification": "documentation",
                "status": "active",
                "source_url": BETA + "/README.md",
                "version": "commit-b",
                "fetched_at": "2026-10-15T00:00:00Z",
            },
        ],
        "decisions": [],
        "changes": [],
    }


def draft() -> dict[str, Any]:
    return {
        key: value
        for key, value in context()["evidence"][0].items()
        if key in EvidenceDraft.model_fields
    }


def comparison() -> dict[str, Any]:
    return {
        "summary": "Both current README snapshots were inspected.",
        "recommendation": "Select Alpha.",
        "decisions": [
            {
                "candidate": ALPHA,
                "dimension": "windows",
                "conclusion": "Windows is documented.",
                "verdict": "meets",
                "constraint_refs": ["windows"],
                "evidence_refs": ["ea"],
            },
            {
                "candidate": BETA,
                "dimension": "windows",
                "conclusion": "Windows is excluded.",
                "verdict": "fails",
                "constraint_refs": ["windows"],
                "evidence_refs": ["eb"],
            },
        ],
        "unknowns": [],
        "next_steps": ["Project owner: run a Windows startup check."],
    }


def response(value: Any, **kwargs: Any) -> LLMResponse:
    return LLMResponse(content=json.dumps(value), **kwargs)


def call(name: str, **kwargs: Any) -> ToolCall:
    return ToolCall(function=Function(name=name, arguments=json.dumps(kwargs)))


async def test_extract_checks_quote_and_records_reported_usage() -> None:
    llm = MockLLM(
        [
            response(
                [draft()],
                model="script-model",
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 9,
                    "total_tokens": 21,
                },
            )
        ]
    )
    reasoner = ResearchReasoner(llm)
    extracted = await reasoner.extract(context())
    assert extracted[0].quote == "Supports Windows."
    assert reasoner.calls[0]["id"].startswith("call_")
    assert {key: value for key, value in reasoner.calls[0].items() if key != "id"} | {
        "duration_ms": 0
    } == {
        "stage": "extract",
        "model": "script-model",
        "attempt": 1,
        "input_tokens": 12,
        "output_tokens": 9,
        "total_tokens": 21,
        "duration_ms": 0,
        "status": "succeeded",
        "error_type": None,
    }


async def test_missing_usage_remains_unknown() -> None:
    reasoner = ResearchReasoner(MockLLM([response([])]))
    await reasoner.extract(context())
    assert reasoner.calls[0]["input_tokens"] is None
    assert reasoner.calls[0]["output_tokens"] is None
    assert reasoner.calls[0]["total_tokens"] is None


async def test_nonexistent_quote_is_repaired_at_most_twice() -> None:
    bad = draft() | {"quote": "Runs without a relay."}
    llm = MockLLM([response([bad]) for _ in range(3)])
    reasoner = ResearchReasoner(llm)
    with pytest.raises(ResearchValidationError, match="not present"):
        await reasoner.extract(context())
    assert len(llm.calls) == len(reasoner.calls) == 3
    assert all(item["error_type"] == "evidence_validation" for item in reasoner.calls)


@pytest.mark.parametrize(
    "patch,match",
    [
        ({"candidate": BETA}, "same candidate"),
        ({"snapshot_id": "missing"}, "Unknown snapshot"),
        ({"verification": "source"}, "source-code observation"),
    ],
)
def test_evidence_rejects_cross_source_and_unearned_source_claim(
    patch: dict[str, Any], match: str
) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        validate_evidence(
            EvidenceDraft.model_validate(draft() | patch), normalize_context(context())
        )


def test_file_kind_does_not_turn_a_readme_into_code() -> None:
    ctx = context()
    ctx["snapshots"][0]["kind"] = "file"
    with pytest.raises(ResearchValidationError, match="source-code observation"):
        validate_evidence(
            EvidenceDraft.model_validate(draft() | {"verification": "source"}),
            normalize_context(ctx),
        )


def test_source_code_observation_is_accepted_for_source_file() -> None:
    ctx = context()
    ctx["snapshots"][0]["url"] = ALPHA + "/runtime.py"
    result = validate_evidence(
        EvidenceDraft.model_validate(draft() | {"verification": "source"}), normalize_context(ctx)
    )
    assert result.verification == "source"


async def test_candidate_trailing_slash_is_canonicalized() -> None:
    ctx = context()
    ctx["snapshots"][0]["candidate"] += "/"
    result = await ResearchReasoner(
        MockLLM([response([draft() | {"candidate": ALPHA + "/"}])])
    ).extract(ctx)
    assert result[0].candidate == ALPHA


@pytest.mark.parametrize("status", ["stale", "superseded", "conflict", "unrecognized"])
async def test_obsolete_and_unknown_status_cannot_support_decision(status: str) -> None:
    ctx = context()
    ctx["evidence"][0]["status"] = status
    with pytest.raises(ResearchValidationError, match="not current"):
        await ResearchReasoner(MockLLM([response(comparison())]), max_repairs=0).compare(ctx)


@pytest.mark.parametrize("verification", ["unknown", "inference"])
async def test_unknown_or_inferred_fact_cannot_establish_hard_requirement(
    verification: str,
) -> None:
    ctx = context()
    ctx["evidence"][0]["verification"] = verification
    with pytest.raises(ResearchValidationError, match="direct current evidence"):
        await ResearchReasoner(MockLLM([response(comparison())]), max_repairs=0).compare(ctx)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("evidence_refs", ["missing"], "Unknown evidence"),
        ("evidence_refs", ["eb"], "another candidate"),
        ("constraint_refs", ["invented"], "unknown constraint"),
    ],
)
async def test_comparison_rejects_malicious_references(field: str, value: Any, match: str) -> None:
    proposed = comparison()
    proposed["decisions"][0][field] = value
    with pytest.raises(ResearchValidationError, match=match):
        await ResearchReasoner(MockLLM([response(proposed)]), max_repairs=0).compare(context())


async def test_missing_hard_decisions_are_unknown_and_no_candidate_is_confirmed() -> None:
    proposed = comparison() | {"decisions": []}
    result = await ResearchReasoner(MockLLM([response(proposed)])).compare(context())
    assert len(result.decisions) == 2
    assert all(item.verdict == "unknown" for item in result.decisions)
    assert "No candidate is confirmed" in result.recommendation
    assert len(result.unknowns) == 2


async def test_supported_hard_verdict_preserves_explainable_recommendation() -> None:
    result = await ResearchReasoner(MockLLM([response(comparison())])).compare(context())
    assert result.recommendation == "Select Alpha."


async def test_valid_json_repair_is_counted_as_an_actual_call() -> None:
    llm = MockLLM([LLMResponse(content="not JSON"), response([draft()])])
    reasoner = ResearchReasoner(llm)
    result = await reasoner.extract(context())
    assert len(result) == 1 and len(llm.calls) == 2
    assert reasoner.calls[0]["error_type"] == "model_format"
    assert "Repair the JSON" in llm.calls[1]["messages"][-1].content


async def test_schema_failure_has_separate_error_category() -> None:
    reasoner = ResearchReasoner(MockLLM([response({"wrong": "shape"})]), max_repairs=0)
    with pytest.raises(ResearchFormatError):
        await reasoner.extract(context())
    assert reasoner.calls[-1]["error_type"] == "model_format"


async def test_truncated_json_is_not_accepted_as_complete() -> None:
    llm = MockLLM([response([], finish_reason="length"), response([draft()])])
    reasoner = ResearchReasoner(llm)
    result = await reasoner.extract(context())
    assert result and len(reasoner.calls) == 2


async def test_provider_error_is_classified_and_counted_without_logging_credentials() -> None:
    class ProviderFailure(RuntimeError):
        status_code = 401

    def fail(_: Any) -> LLMResponse:
        raise ProviderFailure("sensitive provider diagnostics")

    reasoner = ResearchReasoner(MockLLM([fail]))
    with pytest.raises(ResearchModelError) as caught:
        await reasoner.extract(context())
    assert caught.value.code == "authentication"
    assert not caught.value.retryable
    assert "sensitive provider diagnostics" not in str(caught.value)
    assert reasoner.calls[-1]["error_type"] == "authentication"
    assert reasoner.calls[-1]["input_tokens"] is None


@pytest.mark.parametrize("header,delay", [("7", 7.0), ("invalid", None), ("inf", None)])
async def test_provider_retry_after_is_forwarded_to_durable_scheduler(
    header: str,
    delay: float | None,
) -> None:
    class Limited(RuntimeError):
        status_code = 429
        response = SimpleNamespace(headers={"Retry-After": header})

    def fail(_: Any) -> LLMResponse:
        raise Limited("rate limited")

    reasoner = ResearchReasoner(MockLLM([fail]))
    with pytest.raises(ResearchModelError) as caught:
        await reasoner.extract(context())
    assert caught.value.retryable and caught.value.code == "rate_limit"
    assert caught.value.retry_after == delay
    assert len(reasoner.calls) == 1


async def test_injected_shell_call_is_unavailable() -> None:
    llm = MockLLM([LLMResponse(tool_calls=[call("shell", command="echo should-not-run")])])
    reasoner = ResearchReasoner(llm)
    with pytest.raises(ResearchToolError, match="not available"):
        await reasoner.extract(context())
    offered = {item["function"]["name"] for item in llm.calls[0]["tools"]}
    assert offered == {"read_source", "query_evidence", "submit_evidence", "propose_comparison"}
    assert reasoner.sentinel.audit.tail() == []


async def test_native_research_tool_goes_through_real_sentinel(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    gate = Sentinel(SentinelSettings(), audit, HeadlessUI())
    llm = MockLLM(
        [
            LLMResponse(tool_calls=[call("read_source", snapshot_id="sa")]),
            response([draft()]),
        ]
    )
    result = await ResearchReasoner(llm, gate).extract(context())
    assert result[0].snapshot_id == "sa"
    assert audit.tail()[0]["tool"] == "read_source"
    assert audit.tail()[0]["decision"] == "allow"
    assert gate.tainted


async def test_oversized_tool_batch_is_repaired_without_partial_execution() -> None:
    oversized = LLMResponse(tool_calls=[call("read_source", snapshot_id="sa") for _ in range(9)])
    llm = MockLLM(
        [oversized, LLMResponse(tool_calls=[call("read_source", snapshot_id="sa")]), response([])]
    )
    reasoner = ResearchReasoner(llm)
    assert await reasoner.extract(context()) == []
    assert len(reasoner.calls) == 3
    assert reasoner.calls[0]["status"] == "invalid_output"
    assert reasoner.calls[0]["error_type"] == "model_format"
    assert len(reasoner.sentinel.audit.tail()) == 1
    retry_messages = llm.calls[1]["messages"]
    assert "no tool ran" in retry_messages[-1].content
    assert not any(message.tool_calls for message in retry_messages)


async def test_tool_batch_and_json_errors_share_the_two_repair_allowance() -> None:
    oversized = LLMResponse(tool_calls=[call("read_source", snapshot_id="sa") for _ in range(9)])
    llm = MockLLM([oversized, response({"wrong": "shape"}), oversized, response([])])
    reasoner = ResearchReasoner(llm)
    with pytest.raises(ResearchFormatError, match="at most eight"):
        await reasoner.extract(context())
    assert len(reasoner.calls) == len(llm.calls) == 3
    assert reasoner.sentinel.audit.tail() == []


async def test_eight_tool_calls_remain_supported() -> None:
    llm = MockLLM(
        [
            LLMResponse(tool_calls=[call("read_source", snapshot_id="sa") for _ in range(8)]),
            response([]),
        ]
    )
    reasoner = ResearchReasoner(llm)
    await reasoner.extract(context())
    assert len(reasoner.sentinel.audit.tail()) == 8
    assert len(reasoner.calls) == 2


async def test_sentinel_deny_does_not_execute_tool(tmp_path: Path) -> None:
    gate = Sentinel(
        SentinelSettings(deny_tools=["read_source"]),
        AuditLog(tmp_path / "audit.jsonl"),
        HeadlessUI(),
    )
    llm = MockLLM([LLMResponse(tool_calls=[call("read_source", snapshot_id="sa")]), response([])])
    await ResearchReasoner(llm, gate).extract(context())
    assert gate.audit.tail()[0]["decision"] == "deny"
    assert "Supports Windows" not in llm.calls[1]["messages"][-1].content


async def test_tools_never_treat_url_as_permission_to_fetch() -> None:
    state = ResearchToolState(context())
    result = await build_research_tools(state).execute(
        "read_source", {"snapshot_id": "https://evil.example"}
    )
    assert not result.ok
    result = await build_research_tools(state).execute("read_source", {"url": ALPHA})
    assert not result.ok


async def test_submit_evidence_is_a_validated_in_memory_proposal() -> None:
    state = ResearchToolState(context())
    result = await build_research_tools(state).execute("submit_evidence", {"evidence": draft()})
    assert result.ok and json.loads(result.output)["persisted"] is False
    assert len(state.drafts) == 1
    assert len(state.context["evidence"]) == 2
    invalid = draft() | {"quote": "Invented fact"}
    rejected = await build_research_tools(state).execute("submit_evidence", {"evidence": invalid})
    assert not rejected.ok and len(state.drafts) == 1


async def test_proposed_comparison_is_validated_before_return() -> None:
    state = ResearchToolState(context())
    result = await build_research_tools(state).execute(
        "propose_comparison", {"comparison": comparison()}
    )
    assert result.ok and state.comparison is not None


async def test_repeated_tool_turns_have_bounded_model_call_count() -> None:
    llm = MockLLM([LLMResponse(tool_calls=[call("query_evidence")]) for _ in range(3)])
    reasoner = ResearchReasoner(llm, max_calls=2)
    with pytest.raises(ResearchCallLimitError):
        await reasoner.extract(context())
    assert len(reasoner.calls) == 2


def test_stage_context_keeps_full_requirements_and_records_omitted_sources() -> None:
    ctx = context()
    ctx["snapshots"][0]["content"] = "a" * 50_000
    selected = build_context("extract", ctx, budget_chars=3000)
    assert selected["spec"] == ctx["spec"] | {"mode": "live"}
    assert "snapshots:sa" in selected["context_gaps"]
    assert selected["snapshots"][0]["id"] == "sb"
    assert len(json.dumps(selected, ensure_ascii=False)) <= 3000


def test_mandatory_requirements_do_not_get_silently_truncated() -> None:
    with pytest.raises(ContextBudgetError):
        build_context("compare", context(), budget_chars=50)


def test_compare_context_omits_unrelated_source_bodies_but_keeps_quotes() -> None:
    selected = build_context("compare", context())
    assert "content" not in selected["snapshots"][0]
    assert selected["evidence"][0]["quote"] == "Supports Windows."


async def test_fixture_is_explicitly_offline_and_uses_current_snapshot_only() -> None:
    ctx = context()
    ctx["spec"]["mode"] = "fixture"
    fixture = FixtureReasoner()
    first = await fixture.extract(ctx)
    assert first[0].quote == "Supports Windows.\nRequires a public relay."
    ctx["snapshots"][0]["content"] = "# Alpha\nSupports Linux only."
    second = await fixture.extract(ctx)
    assert second[0].quote == "Supports Linux only."
    result = await fixture.compare(context() | {"spec": ctx["spec"]})
    assert "Offline scripted fixture" in result.summary
    assert all(item.verdict == "unknown" for item in result.decisions if item.constraint_refs)
    assert all(item["model"] == "mock" for item in fixture.calls)


async def test_fixture_refuses_to_appear_as_live_reasoning() -> None:
    with pytest.raises(ResearchValidationError, match="spec.mode=fixture"):
        await FixtureReasoner().extract(context())


async def test_impact_rejects_invented_dependency() -> None:
    value = {
        "change_summary": "New dependency",
        "impact_level": "uncertain",
        "affected_decisions": ["invented"],
    }
    with pytest.raises(ResearchValidationError, match="unknown decision"):
        await ResearchReasoner(MockLLM([response(value)]), max_repairs=0).impact(context())


async def test_fixture_impact_does_not_claim_updated_recommendation_without_evidence() -> None:
    ctx = context()
    ctx["spec"]["mode"] = "fixture"
    ctx["decisions"] = [comparison()["decisions"][0] | {"id": "da"}]
    before = ctx["snapshots"][0]
    after = before | {"content": "Supports Linux only.", "version": "commit-next"}
    ctx["changes"] = [
        {
            "id": "change-a",
            "source_id": "source-a",
            "before": before,
            "after": after,
            "change_summary": "Platform requirement changed",
        }
    ]
    result = await FixtureReasoner().impact(ctx)
    assert result.impact_level == "uncertain"
    assert result.affected_decisions == ["da"]
    assert result.affected_constraints == ["windows"]


def test_report_has_six_sections_and_version_bound_evidence() -> None:
    ctx = context()
    ctx["spec"]["mode"] = "fixture"
    rendered = render_report(ctx["spec"], comparison(), ctx["evidence"])
    assert rendered.count("\n## ") == 6
    assert "离线脚本回放" in rendered
    assert "commit-a" in rendered and "Snapshot / 快照: sa" in rendered
    assert ALPHA + "/README.md" in rendered
    assert "Documentation claim / 文档声明" in rendered
    assert "not runtime tests" in rendered
    assert "Supports Windows." in rendered


def test_report_escapes_source_html_and_rejects_unsafe_source_links() -> None:
    ctx = context()
    ctx["evidence"][0]["fact"] = "<script>alert(1)</script> | forged column"
    ctx["evidence"][0]["source_url"] = "javascript:alert(1)"
    rendered = render_report(ctx["spec"], comparison(), ctx["evidence"])
    assert "<script>" not in rendered and "javascript:" not in rendered
    assert "&lt;script&gt;" in rendered and "\\| forged column" in rendered


def test_report_rejects_cross_candidate_citation() -> None:
    proposed = comparison()
    proposed["decisions"][0]["evidence_refs"] = ["eb"]
    with pytest.raises(ResearchValidationError):
        render_report(context()["spec"], proposed, context()["evidence"])


def test_report_difference_preserves_before_and_after_changes() -> None:
    assert diff_report("same\n", "same\n") == ""
    delta = diff_report("old\n", "new\n")
    assert "--- previous.md" in delta and "+++ current.md" in delta
    assert "-old" in delta and "+new" in delta


def test_input_records_are_not_mutated_by_validation() -> None:
    original = context()
    copied = deepcopy(original)
    validate_comparison(Comparison.model_validate(comparison()), normalize_context(original))
    assert original == copied
