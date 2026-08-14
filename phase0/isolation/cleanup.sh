#!/usr/bin/env bash
# 按 isolate.sh 打的标记清理 DOCKER-USER 规则并删除任务网络。
# 用法: sudo ./cleanup.sh <task-id>
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo/root" >&2; exit 1; }
TASK_ID="${1:?usage: cleanup.sh <task-id>}"
TAG="strix-task-$TASK_ID"
NET="strix-task-$TASK_ID"

# 反复删除直到没有带标记的规则
while iptables -S DOCKER-USER 2>/dev/null | grep -q "$TAG"; do
  RULE=$(iptables -S DOCKER-USER | grep "$TAG" | head -1 | sed 's/^-A /-D /')
  iptables "$RULE" 2>/dev/null || iptables -C $(echo "$RULE" | sed 's/^-D //') 2>/dev/null || true
  # 上面删除失败时强制重试下一条，避免死循环
  iptables -S DOCKER-USER | grep -q "$TAG" || break
  RULE2=$(iptables -S DOCKER-USER | grep "$TAG" | head -1 | sed 's/^-A /-D /')
  [[ "$RULE2" == "$RULE" ]] && { iptables $RULE2 2>/dev/null || true; }
done
echo "rules flushed for $TAG"

docker network rm "$NET" 2>/dev/null && echo "network $NET removed" || echo "network $NET already gone"
