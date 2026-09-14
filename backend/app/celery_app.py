from __future__ import annotations

from celery import Celery

from .config import get_settings

settings = get_settings()

# worker 线程池硬顶：扫描是子进程等待型 I/O 负载，线程池足够且跨平台可用
# （Windows 无 prefork）；实际并行扫描数由 run_scan 闸门按系统设置
# scan_concurrency 实时控制（app/tasks.py，默认 2），这里只需 ≥ 设置上限兜底
WORKER_POOL_SIZE = 8

celery_app = Celery("strix_platform", broker=settings.redis_url, backend=settings.redis_url)

# Redis broker 可见性超时：acks_late 下任务执行超过它会被重新投递（默认 1 小时），
# 而扫描普遍超 1 小时 → 同一任务被第二个 run_scan 并发重复执行：Stage 1 会清掉
# 正在扫描的源码目录，旧调用收尾还会经尾随线程误登记并 docker rm -f 新调用的
# 沙箱容器（运行中容器被删 → 引擎全智能体 404 "No such container" 崩溃）。
# 设为 24h 覆盖最长扫描；重投递的残余风险由 run_scan 的执行心跳重入防护兜底。
# broker 与 result backend 须取同值（Celery 对 Redis 的官方要求）。
_REDIS_VISIBILITY_TIMEOUT = 24 * 3600

celery_app.conf.update(
    include=["app.tasks", "app.translate", "app.schedules", "app.sandbox", "app.retention"],
    task_serializer="json",
    accept_content=["json"],
    worker_pool="threads",
    worker_concurrency=WORKER_POOL_SIZE,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=0,  # 超时由 runner 内部按模式控制并优雅回收
    broker_transport_options={"visibility_timeout": _REDIS_VISIBILITY_TIMEOUT},
    result_backend_transport_options={"visibility_timeout": _REDIS_VISIBILITY_TIMEOUT},
    # 定时扫描：beat 每分钟敲一次轮询任务（worker 以 --beat 内嵌启动，见 compose），
    # 到期判断与补跑逻辑见 app.schedules.dispatch_due_schedules；
    # 僵尸任务回收见 app.schedules.reclaim_zombie_tasks；
    # 等待中任务重投递见 app.tasks.redispatch_pending_tasks（槽位排队与消息丢失
    # 的统一自愈：pending 任务不依赖任何单条消息存活，每分钟以 DB 状态为准补投）；
    # 沙箱容器兜底清扫见 app.sandbox.cleanup_stale_sandboxes；
    # 历史任务工作区源码回收见 app.retention.cleanup_task_workspaces（scan/ 产物永不清理）
    beat_schedule={
        "dispatch-due-schedules": {
            "task": "dispatch_due_schedules",
            "schedule": 60.0,
        },
        "reclaim-zombie-tasks": {
            "task": "reclaim_zombie_tasks",
            "schedule": 60.0,
        },
        "redispatch-pending-tasks": {
            "task": "redispatch_pending_tasks",
            "schedule": 60.0,
        },
        "cleanup-stale-sandboxes": {
            "task": "cleanup_stale_sandboxes",
            "schedule": 1800.0,
        },
        "cleanup-task-workspaces": {
            "task": "cleanup_task_workspaces",
            "schedule": 21600.0,  # 每 6 小时
        },
    },
)
