"""FastAPI 入口：任务提交/查询/报告/产物 API。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .artifacts import artifact_response_path, presigned_artifact_url
from .config import get_settings
from .db import get_db, init_db
from .models import AuditEntry, Finding, Task
from .runner import read_run_artifacts  # noqa: F401（契约复用说明）
from .security import client_ip, require_token
from .targets import check_target_allowed
from .tasks import new_task_id, run_scan

settings = get_settings()

app = FastAPI(title="Strix 内部安全测试平台", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 内网部署；生产建议收紧为前端域名
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    Path(settings.workspace_root).mkdir(parents=True, exist_ok=True)
    (Path(settings.workspace_root) / "uploads").mkdir(exist_ok=True)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0", "engine": f"strix {settings.strix_version}"}


def _audit(db: Session, request: Request, action: str, detail: str = "") -> None:
    db.add(AuditEntry(client_ip=client_ip(request), action=action, detail=detail[:2000]))
    db.commit()


def _task_summary(t: Task) -> dict:
    return {
        "id": t.id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "status": t.status,
        "scan_mode": t.scan_mode,
        "source_type": t.source_type,
        "source_ref": t.source_ref,
        "test_url": t.test_url,
        "findings_count": t.findings_count,
        "severity_counts": json.loads(t.severity_counts) if t.severity_counts else {},
        "duration_sec": t.duration_sec,
        "exit_code": t.exit_code,
        "timed_out": t.timed_out,
        "total_tokens": t.total_tokens,
        "error": t.error or "",
    }


@app.post("/api/tasks", dependencies=[Depends(require_token)])
async def create_task(
    request: Request,
    db: Session = Depends(get_db),
    scan_mode: str = Form("quick"),
    test_url: str = Form(""),
    git_url: str = Form(""),
    file: UploadFile | None = File(default=None),
):
    if scan_mode not in ("quick", "standard", "deep"):
        raise HTTPException(400, "scan_mode 必须是 quick/standard/deep")

    # 黑盒目标允许清单校验（护栏③）
    if test_url:
        ok, reason = check_target_allowed(test_url)
        if not ok:
            raise HTTPException(400, reason)

    task_id = new_task_id()
    if file is not None and file.filename:
        source_type = "zip"
        source_ref = file.filename
        upload_dir = Path(settings.workspace_root) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        zip_path = upload_dir / f"{task_id}.zip"
        cap = settings.max_upload_mb * 1024 * 1024
        size = 0
        try:
            with zip_path.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(400, f"压缩包超出上限 {settings.max_upload_mb}MB")
                    out.write(chunk)
        except HTTPException:
            zip_path.unlink(missing_ok=True)
            raise
        if size == 0:
            zip_path.unlink(missing_ok=True)
            raise HTTPException(400, "上传的压缩包为空")
    elif git_url:
        source_type = "git"
        source_ref = git_url
    else:
        raise HTTPException(400, "需要提供 git_url 或上传 zip 压缩包")

    task = Task(
        id=task_id, scan_mode=scan_mode, source_type=source_type,
        source_ref=source_ref, test_url=test_url, status="pending",
    )
    db.add(task)
    db.commit()
    _audit(db, request, "task.submit", f"id={task_id} mode={scan_mode} source={source_type}:{source_ref[:200]} url={test_url}")
    run_scan.delay(task_id)
    return {"id": task_id, "status": "pending"}


@app.get("/api/tasks", dependencies=[Depends(require_token)])
def list_tasks(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    status: str = "",
):
    q = select(Task).order_by(desc(Task.created_at)).offset(offset).limit(min(limit, 200))
    if status:
        q = q.where(Task.status == status)
    rows = db.execute(q).scalars().all()
    total = db.execute(select(func.count(Task.id))).scalar() or 0
    return {"total": total, "items": [_task_summary(t) for t in rows]}


def _get_task(db: Session, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


@app.get("/api/tasks/{task_id}", dependencies=[Depends(require_token)])
def task_detail(task_id: str, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    findings = db.execute(
        select(Finding).where(Finding.task_id == task_id)
        .order_by(Finding.severity, Finding.cvss.desc())
    ).scalars().all()
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings = sorted(findings, key=lambda f: (order.get(f.severity, 9), -(f.cvss or 0)))
    return {
        **_task_summary(task),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "attempts": task.attempts,
        "run_dir_name": task.run_dir_name,
        "model": task.model,
        "strix_version": task.strix_version,
        "has_artifacts": bool(task.artifacts_ref),
        "findings": [
            {
                "id": f.id,
                "vuln_id": f.vuln_id,
                "title": f.title,
                "severity": f.severity,
                "cvss": f.cvss,
                "cwe": f.cwe,
                "endpoint": f.endpoint,
                "has_poc": f.has_poc,
                "description": f.description,
                "remediation": f.remediation,
            }
            for f in findings
        ],
    }


@app.get("/api/tasks/{task_id}/log", dependencies=[Depends(require_token)])
def task_log(task_id: str, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    return {"log": task.log or ""}


@app.get("/api/tasks/{task_id}/artifacts", dependencies=[Depends(require_token)])
def task_artifacts(task_id: str, request: Request, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if not task.artifacts_ref:
        raise HTTPException(404, "产物尚未归档")
    _audit(db, request, "task.artifacts", f"id={task_id}")
    if task.artifacts_ref.startswith("s3://"):
        url = presigned_artifact_url(task.artifacts_ref)
        if url:
            return {"url": url}
        raise HTTPException(502, "对象存储不可用")
    path = artifact_response_path(task.artifacts_ref)
    if path is None:
        raise HTTPException(404, "产物文件缺失")
    return FileResponse(path, filename=f"{task_id}-artifacts.zip", media_type="application/zip")
