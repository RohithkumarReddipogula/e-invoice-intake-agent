# e-invoice-intake-agent: Specification

Status: draft for review. No code yet.

## 1. Goal

An intake agent for incoming invoices. It reads an invoice, extracts the fields, checks
them, and either approves it automatically or sends it to a human with a clear reason.
Approved invoices go to an ERP system.

The main promise: **an invoice is auto-approved only when we are sure.** Every field must be
confident and every rule must pass. Everything else goes to a person. The eval measures
whether this promise holds (escaped errors).

This is a public portfolio project. All sample invoices are synthetic. No real company or
personal data is in the repository.

## 2. Inputs

| Input | How we detect it | How we extract the fields |
|---|---|---|
| XRechnung XML (UBL or CII) | XML root element | Read the XML directly (XPath). No LLM. |
| ZUGFeRD / Factur-X PDF | PDF with an embedded invoice XML | Extract the XML, then read it directly. No LLM. |
| Plain PDF | PDF without embedded invoice XML | Text from the PDF, then LLM with a Pydantic schema. |

- Max file size: 20 MB.
- Plain PDFs without a text layer (scans) are not supported in v1. They go to review with
  the reason "PDF has no text layer (scan); OCR is not supported".
- Credit notes (UBL CreditNote, CII type code 381) are detected and sent to review with the
  reason "credit notes are not supported yet".
- Anything else (not PDF, not invoice XML) is rejected at upload with an error message.

## 3. Extracted fields

One Pydantic model `InvoiceData` for all three input types:

| Field | Type | Notes |
|---|---|---|
| `invoice_number` | str | BT-1 |
| `issue_date` | date | BT-2 |
| `due_date` | date \| None | BT-9 |
| `currency` | str | ISO 4217, BT-5 |
| `seller_name` | str | BT-27 |
| `seller_vat_id` | str \| None | BT-31 |
| `seller_iban` | str \| None | BT-84 |
| `buyer_name` | str | BT-44 |
| `line_items` | list[LineItem] | description, quantity, unit_price, net_amount, vat_rate |
| `allowance_total` | Decimal | BT-107, default 0 |
| `charge_total` | Decimal | BT-108, default 0 |
| `net_total` | Decimal | BT-109 (total without VAT) |
| `vat_total` | Decimal | BT-110 |
| `gross_total` | Decimal | BT-112 (total with VAT) |

All money values are `Decimal`, never `float`.

Every field has a **confidence** in [0, 1] and a **source** (`xml` or `llm`):

- Fields read from XML: confidence 1.0.
- Fields from the LLM: `confidence = min(llm_confidence, grounding)`, where
  - `llm_confidence` is the score the LLM returns per field (part of the schema), and
  - `grounding` is a deterministic check: 1.0 if the value (normalised) appears in the PDF
    text, else 0.0. Example: the LLM says the IBAN is `DE89...`, but that string is not in
    the text, so the field is not confident, no matter what the LLM says.

  LLM self-reported confidence is known to be badly calibrated. That is why we combine it
  with grounding, and why the eval checks it (escaped errors, section 9).
- A field is **confident** when `confidence >= CONFIDENCE_THRESHOLD` (default 0.9, set in
  config, tuned on the eval dev split, never on the test split).

## 4. Business rules

Each rule returns `pass`, `fail` or `skip` (with a reason). Money comparisons use a tolerance
of 0.01 in the invoice currency.

| ID | Rule | Fails when |
|---|---|---|
| R1 `lines_sum` | sum(line net amounts) - allowances + charges = net_total | difference > 0.01 |
| R2 `net_vat_gross` | net_total + vat_total = gross_total | difference > 0.01 |
| R3 `vat_id_format` | seller VAT ID matches the format of its country prefix (EU VIES formats, e.g. `DE` + 9 digits) | wrong format; missing VAT ID is a fail too (needed for a correct German invoice, except small businesses, which go to review) |
| R4 `iban_checksum` | IBAN has a valid country length and passes mod-97 | invalid; missing IBAN is `skip` |
| R5 `dates` | issue date is a real date, not in the future, not older than 2 years; due date (if any) >= issue date | any of these |
| R6 `duplicate` | no other invoice with the same (seller key, normalised invoice number) | duplicate found |
| R7 `structured_validation` | for XML and ZUGFeRD only: e-rechnung-inbox status is `valid` | status `invalid`, `not_checked` or `error` |

- Seller key for R6: normalised VAT ID if present, else normalised seller name.
  Normalised invoice number: upper case, no spaces. R6 compares against all earlier
  invoices in the database, in any status except `rejected`.
- R7 is skipped for plain PDFs (no XML to validate).
- R3 checks format only. No online VIES lookup in v1 (no network calls in rules).

## 5. Structured validation (dependency)

Structured invoices are validated with
[e-rechnung-inbox](https://github.com/RohithkumarReddipogula/e-rechnung-inbox), installed as
a normal dependency. Its code is not copied into this repository.

- Install: it is not on PyPI, so `pyproject.toml` pins a git commit:
  `e-rechnung-inbox @ git+https://github.com/RohithkumarReddipogula/e-rechnung-inbox@<sha>`.
- We use its public API: `check_file()` / `check_files()` and `CheckResult`.
- All calls go through one adapter module (`intake/structured.py`). If the dependency
  changes, only this file changes.
- e-rechnung-inbox runs the KoSIT validator in **Docker**. If Docker or the image is not
  available, the result status is `error`, R7 fails, and the invoice goes to review with
  the reason. It is never auto-approved without a successful validation.
- Field extraction from XML (section 3) is our own code (XPath for UBL and CII).
  e-rechnung-inbox validates; it does not extract fields.

## 6. Decision

```
auto_approved  if  every field is confident  AND  every rule is pass or skip
needs_review   otherwise
```

The review reason lists every problem in plain language, for example:

- "Field `seller_iban` has low confidence (0.42): value not found in the PDF text."
- "R2 net_vat_gross failed: 100.00 + 19.00 = 119.00, but gross is 120.00."
- "R7 structured_validation: rejected by the KoSIT validator, BR-DE-15: Buyer reference is missing."

## 7. Invoice lifecycle

```
received -> extracted -> auto_approved ----------------> exported
                      \-> needs_review -> approved ----> exported
                                      \-> rejected
(extraction error)    -> needs_review   (reason: what failed)
(ERP call fails)      -> export_failed  (retry possible)
```

Every status change is written to an `events` table: who (system, `web:<name>`,
`mcp:<client>`), when, old status, new status, note. This is the audit trail.

## 8. Components

### 8.1 REST API (FastAPI)

| Method | Path | What it does |
|---|---|---|
| POST | `/invoices` | Upload a file, run the pipeline, return the invoice with status |
| GET | `/invoices?status=` | List invoices |
| GET | `/invoices/{id}` | Fields, confidences, rule results, events |
| PATCH | `/invoices/{id}/fields` | Human correction of fields; rules run again |
| POST | `/invoices/{id}/approve` | Approve (needs a reviewer name; a note is required if a rule still fails) |
| POST | `/invoices/{id}/reject` | Reject with a reason |
| POST | `/invoices/{id}/export` | Retry export after `export_failed` |
| GET | `/invoices/{id}/file` | The original file |

### 8.2 Review web page

Server-rendered HTML (FastAPI + Jinja2, no JavaScript build step).

- Queue page: all `needs_review` invoices, with the main reason in one line.
- Detail page: the original PDF/XML next to the fields; low-confidence fields and failed
  rules are highlighted, each with its reason. The reviewer can correct fields, then
  approve or reject.
- No login in v1. The server binds to `127.0.0.1` by default. This is stated in the README.

### 8.3 Mock ERP

A separate small FastAPI app (`mock_erp/`), its own port and its own SQLite file.

- `POST /api/invoices` with the approved invoice as JSON. Header `Idempotency-Key: <invoice id>`;
  sending the same invoice twice creates it only once.
- `GET /api/invoices` to see what arrived.
- Can be started in a "flaky" mode (fails every n-th request) to test retries.

The intake app sends to the ERP right after approval (HTTP client with timeout and 3
retries). On failure the status is `export_failed`, and the export can be retried.

### 8.4 MCP server

Python MCP SDK, stdio transport. Uses the same service layer as the REST API (no
duplicated logic).

| Tool | Input | Output |
|---|---|---|
| `list_invoices` | `status` (optional), `limit` (default 20, max 100) | id, number, seller, gross, status, main reason |
| `get_invoice` | `invoice_id` | fields with confidences, rule results, review reasons, events |
| `approve_invoice` | `invoice_id`, `note` | new status |

`approve_invoice` follows the same rules as the web page: only invoices in
`needs_review`, a note is required, and the event is logged as `mcp:<client>`.
It refuses invoices with any failed rule (decision 4): those need the web page.

### 8.5 Storage

SQLite (one file, path from config). Tables: `invoices`, `fields` (value, confidence,
source, corrected_by), `rule_results`, `events`, `exports`. Original files are stored on
disk under `data/files/<sha256>`. The schema is created by plain SQL migrations.

### 8.6 LLM extraction

- Provider: Anthropic Claude (see open question 1), model name in config.
- The PDF text (from `pypdf`) goes into the prompt. The answer must match the Pydantic
  schema `LlmInvoiceExtraction` (same fields as section 3, each with `value` and
  `confidence`). An answer that does not match the schema counts as an extraction error,
  so the invoice goes to review.
- **Disk cache:** key = sha256(model, prompt version, schema hash, PDF text). Stored as
  JSON under `llm_cache/`. The cache for the eval set is committed to git, so CI and anyone
  cloning the repo can run the eval without a key.
- **Offline mode:** `LLM_OFFLINE=1` (set in CI). A cache miss then raises an error. The
  network client is not even created. A test checks this.
- **Secrets:** the API key is read from the environment, loaded from `.env`. `.env` is in
  `.gitignore`; `.env.example` (no values) is committed. The key is a `SecretStr` and is
  never logged or printed.

## 9. Evaluation (`eval/`)

### 9.1 Dataset

Synthetic and generated by a script with a fixed seed, so it is reproducible.

- About 150 invoices: 50 XRechnung XML (UBL and CII), 30 ZUGFeRD PDFs, 70 plain PDFs in
  several layouts (different templates, one-page and multi-page, German and English
  labels).
- Each invoice has a ground-truth JSON with the correct field values and the expected
  decision.
- About 25% have a planted problem: wrong sum, wrong VAT, broken IBAN, bad VAT ID,
  impossible date, duplicate number. These must never be auto-approved.
- Split: `dev` (tune the threshold) and `test` (report numbers). Fixed split, committed.

### 9.2 Metrics

| Metric | Definition |
|---|---|
| Field accuracy (per field) | share of invoices where the extracted value equals ground truth after normalisation (Decimal equality for money, ISO dates, trimmed case-folded strings). Also reported per input type, because XML fields are trivially correct. |
| Auto-approve rate | auto-approved / all invoices |
| Escaped errors | auto-approved invoices that have at least one wrong field **or** a planted problem. Reported as count and as rate per auto-approved invoice. Target: 0. |
| Confidence check | accuracy of fields above vs. below the threshold |

All rates get a **bootstrap 95% confidence interval**: resample invoices (not fields) with
replacement, 10,000 resamples, percentile method, fixed seed.

### 9.3 Output

`python -m eval.run --split test` writes `eval/results/<date>.json` and a Markdown table
(`eval/results/latest.md`), which the README links to. With a small dataset the intervals
are wide; the report says so instead of hiding it.

## 10. Quality and CI

- Python 3.12, `ruff check` and `ruff format --check`, `pytest`.
- GitHub Actions on push and pull request:
  1. lint
  2. unit and integration tests (`pytest -m "not docker"`), `LLM_OFFLINE=1`, no API key
  3. eval smoke run on the committed cache (offline), fails if escaped errors > 0
  4. optional job: build the KoSIT image and run the `docker` tests (like e-rechnung-inbox does)
- The real validator is replaced by a fake adapter in unit tests (recorded `CheckResult`s).
- Actions pinned to commit SHAs, `permissions: contents: read`.

## 11. Security notes

- XML parsing with entity resolution and network access off (no XXE).
- Upload: size limit, file type checked by content, not by file name.
- Files are stored under their hash, never under the uploaded file name.
- PDF text goes into the LLM prompt: a PDF could contain prompt-injection text. The LLM
  only fills a schema and has no tools, and all values go through rules and grounding,
  so injected text can at worst produce wrong values, which then go to review.

## 12. Out of scope (v1)

OCR for scanned PDFs, credit notes, login and roles, online VIES check, real ERP
connectors, email inbox polling, multi-currency conversion.

## 13. Decisions (answered 2026-10-15)

1. **LLM:** Anthropic, model configurable. Candidates: `claude-sonnet-5-5` ($2 / $10 per
   million input / output tokens) and `claude-haiku-5-5` ($0.10 / $0.50). Both are compared on
   the dev split; the cheaper one wins if accuracy is similar. Cost estimate before any live call.
2. **Pinning:** e-rechnung-inbox pinned to commit `2e34c14`. No tag for now.
3. **Docker in CI:** optional job builds the KoSIT image and runs the `docker` tests, like
   e-rechnung-inbox does.
4. **MCP approve:** `approve_invoice` only for invoices held for low confidence, never for
   rule failures.
5. **Branch:** `main`.
