# 证据与 config_status

## 定级表（B5 结果 §1；两期 late / early；Holm α = 0.05）

| 格 | 比较 | 定级 | late | early | Holm p |
|---|---|---|---|---|---|
| V1 | V vs 等暴露买入持有 C†：Sharpe | 无法估计 | +0.044 | +0.063 | 0.002 |
| V2 | V vs C†：最大回撤（pp） | 方向一致，符合预测 | +2.09 | +5.92 | 0.002 |
| F1 | F vs 随机时点洗牌：Calmar | 无法估计 | +0.028 | -0.007 | 0.742 |
| F2 | F vs 洗牌：最大回撤（pp） | 方向一致，符合预测 | +5.72 | +3.27 | 0.002 |
| VF1 | VF vs C†(VF)：Sharpe | 无法估计 | +0.059 | -0.007 | 0.491 |
| — | VF：回撤 | 从未检验 | — | — | — |

默认 vf75 未检验：只有 15 只事后选出股票上的描述性对比。

## config_status

| 值 | 含义 | `applies_to_current_config` 为 true 的行 |
|---|---|---|
| `tested_drawdown_only` | 有效配置等于 V 或 F：回撤格通过，收益效率未确立 | V → V1、V2；F → F1、F2 |
| `components_tested_separately` | 有效配置等于 VF：只检验过 Sharpe（未通过），回撤从未检验 | VF1、「VF 回撤从未检验」 |
| `untested_parameters` | vf75、其它任何组合，或锚点 / 指标长度偏离 | 无 |

「not established / never tested」行为 true 时，表示这个否定结论适用于当前配置。`universe_membership` 只说明标的是否在 25 只检验宇宙内，不改变上表。
