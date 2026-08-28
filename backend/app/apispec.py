"""API 规范目标：OpenAPI/Swagger/Postman/Apifox 导出文件的校验，及 Apifox 在线拉取。

strix 原生支持把 API 规范文件作为 -t 目标（帮助文本：OpenAPI/Swagger .json/.yaml、
Postman collection 导出）。平台侧负责：文件格式校验与保存，以及用 Apifox 开放 API
把项目在线导出为 OpenAPI（需 Apifox 项目 ID + 访问令牌，令牌只在导出瞬间使用、不落库）。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import httpx

MAX_SPEC_MB = 20
SPEC_EXTS = (".json", ".yaml", ".yml")

# 简单防注入：技能/规范文件名只允许安全字符
_NAME_RE = re.compile(r"^[\w.\- ()\[\]]{1,120}$")


class ApiSpecError(Exception):
    pass


def safe_spec_name(filename: str) -> str:
    """清洗上传文件名：unicode 规范化、去路径成分、白名单字符；非法返回异常。"""
    name = unicodedata.normalize("NFKC", filename or "").strip()
    name = Path(name).name  # 去掉任何路径成分
    if not name or not _NAME_RE.match(name):
        raise ApiSpecError(f"API 规范文件名不合法: {filename!r}")
    if Path(name).suffix.lower() not in SPEC_EXTS:
        raise ApiSpecError("API 规范仅支持 .json / .yaml / .yml 文件（OpenAPI/Swagger、Postman、Apifox 导出）")
    return name


def validate_spec_content(data: bytes, filename: str) -> None:
    """内容粗校验：能解析为 JSON 或 YAML，且长成机器可读接口描述（而非任意文档）。"""
    text = data.decode("utf-8", errors="replace")
    parsed: object = None
    if filename.lower().endswith(".json"):
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise ApiSpecError(f"{filename} 不是合法 JSON：{exc}") from exc
    else:
        try:
            import yaml

            parsed = yaml.safe_load(text)
        except ImportError as exc:
            raise ApiSpecError("服务器未安装 PyYAML，无法解析 YAML 规范") from exc
        except Exception as exc:  # noqa: BLE001 —— yaml 自身异常类型不定
            raise ApiSpecError(f"{filename} 不是合法 YAML：{exc}") from exc
    if not isinstance(parsed, (dict, list)):
        raise ApiSpecError(f"{filename} 内容不是结构化接口描述")
    if isinstance(parsed, dict):
        keys = {str(k).lower() for k in parsed}
        known = {"openapi", "swagger", "paths", "apifoxproject", "info", "item", "variables"}
        if not keys & known:
            raise ApiSpecError(
                f"{filename} 未识别为 OpenAPI/Swagger、Postman 或 Apifox 导出（缺少 openapi/swagger/paths/item 等字段）"
            )


def fetch_apifox_openapi(
    project_id: str,
    token: str,
    api_base: str = "https://api.apifox.com",
    timeout: float = 60.0,
) -> tuple[bytes, str]:
    """经 Apifox 开放 API 把项目导出为 OpenAPI JSON，返回 (内容, 文件名)。

    文档：GET /v1/projects/{id}/export-openapi?locale=zh-CN，
    鉴权 Authorization: Bearer <访问令牌>。令牌只在本次请求使用，不落库不回显。
    """
    pid = project_id.strip()
    if not re.fullmatch(r"\d{1,20}", pid):
        raise ApiSpecError("Apifox 项目 ID 应为数字（项目设置 → 基本信息）")
    if not token.strip():
        raise ApiSpecError("缺少 Apifox 访问令牌（Apifox → 头像 → 「账号设置 → API 访问令牌」）")
    base = (api_base or "https://api.apifox.com").strip().rstrip("/")
    url = f"{base}/v1/projects/{pid}/export-openapi"
    try:
        resp = httpx.get(
            url,
            params={"locale": "zh-CN"},
            headers={"Authorization": f"Bearer {token.strip()}", "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.ConnectError as exc:
        raise ApiSpecError(f"无法连接 Apifox 开放 API（{base}）：{exc}") from exc
    except httpx.HTTPError as exc:
        raise ApiSpecError(f"Apifox 导出请求失败：{type(exc).__name__}: {exc}") from exc
    if resp.status_code == 401:
        raise ApiSpecError("Apifox 令牌无效或已过期（401）")
    if resp.status_code == 404:
        raise ApiSpecError("Apifox 项目不存在或令牌无权访问（404）")
    if resp.status_code != 200:
        raise ApiSpecError(f"Apifox 导出失败（HTTP {resp.status_code}）：{resp.text[:200]}")
    data = resp.content
    if not data:
        raise ApiSpecError("Apifox 返回了空内容")
    validate_spec_content(data, "apifox-openapi.json")
    return data, f"apifox-{pid}-openapi.json"
