"""Read public sources through Sentinel and retain real snapshots and conditional reads."""

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
from nanomuse.research.contracts import FetchedSource  # noqa: E402
from nanomuse.research.sources import HttpSourceFetcher  # noqa: E402
from nanomuse.research.sources.dns import DNS_MODES, CloudflareResolver  # noqa: E402
from nanomuse.research.sources.security import resolve_host  # noqa: E402
from nanomuse.research.sources.tool import FetchRegisteredSource  # noqa: E402
from nanomuse.schema import Function, ToolCall  # noqa: E402
from nanomuse.sentinel import AuditLog, Sentinel  # noqa: E402
from nanomuse.ui import HeadlessUI  # noqa: E402

REPOSITORIES = (
    "https://github.com/Anil-matcha/open-dots",
    "https://github.com/nano-muse/nanoMuse",
)
TARGETS = (
    ("readme", REPOSITORIES[0]),
    ("readme", REPOSITORIES[1]),
    ("metadata", REPOSITORIES[1]),
    ("releases", REPOSITORIES[1]),
    (
        "file",
        REPOSITORIES[1] + "/blob/a0d727fc04eafa74694a05af8aeac3d793efbafd/docs/architecture.md",
    ),
    ("document", "https://www.python-httpx.org/advanced/transports/"),
)


async def run(output: Path, dns: str, kinds: list[str] | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    audit = AuditLog(output / "audit.jsonl")
    sentinel = Sentinel(SentinelSettings(), audit, HeadlessUI(approve=False))
    fetcher = HttpSourceFetcher(
        resolver=CloudflareResolver() if dns == "cloudflare" else resolve_host,
    )
    rows = []
    selected = [(kind, url) for kind, url in TARGETS if not kinds or kind in kinds]
    for index, (kind, url) in enumerate(selected, 1):
        source = {
            "id": f"network_{index}",
            "project_id": "network_acceptance",
            "candidate": REPOSITORIES[0] if url == REPOSITORIES[0] else REPOSITORIES[1],
            "kind": kind,
            "url": url,
        }
        row: dict[str, Any] = {"kind": kind, "url": url, "status": "failed"}
        task = sentinel.begin_task("Verify public source transport", conversation="network_probe")
        try:
            saved = None
            for repeat in (False, True):
                tool = FetchRegisteredSource(source=source, previous=saved, fetcher=fetcher)
                call = ToolCall(
                    function=Function(
                        name="read_source", arguments=json.dumps({"source_id": source["id"]})
                    )
                )
                result = await sentinel.guard(call, tool)
                if result.error:
                    # The tool exposes classified, sanitized source errors.
                    raise RuntimeError(
                        str(tool.last_error) if tool.last_error else "Source read denied"
                    )
                fetched = FetchedSource.model_validate_json(result.output)
                if repeat:
                    row["repeat_not_modified"] = fetched.not_modified
                else:
                    saved = fetched.model_dump()
                    snapshot = f"{index:02d}-{kind}.json"
                    (output / snapshot).write_text(
                        json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                    )
                    row.update(
                        snapshot=snapshot,
                        version=fetched.version,
                        fetched_url=fetched.url,
                        chars=len(fetched.content),
                        etag_present=bool(fetched.etag),
                    )
            row["status"] = "passed" if row["repeat_not_modified"] else "changed_during_check"
        except Exception as exc:
            row["error"] = "Source read failed; consult the local Sentinel audit."
            row["error_code"] = getattr(
                getattr(tool, "last_error", None), "code", type(exc).__name__
            )
        finally:
            sentinel.end_task(task)
        rows.append(row)
        print(f"{kind} {url}: {row['status']}", flush=True)
    summary = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_dns": dns,
        "execution_mode": "live_network_without_model",
        "model_calls": 0,
        "passed": sum(row["status"] == "passed" for row in rows),
        "planned": len(selected),
        "audit_tool_calls": len(
            [entry for entry in audit.tail(100) if entry.get("event") == "tool_call"]
        ),
        "sources": rows,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dns", choices=DNS_MODES, default="system")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=sorted({kind for kind, _ in TARGETS}), action="append")
    args = parser.parse_args()
    summary = asyncio.run(run(args.output, args.source_dns, args.kind))
    print(f"Public source checks: {summary['passed']}/{summary['planned']}; no model calls.")
    return 0 if summary["passed"] == summary["planned"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
