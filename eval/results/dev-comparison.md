# dev split: all runs side by side

| | claude-haiku-5.5 · prompt v1 | claude-haiku-5.5 · prompt v2 | claude-sonnet-5.5 · prompt v1 | claude-sonnet-5.5 · prompt v2 |
|---|---|---|---|---|
| LLM cost (calls) | $0.028 (35) | $0.029 (35) | $0.399 (35) | $0.406 (35) |
| Threshold chosen on this split | 0.9 | 0.9 | 0.925 | 0.9 |
| Auto-approve rate, all | 68.0% [57%-79%] | 69.3% [59%-80%] | 60.0% [49%-71%] | 73.3% [63%-83%] |
| Auto-approve rate, plain PDF | 51.4% [34%-69%] | 54.3% [37%-71%] | 34.3% [20%-51%] | 62.9% [46%-80%] |
| Escaped errors per invoice, all | 0.0% [0%-0%] ≤3.9% | 0.0% [0%-0%] ≤3.9% | 0.0% [0%-0%] ≤3.9% | 0.0% [0%-0%] ≤3.9% |
| Escaped errors per invoice, plain PDF | 0.0% [0%-0%] ≤8.2% | 0.0% [0%-0%] ≤8.2% | 0.0% [0%-0%] ≤8.2% | 0.0% [0%-0%] ≤8.2% |
| Planted problems auto-approved, all | 0.0% [0%-0%] ≤14.6% | 0.0% [0%-0%] ≤14.6% | 0.0% [0%-0%] ≤14.6% | 0.0% [0%-0%] ≤14.6% |
| Planted problems auto-approved, plain PDF | 0.0% [0%-0%] ≤22.1% | 0.0% [0%-0%] ≤22.1% | 0.0% [0%-0%] ≤22.1% | 0.0% [0%-0%] ≤22.1% |
| `line_items` accuracy, plain PDF | 91.4% [80%-100%] | 100.0% [100%-100%] | 97.1% [91%-100%] | 100.0% [100%-100%] |
| Other 13 fields, plain PDF | 100.0% (lowest: `allowance_total`) | 100.0% (lowest: `allowance_total`) | 100.0% (lowest: `allowance_total`) | 100.0% (lowest: `allowance_total`) |

## Threshold sweep (auto-approved / escaped, of all invoices)

| | claude-haiku-5.5 · prompt v1 | claude-haiku-5.5 · prompt v2 | claude-sonnet-5.5 · prompt v1 | claude-sonnet-5.5 · prompt v2 |
|---|---|---|---|---|
| 0.5 | 56 / 2 | 56 / 0 | 56 / 1 | 56 / 0 |
| 0.6 | 56 / 2 | 56 / 0 | 56 / 1 | 56 / 0 |
| 0.7 | 56 / 2 | 56 / 0 | 56 / 1 | 56 / 0 |
| 0.75 | 56 / 2 | 56 / 0 | 56 / 1 | 56 / 0 |
| 0.8 | 56 / 2 | 56 / 0 | 56 / 1 | 56 / 0 |
| 0.85 | 55 / 2 | 56 / 0 | 55 / 1 | 55 / 0 |
| 0.9 | 51 / 0 | 52 / 0 | 55 / 1 | 55 / 0 |
| 0.925 | 35 / 0 | 35 / 0 | 45 / 0 | 46 / 0 |
| 0.95 | 33 / 0 | 33 / 0 | 39 / 0 | 38 / 0 |
| 0.975 | 33 / 0 | 33 / 0 | 33 / 0 | 33 / 0 |
| 0.99 | 33 / 0 | 33 / 0 | 33 / 0 | 33 / 0 |

## Wrong values (any confidence)

- claude-haiku-5.5 · prompt v1: `dev-030` ['line_items'], `dev-072` ['line_items'], `dev-075` ['line_items']
- claude-haiku-5.5 · prompt v2: none
- claude-sonnet-5.5 · prompt v1: `dev-030` ['line_items']
- claude-sonnet-5.5 · prompt v2: none
