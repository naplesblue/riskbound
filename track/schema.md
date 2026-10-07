# 前瞻记录格式（track schema）

本目录是 riskbound 默认规则在 25 只历史检验宇宙上的**前瞻记录**：每个交易日收盘后追加一次，只追加、可审计、可核验。它**不是**「不可篡改」的；能检出什么、不能检出什么见下文「完整性检查」。

## 文件

| 文件 | 内容 |
|---|---|
| `daily.jsonl` | 每行一个 (asof, symbol) 记录，UTF-8，`\n` 换行，键序固定 |
| `observations.jsonl` | GitHub PushEvent 的转录（推送到达公开远端的时间），独立哈希链 |

不保存 OHLCV 行情文件或缓存；每行只带当日一个复权收盘价（`close`）。

## daily.jsonl 字段（键序即下表顺序）

| field | type | meaning |
|---|---|---|
| `asof` | `YYYY-MM-DD` | consensus date of this run: the latest settled close across the 25 symbols |
| `symbol` | string | ticker (one of the 25 backtest-universe symbols) |
| `close` | float | adjusted close on `asof` (as returned by the data source on `generated_at`) |
| `w` | float | target weight of the default rule (vf75) for the next open |
| `w_vol` | float | volatility component after the floor |
| `above_sma200` | bool | close > 200-day SMA (true while the SMA is undefined) |
| `sma200` | float \| null | 200-day SMA |
| `sigma` | float \| null | 20-day realized volatility, annualized |
| `sigma_tgt` | float \| null | expanding median of `sigma` since the anchor |
| `vol_ratio` | float \| null | `sigma / sigma_tgt` |
| `params` | object | `preset, vol, vol_floor, below_sma, anchor, sig_n, med_min, sma_n` |
| `rule_version` | string | package version that produced the row |
| `bars_through` | `YYYY-MM-DD` | last date actually covered by the symbol's data; always equals `asof` |
| `runner` | `github-actions` \| `local` | informational only (from `GITHUB_ACTIONS`) |
| `generated_at` | ISO 8601 UTC | time the row was computed, seconds, `+00:00` |
| `prev_hash` | hex | `row_hash` of the previous line; 64 zeros on the first line |
| `row_hash` | hex | sha256 of the canonical form of this row without `row_hash` |

浮点数按 Python 默认 `repr` 写出（不四舍五入）；无定义写 `null`。

## observations.jsonl 字段

| field | type | meaning |
|---|---|---|
| `event_id` | string | GitHub event id (unique in the file) |
| `push_time` | ISO 8601 UTC | event `created_at`: when the push reached the public remote, recorded by GitHub |
| `head_sha` | 40 hex | `payload.head` |
| `commits` | list of 40 hex | `payload.commits[].sha` |
| `repo` | `OWNER/REPO` | repository observed |
| `observed_at` | ISO 8601 UTC | when `riskbound track observe` fetched the event |
| `prev_hash`, `row_hash` | hex | same chain rules as `daily.jsonl`, independent chain |

只保存上述字段，不保存事件中的提交者姓名、邮箱、提交消息或 URL。

## 规范化与哈希

```
canonical(row) = json.dumps(row_without_row_hash, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False)
row_hash = sha256(canonical(row)).hexdigest()
```

文件中的行用固定键序、`separators=(",", ":")`、`ensure_ascii=True` 写出；哈希只依赖规范化形式。

## 写入规则（`riskbound track append`）

1. 对 25 只逐一取数（总预算默认 600 秒；预算用尽则整次放弃、不写），剔除盘中未落定的末根，得到每只的 `bars_through`。
2. `asof = max(bars_through)`；asof 必须不晚于美东今天，且为周一至周五，否则整次拒绝。
3. `bars_through < asof` 的标的（含取数失败沿用旧缓存、响应缺最新交易日）本次不写；之后再运行时，只要共识 asof 不变，就补写缺的 (asof, symbol)。前一日缺失的 asof **不回填**。
4. 已存在的 (asof, symbol) 不重写。
5. 写入前先做文件内校验；断链则拒绝写入，需从 git 历史恢复。写入方式为读全文、追加、原子替换。
6. 正式运行不接受 `--asof`；`--dry-run [--asof DATE]` 只打印、不写。

## 完整性检查（`riskbound track verify`）

| 级别 | 能检出 | 不能检出 |
|---|---|---|
| 文件内（默认） | 内部行的修改、删除、重排；重复键；`bars_through ≠ asof` | **尾部整段截断**（截掉末尾若干行后，剩下的链仍然自洽） |
| `--prefix-of REF` | 相对参照版本（文件或 `REF:path`）的尾部截断与任何重写 | 参照版本之后新增内容本身的真伪 |
| `--public-ref REF` | 每行的公开状态（见 README 四态） | 观测缺失时无法事后补证 |

参照应取已公开的提交（如 `origin/main:track/daily.jsonl`）。

## deadline

`deadline(asof)` = asof 之后第一个周一至周五的 09:30 美东时间（`America/New_York`，自动处理夏令时）。**节假日不建模**：遇到休市日时，deadline 比真实的下一个开盘更早，判定偏保守（更容易判为 late）。
