"""A plain-PDF invoice, the correct LLM answer for it, and a fake Anthropic client."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from intake.llm.schema import LlmInvoiceExtraction

INVOICE_LINES = [
    "Muster Software GmbH - Musterstraße 1 - 10115 Berlin",
    "USt-IdNr.: DE123456789",
    "Rechnung an: Beispiel Handels AG, Hafenweg 7, 20457 Hamburg",
    "Rechnungsnummer: RE-2026-0042",
    "Rechnungsdatum: 15.09.2026     Fällig am: 15.10.2026",
    "Pos  Beschreibung                 Menge   Einzelpreis   Gesamt",
    "1    Software license, annual     2       450,00        900,00",
    "2    Support hours                10      85,00         850,00",
    "Nettobetrag: 1.750,00 EUR",
    "USt. 19 %: 332,50 EUR",
    "Gesamtbetrag: 2.082,50 EUR",
    "Bitte überweisen an IBAN DE89 3704 0044 0532 0130 00",
]


def answer(**overrides: Any) -> LlmInvoiceExtraction:
    """The correct extraction for INVOICE_LINES, confidence 0.95 everywhere."""

    def f(value):
        return {"value": value, "confidence": 0.95}

    data: dict[str, Any] = {
        "invoice_number": f("RE-2026-0042"),
        "issue_date": f("2026-09-15"),
        "due_date": f("2026-10-15"),
        "currency": f("EUR"),
        "seller_name": f("Muster Software GmbH"),
        "seller_vat_id": f("DE123456789"),
        "seller_iban": f("DE89370400440532013000"),
        "buyer_name": f("Beispiel Handels AG"),
        "line_items": f(
            [
                {
                    "description": "Software license, annual",
                    "quantity": "2",
                    "unit_price": "450.00",
                    "net_amount": "900.00",
                    "vat_rate": "19",
                },
                {
                    "description": "Support hours",
                    "quantity": "10",
                    "unit_price": "85.00",
                    "net_amount": "850.00",
                    "vat_rate": "19",
                },
            ]
        ),
        "allowance_total": f(None),
        "charge_total": f(None),
        "net_total": f("1750.00"),
        "vat_total": f("332.50"),
        "gross_total": f("2082.50"),
    }
    data.update(overrides)
    return LlmInvoiceExtraction.model_validate(data)


class FakeClient:
    """Stands in for anthropic.Anthropic; records every request."""

    def __init__(self, output: LlmInvoiceExtraction | None = None, stop_reason: str = "end_turn"):
        self.output = output if output is not None else answer()
        self.stop_reason = stop_reason
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            parsed_output=self.output if self.stop_reason == "end_turn" else None,
            usage=SimpleNamespace(input_tokens=1200, output_tokens=450),
            _request_id="req_test",
        )
