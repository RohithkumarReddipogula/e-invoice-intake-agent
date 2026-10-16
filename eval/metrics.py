"""Eval metrics with bootstrap 95% confidence intervals (SPEC section 9.2).

The unit of resampling is the invoice: fields of one invoice are not independent.
For a count of 0 (e.g. no escaped errors) the percentile bootstrap gives [0, 0], which
says nothing; we then also give the exact one-sided 95% upper bound 1 - 0.05^(1/n).
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from intake.models import FIELD_NAMES, InvoiceData

N_RESAMPLES = 10_000
SEED = 2026


@dataclass
class Estimate:
    value: float | None  # None: undefined, e.g. a rate per auto-approved with none approved
    low: float | None
    high: float | None
    n: int  # denominator
    zero_upper: float | None = None  # one-sided 95 % upper bound when the count is 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "ci95": [self.low, self.high],
            "n": self.n,
            "zero_upper95": self.zero_upper,
        }


# --- correctness of one field ----------------------------------------------------------


def _text(v: Any) -> str:
    return " ".join(str(v).split()).casefold()


def _compact(v: Any) -> str:
    return re.sub(r"\s+", "", str(v)).upper()


def field_correct(name: str, truth: InvoiceData, got: InvoiceData | None) -> bool:
    """Exact after light normalisation; money and dates must be equal values."""
    if got is None:
        return False
    t, g = getattr(truth, name), getattr(got, name)
    if t is None or g is None:
        return t is None and g is None
    if name in ("seller_vat_id", "seller_iban", "currency"):
        return _compact(t) == _compact(g)
    if name in ("invoice_number", "seller_name", "buyer_name"):
        return _text(t) == _text(g)
    if name == "line_items":
        if len(t) != len(g):
            return False
        return all(
            _text(a.description or "") == _text(b.description or "")
            and a.quantity == b.quantity
            and a.unit_price == b.unit_price
            and a.net_amount == b.net_amount
            and a.vat_rate == b.vat_rate
            for a, b in zip(t, g, strict=True)
        )
    return t == g  # dates, Decimals


def all_fields_correct(truth: InvoiceData, got: InvoiceData | None) -> dict[str, bool]:
    return {name: field_correct(name, truth, got) for name in FIELD_NAMES}


# --- bootstrap -------------------------------------------------------------------------


def zero_upper_bound(n: int) -> float:
    """Exact one-sided 95% upper bound for a proportion when 0 of n events were seen."""
    return 1 - 0.05 ** (1 / n) if n else 1.0


def _percentile(sorted_values: list[float], q: float) -> float:
    i = (len(sorted_values) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (i - lo)


def bootstrap_ratio(
    numerators: Sequence[float],
    denominators: Sequence[float],
    n_resamples: int = N_RESAMPLES,
    seed: int = SEED,
) -> Estimate:
    """sum(num) / sum(den) over invoices, with a percentile bootstrap over invoices."""
    n = len(numerators)
    total_den = sum(denominators)
    if total_den == 0:
        return Estimate(None, None, None, 0)
    point = sum(numerators) / total_den
    rng = random.Random(seed)
    stats = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        den = sum(denominators[i] for i in idx)
        if den:
            stats.append(sum(numerators[i] for i in idx) / den)
    stats.sort()
    est = Estimate(point, _percentile(stats, 0.025), _percentile(stats, 0.975), int(total_den))
    if sum(numerators) == 0:
        est.zero_upper = zero_upper_bound(int(total_den))
    return est


def proportion(flags: Sequence[bool], **kwargs: Any) -> Estimate:
    return bootstrap_ratio([float(f) for f in flags], [1.0] * len(flags), **kwargs)


# --- the eval metrics ------------------------------------------------------------------


@dataclass
class Row:
    """One invoice after the pipeline ran."""

    id: str
    kind: str
    planted: str | None
    status: str
    correct: dict[str, bool]  # per field
    confidence: dict[str, float]  # per field
    rule_failed: bool
    blocking: bool

    @property
    def all_correct(self) -> bool:
        return all(self.correct.values())

    def auto_approved_at(self, threshold: float) -> bool:
        """Re-decide at another threshold: rules and blocking do not depend on it."""
        if self.rule_failed or self.blocking or not self.confidence:
            return False
        return all(self.confidence.get(n, 0.0) >= threshold for n in FIELD_NAMES)

    def escaped_at(self, threshold: float) -> bool:
        """Auto-approved although a value is wrong or the invoice has a planted problem."""
        return self.auto_approved_at(threshold) and (not self.all_correct or bool(self.planted))


def field_accuracy(rows: Sequence[Row], **kwargs: Any) -> dict[str, Estimate]:
    return {name: proportion([r.correct[name] for r in rows], **kwargs) for name in FIELD_NAMES}


def decision_metrics(rows: Sequence[Row], threshold: float, **kwargs: Any) -> dict[str, Estimate]:
    auto = [r.auto_approved_at(threshold) for r in rows]
    escaped = [r.escaped_at(threshold) for r in rows]
    planted_auto = [r.auto_approved_at(threshold) for r in rows if r.planted]
    return {
        "auto_approve_rate": proportion(auto, **kwargs),
        "escaped_per_invoice": proportion(escaped, **kwargs),
        "escaped_per_auto_approved": bootstrap_ratio(
            [float(e) for e in escaped], [float(a) for a in auto], **kwargs
        ),
        "planted_auto_approved": proportion(planted_auto, **kwargs),
    }


def confidence_check(rows: Sequence[Row], threshold: float) -> dict[str, Any]:
    """Accuracy of fields at/above vs. below the threshold (LLM rows only)."""
    above = [
        r.correct[n] for r in rows for n in FIELD_NAMES if r.confidence.get(n, 0.0) >= threshold
    ]
    below = [
        r.correct[n] for r in rows for n in FIELD_NAMES if r.confidence.get(n, 0.0) < threshold
    ]
    return {
        "fields_above": len(above),
        "accuracy_above": sum(above) / len(above) if above else None,
        "fields_below": len(below),
        "accuracy_below": sum(below) / len(below) if below else None,
    }


def threshold_sweep(rows: Sequence[Row], thresholds: Sequence[float]) -> list[dict[str, Any]]:
    """Point values only (no bootstrap): for choosing the threshold on dev."""
    out = []
    for t in thresholds:
        auto = sum(r.auto_approved_at(t) for r in rows)
        escaped = sum(r.escaped_at(t) for r in rows)
        out.append({"threshold": t, "auto_approved": auto, "escaped": escaped, "n": len(rows)})
    return out


def choose_threshold(sweep: list[dict[str, Any]]) -> float:
    """The lowest threshold with no escaped error on dev (most automation, no known escape).
    Falls back to the highest threshold if every threshold lets something escape."""
    safe = [s["threshold"] for s in sweep if s["escaped"] == 0]
    return min(safe) if safe else max(s["threshold"] for s in sweep)


def by(rows: Sequence[Row], key: Callable[[Row], str]) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    return groups
