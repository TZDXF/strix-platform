"""strix 执行器：子进程调用 CLI + 失败自动重试 + run 目录定位 + 过程日志转发。

契约（Phase 0 实测，strix 1.5.3）：
- CLI 无 --run-name 参数，run 目录名自动生成（strix_runs/<auto>/），用前后快照 diff 定位
- 退出码：0 无发现 / 1 执行错误 / 2 发现漏洞
- LLM 连接失败时 TUI 输出含 "LLM CONNECTION FAILED"（free 池间歇故障，决策 #7 需重试）
- 引擎在 strix_runs/<auto>/strix.log 持续写生命周期事件（沙箱/智能体/轮次/告警），
  等待子进程期间由 tail 线程把关键事件翻译后转投任务日志，避免长扫描期间日志空白
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .config import get_settings
from .tasklog import now_bj

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


# ---- strix.log 过程事件转发 ----

_TAIL_INTERVAL = 2.0        # strix.log 轮询间隔（秒）
_TAIL_FRESH_WIN = 120.0     # mtime 落在该窗口内的文件视为本次运行，从头转发；旧残留跳到末尾
_HEARTBEAT_SILENCE = 300.0  # 连续无事件达该时长输出心跳（秒）


def _agent_label(agent_id: str, state: dict) -> str:
    return state["agents"].get(agent_id) or (agent_id[:6] if agent_id else "-")


def _summarize_strix_line(line: str, state: dict) -> str | None:
    """把一行 strix.log 翻译成任务日志摘要；无关注行返回 None。

    只挑对用户有信息量的生命周期事件（引擎/沙箱/智能体/轮次/阶段性结果/告警），
    逐轮的调试噪声（Calling LLM、Tracing disabled、报表落盘等）不转发。
    """
    m = re.search(r"Starting Strix scan (\S+) \(image=([^,]+), max_turns=(\d+)", line)
    if m:
        image = m.group(2).rsplit("/", 1)[-1]
        return f"[scan] 引擎启动：run {m.group(1)}（镜像 {image}，最大轮次 {m.group(3)}）"
    m = re.search(r"LLM model resolved: (\S+)", line)
    if m:
        return f"[scan] 模型解析: {m.group(1)}"
    m = re.search(r"Sandbox container created: id=(\w+)", line)
    if m:
        return f"[scan] 沙箱容器已创建（id {m.group(1)}）"
    if "Sandbox ready for scan" in line or "ready and cached" in line:
        return "[scan] 沙箱就绪（Docker 沙箱 + Caido 代理）"
    m = re.search(r"Built root agent '([^']+)' \(skills=\d+, tools=(\d+), scan_mode=(\w+), whitebox=(\w+)\)", line)
    if m:
        box = "白盒" if m.group(4) == "True" else "黑盒"
        return f"[scan] 根智能体就绪：{m.group(1)}（{m.group(2)} 个工具，{m.group(3)} 模式，{box}）"
    m = re.search(r"agent\.register (\w+) \((.+?)\) parent=(\S+)", line)
    if m:
        state["agents"][m.group(1)] = m.group(2)
        if m.group(3) == "-":
            return f"[scan] 智能体启动: {m.group(2)}"
        return f"[scan] 智能体启动: {m.group(2)}（由 {_agent_label(m.group(3), state)} 派生）"
    m = re.search(r"agent\.status (\w+)=(\w+)", line)
    if m:
        name = _agent_label(m.group(1), state)
        zh = {"completed": "完成", "failed": "失败", "running": "运行中", "waiting": "等待中"}.get(
            m.group(2), m.group(2)
        )
        return f"[scan] 智能体{zh}: {name}"
    m = re.search(r"Starting turn (\d+), current_agent=(.+?)\s*$", line)
    if m:
        n = int(m.group(1))
        state["turn"] = (m.group(2), n)
        if n == 1 or n % 5 == 0:  # 逐轮全转发太密（deep 可达数百轮），第 1 轮 + 每 5 轮采样
            return f"[scan] {m.group(2)} 第 {n} 轮"
        return None
    m = re.search(r"Wrote SARIF 2\.1\.0 report: .*?\((\d+) results\)", line)
    if m:
        n = int(m.group(1))
        changed = n != state["results"]
        state["results"] = n
        if changed and n > 0:  # 0 个结果每轮都写，只在出现/变化时刷
            return f"[scan] 阶段性结果：已确认 {n} 个问题"
        return None
    if (" WARNING " in line or " ERROR " in line) and "telemetry" not in line:
        msg = line.split(" - ", 1)[-1].strip()
        msg = re.sub(r"\S*strix_runs/\S+", "<run目录>", msg)[:200]
        tag = "错误" if " ERROR " in line else "警告"
        return f"[scan][{tag}] {msg}"
    return None


def _tail_strix_log(work_dir: Path, log: Callable[[str], None], stop: threading.Event) -> None:
    """后台 tail 最新 strix_runs/*/strix.log，把关键事件转发进任务日志。

    仅在 execute_scan 等待子进程期间运行（wait 返回即被 join），log 回调
    不会与主线程并发使用 DB 会话。
    """
    state: dict = {"agents": {}, "results": -1, "turn": None}
    offsets: dict[Path, int] = {}
    current: Path | None = None
    buf = b""
    last_msg = ""
    last_event = time.monotonic()
    started = time.monotonic()

    while not stop.is_set():
        try:
            logs = list((work_dir / "strix_runs").glob("*/strix.log"))
            newest = max(logs, key=lambda p: p.stat().st_mtime) if logs else None
            if newest != current:
                current, buf = newest, b""
            if current is not None:
                size = current.stat().st_size
                off = offsets.get(current)
                if off is None:
                    off = 0 if time.time() - current.stat().st_mtime < _TAIL_FRESH_WIN else size
                if size < off:  # 文件被截断/轮转，重头跟踪
                    off, buf = 0, b""
                if size > off:
                    with current.open("rb") as f:
                        f.seek(off)
                        chunk = f.read()
                    offsets[current] = off + len(chunk)
                    buf += chunk
                    *complete, buf = buf.split(b"\n")
                    for raw in complete:
                        msg = _summarize_strix_line(raw.decode("utf-8", "replace"), state)
                        # 相邻去重：同一事件会由 session_manager 与 runner 各写一条（如沙箱就绪）
                        if msg and msg != last_msg:
                            log(msg)
                            last_msg = msg
                            last_event = time.monotonic()
            if time.monotonic() - last_event >= _HEARTBEAT_SILENCE:
                mins = int((time.monotonic() - started) // 60)
                tip = f"（{state['turn'][0]} 第 {state['turn'][1]} 轮）" if state["turn"] else ""
                if state["results"] > 0:
                    tip += f"，已确认 {state['results']} 个问题"
                log(f"[scan] 运行中… 已执行 {mins} 分钟{tip}")
                last_event = time.monotonic()
        except Exception:  # noqa: BLE001 —— 监控线程任何异常都不能影响扫描本身
            time.sleep(1)
        stop.wait(_TAIL_INTERVAL)


def _display_cmd(cmd: list[str], work_dir: Path) -> str:
    """供日志展示的命令行：折叠长指令、缩短工作区路径（密钥在环境变量里，不在命令行）。"""
    parts: list[str] = []
    skip_value = False
    for a in cmd:
        if skip_value:
            skip_value = False
        elif a == "--instruction":
            parts.append("--instruction <测试指令>")
            skip_value = True
        else:
            parts.append(a)
    return " ".join(parts).replace(str(work_dir), ".")


def execute_scan(
    work_dir: Path,
    src_dir: Path,
    test_targets: list[dict],
    scan_mode: str,
    log: Callable[[str], None],
    model: str = "",
    instruction: str = "",
    llm_api_key: str = "",
) -> dict:
    """运行 strix，返回 {exit_code, timed_out, run_dir_name, attempts}。

    test_targets 为黑盒目标列表 [{"url", "note"}]：strix 的 -t 可重复传多个目标，
    每个地址各占一个 -t；note（地址作用说明）由调用方并入 instruction 注入。
    """
    s = get_settings()
    work_dir.mkdir(parents=True, exist_ok=True)
    before = _list_runs(work_dir)

    cmd = [s.strix_bin, "-n", "-t", str(src_dir)]
    for t in test_targets:
        cmd += ["-t", t["url"]]
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

    log(f"[scan] 命令: {_display_cmd(cmd, work_dir)}")

    exit_code: int | None = None
    timed_out = False
    attempts = 0

    for attempt in range(1, s.max_scan_attempts + 1):
        attempts = attempt
        log(f"[scan] 第 {attempt}/{s.max_scan_attempts} 次执行（超时 {timeout_sec // 60} 分钟）")
        # tail 线程只在 proc.wait 期间运行，wait 返回（含超时）即 join，
        # 保证 log 回调（写 DB）不与主线程并发
        stop_evt = threading.Event()
        mon = threading.Thread(target=_tail_strix_log, args=(work_dir, log, stop_evt), daemon=True)
        mon.start()
        try:
            with scan_log.open("ab") as out:
                out.write(f"\n===== attempt {attempt} {now_bj().strftime('%Y-%m-%dT%H:%M:%S')} =====\n".encode())
                proc = subprocess.Popen(cmd, cwd=str(work_dir), stdout=out, stderr=subprocess.STDOUT, env=env)
                try:
                    exit_code = proc.wait(timeout=timeout_sec)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=60)
                    exit_code = proc.returncode
                    timed_out = True
        finally:
            stop_evt.set()
            mon.join(timeout=10)
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
    if run_dir_name:
        log(f"[scan] run 目录: {run_dir_name}")

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
