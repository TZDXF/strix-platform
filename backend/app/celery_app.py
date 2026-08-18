from __future__ import annotations

from celery import Celery

from .config import get_settings

settings = get_settings()

celery_app = Celery("strix_platform", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    include=["app.tasks", "app.translate", "app.schedules"],
    task_serializer="json",
    accept_content=["json"],
    # 平台级并发限制：共享主机同时只跑 1 个扫描（worker 单进程）
    worker_concurrency=1,
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
