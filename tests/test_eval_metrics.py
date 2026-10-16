"""Metrics on small examples where the answer is known."""

from datetime import date
from decimal import Decimal

import pytest

from eval.metrics import (
    Row,
    bootstrap_ratio,
    choose_threshold,
    decision_metrics,
    field_correct,
    proportion,
    threshold_sweep,
    zero_upper_bound,
)
from intake.models import FIELD_NAMES, InvoiceData, LineItem

D = Decimal


def row(planted=None, wrong=(), conf=1.0, rule_failed=False, blocking=False, **overrides):
    confidence = dict.fromkeys(FIELD_NAMES, conf) | overrides
    return Row(
        id="x",
        kind="plain_pdf",
        planted=planted,
        status="?",
        correct={n: n not in wrong for n in FIELD_NAMES},
        confidence=confidence,
        rule_failed=rule_failed,
        blocking=blocking,
    )


# --- field_correct ---------------------------------------------------------------------


def test_normalisation():
    truth = InvoiceData(
        invoice_number="RE-1",
        seller_name="Muster GmbH",
        seller_iban="DE89370400440532013000",
        seller_vat_id="DE123456789",
        gross_total=D("119.00"),
        issue_date=date(2026, 9, 1),
    )
    got = InvoiceData(
        invoice_number=" re-1 ",
        seller_name="Muster  GmbH",
        seller_iban="DE89 3704 0044 0532 0130 00",
        seller_vat_id="de 123456789",
        gross_total=D("119"),
        issue_date=date(2026, 9, 1),
    )
    for name in ("invoice_number", "seller_name", "seller_iban", "seller_vat_id", "gross_total",
                 "issue_date"):  # fmt: skip
        assert field_correct(name, truth, got), name
    assert not field_correct("gross_total", truth, InvoiceData(gross_total=D("119.01")))
    assert not field_correct("issue_date", truth, InvoiceData(issue_date=date(2026, 1, 9)))


def test_missing_values():
    assert field_correct("due_date", InvoiceData(), InvoiceData())
    assert not field_correct("due_date", InvoiceData(due_date=date(2026, 1, 1)), InvoiceData())
    assert not field_correct("due_date", InvoiceData(), InvoiceData(due_date=date(2026, 1, 1)))
    assert not field_correct("invoice_number", InvoiceData(invoice_number="1"), None)


def test_line_items_must_match_completely():
    line = LineItem(description="Work", quantity=D("2"), unit_price=D("50"), net_amount=D("100"),
                    vat_rate=D("19"))  # fmt: skip
    truth = InvoiceData(line_items=[line])
    assert field_correct("line_items", truth, InvoiceData(line_items=[line]))
    other = line.model_copy(update={"net_amount": D("100.01")})
    assert not field_correct("line_items", truth, InvoiceData(line_items=[other]))
    assert not field_correct("line_items", truth, InvoiceData(line_items=[line, line]))


# --- bootstrap -------------------------------------------------------------------------


def test_proportion_point_and_interval():
    est = proportion([True] * 80 + [False] * 20, n_resamples=2000)
    assert est.value == 0.8
    assert est.low < 0.8 < est.high
    # Normal approximation: 0.8 +- 1.96 * sqrt(0.16 / 100) = 0.72 .. 0.88
    assert est.low == pytest.approx(0.72, abs=0.03)
    assert est.high == pytest.approx(0.88, abs=0.03)


def test_bootstrap_is_reproducible():
    flags = [True, False, True, True, False] * 10
    assert proportion(flags, n_resamples=500) == proportion(flags, n_resamples=500)


def test_zero_count_gets_an_upper_bound():
    est = proportion([False] * 60, n_resamples=500)
    assert (est.value, est.low, est.high) == (0.0, 0.0, 0.0)
    assert est.zero_upper == pytest.approx(zero_upper_bound(60))
    assert est.zero_upper == pytest.approx(0.0487, abs=0.0005)  # about 3/n


def test_ratio_without_denominator_is_undefined():
    est = bootstrap_ratio([0.0, 0.0], [0.0, 0.0])
    assert est.value is None and est.n == 0


# --- decisions -------------------------------------------------------------------------


def test_escaped_errors():
    rows = [
        row(),  # auto-approved, correct: fine
        row(wrong=["gross_total"]),  # auto-approved but wrong: escaped
        row(planted="iban"),  # auto-approved with a planted problem: escaped
        row(planted="iban", rule_failed=True),  # caught by a rule
        row(wrong=["buyer_name"], buyer_name=0.4),  # wrong, but held for confidence
    ]
    m = decision_metrics(rows, threshold=0.9, n_resamples=200)
    assert m["auto_approve_rate"].value == pytest.approx(3 / 5)
    assert m["escaped_per_invoice"].value == pytest.approx(2 / 5)
    assert m["escaped_per_auto_approved"].value == pytest.approx(2 / 3)
    assert m["planted_auto_approved"].value == pytest.approx(1 / 2)


def test_threshold_changes_the_decision():
    r = row(wrong=["buyer_name"], buyer_name=0.85)
    assert r.escaped_at(0.8)
    assert not r.auto_approved_at(0.9)


def test_choose_threshold():
    rows = [row(), row(wrong=["net_total"], net_total=0.92), row(seller_name=0.75)]
    sweep = threshold_sweep(rows, [0.7, 0.8, 0.9, 0.95])
    assert [s["escaped"] for s in sweep] == [1, 1, 1, 0]
    assert choose_threshold(sweep) == 0.95
    assert (
        choose_threshold([{"threshold": 0.5, "escaped": 1}, {"threshold": 0.9, "escaped": 2}])
        == 0.9
    )
