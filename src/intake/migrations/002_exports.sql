-- One row per export attempt to the ERP (each row may contain several HTTP retries).

CREATE TABLE exports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    ok         INTEGER NOT NULL,           -- 1 = the ERP has the invoice
    attempts   INTEGER NOT NULL,           -- HTTP requests made
    erp_id     TEXT,
    error      TEXT
);
CREATE INDEX exports_invoice ON exports (invoice_id, id);
