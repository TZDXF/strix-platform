#!/usr/bin/env bash
# strix-platform 部署入口：自动探测并把工作区路径对齐为宿主 Docker daemon 可见路径。
#
# 为什么需要：strix 沙箱由宿主 daemon 经 docker.sock 创建，bind mount 源路径按 daemon
# 所在命名空间解析，且 strix 内部会对目标路径做 realpath（符号链接会被还原），因此
# worker 容器内的工作区必须「真实挂载」在 daemon 可见的同一路径上。该路径含盘符等
# 平台差异，compose 无法自行计算，故由本脚本在 compose 之前注入：
#   - Docker Desktop（Win/mac）：/run/desktop/mnt/host/<宿主绝对路径>
#   - 原生 Linux：WORKSPACE_HOST_DIR 与 WORKSPACE_CONTAINER_ROOT 设为同一宿主绝对路径
# 用法与 docker compose 完全一致，例如：
#   ./deploy.sh up -d --build
#   ./deploy.sh logs -f worker
set -euo pipefail

cd "$(dirname "$0")"

os_name="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || echo '')"
host_dir="$(pwd -W 2>/dev/null || pwd)"   # Git Bash 下 pwd -W 得到 D:/... 形式
workspace_host="$host_dir/docker/data/workspaces"

case "$os_name" in
  "Docker Desktop")
    case "$workspace_host" in
      [A-Za-z]:/*)   # Windows 盘符 → Docker Desktop VM 内的挂载点（盘符小写）
        drive="$(echo "${workspace_host%%:*}" | tr 'A-Z' 'a-z')"
        rest="${workspace_host#*:}"
        container_root="/run/desktop/mnt/host/$drive$rest"
        ;;
      /*)            # macOS：daemon 可解析宿主绝对路径，但容器内须同串，统一加 VM 前缀
        container_root="/run/desktop/mnt/host$workspace_host"
        ;;
      *)
        echo "[deploy] 无法识别的工作区路径：$workspace_host" >&2
        exit 1
        ;;
    esac
    ;;
  *)
    # 原生 Linux：daemon 与宿主同一命名空间，挂载点直接用宿主绝对路径即可
    container_root="$workspace_host"
    ;;
esac

export WORKSPACE_HOST_DIR="$workspace_host"
export WORKSPACE_CONTAINER_ROOT="$container_root"
echo "[deploy] 工作区：宿主 $WORKSPACE_HOST_DIR → 容器/daemon 同名路径 $WORKSPACE_CONTAINER_ROOT"

# Git Bash（MSYS）会把看似路径的参数/环境变量值自动改写成 Windows 形式
# （如 /run/desktop/... → C:/Program Files/Git/run/desktop/...），导致挂载配置
# 损坏；这里显式关闭对本脚本路径变量的转换。非 MSYS 环境下设置无副作用。
export MSYS_NO_PATHCONV=1
export MSYS2_ENV_CONV_EXCL="WORKSPACE_HOST_DIR;WORKSPACE_CONTAINER_ROOT"

exec docker compose "$@"
