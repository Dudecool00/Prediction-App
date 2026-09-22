# Manual passing-yards comparison

**Historical research demonstration — manually entered hypothetical prices.**

Josh Allen · 2024_01_ARI_BUF · xgb_schedule

Projection: **212.08 yards**. Nominal 90% interval: **69.89 to 354.27 yards**. Calibration sample: 230 QB-games.

Manual line: **225.5 yards**. Estimated push probability: **0.00%**.

| Side | Price | Model win, no push | Break-even, no push | Edge (pp) | Fair American | Estimated EV |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Over | -110 | 41.77% | 52.38% | -10.61 | +139.4 | -20.25% |
| Under | -110 | 58.23% | 52.38% | +5.84 | -139.4 | +11.16% |

Probability edge is in percentage points; EV is expected profit per dollar staked. Win and break-even probabilities in the table exclude pushes. Full unconditional win/loss/push probabilities and unrounded calculations are in the JSON report.

## Coverage evidence

- Overall: nominal 90%, observed 89.68%, n=1327.
- 5+ prior games: nominal 90%, observed 90.42%, n=1159.

## Timestamps and model

- Conceptual prediction time: 2024-09-08T16:00:00+00:00.
- Model training cutoff: 2023-12-01T00:15:00+00:00.
- Calibration cutoff: 2024-09-05T23:20:00+00:00.
- Latest calibration result available: 2024-01-09T01:20:00+00:00.
- Source snapshot retrieved: 2026-09-15T03:08:35.222604+00:00.
- Research generated: 2026-09-22T17:53:06.051700+00:00.
- Manual comparison generated: 2026-09-22T17:53:58.649421+00:00.
- Model source SHA-256: `2f76737eda5646b2c4c4c8ce393f48cdb4f9c0975b720e3a44c381f4a5f7c7f4`.
- Probability method: `rounded_signed_residuals_v1`.

These historical prediction times do not mean forecasts were actually issued then. Snapshot retrieval time is not proof that later source revisions were known pregame.

## Limits

- Historical demonstration with manually entered hypothetical prices; not a live quote or betting backtest.
- Probabilities condition on recorded participation; starter labels, injuries, and weather remain unresolved.
- Outcome probabilities round point-plus-residual samples to whole yards; push estimates are unvalidated and can be zero.
- Interval coverage is empirical, not a guarantee for this player; small-history groups can under-cover.
- EV assumes this market has action, standard win payouts, and full refunds on pushes; other void rules are not modeled.
- Optional game spread/total are recorded notes, not model inputs. Prices and lines never alter the forecast.
- Model estimates may be wrong and may lose money; no recommended stake or profitability claim.
