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

Copy `.env.example` to `.env` and set a key only if you want live LLM calls: either
`ANTHROPIC_API_KEY` (default provider), or `OPENROUTER_API_KEY` with
`INTAKE_LLM_PROVIDER=openrouter` and an OpenRouter model ID such as
`INTAKE_LLM_MODEL=anthropic/claude-sonnet-5.5`. Tests and CI never call the LLM API.

## MCP server

`python -m intake.mcp_server` runs an MCP server over stdio with three tools:

| Tool | What it does |
|---|---|
| `list_invoices` | invoices, newest first, optionally one status (e.g. `needs_review`) |
| `get_invoice` | fields with confidence, checks R1-R7, review reasons, history, ERP state |
| `approve_invoice` | approves an invoice held **only for low confidence** (needs a note); invoices with failed checks are refused and stay for the review page |

Every approval is logged as `mcp:<client name>`. Invoice values are untrusted input, and the
tool descriptions tell the calling model to treat them as data.

Claude Code (from the repository folder):

```bash
claude mcp add invoice-intake -- "$PWD/.venv/bin/python" -m intake.mcp_server
```

Claude Desktop (`claude_desktop_config.json`); use absolute paths, because the working
directory is not the repository:

```json
{
  "mcpServers": {
    "invoice-intake": {
      "command": "/path/to/e-invoice-intake-agent/.venv/bin/python",
      "args": ["-m", "intake.mcp_server"],
      "env": {
        "INTAKE_DATABASE_PATH": "/path/to/e-invoice-intake-agent/data/intake.db",
        "INTAKE_FILES_DIR": "/path/to/e-invoice-intake-agent/data/files"
      }
    }
  }
}
```
