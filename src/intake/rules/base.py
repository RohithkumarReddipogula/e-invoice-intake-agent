"""Rule results. A rule never raises; it returns pass, fail or skip with a reason."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

# Money comparisons allow a difference of up to one cent (SPEC section 4).
TOLERANCE = Decimal("0.01")


class RuleStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


class RuleResult(BaseModel):
    rule_id: str  # "R1" ... "R7"
    name: str  # e.g. "lines_sum"
    status: RuleStatus
    reason: str  # plain language, shown to the reviewer


def passed(rule_id: str, name: str, reason: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, name=name, status=RuleStatus.PASS, reason=reason)


def failed(rule_id: str, name: str, reason: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, name=name, status=RuleStatus.FAIL, reason=reason)


def skipped(rule_id: str, name: str, reason: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, name=name, status=RuleStatus.SKIP, reason=reason)


def money(value: Decimal) -> str:
    return f"{value:.2f}"
