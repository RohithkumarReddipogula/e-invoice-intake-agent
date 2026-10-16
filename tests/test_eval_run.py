"""The eval runner end to end, with a perfect fake LLM, plus the budget guard and KoSIT cache."""

import json
from types import SimpleNamespace

import pytest

from eval.generate import write_split
from eval.kosit_cache import KositCache, KositCacheMiss, from_json, to_json
from eval.metrics import FIELD_NAMES
from eval.run import BudgetExceeded, LlmCacheMiss, markdown, report, run_split
from intake.config import Settings
from intake.llm.schema import LlmInvoiceExtraction

from .check_results import load_check_result


@pytest.fixture(scope="module")
def split_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("evaldata")
    write_split(out, "dev", seed=2026)
    return out / "dev"


def perfect_answers(split_dir) -> dict[str, LlmInvoiceExtraction]:
    """invoice number -> the correct LLM answer (confidence 0.95)."""
    answers = {}
    for truth_file in split_dir.glob("*.truth.json"):
        t = json.loads(truth_file.read_text())["truth"]

        def f(value):
            return {"value": value, "confidence": 0.95}

        lines = [
            {
                k: line[k]
                for k in ("description", "quantity", "unit_price", "net_amount", "vat_rate")
            }
            for line in t["line_items"]
        ]
        answers[t["invoice_number"]] = LlmInvoiceExtraction.model_validate(
            {
                **{k: f(t[k]) for k in ("invoice_number", "issue_date", "due_date", "currency",
                                        "seller_name", "seller_vat_id", "seller_iban",
                                        "buyer_name", "net_total", "vat_total", "gross_total")},
                "allowance_total": f(t["allowance_total"] if t["allowance_total"] != "0" else None),
                "charge_total": f(None),
                "line_items": f(lines),
            }
        )  # fmt: skip
    return answers


class PerfectClient:
    def __init__(self, answers):
        self.answers = answers
        self.calls = 0
        self.messages = SimpleNamespace(parse=self.parse)

    def parse(self, **kwargs):
        self.calls += 1
        text = kwargs["messages"][0]["content"]
        number = next(n for n in self.answers if n in text)
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=self.answers[number],
            usage=SimpleNamespace(input_tokens=2500, output_tokens=900),
            _request_id="req_test",
        )


def settings(tmp_path, model="claude-haiku-5-5"):
    return Settings(_env_file=None, llm_model=model, llm_cache_dir=tmp_path / "llm_cache")


def valid(path):
    return load_check_result("valid")


def test_perfect_extractor_gives_perfect_scores(split_dir, tmp_path):
    client = PerfectClient(perfect_answers(split_dir))
    rows = run_split(split_dir, settings(tmp_path), valid, live=True,
                     client_factory=lambda s: client)  # fmt: skip
    assert len(rows) == 75
    assert client.calls == 35  # one per plain PDF
    assert all(r.all_correct for r in rows), [r.id for r in rows if not r.all_correct]
    result = report(rows, threshold=0.9, n_resamples=200)
    assert result["escaped"] == []
    assert result["decision"]["planted_auto_approved"]["value"] == 0.0
    # Every clean invoice is auto-approved, every planted one held.
    for r in rows:
        assert (r.status == "auto_approved") == (r.planted is None), (r.id, r.status, r.planted)
    assert all(result["field_accuracy"][f]["value"] == 1.0 for f in FIELD_NAMES)
    md = markdown(result, "dev", "fake")
    assert "| Auto-approve rate |" in md and "None at this threshold." in md


def test_second_run_is_served_from_the_cache(split_dir, tmp_path):
    client = PerfectClient(perfect_answers(split_dir))
    run_split(split_dir, settings(tmp_path), valid, live=True, client_factory=lambda s: client)
    offline = run_split(split_dir, settings(tmp_path), valid, live=False,
                        client_factory=lambda s: pytest.fail("no client offline"))  # fmt: skip
    assert client.calls == 35
    assert all(r.all_correct for r in offline)


def test_budget_guard_stops_before_the_first_call(split_dir, tmp_path):
    client = PerfectClient(perfect_answers(split_dir))
    with pytest.raises(BudgetExceeded, match="Stopping before a live call"):
        run_split(split_dir, settings(tmp_path, "claude-sonnet-5-5"), valid, live=True,
                  budget=0.01, client_factory=lambda s: client)  # fmt: skip
    assert client.calls == 0


def test_offline_without_cache_holds_plain_pdfs(split_dir, tmp_path):
    rows = run_split(split_dir, settings(tmp_path), valid, live=False)
    plain = [r for r in rows if r.kind == "plain_pdf"]
    assert all(r.status == "needs_review" for r in plain)
    assert all(not r.auto_approved_at(0.0) for r in plain)


def test_kosit_cache_roundtrip_and_miss(tmp_path, split_dir):
    cache = KositCache(tmp_path / "kosit")
    xml = next(split_dir.glob("*.xml"))
    with pytest.raises(KositCacheMiss, match="fill-kosit"):
        cache(xml)
    result = load_check_result("invalid")
    cache.directory.mkdir()
    cache.path_for(xml).write_text(json.dumps(to_json(result)))
    again = cache(xml)
    assert again.status == result.status
    assert [f.rule_id for f in again.findings] == [f.rule_id for f in result.findings]
    assert from_json(to_json(result)).reason == result.reason


def test_require_cached_fails_on_a_cache_miss(split_dir, tmp_path):
    with pytest.raises(LlmCacheMiss, match="No cached LLM answer"):
        run_split(split_dir, settings(tmp_path), valid, live=False, require_cached=True)
