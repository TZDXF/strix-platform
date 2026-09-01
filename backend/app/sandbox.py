"""沙箱容器兜底清扫（celery beat 周期任务）。

治本路径：runner.execute_scan 结束（含取消/超时/转向等强杀出口）时按本次 run
解析到的容器 ID 逐一 docker rm -f。本模块只兜底治本路径覆盖不到的残留：
worker 崩溃、回收时 docker 瞬时不可用、以及接入本机制之前的历史孤儿容器
（那时平台只解析容器 ID 供展示，从不回收，杀掉 CLI 进程树后容器照跑）。

回收基准（按运维约定）：以「所属任务上一次运行结束」（tasks.finished_at，续扫/
重跑会刷新）起算，闲置超过 SANDBOX_SWEEP_IDLE_SEC 才回收；无任何任务登记可
归属的容器退化为按容器自身创建时长判断。运行中任务的容器在创建瞬间由
tasks._remember_sandbox_container 增量登记，清扫绝不触碰——不限时扫描
（timeout_disabled）可运行数天，按容器年龄一刀切会误杀，登记是唯一可靠信号。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from .celery_app import celery_app
from .db import SessionLocal
from .models import Task
from .tasks import _ACTIVE_SCAN_STATUSES

# 沙箱闲置判定阈值：任务上一次运行结束 12 小时后，其登记的容器才允许被清扫
SANDBOX_SWEEP_IDLE_SEC = 12 * 3600


def _aware(dt: datetime | None) -> datetime | None:
    """统一为带时区时间再比较（sqlite 等方言可能返回 naive UTC）。"""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _id_match(container_id: str, known: str) -> bool:
    """容器 ID 匹配：strix 日志里的 id 可能是短 ID，daemon 侧 attrs 是 64 位全 ID。"""
    return bool(known) and (container_id.startswith(known) or known.startswith(container_id))


def _decode_ids(raw: str) -> list[str]:
    try:
        ids = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(i) for i in ids if i]


@celery_app.task(name="cleanup_stale_sandboxes")
def cleanup_stale_sandboxes() -> dict:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        protected: list[str] = []
        idle_since: dict[str, datetime] = {}  # 登记的容器 id → 所属任务最近一次运行结束
        rows = db.execute(
            select(Task).where(
                Task.status.in_(_ACTIVE_SCAN_STATUSES) | (Task.sandbox_container_ids != "")
            )
        ).scalars().all()
        for task in rows:
            ids = _decode_ids(task.sandbox_container_ids)
            if not ids:
                continue
            if task.status in _ACTIVE_SCAN_STATUSES:
                protected.extend(ids)
                continue
            end = _aware(task.finished_at)
            if end is None:
                continue  # 终态时间缺失无法判定闲置起点，宁可不动
            for cid in ids:  # 同一容器被多次续扫登记时取最近一次结束时间
                if cid not in idle_since or end > idle_since[cid]:
                    idle_since[cid] = end
        try:
            import docker

            client = docker.from_env(timeout=15)
        except Exception as exc:  # noqa: BLE001 —— 无 docker.sock（api 容器/本机直跑）时静默跳过
            return {"removed": 0, "reason": str(exc)}
        removed: list[str] = []
        try:
            for ctr in client.containers.list(all=True):  # all=True：已退出的孤儿也要清
                image = (ctr.attrs.get("Config") or {}).get("Image") or ""
                if "strix-sandbox" not in image:
                    continue
                cid = ctr.attrs.get("Id", "")
                if any(_id_match(cid, p) for p in protected):
                    continue
                base = next((t for k, t in idle_since.items() if _id_match(cid, k)), None)
                if base is None:  # 无任务可归属（历史孤儿/登记丢失）：按容器创建时长兜底
                    created = ctr.attrs.get("Created")
                    base = datetime.fromtimestamp(int(created), tz=timezone.utc) if created else None
                if base is None or (now - base).total_seconds() < SANDBOX_SWEEP_IDLE_SEC:
                    continue
                try:
                    ctr.remove(force=True)
                    removed.append(cid[:12])
                except Exception:  # noqa: BLE001 —— 单个容器失败不阻断整轮清扫
                    pass
        finally:
            client.close()
        if removed:
            return {"removed": len(removed), "ids": removed}
        return {"removed": 0}
    finally:
        db.close()
