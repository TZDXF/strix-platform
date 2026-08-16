from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """平台用户：admin（超管）可管理账号与查看全部任务，user 仅见自己的项目/任务。"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), default="")
    role = Column(String(16), default="user")  # admin | user
    display_name = Column(String(128), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    # 个人 AI 网关密钥（必填）：该用户发起的扫描与翻译用它调用 LLM 网关，平台不再持有统一密钥
    llm_api_key = Column(Text, default="")
    # 通知邮箱（可选）：任务完成/失败时向该地址发送提醒邮件
    email = Column(String(255), default="")


class PlatformModel(Base):
    """平台可用模型：超管在设置页维护（通过网关密钥查询后挑选加入），供任务提交时选择。"""

    __tablename__ = "platform_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    is_default = Column(Boolean, default=False)  # 平台默认模型（全表至多一个）
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class Project(Base):
    """扫描项目：Git 仓库（可配访问密钥）或 zip 上传两种来源。"""

    __tablename__ = "projects"

    id = Column(String(32), primary_key=True)  # uuid4 hex
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    source_type = Column(String(8), default="git")  # git | zip
    git_url = Column(Text, default="")  # 旧版单仓库地址（兼容存量数据，读取时回退用）
    # 绑定的代码仓库列表：JSON [{"url": "...", "note": "用途说明"}]；非空字符串时优先生效
    git_repos = Column(Text, default="")
    # 访问凭据（仅创建人/超管可写，接口永不回显明文）；仅支持 Personal Access Token
    git_auth_type = Column(String(8), default="")  # "" | token
    git_token = Column(Text, default="")  # PAT 明文（`token` 或 `user:token`），仅后端使用
    default_test_url = Column(Text, default="")  # 旧版单地址（兼容存量数据，读取时回退用）
    # 默认黑盒测试地址列表：JSON [{"url": "...", "note": "作用说明"}]；非空字符串时优先生效
    default_test_targets = Column(Text, default="")
    is_archived = Column(Boolean, default=False)  # 归档（软删除）：数据保留，仅不能再发起新任务
    created_by = Column(Integer, ForeignKey("users.id"), index=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class GitConfig(Base):
    """用户个人 Git 服务配置（GitLab）：保存服务地址与访问令牌，创建项目时可拉取仓库列表。"""

    __tablename__ = "git_configs"

    id = Column(String(32), primary_key=True)  # uuid4 hex
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(128), default="")  # 显示名，如「内网 GitLab」
    base_url = Column(Text, default="")  # 如 http://192.168.1.3:12580
    token = Column(Text, default="")  # PAT 明文，仅后端使用，接口永不回显
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class ProjectUpload(Base):
    """项目内历史上传的 zip 包，供再次发起扫描时复用。"""

    __tablename__ = "project_uploads"

    id = Column(String(32), primary_key=True)  # uuid4 hex
    project_id = Column(String(32), ForeignKey("projects.id"), index=True)
    filename = Column(String(255), default="")
    size_bytes = Column(BigInteger, default=0)
    stored_path = Column(Text, default="")  # workspace 下的相对路径
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), default=_now)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(32), primary_key=True)  # uuid4 hex
    project_id = Column(String(32), ForeignKey("projects.id"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    # pending -> fetching -> scanning -> parsing -> translating -> done | failed
    status = Column(String(16), default="pending", index=True)
    scan_mode = Column(String(16), default="quick")
    source_type = Column(String(8))  # git | zip
    source_ref = Column(Text, default="")  # git URL（多仓库时为首个仓库）或上传文件名
    branch = Column(String(255), default="")  # 旧版单仓库扫描分支（兼容存量任务）
    # 多仓库各自的扫描分支：JSON [{"url": "...", "branch": ""}]；非空字符串时优先生效
    repo_branches = Column(Text, default="")
    upload_id = Column(String(32), ForeignKey("project_uploads.id"), nullable=True)
    test_url = Column(Text, default="")  # 旧版单地址（兼容存量任务，读取时回退用）
    # 黑盒测试地址列表：JSON [{"url": "...", "note": "作用说明"}]；非空字符串时优先生效
    test_targets = Column(Text, default="")
    instruction = Column(Text, default="")  # 用户自定义测试指令（--instruction，可选）
    report_lang = Column(String(8), default="zh")  # 固定中文报告；保留字段兼容历史 en 任务
    zh_status = Column(String(16), default="")  # 翻译状态："" | pending | done | failed

    # 执行结果
    run_dir_name = Column(String(255), default="")
    strix_version = Column(String(32), default="")
    model = Column(String(64), default="")
    exit_code = Column(Integer, nullable=True)
    attempts = Column(Integer, default=0)
    timed_out = Column(Boolean, default=False)
    duration_sec = Column(Integer, nullable=True)
    total_tokens = Column(BigInteger, nullable=True)
    input_tokens = Column(BigInteger, nullable=True)
    output_tokens = Column(BigInteger, nullable=True)
    llm_requests = Column(Integer, nullable=True)
    # JSON: [{agent_id, agent_name, model, requests, input/output/total_tokens, started_at, finished_at, status, parent}]
    agents_usage = Column(Text, default="")
    findings_count = Column(Integer, default=0)
    severity_counts = Column(Text, default="")  # JSON: {"critical":n,...}
    artifacts_ref = Column(Text, default="")  # 本地 zip 路径 或 s3://key
    report_md = Column(Text, default="")  # 官方执行摘要报告（penetration_test_report.md）
    log = Column(Text, default="")  # 关键执行日志（滚动截断）
    error = Column(Text, default="")


class Finding(Base):
    __tablename__ = "findings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(32), ForeignKey("tasks.id"), index=True)
    vuln_id = Column(String(64), default="")
    title = Column(Text, default="")
    severity = Column(String(16), default="info", index=True)
    cvss = Column(Float, nullable=True)
    cwe = Column(String(32), default="")
    cve = Column(String(64), default="")
    endpoint = Column(Text, default="")
    target = Column(Text, default="")
    has_poc = Column(Boolean, default=False)
    description = Column(Text, default="")
    remediation = Column(Text, default="")
    poc_description = Column(Text, default="")
    poc_code = Column(Text, default="")
    # 中文翻译（report_lang=zh 时由 LLM 生成/翻译）
    title_zh = Column(Text, default="")
    description_zh = Column(Text, default="")
    remediation_zh = Column(Text, default="")
    raw = Column(Text, default="")  # 原始 finding JSON


Index("ix_findings_task_severity", Finding.task_id, Finding.severity)


class SystemSetting(Base):
    """系统设置（仅超管在「系统设置」页维护）：键值对存储，如 SMTP 邮件配置。"""

    __tablename__ = "system_settings"

    key = Column("key", String(64), primary_key=True)  # "key" 非保留字冲突，显式列名避免歧义
    value = Column(Text, default="")
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class AuditEntry(Base):
    """审计日志（谁在何时提交/查询了什么）。"""

    __tablename__ = "audit_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime(timezone=True), default=_now, index=True)
    user_id = Column(Integer, nullable=True)
    client_ip = Column(String(64), default="")
    action = Column(String(64), default="")
    detail = Column(Text, default="")
