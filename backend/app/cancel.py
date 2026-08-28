"""跨进程任务控制标记：API 进程写 Redis，worker 在扫描等待循环里轮询。

API 服务与 Celery worker 是独立进程，取消/转向请求经 Redis 中转（与 broker 同实例，
不引入新依赖）；worker 侧轮询失败按"未取消/无转向"处理（fail-open，扫描不受影响）。

转向（steer）：扫描运行中向智能体下发新指令。引擎无头模式无进程外通道，平台按
「安全打断 + --resume 续扫 + 新指令注入根智能体」实现（runner.execute_scan），
标记值即转向指令文本。
"""

from __future__ import annotations

import redis
from redis.exceptions import RedisError

from .config import get_settings

_client: redis.Redis | None = None

# 标记保留期：任务最长超时 8h + 重试，7 天足够覆盖；到期自动过期防残留
_TTL_SEC = 7 * 24 * 3600


def _r() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            get_settings().redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2,
        )
    return _client


def _key(task_id: str) -> str:
    return f"strix:cancel:{task_id}"


def _steer_key(task_id: str) -> str:
    return f"strix:steer:{task_id}"


def request_cancel(task_id: str) -> bool:
    """写入取消标记；返回是否写入成功（Redis 不可用时向调用方报错）。"""
    try:
        _r().set(_key(task_id), "1", ex=_TTL_SEC)
        return True
    except RedisError:
        return False


def cancel_requested(task_id: str) -> bool:
    try:
        return _r().get(_key(task_id)) == "1"
    except RedisError:
        return False


def clear_cancel(task_id: str) -> None:
    try:
        _r().delete(_key(task_id))
    except RedisError:
        pass


def request_steer(task_id: str, message: str) -> bool:
    """写入转向指令标记（同任务同时刻只保留最新一条，覆盖未消费的旧指令）。"""
    try:
        _r().set(_steer_key(task_id), message[:2000], ex=_TTL_SEC)
        return True
    except RedisError:
        return False


def pop_steer(task_id: str) -> str:
    """取出并清除转向指令（worker 消费）；无标记或 Redis 不可用返回空串。"""
    try:
        return _r().getdel(_steer_key(task_id)) or ""
    except RedisError:
        return ""


def clear_steer(task_id: str) -> None:
    try:
        _r().delete(_steer_key(task_id))
    except RedisError:
        pass
