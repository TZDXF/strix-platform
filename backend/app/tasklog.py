"""任务执行日志公共件：统一北京时间戳 + 滚动追加。

worker/strix 容器按 compose 默认跑在 UTC（未设 TZ），平台日志时间戳显式固定
+8 输出北京时间，不依赖镜像内 tzdata；中国无夏令时，固定偏移即恒准。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

BJT = timezone(timedelta(hours=8))  # 北京时间（UTC+8）

LOG_CAP = 400_000  # task.log 滚动上限（字符）；过程事件转发后行数变多，留足 deep 模式余量


def now_bj() -> datetime:
    return datetime.now(BJT)


def append_log(task, line: str) -> None:
    """向 task.log 追加一行北京时间戳日志（不 commit，事务由调用方控制）。"""
    task.log = (task.log or "") + f"[{now_bj().strftime('%H:%M:%S')}] {line}\n"
    if len(task.log) > LOG_CAP:
        task.log = task.log[-LOG_CAP:]
