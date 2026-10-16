"""Explicit source locations are checked; prose entailment remains a review task."""

from __future__ import annotations

from typing import Any

import pytest

from nanomuse.research.context import (
    ResearchValidationError,
    complete_evidence_quote,
    locator_status,
    validate_evidence,
)
from nanomuse.research.contracts import Comparison, EvidenceDraft, ProjectSpec
from nanomuse.research.reports import render_report, semantic_review_status

ALPHA = "https://github.com/sample/alpha"
BETA = "https://github.com/sample/beta"
URL = ALPHA + "/blob/" + "a" * 40 + "/docs/README.md"


def context(**changes: Any) -> dict[str, Any]:
    snapshot = {
        "id": "snapshot",
        "candidate": ALPHA,
        "url": URL,
        "content": "# Deployment\n\nLocal mode is available.\nHosted mode is optional.\n",
        **changes,
    }
    return {"spec": {"candidates": [ALPHA, BETA]}, "snapshots": [snapshot]}


def evidence(locator: str, **changes: Any) -> EvidenceDraft:
    return EvidenceDraft.model_validate(
        {
            "snapshot_id": "snapshot",
            "candidate": ALPHA,
            "dimension": "deployment",
            "fact": "Local mode is available.",
            "quote": "Local mode is available.",
            "locator": locator,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "locator",
    [
        "L3",
        "#L3",
        "lines 3-4",
        "line 3",
        "3-4",
        "README.md:3",
        "docs/README.md:3-4",
        "docs/README.md#L3-L4",
        URL + "#L3",
        URL + "#L3-L4",
        "README.md",
        "docs/README.md",
    ],
)
def test_correct_locator_identifies_actual_bound_snapshot(locator: str) -> None:
    draft = evidence(locator)
    assert validate_evidence(draft, context()) == draft


@pytest.mark.parametrize(
    ("locator", "message"),
    [
        ("L1", "outside its claimed locator"),
        ("L99", "outside the snapshot"),
        ("L4-L3", "increasing positive"),
        ("L0", "positive"),
        ("L999999999999999999999999999999", "outside the snapshot"),
        ("src/main.py:3", "path differs"),
        ("other/README.md:3", "path differs"),
        (URL.replace("/docs/", "/other/") + "#L3", "URL differs"),
        (URL + "#L4", "outside its claimed locator"),
    ],
)
def test_wrong_locator_rejected_without_guessing_source_location(
    locator: str, message: str
) -> None:
    with pytest.raises(ResearchValidationError, match=message):
        validate_evidence(evidence(locator), context())


@pytest.mark.parametrize("locator", ["Deployment section", "section:2", "#deployment", ""])
def test_legacy_freeform_locations_remain_compatible_and_require_review(locator: str) -> None:
    assert validate_evidence(evidence(locator), context()).locator == locator
    assert locator_status(locator)["review_required"]


def test_normalized_html_lines_not_claimed_to_be_original_source_lines() -> None:
    url = "https://docs.example/deploy.html"
    snapshot = context(url=url)["snapshots"][0]
    status = locator_status(url + "#L99", snapshot)
    assert status["review_required"] and "start_line" not in status
    assert validate_evidence(evidence(url + "#L99"), context(url=url))


def test_line_basis_is_saved_snapshot_text_and_quotes_do_not_escape_bound_lines() -> None:
    snapshot = context()["snapshots"][0]
    assert locator_status("lines 3-4", snapshot)["line_basis"] == "snapshot_text"
    original = evidence("L3", quote="Local mode is available", fact="Local mode is available")
    # Quote completion must not expand the citation to an unclaimed following line.
    assert complete_evidence_quote(original, context()).quote == original.quote


def test_metadata_and_report_keep_semantic_review_explicit_for_all_claims() -> None:
    rows = [{"id": "evidence", **evidence("Deployment").model_dump()}]
    review = semantic_review_status(rows)
    assert review["required"]
    assert review["unchecked"] == [
        "natural_language_entailment",
        "cross_candidate_summary_support",
        "absence_claim_support",
    ]
    assert review["locator_review_refs"] == ["evidence"]
    project = ProjectSpec(
        title="Comparison", goal="Compare", candidates=[ALPHA, BETA], mode="fixture"
    )
    comparison = Comparison(summary="Review observations", recommendation="Review", decisions=[])
    report = render_report(project, comparison, rows)
    assert "Semantic review required" in report and "需审阅语义" in report
    assert "missing information is not treated as absence" in report


def test_legacy_malformed_location_is_reviewable_without_breaking_report_read() -> None:
    status = semantic_review_status([{"id": "old", "locator": "L0"}])
    assert status["required"] and status["locator_invalid_refs"] == ["old"]
