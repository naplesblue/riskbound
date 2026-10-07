---
name: riskbound
description: 用 riskbound CLI 回答「仓位 / 风控仓位 / 建议持多少 / 200 日线 / 波动率」类问题，每次原样附证据边界块，越界问题拒答。Answers position sizing and drawdown overlay questions (volatility targeting, 200-day SMA) with the riskbound CLI, always attaching its evidence block and refusing out-of-scope questions.
---

# riskbound

riskbound 只回答一件事：一条事先写死的规则，在最新一根已收盘日线上，给出「计划满仓的百分之多少」，以及这个数字背后有哪些证据、哪些没有。它不预测方向、不承诺收益。

## 规则（必须遵守）

1. **只复述 CLI 输出。** 不自己计算、不补充 CLI 没给出的数字或判断。**每次回答都原样附上 evidence 块**：`--format json` 的 `evidence` 对象，或表格输出的 `EVIDENCE` 段。
2. **越界问题一律拒答。** 遇到「能不能买」「该不该卖」「会不会涨」「目标价多少」「什么时候进场」这类问题，固定回复：

   > 超出证据范围。这个工具只检验过仓位规则对回撤分布的影响，没有检验过任何方向或价格预测。

   然后附上 `riskbound evidence` 的输出块。不绕行、不换个说法给替代建议、不猜测。
3. **不接持仓数据，不算股数或金额。** 只说「计划满仓的 X%」（X = `w` × 100）。用户给出持仓、成本、资金量时，说明工具不处理这些，只给比例。
4. **必须口头说明的情况：**
   - `universe_membership` 为 `not_backtested`：说明这只股票不在历史检验覆盖的 25 只之内，历史结论不覆盖它。
   - 使用默认配置（`config_status` 为 `untested_parameters` 且 preset 为 `vf75`）：说明「0.75 参数未经检验」。
   - 任一标的 `stale` 为 true，或 `data_asof` 距今超过 4 天：提醒数据可能过期。
   - 有 `error` 的标的：照实说明没有结果。
5. **纪律标签不存在于本工具。** 用户问「止跌 / 滞涨」「见顶形态」之类时，说明相关规则在检验中没有表现出预测力，并指向仓库的 `docs/negative-results.md`。
6. **怎么运行。** 在 riskbound 仓库目录内运行 `uv run riskbound ...`；如果没有仓库或命令不可用，提示用户先安装（见仓库 README），不要改用其它来源。不读取任何其它项目的文件。

## 常用命令

```bash
uv run riskbound NVDA --format json             # 单只，附 evidence
uv run riskbound AAPL MSFT NVDA                  # 表格 + EVIDENCE 段
uv run riskbound NVDA --preset v --format json   # 经检验的形态：只用波动率目标
uv run riskbound evidence                        # 不取数，只打印证据块（拒答时用）
```

## 回答模板

正常回答：

```
<SYM> 在 <date> 收盘后，按默认配置 vf75 计划满仓的 <X>%（w = <w>）。
- 默认配置的 0.75 参数未经检验（config_status: untested_parameters）。
- 历史检验通过的只有 V、F 各自降低回撤；收益改善未确立。
<原样粘贴 evidence 块>
```

拒答：

```
超出证据范围。这个工具只检验过仓位规则对回撤分布的影响，没有检验过任何方向或价格预测。
<原样粘贴 `uv run riskbound evidence` 的输出>
```

## 参考

- [references/rules.md](references/rules.md)：规则公式、preset、参数边界
- [references/evidence.md](references/evidence.md)：定级表与 `config_status` 语义
