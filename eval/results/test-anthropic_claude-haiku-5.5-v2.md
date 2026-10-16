# Eval: test split, anthropic/claude-haiku-5.5

75 invoices, threshold 0.9. Values with bootstrap 95% CI (10,000 resamples over invoices). Synthetic data; small n, so the intervals are wide.

## Decisions

| Metric | All | Structured (XML, ZUGFeRD) | Plain PDF (LLM) |
|---|---|---|---|
| Auto-approve rate | 70.7% [60.0%, 81.3%] | 72.5% [57.5%, 85.0%] | 68.6% [51.4%, 82.9%] |
| Escaped errors per invoice | 0.0% [0.0%, 0.0%], ≤ 3.9% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 7.2% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 8.2% (one-sided 95%) |
| Escaped errors per auto-approved | 0.0% [0.0%, 0.0%], ≤ 5.5% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 9.8% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 11.7% (one-sided 95%) |
| Planted problems auto-approved | 0.0% [0.0%, 0.0%], ≤ 14.6% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 23.8% (one-sided 95%) | 0.0% [0.0%, 0.0%], ≤ 31.2% (one-sided 95%) |

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
| `line_items` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `allowance_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `charge_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `net_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `vat_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |
| `gross_total` | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] |

## Is the confidence honest? (plain PDFs)

- fields at or above the threshold: 478, accuracy 100.0%
- fields below the threshold: 12, accuracy 100.0%

## Threshold sweep

| Threshold | Auto-approved | Escaped |
|---|---|---|
| 0.5 | 56 / 75 | 0 |
| 0.6 | 56 / 75 | 0 |
| 0.7 | 56 / 75 | 0 |
| 0.75 | 56 / 75 | 0 |
| 0.8 | 56 / 75 | 0 |
| 0.85 | 54 / 75 | 0 |
| 0.9 | 53 / 75 | 0 |
| 0.925 | 34 / 75 | 0 |
| 0.95 | 30 / 75 | 0 |
| 0.975 | 29 / 75 | 0 |
| 0.99 | 29 / 75 | 0 |

## Escaped errors

None at this threshold.
