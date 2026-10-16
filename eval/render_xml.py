"""Synthetic invoices as XRechnung 3.0 XML (UBL and CII), same structure as the test fixtures."""

from __future__ import annotations

from decimal import Decimal
from xml.sax.saxutils import escape

from .dataset import Invoice, Party

XRECHNUNG = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
PEPPOL = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
BUYER_REFERENCE = "04011000-12345-34"


def e(value: object) -> str:
    return escape(str(value))


def amt(value: Decimal) -> str:
    return f"{value:.2f}"


def rate(value: Decimal) -> str:
    return f"{value.normalize():f}"


# --- UBL -------------------------------------------------------------------------------


def _ubl_party(p: Party, seller: bool) -> str:
    tax = ""
    if seller and p.vat_id:
        tax = f"""
      <cac:PartyTaxScheme>
        <cbc:CompanyID>{e(p.vat_id)}</cbc:CompanyID>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>
      </cac:PartyTaxScheme>"""
    contact = ""
    if seller:
        contact = f"""
      <cac:Contact>
        <cbc:Name>{e(p.contact)}</cbc:Name>
        <cbc:Telephone>{e(p.phone)}</cbc:Telephone>
        <cbc:ElectronicMail>{e(p.email)}</cbc:ElectronicMail>
      </cac:Contact>"""
    return f"""
    <cac:Party>
      <cbc:EndpointID schemeID="EM">{e(p.email)}</cbc:EndpointID>
      <cac:PostalAddress>
        <cbc:StreetName>{e(p.street)}</cbc:StreetName>
        <cbc:CityName>{e(p.city)}</cbc:CityName>
        <cbc:PostalZone>{e(p.postcode)}</cbc:PostalZone>
        <cac:Country><cbc:IdentificationCode>DE</cbc:IdentificationCode></cac:Country>
      </cac:PostalAddress>{tax}
      <cac:PartyLegalEntity><cbc:RegistrationName>{e(p.name)}</cbc:RegistrationName></cac:PartyLegalEntity>{contact}
    </cac:Party>"""


def ubl(inv: Invoice) -> bytes:
    due = f"\n  <cbc:DueDate>{inv.due_date.isoformat()}</cbc:DueDate>" if inv.due_date else ""
    allowance = ""
    if inv.allowance:
        allowance = f"""
  <cac:AllowanceCharge>
    <cbc:ChargeIndicator>false</cbc:ChargeIndicator>
    <cbc:AllowanceChargeReason>Rabatt</cbc:AllowanceChargeReason>
    <cbc:Amount currencyID="EUR">{amt(inv.allowance)}</cbc:Amount>
    <cac:TaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>19</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory>
  </cac:AllowanceCharge>"""
    subtotals = "".join(
        f"""
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">{amt(taxable)}</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">{amt(tax)}</cbc:TaxAmount>
      <cac:TaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>{rate(r)}</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory>
    </cac:TaxSubtotal>"""
        for r, taxable, tax in inv.vat_breakdown
    )
    line_sum = sum((n for *_, n, _r in inv.lines), Decimal("0"))
    allowance_total = (
        f'\n    <cbc:AllowanceTotalAmount currencyID="EUR">{amt(inv.allowance)}</cbc:AllowanceTotalAmount>'
        if inv.allowance
        else ""
    )
    lines = "".join(
        f"""
  <cac:InvoiceLine>
    <cbc:ID>{i}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="{unit}">{rate(q)}</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">{amt(net)}</cbc:LineExtensionAmount>
    <cac:Item>
      <cbc:Name>{e(desc)}</cbc:Name>
      <cac:ClassifiedTaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>{rate(r)}</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="EUR">{amt(price)}</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>"""
        for i, (desc, unit, q, price, net, r) in enumerate(inv.lines, 1)
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- Synthetic eval invoice {e(inv.id)}. All companies, people and numbers are invented. -->
<ubl:Invoice xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
             xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
             xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>{XRECHNUNG}</cbc:CustomizationID>
  <cbc:ProfileID>{PEPPOL}</cbc:ProfileID>
  <cbc:ID>{e(inv.number)}</cbc:ID>
  <cbc:IssueDate>{inv.issue_date.isoformat()}</cbc:IssueDate>{due}
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cbc:BuyerReference>{BUYER_REFERENCE}</cbc:BuyerReference>
  <cac:AccountingSupplierParty>{_ubl_party(inv.seller, True)}
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>{_ubl_party(inv.buyer, False)}
  </cac:AccountingCustomerParty>
  <cac:PaymentMeans>
    <cbc:PaymentMeansCode>58</cbc:PaymentMeansCode>
    <cac:PayeeFinancialAccount><cbc:ID>{e(inv.seller.iban)}</cbc:ID></cac:PayeeFinancialAccount>
  </cac:PaymentMeans>
  <cac:PaymentTerms><cbc:Note>Zahlbar ohne Abzug.</cbc:Note></cac:PaymentTerms>{allowance}
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">{amt(inv.vat_total)}</cbc:TaxAmount>{subtotals}
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">{amt(line_sum)}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">{amt(inv.net_total)}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">{amt(inv.gross_total)}</cbc:TaxInclusiveAmount>{allowance_total}
    <cbc:PayableAmount currencyID="EUR">{amt(inv.gross_total)}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>{lines}
</ubl:Invoice>
"""
    return xml.encode("utf-8")


# --- CII -------------------------------------------------------------------------------


def _cii_address(p: Party) -> str:
    return f"""
        <ram:PostalTradeAddress>
          <ram:PostcodeCode>{e(p.postcode)}</ram:PostcodeCode>
          <ram:LineOne>{e(p.street)}</ram:LineOne>
          <ram:CityName>{e(p.city)}</ram:CityName>
          <ram:CountryID>DE</ram:CountryID>
        </ram:PostalTradeAddress>
        <ram:URIUniversalCommunication><ram:URIID schemeID="EM">{e(p.email)}</ram:URIID></ram:URIUniversalCommunication>"""


def cii(inv: Invoice) -> bytes:
    lines = "".join(
        f"""
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument><ram:LineID>{i}</ram:LineID></ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedTradeProduct><ram:Name>{e(desc)}</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice><ram:ChargeAmount>{amt(price)}</ram:ChargeAmount></ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="{unit}">{rate(q)}</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:ApplicableTradeTax><ram:TypeCode>VAT</ram:TypeCode><ram:CategoryCode>S</ram:CategoryCode><ram:RateApplicablePercent>{rate(r)}</ram:RateApplicablePercent></ram:ApplicableTradeTax>
        <ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>{amt(net)}</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>"""
        for i, (desc, unit, q, price, net, r) in enumerate(inv.lines, 1)
    )
    taxes = "".join(
        f"""
      <ram:ApplicableTradeTax>
        <ram:CalculatedAmount>{amt(tax)}</ram:CalculatedAmount>
        <ram:TypeCode>VAT</ram:TypeCode>
        <ram:BasisAmount>{amt(taxable)}</ram:BasisAmount>
        <ram:CategoryCode>S</ram:CategoryCode>
        <ram:RateApplicablePercent>{rate(r)}</ram:RateApplicablePercent>
      </ram:ApplicableTradeTax>"""
        for r, taxable, tax in inv.vat_breakdown
    )
    allowance = ""
    if inv.allowance:
        allowance = f"""
      <ram:SpecifiedTradeAllowanceCharge>
        <ram:ChargeIndicator><udt:Indicator>false</udt:Indicator></ram:ChargeIndicator>
        <ram:ActualAmount>{amt(inv.allowance)}</ram:ActualAmount>
        <ram:Reason>Rabatt</ram:Reason>
        <ram:CategoryTradeTax><ram:TypeCode>VAT</ram:TypeCode><ram:CategoryCode>S</ram:CategoryCode><ram:RateApplicablePercent>19</ram:RateApplicablePercent></ram:CategoryTradeTax>
      </ram:SpecifiedTradeAllowanceCharge>"""
    due = ""
    if inv.due_date:
        due = f"""
        <ram:DueDateDateTime><udt:DateTimeString format="102">{inv.due_date:%Y%m%d}</udt:DateTimeString></ram:DueDateDateTime>"""
    line_sum = sum((n for *_, n, _r in inv.lines), Decimal("0"))
    s = inv.seller
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- Synthetic eval invoice {e(inv.id)}. All companies, people and numbers are invented. -->
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
                          xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
                          xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocumentContext>
    <ram:BusinessProcessSpecifiedDocumentContextParameter><ram:ID>{PEPPOL}</ram:ID></ram:BusinessProcessSpecifiedDocumentContextParameter>
    <ram:GuidelineSpecifiedDocumentContextParameter><ram:ID>{XRECHNUNG}</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument>
    <ram:ID>{e(inv.number)}</ram:ID>
    <ram:TypeCode>380</ram:TypeCode>
    <ram:IssueDateTime><udt:DateTimeString format="102">{inv.issue_date:%Y%m%d}</udt:DateTimeString></ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>{lines}
    <ram:ApplicableHeaderTradeAgreement>
      <ram:BuyerReference>{BUYER_REFERENCE}</ram:BuyerReference>
      <ram:SellerTradeParty>
        <ram:Name>{e(s.name)}</ram:Name>
        <ram:DefinedTradeContact>
          <ram:PersonName>{e(s.contact)}</ram:PersonName>
          <ram:TelephoneUniversalCommunication><ram:CompleteNumber>{e(s.phone)}</ram:CompleteNumber></ram:TelephoneUniversalCommunication>
          <ram:EmailURIUniversalCommunication><ram:URIID>{e(s.email)}</ram:URIID></ram:EmailURIUniversalCommunication>
        </ram:DefinedTradeContact>{_cii_address(s)}
        <ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">{e(s.vat_id)}</ram:ID></ram:SpecifiedTaxRegistration>
      </ram:SellerTradeParty>
      <ram:BuyerTradeParty>
        <ram:Name>{e(inv.buyer.name)}</ram:Name>{_cii_address(inv.buyer)}
      </ram:BuyerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeDelivery/>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementPaymentMeans>
        <ram:TypeCode>58</ram:TypeCode>
        <ram:PayeePartyCreditorFinancialAccount><ram:IBANID>{e(s.iban)}</ram:IBANID></ram:PayeePartyCreditorFinancialAccount>
      </ram:SpecifiedTradeSettlementPaymentMeans>{taxes}{allowance}
      <ram:SpecifiedTradePaymentTerms>
        <ram:Description>Zahlbar ohne Abzug.</ram:Description>{due}
      </ram:SpecifiedTradePaymentTerms>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:LineTotalAmount>{amt(line_sum)}</ram:LineTotalAmount>
        <ram:ChargeTotalAmount>0.00</ram:ChargeTotalAmount>
        <ram:AllowanceTotalAmount>{amt(inv.allowance)}</ram:AllowanceTotalAmount>
        <ram:TaxBasisTotalAmount>{amt(inv.net_total)}</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount currencyID="EUR">{amt(inv.vat_total)}</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>{amt(inv.gross_total)}</ram:GrandTotalAmount>
        <ram:DuePayableAmount>{amt(inv.gross_total)}</ram:DuePayableAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""
    return xml.encode("utf-8")
