"""Plain PDF -> text -> LLM -> Extraction with combined confidences.

confidence = min(llm_confidence, grounding), grounding being 1.0 if the value is found in
the PDF text and 0.0 if not (SPEC section 3).
"""

from __future__ import annotations

import io
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..models import REQUIRED_FIELDS, Extraction, InvoiceData, LineItem, Source
from .client import LlmExtractor
from .grounding import amount_found, currency_found, date_found, squash, text_found
from .schema import LlmInvoiceExtraction, LlmLineItem

# Fewer printable characters than this: the PDF is a scan without a text layer.
MIN_TEXT_CHARS = 20

TEXT_FIELDS = (
    "invoice_number",
    "currency",
    "seller_name",
    "seller_vat_id",
    "seller_iban",
    "buyer_name",
)
DATE_FIELDS = ("issue_date", "due_date")
AMOUNT_FIELDS = ("allowance_total", "charge_total", "net_total", "vat_total", "gross_total")
NOT_FOUND = "value not found in the PDF text"


class NoTextLayer(Exception):
    pass


def pdf_text(pdf_bytes: bytes) -> str:
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError) as exc:
        raise NoTextLayer(f"Could not read text from the PDF: {exc}") from exc
    if len(squash(text)) < MIN_TEXT_CHARS:
        raise NoTextLayer("PDF has no text layer (scan); OCR is not supported.")
    return text


def extract_llm(pdf_bytes: bytes, extractor: LlmExtractor) -> Extraction:
    text = pdf_text(pdf_bytes)
    return to_extraction(extractor.extract(text), text)


def _parse_decimal(raw: str) -> Decimal | None:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _line(item: LlmLineItem) -> tuple[LineItem, bool]:
    """Convert one line; the bool says whether every given number could be parsed."""
    values: dict[str, object] = {"description": item.description}
    ok = True
    for key in ("quantity", "unit_price", "net_amount", "vat_rate"):
        raw = getattr(item, key)
        if raw is None:
            continue
        parsed = _parse_decimal(raw)
        ok = ok and parsed is not None
        values[key] = parsed
    return LineItem(**values), ok


def to_extraction(llm: LlmInvoiceExtraction, text: str) -> Extraction:
    values: dict[str, object] = {}
    confidence: dict[str, float] = {}
    notes: dict[str, str] = {}

    def grounded(name: str, llm_confidence: float, found: bool) -> None:
        confidence[name] = min(llm_confidence, 1.0 if found else 0.0)
        if not found:
            notes[name] = NOT_FOUND

    for name in TEXT_FIELDS:
        field = getattr(llm, name)
        if field.value is None:
            continue
        value = " ".join(field.value.split())
        values[name] = value
        if name == "currency":
            values[name] = value.upper()
            grounded(name, field.confidence, currency_found(value, text))
        else:
            grounded(name, field.confidence, text_found(value, text))

    for name in DATE_FIELDS:
        field = getattr(llm, name)
        if field.value is None:
            continue
        parsed_date = _parse_date(field.value)
        if parsed_date is None:
            confidence[name], notes[name] = 0.0, f"LLM returned an invalid date {field.value!r}"
            continue
        values[name] = parsed_date
        grounded(name, field.confidence, date_found(parsed_date, text))

    for name in AMOUNT_FIELDS:
        field = getattr(llm, name)
        if field.value is None:
            continue
        parsed = _parse_decimal(field.value)
        if parsed is None:
            confidence[name], notes[name] = 0.0, f"LLM returned an invalid amount {field.value!r}"
            continue
        values[name] = parsed
        # A zero allowance/charge is usually not printed at all.
        found = parsed == 0 or amount_found(parsed, text)
        grounded(name, field.confidence, found)

    lines = [_line(item) for item in llm.line_items.value]
    values["line_items"] = [line for line, _ in lines]
    line_problems = [
        f"line {i}: invalid number" for i, (_, ok) in enumerate(lines, 1) if not ok
    ] + [
        f"line {i}: net amount not found in the PDF text"
        for i, (line, ok) in enumerate(lines, 1)
        if ok and (line.net_amount is None or not amount_found(line.net_amount, text))
    ]
    if not lines:
        confidence["line_items"], notes["line_items"] = 0.0, "no line items found"
    elif line_problems:
        confidence["line_items"], notes["line_items"] = 0.0, "; ".join(line_problems)
    else:
        confidence["line_items"] = llm.line_items.confidence

    data = InvoiceData(**{k: v for k, v in values.items() if v is not None})

    for name in InvoiceData.model_fields:
        if name in confidence:
            continue
        # Value is null: the LLM says it is absent. Nothing to ground.
        if name in REQUIRED_FIELDS:
            confidence[name], notes[name] = 0.0, "not found by the LLM"
        else:
            confidence[name] = getattr(llm, name).confidence
    return Extraction(source=Source.LLM, data=data, confidence=confidence, notes=notes)
