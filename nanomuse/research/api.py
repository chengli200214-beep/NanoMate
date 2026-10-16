"""Research routes, using the host application's authentication dependency."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from nanomuse.research.clock import iso
from nanomuse.research.contracts import ProjectSpec, SourceInput
from nanomuse.research.impact import mute_scope
from nanomuse.research.orchestrator import NeedsAttention, ResearchOrchestrator, report_diff
from nanomuse.research.schedule import next_check
from nanomuse.research.store import ResearchNotFoundError, ResearchStore, fingerprint


class RunBody(BaseModel):
    kind: str = "research"
    request_key: str | None = Field(default=None, max_length=200)


class ActionBody(BaseModel):
    action: str


class CorrectionBody(BaseModel):
    fact: str = Field(min_length=1, max_length=10000)
    reason: str = Field(min_length=1, max_length=2000)
    quote: str | None = Field(default=None, max_length=2000)


class FeedbackBody(BaseModel):
    feedback: str


class TrackingBody(BaseModel):
    enabled: bool
    interval_minutes: int = Field(default=1440, ge=1, le=10080)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    daily_time: str = Field(default="", max_length=5)
    max_calls: int = Field(default=40, ge=1, le=200)


class DomainRoute(APIRoute):
    def get_route_handler(self) -> Any:
        handler = super().get_route_handler()

        async def guarded(request: Any) -> Any:
            try:
                return await handler(request)
            except ResearchNotFoundError as exc:
                raise HTTPException(404, "No such research record") from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        return guarded


def install_research(app: FastAPI, service: Any, dependencies: list[Any]) -> ResearchOrchestrator:
    store = ResearchStore(service.data_dir / "research")

    def model() -> Any:
        if not service.settings_view().get("llm_ready", False):
            raise NeedsAttention("Configure a model in Settings before running live research.")
        return service.app.llm

    host = getattr(service, "app", None)
    bus = getattr(service, "bus", None)
    worker = ResearchOrchestrator(
        store,
        llm=model,
        sentinel=getattr(host, "sentinel", None),
        on_event=bus.publish if bus else None,
    )
    app.state.research = worker
    router = APIRouter(prefix="/api/research", dependencies=dependencies, route_class=DomainRoute)

    @router.get("/projects")
    async def projects() -> list[dict[str, Any]]:
        return store.list("project")

    @router.post("/projects")
    async def create_project(body: ProjectSpec) -> dict[str, Any]:
        return store.create_project(body)

    @router.get("/projects/{project_id}")
    async def project(project_id: str) -> dict[str, Any]:
        return store.project(project_id)

    @router.post("/projects/{project_id}/revisions")
    async def revise(project_id: str, body: ProjectSpec) -> dict[str, Any]:
        revision = store.revise(project_id, body)
        worker.wake()
        return revision

    @router.post("/projects/{project_id}/sources")
    async def add_source(project_id: str, body: SourceInput) -> dict[str, Any]:
        return store.add_source(project_id, body)

    @router.post("/projects/{project_id}/runs")
    async def run(project_id: str, body: RunBody) -> dict[str, Any]:
        value = store.enqueue(project_id, body.kind, body.request_key)
        worker.wake()
        return value

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        return store.run(run_id)

    @router.post("/runs/{run_id}/control")
    async def control(run_id: str, body: ActionBody) -> dict[str, Any]:
        result = store.control(run_id, body.action)
        worker.wake()
        return result

    @router.get("/projects/{project_id}/evidence")
    async def evidence(
        project_id: str, candidate: str = "", dimension: str = "", status: str = ""
    ) -> list[dict[str, Any]]:
        store.get("project", project_id)
        return [
            e
            for e in store.list("evidence", project_id)
            if (not candidate or e["candidate"] == candidate)
            and (not dimension or e["dimension"] == dimension)
            and (not status or e["status"] == status)
        ]

    @router.get("/projects/{project_id}/conflicts")
    async def conflicts(project_id: str) -> list[dict[str, Any]]:
        store.get("project", project_id)
        return store.list("evidence_conflict", project_id)

    @router.post("/evidence/{evidence_id}/correction")
    async def correct(evidence_id: str, body: CorrectionBody) -> dict[str, Any]:
        result = store.correction(evidence_id, body.fact, body.reason, body.quote)
        worker.wake()
        return result

    @router.post("/evidence/{evidence_id}/reverify")
    async def reverify(evidence_id: str) -> dict[str, Any]:
        result = store.reverify(evidence_id)
        worker.wake()
        return result

    @router.get("/projects/{project_id}/reports")
    async def reports(project_id: str) -> list[dict[str, Any]]:
        project = store.get("project", project_id)
        stale_decisions = {
            d["id"] for d in store.list("decision_revision", project_id) if d["status"] == "stale"
        }
        return [
            {
                **report,
                "stale": (
                    report["spec_id"] != project["spec_id"]
                    or report.get("input_version", 1) != project.get("input_version", 1)
                    or bool(stale_decisions.intersection(report.get("decision_refs", [])))
                ),
            }
            for report in reversed(store.list("report_revision", project_id))
        ]

    @router.get("/reports/{report_id}/diff")
    async def diff(report_id: str, previous: str = "") -> dict[str, Any]:
        report = store.get("report_revision", report_id)
        versions = store.list("report_revision", report["project_id"])
        before = (
            store.get("report_revision", previous)
            if previous
            else next(
                (v for v in reversed(versions[: versions.index(report)]) if v["id"] != report_id),
                None,
            )
        )
        if before and before["project_id"] != report["project_id"]:
            raise HTTPException(404, "No such report version in this project")
        text = before["markdown"] if before else ""
        return {
            "before": text,
            "after": report["markdown"],
            "diff": report_diff(text, report["markdown"]),
        }

    @router.get("/reports/{report_id}/export")
    async def export(report_id: str) -> Response:
        report = store.get("report_revision", report_id)
        return Response(
            report["markdown"],
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.md"'},
        )

    @router.put("/projects/{project_id}/tracking")
    async def tracking(project_id: str, body: TrackingBody) -> dict[str, Any]:
        values = body.model_dump()
        values["next_check_at"] = next_check(store.clock.now(), values)
        with store.transaction():
            value = store.get("project", project_id)
            value["tracking"] = values
            store._put("project", value)
        worker.wake()
        return values

    @router.get("/projects/{project_id}/changes")
    async def changes(project_id: str) -> dict[str, Any]:
        project = store.get("project", project_id)
        return {
            "events": store.list("impact_event", project_id),
            "notifications": store.list("notification", project_id),
            "mute_rules": [
                {
                    **rule,
                    "enabled": bool(rule.get("enabled", True))
                    and rule.get("scope", {}).get("spec_id") == project["spec_id"],
                }
                for rule in store.list("mute_rule", project_id)
            ],
        }

    @router.post("/changes/{change_id}/action")
    async def change_action(change_id: str, body: ActionBody) -> dict[str, Any]:
        with store.transaction():
            event = store.get("impact_event", change_id)
            if body.action == "accept":
                event["status"] = "accepted"
            elif body.action == "useless":
                event["feedback"] = "useless"
                for notice in store.list("notification", event["project_id"]):
                    if notice["event_id"] == change_id:
                        notice["feedback"] = "useless"
                        store.db.execute(
                            "UPDATE notification SET data=? WHERE id=?",
                            (json.dumps(notice, ensure_ascii=False), notice["id"]),
                        )
            elif body.action in {"mute", "unmute"}:
                scope = mute_scope(event)
                rule_id = "mute_" + fingerprint(scope)[:24]
                store._put(
                    "mute_rule",
                    {
                        "id": rule_id,
                        "project_id": event["project_id"],
                        "source_id": event["source_id"],
                        "impact_level": event["impact_level"],
                        "scope": scope,
                        "enabled": body.action == "mute",
                        "created_at": iso(store.clock.now()),
                    },
                )
            else:
                raise ValueError("Unknown change action")
            store._put("impact_event", event)
        return event

    @router.post("/notifications/{notice_id}/feedback")
    async def feedback(notice_id: str, body: FeedbackBody) -> dict[str, Any]:
        if body.feedback not in {"useful", "useless", "read"}:
            raise ValueError("Unknown feedback")
        with store.transaction():
            notice = store.get("notification", notice_id)
            notice["feedback"] = body.feedback
            store.db.execute(
                "UPDATE notification SET data=? WHERE id=?",
                (json.dumps(notice, ensure_ascii=False), notice_id),
            )
        return notice

    @router.get("/projects/{project_id}/diagnostics")
    async def diagnostics(project_id: str) -> dict[str, Any]:
        store.get("project", project_id)
        runs = store.runs(project_id)
        return {
            "runs": runs,
            "steps": [step for r in runs for step in store.run(r["id"])["steps"]],
            "calls": store.list("model_call", project_id),
            "events": store.list("impact_event", project_id),
        }

    app.include_router(router)
    return worker
