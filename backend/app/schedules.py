"""定时扫描：cron 解析与下次运行计算（北京时间）+ 到期轮询派发 + 计划触发。

设计要点：
- 不引入新依赖：自实现 5 字段 cron（支持 *、*/n、a、a-b、a-b/n 与逗号列表），
  遵循标准 cron 的 dom/dow「同时受限则取并集」规则；dow 0/7 均为周日
- 中国无夏令时，cron 按固定 UTC+8 解释；next_run_at 统一存 UTC
- celery beat 每分钟敲一次 dispatch_due_schedules（内嵌 beat，单 worker 串行执行
  扫描，轮询消息排队等扫描结束再跑，靠 next_run_at 判断自然补跑错过的周期）
- 到期抢占用乐观锁（UPDATE ... WHERE next_run_at=<观察到值>）防并发双发
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .celery_app import celery_app
from .db import SessionLocal
from .llm_models import default_model, platform_models
from .models import Project, ProjectUpload, Schedule, Task, User
from .sources import effective_repo_branches, effective_repos
from .targets import effective_targets
from .tasks import new_task_id, run_scan
from .tasklog import append_log as _log

BJT = timezone(timedelta(hours=8))  # 北京时间（cron 解释时区）

# 各字段（min/hour/dom/mon/dow）的取值范围
_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_DOW_NAMES = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


def _parse_field(expr: str, lo: int, hi: int, is_dow: bool = False) -> set[int] | None:
    """解析单个 cron 字段为取值集合；非法返回 None。"""
    values: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                return None
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                return None
            start, end = int(a), int(b)
        else:
            if not part.isdigit():
                return None
            start = int(part)
            # cron 惯例：单值带步长（如 "5/15"）表示从 5 到字段上限
            end = hi if step > 1 else start
        if start < lo or end > hi or start > end:
            return None
        values.update(range(start, end + 1, step))
    if is_dow:
        values = {0 if v == 7 else v for v in values}
    return values or None


def parse_cron(expr: str) -> tuple[dict, str]:
    """解析完整 cron 表达式；返回 (字段取值集合, 错误信息)。合法时错误为空串。"""
    parts = expr.split()
    if len(parts) != 5:
        return {}, "cron 需为 5 个字段：分 时 日 月 周（如 \"0 8 * * *\"）"
    fields: dict[str, set[int]] = {}
    dom_dow: dict[str, set[int]] = {}
    for name, raw, (lo, hi) in zip(("min", "hour", "dom", "mon", "dow"), parts, _FIELD_RANGES):
        vals = _parse_field(raw, lo, hi, is_dow=(name == "dow"))
        if vals is None:
            return {}, f"字段「{name}」不合法：{raw}"
        fields[name] = vals
        if raw != "*":
            dom_dow[name] = vals
    # dom 与 dow 同时受限时按并集匹配（标准 cron 语义），否则只看受限的一方
    if "dom" in dom_dow and "dow" in dom_dow:
        fields["_day_rule"] = "both"
    elif "dom" in dom_dow:
        fields["_day_rule"] = "dom"
    elif "dow" in dom_dow:
        fields["_day_rule"] = "dow"
    else:
        fields["_day_rule"] = "any"
    return fields, ""


def cron_valid(expr: str) -> str:
    """校验 cron；合法返回空串，否则返回中文错误。"""
    _, err = parse_cron(expr)
    return err


def _day_matches(fields: dict, d: datetime) -> bool:
    rule = fields["_day_rule"]
    if rule == "any":
        return True
    dom_ok = d.day in fields["dom"]
    dow_ok = (d.weekday() + 1) % 7 in fields["dow"]  # cron 周日=0；datetime 周一=0
    if rule == "both":
        return dom_ok or dow_ok
    if rule == "dom":
        return dom_ok
    return dow_ok


def next_cron_run(expr: str, after_utc: datetime) -> datetime | None:
    """计算 after_utc（UTC）之后下一次命中时刻，返回 UTC；无命中（4 年内）返回 None。"""
    fields, err = parse_cron(expr)
    if err:
        return None
    start = (after_utc.astimezone(BJT) + timedelta(minutes=1)).replace(second=0, microsecond=0)
    day = start
    for _ in range(366 * 4):  # 覆盖闰年组合（如 2 月 29 日），最多 4 年
        if day.month in fields["mon"] and _day_matches(fields, day):
            for h in sorted(fields["hour"]):
                for m in sorted(fields["min"]):
                    t = day.replace(hour=h, minute=m)
                    if t >= start:
                        return t.astimezone(timezone.utc)
        day += timedelta(days=1)
    return None


def cron_describe(expr: str) -> str:
    """把常见 cron 模式翻成中文描述；复杂表达式原样展示。"""
    fields, err = parse_cron(expr)
    if err:
        return expr
    mins, hours = sorted(fields["min"]), sorted(fields["hour"])
    rule = fields["_day_rule"]
    hm = f"{hours[0]:02d}:{mins[0]:02d}" if len(hours) == 1 and len(mins) == 1 else None
    if rule == "any":
        if hours == list(range(24)) and len(mins) > 1 and _is_step(mins):
            return f"每 {mins[1] - mins[0]} 分钟"
        if hm is None and len(mins) == 1 and len(hours) > 1 and _is_step(hours):
            return f"每 {hours[1] - hours[0]} 小时"
        if hm:
            return f"每天 {hm}"
    if rule == "dow" and hm:
        dows = sorted(fields["dow"])
        if dows == [1, 2, 3, 4, 5]:
            return f"每个工作日 {hm}"
        if len(dows) == 1:
            return f"每{_DOW_NAMES[dows[0]]} {hm}"
    if rule == "dom" and hm and len(fields["dom"]) == 1 and fields["mon"] == set(range(1, 13)):
        return f"每月 {sorted(fields['dom'])[0]} 日 {hm}"
    return expr


def _is_step(vals: list[int]) -> bool:
    return len(vals) > 1 and all(b - a == vals[1] - vals[0] for a, b in zip(vals, vals[1:]))


def schedule_out(sch: Schedule, db: Session) -> dict:
    last_task = db.get(Task, sch.last_task_id) if sch.last_task_id else None
    creator = db.get(User, sch.created_by) if sch.created_by else None
    return {
        "id": sch.id,
        "project_id": sch.project_id,
        "name": sch.name or "未命名计划",
        "cron": sch.cron,
        "cron_desc": cron_describe(sch.cron),
        "enabled": bool(sch.enabled),
        "scan_mode": sch.scan_mode,
        "model": sch.model or "",
        "instruction": sch.instruction or "",
        "web_search": bool(sch.web_search),
        "repo_branches": effective_repo_branches(sch.repo_branches, "", ""),
        "test_targets": effective_targets(sch.test_targets, ""),
        "upload_id": sch.upload_id or "",
        "next_run_at": sch.next_run_at.isoformat() if sch.next_run_at else None,
        "last_run_at": sch.last_run_at.isoformat() if sch.last_run_at else None,
        "last_task_id": sch.last_task_id or "",
        "last_task_status": last_task.status if last_task else "",
        "last_error": sch.last_error or "",
        "created_by_name": creator.username if creator else "-",
        "created_at": sch.created_at.isoformat() if sch.created_at else None,
    }


def fire_schedule(db: Session, sch: Schedule, manual: bool = False) -> tuple[str | None, str]:
    """按计划快照创建任务并派发；返回 (task_id, 错误信息)。

    校验失败不抛异常（轮询任务不能因单个计划挂掉）：可恢复的记 last_error 跳过，
    不可恢复的（项目归档/仓库全部被移除）直接停用计划。
    """
    def _fail(err: str, *, disable: bool = False) -> tuple[None, str]:
        sch.last_error = err[:500]
        if disable:
            sch.enabled = False
            sch.next_run_at = None
        db.commit()
        return None, err

    project = db.get(Project, sch.project_id)
    if project is None:
        return _fail("项目已被删除，计划已停用", disable=True)
    if project.is_archived:
        return _fail("项目已归档，计划已停用；恢复项目后可重新启用", disable=True)

    creator = db.get(User, sch.created_by) if sch.created_by else None
    if creator is None or not (creator.llm_api_key or "").strip():
        return _fail("计划创建者未配置个人 AI 密钥，暂无法触发；请其到「设置」页配置")

    source_ref = ""
    upload_id = None
    repo_refs: list[dict] = []
    if project.source_type == "git":
        repos = effective_repos(project.git_repos, project.git_url)
        bound = {r["url"] for r in repos}
        snapshot = [r for r in effective_repo_branches(sch.repo_branches, "", "") if r["url"] in bound]
        if not snapshot:  # 兜底：快照里的仓库全被移除，退回项目当前全部仓库（默认分支）
            snapshot = [{"url": r["url"], "branch": ""} for r in repos]
        if not snapshot:
            return _fail("项目未绑定任何仓库，计划已停用", disable=True)
        repo_refs = snapshot
        source_ref = repo_refs[0]["url"]
    else:
        upload = db.get(ProjectUpload, sch.upload_id) if sch.upload_id else None
        if upload is None or upload.project_id != project.id:
            return _fail("计划引用的历史上传不存在（可能已被删除），请编辑计划重新选择")
        upload_id = upload.id
        source_ref = upload.filename

    # 模型校验从宽：快照模型下架时回退平台默认，不让整个计划卡死
    model = sch.model or ""
    if model and model not in platform_models(db):
        model = default_model(db)

    task_id = new_task_id()
    task = Task(
        id=task_id, project_id=project.id, created_by=sch.created_by,
        schedule_id=sch.id, scan_mode=sch.scan_mode, source_type=project.source_type,
        source_ref=source_ref,
        branch=repo_refs[0]["branch"] if repo_refs else "",
        repo_branches=json.dumps(repo_refs, ensure_ascii=False),
        upload_id=upload_id,
        test_targets=sch.test_targets or "",
        instruction=sch.instruction or "",
        web_search=bool(sch.web_search),
        model=model, report_lang="zh", status="pending",
    )
    db.add(task)
    sch.last_run_at = datetime.now(timezone.utc)
    sch.last_task_id = task_id
    sch.last_error = ""
    db.commit()
    _log(task, f"[task] 由定时计划「{sch.name or sch.id[:8]}」{'手动立即执行' if manual else '按周期自动'}触发")
    db.commit()
    run_scan.delay(task_id)
    return task_id, ""


@celery_app.task(name="dispatch_due_schedules")
def dispatch_due_schedules() -> dict:
    """每分钟由 beat 敲一次：找出到期计划，乐观锁抢占后触发。"""
    db = SessionLocal()
    fired: list[str] = []
    errors: dict[str, str] = {}
    try:
        now = datetime.now(timezone.utc)
        rows = db.execute(
            select(Schedule).where(Schedule.enabled == True, Schedule.next_run_at <= now)  # noqa: E712
        ).scalars().all()
        for sch in rows:
            observed_next = sch.next_run_at
            new_next = next_cron_run(sch.cron, now)
            # 先抢占（推进 next_run_at）再触发：即使触发失败也不会每分钟重复尝试
            res = db.execute(
                update(Schedule)
                .where(Schedule.id == sch.id, Schedule.next_run_at == observed_next)
                .values(next_run_at=new_next)
            )
            db.commit()
            if res.rowcount == 0:
                continue  # 已被并发处理
            task_id, err = fire_schedule(db, sch)
            if task_id:
                fired.append(task_id)
            elif err:
                errors[sch.id] = err
    finally:
        db.close()
    return {"fired": len(fired), "task_ids": fired, "errors": errors}


def new_schedule_id() -> str:
    return uuid.uuid4().hex
