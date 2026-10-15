"""Stage context selection and checks against project-scoped source snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

from nanomuse.research.contracts import Comparison, DecisionDraft, EvidenceDraft, ProjectSpec

CURRENT_STATUSES = frozenset({"active"})
DIRECT_VERIFICATIONS = frozenset({"documentation", "source"})
_SOURCE_EXTENSIONS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".kt",
    ".java",
    ".swift",
    ".go",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".cs",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
)


class ResearchValidationError(ValueError):
    """A structurally valid model result does not match the registered evidence."""

    code = "evidence_validation"


class ContextBudgetError(ValueError):
    """Mandatory project requirements cannot fit without being silently discarded."""

    code = "context_budget"


def record(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    raise ResearchValidationError("Expected an object in the research context")


def normalize_context(context: dict[str, Any]) -> dict[str, Any]:
    """Copy the service boundary without accepting executable callbacks as prompt data."""
    spec = ProjectSpec.model_validate(record(context.get("spec", {})))
    result: dict[str, Any] = {"spec": spec.model_dump(mode="json")}
    for name in ("snapshots", "evidence", "decisions", "changes"):
        values = context.get(name, [])
        if not isinstance(values, list):
            raise ResearchValidationError(f"{name} must be an array")
        result[name] = [record(item) for item in values]
    candidates = {item.rstrip("/"): item for item in spec.candidates}
    for name in ("snapshots", "evidence", "decisions"):
        for item in result[name]:
            candidate = str(item.get("candidate", ""))
            if candidate.rstrip("/") in candidates:
                item["candidate"] = candidates[candidate.rstrip("/")]
    for name in ("snapshots", "evidence", "decisions"):
        ids = [str(item.get("id", "")) for item in result[name]]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ResearchValidationError(f"{name} requires unique, nonempty IDs")
    return result


def evidence_is_current(evidence: dict[str, Any]) -> bool:
    return evidence.get("status", "active") in CURRENT_STATUSES


def source_observation(snapshot: dict[str, Any]) -> bool:
    path = urlsplit(str(snapshot.get("url", ""))).path.lower()
    return snapshot.get("kind") == "source" or path.endswith(_SOURCE_EXTENSIONS)


def validate_evidence(draft: EvidenceDraft, context: dict[str, Any]) -> EvidenceDraft:
    snapshots = {str(item["id"]): item for item in context.get("snapshots", [])}
    snapshot = snapshots.get(draft.snapshot_id)
    if snapshot is None:
        raise ResearchValidationError(f"Unknown snapshot: {draft.snapshot_id}")
    candidates = context["spec"]["candidates"]
    canonical = {item.rstrip("/"): item for item in candidates}
    draft = draft.model_copy(
        update={"candidate": canonical.get(draft.candidate.rstrip("/"), draft.candidate)}
    )
    if draft.candidate not in candidates or draft.candidate != snapshot.get("candidate"):
        raise ResearchValidationError("Evidence and snapshot must belong to the same candidate")
    if not draft.fact.strip() or not draft.dimension.strip():
        raise ResearchValidationError("Evidence requires a nonempty fact and dimension")
    content = str(snapshot.get("content", ""))
    if not draft.quote.strip() or draft.quote not in content:
        raise ResearchValidationError("Evidence quote is not present in the bound snapshot")
    if draft.verification == "source" and not source_observation(snapshot):
        raise ResearchValidationError("A documentation snapshot is not a source-code observation")
    return draft


def validate_comparison(comparison: Comparison, context: dict[str, Any]) -> Comparison:
    spec = ProjectSpec.model_validate(context["spec"])
    evidence = {str(item["id"]): item for item in context.get("evidence", [])}
    constraints = {item.id: item for item in spec.constraints}
    checked: list[DecisionDraft] = []
    for decision in comparison.decisions:
        canonical = {item.rstrip("/"): item for item in spec.candidates}
        decision = decision.model_copy(
            update={"candidate": canonical.get(decision.candidate.rstrip("/"), decision.candidate)}
        )
        if decision.candidate not in spec.candidates:
            raise ResearchValidationError(f"Unknown candidate: {decision.candidate}")
        if not decision.dimension.strip() or not decision.conclusion.strip():
            raise ResearchValidationError("Decision requires a dimension and conclusion")
        if any(ref not in constraints for ref in decision.constraint_refs):
            raise ResearchValidationError("Decision refers to an unknown constraint")
        support: list[dict[str, Any]] = []
        for ref in decision.evidence_refs:
            item = evidence.get(ref)
            if item is None:
                raise ResearchValidationError(f"Unknown evidence reference: {ref}")
            if item.get("candidate") != decision.candidate:
                raise ResearchValidationError("Decision evidence belongs to another candidate")
            if not evidence_is_current(item):
                raise ResearchValidationError(f"Decision uses evidence that is not current: {ref}")
            validate_evidence(
                EvidenceDraft.model_validate(
                    {key: item[key] for key in EvidenceDraft.model_fields if key in item}
                ),
                context,
            )
            support.append(item)
        if decision.verdict in {"meets", "fails"}:
            if not support or not any(
                item.get("verification") in DIRECT_VERIFICATIONS for item in support
            ):
                raise ResearchValidationError(
                    "A definitive verdict requires direct current evidence"
                )
            if any(constraints[ref].hard for ref in decision.constraint_refs) and any(
                item.get("verification") == "unknown" for item in support
            ):
                raise ResearchValidationError("Unknown evidence cannot settle a hard constraint")
        checked.append(decision)
    unknowns = list(comparison.unknowns)
    for candidate in spec.candidates:
        for constraint in spec.constraints:
            if not constraint.hard:
                continue
            matching = [
                item
                for item in checked
                if item.candidate == candidate and constraint.id in item.constraint_refs
            ]
            if not matching:
                checked.append(
                    DecisionDraft(
                        candidate=candidate,
                        dimension=constraint.id,
                        conclusion="This hard constraint has not been established by current evidence.",
                        verdict="unknown",
                        constraint_refs=[constraint.id],
                    )
                )
                unknowns.append(f"{candidate}: {constraint.text}")
    recommendation = comparison.recommendation
    hard_ids = {item.id for item in spec.constraints if item.hard}
    if hard_ids and not any(
        all(
            any(
                item.candidate == candidate
                and ref in item.constraint_refs
                and item.verdict == "meets"
                for item in checked
            )
            and not any(
                item.candidate == candidate
                and ref in item.constraint_refs
                and item.verdict in {"fails", "unknown"}
                for item in checked
            )
            for ref in hard_ids
        )
        for candidate in spec.candidates
    ):
        recommendation = (
            "No candidate is confirmed to meet every hard constraint. "
            "Complete the unresolved checks before making a selection."
        )
    return comparison.model_copy(
        update={
            "decisions": checked,
            "unknowns": list(dict.fromkeys(unknowns)),
            "recommendation": recommendation,
        }
    )


def build_context(
    stage: str, context: dict[str, Any], budget_chars: int = 48_000
) -> dict[str, Any]:
    """Keep requirements intact and record omissions instead of cutting quotes in half.

    Complete snapshots remain addressable through read_source. Comparisons only receive
    source excerpts needed to validate their selected evidence, not unrelated source bodies.
    The budget is a character budget, not an estimate of billable tokens.
    """
    normalized = normalize_context(context)
    selected: dict[str, Any] = {
        "spec": normalized["spec"],
        "snapshots": [],
        "evidence": [],
        "decisions": [],
        "changes": [],
        "context_gaps": [],
    }

    def size() -> int:
        return len(json.dumps(selected, ensure_ascii=False))

    if size() > budget_chars:
        raise ContextBudgetError("The project requirements exceed the context character budget")
    order = {
        "extract": ("snapshots", "evidence", "changes"),
        "compare": ("evidence", "snapshots", "decisions"),
        "impact": ("changes", "decisions", "evidence", "snapshots"),
    }.get(stage)
    if order is None:
        raise ValueError(f"Unknown research stage: {stage}")
    for name in order:
        values = normalized[name]
        if name == "evidence":
            values = sorted(values, key=lambda item: not evidence_is_current(item))
        for item in values:
            value = dict(item)
            if name == "snapshots" and stage != "extract":
                value.pop("content", None)
            selected[name].append(value)
            if size() > budget_chars - 400:
                selected[name].pop()
                selected["context_gaps"].append(f"{name}:{item.get('id', 'item')}")
    # Gaps can themselves be large. Keep their count while bounding their IDs.
    gaps = selected["context_gaps"]
    if len(gaps) > 10:
        selected["context_gaps"] = gaps[:10] + [f"{len(gaps) - 10} additional items omitted"]
    if size() > budget_chars:
        raise ContextBudgetError("Context gap metadata exceeds the context character budget")
    return selected


__all__ = [
    "ContextBudgetError",
    "ResearchValidationError",
    "build_context",
    "evidence_is_current",
    "normalize_context",
    "source_observation",
    "validate_comparison",
    "validate_evidence",
]
