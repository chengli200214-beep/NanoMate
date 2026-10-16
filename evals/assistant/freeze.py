"""Prepare reviewable inputs without loading credentials or calling a provider."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from nanomuse.config import LLMSettings

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
SCORING = HERE / "scoring.json"
CASE_IDS = tuple(f"{group}{number}" for group in "ABCD" for number in range(1, 7))
PILOTS = ("A4", "B2", "C5", "D2")
REPEATS = ("A4", "C1", "C5", "D2")
SCOPES = ("synthetic_materials_disclosed", "public_pinned_snapshots_with_synthetic_changes")
MODEL_FIELDS = (
    "provider",
    "model",
    "base_url",
    "max_tokens",
    "temperature",
    "timeout",
    "max_retries",
    "stream",
    "tool_mode",
    "vision",
    "pass_reasoning",
    "extra_body",
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matrix() -> list[dict[str, Any]]:
    initial = [*PILOTS, *(case for case in CASE_IDS if case not in PILOTS)]
    rows = [{"case_id": case, "round": 1, "pilot": case in PILOTS} for case in initial]
    rows += [
        {"case_id": case, "round": round_, "pilot": False} for case in REPEATS for round_ in (2, 3)
    ]
    return [{"execution_id": f"{row['case_id']}-r{row['round']}", **row} for row in rows]


def model_config(slot: LLMSettings) -> dict[str, Any]:
    url = urlparse(slot.base_url or "")
    thinking = slot.extra_body.get("thinking")
    if (
        slot.provider != "openai"
        or slot.model not in {"deepseek-flash", "deepseek-v4-pro"}
        or url.scheme != "https"
        or url.hostname != "api.deepseek.com"
        or url.port not in {None, 443}
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/", "/v1", "/v1/"}
        or slot.max_retries != 0
        or slot.tool_mode != "native"
        or slot.vision != "off"
        or slot.stream
        or not 0 < slot.max_tokens <= 4096
        or not 0 < slot.timeout <= 120
        or slot.proxy
        or slot.extra_headers
        or set(slot.extra_body) - {"thinking", "reasoning_effort"}
        or not math.isfinite(slot.temperature)
        or not 0 <= slot.temperature <= 2
        or (
            thinking is not None
            and (
                not isinstance(thinking, dict)
                or set(thinking) != {"type"}
                or thinking["type"] not in {"enabled", "disabled"}
            )
        )
        or (
            "reasoning_effort" in slot.extra_body
            and slot.extra_body["reasoning_effort"] not in {"low", "high", "max"}
        )
    ):
        raise ValueError("Use the reviewed official text-only model without retries or fallbacks")
    # An allowlist avoids serializing api_key or unrelated media credentials.
    return {field: getattr(slot, field) for field in MODEL_FIELDS}


def dependencies() -> dict[str, str]:
    # Names and versions only, never editable locations, index URLs or credentials.
    found = {"python": platform.python_version()}
    for distribution in importlib.metadata.distributions():
        name = str(distribution.metadata["Name"] or "").lower().replace("_", "-")
        if name:
            key = f"package:{name}"
            version = distribution.version
            if key in found and found[key] != version:
                raise ValueError("Resolve duplicate installed dependency versions before freezing")
            found[key] = version
    return dict(sorted(found.items()))


def code_files(root: Path) -> dict[str, str]:
    files = [
        root / name
        for name in (
            "pyproject.toml",
            "requirements-assistant-windows.txt",
            "requirements-research.txt",
            "evals/__init__.py",
            "evals/research/__init__.py",
            "evals/research/budget.py",
            "web/package.json",
            "web/package-lock.json",
        )
    ]
    for directory in ("nanomuse", "evals/assistant", "web/src"):
        files += [
            path
            for path in (root / directory).rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".json", ".ts", ".tsx", ".js", ".css", ".html", ".wasm"}
        ]
    return {
        path.relative_to(root).as_posix(): file_hash(path)
        for path in sorted(files)
        if path.exists()
    }


def validate_inputs(manifest: dict[str, Any], scoring: dict[str, Any]) -> None:
    if (
        manifest.get("schema") != "assistant-acceptance-v1"
        or scoring.get("schema") != "assistant-scoring-v1"
    ):
        raise ValueError("Expected personal-assistant inputs, not the old research evaluation")
    for document in (manifest, scoring):
        rows = document.get("cases", [])
        if [row.get("id") for row in rows] != list(CASE_IDS):
            raise ValueError("Freeze all 24 ordered assistant case IDs")
    if any(not row.get("input") or not row.get("required") for row in manifest["cases"]):
        raise ValueError("Every scenario requires an input and observable pass conditions")


def prepare(
    root: Path,
    output: Path,
    slot: LLMSettings,
    scope: str,
    *,
    manifest_path: Path = MANIFEST,
    scoring_path: Path = SCORING,
) -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValueError("Choose and disclose the research material scope")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    scoring = json.loads(scoring_path.read_text("utf-8"))
    validate_inputs(manifest, scoring)
    root, output = root.resolve(), output.resolve()
    if ".local" not in output.parts:
        raise ValueError("Keep approval and evaluation artifacts in an ignored .local directory")
    frozen = {
        "schema": "assistant-evaluation-freeze-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "code_root": str(root),
        "code_files": code_files(root),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": file_hash(manifest_path),
        "scoring_path": str(scoring_path.resolve()),
        "scoring_sha256": file_hash(scoring_path),
        "model": model_config(slot),
        "scope": scope,
        "dependencies": dependencies(),
        "driver_profile": {"browser": {"enabled": False}, "hands": {"enabled": False}},
        "matrix": matrix(),
    }
    frozen["freeze_sha256"] = digest(frozen)
    output.mkdir(parents=True, exist_ok=False)
    (output / "freeze.json").write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    return frozen


def verify(frozen: dict[str, Any]) -> None:
    unsigned = {key: value for key, value in frozen.items() if key != "freeze_sha256"}
    if frozen.get("schema") != "assistant-evaluation-freeze-v1" or digest(unsigned) != frozen.get(
        "freeze_sha256"
    ):
        raise ValueError("The assistant freeze was changed")
    if (
        code_files(Path(frozen["code_root"])) != frozen["code_files"]
        or dependencies() != frozen["dependencies"]
        or file_hash(Path(frozen["manifest_path"])) != frozen["manifest_sha256"]
        or file_hash(Path(frozen["scoring_path"])) != frozen["scoring_sha256"]
        or frozen["matrix"] != matrix()
        or any(
            file_hash(Path(item["path"])) != item["sha256"]
            for item in frozen.get("case_fixtures", {}).values()
        )
    ):
        raise ValueError(
            "Code, inputs, fixtures or dependencies changed; obtain a new reviewed freeze"
        )
    if frozen.get("driver_readiness") is not None:
        from evals.assistant.drivers import readiness

        if readiness(frozen["driver_profile"]) != frozen["driver_readiness"]:
            raise ValueError("The frozen deterministic driver installation changed")


def configure_drivers(directory: Path, profile_path: Path, browsers: Path | None) -> dict[str, Any]:
    """Bind a new explicit local driver profile before approval, without GUI dispatch."""
    from evals.assistant.drivers import load_profile, readiness

    if (directory / "approval.json").exists() or (directory / "budget.sqlite3").exists():
        raise ValueError("An approved or started batch cannot change drivers")
    path = directory / "freeze.json"
    frozen = json.loads(path.read_text("utf-8"))
    verify(frozen)
    profile = load_profile(profile_path, browsers)
    status = readiness(profile)
    if not status["ready"]:
        raise ValueError("Deterministic driver dependencies are not ready")
    frozen.update(driver_profile=profile, driver_readiness=status)
    frozen.pop("freeze_sha256")
    frozen["freeze_sha256"] = digest(frozen)
    for target, value in (
        (path, frozen),
        (directory / "approval.pending.json", approval_template(frozen)),
    ):
        temporary = target.with_name(f".{target.name}-{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
        temporary.replace(target)
    return status


def approval_template(frozen: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "assistant-evaluation-approval-v1",
        "status": "pending",
        "human_review_completed": False,
        "freeze_sha256": frozen["freeze_sha256"],
        "manifest_sha256": frozen["manifest_sha256"],
        "scoring_sha256": frozen["scoring_sha256"],
        "model_sha256": digest(frozen["model"]),
        "scope": frozen["scope"],
        "phase": "pilots",
        "automatic_topup_allowed": False,
        "annotations": [{"id": case, "approved": False} for case in CASE_IDS],
        "budget_policy": None,
    }


def refresh_sources(directory: Path) -> dict[str, Any]:
    """Refreeze corrected code without reseeding unchanged, unapproved fixtures."""
    if (directory / "approval.json").exists() or (directory / "budget.sqlite3").exists():
        raise ValueError("An approved or started batch cannot be silently refrozen")
    path = directory / "freeze.json"
    frozen = json.loads(path.read_text("utf-8"))
    unsigned = {key: value for key, value in frozen.items() if key != "freeze_sha256"}
    if (
        digest(unsigned) != frozen.get("freeze_sha256")
        or frozen.get("schema") != "assistant-evaluation-freeze-v1"
    ):
        raise ValueError("The original freeze record is invalid")
    if (
        file_hash(Path(frozen["manifest_path"])) != frozen["manifest_sha256"]
        or file_hash(Path(frozen["scoring_path"])) != frozen["scoring_sha256"]
    ):
        raise ValueError("Changed labels require a new preparation bundle")
    if any(
        file_hash(Path(item["path"])) != item["sha256"]
        for item in frozen.get("case_fixtures", {}).values()
    ):
        raise ValueError("The case fixture receipts changed")
    frozen.update(
        code_files=code_files(Path(frozen["code_root"])),
        dependencies=dependencies(),
        source_refreshed_at=datetime.now(UTC).isoformat(),
    )
    frozen.pop("freeze_sha256")
    frozen["freeze_sha256"] = digest(frozen)
    for target, value in (
        (path, frozen),
        (directory / "approval.pending.json", approval_template(frozen)),
    ):
        temporary = target.with_name(f".{target.name}-{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
        temporary.replace(target)
    return frozen


def approval(frozen: dict[str, Any], receipt: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    verify(frozen)
    expected = approval_template(frozen)
    if (
        receipt.get("schema") != expected["schema"]
        or receipt.get("status") != "approved"
        or receipt.get("human_review_completed") is not True
        or receipt.get("automatic_topup_allowed") is not False
        or any(
            receipt.get(key) != expected[key]
            for key in (
                "freeze_sha256",
                "manifest_sha256",
                "scoring_sha256",
                "model_sha256",
                "scope",
            )
        )
        or receipt.get("annotations") != [{"id": case, "approved": True} for case in CASE_IDS]
        or receipt.get("phase") not in {"pilots", "complete"}
    ):
        raise ValueError(
            "An exact new assistant approval is required; old research approval is invalid"
        )
    policy = receipt.get("budget_policy")
    if not isinstance(policy, dict) or policy.get("model") != frozen["model"]["model"]:
        raise ValueError("The approved policy must name the frozen model")
    required = {
        "limit_cny",
        "stop_reserve_cny",
        "context_token_ceiling",
        "input_peak_micro_cny",
        "output_peak_micro_cny",
        "pricing_verified_at",
        "price_source",
    }
    if (
        not required <= policy.keys()
        or policy["price_source"] != "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
    ):
        raise ValueError("Record reviewed vendor rates and their verification date locally")
    datetime.fromisoformat(policy["pricing_verified_at"])
    allowed = {
        row["execution_id"]
        for row in frozen["matrix"]
        if receipt["phase"] == "complete" or row["pilot"]
    }
    return {
        **policy,
        "assistant_freeze_sha256": frozen["freeze_sha256"],
        "assistant_approval_sha256": digest(receipt),
    }, allowed
