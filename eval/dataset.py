"""Synthetic invoices with ground truth. Everything is invented and generated from a seed.

An invoice is first built consistent (correct sums, valid IDs), then a planted problem may
change what is printed. The ground truth is always what is *printed*: field accuracy
measures reading, and `planted` says what is wrong with the invoice itself.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from intake.models import InvoiceData, LineItem

CENT = Decimal("0.01")
EVAL_TODAY = date(2026, 10, 15)  # the pipeline's "today" during the eval (R5)

PLANTED_KINDS = ("lines_sum", "net_vat_gross", "vat_id", "iban", "date", "duplicate")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def iban_de(blz: str, account: str) -> str:
    """A German IBAN with correct check digits (ISO 13616 mod 97)."""
    bban = blz + account.zfill(10)
    number = int(bban + "131400")  # D=13, E=14, check digits 00
    return f"DE{98 - number % 97:02d}{bban}"


@dataclass(frozen=True)
class Party:
    name: str
    street: str
    postcode: str
    city: str
    email: str
    vat_id: str | None = None
    iban: str | None = None
    tax_number: str | None = None  # Steuernummer: a distractor next to the VAT ID
    contact: str = ""
    phone: str = ""
    number_format: str = "RE-{year}-{n:04d}"


SELLERS = [
    Party("Muster Software GmbH", "Musterstraße 1", "10115", "Berlin",
          "rechnung@muster-software.example", "DE123456789", iban_de("37040044", "532013000"),
          "30/123/45678", "Erika Beispiel", "+49 30 1234567", "RE-{year}-{n:04d}"),
    Party("Nordlicht Druck GmbH", "Fördeweg 12", "24103", "Kiel",
          "rechnung@nordlicht-druck.example", "DE987654321", iban_de("12030000", "202051"),
          "19/287/00451", "Max Probe", "+49 431 765432", "{year}/INV/{n:03d}"),
    Party("Alpenblick IT-Service KG", "Bergstraße 44", "80331", "München",
          "buchhaltung@alpenblick-it.example", "DE246813579", iban_de("70150000", "1023456"),
          "143/552/10987", "Sabine Gipfel", "+49 89 445566", "AB-{n:05d}"),
    Party("Rheinufer Logistik GmbH", "Hafenstraße 3", "50667", "Köln",
          "invoices@rheinufer-logistik.example", "DE135792468", iban_de("37050198", "9988776"),
          "215/5711/0816", "Jonas Fluss", "+49 221 998877", "RL{year}-{n:04d}"),
    Party("Elbtal Büroservice e.K.", "Marktplatz 9", "01067", "Dresden",
          "rechnung@elbtal-buero.example", "DE864209753", iban_de("85050300", "3120045678"),
          "202/111/22233", "Petra Elbe", "+49 351 223344", "EB-{year}{n:04d}"),
    Party("Hanse Consulting Partners GmbH", "Speicherstadt 21", "20457", "Hamburg",
          "billing@hanse-consulting.example", "DE975318642", iban_de("20050550", "1234567890"),
          "47/701/03456", "Lars Kontor", "+49 40 556677", "HCP-{n:06d}"),
    Party("Schwarzwald Werkzeugbau GmbH", "Industriering 7", "79098", "Freiburg",
          "fakturierung@sw-werkzeugbau.example", "DE192837465", iban_de("68050101", "12345678"),
          "06454/12345", "Tobias Tanne", "+49 761 334455", "SW/{year}/{n:05d}"),
    Party("Ostsee Catering UG", "Strandallee 2", "18055", "Rostock",
          "rechnung@ostsee-catering.example", "DE564738291", iban_de("13050000", "201234567"),
          "079/123/04567", "Mia Möwe", "+49 381 112233", "OC-{n:04d}"),
]  # fmt: skip

BUYERS = [
    Party("Beispiel Handels AG", "Hafenweg 7", "20457", "Hamburg",
          "einkauf@beispiel-handel.example"),
    Party("Stadtwerke Musterstadt GmbH", "Am Kraftwerk 1", "12345", "Musterstadt",
          "kreditoren@sw-musterstadt.example"),
    Party("Probe Maschinenbau AG", "Werkstraße 100", "70173", "Stuttgart",
          "ap@probe-maschinenbau.example"),
    Party("Kanzlei Recht & Ordnung", "Justizplatz 4", "60311", "Frankfurt am Main",
          "buero@recht-ordnung.example"),
    Party("Grünfeld Bio Markt eG", "Feldweg 8", "30159", "Hannover",
          "rechnungen@gruenfeld-bio.example"),
]  # fmt: skip

CATALOG = [
    # description, unit code, price range (EUR), VAT rate, quantity kind
    ("Software license, annual", "C62", (120, 900), 19, "int"),
    ("Support hours", "HUR", (65, 140), 19, "half"),
    ("Consulting day", "DAY", (650, 1400), 19, "int"),
    ("Flyer A5, 1000 pcs", "C62", (60, 180), 19, "int"),
    ("Brochure printing", "C62", (20, 90), 7, "int"),
    ("Specialist book", "C62", (25, 80), 7, "int"),
    ("Freight Hamburg-Munich", "C62", (180, 650), 19, "int"),
    ("Pallet storage per month", "MON", (12, 35), 19, "int"),
    ("Office chair", "C62", (180, 420), 19, "int"),
    ("Catering per person", "C62", (18, 45), 7, "int"),
    ("Milling cutter set", "SET", (90, 260), 19, "int"),
    ("Cloud hosting", "MON", (40, 300), 19, "int"),
]


@dataclass
class Invoice:
    """One synthetic invoice, as printed, plus how it was generated."""

    id: str
    kind: str  # xml_ubl | xml_cii | zugferd | plain_pdf
    layout: str  # plain PDFs and the visual part of ZUGFeRD: classic_de | modern_en | compact_de
    seller: Party
    buyer: Party
    number: str
    issue_date: date
    due_date: date | None
    lines: list[tuple[str, str, Decimal, Decimal, Decimal, Decimal]]
    # (description, unit code, quantity, unit price, line net, vat rate)
    allowance: Decimal
    net_total: Decimal
    vat_breakdown: list[tuple[Decimal, Decimal, Decimal]]  # (rate, taxable, tax)
    vat_total: Decimal
    gross_total: Decimal
    planted: str | None = None
    duplicate_of: str | None = None
    notes: list[str] = field(default_factory=list)

    def truth(self) -> InvoiceData:
        return InvoiceData(
            invoice_number=self.number,
            issue_date=self.issue_date,
            due_date=self.due_date,
            currency="EUR",
            seller_name=self.seller.name,
            seller_vat_id=self.seller.vat_id,
            seller_iban=self.seller.iban,
            buyer_name=self.buyer.name,
            line_items=[
                LineItem(description=d, quantity=q, unit_price=p, net_amount=n, vat_rate=r)
                for d, _unit, q, p, n, r in self.lines
            ],
            allowance_total=self.allowance,
            charge_total=Decimal("0"),
            net_total=self.net_total,
            vat_total=self.vat_total,
            gross_total=self.gross_total,
        )


def build_invoice(rng: random.Random, invoice_id: str, kind: str, layout: str, n: int) -> Invoice:
    seller = rng.choice(SELLERS)
    buyer = rng.choice(BUYERS)
    issue = EVAL_TODAY - timedelta(days=rng.randint(3, 270))
    due = None if rng.random() < 0.1 else issue + timedelta(days=rng.choice([14, 30, 30, 60]))

    lines = []
    for description, unit, (low, high), rate, qty_kind in rng.sample(CATALOG, rng.randint(1, 5)):
        if qty_kind == "half":
            quantity = Decimal(rng.randint(1, 24)) / 2
        else:
            quantity = Decimal(rng.randint(1, 12))
        price = money(Decimal(rng.randint(low * 100, high * 100)) / 100)
        lines.append((description, unit, quantity, price, money(quantity * price), Decimal(rate)))

    has_19 = any(r == 19 for *_, r in lines)
    allowance = Decimal("0")
    if has_19 and rng.random() < 0.15:
        allowance = money(Decimal(rng.randint(500, 5000)) / 100)

    return _with_totals(
        Invoice(
            id=invoice_id,
            kind=kind,
            layout=layout,
            seller=seller,
            buyer=buyer,
            number=seller.number_format.format(year=issue.year, n=n),
            issue_date=issue,
            due_date=due,
            lines=lines,
            allowance=allowance,
            net_total=Decimal("0"),
            vat_breakdown=[],
            vat_total=Decimal("0"),
            gross_total=Decimal("0"),
        )
    )


def _with_totals(inv: Invoice) -> Invoice:
    """Correct totals (EN 16931): VAT per rate on the taxable amount, allowance at 19 %."""
    by_rate: dict[Decimal, Decimal] = {}
    for *_, net, rate in inv.lines:
        by_rate[rate] = by_rate.get(rate, Decimal("0")) + net
    if inv.allowance:
        by_rate[Decimal(19)] -= inv.allowance
    breakdown = [
        (rate, money(taxable), money(taxable * rate / 100))
        for rate, taxable in sorted(by_rate.items(), reverse=True)
    ]
    net_total = money(sum((n for *_, n, _r in inv.lines), Decimal("0")) - inv.allowance)
    vat_total = sum((tax for *_, tax in breakdown), Decimal("0"))
    return replace(
        inv,
        net_total=net_total,
        vat_breakdown=breakdown,
        vat_total=vat_total,
        gross_total=net_total + vat_total,
    )


def plant(rng: random.Random, inv: Invoice, kind: str, earlier: list[Invoice]) -> Invoice:
    """Make the printed invoice wrong in one way. Ground truth follows the print."""
    if kind == "lines_sum":
        # Net total does not match the lines; VAT and gross are consistent with the wrong net.
        delta = money(Decimal(rng.choice([10, 25, 50, 100])))
        net = inv.net_total + delta
        vat = sum((tax for *_, tax in inv.vat_breakdown), Decimal("0"))
        return replace(inv, planted=kind, net_total=net, gross_total=net + vat)
    if kind == "net_vat_gross":
        return replace(inv, planted=kind, gross_total=inv.gross_total + Decimal("1.00"))
    if kind == "vat_id":
        bad = inv.seller.vat_id[:-1]  # DE + 8 digits
        return replace(inv, planted=kind, seller=replace(inv.seller, vat_id=bad))
    if kind == "iban":
        iban = inv.seller.iban
        last = str((int(iban[-1]) + 1) % 10)
        return replace(inv, planted=kind, seller=replace(inv.seller, iban=iban[:-1] + last))
    if kind == "date":
        if rng.random() < 0.5 or inv.due_date is None:
            future = EVAL_TODAY + timedelta(days=rng.randint(5, 60))
            return replace(inv, planted=kind, issue_date=future, due_date=None)
        return replace(inv, planted=kind, due_date=inv.issue_date - timedelta(days=10))
    if kind == "duplicate":
        original = rng.choice(earlier)
        # The same invoice sent again; the file may look different (other kind or layout).
        return replace(original, id=inv.id, kind=inv.kind, layout=inv.layout, planted=kind,
                       duplicate_of=original.id)  # fmt: skip
    raise ValueError(kind)


SPLIT_COUNTS = {"xml_ubl": 13, "xml_cii": 12, "zugferd": 15, "plain_pdf": 35}  # 75 per split
LAYOUTS = ("classic_de", "modern_en", "compact_de")
PLANTED_SHARE = 0.25


def generate_split(split: str, seed: int) -> list[Invoice]:
    """75 invoices in processing order; duplicates always come after their original."""
    rng = random.Random(f"{seed}:{split}")
    kinds = [k for k, count in SPLIT_COUNTS.items() for _ in range(count)]
    rng.shuffle(kinds)
    n_planted = round(len(kinds) * PLANTED_SHARE)
    planted_slots = set(rng.sample(range(10, len(kinds)), n_planted))  # leave room for originals
    planted_kinds = [PLANTED_KINDS[i % len(PLANTED_KINDS)] for i in range(n_planted)]
    rng.shuffle(planted_kinds)

    invoices: list[Invoice] = []
    for i, kind in enumerate(kinds):
        layout = LAYOUTS[i % len(LAYOUTS)]
        invoice_id = f"{split}-{i + 1:03d}"
        # Unique within the split (step 17 > jitter 9), so only planted duplicates repeat.
        number = 1000 + 17 * i + rng.randint(0, 9)
        inv = build_invoice(rng, invoice_id, kind, layout, number)
        if i in planted_slots:
            originals = [x for x in invoices if x.planted is None]
            inv = plant(rng, inv, planted_kinds.pop(), originals)
        invoices.append(inv)
    return invoices
