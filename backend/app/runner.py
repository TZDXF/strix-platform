"""strix 执行器：子进程调用 CLI + 失败自动重试 + run 目录定位 + 过程日志转发。

契约（Phase 0 实测，strix 1.5.3）：
- CLI 无 --run-name 参数，run 目录名自动生成（strix_runs/<auto>/），用前后快照 diff 定位
- 退出码：0 无发现 / 1 执行错误 / 2 发现漏洞
- LLM 连接失败时 TUI 输出含 "LLM CONNECTION FAILED"（free 池间歇故障，决策 #7 需重试）
- 引擎在 strix_runs/<auto>/strix.log 持续写生命周期事件（沙箱/智能体/轮次/告警），
  等待子进程期间由 tail 线程把关键事件翻译后转投任务日志，避免长扫描期间日志空白
- --resume <run_name> 从 run 目录恢复根/子智能体完整 LLM 历史；恢复时传新 --instruction
  会被引擎注入根智能体会话（runner.resume_instruction），平台用它实现运行中转向
- 无头子进程默认无自定义技能通道：经 python -c 引导段先 register_skill_dir 再进引擎主入口
- 正常结束（退出码 0）但登记漏洞为 0 时，平台自动 --resume 追问根智能体一轮（0 漏洞核实轮）：
  弱模型可能把发现只写进报告文本而不调登记工具，这轮让它补登记或说明空结果原因
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .config import get_settings
from .tasklog import now_bj

RETRY_MARKER = "LLM CONNECTION FAILED"

# 0 漏洞核实轮：只是收尾追问（补登记/说明原因），不应跑成第二次完整扫描，固定 20 分钟上限
_VERIFY_TIMEOUT_SEC = 20 * 60

# 核实轮注入根智能体的追问指令（英文与平台其他注入指令一致，回复要求中文）
_VERIFY_INSTRUCTION = (
    "POST-SCAN VERIFICATION (final wrap-up round; do NOT start a broad new scan):\n"
    "The scan has ended with 0 registered vulnerabilities. Registered findings are the ONLY "
    "thing the platform imports — security issues described only in your executive summary "
    "or report prose are NOT counted. Now:\n"
    "1. Re-check the issues you investigated during this run (your todo list, notes and the "
    "summary you wrote).\n"
    "2. For each real, confirmed security issue you found but did NOT register, register it "
    "NOW via the vulnerability reporting tool (target/severity/description/PoC as required, "
    "following the target mapping rules given earlier).\n"
    "3. If you genuinely found no issues, reply with a short explanation in Simplified "
    "Chinese (简体中文) of why the result is 0 (e.g. scope/mode limits, target unreachable, "
    "code clean in the areas examined)."
)

# strix 对这些前缀有专门路由，不加 openai/ 前缀
_PASSTHROUGH_PREFIXES = ("openai/", "litellm/", "any-llm/", "ollama/")

# strix 子进程引导段：先把平台技能目录注册进引擎（优先级高于内置技能），再进 CLI 主入口。
# 经 `python -c <引导段> <strix 参数...>` 启动时 sys.argv = ['-c', <参数...>]，
# argparse 解析 sys.argv[1:]，与 strix 控制台脚本行为一致
_BOOTSTRAP = (
    "import os\n"
    "d = os.environ.get('PLATFORM_SKILLS_DIR', '')\n"
    "if d and os.path.isdir(d):\n"
    "    from strix.skills import register_skill_dir\n"
    "    register_skill_dir(d)\n"
    "from strix.interface.main import main\n"
    "main()\n"
)


def _strix_env(model: str = "", llm_api_key: str = "", skills_dir: Path | None = None) -> dict[str, str]:
    s = get_settings()
    env = {**os.environ}
    env["STRIX_TELEMETRY"] = "off"
    if skills_dir is not None:
        env["PLATFORM_SKILLS_DIR"] = str(skills_dir)
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
    以及证明 AI 通路存活的事件：LLM 调用与工具执行按间隔采样转发（首次必报，
    之后每 20/25 次报一次），其余逐行调试噪声（Tracing disabled、报表落盘等）不转发。
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
        cid = m.group(1)
        ids = state.setdefault("sandbox_ids", [])
        if cid not in ids:
            ids.append(cid)
            cb = state.get("on_sandbox_id")
            if cb:  # 增量外报：兜底清扫需在扫描进行中就知道哪些容器属运行中任务
                try:
                    cb(cid)
                except Exception:  # noqa: BLE001 —— 登记失败不影响扫描
                    pass
        return f"[scan] 沙箱容器已创建（id {cid}）"
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
    if "Calling LLM" in line:  # openai.agents 每次发起 LLM 请求打一行 DEBUG
        state["llm_calls"] += 1
        n = state["llm_calls"]
        state["last_action"] = f"第 {n} 次 LLM 调用尚未返回（等待模型响应）"
        if n == 1 or n % 20 == 0:  # 首次必报（确认 AI 通路），之后采样
            return f"[scan] 调用 LLM（第 {n} 次）"
        return None
    m = re.search(r"Invoking tool (\S+)", line)
    if m:
        state["tool_calls"] += 1
        state["last_tool"] = m.group(1)
        n = state["tool_calls"]
        state["last_action"] = f"工具 {m.group(1)} 执行中（第 {n} 次工具调用）"
        if n == 1 or n % 25 == 0:  # 首次必报（引擎开始干活），之后采样
            return f"[scan] 执行工具 {m.group(1)}（第 {n} 次）"
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


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _scan_log_tail(work_dir: Path, limit: int = 300) -> str:
    """取 scan.log（strix CLI 的 stdout/stderr）尾部的可读文本，用于启动卡点诊断。"""
    try:
        text = (work_dir / "scan.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = _ANSI_RE.sub("", text).strip()[-limit:].replace("\n", " ⏎ ")
    return re.sub(r"\S*strix_runs/\S*", "<run目录>", tail)


def _tail_strix_log(
    work_dir: Path,
    log: Callable[[str], None],
    stop: threading.Event,
    sandbox_ids: list[str] | None = None,
    on_sandbox_id: Callable[[str], None] | None = None,
) -> None:
    """后台 tail 最新 strix_runs/*/strix.log，把关键事件转发进任务日志。

    仅在 execute_scan 等待子进程期间运行（wait 返回即被 join），log 回调
    不会与主线程并发使用 DB 会话。sandbox_ids 为调用方持有的共享列表，解析到
    「Sandbox container created」就追加并触发 on_sandbox_id（增量登记用）。
    """
    state: dict = {
        "agents": {}, "results": -1, "turn": None,
        "llm_calls": 0, "tool_calls": 0, "last_tool": "", "last_action": "",
        "sandbox_ids": sandbox_ids if sandbox_ids is not None else [],
        "on_sandbox_id": on_sandbox_id,
    }
    offsets: dict[Path, int] = {}
    current: Path | None = None
    buf = b""
    last_msg = ""
    last_event = time.monotonic()
    last_content = time.monotonic()  # 最近一次从 strix.log 读到新内容（含未转发行）
    last_raw = ""                    # strix.log 最新一行原文，供长时间无进展时诊断
    warned_no_log = False
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
                        text = raw.decode("utf-8", "replace")
                        if text.strip():
                            last_raw = text
                        msg = _summarize_strix_line(text, state)
                        # 相邻去重：同一事件会由 session_manager 与 runner 各写一条（如沙箱就绪）
                        if msg and msg != last_msg:
                            log(msg)
                            last_msg = msg
                            last_event = time.monotonic()
                    last_content = time.monotonic()
            elif not warned_no_log and time.monotonic() - started >= 90:
                warned_no_log = True
                hint = _scan_log_tail(work_dir)
                log(
                    "[scan][警告] 引擎日志 strix_runs/*/strix.log 迟迟未生成，引擎可能卡在启动阶段"
                    + (f"（如拉取沙箱镜像）；scan.log 尾部: {hint}" if hint else "；且 scan.log 无输出")
                )
            if time.monotonic() - last_event >= _HEARTBEAT_SILENCE:
                mins = int((time.monotonic() - started) // 60)
                tips = []
                if state["turn"]:
                    tips.append(f"{state['turn'][0]} 第 {state['turn'][1]} 轮")
                if state["llm_calls"]:
                    tips.append(f"已调用 LLM {state['llm_calls']} 次")
                if state["results"] > 0:
                    tips.append(f"已确认 {state['results']} 个问题")
                suffix = f"（{' · '.join(tips)}）" if tips else "（尚未出现引擎日志）" if current is None else ""
                log(f"[scan] 运行中… 已执行 {mins} 分钟{suffix}")
                stale = int(time.monotonic() - last_content)
                if current is not None and stale >= _HEARTBEAT_SILENCE:
                    # 引擎日志有文件但长时间无新内容：贴出最后一行帮助定位卡点
                    detail = re.sub(r"\S*strix_runs/\S+", "<run目录>", last_raw)[:200]
                    log(f"[scan][诊断] 引擎日志已 {stale} 秒无新内容，最后一行: {detail or '-'}")
                    where = state.get("last_action") or "引擎启动阶段"
                    log(f"[scan][诊断] 卡点推测: {where}；若持续无变化可检查容器与 LLM 网关连通性")
                last_event = time.monotonic()
        except Exception:  # noqa: BLE001 —— 监控线程任何异常都不能影响扫描本身
            time.sleep(1)
        stop.wait(_TAIL_INTERVAL)


def _display_cmd(cmd: list[str], work_dir: Path) -> str:
    """供日志展示的命令行：折叠长指令、缩短工作区路径（密钥在环境变量里，不在命令行）。"""
    parts: list[str] = []
    if cmd and cmd[0] == sys.executable and len(cmd) > 3 and cmd[1] == "-c":
        parts.append("strix")  # 引导段启动（技能注入）按 strix CLI 展示
        cmd = cmd[3:]  # 跳过 python / -c / 引导段代码三段，其后才是 strix 参数
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


def _run_resumable(work_dir: Path, run_name: str) -> bool:
    """run 目录是否可续扫：--resume 需要引擎已写出智能体拓扑（agents.json）。"""
    return bool(run_name) and (work_dir / "strix_runs" / run_name / "agents.json").is_file()


def _registered_findings(work_dir: Path, run_name: str) -> int:
    """run 目录已登记漏洞数（vulnerabilities.json；缺失/损坏按 0）。

    平台导入漏洞的唯一来源就是这份文件（read_run_artifacts 同源），
    “是否为 0”以它为准，而不是报告文本。
    """
    import json

    try:
        data = json.loads(
            (work_dir / "strix_runs" / run_name / "vulnerabilities.json").read_text(encoding="utf-8")
        )
        return len(data) if isinstance(data, list) else 0
    except (OSError, ValueError):
        return 0


def _kill_tree(proc: subprocess.Popen) -> None:
    """终止子进程及其全部子孙（strix CLI 自身会派生 docker 等子进程）。

    POSIX：Popen 以 start_new_session 启动，先 SIGTERM 进程组给 CLI 清理沙箱的
    机会，10 秒未退再 SIGKILL；Windows 无进程组信号，用 taskkill /F /T 强杀树。
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=30,
            )
        else:
            import signal

            pgid = os.getpgid(proc.pid)
            try:
                os.killpg(pgid, signal.SIGTERM)
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        try:
            proc.kill()  # 双保险：上面任何一步没杀掉时兜底
        except OSError:
            pass


def _remove_sandbox_containers(ids: list[str], log: Callable[[str], None]) -> None:
    """任务结束后回收本次 run 创建的沙箱容器（docker rm -f，尽力而为）。

    杀掉 CLI 进程树不会停掉 daemon 上已启动的容器（强杀 docker 客户端，容器照跑），
    取消/超时/转向/重试耗尽等强杀出口都会泄漏沙箱，故在 execute_scan 的收尾处显式
    移除；引擎正常退出已自行清理的容器按 NotFound 忽略。docker 不可用（如本机直跑
    无 sock）时静默跳过，残留容器交由兜底清扫回收。
    """
    if not ids:
        return
    try:
        import docker
        from docker.errors import NotFound
    except Exception:  # noqa: BLE001
        return
    try:
        client = docker.from_env(timeout=15)
    except Exception:  # noqa: BLE001
        return
    for cid in dict.fromkeys(ids):  # 去重保序：多路并行/续扫可能登记同一容器
        try:
            client.containers.get(cid).remove(force=True)
            log(f"[scan] 沙箱容器已回收（id {cid}）")
        except NotFound:
            pass
        except Exception as exc:  # noqa: BLE001 —— 单个容器失败不阻断收尾
            log(f"[scan][警告] 沙箱容器回收失败（id {cid}）: {exc}")
    client.close()


def execute_scan(
    work_dir: Path,
    src_dirs: list[Path],
    test_targets: list[dict],
    scan_mode: str,
    log: Callable[[str], None],
    model: str = "",
    instruction: str = "",
    llm_api_key: str = "",
    cancel_check: Callable[[], bool] | None = None,
    timeout_disabled: bool = False,
    scope_mode: str = "",
    diff_base: str = "",
    resume_run: str = "",
    spec_files: list[Path] | None = None,
    skills_dir: Path | None = None,
    steer_check: Callable[[], str] | None = None,
    on_sandbox_id: Callable[[str], None] | None = None,
) -> dict:
    """运行 strix，返回 {exit_code, timed_out, cancelled, run_dir_name, attempts, resume_used, sandbox_ids}。

    src_dirs 为白盒源码目录列表（多仓库任务每仓库一个目录）：strix 的 -t 可重复
    传多个目标，每个目录各占一个 -t，引擎为每个目标分配独立的 /workspace/<目录名>
    子目录并在根智能体任务里逐目标列出，漏洞的 target 字段可据此归属到仓库。
    test_targets 为黑盒目标列表 [{"url", "note"}]：每个地址各占一个 -t；
    note（地址作用说明）由调用方并入 instruction 注入。
    spec_files 为 API 规范文件列表（OpenAPI/Swagger/Postman/Apifox 导出）：逐个 -t。
    scope_mode/diff_base 透传 --scope-mode/--diff-base（diff 增量扫描）。
    resume_run 非空且 run 目录可恢复（agents.json 存在）时加 --resume 断点续扫；
    LLM 连接失败自动重试同样优先 --resume 从断点继续。
    steer_check 在引擎运行期间轮询，返回非空转向指令时：安全打断引擎 → 追加转向块到
    instruction → --resume 立即续扫（引擎把新指令注入根智能体会话），不占用重试次数；
    run 目录尚不可恢复时退化为带新指令的全量重启。skills_dir 非空时注入平台技能目录。
    cancel_check 返回 True 时终止进程树并放弃后续重试（结果 cancelled=True）。
    on_sandbox_id 在解析到沙箱容器创建时即时回调（跨线程，须自行保证可重入），
    供调用方在扫描进行中增量登记容器；返回值 sandbox_ids 为本次全部容器 ID。
    任务结束（含取消/超时/转向等强杀出口）时逐一 docker rm -f 回收这些容器。
    timeout_disabled=True 时不限时运行：跳过档位默认时长的超时回收（多仓库/大型
    项目扫描常超出档位上限），仅保留用户取消/转向通道。
    正常结束（退出码 0）但登记漏洞为 0 时追加一轮“0 漏洞核实轮”（strix_verify_zero_findings
    开关，默认开）：--resume 追问根智能体一次，补登记漏报的发现或说明空结果原因；
    核实轮固定 20 分钟上限、不占用重试次数，其取消/超时不改变主扫描的结果状态。
    """
    s = get_settings()
    work_dir.mkdir(parents=True, exist_ok=True)

    base_instruction = instruction
    steered_msgs: list[str] = []
    steered_restart = False
    resume_used = bool(resume_run)
    # 转向消息 → 追加指令块（引擎 resume 时注入根智能体；子智能体由根在任务派发时转达）
    def _instruction_now() -> str:
        if not steered_msgs:
            return base_instruction
        blocks = "\n\n".join(
            f"MID-SCAN USER STEERING (higher priority, apply from now on):\n{m}"
            for m in steered_msgs
        )
        return f"{base_instruction}\n\n{blocks}".strip()

    def _build_cmd() -> list[str]:
        cmd = [sys.executable, "-c", _BOOTSTRAP] if skills_dir is not None else ["strix"]
        cmd += ["-n"]
        if resume_run and _run_resumable(work_dir, resume_run):
            cmd += ["--resume", resume_run]
        for d in src_dirs:
            cmd += ["-t", str(d)]
        for f in spec_files or []:
            cmd += ["-t", str(f)]
        for t in test_targets:
            cmd += ["-t", t["url"]]
        cmd += ["-m", scan_mode]
        if scope_mode in ("auto", "diff", "full"):
            cmd += ["--scope-mode", scope_mode]
        if scope_mode == "diff" and diff_base.strip():
            cmd += ["--diff-base", diff_base.strip()]
        if instruction:
            cmd += ["--instruction", instruction]
        if s.strix_max_budget > 0:
            cmd += ["--max-budget", str(s.strix_max_budget)]
        if s.strix_extra_args.strip():
            cmd += shlex.split(s.strix_extra_args.strip())
        return cmd

    timeout_sec = {"quick": s.timeout_quick, "standard": s.timeout_standard, "deep": s.timeout_deep}.get(
        scan_mode, s.timeout_standard
    )
    env = _strix_env(model, llm_api_key, skills_dir)
    scan_log = work_dir / "scan.log"

    exit_code: int | None = None
    timed_out = False
    cancelled = False
    attempts = 0
    sandbox_ids: list[str] = []  # 本次任务历次 run 创建的沙箱容器，结束（含强杀出口）统一回收

    while True:
        if cancel_check and cancel_check():
            cancelled = True
            log("[scan] 检测到取消请求，停止执行")
            break
        if steered_restart:
            steered_restart = False  # 用户转向重启：不占用 LLM 失败重试次数
            if _run_resumable(work_dir, resume_run):
                log("[scan] 转向重启：从断点 --resume 继续（新指令将注入根智能体）")
            else:
                log("[scan] 转向重启：run 目录尚不可恢复，带新指令重新开始")
        else:
            if attempts >= s.max_scan_attempts:
                break
            attempts += 1
            log(
                f"[scan] 第 {attempts}/{s.max_scan_attempts} 次执行（"
                + ("不限时运行" if timeout_disabled else f"超时 {timeout_sec // 60} 分钟")
                + "）"
            )
        instruction = _instruction_now()
        cmd = _build_cmd()
        known = set(_list_runs(work_dir))
        log(f"[scan] 命令: {_display_cmd(cmd, work_dir)}")
        steered = False

        # tail 线程只在 proc 存活期间运行，wait 返回（含超时/取消/转向）即 join，
        # 保证 log 回调（写 DB）不与主线程并发
        stop_evt = threading.Event()
        mon = threading.Thread(
            target=_tail_strix_log, args=(work_dir, log, stop_evt, sandbox_ids, on_sandbox_id),
            daemon=True,
        )
        mon.start()
        try:
            with scan_log.open("ab") as out:
                out.write(f"\n===== attempt {attempts} {now_bj().strftime('%Y-%m-%dT%H:%M:%S')} =====\n".encode())
                # POSIX 下独立进程组：取消/超时可 killpg 整树而不伤 worker 自身
                proc = subprocess.Popen(
                    cmd, cwd=str(work_dir), stdout=out, stderr=subprocess.STDOUT, env=env,
                    start_new_session=(os.name != "nt"),
                )
                # 等待期间每 2 秒轮询一次取消/转向标记（跨进程经 Redis）；总时长仍受
                # timeout_sec 约束（timeout_disabled 时不限时，仅响应取消/转向），
                # 语义与原 proc.wait(timeout=...) 一致
                started = time.monotonic()
                while True:
                    try:
                        exit_code = proc.wait(timeout=2)
                        break
                    except subprocess.TimeoutExpired:
                        if cancel_check and cancel_check():
                            cancelled = True
                            log("[scan] 收到取消请求，正在终止引擎进程…")
                            _kill_tree(proc)
                            proc.wait(timeout=60)
                            exit_code = proc.returncode
                            break
                        steer_msg = (steer_check() or "").strip() if steer_check else ""
                        if steer_msg:
                            steered = True
                            steered_msgs.append(steer_msg[:2000])
                            log(f"[scan] 收到转向指令（{len(steer_msg)} 字），正在安全打断并应用…")
                            _kill_tree(proc)
                            proc.wait(timeout=60)
                            exit_code = proc.returncode
                            break
                        if not timeout_disabled and time.monotonic() - started >= timeout_sec:
                            _kill_tree(proc)
                            proc.wait(timeout=60)
                            exit_code = proc.returncode
                            timed_out = True
                            break
        finally:
            stop_evt.set()
            mon.join(timeout=10)

        # 定位本次 run 目录：优先新增目录，回退已知最新 run.json
        new = _list_runs(work_dir) - known
        if len(new) == 1:
            resume_run = new.pop()
        elif new:
            base = work_dir / "strix_runs"
            resume_run = max(new, key=lambda n: (base / n / "run.json").stat().st_mtime)
        resume_used = _run_resumable(work_dir, resume_run)

        if cancelled:
            log("[scan] 退出码 " + str(exit_code) + "（用户取消）")
            break
        if timed_out:
            log(f"[scan] 退出码 {exit_code}（超时被回收）")
            break
        if steered:
            steered_restart = True
            continue

        log(f"[scan] 退出码 {exit_code}")
        tail = ""
        try:
            tail = scan_log.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
        if exit_code == 1 and RETRY_MARKER in tail:
            if attempts >= s.max_scan_attempts:
                log("[scan] 已达最大重试次数，停止执行")
                break
            log("[scan] LLM 连接失败（网关 free 池间歇故障），30 秒后自动重试"
                + ("（--resume 从断点继续）" if resume_used else ""))
            for _ in range(30):  # 分段睡眠，重试等待期间仍响应取消
                if cancel_check and cancel_check():
                    cancelled = True
                    break
                time.sleep(1)
            if cancelled:
                log("[scan] 重试等待期间收到取消请求，停止执行")
                break
            continue
        break

    run_dir_name = resume_run if resume_run and resume_run in _list_runs(work_dir) else ""
    if run_dir_name:
        log(f"[scan] run 目录: {run_dir_name}")

    # ---- 0 漏洞核实轮：正常结束但登记漏洞为 0，--resume 追问根智能体一次 ----
    # 弱模型可能把发现只写进报告文本而不调登记工具（平台只导入 vulnerabilities.json），
    # 页面就显示 0。这轮让它补登记或确认空结果；只追问一次，不占用重试次数，
    # 核实轮的取消/超时不改变主扫描的结果状态（cancelled/timed_out/exit_code 保持原值）。
    if (
        s.strix_verify_zero_findings
        and not cancelled
        and not timed_out
        and exit_code == 0
        and run_dir_name
        and _run_resumable(work_dir, run_dir_name)
        and _registered_findings(work_dir, run_dir_name) == 0
    ):
        log("[scan] 扫描正常结束但登记漏洞为 0，追加核实轮：追问根智能体是否有漏登记的发现")
        instruction = f"{instruction}\n\n{_VERIFY_INSTRUCTION}".strip()
        cmd = _build_cmd()
        log(f"[scan] 命令: {_display_cmd(cmd, work_dir)}")
        stop_evt = threading.Event()
        mon = threading.Thread(
            target=_tail_strix_log, args=(work_dir, log, stop_evt, sandbox_ids, on_sandbox_id),
            daemon=True,
        )
        mon.start()
        verify_exit: int | None = None
        try:
            with scan_log.open("ab") as out:
                out.write(f"\n===== verify {now_bj().strftime('%Y-%m-%dT%H:%M:%S')} =====\n".encode())
                proc = subprocess.Popen(
                    cmd, cwd=str(work_dir), stdout=out, stderr=subprocess.STDOUT, env=env,
                    start_new_session=(os.name != "nt"),
                )
                started = time.monotonic()
                while True:
                    try:
                        verify_exit = proc.wait(timeout=2)
                        break
                    except subprocess.TimeoutExpired:
                        if cancel_check and cancel_check():
                            log("[scan] 核实轮收到取消请求，终止引擎进程")
                            _kill_tree(proc)
                            proc.wait(timeout=60)
                            break
                        if time.monotonic() - started >= _VERIFY_TIMEOUT_SEC:
                            log("[scan] 核实轮超时（20 分钟上限），终止引擎进程；主扫描结果不受影响")
                            _kill_tree(proc)
                            proc.wait(timeout=60)
                            break
        finally:
            stop_evt.set()
            mon.join(timeout=10)

        registered = _registered_findings(work_dir, run_dir_name)
        if registered > 0:
            log(f"[scan] 核实轮生效：智能体补登记了 {registered} 个漏洞，产物解析按新结果入库")
        else:
            log(
                f"[scan] 核实轮结束（退出码 {verify_exit}）：登记漏洞仍为 0，"
                "智能体的空结果说明见执行摘要报告与智能体执行记录"
            )

    # 沙箱回收必须放在 while 循环之后：取消/超时/转向/重试耗尽都经 break 走到这里，
    # 只有引擎进程本身的未捕获异常才绕过（残留容器由兜底清扫按 12 小时无主回收）
    _remove_sandbox_containers(sandbox_ids, log)

    return {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "run_dir_name": run_dir_name,
        "attempts": attempts,
        "resume_used": resume_used,
        "steered": len(steered_msgs),
        "sandbox_ids": list(sandbox_ids),
    }


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
