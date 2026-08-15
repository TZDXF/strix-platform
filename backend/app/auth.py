"""用户认证：PBKDF2 口令哈希 + HMAC 签名令牌 + FastAPI 依赖（require_user / require_admin）。

不引入额外依赖：口令用 hashlib.pbkdf2_hmac，令牌为「base64(payload).base64(hmac)」结构，
有效期与签名密钥由环境变量控制（SECRET_KEY / TOKEN_EXPIRY_HOURS）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

_PBKDF2_ITERS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"pbkdf2${_PBKDF2_ITERS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def _secret() -> bytes:
    from .config import get_settings

    return get_settings().secret_key.encode("utf-8")


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_token(user: User) -> str:
    payload = {
        "uid": user.id,
        "username": user.username,
        "role": user.role,
        "exp": int(time.time()) + get_expiry_hours() * 3600,
    }
    body = _b64e(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    sig = _b64e(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def get_expiry_hours() -> int:
    from .config import get_settings

    return get_settings().token_expiry_hours


def decode_token(token: str) -> dict[str, Any] | None:
    """校验签名与有效期，返回 payload；非法/过期返回 None。"""
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64e(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    return payload


def _extract_token(x_api_token: str | None, authorization: str | None) -> str | None:
    if x_api_token:
        return x_api_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def require_user(
    request: Request,
    db: Session = Depends(get_db),
    x_api_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> User:
    token = _extract_token(x_api_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = db.get(User, payload.get("uid"))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
    request.state.user_id = user.id
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def bootstrap_admin(db: Session) -> None:
    """用户库为空时创建初始超管（密码来自 ADMIN_INITIAL_PASSWORD）。"""
    from .config import get_settings

    if db.query(User).count() > 0:
        return
    password = get_settings().admin_initial_password
    admin = User(username="admin", password_hash=hash_password(password), role="admin", display_name="管理员")
    db.add(admin)
    db.commit()


def generate_password(length: int = 12) -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
