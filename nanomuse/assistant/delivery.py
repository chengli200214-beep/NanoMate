"""Local main-chat projections of the research module's durable notice outbox."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import quote

from nanomuse.server.events import MAIN_THREAD


async def deliver_task(service: Any, item: dict[str, Any]) -> None:
    """A deterministic task-state notice, with no new model turn or user authority."""
    thread_id = item["origin"]["thread"]
    if thread_id not in service.threads:
        raise ValueError("The origin thread no longer exists")
    timeline = service.timeline(thread_id)
    payload_hash = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
    pending = getattr(service, "_assistant_task_notice_pending", None)
    if pending is None:
        pending = set()
        service._assistant_task_notice_pending = pending
    existing = timeline.get(item["id"])
    if existing is not None:
        if (
            existing.get("source") != "assistant_task"
            or existing.get("source_content_sha256") != payload_hash
        ):
            raise ValueError("Task notification ID already used with different content")
        await timeline.add_durable(existing)
        if item["id"] in pending:
            service.bus.publish({"kind": "event", "event": existing})
            pending.discard(item["id"])
        return
    task = item["task"]
    thread = service.threads.get(thread_id)
    language = (
        getattr(getattr(thread, "agent", None), "ui_language", "")
        or service.settings.agent.language
    )
    zh = language.lower().startswith("zh")
    title = str(task["title"]).replace("\r", " ").replace("\n", " ")[:500]
    title = "".join("\\" + c if c in "\\`*_{}[]<>#|" else c for c in title)
    heading = (
        ("阶段任务已完成，后续规划待处理" if item["continue"] else "任务已完成")
        if zh
        else ("Phase completed, further planning pending" if item["continue"] else "Task completed")
    )
    label = "查看任务及已核验产物" if zh else "View task and verified artifacts"
    text = f"{heading}: {title}\n\n[{label}](/?tab=tasks&task={quote(task['id'], safe='')})"
    if item.get("blocked_reason"):
        heading = "后续规划已停止，需要检查" if zh else "Further planning stopped, review required"
        reasons = {
            "origin_request_changed": (
                "对话中有新的用户请求",
                "A newer user request changed the conversation",
            ),
            "continuation_deadline": ("已达到后续规划时限", "The planning deadline was reached"),
            "continuation_limit": (
                "已达到后续规划次数上限",
                "The planning continuation limit was reached",
            ),
            "interrupted_planning": (
                "上次规划可能被中断，不会自动重试",
                "Previous planning may have been interrupted and will not be retried automatically",
            ),
        }
        reason = reasons.get(
            item["blocked_reason"],
            (
                "请求、模型或执行授权需要重新检查",
                "The request, model or execution allowance needs review",
            ),
        )[0 if zh else 1]
        text = f"{heading}: {title}\n\n{reason}\n\n[{label}](/?tab=tasks&task={quote(task['id'], safe='')})"
    event = {
        "id": item["id"],
        "type": "assistant",
        "thread": thread_id,
        "text": text,
        "source": "assistant_task",
        "source_content_is_untrusted": True,
        "source_content_sha256": payload_hash,
        "assistant_task": {
            "task_id": task["id"],
            "revision": task["revision"],
            "status": task["status"],
            "request_scope_id": item["origin"]["scope_id"],
        },
    }
    pending.add(item["id"])
    await timeline.add_durable(event)
    if item["id"] in pending:
        service.bus.publish({"kind": "event", "event": timeline.get(item["id"])})
        pending.discard(item["id"])


async def deliver_research(service: Any, item: dict[str, Any]) -> None:
    """Persist before acknowledgement, without starting a model turn or sending mail.

    The result remains source material. Its body is not appended as a user message,
    and does not grant permissions. The agent can inspect its versioned task/report
    through assistant_tasks on the next user turn.
    """
    timeline = service.timeline(MAIN_THREAD)
    pending = getattr(service, "_assistant_notice_pending", None)
    if pending is None:
        pending = set()
        service._assistant_notice_pending = pending
    payload_hash = hashlib.sha256(
        json.dumps(item, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    existing = timeline.get(item["id"])
    if existing is not None:
        if (
            existing.get("source") != "research"
            or existing.get("source_content_sha256") != payload_hash
        ):
            raise ValueError("Research notification ID already used with different content")
        # Rendering belongs to the first delivery attempt. A later language change
        # must not change an immutable event or block this outbox and later notices.
        await timeline.add_durable(existing)
        if item["id"] in pending:
            service.bus.publish({"kind": "event", "event": existing})
            pending.discard(item["id"])
        return
    pending.add(item["id"])
    language = service.settings.agent.language
    main = getattr(service, "threads", {}).get(MAIN_THREAD)
    if main is not None:
        language = getattr(main.agent, "ui_language", "") or language
    zh = str(language).lower().startswith("zh")
    is_report = item["type"] == "research_report"
    heading = (
        ("调研报告已更新" if is_report else "调研发现重要变化")
        if zh
        else ("Research report updated" if is_report else "Important research change")
    )
    label = "打开调研项目" if zh else "Open research project"
    link = f"/?tab=research&project={quote(item['project_id'], safe='')}"
    # Quote the untrusted body as data, preventing a source from constructing local
    # navigation links or masquerading as the surrounding system notification.
    title = str(item["title"]).replace("\r", " ").replace("\n", " ")[:500]
    title = "".join("\\" + c if c in "\\`*_{}[]<>#|" else c for c in title)
    content = str(item["content"])[:4000]
    quoted = "\n".join(
        "> " + "".join("\\" + c if c in "\\`*_{}[]<>#|" else c for c in line)
        for line in content.splitlines()
    )
    text = f"{heading}: {title}\n\n{quoted}\n\n[{label}]({link})"
    event = {
        "id": item["id"],
        "type": "assistant",
        "thread": MAIN_THREAD,
        "text": text,
        "source": "research",
        "research": {
            key: item.get(key, "") for key in ("project_id", "task_id", "report_id", "event_id")
        },
        "source_content_is_untrusted": True,
        "source_content_sha256": payload_hash,
    }
    await timeline.add_durable(event)
    if item["id"] in pending:
        service.bus.publish({"kind": "event", "event": timeline.get(item["id"])})
        pending.discard(item["id"])
