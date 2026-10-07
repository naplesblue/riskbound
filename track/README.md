# 前瞻记录（forward log）

这里按交易日记录 riskbound 默认配置（vf75，**参数未经检验**）在 25 只历史检验宇宙上的目标仓位。目的只有一个：让规则在**回测之后**的表现可以被第三方核对，而不是事后挑选。

- 记录内容与格式：见 [`schema.md`](schema.md)。
- 本记录只描述规则当时给出的数字，**不构成投资建议，不预测方向**。

## 公开状态的四态（只有 `forward` 计入前瞻证据）

| state | 含义 |
|---|---|
| `unpublished` | 引入这一行的提交还不在公开分支上（本地已提交但未推送，也属于这一态） |
| `published_unverified` | 已在公开分支上，但没有任何 PushEvent 观测覆盖该提交；**永久不计入**，因为观测缺失无法事后补造 |
| `late` | 覆盖该提交的最早 PushEvent 时间晚于 deadline |
| `forward` | 覆盖该提交的最早 PushEvent 时间不晚于 deadline（asof 之后第一个工作日 09:30 美东） |

「覆盖」指：引入提交在事件的 `commits` 中，或等于 `head_sha`，或是 `head_sha` 的祖先。

## 可信度边界

- **PushEvent 由 GitHub 记录**，第三方可以在保留期内通过 GitHub 事件接口独立核对（事件 id 记在 `observations.jsonl`）。GitHub 事件流只保留有限天数、有条数上限和可见延迟，所以 `observe` 必须在推送后及时、重复地运行；错过的观测不能补，相应行会一直是 `published_unverified`。
- **`observations.jsonl` 是本项目对外部记录的转录**，带事件 id 以便核对，它本身不是证据来源。
- **提交时间（committer time）可以由提交者任意设置**，只作信息展示，不能单独证明「何时公开」。
- **`runner` 字段**（`github-actions` / `local`）只作信息。
- 哈希链能检出内部行的修改与删除，**不能检出尾部截断**；尾部截断与重写需用 `verify --prefix-of <已公开的版本>` 检查。本记录的定位是「只追加、可审计、可核验」，**不是「不可篡改」**。
- deadline 不考虑节假日，判定偏保守。
- 复权收盘价会随分红、拆股回溯调整；`close` 以生成当日数据源返回的值为准，日后重取可能不同。

## 运行方式

- 主调度：`.github/workflows/track.yml`（GitHub Actions）。**仓库推送到 GitHub 并经所有者确认启用前不会运行。**
- 备用：`scripts/track_local.sh`（本机 cron），只追加并本地提交，不自动推送。
- 手动：

```
uv run riskbound track append            # 追加（共识 asof）
uv run riskbound track append --dry-run  # 只看将写什么
uv run riskbound track verify            # 文件内校验 + 覆盖报告
uv run riskbound track verify --prefix-of origin/main:track/daily.jsonl
uv run riskbound track observe --github OWNER/REPO
uv run riskbound track verify --public-ref origin/main
```

`--prefix-of` 与 `--public-ref` 使用的是本地 remote-tracking ref，运行前先 `git fetch origin`，否则过期的 ref 会把已公开的行少报为 `unpublished`（不会多给 `forward`）。

本项目维护者另有私下的影子运行，自 2026-10-05 起；它不并入本记录。
