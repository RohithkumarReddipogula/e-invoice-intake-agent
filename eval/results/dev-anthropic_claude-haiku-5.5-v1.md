# Eval: dev split, anthropic/claude-haiku-5.5

75 invoices, threshold 0.9. Values with bootstrap 95% CI (10,000 resamples over invoices). Synthetic data; small n, so the intervals are wide.

## Decisions

| Metric | All | Structured (XML, ZUGFeRD) | Plain PDF (LLM) |
|---|---|---|---|
| Auto-approve rate | 68.0% [57.3%, 78.7%] | 82.5% [70.0%, 92.5%] | 51.4% [34.3%, 68.6%] |
| Escaped errors per invoice | 0.0% [0.0%, 0.0%], ≤ 3.9% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 7.2% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 8.2% (one-sided 95%) |
| Escaped errors per auto-approved | 0.0% [0.0%, 0.0%], ≤ 5.7% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 8.7% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 15.3% (one-sided 95%) |
| Planted problems auto-approved | 0.0% [0.0%, 0.0%], ≤ 14.6% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 34.8% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 22.1% (one-sided 95%) |

## Field accuracy

| Field | All | Structured | Plain PDF (LLM) |
|---|---|---|---|
| `invoice_number` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `issue_date` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `due_date` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `currency` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `seller_name` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `seller_vat_id` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `seller_iban` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `buyer_name` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `line_items` | 96.0% [90.7%, 100.0%] | 100.0% [100.0%, 100.0%] | 91.4% [80.0%, 100.0%] |
| `allowance_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `charge_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `net_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `vat_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `gross_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |

## Is the confidence honest? (plain PDFs)

- fields at or above the threshold: 471, accuracy 99.6%
- fields below the threshold: 19, accuracy 94.7%

## Threshold sweep

| Threshold | Auto-approved | Escaped |
|---|---|---|
| 0.5 | 56 / 75 | 2 |
| 0.6 | 56 / 75 | 2 |
| 0.7 | 56 / 75 | 2 |
| 0.75 | 56 / 75 | 2 |
| 0.8 | 56 / 75 | 2 |
| 0.85 | 55 / 75 | 2 |
| 0.9 | 51 / 75 | 0 |
| 0.925 | 35 / 75 | 0 |
| 0.95 | 33 / 75 | 0 |
| 0.975 | 33 / 75 | 0 |
| 0.99 | 33 / 75 | 0 |

## Escaped errors

None at this threshold.
