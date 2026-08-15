# Strix 内部安全测试平台

团队内部使用的安全测试网站：创建**项目**（Git 仓库或 zip 压缩包），
在项目内发起 [Strix](https://github.com/usestrix/strix) AI 渗透测试（白盒源码 + 黑盒 URL），
生成漏洞报告（严重级别 / PoC / 修复建议，支持中文），可导出官方风格 PDF 报告，产物可下载归档。

> 架构决策与可行性分析见 [docs/feasibility.md](docs/feasibility.md)。
> Phase 0 引擎验证数据见 [phase0/EVALUATION.md](phase0/EVALUATION.md)
> （Juice Shop 靶场：10 条发现 / 0 误报 / quick 模式 77 分钟 / 8200 万 token）。

## 组成

```
frontend/   Vue 3 + Vite（登录 / 项目 / 任务 / 用户管理 / 报告页）
backend/    FastAPI + Celery + SQLAlchemy
              ├─ app/main.py      API（用户认证 + 项目 + 任务 + 报告/产物）
              ├─ app/auth.py      账号体系（PBKDF2 口令 + HMAC 签名令牌）
              ├─ app/tasks.py     流水线：fetch → scan → parse → archive → translate
              ├─ app/translate.py 中文报告兜底翻译（LLM 网关）
              └─ app/runner.py    strix 子进程执行器（失败自动重试）
docker-compose.yml  postgres + redis + rustfs + api + worker + frontend
```

## 后台管理功能

1. **用户管理**：超管（admin）/ 普通用户（user）两种角色，账号只能由超管创建（用户管理页），
   支持重置密码、启用/停用、升降角色；系统保留至少一个可用超管。
   首次启动自动创建 `admin`，密码取 `ADMIN_INITIAL_PASSWORD`（默认 `strix-admin-123`，请尽快修改）。
2. **任务列表**：普通用户只看自己的任务，超管查看全部（含提交人列）。
3. **项目化任务发布**：先创建项目（Git 地址可配置 token / SSH 私钥凭据；或 zip 上传型项目），
   在项目内发起扫描：Git 项目拉取远端**分支列表**供选择（默认分支排首位）；
   zip 项目可选**历史上传**复用或**新上传**（新上传自动存入历史）。
4. **PDF 导出**：报告页一键导出官方风格 PDF（复用 strix 内置 reportlab 渲染，结果本地缓存）。
5. **中文报告**：发起任务可选「中文报告」——通过 `--instruction` 提示词要求 strix 用简体中文撰写，
   扫描完成后再经 LLM 网关把标题/描述/修复建议翻译入库兜底（`*_zh` 字段，前端可切换）。
6. **报告页**：对齐 `strix view` 官方报告结构——严重级别概览网格（严重/高危/中危/低危/提示）、
   官方执行摘要报告（`penetration_test_report.md`，Markdown 渲染）、逐条漏洞明细
   （彩色级别条 / CVSS / CWE / PoC 说明与脚本）。

## 本地开发

```bash
# 依赖容器
docker run -d --name strix-pg -e POSTGRES_USER=strix -e POSTGRES_PASSWORD=strix -e POSTGRES_DB=strix -p 5432:5432 postgres:16-alpine
docker run -d --name strix-redis -p 6379:6379 redis:7-alpine

# 后端（uv 管理，Python 3.12+，含 strix-agent==1.5.3）
cd backend
uv sync
cp ../phase0/.env .env   # 或按下表配置；STRIX_BIN 指向 venv 里的 strix 可执行文件
uv run uvicorn app.main:app --port 8000
uv run celery -A app.celery_app.celery_app worker --pool=solo --loglevel=info   # Windows 必须 --pool=solo

# 前端（API_PROXY 可覆盖代理目标，默认 http://localhost:8000）
cd frontend && npm install && npm run dev   # http://localhost:5173
```

首次启动用 `admin` / `ADMIN_INITIAL_PASSWORD`（默认 `strix-admin-123`）登录，
在「用户管理」给同事开号后请立即修改默认密码。

## 服务器部署（Docker Compose，Linux + Docker）

```bash
cp .env.deploy.example .env   # 填 SECRET_KEY / LLM 网关两项 / RustFS 密钥
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

访问 `http://<server>/`，使用账号密码登录。

> Worker 挂载了 `/var/run/docker.sock`（strix 沙箱依赖 Docker）。
> 共享主机部署时务必阅读可行性文档 5.2 节的网络隔离要求（任务级 DOCKER-USER 白名单），
> 验证脚本见 `phase0/isolation/`。

## 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `SECRET_KEY` | 登录令牌签名密钥（必填） | - |
| `ADMIN_INITIAL_PASSWORD` | 初始超管密码（用户库为空时生效） | `strix-admin-123` |
| `TOKEN_EXPIRY_HOURS` | 登录有效期（小时） | 24 |
| `DATABASE_URL` / `REDIS_URL` | 依赖连接 | 本机默认 |
| `LLM_API_BASE` / `LLM_API_KEY` / `STRIX_LLM` | 公司 LLM 网关（OpenAI 兼容）与默认模型 | `free` |
| `FREE_MODELS` | 提交任务时可选的免费模型列表（逗号分隔） | `free` |
| `WORKSPACE_ROOT` | 任务源码/工作区/上传目录 | `./workspaces` |
| `STRIX_BIN` | strix 可执行文件路径（dev 为 `backend/.venv` 内路径） | `strix` |
| `S3_ENABLED` / `S3_ENDPOINT` / `S3_*` | RustFS 对象存储；false 时产物留本地磁盘 | false |
| `TARGET_ALLOWLIST` | 黑盒目标允许清单（域名后缀/CIDR，逗号分隔） | 空=仅内网/回环 |
| `MAX_UPLOAD_MB` | zip 上限 | 500 |
| `TIMEOUT_QUICK/STANDARD/DEEP` | 任务超时秒数（Phase 0 实测校准） | 7200/14400/28800 |
| `MAX_SCAN_ATTEMPTS` | LLM 连接失败重试次数 | 3 |

## 使用流程

1. 超管在「用户管理」创建账号 → 使用者登录。
2. 「项目」页新建项目：Git 仓库（可配 token / SSH 私钥）或 zip 上传。
3. 项目内「发起扫描任务」：Git 项目选分支，zip 项目选历史上传/新上传；可选档位
   （quick/standard/deep 全开放）、模型、黑盒地址、中文报告。
4. 任务列表实时刷新状态：`pending → fetching → scanning → parsing → (translating) → done/failed`。
5. 报告页查看：严重级别概览 + 漏洞明细（可展开 PoC）+ 官方执行摘要；可导出 PDF、下载产物 zip
   （漏洞 JSON/CSV/MD、SARIF、执行日志）。

## 安全护栏

- 账号体系：口令 PBKDF2 存储，登录令牌 HMAC 签名 + 有效期；账号仅超管可创建
- 数据隔离：普通用户仅见自己的项目与任务；超管可见全部
- 全量审计日志（`audit_entries` 表：用户/IP/动作/详情）
- 黑盒目标允许清单（默认仅内网/回环地址，公网目标直接拒绝）
- Git 凭据仅存后端、接口永不回显、日志脱敏

## 已知边界

- 扫描耗时以实测为准：quick 双目标约 1 小时+（任务超时已按此校准）
- free 模型池有间歇故障，worker 自动重试（最多 3 次）
- PDF 由 run 工作区实时生成并缓存；工作区被清理后可改用产物 zip 归档
- 漏洞结果为 AI 辅助测试产出，报告页已标注建议人工复核
