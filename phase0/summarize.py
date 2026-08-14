#!/usr/bin/env python3
"""Phase 0 汇总：聚合 results/*/summary.json 生成评测表 EVALUATION.md。

用法:
    python3 summarize.py                       # 生成 phase0/EVALUATION.md

评测表中"人工核验"列（verdict）需要安全同学填写 summary.json 中对应
finding 的 verdict 字段（true_positive / false_positive / needs_review），
重新运行本脚本即可反映到报告中。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PHASE0_DIR = Path(__file__).resolve().parent
SEV_ORDER = ["critical", "high", "medium", "low", "info"]
VERDICT_CN = {
    "true_positive": "真实",
    "false_positive": "误报",
    "needs_review": "待定",
    "": "未核验",
}


def fmt_duration(sec) -> str:
    if sec is None:
        return "-"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def main() -> int:
    results_dir = PHASE0_DIR / "results"
    summaries = []
    for p in sorted(results_dir.glob("*/summary.json")):
        try:
            summaries.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warn: skip unreadable {p}: {exc}", file=sys.stderr)
    if not summaries:
        print("no results found under phase0/results/", file=sys.stderr)
        return 1

    lines: list[str] = []
    lines.append("# Strix Phase 0 引擎验证评测表")
    lines.append("")
    lines.append(f"> 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}｜扫描数：{len(summaries)}")
    lines.append("")

    # ---- 总表 ----
    lines.append("## 1. 扫描总表")
    lines.append("")
    lines.append("| # | 标签 | 模式 | 目标 | 耗时 | 退出码 | 超时 | C | H | M | L | I | 合计 | tokens | 成本估算* |")
    lines.append("|---|------|------|------|------|--------|------|---|---|---|---|---|------|--------|----------|")
    for i, s in enumerate(summaries, 1):
        f = s.get("findings", {})
        sev = f.get("by_severity", {})
        usage = s.get("llm_usage") or {}
        target = s.get("source") or "-"
        if s.get("url"):
            target = f"{target} + {s['url']}"
        tokens = usage.get("total_tokens")
        tokens_s = f"{tokens:,}" if isinstance(tokens, (int, float)) else "-"
        cost = usage.get("cost_est_usd")
        cost_s = f"${cost:.2f}" if isinstance(cost, (int, float)) else "-"
        sev_cells = " | ".join(str(sev.get(k, 0)) for k in SEV_ORDER)
        lines.append(
            f"| {i} | {s.get('label')} | {s.get('scan_mode')} | {target} "
            f"| {fmt_duration(s.get('duration_sec'))} | {s.get('exit_code')} "
            f"| {'是' if s.get('timed_out') else '否'} | {sev_cells} | {f.get('total', 0)} "
            f"| {tokens_s} | {cost_s} |"
        )
    lines.append("")
    lines.append("\\* 成本估算来自 strix 的 litellm 估算，走自定义网关模型可能显示 $0；正式成本以网关账单为准。")
    lines.append("")

    # ---- 模型与环境 ----
    lines.append("## 2. 环境信息")
    lines.append("")
    models = sorted({str(s.get("model")) for s in summaries if s.get("model")})
    versions = sorted({str(s.get("strix_version")) for s in summaries if s.get("strix_version")})
    lines.append(f"- 模型：{'、'.join(models) or '-'}")
    lines.append(f"- strix 版本：{'、'.join(versions) or '-'}（平台集成时按此版本 pin）")
    lines.append("")

    # ---- 人工核验清单 ----
    lines.append("## 3. 高危发现人工核验清单（critical/high）")
    lines.append("")
    lines.append("核验方法：对照 PoC 与代码位置逐条复现。结论填入对应 `results/<label>/summary.json` 中该 finding 的")
    lines.append("`verdict` 字段（`true_positive` / `false_positive` / `needs_review`），重跑 `summarize.py` 汇总。")
    lines.append("")
    lines.append("| 标签 | 漏洞 | 级别 | CVSS | PoC | 结论 |")
    lines.append("|------|------|------|------|-----|------|")
    any_high = False
    for s in summaries:
        for item in s.get("findings", {}).get("items", []):
            if str(item.get("severity")).lower() not in ("critical", "high"):
                continue
            any_high = True
            lines.append(
                f"| {s.get('label')} | {item.get('title')}（{item.get('id')}） "
                f"| {str(item.get('severity')).upper()} | {item.get('cvss') or '-'} "
                f"| {'有' if item.get('has_poc') else '无'} | {VERDICT_CN.get(item.get('verdict'), '未核验')} |"
            )
    if not any_high:
        lines.append("| - |（无 critical/high 发现） | - | - | - | - |")
    lines.append("")

    # ---- 误报统计 ----
    verified = [
        (s.get("label"), it)
        for s in summaries
        for it in s.get("findings", {}).get("items", [])
        if it.get("verdict")
    ]
    lines.append("## 4. 核验统计")
    lines.append("")
    if verified:
        tp = sum(1 for _, it in verified if it.get("verdict") == "true_positive")
        fp = sum(1 for _, it in verified if it.get("verdict") == "false_positive")
        lines.append(f"- 已核验：{len(verified)} 条｜真实：{tp}｜误报：{fp}｜误报率：{fp / len(verified):.0%}")
    else:
        lines.append("- 暂无核验数据（按第 3 节说明填写 verdict 后重新运行 summarize.py）")
    lines.append("")

    # ---- Go/No-Go ----
    lines.append("## 5. Go/No-Go 检查单")
    lines.append("")
    crit_findings = [
        (s.get("label"), it)
        for s in summaries
        for it in s.get("findings", {}).get("items", [])
        if str(it.get("severity")).lower() == "critical"
    ]
    checks = [
        (
            "靶场验证：标准靶场（Juice Shop/DVWA）中 strix 能发现其已知核心漏洞",
            "人工判断（要求 quick 或 standard 模式至少发现 2 类靶场已知漏洞）",
        ),
        (
            "真实应用验证：内部典型应用扫描产出可解释、可复现的发现",
            "人工核验（高危发现核验为真实的比例 ≥ 50%，或能解释误报原因）",
        ),
        (
            "耗时在可接受范围：quick 分钟级、standard ≤ 1.5h",
            "见第 1 节耗时列",
        ),
        (
            "单次成本数据已取得（网关账单为准），可用于网关配额参数设定",
            "见第 1 节成本列 + 网关账单",
        ),
        (
            "受限出网实测通过（isolation/ 目录流程，strix 功能无致命退化）",
            "见 isolation/README.md 的验证记录",
        ),
        (
            "产物契约稳定：vulnerabilities.json / run.json 可解析（本表即证明）",
            f"解析告警数：{sum(1 for s in summaries if s.get('parse_warnings'))}（详见各 summary.json）",
        ),
        (
            "退出码行为符合预期（0/1/2）",
            f"出现过的退出码：{sorted({s.get('exit_code') for s in summaries if s.get('exit_code') is not None})}",
        ),
    ]
    lines.append("| # | 检查项 | 数据/判定 | 是否通过 |")
    lines.append("|---|--------|-----------|----------|")
    for i, (name, data) in enumerate(checks, 1):
        lines.append(f"| {i} | {name} | {data} | （填：是/否） |")
    lines.append("")
    lines.append(f"critical 发现共 {len(crit_findings)} 条（详见第 3 节）。全部检查项通过 → 立项 Go。")
    lines.append("")

    out = PHASE0_DIR / "EVALUATION.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
