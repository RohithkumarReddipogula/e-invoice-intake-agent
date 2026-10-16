# Rollout plan

How this intake agent would go from a portfolio project to production use in an
accounts-payable team. The principle: **automation is earned with measured evidence, one
input type at a time, and every step can be undone.** The key number at every step is the
escaped-error count (auto-approved invoices with a wrong value or a real problem).

## 1. Discovery (2–4 weeks)

- Collect a representative sample of real incoming invoices (e.g. three months): share of
  XRechnung, ZUGFeRD and plain PDFs, scans, credit notes, foreign suppliers, typical amounts.
- Map today's process: who checks what, where invoices are booked, approval limits, how
  duplicates and wrong invoices are handled today, current error rate and handling time.
- Label a few hundred real invoices with ground truth, the same way as the synthetic eval,
  and rerun the eval on them. The synthetic results in the README do **not** transfer; this
  real eval is the baseline for everything below.
- Clarify data protection with the data protection officer and IT: data processing agreement
  with the LLM provider, EU processing, retention, access (see Guardrails).

## 2. Shadow mode (4–8 weeks)

- The agent processes every incoming invoice in parallel, but **decides nothing**: no export
  to the ERP, the team keeps working as today.
- Every agent decision is compared with the human decision. Each disagreement is reviewed:
  agent error, human error, or unclear case.
- Exit criterion: on a few hundred invoices per input type, zero escaped errors and an
  understood list of the remaining review reasons. Report the one-sided 95% upper bound,
  not just "zero".

## 3. Pilot: auto-approval for structured XML only

- Switch on auto-approval only for **XRechnung and ZUGFeRD** invoices (fields read from XML,
  validated by KoSIT, all rules pass). Plain PDFs keep going to review, with the extracted
  fields pre-filled to save typing.
- **Turn on both guardrails in the pilot:** `INTAKE_MAX_AUTO_AMOUNT` (e.g. the amount a
  single clerk may approve today) and `INTAKE_REVIEW_NEW_SUPPLIERS=1`. Both are off by
  default because the published eval was measured without them.
- Start with a small group of known suppliers, then all suppliers sending XML.
- A person samples a share of auto-approved invoices every week (e.g. 10%) and checks them.
  Any escaped error stops automation for that input type until the cause is fixed.

## 4. Gradual widening, while escaped errors stay at zero

- Widen one step at a time, each with its own measurement period: more suppliers, then
  plain PDFs from known layouts with a strict threshold, then higher amount limits.
- Before each step: the escaped-error count of the previous step is zero, the sample checks
  agree, and the upper bound on escaped errors is below the agreed tolerance.
- Every step can be undone with configuration (threshold, allowed input types, amount
  limit), without a deployment.
- Re-run the eval whenever the model, the prompt or the validator configuration changes;
  CI already fails on any escaped error in the eval.

## Guardrails

| Guardrail | Why | State in this repository |
|---|---|---|
| **Amount limit:** above a configurable gross amount, always a person | Limits the damage of any error that gets through | Implemented, off by default: `INTAKE_MAX_AUTO_AMOUNT`. **Turn on in the pilot.** |
| **New suppliers always reviewed:** a seller without an earlier accepted invoice, or a known seller with an IBAN not used on its accepted invoices, goes to a person | New supplier and changed bank details are the classic invoice-fraud pattern | Implemented, off by default: `INTAKE_REVIEW_NEW_SUPPLIERS=1`. **Turn on in the pilot.** |
| **Audit log:** every status change with actor, time, old/new status and note; every ERP export attempt | Traceability for audits (GoBD) and for analysing errors | Implemented (`events` and `exports` tables, shown on the review page) |
| **EU data:** invoice data processed only by providers with EU processing and a data processing agreement | GDPR; invoices contain personal and business data | **Not guaranteed today.** The eval used OpenRouter without region pinning (calls were served by Azure). Fine for synthetic data, not for real invoices: choose an endpoint with EU data processing before any real data |
| **No unvalidated structured invoice is auto-approved** | KoSIT is the reference for XRechnung | Implemented (R7 fails if the validator cannot run) |
| **LLM only fills a schema, without tools** | Prompt injection in an invoice can at worst produce wrong values, which grounding and the rules catch or send to review | Implemented |
| **Login and roles** for the review page | Who may approve what, four-eyes principle above a limit | Not implemented (v1 binds to 127.0.0.1 only) |
