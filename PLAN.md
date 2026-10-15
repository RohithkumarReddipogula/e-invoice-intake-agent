# Plan

Build in small milestones. Each milestone ends with: `ruff check`, `ruff format --check` and
`pytest` all green, then a short summary for you. **I ask before every git commit or push.**

## M0: Project skeleton
- `pyproject.toml` (Python 3.12, hatchling, ruff config, pytest markers `docker`, `live_llm`).
- Dependencies: fastapi, uvicorn, pydantic, pydantic-settings, jinja2, httpx, lxml, pypdf,
  anthropic, mcp, and e-rechnung-inbox pinned by git SHA. Dev: pytest, ruff, reportlab
  (for generating test PDFs).
- `.gitignore` (`.env`, `.venv/`, `data/`, `.claude/settings.local.json`, caches), `.env.example`.
- `src/intake/config.py`: settings, API key as `SecretStr`.
- GitHub Actions: lint + tests.
- Check: `pip install -e ".[dev]"` works in a fresh venv, CI file is valid, empty test passes.

## M1: Model and structured extraction
- `intake/models.py`: `InvoiceData`, `LineItem`, `FieldValue(value, confidence, source)`.
- `intake/detect.py`: XML vs. ZUGFeRD PDF vs. plain PDF (uses `factur-x`, which
  e-rechnung-inbox already depends on, for the embedded XML).
- `intake/xml_extract.py`: XPath mapping for UBL and CII.
- Fixtures: a few hand-written UBL and CII invoices and one ZUGFeRD PDF.
- Tests: every field from every fixture; credit note detected; XXE payload is not resolved.

## M2: Business rules
- `intake/rules/`: R1-R6, one function each, pure, returning `RuleResult`.
- IBAN country lengths and EU VAT ID patterns as data tables.
- Tests: pass and fail cases for each rule, rounding edge cases (0.005), known valid and
  invalid IBANs, leap-day dates.

## M3: Structured validation adapter (R7)
- `intake/structured.py`: wraps `erechnung_inbox.check_file`, maps status to R7.
- Fake adapter for tests (recorded `CheckResult` JSON); real one behind the `docker` marker.
- CI job `kosit`: check out e-rechnung-inbox at the pinned commit, build its validator image,
  run `pytest -m docker`.
- Tests: valid, invalid with findings, not_checked, Docker missing, all map to the right
  rule result and reason text.

## M4: LLM extraction with cache
- `intake/llm/`: prompt (versioned), `LlmInvoiceExtraction` schema, Anthropic client,
  disk cache, offline mode, grounding check, confidence combination.
- Tests (no network): cache hit, cache miss in offline mode raises, client is not created
  offline, schema mismatch leads to review, grounding lowers confidence.
- One `live_llm` test, skipped unless a key is set and you run it on purpose.

## M5: Pipeline, storage, decision
- `intake/db.py` (SQLite + SQL migrations), `intake/pipeline.py`,
  `intake/decision.py`, `intake/service.py` (used by REST, web and MCP).
- Duplicate check (R6) against the database. Store both keys per invoice (normalised VAT ID
  and normalised seller name); R6 fails if either key plus the invoice number matches. This
  replaces M2's single key (VAT ID, else name).
- Tests: same seller, first invoice with VAT ID, duplicate without VAT ID, flagged. Also the
  reverse order, and rejected invoices are ignored.
- Tests: end-to-end on fixtures: XML auto-approved, broken sum goes to review with the
  correct reason, duplicate goes to review, events are written.

## M6: REST API and review web page
- `intake/api.py`, `intake/web/` (Jinja2 templates).
- Tests with FastAPI `TestClient`: upload, list, correct fields, approve (note required
  when a rule fails), reject.
- Manual check: run the app, upload fixtures, look at the review page in the browser.

## M7: Mock ERP and export
- `mock_erp/app.py` with idempotency and flaky mode; `intake/erp_client.py` with retries.
- Tests: export after approval, retry after failure, no duplicate in the ERP.

## M8: MCP server
- `intake/mcp_server.py` with `list_invoices`, `get_invoice`, `approve_invoice`.
- Tests: call the tools in-process; approve outside `needs_review` is refused; event
  logged as `mcp:<client>`.
- README snippet: how to add it to Claude Desktop / Claude Code.

## M9: Eval
- `eval/generate.py`: synthetic dataset (fixed seed) with ground truth and planted problems,
  dev/test split.
- **Live LLM step (asks you first):** fill the LLM cache once for the dataset. This is the
  only step that uses your API key and costs money. I will tell you the number of calls
  and estimated cost before running it. **Budget: 10 USD in total** for filling the cache
  and the Sonnet vs. Haiku comparison. You put the key into `.env` yourself.
- `eval/run.py`, `eval/metrics.py` (bootstrap CIs, tested on known small examples).
- Compare `claude-sonnet-5-5` and `claude-haiku-5-5` on `dev` (cost estimate first); pick the
  cheaper one if accuracy is similar.
- Tune the threshold on `dev`, report on `test`.
- CI job: eval on the committed cache, fails if escaped errors > 0.

## M10: Polish
- README: what it does, architecture diagram, quick start, eval results with CIs,
  limitations (honest), link to e-rechnung-inbox.

## Ground rules I follow
- I never open, print or `cat` `.env`.
- I ask before `git commit`, `git push`, `rm`, `sudo`, and anything outside this folder.
- No live LLM calls in tests or CI; live calls only in M9 after you say yes.
