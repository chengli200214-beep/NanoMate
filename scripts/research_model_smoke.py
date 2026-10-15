"""Verify a real model-selected research tool call using the local encrypted connection."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from nanomuse.config import SentinelSettings  # noqa: E402
from nanomuse.research.contracts import ProjectSpec  # noqa: E402
from nanomuse.research.model import (  # noqa: E402
    create_research_llm,
    load_research_settings,
    research_model_ready,
)
from nanomuse.research.tools import ReadSource, ResearchToolState  # noqa: E402
from nanomuse.schema import Message, ToolResult  # noqa: E402
from nanomuse.sentinel import AuditLog, Sentinel  # noqa: E402
from nanomuse.ui import HeadlessUI  # noqa: E402


async def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=False)
    settings = load_research_settings(args.config, args.data_dir)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "needs_configuration",
        "provider": settings.llm.provider,
        "model": settings.llm.model,
        "input_kind": "synthetic_tool_connectivity_probe",
        "tool_call_verified": False,
        "calls": [],
        "monetary_cost": None,
        "quality_score": None,
    }
    if not research_model_ready(settings):
        return summary
    audit = AuditLog(args.output / "audit.jsonl")
    sentinel = Sentinel(SentinelSettings(), audit, HeadlessUI(approve=False))
    spec = ProjectSpec(
        title="Model tool connectivity",
        goal="Read a registered test snapshot without any external action.",
        candidates=[
            "https://github.com/research-example/a",
            "https://github.com/research-example/b",
        ],
    )
    state = ResearchToolState(
        {
            "project_id": "connectivity_probe",
            "spec": spec.model_dump(),
            "snapshots": [
                {
                    "id": "connectivity_snapshot",
                    "project_id": "connectivity_probe",
                    "source_id": "connectivity_source",
                    "candidate": spec.candidates[0],
                    "kind": "manual",
                    "url": "",
                    "version": "probe-v1",
                    "content": "The registered snapshot contains the marker: research-tool-ok.",
                }
            ],
            "evidence": [],
            "decisions": [],
            "conflicts": [],
            "changes": [],
        }
    )
    tool = ReadSource(state=state)
    messages = [
        Message.system(
            "Use the provided read_source tool to read snapshot connectivity_snapshot. Call the tool before responding. Do not invent its content."
        ),
        Message.user("Read the registered snapshot now using read_source."),
    ]
    llm = create_research_llm(settings)
    task = sentinel.begin_task(
        "Verify research tool connectivity", conversation="connectivity_probe"
    )
    try:
        for attempt in range(1, 3):
            entry: dict[str, Any] = {"attempt": attempt, "status": "running"}
            with (args.output / "requests.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry) + "\n")
            try:
                response = await asyncio.wait_for(llm.ask(messages, tools=[tool.to_param()]), 90)
                entry.update(status="succeeded", usage=response.usage)
                messages.append(response.to_message())
                for call in response.tool_calls or []:
                    result = (
                        await sentinel.guard(call, tool)
                        if call.name == "read_source"
                        else ToolResult.fail("Tool is not available in this probe")
                    )
                    messages.append(Message.tool(result.for_model(), call.id, call.name))
                    if result.ok and "research-tool-ok" in result.output:
                        summary.update(status="passed", tool_call_verified=True)
                if summary["tool_call_verified"]:
                    break
                messages.append(
                    Message.user(
                        "A native read_source tool call is required. Use snapshot_id=connectivity_snapshot."
                    )
                )
            except Exception as exc:
                entry.update(status="failed", error_type=type(exc).__name__)
                code = getattr(exc, "status_code", None)
                entry["http_status"] = code if isinstance(code, int) else None
                summary["status"] = "failed"
                break
            finally:
                summary["calls"].append(entry)
                with (args.output / "requests.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(entry) + "\n")
        if summary["status"] == "needs_configuration":
            summary["status"] = "failed"
    finally:
        sentinel.end_task(task)
        await llm.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path(".local/research-dev"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Model tool call: {summary['status']}; requests: {len(summary['calls'])}.")
    return 0 if summary["tool_call_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
