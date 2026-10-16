"""Apply the attention policy without turning an uncertain claim into a fact."""

from __future__ import annotations

from typing import Any

from nanomuse.research.contracts import Comparison, ImpactJudgment


def mute_scope(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": event["source_id"],
        "impact_level": event["impact_level"],
        "spec_id": event.get("spec_id", ""),
        "change_type": event.get("change_type", "content"),
        "constraint_refs": sorted(
            set(event.get("affected_constraints", []))
            | set(event.get("notification_policy", {}).get("constraint_refs", []))
        ),
    }


def evaluated_event(
    event: dict[str, Any],
    judgment: ImpactJudgment,
    comparison: Comparison,
    spec: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """An unresolved hard-requirement conflict needs attention after its source changes.

    The model's uncertainty stays visible. The policy changes reminder importance,
    never the comparison verdict or the conflict itself. Existing recommendation
    muting and event deduplication continue to apply.
    """
    result = {**event, **judgment.model_dump(), "status": "evaluated"}
    if judgment.impact_level != "uncertain":
        return result
    by_id = {row["id"]: row for row in evidence if row.get("status") == "active"}
    changed = {ref for ref, row in by_id.items() if row["snapshot_id"] == event["after_snapshot"]}
    hard = {row["id"] for row in spec["constraints"] if row["hard"]}
    affected: set[str] = set()
    supporting: set[str] = set()
    for conflict in comparison.conflicts:
        refs = set(conflict.evidence_refs)
        if not refs <= by_id.keys() or not refs & changed:
            continue
        if any(by_id[ref]["candidate"] != conflict.candidate for ref in refs):
            continue
        for decision in comparison.decisions:
            constraints = set(decision.constraint_refs) & hard
            if (
                decision.candidate == conflict.candidate
                and decision.verdict == "unknown"
                and set(decision.evidence_refs) & refs
                and constraints
            ):
                affected.update(constraints)
                supporting.update(refs)
    if affected:
        result["model_judgment"] = judgment.model_dump()
        result["impact_level"] = "recommendation"
        result["notification_policy"] = {
            "id": "changed_hard_requirement_conflict",
            "constraint_refs": sorted(affected),
            "evidence_refs": sorted(supporting),
        }
    return result
