"""strix 执行器：子进程调用 CLI + 失败自动重试 + run 目录定位。

契约（Phase 0 实测，strix 1.5.3）：
- CLI 无 --run-name 参数，run 目录名自动生成（strix_runs/<auto>/），用前后快照 diff 定位
- 退出码：0 无发现 / 1 执行错误 / 2 发现漏洞
- LLM 连接失败时 TUI 输出含 "LLM CONNECTION FAILED"（free 池间歇故障，决策 #7 需重试）
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable

from .config import get_settings

RETRY_MARKER = "LLM CONNECTION FAILED"

# strix 对这些前缀有专门路由，不加 openai/ 前缀
_PASSTHROUGH_PREFIXES = ("openai/", "litellm/", "any-llm/", "ollama/")


def _strix_env(model: str = "", llm_api_key: str = "") -> dict[str, str]:
    s = get_settings()
    env = {**os.environ}
    env["STRIX_TELEMETRY"] = "off"
    if s.llm_api_base:
        env["LLM_API_BASE"] = s.llm_api_base
    # 仅使用任务创建者的个人密钥（平台不持有统一密钥）
    if llm_api_key:
        env["LLM_API_KEY"] = llm_api_key
    name = (model or s.strix_llm).strip()
    # 网关模型名如 "free/xxx" 含 "/"，litellm 会把首段当 provider 解析而报错；
    # 配置了网关时统一加 "openai/" 前缀：strix 会剥掉前缀、把模型名原样发给网关
    if s.llm_api_base and "/" in name and not name.lower().startswith(_PASSTHROUGH_PREFIXES):
        name = "openai/" + name
    env["STRIX_LLM"] = name
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
    model: str = "",
    instruction: str = "",
    llm_api_key: str = "",
) -> dict:
    """运行 strix，返回 {exit_code, timed_out, run_dir_name, attempts}。"""
    s = get_settings()
    work_dir.mkdir(parents=True, exist_ok=True)
    before = _list_runs(work_dir)

    cmd = [s.strix_bin, "-n", "-t", str(src_dir)]
    if test_url:
        cmd += ["-t", test_url]
    cmd += ["-m", scan_mode]
    if instruction:
        cmd += ["--instruction", instruction]
    if s.strix_max_budget > 0:
        cmd += ["--max-budget", str(s.strix_max_budget)]
    if s.strix_extra_args.strip():
        cmd += shlex.split(s.strix_extra_args.strip())

    timeout_sec = {"quick": s.timeout_quick, "standard": s.timeout_standard, "deep": s.timeout_deep}.get(
        scan_mode, s.timeout_standard
    )
    env = _strix_env(model, llm_api_key)
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
