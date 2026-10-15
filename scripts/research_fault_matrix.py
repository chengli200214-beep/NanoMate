"""Run the ten frozen fault assertions three times each, using offline inputs."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

WORKFLOW = "tests/research/test_workflow.py::"
MATRIX = [
    WORKFLOW + "test_timeout_is_bounded",
    WORKFLOW + "test_unavailable_source_is_a_visible_gap",
    WORKFLOW + "test_killed_before_commit_can_be_reclaimed",
    WORKFLOW + "test_committed_source_is_not_repeated_after_restart",
    WORKFLOW + "test_uncommitted_report_does_not_replace_current",
    WORKFLOW + "test_report_and_notice_are_atomic_and_idempotent",
    WORKFLOW + "test_duplicate_unchanged_event_has_no_model_calls",
    WORKFLOW + "test_old_conditions_cannot_publish",
    "tests/research/test_consistency.py::"
    "test_conflict_retains_both_source_versions_and_cannot_settle_requirement",
    WORKFLOW + "test_expired_worker_cannot_override_new_attempt",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".local/verification/fault-matrix.xml"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists():
        parser.error("The result file already exists; choose a new output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *MATRIX, f"--junitxml={output}", "--tb=short"],
        cwd=root,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
