"""The FastAPI app: REST API + review web page.

Run locally:  python -m intake        (binds to 127.0.0.1; there is no login in v1)
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from .api import build_router
from .config import Settings
from .service import IntakeService
from .web import build_web_router

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(service: IntakeService | None = None) -> FastAPI:
    if service is None:
        service = IntakeService.from_settings(Settings())
    app = FastAPI(title="e-invoice-intake-agent", version="0.1.0")
    app.state.service = service

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        # There is no login, so a page on another site must not be able to approve or
        # reject invoices through the reviewer's browser (cross-site request forgery).
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return PlainTextResponse("Cross-origin request refused.", status_code=403)
        return await call_next(request)

    app.include_router(build_router(service))
    app.include_router(build_web_router(service))

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/review")

    return app
