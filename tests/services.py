"""An IntakeService wired to a fake KoSIT validator, a fake LLM and a temp directory."""

from datetime import date

from intake import db
from intake.config import Settings
from intake.llm import LlmExtractor
from intake.pipeline import Pipeline
from intake.service import IntakeService

from .check_results import load_check_result
from .llm_fakes import FakeClient

TODAY = date(2026, 10, 15)
UBL_VAT_BLOCK = (
    b"<cac:PartyTaxScheme>\n        <cbc:CompanyID>DE123456789</cbc:CompanyID>\n"
    b"        <cac:TaxScheme>\n          <cbc:ID>VAT</cbc:ID>\n        </cac:TaxScheme>\n"
    b"      </cac:PartyTaxScheme>"
)


def make_service(
    tmp_path, *, check="valid", llm_output=None, offline=False, erp=None
) -> IntakeService:
    """`erp`: an ErpClient, or None for no export (approved invoices stay approved)."""
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "intake.db",
        files_dir=tmp_path / "files",
        llm_cache_dir=tmp_path / "llm_cache",
        llm_offline=offline,
    )
    llm = LlmExtractor(settings, client_factory=lambda s: FakeClient(llm_output))
    pipeline = Pipeline(
        settings,
        db.Database(settings.database_path),
        llm,
        validator=lambda path: load_check_result(check),
        today=lambda: TODAY,
    )
    return IntakeService(pipeline, erp)
