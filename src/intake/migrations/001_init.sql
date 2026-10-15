-- Invoices, their fields, rule results, duplicate keys and the audit trail.

CREATE TABLE invoices (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,           -- ISO 8601, UTC
    updated_at    TEXT NOT NULL,
    filename      TEXT NOT NULL,           -- as uploaded; display only, never used as a path
    file_sha256   TEXT NOT NULL,           -- the file is stored under files_dir/<sha256>
    input_kind    TEXT NOT NULL,           -- xml | zugferd_pdf | plain_pdf
    status        TEXT NOT NULL,
    source        TEXT,                    -- xml | llm; NULL if extraction failed
    data_json     TEXT,                    -- InvoiceData as JSON; NULL if extraction failed
    blocking_json TEXT NOT NULL DEFAULT '[]',  -- problems that always need a person
    reasons_json  TEXT NOT NULL DEFAULT '[]'   -- review reasons from the last decision
);
CREATE INDEX invoices_status ON invoices (status, created_at);

CREATE TABLE fields (
    invoice_id   TEXT NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    confidence   REAL NOT NULL,
    note         TEXT,
    corrected_by TEXT,                     -- set when a person changed the value
    PRIMARY KEY (invoice_id, name)
);

CREATE TABLE rule_results (
    invoice_id TEXT NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    rule_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    status     TEXT NOT NULL,              -- pass | fail | skip
    reason     TEXT NOT NULL,
    PRIMARY KEY (invoice_id, rule_id)
);

-- R6: up to two keys per invoice (VAT ID and seller name), see SPEC section 4.
CREATE TABLE duplicate_keys (
    invoice_id     TEXT NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    seller_key     TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    PRIMARY KEY (invoice_id, seller_key)
);
CREATE INDEX duplicate_keys_lookup ON duplicate_keys (seller_key, invoice_number);

CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,              -- system | web:<name> | mcp:<client>
    old_status TEXT,
    new_status TEXT NOT NULL,
    note       TEXT
);
CREATE INDEX events_invoice ON events (invoice_id, id);
