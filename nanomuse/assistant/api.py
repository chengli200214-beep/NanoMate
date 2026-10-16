"""Authenticated task ledger API, contract assistant-tasks-v1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from nanomuse.assistant.coordinator import Coordinator
from nanomuse.assistant.ledger import Conflict, Ledger, Plan


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    plan: Plan
    request_id: str = Field(min_length=1, max_length=150)
    due_at: float = Field(default=0, ge=0)
    confirm_preferences: bool = False


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=150)
    plan: Plan
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class ActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=150)
    expected_revision: int = Field(ge=1)
    action: str = Field(pattern="^(pause|resume|cancel)$")


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=150)
    expected_revision: int = Field(ge=1)
    evidence: str = Field(min_length=1, max_length=4000)


class PreferenceConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=150)
    expected_revision: int = Field(ge=1)


class ScheduleBody(CreateBody):
    interval_seconds: int = Field(ge=60, le=31536000)


class TaskRoute(APIRoute):
    def get_route_handler(self) -> Any:
        handler = super().get_route_handler()

        async def guarded(request: Any) -> Any:
            try:
                return await handler(request)
            except KeyError as exc:
                raise HTTPException(404, "Task or module record not found") from exc
            except Conflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        return guarded


def install_assistant(app: FastAPI, service: Any, dependencies: list[Any]) -> None:
    ledger = Ledger(service.data_dir / "assistant" / "tasks.sqlite3")
    coordinator = Coordinator(ledger, app.state.research)
    app.state.assistant = coordinator
    router = APIRouter(prefix="/api/assistant", dependencies=dependencies, route_class=TaskRoute)
    host = getattr(service, "app", None)
    if host is not None:
        from nanomuse.assistant.personal import PersonalRuntime

        coordinator.personal = PersonalRuntime(ledger, service)

    def validate(plan: Plan, scheduled: bool = False) -> None:
        if coordinator.personal is not None:
            coordinator.personal.validate(plan)
        for step in plan.steps:
            if step.adapter == "research":
                project = app.state.research.store.project(step.project_id)
                if (step.spec_id and step.spec_id != project["spec_id"]) or (
                    step.input_version and step.input_version != project["input_version"]
                ):
                    raise Conflict("Research inputs changed; review the new project conditions")
                step.spec_id = project["spec_id"]
                step.input_version = project["input_version"]
                if scheduled and project["tracking"]["enabled"]:
                    raise Conflict("Research tracking already owns this project's schedule")

    from nanomuse.assistant.tool import AssistantTasks

    if host is not None:
        host.tools.add(AssistantTasks(ledger=ledger, validate_plan=validate))

    @router.get("/tasks")
    async def tasks() -> list[dict[str, Any]]:
        return ledger.tasks()

    @router.post("/tasks")
    async def create(body: CreateBody) -> dict[str, Any]:
        previous = ledger.replay_creation(
            body.plan, body.request_id, body.due_at, body.confirm_preferences
        )
        if previous is not None:
            return previous
        validate(body.plan)
        return ledger.create(body.plan, body.request_id, body.due_at, body.confirm_preferences)

    @router.get("/tasks/{task_id}")
    async def task(task_id: str) -> dict[str, Any]:
        return ledger.get(task_id)

    @router.get("/tasks/{task_id}/artifacts")
    async def artifacts(task_id: str) -> list[dict[str, Any]]:
        task = ledger.get(task_id)
        records = [
            {"step_id": step["id"], "revision": step["revision"], **report}
            for step in task["steps"]
            for report in step["result"].get("reports", [])
        ]
        for step in task["steps"]:
            artifact = step["result"].get("artifact")
            if artifact:
                try:
                    checked = coordinator.personal.documents.read(artifact["id"])
                    checked.pop("content", None)
                except (ValueError, OSError, KeyError):
                    checked = {**artifact, "verified": False}
                records.append({"step_id": step["id"], "revision": step["revision"], **checked})
        return records

    @router.get("/artifacts/{artifact_id}")
    async def artifact(artifact_id: str) -> dict[str, Any]:
        if coordinator.personal is None:
            raise KeyError(artifact_id)
        return coordinator.personal.documents.read(artifact_id)

    @router.get("/preferences")
    async def preferences() -> list[dict[str, Any]]:
        return (
            coordinator.personal.preferences.list(include_deleted=True)
            if coordinator.personal
            else []
        )

    @router.post("/tasks/{task_id}/steps/{step_id}/authorize-preference")
    async def authorize(task_id: str, step_id: str, body: PreferenceConfirmation) -> dict[str, Any]:
        return ledger.authorize_preference(
            task_id, step_id, body.expected_revision, body.request_id
        )

    @router.get("/module-bindings")
    async def bindings() -> list[dict[str, Any]]:
        # Read through the authoritative stores. Never mark a delivered reminder as
        # executed work, or turn a view refresh into a second scheduler.
        if host is None:
            return []
        return [
            {
                "id": f"goal:{g.id}",
                "module": "goals",
                "record_id": g.id,
                "title": g.title,
                "status": g.status,
                "owner": "MuseService",
            }
            for g in host.goals.list()
        ] + [
            {
                "id": f"reminder:{r.id}",
                "module": "reminders",
                "record_id": r.id,
                "title": r.text,
                "status": r.status,
                "owner": "MuseService",
                "next_at": r.next_at,
            }
            for r in host.reminders.list()
        ]

    @router.post("/tasks/{task_id}/plan-revisions")
    async def revise(task_id: str, body: RevisionBody) -> dict[str, Any]:
        previous = ledger.replay_revision(
            task_id, body.expected_revision, body.plan, body.reason, body.request_id
        )
        if previous is not None:
            return previous
        validate(body.plan)
        return ledger.revise(
            task_id, body.expected_revision, body.plan, body.reason, body.request_id
        )

    @router.post("/tasks/{task_id}/actions")
    async def control(task_id: str, body: ActionBody) -> dict[str, Any]:
        return ledger.control(task_id, body.expected_revision, body.action, body.request_id)

    @router.post("/tasks/{task_id}/steps/{step_id}/confirm")
    async def confirm(task_id: str, step_id: str, body: ConfirmBody) -> dict[str, Any]:
        return ledger.confirm(
            task_id, step_id, body.expected_revision, body.evidence, body.request_id
        )

    @router.get("/schedules")
    async def schedules() -> list[dict[str, Any]]:
        return ledger.schedules()

    @router.post("/schedules")
    async def schedule(body: ScheduleBody) -> dict[str, Any]:
        if any(saved["request_id"] == body.request_id for saved in ledger.schedules()):
            return ledger.schedule(body.plan, body.request_id, body.due_at, body.interval_seconds)
        validate(body.plan, scheduled=True)
        projects = {s.project_id for s in body.plan.steps if s.adapter == "research"}
        for old in ledger.schedules():
            if (
                old["enabled"]
                and old["request_id"] != body.request_id
                and any(
                    step.get("project_id") in projects for step in old["payload"]["plan"]["steps"]
                )
            ):
                raise Conflict("An assistant schedule already owns this research project")
        return ledger.schedule(body.plan, body.request_id, body.due_at, body.interval_seconds)

    @router.delete("/schedules/{schedule_id}")
    async def disable(schedule_id: str) -> dict[str, bool]:
        ledger.disable_schedule(schedule_id)
        return {"disabled": True}

    app.include_router(router)
