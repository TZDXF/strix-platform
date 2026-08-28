#!/usr/bin/env bash
# strix-platform 部署入口：docker compose 的透传别名（兼容老习惯保留）。
#
# 历史背景：工作区曾用宿主目录 bind 挂载，其 daemon 可见路径含盘符等平台差异，
# 本脚本曾负责探测注入 WORKSPACE_HOST_DIR / WORKSPACE_CONTAINER_ROOT。现 compose
# 已改为固定名具名卷承载工作区，容器内挂载点即该卷在 daemon 侧的真实存储路径，
# 三方路径天然对齐，直接 `docker compose up -d --build` 即可，本脚本不再做任何处理。
#
# 用法与 docker compose 完全一致，例如：
#   ./deploy.sh up -d --build
#   ./deploy.sh logs -f worker
set -euo pipefail

cd "$(dirname "$0")"
exec docker compose "$@"
