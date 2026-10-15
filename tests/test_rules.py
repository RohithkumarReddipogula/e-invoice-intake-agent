from datetime import date
from decimal import Decimal

import pytest

from intake.detect import detect
from intake.models import InvoiceData, LineItem
from intake.rules import RuleStatus, duplicate_keys, run_rules
from intake.rules.amounts import check_lines_sum, check_net_vat_gross
from intake.rules.dates import check_dates
from intake.rules.duplicate import check_duplicate
from intake.rules.identifiers import check_iban, check_vat_id_format
from intake.xml_extract import extract_xml

D = Decimal
TODAY = date(2026, 10, 15)


def invoice(**overrides) -> InvoiceData:
    """A small, consistent invoice; tests change one thing at a time."""
    values = {
        "invoice_number": "RE-1",
        "issue_date": date(2026, 10, 1),
        "due_date": date(2026, 10, 31),
        "currency": "EUR",
        "seller_name": "Muster Software GmbH",
        "seller_vat_id": "DE123456789",
        "seller_iban": "DE89370400440532013000",
        "buyer_name": "Beispiel Handels AG",
        "line_items": [LineItem(net_amount=D("60.00")), LineItem(net_amount=D("40.00"))],
        "net_total": D("100.00"),
        "vat_total": D("19.00"),
        "gross_total": D("119.00"),
    }
    values.update(overrides)
    return InvoiceData(**values)


# --- R1 lines_sum ----------------------------------------------------------------------


def test_r1_pass():
    result = check_lines_sum(invoice())
    assert result.status is RuleStatus.PASS
    assert result.reason == "lines 100.00 = net total 100.00."


def test_r1_fail_shows_the_numbers():
    result = check_lines_sum(invoice(net_total=D("110.00")))
    assert result.status is RuleStatus.FAIL
    assert result.reason == "lines 100.00 = 100.00, but the net total is 110.00."


def test_r1_allowances_and_charges():
    data = invoice(allowance_total=D("10.00"), charge_total=D("5.00"), net_total=D("95.00"))
    result = check_lines_sum(data)
    assert result.status is RuleStatus.PASS
    assert result.reason == "lines 100.00 - allowances 10.00 + charges 5.00 = net total 95.00."


@pytest.mark.parametrize(
    ("net_total", "status"),
    [
        (D("100.005"), RuleStatus.PASS),  # half a cent off: rounding, fine
        (D("100.01"), RuleStatus.PASS),  # exactly one cent: still within tolerance
        (D("99.99"), RuleStatus.PASS),
        (D("100.011"), RuleStatus.FAIL),
        (D("99.989"), RuleStatus.FAIL),
    ],
)
def test_r1_tolerance(net_total, status):
    assert check_lines_sum(invoice(net_total=net_total)).status is status


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"line_items": []}, "no line items"),
        ({"line_items": [LineItem(net_amount=D("1")), LineItem()]}, "missing on line 2"),
        ({"net_total": None}, "net total is missing"),
    ],
)
def test_r1_missing_data_fails(overrides, reason):
    result = check_lines_sum(invoice(**overrides))
    assert result.status is RuleStatus.FAIL
    assert reason in result.reason


# --- R2 net_vat_gross ------------------------------------------------------------------


def test_r2_pass():
    assert check_net_vat_gross(invoice()).status is RuleStatus.PASS


def test_r2_fail_shows_the_numbers():
    result = check_net_vat_gross(invoice(gross_total=D("120.00")))
    assert result.status is RuleStatus.FAIL
    assert result.reason == "100.00 + 19.00 = 119.00, but the gross total is 120.00."


def test_r2_one_cent_rounding_passes():
    assert check_net_vat_gross(invoice(gross_total=D("119.01"))).status is RuleStatus.PASS


def test_r2_missing_data_fails():
    result = check_net_vat_gross(invoice(vat_total=None, gross_total=None))
    assert result.status is RuleStatus.FAIL
    assert result.reason == "Cannot check: VAT total, gross total missing."


# --- R3 vat_id_format ------------------------------------------------------------------


@pytest.mark.parametrize(
    "vat_id",
    [
        "DE123456789",
        "de 123 456 789",
        "DE-123.456.789",
        "ATU12345678",
        "FR12345678901",
        "NL123456789B01",
        "EL123456789",
        "ESX1234567X",
        "IE1234567WA",
        "SE123456789001",
    ],
)
def test_r3_valid_formats(vat_id):
    assert check_vat_id_format(invoice(seller_vat_id=vat_id)).status is RuleStatus.PASS


@pytest.mark.parametrize(
    ("vat_id", "reason"),
    [
        ("DE12345678", "does not match the DE format"),
        ("DE1234567890", "does not match the DE format"),
        ("ATX12345678", "does not match the AT format"),
        ("NL123456789A01", "does not match the NL format"),
        ("GR123456789", "EU country prefix"),  # Greece is EL
        ("CHE-123.456.789", "EU country prefix"),
        ("123456789", "EU country prefix"),
    ],
)
def test_r3_invalid_formats(vat_id, reason):
    result = check_vat_id_format(invoice(seller_vat_id=vat_id))
    assert result.status is RuleStatus.FAIL
    assert reason in result.reason


def test_r3_missing_vat_id_fails():
    result = check_vat_id_format(invoice(seller_vat_id=None))
    assert result.status is RuleStatus.FAIL
    assert "missing" in result.reason


# --- R4 iban_checksum ------------------------------------------------------------------


@pytest.mark.parametrize(
    "iban",
    [
        "DE89370400440532013000",
        "DE89 3704 0044 0532 0130 00",
        "de89370400440532013000",
        "GB82WEST12345698765432",
        "NL91ABNA0417164300",
        "FR1420041010050500013M02606",
        "BE68539007547034",
        "NO9386011117947",
        "CH9300762011623852957",
    ],
)
def test_r4_valid_ibans(iban):
    assert check_iban(invoice(seller_iban=iban)).status is RuleStatus.PASS


@pytest.mark.parametrize(
    ("iban", "reason"),
    [
        ("DE89370400440532013001", "wrong checksum"),
        ("DE98370400440532013000", "wrong checksum"),  # swapped check digits
        ("DE8937040044053201300", "has 21 characters, DE IBANs have 22"),
        ("XX89370400440532013000", "unknown country code"),
        ("DE89-3704-0044", "invalid format"),
    ],
)
def test_r4_invalid_ibans(iban, reason):
    result = check_iban(invoice(seller_iban=iban))
    assert result.status is RuleStatus.FAIL
    assert reason in result.reason


def test_r4_missing_iban_is_skipped():
    assert check_iban(invoice(seller_iban=None)).status is RuleStatus.SKIP


# --- R5 dates --------------------------------------------------------------------------


def test_r5_pass():
    assert check_dates(invoice(), TODAY).status is RuleStatus.PASS


def test_r5_issued_today_passes():
    assert check_dates(invoice(issue_date=TODAY, due_date=None), TODAY).status is RuleStatus.PASS


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"issue_date": date(2026, 10, 16), "due_date": None}, "in the future"),
        ({"issue_date": date(2024, 10, 14), "due_date": None}, "more than 2 years old"),
        ({"due_date": date(2026, 9, 30)}, "before the issue date"),
        ({"issue_date": None}, "missing or not a valid date"),
    ],
)
def test_r5_fails(overrides, reason):
    result = check_dates(invoice(**overrides), TODAY)
    assert result.status is RuleStatus.FAIL
    assert reason in result.reason


def test_r5_exactly_two_years_old_passes():
    data = invoice(issue_date=date(2024, 10, 15), due_date=None)
    assert check_dates(data, TODAY).status is RuleStatus.PASS


def test_r5_leap_day():
    today = date(2028, 2, 29)
    # Two years before 29 Feb 2028 is 28 Feb 2026 (2026 has no 29 Feb).
    ok = invoice(issue_date=date(2026, 2, 28), due_date=None)
    too_old = invoice(issue_date=date(2026, 2, 27), due_date=None)
    assert check_dates(ok, today).status is RuleStatus.PASS
    assert check_dates(too_old, today).status is RuleStatus.FAIL
    leap_issue = invoice(issue_date=date(2028, 2, 29), due_date=None)
    assert check_dates(leap_issue, today).status is RuleStatus.PASS


# --- R6 duplicate ----------------------------------------------------------------------


def test_r6_no_earlier_invoice():
    assert check_duplicate(invoice(), existing=set()).status is RuleStatus.PASS


def test_r6_keys_from_vat_id_and_name():
    assert duplicate_keys(invoice(invoice_number="re-1 ")) == {
        ("vat:DE123456789", "RE-1"),
        ("name:mustersoftwaregmbh", "RE-1"),
    }


def test_r6_same_seller_same_number_fails():
    earlier = duplicate_keys(invoice())
    result = check_duplicate(invoice(invoice_number="re-1 "), existing=earlier)
    assert result.status is RuleStatus.FAIL
    assert "already received" in result.reason
    assert "(same seller name and VAT ID)" in result.reason


def test_r6_vat_id_is_normalised():
    earlier = duplicate_keys(invoice(seller_vat_id="DE 123 456 789", seller_name="Old Name"))
    result = check_duplicate(invoice(), existing=earlier)
    assert result.status is RuleStatus.FAIL
    assert "(same VAT ID)" in result.reason


def test_r6_first_with_vat_id_duplicate_without():
    earlier = duplicate_keys(invoice())
    duplicate = invoice(seller_vat_id=None)
    result = check_duplicate(duplicate, existing=earlier)
    assert result.status is RuleStatus.FAIL
    assert "(same seller name)" in result.reason


def test_r6_first_without_vat_id_duplicate_with():
    earlier = duplicate_keys(invoice(seller_vat_id=None))
    assert check_duplicate(invoice(), existing=earlier).status is RuleStatus.FAIL


def test_r6_seller_name_is_normalised():
    earlier = duplicate_keys(invoice(seller_vat_id=None, seller_name="Muster  Software GmbH."))
    data = invoice(seller_vat_id=None, seller_name="muster software gmbh")
    assert check_duplicate(data, existing=earlier).status is RuleStatus.FAIL


def test_r6_other_seller_same_number_passes():
    earlier = duplicate_keys(invoice(seller_vat_id="DE987654321", seller_name="Nordlicht Druck"))
    assert check_duplicate(invoice(), existing=earlier).status is RuleStatus.PASS


def test_r6_same_name_other_vat_id_is_flagged():
    # Documented side effect of the name key: goes to review, the safe direction.
    earlier = duplicate_keys(invoice(seller_vat_id="DE987654321"))
    assert check_duplicate(invoice(), existing=earlier).status is RuleStatus.FAIL


def test_r6_same_seller_other_number_passes():
    earlier = duplicate_keys(invoice(invoice_number="RE-2"))
    assert check_duplicate(invoice(), existing=earlier).status is RuleStatus.PASS


def test_r6_missing_number_fails():
    result = check_duplicate(invoice(invoice_number=None), existing=set())
    assert result.status is RuleStatus.FAIL


def test_r6_missing_seller_fails():
    result = check_duplicate(invoice(seller_vat_id=None, seller_name=None), existing=set())
    assert result.status is RuleStatus.FAIL


# --- all rules on the fixtures ---------------------------------------------------------


@pytest.mark.parametrize("fixture", ["ubl_xml", "cii_xml"])
def test_fixtures_pass_all_rules(fixture, request):
    data = extract_xml(detect(request.getfixturevalue(fixture))).data
    results = run_rules(data, today=TODAY, existing=set())
    assert [r.rule_id for r in results] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    assert all(r.status is RuleStatus.PASS for r in results), results
