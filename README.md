# riskbound

**riskbound 是一个带证据边界的仓位规则工具，附一份关于技术规则如何诚实检验的报告。**

> **不预测方向、不承诺收益；降低回撤的代价是牺牲收益，长牛行情里大概率跑输买入持有。**
>
> 它只告诉你：一条事先写死的规则，在最新一根已收盘的日线上，给出「计划满仓的百分之多少」；以及这个数字背后有哪些证据、哪些没有证据。它不是择时工具，不构成投资建议。

默认配置（`vf75`）的 0.75 参数**未经检验**，是一个描述性对比里挑出的折中；经过预登记检验的只有两件事：波动率目标（V）与 200 日线半仓（F）**各自**降低了最大回撤。收益改善没有得到证明。详见 [证据](docs/evidence.md)。

[English](README.en.md)

## 它做什么

| 部分 | 内容 |
|---|---|
| 仓位规则 | 波动率目标 V：`w = min(1, σ_tgt / σ̂)`；200 日线过滤 F：收盘 > SMA200 → 1，否则 0.5（SMA 无定义视为线上）；两者可组合，线下系数可调。逐位复刻冻结的回测代码。 |
| 证据边界块 | 每次输出都附 `evidence`：历史检验结论逐条列出，并标明它是否适用于**你当前的配置**与标的。 |
| 前瞻记录（v1） | `track/`：在 25 只回测宇宙上逐日追加记录，只追加、可审计、可核验；只有 `forward` 状态的行计入前瞻证据。 |
| 检验子包 | `riskbound.harness`：用同一套对照与统计（等暴露对照、随机时点洗牌、两期同向、Holm）检验你自己的规则。 |
| 负面结果 | 一组常见的 K 线形态与入场规则在同样的检验下没有稳定增量，见 [负面结果](docs/negative-results.md)。 |

## 证据一览

| 结论 | 状态 | 来源 |
|---|---|---|
| V 相对等暴露买入持有，最大回撤更小 | 通过（两期 +2.09 / +5.92pp，Holm p = 0.002） | B5 结果 §1 |
| F 相对随机时点半仓，最大回撤更小 | 通过（两期 +5.72 / +3.27pp，Holm p = 0.002） | B5 结果 §1 |
| V 的 Sharpe、F 的 Calmar、VF 的 Sharpe 改善 | 未确立 | B5 结果 §1 |
| VF 组合降低回撤 | 从未检验 | — |
| 默认 vf75 的任何结论 | **未检验**（只有描述性对比） | 变体对比 §1 |

证据范围：25 只美股科技大盘股，2016-09 ~ 2025-12 两期，日线收盘计算、下一开盘执行。

## 安装

```bash
git clone https://github.com/naplesblue/riskbound riskbound && cd riskbound
uv sync
uv run riskbound AAPL
```

也可以 `uv tool install .` 或 `pip install .` 后直接用 `riskbound`。需要 Python ≥ 3.11；行情来自 yfinance，缓存在 `~/.cache/riskbound/bars`（`--cache-dir` 或环境变量 `RISKBOUND_CACHE_DIR` 可改）。

## 用法

```bash
riskbound AAPL NVDA                      # 表格 + EVIDENCE 段
riskbound AAPL --format json             # 机器可读，含 evidence 块
riskbound AAPL --preset v                # 经检验的形态之一：只用波动率目标
riskbound AAPL --preset vf75 --vol-floor none --below-sma 0.5   # 覆盖参数（按值判定适用性）
riskbound evidence                       # 不取数，只打印证据块
riskbound AAPL --offline --cache-dir DIR # 只读缓存，不联网
```

| preset | vol | vol_floor | below_sma | 历史检验 | `config_status` |
|---|---|---|---|---|---|
| `v` | 是 | 无 | 1.0 | V 的回撤格通过 | `tested_drawdown_only` |
| `f` | 否 | — | 0.5 | F 的回撤格通过 | `tested_drawdown_only` |
| `vf` | 是 | 无 | 0.5 | 只检验过 Sharpe，未通过；无回撤格 | `components_tested_separately` |
| `vf75`（默认） | 是 | 0.75 | 0.75 | 未检验 | `untested_parameters` |

任何覆盖使三元组不再等于 `v` / `f` / `vf`，或改动锚点 / 指标长度，都会得到 `untested_parameters`，并在 `deviations` 中列出偏离项。

退出码：`0` 全部成功；`0` 且 stderr 警告：部分标的失败；`1` 全部失败；`2` 参数错误。

前瞻记录（v1）：`riskbound track append | verify | observe`，见 [track/README.md](track/README.md)。

## 前提与提醒（摘要）

- 只在美股科技大盘股、日线上检验过；换行业、小盘、周期不保证成立。
- 全部是历史回测，没有实盘记录；前瞻记录从仓库公开起才开始累积。
- 降低的是回撤，代价是收益；长牛里大概率跑输满仓。
- 默认参数 0.75 未经检验。
- 统计关联，不构成投资建议；使用者自担后果。

完整清单见 [前提与提醒](docs/caveats.md)。

## 文档

- [证据](docs/evidence.md)：检验了什么、结果如何、数字来源
- [负面结果](docs/negative-results.md)：没有通过检验的规则
- [方法](docs/methodology.md)：预登记、对照、统计，以及如何用 harness 检验自己的规则
- [前提与提醒](docs/caveats.md)
- [Claude Code skill](skill/README.md)

## 许可

MIT，见 [LICENSE](LICENSE)。
