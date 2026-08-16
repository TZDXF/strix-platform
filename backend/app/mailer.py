"""邮件通知：SMTP 配置存于 system_settings 表（超管在「系统设置」页维护），
任务到达终态（done/failed）时向创建者的通知邮箱发送提醒。"""

from __future__ import annotations

import json
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Project, SystemSetting, Task, User

# system_settings 表中的键
K_HOST = "smtp_host"
K_PORT = "smtp_port"
K_USER = "smtp_user"
K_PASSWORD = "smtp_password"
K_USE_TLS = "smtp_use_tls"          # "1" = STARTTLS（常见 587），"0" = 明文/SMTPS 由端口决定
K_SSL = "smtp_ssl"                  # "1" = SMTPS 直连 TLS（常见 465）
K_FROM = "mail_from"                # 发件人邮箱地址；留空用 smtp_user
K_SENDER_NAME = "mail_sender_name"  # 发件人显示名；留空用 "Strix 平台"
K_SITE_URL = "site_url"             # 平台访问地址，用于拼接任务链接
K_NOTIFY_DONE = "notify_done"       # "1" = 任务完成时通知
K_NOTIFY_FAILED = "notify_failed"   # "1" = 任务失败时通知

_DEFAULTS: dict[str, str] = {
    K_HOST: "",
    K_PORT: "587",
    K_USER: "",
    K_PASSWORD: "",
    K_USE_TLS: "1",
    K_SSL: "0",
    K_FROM: "",
    K_SENDER_NAME: "",
    K_SITE_URL: "",
    K_NOTIFY_DONE: "1",
    K_NOTIFY_FAILED: "1",
}

SEV_LABEL = {"critical": "严重", "high": "高危", "medium": "中危", "low": "低危", "info": "提示"}


def get_mail_settings(db: Session) -> dict[str, str]:
    """读取邮件设置（含默认值兜底）。"""
    values = dict(_DEFAULTS)
    rows = db.execute(select(SystemSetting).where(SystemSetting.key.in_(_DEFAULTS))).scalars().all()
    for row in rows:
        if row.value != "":
            values[row.key] = row.value
    return values


def set_mail_settings(db: Session, values: dict[str, str], user_id: int | None) -> None:
    """写入邮件设置（空字符串表示恢复默认值）。"""
    for key, value in values.items():
        if key not in _DEFAULTS:
            continue
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key, value=value, updated_by=user_id)
            db.add(row)
        else:
            row.value = value
            row.updated_by = user_id
    db.commit()


def mail_configured(s: dict[str, str]) -> bool:
    return bool(s.get(K_HOST, "").strip())


def mail_settings_public(db: Session) -> dict:
    """给「系统设置」页的视图模型：密码不回显，布尔值还原。"""
    s = get_mail_settings(db)
    return {
        "smtp_host": s[K_HOST],
        "smtp_port": int(s.get(K_PORT) or 587),
        "smtp_user": s[K_USER],
        "has_password": bool(s[K_PASSWORD]),
        "smtp_use_tls": s.get(K_USE_TLS) == "1",
        "smtp_ssl": s.get(K_SSL) == "1",
        "mail_from": s[K_FROM],
        "mail_sender_name": s[K_SENDER_NAME],
        "site_url": s[K_SITE_URL],
        "notify_done": s.get(K_NOTIFY_DONE, "1") == "1",
        "notify_failed": s.get(K_NOTIFY_FAILED, "1") == "1",
        "configured": mail_configured(s),
    }


def send_mail(settings_map: dict[str, str], to: str, subject: str, html: str, timeout: int = 20) -> None:
    """按系统设置发送一封 HTML 邮件；失败抛异常（由调用方决定记录方式）。"""
    host = settings_map[K_HOST].strip()
    if not host:
        raise RuntimeError("未配置 SMTP 服务器地址")
    port = int(settings_map.get(K_PORT) or 587)
    user = settings_map.get(K_USER, "")
    password = settings_map.get(K_PASSWORD, "")
    sender_addr = settings_map.get(K_FROM, "").strip() or user
    if not sender_addr:
        raise RuntimeError("未配置发件人地址（SMTP 用户名或发件人邮箱）")
    sender_name = settings_map.get(K_SENDER_NAME, "").strip() or "Strix 平台"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(sender_name, "utf-8")), sender_addr))
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    use_ssl = settings_map.get(K_SSL) == "1"
    use_tls = settings_map.get(K_USE_TLS, "1") == "1"
    if use_ssl:
        smtp = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
    else:
        smtp = smtplib.SMTP(host, port, timeout=timeout)
    try:
        smtp.ehlo()
        if use_tls and not use_ssl:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if user:
            smtp.login(user, password)
        smtp.sendmail(sender_addr, [to], msg.as_string())
    finally:
        try:
            smtp.quit()
        except Exception:  # noqa: BLE001 —— 关闭失败不影响发送结果
            pass


def _sev_table(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    order = ["critical", "high", "medium", "low", "info"]
    cells = "".join(
        f'<td style="padding:6px 14px;border:1px solid #e3e6ec;">{SEV_LABEL.get(sev, sev)}：<b>{counts.get(sev, 0)}</b></td>'
        for sev in order if counts.get(sev)
    )
    return f'<table style="border-collapse:collapse;margin:12px 0;"><tr>{cells}</tr></table>'


def _status_word(task: Task) -> str:
    return "扫描失败" if task.status == "failed" else "扫描完成"


def notify_task_finished(task_id: str) -> dict:
    """任务到达终态后发送提醒邮件；返回发送结果（未配置/无邮箱/异常均不抛出）。"""
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return {"sent": False, "reason": "task not found"}
        s = get_mail_settings(db)
        if not mail_configured(s):
            return {"sent": False, "reason": "邮件未配置"}
        if task.status == "done" and s.get(K_NOTIFY_DONE, "1") != "1":
            return {"sent": False, "reason": "完成通知已关闭"}
        if task.status == "failed" and s.get(K_NOTIFY_FAILED, "1") != "1":
            return {"sent": False, "reason": "失败通知已关闭"}

        creator = db.get(User, task.created_by) if task.created_by else None
        to = (creator.email or "").strip() if creator else ""
        if not to:
            return {"sent": False, "reason": "创建者未设置通知邮箱"}
        project = db.get(Project, task.project_id) if task.project_id else None
        project_name = project.name if project else "-"

        try:
            counts = json.loads(task.severity_counts) if task.severity_counts else {}
        except (ValueError, TypeError):
            counts = {}

        failed = task.status == "failed"
        color = "#d64545" if failed else "#2e9e5b"
        duration = f"{task.duration_sec // 60} 分 {task.duration_sec % 60} 秒" if task.duration_sec else "-"
        site = s.get(K_SITE_URL, "").strip().rstrip("/")
        link = f"{site}/#/task/{task.id}" if site else ""

        rows = [
            ("项目", project_name),
            ("任务 ID", task.id),
            ("扫描模式", task.scan_mode),
            ("模型", task.model or "-"),
            ("耗时", duration),
        ]
        if not failed:
            rows.append(("发现漏洞", str(task.findings_count)))
        rows_html = "".join(
            f'<tr><td style="padding:5px 14px 5px 0;color:#6b7280;white-space:nowrap;">{k}</td>'
            f'<td style="padding:5px 0;font-weight:600;">{v}</td></tr>'
            for k, v in rows
        )
        error_html = (
            f'<p style="margin:12px 0;padding:10px 14px;background:#fdeeee;border-radius:6px;color:#b03a3a;">'
            f'失败原因：{task.error or "详见任务日志"}</p>' if failed and task.error else ""
        )
        link_html = (
            f'<p style="margin:16px 0 4px;"><a href="{link}" style="background:#2563eb;color:#fff;'
            f'padding:9px 22px;border-radius:6px;text-decoration:none;font-weight:600;">查看任务详情</a></p>'
            if link else ""
        )
        html = f"""<div style="font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;max-width:560px;">
<p style="margin:0 0 4px;font-size:16px;font-weight:700;color:{color};">{_status_word(task)}</p>
<p style="margin:0 0 12px;color:#6b7280;">你在 Strix 平台提交的扫描任务已结束，摘要如下：</p>
<table style="border-collapse:collapse;font-size:14px;color:#1f2937;">{rows_html}</table>
{_sev_table(counts)}
{error_html}
{link_html}
<p style="margin:20px 0 0;font-size:12px;color:#9ca3af;">本邮件由系统自动发送，请勿回复。</p>
</div>"""
        subject = f"[Strix] {_status_word(task)}：{project_name}"
        send_mail(s, to, subject, html)
        return {"sent": True, "to": to}
    except Exception as exc:  # noqa: BLE001 —— 通知失败不影响任务本身
        return {"sent": False, "reason": str(exc)}
    finally:
        db.close()
