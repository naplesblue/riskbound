# riskbound

**Position-sizing rules with explicit evidence boundaries, plus a report on testing technical rules honestly.**

> **No directional forecast, no return promise. Lower drawdown costs return; in long bull markets it will likely lag buy-and-hold.** Not investment advice.

The default config (`vf75`, 0.75 parameters) is **untested**. Pre-registered tests passed only for drawdown reduction of volatility targeting (V) and of the 200-day SMA half position (F), each on its own. See [evidence](docs/evidence.en.md).

```bash
git clone https://github.com/naplesblue/riskbound riskbound && cd riskbound && uv sync
uv run riskbound AAPL --format json   # every output carries an `evidence` block
uv run riskbound evidence             # the evidence block alone
```

| preset | tested | `config_status` |
|---|---|---|
| `v` | V drawdown cell passed | `tested_drawdown_only` |
| `f` | F drawdown cell passed | `tested_drawdown_only` |
| `vf` | Sharpe only, not passed | `components_tested_separately` |
| `vf75` (default) | untested | `untested_parameters` |

Docs: [evidence](docs/evidence.en.md) · [negative results](docs/negative-results.en.md) · [methodology](docs/methodology.en.md) · [caveats](docs/caveats.en.md) · [skill](skill/README.md). MIT.
