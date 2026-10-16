"""Stage context selection and checks against project-scoped source snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

from nanomuse.research.contracts import (
    Comparison,
    ConflictDraft,
    DecisionDraft,
    EvidenceDraft,
    ProjectSpec,
)

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
    if context.get("project_id"):
        result["project_id"] = str(context["project_id"])
    for name in ("snapshots", "evidence", "decisions", "changes", "conflicts"):
        values = context.get(name, [])
        if not isinstance(values, list):
            raise ResearchValidationError(f"{name} must be an array")
        result[name] = [record(item) for item in values]
    candidates = {item.rstrip("/"): item for item in spec.candidates}
    for name in ("snapshots", "evidence", "decisions", "conflicts"):
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
    if context.get("project_id") and snapshot.get("project_id") not in {
        None,
        context["project_id"],
    }:
        raise ResearchValidationError("Evidence snapshot belongs to another project")
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


def validate_conflicts(
    conflicts: list[ConflictDraft],
    context: dict[str, Any],
    *,
    check_snapshots: bool = True,
) -> list[ConflictDraft]:
    """Bind both sides to current project evidence and derive the actual version scope.

    The renderer only has persisted evidence metadata, so it repeats reference and
    status checks with check_snapshots=False. The reasoner always verifies exact quotes.
    """
    candidates = {item.rstrip("/"): item for item in context["spec"]["candidates"]}
    evidence = {str(item["id"]): item for item in context.get("evidence", [])}
    snapshots = {str(item["id"]): item for item in context.get("snapshots", [])}
    expected_project = context.get("project_id")
    projects = {item.get("project_id") for item in evidence.values() if item.get("project_id")}
    if not expected_project and len(projects) == 1:
        expected_project = next(iter(projects))
    elif not expected_project and len(projects) > 1:
        raise ResearchValidationError("Conflict evidence context spans multiple projects")
    checked: list[ConflictDraft] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for conflict in conflicts:
        candidate = candidates.get(conflict.candidate.rstrip("/"))
        if candidate is None:
            raise ResearchValidationError("Conflict refers to an unknown candidate")
        if not conflict.dimension.strip() or not conflict.reason.strip():
            raise ResearchValidationError("Conflict requires a nonempty dimension and reason")
        refs = conflict.evidence_refs
        if len(set(refs)) < 2 or len(set(refs)) != len(refs):
            raise ResearchValidationError("Conflict requires at least two distinct evidence IDs")
        scope: list[str] = []
        pair_projects: set[str] = set()
        for ref in refs:
            item = evidence.get(ref)
            if item is None:
                raise ResearchValidationError(f"Conflict refers to unknown evidence: {ref}")
            if str(item.get("candidate", "")).rstrip("/") != candidate.rstrip("/"):
                raise ResearchValidationError("Conflict evidence belongs to another candidate")
            if not evidence_is_current(item):
                raise ResearchValidationError("Conflict evidence must be current")
            project = item.get("project_id")
            if project:
                pair_projects.add(str(project))
            if expected_project and project not in {None, expected_project}:
                raise ResearchValidationError("Conflict evidence belongs to another project")
            if check_snapshots:
                validate_evidence(
                    EvidenceDraft.model_validate(
                        {key: item[key] for key in EvidenceDraft.model_fields if key in item}
                    ),
                    context,
                )
            snapshot_id = str(item.get("snapshot_id", "unknown"))
            snapshot = snapshots.get(snapshot_id, {})
            if (
                check_snapshots
                and item.get("version")
                and snapshot.get("version")
                and item["version"] != snapshot["version"]
            ):
                raise ResearchValidationError(
                    "Conflict evidence version does not match its snapshot"
                )
            version = str(snapshot.get("version") or item.get("version") or "unknown")
            scope.append(f"{ref}: {version} ({snapshot_id})")
        if len(pair_projects) > 1:
            raise ResearchValidationError("Conflict evidence spans multiple projects")
        key = (candidate, conflict.dimension, tuple(sorted(refs)))
        if key not in seen:
            version_scope = "; ".join(scope)
            if len(version_scope) > 1000:
                raise ResearchValidationError(
                    "Conflict version scope is too long; split it into smaller evidence pairs"
                )
            seen.add(key)
            checked.append(
                conflict.model_copy(
                    update={
                        "candidate": candidate,
                        "version_scope": version_scope,
                    }
                )
            )
    return checked


def conflict_affects_decision(
    conflict: ConflictDraft,
    decision: DecisionDraft,
    decisions: list[DecisionDraft],
) -> bool:
    """Expand an explicit evidence/dimension dependency to its shared constraints."""
    if conflict.candidate.rstrip("/") != decision.candidate.rstrip("/"):
        return False

    def direct(item: DecisionDraft) -> bool:
        return item.candidate.rstrip("/") == conflict.candidate.rstrip("/") and (
            item.dimension == conflict.dimension
            or conflict.dimension in item.constraint_refs
            or bool(set(item.evidence_refs) & set(conflict.evidence_refs))
        )

    affected_constraints = {
        ref for item in decisions if direct(item) for ref in item.constraint_refs
    }
    return direct(decision) or bool(affected_constraints & set(decision.constraint_refs))


def require_unknown_for_conflicts(
    decisions: list[DecisionDraft],
    conflicts: list[ConflictDraft],
) -> list[DecisionDraft]:
    result = []
    for decision in decisions:
        affected = any(conflict_affects_decision(item, decision, decisions) for item in conflicts)
        if affected and decision.verdict in {"meets", "fails"}:
            raise ResearchValidationError(
                "Unresolved conflict requires an unknown decision verdict"
            )
        if affected and decision.verdict == "preference":
            decision = decision.model_copy(update={"verdict": "unknown"})
        result.append(decision)
    return result


def validate_decision_support(
    decision: DecisionDraft, support: list[dict[str, Any]], spec: ProjectSpec
) -> None:
    constraints = {item.id: item for item in spec.constraints}
    if any(ref not in constraints for ref in decision.constraint_refs):
        raise ResearchValidationError("Decision refers to an unknown constraint")
    if decision.verdict not in {"meets", "fails"}:
        return
    if not support or not any(item.get("verification") in DIRECT_VERIFICATIONS for item in support):
        raise ResearchValidationError("A definitive verdict requires direct current evidence")
    if any(constraints[ref].hard for ref in decision.constraint_refs) and any(
        item.get("verification") == "unknown" for item in support
    ):
        raise ResearchValidationError("Unknown evidence cannot settle a hard constraint")
    for ref in decision.constraint_refs:
        if constraints[ref].hard and not any(
            item.get("verification") in DIRECT_VERIFICATIONS and item.get("dimension") == ref
            for item in support
        ):
            raise ResearchValidationError(
                f"Hard constraint {ref} requires direct evidence in its own dimension"
            )


def validate_comparison(comparison: Comparison, context: dict[str, Any]) -> Comparison:
    spec = ProjectSpec.model_validate(context["spec"])
    evidence = {str(item["id"]): item for item in context.get("evidence", [])}
    constraints = {item.id: item for item in spec.constraints}
    for item in context.get("conflicts", []):
        if (
            item.get("status", "open") == "open"
            and context.get("project_id")
            and item.get("project_id") not in {None, context["project_id"]}
        ):
            raise ResearchValidationError("Registered conflict belongs to another project")
    known_conflicts = [
        ConflictDraft.model_validate(
            {key: item[key] for key in ConflictDraft.model_fields if key in item}
        )
        for item in context.get("conflicts", [])
        if item.get("status", "open") == "open"
    ]
    conflicts = validate_conflicts([*comparison.conflicts, *known_conflicts], context)
    require_unknown_for_conflicts(comparison.decisions, conflicts)
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
        validate_decision_support(decision, support, spec)
        checked.append(decision)
    checked = require_unknown_for_conflicts(checked, conflicts)
    for conflict in conflicts:
        if not any(conflict_affects_decision(conflict, item, checked) for item in checked):
            checked.append(
                DecisionDraft(
                    candidate=conflict.candidate,
                    dimension=conflict.dimension,
                    conclusion="The registered evidence conflicts. Verification is required.",
                    verdict="unknown",
                    evidence_refs=conflict.evidence_refs,
                    constraint_refs=[conflict.dimension]
                    if conflict.dimension in constraints
                    else [],
                )
            )
    unknowns = list(comparison.unknowns)
    unknowns.extend(
        f"{item.candidate} / {item.dimension}: {item.reason} [{item.version_scope}]"
        for item in conflicts
    )
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
    return bind_recommendation(
        spec,
        comparison.model_copy(
            update={
                "decisions": checked,
                "unknowns": list(dict.fromkeys(unknowns)),
                "conflicts": conflicts,
            }
        ),
    )


def constraint_verdict(decisions: list[DecisionDraft]) -> str:
    """A preference cannot establish a hard fact or erase an established fact."""
    verdicts = {item.verdict for item in decisions}
    if "fails" in verdicts:
        return "fails"
    if "unknown" in verdicts or "meets" not in verdicts:
        return "unknown"
    return "meets"


def bind_recommendation(spec: ProjectSpec, comparison: Comparison) -> Comparison:
    """Derive the displayed recommendation from one eligible structured candidate.

    Old stored comparisons without the optional field remain readable, but cannot
    make an unchecked selection when the project has hard requirements.
    """
    hard_ids = {item.id for item in spec.constraints if item.hard}
    eligible = {
        candidate
        for candidate in spec.candidates
        if all(
            constraint_verdict(
                [
                    item
                    for item in comparison.decisions
                    if item.candidate == candidate and ref in item.constraint_refs
                ]
            )
            == "meets"
            for ref in hard_ids
        )
    }
    selected = comparison.recommended_candidate
    if selected is not None:
        selected = {item.rstrip("/"): item for item in spec.candidates}.get(selected.rstrip("/"))
    if selected not in eligible:
        selected = None
    if hard_ids and not eligible:
        recommendation = (
            "No candidate is confirmed to meet every hard constraint. "
            "Complete the unresolved checks before making a selection. / "
            "尚未确认任何候选满足全部硬条件，请先完成待核验项。"
        )
    elif selected is not None:
        recommendation = f"Recommended candidate / 推荐候选: {selected}."
    elif hard_ids:
        recommendation = (
            "No eligible candidate was selected. Review the hard requirements before selecting. / "
            "尚未选定合格候选，请核对硬条件后再选择。"
        )
    else:
        recommendation = comparison.recommendation
    return comparison.model_copy(
        update={"recommended_candidate": selected, "recommendation": recommendation}
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
        "conflicts": [],
        "context_gaps": [],
    }
    if normalized.get("project_id"):
        selected["project_id"] = normalized["project_id"]

    def size() -> int:
        return len(json.dumps(selected, ensure_ascii=False))

    if size() > budget_chars:
        raise ContextBudgetError("The project requirements exceed the context character budget")
    order = {
        "extract": ("snapshots", "evidence", "changes", "conflicts"),
        "compare": ("conflicts", "evidence", "snapshots", "decisions"),
        "impact": ("changes", "conflicts", "evidence", "decisions", "snapshots"),
    }.get(stage)
    if order is None:
        raise ValueError(f"Unknown research stage: {stage}")
    hard_ids = {item["id"] for item in normalized["spec"]["constraints"] if item["hard"]}
    conflict_refs = {
        ref for conflict in normalized["conflicts"] for ref in conflict.get("evidence_refs", [])
    }
    critical_gaps: list[str] = []
    for name in order:
        values = normalized[name]
        if name == "evidence":
            values = sorted(
                values,
                key=lambda item: (
                    not evidence_is_current(item),
                    item["id"] not in conflict_refs,
                    item.get("dimension") not in hard_ids,
                ),
            )
        for item in values:
            value = dict(item)
            if name == "snapshots" and stage != "extract":
                value.pop("content", None)
            selected[name].append(value)
            if size() > budget_chars - 400:
                selected[name].pop()
                gap = f"{name}:{item.get('id', 'item')}"
                if name == "evidence" and (
                    item["id"] in conflict_refs or item.get("dimension") in hard_ids
                ):
                    gap += f" candidate={item.get('candidate', '')} dimension={item.get('dimension', '')}"
                    critical_gaps.append(gap)
                else:
                    selected["context_gaps"].append(gap)
    # Gaps can themselves be large. Keep their count while bounding their IDs.
    gaps = selected["context_gaps"]
    if len(gaps) > 10:
        selected["context_gaps"] = gaps[:10] + [f"{len(gaps) - 10} additional items omitted"]
    selected["context_gaps"] = critical_gaps + selected["context_gaps"]
    if size() > budget_chars:
        raise ContextBudgetError("Context gap metadata exceeds the context character budget")
    return selected


__all__ = [
    "ContextBudgetError",
    "ResearchValidationError",
    "build_context",
    "bind_recommendation",
    "constraint_verdict",
    "conflict_affects_decision",
    "evidence_is_current",
    "normalize_context",
    "source_observation",
    "require_unknown_for_conflicts",
    "validate_comparison",
    "validate_conflicts",
    "validate_evidence",
]
