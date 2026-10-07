"""Documentation guards: banned words / private information, the skill's behaviour rules, evidence tables bound
to the code and to the fixed published figures, and CLI option spelling.

Each guard is a pure function returning a list of problems. The tests check that the real documents produce
no problems, and that targeted counterexamples (built in memory from the real text) do.
"""

from __future__ import annotations

import argparse
import base64
import re
from pathlib import Path

import pytest

from riskbound import cli, evidence

ROOT = Path(__file__).resolve().parent.parent


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ================================================================ 1. banned words and private information
# Identifying terms of the private upstream project are stored base64-encoded so that the guard list itself
# does not reproduce them in the public repository.
_IDENTIFYING = [base64.b64decode(b).decode() for b in ('WW91dHViZU5vdGVz', '6KeG6YeO', '546v55CD')]
BANNED = re.compile("|".join([r"Volumes", r"/Users/", r"@gmail", r"watchlist", r"DEEPSEEK", r"TELEGRAM", r"CLOUDFLARE",
                              r"FMP", r"SEC_USER_AGENT", r"会员", r"课程", r"博主", r"作者", r"signal\b", r"买点", r"信号",
                              r"推荐", r"建议买入", *map(re.escape, _IDENTIFYING)]), re.IGNORECASE)
BUY_POINT_ALLOWED = {"docs/negative-results.md"}      # the buy-point negative result only (see below)
EXCLUDED = {"docs/product-decisions.md"}             # internal decision log, moved out before publishing


def doc_files() -> list[str]:
    files = ["README.md", "README.en.md"]
    files += sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").glob("*.md"))
    files += sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "skill").rglob("*") if p.is_file())
    files += sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "track").glob("*.md"))
    return [f for f in files if f not in EXCLUDED and (ROOT / f).exists()]


def banned_hits(rel: str, text: str) -> list[str]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in BANNED.finditer(line):
            if m.group(0) == "买点" and rel in BUY_POINT_ALLOWED:
                continue
            hits.append(f"{rel}:{n}: {m.group(0)}")
    return hits


def buy_point_placement(text: str) -> list[str]:
    """In negative-results, 买点 may appear only in the opening sentence and in section 5."""
    if "## 5. 买点规则" not in text:
        return ["section '## 5. 买点规则' missing"]
    before, rest = text.split("## 5. 买点规则", 1)
    after = rest.split("\n## ", 1)[1] if "\n## " in rest else ""
    out = []
    if before.count("买点") > 1:
        out.append("买点 used more than once before section 5")
    if "买点" in after:
        out.append("买点 used after section 5")
    return out


def test_scan_covers_track_docs():
    files = doc_files()
    assert "track/README.md" in files and "track/schema.md" in files
    assert "docs/product-decisions.md" not in files and not any(f.startswith("docs/plans/") for f in files)


@pytest.mark.parametrize("rel", doc_files())
def test_no_banned_words(rel):
    assert banned_hits(rel, read(rel)) == []


def test_buy_point_only_in_its_section():
    assert buy_point_placement(read("docs/negative-results.md")) == []


@pytest.mark.parametrize("word", ["信号", "推荐", "建议买入", "作者", "课程", "会员", "博主", "买点",
                                  "watchlist", "signal", "/Users/someone", "/Volumes/x", "a@gmail.com",
                                  "DEEPSEEK_KEY", "TELEGRAM", "CLOUDFLARE", "FMP", "SEC_USER_AGENT", *_IDENTIFYING])
@pytest.mark.parametrize("rel", ["README.md", "track/README.md", "track/schema.md", "skill/SKILL.md"])
def test_counterexample_each_banned_word_fails(rel, word):
    assert banned_hits(rel, read(rel) + f"\n示例 {word} 示例\n")


def test_counterexample_buy_point_outside_allowed_places_fails():
    text = read("docs/negative-results.md")
    assert banned_hits("docs/evidence.md", "买点") and not banned_hits("docs/negative-results.md", "买点")
    assert buy_point_placement(text + "\n## 7. 其它\n买点\n")
    assert buy_point_placement(text.replace("## 1. 见顶 K 线形态", "## 1. 见顶 K 线形态与买点"))


# ================================================================ 2. the skill's six behaviour rules
SKILL_RULES = {
    1: ["只复述 CLI 输出", "不补充 CLI 没给出的数字或判断", "每次回答都原样附上 evidence 块", "`evidence` 对象",
        "`EVIDENCE` 段"],
    2: ["能不能买", "该不该卖", "会不会涨", "目标价", "什么时候进场", "超出证据范围",
        "只检验过仓位规则对回撤分布的影响", "没有检验过任何方向或价格预测", "`riskbound evidence` 的输出块",
        "不绕行", "不换个说法给替代建议", "不猜测"],
    3: ["不接持仓数据", "不算股数或金额", "计划满仓的 X%", "只给比例"],
    4: ["`universe_membership` 为 `not_backtested`", "历史结论不覆盖它", "0.75 参数未经检验",
        "`stale` 为 true", "`data_asof` 距今超过 4 天", "数据可能过期", "`error`"],
    5: ["纪律标签不存在于本工具", "止跌 / 滞涨", "没有表现出预测力", "docs/negative-results.md"],
    6: ["`uv run riskbound ...`", "提示用户先安装", "不要改用其它来源", "不读取任何其它项目的文件"],
}
REFUSAL = "超出证据范围。这个工具只检验过仓位规则对回撤分布的影响，没有检验过任何方向或价格预测。"


def skill_rules(text: str) -> dict[int, str]:
    sec = text.split("## 规则（必须遵守）", 1)
    if len(sec) != 2:
        return {}
    body = sec[1].split("\n## ", 1)[0]
    parts = re.split(r"(?m)^(\d+)\. ", body)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def skill_problems(text: str) -> list[str]:
    out = []
    if not text.startswith("---\nname: riskbound\n"):
        out.append("frontmatter name")
    front = text.split("---", 2)[1] if text.count("---") >= 2 else ""
    for w in ("仓位", "风控仓位", "建议持多少", "200 日线", "波动率", "position sizing", "drawdown overlay", "riskbound"):
        if w not in front:
            out.append(f"trigger word missing: {w}")
    rules = skill_rules(text)
    if sorted(rules) != [1, 2, 3, 4, 5, 6]:
        out.append(f"rules numbered {sorted(rules)}, expected 1..6")
    for k, phrases in SKILL_RULES.items():
        for ph in phrases:
            if ph not in rules.get(k, ""):
                out.append(f"rule {k}: missing {ph!r}")
    tmpl = text.split("拒答：", 1)
    if len(tmpl) != 2 or REFUSAL not in tmpl[1] or "uv run riskbound evidence" not in tmpl[1]:
        out.append("refusal template must contain the fixed sentence and the `riskbound evidence` output")
    return out


def test_skill_rules_hold():
    assert skill_problems(read("skill/SKILL.md")) == []


@pytest.mark.parametrize("k", [1, 2, 3, 4, 5, 6])
def test_counterexample_deleting_a_skill_rule_fails(k):
    text = read("skill/SKILL.md")
    body = skill_rules(text)[k]
    assert skill_problems(text.replace(f"{k}. {body}", "", 1))


@pytest.mark.parametrize("old,new", [
    ("**每次回答都原样附上 evidence 块**", "可以附上 evidence 块"),
    ("只检验过仓位规则对回撤分布的影响", "检验过方向"),
    ("不绕行、不换个说法给替代建议、不猜测。", "可以给出替代建议。"),
    ("不接持仓数据，不算股数或金额。", "可以根据持仓计算股数。"),
    ("`universe_membership` 为 `not_backtested`", "`universe_membership` 为空"),
    ("「0.75 参数未经检验」", "「默认参数」"),
    ("`data_asof` 距今超过 4 天", "`data_asof` 距今超过 30 天"),
    ("`stale` 为 true", "`stale` 为 false"),
    ("没有表现出预测力", "有一定预测力"),
    ("不读取任何其它项目的文件", "可以读取其它项目的文件"),
    ("description: 用 riskbound CLI 回答「仓位 / 风控仓位", "description: 用 riskbound CLI 回答「"),
    ("> 超出证据范围。这个工具只检验过仓位规则对回撤分布的影响，没有检验过任何方向或价格预测。\n\n   然后附上",
     "> 抱歉。\n\n   然后附上"),
])
def test_counterexample_weakening_a_skill_rule_fails(old, new):
    text = read("skill/SKILL.md")
    if old not in text:                       # mutations must hit real text; otherwise the test proves nothing
        pytest.fail(f"mutation target not found: {old!r}")
    assert skill_problems(text.replace(old, new, 1))


# ================================================================ 3. evidence tables and statements
def tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Markdown tables as (header, rows); cells stripped of ** and backticks; unicode minus -> '-'."""
    out, cur = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            cur.append(line)
            continue
        if len(cur) >= 2:
            rows = [[c.strip().replace("**", "").replace("`", "").replace("−", "-") for c in r.strip("|").split("|")]
                    for r in cur]
            out.append((rows[0], [r for r in rows[2:]]))
        cur = []
    return out


def find_table(text: str, first_cols: tuple[str, ...]):
    for header, rows in tables(text):
        if tuple(header[:len(first_cols)]) == first_cols:
            return header, rows
    return None, []


def section(text: str, heading_prefix: str) -> str:
    m = re.search(rf"(?m)^## {re.escape(heading_prefix)}.*$", text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"(?m)^## ", rest)
    return text[m.start():m.end() + (nxt.start() if nxt else len(rest))]


# The published grading table (taskbook M5 "证据层", checked against B5 results section 1).
GRADING = {
    "V1": ("波动率目标 V vs 等暴露买入持有 C†：Sharpe 差", "无法估计（late 未达 0.05 最小效应）",
           "+0.044", "+0.063", "+0.054 [+0.031, +0.077]", "5.0e-04", "0.002"),
    "V2": ("V vs C†：最大回撤差（pp）", "方向一致，符合预测", "+2.09", "+5.92", "+4.01 [+2.86, +5.19]", "5.0e-04", "0.002"),
    "F1": ("200 日线半仓 F vs 随机时点洗牌 P(F)：Calmar 差", "无法估计", "+0.028", "-0.007",
           "+0.010 [-0.044, +0.067]", "0.742", "0.742"),
    "F2": ("F vs P(F)：最大回撤差（pp）", "方向一致，符合预测", "+5.72", "+3.27", "+4.50 [+2.74, +6.34]", "5.0e-04",
           "0.002"),
    "VF1": ("组合 VF vs C†(VF)：Sharpe 差", "无法估计", "+0.059", "-0.007", "+0.026 [-0.018, +0.067]", "0.245", "0.491"),
}
# The published descriptive table (taskbook M5, checked against the variants comparison section 1, c0).
DESCRIPTIVE = [
    ("late", "A 买入持有", "25.3%", "44.2%", "28.8%", "0.93", "0.57", "1.000"),
    ("late", "V", "23.1%", "36.4%", "23.2%", "1.01", "0.63", "0.863"),
    ("late", "F50", "21.8%", "32.6%", "20.7%", "1.06", "0.67", "0.816"),
    ("late", "VF", "20.9%", "27.8%", "18.2%", "1.13", "0.75", "0.735"),
    ("late", "VF75", "22.7%", "34.1%", "21.8%", "1.04", "0.67", "0.832"),
    ("early", "A 买入持有", "46.2%", "31.7%", "26.3%", "1.58", "1.46", "1.000"),
    ("early", "V", "36.2%", "20.6%", "18.4%", "1.77", "1.76", "0.826"),
    ("early", "F50", "39.2%", "28.6%", "22.6%", "1.59", "1.37", "0.909"),
    ("early", "VF", "31.9%", "17.7%", "16.7%", "1.75", "1.80", "0.761"),
    ("early", "VF75", "37.6%", "24.1%", "20.3%", "1.68", "1.56", "0.850"),
]


def finding_numbers(f: dict) -> dict:
    """late / early / Holm p parsed from the FINDINGS detail text."""
    m = re.search(r"([+-]\d+\.\d+)(?:pp)? late / ([+-]\d+\.\d+)(?:pp)? early", f["detail"])
    h = re.search(r"Holm p = ([\d.]+)", f["detail"])
    return {"late": m.group(1), "early": m.group(2), "holm": h.group(1)}


FINDINGS_BY_CELL = {f["cell"]: f for f in evidence.FINDINGS if f["cell"]}
RESULT_WORDS = {"passed": "方向一致", "not established": "无法估计"}
PASSED_VERDICT = "方向一致，符合预测"            # passed means consistent AND as predicted (not "与预测相反")


def verdict_problem(verdict: str, result: str) -> str | None:
    """None if the verdict cell states the FINDINGS result exactly (whitespace-normalised)."""
    v = re.sub(r"\s+", "", verdict)
    if result == "passed":
        return None if v == PASSED_VERDICT else f"verdict {verdict!r} must be exactly {PASSED_VERDICT!r}"
    if not v.startswith("无法估计") or "方向一致" in v:
        return f"verdict {verdict!r} must start with 无法估计 for a not-established finding"
    return None


def _bind_row(where: str, cell: str, comparison: str, verdict: str, late: str, early: str, holm: str) -> list[str]:
    """One table row against FINDINGS: numbers per column, result, metric and control."""
    f = FINDINGS_BY_CELL[cell]
    nums = finding_numbers(f)
    out = []
    for col, got, want in (("late", late, nums["late"]), ("early", early, nums["early"]), ("Holm p", holm, nums["holm"])):
        if got != want:
            out.append(f"{where} {cell} {col}: {got!r} != FINDINGS {want!r}")
    vp = verdict_problem(verdict, f["result"])
    if vp:
        out.append(f"{where} {cell}: {vp} (FINDINGS result {f['result']!r})")
    metric = "Sharpe" if "Sharpe" in f["claim"] else "Calmar" if "Calmar" in f["claim"] else "最大回撤"
    if metric not in comparison:
        out.append(f"{where} {cell}: comparison {comparison!r} lacks metric {metric!r}")
    controls = ("C†",) if "exposure-matched" in f["claim"] else ("P(F)", "洗牌")
    if not any(c in comparison for c in controls) or ("C†" in comparison and controls != ("C†",)):
        out.append(f"{where} {cell}: comparison {comparison!r} does not name control {controls}")
    return out


def evidence_doc_problems(text: str) -> list[str]:
    out = []
    header, rows = find_table(text, ("格", "比较", "定级", "late", "early"))
    if header != ["格", "比较", "定级", "late", "early", "T [95%]", "p", "Holm p"]:
        return [f"grading table header {header!r}"]
    got = {r[0]: tuple(r[1:]) for r in rows}
    if list(got) != list(GRADING):
        out.append(f"grading rows {list(got)} != {list(GRADING)}")
    for cell, want in GRADING.items():
        row = got.get(cell)
        if row is None:
            continue
        if row != want:
            out.append(f"grading {cell}: {row!r} != published {want!r}")
        out += _bind_row("evidence.md", cell, row[0], row[1], row[2], row[3], row[6])
    grading_sec = section(text, "预登记检验")
    if "| V2 |" not in grading_sec:
        out.append("grading table is not inside the '预登记检验' section")
    if "**B5 家族没有 VF 的回撤格**" not in grading_sec:
        out.append("statement 'B5 家族没有 VF 的回撤格' missing from the grading section")
    summary = section(text, "一句话")
    if not re.search(r"(?m)^- \*\*未检验\*\*：默认配置 vf75", summary):
        out.append("summary must state that the default vf75 is untested")
    if not re.search(r"(?m)^- \*\*从未检验\*\*：.*VF", summary):
        out.append("summary must state that VF drawdown was never tested")
    desc_sec = section(text, "描述性对比")
    if "不是检验" not in desc_sec.splitlines()[0] if desc_sec else True:
        out.append("descriptive section heading must say 不是检验")
    if "**以下数字不做检验、不定级、不进 Holm。**" not in desc_sec:
        out.append("descriptive section must state 不做检验、不定级、不进 Holm")
    dh, drows = find_table(desc_sec, ("期", "方案", "CAGR"))
    if dh != ["期", "方案", "CAGR", "最大回撤", "年化波动", "Sharpe", "Calmar", "平均仓位"]:
        out.append("descriptive table missing from the descriptive section")
    elif [tuple(r) for r in drows] != DESCRIPTIVE:
        bad = [(i, tuple(r)) for i, r in enumerate(drows) if i >= len(DESCRIPTIVE) or tuple(r) != DESCRIPTIVE[i]]
        out.append(f"descriptive rows differ from the published table: {bad[:3]}")
    if find_table(grading_sec, ("期", "方案", "CAGR"))[0]:
        out.append("descriptive table must not appear in the grading section")
    return out


def readme_evidence_problems(text: str) -> list[str]:
    out = []
    header, rows = find_table(text, ("结论", "状态", "来源"))
    if not rows:
        return ["README evidence summary table missing"]
    want = {"V2": "V 相对等暴露买入持有，最大回撤更小", "F2": "F 相对随机时点半仓，最大回撤更小"}
    by = {r[0]: r for r in rows}
    for cell, claim in want.items():
        r = by.get(claim)
        if r is None:
            out.append(f"README row missing: {claim}")
            continue
        n = finding_numbers(FINDINGS_BY_CELL[cell])
        expect = f"通过（两期 {n['late']} / {n['early']}pp，Holm p = {n['holm']}）"
        if r[1] != expect or r[2] != "B5 结果 §1":
            out.append(f"README {cell}: {r[1:]!r} != {expect!r}")
    eff = by.get("V 的 Sharpe、F 的 Calmar、VF 的 Sharpe 改善")
    if eff is None:
        out.append("README row missing: V 的 Sharpe、F 的 Calmar、VF 的 Sharpe 改善")
    else:
        results = {c: FINDINGS_BY_CELL[c]["result"] for c in ("V1", "F1", "VF1")}
        if set(results.values()) != {"not established"}:
            out.append(f"README efficiency row assumes V1/F1/VF1 not established; FINDINGS says {results}")
        if (eff[1], eff[2]) != ("未确立", "B5 结果 §1"):
            out.append(f"README efficiency row: {eff[1:]!r} != ('未确立', 'B5 结果 §1')")
    if by.get("VF 组合降低回撤", ["", ""])[1] != "从未检验":
        out.append("README must say VF drawdown 从未检验")
    if "未检验" not in by.get("默认 vf75 的任何结论", ["", ""])[1]:
        out.append("README must say default vf75 未检验")
    ph, prow = find_table(text, ("preset", "vol", "vol_floor", "below_sma"))
    pv = {r[0]: r for r in prow}
    if "vf75（默认）" not in pv or pv["vf75（默认）"][4] != "未检验" or pv["vf75（默认）"][5] != "untested_parameters":
        out.append("README preset table: vf75 must be 未检验 / untested_parameters")
    if not re.search(r"默认配置（`?vf75`?）的 0\.75 参数\*\*未经检验\*\*", text):
        out.append("README must state that the default 0.75 parameters are untested")
    return out


def skill_evidence_problems(text: str) -> list[str]:
    out = []
    header, rows = find_table(text, ("格", "比较", "定级", "late", "early", "Holm p"))
    if not rows:
        return ["skill evidence table missing"]
    by = {r[0]: r for r in rows}
    for cell in GRADING:
        r = by.get(cell)
        if r is None:
            out.append(f"skill table missing {cell}")
            continue
        out += _bind_row("skill/references/evidence.md", cell, r[1], r[2], r[3], r[4], r[5])
    if by.get("—", [None, None, ""])[2] != "从未检验":
        out.append("skill table must list VF drawdown as 从未检验")
    if "默认 vf75 未检验" not in text:
        out.append("skill references must say 默认 vf75 未检验")
    return out


def evidence_en_problems(text: str) -> list[str]:
    out = []
    header, rows = find_table(text, ("cell", "comparison", "verdict", "late", "early", "Holm p"))
    by = {r[0]: r for r in rows}
    words = {"passed": "consistent", "not established": "inconclusive"}
    for cell, f in FINDINGS_BY_CELL.items():
        r = by.get(cell)
        n = finding_numbers(f)
        if r is None or (r[3], r[4], r[5]) != (n["late"], n["early"], n["holm"]) or words[f["result"]] not in r[2]:
            out.append(f"evidence.en.md {cell}: {r!r}")
    if "No drawdown cell exists for VF" not in text or "vf75 is untested" not in text:
        out.append("evidence.en.md must state no VF drawdown cell and vf75 untested")
    return out


def test_evidence_doc_tables():
    assert evidence_doc_problems(read("docs/evidence.md")) == []


def test_readme_evidence_rows():
    assert readme_evidence_problems(read("README.md")) == []


def test_skill_evidence_table():
    assert skill_evidence_problems(read("skill/references/evidence.md")) == []


def test_english_evidence_table():
    assert evidence_en_problems(read("docs/evidence.en.md")) == []


def _swap(text, a, b):
    return text.replace(a, "\0").replace(b, a).replace("\0", b)


EVIDENCE_MUTATIONS = [
    ("swap V2 late/early", lambda t: t.replace("| +2.09 | +5.92 |", "| +5.92 | +2.09 |")),
    ("V2 verdict rewritten", lambda t: t.replace("| **方向一致，符合预测** | +2.09", "| 无法估计 | +2.09")),
    ("F1 Holm p changed", lambda t: t.replace("| 0.742 | 0.742 |", "| 0.742 | 0.074 |")),
    ("VF1 early sign", lambda t: t.replace("| +0.059 | -0.007 |", "| +0.059 | +0.007 |")),
    ("control swapped", lambda t: t.replace("F vs P(F)：最大回撤差（pp）", "F vs C†：最大回撤差（pp）")),
    ("CI changed", lambda t: t.replace("+4.01 [+2.86, +5.19]", "+4.01 [+2.68, +5.19]")),
    ("row deleted", lambda t: re.sub(r"(?m)^\| F1 \|.*\n", "", t)),
    ("no-VF-drawdown statement deleted", lambda t: t.replace("- **B5 家族没有 VF 的回撤格**；组合规则只被检验过 Sharpe（VF1），且未通过。\n", "")),
    ("vf75 untested statement deleted", lambda t: t.replace("- **未检验**：默认配置 vf75", "- 默认配置 vf75")),
    ("descriptive heading made a test", lambda t: t.replace("（变体对比 §1，不是检验）", "（变体对比 §1）")),
    ("descriptive disclaimer deleted", lambda t: t.replace("**以下数字不做检验、不定级、不进 Holm。**", "")),
    ("descriptive number changed", lambda t: t.replace("| late | VF75 | 22.7% |", "| late | VF75 | 27.2% |")),
    ("descriptive rows swapped", lambda t: _swap(t, "| late | V | 23.1%", "| late | F50 | 21.8%")),
    ("descriptive table moved into the grading section",
     lambda t: t.replace("读法：", "| 期 | 方案 | CAGR | 最大回撤 | 年化波动 | Sharpe | Calmar | 平均仓位 |\n|---|---|---|---|---|---|---|---|\n"
                                 "| late | VF75 | 22.7% | 34.1% | 21.8% | 1.04 | 0.67 | 0.832 |\n\n读法：", 1)),
]


@pytest.mark.parametrize("name,mutate", EVIDENCE_MUTATIONS, ids=[m[0] for m in EVIDENCE_MUTATIONS])
def test_counterexample_evidence_doc(name, mutate):
    text = read("docs/evidence.md")
    bad = mutate(text)
    assert bad != text, f"mutation did not apply: {name}"
    assert evidence_doc_problems(bad), name


@pytest.mark.parametrize("name,rel,mutate,check", [
    ("README V2 numbers swapped", "README.md", lambda t: t.replace("两期 +2.09 / +5.92pp", "两期 +5.92 / +2.09pp"),
     readme_evidence_problems),
    ("README F2 Holm changed", "README.md", lambda t: t.replace("+5.72 / +3.27pp，Holm p = 0.002", "+5.72 / +3.27pp，Holm p = 0.02"),
     readme_evidence_problems),
    ("README vf75 untested removed", "README.md", lambda t: t.replace("| **未检验**（只有描述性对比）", "| 描述性对比"),
     readme_evidence_problems),
    ("README efficiency row made passed", "README.md",
     lambda t: t.replace("| V 的 Sharpe、F 的 Calmar、VF 的 Sharpe 改善 | 未确立 |", "| V 的 Sharpe、F 的 Calmar、VF 的 Sharpe 改善 | 通过 |"),
     readme_evidence_problems),
    ("README efficiency row deleted", "README.md",
     lambda t: re.sub(r"(?m)^\| V 的 Sharpe、F 的 Calmar、VF 的 Sharpe 改善 \|.*\n", "", t), readme_evidence_problems),
    ("skill V2 direction reversed", "skill/references/evidence.md",
     lambda t: t.replace("| V2 | V vs C†：最大回撤（pp） | 方向一致，符合预测 |", "| V2 | V vs C†：最大回撤（pp） | 方向一致，与预测相反 |"),
     skill_evidence_problems),
    ("skill F2 direction reversed", "skill/references/evidence.md",
     lambda t: t.replace("| F2 | F vs 洗牌：最大回撤（pp） | 方向一致，符合预测 |", "| F2 | F vs 洗牌：最大回撤（pp） | 方向一致，与预测相反 |"),
     skill_evidence_problems),
    ("skill V1 made consistent", "skill/references/evidence.md",
     lambda t: t.replace("| V1 | V vs 等暴露买入持有 C†：Sharpe | 无法估计 |", "| V1 | V vs 等暴露买入持有 C†：Sharpe | 方向一致，符合预测 |"),
     skill_evidence_problems),
    ("README preset row", "README.md", lambda t: t.replace("| 未检验 | `untested_parameters` |", "| 已检验 | `tested_drawdown_only` |"),
     readme_evidence_problems),
    ("skill V2 late changed", "skill/references/evidence.md", lambda t: t.replace("| +2.09 | +5.92 |", "| +2.90 | +5.92 |"),
     skill_evidence_problems),
    ("skill F2 verdict changed", "skill/references/evidence.md",
     lambda t: t.replace("| F2 | F vs 洗牌：最大回撤（pp） | 方向一致，符合预测 |", "| F2 | F vs 洗牌：最大回撤（pp） | 无法估计 |"),
     skill_evidence_problems),
    ("skill VF never-tested row removed", "skill/references/evidence.md", lambda t: re.sub(r"(?m)^\| — \|.*\n", "", t),
     skill_evidence_problems),
    ("english V2 swapped", "docs/evidence.en.md", lambda t: t.replace("| +2.09 | +5.92 |", "| +5.92 | +2.09 |"),
     evidence_en_problems),
])
def test_counterexample_other_evidence_tables(name, rel, mutate, check):
    text = read(rel)
    bad = mutate(text)
    assert bad != text, f"mutation did not apply: {name}"
    assert check(bad), name


# ================================================================ 3b. CLI option spelling against the parsers
def _parsers() -> dict[str, argparse.ArgumentParser]:
    out = {"": cli._symbols_parser(), "evidence": cli._evidence_parser()}
    tp = cli._track_parser()
    for act in tp._actions:
        if isinstance(act, argparse._SubParsersAction):
            for name, sub in act.choices.items():
                out[f"track {name}"] = sub
    return out


PARSERS = _parsers()
OPTIONS = {k: {o for a in p._actions for o in a.option_strings} for k, p in PARSERS.items()}
CHOICES = {k: {o: set(a.choices) for a in p._actions if a.choices and not isinstance(a, argparse._SubParsersAction)
               for o in a.option_strings} for k, p in PARSERS.items()}
ALL_OPTIONS = set().union(*OPTIONS.values())
CLI_DOCS = ["README.md", "README.en.md", "skill/SKILL.md", "skill/references/rules.md", "docs/methodology.md",
            "track/README.md"]


def _command_key(toks: list[str]) -> str | None:
    """Which parser a `riskbound ...` command line uses; None when the line is prose, not a command."""
    if not toks:
        return None
    if toks[0] == "evidence":
        return "evidence"
    if toks[0] == "track":
        return f"track {toks[1]}" if len(toks) > 1 and f"track {toks[1]}" in PARSERS else None
    if re.fullmatch(r"[A-Z][A-Z0-9.^-]*|SYM|<SYMS?>", toks[0]):
        return ""
    return None


def cli_problems(rel: str, text: str) -> list[str]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for opt in re.findall(r"(?<![\w-])--[A-Za-z][\w-]*", line):
            if opt not in ALL_OPTIONS:
                out.append(f"{rel}:{n}: unknown option {opt}")
        for m in re.finditer(r"riskbound((?:[ \t]+[^\s|`#]+)*)", line):
            toks = m.group(1).split()
            key = _command_key(toks)
            if key is None:
                continue
            for i, t in enumerate(toks):
                if not t.startswith("--"):
                    continue
                if t not in OPTIONS[key]:
                    out.append(f"{rel}:{n}: option {t} not accepted by 'riskbound {key}'")
                elif t in CHOICES[key] and i + 1 < len(toks) and not toks[i + 1].startswith(("<", "-")) \
                        and toks[i + 1] not in CHOICES[key][t]:
                    out.append(f"{rel}:{n}: {t} {toks[i + 1]} is not a valid choice")
    return out


@pytest.mark.parametrize("rel", [r for r in CLI_DOCS if (ROOT / r).exists()])
def test_cli_options_spelled_as_defined(rel):
    assert cli_problems(rel, read(rel)) == []


def test_readme_uses_the_main_options():
    text = read("README.md")
    for opt in ("--format", "--preset", "--vol-floor", "--below-sma", "--offline", "--cache-dir"):
        assert opt in text, opt


@pytest.mark.parametrize("old,new", [
    ("--vol-floor none", "--vol_floor none"),
    ("--below-sma 0.5", "--below-sma-weight 0.5"),
    ("--format json  ", "--fmt json  "),
    ("--preset v  ", "--preset vv  "),
    ("riskbound AAPL --offline --cache-dir DIR", "riskbound AAPL --offline --cachedir DIR"),
])
def test_counterexample_cli_spelling(old, new):
    text = read("README.md")
    assert old in text, old
    assert cli_problems("README.md", text.replace(old, new, 1))


def test_counterexample_option_on_wrong_subcommand():
    assert cli_problems("x.md", "riskbound evidence --offline")
    assert cli_problems("x.md", "riskbound track verify --dry-run")
    assert not cli_problems("x.md", "riskbound track append --dry-run")


# ================================================================ 4. other consistency checks
def test_expected_files_exist():
    for rel in ("README.md", "README.en.md", "skill/SKILL.md", "skill/README.md", "skill/references/rules.md",
                "skill/references/evidence.md"):
        assert (ROOT / rel).is_file(), rel
    for name in ("evidence", "negative-results", "methodology", "caveats"):
        assert (ROOT / f"docs/{name}.md").is_file() and (ROOT / f"docs/{name}.en.md").is_file(), name


def test_universe_listed():
    text = read("docs/evidence.md")
    assert "`" + " ".join(evidence.BACKTEST_UNIVERSE) + "`" in text


def test_readme_first_screen():
    head = "\n".join(read("README.md").splitlines()[:30])
    assert "不预测方向" in head and "不承诺收益" in head and "跑输买入持有" in head


def test_config_status_values_documented():
    text = read("skill/references/evidence.md")
    for v in (evidence.TESTED_DRAWDOWN_ONLY, evidence.COMPONENTS_TESTED_SEPARATELY, evidence.UNTESTED_PARAMETERS):
        assert v in text, v


F_RULE = "收盘 > SMA200 → 1，否则 0.5"
F_WRONG = re.compile(r"收盘\s*<\s*SMA200|收盘\s*≥\s*SMA200|收盘在 SMA200 下方")


def f_rule_problems(texts: dict[str, str]) -> list[str]:
    out = [f"{rel}: boundary stated as below / at-or-above SMA200" for rel, t in texts.items() if F_WRONG.search(t)]
    if F_RULE not in texts.get("docs/evidence.md", ""):
        out.append("docs/evidence.md must define F as " + F_RULE)
    if F_RULE not in texts.get("README.md", ""):
        out.append("README.md must define F as " + F_RULE)
    if "收盘 > SMA200 ? 1 : below_sma" not in texts.get("skill/references/rules.md", ""):
        out.append("skill/references/rules.md must use 收盘 > SMA200 ? 1 : below_sma")
    return out


F_DOCS = ["README.md", "docs/evidence.md", "docs/caveats.md", "skill/references/rules.md", "skill/SKILL.md"]


def test_f_rule_boundary_is_strictly_above():
    assert f_rule_problems({r: read(r) for r in F_DOCS}) == []


def test_counterexample_f_rule_boundary():
    texts = {r: read(r) for r in F_DOCS}
    bad = dict(texts, **{"docs/evidence.md": texts["docs/evidence.md"].replace(F_RULE, "收盘 < SMA200 → 0.5，否则 1")})
    assert f_rule_problems(bad)


@pytest.mark.parametrize("zh,en,limit", [("README.md", "README.en.md", 1 / 2)]
                         + [(f"docs/{n}.md", f"docs/{n}.en.md", 1 / 3)
                            for n in ("evidence", "negative-results", "methodology", "caveats")])
def test_english_versions_are_short(zh, en, limit):
    assert len(read(en)) <= len(read(zh)) * limit, en
