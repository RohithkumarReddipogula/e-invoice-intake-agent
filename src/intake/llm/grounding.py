"""Grounding: does an extracted value actually appear in the PDF text?

LLM self-reported confidence is badly calibrated, so an LLM value only counts as confident
if we can also find it in the text (SPEC section 3). Matching is deliberately simple and
strict: a value printed in an unusual format is not found, which only costs a review.

Text values (names, IBAN, VAT ID) are compared without whitespace, so "DE89 3704 ..." matches
"DE893704...". Numbers and dates keep whitespace as a boundary, because removing it would
merge neighbouring numbers ("Qty 2  450.00" must not become "2450.00").
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}

MONTHS_EN = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]  # fmt: skip
MONTHS_DE = [
    "januar", "februar", "märz", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "dezember",
]  # fmt: skip

# Not part of a longer number on either side: "34.50" must not match inside "1234.50",
# and "5.9.2026" must not match inside "15.9.2026".
_BEFORE = r"(?<![\d.,])"
_AFTER = r"(?![\d]|[.,]\d)"


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def collapse(text: str) -> str:
    return " ".join(text.split()).casefold()


def _form_found(form: str, collapsed_text: str) -> bool:
    # A space in a form matches any (or no) whitespace in the text.
    body = r"\s*".join(re.escape(part) for part in form.split(" "))
    return re.search(_BEFORE + body + _AFTER, collapsed_text) is not None


def text_found(value: str, text: str) -> bool:
    needle = squash(value)
    return bool(needle) and needle in squash(text)


def currency_found(code: str, text: str) -> bool:
    symbol = CURRENCY_SYMBOLS.get(code.upper())
    return text_found(code, text) or (symbol is not None and symbol in text)


def amount_renderings(value: Decimal) -> set[str]:
    """How an amount may be printed: 1234.5 -> 1234.50, 1,234.50, 1.234,50, 1 234,50, ..."""
    quantized = abs(value).quantize(Decimal("0.01"))
    whole, cents = f"{quantized:.2f}".split(".")
    grouped = f"{int(whole):,}"
    groupings = {whole, grouped, grouped.replace(",", "."), grouped.replace(",", " ")}
    forms = {f"{g}{sep}{cents}" for g in groupings for sep in ".,"}
    # Mixed forms like "1.234.50" are not real; drop forms where separators collide.
    forms = {f for f in forms if not (f.count(".") > 1 and "," not in f)}
    if cents == "00":  # whole amounts and quantities are often printed without decimals
        forms |= groupings
    return forms


def amount_found(value: Decimal, text: str) -> bool:
    collapsed = collapse(text)
    return any(_form_found(form, collapsed) for form in amount_renderings(value))


def date_renderings(value: date) -> set[str]:
    d, m, y = value.day, value.month, value.year
    forms = {
        value.isoformat(),
        f"{d:02d}.{m:02d}.{y}",
        f"{d}.{m}.{y}",
        f"{d:02d}/{m:02d}/{y}",
        f"{m:02d}/{d:02d}/{y}",
        f"{d:02d}.{m:02d}.{y % 100:02d}",
    }
    for months in (MONTHS_EN, MONTHS_DE):
        name = months[m - 1]
        forms |= {f"{d} {name} {y}", f"{d}. {name} {y}", f"{d:02d}. {name} {y}", f"{name} {d}, {y}"}
    return forms


def date_found(value: date, text: str) -> bool:
    collapsed = collapse(text)
    return any(_form_found(form, collapsed) for form in date_renderings(value))
