"""FastAPI 入口：认证 / 用户管理（超管） / 项目 / 任务发布与查询 / 报告与产物 API。"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .artifacts import artifact_response_path, ensure_bucket, presigned_artifact_url
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
from .gitlab import GitLabError, list_projects as gitlab_list_projects, normalize_base_url, same_host, verify_token
from .llm_models import default_model, discover_models, platform_models, seed_platform_models, valid_model_name
from .mailer import (
    K_FROM, K_HOST, K_NOTIFY_DONE, K_NOTIFY_FAILED, K_PASSWORD, K_PORT,
    K_SENDER_NAME, K_SITE_URL, K_SSL, K_USE_TLS, K_USER,
    get_mail_settings, mail_configured, mail_settings_public, send_mail, set_mail_settings,
)
from .models import AuditEntry, Finding, GitConfig, PlatformModel, Project, ProjectUpload, Task, User
from .runner import read_run_artifacts  # noqa: F401（契约复用说明）
from .sources import (
    SourceError,
    dump_repos,
    effective_repo_branches,
    effective_repos,
    list_branches,
    load_repo_tokens,
    normalize_repos,
    parse_repo_branches,
    parse_repos,
    repo_credential,
)
from .targets import (
    check_target_allowed,
    dump_targets,
    effective_targets,
    normalize_targets,
    parse_targets,
    probe_target,
)
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
        seed_platform_models(db)  # 首次启动用 FREE_MODELS 播种平台模型表，之后以表内数据为准
    finally:
        db.close()
    if not ensure_bucket():
        print("[startup] S3 归档桶不可用（RustFS 未就绪或密钥错误），任务归档将失败；不影响扫描本身")


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
        "has_llm_key": bool(u.llm_api_key),
        "email": u.email or "",
    }


@app.get("/api/auth/me", dependencies=[Depends(require_user)])
def me(user: User = Depends(require_user)):
    return _user_out(user)


# ===================== 个人设置（登录用户自助） =====================


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/change-password", dependencies=[Depends(require_user)])
def change_password(body: ChangePasswordBody, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not verify_password(body.old_password, user.password_hash or ""):
        _audit(db, request, "auth.change_password_failed", f"username={user.username}", user=user)
        raise HTTPException(400, "原密码不正确")
    if len(body.new_password) < 8:
        raise HTTPException(400, "新密码至少 8 位")
    if body.new_password == body.old_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    _audit(db, request, "auth.change_password", f"username={user.username}", user=user)
    return {"ok": True}


class LlmKeyBody(BaseModel):
    api_key: str


@app.put("/api/me/llm-key", dependencies=[Depends(require_user)])
def set_llm_key(body: LlmKeyBody, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    key = body.api_key.strip()
    if not key:
        raise HTTPException(400, "密钥不能为空")
    if len(key) > 512:
        raise HTTPException(400, "密钥过长")
    user.llm_api_key = key
    db.commit()
    _audit(db, request, "me.llm_key_set", f"username={user.username}", user=user)  # 不记录密钥内容
    return _user_out(user)


@app.delete("/api/me/llm-key", dependencies=[Depends(require_user)])
def clear_llm_key(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    user.llm_api_key = ""
    db.commit()
    _audit(db, request, "me.llm_key_clear", f"username={user.username}", user=user)
    return _user_out(user)


class EmailBody(BaseModel):
    email: str


@app.put("/api/me/email", dependencies=[Depends(require_user)])
def set_notification_email(body: EmailBody, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """设置/清除个人通知邮箱：任务完成或失败时向该地址发送提醒邮件（需超管先在系统设置中配置 SMTP）。"""
    email = body.email.strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "邮箱格式不正确")
    user.email = email
    db.commit()
    _audit(db, request, "me.email_set" if email else "me.email_clear", f"username={user.username} email={email or '-'}", user=user)
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
    if body.role != "user":
        raise HTTPException(400, "不允许创建超管账号，超管只能由系统初始化")
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
    if body.role and body.role != user.role:
        raise HTTPException(400, "系统不允许修改用户角色")
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


# ===================== 平台模型管理（仅超管） =====================


def _model_out(m: PlatformModel) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "is_default": bool(m.is_default),
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _all_models(db: Session) -> list[PlatformModel]:
    return db.execute(select(PlatformModel).order_by(PlatformModel.id)).scalars().all()


@app.get("/api/admin/models", dependencies=[Depends(require_admin)])
def admin_list_models(db: Session = Depends(get_db)):
    return {"items": [_model_out(m) for m in _all_models(db)]}


class ModelDiscoverBody(BaseModel):
    api_key: str


@app.post("/api/admin/models/discover", dependencies=[Depends(require_admin)])
def admin_discover_models(
    body: ModelDiscoverBody, request: Request,
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
):
    """超管用网关密钥查询可用模型列表（OpenAI 兼容 /models），密钥仅本次使用、不落库。"""
    key = body.api_key.strip()
    if not key:
        raise HTTPException(400, "密钥不能为空")
    if len(key) > 512:
        raise HTTPException(400, "密钥过长")
    try:
        names = discover_models(key)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"网关查询失败：{exc}")
    _audit(db, request, "model.discover", f"found={len(names)}", user=admin)  # 不记录密钥内容
    return {"items": names}


class ModelAddBody(BaseModel):
    names: list[str]
    default: str = ""  # 可选：把本次添加的某个模型设为平台默认


@app.post("/api/admin/models", dependencies=[Depends(require_admin)])
def admin_add_models(
    body: ModelAddBody, request: Request,
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
):
    names = [n.strip() for n in body.names if n.strip()]
    if not names:
        raise HTTPException(400, "未选择任何模型")
    for name in names:
        if not valid_model_name(name):
            raise HTTPException(400, f"模型名不合法：{name}")
    default_name = (body.default or "").strip()
    if default_name and default_name not in names:
        raise HTTPException(400, "default 必须是本次添加的模型之一")
    existing = set(platform_models(db))
    has_default = db.execute(
        select(func.count(PlatformModel.id)).where(PlatformModel.is_default == True)  # noqa: E712
    ).scalar() or 0
    added: list[PlatformModel] = []
    for name in names:
        if name in existing:
            continue
        m = PlatformModel(name=name, created_by=admin.id)
        db.add(m)
        added.append(m)
        existing.add(name)
    if not added:
        db.rollback()
        raise HTTPException(400, "所选模型均已存在")
    if not has_default and not default_name:
        added[0].is_default = True  # 首次添加时自动指定默认，保证任务下拉可用
    db.commit()
    if default_name:
        row = db.execute(select(PlatformModel).where(PlatformModel.name == default_name)).scalar_one()
        for m in _all_models(db):
            m.is_default = m.id == row.id
        db.commit()
    _audit(db, request, "model.add", f"added={[m.name for m in added]} default={default_name or 'auto'}", user=admin)
    return {"items": [_model_out(m) for m in _all_models(db)]}


class ModelPatchBody(BaseModel):
    is_default: bool | None = None


@app.patch("/api/admin/models/{model_id}", dependencies=[Depends(require_admin)])
def admin_patch_model(
    model_id: int, body: ModelPatchBody, request: Request,
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
):
    m = db.get(PlatformModel, model_id)
    if m is None:
        raise HTTPException(404, "模型不存在")
    if body.is_default:
        for row in _all_models(db):
            row.is_default = row.id == m.id
        db.commit()
        _audit(db, request, "model.default", f"name={m.name}", user=admin)
    return _model_out(m)


@app.delete("/api/admin/models/{model_id}", dependencies=[Depends(require_admin)])
def admin_delete_model(
    model_id: int, request: Request,
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
):
    m = db.get(PlatformModel, model_id)
    if m is None:
        raise HTTPException(404, "模型不存在")
    name, was_default = m.name, bool(m.is_default)
    db.delete(m)
    db.commit()
    if was_default:  # 删除默认后由剩余第一个接替，保持默认始终存在
        first = db.execute(select(PlatformModel).order_by(PlatformModel.id).limit(1)).scalar_one_or_none()
        if first:
            first.is_default = True
            db.commit()
    _audit(db, request, "model.delete", f"name={name} was_default={was_default}", user=admin)
    return {"ok": True}


# ===================== 系统设置：邮件提醒（仅超管） =====================


class MailSettingsBody(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""  # 留空 = 保持已有密码不变
    clear_password: bool = False  # 显式清除已保存的密码
    smtp_use_tls: bool = True  # STARTTLS（常见 587）
    smtp_ssl: bool = False  # SMTPS 直连 TLS（常见 465），与 STARTTLS 互斥
    mail_from: str = ""  # 发件人邮箱；留空用 SMTP 用户名
    mail_sender_name: str = ""  # 发件人显示名；留空用「Strix 平台」
    site_url: str = ""  # 平台访问地址，用于拼接邮件里的任务链接
    notify_done: bool = True  # 任务完成时通知
    notify_failed: bool = True  # 任务失败时通知


@app.get("/api/admin/mail-settings", dependencies=[Depends(require_admin)])
def admin_get_mail_settings(db: Session = Depends(get_db)):
    return mail_settings_public(db)


@app.put("/api/admin/mail-settings", dependencies=[Depends(require_admin)])
def admin_save_mail_settings(
    body: MailSettingsBody, request: Request,
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
):
    host = body.smtp_host.strip()
    if host:
        if not 1 <= body.smtp_port <= 65535:
            raise HTTPException(400, "SMTP 端口需在 1-65535 之间")
        if body.smtp_ssl and body.smtp_use_tls:
            raise HTTPException(400, "SMTPS（465）与 STARTTLS（587）只能选一种加密方式")
    if len(body.smtp_password) > 256:
        raise HTTPException(400, "SMTP 密码过长")
    current = get_mail_settings(db)
    password = "" if body.clear_password else (body.smtp_password or current[K_PASSWORD])

    set_mail_settings(db, {
        K_HOST: host,
        K_PORT: str(body.smtp_port),
        K_USER: body.smtp_user.strip(),
        K_PASSWORD: password,
        K_USE_TLS: "1" if body.smtp_use_tls and not body.smtp_ssl else "0",
        K_SSL: "1" if body.smtp_ssl else "0",
        K_FROM: body.mail_from.strip(),
        K_SENDER_NAME: body.mail_sender_name.strip(),
        K_SITE_URL: body.site_url.strip(),
        K_NOTIFY_DONE: "1" if body.notify_done else "0",
        K_NOTIFY_FAILED: "1" if body.notify_failed else "0",
    }, user_id=admin.id)
    _audit(db, request, "mail.settings_save", f"host={host or '-'} port={body.smtp_port} ssl={body.smtp_ssl}", user=admin)
    return mail_settings_public(db)


class MailTestBody(BaseModel):
    to: str


@app.post("/api/admin/mail-settings/test", dependencies=[Depends(require_admin)])
def admin_test_mail_settings(
    body: MailTestBody, request: Request,
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
):
    """用已保存的配置真实发送一封测试邮件，验证 SMTP 连通性。"""
    to = body.to.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to):
        raise HTTPException(400, "收件邮箱格式不正确")
    s = get_mail_settings(db)
    if not mail_configured(s):
        raise HTTPException(400, "请先保存 SMTP 服务器配置")
    sender = s.get(K_FROM, "").strip() or s.get(K_USER, "")
    html = (
        '<div style="font-family:sans-serif;max-width:520px;">'
        '<p style="font-size:16px;font-weight:700;color:#2e9e5b;">测试邮件</p>'
        "<p>这是一封来自 Strix 安全测试平台的测试邮件；收到它说明邮件提醒配置正确，"
        "任务完成/失败时将向用户设置的通知邮箱发送提醒。</p>"
        "</div>"
    )
    try:
        send_mail(s, to, "[Strix] 邮件提醒配置测试", html)
    except Exception as exc:  # noqa: BLE001 —— 把 SMTP 报错原样反馈给管理员
        raise HTTPException(400, f"发送失败：{exc}")
    _audit(db, request, "mail.settings_test", f"to={to} from={sender}", user=admin)
    return {"ok": True, "detail": f"测试邮件已发送至 {to}"}


# ===================== 个人 Git 配置（GitLab） =====================


def _git_config_out(c: GitConfig) -> dict:
    return {
        "id": c.id,
        "name": c.name or c.base_url,
        "base_url": c.base_url,
        "has_token": bool(c.token),
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _get_git_config_checked(db: Session, config_id: str, user: User) -> GitConfig:
    config = db.get(GitConfig, config_id)
    if config is None or (user.role != "admin" and config.user_id != user.id):
        raise HTTPException(404, "Git 配置不存在")
    return config


@app.get("/api/git-configs", dependencies=[Depends(require_user)])
def list_git_configs(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.execute(select(GitConfig).where(GitConfig.user_id == user.id).order_by(GitConfig.id)).scalars().all()
    return {"items": [_git_config_out(c) for c in rows]}


class GitConfigCreateBody(BaseModel):
    name: str = ""
    base_url: str
    token: str


@app.post("/api/git-configs", dependencies=[Depends(require_user)])
def create_git_config(body: GitConfigCreateBody, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """保存个人 Git 服务配置；保存前真实验证令牌（调 /api/v4/user），无效直接报错。"""
    token = body.token.strip()
    if not token:
        raise HTTPException(400, "访问令牌不能为空")
    if len(token) > 512:
        raise HTTPException(400, "令牌过长")
    try:
        base_url = normalize_base_url(body.base_url)
    except GitLabError as exc:
        raise HTTPException(400, str(exc))
    try:
        info = verify_token(base_url, token)
    except GitLabError as exc:
        raise HTTPException(400, str(exc))
    config = GitConfig(
        id=uuid.uuid4().hex, user_id=user.id,
        name=(body.name.strip() or info.get("username", ""))[:128],
        base_url=base_url, token=token,
    )
    db.add(config)
    db.commit()
    _audit(db, request, "git_config.create", f"id={config.id} base_url={base_url} user={info.get('username')}", user=user)
    return _git_config_out(config)


class GitConfigPatchBody(BaseModel):
    name: str = ""
    base_url: str = ""
    token: str = ""


@app.patch("/api/git-configs/{config_id}", dependencies=[Depends(require_user)])
def patch_git_config(config_id: str, body: GitConfigPatchBody, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    config = _get_git_config_checked(db, config_id, user)
    base_url = config.base_url
    if body.base_url.strip():
        try:
            base_url = normalize_base_url(body.base_url)
        except GitLabError as exc:
            raise HTTPException(400, str(exc))
    token = body.token.strip()
    if len(token) > 512:
        raise HTTPException(400, "令牌过长")
    if body.name.strip():
        config.name = body.name.strip()[:128]
    if base_url != config.base_url or token:
        # 地址或令牌有变化时，用新令牌（未填则用旧令牌）对新地址做真实验证
        try:
            info = verify_token(base_url, token or config.token)
        except GitLabError as exc:
            raise HTTPException(400, str(exc))
        config.base_url = base_url
        if token:
            config.token = token
        _audit(db, request, "git_config.token_update", f"id={config_id} base_url={base_url} user={info.get('username')}", user=user)
    db.commit()
    _audit(db, request, "git_config.patch", f"id={config_id}", user=user)
    return _git_config_out(config)


@app.delete("/api/git-configs/{config_id}", dependencies=[Depends(require_user)])
def delete_git_config(config_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """删除 Git 配置；已用它创建的项目凭据已单独保存，不受影响。"""
    config = _get_git_config_checked(db, config_id, user)
    db.delete(config)
    db.commit()
    _audit(db, request, "git_config.delete", f"id={config_id}", user=user)
    return {"ok": True}


@app.get("/api/git-configs/{config_id}/projects", dependencies=[Depends(require_user)])
def git_config_projects(config_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """列出该 Git 服务下当前用户可见的仓库（带组织/命名空间信息），供创建项目时选择。"""
    config = _get_git_config_checked(db, config_id, user)
    try:
        items = gitlab_list_projects(config.base_url, config.token)
    except GitLabError as exc:
        raise HTTPException(400, str(exc))
    _audit(db, request, "git_config.list_projects", f"id={config_id} count={len(items)}", user=user)
    return {"items": items}


# ===================== 项目 =====================


def _project_out(p: Project, db: Session) -> dict:
    tasks_count = db.execute(select(func.count(Task.id)).where(Task.project_id == p.id)).scalar() or 0
    uploads_count = db.execute(select(func.count(ProjectUpload.id)).where(ProjectUpload.project_id == p.id)).scalar() or 0
    creator = db.get(User, p.created_by) if p.created_by else None
    targets = effective_targets(p.default_test_targets, p.default_test_url)
    repos = effective_repos(p.git_repos, p.git_url)
    repo_tokens = load_repo_tokens(p.repo_tokens)

    def _cred(url: str) -> str:
        if url in repo_tokens:
            return "repo"  # 仓库级专属令牌（表单逐仓库填写，或从个人 Git 配置按域名解析）
        return "project" if p.git_token else "none"  # 旧版项目级 PAT 兜底 / 无凭据

    return {
        "id": p.id,
        "name": p.name,
        "description": p.description or "",
        "source_type": p.source_type,
        "git_url": repos[0]["url"] if repos else "",  # 兼容旧字段：首个仓库
        "git_repos": [{**r, "credential": _cred(r["url"])} for r in repos],
        # 仅支持 PAT；存量 ssh 配置一律视为未配置凭据
        "git_auth_type": "token" if p.git_auth_type == "token" else "",
        "has_credentials": bool(p.git_token),
        "default_test_url": targets[0]["url"] if targets else "",  # 兼容旧字段：首个地址
        "default_test_targets": targets,
        "is_archived": bool(p.is_archived),
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


def _checked_targets(raw) -> list[dict]:
    """多目标入口共用：清洗 + 数量上限 + 逐个过黑盒地址允许清单；不通过直接 400。"""
    targets, err = normalize_targets(parse_targets(raw))
    if err:
        raise HTTPException(400, err)
    for t in targets:
        ok, reason = check_target_allowed(t["url"])
        if not ok:
            raise HTTPException(400, reason)
    return targets


def _find_config_for_host(db: Session, user: User, repo_url: str) -> GitConfig | None:
    """在操作者的个人 Git 配置中找与仓库地址同域的一条（用于自动解析凭据）。"""
    configs = db.execute(select(GitConfig).where(GitConfig.user_id == user.id).order_by(GitConfig.id)).scalars().all()
    return next((c for c in configs if c.token and same_host(c.base_url, repo_url)), None)


def _resolve_repo_tokens(db: Session, user: User, repos: list[dict], previous: dict[str, str] | None = None) -> dict[str, str]:
    """为仓库列表解析各仓库专属凭据快照 {url: token}。

    优先级：表单逐仓库显式填写的 token > 编辑前已有快照（令牌不回显，留空即保持）
    > 按域名从操作者个人 Git 配置自动获取（多仓库常来自不同 Git 服务，逐仓库各自解析）。
    已从列表删除的仓库随删随清。
    """
    tokens = {u: t for u, t in (previous or {}).items() if t}
    for r in repos:
        url = r["url"]
        explicit = (r.get("token") or "").strip()
        if explicit:
            tokens[url] = explicit
        elif url not in tokens:
            cfg = _find_config_for_host(db, user, url)
            if cfg:
                tokens[url] = cfg.token
    urls = {r["url"] for r in repos}
    return {u: t for u, t in tokens.items() if u in urls and t}


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
    git_config_id: str = ""  # 从个人 Git 配置导入时填写：凭据取自该配置
    git_repos: str | list | None = None  # 绑定的仓库列表：JSON 字符串或 [{"url","note"}]
    default_test_url: str = ""  # 旧版单地址（兼容旧客户端）
    default_test_targets: str | list | None = None  # 多目标列表：JSON 字符串或 [{"url","note"}]


@app.post("/api/projects", dependencies=[Depends(require_user)])
def create_project(body: ProjectCreateBody, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    name = body.name.strip()
    if not (1 <= len(name) <= 128):
        raise HTTPException(400, "项目名需为 1-128 个字符")
    if body.source_type not in ("git", "zip"):
        raise HTTPException(400, "source_type 必须是 git 或 zip")
    git_auth_type = body.git_auth_type
    git_token = body.git_token.strip()
    repos: list[dict] = []
    if body.source_type == "git":
        # 仓库列表：git_repos 优先（可绑定多个），旧 git_url 单仓库兼容
        if body.git_repos is not None:
            repos, err = normalize_repos(parse_repos(body.git_repos))
        elif body.git_url.strip():
            repos, err = normalize_repos([{"url": body.git_url, "note": ""}])
        else:
            repos, err = [], ""
        if err:
            raise HTTPException(400, err)
        if not repos:
            raise HTTPException(400, "Git 项目必须至少绑定一个仓库")
        if git_auth_type not in ("", "token"):
            raise HTTPException(400, "git_auth_type 必须是 token / 留空（仅支持 Personal Access Token）")
        if git_auth_type == "token" and not git_token:
            raise HTTPException(400, "选择 token 认证时必须填写 token")
        if body.git_config_id:
            # 从个人 Git 配置导入：校验归属与服务地址（每个仓库都须与配置同域），凭据复制自配置
            config = _get_git_config_checked(db, body.git_config_id, user)
            for r in repos:
                if not same_host(config.base_url, r["url"]):
                    raise HTTPException(400, f"仓库 {r['url']} 与所选 Git 配置的服务地址不一致")
            git_auth_type, git_token = "token", config.token
    if body.default_test_targets is not None:
        default_targets = _checked_targets(body.default_test_targets)
    elif body.default_test_url:
        default_targets = _checked_targets([{"url": body.default_test_url, "note": ""}])
    else:
        default_targets = []
    repo_tokens = _resolve_repo_tokens(db, user, repos) if repos else {}
    project = Project(
        id=uuid.uuid4().hex, name=name, description=body.description.strip()[:2000],
        source_type=body.source_type,
        git_url=repos[0]["url"] if repos else "",
        git_repos=dump_repos(repos),
        repo_tokens=json.dumps(repo_tokens, ensure_ascii=False) if repo_tokens else "",
        git_auth_type=git_auth_type, git_token=git_token,
        default_test_url=default_targets[0]["url"] if default_targets else "",
        default_test_targets=dump_targets(default_targets),
        created_by=user.id,
    )
    db.add(project)
    db.commit()
    _audit(db, request, "project.create", f"id={project.id} name={name} type={body.source_type}", user=user)
    return _project_out(project, db)


class ProjectPatchBody(BaseModel):
    name: str = ""
    description: str = ""
    git_url: str = ""  # 旧版单仓库（兼容旧客户端，仅在未提交列表时生效）
    git_repos: str | list | None = None  # 仓库列表：None=不修改；否则全量替换
    git_auth_type: str | None = None  # None=不修改，""=清除凭据
    git_token: str = ""
    default_test_url: str = ""  # 旧版单地址（兼容旧客户端，仅在未提交列表时生效）
    default_test_targets: str | list | None = None  # None=不修改；否则全量替换（含空列表=清空）


@app.patch("/api/projects/{project_id}", dependencies=[Depends(require_user)])
def patch_project(project_id: str, body: ProjectPatchBody, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = _get_project_checked(db, project_id, user)
    if body.name:
        project.name = body.name.strip()[:128]
    if body.description:
        project.description = body.description.strip()[:2000]
    if body.git_repos is not None:
        repos, err = normalize_repos(parse_repos(body.git_repos))
        if err:
            raise HTTPException(400, err)
        if project.source_type == "git" and not repos:
            raise HTTPException(400, "Git 项目必须至少保留一个仓库")
        project.git_repos = dump_repos(repos)
        project.git_url = repos[0]["url"] if repos else ""
        # 重算各仓库凭据快照：保留仍存在地址的旧快照，新地址按域名从操作者 Git 配置解析
        resolved = _resolve_repo_tokens(db, user, repos, load_repo_tokens(project.repo_tokens))
        project.repo_tokens = json.dumps(resolved, ensure_ascii=False) if resolved else ""
    elif body.git_url and body.git_url.strip():
        project.git_url = body.git_url.strip()
        project.git_repos = dump_repos([{"url": body.git_url.strip(), "note": ""}])
        project.repo_tokens = ""
    if body.default_test_targets is not None:
        targets = _checked_targets(body.default_test_targets)
        project.default_test_targets = dump_targets(targets)
        project.default_test_url = targets[0]["url"] if targets else ""
    elif body.default_test_url:
        targets = _checked_targets([{"url": body.default_test_url, "note": ""}])
        project.default_test_url = targets[0]["url"]
        project.default_test_targets = dump_targets(targets)
    if body.git_auth_type is not None:
        if body.git_auth_type not in ("", "token"):
            raise HTTPException(400, "git_auth_type 必须是 token / 留空（仅支持 Personal Access Token）")
        if body.git_auth_type == "token" and not (body.git_token.strip() or project.git_token):
            raise HTTPException(400, "选择 token 认证时必须填写 token")
        project.git_auth_type = body.git_auth_type
        if body.git_auth_type != "token":
            project.git_token = ""
    if body.git_token:
        project.git_token = body.git_token.strip()
    db.commit()
    _audit(db, request, "project.patch", f"id={project_id}", user=user)
    return _project_out(project, db)


@app.post("/api/projects/{project_id}/archive", dependencies=[Depends(require_user)])
def archive_project(project_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """归档项目（软删除）：保留全部数据，仅不能再发起新任务，可随时恢复。"""
    project = _get_project_checked(db, project_id, user)
    running = db.execute(
        select(func.count(Task.id)).where(
            Task.project_id == project_id,
            Task.status.in_(["pending", "fetching", "scanning", "parsing"]),
        )
    ).scalar() or 0
    if running:
        raise HTTPException(400, "项目下还有执行中的任务，待完成后再归档")
    project.is_archived = True
    db.commit()
    _audit(db, request, "project.archive", f"id={project_id} name={project.name}", user=user)
    return _project_out(project, db)


@app.post("/api/projects/{project_id}/unarchive", dependencies=[Depends(require_user)])
def unarchive_project(project_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    project = _get_project_checked(db, project_id, user)
    project.is_archived = False
    db.commit()
    _audit(db, request, "project.unarchive", f"id={project_id} name={project.name}", user=user)
    return _project_out(project, db)


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
def project_branches(project_id: str, repo_url: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)):
    """列分支；repo_url 指定多仓库项目中的某个仓库（须在绑定列表内），缺省为首个仓库。"""
    project = _get_project_checked(db, project_id, user)
    if project.source_type != "git":
        raise HTTPException(400, "仅 Git 项目支持分支选择")
    repos = effective_repos(project.git_repos, project.git_url)
    if not repos:
        raise HTTPException(400, "项目未绑定仓库")
    if repo_url:
        if repo_url not in {r["url"] for r in repos}:
            raise HTTPException(400, "仓库不在项目绑定列表内")
        url = repo_url
    else:
        url = repos[0]["url"]
    auth_type, token = repo_credential(
        load_repo_tokens(project.repo_tokens), url, project.git_auth_type, project.git_token,
    )
    try:
        branches = list_branches(url, auth_type=auth_type, token=token)
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
    targets = effective_targets(t.test_targets, t.test_url)
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
        "repo_branches": effective_repo_branches(
            t.repo_branches, t.source_ref if t.source_type == "git" else "", t.branch or "",
        ),
        "test_url": targets[0]["url"] if targets else "",  # 兼容旧字段：首个地址
        "test_targets": targets,
        "instruction": t.instruction or "",
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
def list_models(db: Session = Depends(get_db)) -> dict:
    """任务提交时可选的模型列表（platform_models 表，超管在设置页维护）。"""
    return {"default": default_model(db), "items": platform_models(db)}


# ===================== 黑盒测试地址探测 =====================


class TargetCheckBody(BaseModel):
    url: str


@app.post("/api/targets/check", dependencies=[Depends(require_user)])
def check_target(body: TargetCheckBody, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """对黑盒测试地址做一次真实访问探测：先过允许清单，再发 HTTP GET 判断可达性。"""
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "地址不能为空")
    ok, reason = check_target_allowed(url)
    if not ok:
        return {"allowed": False, "reason": reason, "reachable": None, "status_code": None, "latency_ms": None, "detail": ""}
    probe = probe_target(url)
    _audit(
        db, request, "target.check",
        f"url={url} reachable={probe['reachable']} status={probe['status_code']} latency={probe['latency_ms']}ms",
        user=user,
    )
    return {"allowed": True, "reason": "", **probe}


# ===================== Git 仓库地址探测 =====================


class GitRepoCheckBody(BaseModel):
    git_url: str
    auth_type: str = ""
    token: str = ""


@app.post("/api/sources/check", dependencies=[Depends(require_user)])
def check_git_repo(
    body: GitRepoCheckBody,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """手动输入的 Git 仓库地址测试：git ls-remote 验证地址与凭据可访问，顺带返回分支列表。"""
    url = body.git_url.strip()
    if not url:
        raise HTTPException(400, "仓库地址不能为空")
    auth_type = body.auth_type
    token = body.token.strip()
    if not token:
        # 未显式携带凭据：按域名从操作者个人 Git 配置自动解析（私有仓库「测试访问」免贴 token）
        cfg = _find_config_for_host(db, user, url)
        if cfg:
            auth_type, token = "token", cfg.token
    started = time.monotonic()
    try:
        branches = list_branches(url, auth_type, token)
    except SourceError as exc:
        _audit(db, request, "git.check", f"url={url} ok=False", user=user)
        return {"reachable": False, "detail": str(exc), "branches": [], "latency_ms": None}
    latency_ms = int((time.monotonic() - started) * 1000)
    _audit(db, request, "git.check", f"url={url} ok=True branches={len(branches)} latency={latency_ms}ms", user=user)
    return {"reachable": True, "detail": "", "branches": branches, "latency_ms": latency_ms}


@app.post("/api/projects/{project_id}/tasks", dependencies=[Depends(require_user)])
async def create_task(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    scan_mode: str = Form("quick"),
    test_url: str = Form(""),  # 旧版单地址（兼容旧客户端）
    test_targets: str = Form(""),  # 多目标列表：JSON [{"url","note"}]
    instruction: str = Form(""),
    model: str = Form(""),
    branch: str = Form(""),  # 旧版单仓库分支（兼容旧客户端）
    repo_branches: str = Form(""),  # 多仓库各自分支：JSON [{"url","branch"}]
    upload_id: str = Form(""),
    file: UploadFile | None = File(default=None),
    user: User = Depends(require_user),
):
    project = _get_project_checked(db, project_id, user)
    if project.is_archived:
        raise HTTPException(400, "项目已归档，无法发起新任务；请先恢复项目")
    if not (user.llm_api_key or "").strip():
        raise HTTPException(400, "尚未配置个人 AI 密钥；请先在「设置」中配置后再提交任务")
    if scan_mode not in ("quick", "standard", "deep"):
        raise HTTPException(400, "scan_mode 必须是 quick/standard/deep")

    # 模型可选：留空用平台默认；指定则必须在平台可用模型列表内（超管维护）
    model = (model or "").strip()
    if model:
        if not valid_model_name(model):
            raise HTTPException(400, "model 不合法")
        if model not in platform_models(db):
            raise HTTPException(400, f"model 必须是平台可用模型之一，请联系超管在「设置」中确认")
    elif not platform_models(db):
        raise HTTPException(400, "平台尚未配置可用模型，请联系超管在「设置」中添加")

    # 黑盒目标允许清单校验（多目标逐个校验；test_targets 优先，旧 test_url 兼容）
    if test_targets.strip():
        targets = _checked_targets(test_targets)
    elif test_url:
        targets = _checked_targets([{"url": test_url, "note": ""}])
    else:
        targets = []

    # 自定义测试指令（透传 strix --instruction）
    instruction = instruction.strip()
    if len(instruction) > 4000:
        raise HTTPException(400, "instruction 最长 4000 个字符")

    task_id = new_task_id()
    upload_ref: ProjectUpload | None = None
    branch = (branch or "").strip()
    repo_refs: list[dict] = []  # [{"url","branch"}]：git 任务实际扫描的仓库与分支
    if project.source_type == "git":
        source_type = "git"
        repos = effective_repos(project.git_repos, project.git_url)
        if not repos:
            raise HTTPException(400, "项目未绑定仓库")
        if repo_branches.strip():
            # 新前端提交：逐仓库分支；URL 必须在项目绑定列表内，分支名做白名单校验
            repo_refs = parse_repo_branches(repo_branches)
            if not repo_refs:
                raise HTTPException(400, "repo_branches 格式不合法")
            bound = {r["url"] for r in repos}
            for r in repo_refs:
                if r["url"] not in bound:
                    raise HTTPException(400, f"仓库 {r['url']} 不在项目绑定列表内")
                if r["branch"] and not re.fullmatch(r"[A-Za-z0-9._/-]+", r["branch"]):
                    raise HTTPException(400, f"仓库 {r['url']} 的分支名不合法")
        else:
            if branch and not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
                raise HTTPException(400, "branch 不合法")
            # 未提交列表：按项目当前仓库展开（单仓库沿用旧 branch 字段，多仓库各用默认分支）
            repo_refs = [
                {"url": r["url"], "branch": branch if len(repos) == 1 and branch else ""}
                for r in repos
            ]
        source_ref = repo_refs[0]["url"]
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
        source_ref=source_ref, branch=repo_refs[0]["branch"] if repo_refs else branch,
        repo_branches=json.dumps(repo_refs, ensure_ascii=False),
        upload_id=upload_ref.id if upload_ref else None,
        test_url=targets[0]["url"] if targets else "",
        test_targets=dump_targets(targets),
        instruction=instruction, model=model, report_lang="zh", status="pending",
    )
    db.add(task)
    db.commit()
    _audit(
        db, request, "task.submit",
        f"id={task_id} project={project_id} mode={scan_mode} model={model or 'default'} "
        f"source={source_type} repos={len(repo_refs)} branch={branch} upload={upload_ref.id if upload_ref else '-'} "
        f"targets={dump_targets(targets) if targets else '-'} instruction={'yes' if instruction else 'no'}",
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


# ===================== 统计汇总 =====================


@app.get("/api/stats", dependencies=[Depends(require_user)])
def stats(db: Session = Depends(get_db), user: User = Depends(require_user)):
    """统计汇总：普通用户仅统计自己创建的项目与任务，超管统计全平台。"""
    is_admin = user.role == "admin"

    def scope_tasks(q):
        return q if is_admin else q.where(Task.created_by == user.id)

    def scope_projects(q):
        return q if is_admin else q.where(Project.created_by == user.id)

    projects_total = db.execute(
        scope_projects(select(func.count(Project.id)).where(Project.is_archived.is_(False)))
    ).scalar() or 0
    projects_archived = db.execute(
        scope_projects(select(func.count(Project.id)).where(Project.is_archived.is_(True)))
    ).scalar() or 0

    tasks_total = db.execute(scope_tasks(select(func.count(Task.id)))).scalar() or 0
    tasks_by_status = dict(db.execute(
        scope_tasks(select(Task.status, func.count(Task.id)).group_by(Task.status))
    ).all())
    tasks_by_mode = dict(db.execute(
        scope_tasks(select(Task.scan_mode, func.count(Task.id)).group_by(Task.scan_mode))
    ).all())
    tasks_by_model = dict(db.execute(
        scope_tasks(
            select(Task.model, func.count(Task.id))
            .group_by(Task.model)
            .order_by(desc(func.count(Task.id)))
        ).limit(6)
    ).all())
    avg_duration = db.execute(
        scope_tasks(select(func.avg(Task.duration_sec)).where(Task.status == "done"))
    ).scalar()
    total_tokens = db.execute(
        scope_tasks(select(func.coalesce(func.sum(Task.total_tokens), 0)))
    ).scalar() or 0
    # token 消耗明细：输入/输出拆分、请求次数、按模型聚合
    total_input_tokens = db.execute(
        scope_tasks(select(func.coalesce(func.sum(Task.input_tokens), 0)))
    ).scalar() or 0
    total_output_tokens = db.execute(
        scope_tasks(select(func.coalesce(func.sum(Task.output_tokens), 0)))
    ).scalar() or 0
    llm_requests_total = db.execute(
        scope_tasks(select(func.coalesce(func.sum(Task.llm_requests), 0)))
    ).scalar() or 0
    tokens_by_model = dict(db.execute(
        scope_tasks(
            select(Task.model, func.coalesce(func.sum(Task.total_tokens), 0))
            .group_by(Task.model)
            .order_by(desc(func.sum(Task.total_tokens)))
        ).limit(6)
    ).all())

    # 漏洞严重度分布：以 findings 表为准（历史任务可能只有 severity_counts 汇总）
    findings_by_severity = dict(db.execute(
        scope_tasks(
            select(Finding.severity, func.count(Finding.id))
            .select_from(Finding)
            .join(Task, Finding.task_id == Task.id)
            .group_by(Finding.severity)
        )
    ).all())
    findings_total = db.execute(
        scope_tasks(select(func.count(Finding.id)).select_from(Finding).join(Task, Finding.task_id == Task.id))
    ).scalar() or 0
    if not findings_total:  # 兜底：解析结果落库前的任务只有 Task.severity_counts JSON
        for t in db.execute(scope_tasks(select(Task.severity_counts).where(Task.findings_count > 0))).scalars():
            try:
                for sev, n in (json.loads(t) or {}).items():
                    findings_by_severity[sev] = findings_by_severity.get(sev, 0) + int(n)
                    findings_total += int(n)
            except (ValueError, TypeError):
                continue

    # 近 14 天任务趋势（按日聚合任务数与 token 消耗，缺失日期补零）
    trend_start = datetime.now(timezone.utc) - timedelta(days=13)
    trend_counts: dict[str, int] = {}
    trend_tokens: dict[str, int] = {}
    for created, tokens in db.execute(
        scope_tasks(select(Task.created_at, Task.total_tokens).where(Task.created_at >= trend_start))
    ).all():
        if created is None:
            continue
        day = created.astimezone(timezone.utc).strftime("%Y-%m-%d")
        trend_counts[day] = trend_counts.get(day, 0) + 1
        trend_tokens[day] = trend_tokens.get(day, 0) + (tokens or 0)
    today = datetime.now(timezone.utc)
    trend = [
        {
            "date": (day := (today - timedelta(days=13 - i)).strftime("%Y-%m-%d")),
            "count": trend_counts.get(day, 0),
            "tokens": trend_tokens.get(day, 0),
        }
        for i in range(14)
    ]

    # 项目漏洞 Top 5（按发现漏洞数排序）
    tasks_per_project = dict(db.execute(
        scope_tasks(
            select(Task.project_id, func.count(Task.id)).where(Task.project_id.is_not(None)).group_by(Task.project_id)
        )
    ).all())
    findings_per_project: dict[str, int] = {}
    findings_sev_per_project: dict[str, dict[str, int]] = {}
    for pid, sev, n in db.execute(
        scope_tasks(
            select(Task.project_id, Finding.severity, func.count(Finding.id))
            .select_from(Finding)
            .join(Task, Finding.task_id == Task.id)
            .where(Task.project_id.is_not(None))
            .group_by(Task.project_id, Finding.severity)
        )
    ).all():
        findings_per_project[pid] = findings_per_project.get(pid, 0) + n
        findings_sev_per_project.setdefault(pid, {})[sev] = n
    top_ids = sorted(tasks_per_project, key=lambda pid: findings_per_project.get(pid, 0), reverse=True)[:5]
    top_projects = [
        {
            "id": pid,
            "name": (p.name if (p := db.get(Project, pid)) else pid),
            "tasks": tasks_per_project.get(pid, 0),
            "findings": findings_per_project.get(pid, 0),
            "severity_counts": findings_sev_per_project.get(pid, {}),
        }
        for pid in top_ids
    ]

    return {
        "scope": "all" if is_admin else "mine",
        "projects_total": projects_total,
        "projects_archived": projects_archived,
        "tasks_total": tasks_total,
        "tasks_by_status": tasks_by_status,
        "tasks_by_mode": tasks_by_mode,
        "tasks_by_model": tasks_by_model,
        "findings_total": findings_total,
        "findings_by_severity": findings_by_severity,
        "avg_duration_sec": round(float(avg_duration), 1) if avg_duration is not None else None,
        "total_tokens": int(total_tokens),
        "total_input_tokens": int(total_input_tokens),
        "total_output_tokens": int(total_output_tokens),
        "llm_requests_total": int(llm_requests_total),
        "tokens_by_model": tokens_by_model,
        "trend": trend,
        "top_projects": top_projects,
    }


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
        "input_tokens": task.input_tokens,
        "output_tokens": task.output_tokens,
        "llm_requests": task.llm_requests,
        "agents": json.loads(task.agents_usage or "[]"),
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
                "target": f.target,
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
    # v2: 启用 CJK 字体修复中文乱码；改名以自动失效旧版乱码缓存
    cache_path = Path(settings.workspace_root) / "reports" / f"{task_id}.v2.pdf"
    if not cache_path.is_file():
        run_dir = Path(settings.workspace_root) / task_id / "scan" / "strix_runs" / task.run_dir_name
        if not run_dir.is_dir():
            raise HTTPException(404, "run 工作区已清理，无法生成 PDF（可下载产物 zip 归档）")
        from app.pdf_fonts import apply_cjk_fonts
        from strix.interface.viewer.report_pdf import generate_report_pdf

        try:
            apply_cjk_fonts()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(generate_report_pdf(run_dir))
        except Exception as exc:  # noqa: BLE001
            cache_path.unlink(missing_ok=True)
            raise HTTPException(500, f"PDF 生成失败: {exc}")
    _audit(db, request, "task.report_pdf", f"id={task_id}", user=user)
    return FileResponse(cache_path, filename=f"strix-report-{task_id[:10]}.pdf", media_type="application/pdf")
