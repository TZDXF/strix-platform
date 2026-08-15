"""扫描结果中文翻译：调用公司 LLM 网关（OpenAI 兼容 /chat/completions）。

用于 report_lang=zh 的任务：即便提示词已要求中文撰写，也作为兜底把
标题/描述/修复建议翻译成简体中文存入 *_zh 字段。
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from sqlalchemy import select

from .celery_app import celery_app
from .config import get_settings
from .db import SessionLocal
from .models import Finding, Task

SYSTEM_PROMPT = (
    "You are a professional translator for security vulnerability reports. "
    "Translate the given fields into Simplified Chinese (简体中文). "
    "Keep technical terms (CVE/CWE IDs, code, endpoints) unchanged. "
    "Respond ONLY with a JSON array, same order and same 'id' fields, "
    "each item: {\"id\": <int>, \"title\": \"...\", \"description\": \"...\", \"remediation\": \"...\"}."
)

_MAX_CHARS = 40_000  # 单次请求文本上限，超出分批


def _chat(messages: list[dict[str, str]], model: str, timeout: int = 300) -> str:
    s = get_settings()
    if not s.llm_api_base:
        raise RuntimeError("未配置 LLM_API_BASE")
    body = json.dumps({"model": model or s.strix_llm, "messages": messages, "temperature": 0}).encode("utf-8")
    req = urllib.request.Request(
        s.llm_api_base.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型未返回 JSON 数组")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("模型返回的不是数组")
    return data


def translate_findings(task_id: str) -> int:
    """把任务下所有 finding 翻译为中文，返回成功条数。失败抛异常（由调用方记状态）。"""
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return 0
        findings = db.execute(select(Finding).where(Finding.task_id == task_id)).scalars().all()
        todo = [f for f in findings if not f.title_zh]
        done = 0
        batch: list[Finding] = []
        batch_chars = 0

        def flush() -> None:
            nonlocal done, batch_chars
            if not batch:
                return
            payload = [
                {
                    "id": f.id,
                    "title": (f.title or "")[:2000],
                    "description": (f.description or "")[:8000],
                    "remediation": (f.remediation or "")[:8000],
                }
                for f in batch
            ]
            reply = _chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                model=task.model or "",
            )
            by_id = {item.get("id"): item for item in _extract_json_array(reply) if isinstance(item, dict)}
            for f in batch:
                item = by_id.get(f.id)
                if item:
                    f.title_zh = str(item.get("title") or f.title)
                    f.description_zh = str(item.get("description") or f.description)
                    f.remediation_zh = str(item.get("remediation") or f.remediation)
                    done += 1
            db.commit()
            batch.clear()
            batch_chars = 0

        for f in todo:
            size = len(f.title or "") + len(f.description or "") + len(f.remediation or "")
            if batch and batch_chars + size > _MAX_CHARS:
                flush()
            batch.append(f)
            batch_chars += size
        flush()
        return done
    finally:
        db.close()


@celery_app.task(name="translate_findings", bind=True, max_retries=2)
def translate_findings_task(self, task_id: str) -> dict:
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return {"error": "task not found"}
        try:
            n = translate_findings(task_id)
            task.zh_status = "done"
            db.commit()
            return {"task_id": task_id, "translated": n}
        except Exception as exc:  # noqa: BLE001
            task.zh_status = "failed"
            db.commit()
            try:
                raise self.retry(exc=exc, countdown=60)
            except self.MaxRetriesExceededError:
                return {"task_id": task_id, "error": str(exc)}
    finally:
        db.close()
