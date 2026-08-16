"""源码获取：git 克隆（凭据/分支）/ 分支列举 / zip 安全解压（防路径穿越、解压炸弹、符号链接）。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

MAX_FILES = 200_000
CHUNK = 1024 * 1024
# 单项目可绑定的仓库数上限
MAX_REPOS = 10
# 单个仓库访问令牌长度上限（与 git_configs 令牌限制一致）
MAX_REPO_TOKEN = 512


class SourceError(Exception):
    pass


# ---- 多仓库列表（JSON [{"url", "note", "token"}]）解析与清洗 ----


def parse_repos(raw) -> list[dict]:
    """解析仓库列表 JSON；容忍 JSON 字符串 / 已解出的 list / 其他脏数据，出错返回 []。

    每项可带 token（创建/编辑表单逐仓库提交的专属访问令牌）：只进 repo_tokens 凭据快照，
    绝不随 git_repos 列持久化，也绝不回显。
    """
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    items: list[dict] = []
    for it in raw:
        if isinstance(it, str):
            items.append({"url": it, "note": "", "token": ""})
        elif isinstance(it, dict):
            items.append({
                "url": str(it.get("url") or ""),
                "note": str(it.get("note") or ""),
                "token": str(it.get("token") or ""),
            })
    return items


def dump_repos(items: list[dict]) -> str:
    # 只落 url/note：逐仓库令牌只进 repo_tokens 快照，绝不写进仓库列表列
    return json.dumps(
        [{"url": r.get("url", ""), "note": r.get("note", "")} for r in items],
        ensure_ascii=False,
    )


def normalize_repos(items: list[dict]) -> tuple[list[dict], str]:
    """清洗用户提交的仓库列表：去空白、丢弃空行、按 URL 去重、限制数量与说明/令牌长度。"""
    cleaned: list[dict] = []
    seen: set[str] = set()
    for it in items:
        url = str(it.get("url") or "").strip()
        note = str(it.get("note") or "").strip()[:200]
        token = str(it.get("token") or "").strip()
        if not url:
            continue
        if url in seen:
            continue
        if len(token) > MAX_REPO_TOKEN:
            return [], f"仓库 {url} 的访问令牌过长（≤{MAX_REPO_TOKEN} 字符）"
        seen.add(url)
        cleaned.append({"url": url, "note": note, "token": token})
    if len(cleaned) > MAX_REPOS:
        return [], f"单个项目最多绑定 {MAX_REPOS} 个仓库"
    return cleaned, ""


def effective_repos(git_repos: str, legacy_url: str) -> list[dict]:
    """读取侧合并：git_repos 列为非空字符串时即权威，否则回退旧版单仓库列（存量项目兼容）。
    输出只含 url/note（即便存量数据混入 token 也不回显）。"""
    if git_repos and git_repos.strip():
        return [
            {"url": r["url"], "note": r.get("note", "")}
            for r in parse_repos(git_repos) if r.get("url")
        ]
    url = (legacy_url or "").strip()
    return [{"url": url, "note": ""}] if url else []


def repo_dir_name(url: str, taken: set[str]) -> str:
    """从仓库 URL 推导克隆子目录名（去 .git 后缀）；重名时追加序号。"""
    name = re.split(r"[/:]", url.rstrip("/")).pop() or "repo"
    if name.endswith(".git"):
        name = name[: -len(".git")]
    base, i = name, 1
    while name in taken:
        i += 1
        name = f"{base}_{i}"
    taken.add(name)
    return name


# ---- 任务侧：多仓库各自的扫描分支（JSON [{"url", "branch"}]） ----


def parse_repo_branches(raw) -> list[dict]:
    """解析任务的多仓库分支 JSON；坏数据返回 []。"""
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for it in raw:
        if isinstance(it, dict):
            out.append({"url": str(it.get("url") or ""), "branch": str(it.get("branch") or "")})
    return out


def effective_repo_branches(repo_branches: str, legacy_source_ref: str, legacy_branch: str) -> list[dict]:
    """读取侧合并：repo_branches 列为非空字符串时即权威，否则回退旧版单仓库
    （source_ref + branch，存量任务兼容）。"""
    if repo_branches and repo_branches.strip():
        return [r for r in parse_repo_branches(repo_branches) if r["url"]]
    url = (legacy_source_ref or "").strip()
    return [{"url": url, "branch": (legacy_branch or "").strip()}] if url else []


def load_repo_tokens(repo_tokens_json: str) -> dict[str, str]:
    """解析各仓库专属凭据快照（JSON {"仓库地址": "token"}）；坏数据返回 {}。"""
    if not repo_tokens_json or not repo_tokens_json.strip():
        return {}
    try:
        data = json.loads(repo_tokens_json)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v}


def repo_credential(repo_tokens: dict[str, str], url: str, project_auth_type: str, project_token: str) -> tuple[str, str]:
    """单个仓库的克隆/探测凭据：仓库级快照优先，项目级 PAT 兜底；返回 (auth_type, token)。"""
    token = repo_tokens.get(url) or (project_token if project_auth_type == "token" else "")
    return ("token", token) if token else ("", "")


def _git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if extra:
        env.update(extra)
    return env


def _token_url(url: str, token: str) -> str:
    """把 https 凭据注入 URL（token 形如 `token` 或 `user:token`）。"""
    if "://" not in url:
        raise SourceError("仅支持 http(s) Git 地址配置 token 凭据")
    scheme, rest = url.split("://", 1)
    if "@" in rest.split("/", 1)[0]:  # 已带凭据
        return url
    userpass = token if ":" in token else f"oauth2:{token}"
    return f"{scheme}://{userpass}@{rest}"


def list_branches(git_url: str, auth_type: str = "", token: str = "") -> list[str]:
    """git ls-remote --heads 列出远端分支；同时返回默认分支放首位。"""
    url = _token_url(git_url, token) if auth_type == "token" and token else git_url
    env = _git_env()
    default = ""
    try:
        head = subprocess.run(
            ["git", "ls-remote", "--symref", url, "HEAD"],
            capture_output=True, text=True, timeout=120, env=env,
        )
        m_head = re.search(r"ref: (refs/heads/\S+)\s+HEAD", head.stdout)
        if m_head:
            default = m_head.group(1).split("/", 2)[2]
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", url],
            capture_output=True, text=True, timeout=120, env=env,
        )
    except FileNotFoundError as exc:
        raise SourceError("服务器未安装 git") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError("获取分支列表超时") from exc
    if proc.returncode != 0:
        raise SourceError(f"获取分支列表失败: {proc.stderr.strip()[-500:]}")
    branches: list[str] = []
    for line in proc.stdout.splitlines():
        m = re.match(r"[0-9a-f]+\s+refs/heads/(\S+)$", line)
        if m:
            branches.append(m.group(1))
    if default and default in branches:
        branches.remove(default)
        branches.insert(0, default)
    return branches


def clone_git(
    url: str,
    dest: Path,
    log,
    branch: str = "",
    auth_type: str = "",
    token: str = "",
) -> None:
    """克隆指定分支（depth 1）；branch 为空时克隆默认分支。"""
    effective_url = _token_url(url, token) if auth_type == "token" and token else url
    env = _git_env()

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch, "--single-branch"]
    else:
        cmd += ["--single-branch"]
    # 凭据不出现在日志里
    display = re.sub(r"://[^@/]+@", "://***@", effective_url)
    log(f"[fetch] git clone (depth 1, branch={branch or '默认'}): {display}")
    try:
        proc = subprocess.run(cmd + [effective_url, str(dest)], capture_output=True, text=True, timeout=900, env=env)
    except FileNotFoundError as exc:
        raise SourceError("服务器未安装 git") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError("git clone 超时（15 分钟）") from exc
    if proc.returncode != 0:
        err = re.sub(r"://[^@/]+@", "://***@", proc.stderr.strip()[-800:])
        raise SourceError(f"git clone 失败: {err}")


def safe_extract_zip(zip_path: Path, dest: Path, max_total_bytes: int, log) -> None:
    log(f"[fetch] 解压 {zip_path.name}（上限 {max_total_bytes // (1024 * 1024)}MB / {MAX_FILES} 个文件）")
    total = 0
    count = 0
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            count += 1
            if count > MAX_FILES:
                raise SourceError("压缩包文件数超出上限（防解压炸弹）")
            if info.is_dir():
                continue
            total += info.file_size
            if total > max_total_bytes:
                raise SourceError("解压后总体积超出上限（防解压炸弹）")
            # 路径穿越与符号链接防护
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise SourceError(f"压缩包含非法条目: {name}")

        zf.extractall(dest)

    # zip 内常见“套一层目录”，strix 直接扫 dest，无需拍平（多目标本就支持目录）
    entries = [p for p in dest.iterdir() if not p.name.startswith(".")]
    log(f"[fetch] 解压完成：{count} 个条目，顶层 {len(entries)} 项")


def du_mb(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total // (1024 * 1024)
