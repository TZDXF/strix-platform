"""容器入口：诊断工作区路径是否已与宿主 Docker daemon 对齐，并转发启动命令。

背景：strix 沙箱由宿主 daemon 经 docker.sock 创建，bind mount 源路径按 daemon
所在命名空间解析，且 strix 内部会对目标路径做 realpath（符号链接会被还原），
因此工作区必须在容器内「真实挂载」于 daemon 可见的同一路径上——这只能在
compose 创建容器时确定，由仓库根目录的 ./deploy.sh 自动探测注入
（WORKSPACE_HOST_DIR / WORKSPACE_CONTAINER_ROOT），或按 README 手工设置。

本脚本不再改写路径，只做两件事：
1. 检测到「容器内挂载点 ≠ daemon 可见路径」时输出明确的修复指引（常见于
   绕过 deploy.sh 直接 docker compose 的场景，症状为沙箱创建报
   "bind source path does not exist"，HTTP 400）；
2. exec 真正的启动命令；未收到命令（如 compose 设了 entrypoint 却漏了
   command）时响亮失败，避免伪装成正常退出。
"""

from __future__ import annotations

import os
import socket
import sys
import time

DEFAULT_ROOT = "/data/workspaces"


def _daemon_visible_path(source: str) -> str | None:
    """把 bind mount 的宿主 Source 翻译成 daemon 解析 bind source 时的等价路径。"""
    s = source.replace("\\", "/")
    if len(s) > 2 and s[1] == ":":  # Windows 盘符 → Docker Desktop VM 内的挂载点
        rest = s[2:].lstrip("/")
        return f"/run/desktop/mnt/host/{s[0].lower()}/{rest}"
    if s.startswith("/"):  # VM 路径或原生 Linux/macOS 宿主绝对路径，daemon 均可直接解析
        return s
    return None


def check_alignment() -> None:
    root = os.environ.get("WORKSPACE_ROOT", DEFAULT_ROOT)
    if os.environ.get("WORKSPACE_CONTAINER_ROOT", "").strip():
        return  # 显式配置：compose 已把挂载点对齐到该路径，无需多言
    try:
        import docker

        ctr = docker.from_env(timeout=5).containers.get(socket.gethostname())
    except Exception:
        return  # api 容器无 docker.sock、本机直跑：不涉及沙箱创建，保持安静
    source = next(
        (
            m.get("Source")
            for m in ctr.attrs.get("Mounts", [])
            if m.get("Type") == "bind" and m.get("Destination") == root
        ),
        None,
    )
    if not source:
        return
    visible = _daemon_visible_path(source)
    if visible and visible != root:
        print(
            f"[entrypoint] 警告：工作区在容器内挂载于 {root}，但宿主 daemon 只认 "
            f"{visible}。strix 创建沙箱时会报 'bind source path does not exist'。\n"
            "[entrypoint] 请改用仓库根目录 ./deploy.sh 部署（自动对齐），或在 .env 设置\n"
            f"[entrypoint] WORKSPACE_CONTAINER_ROOT={visible}",
            file=sys.stderr,
        )


def main() -> None:
    t0 = time.perf_counter()
    check_alignment()
    print(f"[entrypoint] 工作区路径诊断完成，耗时 {time.perf_counter() - t0:.1f}s", flush=True)
    cmd = sys.argv[1:]
    if not cmd:
        print("[entrypoint] 未提供启动命令（检查 compose 的 command 或镜像 CMD）", file=sys.stderr)
        sys.exit(1)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
