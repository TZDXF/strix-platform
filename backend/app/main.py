"""FastAPI 入口：认证 / 用户管理（超管） / 项目 / 任务发布与查询 / 报告与产物 API。"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .artifacts import artifact_response_path, presigned_artifact_url
from .auth import (
    bootstrap_admin,
    client_ip,
    create_token,
    hash_password,
    require_admin,
    require_user,
    verify_password,
)
from .config import get_settings
from .db import get_db, init_db
from .llm_models import free_models
from .models import AuditEntry, Finding, Project, ProjectUpload, Task, User
from .runner import read_run_artifacts  # noqa: F401（契约复用说明）
from .sources import SourceError, list_branches
from .targets import check_target_allowed
from .tasks import new_task_id, run_scan

settings = get_settings()

app = FastAPI(title="Strix 内部安全测试平台", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 内网部署；生产建议收紧为前端域名
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    ws = Path(settings.workspace_root)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "uploads").mkdir(exist_ok=True)
    (ws / "reports").mkdir(exist_ok=True)
    (ws / "uploads" / "projects").mkdir(exist_ok=True)
    from .db import SessionLocal

    db = SessionLocal()
    try:
        bootstrap_admin(db)
    finally:
        db.close()


def _audit(db: Session, request: Request, action: str, detail: str = "", user=None) -> None:
    db.add(AuditEntry(
        user_id=user.id if user else getattr(request.state, "user_id", None),
        client_ip=client_ip(request), action=action, detail=detail[:2000],
    ))
    db.commit()


# ===================== 认证 =====================


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.username == body.username.strip())).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash or ""):
        _audit(db, request, "auth.login_failed", f"username={body.username}", user=user)
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已被停用，请联系管理员")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    _audit(db, request, "auth.login", f"username={user.username}", user=user)
    return {"token": create_token(user), "user": _user_out(user)}


def _user_out(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "display_name": u.display_name or u.username,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


@app.get("/api/auth/me", dependencies=[Depends(require_user)])
def me(user: User = Depends(require_user)):
    return _user_out(user)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": "0.2.0", "engine": f"strix {settings.strix_version}"}


# ===================== 用户管理（仅超管） =====================


@app.get("/api/users", dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    rows = db.execute(select(User).order_by(User.id)).scalars().all()
    return {"items": [_user_out(u) for u in rows]}


class UserCreateBody(BaseModel):
    username: str
    password: str
    role: str = "user"
    display_name: str = ""


@app.post("/api/users", dependencies=[Depends(require_admin)])
def create_user(body: UserCreateBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    username = body.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,64}", username):
        raise HTTPException(400, "用户名需为 2-64 位字母/数字/_.-")
    if len(body.password) < 8:
        raise HTTPException(400, "密码至少 8 位")
    if body.role not in ("admin", "user"):
        raise HTTPException(400, "role 必须是 admin 或 user")
    if db.execute(select(func.count(User.id)).where(User.username == username)).scalar():
        raise HTTPException(400, "用户名已存在")
    user = User(
        username=username, password_hash=hash_password(body.password),
        role=body.role, display_name=body.display_name.strip()[:128],
    )
    db.add(user)
    db.commit()
    _audit(db, request, "user.create", f"id={user.id} username={username} role={body.role}", user=admin)
    return _user_out(user)


class UserPatchBody(BaseModel):
    password: str = ""
    role: str = ""
    display_name: str = ""
    is_active: bool | None = None


@app.patch("/api/users/{user_id}", dependencies=[Depends(require_admin)])
def patch_user(user_id: int, body: UserPatchBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(400, "密码至少 8 位")
        user.password_hash = hash_password(body.password)
    if body.role:
        if body.role not in ("admin", "user"):
            raise HTTPException(400, "role 必须是 admin 或 user")
        _guard_last_admin(db, user, role=body.role)
        user.role = body.role
    if body.display_name:
        user.display_name = body.display_name.strip()[:128]
    if body.is_active is not None:
        _guard_last_admin(db, user, is_active=body.is_active)
        user.is_active = body.is_active
    db.commit()
    _audit(db, request, "user.patch", f"id={user_id} active={body.is_active} role={body.role}", user=admin)
    return _user_out(user)


@app.delete("/api/users/{user_id}", dependencies=[Depends(require_admin)])
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    if user.id == admin.id:
        raise HTTPException(400, "不能删除自己")
    _guard_last_admin(db, user, deleting=True)
    db.delete(user)
    db.commit()
    _audit(db, request, "user.delete", f"id={user_id} username={user.username}", user=admin)
    return {"ok": True}


def _guard_last_admin(db: Session, user: User, role: str = "", is_active: bool | None = None, deleting: bool = False) -> None:
    """防止把系统里最后一个可用超管降级/停用/删除。"""
    if user.role != "admin":
        return
    would_lose = deleting or role == "user" or is_active is False
    if not would_lose:
        return
    admins = db.execute(
        select(func.count(User.id)).where(User.role == "admin", User.is_active == True, User.id != user.id)  # noqa: E712
    ).scalar() or 0
    if admins == 0:
        raise HTTPException(400, "系统至少需要一个可用的超管账号")


# ===================== 项目 =====================


def _project_out(p: Project, db: Session) -> dict:
    tasks_count = db.execute(select(func.count(Task.id)).where(Task.project_id == p.id)).scalar() or 0
    uploads_count = db.execute(select(func.count(ProjectUpload.id)).where(ProjectUpload.project_id == p.id)).scalar() or 0
    creator = db.get(User, p.created_by) if p.created_by else None
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description or "",
        "source_type": p.source_type,
        "git_url": p.git_url or "",
        "git_auth_type": p.git_auth_type or "",
        "has_credentials": bool(p.git_token or p.git_ssh_key),
        "default_test_url": p.default_test_url or "",
        "created_by": p.created_by,
        "created_by_name": creator.username if creator else "-",
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "tasks_count": tasks_count,
        "uploads_count": uploads_count,
    }


def _get_project_checked(db: Session, project_id: str, user: User) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "项目不存在")
    if user.role != "admin" and project.created_by != user.id:
        raise HTTPException(403, "无权访问该项目")
    return project


@app.get("/api/projects", dependencies=[Depends(require_user)])
def list_projects(user: User = Depends(require_user), db: Session = Depends(get_db)):
    q = select(Project).order_by(desc(Project.created_at))
    if user.role != "admin":
        q = q.where(Project.created_by == user.id)
    rows = db.execute(q).scalars().all()
    return {"items": [_project_out(p, db) for p in rows]}


class ProjectCreateBody(BaseModel):
    name: str
    description: str = ""
    source_type: str = "git"
    git_url: str = ""
    git_auth_type: str = ""
    git_token: str = ""
    git_ssh_key: str = ""
    default_test_url: str = ""


@app.post("/api/projects", dependencies=[Depends(require_user)])
def create_project(body: ProjectCreateBody, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    name = body.name.strip()
    if not (1 <= len(name) <= 128):
        raise HTTPException(400, "项目名需为 1-128 个字符")
    if body.source_type not in ("git", "zip"):
        raise HTTPException(400, "source_type 必须是 git 或 zip")
    if body.source_type == "git":
        if not body.git_url.strip():
            raise HTTPException(400, "Git 项目必须填写仓库地址")
        if body.git_auth_type not in ("", "token", "ssh"):
            raise HTTPException(400, "git_auth_type 必须是 token / ssh / 留空")
        if body.git_auth_type == "token" and not body.git_token.strip():
            raise HTTPException(400, "选择 token 认证时必须填写 token")
        if body.git_auth_type == "ssh" and not body.git_ssh_key.strip():
            raise HTTPException(400, "选择 SSH 认证时必须填写私钥")
    if body.default_test_url:
        ok, reason = check_target_allowed(body.default_test_url)
        if not ok:
            raise HTTPException(400, reason)
    project = Project(
        id=uuid.uuid4().hex, name=name, description=body.description.strip()[:2000],
        source_type=body.source_type, git_url=body.git_url.strip(),
        git_auth_type=body.git_auth_type, git_token=body.git_token.strip(),
        git_ssh_key=body.git_ssh_key, default_test_url=body.default_test_url.strip(),
        created_by=user.id,
    )
    db.add(project)
    db.commit()
    _audit(db, request, "project.create", f"id={project.id} name={name} type={body.source_type}", user=user)
    return _project_out(project, db)


class ProjectPatchBody(BaseModel):
    name: str = ""
    description: str = ""
    git_url: str = ""
    git_auth_type: str = ""
    git_token: str = ""
    git_ssh_key: str = ""
    default_test_url: str = ""


@app.patch("/api/projects/{project_id}", dependencies=[Depends(require_user)])
def patch_project(project_id: str, body: ProjectPatchBody, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = _get_project_checked(db, project_id, user)
    if body.name:
        project.name = body.name.strip()[:128]
    if body.description:
        project.description = body.description.strip()[:2000]
    if body.git_url:
        project.git_url = body.git_url.strip()
    if body.default_test_url:
        ok, reason = check_target_allowed(body.default_test_url)
        if not ok:
            raise HTTPException(400, reason)
        project.default_test_url = body.default_test_url.strip()
    if body.git_auth_type:
        if body.git_auth_type not in ("", "token", "ssh"):
            raise HTTPException(400, "git_auth_type 必须是 token / ssh / 留空")
        project.git_auth_type = body.git_auth_type
    if body.git_token:
        project.git_token = body.git_token.strip()
    if body.git_ssh_key:
        project.git_ssh_key = body.git_ssh_key
    db.commit()
    _audit(db, request, "project.patch", f"id={project_id}", user=user)
    return _project_out(project, db)


@app.delete("/api/projects/{project_id}", dependencies=[Depends(require_user)])
def delete_project(project_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = _get_project_checked(db, project_id, user)
    running = db.execute(
        select(func.count(Task.id)).where(
            Task.project_id == project_id,
            Task.status.in_(["pending", "fetching", "scanning", "parsing"]),
        )
    ).scalar() or 0
    if running:
        raise HTTPException(400, "项目下还有执行中的任务，无法删除")
    db.execute(Task.__table__.delete().where(Task.project_id == project_id))  # 历史任务归属项目，随项目删除
    db.execute(ProjectUpload.__table__.delete().where(ProjectUpload.project_id == project_id))
    db.delete(project)
    db.commit()
    _audit(db, request, "project.delete", f"id={project_id} name={project.name}", user=user)
    return {"ok": True}


@app.get("/api/projects/{project_id}", dependencies=[Depends(require_user)])
def project_detail(project_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = _get_project_checked(db, project_id, user)
    uploads = db.execute(
        select(ProjectUpload).where(ProjectUpload.project_id == project_id).order_by(desc(ProjectUpload.created_at))
    ).scalars().all()
    tasks = db.execute(
        select(Task).where(Task.project_id == project_id).order_by(desc(Task.created_at)).limit(50)
    ).scalars().all()
    return {
        **_project_out(project, db),
        "uploads": [
            {
                "id": u.id, "filename": u.filename, "size_bytes": u.size_bytes,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in uploads
        ],
        "tasks": [_task_summary(t, db) for t in tasks],
    }


@app.get("/api/projects/{project_id}/branches", dependencies=[Depends(require_user)])
def project_branches(project_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = _get_project_checked(db, project_id, user)
    if project.source_type != "git":
        raise HTTPException(400, "仅 Git 项目支持分支选择")
    try:
        branches = list_branches(
            project.git_url, auth_type=project.git_auth_type,
            token=project.git_token, ssh_key=project.git_ssh_key,
        )
    except SourceError as exc:
        raise HTTPException(400, str(exc))
    return {"items": branches}


# ===================== 项目内 zip 上传（历史上传复用） =====================


@app.post("/api/projects/{project_id}/uploads", dependencies=[Depends(require_user)])
async def project_upload(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    project = _get_project_checked(db, project_id, user)
    if project.source_type != "zip":
        raise HTTPException(400, "仅 zip 项目支持上传压缩包")
    record = await _save_upload(db, project_id, file, user)
    _audit(db, request, "project.upload", f"project={project_id} upload={record.id} file={record.filename}", user=user)
    return {"id": record.id, "filename": record.filename, "size_bytes": record.size_bytes}


async def _save_upload(db: Session, project_id: str, file: UploadFile, user: User) -> ProjectUpload:
    upload_id = uuid.uuid4().hex
    upload_dir = Path(settings.workspace_root) / "uploads" / "projects"
    upload_dir.mkdir(parents=True, exist_ok=True)
    zip_path = upload_dir / f"{upload_id}.zip"
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
    record = ProjectUpload(
        id=upload_id, project_id=project_id,
        filename=(file.filename or "upload.zip")[:255], size_bytes=size,
        stored_path=str(zip_path), uploaded_by=user.id,
    )
    db.add(record)
    db.commit()
    return record


@app.delete("/api/projects/{project_id}/uploads/{upload_id}", dependencies=[Depends(require_user)])
def delete_upload(project_id: str, upload_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    _get_project_checked(db, project_id, user)
    record = db.get(ProjectUpload, upload_id)
    if record is None or record.project_id != project_id:
        raise HTTPException(404, "上传记录不存在")
    running = db.execute(
        select(func.count(Task.id)).where(
            Task.upload_id == upload_id,
            Task.status.in_(["pending", "fetching", "scanning", "parsing"]),
        )
    ).scalar() or 0
    if running:
        raise HTTPException(400, "该上传正在被任务使用")
    Path(record.stored_path).unlink(missing_ok=True)
    db.delete(record)
    db.commit()
    _audit(db, request, "project.upload_delete", f"project={project_id} upload={upload_id}", user=user)
    return {"ok": True}


# ===================== 任务 =====================


def _task_summary(t: Task, db: Session) -> dict:
    project = db.get(Project, t.project_id) if t.project_id else None
    creator = db.get(User, t.created_by) if t.created_by else None
    return {
        "id": t.id,
        "project_id": t.project_id,
        "project_name": project.name if project else "-",
        "created_by": t.created_by,
        "created_by_name": creator.username if creator else "-",
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "status": t.status,
        "scan_mode": t.scan_mode,
        "source_type": t.source_type,
        "source_ref": t.source_ref,
        "branch": t.branch or "",
        "test_url": t.test_url,
        "report_lang": t.report_lang,
        "zh_status": t.zh_status or "",
        "model": t.model or "",
        "findings_count": t.findings_count,
        "severity_counts": json.loads(t.severity_counts) if t.severity_counts else {},
        "duration_sec": t.duration_sec,
        "exit_code": t.exit_code,
        "timed_out": t.timed_out,
        "total_tokens": t.total_tokens,
        "error": t.error or "",
    }


@app.get("/api/models", dependencies=[Depends(require_user)])
def list_models() -> dict:
    """任务提交时可选的免费模型列表（来自环境变量 FREE_MODELS）。"""
    return {"default": settings.strix_llm, "items": free_models()}


@app.post("/api/projects/{project_id}/tasks", dependencies=[Depends(require_user)])
async def create_task(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    scan_mode: str = Form("quick"),
    test_url: str = Form(""),
    model: str = Form(""),
    branch: str = Form(""),
    upload_id: str = Form(""),
    report_lang: str = Form("en"),
    file: UploadFile | None = File(default=None),
    user: User = Depends(require_user),
):
    project = _get_project_checked(db, project_id, user)
    if scan_mode not in ("quick", "standard", "deep"):
        raise HTTPException(400, "scan_mode 必须是 quick/standard/deep")
    if report_lang not in ("en", "zh"):
        raise HTTPException(400, "report_lang 必须是 en 或 zh")

    # 模型可选：留空用平台默认；指定则必须在免费模型列表内
    model = (model or "").strip()
    if model:
        if len(model) > 64 or not re.fullmatch(r"[A-Za-z0-9._/-]+", model):
            raise HTTPException(400, "model 不合法")
        free = free_models()
        if free and model not in free:
            raise HTTPException(400, f"model 必须是 free 模型之一：{', '.join(free)}")

    # 黑盒目标允许清单校验
    if test_url:
        ok, reason = check_target_allowed(test_url)
        if not ok:
            raise HTTPException(400, reason)

    task_id = new_task_id()
    upload_ref: ProjectUpload | None = None
    branch = (branch or "").strip()
    if project.source_type == "git":
        source_type = "git"
        source_ref = project.git_url
        if branch and not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
            raise HTTPException(400, "branch 不合法")
    else:
        source_type = "zip"
        if file is not None and file.filename:
            # 新上传：落到项目历史上传，便于下次复用
            upload_ref = await _save_upload(db, project_id, file, user)
            source_ref = upload_ref.filename
        elif upload_id:
            upload_ref = db.get(ProjectUpload, upload_id)
            if upload_ref is None or upload_ref.project_id != project_id:
                raise HTTPException(400, "历史上传不存在")
            source_ref = upload_ref.filename
        else:
            raise HTTPException(400, "请选择历史上传或上传新的 zip 压缩包")

    task = Task(
        id=task_id, project_id=project_id, created_by=user.id,
        scan_mode=scan_mode, source_type=source_type,
        source_ref=source_ref, branch=branch, upload_id=upload_ref.id if upload_ref else None,
        test_url=test_url, model=model, report_lang=report_lang, status="pending",
    )
    db.add(task)
    db.commit()
    _audit(
        db, request, "task.submit",
        f"id={task_id} project={project_id} mode={scan_mode} model={model or 'default'} "
        f"source={source_type} branch={branch} upload={upload_ref.id if upload_ref else '-'} "
        f"lang={report_lang} url={test_url}",
        user=user,
    )
    run_scan.delay(task_id)
    return {"id": task_id, "status": "pending"}


@app.get("/api/tasks", dependencies=[Depends(require_user)])
def list_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    limit: int = 50,
    offset: int = 0,
    status: str = "",
    project_id: str = "",
    created_by: int = 0,
):
    q = select(Task).order_by(desc(Task.created_at))
    count_q = select(func.count(Task.id))
    if user.role != "admin":  # 普通用户只看自己的任务，超管看全部（可按创建人过滤）
        q = q.where(Task.created_by == user.id)
        count_q = count_q.where(Task.created_by == user.id)
    elif created_by:
        q = q.where(Task.created_by == created_by)
        count_q = count_q.where(Task.created_by == created_by)
    if project_id:
        q = q.where(Task.project_id == project_id)
        count_q = count_q.where(Task.project_id == project_id)
    if status:
        q = q.where(Task.status == status)
        count_q = count_q.where(Task.status == status)
    rows = db.execute(q.offset(offset).limit(min(limit, 200))).scalars().all()
    total = db.execute(count_q).scalar() or 0
    return {"total": total, "items": [_task_summary(t, db) for t in rows]}


def _get_task_checked(db: Session, task_id: str, user: User) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if user.role != "admin" and task.created_by != user.id:
        raise HTTPException(403, "无权访问该任务")
    return task


@app.get("/api/tasks/{task_id}", dependencies=[Depends(require_user)])
def task_detail(task_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = _get_task_checked(db, task_id, user)
    findings = db.execute(
        select(Finding).where(Finding.task_id == task_id)
        .order_by(Finding.severity, Finding.cvss.desc())
    ).scalars().all()
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings = sorted(findings, key=lambda f: (order.get(f.severity, 9), -(f.cvss or 0)))
    return {
        **_task_summary(task, db),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "attempts": task.attempts,
        "run_dir_name": task.run_dir_name,
        "strix_version": task.strix_version,
        "has_artifacts": bool(task.artifacts_ref),
        "has_report_md": bool(task.report_md),
        "report_md": task.report_md or "",
        "findings": [
            {
                "id": f.id,
                "vuln_id": f.vuln_id,
                "title": f.title,
                "title_zh": f.title_zh,
                "severity": f.severity,
                "cvss": f.cvss,
                "cwe": f.cwe,
                "endpoint": f.endpoint,
                "has_poc": f.has_poc,
                "description": f.description,
                "description_zh": f.description_zh,
                "remediation": f.remediation,
                "remediation_zh": f.remediation_zh,
                "poc_description": f.poc_description,
                "poc_code": f.poc_code,
            }
            for f in findings
        ],
    }


@app.get("/api/tasks/{task_id}/log", dependencies=[Depends(require_user)])
def task_log(task_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = _get_task_checked(db, task_id, user)
    return {"log": task.log or ""}


@app.get("/api/tasks/{task_id}/artifacts", dependencies=[Depends(require_user)])
def task_artifacts(task_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    task = _get_task_checked(db, task_id, user)
    if not task.artifacts_ref:
        raise HTTPException(404, "产物尚未归档")
    _audit(db, request, "task.artifacts", f"id={task_id}", user=user)
    if task.artifacts_ref.startswith("s3://"):
        url = presigned_artifact_url(task.artifacts_ref)
        if url:
            return {"url": url}
        raise HTTPException(502, "对象存储不可用")
    path = artifact_response_path(task.artifacts_ref)
    if path is None:
        raise HTTPException(404, "产物文件缺失")
    return FileResponse(path, filename=f"{task_id}-artifacts.zip", media_type="application/zip")


@app.get("/api/tasks/{task_id}/report.pdf", dependencies=[Depends(require_user)])
def task_report_pdf(task_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """导出官方风格 PDF 报告（复用 strix 自带 reportlab 渲染，结果缓存本地）。"""
    task = _get_task_checked(db, task_id, user)
    if not task.run_dir_name:
        raise HTTPException(404, "任务还没有可导出的报告")
    cache_path = Path(settings.workspace_root) / "reports" / f"{task_id}.pdf"
    if not cache_path.is_file():
        run_dir = Path(settings.workspace_root) / task_id / "scan" / "strix_runs" / task.run_dir_name
        if not run_dir.is_dir():
            raise HTTPException(404, "run 工作区已清理，无法生成 PDF（可下载产物 zip 归档）")
        from strix.interface.viewer.report_pdf import generate_report_pdf

        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(generate_report_pdf(run_dir))
        except Exception as exc:  # noqa: BLE001
            cache_path.unlink(missing_ok=True)
            raise HTTPException(500, f"PDF 生成失败: {exc}")
    _audit(db, request, "task.report_pdf", f"id={task_id}", user=user)
    return FileResponse(cache_path, filename=f"strix-report-{task_id[:10]}.pdf", media_type="application/pdf")
