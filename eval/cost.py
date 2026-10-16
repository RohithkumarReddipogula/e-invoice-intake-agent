"""Cost estimate before live LLM calls, and actual spend from the cache.

python -m eval.cost [--split dev] [--model claude-sonnet-5-5 ...]

No API key needed: tokens are estimated from characters (conservatively 3 chars/token).
Actual spend is read from the `usage` stored in every cache entry, so the budget counts all
live calls ever made for this repository, across runs.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

from intake.llm.client import MAX_TOKENS
from intake.llm.extract import pdf_text
from intake.llm.prompt import SYSTEM_PROMPT, user_message
from intake.llm.schema import LlmInvoiceExtraction

# USD per million tokens (Anthropic first-party prices, prompts up to 100K tokens).
PRICES = {
    # Anthropic API (model IDs as in Anthropic's docs)
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    # OpenRouter (IDs and prices from https://openrouter.ai/api/v1/models, checked 2026-10-16)
    "anthropic/claude-sonnet-5.5": (2.00, 10.00),
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
}
BUDGET_USD = 10.0
CHARS_PER_TOKEN = 3.0  # conservative for German text and JSON (real ratio is usually higher)
REQUEST_OVERHEAD_TOKENS = 400  # message framing and structured-output instructions
THINKING_TOKENS = {"low": 0, "high": 3000}  # adaptive thinking may or may not think


@dataclass
class CallEstimate:
    input_tokens: int
    output_tokens_low: int
    output_tokens_high: int


def tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN)


def estimate_call(text: str) -> CallEstimate:
    schema = json.dumps(LlmInvoiceExtraction.model_json_schema())
    input_tokens = tokens(len(SYSTEM_PROMPT) + len(user_message(text)) + len(schema))
    # The answer repeats roughly every value once, wrapped in JSON with confidences.
    answer_tokens = 450 + tokens(len(text) // 2)
    return CallEstimate(
        input_tokens=input_tokens + REQUEST_OVERHEAD_TOKENS,
        output_tokens_low=answer_tokens + THINKING_TOKENS["low"],
        output_tokens_high=answer_tokens + THINKING_TOKENS["high"],
    )


def usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def worst_case_call(model: str, est: CallEstimate) -> float:
    """A call that runs into the max_tokens cap."""
    return usd(model, est.input_tokens, MAX_TOKENS)


def plain_pdf_texts(split_dir: Path) -> list[str]:
    manifest = json.loads((split_dir / "manifest.json").read_text())["invoices"]
    return [
        pdf_text((split_dir / m["file"]).read_bytes()) for m in manifest if m["kind"] == "plain_pdf"
    ]


def entry_cost(entry: dict) -> float | None:
    """What one cached live call cost: OpenRouter's reported cost if there is one, else the
    tokens times the price table. None if neither is known."""
    usage, model = entry.get("usage") or {}, entry.get("model")
    if usage.get("cost_usd") is not None:
        return float(usage["cost_usd"])
    if model in PRICES and usage.get("input_tokens") is not None:
        return usd(model, usage["input_tokens"], usage.get("output_tokens") or 0)
    return None


def spent(cache_dir: Path) -> tuple[float, int]:
    """USD spent so far over every cache entry, and the number of live calls.

    An entry with an unknown cost counts as a whole budget, so it stops further live calls
    instead of being silently ignored."""
    total, calls = 0.0, 0
    for entry_file in cache_dir.glob("*.json"):
        cost = entry_cost(json.loads(entry_file.read_text(encoding="utf-8")))
        total += BUDGET_USD if cost is None else cost
        calls += 1
    return total, calls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("eval/data"))
    parser.add_argument("--cache", type=Path, default=Path("eval/llm_cache"))
    parser.add_argument("--split", action="append", help="dev and/or test (default: both)")
    parser.add_argument("--model", action="append", help="default: all priced models")
    parser.add_argument("--provider", choices=["anthropic", "openrouter"])
    args = parser.parse_args()
    splits = args.split or ["dev", "test"]
    models = args.model or [
        m
        for m in PRICES
        if args.provider is None or (args.provider == "openrouter") == m.startswith("anthropic/")
    ]

    for split in splits:
        texts = plain_pdf_texts(args.data / split)
        estimates = [estimate_call(t) for t in texts]
        tin = sum(e.input_tokens for e in estimates)
        tlow = sum(e.output_tokens_low for e in estimates)
        thigh = sum(e.output_tokens_high for e in estimates)
        print(f"{split}: {len(texts)} plain PDFs -> {len(texts)} calls per model")
        print(f"  tokens per split: ~{tin:,} input, ~{tlow:,}-{thigh:,} output")
        for model in models:
            low, high = usd(model, tin, tlow), usd(model, tin, thigh)
            worst = sum(worst_case_call(model, e) for e in estimates)
            print(
                f"  {model:<18} ${low:.2f} - ${high:.2f}  (every call at max_tokens: ${worst:.2f})"
            )
    total, calls = spent(args.cache)
    print(f"spent so far: ${total:.2f} in {calls} cached live calls (budget ${BUDGET_USD:.2f})")


if __name__ == "__main__":
    main()
