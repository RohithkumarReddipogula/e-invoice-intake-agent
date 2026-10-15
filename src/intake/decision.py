"""Auto-approve only when every field is confident and every rule passes or skips (SPEC 6)."""

from __future__ import annotations

from pydantic import BaseModel

from .models import FIELD_NAMES, Extraction
from .rules import RuleResult, RuleStatus


class Decision(BaseModel):
    auto_approve: bool
    reasons: list[str]  # every problem, in plain language; empty when auto-approved
    low_confidence_fields: list[str]
    failed_rules: list[str]  # rule ids


def decide(
    extraction: Extraction | None,
    rules: list[RuleResult],
    threshold: float,
    blocking: list[str] | None = None,
) -> Decision:
    """`blocking`: problems that always need a person, e.g. extraction failed, credit note."""
    reasons = list(blocking or [])
    if extraction is None and not reasons:
        reasons.append("No data could be extracted.")

    low: list[str] = []
    if extraction is not None:
        for name in FIELD_NAMES:
            # A field without a confidence is never confident.
            confidence = extraction.confidence.get(name, 0.0)
            if confidence < threshold:
                low.append(name)
                note = extraction.notes.get(name, "below the threshold")
                reasons.append(f"Field `{name}` has low confidence ({confidence:.2f}): {note}.")

    failed = [r for r in rules if r.status is RuleStatus.FAIL]
    reasons.extend(f"{r.rule_id} {r.name} failed: {r.reason}" for r in failed)

    return Decision(
        auto_approve=not reasons,
        reasons=reasons,
        low_confidence_fields=low,
        failed_rules=[r.rule_id for r in failed],
    )
