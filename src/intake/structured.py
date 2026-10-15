"""R7 structured_validation: the only place that talks to e-rechnung-inbox.

e-rechnung-inbox runs the official KoSIT validator in Docker. We use only its public API
(check_file, CheckResult). If the dependency changes, only this file changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from erechnung_inbox import CheckResult, Severity, Status, check_file

from .detect import InputKind
from .rules.base import RuleResult, failed, passed, skipped

R7, R7_NAME = "R7", "structured_validation"
MAX_FINDINGS_IN_REASON = 5


class StructuredValidator(Protocol):
    def __call__(self, path: Path) -> CheckResult: ...


def kosit_validator(path: Path) -> CheckResult:
    """The real validator. Needs Docker and the KoSIT image (see e-rechnung-inbox README)."""
    return check_file(path)


def check_structured(
    kind: InputKind, path: Path, validator: StructuredValidator = kosit_validator
) -> RuleResult:
    if kind is InputKind.PLAIN_PDF:
        return skipped(R7, R7_NAME, "Plain PDF: there is no structured XML to validate.")
    return rule_from_check_result(validator(path))


def rule_from_check_result(result: CheckResult) -> RuleResult:
    """Map an e-rechnung-inbox result to R7. Only `valid` passes; everything else fails."""
    if result.status is Status.VALID:
        warnings = len(result.findings_by_severity(Severity.WARNING))
        suffix = f" {warnings} warning(s), no errors." if warnings else ""
        return passed(R7, R7_NAME, result.reason + suffix)

    if result.status is Status.INVALID:
        errors = result.findings_by_severity(Severity.ERROR)
        shown = [f"{f.rule_id or 'schema'}: {f.message}" for f in errors[:MAX_FINDINGS_IN_REASON]]
        more = len(errors) - len(shown)
        details = "; ".join(shown) + (f"; and {more} more" if more > 0 else "")
        return failed(R7, R7_NAME, f"{result.reason} {details}".strip())

    if result.status is Status.NOT_CHECKED:
        return failed(R7, R7_NAME, result.reason)

    # Status.ERROR: Docker missing, image missing, unreadable file, validator crash.
    return failed(R7, R7_NAME, f"Structured validation could not run: {result.reason}")
