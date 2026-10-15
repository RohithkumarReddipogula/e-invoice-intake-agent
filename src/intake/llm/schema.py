"""The schema the LLM must fill: every field has a value and a self-reported confidence.

Amounts and dates are strings in a fixed format ("1234.50", "2026-09-15"). We parse them
ourselves, so a badly formatted value becomes a low-confidence field instead of a crash.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

CONFIDENCE_HELP = (
    "Your confidence in [0, 1] that the value is exactly right. Use a low value when the "
    "document is ambiguous, the value is hard to read, or you had to infer it."
)
AMOUNT_HELP = "Decimal number with a dot and no thousands separator, e.g. 1234.50. Null if absent."


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextField(_Strict):
    value: str | None = Field(description="Exactly as printed on the invoice. Null if absent.")
    confidence: float = Field(ge=0.0, le=1.0, description=CONFIDENCE_HELP)


class DateField(_Strict):
    value: str | None = Field(description="Date as YYYY-MM-DD. Null if absent.")
    confidence: float = Field(ge=0.0, le=1.0, description=CONFIDENCE_HELP)


class AmountField(_Strict):
    value: str | None = Field(description=AMOUNT_HELP)
    confidence: float = Field(ge=0.0, le=1.0, description=CONFIDENCE_HELP)


class LlmLineItem(_Strict):
    description: str | None
    quantity: str | None = Field(description=AMOUNT_HELP)
    unit_price: str | None = Field(description="Net price per unit. " + AMOUNT_HELP)
    net_amount: str | None = Field(description="Net line total (without VAT). " + AMOUNT_HELP)
    vat_rate: str | None = Field(description="VAT percent, e.g. 19 or 7. Null if not shown.")


class LineItemsField(_Strict):
    value: list[LlmLineItem]
    confidence: float = Field(ge=0.0, le=1.0, description=CONFIDENCE_HELP)


class LlmInvoiceExtraction(_Strict):
    invoice_number: TextField
    issue_date: DateField
    due_date: DateField
    currency: TextField = Field(description="ISO 4217 code, e.g. EUR (not the symbol).")
    seller_name: TextField = Field(description="Legal name of the company issuing the invoice.")
    seller_vat_id: TextField = Field(
        description="Seller VAT ID (USt-IdNr.), e.g. DE123456789. Not the tax number "
        "(Steuernummer)."
    )
    seller_iban: TextField = Field(description="IBAN the buyer should pay to.")
    buyer_name: TextField
    line_items: LineItemsField
    allowance_total: AmountField = Field(
        description="Sum of document-level discounts (not line discounts). Null if none."
    )
    charge_total: AmountField = Field(
        description="Sum of document-level surcharges, e.g. shipping. Null if none."
    )
    net_total: AmountField = Field(description="Total without VAT.")
    vat_total: AmountField = Field(description="Total VAT amount.")
    gross_total: AmountField = Field(description="Total including VAT.")
