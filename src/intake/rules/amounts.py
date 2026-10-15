"""R1 lines_sum and R2 net_vat_gross.

Missing amounts are a fail, not a skip: a sum we cannot check must never pass silently.
"""

from __future__ import annotations

from decimal import Decimal

from ..models import InvoiceData
from .base import TOLERANCE, RuleResult, failed, money, passed

R1, R1_NAME = "R1", "lines_sum"
R2, R2_NAME = "R2", "net_vat_gross"


def check_lines_sum(data: InvoiceData) -> RuleResult:
    """Line net amounts - allowances + charges = net total (BT-106 - BT-107 + BT-108 = BT-109)."""
    if not data.line_items:
        return failed(R1, R1_NAME, "Cannot check: the invoice has no line items.")
    amounts = [line.net_amount for line in data.line_items]
    missing = [str(i) for i, amount in enumerate(amounts, 1) if amount is None]
    if missing:
        return failed(
            R1, R1_NAME, f"Cannot check: net amount missing on line {', '.join(missing)}."
        )
    if data.net_total is None:
        return failed(R1, R1_NAME, "Cannot check: net total is missing.")

    lines_sum = sum((a for a in amounts if a is not None), Decimal("0"))
    expected = lines_sum - data.allowance_total + data.charge_total
    formula = f"lines {money(lines_sum)}"
    if data.allowance_total:
        formula += f" - allowances {money(data.allowance_total)}"
    if data.charge_total:
        formula += f" + charges {money(data.charge_total)}"

    if abs(expected - data.net_total) > TOLERANCE:
        return failed(
            R1,
            R1_NAME,
            f"{formula} = {money(expected)}, but the net total is {money(data.net_total)}.",
        )
    return passed(R1, R1_NAME, f"{formula} = net total {money(data.net_total)}.")


def check_net_vat_gross(data: InvoiceData) -> RuleResult:
    """Net total + VAT total = gross total (BT-109 + BT-110 = BT-112)."""
    net, vat, gross = data.net_total, data.vat_total, data.gross_total
    if net is None or vat is None or gross is None:
        labels = {"net total": net, "VAT total": vat, "gross total": gross}
        missing = ", ".join(label for label, value in labels.items() if value is None)
        return failed(R2, R2_NAME, f"Cannot check: {missing} missing.")

    expected = net + vat
    if abs(expected - gross) > TOLERANCE:
        return failed(
            R2,
            R2_NAME,
            f"{money(net)} + {money(vat)} = {money(expected)}, "
            f"but the gross total is {money(gross)}.",
        )
    return passed(R2, R2_NAME, f"{money(net)} + {money(vat)} = {money(gross)}.")
