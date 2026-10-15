"""R6 duplicate: same seller and same invoice number as an earlier invoice.

Every invoice has up to two keys, one from the VAT ID and one from the seller name. R6 fails
if either key matches an earlier invoice, so a duplicate sent once with and once without a
VAT ID is caught. Two different sellers with the same name and the same invoice number are
flagged too; they go to review, which is the safe direction (SPEC section 4).
"""

from __future__ import annotations

import re
from collections.abc import Collection

from ..models import InvoiceData
from .base import RuleResult, failed, passed
from .identifiers import normalize_vat_id

R6, R6_NAME = "R6", "duplicate"

DuplicateKey = tuple[str, str]  # (seller key, normalised invoice number)


def seller_keys(data: InvoiceData) -> set[str]:
    keys = set()
    if data.seller_vat_id and (vat := normalize_vat_id(data.seller_vat_id)):
        keys.add("vat:" + vat)
    # "Muster GmbH", "MUSTER  GmbH." and "Muster-GmbH" are the same seller.
    if data.seller_name and (name := re.sub(r"\W", "", data.seller_name.casefold())):
        keys.add("name:" + name)
    return keys


def normalize_invoice_number(number: str) -> str:
    return re.sub(r"\s", "", number).upper()


def duplicate_keys(data: InvoiceData) -> set[DuplicateKey]:
    if not data.invoice_number:
        return set()
    number = normalize_invoice_number(data.invoice_number)
    return {(seller, number) for seller in seller_keys(data)}


def check_duplicate(data: InvoiceData, existing: Collection[DuplicateKey]) -> RuleResult:
    """`existing`: keys of earlier invoices in any status except rejected (the caller's job)."""
    keys = duplicate_keys(data)
    if not keys:
        return failed(R6, R6_NAME, "Cannot check: invoice number or seller is missing.")
    matches = sorted(
        seller.split(":", 1)[0] for seller, number in keys if (seller, number) in existing
    )
    if matches:
        by = " and ".join({"vat": "VAT ID", "name": "seller name"}[m] for m in matches)
        return failed(
            R6,
            R6_NAME,
            f"Invoice number {data.invoice_number!r} was already received from this seller "
            f"(same {by}).",
        )
    return passed(R6, R6_NAME, "No earlier invoice with this number from this seller.")
