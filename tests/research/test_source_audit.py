"""Research reads must cross the real Sentinel and preserve their source boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanomuse.config import SentinelSettings
from nanomuse.research.contracts import FetchedSource, ProjectSpec
from nanomuse.research.sources.tool import FetchRegisteredSource
from nanomuse.research.tools import ResearchToolState, build_research_tools
from nanomuse.schema import Function, RiskLevel, ToolCall
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.ui import HeadlessUI


class _RecordingFetcher:
    def __init__(self) -> None:
        self.reads: list[dict[str, Any]] = []

    async def fetch(
        self, source: dict[str, Any], previous: dict[str, Any] | None = None
    ) -> FetchedSource:
        self.reads.append(dict(source))
        return FetchedSource(
            url=source["url"], content="Registered source content", version="fixed"
        )


def _source(kind: str = "document") -> dict[str, Any]:
    return {
        "id": "registered_source",
        "project_id": "project_a",
        "candidate": "https://github.com/research-example/a",
        "kind": kind,
        "url": "https://docs.example/deployment",
        "content": "User-supplied context" if kind == "manual" else "",
    }


def _call(**arguments: Any) -> ToolCall:
    return ToolCall(function=Function(name="read_source", arguments=json.dumps(arguments)))


async def test_registered_source_crosses_sentinel_with_auditable_result(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    sentinel = Sentinel(SentinelSettings(), audit, HeadlessUI(approve=False))
    fetcher = _RecordingFetcher()
    tool = FetchRegisteredSource(source=_source(), fetcher=fetcher)
    task = sentinel.begin_task("Read project sources", conversation="research:project_a")
    try:
        result = await sentinel.guard(_call(source_id="registered_source"), tool)
    finally:
        sentinel.end_task(task)
    assert result.ok
    assert len(fetcher.reads) == 1
    assert json.loads(result.output)["content"] == "Registered source content"
    entry = audit.tail(1)[0]
    assert entry["event"] == "tool_call"
    assert entry["tool"] == "read_source"
    assert entry["risk"] == RiskLevel.MODERATE.value
    assert entry["egress_target"] == "docs.example"
    assert entry["args"] == {"source_id": "registered_source"}
    assert entry["ok"] is True


async def test_arbitrary_source_id_is_refused_and_failure_is_audited(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    sentinel = Sentinel(SentinelSettings(), audit, HeadlessUI())
    fetcher = _RecordingFetcher()
    tool = FetchRegisteredSource(source=_source(), fetcher=fetcher)
    for source_id in ("another_project_source", "https://docs.example/unregistered", ""):
        result = await sentinel.guard(_call(source_id=source_id), tool)
        assert not result.ok
        assert "not registered" in result.error
        entry = audit.tail(1)[0]
        assert entry["event"] == "tool_call"
        assert entry["tool"] == "read_source"
        assert entry["args"]["source_id"] == source_id
        assert entry["ok"] is False
        assert "not registered" in entry["error"]
    assert fetcher.reads == []
    assert len(audit.tail(10)) == 3


async def test_sentinel_denial_prevents_even_registered_fetch(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    sentinel = Sentinel(
        SentinelSettings(deny_tools=["read_source"]), audit, HeadlessUI(approve=True)
    )
    fetcher = _RecordingFetcher()
    tool = FetchRegisteredSource(source=_source(), fetcher=fetcher)
    result = await sentinel.guard(_call(source_id="registered_source"), tool)
    assert not result.ok
    assert fetcher.reads == []
    entry = audit.tail(1)[0]
    assert entry["decision"] == "deny"
    assert entry["ok"] is False


async def test_model_cannot_change_registered_destination(tmp_path: Path) -> None:
    sentinel = Sentinel(SentinelSettings(), AuditLog(tmp_path / "audit.jsonl"), HeadlessUI())
    fetcher = _RecordingFetcher()
    tool = FetchRegisteredSource(source=_source(), fetcher=fetcher)
    result = await sentinel.guard(
        _call(source_id="registered_source", url="https://other.example/unregistered"), tool
    )
    assert result.ok
    assert len(fetcher.reads) == 1
    assert fetcher.reads[0]["url"] == "https://docs.example/deployment"


async def test_manual_source_is_audited_as_private_local_data(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    sentinel = Sentinel(SentinelSettings(), audit, HeadlessUI())
    tool = FetchRegisteredSource(source=_source("manual"), fetcher=_RecordingFetcher())
    task = sentinel.begin_task("Read imported notes", conversation="research:project_a")
    try:
        result = await sentinel.guard(_call(source_id="registered_source"), tool)
        assert result.ok
        assert sentinel.tainted
    finally:
        sentinel.end_task(task)
    entry = audit.tail(1)[0]
    assert entry["risk"] == RiskLevel.SAFE.value
    assert tool.assess({"source_id": "registered_source"}).egress is False


def test_research_model_only_receives_four_project_tools() -> None:
    state = ResearchToolState(
        {
            "project_id": "project_a",
            "spec": ProjectSpec(
                title="Tool scope",
                goal="Compare registered sources",
                candidates=[
                    "https://github.com/research-example/a",
                    "https://github.com/research-example/b",
                ],
                mode="fixture",
            ).model_dump(),
            "snapshots": [],
            "evidence": [],
            "decisions": [],
            "changes": [],
        }
    )
    tools = build_research_tools(state)
    assert {tool.name for tool in tools} == {
        "read_source",
        "query_evidence",
        "submit_evidence",
        "propose_comparison",
    }
    for disallowed in (
        "shell",
        "python_execute",
        "write_file",
        "send_email",
        "web_fetch",
        "browser",
    ):
        assert disallowed not in tools
    source_tool = next(tool for tool in tools if tool.name == "read_source")
    assert set(source_tool.parameters["properties"]) == {"snapshot_id"}
