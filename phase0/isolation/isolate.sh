#!/usr/bin/env bash
# 创建任务专用 docker 网络 + DOCKER-USER 出向白名单（Phase 0 手工验证用）。
# 用法: sudo ./isolate.sh <task-id> <目标host:port[,host:port...]> [llm网关host:port] [包镜像host:port]
# 示例: sudo ./isolate.sh test01 10.0.0.5:3000 llm-gw.company.internal:443
#
# 注意: 域名会在建规则时解析为 IP；若目标使用 CDN/多 IP 需自行扩展。
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo/root" >&2; exit 1; }
TASK_ID="${1:?usage: isolate.sh <task-id> <targets host:port[,..]> [gateway host:port] [mirror host:port]}"
TARGETS="${2:?need target host:port list}"
GATEWAY="${3:-}"
MIRROR="${4:-}"

NET="strix-task-$TASK_ID"
docker network create "$NET" >/dev/null
SUBNET=$(docker network inspect "$NET" -f '{{(index .IPAM.Config 0).Subnet}}')
echo "network=$NET subnet=$SUBNET"

# 依赖项检查
for bin in iptables getent; do
  command -v "$bin" >/dev/null || { echo "missing: $bin" >&2; exit 1; }
done

# 规则统一打注释标记，cleanup.sh 按标记删除
TAG="strix-task-$TASK_ID"

add_allow() {  # add_allow <host:port>
  local hp="$1" host port ips
  host="${hp%%:*}" port="${hp##*:}"
  ips=$(getent hosts "$host" | awk '{print $1}' | sort -u)
  [[ -n "$ips" ]] || { echo "WARN: cannot resolve $host, skipped" >&2; return; }
  for ip in $ips; do
    iptables -I DOCKER-USER -s "$SUBNET" -d "$ip" -p tcp --dport "$port" \
      -m comment --comment "$TAG" -j ACCEPT
    echo "allow: $SUBNET -> $host($ip):$port"
  done
}

# 已建立连接放行
iptables -I DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED \
  -m comment --comment "$TAG" -j ACCEPT

IFS=',' read -ra TARR <<< "$TARGETS"
for t in "${TARR[@]}"; do add_allow "$t"; done
[[ -n "$GATEWAY" ]] && add_allow "$GATEWAY"
[[ -n "$MIRROR" ]] && add_allow "$MIRROR"

# 其余出向一律拒绝（放在 DOCKER-USER 末尾，位于上述 ACCEPT 之后）
iptables -A DOCKER-USER -s "$SUBNET" -m comment --comment "$TAG" -j DROP
echo "drop:   $SUBNET -> anywhere else"
echo "done. verify with the test container command in README.md"
