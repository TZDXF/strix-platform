"""执行步骤转录（transcript）：解析 run 目录的 .state/agents.db + agents.json，
构建与 strix view 一致的智能体执行步骤事件流（chat / tool 两类事件）。

数据来源（strix 1.5.3，strix/interface/tui/live_view.py + history.py 同源）：
  run_dir/.state/agents.json   智能体拓扑 {statuses, names, parent_of, errors}
  run_dir/.state/agents.db     SQLite `agent_messages(id, session_id, message_data, created_at)`
  run_dir/run.json             user_instruction（根智能体的开场用户消息）+ start_time

message_data 为 OpenAI Agents SDK 会话条目：
  {"type":"message","role":"user|assistant","content":[...]}   → chat 事件
  {"type":"function_call","call_id","name","arguments"}        → tool 事件（调用）
  {"type":"function_call_output","call_id","output"}           → 并回对应 tool 事件（结果）

与 viewer 一致的取舍：
  各智能体的首条 user 消息是其启动任务（非用户输入），不进转录；系统注入的
  内部指令前缀同样过滤；run.json 的 user_instruction 作为根智能体开场消息补入。

输出结构（JSON 缓存进 tasks.transcript，所有 ts 为绝对 epoch 秒）：
{
  "agents": [{"id","name","parent_id","status"}],
  "events": [{"ts","type":"chat|tool","agent","d"}],
  "counts": {agent_id: {"chat": n, "tool": n, "total": n}},   # all 之外的汇总键 "all"
  "started_at": 首事件 epoch 秒, "truncated": bool,
}
chat.d = {"role","content"}；tool.d = {"tool_name","args","status","result"}
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

STATE_DIR = ".state"
AGENTS_JSON = "agents.json"
AGENTS_DB = "agents.db"

_EVENT_CAP = 6000        # 事件总数上限，超出按步长抽稀（长任务保护前端与存储）
_CHAT_MAX = 6000         # 单条消息截断
_RESULT_MAX = 4000       # 单条工具输出截断
_ARGS_MAX = 2500         # 单条工具参数（JSON 文本）截断

# 系统注入的用户轮次前缀（strix live_view._INTERNAL_TURN_PREFIXES 同源），非用户真实输入
_INTERNAL_TURN_PREFIXES = (
    "[Message from ",
    "== Inherited context from parent",
    "Your previous message ended a turn without a tool call.",
    "Your previous response ended the autonomous run without a lifecycle tool call.",
    "[NOTICE] Turn budget:",
    "[URGENT] Turn budget:",
    "[CRITICAL] Turn budget:",
    "[NOTICE] Scan cost budget:",
    "[URGENT] Scan cost budget:",
    "[CRITICAL] Scan cost budget:",
)


def _is_internal_turn(content: str) -> bool:
    return content.lstrip().startswith(_INTERNAL_TURN_PREFIXES)


def _parse_json(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def _msg_text(content) -> str:
    parts = []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "".join(parts)


def _iso_to_epoch(ts: str | None) -> float:
    """created_at ISO（含/不含时区）→ epoch 秒；解析失败返回 0。"""
    if not isinstance(ts, str) or not ts.strip():
        return 0.0
    try:
        dt = datetime.fromisoformat(ts.strip())
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _load_topology(run_dir: Path) -> dict[str, dict]:
    try:
        data = json.loads((run_dir / STATE_DIR / AGENTS_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    statuses = data.get("statuses") or {}
    names = data.get("names") or {}
    parents = data.get("parent_of") or {}
    if not isinstance(statuses, dict):
        return {}
    agents: dict[str, dict] = {}
    for aid, status in statuses.items():
        if not isinstance(aid, str):
            continue
        agents[aid] = {
            "id": aid,
            "name": names.get(aid, aid) if isinstance(names, dict) else aid,
            "parent_id": parents.get(aid) if isinstance(parents, dict) else None,
            "status": str(status or ""),
        }
    return agents


def _load_session_items(run_dir: Path) -> list[tuple[str, dict, str]]:
    """读取 agents.db 全部消息行（只读模式，兼容运行中 WAL 并发写），按 id 升序。"""
    db_path = run_dir / STATE_DIR / AGENTS_DB
    if not db_path.is_file():
        return []
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        rows = conn.execute(
            "select session_id, message_data, created_at from agent_messages order by id"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if conn is not None:
            conn.close()
    items: list[tuple[str, dict, str]] = []
    for session_id, message_data, created_at in rows:
        try:
            item = json.loads(message_data)
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict):
            items.append((str(session_id), item, created_at if isinstance(created_at, str) else ""))
    return items


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f" …（截断，共 {len(text)} 字）"


def _result_status(result) -> str:
    if isinstance(result, dict) and result.get("success") is False:
        return "failed"
    return "completed"


def build_transcript(run_dir: Path) -> dict | None:
    """把 run 目录解析为转录结构；缺少 agents.json 且无消息行时返回 None。"""
    agents = _load_topology(run_dir)
    if not agents:
        return None

    run_record: dict = {}
    try:
        loaded = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            run_record = loaded
    except (OSError, ValueError):
        pass

    root_id = next((aid for aid, a in agents.items() if not a.get("parent_id")), None)
    events: list[dict] = []

    # 开场用户消息：run.json 的 user_instruction（与 viewer 一致，置于根智能体名下）
    instruction = run_record.get("user_instruction")
    start_ts = _iso_to_epoch(run_record.get("start_time"))
    if isinstance(instruction, str) and instruction.strip() and root_id:
        events.append({
            "ts": start_ts, "type": "chat", "agent": root_id,
            "d": {"role": "user", "content": _clip(instruction, _CHAT_MAX)},
        })

    tasked: set[str] = set()
    # (agent_id, call_id) → tool 事件下标；call 先挂 running，output 并回结果
    open_tools: dict[tuple[str, str], int] = {}
    for agent_id, item, created_at in _load_session_items(run_dir):
        if agent_id not in agents:
            continue
        ts = _iso_to_epoch(created_at)
        item_type = item.get("type")
        role = item.get("role")
        if role in ("user", "assistant") and item_type in (None, "message"):
            content = _msg_text(item.get("content", ""))
            if not content:
                continue
            if role == "user":
                # 首条 user 轮次是启动任务；内部指令前缀非用户输入——两者都不进转录
                first = agent_id not in tasked
                tasked.add(agent_id)
                if first or _is_internal_turn(content):
                    continue
            events.append({
                "ts": ts, "type": "chat", "agent": agent_id,
                "d": {"role": role, "content": _clip(content, _CHAT_MAX)},
            })
        elif item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            args = _parse_json(item.get("arguments"))
            events.append({
                "ts": ts, "type": "tool", "agent": agent_id,
                "d": {
                    "tool_name": str(item.get("name") or "tool"),
                    "args": _clip(json.dumps(args, ensure_ascii=False) if args is not None else "", _ARGS_MAX),
                    "status": "running",
                    "result": "",
                },
            })
            if call_id:
                open_tools[(agent_id, call_id)] = len(events) - 1
        elif item_type == "function_call_output":
            call_id = str(item.get("call_id") or item.get("id") or "")
            result = _parse_json(item.get("output"))
            result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            status = _result_status(result)
            idx = open_tools.get((agent_id, call_id))
            if idx is not None:
                events[idx]["d"]["result"] = _clip(result_text or "", _RESULT_MAX)
                events[idx]["d"]["status"] = status
                if ts and not events[idx]["ts"]:
                    events[idx]["ts"] = ts
            else:
                # 只有输出没有调用记录（历史截断等）：仍保留一条结果事件
                events.append({
                    "ts": ts, "type": "tool", "agent": agent_id,
                    "d": {
                        "tool_name": "tool", "args": "", "status": status,
                        "result": _clip(result_text or "", _RESULT_MAX),
                    },
                })

    # ts 补齐：缺时间戳的行沿用前一条事件时间，保证单调不减
    last = start_ts or 0.0
    for e in events:
        if not e["ts"]:
            e["ts"] = last
        last = max(last, e["ts"])
    events.sort(key=lambda e: e["ts"])

    truncated = False
    if len(events) > _EVENT_CAP:
        stride = -(-len(events) // _EVENT_CAP)
        events = events[::stride]
        truncated = True

    counts: dict[str, dict[str, int]] = {}
    for e in events:
        c = counts.setdefault(e["agent"], {"chat": 0, "tool": 0, "total": 0})
        c[e["type"]] += 1
        c["total"] += 1
    counts["all"] = {
        "chat": sum(c["chat"] for k, c in counts.items() if k != "all"),
        "tool": sum(c["tool"] for k, c in counts.items() if k != "all"),
        "total": len(events),
    }

    ordered_agents = sorted(
        agents.values(),
        key=lambda a: (a["parent_id"] is not None, next(
            (e["ts"] for e in events if e["agent"] == a["id"]), float("inf"),
        ), a["name"]),
    )
    if not events:
        return None
    return {
        "agents": ordered_agents,
        "events": events,
        "counts": counts,
        "started_at": events[0]["ts"],
        "truncated": truncated,
    }


def extract_run_dir_from_zip(zf: zipfile.ZipFile, prefer_run_dir: str = "") -> Path | None:
    """从产物 zip 中抽取 run.json + .state/*（含 WAL/SHM）到临时目录，返回该目录。

    prefer_run_dir 为任务记录的 run 目录名，存在对应成员时精确命中；
    否则取第一个含 .state/agents.json 的成员前缀。找不到返回 None。
    """
    members = zf.namelist()
    prefixes = {
        m[: -len(f"/{STATE_DIR}/{AGENTS_JSON}")]
        for m in members
        if m.endswith(f"/{STATE_DIR}/{AGENTS_JSON}")
    }
    if not prefixes:
        return None
    prefix = f"{prefer_run_dir}/" if prefer_run_dir in prefixes else f"{sorted(prefixes)[0]}/"

    wanted = {"run.json", f"{STATE_DIR}/{AGENTS_JSON}", f"{STATE_DIR}/{AGENTS_DB}"}
    tmp = Path(tempfile.mkdtemp(prefix="strix-transcript-"))
    for member in members:
        if not member.startswith(prefix):
            continue
        rel = member[len(prefix):]
        # WAL/SHM 与库文件同名带后缀（agents.db-wal / -shm）：一并抽出保证读到完整数据
        if rel not in wanted and not rel.startswith(f"{STATE_DIR}/{AGENTS_DB}-"):
            continue
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zf.open(member) as src, dest.open("wb") as out:
                out.write(src.read())
        except (OSError, zipfile.BadZipFile):
            continue
    return tmp
