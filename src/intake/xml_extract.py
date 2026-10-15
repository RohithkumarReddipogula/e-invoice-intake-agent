"""Read invoice fields directly from UBL or CII XML (no LLM).

Values from the XML get confidence 1.0. A required field that is missing or cannot be
parsed gets confidence 0.0 and a note; the invoice then goes to review.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from lxml import etree

from .detect import Detected, Syntax
from .models import REQUIRED_FIELDS, Extraction, InvoiceData, LineItem, Source
from .xmlutil import NS


class ParseError(Exception):
    """A value is present in the XML but cannot be parsed."""


def _text(node: etree._Element, xpath: str) -> str | None:
    found = node.xpath(xpath, namespaces=NS)
    if not found:
        return None
    value = found[0] if isinstance(found[0], str) else found[0].text
    value = (value or "").strip()
    return value or None


def _decimal(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ParseError(f"not a number: {raw!r}") from exc
    if not value.is_finite():
        raise ParseError(f"not a finite number: {raw!r}")
    return value


def _iso_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ParseError(f"not a date (YYYY-MM-DD): {raw!r}") from exc


def _cii_date(raw: str) -> date:
    # CII dates use format 102 (YYYYMMDD) in all EN 16931 profiles.
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError as exc:
        raise ParseError(f"not a date (YYYYMMDD): {raw!r}") from exc


def _str(raw: str) -> str:
    return " ".join(raw.split())


# --- UBL -------------------------------------------------------------------------------

_UBL_SELLER = "cac:AccountingSupplierParty/cac:Party"
_UBL_BUYER = "cac:AccountingCustomerParty/cac:Party"
_UBL_TOTALS = "cac:LegalMonetaryTotal"

_UBL_FIELDS: dict[str, tuple[list[str], Callable[[str], object]]] = {
    "invoice_number": (["cbc:ID"], _str),
    "issue_date": (["cbc:IssueDate"], _iso_date),
    "due_date": (["cbc:DueDate", "cac:PaymentMeans/cbc:PaymentDueDate"], _iso_date),
    "currency": (["cbc:DocumentCurrencyCode"], _str),
    "seller_name": (
        [
            f"{_UBL_SELLER}/cac:PartyLegalEntity/cbc:RegistrationName",
            f"{_UBL_SELLER}/cac:PartyName/cbc:Name",
        ],
        _str,
    ),
    "seller_vat_id": (
        [f"{_UBL_SELLER}/cac:PartyTaxScheme[cac:TaxScheme/cbc:ID='VAT']/cbc:CompanyID"],
        _str,
    ),
    "seller_iban": (["cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID"], _str),
    "buyer_name": (
        [
            f"{_UBL_BUYER}/cac:PartyLegalEntity/cbc:RegistrationName",
            f"{_UBL_BUYER}/cac:PartyName/cbc:Name",
        ],
        _str,
    ),
    "allowance_total": ([f"{_UBL_TOTALS}/cbc:AllowanceTotalAmount"], _decimal),
    "charge_total": ([f"{_UBL_TOTALS}/cbc:ChargeTotalAmount"], _decimal),
    "net_total": ([f"{_UBL_TOTALS}/cbc:TaxExclusiveAmount"], _decimal),
    # BT-110 is the TaxTotal in document currency; a second TaxTotal (BT-111) may follow
    # in accounting currency. Filled in by _vat_total_xpaths.
    "vat_total": ([], _decimal),
    "gross_total": ([f"{_UBL_TOTALS}/cbc:TaxInclusiveAmount"], _decimal),
}

_UBL_LINE = {
    # Credit notes use CreditNoteLine; they are detected and sent to review, but we still
    # extract them so the reviewer sees the data.
    "lines": "cac:InvoiceLine | cac:CreditNoteLine",
    "description": "cac:Item/cbc:Name",
    "quantity": "cbc:InvoicedQuantity | cbc:CreditedQuantity",
    "unit_price": "cac:Price/cbc:PriceAmount",
    "net_amount": "cbc:LineExtensionAmount",
    "vat_rate": "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent",
}

# --- CII -------------------------------------------------------------------------------

_CII_TRADE = "rsm:SupplyChainTradeTransaction"
_CII_AGREEMENT = f"{_CII_TRADE}/ram:ApplicableHeaderTradeAgreement"
_CII_SETTLEMENT = f"{_CII_TRADE}/ram:ApplicableHeaderTradeSettlement"
_CII_SUMS = f"{_CII_SETTLEMENT}/ram:SpecifiedTradeSettlementHeaderMonetarySummation"

_CII_FIELDS: dict[str, tuple[list[str], Callable[[str], object]]] = {
    "invoice_number": (["rsm:ExchangedDocument/ram:ID"], _str),
    "issue_date": (["rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"], _cii_date),
    "due_date": (
        [
            f"{_CII_SETTLEMENT}/ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString"
        ],
        _cii_date,
    ),
    "currency": ([f"{_CII_SETTLEMENT}/ram:InvoiceCurrencyCode"], _str),
    "seller_name": ([f"{_CII_AGREEMENT}/ram:SellerTradeParty/ram:Name"], _str),
    "seller_vat_id": (
        [
            f"{_CII_AGREEMENT}/ram:SellerTradeParty/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']"
        ],
        _str,
    ),
    "seller_iban": (
        [
            f"{_CII_SETTLEMENT}/ram:SpecifiedTradeSettlementPaymentMeans"
            "/ram:PayeePartyCreditorFinancialAccount/ram:IBANID"
        ],
        _str,
    ),
    "buyer_name": ([f"{_CII_AGREEMENT}/ram:BuyerTradeParty/ram:Name"], _str),
    "allowance_total": ([f"{_CII_SUMS}/ram:AllowanceTotalAmount"], _decimal),
    "charge_total": ([f"{_CII_SUMS}/ram:ChargeTotalAmount"], _decimal),
    "net_total": ([f"{_CII_SUMS}/ram:TaxBasisTotalAmount"], _decimal),
    "vat_total": ([], _decimal),  # see _vat_total_xpaths
    "gross_total": ([f"{_CII_SUMS}/ram:GrandTotalAmount"], _decimal),
}

_CII_LINE = {
    "lines": f"{_CII_TRADE}/ram:IncludedSupplyChainTradeLineItem",
    "description": "ram:SpecifiedTradeProduct/ram:Name",
    "quantity": "ram:SpecifiedLineTradeDelivery/ram:BilledQuantity",
    "unit_price": "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount",
    "net_amount": (
        "ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation"
        "/ram:LineTotalAmount"
    ),
    "vat_rate": "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:RateApplicablePercent",
}


def _vat_total_xpaths(syntax: Syntax, currency: str | None) -> list[str]:
    """BT-110: the VAT total in document currency. Prefer the one with a matching currencyID."""
    if syntax is Syntax.UBL:
        base = "cac:TaxTotal/cbc:TaxAmount"
    else:
        base = f"{_CII_SUMS}/ram:TaxTotalAmount"
    paths = [f"{base}[@currencyID='{currency}']"] if currency and currency.isalpha() else []
    return [*paths, base]


# --- extraction ------------------------------------------------------------------------


def extract_xml(detected: Detected) -> Extraction:
    if detected.root is None or detected.syntax is None:
        raise ValueError("extract_xml needs a detected XML invoice")
    root = detected.root
    if detected.syntax is Syntax.UBL:
        field_map, line_map = _UBL_FIELDS, _UBL_LINE
    else:
        field_map, line_map = _CII_FIELDS, _CII_LINE

    values: dict[str, object] = {}
    notes: dict[str, str] = {}

    for name, (xpaths, parse) in field_map.items():
        if name == "vat_total":
            xpaths = _vat_total_xpaths(detected.syntax, values.get("currency"))  # type: ignore[arg-type]
        raw = next((v for xp in xpaths if (v := _text(root, xp)) is not None), None)
        if raw is None:
            continue
        try:
            values[name] = parse(raw)
        except ParseError as exc:
            notes[name] = f"cannot parse value in the XML: {exc}"

    lines, line_notes = _extract_lines(root, line_map)
    values["line_items"] = lines
    if line_notes:
        notes["line_items"] = "; ".join(line_notes)
    elif not lines:
        notes["line_items"] = "no invoice lines found in the XML"

    data = InvoiceData(**values)

    confidence: dict[str, float] = {}
    for name in InvoiceData.model_fields:
        if name in notes:
            confidence[name] = 0.0
        elif name in REQUIRED_FIELDS and getattr(data, name) is None:
            confidence[name] = 0.0
            notes[name] = "not found in the XML"
        else:
            confidence[name] = 1.0

    return Extraction(source=Source.XML, data=data, confidence=confidence, notes=notes)


def _extract_lines(
    root: etree._Element, line_map: dict[str, str]
) -> tuple[list[LineItem], list[str]]:
    lines: list[LineItem] = []
    problems: list[str] = []
    for i, node in enumerate(root.xpath(line_map["lines"], namespaces=NS), start=1):
        item: dict[str, object] = {}
        description = _text(node, line_map["description"])
        if description is not None:
            item["description"] = _str(description)
        for key in ("quantity", "unit_price", "net_amount", "vat_rate"):
            raw = _text(node, line_map[key])
            if raw is None:
                continue
            try:
                item[key] = _decimal(raw)
            except ParseError as exc:
                problems.append(f"line {i} {key}: {exc}")
        if "net_amount" not in item:
            problems.append(f"line {i}: net amount missing")
        lines.append(LineItem(**item))
    return lines, problems
