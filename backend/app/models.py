from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(32), primary_key=True)  # uuid4 hex
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    # pending -> fetching -> scanning -> parsing -> done | failed
    status = Column(String(16), default="pending", index=True)
    scan_mode = Column(String(16), default="quick")
    source_type = Column(String(8))  # git | zip
    source_ref = Column(Text, default="")  # git URL 或上传文件名
    test_url = Column(Text, default="")  # 用户提供的黑盒测试地址（可选）

    # 执行结果
    run_dir_name = Column(String(255), default="")
    strix_version = Column(String(32), default="")
    model = Column(String(64), default="")
    exit_code = Column(Integer, nullable=True)
    attempts = Column(Integer, default=0)
    timed_out = Column(Boolean, default=False)
    duration_sec = Column(Integer, nullable=True)
    total_tokens = Column(BigInteger, nullable=True)
    llm_requests = Column(Integer, nullable=True)
    findings_count = Column(Integer, default=0)
    severity_counts = Column(Text, default="")  # JSON: {"critical":n,...}
    artifacts_ref = Column(Text, default="")  # 本地 zip 路径 或 s3://key
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
    raw = Column(Text, default="")  # 原始 finding JSON


Index("ix_findings_task_severity", Finding.task_id, Finding.severity)


class AuditEntry(Base):
    """最低成本护栏②：审计日志（谁在何时提交/查询了什么）。"""

    __tablename__ = "audit_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime(timezone=True), default=_now, index=True)
    client_ip = Column(String(64), default="")
    action = Column(String(64), default="")
    detail = Column(Text, default="")
