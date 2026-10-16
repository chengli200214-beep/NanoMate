"""One host-owned coordinator; research owns its own execution and Sentinel checks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nanomuse.assistant.ledger import EXECUTION_ADAPTERS, Ledger
from nanomuse.assistant.research_link import ResearchLink
from nanomuse.research.orchestrator import ResearchOrchestrator

logger = logging.getLogger(__name__)


class Coordinator:
    def __init__(self, ledger: Ledger, research: ResearchOrchestrator):
        self.ledger = ledger
        self.research = research
        self._task: asyncio.Task[None] | None = None
        self.personal: Any = None
        self.research_link: ResearchLink | None = None
        self.service: Any = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="assistant-ledger")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.personal is not None:
            await self.personal.stop()

    async def _loop(self) -> None:
        while True:
            try:
                self.tick()
                if self.personal is not None:
                    self.personal.pulse()
                if self.research_link is not None and self.service is not None:
                    from nanomuse.assistant.delivery import deliver_research

                    for item in self.research_link.pending_deliveries():
                        await deliver_research(self.service, item)
                        self.research_link.delivered(item["id"])
            except Exception:
                logger.exception("Assistant coordinator pass failed")
            await asyncio.sleep(1)

    def tick(self) -> None:
        if self.research_link is not None:
            self.research_link.recover()
        self.ledger.fire_due()
        for step in self.ledger.bound():
            if step["spec"]["adapter"] != "research":
                continue
            try:
                self._sync(step)
            except (KeyError, ValueError):
                self.ledger.reconcile(
                    step["id"],
                    "needs_attention",
                    {**step["result"], "error": "Module record unavailable"},
                )
        claimed = self.ledger.claim()
        if claimed:
            self._dispatch(claimed)
        self.ledger.settle()

    def _dispatch(self, step: dict[str, Any]) -> None:
        spec = step["spec"]
        if spec["adapter"] == "checkpoint":
            self.ledger.finish_claim(step, "waiting_user", {})
            return
        if spec["adapter"] != "research":
            if self.personal is None:
                self.ledger.finish_claim(
                    step, "needs_attention", {"error": "Personal runtime unavailable"}
                )
            elif spec["adapter"] == "preference" and not self.ledger.preference_authorized(
                step["id"]
            ):
                self.ledger.finish_claim(
                    step, "waiting_user", {"requires_preference_confirmation": True}
                )
            elif spec["adapter"] in EXECUTION_ADAPTERS and not self.ledger.preference_authorized(
                step["id"]
            ):
                self.ledger.finish_claim(
                    step, "waiting_user", {"requires_execution_confirmation": True}
                )
            else:
                self.ledger.finish_claim(step, "bound", {"adapter": spec["adapter"]})
            return
        try:
            # The durable step is the dispatch outbox. Its ID never changes on retry.
            # A crash after enqueue but before binding reuses the domain request key.
            key = f"assistant:{step['id']}"
            run = self.research_link.run_for_step(step) if self.research_link is not None else None
            run = run or next(
                (
                    r
                    for r in self.research.store.runs(spec["project_id"])
                    if r["request_key"] == key
                ),
                None,
            )
            if run is None:
                project = self.research.store.project(spec["project_id"])
                if (spec.get("spec_id") and spec["spec_id"] != project["spec_id"]) or (
                    spec.get("input_version") and spec["input_version"] != project["input_version"]
                ):
                    raise ValueError("Research inputs changed")
                run = self.research.store.enqueue(spec["project_id"], spec["kind"], key)
            state = self.ledger.get(step["task_id"])["status"]
            if state == "cancelling" and run["status"] not in {"succeeded", "cancelled"}:
                run = self.research.store.control(run["id"], "cancel")
            elif state == "paused" and run["status"] in {"queued", "running"}:
                run = self.research.store.control(run["id"], "pause")
            self.ledger.finish_claim(
                step, "bound", {"run_id": run["id"], "project_id": spec["project_id"]}
            )
            self.research.wake()
        except (KeyError, ValueError):
            self.ledger.finish_claim(
                step, "needs_attention", {"error": "Research dispatch rejected"}
            )

    def _sync(self, step: dict[str, Any]) -> None:
        run = self.research.store.run(step["result"]["run_id"])
        state = step["task_status"]
        if state == "cancelling" and run["status"] not in {"succeeded", "cancelled"}:
            run = self.research.store.control(run["id"], "cancel")
        elif state == "paused" and run["status"] in {"queued", "running"}:
            run = self.research.store.control(run["id"], "pause")
        elif state in {"queued", "running"} and run["status"] == "paused":
            run = self.research.store.control(run["id"], "resume")
        if run["status"] in {"succeeded", "cancelled", "failed", "needs_attention"}:
            project = self.research.store.get("project", run["project_id"])
            current = (
                run["spec_id"] == project["spec_id"]
                and run["input_version"] == project["input_version"]
            )
            status = "completed" if run["status"] == "succeeded" and current else "needs_attention"
            reports = [
                {"id": r["id"], "hash": r["hash"]}
                for r in self.research.store.list("report_revision", run["project_id"])
                if r["run_id"] == run["id"]
            ]
            self.ledger.reconcile(
                step["id"],
                status,
                {
                    **step["result"],
                    "domain_status": run["status"],
                    "reports": reports,
                    "inputs_current": current,
                },
            )
        self.research.wake()
