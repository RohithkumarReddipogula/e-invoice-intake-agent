"""Optional guardrails for production use (docs/rollout-plan.md). Both are OFF by default.

- INTAKE_MAX_AUTO_AMOUNT: above this gross amount an invoice always goes to a person.
- INTAKE_REVIEW_NEW_SUPPLIERS: an invoice goes to a person if its seller has no earlier
  accepted invoice (auto-approved, approved or exported), or if a known seller asks to be
  paid to an IBAN never used on its accepted invoices (the classic invoice-fraud pattern).

A triggered guardrail is a blocking problem: a person can approve with a note, MCP cannot.
The published eval numbers were measured with both off, and the eval runner keeps them off.
"""

from __future__ import annotations

from decimal import Decimal

from .config import Settings
from .models import InvoiceData
from .rules.identifiers import normalize_iban


def guardrail_reasons(
    data: InvoiceData, settings: Settings, accepted_ibans: set[str] | None
) -> list[str]:
    """`accepted_ibans`: IBANs on the seller's earlier accepted invoices; None = new seller."""
    reasons = []
    limit = settings.max_auto_amount
    if limit is not None and data.gross_total is not None and data.gross_total > limit:
        reasons.append(
            f"Gross total {_money(data.gross_total)} is above the auto-approval limit of "
            f"{_money(limit)} (INTAKE_MAX_AUTO_AMOUNT): a person checks it."
        )
    if settings.review_new_suppliers:
        if accepted_ibans is None:
            reasons.append(
                "New supplier: no accepted invoice from this seller yet. First invoices are "
                "always checked by a person (INTAKE_REVIEW_NEW_SUPPLIERS)."
            )
        elif data.seller_iban and normalize_iban(data.seller_iban) not in accepted_ibans:
            reasons.append(
                f"Bank details changed: IBAN {data.seller_iban} was not used on this seller's "
                "accepted invoices. Confirm it with the supplier (INTAKE_REVIEW_NEW_SUPPLIERS)."
            )
    return reasons


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"
