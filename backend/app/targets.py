"""黑盒测试地址允许清单校验（护栏③）。

黑盒扫描会向该地址发送真实攻击流量，必须限制在内网测试环境。
TARGET_ALLOWLIST 为空时采用安全默认：仅放行内网/回环 IP 与内部样式主机名。
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from urllib.parse import urlparse

import httpx

from .config import get_settings

_DEFAULT_SUFFIXES = (".internal", ".local", ".test", ".lan", ".lab")

# 单任务/单项目的黑盒测试地址上限与作用说明长度上限
MAX_TARGETS = 10
MAX_NOTE_LEN = 200


# ---- 多目标列表（JSON [{"url", "note"}]）解析与清洗 ----


def parse_targets(raw) -> list[dict]:
    """解析地址列表 JSON；容忍 JSON 字符串 / 已解出的 list / 其他脏数据，出错返回 []。"""
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    items: list[dict] = []
    for it in raw:
        if isinstance(it, str):
            items.append({"url": it, "note": ""})
        elif isinstance(it, dict):
            items.append({"url": str(it.get("url") or ""), "note": str(it.get("note") or "")})
    return items


def dump_targets(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


def normalize_targets(items: list[dict]) -> tuple[list[dict], str]:
    """清洗用户提交的目标：去空白、丢弃空地址行、按 URL 去重、限制数量与说明长度。

    返回 (清洗后的列表, 错误信息)；出错时列表为空、错误信息可直接作为 400 响应。
    """
    cleaned: list[dict] = []
    seen: set[str] = set()
    for it in items:
        url = str(it.get("url") or "").strip()
        note = str(it.get("note") or "").strip()[:MAX_NOTE_LEN]
        if not url:
            continue  # 前端允许保留未填完的空行，提交时静默丢弃
        if url in seen:
            continue
        seen.add(url)
        cleaned.append({"url": url, "note": note})
    if len(cleaned) > MAX_TARGETS:
        return [], f"黑盒测试地址最多 {MAX_TARGETS} 个"
    return cleaned, ""


def effective_targets(test_targets: str, legacy_url: str) -> list[dict]:
    """读取侧合并：test_targets 列为非空字符串时即权威（"[]" 表示显式无目标），
    否则回退旧版单地址列（存量任务/项目兼容）。"""
    if test_targets and test_targets.strip():
        return [t for t in parse_targets(test_targets) if t.get("url")]
    url = (legacy_url or "").strip()
    return [{"url": url, "note": ""}] if url else []


def _allowlist_entries() -> tuple[list[str], list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    suffixes: list[str] = []
    networks = []
    for raw in (get_settings().target_allowlist or "").split(","):
        item = raw.strip().lower()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            suffixes.append(item if item.startswith(".") else f".{item}")
    return suffixes, networks


def check_target_allowed(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False, "URL 无法解析"
    if parsed.scheme not in ("http", "https"):
        return False, "仅接受 http/https 地址"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "缺少主机名"

    suffixes, networks = _allowlist_entries()

    # 1) 显式允许清单命中即放行
    for suffix in suffixes:
        if host.endswith(suffix):
            return True, ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        for net in networks:
            if ip in net:
                return True, ""

    # 2) 安全默认：回环/内网/链路本地 IP；内部样式主机名
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local):
        return True, ""
    if not ip:
        if "." not in host or host == "host.docker.internal" or host.endswith(_DEFAULT_SUFFIXES):
            return True, ""

    return (
        False,
        f"目标 {host} 不在允许清单（内网测试地址，或通过 TARGET_ALLOWLIST 添加域名后缀/CIDR）",
    )


def resolve_note(host: str) -> str:
    try:
        socket.gethostbyname(host)
        return ""
    except OSError:
        return f"（警告：主机 {host} 当前无法解析）"


def probe_target(url: str, timeout: float = 5.0) -> dict:
    """对已放行的黑盒地址发起一次轻量 HTTP GET 探测，返回可达性结果。

    内网测试环境常配自签名证书，这里与扫描引擎保持一致不校验 TLS。
    任何 HTTP 状态码（含 4xx/5xx）都算可达，只有连接/超时类错误算不可达。
    """
    started = time.monotonic()
    result: dict = {"reachable": False, "status_code": None, "latency_ms": None, "detail": ""}
    try:
        resp = httpx.get(url.strip(), timeout=timeout, follow_redirects=True, verify=False)
    except httpx.ConnectError:
        result["detail"] = "连接失败（主机无法解析、不可达或端口未开放）"
    except httpx.ConnectTimeout:
        result["detail"] = "连接超时"
    except httpx.ReadTimeout:
        result["detail"] = "响应超时（连接已建立但服务未在时限内返回）"
    except httpx.HTTPError as e:
        result["detail"] = f"{type(e).__name__}: {e}"
    else:
        result["reachable"] = True
        result["status_code"] = resp.status_code
        result["detail"] = "目标可达"
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    return result
