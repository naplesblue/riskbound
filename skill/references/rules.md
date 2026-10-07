# 规则与参数

## 公式（t 收盘计算，t+1 开盘执行）

| 量 | 定义 |
|---|---|
| σ̂ | 最近 20 个日对数收益的标准差（ddof = 1）× √252 |
| σ_tgt | σ̂ 自锚点（默认 2019-06-01）起的扩张中位数，有效观测 ≥ 250 才有定义 |
| SMA200 | 最近 200 个收盘均值；无定义视为线上 |
| w_V | `min(1, σ_tgt / σ̂)`；无定义或 σ̂ = 0 时为 1 |
| w_vol | `vol_floor` 为 none 时等于 w_V，否则 `max(vol_floor, w_V)`；`vol` 关闭时恒为 1 |
| w | `w_vol × (收盘 > SMA200 ? 1 : below_sma)`；收盘等于 SMA200 时取 below_sma |
| 调仓门槛 | \|Δw\| ≥ 0.10 |

## preset

| preset | vol | vol_floor | below_sma | 对应检验 |
|---|---|---|---|---|
| `v` | 是 | none | 1.0 | B5 V |
| `f` | 否 | none | 0.5 | B5 F |
| `vf` | 是 | none | 0.5 | B5 VF |
| `vf75`（默认） | 是 | 0.75 | 0.75 | 未检验（描述性对比） |

## 参数边界

- `--vol-floor`：[0, 1] 或 `none`；`--below-sma`：(0, 1]；`--anchor`：日期。
- 冻结边界：`sig_n = 20`、`med_min = 250`、`sma_n = 200`、`anchor = 2019-06-01`、调仓门槛 0.10。任一偏离 → `untested_parameters`，偏离项列在 `deviations`。
- 适用性按 (vol, vol_floor, below_sma) 的**值**判定：`vol_floor = 0` 与 `none` 不同；覆盖后恰好等于某个经检验三元组时，按该三元组判定。
