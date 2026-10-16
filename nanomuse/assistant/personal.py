"""Serial personal-domain execution through the host Sentinel, without an LLM loop."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from nanomuse.assistant.documents import DocumentArtifacts, DocumentSpec, prepare
from nanomuse.assistant.ledger import Ledger, Plan
from nanomuse.assistant.preferences import PreferenceAction, PreferenceStore
from nanomuse.assistant.reminders import ReminderAction, ReminderActions
from nanomuse.schema import Function, RiskLevel, ToolCall, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment

PERSONAL_ADAPTERS = {"reminder", "document", "preference"}


class PersonalTool(BaseTool):
    name: str
    description: str = "Apply the current user task's immutable personal action."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    risk: RiskLevel = RiskLevel.MODERATE
    reads_private_data: bool = True
    runtime: Any
    step: dict[str, Any]

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        return CallAssessment(
            risk=self.risk,
            reads_private_data=True,
            target=f"{self.step['task_id']}:{self.step['revision']}:{self.step['id']}",
            summary=f"{self.step['spec']['adapter']}: {self.step['spec']['title']}",
        )

    async def execute(self, **_: Any) -> ToolResult:
        # The immutable step is re-read after any approval wait, before the first effect.
        if not self.runtime.ledger.personal_state(self.step["id"]):
            return ToolResult.fail("Task paused, cancelled or replaced before execution")
        if self.step["spec"][
            "adapter"
        ] == "preference" and not self.runtime.ledger.preference_authorized(self.step["id"]):
            return ToolResult.fail("Preference intent needs user confirmation")
        result = self.runtime.apply(self.step)
        return ToolResult(output=json.dumps(result, ensure_ascii=False))


class PersonalRuntime:
    def __init__(self, ledger: Ledger, service: Any):
        self.ledger = ledger
        self.service = service
        self.workspace = Path(service.settings.agent.workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.documents = DocumentArtifacts(service.data_dir / "assistant", self.workspace)
        self.preferences = PreferenceStore(
            service.data_dir / "assistant" / "preferences.sqlite3", service.app.memory
        )
        self.reminders = ReminderActions(service.app.reminders)
        self._worker: asyncio.Task[None] | None = None
        self._step_id = ""

    def validate(self, plan: Plan) -> None:
        for step in plan.steps:
            if step.document is not None:
                step.document = prepare(step.document, self.workspace)
            if step.reminder is not None and step.reminder.action != "create":
                current = self.service.app.reminders.get(step.reminder.reminder_id)
                if current is None:
                    raise KeyError("Reminder not found")
                if (
                    current.revision != step.reminder.expected_revision
                    or current.status != "active"
                ):
                    raise ValueError("Reminder changed; review its current revision")
            if step.preference is not None and self.service.app.memory is None:
                raise ValueError("Enable memory before creating a personal preference task")

    def pulse(self) -> None:
        self.preferences.recover()
        bound = [s for s in self.ledger.bound() if s["spec"]["adapter"] in PERSONAL_ADAPTERS]
        if self._worker is not None and not self._worker.done():
            current = next((s for s in bound if s["id"] == self._step_id), None)
            if current is None or current["task_status"] in {"paused", "cancelling", "cancelled"}:
                self._worker.cancel()
            return
        for step in bound:
            if step["task_status"] == "cancelling":
                status, evidence = self.cancel_evidence(step)
                self.ledger.reconcile(step["id"], status, evidence)
                continue
            if step["task_status"] == "paused":
                continue
            self._step_id = step["id"]
            self._worker = asyncio.create_task(self._run(step), name="assistant-personal-action")
            break

    def cancel_evidence(self, step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Observe committed receipts before reporting what cancellation prevented."""
        adapter = step["spec"]["adapter"]
        key = f"assistant:{step['id']}"
        path: str | Path
        if adapter == "reminder":
            path, query = (
                self.reminders.store.path,
                "SELECT result FROM reminder_operations WHERE request_key=?",
            )
        elif adapter == "preference":
            path, query = (
                self.preferences.path,
                "SELECT result FROM preference_receipt WHERE request_key=?",
            )
        else:
            path, query = (
                self.documents.path,
                "SELECT metadata,published FROM artifact WHERE request_key=?",
            )
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(query, (key,)).fetchone()
        if row is None:
            return "cancelled", {"adapter": adapter, "cancelled_before_execution": True}
        if adapter != "document":
            receipt = json.loads(row["result"])
            result = {"preference": receipt} if adapter == "preference" else receipt
            return "completed", {"adapter": adapter, "recovered_receipt": True, **result}
        metadata = json.loads(row["metadata"])
        try:
            verified = self.documents._verified(metadata)
            # Only acknowledge already-existing bytes, never resume publication after cancel.
            with self.documents._transaction() as db:
                self.documents._mark_published(db, metadata["id"])
            return "completed", {
                "adapter": adapter,
                "artifact": verified,
                "recovered_receipt": True,
            }
        except (ValueError, OSError):
            return "outcome_unknown", {
                "adapter": adapter,
                "artifact": {**metadata, "verified": False},
                "error": "Publication intent exists but its file could not be verified",
            }

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def _run(self, step: dict[str, Any]) -> None:
        sentinel = self.service.app.sentinel
        token = sentinel.begin_task(
            purpose=step["spec"]["title"],
            task_id=f"{step['task_id']}:{step['revision']}",
            conversation=f"assistant:{step['task_id']}",
        )
        try:
            if not self.ledger.personal_state(step["id"], waiting=True):
                return
            tool = PersonalTool(
                name="assistant_" + step["spec"]["adapter"], runtime=self, step=step
            )
            result = await sentinel.guard(
                ToolCall(function=Function(name=tool.name, arguments="{}")), tool
            )
            if result.error:
                self.ledger.reconcile(
                    step["id"],
                    "needs_attention",
                    {"error": "Personal action rejected or could not be verified"},
                )
            else:
                self.ledger.reconcile(step["id"], "completed", json.loads(result.output))
                self.service.bus.publish({"kind": "reminders"})
                self.service.bus.publish({"kind": "memory"})
        except asyncio.CancelledError:
            raise
        except Exception:
            self.ledger.reconcile(
                step["id"], "needs_attention", {"error": "Personal action could not be verified"}
            )
        finally:
            sentinel.end_task(token)

    def apply(self, step: dict[str, Any]) -> dict[str, Any]:
        spec = step["spec"]
        key = f"assistant:{step['id']}"
        adapter = spec["adapter"]
        if adapter == "reminder":
            result = self.reminders.execute(ReminderAction.model_validate(spec["reminder"]), key)
            return {"adapter": adapter, **result, "verification": "reminder_configuration"}
        if adapter == "document":
            artifact = self.documents.execute(
                DocumentSpec.model_validate(spec["document"]),
                key,
                step["task_id"],
                step["revision"],
                step["id"],
            )
            return {"adapter": adapter, "artifact": artifact, "verification": "file_sha256"}
        if adapter == "preference":
            result = self.preferences.execute(
                PreferenceAction.model_validate(spec["preference"]),
                key,
                step["task_id"],
                step["revision"],
                step["id"],
            )
            return {
                "adapter": adapter,
                "preference": result,
                "verification": "confirmed_memory_projection",
            }
        raise ValueError("Unknown personal adapter")
