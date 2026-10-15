"""Four project-scoped research tools, with no general chat capabilities."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, ValidationError

from nanomuse.research.context import (
    ResearchValidationError,
    normalize_context,
    validate_comparison,
    validate_evidence,
)
from nanomuse.research.contracts import Comparison, EvidenceDraft, StrictModel
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, ToolCollection


@dataclass
class ResearchToolState:
    context: dict[str, Any]
    drafts: list[EvidenceDraft] = field(default_factory=list)
    comparison: Comparison | None = None

    def __post_init__(self) -> None:
        self.context = normalize_context(self.context)


class ReadSourceArgs(StrictModel):
    snapshot_id: str = Field(min_length=1)


class QueryEvidenceArgs(StrictModel):
    candidate: str = ""
    dimension: str = ""
    status: str = ""


class SubmitEvidenceArgs(StrictModel):
    evidence: EvidenceDraft


class ProposeComparisonArgs(StrictModel):
    comparison: Comparison


class _ResearchTool(BaseTool):
    state: ResearchToolState = Field(exclude=True, repr=False)
    risk: RiskLevel = RiskLevel.SAFE
    # Imported project sources may contain information the user supplied privately.
    reads_private_data: bool = True
    egress: bool = False


class ReadSource(_ResearchTool):
    name: str = "read_source"
    description: str = "Read one registered source snapshot by ID. Does not fetch URLs."
    parameters: dict[str, Any] = ReadSourceArgs.model_json_schema()

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            args = ReadSourceArgs.model_validate(kwargs)
            snapshot = next(
                (
                    item
                    for item in self.state.context["snapshots"]
                    if item["id"] == args.snapshot_id
                ),
                None,
            )
            if snapshot is None:
                return ToolResult.fail("Unknown registered snapshot ID")
            return ToolResult(output=json.dumps(snapshot, ensure_ascii=False))
        except ValidationError as exc:
            return ToolResult.fail(f"Invalid read_source arguments: {exc}")


class QueryEvidence(_ResearchTool):
    name: str = "query_evidence"
    description: str = "Query this project's evidence by candidate, dimension or status."
    parameters: dict[str, Any] = QueryEvidenceArgs.model_json_schema()

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            args = QueryEvidenceArgs.model_validate(kwargs)
            if args.candidate and args.candidate not in self.state.context["spec"]["candidates"]:
                return ToolResult.fail("Unknown project candidate")
            items = [
                item
                for item in self.state.context["evidence"]
                if all(
                    not getattr(args, key) or item.get(key) == getattr(args, key)
                    for key in ("candidate", "dimension", "status")
                )
            ]
            return ToolResult(output=json.dumps(items, ensure_ascii=False))
        except ValidationError as exc:
            return ToolResult.fail(f"Invalid query_evidence arguments: {exc}")


class SubmitEvidence(_ResearchTool):
    name: str = "submit_evidence"
    description: str = (
        "Validate a quoted evidence draft against its snapshot. Returns an in-memory draft; "
        "the orchestrator controls persistence."
    )
    parameters: dict[str, Any] = SubmitEvidenceArgs.model_json_schema()

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            args = SubmitEvidenceArgs.model_validate(kwargs)
            draft = validate_evidence(args.evidence, self.state.context)
            self.state.drafts.append(draft)
            return ToolResult(
                output=json.dumps(
                    {"draft": draft.model_dump(), "persisted": False}, ensure_ascii=False
                )
            )
        except (ValidationError, ResearchValidationError) as exc:
            return ToolResult.fail(f"Evidence draft rejected: {exc}")


class ProposeComparison(_ResearchTool):
    name: str = "propose_comparison"
    description: str = (
        "Validate project-scoped comparison references and hard requirements. Returns a draft; "
        "conflicts must bind both evidence sources and keep affected verdicts unknown. "
        "Does not publish a report or send notifications."
    )
    parameters: dict[str, Any] = ProposeComparisonArgs.model_json_schema()

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            args = ProposeComparisonArgs.model_validate(kwargs)
            comparison = validate_comparison(args.comparison, self.state.context)
            self.state.comparison = comparison
            return ToolResult(output=comparison.model_dump_json())
        except (ValidationError, ResearchValidationError) as exc:
            return ToolResult.fail(f"Comparison draft rejected: {exc}")


def build_research_tools(state: ResearchToolState) -> ToolCollection:
    """Register only these tools. The caller must execute each through Sentinel.guard."""
    return ToolCollection(
        ReadSource(state=state),
        QueryEvidence(state=state),
        SubmitEvidence(state=state),
        ProposeComparison(state=state),
    )


__all__ = [
    "ProposeComparison",
    "QueryEvidence",
    "ReadSource",
    "ResearchToolState",
    "SubmitEvidence",
    "build_research_tools",
]
