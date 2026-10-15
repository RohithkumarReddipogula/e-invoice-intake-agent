import pytest

from intake.detect import InputKind, Syntax, UnsupportedDocument, detect


def test_ubl_xml(ubl_xml):
    detected = detect(ubl_xml)
    assert detected.kind is InputKind.XML
    assert detected.syntax is Syntax.UBL
    assert detected.is_credit_note is False


def test_cii_xml(cii_xml):
    detected = detect(cii_xml)
    assert detected.kind is InputKind.XML
    assert detected.syntax is Syntax.CII
    assert detected.is_credit_note is False


def test_zugferd_pdf(zugferd_pdf, cii_xml):
    detected = detect(zugferd_pdf)
    assert detected.kind is InputKind.ZUGFERD_PDF
    assert detected.syntax is Syntax.CII
    assert detected.xml == cii_xml


def test_plain_pdf(plain_pdf):
    detected = detect(plain_pdf)
    assert detected.kind is InputKind.PLAIN_PDF
    assert detected.syntax is None
    assert detected.root is None


def test_pdf_with_leading_junk(plain_pdf):
    assert detect(b"\x00\x00junk" + plain_pdf).kind is InputKind.PLAIN_PDF


def test_ubl_credit_note():
    xml = (
        b'<cn:CreditNote xmlns:cn="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"'
        b' xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        b"<cbc:ID>GS-1</cbc:ID></cn:CreditNote>"
    )
    detected = detect(xml)
    assert detected.syntax is Syntax.UBL
    assert detected.is_credit_note is True


def test_cii_type_code_381_is_credit_note(cii_xml):
    xml = cii_xml.replace(b"<ram:TypeCode>380</ram:TypeCode>", b"<ram:TypeCode>381</ram:TypeCode>")
    assert detect(xml).is_credit_note is True


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"hello, not an invoice", "not well-formed XML"),
        (b"<order><id>1</id></order>", "Unknown XML document"),
        (b"%PDF-1.7\nthis is not really a pdf", "Could not read the PDF"),
    ],
)
def test_unsupported(data, message):
    with pytest.raises(UnsupportedDocument, match=message):
        detect(data)
