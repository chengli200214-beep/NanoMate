"""Connection success alone cannot satisfy real research tool acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import scripts.research_model_smoke as smoke
from nanomuse.config import Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.schema import Function, LLMResponse, ToolCall


def args_for(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(config=None, data_dir=tmp_path / "app", output=tmp_path / "output")


async def test_no_local_key_makes_no_request_and_records_no_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smoke, "load_research_settings", lambda *a: Settings(data_dir=tmp_path))
    monkeypatch.setattr(smoke, "research_model_ready", lambda s: False)
    monkeypatch.setattr(smoke, "create_research_llm", lambda s: pytest.fail("created a client"))
    result = await smoke.run(args_for(tmp_path))
    assert result["status"] == "needs_configuration" and not result["calls"]
    assert result["tool_call_verified"] is False


@pytest.mark.parametrize("native", [True, False])
async def test_only_a_model_selected_valid_tool_call_satisfies_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native: bool,
) -> None:
    responses = (
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        function=Function(
                            name="read_source",
                            arguments=json.dumps({"snapshot_id": "connectivity_snapshot"}),
                        )
                    )
                ]
            )
        ]
        if native
        else [LLMResponse(content="Connection works."), LLMResponse(content="Done.")]
    )
    llm = MockLLM(responses)
    monkeypatch.setattr(smoke, "load_research_settings", lambda *a: Settings(data_dir=tmp_path))
    monkeypatch.setattr(smoke, "research_model_ready", lambda s: True)
    monkeypatch.setattr(smoke, "create_research_llm", lambda s: llm)
    result = await smoke.run(args_for(tmp_path))
    assert result["tool_call_verified"] is native
    assert result["status"] == ("passed" if native else "failed")
    assert len(result["calls"]) == (1 if native else 2)
    assert result["quality_score"] is None
    if native:
        audit = (tmp_path / "output/audit.jsonl").read_text("utf-8")
        assert '"tool": "read_source"' in audit
        assert '"ok": true' in audit
