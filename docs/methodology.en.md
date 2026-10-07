# Methodology (summary)

- Frozen spec (sha256) before results; two independent periods, same sign required.
- Controls: exposure-matched buy-and-hold C† (bisection, residual ≤ 1e-9, eligibility ≤ 1e-6); segment shuffle P(F), 200 draws.
- Stock-block shift bootstrap, 2000 draws; untestable rules 1/3/4/5 first; Holm; verdict and Holm reported separately.
- Minimum effects: drawdown 1.0pp, Sharpe/Calmar 0.05.
- Reuse via `riskbound.harness.evaluate_overlay` / `evaluate_segments`; pass exactly two periods.
