"""R3 vat_id_format and R4 iban_checksum."""

from __future__ import annotations

import re

from ..models import InvoiceData
from .base import RuleResult, failed, passed, skipped

R3, R3_NAME = "R3", "vat_id_format"
R4, R4_NAME = "R4", "iban_checksum"

# EU VAT ID formats after the country prefix, as used by the EU VIES service.
# Format only: no online lookup and no national check digits (SPEC section 4).
VAT_ID_PATTERNS: dict[str, str] = {
    "AT": r"U\d{8}",
    "BE": r"[01]\d{9}",
    "BG": r"\d{9,10}",
    "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}",
    "DE": r"\d{9}",
    "DK": r"\d{8}",
    "EE": r"\d{9}",
    "EL": r"\d{9}",  # Greece uses EL, not GR
    "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}",
    "FR": r"[A-HJ-NP-Z0-9]{2}\d{9}",
    "HR": r"\d{11}",
    "HU": r"\d{8}",
    "IE": r"\d{7}[A-W][A-I]?|\d[A-Z+*]\d{5}[A-W]",
    "IT": r"\d{11}",
    "LT": r"\d{9}|\d{12}",
    "LU": r"\d{8}",
    "LV": r"\d{11}",
    "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}",
    "PL": r"\d{10}",
    "PT": r"\d{9}",
    "RO": r"[1-9]\d{1,9}",
    "SE": r"\d{10}01",
    "SI": r"\d{8}",
    "SK": r"\d{10}",
    "XI": r"\d{9}|\d{12}|GD\d{3}|HA\d{3}",  # Northern Ireland
}
_VAT_RE = {country: re.compile(rf"(?:{p})") for country, p in VAT_ID_PATTERNS.items()}

# IBAN length per country, from the SWIFT IBAN registry.
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22,
    "BH": 22, "BI": 27, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24,
    "DE": 22, "DJ": 27, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24, "FI": 18,
    "FK": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27,
    "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26, "IT": 27,
    "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "LY": 25, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MN": 20, "MR": 27,
    "MT": 31, "MU": 30, "NI": 28, "NL": 18, "NO": 15, "OM": 23, "PK": 24, "PL": 28,
    "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "RU": 33, "SA": 24, "SC": 31,
    "SD": 18, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "SO": 23, "ST": 25, "SV": 28,
    "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20, "YE": 30,
}  # fmt: skip

_SEPARATORS = re.compile(r"[\s.\-]")


def normalize_vat_id(value: str) -> str:
    return _SEPARATORS.sub("", value).upper()


def normalize_iban(value: str) -> str:
    return re.sub(r"\s", "", value).upper()


def check_vat_id_format(data: InvoiceData) -> RuleResult:
    if not data.seller_vat_id:
        return failed(
            R3,
            R3_NAME,
            "Seller VAT ID is missing. It is needed for a correct invoice "
            "(small businesses without a VAT ID need a manual check).",
        )
    vat_id = normalize_vat_id(data.seller_vat_id)
    country, rest = vat_id[:2], vat_id[2:]
    pattern = _VAT_RE.get(country)
    if pattern is None:
        return failed(
            R3,
            R3_NAME,
            f"Seller VAT ID {data.seller_vat_id!r} does not start with an EU country prefix "
            "(only EU VAT IDs are checked automatically).",
        )
    if not pattern.fullmatch(rest):
        return failed(
            R3,
            R3_NAME,
            f"Seller VAT ID {data.seller_vat_id!r} does not match the {country} format "
            f"({country} + {VAT_ID_PATTERNS[country]}).",
        )
    return passed(R3, R3_NAME, f"Seller VAT ID {vat_id} has a valid {country} format.")


def iban_is_valid(iban: str) -> tuple[bool, str]:
    """Check country length and the ISO 13616 mod-97 checksum. Returns (valid, reason)."""
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", iban):
        return False, "has an invalid format"
    expected_length = IBAN_LENGTHS.get(iban[:2])
    if expected_length is None:
        return False, f"has an unknown country code {iban[:2]!r}"
    if len(iban) != expected_length:
        return False, f"has {len(iban)} characters, {iban[:2]} IBANs have {expected_length}"
    rearranged = iban[4:] + iban[:4]
    number = int("".join(str(int(ch, 36)) for ch in rearranged))
    if number % 97 != 1:
        return False, "has a wrong checksum"
    return True, "is valid"


def check_iban(data: InvoiceData) -> RuleResult:
    if not data.seller_iban:
        return skipped(R4, R4_NAME, "No IBAN on the invoice (payment by other means).")
    iban = normalize_iban(data.seller_iban)
    valid, why = iban_is_valid(iban)
    if not valid:
        return failed(R4, R4_NAME, f"IBAN {data.seller_iban!r} {why}.")
    return passed(R4, R4_NAME, f"IBAN {iban} {why}.")
