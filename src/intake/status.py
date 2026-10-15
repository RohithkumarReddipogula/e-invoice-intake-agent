"""Invoice lifecycle (SPEC section 7)."""

from enum import StrEnum


class InvoiceStatus(StrEnum):
    RECEIVED = "received"
    EXTRACTED = "extracted"
    AUTO_APPROVED = "auto_approved"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPORTED = "exported"
    EXPORT_FAILED = "export_failed"
