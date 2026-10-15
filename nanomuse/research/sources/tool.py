"""Registered public reads cross the same Sentinel as model-selected tools."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field

from nanomuse.research.contracts import SourceFetcher
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment


class FetchRegisteredSource(BaseTool):
    name: str = "read_source"
    description: str = "Read one source registered by the project owner."
    source: dict[str, Any]
    previous: dict[str, Any] | None = None
    fetcher: SourceFetcher
    last_error: Any = Field(default=None, exclude=True)

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        manual = self.source["kind"] == "manual"
        host = urlsplit(self.source["url"]).hostname
        return CallAssessment(
            risk=RiskLevel.SAFE if manual else RiskLevel.MODERATE,
            reads_private_data=manual,
            egress=not manual,
            egress_target=host,
            egress_configured=True,
            target=host,
            summary="Read registered research source",
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        if kwargs.get("source_id") != self.source["id"]:
            return ToolResult.fail("Source is not registered for this step")
        try:
            value = await self.fetcher.fetch(self.source, self.previous)
            return ToolResult(output=json.dumps(value.model_dump(), ensure_ascii=False))
        except Exception as exc:
            self.last_error = exc
            return ToolResult.fail("The registered source could not be read")
