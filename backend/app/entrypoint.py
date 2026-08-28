"""容器入口：诊断工作区路径是否已与宿主 Docker daemon 对齐，并转发启动命令。

背景：strix 沙箱由宿主 daemon 经 docker.sock 创建，bind mount 源路径按 daemon
所在命名空间解析，且 strix 内部会对目标路径做 realpath（符号链接会被还原），
因此工作区必须在容器内「真实挂载」于 daemon 可见的同一路径上。

默认部署（docker-compose.yml）已用固定名具名卷承载工作区，并把容器内挂载点
设为该卷在 daemon 侧的存储路径（worker/api 与沙箱三方路径天然同串对齐），
无需任何平台探测。本脚本保留为防线：若有人覆盖 WORKSPACE_ROOT 或改回平台
相关 bind 部署导致不对齐，启动时明确提示——否则症状只剩 strix 报
"bind source path does not exist"（HTTP 400），难以定位。
"""

from __future__ import annotations

import os
import socket
import sys
import time

DEFAULT_ROOT = "/var/lib/docker/volumes/strix-platform_workspaces/_data"


def _daemon_visible_path(mount: dict) -> str | None:
    """取该挂载在 daemon 命名空间里的等价可见路径；无法判定返回 None。"""
    mtype = mount.get("Type") or ("volume" if mount.get("Name") else "bind")
    source = (mount.get("Source") or "").replace("\\", "/")
    if mtype == "volume":
        return source  # 具名卷的宿主侧存储路径，daemon 可直接解析
    if len(source) > 2 and source[1] == ":":  # Windows 盘符 → Docker Desktop VM 挂载点
        rest = source[2:].lstrip("/")
        return f"/run/desktop/mnt/host/{source[0].lower()}/{rest}"
    if source.startswith("/"):  # macOS/Linux 宿主绝对路径，daemon 直接解析
        return source
    return None


def check_alignment() -> None:
    root = os.environ.get("WORKSPACE_ROOT", DEFAULT_ROOT)
    if os.environ.get("WORKSPACE_CONTAINER_ROOT", "").strip():
        return  # 显式旧式配置：视为运维自管对齐，不再多言
    try:
        import docker

        ctr = docker.from_env(timeout=5).containers.get(socket.gethostname())
    except Exception:
        return  # api 容器无 docker.sock、本机直跑：不涉及沙箱创建，保持安静
    mount = next(
        (m for m in ctr.attrs.get("Mounts", []) if m.get("Destination") == root),
        None,
    )
    if not mount:
        return
    visible = _daemon_visible_path(mount)
    if visible and visible != root:
        print(
            f"[entrypoint] 警告：工作区在容器内挂载于 {root}（{mount.get('Type')}），"
            f"但宿主 daemon 只认 {visible}。strix 创建沙箱时会报 "
            "'bind source path does not exist'。\n"
            "[entrypoint] 请恢复 compose 默认的工作区具名卷部署"
            f"（WORKSPACE_ROOT={DEFAULT_ROOT}），或按 README 把容器内挂载点与 "
            "daemon 路径手工对齐。",
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
