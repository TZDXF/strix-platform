from __future__ import annotations

from celery import Celery

from .config import get_settings

settings = get_settings()

celery_app = Celery("strix_platform", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    include=["app.tasks"],
    task_serializer="json",
    accept_content=["json"],
    # 平台级并发限制：共享主机同时只跑 1 个扫描（worker 单进程）
    worker_concurrency=1,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=0,  # 超时由 runner 内部按模式控制并优雅回收
)
