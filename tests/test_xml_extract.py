from datetime import date
from decimal import Decimal

from intake.detect import detect
from intake.models import FIELD_NAMES, InvoiceData, LineItem, Source
from intake.xml_extract import extract_xml

UBL_EXPECTED = InvoiceData(
    invoice_number="RE-2026-0042",
    issue_date=date(2026, 9, 15),
    due_date=date(2026, 10, 15),
    currency="EUR",
    seller_name="Muster Software GmbH",
    seller_vat_id="DE123456789",
    seller_iban="DE89370400440532013000",
    buyer_name="Beispiel Handels AG",
    line_items=[
        LineItem(
            description="Software license, annual",
            quantity=Decimal("2"),
            unit_price=Decimal("450.00"),
            net_amount=Decimal("900.00"),
            vat_rate=Decimal("19"),
        ),
        LineItem(
            description="Support hours",
            quantity=Decimal("10"),
            unit_price=Decimal("85.00"),
            net_amount=Decimal("850.00"),
            vat_rate=Decimal("19"),
        ),
    ],
    net_total=Decimal("1750.00"),
    vat_total=Decimal("332.50"),
    gross_total=Decimal("2082.50"),
)

CII_EXPECTED = InvoiceData(
    invoice_number="2026/INV/117",
    issue_date=date(2026, 9, 1),
    due_date=date(2026, 9, 30),
    currency="EUR",
    seller_name="Nordlicht Druck GmbH",
    seller_vat_id="DE987654321",
    seller_iban="DE02120300000000202051",
    buyer_name="Beispiel Handels AG",
    line_items=[
        LineItem(
            description="Flyer A5, 1000 pcs",
            quantity=Decimal("1"),
            unit_price=Decimal("120.00"),
            net_amount=Decimal("120.00"),
            vat_rate=Decimal("19"),
        ),
        LineItem(
            description="Brochure printing",
            quantity=Decimal("3"),
            unit_price=Decimal("40.00"),
            net_amount=Decimal("120.00"),
            vat_rate=Decimal("7"),
        ),
    ],
    allowance_total=Decimal("10.00"),
    charge_total=Decimal("0.00"),
    net_total=Decimal("230.00"),
    vat_total=Decimal("29.30"),
    gross_total=Decimal("259.30"),
)


def extract(data: bytes):
    return extract_xml(detect(data))


def assert_all_confident(extraction):
    assert extraction.source is Source.XML
    assert extraction.notes == {}
    assert set(extraction.confidence) == set(FIELD_NAMES)
    assert all(c == 1.0 for c in extraction.confidence.values())


def test_ubl_all_fields(ubl_xml):
    extraction = extract(ubl_xml)
    assert extraction.data == UBL_EXPECTED
    assert_all_confident(extraction)


def test_cii_all_fields(cii_xml):
    extraction = extract(cii_xml)
    assert extraction.data == CII_EXPECTED
    assert_all_confident(extraction)


def test_zugferd_reads_embedded_xml(zugferd_pdf):
    extraction = extract(zugferd_pdf)
    assert extraction.data == CII_EXPECTED
    assert_all_confident(extraction)


def test_missing_required_field_is_not_confident(ubl_xml):
    xml = ubl_xml.replace(b"<cbc:ID>RE-2026-0042</cbc:ID>", b"")
    extraction = extract(xml)
    assert extraction.data.invoice_number is None
    assert extraction.confidence["invoice_number"] == 0.0
    assert extraction.notes["invoice_number"] == "not found in the XML"


def test_missing_optional_field_stays_confident(ubl_xml):
    xml = ubl_xml.replace(b"<cbc:DueDate>2026-10-15</cbc:DueDate>", b"")
    extraction = extract(xml)
    assert extraction.data.due_date is None
    assert extraction.confidence["due_date"] == 1.0
    assert "due_date" not in extraction.notes


def test_unparseable_values_are_not_confident(cii_xml):
    xml = cii_xml.replace(b"<ram:GrandTotalAmount>259.30", b"<ram:GrandTotalAmount>259,30")
    xml = xml.replace(b">20260901<", b">2026-09-01<")
    extraction = extract(xml)
    assert extraction.data.gross_total is None
    assert extraction.confidence["gross_total"] == 0.0
    assert "not a number" in extraction.notes["gross_total"]
    assert extraction.confidence["issue_date"] == 0.0
    assert "YYYYMMDD" in extraction.notes["issue_date"]


def test_line_without_net_amount_is_not_confident(cii_xml):
    xml = cii_xml.replace(b"<ram:LineTotalAmount>120.00</ram:LineTotalAmount>", b"", 1)
    extraction = extract(xml)
    assert extraction.confidence["line_items"] == 0.0
    assert extraction.notes["line_items"] == "line 1: net amount missing"


def test_no_lines_is_not_confident(ubl_xml):
    start = ubl_xml.index(b"<cac:InvoiceLine>")
    end = ubl_xml.rindex(b"</cac:InvoiceLine>") + len(b"</cac:InvoiceLine>")
    extraction = extract(ubl_xml[:start] + ubl_xml[end:])
    assert extraction.data.line_items == []
    assert extraction.confidence["line_items"] == 0.0


def test_vat_total_uses_document_currency(ubl_xml):
    # A second TaxTotal in accounting currency (BT-111) placed first must not be picked.
    second = b'<cac:TaxTotal><cbc:TaxAmount currencyID="CHF">311.00</cbc:TaxAmount></cac:TaxTotal>'
    xml = ubl_xml.replace(b"<cac:TaxTotal>", second + b"<cac:TaxTotal>", 1)
    assert extract(xml).data.vat_total == Decimal("332.50")


def test_whitespace_in_names_is_normalised(ubl_xml):
    xml = ubl_xml.replace(b">Muster Software GmbH<", b">\n   Muster   Software\tGmbH  <")
    assert extract(xml).data.seller_name == "Muster Software GmbH"


def test_external_entity_is_not_resolved(tmp_path, ubl_xml):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT")
    doctype = f'<!DOCTYPE ubl:Invoice [<!ENTITY xxe SYSTEM "file://{secret}">]>'.encode()
    xml = ubl_xml.replace(b"?>", b"?>" + doctype, 1)
    xml = xml.replace(b">Muster Software GmbH<", b">&xxe;<")
    extraction = extract(xml)
    assert "TOP-SECRET-CONTENT" not in extraction.model_dump_json()
