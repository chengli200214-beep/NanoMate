"""Serial personal-domain execution through the host Sentinel, without an LLM loop."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from nanomuse.assistant.browser_actions import BrowserActions, BrowserSpec, prepare_download
from nanomuse.assistant.documents import DocumentArtifacts, DocumentSpec, prepare
from nanomuse.assistant.execution import ExecutionJournal
from nanomuse.assistant.file_actions import FileActions, FileSpec
from nanomuse.assistant.file_actions import prepare as prepare_file
from nanomuse.assistant.ledger import EXECUTION_ADAPTERS, Ledger, Plan
from nanomuse.assistant.native_actions import NativeActions, NativeSpec
from nanomuse.assistant.preferences import PreferenceAction, PreferenceStore
from nanomuse.assistant.reminders import ReminderAction, ReminderActions
from nanomuse.schema import Function, RiskLevel, ToolCall, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.computer import ComputerAct

PERSONAL_ADAPTERS = {"reminder", "document", "preference", *EXECUTION_ADAPTERS}
SURFACE_TOOLS = {"browser", "computer_screen", "computer_act", "computer_task"}


class NativeObservationTool(BaseTool):
    name: str = "assistant_observe_native"
    description: str = "Observe the Windows desktop for manual task recovery."
    risk: RiskLevel = RiskLevel.SAFE
    reads_private_data: bool = True
    driver: Any

    async def execute(self, **_: Any) -> ToolResult:
        return ToolResult(output=json.dumps(await self.driver.observe(), ensure_ascii=False))


class PersonalTool(BaseTool):
    name: str
    description: str = "Apply the current user task's immutable personal action."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    risk: RiskLevel = RiskLevel.MODERATE
    reads_private_data: bool = True
    runtime: Any
    step: dict[str, Any]

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        spec = self.step["spec"]
        if spec["adapter"] == "native":
            return self.runtime.native.assess(NativeSpec.model_validate(spec["native"]))
        if spec["adapter"] == "browser":
            return CallAssessment(
                risk=RiskLevel.SAFE, reads_private_data=True, summary=spec["title"]
            )
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
        if self.step["spec"]["adapter"] in EXECUTION_ADAPTERS:
            if not self.runtime.ledger.preference_authorized(self.step["id"]):
                return ToolResult.fail("Execution preview needs user approval")
            result = await self.runtime.apply_execution(self.step)
        else:
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
        self.files = FileActions(service.data_dir / "assistant", self.workspace)
        self.browser = BrowserActions(service, self.workspace, service.data_dir / "assistant")
        self.native = NativeActions(service, self.workspace)
        self.journal = ExecutionJournal(service.data_dir / "assistant" / "actions.sqlite3")
        self._surface_owner = ""
        self._stopping = False
        self._human_owner = ledger.manual_owner()
        ledger.control_hook = self.interrupt
        if getattr(service.app, "sentinel", None) is not None:
            service.app.sentinel.execution_fence = self.fence
        self._worker: asyncio.Task[None] | None = None
        self._step_id = ""
        self.browser.can_execute = self.can_execute
        self.native.can_execute = self.can_execute
        self.wire_fences()

    def wire_fences(self) -> None:
        computer = getattr(self.service.app, "computer", None)
        if computer is not None:
            computer.input_fence = lambda: self.fence("computer_act")
        act = self.service.app.tools.get("computer_act")
        if isinstance(act, ComputerAct):
            act.input_fence = lambda: self.fence("computer_act")
        browser = getattr(self.service, "browser", None)
        if browser is not None:
            browser.input_fence = lambda: self.fence("browser")

    def can_execute(self) -> bool:
        return bool(
            not self._human_owner and self._step_id and self.ledger.personal_state(self._step_id)
        )

    def validate(self, plan: Plan) -> None:
        planned_directories: set[str] = set()
        targets: set[str] = set()
        for position, step in enumerate(plan.steps):
            if step.browser is not None:
                step.browser = prepare_download(step.browser, self.workspace)
            if step.file is not None:
                target = step.file.path.casefold()
                if target in targets:
                    raise ValueError("A plan cannot write the same file target twice")
                targets.add(target)
                reference = step.file.content_from_step
                browser_source = (
                    plan.steps[reference].browser
                    if reference is not None and reference < position
                    else None
                )
                if reference is not None and (
                    reference >= position
                    or plan.steps[reference].adapter != "browser"
                    or browser_source is None
                    or browser_source.action != "extract"
                ):
                    raise ValueError("File content must reference an earlier browser extract step")
                step.file = prepare_file(
                    step.file, self.workspace, planned_directories=planned_directories
                )
                if step.file.action == "mkdir":
                    planned_directories.add(target)
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

    def fence(self, name: str) -> str | None:
        if name not in SURFACE_TOOLS and name not in {"assistant_browser", "assistant_native"}:
            return None
        from nanomuse.sentinel.gate import current_task

        current = current_task()
        if self._stopping:
            return "The assistant executor is stopping. No further input is permitted."
        if current and current.conversation.startswith("assistant:"):
            task_id = current.conversation.removeprefix("assistant:")
            if self.ledger.task_status(task_id) in {
                "paused",
                "cancelling",
                "cancelled",
                "needs_attention",
            }:
                return "Task no longer permits automatic input. Observe before continuing."
        if self._human_owner:
            return "User controls this session. Resume its task before automatic input."
        if self._surface_owner and (
            current is None or current.conversation != f"assistant:{self._surface_owner}"
        ):
            return "A task owns the browser and desktop session. Wait or take over that task."
        return None

    def interrupt(self, task_id: str, action: str) -> None:
        if action == "take_over":
            self._human_owner = task_id
        if action in {"pause", "cancel", "take_over"}:
            self.browser.invalidate()
            self.native.invalidate()
        if action in {"pause", "cancel", "take_over"} and self._step_id:
            if self.ledger.get(task_id)["id"] == task_id and self._worker is not None:
                step = next(
                    (s for s in self.ledger.get(task_id)["steps"] if s["id"] == self._step_id), None
                )
                if step is not None:
                    self._worker.cancel()
        if action in {"cancel", "resume"} and self._human_owner == task_id:
            self._human_owner = ""

    def take_over(self, task_id: str) -> None:
        self._human_owner = task_id
        self.interrupt(task_id, "pause")

    async def observe(self, surface: str) -> dict[str, Any]:
        if self._worker is not None and not self._worker.done():
            raise ValueError("Pause the active task before observing for manual recovery")
        token = self.service.app.sentinel.begin_task(purpose="Observe for task recovery")
        try:
            if surface == "browser":
                return dict(await self.browser.observe())
            tool = NativeObservationTool(driver=self.native)
            response = await self.service.app.sentinel.guard(
                ToolCall(function=Function(name=tool.name, arguments="{}")), tool
            )
            if response.error:
                raise ValueError(response.error)
            result = json.loads(response.output)
            screenshot = Path(result.pop("screenshot"))
            result["screenshot_path"] = screenshot.relative_to(self.workspace).as_posix()
            client = result.pop("client_screenshot", "")
            if client:
                result["client_screenshot_path"] = (
                    Path(client).relative_to(self.workspace).as_posix()
                )
            return dict(result)
        finally:
            self.service.app.sentinel.end_task(token)

    async def apply_execution(self, step: dict[str, Any]) -> dict[str, Any]:
        spec, adapter = step["spec"], step["spec"]["adapter"]
        if adapter == "file":
            value = FileSpec.model_validate(spec["file"])
            if value.content_from_step is not None:
                source = next(
                    s
                    for s in self.ledger.get(step["task_id"])["steps"]
                    if s["revision"] == step["revision"]
                    and s["position"] == value.content_from_step
                )
                content = source["result"].get("text")
                if source["status"] != "completed" or not isinstance(content, str):
                    raise ValueError("Browser source has no verified extraction")
                value = value.model_copy(update={"content": content, "content_from_step": None})
            row = self.journal.get(step["id"])
            if row is None:
                self.journal.start(step, value.model_dump())
            result = self.files.execute(
                value, f"assistant:{step['id']}", step["task_id"], step["revision"], step["id"]
            )
            recorded = {"adapter": adapter, **result}
            self.journal.finish(step["id"], recorded)
            return recorded
        sentinel = self.service.app.sentinel
        if any(sentinel.active_tools.get(name, 0) for name in SURFACE_TOOLS):
            raise ValueError("A chat is using the browser or desktop; wait before retrying")
        self._surface_owner = step["task_id"]
        try:
            previous = self.journal.start(step, spec[adapter])
            if previous is not None:
                return previous
            if adapter == "browser":
                browser_spec = BrowserSpec.model_validate(spec[adapter])
                if browser_spec.action == "download":
                    output = await self.browser.execute(
                        browser_spec,
                        f"assistant:{step['id']}",
                        step["task_id"],
                        step["revision"],
                        step["id"],
                    )
                else:
                    output = await self.browser.execute(browser_spec)
            else:
                output = await self.native.execute(NativeSpec.model_validate(spec[adapter]))
                if isinstance(output.get("observation"), dict):
                    observed = dict(output["observation"])
                    screenshot = observed.pop("screenshot", "")
                    if screenshot:
                        observed["screenshot_path"] = (
                            Path(screenshot).relative_to(self.workspace).as_posix()
                        )
                    client = observed.pop("client_screenshot", "")
                    if client:
                        observed["client_screenshot_path"] = (
                            Path(client).relative_to(self.workspace).as_posix()
                        )
                    output["observation"] = observed
            result = {"adapter": adapter, **output}
            self.journal.finish(step["id"], result)
            return result
        finally:
            self._surface_owner = ""

    def pulse(self) -> None:
        self.wire_fences()
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
                if step["spec"]["adapter"] in EXECUTION_ADAPTERS:
                    status, evidence = self.cancel_evidence(step)
                    if status == "cancelled":
                        status = "needs_attention"
                        evidence = {"paused_before_execution": True, "requires_new_preview": True}
                    self.ledger.reconcile(step["id"], status, evidence)
                continue
            self._step_id = step["id"]
            self._worker = asyncio.create_task(self._run(step), name="assistant-personal-action")
            break

    def cancel_evidence(self, step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Observe committed receipts before reporting what cancellation prevented."""
        adapter = step["spec"]["adapter"]
        key = f"assistant:{step['id']}"
        if adapter == "file":
            status, evidence = self.files.cancel_evidence(key)
            if evidence.get("outcome_unknown"):
                status = "outcome_unknown"
            elif status == "completed":
                self.journal.finish(step["id"], {"adapter": adapter, **evidence})
            return status, evidence
        if adapter in {"browser", "native"}:
            row = self.journal.get(step["id"])
            if row is None:
                return "cancelled", {"adapter": adapter, "cancelled_before_execution": True}
            if (
                adapter == "browser"
                and step["spec"]["browser"]["action"] == "download"
                and self.browser.downloads is not None
            ):
                status, evidence = self.browser.downloads.cancel_evidence(key)
                if status == "completed":
                    self.journal.finish(step["id"], {"adapter": adapter, **evidence})
                return status, {"adapter": adapter, **evidence}
            if row["status"] == "completed":
                return "completed", {**row["result"], "recovered_receipt": True}
            return "outcome_unknown", {
                "adapter": adapter,
                "error": "Input intent exists. Observe and revise; do not repeat it.",
            }
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
        self._stopping = True
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def _run(self, step: dict[str, Any]) -> None:
        if step["spec"]["adapter"] in {"browser", "native"}:
            receipt = self.journal.get(step["id"])
            if receipt is not None:
                if (
                    step["spec"]["adapter"] == "browser"
                    and step["spec"]["browser"]["action"] == "download"
                    and self.browser.downloads is not None
                ):
                    status, evidence = self.cancel_evidence(step)
                    if status == "cancelled":
                        status = "needs_attention"
                        evidence = {"requires_new_preview": True, "stopped_before_download": True}
                    self.ledger.reconcile(step["id"], status, evidence)
                    return
                if receipt["status"] == "completed":
                    self.ledger.reconcile(
                        step["id"], "completed", {**receipt["result"], "recovered_receipt": True}
                    )
                else:
                    self.ledger.reconcile(
                        step["id"],
                        "outcome_unknown",
                        {
                            "action_started": True,
                            "error": "An input intent remains unresolved. Observe before revising; do not repeat it.",
                        },
                    )
                return
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
                row = self.journal.get(step["id"])
                unknown = row is not None and row["status"] == "started"
                self.ledger.reconcile(
                    step["id"],
                    "outcome_unknown" if unknown else "needs_attention",
                    {
                        "error": result.error[:500]
                        if step["spec"]["adapter"] in EXECUTION_ADAPTERS
                        else "Action could not be verified. Observe current state before revising.",
                        "action_started": unknown,
                    },
                )
            else:
                self.ledger.reconcile(step["id"], "completed", json.loads(result.output))
                self.service.bus.publish({"kind": "reminders"})
                self.service.bus.publish({"kind": "memory"})
        except asyncio.CancelledError:
            raise
        except Exception:
            row = self.journal.get(step["id"])
            self.ledger.reconcile(
                step["id"],
                "outcome_unknown"
                if row is not None and row["status"] == "started"
                else "needs_attention",
                {"error": "Personal action could not be verified. Observe before revising."},
            )
        finally:
            sentinel.end_task(token)
            ui = getattr(self.service, "ui", None)
            main = getattr(self.service, "threads", {}).get("main")
            if ui is not None and not ui.pending_approvals and not (main and main.busy):
                ui.set_status("idle", thread="main")

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
