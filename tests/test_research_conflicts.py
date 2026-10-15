from __future__ import annotations

import json
from typing import Any

import pytest

from nanomuse.llm.mock import MockLLM
from nanomuse.research.context import ResearchValidationError, build_context, normalize_context
from nanomuse.research.contracts import ConflictDraft
from nanomuse.research.reasoning import FixtureReasoner, ResearchReasoner
from nanomuse.research.reports import render_report
from nanomuse.schema import Function, LLMResponse, ToolCall

ALPHA = "https://alpha.example/project"
BETA = "https://beta.example/project"


def conflict_context() -> dict[str, Any]:
    snapshots = [
        {
            "id": "sa",
            "candidate": ALPHA,
            "url": ALPHA + "/README.md",
            "version": "readme-v1",
            "content": "Supports Windows.",
            "project_id": "project-a",
        },
        {
            "id": "sc",
            "candidate": ALPHA,
            "url": ALPHA + "/platform.md",
            "version": "platform-v1",
            "content": "Windows is not supported.",
            "project_id": "project-a",
        },
        {
            "id": "sb",
            "candidate": BETA,
            "url": BETA + "/README.md",
            "version": "beta-v1",
            "content": "Platforms are undocumented.",
            "project_id": "project-a",
        },
    ]
    return {
        "project_id": "project-a",
        "spec": {
            "title": "Platform comparison",
            "goal": "Compare documented Windows support",
            "candidates": [ALPHA, BETA],
            "constraints": [{"id": "windows", "text": "Must support Windows", "hard": True}],
        },
        "snapshots": snapshots,
        "evidence": [
            {
                "id": {"sa": "ea", "sc": "ec", "sb": "eb"}[item["id"]],
                "snapshot_id": item["id"],
                "candidate": item["candidate"],
                "dimension": "platform",
                "fact": item["content"],
                "quote": item["content"],
                "verification": "documentation",
                "status": "active",
                "source_url": item["url"],
                "version": item["version"],
                "project_id": "project-a",
            }
            for item in snapshots
        ],
        "decisions": [],
        "changes": [],
    }


def conflict() -> dict[str, Any]:
    return {
        "candidate": ALPHA,
        "dimension": "platform",
        "evidence_refs": ["ea", "ec"],
        "reason": "The current README declares Windows support and the platform page denies it.",
        "version_scope": "an unverified model version label",
    }


def proposed() -> dict[str, Any]:
    return {
        "summary": "Conflicting documentation requires verification.",
        "recommendation": "Defer selection until the platform conflict is verified.",
        "decisions": [
            {
                "candidate": ALPHA,
                "dimension": "platform",
                "conclusion": "Windows support conflicts.",
                "verdict": "unknown",
                "constraint_refs": ["windows"],
                "evidence_refs": ["ea"],
            },
            {
                "candidate": BETA,
                "dimension": "platform",
                "conclusion": "Support is undocumented.",
                "verdict": "unknown",
                "constraint_refs": ["windows"],
                "evidence_refs": [],
            },
        ],
        "conflicts": [conflict()],
        "unknowns": [],
        "next_steps": [],
    }


def response(value: Any) -> LLMResponse:
    return LLMResponse(content=json.dumps(value))


async def test_structured_conflict_binds_both_actual_source_versions() -> None:
    llm = MockLLM([response(proposed())])
    result = await ResearchReasoner(llm).compare(conflict_context())
    assert result.conflicts[0].evidence_refs == ["ea", "ec"]
    assert "ea: readme-v1 (sa)" in result.conflicts[0].version_scope
    assert "ec: platform-v1 (sc)" in result.conflicts[0].version_scope
    assert "unverified model version" not in result.conflicts[0].version_scope
    assert all(item.verdict == "unknown" for item in result.decisions)
    offered = next(
        item for item in llm.calls[0]["tools"] if item["function"]["name"] == "propose_comparison"
    )
    schema = offered["function"]["parameters"]
    assert "conflicts" in schema["$defs"]["Comparison"]["properties"]
    assert schema["$defs"]["ConflictDraft"]["properties"]["evidence_refs"]["minItems"] == 2


@pytest.mark.parametrize(
    "refs,match",
    [
        (["ea", "ea"], "distinct evidence"),
        (["ea", "missing"], "unknown evidence"),
        (["ea", "eb"], "another candidate"),
    ],
)
async def test_conflict_rejects_duplicate_missing_and_cross_candidate_refs(
    refs: list[str], match: str
) -> None:
    value = proposed()
    value["conflicts"][0]["evidence_refs"] = refs
    reasoner = ResearchReasoner(MockLLM([response(value)]), max_repairs=0)
    with pytest.raises(ResearchValidationError, match=match):
        await reasoner.compare(conflict_context())
    assert reasoner.calls[-1]["error_type"] == "evidence_validation"


@pytest.mark.parametrize("status", ["stale", "superseded", "conflict"])
async def test_conflict_requires_current_evidence(status: str) -> None:
    ctx = conflict_context()
    ctx["evidence"][1]["status"] = status
    with pytest.raises(ResearchValidationError, match="must be current"):
        await ResearchReasoner(MockLLM([response(proposed())]), max_repairs=0).compare(ctx)


async def test_conflict_rejects_evidence_from_another_project() -> None:
    ctx = conflict_context()
    ctx["evidence"][1]["project_id"] = "project-b"
    with pytest.raises(ResearchValidationError, match="another project"):
        await ResearchReasoner(MockLLM([response(proposed())]), max_repairs=0).compare(ctx)


async def test_conflict_rechecks_quote_and_snapshot_version() -> None:
    ctx = conflict_context()
    ctx["evidence"][1]["quote"] = "A fabricated platform fact."
    with pytest.raises(ResearchValidationError, match="not present"):
        await ResearchReasoner(MockLLM([response(proposed())]), max_repairs=0).compare(ctx)
    ctx = conflict_context()
    ctx["evidence"][1]["version"] = "invented-v999"
    with pytest.raises(ResearchValidationError, match="version does not match"):
        await ResearchReasoner(MockLLM([response(proposed())]), max_repairs=0).compare(ctx)


@pytest.mark.parametrize("verdict", ["meets", "fails"])
async def test_unresolved_conflict_rejects_definitive_verdicts(verdict: str) -> None:
    value = proposed()
    value["decisions"][0]["verdict"] = verdict
    with pytest.raises(ResearchValidationError, match="requires an unknown"):
        await ResearchReasoner(MockLLM([response(value)]), max_repairs=0).compare(
            conflict_context()
        )


async def test_conflicting_verdict_can_be_repaired_to_unknown() -> None:
    bad = proposed()
    bad["decisions"][0]["verdict"] = "meets"
    llm = MockLLM([response(bad), response(proposed())])
    reasoner = ResearchReasoner(llm)
    result = await reasoner.compare(conflict_context())
    assert result.decisions[0].verdict == "unknown" and len(llm.calls) == 2
    assert reasoner.calls[0]["status"] == "invalid_output"
    assert reasoner.calls[1]["status"] == "succeeded"


async def test_conflict_on_shared_constraint_cannot_be_bypassed_by_relabelling_dimension() -> None:
    value = proposed()
    value["decisions"].append(
        {
            "candidate": ALPHA,
            "dimension": "overall suitability",
            "conclusion": "Windows confirmed.",
            "verdict": "meets",
            "constraint_refs": ["windows"],
            "evidence_refs": ["ea"],
        }
    )
    with pytest.raises(ResearchValidationError, match="requires an unknown"):
        await ResearchReasoner(MockLLM([response(value)]), max_repairs=0).compare(
            conflict_context()
        )


async def test_registered_open_conflict_is_not_silently_dropped_by_new_comparison() -> None:
    ctx = conflict_context()
    ctx["conflicts"] = [
        conflict() | {"id": "conflict-a", "project_id": "project-a", "status": "open"}
    ]
    value = proposed() | {"conflicts": [], "decisions": []}
    result = await ResearchReasoner(MockLLM([response(value)])).compare(ctx)
    assert len(result.conflicts) == 1
    assert any(
        item.evidence_refs == ["ea", "ec"] and item.verdict == "unknown"
        for item in result.decisions
    )
    assert "platform-v1" in result.unknowns[0]


async def test_registered_foreign_conflict_is_rejected_even_with_local_refs() -> None:
    ctx = conflict_context()
    ctx["conflicts"] = [
        conflict() | {"id": "conflict-b", "project_id": "project-b", "status": "open"}
    ]
    with pytest.raises(
        ResearchValidationError, match="Registered conflict belongs to another project"
    ):
        await ResearchReasoner(MockLLM([response(proposed())]), max_repairs=0).compare(ctx)


def test_context_preserves_project_and_conflicts_and_old_contexts_default_to_empty() -> None:
    ctx = conflict_context()
    assert normalize_context(ctx)["conflicts"] == []
    ctx["conflicts"] = [conflict()]
    selected = build_context("compare", ctx)
    assert selected["project_id"] == "project-a" and selected["conflicts"] == [conflict()]


async def test_fixture_does_not_invent_semantic_conflicts_from_answer_labels() -> None:
    ctx = conflict_context()
    ctx["spec"]["mode"] = "fixture"
    ctx["evaluation_expected_conflict"] = True
    fixture = FixtureReasoner()
    result = await fixture.compare(ctx)
    assert result.conflicts == []
    ctx["conflicts"] = [conflict() | {"status": "open"}]
    replayed = await fixture.compare(ctx)
    assert len(replayed.conflicts) == 1
    assert all(item.verdict == "unknown" for item in replayed.decisions if item.candidate == ALPHA)


async def test_conflict_proposal_tool_uses_real_sentinel_and_the_new_schema() -> None:
    tool_call = ToolCall(
        function=Function(
            name="propose_comparison", arguments=json.dumps({"comparison": proposed()})
        )
    )
    llm = MockLLM([LLMResponse(tool_calls=[tool_call]), response(proposed())])
    reasoner = ResearchReasoner(llm)
    result = await reasoner.compare(conflict_context())
    assert result.conflicts[0].evidence_refs == ["ea", "ec"]
    assert reasoner.sentinel.audit.tail()[0]["tool"] == "propose_comparison"


async def test_report_keeps_both_sources_versions_and_exactly_six_sections() -> None:
    ctx = conflict_context()
    result = await ResearchReasoner(MockLLM([response(proposed())])).compare(ctx)
    markdown = render_report(ctx["spec"], result, ctx["evidence"])
    assert markdown.count("\n## ") == 6
    assert "Unresolved evidence conflict / 未解决的证据冲突" in markdown
    assert ALPHA + "/README.md" in markdown and ALPHA + "/platform.md" in markdown
    assert "readme-v1" in markdown and "platform-v1" in markdown
    assert "Windows is not supported." in markdown
    assert "Supports Windows." in markdown


def test_renderer_cannot_publish_a_definitive_conflicted_verdict() -> None:
    value = proposed()
    value["decisions"][0]["verdict"] = "fails"
    with pytest.raises(ResearchValidationError, match="requires an unknown"):
        render_report(conflict_context()["spec"], value, conflict_context()["evidence"])


def test_conflict_shape_does_not_accept_a_single_reference() -> None:
    with pytest.raises(ValueError):
        ConflictDraft.model_validate(conflict() | {"evidence_refs": ["ea"]})


async def test_uncertain_update_on_another_source_does_not_promote_an_existing_conflict() -> None:
    from nanomuse.research.contracts import ImpactJudgment
    from nanomuse.research.impact import evaluated_event

    context = conflict_context()
    comparison = await ResearchReasoner(MockLLM([response(proposed())])).compare(context)
    judgment = ImpactJudgment(
        change_summary="A different source changed.",
        affected_constraints=["windows"],
        impact_level="uncertain",
    )
    event = evaluated_event(
        {"after_snapshot": "sb"}, judgment, comparison, context["spec"], context["evidence"]
    )
    assert event["impact_level"] == "uncertain"
    assert "notification_policy" not in event and "model_judgment" not in event
    assert comparison.decisions[0].verdict == "unknown"
