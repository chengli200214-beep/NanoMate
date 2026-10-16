"""Keep unpriced model routes outside the strict local assistant profile."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

_UNPRICED = frozenset(
    {
        "/api/avatar/begin",
        "/api/avatar/start",
        "/api/avatar/choose",
        "/api/avatar/moods",
        "/api/cloud/use-as-model",
    }
)


def install_usage_boundary(
    app: FastAPI, service: Any, authorize: Callable[[Request], None]
) -> None:
    if not service.settings.assistant_usage.enabled:
        return

    @app.middleware("http")
    async def reject_unpriced_models(request: Request, call_next: Any) -> Any:
        path = request.url.path.rstrip("/")
        unpriced = path in _UNPRICED or (
            path.startswith("/api/coding/") and path != "/api/coding/stop"
        )
        if request.method not in {"GET", "HEAD", "OPTIONS"} and unpriced:
            try:
                authorize(request)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            return JSONResponse(
                {
                    "code": "assistant_usage_stopped",
                    "detail": "This model route has no reviewed local usage policy. / "
                    "此模型入口尚无已审定的本地用量政策。",
                },
                status_code=409,
            )
        return await call_next(request)
