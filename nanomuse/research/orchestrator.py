"""Bounded research steps with durable progress and lease renewal."""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import os
from collections.abc import Callable
from typing import Any

from nanomuse.llm.base import BaseLLM
from nanomuse.research.clock import iso
from nanomuse.research.contracts import FetchedSource, ProjectSpec, Reasoner, SourceFetcher
from nanomuse.research.faults import crash_point
from nanomuse.research.store import ResearchStore, fingerprint, uid
from nanomuse.schema import Function, ToolCall
from nanomuse.sentinel import Sentinel


class NeedsAttention(Exception):
    """A user must supply configuration or extend a budget."""


class ResearchOrchestrator:
    def __init__(
        self,
        store: ResearchStore,
        llm: Callable[[], BaseLLM] | None = None,
        reasoner: Reasoner | None = None,
        fetcher: SourceFetcher | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        sentinel: Sentinel | None = None,
    ) -> None:
        self.store = store
        self.llm = llm
        self.reasoner = reasoner
        self.fetcher = fetcher
        self.on_event = on_event
        self.sentinel = sentinel
        self.owner = uid("worker")
        self.task: asyncio.Task[None] | None = None
        self._reasoners: dict[str, Reasoner] = {}
        self._wake = asyncio.Event()

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop(), name="research-worker")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE research_step SET status='pending',lease_until=0 "
                "WHERE status='running' AND lease_owner=?",
                (self.owner,),
            )

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            self.schedule_due()
            if not await self.tick():
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), 1)

    def schedule_due(self) -> None:
        now = self.store.clock.now()
        for project in self.store.list("project"):
            tracking = project["tracking"]
            if not tracking["enabled"] or tracking["next_check_at"] > now:
                continue
            with self.store.transaction():
                project = self.store.get("project", project["id"])
                tracking = project["tracking"]
                due = tracking["next_check_at"]
                if due > now:
                    continue
                active = any(
                    r["status"] in {"queued", "running", "paused", "needs_attention"}
                    for r in self.store.runs(project["id"])
                )
                if active:
                    continue
                tracking["next_check_at"] = now + tracking["interval_minutes"] * 60
                if tracking.get("daily_time"):
                    from nanomuse.research.api import next_check

                    tracking["next_check_at"] = next_check(now, tracking)
                self.store._put("project", project)
                self.store.enqueue(project["id"], "check", f"schedule:{due}", due, now)

    async def _renew(self, step: dict[str, Any]) -> None:
        while True:
            await asyncio.sleep(10)
            if not self.store.renew(step):
                return

    async def tick(self) -> bool:
        step = self.store.claim(self.owner)
        if step is None:
            return False
        crash_point("after_claim")
        heartbeat = asyncio.create_task(self._renew(step))
        reasoner: Reasoner | None = None
        calls_before = 0
        task_token = (
            self.sentinel.begin_task(
                purpose="Research registered project sources",
                conversation=f"research:{step['project_id']}",
            )
            if self.sentinel
            else None
        )
        try:
            project = self.store.project(step["project_id"])
            run = self.store.run(step["run_id"])
            saved_spec = self.store.get("spec_revision", run["spec_id"])
            spec = ProjectSpec.model_validate(
                {k: saved_spec[k] for k in ProjectSpec.model_fields}
            ).model_dump()
            if step["stage"] != "collect":
                collected = [s for s in run["steps"] if s["stage"] == "collect"]
                if (
                    step["stage"] == "extract"
                    and run["kind"] == "check"
                    and collected
                    and all(
                        s["status"] == "succeeded"
                        and not s["output"].get("changed", True)
                        and not s["error"]
                        for s in collected
                    )
                    and project["current_report"] is not None
                    and not project["current_report"]["stale"]
                ):
                    self.store.complete(step, {"no_changes": True})
                    return True
                calls = [
                    c
                    for c in self.store.list("model_call", project["id"])
                    if c["run_id"] == run["id"]
                ]
                needs_model = step["stage"] != "publish" or bool(
                    self._context(project["id"], spec)["changes"]
                )
                if needs_model and len(calls) >= project["tracking"]["max_calls"]:
                    raise NeedsAttention(
                        "Model call budget reached. Increase the budget and retry."
                    )
                if needs_model:
                    reasoner = self._get_reasoner(project, run["id"])
                    if hasattr(reasoner, "set_call_budget"):
                        reasoner.set_call_budget(project["tracking"]["max_calls"] - len(calls))
                    if hasattr(reasoner, "set_call_observer"):
                        reasoner.set_call_observer(
                            lambda call: self.store.record_calls(
                                step["project_id"], step["run_id"], step["id"], [call]
                            )
                        )
                    calls_before = len(reasoner.calls)
            output = await asyncio.wait_for(self._execute(step, run, spec, reasoner), 180)
            crash_point("before_step_commit")
            committed = self.store.complete(step, output)
            crash_point("after_step_commit")
            if committed and self.on_event:
                self.on_event(
                    {
                        "type": "research",
                        "project_id": project["id"],
                        "run_id": run["id"],
                        "stage": step["stage"],
                    }
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            retryable = bool(getattr(exc, "retryable", False)) or isinstance(exc, TimeoutError)
            attention = isinstance(exc, NeedsAttention) or getattr(exc, "code", "") in {
                "auth",
                "authentication",
                "model_budget",
                "context_budget",
            }
            # Provider errors may include request details. Keep credentials out of stored diagnostics.
            message = str(exc)[:500]
            for secret_name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GH_TOKEN"):
                secret = os.environ.get(secret_name)
                if secret:
                    message = message.replace(secret, "[redacted]")
            self.store.fail(
                step,
                f"{type(exc).__name__}: {message}",
                retryable,
                attention,
                float(getattr(exc, "retry_after", None) or 1),
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            if reasoner:
                self.store.record_calls(
                    step["project_id"], step["run_id"], step["id"], reasoner.calls[calls_before:]
                )
            if self.sentinel and task_token is not None:
                self.sentinel.end_task(task_token)
            if self.store.run(step["run_id"])["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "needs_attention",
            }:
                self._reasoners.pop(step["run_id"], None)
        return True

    def _get_reasoner(self, project: dict[str, Any], run_id: str) -> Reasoner:
        if self.reasoner:
            return self.reasoner
        if run_id not in self._reasoners:
            from nanomuse.research.reasoning import FixtureReasoner, ResearchReasoner

            if project["mode"] == "fixture":
                self._reasoners[run_id] = FixtureReasoner(sentinel=self.sentinel)
            elif self.llm:
                self._reasoners[run_id] = ResearchReasoner(self.llm(), sentinel=self.sentinel)
            else:
                raise NeedsAttention("Configure a model in Settings, or create a fixture project.")
        return self._reasoners[run_id]

    def _context(self, project_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        project = self.store.get("project", project_id)
        snapshots = self.store.snapshots(project_id)
        readable = {
            s["id"] for s in snapshots if not self.store.get("source", s["source_id"]).get("error")
        }
        evidence = [
            e
            for e in self.store.list("evidence", project_id)
            if e["status"] == "active" and e["snapshot_id"] in readable
        ]
        active_ids = {e["id"] for e in evidence}
        return {
            "project_id": project_id,
            "spec": spec,
            "snapshots": [s for s in snapshots if s["id"] in readable],
            "evidence": evidence,
            "decisions": [
                d
                for d in self.store.list("decision_revision", project_id)
                if d["spec_id"] == project["spec_id"]
            ],
            "conflicts": [
                c
                for c in self.store.list("evidence_conflict", project_id)
                if c["status"] == "open" and set(c["evidence_refs"]) <= active_ids
            ],
            "changes": [
                e
                for e in self.store.list("impact_event", project_id)
                if e["status"] == "pending" and e["spec_id"] == project["spec_id"]
            ],
        }

    async def _execute(
        self,
        step: dict[str, Any],
        run: dict[str, Any],
        spec: dict[str, Any],
        reasoner: Reasoner | None,
    ) -> dict[str, Any]:
        project_id = step["project_id"]
        if step["stage"] == "collect":
            from nanomuse.research.sources import FixtureFetcher, HttpSourceFetcher

            source = self.store.get("source", step["source_id"])
            previous = next(
                (s for s in self.store.snapshots(project_id) if s["source_id"] == source["id"]),
                None,
            )
            if previous and source.get("etag"):
                previous = {**previous, "etag": source["etag"]}
            fetcher = self.fetcher or (
                FixtureFetcher() if spec["mode"] == "fixture" else HttpSourceFetcher()
            )
            if self.sentinel:
                from nanomuse.research.sources.tool import FetchRegisteredSource

                tool = FetchRegisteredSource(source=source, previous=previous, fetcher=fetcher)
                call = ToolCall(
                    function=Function(
                        name="read_source", arguments=json.dumps({"source_id": source["id"]})
                    )
                )
                result = await self.sentinel.guard(call, tool)
                if tool.last_error:
                    raise tool.last_error
                if result.error:
                    raise NeedsAttention("Permission to read the registered source was denied")
                fetched = FetchedSource.model_validate_json(result.output)
            else:
                fetched = await fetcher.fetch(source, previous)
            content = previous["content"] if fetched.not_modified and previous else fetched.content
            content_hash = fingerprint(content)
            snapshot_id = (
                "snapshot_"
                + fingerprint([source["id"], content_hash, fetched.version, fetched.url])[:20]
            )
            snapshot = {
                **fetched.model_dump(),
                "id": snapshot_id,
                "project_id": project_id,
                "source_id": source["id"],
                "candidate": source["candidate"],
                "content": content,
                "content_hash": content_hash,
                "fetched_at": fetched.fetched_at or iso(self.store.clock.now()),
                "checked_at": iso(self.store.clock.now()),
                "kind": source["kind"],
            }
            return {"snapshot": snapshot, "previous_snapshot": previous["id"] if previous else None}

        context = self._context(project_id, spec)
        if reasoner is None and step["stage"] != "publish":
            raise NeedsAttention("Research model is not configured")
        if step["stage"] == "extract":
            assert reasoner is not None
            if not context["snapshots"]:
                raise NeedsAttention(
                    "No source could be read. Add a source or retry after restoring access."
                )
            needs_verification = {
                e["snapshot_id"]
                for e in self.store.list("evidence", project_id)
                if e["status"] == "stale"
            }
            existing = {e["snapshot_id"] for e in context["evidence"]} - needs_verification
            selected = [s for s in context["snapshots"] if s["id"] not in existing]
            if not selected:
                return {"evidence": []}
            context["snapshots"] = selected
            drafts = await reasoner.extract(context)
            return {
                "evidence": [e.model_dump() for e in drafts],
                "verified_snapshots": [s["id"] for s in selected],
            }
        if step["stage"] == "compare":
            assert reasoner is not None
            comparison = await reasoner.compare(context)
            gaps = [
                f"Source unavailable / 来源未能读取: {s['title'] or s['url']}. {s['error']}"
                for s in self.store.list("source", project_id)
                if s["enabled"] and s.get("error")
            ]
            if gaps:
                comparison = comparison.model_copy(update={"unknowns": comparison.unknowns + gaps})
            return {"comparison": comparison.model_dump()}
        if step["stage"] == "publish":
            from nanomuse.research.contracts import Comparison
            from nanomuse.research.reports import render_report

            compared = next(s for s in run["steps"] if s["stage"] == "compare")
            comparison = Comparison.model_validate(compared["output"]["comparison"])
            markdown = render_report(spec, comparison, context["evidence"])
            report_id = "report_" + fingerprint([run["id"], markdown])[:20]
            reports_dir = self.store.directory / "reports" / project_id
            reports_dir.mkdir(parents=True, exist_ok=True)
            path = reports_dir / f"{report_id}.md"
            if not path.exists():
                temp = path.with_suffix(f".{step['attempt']}.tmp")
                temp.write_text(markdown, encoding="utf-8")
                crash_point("after_report_write")
                temp.replace(path)
            elif path.read_text("utf-8") != markdown:
                raise RuntimeError("Report hash does not match existing file")
            events = []
            for event in context["changes"]:
                if reasoner is None:
                    raise NeedsAttention("Configure a model to assess changed sources")
                before = self.store.get("source_snapshot", event["before_snapshot"])
                after = self.store.get("source_snapshot", event["after_snapshot"])
                impact_context = {
                    **context,
                    "changes": [{**event, "before": before, "after": after}],
                }
                judgment = await reasoner.impact(impact_context)
                events.append({**event, **judgment.model_dump(), "status": "evaluated"})
            decisions = [d["id"] for d in context["decisions"] if d.get("run_id") == run["id"]]
            return {
                "report": {
                    "id": report_id,
                    "project_id": project_id,
                    "spec_id": run["spec_id"],
                    "input_version": run["input_version"],
                    "run_id": run["id"],
                    "markdown": markdown,
                    "path": str(path),
                    "hash": fingerprint(markdown),
                    "decision_refs": decisions,
                    "created_at": iso(self.store.clock.now()),
                    "stale": False,
                    "mode": spec["mode"],
                },
                "events": events,
            }
        raise ValueError("Unknown research stage")


def report_diff(before: str, after: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(), fromfile="previous", tofile="current"
        )
    )
