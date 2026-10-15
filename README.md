# e-invoice-intake-agent

Agent that turns incoming German invoices into validated data, with human review for doubtful cases.

Intake agent for incoming invoices (XRechnung XML, ZUGFeRD PDF, plain PDF). It extracts the
fields, checks business rules, and auto-approves an invoice only when every field is
confident and every rule passes. Everything else goes to a human review page with the reason.

> Status: early. See [SPEC.md](SPEC.md) and [PLAN.md](PLAN.md).

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check src tests
.venv/bin/pytest
```

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` only if you want live LLM calls.
Tests and CI never call the LLM API.
