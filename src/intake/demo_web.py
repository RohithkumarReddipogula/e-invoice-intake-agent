"""Demo pages: sample invoices to process, and a read-only view of the mock ERP inbox."""

from __future__ import annotations

import json
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import demo
from .demo import DemoFull
from .service import IntakeService
from .web import DONE_MESSAGES, TEMPLATES, local_time

DONE_MESSAGES.setdefault("processed", "Invoice processed by the pipeline. This is the result.")


def build_demo_router(service: IntakeService) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    zone = ZoneInfo(service.settings.display_timezone)

    def page(request: Request, name: str, context: dict, status: int = 200):
        context = {
            "demo": True,
            "reviewer": "Demo visitor",
            "next_reset": local_time(demo.next_reset().isoformat(), zone),
            **context,
        }
        return TEMPLATES.TemplateResponse(request, name, context, status_code=status)

    @router.get("/demo", response_class=HTMLResponse)
    def samples(request: Request):
        kinds = {}
        manifest = json.loads((service.settings.demo_data_dir / "manifest.json").read_text())
        for entry in manifest["invoices"]:
            kinds[entry["id"]] = entry["kind"].replace("_", " ")
        return page(request, "demo.html", {"samples": demo.SAMPLES, "kinds": kinds})

    @router.post("/demo/process/{sample_id}")
    def process(request: Request, sample_id: str):
        try:
            demo.sample(sample_id)
        except KeyError:
            return page(request, "demo.html", {"samples": demo.SAMPLES, "kinds": {},
                                               "error": "Unknown sample."}, 404)  # fmt: skip
        try:
            record = demo.process(service, sample_id)
        except DemoFull as exc:
            return page(request, "demo.html", {"samples": demo.SAMPLES, "kinds": {},
                                               "error": str(exc)}, 429)  # fmt: skip
        return RedirectResponse(f"/review/{record.id}?done=processed", status_code=303)

    @router.get("/demo/erp", response_class=HTMLResponse)
    def erp_inbox(request: Request):
        invoices, error = [], None
        if service.erp is None:
            error = "No ERP configured."
        else:
            try:
                response = service.erp.client.get(f"{service.erp.base_url}/api/invoices")
                response.raise_for_status()
                invoices = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                error = f"The mock ERP is not reachable: {exc}"
        return page(request, "demo_erp.html", {"invoices": invoices, "error": error})

    return router
