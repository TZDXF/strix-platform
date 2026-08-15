"""免费模型列表：来自环境变量 FREE_MODELS（逗号分隔），不访问网关。"""

from __future__ import annotations

from .config import get_settings


def free_models() -> list[str]:
    raw = get_settings().free_models
    return [m.strip() for m in raw.split(",") if m.strip()]
