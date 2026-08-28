"""生成 Markdown 格式的渗透测试报告。

与 strix 自带的 reportlab PDF 渲染同源同构（run.json + vulnerabilities.json）：
封面元信息、执行摘要、严重度分布、方法论 / 技术分析 / 改进建议、逐条漏洞明细
（含 PoC 脚本与证据）。纯文本拼接，无第三方依赖，产物可直接预览与二次编辑。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from strix.interface.viewer.transcript import (
    primary_target,
    read_run_summary,
    read_vulnerabilities,
    severity_counts,
)


if TYPE_CHECKING:
    from pathlib import Path


_SEVERITY_ORDER = ("critical", "high", "medium", "low")
_SEVERITY_LABELS = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}

_FENCE_RE = re.compile(r"^```([^\n`]*)\n(.*?)\n?```$", re.DOTALL)


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip().replace(" UTC", "Z").replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _fmt_time(raw: Any) -> str:
    parsed = _parse_time(raw)
    return parsed.strftime("%Y-%m-%d %H:%M UTC") if parsed else "n/a"


def _duration(start: Any, end: Any) -> str:
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    if not start_dt or not end_dt:
        return "n/a"
    seconds = int((end_dt - start_dt).total_seconds())
    if seconds < 0:
        return "n/a"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _strip_leading_heading(md: str) -> str:
    """去掉一个开头标题（每个章节自行提供标题）。"""
    lines = md.lstrip("\n").split("\n")
    if lines and re.match(r"^#{1,6}\s+", lines[0].strip()):
        return "\n".join(lines[1:]).lstrip("\n")
    return md


def _code_block(value: Any) -> str:
    """以四反引号围栏嵌入代码，可安全包含内容中的 ``` 围栏，并保留语言标记。"""
    if not isinstance(value, str):
        value = str(value)
    lang = ""
    match = _FENCE_RE.match(value.strip())
    if match:  # 剥掉外层 ``` 围栏，避免嵌套围栏渲染混乱
        value, lang = match.group(2), match.group(1)
    return f"````{lang}\n{value.strip()}\n````"


def _section_md(title: str, value: Any, *, code: bool = False) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    body = _code_block(value) if code else str(value).strip()
    return f"#### {title}\n\n{body}\n"


def _finding_md(index: int, vuln: dict[str, Any]) -> str:
    title = vuln.get("title") or "未命名漏洞"
    severity = str(vuln.get("severity") or "").lower().strip() or "low"

    meta: list[str] = [f"严重级别：**{_SEVERITY_LABELS.get(severity, severity.upper())}**"]
    if vuln.get("cvss") is not None:
        meta.append(f"CVSS：{vuln.get('cvss')}")
    for key, label in (("target", "目标"), ("endpoint", "端点"), ("method", "方法")):
        if vuln.get(key):
            meta.append(f"{label}：{vuln.get(key)}")

    parts = [f"### {index}. {title}\n", " · ".join(meta) + "\n"]
    remediation = vuln.get("remediation_steps")
    if isinstance(remediation, list):
        remediation = "\n".join(f"- {step}" for step in remediation)
    parts.append(_section_md("描述", vuln.get("description")))
    parts.append(_section_md("影响", vuln.get("impact")))
    parts.append(_section_md("技术分析", vuln.get("technical_analysis")))
    parts.append(_section_md("PoC 说明", vuln.get("poc_description")))
    parts.append(_section_md("PoC 脚本", vuln.get("poc_script_code"), code=True))
    parts.append(_section_md("证据", vuln.get("evidence"), code=True))
    parts.append(_section_md("修复建议", remediation))
    return "\n".join(part for part in parts if part)


def generate_report_md(run_dir: Path) -> str:
    """为 run_dir 生成完整明细的 Markdown 渗透测试报告。"""
    record = read_run_summary(run_dir)
    vulns = [v for v in read_vulnerabilities(run_dir) if isinstance(v, dict)]
    counts = severity_counts(vulns)
    run_name = str(record.get("run_name") or run_dir.name)
    target = primary_target(record) or "unknown target"

    meta_rows = [
        ("目标", target),
        ("Run", run_name),
        ("扫描模式", str(record.get("scan_mode") or "n/a")),
        ("状态", str(record.get("status") or "n/a")),
        ("开始时间", _fmt_time(record.get("start_time"))),
        ("结束时间", _fmt_time(record.get("end_time"))),
        ("耗时", _duration(record.get("start_time"), record.get("end_time"))),
    ]
    meta_table = "\n".join(f"| {label} | {value} |" for label, value in meta_rows)

    dist = " · ".join(f"{_SEVERITY_LABELS[name]} {counts.get(name, 0)}" for name in _SEVERITY_ORDER)
    parts = [
        "# Strix 渗透测试报告\n",
        f"> **CONFIDENTIAL** — 本报告包含完整漏洞细节与验证脚本，请妥善保管。\n",
        "## 基本信息\n",
        "| 项目 | 内容 |",
        "| --- | --- |",
        meta_table + "\n",
        "## 执行摘要\n",
        f"共 **{len(vulns)}** 个漏洞：{dist}\n",
    ]

    scan_results = record.get("scan_results")
    if isinstance(scan_results, dict):
        summary = scan_results.get("executive_summary")
        if isinstance(summary, str) and summary.strip():
            parts.append(_strip_leading_heading(summary).strip() + "\n")
        for title, key in (("方法论", "methodology"), ("技术分析", "technical_analysis"), ("改进建议", "recommendations")):
            value = scan_results.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(f"## {title}\n")
                parts.append(_strip_leading_heading(value).strip() + "\n")

    parts.append("## 漏洞详情\n")
    if vulns:
        for index, vuln in enumerate(vulns, start=1):
            parts.append(_finding_md(index, vuln))
    else:
        parts.append("本次运行未记录任何漏洞。\n")

    return "\n".join(parts)


__all__ = ["generate_report_md"]
