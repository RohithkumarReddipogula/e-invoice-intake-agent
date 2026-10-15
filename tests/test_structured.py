"""R7 mapping, with sample e-rechnung-inbox results instead of the real validator."""

import pytest
from erechnung_inbox import Finding, Severity

from intake.detect import InputKind
from intake.rules import RuleStatus
from intake.structured import MAX_FINDINGS_IN_REASON, check_structured, rule_from_check_result

from .check_results import load_check_result


def test_valid_passes():
    result = rule_from_check_result(load_check_result("valid"))
    assert result.rule_id == "R7"
    assert result.status is RuleStatus.PASS
    assert "Accepted by the KoSIT validator" in result.reason


def test_valid_with_warning_passes_and_mentions_it():
    result = rule_from_check_result(load_check_result("valid_with_warning"))
    assert result.status is RuleStatus.PASS
    assert result.reason.endswith("1 warning(s), no errors.")


def test_invalid_fails_with_error_findings_only():
    result = rule_from_check_result(load_check_result("invalid"))
    assert result.status is RuleStatus.FAIL
    assert result.reason.startswith("Rejected by the KoSIT validator: 2 errors.")
    assert "BR-CO-15: [BR-CO-15]-Invoice total amount with VAT" in result.reason
    assert "BR-DE-15" in result.reason
    assert "BR-DE-21" not in result.reason  # a warning, not an error


def test_invalid_reason_is_capped():
    check = load_check_result("invalid")
    template = check.findings[0]
    check.findings = [
        Finding(f"BR-{i:02d}", Severity.ERROR, template.location, f"problem {i}") for i in range(8)
    ]
    reason = rule_from_check_result(check).reason
    assert f"BR-{MAX_FINDINGS_IN_REASON - 1:02d}" in reason
    assert f"BR-{MAX_FINDINGS_IN_REASON:02d}" not in reason
    assert reason.endswith(f"and {8 - MAX_FINDINGS_IN_REASON} more")


def test_schema_error_without_rule_id():
    check = load_check_result("invalid")
    check.findings = [Finding(None, Severity.ERROR, "line 3, column 7", "element not allowed")]
    assert "schema: element not allowed" in rule_from_check_result(check).reason


def test_not_checked_fails():
    result = rule_from_check_result(load_check_result("not_checked"))
    assert result.status is RuleStatus.FAIL
    assert "no rules for profile BASIC" in result.reason


def test_error_fails():
    result = rule_from_check_result(load_check_result("error"))
    assert result.status is RuleStatus.FAIL
    assert result.reason.startswith("Structured validation could not run:")
    assert "Docker is installed but not running" in result.reason


@pytest.mark.parametrize("kind", [InputKind.XML, InputKind.ZUGFERD_PDF])
def test_structured_inputs_are_validated(kind, tmp_path):
    calls = []

    def fake_validator(path):
        calls.append(path)
        return load_check_result("valid")

    path = tmp_path / "invoice"
    result = check_structured(kind, path, validator=fake_validator)
    assert calls == [path]
    assert result.status is RuleStatus.PASS


def test_plain_pdf_is_skipped_without_calling_the_validator(tmp_path):
    def must_not_run(path):
        raise AssertionError("validator called for a plain PDF")

    result = check_structured(InputKind.PLAIN_PDF, tmp_path / "x.pdf", validator=must_not_run)
    assert result.status is RuleStatus.SKIP
