from datetime import date
from decimal import Decimal

import pytest

from intake.llm.grounding import amount_found, currency_found, date_found, text_found

D = Decimal


@pytest.mark.parametrize(
    "printed",
    ["1234.50", "1,234.50", "1.234,50", "1234,50", "1 234,50", "Summe: 1.234,50 EUR", "€1,234.50"],
)
def test_amount_found_in_common_formats(printed):
    assert amount_found(D("1234.5"), printed)


@pytest.mark.parametrize(
    "printed",
    [
        "1234.50",  # 34.50 is only the tail of a longer number
        "34.505",
        "34,5",  # one decimal is not how amounts are printed
        "134.50",
    ],
)
def test_amount_not_found_inside_other_numbers(printed):
    assert not amount_found(D("34.50"), printed)


def test_amount_next_to_another_number_on_the_same_line():
    # Removing whitespace would turn this into "2450.00" and lose the 450.00.
    assert amount_found(D("450.00"), "Software license   2   450.00   900.00")


def test_whole_amount_without_decimals():
    assert amount_found(D("10"), "Support hours 10 x 85,00")
    assert amount_found(D("1000.00"), "1.000 Stück")


def test_negative_amount_is_found_by_its_absolute_value():
    assert amount_found(D("-10.00"), "Rabatt -10,00")


@pytest.mark.parametrize(
    "printed",
    [
        "2026-09-05",
        "05.09.2026",
        "5.9.2026",
        "05/09/2026",
        "5 September 2026",
        "5. September 2026",
        "September 5, 2026",
        "Rechnungsdatum: 05.09.26",
    ],
)
def test_date_found(printed):
    assert date_found(date(2026, 9, 5), printed)


def test_date_not_found_inside_another_date():
    assert not date_found(date(2026, 9, 5), "15.9.2026")
    assert not date_found(date(2026, 9, 5), "25. September 2026")


def test_german_month_name():
    assert date_found(date(2026, 3, 1), "1. März 2026")


def test_text_ignores_spaces_and_case():
    assert text_found("DE89370400440532013000", "IBAN: DE89 3704 0044 0532 0130 00")
    assert text_found("Muster Software GmbH", "MUSTER SOFTWARE GMBH")
    assert not text_found("DE89370400440532013001", "IBAN: DE89 3704 0044 0532 0130 00")


def test_empty_text_value_is_not_found():
    assert not text_found("   ", "anything")


def test_currency_symbol():
    assert currency_found("EUR", "Total 119,00 €")
    assert currency_found("eur", "Total 119,00 EUR")
    assert not currency_found("USD", "Total 119,00 €")
