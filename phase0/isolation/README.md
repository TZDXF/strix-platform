# Phase 0 受限出网验证

目的：验证 strix 沙箱在"出向白名单"条件下能否正常工作。这是共享主机部署的
安全前提（见 ../docs/feasibility.md 第 4/5.2 节），必须在写平台代码前实测。

白名单只放行三类目的地：
1. 本任务的黑盒目标（用户填写的 URL）
2. 公司 LLM 网关（Agent 编排层的 LLM 调用来自宿主机进程，不走此网络；此条
   供沙箱内工具确需访问网关的场景）
3. 内部包管理镜像（构建 PoC 依赖）

其余一律拒绝：内网其他网段、公网、云元数据地址（169.254.169.254）。

## 使用（Linux 服务器上、root/sudo）

```bash
# 1. 创建任务网络 + 白名单规则
#    参数：任务ID 目标host:port[,host:port...] [LLM网关host:port] [包镜像host:port]
sudo ./isolate.sh task-test01 10.0.0.5:3000 llm-gw.company.internal:443 nexus.company.internal:443

# 2. 在受限网络里起一个测试容器做连通性验证
docker run --rm --network strix-task-task-test01 \
  -e TARGET=10.0.0.5:3000 alpine sh -c '
    echo "--- 应通: 目标 ---"; wget -q -O- --timeout=5 http://$TARGET | head -c 200 && echo
    echo "--- 应断: 公网 ---"; wget -q -O- --timeout=5 https://example.com && echo UNEXPECTED-OK || echo blocked-ok
    echo "--- 应断: 云元数据 ---"; wget -q -O- --timeout=3 http://169.254.169.254 && echo UNEXPECTED-OK || echo blocked-ok
  '

# 3. 用真实 strix 跑一次扫描验证功能（在受限网络下）
#    strix 的沙箱容器由 docker backend 创建；把沙箱接入任务网络的方式见下方说明

# 4. 清理
sudo ./cleanup.sh task-test01
```

## 让 strix 沙箱接入任务网络的说明

strix 通过 Docker backend 创建沙箱容器。Phase 0 采用最简单的验证路径：
- 方案一（推荐）：直接用 DOCKER-USER 按"源网络"过滤宿主机上所有 docker 网络，
  isolate.sh 的规则按子网匹配，对 strix 自建的网络同样生效——先用 strix 跑起来，
  观察其沙箱网络名，把该子网加入规则。
- 方案二：STRIX_RUNTIME_BACKEND 如支持指定网络（查 `strix/config/settings.py`
  对应的环境变量），直接让沙箱用任务网络。

无论哪种，验证记录（哪些能力退化）写回本 README 末尾。

## 验证记录（跑完填写）

| 日期 | strix 版本 | 验证内容 | 结果 | 退化项 | 备注 |
|------|-----------|----------|------|--------|------|
|      |           |          |      |        |      |
