"""Local main-chat projections of the research module's durable notice outbox."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import quote

from nanomuse.server.events import MAIN_THREAD


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
