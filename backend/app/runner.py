"""strix 执行器：子进程调用 CLI + 失败自动重试 + run 目录定位。

契约（Phase 0 实测，strix 1.5.3）：
- CLI 无 --run-name 参数，run 目录名自动生成（strix_runs/<auto>/），用前后快照 diff 定位
- 退出码：0 无发现 / 1 执行错误 / 2 发现漏洞
- LLM 连接失败时 TUI 输出含 "LLM CONNECTION FAILED"（free 池间歇故障，决策 #7 需重试）
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from .config import get_settings

RETRY_MARKER = "LLM CONNECTION FAILED"


def _strix_env() -> dict[str, str]:
    s = get_settings()
    env = {**os.environ}
    env["STRIX_TELEMETRY"] = "off"
    if s.llm_api_base:
        env["LLM_API_BASE"] = s.llm_api_base
    if s.llm_api_key:
        env["LLM_API_KEY"] = s.llm_api_key
    if s.strix_llm:
        env["STRIX_LLM"] = s.strix_llm
    return env


def _list_runs(work_dir: Path) -> set[str]:
    base = work_dir / "strix_runs"
    if not base.is_dir():
        return set()
    return {p.name for p in base.iterdir() if (p / "run.json").is_file()}


def execute_scan(
    work_dir: Path,
    src_dir: Path,
    test_url: str,
    scan_mode: str,
    log: Callable[[str], None],
) -> dict:
    """运行 strix，返回 {exit_code, timed_out, run_dir_name, attempts}。"""
    s = get_settings()
    work_dir.mkdir(parents=True, exist_ok=True)
    before = _list_runs(work_dir)

    cmd = [s.strix_bin, "-n", "-t", str(src_dir)]
    if test_url:
        cmd += ["-t", test_url]
    cmd += ["-m", scan_mode]

    timeout_sec = {"quick": s.timeout_quick, "standard": s.timeout_standard, "deep": s.timeout_deep}.get(
        scan_mode, s.timeout_standard
    )
    env = _strix_env()
    scan_log = work_dir / "scan.log"

    exit_code: int | None = None
    timed_out = False
    attempts = 0

    for attempt in range(1, s.max_scan_attempts + 1):
        attempts = attempt
        log(f"[scan] 第 {attempt}/{s.max_scan_attempts} 次执行（超时 {timeout_sec // 60} 分钟）")
        with scan_log.open("ab") as out:
            out.write(f"\n===== attempt {attempt} {time.strftime('%Y-%m-%dT%H:%M:%S')} =====\n".encode())
            proc = subprocess.Popen(cmd, cwd=str(work_dir), stdout=out, stderr=subprocess.STDOUT, env=env)
            try:
                exit_code = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=60)
                exit_code = proc.returncode
                timed_out = True
        log(f"[scan] 退出码 {exit_code}" + ("（超时被回收）" if timed_out else ""))

        if timed_out:
            break  # 超时是运维回收，不重试

        tail = ""
        try:
            tail = scan_log.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
        if exit_code == 1 and RETRY_MARKER in tail:
            log("[scan] LLM 连接失败（网关 free 池间歇故障），30 秒后自动重试")
            time.sleep(30)
            continue
        break

    # 定位本次 run 目录：优先新增目录，回退最新 run.json
    after = _list_runs(work_dir)
    new = after - before
    run_dir_name = ""
    base = work_dir / "strix_runs"
    if len(new) == 1:
        run_dir_name = new.pop()
    elif new:
        run_dir_name = max(new, key=lambda n: (base / n / "run.json").stat().st_mtime)
    elif after:
        run_dir_name = max(after, key=lambda n: (base / n / "run.json").stat().st_mtime)

    return {"exit_code": exit_code, "timed_out": timed_out, "run_dir_name": run_dir_name, "attempts": attempts}


def read_run_artifacts(work_dir: Path, run_dir_name: str) -> tuple[dict, list[dict]]:
    """解析 run.json + vulnerabilities.json（容错：文件缺失返回空）。"""
    import json

    run_dir = work_dir / "strix_runs" / run_dir_name
    run_record: dict = {}
    vulns: list[dict] = []
    if run_dir.is_dir():
        try:
            run_record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            run_record = {}
        try:
            data = json.loads((run_dir / "vulnerabilities.json").read_text(encoding="utf-8"))
            if isinstance(data, list):
                vulns = data
        except (OSError, ValueError):
            vulns = []
    return run_record, vulns
