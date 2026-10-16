"""Pure Markdown rendering and text diffs. Report publication belongs to the store."""

from __future__ import annotations

import difflib
import html
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, urlsplit

from nanomuse.research.context import (
    ResearchValidationError,
    bind_recommendation,
    constraint_verdict,
    evidence_is_current,
    record,
    require_unknown_for_conflicts,
    validate_conflicts,
    validate_decision_support,
)
from nanomuse.research.contracts import Comparison, ProjectSpec

_VERDICTS = {
    "meets": "Meets / 满足",
    "fails": "Fails / 不满足",
    "unknown": "Unknown / 无法确认",
    "preference": "Preference / 偏好",
}
_VERIFICATIONS = {
    "documentation": "Documentation claim / 文档声明",
    "source": "Source observation / 源码观察",
    "inference": "Inference / 推断",
    "unknown": "Unknown / 无法确认",
    "user_correction": "User correction, awaiting verification / 用户纠正，待独立核验",
}


def _text(value: Any) -> str:
    text = html.escape(str(value), quote=False)
    text = text.replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_[\]{}|])", r"\\\1", text)


def _link(label: Any, url: Any) -> str:
    target = str(url)
    parsed = urlsplit(target)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return "Source URL unavailable / 来源地址缺失"
    encoded = quote(target, safe=":/?#@!$&'*=+%,;~.-_")
    return f"[{_text(label)}](<{encoded}>)"


def render_report(
    spec: ProjectSpec | dict[str, Any],
    comparison: Comparison | dict[str, Any],
    evidence: Iterable[dict[str, Any]],
) -> str:
    """Six fixed sections with explicit uncertainty and version-bound citations."""
    project = ProjectSpec.model_validate(record(spec))
    result = Comparison.model_validate(record(comparison))
    rows = {str(item["id"]): item for item in evidence}
    conflicts = validate_conflicts(
        result.conflicts,
        {
            "spec": project.model_dump(),
            "evidence": list(rows.values()),
        },
        check_snapshots=False,
    )
    decisions = require_unknown_for_conflicts(result.decisions, conflicts)
    result = bind_recommendation(
        project, result.model_copy(update={"conflicts": conflicts, "decisions": decisions})
    )
    dimensions = {item.id: item.text for item in project.constraints}
    dimensions["overview"] = "Overview / 概览"
    for decision in result.decisions:
        for ref in decision.evidence_refs:
            item = rows.get(ref)
            if item is None or item.get("candidate", "").rstrip("/") != decision.candidate.rstrip(
                "/"
            ):
                raise ResearchValidationError(
                    "Report contains an unbound or cross-candidate citation"
                )
            if not evidence_is_current(item):
                raise ResearchValidationError("Report contains an obsolete evidence citation")
        validate_decision_support(decision, [rows[ref] for ref in decision.evidence_refs], project)
    lines = [f"# {_text(project.title)}", ""]
    if project.mode == "fixture":
        lines.extend(
            [
                "> Offline scripted fixture / 离线脚本回放。Saved snapshots and scripted responses "
                "are not live model verification. / 固定快照和脚本输出不代表实时模型核验。",
                "",
            ]
        )
    lines.extend(
        [
            "## 1. Current recommendation / 当前推荐",
            "",
            _text(result.recommendation),
            "",
            _text(result.summary),
            "",
            "Goal / 目标: " + _text(project.goal),
            "",
            "## 2. Hard requirements / 硬条件检查",
            "",
            "| Candidate / 候选 | Requirement / 条件 | Verdict / 判断 | Evidence / 证据 |",
            "| --- | --- | --- | --- |",
        ]
    )
    hard = [item for item in project.constraints if item.hard]
    if not hard:
        lines.append(
            "| All / 全部 | No hard requirements supplied / 未提供硬条件 | Unknown / 无法确认 | |"
        )
    for candidate in project.candidates:
        for constraint in hard:
            matching = [
                item
                for item in result.decisions
                if item.candidate == candidate and constraint.id in item.constraint_refs
            ]
            refs = sorted({ref for item in matching for ref in item.evidence_refs})
            lines.append(
                f"| {_text(candidate)} | {_text(constraint.text)} | "
                f"{_VERDICTS[constraint_verdict(matching)]} | {_text(', '.join(refs))} |"
            )
    lines.extend(
        [
            "",
            "## 3. Candidate comparison / 候选对照",
            "",
            "| Candidate / 候选 | Dimension / 维度 | Conclusion / 结论 | Verdict / 判断 | Evidence / 证据 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for decision in result.decisions:
        lines.append(
            f"| {_text(decision.candidate)} | {_text(dimensions.get(decision.dimension, decision.dimension))} | "
            f"{_text(decision.conclusion)} | {_VERDICTS[decision.verdict]} | "
            f"{_text(', '.join(decision.evidence_refs))} |"
        )
    if not result.decisions:
        lines.append("| | | No current evidence / 暂无当前证据 | Unknown / 无法确认 | |")
    lines.extend(["", "## 4. Recommendation evidence / 推荐依据", ""])
    used = sorted(
        {ref for decision in result.decisions for ref in decision.evidence_refs}
        | {ref for conflict in result.conflicts for ref in conflict.evidence_refs}
    )
    if not used:
        lines.append("No cited evidence. / 暂无引用证据。")
    for ref in used:
        item = rows[ref]
        verification = _VERIFICATIONS.get(
            str(item.get("verification", "unknown")), _VERIFICATIONS["unknown"]
        )
        lines.extend(
            [
                f"- **{_text(ref)}**: {_text(item.get('fact', ''))}",
                f"  - {_text(item.get('candidate', ''))}. {verification}.",
                "  - Snapshot / 快照: "
                + _text(item.get("snapshot_id", "unknown"))
                + ", version / 版本: "
                + _text(item.get("version") or "unknown / 未知")
                + ", fetched / 抓取时间: "
                + _text(item.get("fetched_at") or "unknown / 未知")
                + ".",
                "  - "
                + _link(
                    item.get("source_url") or item.get("url") or "Source / 来源",
                    item.get("source_url") or item.get("url") or "",
                ),
                "  - Locator / 位置: "
                + _text(item.get("locator") or "not provided / 未提供")
                + ".",
                "  - Quote / 原文: " + _text(item.get("quote", "")),
            ]
        )
    lines.extend(["", "## 5. Uncertainties / 不确定项", ""])
    for conflict in result.conflicts:
        lines.extend(
            [
                f"- **Unresolved evidence conflict / 未解决的证据冲突**: "
                f"{_text(conflict.candidate)}, {_text(dimensions.get(conflict.dimension, conflict.dimension))}.",
                "  - Reason / 原因: " + _text(conflict.reason),
                "  - Evidence / 双方证据: " + _text(", ".join(conflict.evidence_refs)),
                "  - Bound version scope / 绑定版本范围: " + _text(conflict.version_scope),
                "  - Keep the verdict unknown until verified. / 核验完成前，相关结论保持无法确认。",
            ]
        )
    unknowns = list(result.unknowns)
    for candidate in project.candidates:
        for constraint in hard:
            matching = [
                item
                for item in result.decisions
                if item.candidate == candidate and constraint.id in item.constraint_refs
            ]
            if constraint_verdict(matching) == "unknown":
                unknowns.append(f"{candidate}: {constraint.text}")
    unknowns.append(
        "Documentation claims and source observations are not runtime tests. / "
        "文档声明和源码观察不能证明运行结果。"
    )
    lines.extend("- " + _text(item) for item in dict.fromkeys(unknowns))
    lines.extend(["", "## 6. Next steps / 下一步", ""])
    next_steps = result.next_steps or [
        "Project owner: collect source evidence for unresolved requirements, then record "
        "the expected and observed results. / 项目负责人补充未知条件的证据，并记录预期与实际结果。"
    ]
    lines.extend(f"{index}. {_text(item)}" for index, item in enumerate(next_steps, 1))
    return "\n".join(lines) + "\n"


def diff_report(before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="previous.md",
            tofile="current.md",
        )
    )


__all__ = ["diff_report", "render_report"]
