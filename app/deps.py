"""FastAPI 依赖：认证、当前用户、调用主体 Principal。"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User, UserApiToken
from .security import decode_access_token, hash_api_token


@dataclass
class Principal:
    """统一的调用主体：可能来自网页会话(JWT)或平台 API Token。"""

    user: User
    source: str  # "web" | "api"
    token: Optional[UserApiToken] = None

    @property
    def daily_request_limit(self) -> int:
        if self.token and self.token.daily_request_limit:
            return int(self.token.daily_request_limit)
        return settings.default_daily_request_limit

    @property
    def daily_token_limit(self) -> int:
        if self.token and self.token.daily_token_limit:
            return int(self.token.daily_token_limit)
        return settings.default_daily_token_limit

    @property
    def rate_limit_per_minute(self) -> int:
        if self.token and self.token.rate_limit_per_minute:
            return int(self.token.rate_limit_per_minute)
        return settings.default_rate_limit_per_minute

    @property
    def model_scope(self) -> str:
        if self.token and self.token.model_scope:
            return self.token.model_scope
        # 网页会话按角色给默认可用等级
        return _role_scope(self.user.role)

    @property
    def allow_batch(self) -> bool:
        if self.token is not None:
            return bool(self.token.allow_batch)
        return self.user.role in ("group", "teacher", "admin")

    @property
    def limit_key(self) -> str:
        return f"token:{self.token.id}" if self.token else f"user:{self.user.id}"


def _role_scope(role: str) -> str:
    return {
        "student": "basic",
        "graduate": "standard",
        "group": "standard",
        "teacher": "advanced",
        "admin": "advanced",
    }.get(role, "basic")


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


# ---------------------------------------------------------------------------
# 网页会话：JWT
# ---------------------------------------------------------------------------
def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少认证信息")
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期或无效，请重新登录")
    user = db.get(User, int(payload["sub"]))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    if user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user


# ---------------------------------------------------------------------------
# 平台 API Token：/api/v1/*
# ---------------------------------------------------------------------------
def get_api_principal(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    raw = _extract_bearer(authorization)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少平台 API Token")
    token = (
        db.query(UserApiToken)
        .filter(UserApiToken.token_hash == hash_api_token(raw))
        .first()
    )
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的 API Token")
    if token.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API Token 已被禁用")
    if token.expires_at and token.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API Token 已过期")
    user = db.get(User, token.user_id)
    if not user or user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号不可用")
    token.last_used_at = datetime.utcnow()
    db.commit()
    return Principal(user=user, source="api", token=token)


def web_principal(user: User = Depends(get_current_user)) -> Principal:
    return Principal(user=user, source="web", token=None)
