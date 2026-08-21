"""平台配置：全部来自环境变量（.env），与部署文档一致。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 依次查找：仓库根 .env（docker compose 插值用的同一份），backend/.env 可覆盖
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # 基础设施
    database_url: str = "postgresql+psycopg2://strix:strix@localhost:5432/strix"
    redis_url: str = "redis://localhost:6379/0"

    # 登录会话签名密钥（必改；用于签发/校验用户访问令牌）
    secret_key: str = "change-me-secret-key"
    # 登录令牌有效期（小时）
    token_expiry_hours: int = 24
    # 首次启动时自动创建的超管账号密码（用户库为空时生效）
    admin_initial_password: str = "admin"

    # 工作目录（任务源码、扫描工作区、产物归档）
    workspace_root: str = "./docker/data/workspaces"

    # strix 引擎版本（仅用于健康检查回显与任务日志，容器内 strix 二进制已固定在 venv PATH）
    strix_version: str = "1.5.3"

    # LLM（公司统一网关），worker 注入给 strix 子进程；密钥由用户个人配置，平台不再持有统一密钥
    llm_api_base: str = ""
    strix_llm: str = "free"  # 平台模型表为空时的最终回退

    # 对象存储（RustFS，S3 API）；未启用时产物留在本地磁盘
    s3_enabled: bool = False
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "strix-platform"

    # 上传限制
    max_upload_mb: int = 500

    # 最低成本护栏②：目标允许清单（逗号分隔：域名后缀 或 CIDR）
    # 为空时默认只放行：内网/回环 IP、无点主机名、*.internal/.local/.test/.lan
    target_allowlist: str = ""

    # 任务超时（秒）：Phase 0 实测 quick 双目标 77 分钟，故 quick=2h 起步
    timeout_quick: int = 7200
    timeout_standard: int = 14400
    timeout_deep: int = 28800

    # LLM 连接失败自动重试次数（决策 #7：free 池有间歇故障）
    max_scan_attempts: int = 3

    # strix 全局附加参数（运维级，所有任务生效）
    # 单任务预算上限（美元）；>0 时传 --max-budget，strix 超预算即中止
    strix_max_budget: float = 0.0
    # 原样追加的额外 CLI 参数（shlex 切分，如 "--scope-mode diff --config /path.json"）
    strix_extra_args: str = ""

    # 联网搜索（可选）：内网 MCP 搜索端点（Streamable HTTP，JSON-RPC 直调）。
    # 配置后任务提交时可勾选「联网搜索」，引擎指令中注入调用指南，智能体经沙箱
    # shell curl 该端点查询公开漏洞/利用资料；留空 = 功能关闭（勾选时提交报 400）
    web_search_mcp_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
