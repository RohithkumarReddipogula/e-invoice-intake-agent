"""The FastAPI app: REST API + review web page (+ the demo pages in demo mode).

Run locally:  python -m intake        (binds to 127.0.0.1; there is no login in v1)
Public demo:  INTAKE_DEMO_MODE=1 python -m intake   (binds to 0.0.0.0:$PORT, see demo.py)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from .api import build_router
from .config import Settings
from .service import IntakeService
from .web import build_web_router

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
log = logging.getLogger("intake.demo")


def create_app(
    service: IntakeService | None = None,
    *,
    demo_erp_reset: Callable[[], None] | None = None,
) -> FastAPI:
    """`demo_erp_reset`: how the hourly demo reset empties the mock ERP (tests inject one)."""
    if service is None:
        service = IntakeService.from_settings(Settings())
    demo = service.settings.demo_mode
    app = FastAPI(
        title="e-invoice-intake-agent",
        version="0.1.0",
        lifespan=_demo_lifespan(service, demo_erp_reset) if demo else None,
    )
    app.state.service = service

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        # There is no login, so a page on another site must not be able to approve or
        # reject invoices through the reviewer's browser (cross-site request forgery).
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return PlainTextResponse("Cross-origin request refused.", status_code=403)
            if demo and request.url.path.startswith("/invoices"):
                # Demo: no uploads and no free-text names or notes through the REST API.
                # Reading works; changes go through the demo pages with preset options.
                return JSONResponse(
                    {"detail": "Demo mode: uploads and REST changes are disabled. Use the "
                               "sample invoices on /demo and the review page."},
                    status_code=403,
                )  # fmt: skip
        return await call_next(request)

    app.include_router(build_router(service))
    app.include_router(build_web_router(service))
    if demo:
        from .demo_web import build_demo_router

        app.include_router(build_demo_router(service))

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/demo" if demo else "/review")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _demo_lifespan(service: IntakeService, erp_reset: Callable[[], None] | None):
    from . import demo
    from .kosit_cache import KositCache

    reset_erp = erp_reset or demo.http_erp_reset(service)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Refuse to start if a sample would miss the caches (e.g. other pypdf version).
        demo.self_check(service, KositCache(service.settings.kosit_cache_dir))
        await asyncio.to_thread(demo.reset, service, reset_erp)
        task = asyncio.create_task(_hourly_reset(service, reset_erp))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return lifespan


async def _hourly_reset(service: IntakeService, reset_erp: Callable[[], None]) -> None:
    from . import demo

    while True:
        wait = (demo.next_reset() - datetime.now(UTC)).total_seconds()
        await asyncio.sleep(max(wait, 1.0))
        try:
            await asyncio.to_thread(demo.reset, service, reset_erp)
            log.info("demo data reset")
        except Exception:  # keep the loop alive; the next hour tries again
            log.exception("demo reset failed")
