"""R6 duplicate: same seller and same invoice number as an earlier invoice."""

from __future__ import annotations

import re
from collections.abc import Collection

from ..models import InvoiceData
from .base import RuleResult, failed, passed
from .identifiers import normalize_vat_id

R6, R6_NAME = "R6", "duplicate"

DuplicateKey = tuple[str, str]  # (seller key, normalised invoice number)


def seller_key(data: InvoiceData) -> str | None:
    """Normalised VAT ID if present, else normalised seller name (SPEC section 4)."""
    if data.seller_vat_id:
        return "vat:" + normalize_vat_id(data.seller_vat_id)
    if data.seller_name:
        # "Muster GmbH", "MUSTER  GmbH." and "Muster-GmbH" are the same seller.
        return "name:" + re.sub(r"\W", "", data.seller_name.casefold())
    return None


def normalize_invoice_number(number: str) -> str:
    return re.sub(r"\s", "", number).upper()


def duplicate_key(data: InvoiceData) -> DuplicateKey | None:
    seller = seller_key(data)
    if seller is None or not data.invoice_number:
        return None
    return seller, normalize_invoice_number(data.invoice_number)


def check_duplicate(data: InvoiceData, existing: Collection[DuplicateKey]) -> RuleResult:
    """`existing`: keys of earlier invoices in any status except rejected (the caller's job)."""
    key = duplicate_key(data)
    if key is None:
        return failed(R6, R6_NAME, "Cannot check: invoice number or seller is missing.")
    if key in existing:
        return failed(
            R6,
            R6_NAME,
            f"Invoice number {data.invoice_number!r} from this seller was already received.",
        )
    return passed(R6, R6_NAME, "No earlier invoice with this number from this seller.")
