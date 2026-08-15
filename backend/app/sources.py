"""源码获取：git 克隆（凭据/分支）/ 分支列举 / zip 安全解压（防路径穿越、解压炸弹、符号链接）。"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

MAX_FILES = 200_000
CHUNK = 1024 * 1024


class SourceError(Exception):
    pass


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


def _ssh_env(key_text: str) -> tuple[dict[str, str], Path]:
    """把 PEM 私钥写入临时文件并构造 GIT_SSH_COMMAND。"""
    key_file = Path(tempfile.mkstemp(suffix=".key")[1])
    key_file.write_text(key_text if key_text.endswith("\n") else key_text + "\n", encoding="utf-8")
    os.chmod(key_file, 0o600)
    cmd = f"ssh -i {key_file} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    return _git_env({"GIT_SSH_COMMAND": cmd}), key_file


def _cleanup_key(key_file: Path | None) -> None:
    if key_file is not None:
        key_file.unlink(missing_ok=True)


def list_branches(git_url: str, auth_type: str = "", token: str = "", ssh_key: str = "") -> list[str]:
    """git ls-remote --heads 列出远端分支；同时返回默认分支放首位。"""
    url, env, key_file = git_url, _git_env(), None
    default = ""
    try:
        if auth_type == "token" and token:
            url = _token_url(git_url, token)
        elif auth_type == "ssh" and ssh_key:
            env, key_file = _ssh_env(ssh_key)
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
    finally:
        _cleanup_key(key_file)


def clone_git(
    url: str,
    dest: Path,
    log,
    branch: str = "",
    auth_type: str = "",
    token: str = "",
    ssh_key: str = "",
) -> None:
    """克隆指定分支（depth 1）；branch 为空时克隆默认分支。"""
    effective_url, env, key_file = url, _git_env(), None
    try:
        if auth_type == "token" and token:
            effective_url = _token_url(url, token)
        elif auth_type == "ssh" and ssh_key:
            env, key_file = _ssh_env(ssh_key)

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
    finally:
        _cleanup_key(key_file)


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
