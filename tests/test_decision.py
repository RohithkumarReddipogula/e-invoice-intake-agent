from intake.decision import decide
from intake.models import FIELD_NAMES, Extraction, InvoiceData, Source
from intake.rules import RuleResult, RuleStatus


def extraction(**confidence_overrides) -> Extraction:
    confidence = dict.fromkeys(FIELD_NAMES, 1.0) | confidence_overrides
    return Extraction(source=Source.LLM, data=InvoiceData(), confidence=confidence)


def rule(rule_id: str, status: RuleStatus, reason: str = "because") -> RuleResult:
    return RuleResult(rule_id=rule_id, name=f"rule_{rule_id}", status=status, reason=reason)


PASSING = [rule("R1", RuleStatus.PASS), rule("R4", RuleStatus.SKIP)]


def test_all_confident_and_passing_is_auto_approved():
    decision = decide(extraction(), PASSING, threshold=0.9)
    assert decision.auto_approve is True
    assert decision.reasons == []


def test_exactly_at_threshold_is_confident():
    assert decide(extraction(net_total=0.9), PASSING, threshold=0.9).auto_approve is True


def test_low_confidence_field_goes_to_review():
    ex = extraction(seller_iban=0.42)
    ex.notes["seller_iban"] = "value not found in the PDF text"
    decision = decide(ex, PASSING, threshold=0.9)
    assert decision.auto_approve is False
    assert decision.low_confidence_fields == ["seller_iban"]
    assert decision.reasons == [
        "Field `seller_iban` has low confidence (0.42): value not found in the PDF text."
    ]


def test_missing_confidence_counts_as_zero():
    ex = extraction()
    del ex.confidence["vat_total"]
    decision = decide(ex, PASSING, threshold=0.9)
    assert decision.low_confidence_fields == ["vat_total"]


def test_failed_rule_goes_to_review():
    rules = [*PASSING, rule("R2", RuleStatus.FAIL, "100.00 + 19.00 = 119.00, but gross is 120.00.")]
    decision = decide(extraction(), rules, threshold=0.9)
    assert decision.auto_approve is False
    assert decision.failed_rules == ["R2"]
    assert decision.reasons == ["R2 rule_R2 failed: 100.00 + 19.00 = 119.00, but gross is 120.00."]


def test_all_problems_are_listed():
    ex = extraction(buyer_name=0.5, net_total=0.1)
    rules = [rule("R2", RuleStatus.FAIL), rule("R5", RuleStatus.FAIL)]
    decision = decide(ex, rules, threshold=0.9, blocking=["Credit notes are not supported yet."])
    assert len(decision.reasons) == 5
    assert decision.reasons[0] == "Credit notes are not supported yet."


def test_no_extraction_is_never_approved():
    decision = decide(None, PASSING, threshold=0.9)
    assert decision.auto_approve is False
    assert decision.reasons == ["No data could be extracted."]
