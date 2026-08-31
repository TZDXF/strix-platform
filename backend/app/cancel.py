"""跨进程任务控制标记：API 进程写 Redis，worker 在扫描等待循环里轮询。

API 服务与 Celery worker 是独立进程，取消/转向请求经 Redis 中转（与 broker 同实例，
不引入新依赖）；worker 侧轮询失败按"未取消/无转向"处理（fail-open，扫描不受影响）。

执行心跳（heartbeat）：worker 执行任务期间周期刷新的短 TTL 标记，用于回答
「该任务此刻是否真有活着的执行者」。worker 容器重启会留下状态卡在运行态、
实际无人执行的任务，且其 Celery 消息要等 broker visibility_timeout（默认 1 小时）
才重投递——期间取消标记无人轮询、永远不会生效。取消接口与定时回收据此把
这类僵尸任务直接落「已取消」终态（main.cancel_task / schedules.reclaim_zombie_tasks）。

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

HB_TOUCH_INTERVAL = 5.0  # 心跳刷新间隔（秒），worker 侧心跳线程按此周期触达 Redis
_HB_TTL_SEC = 20         # 心跳过期时长：刷新间隔的 4 倍余量，容忍偶发 Redis 抖动


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


# ---- 执行心跳 ----

def _hb_key(task_id: str) -> str:
    return f"strix:hb:{task_id}"


def touch_heartbeat(task_id: str) -> None:
    """刷新执行心跳；Redis 不可用时静默跳过（误缺由回收侧 updated_at 宽限兜底）。"""
    try:
        _r().set(_hb_key(task_id), "1", ex=_HB_TTL_SEC)
    except RedisError:
        pass


def has_heartbeat(task_id: str) -> bool:
    """是否存在执行心跳（即任务有活着的执行者）。

    Redis 不可用时保守视为存在（fail-open）：拿不到证据时不把运行态任务当
    僵尸处理，宁可让取消退回被动路径，也不误杀正在执行的扫描。
    """
    try:
        return bool(_r().exists(_hb_key(task_id)))
    except RedisError:
        return True


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
