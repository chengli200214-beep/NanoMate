"""Bind an owner's annotation and spending decisions to exact local review artifacts."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any


def load_approval(
    receipt_path: Path, scoring_path: Path, manifest_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = json.loads(receipt_path.read_text("utf-8"))
    scoring = json.loads(scoring_path.read_text("utf-8"))
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    scoring_hash = hashlib.sha256(scoring_path.read_bytes()).hexdigest()
    expected = {row["id"] for row in manifest["scenarios"]}
    cases = receipt.get("scenarios", [])
    budget = receipt.get("budget", {})
    limit = Decimal(str(budget.get("additional_limit_cny")))
    if (
        receipt.get("status") != "approved"
        or receipt.get("human_review_completed") is not True
        or not receipt.get("reviewed_at")
        or receipt.get("independent_review") is not False
        or receipt.get("scope", {}).get("selected")
        != "two_synthetic_candidates_plus_separate_real_repository_smoke"
        or receipt.get("source_manifest_sha256") != manifest_hash
        or scoring.get("source_manifest_sha256") != manifest_hash
        or receipt.get("scoring_rules", {}).get("source_sha256") != scoring_hash
        or receipt.get("scoring_rules", {}).get("decision") != "approve"
        or len(cases) != len(expected)
        or {row["id"] for row in cases} != expected
        or any(row.get("decision") != "approve" for row in cases)
        or {row["scenario_id"] for row in scoring["cases"]} != expected
        or budget.get("status") != "approved"
        or budget.get("automatic_topup_allowed") is not False
        or not limit.is_finite()
        or limit <= 5
    ):
        raise ValueError("Live evaluation requires complete, hash-bound owner approval")
    approval = {
        "status": "owner_approved_agent_authored_not_independent",
        "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "scoring_sha256": scoring_hash,
        "reviewed_at": receipt["reviewed_at"],
        "scope": receipt["scope"]["selected"],
        "semantic_output_review": "pending",
    }
    policy = {
        "model": "deepseek-flash",
        "limit_cny": str(limit),
        "stop_reserve_cny": "5",
        "context_token_ceiling": 1_048_576,
        "input_peak_micro_cny": 2,
        "output_peak_micro_cny": 8,
        "pricing_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
        "pricing_verified_date": "2026-10-16",
        "cost_interpretation": "peak_cache_miss_upper_estimate_not_provider_bill",
        "receipt_sha256": approval["receipt_sha256"],
    }
    return approval, policy
