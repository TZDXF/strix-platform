"""多平台 Git 服务客户端（GitLab / GitHub / Gitee）：验证令牌 / 拉取当前用户可见的仓库列表。

用于「个人 Git 配置」：用户保存 Git 服务地址 + 平台 + Access Token，
创建项目时通过它列出可选仓库（区分组织/命名空间）。
各平台仓库列表统一归一化为 GitLab 形状（id / name / path_with_namespace /
web_url / http_url_to_repo / default_branch / visibility / last_activity_at /
namespace{kind,name,full_path}），前端按组织筛选等逻辑无需感知平台差异。

克隆凭据：三平台 https 克隆均支持 `oauth2:<token>@host` 形式
（GitHub 任意用户名 + PAT 皆可，Gitee 官方要求用户名固定为 oauth2），
sources._token_url 的注入方式天然兼容，无需按平台区分。
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

PER_PAGE = 100
MAX_PAGES = 20  # 单次最多拉 20 页（2000 个仓库），内网实例足够

PLATFORMS = ("gitlab", "github", "gitee")

_PLATFORM_NAMES = {"gitlab": "GitLab", "github": "GitHub", "gitee": "Gitee"}


class GitProviderError(Exception):
    pass


def normalize_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        raise GitProviderError("Git 服务地址不能为空")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise GitProviderError("Git 服务地址需以 http(s):// 开头，如 http://192.168.1.3:12580")
    return url


def resolve_platform(platform: str, base_url: str) -> str:
    """确定平台：已知公有云站点按域名强制校正（防止选错平台导致 API 路径不通），
    其余按用户选择，未知值回退 gitlab（自建 GitLab / Gitee Enterprise 等按选值走）。"""
    host = (urlparse(base_url).hostname or "").lower()
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    if host == "gitee.com" or host.endswith(".gitee.com"):
        return "gitee"
    return platform if platform in PLATFORMS else "gitlab"


def _explain_status(platform: str, status_code: int, body: str) -> str:
    if status_code == 401:
        return "访问令牌无效或已过期"
    if status_code == 403:
        if platform == "gitlab":
            if "insufficient_scope" in body:
                return "令牌权限不足：需要 read_api 或 api scope（请在 GitLab 重新生成令牌时勾选）"
            return "令牌无权访问该资源"
        if platform == "github":
            return "令牌权限不足：fine-grained 令牌需授予对应仓库的 Contents 读取权限，classic 令牌需 repo 权限"
        return "令牌权限不足：请在 Gitee 生成私人令牌时勾选 projects 权限"
    name = _PLATFORM_NAMES.get(platform, "Git 服务")
    return f"{name} 返回 {status_code}: {body[:300]}"


def same_host(base_url: str, repo_url: str) -> bool:
    """仓库地址是否属于该 Git 服务（按 host[:port] 比较），防止令牌被配到别的服务地址上。"""
    try:
        a = urlparse(normalize_base_url(base_url))
        b = urlparse(repo_url.strip())
    except GitProviderError:
        return False
    if not b.hostname:
        return False
    port_a = a.port or (443 if a.scheme == "https" else 80)
    port_b = b.port or (443 if b.scheme == "https" else 80)
    return a.hostname.lower() == b.hostname.lower() and port_a == port_b


# ===================== GitLab（REST API v4） =====================


def _gitlab_headers(token: str) -> dict[str, str]:
    return {"PRIVATE-TOKEN": token}


def _gitlab_verify(api_base: str, token: str) -> dict:
    try:
        resp = httpx.get(f"{api_base}/user", headers=_gitlab_headers(token), timeout=15)
    except httpx.HTTPError as exc:
        raise GitProviderError(f"无法连接 Git 服务：{exc}") from exc
    if resp.status_code != 200:
        raise GitProviderError(_explain_status("gitlab", resp.status_code, resp.text))
    info = resp.json()
    if not isinstance(info, dict) or not info.get("username"):
        raise GitProviderError("GitLab 响应异常，无法识别用户信息")
    return info


def _gitlab_list(api_base: str, token: str) -> list[dict]:
    items: list[dict] = []
    page = 1
    with httpx.Client(timeout=30, headers=_gitlab_headers(token)) as client:
        while page <= MAX_PAGES:
            try:
                resp = client.get(
                    f"{api_base}/projects",
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
                raise GitProviderError(f"无法连接 Git 服务：{exc}") from exc
            if resp.status_code != 200:
                raise GitProviderError(_explain_status("gitlab", resp.status_code, resp.text))
            batch = resp.json()
            if not isinstance(batch, list):
                raise GitProviderError("GitLab 响应异常，无法解析仓库列表")
            items.extend(_simplify_gitlab(p) for p in batch)
            next_page = resp.headers.get("x-next-page", "")
            if not next_page or not batch:
                break
            page = int(next_page)
    return items


def _simplify_gitlab(p: dict) -> dict:
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


# ===================== GitHub（github.com / GitHub Enterprise Server） =====================


def _github_api_base(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if host == "github.com" or host.endswith(".github.com"):
        return "https://api.github.com"
    return base_url.rstrip("/") + "/api/v3"  # GitHub Enterprise Server


def _github_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _github_verify(api_base: str, token: str) -> dict:
    try:
        resp = httpx.get(f"{api_base}/user", headers=_github_headers(token), timeout=15)
    except httpx.HTTPError as exc:
        raise GitProviderError(f"无法连接 Git 服务：{exc}") from exc
    if resp.status_code != 200:
        raise GitProviderError(_explain_status("github", resp.status_code, resp.text))
    info = resp.json()
    if not isinstance(info, dict) or not info.get("login"):
        raise GitProviderError("GitHub 响应异常，无法识别用户信息")
    return {"username": info["login"], "name": info.get("name") or ""}


def _github_list(api_base: str, token: str) -> list[dict]:
    items: list[dict] = []
    page = 1
    with httpx.Client(timeout=30, headers=_github_headers(token)) as client:
        while page <= MAX_PAGES:
            try:
                resp = client.get(
                    f"{api_base}/user/repos",
                    params={
                        # 与 GitLab membership=true 对齐：我拥有的 + 我是协作者的 + 所在组织的
                        "affiliation": "owner,collaborator,organization_member",
                        "sort": "pushed",
                        "direction": "desc",
                        "per_page": PER_PAGE,
                        "page": page,
                    },
                )
            except httpx.HTTPError as exc:
                raise GitProviderError(f"无法连接 Git 服务：{exc}") from exc
            if resp.status_code != 200:
                raise GitProviderError(_explain_status("github", resp.status_code, resp.text))
            batch = resp.json()
            if not isinstance(batch, list):
                raise GitProviderError("GitHub 响应异常，无法解析仓库列表")
            items.extend(_normalize_github(p) for p in batch)
            if len(batch) < PER_PAGE:
                break
            page += 1
    return items


def _normalize_github(p: dict) -> dict:
    owner = p.get("owner") or {}
    return {
        "id": p.get("id"),
        "name": p.get("name") or "",
        "path_with_namespace": p.get("full_name") or "",
        "web_url": p.get("html_url") or "",
        "http_url_to_repo": p.get("clone_url") or p.get("html_url") or "",
        "default_branch": p.get("default_branch") or "",
        "visibility": "private" if p.get("private") else "public",
        "last_activity_at": p.get("pushed_at") or p.get("updated_at") or "",
        "namespace": {
            "kind": "group" if owner.get("type") == "Organization" else "user",
            "name": owner.get("login") or "",
            "full_path": owner.get("login") or "",
        },
    }


# ===================== Gitee（OpenAPI v5） =====================


def _gitee_api_base(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/v5"


def _gitee_verify(api_base: str, token: str) -> dict:
    try:
        resp = httpx.get(f"{api_base}/user", params={"access_token": token}, timeout=15)
    except httpx.HTTPError as exc:
        raise GitProviderError(f"无法连接 Git 服务：{exc}") from exc
    if resp.status_code != 200:
        raise GitProviderError(_explain_status("gitee", resp.status_code, resp.text))
    info = resp.json()
    if not isinstance(info, dict) or not info.get("login"):
        raise GitProviderError("Gitee 响应异常，无法识别用户信息")
    return {"username": info["login"], "name": info.get("name") or ""}


def _gitee_list(api_base: str, token: str) -> list[dict]:
    items: list[dict] = []
    page = 1
    with httpx.Client(timeout=30) as client:
        while page <= MAX_PAGES:
            try:
                resp = client.get(
                    f"{api_base}/user/repos",
                    params={
                        "access_token": token,
                        "affiliation": "owner,collaborator,organization_member",
                        "sort": "pushed",
                        "direction": "desc",  # 旧版接口不识别该参数时自动忽略
                        "per_page": PER_PAGE,
                        "page": page,
                    },
                )
            except httpx.HTTPError as exc:
                raise GitProviderError(f"无法连接 Git 服务：{exc}") from exc
            if resp.status_code != 200:
                raise GitProviderError(_explain_status("gitee", resp.status_code, resp.text))
            batch = resp.json()
            if not isinstance(batch, list):
                raise GitProviderError("Gitee 响应异常，无法解析仓库列表")
            items.extend(_normalize_gitee(p) for p in batch)
            if len(batch) < PER_PAGE:
                break
            page += 1
    return items


def _normalize_gitee(p: dict) -> dict:
    ns = p.get("namespace") or {}
    full = p.get("full_name") or "/".join(x for x in (ns.get("path"), p.get("path")) if x)
    return {
        "id": p.get("id"),
        "name": p.get("name") or p.get("path") or "",
        "path_with_namespace": full or "",
        "web_url": p.get("html_url") or "",
        "http_url_to_repo": p.get("html_url") or "",  # Gitee 的 https 克隆地址与页面地址一致
        "default_branch": p.get("default_branch") or "",
        "visibility": "private" if p.get("private") else "public",
        "last_activity_at": p.get("pushed_at") or p.get("updated_at") or "",
        "namespace": {
            "kind": "group" if ns.get("type") in ("organization", "enterprise") else "user",
            "name": ns.get("name") or ns.get("path") or "",
            "full_path": ns.get("path") or "",
        },
    }


# ===================== 平台调度 =====================


def verify_token(platform: str, base_url: str, token: str) -> dict:
    """验证令牌并返回当前用户信息（含 username）；失败抛 GitProviderError。"""
    platform = resolve_platform(platform, base_url)
    base = normalize_base_url(base_url)
    if platform == "github":
        return _github_verify(_github_api_base(base), token)
    if platform == "gitee":
        return _gitee_verify(_gitee_api_base(base), token)
    return _gitlab_verify(base + "/api/v4", token)


def list_projects(platform: str, base_url: str, token: str) -> list[dict]:
    """拉取当前用户可见的仓库列表（已归一化），按最近活跃排序，带命名空间（组织）信息。"""
    platform = resolve_platform(platform, base_url)
    base = normalize_base_url(base_url)
    if platform == "github":
        return _github_list(_github_api_base(base), token)
    if platform == "gitee":
        return _gitee_list(_gitee_api_base(base), token)
    return _gitlab_list(base + "/api/v4", token)
