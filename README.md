# Strix 内部安全测试平台

团队内部使用的安全测试网站：创建**项目**（Git 仓库或 zip 压缩包），
在项目内发起 [Strix](https://github.com/usestrix/strix) AI 渗透测试（白盒源码 + 黑盒 URL），
生成漏洞报告（严重级别 / PoC / 修复建议，支持中文），可导出 Markdown 格式报告，产物可下载归档。

> 架构决策与可行性分析见 [docs/feasibility.md](docs/feasibility.md)。
> Phase 0 引擎验证数据见 [phase0/EVALUATION.md](phase0/EVALUATION.md)
> （Juice Shop 靶场：10 条发现 / 0 误报 / quick 模式 77 分钟 / 8200 万 token）。

## 组成

```
frontend/   Vue 3 + Vite（登录 / 项目 / 任务 / 用户管理 / 报告页）
backend/    FastAPI + Celery + SQLAlchemy
              ├─ app/main.py      API（用户认证 + 项目 + 任务 + 报告/产物）
              ├─ app/auth.py      账号体系（PBKDF2 口令 + HMAC 签名令牌）
              ├─ app/tasks.py     流水线：fetch → scan → parse → archive
              ├─ app/translate.py 中文翻译（LLM 网关，任务页按钮手动触发）
              ├─ app/mailer.py    邮件提醒（SMTP 配置存库 + 任务完成/失败通知）
              ├─ app/replay.py    执行过程回放（解析 strix.log 生成事件时间线）
              └─ app/runner.py    strix 子进程执行器（失败自动重试）
docker-compose.yml  postgres + redis + rustfs + api + worker + frontend
```

## 后台管理功能

1. **用户管理**：超管（admin）/ 普通用户（user）两种角色，账号只能由超管创建（用户管理页），
   支持重置密码、启用/停用、升降角色；系统保留至少一个可用超管。
   首次启动自动创建 `admin`，密码取 `ADMIN_INITIAL_PASSWORD`（默认 `admin`，请尽快修改）。
2. **任务列表**：普通用户只看自己的任务，超管查看全部（含提交人列）。
3. **项目化任务发布**：先创建项目（Git 地址可配置 Personal Access Token 凭据；或 zip 上传型项目），
   在项目内发起扫描：Git 项目拉取远端**分支列表**供选择（默认分支排首位）；
   zip 项目可选**历史上传**复用或**新上传**（新上传自动存入历史）。
4. **报告导出**：报告页一键导出 Markdown 格式完整报告（与报告页同源数据，结果本地缓存）。
5. **中文报告**：发起任务即通过 `--instruction` 提示词要求 strix 用简体中文撰写；
   流程内不做自动翻译——如报告仍为英文，可在任务详情页点「翻译成中文」，
   经 LLM 网关把标题/描述/修复建议翻译入库（`*_zh` 字段，前端可切换）。
6. **报告页**：对齐 `strix view` 官方报告结构——严重级别概览网格（严重/高危/中危/低危/提示）、
   官方执行摘要报告（`penetration_test_report.md`，Markdown 渲染）、逐条漏洞明细
   （彩色级别条 / CVSS / CWE / PoC 说明与脚本）。
7. **执行过程回放**：任务详情「智能体」页可回放具体执行过程——解析引擎日志 `strix.log`
   生成事件时间线（引擎启停 / 智能体派生与收工 / 工具调用 / 漏洞落盘 / 告警错误），
   播放器支持倍速播放、时间轴拖动定位、执行密度图、各智能体并发泳道与事件流筛选；
   工作区日志被清理后自动回退解析产物 zip 中的存档（解析结果缓存入库）。
8. **系统设置页（仅超管）**：集中管理平台级配置——平台模型管理（网关密钥查询可用模型后加入平台、
   指定默认模型）与邮件提醒（SMTP 服务器/端口/加密方式/发件人，可真实发送测试邮件验证连通）。
9. **邮件提醒**：超管在「系统设置」配置 SMTP 后，扫描任务完成/失败时平台自动向创建者
   在「个人设置」填写的通知邮箱发送结果摘要邮件（严重度分布、耗时、失败原因、任务链接）。

## 本地开发

```bash
# 依赖容器
docker run -d --name strix-pg -e POSTGRES_USER=strix -e POSTGRES_PASSWORD=strix -e POSTGRES_DB=strix -p 5432:5432 postgres:16-alpine
docker run -d --name strix-redis -p 6379:6379 redis:7-alpine

# 环境变量统一放仓库根目录 .env（docker compose 与后端共用；backend/.env 可选覆盖）
cp phase0/.env .env      # 或按下表配置

# 后端（uv 管理，Python 3.12+，含 strix-agent==1.5.3）
cd backend
uv sync
uv run uvicorn app.main:app --port 8000
uv run celery -A app.celery_app.celery_app worker --pool=solo --beat --loglevel=info   # Windows 必须 --pool=solo；--beat 驱动定时扫描

# 前端（API_PROXY 可覆盖代理目标，默认 http://localhost:8000）
cd frontend && npm install && npm run dev   # http://localhost:5173
```

首次启动用 `admin` / `ADMIN_INITIAL_PASSWORD`（默认 `admin`）登录，
在「用户管理」给同事开号后请立即修改默认密码。

## 服务器部署（Docker Compose，Linux + Docker）

### 前置条件

- Linux 服务器 + Docker（含 compose 插件）；宿主机 Docker 即 strix 沙箱的运行载体
- 可访问公司 LLM 网关（OpenAI 兼容接口）
- 可访问外网镜像源（首次构建需拉取基础镜像；首次扫描会自动拉取 strix 沙箱镜像 `ghcr.io/usestrix/strix-sandbox`，可预拉取）

### 首次部署

1. **配置环境变量**：复制模板并填写必填项（`SECRET_KEY`、`LLM_API_BASE`，建议同时改掉
   `ADMIN_INITIAL_PASSWORD` / `RUSTFS_*` / `POSTGRES_PASSWORD` 默认值）：

   ```bash
   cp .env.deploy.example .env
   vi .env
   ```

2. **构建并启动全部服务**（postgres / redis / rustfs / api / worker / frontend）：

   ```bash
   docker compose up -d --build
   ```

   RustFS 归档桶由 api 启动时自动创建（`ensure_bucket`，等待 RustFS 就绪并幂等），
   上传时若桶仍缺失还会现场补建一次，无需手动操作。

3. **验证部署**：

   ```bash
   # worker 内 docker CLI 可达宿主机 daemon（strix 沙箱依赖，失败则扫描秒退）
   docker compose exec worker docker version --format 'client {{.Client.Version}} / server {{.Server.Version}}'
   docker compose ps        # 六个服务均 Up / healthy
   ```

4. 访问 `http://<server>/`，用 `admin` / `ADMIN_INITIAL_PASSWORD` 登录，
   在「用户管理」给同事开号后**立即修改默认密码**。

### 升级

```bash
git pull
docker compose up -d --build    # 仅重建有变化的镜像，数据卷（pg/工作区/对象存储）保留
```

> 直接 `docker compose` 即可，任何宿主平台均无需路径探测脚本。工作区由固定名具名卷
> `strix-platform_workspaces` 承载，容器内挂载点就是该卷在 daemon 侧的真实存储路径
> （`/var/lib/docker/volumes/strix-platform_workspaces/_data`）——strix 沙箱经宿主
> daemon bind mount 源码目录时按 daemon 所在文件系统解析路径且内部会做 realpath，
> 该常量路径在 Docker Desktop 与原生 Linux 上都真实存在，worker/api/沙箱三方天然对齐。
> 仓库根的 `./deploy.sh` 现仅是 `docker compose` 的透传别名，为老习惯保留。

> 历史数据迁移（v0.9 之前用宿主目录 bind 挂载的部署）：旧工作区在
> `./docker/data/workspaces`，一次性拷入新卷后即可继续查看历史任务：
>
> ```bash
> docker run --rm -v strix-platform_workspaces:/dst \
>   -v "$(pwd -W 2>/dev/null || pwd)/docker/data/workspaces:/src:ro" \
>   python:3.12-slim-bookworm cp -a /src/. /dst/
> ```

### 常见问题

| 现象 | 原因与处理 |
|------|-----------|
| 任务日志含 `DOCKER NOT INSTALLED`，秒退退出码 1 | worker 容器内无 `docker` CLI 或未挂载 docker.sock。镜像已内置 `docker-cli`（见 `backend/Dockerfile`），若仍出现请确认 compose 中 worker 挂载了 `/var/run/docker.sock`，并重新 `docker compose build worker && docker compose up -d worker` |
| 归档阶段 `NoSuchBucket` | 正常部署不会出现（api 启动与上传时均自动建桶）；若出现多为 RustFS 密钥与 `.env` 不一致，或 api 启动日志中有 `[startup] S3 归档桶不可用`，检查 `RUSTFS_*` 配置后重启 api |
| 首个任务卡在 scanning 很久 | 首次扫描需拉取沙箱镜像，可在宿主机预拉取：`docker pull ghcr.io/usestrix/strix-sandbox:<tag>` |
| 任务日志含 `invalid mount config ... bind source path does not exist`，秒退退出码 1 | 沙箱容器的 bind mount 由宿主 daemon 解析路径且 strix 内部会做 realpath，工作区在容器内挂载点与 daemon 可见路径不一致。默认具名卷部署不会出现；若自行覆盖过 `WORKSPACE_ROOT` 或改回宿主目录 bind 挂载，请恢复 compose 默认（容器 api/worker 启动日志中 entrypoint 诊断会打印对齐指引） |
| `SECRET_KEY` / `LLM_API_BASE` 启动报错 | `.env` 未填写必填项，对照 `.env.deploy.example` |

> Worker 挂载了 `/var/run/docker.sock`（strix 沙箱通过宿主机 Docker 启动分析容器，镜像内已安装 docker-cli）。
> 共享主机部署时务必阅读可行性文档 5.2 节的网络隔离要求（任务级 DOCKER-USER 白名单），
> 验证脚本见 `phase0/isolation/`。

## 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `SECRET_KEY` | 登录令牌签名密钥（必填） | - |
| `ADMIN_INITIAL_PASSWORD` | 初始超管密码（用户库为空时生效） | `admin` |
| `TOKEN_EXPIRY_HOURS` | 登录有效期（小时） | 24 |
| `DATABASE_URL` / `REDIS_URL` | 依赖连接 | 本机默认 |
| `LLM_API_BASE` / `STRIX_LLM` | 公司 LLM 网关（OpenAI 兼容）与默认模型；API 密钥由用户在「设置」页个人配置，平台无统一密钥 | `free` |
| `WORKSPACE_ROOT` | 任务源码/工作区/上传目录；compose 默认指向工作区具名卷的 daemon 侧存储路径，本机直跑后端时才是相对路径 | `/var/lib/docker/volumes/strix-platform_workspaces/_data`（容器）· `./docker/data/workspaces`（本地直跑） |
| `S3_ENABLED` / `S3_ENDPOINT` / `S3_*` | RustFS 对象存储；false 时产物留本地磁盘 | false |
| `TARGET_ALLOWLIST` | 黑盒目标允许清单（域名后缀/CIDR，逗号分隔） | 空=仅内网/回环 |
| `MAX_UPLOAD_MB` | zip 上限 | 500 |
| `TIMEOUT_QUICK/STANDARD/DEEP` | 任务超时秒数（Phase 0 实测校准）；发起任务时可勾选「不限时运行」按任务关闭超时终止 | 7200/14400/28800 |
| `MAX_SCAN_ATTEMPTS` | LLM 连接失败重试次数 | 3 |
| `STRIX_MAX_BUDGET` | 单任务 LLM 预算上限（美元）；>0 时传 `--max-budget` | 0=不限 |
| `STRIX_EXTRA_ARGS` | 原样追加的 strix CLI 参数（shlex 切分，如 `--scope-mode full`） | 空 |

## 使用流程

1. 超管在「用户管理」创建账号 → 使用者登录。
2. 「项目」页新建项目：Git 仓库（可配 Personal Access Token）或 zip 上传。
3. 项目内「发起扫描任务」：Git 项目选分支，zip 项目选历史上传/新上传；可选档位
   （quick/standard/deep 全开放）、模型、黑盒地址、中文报告、测试指令
   （聚焦的漏洞类型 / 测试要求 / 测试账号，透传 `--instruction`，留空则全量扫描）。
   多仓库 / 大型项目可勾选「不限时运行」，按任务关闭档位默认的超时终止。
4. 任务列表实时刷新状态：`pending → fetching → scanning → parsing → (translating) → done/failed`。
5. 报告页查看：严重级别概览 + 漏洞明细（可展开 PoC）+ 官方执行摘要；可导出 Markdown 报告、下载产物 zip
   （漏洞 JSON/CSV/MD、SARIF、执行日志）。「智能体」页可回放具体执行过程
   （倍速播放 / 拖动定位 / 智能体并发泳道 / 事件流筛选）。
6. （可选）超管在「系统设置」配置邮件提醒（SMTP），用户在「个人设置 → 通知邮箱」填写收件地址；
   任务完成或失败时会自动收到提醒邮件。

## 安全护栏

- 账号体系：口令 PBKDF2 存储，登录令牌 HMAC 签名 + 有效期；账号仅超管可创建
- 数据隔离：普通用户仅见自己的项目与任务；超管可见全部
- 全量审计日志（`audit_entries` 表：用户/IP/动作/详情）
- 黑盒目标允许清单（默认仅内网/回环地址，公网目标直接拒绝）
- Git 凭据仅存后端、接口永不回显、日志脱敏

## 已知边界

- 扫描耗时以实测为准：quick 双目标约 1 小时+（任务超时已按此校准）
- free 模型池有间歇故障，worker 自动重试（最多 3 次）
- Markdown 报告由 run 工作区实时生成并缓存；工作区被清理后可改用产物 zip 归档
- 漏洞结果为 AI 辅助测试产出，报告页已标注建议人工复核
