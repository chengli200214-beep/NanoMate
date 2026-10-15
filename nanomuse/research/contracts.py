"""Research contracts v1. Changes belong to the integration owner."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 2
CONTRACT_VERSION = "research-v1"
STAGES = ("collect", "extract", "compare", "publish")
RUN_TERMINAL = {"succeeded", "failed", "cancelled"}
STEP_TERMINAL = {"succeeded", "cancelled", "superseded"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Constraint(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1000)
    hard: bool = True


class ProjectSpec(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=10000)
    candidates: list[str] = Field(min_length=2, max_length=3)
    constraints: list[Constraint] = Field(default_factory=list, max_length=30)
    mode: Literal["live", "fixture"] = "live"


class SourceInput(StrictModel):
    candidate: str = Field(default="", max_length=300)
    kind: Literal["readme", "file", "releases", "metadata", "document", "manual"] = "readme"
    url: str = Field(default="", max_length=2000)
    content: str = Field(default="", max_length=200000)
    title: str = Field(default="", max_length=300)


class FetchedSource(StrictModel):
    url: str
    title: str = ""
    content: str
    version: str = ""
    etag: str = ""
    fetched_at: str = ""
    not_modified: bool = False


@runtime_checkable
class SourceFetcher(Protocol):
    async def fetch(
        self, source: dict[str, Any], previous: dict[str, Any] | None = None
    ) -> FetchedSource: ...


class EvidenceDraft(StrictModel):
    snapshot_id: str
    candidate: str
    dimension: str
    fact: str
    quote: str = Field(min_length=1, max_length=2000)
    locator: str = ""
    verification: Literal["documentation", "source", "inference", "unknown", "user_correction"] = (
        "documentation"
    )


class DecisionDraft(StrictModel):
    candidate: str
    dimension: str
    conclusion: str
    verdict: Literal["meets", "fails", "unknown", "preference"] = "unknown"
    constraint_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class ConflictDraft(StrictModel):
    candidate: str
    dimension: str
    evidence_refs: list[str] = Field(min_length=2, max_length=20)
    reason: str = Field(min_length=1, max_length=2000)
    version_scope: str = Field(default="", max_length=1000)


class Comparison(StrictModel):
    summary: str
    recommendation: str
    decisions: list[DecisionDraft]
    conflicts: list[ConflictDraft] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class ImpactJudgment(StrictModel):
    change_summary: str
    affected_constraints: list[str] = Field(default_factory=list)
    affected_decisions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    impact_level: Literal["irrelevant", "related", "recommendation", "uncertain"]
    recommended_action: str = ""


class Clock(Protocol):
    def now(self) -> float: ...


class Reasoner(Protocol):
    calls: list[dict[str, Any]]

    async def extract(self, context: dict[str, Any]) -> list[EvidenceDraft]: ...

    async def compare(self, context: dict[str, Any]) -> Comparison: ...

    async def impact(self, context: dict[str, Any]) -> ImpactJudgment: ...
