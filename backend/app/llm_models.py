"""平台可用模型：存于 platform_models 表，由超管在设置页维护。"""

from __future__ import annotations

import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import PlatformModel

MODEL_NAME_RE = re.compile(r"[A-Za-z0-9._/-]+")


def valid_model_name(name: str) -> bool:
    return bool(name) and len(name) <= 128 and MODEL_NAME_RE.fullmatch(name) is not None


def seed_platform_models(db: Session) -> None:
    """（保留钩子；表已有数据时为空操作。）"""
    pass


def platform_models(db: Session) -> list[str]:
    rows = db.execute(select(PlatformModel).order_by(PlatformModel.id)).scalars().all()
    return [r.name for r in rows]


def default_model(db: Session) -> str:
    """平台默认模型；无默认标记时取第一个，表为空则回退到 STRIX_LLM 环境变量。"""
    row = db.execute(
        select(PlatformModel).where(PlatformModel.is_default == True).order_by(PlatformModel.id).limit(1)  # noqa: E712
    ).scalar_one_or_none()
    if row is None:
        row = db.execute(select(PlatformModel).order_by(PlatformModel.id).limit(1)).scalar_one_or_none()
    return row.name if row else get_settings().strix_llm


def discover_models(api_key: str) -> list[str]:
    """用密钥查询 LLM 网关（OpenAI 兼容 /models）的可用模型列表。"""
    s = get_settings()
    if not s.llm_api_base:
        raise RuntimeError("平台未配置 LLM_API_BASE，无法查询网关模型")
    resp = httpx.get(
        s.llm_api_base.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    if resp.status_code in (401, 403):
        raise RuntimeError("密钥无效或无权限")
    resp.raise_for_status()
    data = resp.json().get("data") or []
    names = {str(item.get("id", "")).strip() for item in data if isinstance(item, dict)}
    return sorted(n for n in names if n)
