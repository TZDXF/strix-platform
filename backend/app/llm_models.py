"""平台可用模型：存于 platform_models 表，由超管在设置页维护。

首次启动若表为空，用环境变量 FREE_MODELS 播种（保证存量部署升级后模型列表不丢），
之后以表内数据为准。超管通过「密钥查询网关 /models → 挑选加入」的方式扩充列表。
"""

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
    """表为空时从 FREE_MODELS 环境变量播种，默认值取 STRIX_LLM（不在列表内则取第一个）。"""
    if db.execute(select(PlatformModel.id).limit(1)).scalar() is not None:
        return
    s = get_settings()
    names = [m.strip() for m in s.free_models.split(",") if m.strip()]
    for i, name in enumerate(names):
        db.add(PlatformModel(name=name, is_default=(name == s.strix_llm) or (i == 0 and s.strix_llm not in names)))
    db.commit()


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
