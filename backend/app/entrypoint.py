"""容器入口：自动把 WORKSPACE_ROOT 对齐为宿主 Docker daemon 可见的同一路径。

背景：strix 沙箱容器由宿主 daemon 经 docker.sock 创建，源码目录以 bind mount
挂入沙箱，挂载源路径按 daemon 所在命名空间解析。worker 容器内的工作区路径
（默认 /data/workspaces）在 Docker Desktop 的 VM 里不存在，沙箱创建会报
"bind source path does not exist"（HTTP 400）。这里在启动时查询自身容器的
挂载配置反推 daemon 可见路径（Docker Desktop 为 /run/desktop/mnt/host/<盘符>/…，
原生 Linux/macOS 即宿主绝对路径，daemon 本就可解析），并在容器内建符号链接
指向实际挂载点，使「容器内文件操作」与「传给 daemon 的挂载源」共用同一串路径。
WORKSPACE_CONTAINER_ROOT 显式配置时优先采用，跳过探测。
"""

from __future__ import annotations

import os
import socket
import sys

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


def resolve_workspace_root() -> str | None:
    root = os.environ.get("WORKSPACE_ROOT", DEFAULT_ROOT)
    explicit = os.environ.get("WORKSPACE_CONTAINER_ROOT", "").strip()
    if explicit:
        return explicit
    try:
        import docker

        client = docker.from_env(timeout=5)
        ctr = client.containers.get(socket.gethostname())
    except Exception as exc:  # api 容器无 docker.sock、本机直跑等场景：保持原值
        print(f"[entrypoint] 工作区路径保持 {root}（未探测到自身容器：{exc}）", file=sys.stderr)
        return None
    source = next(
        (
            m.get("Source")
            for m in ctr.attrs.get("Mounts", [])
            if m.get("Type") == "bind" and m.get("Destination") == root
        ),
        None,
    )
    if not source:
        print(f"[entrypoint] 工作区路径保持 {root}（自身挂载中未找到 {root}）", file=sys.stderr)
        return None
    visible = _daemon_visible_path(source)
    if not visible or visible == root:
        return visible
    # 把 daemon 可见路径链到现有挂载点；若该位置残留同名空目录则先移除
    os.makedirs(os.path.dirname(visible), exist_ok=True)
    try:
        if os.path.islink(visible) or not os.path.lexists(visible):
            os.symlink(root, visible)
        elif os.path.isdir(visible) and not os.listdir(visible):
            os.rmdir(visible)
            os.symlink(root, visible)
        else:
            print(f"[entrypoint] {visible} 已存在且非空，跳过符号链接", file=sys.stderr)
            return None
    except OSError as exc:
        print(f"[entrypoint] 建立符号链接失败：{exc}", file=sys.stderr)
        return None
    print(f"[entrypoint] WORKSPACE_ROOT 对齐为 daemon 可见路径: {visible}", file=sys.stderr)
    return visible


def main() -> None:
    resolved = resolve_workspace_root()
    if resolved:
        os.environ["WORKSPACE_ROOT"] = resolved
    cmd = sys.argv[1:]
    if not cmd:
        # compose 设置了 entrypoint 却没给 command 时会发生：静默退出 0 会伪装成
        # 正常启动，必须响亮失败
        print("[entrypoint] 未提供启动命令（检查 compose 的 command 或镜像 CMD）", file=sys.stderr)
        sys.exit(1)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
