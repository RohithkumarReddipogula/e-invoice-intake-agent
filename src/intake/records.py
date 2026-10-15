"""What the service layer returns to REST, the web page and MCP."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from .models import InvoiceData
from .rules import RuleResult, RuleStatus
from .status import InvoiceStatus


class Event(BaseModel):
    at: str
    actor: str
    old_status: str | None
    new_status: str
    note: str | None


class InvoiceRecord(BaseModel):
    id: str
    created_at: str
    updated_at: str
    filename: str
    file_sha256: str
    input_kind: str
    status: InvoiceStatus
    source: str | None
    data: InvoiceData | None
    confidence: dict[str, float]
    notes: dict[str, str]
    corrected_by: dict[str, str]
    rules: list[RuleResult]
    blocking: list[str]  # always need a person: extraction failed, credit note, ...
    reasons: list[str]  # review reasons from the last decision
    events: list[Event]

    @property
    def failed_rules(self) -> list[RuleResult]:
        return [r for r in self.rules if r.status is RuleStatus.FAIL]

    @property
    def held_for_confidence_only(self) -> bool:
        """In review only because of low confidence: no failed rule, nothing blocking.

        Only these may be approved via MCP (SPEC decision 4).
        """
        return (
            self.status is InvoiceStatus.NEEDS_REVIEW
            and self.data is not None
            and not self.blocking
            and not self.failed_rules
        )


class InvoiceSummary(BaseModel):
    id: str
    created_at: str
    status: InvoiceStatus
    invoice_number: str | None
    seller_name: str | None
    gross_total: Decimal | None
    currency: str | None
    main_reason: str | None
