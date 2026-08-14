"""共享令牌认证 + 审计日志（最低成本护栏，见可行性文档 5.4）。"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from .config import get_settings


def require_token(
    request: Request,
    x_api_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    token = x_api_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    expected = get_settings().api_token
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing api token")
    return token


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
