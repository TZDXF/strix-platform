"""扫描任务流水线：获取源码（项目/分支/凭据） → strix 执行 → 产物解析入库 → 归档 → 中文翻译。"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete

from .artifacts import archive_run
from .celery_app import celery_app
from .config import get_settings
from .db import SessionLocal
from .llm_models import default_model
from .models import Finding, Project, ProjectUpload, Task, User
from .runner import execute_scan, read_run_artifacts
from .sources import SourceError, clone_git, du_mb, safe_extract_zip
from .translate import translate_findings_task

LOG_CAP = 200_000  # task.log 滚动上限（字符）

ZH_INSTRUCTION = (
    "Please write the entire report in Simplified Chinese (简体中文): "
    "vulnerability titles, descriptions, PoC explanations and remediation steps. "
    "Keep technical identifiers (CVE/CWE, code, endpoints) unchanged."
)


def _log(task, line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    entry = f"[{stamp}] {line}\n"
    task.log = (task.log or "") + entry
    if len(task.log) > LOG_CAP:
        task.log = task.log[-LOG_CAP:]


def _set_status(db, task, status: str) -> None:
    task.status = status
    task.updated_at = datetime.now(timezone.utc)
    db.commit()


@celery_app.task(name="run_scan", bind=True, max_retries=1)
def run_scan(self, task_id: str) -> dict:
    s = get_settings()
    db = SessionLocal()
    task = db.get(Task, task_id)
    if task is None:
        db.close()
        return {"error": "task not found"}

    project = db.get(Project, task.project_id) if task.project_id else None
    creator = db.get(User, task.created_by) if task.created_by else None
    user_llm_key = (creator.llm_api_key or "") if creator else ""
    if not user_llm_key:
        task.error = "创建者未配置个人 AI 密钥，无法执行扫描；请先在「设置」中配置后再提交任务"
        _log(task, f"[fail] {task.error}")
        task.finished_at = datetime.now(timezone.utc)
        _set_status(db, task, "failed")
        db.close()
        return {"task_id": task_id, "error": task.error}

    ws = Path(s.workspace_root)
    task_ws = ws / task_id
    src_dir = task_ws / "src"
    scan_dir = task_ws / "scan"
    artifacts_dir = ws / "artifacts"
    task.model = task.model or default_model(db)  # 平台默认模型（platform_models 表，超管维护）
    task.strix_version = s.strix_version
    task.attempts = 0

    try:
        task.started_at = datetime.now(timezone.utc)
        _set_status(db, task, "fetching")

        # ---- Stage 1: 获取源码 ----
        import shutil

        if src_dir.exists():  # 重派发/重试时清掉上次残留，保证可幂等重跑
            shutil.rmtree(src_dir, ignore_errors=True)
        src_dir.mkdir(parents=True, exist_ok=True)
        if task.source_type == "git":
            clone_git(
                task.source_ref, src_dir, lambda m: (_log(task, m), db.commit()),
                branch=task.branch or "",
                auth_type=(project.git_auth_type if project else ""),
                token=(project.git_token if project else ""),
            )
        else:
            upload = db.get(ProjectUpload, task.upload_id) if task.upload_id else None
            zip_path = Path(upload.stored_path) if upload and upload.stored_path else ws / "uploads" / f"{task_id}.zip"
            if not zip_path.is_file():
                raise SourceError("上传的压缩包丢失，请重新提交")
            safe_extract_zip(
                zip_path, src_dir, s.max_upload_mb * 1024 * 1024,
                lambda m: (_log(task, m), db.commit()),
            )
        _log(task, f"[fetch] 源码就绪（{du_mb(src_dir)}MB）")

        # ---- Stage 2: strix 扫描（用户自定义指令 + 中文报告提示词均通过 --instruction 注入）----
        _set_status(db, task, "scanning")
        _log(task, f"[scan] 模型: {task.model}（中文报告）")
        instruction = f"{task.instruction or ''}\n{ZH_INSTRUCTION}".strip()
        result = execute_scan(
            work_dir=scan_dir,
            src_dir=src_dir,
            test_url=task.test_url or "",
            scan_mode=task.scan_mode,
            model=task.model or "",
            instruction=instruction,
            llm_api_key=user_llm_key,
            log=lambda m: (_log(task, m), db.commit()),
        )
        task.exit_code = result["exit_code"]
        task.attempts = result["attempts"]
        task.timed_out = result["timed_out"]
        task.run_dir_name = result["run_dir_name"]

        # ---- Stage 3: 产物解析入库 ----
        _set_status(db, task, "parsing")
        run_record, vulns = read_run_artifacts(scan_dir, result["run_dir_name"])
        usage = run_record.get("llm_usage") or {}
        task.total_tokens = usage.get("total_tokens")
        task.llm_requests = usage.get("requests")
        # 官方执行摘要报告（strix view 展示的同款 penetration_test_report.md）
        report_path = scan_dir / "strix_runs" / result["run_dir_name"] / "penetration_test_report.md"
        try:
            task.report_md = report_path.read_text(encoding="utf-8")[:500_000]
        except OSError:
            task.report_md = ""

        db.execute(delete(Finding).where(Finding.task_id == task_id))
        counts: dict[str, int] = {}
        for v in vulns:
            sev = str(v.get("severity", "info")).lower()
            counts[sev] = counts.get(sev, 0) + 1
            db.add(
                Finding(
                    task_id=task_id,
                    vuln_id=str(v.get("id", "")),
                    title=str(v.get("title", "")),
                    severity=sev,
                    cvss=v.get("cvss"),
                    cwe=str(v.get("cwe", "") or ""),
                    cve=str(v.get("cve", "") or ""),
                    endpoint=str(v.get("endpoint", "") or ""),
                    target=str(v.get("target", "") or ""),
                    has_poc=bool(v.get("poc_script_code") or v.get("poc_description")),
                    description=str(v.get("description", "") or ""),
                    remediation=str(v.get("remediation_steps", "") or ""),
                    poc_description=str(v.get("poc_description", "") or ""),
                    poc_code=str(v.get("poc_script_code", "") or ""),
                    raw=json.dumps(v, ensure_ascii=False),
                )
            )
        task.findings_count = len(vulns)
        task.severity_counts = json.dumps(counts, ensure_ascii=False)
        _log(
            task,
            f"[parse] 漏洞 {len(vulns)} 条 {json.dumps(counts, ensure_ascii=False)} "
            f"tokens={usage.get('total_tokens', '-')} 退出码={task.exit_code}",
        )

        # ---- Stage 4: 归档 ----
        try:
            task.artifacts_ref = archive_run(scan_dir, result["run_dir_name"], task_id, artifacts_dir)
            _log(task, f"[archive] 产物已归档: {task.artifacts_ref}")
        except Exception as exc:  # 归档失败不影响结果
            _log(task, f"[archive] 归档失败（不影响结果）: {exc}")

        task.finished_at = datetime.now(timezone.utc)
        if task.started_at:
            task.duration_sec = int((task.finished_at - task.started_at).total_seconds())
        _log(
            task,
            f"[done] 状态: {'超时终止' if task.timed_out else '完成'}，"
            f"耗时 {task.duration_sec} 秒，发现 {task.findings_count} 条",
        )
        _set_status(db, task, "done")

        # ---- Stage 5: 中文翻译（提示词要求中文撰写失败时的兜底）----
        if task.findings_count > 0:
            task.zh_status = "pending"
            db.commit()
            _log(task, "[translate] 调度中文翻译任务")
            translate_findings_task.delay(task_id)
        return {"task_id": task_id, "exit_code": task.exit_code, "findings": task.findings_count}

    except SourceError as exc:
        task.error = f"源码获取失败: {exc}"
        _log(task, f"[fail] {task.error}")
        task.finished_at = datetime.now(timezone.utc)
        _set_status(db, task, "failed")
        return {"task_id": task_id, "error": task.error}
    except Exception as exc:  # noqa: BLE001 —— 流水线任何异常都要落到任务状态
        task.error = f"执行异常: {exc}"
        _log(task, f"[fail] {task.error}")
        task.finished_at = datetime.now(timezone.utc)
        _set_status(db, task, "failed")
        return {"task_id": task_id, "error": task.error}
    finally:
        db.close()


def new_task_id() -> str:
    return uuid.uuid4().hex
