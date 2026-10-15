"""Plain PDF -> Extraction: grounding and the combined confidence."""

from datetime import date
from decimal import Decimal

import pytest

from intake.config import Settings
from intake.llm import LlmExtractor, NoTextLayer, extract_llm, pdf_text
from intake.llm.extract import NOT_FOUND
from intake.models import FIELD_NAMES, Source

from .conftest import render_pdf
from .llm_fakes import INVOICE_LINES, FakeClient, answer

D = Decimal


@pytest.fixture
def invoice_pdf() -> bytes:
    return render_pdf(INVOICE_LINES)


def run(pdf: bytes, tmp_path, output=None):
    settings = Settings(_env_file=None, llm_cache_dir=tmp_path / "cache", llm_offline=False)
    return extract_llm(pdf, LlmExtractor(settings, lambda s: FakeClient(output)))


def test_correct_answer_keeps_the_llm_confidence(invoice_pdf, tmp_path):
    extraction = run(invoice_pdf, tmp_path)
    assert extraction.source is Source.LLM
    data = extraction.data
    assert data.invoice_number == "RE-2026-0042"
    assert data.issue_date == date(2026, 9, 15)
    assert data.seller_iban == "DE89370400440532013000"
    assert data.net_total == D("1750.00")
    assert data.gross_total == D("2082.50")
    assert [line.net_amount for line in data.line_items] == [D("900.00"), D("850.00")]
    assert data.allowance_total == D("0")
    assert set(extraction.confidence) == set(FIELD_NAMES)
    assert all(c == 0.95 for c in extraction.confidence.values()), extraction.notes
    assert extraction.notes == {}


def test_hallucinated_value_is_not_confident(invoice_pdf, tmp_path):
    # The LLM is very sure, but this IBAN is not on the invoice.
    output = answer(seller_iban={"value": "DE02120300000000202051", "confidence": 0.99})
    extraction = run(invoice_pdf, tmp_path, output)
    assert extraction.data.seller_iban == "DE02120300000000202051"
    assert extraction.confidence["seller_iban"] == 0.0
    assert extraction.notes["seller_iban"] == NOT_FOUND


def test_wrong_amount_is_not_confident(invoice_pdf, tmp_path):
    output = answer(gross_total={"value": "2082.00", "confidence": 0.99})
    extraction = run(invoice_pdf, tmp_path, output)
    assert extraction.confidence["gross_total"] == 0.0


def test_low_llm_confidence_wins_over_grounding(invoice_pdf, tmp_path):
    output = answer(buyer_name={"value": "Beispiel Handels AG", "confidence": 0.4})
    assert run(invoice_pdf, tmp_path, output).confidence["buyer_name"] == 0.4


def test_invalid_formats_are_not_confident(invoice_pdf, tmp_path):
    output = answer(
        net_total={"value": "1.750,00", "confidence": 0.99},
        issue_date={"value": "15.09.2026", "confidence": 0.99},
    )
    extraction = run(invoice_pdf, tmp_path, output)
    assert extraction.data.net_total is None
    assert extraction.confidence["net_total"] == 0.0
    assert "invalid amount" in extraction.notes["net_total"]
    assert extraction.confidence["issue_date"] == 0.0
    assert "invalid date" in extraction.notes["issue_date"]


def test_missing_required_field_is_not_confident(invoice_pdf, tmp_path):
    output = answer(invoice_number={"value": None, "confidence": 0.9})
    extraction = run(invoice_pdf, tmp_path, output)
    assert extraction.confidence["invoice_number"] == 0.0
    assert extraction.notes["invoice_number"] == "not found by the LLM"


def test_absent_optional_field_keeps_the_llm_confidence(invoice_pdf, tmp_path):
    output = answer(due_date={"value": None, "confidence": 0.8})
    extraction = run(invoice_pdf, tmp_path, output)
    assert extraction.data.due_date is None
    assert extraction.confidence["due_date"] == 0.8


def test_line_amount_not_in_text_makes_line_items_not_confident(invoice_pdf, tmp_path):
    lines = answer().line_items.model_dump()
    lines["value"][1]["net_amount"] = "805.00"
    extraction = run(invoice_pdf, tmp_path, answer(line_items=lines))
    assert extraction.confidence["line_items"] == 0.0
    assert extraction.notes["line_items"] == "line 2: net amount not found in the PDF text"


def test_no_line_items_is_not_confident(invoice_pdf, tmp_path):
    extraction = run(invoice_pdf, tmp_path, answer(line_items={"value": [], "confidence": 0.9}))
    assert extraction.confidence["line_items"] == 0.0


def test_pdf_text_reads_all_lines(invoice_pdf):
    text = pdf_text(invoice_pdf)
    assert "RE-2026-0042" in text
    assert "DE89 3704 0044 0532 0130 00" in text


def test_pdf_without_text_layer():
    with pytest.raises(NoTextLayer, match="no text layer"):
        pdf_text(render_pdf([]))


def test_offline_miss_for_a_new_pdf(invoice_pdf, tmp_path):
    from intake.llm import OfflineCacheMiss

    settings = Settings(_env_file=None, llm_cache_dir=tmp_path / "cache", llm_offline=True)
    with pytest.raises(OfflineCacheMiss):
        extract_llm(invoice_pdf, LlmExtractor(settings))


@pytest.mark.live_llm
def test_live_extraction(invoice_pdf, tmp_path):
    """Real API call. Runs only with INTAKE_LIVE_LLM=1 and a key; never in CI."""
    settings = Settings(llm_cache_dir=tmp_path / "cache")
    extraction = extract_llm(invoice_pdf, LlmExtractor(settings))
    assert extraction.data.invoice_number == "RE-2026-0042"
    assert extraction.data.gross_total == D("2082.50")
