"""Invoice data and extraction results, shared by the XML and the LLM extractors."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class Source(StrEnum):
    XML = "xml"
    LLM = "llm"


class LineItem(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None  # net price per unit (BT-146)
    net_amount: Decimal | None = None  # line net amount (BT-131)
    vat_rate: Decimal | None = None  # percent, e.g. 19


class InvoiceData(BaseModel):
    """The fields we extract (SPEC section 3). None means "not found".

    Fields are optional here because extraction can fail for any of them; whether a missing
    field is a problem is decided by its confidence, see REQUIRED_FIELDS.
    """

    invoice_number: str | None = None  # BT-1
    issue_date: date | None = None  # BT-2
    due_date: date | None = None  # BT-9
    currency: str | None = None  # BT-5
    seller_name: str | None = None  # BT-27
    seller_vat_id: str | None = None  # BT-31
    seller_iban: str | None = None  # BT-84
    buyer_name: str | None = None  # BT-44
    line_items: list[LineItem] = Field(default_factory=list)
    allowance_total: Decimal = Decimal("0")  # BT-107
    charge_total: Decimal = Decimal("0")  # BT-108
    net_total: Decimal | None = None  # BT-109
    vat_total: Decimal | None = None  # BT-110
    gross_total: Decimal | None = None  # BT-112


FIELD_NAMES: tuple[str, ...] = tuple(InvoiceData.model_fields)

# A missing value in one of these fields can never be confident.
# due_date, seller_vat_id and seller_iban may legitimately be absent; the rules decide about them.
REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "invoice_number",
        "issue_date",
        "currency",
        "seller_name",
        "buyer_name",
        "line_items",
        "net_total",
        "vat_total",
        "gross_total",
    }
)


class Extraction(BaseModel):
    """Extracted invoice data plus a confidence in [0, 1] for every field."""

    source: Source
    data: InvoiceData
    confidence: dict[str, float]
    # Why a field is not confident, e.g. "not found in the XML". Only set for problem fields.
    notes: dict[str, str] = Field(default_factory=dict)
