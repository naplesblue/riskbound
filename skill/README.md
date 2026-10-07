# riskbound skill for Claude Code

把本目录安装为 Claude Code 的个人 skill：

```bash
mkdir -p ~/.claude/skills/riskbound
cp -R skill/SKILL.md skill/references ~/.claude/skills/riskbound/
```

前提：已按仓库 README 安装好 riskbound，并在仓库目录内启动 Claude Code（skill 通过 `uv run riskbound ...` 调用 CLI）。

安装后，问「NVDA 现在风控仓位建议持多少」「200 日线下该降到多少」之类的问题会触发它；越界问题（能不能买、目标价等）会得到「超出证据范围」加证据块的固定回复。

更新：重新执行上面的复制命令即可。卸载：删除 `~/.claude/skills/riskbound/`。
