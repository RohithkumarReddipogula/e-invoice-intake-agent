"""Detect what kind of document an upload is: invoice XML, ZUGFeRD PDF or plain PDF."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from facturx import get_xml_from_pdf
from lxml import etree

from .xmlutil import NS, parse_xml

PDF_MAGIC = b"%PDF-"

# UNTDID 1001 document type codes that mean "credit note" (EN 16931 code list for BT-3).
CREDIT_NOTE_TYPE_CODES = frozenset(
    {"81", "83", "261", "262", "296", "308", "381", "396", "420", "458", "532"}
)


class InputKind(StrEnum):
    XML = "xml"
    ZUGFERD_PDF = "zugferd_pdf"
    PLAIN_PDF = "plain_pdf"


class Syntax(StrEnum):
    UBL = "ubl"
    CII = "cii"


class UnsupportedDocument(Exception):
    """Not something we can process at all (rejected at upload)."""


@dataclass
class Detected:
    kind: InputKind
    syntax: Syntax | None = None  # None for plain PDFs
    root: etree._Element | None = None  # parsed invoice XML, None for plain PDFs
    xml: bytes | None = None
    is_credit_note: bool = False


_ROOTS = {
    f"{{{NS['ubl']}}}Invoice": Syntax.UBL,
    f"{{{NS['cn']}}}CreditNote": Syntax.UBL,
    f"{{{NS['rsm']}}}CrossIndustryInvoice": Syntax.CII,
}

_TYPE_CODE_XPATH = {
    Syntax.UBL: "cbc:InvoiceTypeCode/text()",
    Syntax.CII: "rsm:ExchangedDocument/ram:TypeCode/text()",
}


def is_pdf(data: bytes) -> bool:
    # The PDF header may be preceded by junk bytes; the PDF spec allows up to 1024.
    return PDF_MAGIC in data[:1024]


def detect(data: bytes) -> Detected:
    if is_pdf(data):
        xml = _embedded_xml(data)
        if xml is None:
            return Detected(kind=InputKind.PLAIN_PDF)
        return _detect_xml(xml, InputKind.ZUGFERD_PDF)
    return _detect_xml(data, InputKind.XML)


def _embedded_xml(pdf_bytes: bytes) -> bytes | None:
    # factur-x logs a warning for every non-invoice attachment; that is noise here.
    logging.getLogger("factur-x").setLevel(logging.ERROR)
    try:
        # XSD checks are e-rechnung-inbox's job (KoSIT validator with the official schemas).
        _filename, xml = get_xml_from_pdf(pdf_bytes, check_xsd=False)
    except Exception as exc:  # pypdf raises many different errors for broken PDFs
        raise UnsupportedDocument(f"Could not read the PDF: {exc}") from exc
    # factur-x returns (False, False) when there is no invoice attachment.
    return xml or None


def _detect_xml(data: bytes, kind: InputKind) -> Detected:
    try:
        root = parse_xml(data)
    except etree.XMLSyntaxError as exc:
        raise UnsupportedDocument(f"Not a PDF and not well-formed XML: {exc}") from exc

    syntax = _ROOTS.get(root.tag)
    if syntax is None:
        raise UnsupportedDocument(
            f"Unknown XML document {root.tag!r}. Expected a UBL Invoice, UBL CreditNote "
            "or UN/CEFACT CrossIndustryInvoice (CII)."
        )

    type_codes = root.xpath(_TYPE_CODE_XPATH[syntax], namespaces=NS)
    is_credit_note = root.tag == f"{{{NS['cn']}}}CreditNote" or (
        bool(type_codes) and type_codes[0].strip() in CREDIT_NOTE_TYPE_CODES
    )
    return Detected(kind=kind, syntax=syntax, root=root, xml=data, is_credit_note=is_credit_note)
