"""执行过程回放：解析 strix.log 生成事件时间线（任务详情「智能体」页回放播放）。

strix.log 行格式（strix 1.5.3 实测）：
  2026-08-24 10:32:30.982 INFO    oa-manage_a6cb - <logger>: <message>
时间戳是引擎容器本地时区的 naive 时间；以文件 mtime 与末条日志时间戳的差值
推断容器时区偏移后折算北京时间（与 tasks._strix_log_shift 同一算法）。

只保留对用户有信息量的事件，噪声行（Tracing/posthog/skill 加载/报表落盘等）丢弃。
事件类别（k）与优先级（回放预算不足时的抽稀顺序，数字越小越保留）：
  engine / agent / finding / warn / error —— 里程碑，永不抽稀
  tool       调用工具          优先级 1（按最近一次 current_agent 归属智能体）
  turn       智能体轮次心跳    优先级 2（数量最大）
LLM 调用不打事件点，仅进总数统计与密度直方图。

输出结构（JSON 缓存进 tasks.execution_events）：
{
  "run", "image", "model", "max_turns",
  "started_at": "ISO8601(+08:00)", "finished_at": "ISO8601(+08:00)",
  "duration_sec": n,
  "totals": {"turns","llm_calls","tool_calls","agents","findings"},
  "agents": [{"id","name","parent","parent_name","t0","t1","status"}],   # 秒数相对起点
  "bucket_sec": n, "activity": [[turns, tools, llm], ...],
  "events": [{"t","k","m"}]         # t=相对起点的秒数; k=类别; m=描述
}
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

_MAX_EVENTS = 6000        # 回放事件输出上限；超出按类别优先级均匀抽稀
_MSG_MAX = 240            # 单事件描述截断长度
_TARGET_BUCKETS = 300     # 密度直方图目标桶数

# 行头：<ts>.<ms> <LEVEL> <run-id> - <logger>: <msg>（run-id 可能为 "-"）
_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)?\s+(INFO|DEBUG|WARNING|ERROR)\s+\S+\s+-\s+(\S+):\s(.*)$"
)
_RE_ENGINE = re.compile(r"Starting Strix scan (\S+) \(image=([^,]+), max_turns=(\d+)")
_RE_MODEL = re.compile(r"LLM model resolved: (\S+)")
_RE_SANDBOX_CREATED = re.compile(r"Sandbox container created: id=(\w+)")
_RE_AGENT_REGISTER = re.compile(r"agent\.register (\w+) \((.+?)\) parent=(\S+)")
_RE_AGENT_SPAWN = re.compile(r"create_agent: spawned (\w+) \((.+?)\) parent=(\S+)")
_RE_AGENT_FINISH = re.compile(r"agent_finish: (\w+) success=(\w+) findings=(\d+)")
_RE_AGENT_STATUS = re.compile(r"agent\.status (\w+)=(\w+)")
_RE_TURN = re.compile(r"Starting turn (\d+), current_agent=(.*?)\s*$")
_RE_TOOL = re.compile(r"Invoking tool (\S+)")
_RE_VULN_SAVED = re.compile(r"Saved (\d+) new vulnerability report\(s\)")

# 抽稀优先级：里程碑类为 0 永不抽稀
_PR = {"engine": 0, "agent": 0, "finding": 0, "warn": 0, "error": 0, "tool": 1, "turn": 2}

# 判定噪声的小写子串：telemetry、tracing、每轮重复落盘提示、LiteLLM 计费探测
_NOISE_SUBSTRINGS = (
    "tracing is disabled", "posthog", "scarf", "litellm cost estimate",
    "essential scan data saved", "updated vulnerability index",
)

_UNIX_EPOCH = datetime(1970, 1, 1)


def _naive_to_epoch(ts: str) -> int:
    """'YYYY-MM-DD HH:MM:SS' 按 UTC 语义换算序数值（确定性，不受本机时区影响）。"""
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    return int((dt - _UNIX_EPOCH).total_seconds())


def _redact(msg: str) -> str:
    msg = re.sub(r"\S*?(strix_runs|workspaces)\S*", "<路径>", msg)
    return re.sub(r"\s+", " ", msg).strip()[:_MSG_MAX]


class _Parser:
    def __init__(self) -> None:
        self.agents: dict[str, dict] = {}
        self.timeline: list[tuple[int, str, str]] = []  # (raw_epoch, kind, 描述)
        self.llm_marks: list[int] = []
        self.turn_tool_marks: list[tuple[int, str]] = []  # 直方图全量口径打点
        self.cur_agent = ""  # 最近一次 current_agent（归属 tool 活动）
        self.counters = {"turns": 0, "llm_calls": 0, "tool_calls": 0}
        self.findings_cum = 0
        self.t0_raw: int | None = None
        self.run = ""
        self.image = ""
        self.model = ""
        self.max_turns = 0
        self._last_dedup_key: tuple[int, str, str] | None = None

    def _emit(self, ts: int, kind: str, text: str) -> None:
        if self.t0_raw is None:
            self.t0_raw = ts
        key = (ts // 2, kind, text)  # 2 秒窗口内同类同文去重（同一事件常由多组件各报一条）
        if key == self._last_dedup_key:
            return
        self._last_dedup_key = key
        self.timeline.append((ts, kind, text))

    def feed(self, line: str) -> None:
        m = _LINE_RE.match(line)
        if not m:
            return
        try:
            ts = _naive_to_epoch(m.group(1))
        except ValueError:
            return
        level, logger, msg = m.group(2), m.group(3), m.group(4).rstrip()
        low = msg.lower()
        if any(s in low for s in _NOISE_SUBSTRINGS):
            return

        if level in ("WARNING", "ERROR"):
            kind = "error" if level == "ERROR" else "warn"
            self._emit(ts, kind, f"{logger}: {_redact(msg)}")
            return

        if (mm := _RE_ENGINE.search(msg)):
            self.run, self.image, self.max_turns = mm.group(1), mm.group(2), int(mm.group(3))
            image_short = self.image.rsplit("/", 1)[-1]
            self._emit(ts, "engine", f"引擎启动：run {self.run}（镜像 {image_short}，最大轮次 {self.max_turns}）")
        elif (mm := _RE_MODEL.search(msg)):
            self.model = mm.group(1)
            self._emit(ts, "engine", f"模型解析: {self.model}")
        elif (mm := _RE_SANDBOX_CREATED.search(msg)):
            self._emit(ts, "engine", f"沙箱容器已创建（id {mm.group(1)[:8]}…）")
        elif "sandbox ready for scan" in low or ("sandbox session" in low and "ready and cached" in low):
            self._emit(ts, "engine", "沙箱就绪（Docker 沙箱 + Caido 代理）")
        elif (mm := _RE_AGENT_REGISTER.match(msg)) or (mm := _RE_AGENT_SPAWN.search(msg)):
            agent_id, name, parent = mm.group(1), mm.group(2), mm.group(3)
            e = self.agents.setdefault(agent_id, {"id": agent_id, "name": name})
            e["parent"] = parent
            e.setdefault("t0", ts)
            if parent == "-":
                self._emit(ts, "agent", f"根智能体启动: {name}")
            else:
                pname = self.agents.get(parent, {}).get("name", "")
                who = f"「{pname}」" if pname else parent[:8]
                self._emit(ts, "agent", f"派生专项智能体: {name}（由 {who} 创建）")
        elif (mm := _RE_AGENT_FINISH.search(msg)):
            e = self.agents.get(mm.group(1))
            n_findings = int(mm.group(3))
            if e:
                e["t1"] = max(e.get("t1") or 0, ts)
                e["status"] = "completed" if mm.group(2) == "True" else "failed"
                extra = f"，报告发现 {n_findings} 条" if n_findings > 0 else ""
                self._emit(ts, "agent", f"智能体收工: {e['name']}（{'成功' if mm.group(2) == 'True' else '失败'}{extra}）")
        elif (mm := _RE_AGENT_STATUS.search(msg)):
            st = mm.group(2)
            if st in ("completed", "failed"):
                e = self.agents.get(mm.group(1))
                name = e["name"] if e else mm.group(1)[:8]
                if e:
                    e["t1"] = max(e.get("t1") or 0, ts)
                    e["status"] = st
                self._emit(ts, "agent", f"智能体{'完成' if st == 'completed' else '失败'}: {name}")
            # waiting / running 为反复出现的中间态，不打点（注册与收工事件已覆盖边界）
        elif (mm := _RE_TURN.search(msg)):
            self.cur_agent = mm.group(2)
            self.counters["turns"] += 1
            self.turn_tool_marks.append((ts, "turn"))
            self._emit(ts, "turn", f"{self.cur_agent} 第 {mm.group(1)} 轮")
        elif (mm := _RE_TOOL.search(msg)):
            self.counters["tool_calls"] += 1
            self.turn_tool_marks.append((ts, "tool"))
            tool = mm.group(1)
            self._emit(ts, "tool", f"{self.cur_agent or '引擎'} 调用工具 {tool}")
            if tool == "finish_scan":
                self._emit(ts, "engine", "扫描收尾：汇总产物并生成最终报告")
        elif "Calling LLM" in msg and logger.startswith("openai.agents"):
            self.counters["llm_calls"] += 1
            self.llm_marks.append(ts)
        elif (mm := _RE_VULN_SAVED.search(msg)):
            self.findings_cum += int(mm.group(1))
            self._emit(ts, "finding", f"漏洞报告落盘（已确认 {self.findings_cum} 条）")


def _sample_to_budget(events: list[dict]) -> list[dict]:
    """事件超过上限时均匀抽稀：先丢轮次心跳，再丢工具调用；里程碑必留。"""
    if len(events) <= _MAX_EVENTS:
        return events
    idx = list(range(len(events)))
    milestone = [i for i, e in zip(idx, events) if _PR.get(e["k"], 2) == 0]
    rest1 = [i for i, e in zip(idx, events) if _PR.get(e["k"], 2) == 1]
    rest2 = [i for i, e in zip(idx, events) if _PR.get(e["k"], 2) == 2]

    def stride_pick(pool: list[int], budget: int) -> list[int]:
        if len(pool) <= budget:
            return pool
        s = math.ceil(len(pool) / max(1, budget))
        return pool[::s]

    chosen = list(milestone)
    budget = _MAX_EVENTS - len(chosen)
    take1 = stride_pick(rest1, max(budget, 0))
    chosen += take1
    budget -= len(take1)
    chosen += stride_pick(rest2, max(budget, 0))
    cs = set(chosen)
    return [e for i, e in enumerate(events) if i in cs]


def build_activity(marks: list[tuple[int, str]], llm_marks: list[int],
                   t0: int, duration: int, bucket_sec: int) -> list[list[int]]:
    """密度直方图（全量口径）：每个桶 [轮次数, 工具数, LLM 数]。"""
    nb = duration // bucket_sec + 2
    grid = [[0, 0, 0] for _ in range(nb)]
    col_of = {"turn": 0, "tool": 1}

    def bump(rel_ts: int, col: int) -> None:
        rel = max(0, rel_ts - t0)
        grid[min(rel // bucket_sec, nb - 1)][col] += 1

    for ts, kind in marks:
        c = col_of.get(kind)
        if c is not None:
            bump(ts, c)
    for ts in llm_marks:
        bump(ts, 2)
    return grid


def _infer_shift(last_naive_epoch: int, mtime_hint: float | None) -> timedelta:
    """日志本地时间戳 → 北京时间偏移推断（与 tasks._strix_log_shift 同算法）。

    日志 naive 时间按 UTC 解读后与文件 mtime 的差值取整到小时即容器时区偏移，
    再 +8 折算北京时间；无 mtime 可用时按 compose 默认 UTC 容器处理（净偏移 +8）。
    """
    if mtime_hint and last_naive_epoch:
        delta_hours = round((mtime_hint - last_naive_epoch) / 3600)
        delta_hours = max(-12, min(14, delta_hours))
        return timedelta(hours=delta_hours + 8)
    return timedelta(hours=8)


def parse_strix_timeline(log_text: str, mtime_hint: float | None = None) -> dict | None:
    """把 strix.log 全文解析为回放时间线；未识别到任何时间线行返回 None。

    mtime_hint：文件 unix mtime（磁盘文件传 stat().st_mtime；zip 成员传把
    ZipInfo.date_time 按 UTC 解读得到的伪 mtime），用于推断容器时区偏移。
    """
    parser = _Parser()
    last_line_raw = 0
    for line in log_text.splitlines():
        parser.feed(line)
        m = _LINE_RE.match(line)
        if m:
            try:
                last_line_raw = max(last_line_raw, _naive_to_epoch(m.group(1)))
            except ValueError:
                pass
    if parser.t0_raw is None:
        return None

    last_event_raw = parser.timeline[-1][0] if parser.timeline else parser.t0_raw
    last_raw = max(last_event_raw, last_line_raw)
    duration = max(1, last_raw - parser.t0_raw)
    shift = _infer_shift(last_line_raw, mtime_hint)

    started_bj = (_UNIX_EPOCH + timedelta(seconds=parser.t0_raw) + shift).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    finished_bj = (_UNIX_EPOCH + timedelta(seconds=last_raw) + shift).strftime("%Y-%m-%dT%H:%M:%S+08:00")

    agents_out = []
    ordered_ids = sorted(
        parser.agents.items(),
        key=lambda kv: (kv[1].get("t0") or 0, kv[1]["name"]),
    )
    for aid, a in ordered_ids:
        pid = a.get("parent", "")
        pname = next((v["name"] for k, v in ordered_ids if k == pid), "")
        agents_out.append({
            "id": aid,
            "name": a["name"],
            "parent": pid,
            "parent_name": pname,
            "t0": max(0, (a.get("t0") or parser.t0_raw) - parser.t0_raw),
            "t1": ((a.get("t1") or last_raw) - parser.t0_raw) if a.get("t1") else None,
            "status": a.get("status", ""),
        })

    events_rel = sorted(
        (
            {"t": max(0, ts - parser.t0_raw), "k": kind, "m": msg}
            for ts, kind, msg in parser.timeline
        ),
        key=lambda e: e["t"],
    )
    events_rel = _sample_to_budget(events_rel)

    bucket_sec = max(15, math.ceil(duration / _TARGET_BUCKETS))
    activity = build_activity(parser.turn_tool_marks, parser.llm_marks, parser.t0_raw, duration, bucket_sec)

    return {
        "run": parser.run,
        "image": parser.image.rsplit("/", 1)[-1],
        "model": parser.model,
        "max_turns": parser.max_turns,
        "started_at": started_bj,
        "finished_at": finished_bj,
        "duration_sec": duration,
        "totals": {
            **parser.counters,
            "agents": len(agents_out),
            "findings": parser.findings_cum,
        },
        "agents": agents_out,
        "bucket_sec": bucket_sec,
        "activity": activity,
        "events": events_rel,
    }


def zip_member_strix_log(zf, prefer_run_dir: str = "") -> tuple[str, float] | None:
    """从已打开的 zip 中读取 strix_runs/*/strix.log，返回 (全文, 伪mtime)；缺失返回 None。

    prefer_run_dir 为任务记录的 run 目录名（匹配则优先精确命中）；
    否则取内嵌时间最新的一个。
    """
    members = zf.namelist()
    candidates = [n for n in members if n.startswith("strix_runs/") and n.endswith("/strix.log")]
    if not candidates:
        candidates = [n for n in members if n.endswith("/strix.log")]
    if not candidates:
        return None

    def pseudo_mtime(name: str) -> float:
        info = zf.getinfo(name)
        try:
            return datetime(*info.date_time).replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, TypeError):
            return 0.0

    exact = f"strix_runs/{prefer_run_dir}/strix.log" if prefer_run_dir else ""
    pick = exact if exact in candidates else max(candidates, key=pseudo_mtime)
    try:
        import io

        with zf.open(pick) as fh:
            content = io.TextIOWrapper(fh, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    return content, pseudo_mtime(pick)
