"""平台配置：全部来自环境变量（.env），与部署文档一致。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 基础设施
    database_url: str = "postgresql+psycopg2://strix:strix@localhost:5432/strix"
    redis_url: str = "redis://localhost:6379/0"

    # 最低成本护栏①：共享访问令牌（全平台一个，够拦无关访问）
    api_token: str = "change-me-in-env"

    # 工作目录（任务源码、扫描工作区、产物归档）
    workspace_root: str = "./workspaces"

    # strix 引擎
    strix_bin: str = "strix"
    strix_version: str = "1.5.3"

    # LLM（公司统一网关），worker 注入给 strix 子进程
    llm_api_base: str = ""
    llm_api_key: str = ""
    strix_llm: str = "free"

    # 可选的免费模型列表（逗号分隔，提交任务时下拉选择；STRIX_LLM 为未指定时的默认）
    free_models: str = "free"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
