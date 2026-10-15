from __future__ import annotations

from typing import Any

import pytest

from nanomuse.llm.mock import MockLLM
from nanomuse.research.reasoning import FixtureReasoner, ResearchReasoner
from nanomuse.schema import LLMResponse


def context(content: str) -> dict[str, Any]:
    return {
        "spec": {
            "title": "Fixture excerpts",
            "goal": "Inspect saved source text",
            "candidates": ["https://alpha.example/project", "https://beta.example/project"],
            "mode": "fixture",
        },
        "snapshots": [
            {
                "id": "snapshot-a",
                "candidate": "https://alpha.example/project",
                "url": "https://alpha.example/project/README.md",
                "version": "fixture-v1",
                "content": content,
            }
        ],
        "evidence": [],
        "decisions": [],
        "changes": [],
    }


async def test_fixture_skips_decorative_table_and_selects_first_readable_paragraph() -> None:
    paragraph = (
        "Downloads are listed in the release page.\nThe desktop and web clients share one account."
    )
    content = (
        "## Install\n\n| | |\n|---|---|\n"
        "| **Windows** | [Download](https://downloads.example/app) |\n\n"
        + paragraph
        + "\n\n## Features\nAnother paragraph.\n"
    )
    result = await FixtureReasoner().extract(context(content))
    assert len(result) == 1
    assert result[0].quote == paragraph and result[0].fact == paragraph
    assert result[0].quote in content


async def test_fixture_keeps_wrapped_markdown_paragraph_instead_of_half_a_sentence() -> None:
    paragraph = (
        "**This project is a self-hosted assistant for people exploring\n"
        "coding-agent workflows.** It runs tasks from a web console,\n"
        "records approvals, and supports scheduled prompts."
    )
    content = "# Project\n\n" + paragraph + "\n\nBoth interfaces use the same API.\n"
    result = await FixtureReasoner().extract(context(content))
    assert result[0].quote == paragraph
    assert "\n" in result[0].quote and result[0].quote in content


@pytest.mark.parametrize(
    "decoration",
    [
        "![Logo](https://images.example/logo.png)\n",
        "[![Build](https://images.example/badge.svg)](https://ci.example/project)\n",
        '<div align="center"><img src="https://images.example/logo.png"></div>\n',
        "<!--\nHidden build badge instructions.\n-->\n",
        "```sh\necho example\n```\n",
        "Project title\n=============\n",
        "[download]: https://downloads.example/app\n",
        "---\n| | |\n| :--- | ---: |\n",
    ],
)
async def test_fixture_ignores_markup_and_decorative_blocks(decoration: str) -> None:
    paragraph = "The runtime stores tasks locally."
    content = decoration + "\n## Runtime\n" + paragraph
    result = await FixtureReasoner().extract(context(content))
    assert result[0].quote == paragraph and result[0].quote in content


async def test_fixture_preserves_original_crlf_between_adjacent_lines() -> None:
    paragraph = "The runtime stores tasks locally.\r\nIt can resume saved work."
    content = "# Project\r\n\r\n" + paragraph + "\r\n\r\nOther text."
    result = await FixtureReasoner().extract(context(content))
    assert result[0].quote == paragraph


async def test_fixture_skips_sources_without_any_readable_paragraph() -> None:
    content = "# Project\n\n| | |\n|---|---|\n![Logo](https://images.example/logo.svg)\n<div></div>"
    result = await FixtureReasoner().extract(context(content))
    assert result == []


async def test_fixture_excerpt_remains_an_exact_substring_within_1000_characters() -> None:
    content = "# Project\n\n" + "A complete statement about the runtime. " * 50
    result = await FixtureReasoner().extract(context(content))
    quote = result[0].quote
    assert len(quote) <= 1000 and quote in content and quote.endswith(".")


async def test_fixture_selection_does_not_change_live_model_extract() -> None:
    # A configured model chooses the exact quote. The fixture-only formatting filter
    # must not be applied to its result, even when it quoted a decorative source line.
    llm = MockLLM(
        [
            LLMResponse(
                content='[{"snapshot_id":"snapshot-a",'
                '"candidate":"https://alpha.example/project",'
                '"dimension":"overview","fact":"Table structure",'
                '"quote":"| | |","verification":"documentation"}]'
            )
        ]
    )
    result = await ResearchReasoner(llm).extract(context("# Project\n| | |\n"))
    assert result[0].quote == "| | |"
