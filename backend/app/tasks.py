"""扫描任务流水线：获取源码（项目/分支/凭据） → strix 执行 → 产物解析入库 → 归档 → 中文翻译。"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from celery.signals import worker_ready as celery_worker_ready
from sqlalchemy import delete, func, select

from .artifacts import archive_run
from .cancel import (
    HB_TOUCH_INTERVAL,
    cancel_requested,
    clear_cancel,
    clear_steer,
    has_heartbeat,
    pop_steer,
    touch_heartbeat,
)
from .celery_app import WORKER_POOL_SIZE, celery_app
from .config import get_settings
from .db import SessionLocal
from .llm_models import default_model
from .mailer import notify_task_finished
from .models import Finding, GitConfig, Project, ProjectUpload, SystemSetting, Task, User
from .runner import execute_scan, read_run_artifacts
from .skills import materialize_skills, web_search_pointer
from .sources import (    SourceError,
    clone_git,
    du_mb,
    effective_repo_branches,
    effective_repos,
    load_repo_tokens,
    repo_credential,
    repo_dir_name,
    safe_extract_zip,
)
from .targets import effective_targets
from .tasklog import append_log as _log
from .transcript import build_transcript

ZH_INSTRUCTION = (
    "Please write the entire report in Simplified Chinese (简体中文): "
    "vulnerability titles, descriptions, PoC explanations and remediation steps. "
    "Keep technical identifiers (CVE/CWE, code, endpoints) unchanged."
)

LOCAL_RANGE_GUIDE = (
    "Black-box validation scope note: this scan has NO externally provided black-box test "
    "targets, so no live environment is in scope and NO traffic may leave the sandbox. "
    "To validate findings empirically you MAY build and run the application locally INSIDE "
    "your sandbox container, provision its supporting database yourself and seed it with "
    "SQL/data bundled in the repository or synthetic values you create. Constraints: "
    "(1) send requests only to loopback addresses of your own sandbox (127.0.0.1/localhost); "
    "if any other host is somehow reachable from the sandbox, do NOT probe or attack it; "
    "(2) clean up temporary rows you inserted once validation completes; "
    "(3) when reporting a finding validated this way, set its target to the actual local "
    "base URL you tested (e.g., http://127.0.0.1:<port>/<context>) so report readers can "
    "tell verification ran against your self-provisioned local instance, not a real system."
)


def web_search_guide(mcp_url: str) -> str:
    """（兼容保留）联网搜索调用指南文本——已迁移为技能库种子技能的正文载体。

    见 skills.build_web_search_content：任务勾选联网搜索时，指令里只注入
    web_search_pointer() 的一行加载指引，具体调用方法在技能正文里，可由
    超管在「技能库」查看和编辑。
    """
    from .skills import build_web_search_content

    return build_web_search_content(mcp_url)


def _heartbeat_loop(task_id: str, stop: threading.Event) -> None:
    """执行心跳线程：任务执行期间周期刷新 Redis 心跳，向取消接口与定时回收证明
    「该任务有活着的执行者」。worker 意外死亡（如容器重启）后标记随 TTL 消失，
    僵尸任务因此可被识别并直接收尾，而不用等消息重投递（broker 默认 1 小时）。
    """
    touch_heartbeat(task_id)
    while not stop.wait(HB_TOUCH_INTERVAL):
        touch_heartbeat(task_id)


def _parse_api_specs(raw) -> list[dict]:
    """解析任务的 API 规范目标 JSON（[{"name","stored_path"}]，路径相对 workspace）；坏数据返回 []。"""
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for it in raw:
        if isinstance(it, dict) and it.get("name") and it.get("stored_path"):
            out.append({"name": str(it["name"]), "stored_path": str(it["stored_path"])})
    return out


def _set_status(db, task, status: str) -> None:
    task.status = status
    task.updated_at = datetime.now(timezone.utc)
    db.commit()


def _remember_sandbox_container(task_id: str, container_id: str) -> None:
    """扫描运行期间增量登记沙箱容器 ID（runner 尾随线程回调，跨线程用独立短会话）。

    兜底清扫要区分「运行中任务的容器」（绝不回收）与孤儿容器；worker 中途崩溃时
    任务收尾代码不会执行，只有边创建边登记才保证运行中超长扫描（timeout_disabled
    不限时）的容器不因扫描时长超过清扫阈值而被误杀。
    """
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        ids = json.loads(task.sandbox_container_ids or "[]")
        if container_id not in ids:
            ids.append(container_id)
            task.sandbox_container_ids = json.dumps(ids[-32:], ensure_ascii=False)
            db.commit()
    except Exception:  # noqa: BLE001 —— 登记失败不影响扫描
        db.rollback()
    finally:
        db.close()


def finish_as_cancelled(db, task, task_id: str, detail: str = "", clear_marks: bool = True) -> dict:
    """任务以「已取消」收尾：终态时间/时长/日志，不解析产物、不翻译、不发提醒邮件。

    clear_marks=False 供 API/定时回收直接落终态的场景：保留取消标记，防与 worker
    侧的准入检查、消息重投递竞争（见 main.cancel_task 与 schedules.reclaim_zombie_tasks）。
    """
    if clear_marks:
        clear_cancel(task_id)
        clear_steer(task_id)
    task.error = ""
    task.finished_at = datetime.now(timezone.utc)
    if task.started_at:
        task.duration_sec = int((task.finished_at - task.started_at).total_seconds())
    _log(task, f"[cancel] 任务已取消{('：' + detail) if detail else ''}，耗时 {task.duration_sec or 0} 秒")
    _set_status(db, task, "cancelled")
    return {"task_id": task_id, "cancelled": True}


# 这些情况属正常跳过（未配置/无邮箱/开关关闭），不写进任务日志
_NOTIFY_QUIET = {"邮件未配置", "创建者未设置通知邮箱", "完成通知已关闭", "失败通知已关闭"}


def _notify(db, task, task_id: str) -> None:
    """任务终态后向创建者发送提醒邮件；结果写进任务日志，发送失败不影响任务状态。"""
    try:
        result = notify_task_finished(task_id)
    except Exception as exc:  # noqa: BLE001 —— 双保险，notify 内部已兜底
        result = {"sent": False, "reason": str(exc)}
    if result.get("sent"):
        _log(task, f"[mail] 已发送{'失败' if task.status == 'failed' else '完成'}提醒至 {result.get('to')}")
    elif result.get("reason") not in _NOTIFY_QUIET:
        _log(task, f"[mail] 提醒邮件未发送: {result.get('reason')}")
    db.commit()


def _strix_log_shift(log_text: str, log_path: Path) -> timedelta:
    """推断 strix.log 本地时间戳 → 北京时间的偏移。

    strix 按容器本地时间写日志（compose 默认 UTC），文件 mtime 是绝对时间，
    两者差值取整到小时即容器时区偏移，再 +8 折算成北京时间；容器若已设
    TZ=Asia/Shanghai，差值为 -8，净偏移为 0，同样正确。
    """
    last = ""
    for line in reversed(log_text.splitlines()):
        if len(line) >= 19 and line[10] == " ":
            last = line[:19]
            break
    try:
        naive = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        delta_hours = round((log_path.stat().st_mtime - naive.timestamp()) / 3600)
        delta_hours = max(-12, min(14, delta_hours))
        return timedelta(hours=delta_hours + 8)
    except (ValueError, OSError):
        return timedelta(hours=8)


def _ts_bj(ts: str, shift: timedelta) -> str:
    try:
        return (datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S") + shift).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts


def _agents_usage(run_record: dict, scan_dir: Path, run_dir_name: str) -> str:
    """合并 run.json 的智能体用量（tokens/请求）与 strix.log 的生命周期事件（启动/完成时间）。

    strix.log 行样例：
      2026-08-14 16:30:52.867 INFO  run - strix.core.agents: agent.register b2383e25 (Root Agent) parent=-
      2026-08-14 17:08:47.265 INFO  run - strix.core.agents: agent.status 0d0065a0=completed
    """
    agents: dict[str, dict] = {}

    def _entry(agent_id: str) -> dict:
        if agent_id not in agents:
            agents[agent_id] = {
                "agent_id": agent_id, "agent_name": "", "model": "", "parent": "",
                "requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                "started_at": "", "finished_at": "", "status": "",
            }
        return agents[agent_id]

    for a in (run_record.get("llm_usage") or {}).get("agents") or []:
        e = _entry(str(a.get("agent_id") or ""))
        e.update({
            "agent_name": str(a.get("agent_name") or ""),
            "model": str(a.get("model") or ""),
            "requests": int(a.get("requests") or 0),
            "input_tokens": int(a.get("input_tokens") or 0),
            "output_tokens": int(a.get("output_tokens") or 0),
            "total_tokens": int(a.get("total_tokens") or 0),
        })

    log_path = scan_dir / "strix_runs" / run_dir_name / "strix.log"
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    # strix.log 里的时间是引擎容器本地时间，换算成北京时间再展示
    shift = _strix_log_shift(log_text, log_path)
    for line in log_text.splitlines():
        ts = line[:23].strip()
        m = re.search(r"agent\.register (\w+) \((.+?)\) parent=(\S+)", line)
        if m:
            e = _entry(m.group(1))
            if not e["agent_name"]:
                e["agent_name"] = m.group(2)
            e["parent"] = m.group(3)
            if ts:
                e["started_at"] = _ts_bj(ts, shift)
            continue
        m = re.search(r"agent\.status (\w+)=(\w+)", line)
        if m:
            e = _entry(m.group(1))
            e["status"] = m.group(2)
            if m.group(2) in ("completed", "failed") and ts:
                e["finished_at"] = _ts_bj(ts, shift)

    ordered = sorted(
        agents.values(),
        key=lambda a: (a["started_at"] or "9999", a["agent_name"]),
    )
    return json.dumps(ordered, ensure_ascii=False)


def _scan_error_detail(scan_dir: Path) -> str:
    """提取 scan.log 中 strix 的报错行（"Error during penetration test: ..."），用于失败原因展示。"""
    try:
        text = (scan_dir / "scan.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in reversed(text.splitlines()):
        if "Error during penetration test:" in line:
            return line.split("Error during penetration test:", 1)[1].strip()[:300]
    return ""


# 沙箱执行通道故障判定阈值：引擎日志里 ExecTransportError 出现次数。
# 容器在扫描中被外部删除/沙箱损坏时，智能体每次 exec_command 都失败（事故中
# 重试 110+ 次），引擎最终"正常完成"却 0 发现——环境故障伪装成扫描完成。
# 超过阈值且登记漏洞为 0 时按执行失败处理，而不是"无漏洞完成"。
_EXEC_ERROR_THRESHOLD = 20


def _exec_channel_error_count(scan_dir: Path, run_dir_name: str) -> int:
    """统计本次 run 的引擎日志里沙箱命令执行失败（ExecTransportError）次数。"""
    if not run_dir_name:
        return 0
    try:
        text = (scan_dir / "strix_runs" / run_dir_name / "strix.log").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return 0
    return text.count("ExecTransportError")


# ---- 并行扫描闸门 ----

K_SCAN_CONCURRENCY = "scan_concurrency"  # system_settings 键：最大并行扫描数
SCAN_CONCURRENCY_DEFAULT = 2             # 未配置时的默认并行扫描数
_ACTIVE_SCAN_STATUSES = ("fetching", "scanning", "parsing")
# 单 worker 内并发线程取任务的准入串行化；多 worker 副本间仅由 DB 计数兜底，可能偶发超 1
_SCAN_ADMISSION_LOCK = threading.Lock()


def get_scan_concurrency(db) -> int:
    """最大并行扫描数（超管在系统设置页维护；每次准入现查，改动即时生效无需重启）。"""
    row = db.get(SystemSetting, K_SCAN_CONCURRENCY)
    try:
        value = int((row.value or "").strip()) if row is not None else 0
    except ValueError:
        value = 0
    if value <= 0:
        return SCAN_CONCURRENCY_DEFAULT
    return min(WORKER_POOL_SIZE, max(1, value))


def set_scan_concurrency(db, value: int, user_id: int | None) -> None:
    row = db.get(SystemSetting, K_SCAN_CONCURRENCY)
    if row is None:
        row = SystemSetting(key=K_SCAN_CONCURRENCY, value=str(value), updated_by=user_id)
        db.add(row)
    else:
        row.value = str(value)
        row.updated_by = user_id
    db.commit()


def _admit_scan_slot(db, task) -> str:
    """并行扫描闸门，三态返回：
    - "admitted"：本次调用占位成功（pending → fetching），调用方即是执行者；
    - "active"：任务已处运行态（重复投递、消息重投递或断点接手），是否放行由
      调用方结合心跳重入防护决定；
    - "full"：槽位满，任务保持 pending 排队，由 beat 每分钟重投递等空位
      （redispatch_pending_tasks）。
    """
    limit = get_scan_concurrency(db)
    with _SCAN_ADMISSION_LOCK:
        if task.status in _ACTIVE_SCAN_STATUSES:
            return "active"
        active = db.execute(
            select(func.count(Task.id)).where(
                Task.status.in_(_ACTIVE_SCAN_STATUSES), Task.id != task.id,
            )
        ).scalar() or 0
        if active >= limit:
            wait_line = f"[queue] 并行扫描槽位已满（{active}/{limit}），任务继续排队，稍后自动重试"
            if not (task.log or "").rstrip().endswith(wait_line):  # 末行相同不重复记，避免刷屏
                _log(task, wait_line)
                db.commit()
            return "full"
        # 占位瞬间先触达心跳再置运行态：并发的重复投递在心跳重入防护处必然观察到
        # 「已有执行者」，不会有两套执行流水线同时开跑的窗口。调用方凭 "admitted"
        # 返回值知道自己刚触达过心跳，不得再拿它当「他人存在」的证据
        touch_heartbeat(task.id)
        task.started_at = datetime.now(timezone.utc)
        _set_status(db, task, "fetching")  # 占位即置运行态，其他准入线程的计数立即可见
        return "admitted"


@celery_app.task(name="run_scan", bind=True, max_retries=1)
def run_scan(self, task_id: str) -> dict:
    s = get_settings()
    db = SessionLocal()
    task = db.get(Task, task_id)
    if task is None:
        db.close()
        return {"error": "task not found"}
    if task.status in ("done", "failed", "cancelled"):
        db.close()  # 终态任务重派发（如取消后到达的旧消息）：直接忽略
        return {"task_id": task_id, "skipped": task.status}
    if cancel_requested(task_id):
        # 排队期间（pending）被取消：还没动过任何资源，直接落终态
        result = finish_as_cancelled(db, task, task_id, "任务在排队等待期间被取消")
        db.close()
        return result
    admitted = _admit_scan_slot(db, task)
    if admitted == "full":
        # 槽位满：状态保持 pending、不占 worker 线程。不在这里自排队（消息链会随
        # 卡队时长无限累积，且任何一条丢失就没人再驱动），统一由 beat 每分钟的
        # redispatch_pending_tasks 重新投递，最多 1 分钟后自然重试
        db.close()
        return {"task_id": task_id, "queued": True}
    if admitted == "active" and has_heartbeat(task_id):
        # 重入防护：任务已处运行态且心跳还在（他人占位时触达或其心跳线程在刷新），
        # 本次必是重复投递（broker 重投递/重复派发）。放行会与原执行者叠加：Stage 1
        # 清掉对方正在扫描的源码、收尾时误删对方沙箱容器（运行中容器被删 → 引擎
        # 404 崩溃）。不写任务日志（避免与原执行者并发写同一行）；任务状态由原执行
        # 者继续推进。"admitted"（刚占位成功）不走这里——闸门刚触达的心跳是自己的，
        # 拿它当「他人存在」会自己跳过自己，任务永远停在运行态无人执行；心跳已
        # 消失的运行态（如 worker 崩溃后的消息重投递）由本次调用接手执行。
        print(f"[run_scan] {task_id} 已有活跃执行者（心跳存在），跳过本次重复投递", flush=True)
        db.close()
        return {"task_id": task_id, "skipped": "duplicate_delivery"}
    clear_steer(task_id)  # 防御：清理上次运行未消费的转向标记，避免新任务被旧指令误转向
    # 执行心跳：占槽成功即启动，全程覆盖取源码/扫描/解析各阶段，finally 统一停止
    hb_stop = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop, args=(task_id, hb_stop), name=f"hb-{task_id[:8]}", daemon=True
    )
    hb_thread.start()

    project = db.get(Project, task.project_id) if task.project_id else None
    creator = db.get(User, task.created_by) if task.created_by else None
    user_llm_key = (creator.llm_api_key or "") if creator else ""
    if not user_llm_key:
        task.error = "创建者未配置个人 AI 密钥，无法执行扫描；请先在「设置」中配置后再提交任务"
        _log(task, f"[fail] {task.error}")
        task.finished_at = datetime.now(timezone.utc)
        _set_status(db, task, "failed")
        _notify(db, task, task_id)
        db.close()
        return {"task_id": task_id, "error": task.error}

    ws = Path(s.workspace_root)
    task_ws = ws / task_id
    src_dir = task_ws / "src"
    scan_dir = task_ws / "scan"
    artifacts_dir = ws / "artifacts"
    task.model = task.model or default_model(db)  # 平台默认模型（platform_models 表，超管维护）
    task.strix_version = s.strix_version
    resume_run = ""
    if task.resume_count and task.resume_count > 0 and task.run_dir_name:
        # 手动续扫：以任务记录的 run 目录为断点（run 目录被清理时 execute_scan 会自动退化为重跑）。
        # 不预先清空上一轮的转录/回放缓存：收尾成功会按新日志重解析覆盖，失败则保留旧值
        #（否则续扫失败会把上一轮成功的执行过程一起毁掉）。
        resume_run = task.run_dir_name
        _log(task, f"[task] 第 {task.resume_count} 次续扫：从 run「{resume_run}」断点恢复（--resume）")
    task.attempts = 0

    try:
        # 状态与 started_at 已在准入闸门（_admit_scan_slot）置位
        repo_refs = effective_repo_branches(task.repo_branches, task.source_ref, task.branch or "")
        scope_mode = (task.scope_mode or "").strip()
        diff_base = (task.diff_base or "").strip()
        full_history = scope_mode == "diff"  # diff 增量需要基线分支历史，浅克隆解析不了
        if scope_mode == "diff":
            _log(task, f"[scan] diff 增量扫描：范围 {scope_mode}，基线 {diff_base or '仓库默认分支'}")
        src_desc = (
            f"git（{len(repo_refs)} 个仓库，分支 {'/'.join(r['branch'] or '默认' for r in repo_refs)}）"
            if task.source_type == "git" and len(repo_refs) != 1
            else f"git（分支 {repo_refs[0]['branch'] or '默认'}）" if task.source_type == "git" else "zip 上传"
        )
        _log(task, f"[task] 任务开始：{src_desc} · {task.scan_mode} 模式 · 模型 {task.model} · strix {s.strix_version}")
        db.commit()

        # ---- Stage 1: 获取源码 ----
        import shutil

        # 各仓库克隆到任务工作区顶层各自目录（不嵌套 src/），逐目录作为独立白盒
        # 目标传给 strix（-t 可重复），引擎为每个目标分配独立 /workspace/<目录名>
        # 子目录，漏洞的 target 字段才能归属到具体仓库
        taken = {"scan", "src"}
        repo_dirs = {r["url"]: task_ws / repo_dir_name(r["url"], taken) for r in repo_refs}
        api_dir = task_ws / "api"
        for d in [*repo_dirs.values(), src_dir]:  # 重派发/重试时清掉上次残留，保证可幂等重跑
            shutil.rmtree(d, ignore_errors=True)
        t_fetch = time.monotonic()
        source_dirs: list[Path]
        if task.source_type == "git":
            git_log = lambda m: (_log(task, m), db.commit())  # noqa: E731
            # 每个仓库各自的凭据：绑定的个人 Git 配置实时令牌（配置换令牌项目自动跟随）优先，
            # 仓库级快照次之，项目级 PAT 兜底（配置被删/失效时回退快照）
            repo_tokens = load_repo_tokens(project.repo_tokens) if project else {}
            proj_auth = project.git_auth_type if project else ""
            proj_token = project.git_token if project else ""
            bound_cfg = db.get(GitConfig, project.git_config_id) if project and project.git_config_id else None
            bound = (bound_cfg.base_url, bound_cfg.token) if bound_cfg and bound_cfg.token else None

            def _clone(url: str, dest, branch: str) -> None:
                auth, tok = repo_credential(repo_tokens, url, proj_auth, proj_token, bound_config=bound)
                clone_git(url, dest, git_log, branch=branch, auth_type=auth, token=tok, full_history=full_history)

            if len(repo_refs) > 1:
                _log(task, f"[fetch] 项目绑定 {len(repo_refs)} 个仓库，逐个克隆为独立扫描目标")
            for r in repo_refs:
                _clone(r["url"], repo_dirs[r["url"]], r["branch"])
            source_dirs = [repo_dirs[r["url"]] for r in repo_refs]
        else:
            upload = db.get(ProjectUpload, task.upload_id) if task.upload_id else None
            zip_path = Path(upload.stored_path) if upload and upload.stored_path else ws / "uploads" / f"{task_id}.zip"
            if not zip_path.is_file():
                raise SourceError("上传的压缩包丢失，请重新提交")
            src_dir.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(
                zip_path, src_dir, s.max_upload_mb * 1024 * 1024,
                lambda m: (_log(task, m), db.commit()),
            )
            source_dirs = [src_dir]

        # API 规范目标（OpenAPI/Swagger/Postman/Apifox 导出）：拷入任务工作区 api/ 目录
        spec_files: list[Path] = []
        api_specs = _parse_api_specs(task.api_specs)
        if api_specs:
            api_dir.mkdir(parents=True, exist_ok=True)
            for spec in api_specs:
                src_path = Path(spec["stored_path"])
                if not src_path.is_file():
                    _log(task, f"[fetch][警告] API 规范文件丢失，跳过: {spec['name']}")
                    continue
                dest = api_dir / spec["name"]
                shutil.copyfile(src_path, dest)
                spec_files.append(dest)
            if spec_files:
                _log(task, f"[fetch] API 规范目标 {'、'.join(p.name for p in spec_files)}")
        _log(
            task,
            f"[fetch] 源码就绪（{sum(du_mb(d) for d in source_dirs)}MB"
            + (f"，{len(source_dirs)} 个目录：" + "、".join(f"{d.name} {du_mb(d)}MB" for d in source_dirs) if len(source_dirs) > 1 else "")
            + f"，耗时 {int(time.monotonic() - t_fetch)} 秒）",
        )

        # ---- Stage 2: strix 扫描（用户自定义指令 + 目标说明 + 中文报告提示词均通过 --instruction 注入）----
        if cancel_requested(task_id):
            result = finish_as_cancelled(db, task, task_id, "源码获取完成后、扫描启动前被取消")
            db.close()
            return result
        _set_status(db, task, "scanning")
        _log(task, f"[scan] 模型: {task.model}（中文报告）")
        targets = effective_targets(task.test_targets, task.test_url)
        if targets:
            _log(
                task,
                "[scan] 黑盒目标: "
                + "；".join(f"{t['url']}（{t['note']}）" if t["note"] else t["url"] for t in targets),
            )
        else:
            _log(task, "[scan] 未配置黑盒目标：授权引擎在沙箱内自建本地靶场（构建并运行应用、自备数据库）做 PoC 实测，仅限沙箱回环地址")
        # 用户指令在首位，其后依次是仓库结构 / 黑盒目标说明块，最后是中文报告要求
        instruction_parts: list[str] = [task.instruction] if task.instruction else []
        if task.source_type == "git" and len(repo_refs) > 1:
            notes = {
                r["url"]: r["note"]
                for r in (effective_repos(project.git_repos, project.git_url) if project else [])
            }
            lines = [
                "This project's source code consists of multiple repositories. "
                "Each repository is provided as a separate whitebox source directory "
                "(its own subdirectory under /workspace, named after the repository). "
                "When reporting a finding, set its target to the affected repository URL:"
            ]
            for i, r in enumerate(repo_refs, 1):
                seg = f"{i}. {r['url']}"
                if r["branch"]:
                    seg += f" (branch {r['branch']})"
                seg += f" [source at /workspace/{repo_dirs[r['url']].name}]"
                if notes.get(r["url"]):
                    seg += f" — {notes[r['url']]}"
                lines.append(seg)
            instruction_parts.append("\n".join(lines))
        if targets:
            lines = ["Black-box test targets for this scan (test each of them):"]
            for i, t in enumerate(targets, 1):
                lines.append(f"{i}. {t['url']}" + (f" — {t['note']}" if t["note"] else ""))
            instruction_parts.append("\n".join(lines))
        else:
            # 无黑盒目标时明确授权边界：允许沙箱内自建靶场做实测，禁止出沙箱流量
            instruction_parts.append(LOCAL_RANGE_GUIDE)
        if spec_files:
            lines = [
                "API specification targets are provided alongside the source code "
                "(OpenAPI/Swagger, Postman collection or Apifox export). Treat them as "
                "the authoritative API contract: enumerate and test the documented "
                "endpoints, parameters and auth flows against the black-box targets "
                "above (or against a locally provisioned instance when no black-box "
                "target is in scope):",
            ]
            for i, p in enumerate(spec_files, 1):
                lines.append(f"{i}. {p.name} (staged at /workspace/{p.name})")
            instruction_parts.append("\n".join(lines))
        if task.web_search and s.web_search_mcp_url:
            instruction_parts.append(web_search_pointer())
        instruction_parts.append(ZH_INSTRUCTION)
        instruction = "\n".join(instruction_parts).strip()

        # 平台技能库：启用的技能材料化到共享目录，经 python -c 引导段注册进引擎
        skills_dir = materialize_skills(db, ws / ".skills")
        if skills_dir:
            _log(task, "[scan] 已注入平台技能库（含联网搜索等自定义技能）")

        result = execute_scan(
            work_dir=scan_dir,
            src_dirs=source_dirs,
            test_targets=targets,
            scan_mode=task.scan_mode,
            model=task.model or "",
            instruction=instruction,
            llm_api_key=user_llm_key,
            log=lambda m: (_log(task, m), db.commit()),
            cancel_check=lambda: cancel_requested(task_id),
            timeout_disabled=bool(task.timeout_disabled),
            scope_mode=scope_mode,
            diff_base=diff_base,
            resume_run=resume_run,
            spec_files=spec_files,
            skills_dir=skills_dir,
            steer_check=lambda: pop_steer(task_id),
            on_sandbox_id=lambda cid: _remember_sandbox_container(task_id, cid),
        )
        task.exit_code = result["exit_code"]
        task.attempts = result["attempts"]
        task.timed_out = result["timed_out"]
        task.run_dir_name = result["run_dir_name"]

        if result.get("cancelled"):
            # 用户取消：不解析半成品产物、不归档、不翻译，直接落终态
            result = finish_as_cancelled(db, task, task_id, "引擎进程已被终止")
            db.close()
            return result

        # ---- Stage 3: 产物解析入库 ----
        _set_status(db, task, "parsing")
        run_record, vulns = read_run_artifacts(scan_dir, result["run_dir_name"])
        # 失败保留：本轮引擎报错（退出码 1），或沙箱执行通道整体故障（大量
        # exec 失败 + 0 登记，环境故障伪装成"无漏洞完成"）时，不删旧 findings、
        # 不覆盖转录/报告/用量——尤其续扫失败不应毁掉此前成功的扫描结果。
        run_failed = task.exit_code == 1 and not task.timed_out
        exec_error_count = _exec_channel_error_count(scan_dir, result["run_dir_name"])
        env_broken = not vulns and exec_error_count >= _EXEC_ERROR_THRESHOLD
        if run_failed or env_broken:
            if run_failed:
                detail = _scan_error_detail(scan_dir)
                task.error = f"strix 执行失败（退出码 1）：{detail or '详见任务日志与归档产物'}"
            else:
                task.error = (
                    f"沙箱命令执行通道故障（exec transport error ×{exec_error_count}），"
                    "引擎未能实际执行扫描，本轮未产出有效结果"
                )
            _log(
                task,
                "[parse] " + ("本轮执行失败" if run_failed else "沙箱执行通道故障")
                + (f"（原结果 {task.findings_count} 条保留）" if task.findings_count else "，本轮无有效结果"),
            )
            try:
                task.artifacts_ref = archive_run(scan_dir, result["run_dir_name"], task_id, artifacts_dir)
                _log(task, f"[archive] 失败轮产物已归档: {task.artifacts_ref}")
            except Exception as exc:  # 归档失败不影响结果
                _log(task, f"[archive] 归档失败（不影响结果）: {exc}")
            task.finished_at = datetime.now(timezone.utc)
            if task.started_at:
                task.duration_sec = int((task.finished_at - task.started_at).total_seconds())
            _log(task, f"[done] 状态: 失败，耗时 {task.duration_sec} 秒，{task.error}")
            _set_status(db, task, "failed")
            _notify(db, task, task_id)
            db.close()
            return {"task_id": task_id, "error": task.error}
        usage = run_record.get("llm_usage") or {}
        task.total_tokens = usage.get("total_tokens")
        task.input_tokens = usage.get("input_tokens")
        task.output_tokens = usage.get("output_tokens")
        task.llm_requests = usage.get("requests")
        task.agents_usage = _agents_usage(run_record, scan_dir, result["run_dir_name"])
        # 官方执行摘要报告（strix view 展示的同款 penetration_test_report.md）
        report_path = scan_dir / "strix_runs" / result["run_dir_name"] / "penetration_test_report.md"
        try:
            task.report_md = report_path.read_text(encoding="utf-8")[:500_000]
        except OSError:
            task.report_md = ""
        if not task.report_md:
            _log(task, "[parse] 未生成执行摘要报告（penetration_test_report.md）")

        # 执行步骤转录（strix view「智能体执行过程」同源数据：.state/agents.db 会话历史）
        try:
            transcript_data = build_transcript(scan_dir / "strix_runs" / result["run_dir_name"])
            task.transcript = json.dumps(transcript_data, ensure_ascii=False) if transcript_data else ""
            if transcript_data:
                _log(
                    task,
                    f"[parse] 执行步骤转录：{transcript_data['counts']['all']['total']} 条事件"
                    f"（{len(transcript_data['agents'])} 个智能体）",
                )
        except Exception as exc:  # noqa: BLE001 —— 转录失败不影响结果
            task.transcript = ""
            _log(task, f"[parse] 执行步骤转录解析失败（不影响结果）: {exc}")

        db.execute(delete(Finding).where(Finding.task_id == task_id))
        task.zh_status = ""  # 续扫/重跑生成全新 findings，旧的中文翻译状态随之失效
        counts: dict[str, int] = {}
        for v in vulns:
            sev = str(v.get("severity", "info")).lower()
            counts[sev] = counts.get(sev, 0) + 1
            db.add(
                Finding(
                    task_id=task_id,
                    vuln_id=str(v.get("id", "")),
                    title=str(v.get("title", "")),
                    severity=sev,
                    cvss=v.get("cvss"),
                    cwe=str(v.get("cwe", "") or ""),
                    cve=str(v.get("cve", "") or ""),
                    endpoint=str(v.get("endpoint", "") or ""),
                    target=str(v.get("target", "") or ""),
                    has_poc=bool(v.get("poc_script_code") or v.get("poc_description")),
                    description=str(v.get("description", "") or ""),
                    remediation=str(v.get("remediation_steps", "") or ""),
                    poc_description=str(v.get("poc_description", "") or ""),
                    poc_code=str(v.get("poc_script_code", "") or ""),
                    raw=json.dumps(v, ensure_ascii=False),
                )
            )
        task.findings_count = len(vulns)
        task.severity_counts = json.dumps(counts, ensure_ascii=False)
        _log(
            task,
            f"[parse] run={result['run_dir_name'] or '-'} · 智能体 {len(usage.get('agents') or [])} 个 · "
            f"漏洞 {len(vulns)} 条 {json.dumps(counts, ensure_ascii=False)} "
            f"tokens={usage.get('total_tokens', '-')} 退出码={task.exit_code}",
        )

        # ---- Stage 4: 归档 ----
        try:
            task.artifacts_ref = archive_run(scan_dir, result["run_dir_name"], task_id, artifacts_dir)
            _log(task, f"[archive] 产物已归档: {task.artifacts_ref}")
        except Exception as exc:  # 归档失败不影响结果
            _log(task, f"[archive] 归档失败（不影响结果）: {exc}")

        task.finished_at = datetime.now(timezone.utc)
        if task.started_at:
            task.duration_sec = int((task.finished_at - task.started_at).total_seconds())
        # 退出码 1 / 沙箱通道故障已在 Stage 3 开头的「失败保留」分支提前返回；
        # 走到这里说明本轮有效：0 无发现 / 2 发现漏洞，均按完成处理
        _log(
            task,
            f"[done] 状态: {'超时终止' if task.timed_out else '完成'}，"
            f"耗时 {task.duration_sec} 秒，发现 {task.findings_count} 条",
        )
        _set_status(db, task, "done")
        _notify(db, task, task_id)

        return {"task_id": task_id, "exit_code": task.exit_code, "findings": task.findings_count}

    except SourceError as exc:
        task.error = f"源码获取失败: {exc}"
        _log(task, f"[fail] {task.error}")
        task.finished_at = datetime.now(timezone.utc)
        _set_status(db, task, "failed")
        _notify(db, task, task_id)
        return {"task_id": task_id, "error": task.error}
    except Exception as exc:  # noqa: BLE001 —— 流水线任何异常都要落到任务状态
        task.error = f"执行异常: {exc}"
        _log(task, f"[fail] {task.error}")
        task.finished_at = datetime.now(timezone.utc)
        _set_status(db, task, "failed")
        _notify(db, task, task_id)
        return {"task_id": task_id, "error": task.error}
    finally:
        hb_stop.set()
        hb_thread.join(timeout=10)
        db.close()


def new_task_id() -> str:
    return uuid.uuid4().hex


# ---- 等待中任务重投递：beat 每分钟 + worker 启动时各触发一次 ----

def _redispatch_pending() -> list[str]:
    """把 DB 里仍处 pending 的任务全部重新投递，返回补投的任务 id 列表。

    pending 任务靠 broker 消息驱动，而消息可能丢失（redis 容器重建、队列清理），
    也没有别的机制会再驱动它们——僵尸回收只管运行态，任务就会永远停在「等待中」。
    以 DB 状态为准周期性补投是自愈层：消息尚在时重复投递无害（run_scan 的取消
    检查/准入闸门/心跳重入防护保证单执行者，重复消息被跳过或等空位后自然重试）。
    """
    db = SessionLocal()
    try:
        ids = db.execute(
            select(Task.id).where(Task.status == "pending").order_by(Task.created_at)
        ).scalars().all()
    finally:
        db.close()
    for task_id in ids:
        run_scan.delay(task_id)
    return list(ids)


@celery_app.task(name="redispatch_pending_tasks")
def redispatch_pending_tasks() -> dict:
    """每分钟由 beat 敲一次：补投等待中任务。消息丢失/消费中断的最终兜底，
    与槽位排队共用同一条路径（槽位满时 run_scan 直接返回，下分钟再来）。"""
    ids = _redispatch_pending()
    if ids:
        print(f"[queue] 补投 {len(ids)} 个等待中任务", flush=True)
    return {"redispatched": len(ids), "task_ids": ids}


@celery_worker_ready.connect
def _redispatch_pending_on_boot(**_: object) -> None:
    """worker 启动时立即补投一轮（beat 内嵌时 60 秒内也会跑到，这里只是不等）。

    恢复失败不阻断 worker 启动（下一分钟 beat 会再试）。
    """
    try:
        ids = _redispatch_pending()
        if ids:
            print(f"[recover] worker 启动：重新派发 {len(ids)} 个等待中任务", flush=True)
    except Exception as exc:  # noqa: BLE001 —— 启动恢复尽力而为，不影响 worker 就绪
        print(f"[recover] worker 启动恢复失败（等待中任务未被重新派发）: {exc}", flush=True)
