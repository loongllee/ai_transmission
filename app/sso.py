"""学校统一身份认证（SSO）。

两种模式（settings.sso_mode）：
  - mock：内置模拟 IdP，离线可演示/测试完整流程（默认）。
  - oidc：标准 OAuth2/OIDC 授权码流程，对接真实学校 IdP。

登录后自动开户（auto-provision）：按 IdP 返回的 学院/专业/课题组 建组织树并绑定成员，
角色按 role 声明映射，签发平台自身 JWT。
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

import httpx
import jwt
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from . import org
from .config import settings
from .models import SsoIdentity, User, WalletAccount
from .security import create_access_token, hash_password

_VALID_ROLES = ("student", "graduate", "group", "teacher", "admin")


# ----------------------------- mock IdP -----------------------------
def mock_issue_code(claims: dict) -> str:
    """模拟学校 IdP 颁发授权码（用平台密钥签名的短期 JWT）。"""
    payload = dict(claims)
    payload["typ"] = "sso_code"
    payload["exp"] = datetime.utcnow() + timedelta(seconds=settings.sso_code_ttl_seconds)
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _decode_mock_code(code: str) -> dict:
    try:
        data = jwt.decode(code, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "SSO 授权码无效或已过期")
    if data.get("typ") != "sso_code":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "SSO 授权码类型错误")
    return data


# ----------------------------- OIDC 模式 -----------------------------
def _oidc_exchange(code: str, redirect_uri: Optional[str]) -> dict:
    if not (settings.sso_token_url and settings.sso_userinfo_url):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OIDC 端点未配置")
    with httpx.Client(timeout=30.0) as client:
        tok = client.post(
            settings.sso_token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri or settings.sso_redirect_uri,
                "client_id": settings.sso_client_id,
                "client_secret": settings.sso_client_secret,
            },
        )
        tok.raise_for_status()
        access = tok.json().get("access_token")
        ui = client.get(settings.sso_userinfo_url, headers={"Authorization": f"Bearer {access}"})
        ui.raise_for_status()
        return ui.json()


# ----------------------------- 开户 / 登录 -----------------------------
def _map_role(claims: dict) -> str:
    role = (claims.get("role") or "").strip()
    return role if role in _VALID_ROLES else settings.sso_default_role


def provision_user(db: Session, provider: str, claims: dict) -> Tuple[User, bool]:
    subject = str(claims.get("sub") or claims.get("username") or "").strip()
    if not subject:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SSO 声明缺少 sub")

    ident = (
        db.query(SsoIdentity)
        .filter(SsoIdentity.provider == provider, SsoIdentity.subject == subject)
        .first()
    )
    created = False
    if ident:
        user = db.get(User, ident.user_id)
    else:
        username = (claims.get("username") or claims.get("preferred_username") or f"sso_{subject}").strip()
        user = db.query(User).filter(User.username == username).first()
        if not user:
            user = User(
                username=username,
                email=claims.get("email"),
                password_hash=hash_password(secrets.token_hex(16)),  # 不可用于密码登录
                role=_map_role(claims),
                status="active",
            )
            db.add(user)
            db.flush()
            db.add(WalletAccount(user_id=user.id, free_points=settings.signup_free_points))
            created = True
        db.add(SsoIdentity(provider=provider, subject=subject, user_id=user.id))
        db.flush()

    # 组织归属（学院/专业/课题组）
    leaf = org.ensure_path(db, claims.get("college"), claims.get("major"), claims.get("group"))
    if leaf:
        org.set_membership(db, user.id, leaf)
    db.commit()
    db.refresh(user)
    return user, created


def exchange_and_login(db: Session, code: str, redirect_uri: str = None) -> dict:
    if not settings.sso_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "SSO 未启用")
    if settings.sso_mode == "mock":
        claims = _decode_mock_code(code)
    else:
        claims = _oidc_exchange(code, redirect_uri)
    user, created = provision_user(db, settings.sso_provider_name, claims)
    if user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")
    token = create_access_token(user.id, user.role, user.username)
    return {"access_token": token, "token_type": "bearer", "created": created, "username": user.username}
