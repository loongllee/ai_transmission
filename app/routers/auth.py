"""认证：注册 / 登录 / 学校统一身份认证 SSO（方案第七节 / 第四阶段）。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import sso as sso_service
from ..config import settings
from ..database import get_db
from ..models import User, WalletAccount
from ..schemas import (
    LoginRequest,
    RegisterRequest,
    SsoCallbackRequest,
    SsoCodeOut,
    SsoConfigOut,
    SsoMockLoginRequest,
    TokenResponse,
    UserOut,
)
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    exists = db.query(User).filter(User.username == payload.username).first()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role="student",
        status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # 注册即发放基础免费额度（方案 10.1）
    db.add(WalletAccount(user_id=user.id, free_points=settings.signup_free_points))
    db.commit()
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if user.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")
    token = create_access_token(user.id, user.role, user.username)
    return TokenResponse(access_token=token)


# ---------- 学校统一身份认证 SSO（第四阶段）----------
@router.get("/sso/config", response_model=SsoConfigOut)
def sso_config():
    return SsoConfigOut(
        enabled=settings.sso_enabled, mode=settings.sso_mode, provider=settings.sso_provider_name
    )


@router.post("/sso/mock/login", response_model=SsoCodeOut)
def sso_mock_login(payload: SsoMockLoginRequest):
    """模拟学校 IdP 登录，颁发授权码（仅 mock 模式可用）。"""
    if not settings.sso_enabled or settings.sso_mode != "mock":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前未启用 mock SSO 模式")
    claims = {
        "sub": payload.username,
        "username": payload.username,
        "email": payload.email,
        "role": payload.role,
        "college": payload.college,
        "major": payload.major,
        "group": payload.group,
    }
    return SsoCodeOut(code=sso_service.mock_issue_code(claims))


@router.post("/sso/callback", response_model=dict)
def sso_callback(payload: SsoCallbackRequest, db: Session = Depends(get_db)):
    """用授权码换取平台 JWT；首次登录自动开户并绑定组织。"""
    return sso_service.exchange_and_login(db, payload.code, payload.redirect_uri)
