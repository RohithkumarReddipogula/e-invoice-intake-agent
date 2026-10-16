# Eval: dev split, anthropic/claude-sonnet-5.5

75 invoices, threshold 0.925. Values with bootstrap 95% CI (10,000 resamples over invoices). Synthetic data; small n, so the intervals are wide.

## Decisions

| Metric | All | Structured (XML, ZUGFeRD) | Plain PDF (LLM) |
|---|---|---|---|
| Auto-approve rate | 60.0% [49.3%, 70.7%] | 82.5% [70.0%, 92.5%] | 34.3% [20.0%, 51.4%] |
| Escaped errors per invoice | 0.0% [0.0%, 0.0%], ≤ 3.9% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 7.2% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 8.2% (one-sided 95%) |
| Escaped errors per auto-approved | 0.0% [0.0%, 0.0%], ≤ 6.4% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 8.7% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 22.1% (one-sided 95%) |
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
| `line_items` | 98.7% [96.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 97.1% [91.4%, 100.0%] |
| `allowance_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `charge_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `net_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `vat_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `gross_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |

## Is the confidence honest? (plain PDFs)

- fields at or above the threshold: 450, accuracy 100.0%
- fields below the threshold: 40, accuracy 97.5%

## Threshold sweep

| Threshold | Auto-approved | Escaped |
|---|---|---|
| 0.5 | 56 / 75 | 1 |
| 0.6 | 56 / 75 | 1 |
| 0.7 | 56 / 75 | 1 |
| 0.75 | 56 / 75 | 1 |
| 0.8 | 56 / 75 | 1 |
| 0.85 | 55 / 75 | 1 |
| 0.9 | 55 / 75 | 1 |
| 0.925 | 45 / 75 | 0 |
| 0.95 | 39 / 75 | 0 |
| 0.975 | 33 / 75 | 0 |
| 0.99 | 33 / 75 | 0 |

## Escaped errors

None at this threshold.
