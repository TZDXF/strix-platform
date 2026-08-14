# Strix 内部安全测试平台

团队内部使用的安全测试网站：提交 **Git 仓库地址或 zip 压缩包**（可选附加内网测试地址），
平台排队执行 [Strix](https://github.com/usestrix/strix) AI 渗透测试（白盒源码 + 黑盒 URL），
生成漏洞报告（严重级别 / PoC / 修复建议），产物可下载归档。

> 架构决策与可行性分析见 [docs/feasibility.md](docs/feasibility.md)（已锁定 7 项决策）。
> Phase 0 引擎验证数据见 [phase0/EVALUATION.md](phase0/EVALUATION.md)
> （Juice Shop 靶场：10 条发现 / 0 误报 / quick 模式 77 分钟 / 8200 万 token）。

## 组成

```
frontend/   Vue 3 + Vite（提交表单 / 任务列表 / 报告详情）
backend/    FastAPI + Celery + SQLAlchemy
              ├─ app/main.py      API（共享令牌 + 审计 + 目标允许清单）
              ├─ app/tasks.py     流水线：fetch → scan → parse → archive
              ├─ app/runner.py    strix 子进程执行器（失败自动重试）
              └─ app/artifacts.py 产物归档（RustFS S3 API / 本地盘）
docker-compose.yml  postgres + redis + rustfs + api + worker + frontend
```

## 本地开发

```bash
# 依赖容器
docker run -d --name strix-pg -e POSTGRES_USER=strix -e POSTGRES_PASSWORD=strix -e POSTGRES_DB=strix -p 5432:5432 postgres:16-alpine
docker run -d --name strix-redis -p 6379:6379 redis:7-alpine

# 后端（Python 3.12+，含 strix-agent==1.5.3）
cd backend
pip install -r requirements.txt
cp ../phase0/.env .env   # 或按下表配置；STRIX_BIN 指向 venv 里的 strix 可执行文件
uvicorn app.main:app --port 8000
celery -A app.celery_app.celery_app worker --pool=solo --loglevel=info   # Windows 必须 --pool=solo

# 前端
cd frontend && npm install && npm run dev   # http://localhost:5173
```

## 服务器部署（Docker Compose，Linux + Docker）

```bash
cp .env.deploy.example .env   # 填 API_TOKEN / LLM 网关三项 / RustFS 密钥
docker compose up -d --build
# 首次：创建 RustFS 桶
docker compose exec api python -c "
import boto3, os
from botocore.client import Config
c = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT'],
    aws_access_key_id=os.environ['S3_ACCESS_KEY'], aws_secret_access_key=os.environ['S3_SECRET_KEY'],
    config=Config(signature_version='s3v4'), region_name='us-east-1')
c.create_bucket(Bucket=os.environ['S3_BUCKET'])"
```

访问 `http://<server>/`，右上角填访问令牌（`API_TOKEN`）。

> Worker 挂载了 `/var/run/docker.sock`（strix 沙箱依赖 Docker）。
> 共享主机部署时务必阅读可行性文档 5.2 节的网络隔离要求（任务级 DOCKER-USER 白名单），
> 验证脚本见 `phase0/isolation/`。

## 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `API_TOKEN` | 共享访问令牌（必填） | - |
| `DATABASE_URL` / `REDIS_URL` | 依赖连接 | 本机默认 |
| `LLM_API_BASE` / `LLM_API_KEY` / `STRIX_LLM` | 公司 LLM 网关（OpenAI 兼容）与模型（决策 #7：仅 free 档） | `free` |
| `WORKSPACE_ROOT` | 任务源码/工作区/上传目录 | `./workspaces` |
| `STRIX_BIN` | strix 可执行文件路径（dev 为 venv 内路径） | `strix` |
| `S3_ENABLED` / `S3_ENDPOINT` / `S3_*` | RustFS 对象存储；false 时产物留本地磁盘 | false |
| `TARGET_ALLOWLIST` | 黑盒目标允许清单（域名后缀/CIDR，逗号分隔） | 空=仅内网/回环 |
| `MAX_UPLOAD_MB` | zip 上限 | 500 |
| `TIMEOUT_QUICK/STANDARD/DEEP` | 任务超时秒数（Phase 0 实测校准） | 7200/14400/28800 |
| `MAX_SCAN_ATTEMPTS` | LLM 连接失败重试次数 | 3 |

## 使用流程

1. 首页填令牌 → 「发布新扫描任务」：Git 地址或 zip + 可选黑盒测试地址 + 档位（quick/standard/deep 全开放）。
2. 任务列表实时刷新状态：`pending → fetching → scanning → parsing → done/failed`。
3. 点开任务看报告：漏洞按严重级别排序，可展开描述与修复建议；可下载产物 zip（漏洞 JSON/CSV/MD、SARIF、执行日志）。

## 安全护栏（决策 #4：暂不做权限体系）

- 共享访问令牌（所有 /api 请求校验）
- 全量审计日志（`audit_entries` 表：IP/动作/详情）
- 黑盒目标允许清单（默认仅内网/回环地址，公网目标直接拒绝）

## 已知边界

- 扫描耗时以实测为准：quick 双目标约 1 小时+（任务超时已按此校准）
- free 模型池有间歇故障，worker 自动重试（最多 3 次）
- 漏洞结果为 AI 辅助测试产出，报告页已标注建议人工复核
