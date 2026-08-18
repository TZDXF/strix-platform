"""扫描任务流水线：获取源码（项目/分支/凭据） → strix 执行 → 产物解析入库 → 归档 → 中文翻译。"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete

from .artifacts import archive_run
from .cancel import cancel_requested, clear_cancel
from .celery_app import celery_app
from .config import get_settings
from .db import SessionLocal
from .llm_models import default_model
from .mailer import notify_task_finished
from .models import Finding, Project, ProjectUpload, Task, User
from .runner import execute_scan, read_run_artifacts
from .sources import (
    SourceError,
    clone_git,
    du_mb,
    effective_repo_branches,
    effective_repos,
    load_repo_tokens,
    repo_credential,
    repo_dir_name,
    safe_extract_zip,
)
from .targets import effective_targets
from .tasklog import append_log as _log
from .translate import translate_findings_task

ZH_INSTRUCTION = (
    "Please write the entire report in Simplified Chinese (简体中文): "
    "vulnerability titles, descriptions, PoC explanations and remediation steps. "
    "Keep technical identifiers (CVE/CWE, code, endpoints) unchanged."
)


def web_search_guide(mcp_url: str) -> str:
    """联网搜索指令块：教智能体用沙箱 shell curl 内网 MCP 搜索端点。

    端点为 Streamable HTTP MCP，实测支持无状态 JSON-RPC 直调（免 initialize/会话），
    返回 SSE data: 行，content[0].text 内是再包一层的 JSON 结果串（title/link/content）。
    指令进入根智能体任务文本；根智能体派生专项智能体时按需把命令带进子任务。
    """
    payload = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"web_search_prime",'
        '"arguments":{"search_query":"<english keywords>","location":"us"}}}'
    )
    return (
        "Web research capability is ENABLED for this scan: an internal web-search gateway "
        "is reachable from the sandbox shell. To search, run:\n"
        f"curl -s -m 30 -X POST '{mcp_url}' "
        "-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' "
        f"-d '{payload}' "
        '| sed -n "s/^data://p" '
        "| jq -r '.result.content[0].text | fromjson | .[] | \"\\(.title) - \\(.link)\\n\\(.content)\"'\n"
        "Guidance: use it to research current CVE details, exploit/PoC approaches, payload "
        "syntax and bypass techniques for the exact technologies in scope. Keep queries in "
        "English and under 70 characters. Optional arguments: search_recency_filter "
        "(oneDay/oneWeek/oneMonth/oneYear), content_size=high for deeper summaries, "
        "search_domain_filter to restrict to specific sites (e.g. nvd.nist.gov). "
        "Cross-check findings across multiple results before acting on them. If the gateway "
        "is unreachable, continue the assessment without web research. When spawning "
        "specialist agents whose task benefits from web research, include this command in "
        "their task text."
    )


def _set_status(db, task, status: str) -> None:
    task.status = status
    task.updated_at = datetime.now(timezone.utc)
    db.commit()


def _finish_as_cancelled(db, task, task_id: str, detail: str = "") -> dict:
    """任务以「已取消」收尾：终态时间/时长/日志，不解析产物、不翻译、不发提醒邮件。"""
    clear_cancel(task_id)
    task.error = ""
    task.finished_at = datetime.now(timezone.utc)
    if task.started_at:
        task.duration_sec = int((task.finished_at - task.started_at).total_seconds())
    _log(task, f"[cancel] 任务已取消{('：' + detail) if detail else ''}，耗时 {task.duration_sec or 0} 秒")
    _set_status(db, task, "cancelled")
    return {"task_id": task_id, "cancelled": True}


# 这些情况属正常跳过（未配置/无邮箱/开关关闭），不写进任务日志
_NOTIFY_QUIET = {"邮件未配置", "创建者未设置通知邮箱", "完成通知已关闭", "失败通知已关闭"}


def _notify(db, task, task_id: str) -> None:
    """任务终态后向创建者发送提醒邮件；结果写进任务日志，发送失败不影响任务状态。"""
    try:
        result = notify_task_finished(task_id)
    except Exception as exc:  # noqa: BLE001 —— 双保险，notify 内部已兜底
        result = {"sent": False, "reason": str(exc)}
    if result.get("sent"):
        _log(task, f"[mail] 已发送{'失败' if task.status == 'failed' else '完成'}提醒至 {result.get('to')}")
    elif result.get("reason") not in _NOTIFY_QUIET:
        _log(task, f"[mail] 提醒邮件未发送: {result.get('reason')}")
    db.commit()


def _strix_log_shift(log_text: str, log_path: Path) -> timedelta:
    """推断 strix.log 本地时间戳 → 北京时间的偏移。

    strix 按容器本地时间写日志（compose 默认 UTC），文件 mtime 是绝对时间，
    两者差值取整到小时即容器时区偏移，再 +8 折算成北京时间；容器若已设
    TZ=Asia/Shanghai，差值为 -8，净偏移为 0，同样正确。
    """
    last = ""
    for line in reversed(log_text.splitlines()):
        if len(line) >= 19 and line[10] == " ":
            last = line[:19]
            break
    try:
        naive = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        delta_hours = round((log_path.stat().st_mtime - naive.timestamp()) / 3600)
        delta_hours = max(-12, min(14, delta_hours))
        return timedelta(hours=delta_hours + 8)
    except (ValueError, OSError):
        return timedelta(hours=8)


def _ts_bj(ts: str, shift: timedelta) -> str:
    try:
        return (datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S") + shift).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts


def _agents_usage(run_record: dict, scan_dir: Path, run_dir_name: str) -> str:
    """合并 run.json 的智能体用量（tokens/请求）与 strix.log 的生命周期事件（启动/完成时间）。

    strix.log 行样例：
      2026-08-14 16:30:52.867 INFO  run - strix.core.agents: agent.register b2383e25 (Root Agent) parent=-
      2026-08-14 17:08:47.265 INFO  run - strix.core.agents: agent.status 0d0065a0=completed
    """
    agents: dict[str, dict] = {}

    def _entry(agent_id: str) -> dict:
        if agent_id not in agents:
            agents[agent_id] = {
                "agent_id": agent_id, "agent_name": "", "model": "", "parent": "",
                "requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                "started_at": "", "finished_at": "", "status": "",
            }
        return agents[agent_id]

    for a in (run_record.get("llm_usage") or {}).get("agents") or []:
        e = _entry(str(a.get("agent_id") or ""))
        e.update({
            "agent_name": str(a.get("agent_name") or ""),
            "model": str(a.get("model") or ""),
            "requests": int(a.get("requests") or 0),
            "input_tokens": int(a.get("input_tokens") or 0),
            "output_tokens": int(a.get("output_tokens") or 0),
            "total_tokens": int(a.get("total_tokens") or 0),
        })

    log_path = scan_dir / "strix_runs" / run_dir_name / "strix.log"
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    # strix.log 里的时间是引擎容器本地时间，换算成北京时间再展示
    shift = _strix_log_shift(log_text, log_path)
    for line in log_text.splitlines():
        ts = line[:23].strip()
        m = re.search(r"agent\.register (\w+) \((.+?)\) parent=(\S+)", line)
        if m:
            e = _entry(m.group(1))
            if not e["agent_name"]:
                e["agent_name"] = m.group(2)
            e["parent"] = m.group(3)
            if ts:
                e["started_at"] = _ts_bj(ts, shift)
            continue
        m = re.search(r"agent\.status (\w+)=(\w+)", line)
        if m:
            e = _entry(m.group(1))
            e["status"] = m.group(2)
            if m.group(2) in ("completed", "failed") and ts:
                e["finished_at"] = _ts_bj(ts, shift)

    ordered = sorted(
        agents.values(),
        key=lambda a: (a["started_at"] or "9999", a["agent_name"]),
    )
    return json.dumps(ordered, ensure_ascii=False)


def _scan_error_detail(scan_dir: Path) -> str:
    """提取 scan.log 中 strix 的报错行（"Error during penetration test: ..."），用于失败原因展示。"""
    try:
        text = (scan_dir / "scan.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in reversed(text.splitlines()):
        if "Error during penetration test:" in line:
            return line.split("Error during penetration test:", 1)[1].strip()[:300]
    return ""


@celery_app.task(name="run_scan", bind=True, max_retries=1)
def run_scan(self, task_id: str) -> dict:
    s = get_settings()
    db = SessionLocal()
    task = db.get(Task, task_id)
    if task is None:
        db.close()
        return {"error": "task not found"}
    if task.status in ("done", "failed", "cancelled"):
        db.close()  # 终态任务重派发（如取消后到达的旧消息）：直接忽略
        return {"task_id": task_id, "skipped": task.status}
    if cancel_requested(task_id):
        # 排队期间（pending）被取消：还没动过任何资源，直接落终态
        result = _finish_as_cancelled(db, task, task_id, "任务在排队等待期间被取消")
        db.close()
        return result

    project = db.get(Project, task.project_id) if task.project_id else None
    creator = db.get(User, task.created_by) if task.created_by else None
    user_llm_key = (creator.llm_api_key or "") if creator else ""
    if not user_llm_key:
        task.error = "创建者未配置个人 AI 密钥，无法执行扫描；请先在「设置」中配置后再提交任务"
        _log(task, f"[fail] {task.error}")
        task.finished_at = datetime.now(timezone.utc)
        _set_status(db, task, "failed")
        _notify(db, task, task_id)
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
        repo_refs = effective_repo_branches(task.repo_branches, task.source_ref, task.branch or "")
        src_desc = (
            f"git（{len(repo_refs)} 个仓库，分支 {'/'.join(r['branch'] or '默认' for r in repo_refs)}）"
            if task.source_type == "git" and len(repo_refs) != 1
            else f"git（分支 {repo_refs[0]['branch'] or '默认'}）" if task.source_type == "git" else "zip 上传"
        )
        _log(task, f"[task] 任务开始：{src_desc} · {task.scan_mode} 模式 · 模型 {task.model} · strix {s.strix_version}")
        db.commit()

        # ---- Stage 1: 获取源码 ----
        import shutil

        # 各仓库克隆到任务工作区顶层各自目录（不嵌套 src/），逐目录作为独立白盒
        # 目标传给 strix（-t 可重复），引擎为每个目标分配独立 /workspace/<目录名>
        # 子目录，漏洞的 target 字段才能归属到具体仓库
        taken = {"scan", "src"}
        repo_dirs = {r["url"]: task_ws / repo_dir_name(r["url"], taken) for r in repo_refs}
        for d in [*repo_dirs.values(), src_dir]:  # 重派发/重试时清掉上次残留，保证可幂等重跑
            shutil.rmtree(d, ignore_errors=True)
        t_fetch = time.monotonic()
        source_dirs: list[Path]
        if task.source_type == "git":
            git_log = lambda m: (_log(task, m), db.commit())  # noqa: E731
            # 每个仓库各自的凭据：仓库级令牌快照（逐仓库填写/从个人 Git 配置按域名解析）优先，项目级 PAT 兜底
            repo_tokens = load_repo_tokens(project.repo_tokens) if project else {}
            proj_auth = project.git_auth_type if project else ""
            proj_token = project.git_token if project else ""

            def _clone(url: str, dest, branch: str) -> None:
                auth, tok = repo_credential(repo_tokens, url, proj_auth, proj_token)
                clone_git(url, dest, git_log, branch=branch, auth_type=auth, token=tok)

            if len(repo_refs) > 1:
                _log(task, f"[fetch] 项目绑定 {len(repo_refs)} 个仓库，逐个克隆为独立扫描目标")
            for r in repo_refs:
                _clone(r["url"], repo_dirs[r["url"]], r["branch"])
            source_dirs = [repo_dirs[r["url"]] for r in repo_refs]
        else:
            upload = db.get(ProjectUpload, task.upload_id) if task.upload_id else None
            zip_path = Path(upload.stored_path) if upload and upload.stored_path else ws / "uploads" / f"{task_id}.zip"
            if not zip_path.is_file():
                raise SourceError("上传的压缩包丢失，请重新提交")
            src_dir.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(
                zip_path, src_dir, s.max_upload_mb * 1024 * 1024,
                lambda m: (_log(task, m), db.commit()),
            )
            source_dirs = [src_dir]
        _log(
            task,
            f"[fetch] 源码就绪（{sum(du_mb(d) for d in source_dirs)}MB"
            + (f"，{len(source_dirs)} 个目录：" + "、".join(f"{d.name} {du_mb(d)}MB" for d in source_dirs) if len(source_dirs) > 1 else "")
            + f"，耗时 {int(time.monotonic() - t_fetch)} 秒）",
        )

        # ---- Stage 2: strix 扫描（用户自定义指令 + 目标说明 + 中文报告提示词均通过 --instruction 注入）----
        if cancel_requested(task_id):
            result = _finish_as_cancelled(db, task, task_id, "源码获取完成后、扫描启动前被取消")
            db.close()
            return result
        _set_status(db, task, "scanning")
        _log(task, f"[scan] 模型: {task.model}（中文报告）")
        targets = effective_targets(task.test_targets, task.test_url)
        if targets:
            _log(
                task,
                "[scan] 黑盒目标: "
                + "；".join(f"{t['url']}（{t['note']}）" if t["note"] else t["url"] for t in targets),
            )
        # 用户指令在首位，其后依次是仓库结构 / 黑盒目标说明块，最后是中文报告要求
        instruction_parts: list[str] = [task.instruction] if task.instruction else []
        if task.source_type == "git" and len(repo_refs) > 1:
            notes = {
                r["url"]: r["note"]
                for r in (effective_repos(project.git_repos, project.git_url) if project else [])
            }
            lines = [
                "This project's source code consists of multiple repositories. "
                "Each repository is provided as a separate whitebox source directory "
                "(its own subdirectory under /workspace, named after the repository). "
                "When reporting a finding, set its target to the affected repository URL:"
            ]
            for i, r in enumerate(repo_refs, 1):
                seg = f"{i}. {r['url']}"
                if r["branch"]:
                    seg += f" (branch {r['branch']})"
                seg += f" [source at /workspace/{repo_dirs[r['url']].name}]"
                if notes.get(r["url"]):
                    seg += f" — {notes[r['url']]}"
                lines.append(seg)
            instruction_parts.append("\n".join(lines))
        if targets:
            lines = ["Black-box test targets for this scan (test each of them):"]
            for i, t in enumerate(targets, 1):
                lines.append(f"{i}. {t['url']}" + (f" — {t['note']}" if t["note"] else ""))
            instruction_parts.append("\n".join(lines))
        if task.web_search and s.web_search_mcp_url:
            instruction_parts.append(web_search_guide(s.web_search_mcp_url))
        instruction_parts.append(ZH_INSTRUCTION)
        instruction = "\n".join(instruction_parts).strip()
        result = execute_scan(
            work_dir=scan_dir,
            src_dirs=source_dirs,
            test_targets=targets,
            scan_mode=task.scan_mode,
            model=task.model or "",
            instruction=instruction,
            llm_api_key=user_llm_key,
            log=lambda m: (_log(task, m), db.commit()),
            cancel_check=lambda: cancel_requested(task_id),
        )
        task.exit_code = result["exit_code"]
        task.attempts = result["attempts"]
        task.timed_out = result["timed_out"]
        task.run_dir_name = result["run_dir_name"]

        if result.get("cancelled"):
            # 用户取消：不解析半成品产物、不归档、不翻译，直接落终态
            result = _finish_as_cancelled(db, task, task_id, "引擎进程已被终止")
            db.close()
            return result

        # ---- Stage 3: 产物解析入库 ----
        _set_status(db, task, "parsing")
        run_record, vulns = read_run_artifacts(scan_dir, result["run_dir_name"])
        usage = run_record.get("llm_usage") or {}
        task.total_tokens = usage.get("total_tokens")
        task.input_tokens = usage.get("input_tokens")
        task.output_tokens = usage.get("output_tokens")
        task.llm_requests = usage.get("requests")
        task.agents_usage = _agents_usage(run_record, scan_dir, result["run_dir_name"])
        # 官方执行摘要报告（strix view 展示的同款 penetration_test_report.md）
        report_path = scan_dir / "strix_runs" / result["run_dir_name"] / "penetration_test_report.md"
        try:
            task.report_md = report_path.read_text(encoding="utf-8")[:500_000]
        except OSError:
            task.report_md = ""
        if not task.report_md:
            _log(task, "[parse] 未生成执行摘要报告（penetration_test_report.md）")

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
            f"[parse] run={result['run_dir_name'] or '-'} · 智能体 {len(usage.get('agents') or [])} 个 · "
            f"漏洞 {len(vulns)} 条 {json.dumps(counts, ensure_ascii=False)} "
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
        if task.exit_code == 1 and not task.timed_out:
            # 退出码契约（runner.py）：0 无发现 / 1 执行错误 / 2 发现漏洞。
            # 1 是沙箱/代理/LLM 网关等执行错误，不是"扫描完成无发现"，须标记失败
            detail = _scan_error_detail(scan_dir)
            task.error = f"strix 执行失败（退出码 1）：{detail or '详见任务日志与归档产物'}"
            _log(task, f"[done] 状态: 失败，耗时 {task.duration_sec} 秒，{task.error}")
            _set_status(db, task, "failed")
            _notify(db, task, task_id)
            return {"task_id": task_id, "error": task.error}
        _log(
            task,
            f"[done] 状态: {'超时终止' if task.timed_out else '完成'}，"
            f"耗时 {task.duration_sec} 秒，发现 {task.findings_count} 条",
        )
        _set_status(db, task, "done")
        _notify(db, task, task_id)

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
        _notify(db, task, task_id)
        return {"task_id": task_id, "error": task.error}
    except Exception as exc:  # noqa: BLE001 —— 流水线任何异常都要落到任务状态
        task.error = f"执行异常: {exc}"
        _log(task, f"[fail] {task.error}")
        task.finished_at = datetime.now(timezone.utc)
        _set_status(db, task, "failed")
        _notify(db, task, task_id)
        return {"task_id": task_id, "error": task.error}
    finally:
        db.close()


def new_task_id() -> str:
    return uuid.uuid4().hex
