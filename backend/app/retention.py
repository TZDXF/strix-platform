"""任务工作区源码定期回收（celery beat 周期任务）。

每个任务的工作区（workspace_root/<task_id>/）里有两类内容：源码副本（git 克隆的
仓库目录、zip 解压的 src/、API 规范拷贝的 api/）和扫描产物（scan/：run 目录、
转录、日志）。源码副本每次派发都会重新获取（git 重新克隆、zip 从 uploads 主本
重新解压、规范从 uploads/apispecs 主本重新拷贝），任务结束后再无用处，却随任务
数无限累积——这是 workspaces 目录磁盘占用的主要来源。

本模块按系统设置 workspace_keep_tasks 只保留最近 N 个任务（按 created_at 计）
的源码，更早的终态任务清掉 scan/ 以外的一切。scan/ 与工作区外的内容（uploads/
主本、artifacts/ 归档、reports/ 报告缓存）永不触碰，因此清理对续扫/重跑无害：
断点续扫只依赖 scan/，源码由 Stage 1 现场重拉。非终态任务（排队/运行中/翻译中）
绝不清理；运行中的任务天然属于「最近 N 个」，与状态过滤构成双保险。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select

from .celery_app import celery_app
from .config import get_settings
from .db import SessionLocal
from .models import SystemSetting, Task
from .sources import du_mb
from .tasklog import append_log
from .tasks import (
    K_WORKSPACE_KEEP_TASKS,
    WORKSPACE_KEEP_TASKS_DEFAULT,
    _TERMINAL_TASK_STATUSES,
)

# 任务工作区内永不清理的目录名（扫描产物）
_PRESERVE_DIRS = {"scan"}


def _keep_count(db) -> int:
    row = db.get(SystemSetting, K_WORKSPACE_KEEP_TASKS)
    try:
        value = int((row.value or "").strip()) if row is not None else -1
    except ValueError:
        value = -1
    if value < 0:
        return WORKSPACE_KEEP_TASKS_DEFAULT
    return value  # 0 = 关闭清理，保留所有任务的工作区


@celery_app.task(name="cleanup_task_workspaces")
def cleanup_task_workspaces() -> dict:
    db = SessionLocal()
    try:
        keep = _keep_count(db)
        if keep <= 0:
            return {"cleaned": 0, "disabled": True}
        tasks = db.execute(
            select(Task).order_by(Task.created_at.desc(), Task.id.desc())
        ).scalars().all()
        keep_ids = {t.id for t in tasks[:keep]}
        ws = Path(get_settings().workspace_root)
        cleaned: list[str] = []
        freed_mb = 0
        for task in tasks[keep:]:
            if task.status not in _TERMINAL_TASK_STATUSES:
                continue
            task_ws = ws / task.id
            if not task_ws.is_dir():
                continue
            for entry in list(task_ws.iterdir()):
                if entry.name in _PRESERVE_DIRS:
                    continue
                freed_mb += du_mb(entry)
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
            cleaned.append(task.id)
            # 留痕到任务日志：之后有人续扫/重跑，能看懂为什么源码要重新拉取
            append_log(task, f"[retention] 工作区源码已按保留策略回收，续扫/重跑时自动重新拉取（scan/ 产物保留）")
            db.commit()
        if cleaned:
            return {"cleaned": len(cleaned), "freed_mb": freed_mb, "ids": cleaned[:50]}
        return {"cleaned": 0, "freed_mb": 0}
    finally:
        db.close()
