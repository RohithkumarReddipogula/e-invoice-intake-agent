"""The eval dataset must be correct, or the eval measures generator bugs."""

import json
from collections import Counter

import pytest

from eval.dataset import EVAL_TODAY, PLANTED_KINDS
from eval.generate import write_split
from intake.detect import detect
from intake.llm.extract import pdf_text
from intake.llm.grounding import amount_found, currency_found, date_found, text_found
from intake.models import FIELD_NAMES, InvoiceData
from intake.rules import RuleStatus, duplicate_keys, run_rules
from intake.xml_extract import extract_xml

RULE_FOR = {
    "lines_sum": "R1",
    "net_vat_gross": "R2",
    "vat_id": "R3",
    "iban": "R4",
    "date": "R5",
    "duplicate": "R6",
}


@pytest.fixture(scope="module", params=["dev", "test"])
def split(request, tmp_path_factory):
    out = tmp_path_factory.mktemp("evaldata")
    manifest = write_split(out, request.param, seed=2026)
    folder = out / request.param
    rows = []
    for m in manifest:
        truth = json.loads((folder / f"{m['id']}.truth.json").read_text())["truth"]
        rows.append((m, InvoiceData.model_validate(truth), (folder / m["file"]).read_bytes()))
    return rows


def test_composition(split):
    kinds = Counter(m["kind"] for m, _, _ in split)
    assert kinds == {"plain_pdf": 35, "zugferd": 15, "xml_ubl": 13, "xml_cii": 12}
    planted = Counter(m["planted"] for m, _, _ in split if m["planted"])
    assert sum(planted.values()) == 19  # 25 %
    assert set(planted) == set(PLANTED_KINDS)


def test_structured_invoices_read_back_exactly(split):
    for m, truth, content in split:
        if m["kind"] == "plain_pdf":
            continue
        got = extract_xml(detect(content)).data
        diff = [f for f in FIELD_NAMES if getattr(got, f) != getattr(truth, f)]
        assert diff == [], (m["id"], diff)


def test_every_true_value_is_printed_in_plain_pdfs(split):
    """Grounding must be able to confirm a correct LLM answer."""
    for m, truth, content in split:
        if m["kind"] != "plain_pdf":
            continue
        text = pdf_text(content)
        checks = {
            "invoice_number": text_found(truth.invoice_number, text),
            "seller_name": text_found(truth.seller_name, text),
            "buyer_name": text_found(truth.buyer_name, text),
            "seller_vat_id": text_found(truth.seller_vat_id, text),
            "seller_iban": text_found(truth.seller_iban, text),
            "currency": currency_found("EUR", text),
            "issue_date": date_found(truth.issue_date, text),
            "net_total": amount_found(truth.net_total, text),
            "vat_total": amount_found(truth.vat_total, text),
            "gross_total": amount_found(truth.gross_total, text),
        }
        if truth.due_date:
            checks["due_date"] = date_found(truth.due_date, text)
        for i, line in enumerate(truth.line_items):
            checks[f"line {i + 1}"] = amount_found(line.net_amount, text)
        missing = [k for k, ok in checks.items() if not ok]
        assert missing == [], (m["id"], m["layout"], missing)


def test_planted_problems_trip_exactly_their_rule(split):
    seen: set = set()
    for m, truth, _ in split:
        rules = run_rules(truth, today=EVAL_TODAY, existing=seen)
        failed = {r.rule_id for r in rules if r.status is RuleStatus.FAIL}
        expected = {RULE_FOR[m["planted"]]} if m["planted"] else set()
        assert failed == expected, (m["id"], m["planted"], failed)
        seen |= duplicate_keys(truth)


def test_duplicates_follow_their_original(split):
    position = {m["id"]: i for i, (m, _, _) in enumerate(split)}
    for m, _, _ in split:
        if m["planted"] == "duplicate":
            assert position[m["duplicate_of"]] < position[m["id"]]


def test_generation_is_reproducible(tmp_path):
    a = write_split(tmp_path / "a", "dev", seed=2026)
    b = write_split(tmp_path / "b", "dev", seed=2026)
    assert a == b
    for m in a:
        truth_a = (tmp_path / "a" / "dev" / f"{m['id']}.truth.json").read_bytes()
        assert truth_a == (tmp_path / "b" / "dev" / f"{m['id']}.truth.json").read_bytes()
        if m["kind"] != "zugferd":  # factur-x stamps the current time into the PDF metadata
            file_a = (tmp_path / "a" / "dev" / m["file"]).read_bytes()
            assert file_a == (tmp_path / "b" / "dev" / m["file"]).read_bytes(), m["id"]
