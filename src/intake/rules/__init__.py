"""Business rules R1-R6 (SPEC section 4). R7 (structured validation) comes with the adapter."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date

from ..models import InvoiceData
from .amounts import check_lines_sum, check_net_vat_gross
from .base import RuleResult, RuleStatus
from .dates import check_dates
from .duplicate import DuplicateKey, check_duplicate, duplicate_keys
from .identifiers import check_iban, check_vat_id_format

__all__ = [
    "DuplicateKey",
    "RuleResult",
    "RuleStatus",
    "duplicate_keys",
    "run_rules",
]


def run_rules(
    data: InvoiceData, *, today: date, existing: Collection[DuplicateKey]
) -> list[RuleResult]:
    return [
        check_lines_sum(data),
        check_net_vat_gross(data),
        check_vat_id_format(data),
        check_iban(data),
        check_dates(data, today),
        check_duplicate(data, existing),
    ]
