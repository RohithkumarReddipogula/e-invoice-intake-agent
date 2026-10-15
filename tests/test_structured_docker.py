"""End-to-end R7 against the real KoSIT validator (Docker). Runs in the `kosit` CI job.

Locally these tests are skipped when Docker or the validator image is missing. With
INTAKE_REQUIRE_DOCKER=1 (set in CI) a missing validator is a failure, not a skip.
"""

import os
from pathlib import Path

import pytest
from erechnung_inbox import Status, check_file

from intake.detect import InputKind
from intake.rules import RuleStatus
from intake.structured import check_structured

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module", autouse=True)
def require_validator():
    probe = check_file(FIXTURES / "xrechnung_ubl.xml")
    if probe.status is Status.ERROR:
        message = f"KoSIT validator not available: {probe.reason}"
        if os.environ.get("INTAKE_REQUIRE_DOCKER") == "1":
            pytest.fail(message)
        pytest.skip(message)


@pytest.mark.parametrize("name", ["xrechnung_ubl.xml", "xrechnung_cii.xml"])
def test_fixtures_are_valid_xrechnung(name):
    result = check_structured(InputKind.XML, FIXTURES / name)
    assert result.status is RuleStatus.PASS, result.reason


def test_zugferd_fixture_is_valid(zugferd_pdf, tmp_path):
    path = tmp_path / "invoice.pdf"
    path.write_bytes(zugferd_pdf)
    result = check_structured(InputKind.ZUGFERD_PDF, path)
    assert result.status is RuleStatus.PASS, result.reason


def test_wrong_gross_total_is_rejected(ubl_xml, tmp_path):
    path = tmp_path / "invoice.xml"
    path.write_bytes(
        ubl_xml.replace(b">2082.50</cbc:TaxInclusiveAmount>", b">2083.50</cbc:TaxInclusiveAmount>")
    )
    result = check_structured(InputKind.XML, path)
    assert result.status is RuleStatus.FAIL
    assert "BR-CO-15" in result.reason
