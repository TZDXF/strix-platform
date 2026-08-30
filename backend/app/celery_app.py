from __future__ import annotations

from celery import Celery

from .config import get_settings

settings = get_settings()

# worker 线程池硬顶：扫描是子进程等待型 I/O 负载，线程池足够且跨平台可用
# （Windows 无 prefork）；实际并行扫描数由 run_scan 闸门按系统设置
# scan_concurrency 实时控制（app/tasks.py，默认 2），这里只需 ≥ 设置上限兜底
WORKER_POOL_SIZE = 8

celery_app = Celery("strix_platform", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    include=["app.tasks", "app.translate", "app.schedules"],
    task_serializer="json",
    accept_content=["json"],
    worker_pool="threads",
    worker_concurrency=WORKER_POOL_SIZE,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=0,  # 超时由 runner 内部按模式控制并优雅回收
    # 定时扫描：beat 每分钟敲一次轮询任务（worker 以 --beat 内嵌启动，见 compose），
    # 到期判断与补跑逻辑见 app.schedules.dispatch_due_schedules
    beat_schedule={
        "dispatch-due-schedules": {
            "task": "dispatch_due_schedules",
            "schedule": 60.0,
        },
    },
)
