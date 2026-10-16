"""Prepare cases, run an explicitly approved window, and record human verdicts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from evals.assistant.freeze import SCOPES, approval_template, prepare
from nanomuse.config import LLMSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--root", type=Path, default=Path.cwd())
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--scope", choices=SCOPES, default="synthetic_materials_disclosed")
    freeze.add_argument("--model", choices=("deepseek-flash", "deepseek-v4-pro"), required=True)
    preparation = commands.add_parser("prepare-case")
    preparation.add_argument("--batch", type=Path, required=True)
    selection = preparation.add_mutually_exclusive_group(required=True)
    selection.add_argument("--execution-id")
    selection.add_argument("--all", action="store_true")
    running = commands.add_parser("run-case")
    running.add_argument("--batch", type=Path, required=True)
    running.add_argument("--execution-id", required=True)
    running.add_argument("--live", action="store_true")
    running.add_argument("--mock-script", type=Path)
    running.add_argument("--config", type=Path)
    running.add_argument("--model-data", type=Path)
    running.add_argument("--port", type=int, default=8792)
    running.add_argument("--no-ui", action="store_true")
    running.add_argument("--wait-seconds", type=float, default=900)
    verdict = commands.add_parser("record-verdict")
    verdict.add_argument("--batch", type=Path, required=True)
    verdict.add_argument("--execution-id", required=True)
    verdict.add_argument("--review", type=Path, required=True)
    amendment = commands.add_parser("amend-approval")
    amendment.add_argument("--batch", type=Path, required=True)
    amendment.add_argument("--receipt", type=Path, required=True)
    refreshing = commands.add_parser("refresh-freeze")
    refreshing.add_argument("--batch", type=Path, required=True)
    drivers = commands.add_parser("configure-drivers")
    drivers.add_argument("--batch", type=Path, required=True)
    drivers.add_argument("--profile", type=Path, required=True)
    drivers.add_argument("--browser-runtime", type=Path)
    checking = commands.add_parser("driver-readiness")
    checking.add_argument("--batch", type=Path, required=True)
    replacement = commands.add_parser("reprepare-case")
    replacement.add_argument("--batch", type=Path, required=True)
    replacement.add_argument("--execution-id", required=True)
    arguments = sys.argv[1:]
    if not arguments or arguments[0].startswith("-"):
        arguments = ["freeze", *arguments]
    args = parser.parse_args(arguments)
    if args.command == "reprepare-case":
        from evals.assistant.runner import prepare_case, retire_unapproved_case

        archive = retire_unapproved_case(args.batch, args.execution_id)
        asyncio.run(prepare_case(args.batch, args.execution_id))
        print(json.dumps({"archived": archive, "provider_requests": 0, "approval": "pending"}))
        return
    if args.command in {"configure-drivers", "driver-readiness"}:
        from evals.assistant.drivers import readiness
        from evals.assistant.freeze import configure_drivers, verify

        if args.command == "configure-drivers":
            result = configure_drivers(args.batch, args.profile, args.browser_runtime)
        else:
            frozen = json.loads((args.batch / "freeze.json").read_text("utf-8"))
            verify(frozen)
            result = readiness(frozen["driver_profile"])
        print(json.dumps(result))
        return
    if args.command == "refresh-freeze":
        from evals.assistant.freeze import refresh_sources

        result = refresh_sources(args.batch)
        print(
            json.dumps(
                {
                    "freeze_sha256": result["freeze_sha256"],
                    "approval": "pending",
                    "provider_requests": 0,
                }
            )
        )
        return
    if args.command == "prepare-case":
        from evals.assistant.runner import prepare_all, prepare_case

        result = asyncio.run(
            prepare_all(args.batch) if args.all else prepare_case(args.batch, args.execution_id)
        )
        print(json.dumps({"prepared": result.get("prepared", 1), "provider_requests": 0}))
        return
    if args.command == "record-verdict":
        from evals.assistant.runner import record_verdict

        result = record_verdict(
            args.batch, args.execution_id, json.loads(args.review.read_text("utf-8"))
        )
        print(json.dumps({"verdict": result["verdict"], "offline_mock": result["offline_mock"]}))
        return
    if args.command == "amend-approval":
        from evals.assistant.amend import amend

        print(json.dumps(amend(args.batch, json.loads(args.receipt.read_text("utf-8")))))
        return
    if args.command == "run-case":
        from evals.assistant.guard import EvaluationGuard
        from evals.assistant.runner import run_case
        from nanomuse.llm.mock import MockLLM
        from nanomuse.schema import LLMResponse

        # Validate new approval and all live fixtures before loading any credential.
        check = EvaluationGuard(args.batch, allow_mock=not args.live)
        frozen_model = check.frozen["model"]
        check.close()
        if args.live:
            if args.config is None or args.model_data is None:
                parser.error("Live mode requires explicit local --config and --model-data")
            from nanomuse.research.model import create_research_llm, load_research_settings

            delegate = create_research_llm(load_research_settings(args.config, args.model_data))
        else:
            if args.mock_script is None:
                parser.error("Offline execution requires an explicit --mock-script")
            delegate = MockLLM(
                [
                    LLMResponse.model_validate(row)
                    for row in json.loads(args.mock_script.read_text("utf-8"))
                ]
            )
            delegate.settings = LLMSettings.model_validate(frozen_model)  # type: ignore[attr-defined]
        result = asyncio.run(
            run_case(
                args.batch,
                args.execution_id,
                delegate,
                allow_mock=not args.live,
                serve=not args.no_ui,
                port=args.port,
                wait_seconds=args.wait_seconds,
            )
        )
        print(
            json.dumps(
                {
                    "execution_id": result["execution_id"],
                    "verdict": result["verdict"],
                    "offline_mock": result["offline_mock"],
                    "error": result["error"],
                }
            )
        )
        return
    slot = LLMSettings(
        model=args.model,
        tool_mode="native",
        max_retries=0,
        vision="off",
        stream=False,
        timeout=120,
        max_tokens=4096,
    )
    frozen = prepare(args.root, args.output, slot, args.scope)
    (args.output / "approval.pending.json").write_text(
        json.dumps(approval_template(frozen), ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    print(
        json.dumps(
            {
                "freeze_sha256": frozen["freeze_sha256"],
                "executions": 32,
                "pilots_included": 4,
                "provider_requests": 0,
                "approval": "pending",
                "domain_runner": "manual_assisted_cases_require_preparation_and_new_approval",
            }
        )
    )


if __name__ == "__main__":
    main()
