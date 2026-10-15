"""Shared fixtures. PDFs are generated at test time, so no binary files live in the repo."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from facturx import generate_from_binary
from reportlab.pdfgen import canvas

FIXTURES = Path(__file__).parent / "fixtures"


def render_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    y = 800
    for line in lines:
        pdf.drawString(72, y, line)
        y -= 16
    pdf.save()
    return buf.getvalue()


@pytest.fixture
def ubl_xml() -> bytes:
    return (FIXTURES / "xrechnung_ubl.xml").read_bytes()


@pytest.fixture
def cii_xml() -> bytes:
    return (FIXTURES / "xrechnung_cii.xml").read_bytes()


@pytest.fixture
def plain_pdf() -> bytes:
    return render_pdf(["Rechnung 2026/INV/117", "Nordlicht Druck GmbH", "Gesamt: 259,30 EUR"])


@pytest.fixture
def zugferd_pdf(cii_xml: bytes) -> bytes:
    """The CII fixture embedded in a PDF, as a ZUGFeRD / Factur-X sender would do it."""
    visual = render_pdf(["Rechnung 2026/INV/117", "(ZUGFeRD test fixture)"])
    return generate_from_binary(visual, cii_xml, flavor="factur-x", check_xsd=False)
