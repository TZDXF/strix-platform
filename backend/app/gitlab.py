"""GitLab REST API v4 客户端：验证令牌 / 拉取当前用户可见的项目列表。

用于「个人 Git 配置」：用户保存 GitLab 地址 + Access Token，
创建项目时通过它列出可选仓库（区分组织/命名空间）。
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

PER_PAGE = 100
MAX_PAGES = 20  # 单次最多拉 20 页（2000 个仓库），内网实例足够


class GitLabError(Exception):
    pass


def normalize_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        raise GitLabError("Git 服务地址不能为空")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise GitLabError("Git 服务地址需以 http(s):// 开头，如 http://192.168.1.3:12580")
    return url


def _headers(token: str) -> dict[str, str]:
    return {"PRIVATE-TOKEN": token}


def _explain_status(status_code: int, body: str) -> str:
    if status_code == 401:
        return "访问令牌无效或已过期"
    if status_code == 403:
        if "insufficient_scope" in body:
            return "令牌权限不足：需要 read_api 或 api scope（请在 GitLab 重新生成令牌时勾选）"
        return "令牌无权访问该资源"
    return f"GitLab 返回 {status_code}: {body[:300]}"


def verify_token(base_url: str, token: str) -> dict:
    """验证令牌并返回当前用户信息（username 等）；失败抛 GitLabError。"""
    api = normalize_base_url(base_url) + "/api/v4"
    try:
        resp = httpx.get(f"{api}/user", headers=_headers(token), timeout=15)
    except httpx.HTTPError as exc:
        raise GitLabError(f"无法连接 Git 服务：{exc}") from exc
    if resp.status_code != 200:
        raise GitLabError(_explain_status(resp.status_code, resp.text))
    info = resp.json()
    if not isinstance(info, dict) or not info.get("username"):
        raise GitLabError("GitLab 响应异常，无法识别用户信息")
    return info


def list_projects(base_url: str, token: str) -> list[dict]:
    """拉取当前用户可见的仓库列表（simple 字段），按最近活跃排序，带命名空间（组织）信息。"""
    api = normalize_base_url(base_url) + "/api/v4"
    items: list[dict] = []
    page = 1
    with httpx.Client(timeout=30, headers=_headers(token)) as client:
        while page <= MAX_PAGES:
            try:
                resp = client.get(
                    f"{api}/projects",
                    params={
                        "membership": "true",  # 只看我是成员的仓库（含所属组织）
                        "simple": "true",
                        "order_by": "last_activity_at",
                        "sort": "desc",
                        "per_page": PER_PAGE,
                        "page": page,
                    },
                )
            except httpx.HTTPError as exc:
                raise GitLabError(f"无法连接 Git 服务：{exc}") from exc
            if resp.status_code != 200:
                raise GitLabError(_explain_status(resp.status_code, resp.text))
            batch = resp.json()
            if not isinstance(batch, list):
                raise GitLabError("GitLab 响应异常，无法解析仓库列表")
            items.extend(_simplify(p) for p in batch)
            next_page = resp.headers.get("x-next-page", "")
            if not next_page or not batch:
                break
            page = int(next_page)
    return items


def _simplify(p: dict) -> dict:
    ns = p.get("namespace") or {}
    return {
        "id": p.get("id"),
        "name": p.get("name") or "",
        "path_with_namespace": p.get("path_with_namespace") or "",
        "web_url": p.get("web_url") or "",
        "http_url_to_repo": p.get("http_url_to_repo") or "",
        "default_branch": p.get("default_branch") or "",
        "visibility": p.get("visibility") or "",
        "last_activity_at": p.get("last_activity_at") or "",
        "namespace": {
            "kind": ns.get("kind") or "",  # group（组织）| user（个人）
            "name": ns.get("name") or "",
            "full_path": ns.get("full_path") or "",
        },
    }


def same_host(base_url: str, repo_url: str) -> bool:
    """仓库地址是否属于该 Git 服务（按 host[:port] 比较），防止令牌被配到别的服务地址上。"""
    try:
        a = urlparse(normalize_base_url(base_url))
        b = urlparse(repo_url.strip())
    except GitLabError:
        return False
    if not b.hostname:
        return False
    port_a = a.port or (443 if a.scheme == "https" else 80)
    port_b = b.port or (443 if b.scheme == "https" else 80)
    return a.hostname.lower() == b.hostname.lower() and port_a == port_b
