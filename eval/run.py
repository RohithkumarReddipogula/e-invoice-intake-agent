"""Run the eval: every invoice of a split through the real pipeline, then the metrics.

python -m eval.run --split dev --model claude-haiku-5-5            # offline (cache only)
python -m eval.run --split dev --model claude-haiku-5-5 --live     # may call the API
python -m eval.run --split test --model ... --threshold 0.9        # threshold chosen on dev
python -m eval.run --fill-kosit                                    # needs Docker, once

Offline is the default: a cache miss is an error. With --live, a call is only made while
the spend so far plus one worst-case call stays within the budget (eval/cost.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from intake import db
from intake.config import Settings
from intake.llm import LlmExtractor
from intake.llm.client import default_client_factory
from intake.llm.extract import pdf_text
from intake.llm.prompt import PROMPT_VERSION
from intake.models import InvoiceData
from intake.pipeline import Pipeline
from intake.service import IntakeService
from intake.structured import StructuredValidator

from .cost import BUDGET_USD, PRICES, estimate_call, spent, worst_case_call
from .dataset import EVAL_TODAY
from .kosit_cache import KositCache, KositCacheMiss
from .metrics import (
    N_RESAMPLES,
    Row,
    all_fields_correct,
    by,
    choose_threshold,
    confidence_check,
    decision_metrics,
    field_accuracy,
    threshold_sweep,
)

THRESHOLDS = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.925, 0.95, 0.975, 0.99]
STRUCTURED = ("xml_ubl", "xml_cii", "zugferd")


class BudgetExceeded(Exception):
    pass


def load_manifest(split_dir: Path) -> list[dict[str, Any]]:
    return json.loads((split_dir / "manifest.json").read_text())["invoices"]


def run_split(
    split_dir: Path,
    settings: Settings,
    validator: StructuredValidator,
    *,
    live: bool = False,
    budget: float = BUDGET_USD,
    client_factory: Callable[[Settings], Any] = default_client_factory,
    progress: Callable[[str], None] = lambda s: None,
) -> list[Row]:
    rows = []
    with tempfile.TemporaryDirectory(prefix="intake-eval-") as tmp:
        run_settings = settings.model_copy(
            update={
                "database_path": Path(tmp) / "eval.db",
                "files_dir": Path(tmp) / "files",
                "llm_offline": not live,
                "erp_base_url": None,
            }
        )
        llm = LlmExtractor(run_settings, client_factory)
        pipeline = Pipeline(
            run_settings,
            db.Database(run_settings.database_path),
            llm,
            validator=validator,
            today=lambda: EVAL_TODAY,
        )
        service = IntakeService(pipeline, erp=None)

        for entry in load_manifest(split_dir):
            content = (split_dir / entry["file"]).read_bytes()
            if live and entry["kind"] == "plain_pdf":
                _check_budget(llm, content, run_settings, budget)
            record = service.ingest(content, entry["file"])
            truth_doc = json.loads((split_dir / f"{entry['id']}.truth.json").read_text())
            truth = InvoiceData.model_validate(truth_doc["truth"])
            rows.append(
                Row(
                    id=entry["id"],
                    kind=entry["kind"],
                    planted=entry["planted"],
                    status=record.status.value,
                    correct=all_fields_correct(truth, record.data),
                    confidence=record.confidence if record.data is not None else {},
                    rule_failed=bool(record.failed_rules),
                    blocking=bool(record.blocking),
                )
            )
            progress(f"{entry['id']} {entry['kind']:<9} {record.status.value}")
    return rows


def _check_budget(llm: LlmExtractor, content: bytes, settings: Settings, budget: float) -> None:
    text = pdf_text(content)
    if llm.cache.get(llm.key_for(text)) is not None:
        return  # cached: free
    if settings.llm_model not in PRICES:
        raise BudgetExceeded(f"No price known for {settings.llm_model!r}; add it to eval/cost.py.")
    so_far, _ = spent(settings.llm_cache_dir)
    worst = worst_case_call(settings.llm_model, estimate_call(text))
    if so_far + worst > budget:
        raise BudgetExceeded(
            f"Stopping before a live call: spent ${so_far:.2f}, a worst-case call costs "
            f"${worst:.2f}, budget ${budget:.2f}."
        )


def report(rows: list[Row], threshold: float, n_resamples: int = N_RESAMPLES) -> dict[str, Any]:
    kw = {"n_resamples": n_resamples}
    groups = by(rows, lambda r: "structured" if r.kind in STRUCTURED else "plain_pdf")
    plain = groups.get("plain_pdf", [])
    return {
        "n_invoices": len(rows),
        "threshold": threshold,
        "field_accuracy": {k: v.as_dict() for k, v in field_accuracy(rows, **kw).items()},
        "field_accuracy_by_input": {
            name: {k: v.as_dict() for k, v in field_accuracy(group, **kw).items()}
            for name, group in sorted(groups.items())
        },
        "decision": {k: v.as_dict() for k, v in decision_metrics(rows, threshold, **kw).items()},
        "decision_by_input": {
            name: {k: v.as_dict() for k, v in decision_metrics(group, threshold, **kw).items()}
            for name, group in sorted(groups.items())
        },
        "confidence_check_plain_pdf": confidence_check(plain, threshold),
        "threshold_sweep": threshold_sweep(rows, THRESHOLDS),
        "escaped": [
            {
                "id": r.id,
                "kind": r.kind,
                "planted": r.planted,
                "wrong": [n for n, ok in r.correct.items() if not ok],
            }
            for r in rows
            if r.escaped_at(threshold)
        ],
        "rows": [r.__dict__ for r in rows],
    }


def _pct(est: dict[str, Any]) -> str:
    if est["value"] is None:
        return "n/a (none)"
    low, high = est["ci95"]
    text = f"{est['value']:.1%} [{low:.1%}, {high:.1%}]"
    if est.get("zero_upper95") is not None:
        text += f", ≤ {est['zero_upper95']:.1%} (one-sided 95%)"
    return text


def markdown(result: dict[str, Any], split: str, model: str) -> str:
    r = result
    lines = [
        f"# Eval: {split} split, {model}",
        "",
        f"{r['n_invoices']} invoices, threshold {r['threshold']}. Values with bootstrap 95% CI "
        "(10,000 resamples over invoices). Synthetic data; small n, so the intervals are wide.",
        "",
        "## Decisions",
        "",
        "| Metric | All | Structured (XML, ZUGFeRD) | Plain PDF (LLM) |",
        "|---|---|---|---|",
    ]
    by_input = r["decision_by_input"]
    labels = {
        "auto_approve_rate": "Auto-approve rate",
        "escaped_per_invoice": "Escaped errors per invoice",
        "escaped_per_auto_approved": "Escaped errors per auto-approved",
        "planted_auto_approved": "Planted problems auto-approved",
    }
    for key, label in labels.items():
        cells = [_pct(r["decision"][key])]
        cells += [
            _pct(by_input[g][key]) if g in by_input else "-" for g in ("structured", "plain_pdf")
        ]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Field accuracy",
        "",
        "| Field | All | Structured | Plain PDF (LLM) |",
        "|---|---|---|---|",
    ]
    fa = r["field_accuracy_by_input"]
    for field, est in r["field_accuracy"].items():
        cells = [_pct(est)] + [
            _pct(fa[g][field]) if g in fa else "-" for g in ("structured", "plain_pdf")
        ]
        lines.append(f"| `{field}` | " + " | ".join(cells) + " |")
    cc = r["confidence_check_plain_pdf"]
    if cc["fields_above"] or cc["fields_below"]:

        def acc(v):
            return "n/a" if v is None else f"{v:.1%}"

        lines += [
            "",
            "## Is the confidence honest? (plain PDFs)",
            "",
            f"- fields at or above the threshold: {cc['fields_above']}, "
            f"accuracy {acc(cc['accuracy_above'])}",
            f"- fields below the threshold: {cc['fields_below']}, "
            f"accuracy {acc(cc['accuracy_below'])}",
        ]
    lines += [
        "",
        "## Threshold sweep",
        "",
        "| Threshold | Auto-approved | Escaped |",
        "|---|---|---|",
    ]
    for s in r["threshold_sweep"]:
        lines.append(f"| {s['threshold']} | {s['auto_approved']} / {s['n']} | {s['escaped']} |")
    lines += ["", "## Escaped errors", ""]
    if r["escaped"]:
        for e in r["escaped"]:
            lines.append(f"- `{e['id']}` ({e['kind']}): planted={e['planted']}, wrong={e['wrong']}")
    else:
        lines.append("None at this threshold.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data", type=Path, default=Path("eval/data"))
    parser.add_argument("--split", default="dev", choices=["dev", "test"])
    parser.add_argument("--provider", default="anthropic", choices=["anthropic", "openrouter"])
    parser.add_argument(
        "--model", default="claude-sonnet-5-5", help="OpenRouter: anthropic/claude-sonnet-5.5"
    )
    parser.add_argument("--live", action="store_true", help="allow LLM API calls (costs money)")
    parser.add_argument("--budget", type=float, default=BUDGET_USD)
    parser.add_argument("--threshold", type=float, help="default: chosen by a sweep (dev only)")
    parser.add_argument("--resamples", type=int, default=N_RESAMPLES)
    parser.add_argument("--fill-kosit", action="store_true", help="validate with Docker, then exit")
    parser.add_argument("--out", type=Path, default=Path("eval/results"))
    parser.add_argument("--max-escaped", type=int, help="exit 1 if more escaped errors (for CI)")
    args = parser.parse_args()

    kosit = KositCache()
    if args.fill_kosit:
        for split in ("dev", "test"):
            split_dir = args.data / split
            files = [
                split_dir / m["file"] for m in load_manifest(split_dir) if m["kind"] in STRUCTURED
            ]
            new, cached = kosit.fill(files)
            print(f"{split}: {new} validated with KoSIT, {cached} already cached")
        return

    if args.split == "test" and args.threshold is None:
        sys.exit("Use --threshold with the value chosen on dev; the test split is never tuned on.")

    settings = Settings(
        llm_provider=args.provider, llm_model=args.model, llm_cache_dir=Path("eval/llm_cache")
    )
    try:
        rows = run_split(
            args.data / args.split,
            settings,
            kosit,
            live=args.live,
            budget=args.budget,
            progress=lambda s: print(s, file=sys.stderr),
        )
    except (BudgetExceeded, KositCacheMiss) as exc:
        sys.exit(str(exc))

    threshold = args.threshold
    if threshold is None:
        floor = settings.confidence_threshold  # the default fixed before seeing data
        threshold = choose_threshold(threshold_sweep(rows, THRESHOLDS), floor)
    result = {
        "split": args.split,
        "provider": args.provider,
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        **report(rows, threshold, args.resamples),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    # One file per split, model and prompt version: earlier prompt versions are kept.
    stem = f"{args.split}-{args.model.replace('/', '_')}-{PROMPT_VERSION}"
    (args.out / f"{stem}.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    (args.out / f"{stem}.md").write_text(markdown(result, args.split, args.model))
    total, calls = spent(Path("eval/llm_cache"))
    print(markdown(result, args.split, args.model))
    print(f"LLM spend so far: ${total:.2f} in {calls} live calls (budget ${args.budget:.2f})")
    if args.max_escaped is not None and len(result["escaped"]) > args.max_escaped:
        sys.exit(f"{len(result['escaped'])} escaped errors > allowed {args.max_escaped}")


if __name__ == "__main__":
    main()
