"""Synthetic invoices as PDFs in three layouts, with distractors next to the real fields.

Distractors make the LLM's job realistic: a tax number (Steuernummer) next to the VAT ID,
an order and a customer number next to the invoice number, a BIC next to the IBAN.
`invariant=1` makes reportlab output byte-identical for the same input.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .dataset import Invoice

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]  # fmt: skip
BICS = ["COBADEFFXXX", "DEUTDEDBXXX", "BYLADEM1001", "HASPDEHHXXX", "GENODEF1S02"]


def de_amount(v: Decimal) -> str:
    whole, cents = f"{abs(v):.2f}".split(".")
    return ("-" if v < 0 else "") + f"{int(whole):,}".replace(",", ".") + "," + cents


def en_amount(v: Decimal) -> str:
    return f"{v:,.2f}"


def qty(v: Decimal, german: bool) -> str:
    text = f"{v.normalize():f}"
    return text.replace(".", ",") if german else text


def grouped(value: str) -> str:
    return " ".join(value[i : i + 4] for i in range(0, len(value), 4))


class Writer:
    """Tiny text layout helper: lines top-down, new page when full."""

    def __init__(self) -> None:
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4, invariant=1)
        self.y = 800.0

    def text(self, x: float, s: str, size: int = 10, bold: bool = False) -> None:
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        self.c.drawString(x, self.y, s)

    def right(self, x: float, s: str, size: int = 10) -> None:
        self.c.setFont("Helvetica", size)
        self.c.drawRightString(x, self.y, s)

    def down(self, step: float = 15) -> None:
        self.y -= step
        if self.y < 60:
            self.new_page()

    def new_page(self) -> None:
        self.c.showPage()
        self.y = 800.0

    def done(self) -> bytes:
        self.c.save()
        return self.buf.getvalue()


def _distractors(inv: Invoice) -> tuple[str, str, str]:
    seed = sum(map(ord, inv.id))
    order_no = f"PO-{4500000 + seed * 37 % 99999}"
    customer_no = f"{10000 + seed * 13 % 89999}"
    bic = BICS[seed % len(BICS)]
    return order_no, customer_no, bic


def classic_de(inv: Invoice) -> bytes:
    s, b = inv.seller, inv.buyer
    order_no, customer_no, bic = _distractors(inv)
    w = Writer()
    w.text(50, s.name, 16, bold=True)
    w.down(18)
    w.text(50, f"{s.street} · {s.postcode} {s.city} · {s.email}", 9)
    w.down(40)
    w.text(50, b.name)
    w.down()
    w.text(50, b.street)
    w.down()
    w.text(50, f"{b.postcode} {b.city}")
    w.down(35)
    w.text(50, "RECHNUNG", 14, bold=True)
    w.down(22)
    w.text(50, f"Rechnungsnummer: {inv.number}")
    w.text(320, f"Kundennummer: {customer_no}")
    w.down()
    w.text(50, f"Rechnungsdatum: {inv.issue_date:%d.%m.%Y}")
    w.text(320, f"Ihre Bestellung: {order_no}")
    w.down()
    if inv.due_date:
        w.text(50, f"Zahlbar bis: {inv.due_date:%d.%m.%Y}")
        w.down()
    w.down(15)
    w.text(50, "Pos", bold=True)
    w.text(80, "Bezeichnung", bold=True)
    w.right(330, "Menge")
    w.right(400, "Einzelpreis")
    w.right(460, "USt.")
    w.right(540, "Gesamt")
    w.down()
    for i, (desc, _unit, q, price, net, rate) in enumerate(inv.lines, 1):
        w.text(50, str(i))
        w.text(80, desc)
        w.right(330, qty(q, True))
        w.right(400, de_amount(price) + " €")
        w.right(460, f"{rate.normalize():f} %")
        w.right(540, de_amount(net) + " €")
        w.down()
    if len(inv.lines) >= 4:
        w.new_page()  # long invoices: totals on page 2
        w.text(50, f"Rechnung {inv.number} - Seite 2", 9)
        w.down(25)
    w.down(10)
    if inv.allowance:
        w.text(320, "Rabatt")
        w.right(540, "-" + de_amount(inv.allowance) + " €")
        w.down()
    w.text(320, "Nettobetrag")
    w.right(540, de_amount(inv.net_total) + " €")
    w.down()
    for rate, taxable, tax in inv.vat_breakdown:
        w.text(320, f"USt. {rate.normalize():f} % auf {de_amount(taxable)} €")
        w.right(540, de_amount(tax) + " €")
        w.down()
    if len(inv.vat_breakdown) > 1:
        # Without this line the VAT total is not printed, only computable; grounding (text
        # search) cannot confirm computed values, so such invoices would always go to review.
        w.text(320, "Summe USt.")
        w.right(540, de_amount(inv.vat_total) + " €")
        w.down()
    w.text(320, "Rechnungsbetrag", bold=True)
    w.right(540, de_amount(inv.gross_total) + " €")
    w.down(40)
    w.text(50, f"Bankverbindung: IBAN {grouped(s.iban)} · BIC {bic}", 9)
    w.down(13)
    w.text(50, f"Steuernummer: {s.tax_number} · USt-IdNr.: {s.vat_id}", 9)
    return w.done()


def modern_en(inv: Invoice) -> bytes:
    s, b = inv.seller, inv.buyer
    order_no, customer_no, bic = _distractors(inv)

    def day(d: date) -> str:
        return f"{d.day} {MONTHS_EN[d.month - 1]} {d.year}"

    w = Writer()
    w.text(50, "INVOICE", 20, bold=True)
    w.text(330, s.name, 12, bold=True)
    w.down(16)
    w.text(330, f"{s.street}, {s.postcode} {s.city}", 9)
    w.down(13)
    w.text(330, f"VAT ID: {s.vat_id}", 9)
    w.down(13)
    w.text(330, f"Tax no.: {s.tax_number}", 9)
    w.down(35)
    w.text(50, "Bill to:", bold=True)
    w.down()
    w.text(50, f"{b.name}, {b.street}, {b.postcode} {b.city}")
    w.down(30)
    w.text(50, f"Invoice No.: {inv.number}")
    w.down()
    w.text(50, f"Invoice date: {day(inv.issue_date)}")
    w.down()
    if inv.due_date:
        w.text(50, f"Due date: {day(inv.due_date)}")
        w.down()
    w.text(50, f"Customer ID: {customer_no}    Purchase order: {order_no}")
    w.down(30)
    w.text(50, "Description", bold=True)
    w.right(330, "Qty")
    w.right(410, "Unit price")
    w.right(460, "VAT")
    w.right(540, "Amount")
    w.down()
    for desc, _unit, q, price, net, rate in inv.lines:
        w.text(50, desc)
        w.right(330, qty(q, False))
        w.right(410, "EUR " + en_amount(price))
        w.right(460, f"{rate.normalize():f}%")
        w.right(540, "EUR " + en_amount(net))
        w.down()
    w.down(10)
    if inv.allowance:
        w.text(320, "Discount")
        w.right(540, "-EUR " + en_amount(inv.allowance))
        w.down()
    w.text(320, "Subtotal (net)")
    w.right(540, "EUR " + en_amount(inv.net_total))
    w.down()
    w.text(320, "VAT")
    w.right(540, "EUR " + en_amount(inv.vat_total))
    w.down()
    w.text(320, "Total due", bold=True)
    w.right(540, "EUR " + en_amount(inv.gross_total))
    w.down(40)
    w.text(50, f"Please transfer to IBAN {s.iban} (BIC {bic}).", 9)
    return w.done()


def compact_de(inv: Invoice) -> bytes:
    s, b = inv.seller, inv.buyer
    order_no, customer_no, bic = _distractors(inv)
    w = Writer()
    w.text(50, f"{s.name} | {s.street} | {s.postcode} {s.city}", 9)
    w.down(30)
    w.text(50, f"An: {b.name}, {b.postcode} {b.city}")
    w.down(25)
    issued = f"{inv.issue_date.day}.{inv.issue_date.month}.{inv.issue_date.year}"
    w.text(50, f"Rechnung Nr. {inv.number} vom {issued}", 12, bold=True)
    w.down(18)
    w.text(50, f"Auftrag {order_no} · Kunde {customer_no}", 9)
    w.down(25)
    for desc, unit, q, price, net, rate in inv.lines:
        w.text(
            50, f"{qty(q, True)} {unit} {desc} à {de_amount(price)} EUR ({rate.normalize():f} %)"
        )
        w.right(540, de_amount(net) + " EUR")
        w.down()
    w.down(8)
    if inv.allowance:
        w.text(50, f"abzgl. Rabatt {de_amount(inv.allowance)} EUR")
        w.down()
    vat_parts = ", ".join(
        f"{r.normalize():f} %: {de_amount(t)} EUR" for r, _x, t in inv.vat_breakdown
    )
    net, vat = de_amount(inv.net_total), de_amount(inv.vat_total)
    w.text(50, f"Summe netto {net} EUR, USt. ({vat_parts}) = {vat} EUR")
    w.down()
    w.text(50, f"Gesamtbetrag {de_amount(inv.gross_total)} EUR", bold=True)
    w.down(25)
    if inv.due_date:
        w.text(50, f"Fällig am {inv.due_date.day}.{inv.due_date.month}.{inv.due_date.year}.", 9)
        w.down(13)
    w.text(
        50, f"IBAN {grouped(s.iban)} / BIC {bic} / St.-Nr. {s.tax_number} / USt-ID {s.vat_id}", 9
    )
    return w.done()


LAYOUT_RENDERERS = {"classic_de": classic_de, "modern_en": modern_en, "compact_de": compact_de}


def render_pdf(inv: Invoice) -> bytes:
    return LAYOUT_RENDERERS[inv.layout](inv)
