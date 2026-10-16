"""python -m eval.compare [--split dev]: every result file of a split side by side.

Writes eval/results/<split>-comparison.md: one column per (model, prompt version), so the
effect of prompt changes stays visible next to the earlier runs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .cost import entry_cost

DECISIONS = {
    "auto_approve_rate": "Auto-approve rate",
    "escaped_per_invoice": "Escaped errors per invoice",
    "planted_auto_approved": "Planted problems auto-approved",
}


def _pct(est: dict[str, Any] | None) -> str:
    if not est or est["value"] is None:
        return "n/a"
    text = f"{est['value']:.1%} [{est['ci95'][0]:.0%}-{est['ci95'][1]:.0%}]"
    if est.get("zero_upper95") is not None:
        text += f" ≤{est['zero_upper95']:.1%}"
    return text


def run_cost(cache_dir: Path, model: str, prompt_version: str) -> tuple[float, int]:
    total, calls = 0.0, 0
    for f in cache_dir.glob("*.json"):
        entry = json.loads(f.read_text(encoding="utf-8"))
        if entry.get("model") == model and entry.get("prompt_version") == prompt_version:
            total += entry_cost(entry) or 0.0
            calls += 1
    return total, calls


def comparison(results: list[dict[str, Any]], cache_dir: Path) -> str:
    cols = [f"{r['model'].split('/')[-1]} · prompt {r['prompt_version']}" for r in results]
    head = "| | " + " | ".join(cols) + " |\n|---|" + "---|" * len(cols)
    lines = [f"# {results[0]['split']} split: all runs side by side", "", head]

    def row(label: str, cells: list[str]) -> None:
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    costs = [run_cost(cache_dir, r["model"], r["prompt_version"]) for r in results]
    row("LLM cost (calls)", [f"${c:.3f} ({n})" for c, n in costs])
    row("Threshold chosen on this split", [str(r["threshold"]) for r in results])
    for key, label in DECISIONS.items():
        row(f"{label}, all", [_pct(r["decision"][key]) for r in results])
        row(
            f"{label}, plain PDF", [_pct(r["decision_by_input"]["plain_pdf"][key]) for r in results]
        )
    for field in ("line_items",):
        row(f"`{field}` accuracy, plain PDF",
            [_pct(r["field_accuracy_by_input"]["plain_pdf"][field]) for r in results])  # fmt: skip
    others = []
    for r in results:
        plain = r["field_accuracy_by_input"]["plain_pdf"]
        worst = min((v["value"], k) for k, v in plain.items() if k != "line_items")
        others.append(f"{worst[0]:.1%} (lowest: `{worst[1]}`)")
    row("Other 13 fields, plain PDF", others)

    lines += ["", "## Threshold sweep (auto-approved / escaped, of all invoices)", "", head]
    for i, t in enumerate(s["threshold"] for s in results[0]["threshold_sweep"]):
        sweep = [r["threshold_sweep"][i] for r in results]
        row(str(t), [f"{s['auto_approved']} / {s['escaped']}" for s in sweep])

    lines += ["", "## Wrong values (any confidence)", ""]
    for col, r in zip(cols, results, strict=True):
        wrong = [
            f"`{x['id']}` {[f for f, ok in x['correct'].items() if not ok]}"
            for x in r["rows"]
            if not all(x["correct"].values())
        ]
        lines.append(f"- {col}: " + (", ".join(wrong) if wrong else "none"))
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--results", type=Path, default=Path("eval/results"))
    parser.add_argument("--cache", type=Path, default=Path("eval/llm_cache"))
    args = parser.parse_args()
    files = sorted(args.results.glob(f"{args.split}-*-v*.json"))
    results = [json.loads(f.read_text()) for f in files]
    results.sort(key=lambda r: (r["model"], r["prompt_version"]))
    text = comparison(results, args.cache)
    (args.results / f"{args.split}-comparison.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
