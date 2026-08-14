"""源码获取：git 克隆 / zip 安全解压（防路径穿越、解压炸弹、符号链接）。"""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

MAX_FILES = 200_000
CHUNK = 1024 * 1024


class SourceError(Exception):
    pass


def clone_git(url: str, dest: Path, log) -> None:
    log(f"[fetch] git clone (depth 1): {url}")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", url, str(dest)],
            capture_output=True, text=True, timeout=900, env=env,
        )
    except FileNotFoundError as exc:
        raise SourceError("服务器未安装 git") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError("git clone 超时（15 分钟）") from exc
    if proc.returncode != 0:
        raise SourceError(f"git clone 失败: {proc.stderr.strip()[-800:]}")


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
