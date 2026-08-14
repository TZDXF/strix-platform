#!/usr/bin/env python3
"""Phase 0 产物解析：读取 run 目录的落盘产物，生成结构化 summary.json。

用法:
    python3 collect.py --label <label>           # 读 work/<label>/meta.json 定位产物
    python3 collect.py --label <label> --work-dir work/<label>

产物契约（来自 strix 1.5.3 源码核实）:
    strix_runs/<run>/run.json              运行记录，含 llm_usage（token/成本估算）
    strix_runs/<run>/vulnerabilities.json  漏洞列表（结构化字段）
    strix_runs/<run>/vulnerabilities.csv   漏洞索引
    strix_runs/<run>/vulnerabilities/*.md  单漏洞详情
    strix_runs/<run>/penetration_test_report.md / *.sarif / *.pdf

注意: 走自定义网关的模型（非 openai/ 前缀）llm_usage.cost 可能为 0——
成本以网关侧账单为准，token 数仍然可靠。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SEVERITIES = ["critical", "high", "medium", "low", "info"]

PHASE0_DIR = Path(__file__).resolve().parent


def _read_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def collect_label(label: str, work_dir: Path | None = None) -> int:
    work = work_dir or PHASE0_DIR / "work" / label
    meta_path = work / "meta.json"
    meta = _read_json(meta_path) or {}
    if not meta:
        print(f"ERROR: {meta_path} missing or unreadable", file=sys.stderr)
        return 1

    run_name = meta.get("run_dir_name") or ""
    run_dir = work / "strix_runs" / run_name if run_name else None

    summary: dict = {
        "label": label,
        "scan_mode": meta.get("scan_mode"),
        "source": meta.get("source"),
        "url": meta.get("url"),
        "start_iso": meta.get("start_iso"),
        "end_iso": meta.get("end_iso"),
        "duration_sec": meta.get("duration_sec"),
        "timed_out": meta.get("timed_out"),
        "exit_code": meta.get("exit_code"),
        "strix_version": meta.get("strix_version"),
        "model": meta.get("model"),
        "run_dir_name": run_name or None,
        "run_record": None,
        "findings": {"total": 0, "by_severity": {s: 0 for s in SEVERITIES}, "items": []},
        "llm_usage": None,
        "artifacts": {},
        "parse_warnings": [],
    }

    if run_dir is None or not run_dir.is_dir():
        summary["parse_warnings"].append("run dir not found (scan may have failed before run creation)")
    else:
        # run.json：状态 + llm_usage
        run_record = _read_json(run_dir / "run.json")
        if isinstance(run_record, dict):
            summary["run_record"] = {
                k: run_record.get(k)
                for k in ("status", "targets", "scan_mode", "started_at", "ended_at", "created_at")
                if k in run_record
            }
            usage = run_record.get("llm_usage")
            if isinstance(usage, dict):
                summary["llm_usage"] = {
                    "requests": usage.get("requests"),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "cost_est_usd": usage.get("cost"),
                    "note": "cost_est_usd 为 litellm 估算；走自定义网关模型可能为 0，成本以网关账单为准",
                    "agents": len(usage.get("agents") or []),
                }
        else:
            summary["parse_warnings"].append("run.json missing/unreadable")

        # vulnerabilities.json：结构化漏洞
        vulns = _read_json(run_dir / "vulnerabilities.json")
        if isinstance(vulns, list):
            by_sev = {s: 0 for s in SEVERITIES}
            items = []
            for v in vulns:
                sev = str(v.get("severity", "info")).lower()
                if sev not in by_sev:
                    by_sev[sev] = by_sev.get(sev, 0) + 1
                else:
                    by_sev[sev] += 1
                items.append(
                    {
                        "id": v.get("id"),
                        "title": v.get("title"),
                        "severity": sev,
                        "cvss": v.get("cvss"),
                        "cwe": v.get("cwe"),
                        "cve": v.get("cve"),
                        "endpoint": v.get("endpoint"),
                        "target": v.get("target"),
                        "has_poc": bool(v.get("poc_script_code") or v.get("poc_description")),
                        "has_fix": bool(v.get("code_locations")),
                        # 人工核验用字段，summarize 阶段填
                        "verdict": "",
                        "verdict_note": "",
                    }
                )
            summary["findings"] = {"total": len(vulns), "by_severity": by_sev, "items": items}
        else:
            summary["parse_warnings"].append("vulnerabilities.json missing/unreadable — fallback check csv")
            csv_path = run_dir / "vulnerabilities.csv"
            if csv_path.is_file():
                summary["parse_warnings"].append(f"vulnerabilities.csv exists at {csv_path}")

        # 产物存在性
        for key, pattern in {
            "report_md": "penetration_test_report.md",
            "vulns_json": "vulnerabilities.json",
            "vulns_csv": "vulnerabilities.csv",
            "vulns_md_dir": "vulnerabilities",
        }.items():
            summary["artifacts"][key] = (run_dir / pattern).exists()
        summary["artifacts"]["sarif_files"] = [p.name for p in run_dir.glob("*.sarif")]
        summary["artifacts"]["pdf_files"] = [p.name for p in run_dir.glob("*.pdf")]

    out_dir = PHASE0_DIR / "results" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary written: {out_path}")
    if summary["parse_warnings"]:
        for w in summary["parse_warnings"]:
            print(f"  warn: {w}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--work-dir", default=None)
    args = ap.parse_args()
    return collect_label(args.label, Path(args.work_dir) if args.work_dir else None)


if __name__ == "__main__":
    sys.exit(main())
