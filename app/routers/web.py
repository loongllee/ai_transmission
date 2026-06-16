"""网页端接口（JWT 会话）：个人信息、钱包、额度、Token 管理、用量、聊天。"""
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import billing, chat
from ..database import get_db
from ..deps import Principal, get_current_user, web_principal
from ..models import Model, User, UserApiToken, UsageLog, WalletAccount
from ..schemas import (
    ApiTokenCreatedOut,
    ApiTokenOut,
    ChatRequest,
    ChatResponse,
    CreateTokenRequest,
    QuotaOut,
    UsageLogOut,
    UserOut,
    WalletOut,
)
from ..security import generate_api_token, hash_api_token

router = APIRouter(prefix="/api/web", tags=["web"])


def _get_wallet(db: Session, user_id: int) -> WalletAccount:
    wallet = db.query(WalletAccount).filter(WalletAccount.user_id == user_id).first()
    if not wallet:
        wallet = WalletAccount(user_id=user_id)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return wallet


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/wallet", response_model=WalletOut)
def wallet(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    w = _get_wallet(db, user.id)
    return WalletOut(
        free_points=w.free_points or 0,
        paid_points=w.paid_points or 0,
        subsidy_points=w.subsidy_points or 0,
        project_points=w.project_points or 0,
        total_used_points=w.total_used_points or 0,
        balance=billing.wallet_balance(w),
    )


@router.get("/quota", response_model=QuotaOut)
def quota(principal: Principal = Depends(web_principal), db: Session = Depends(get_db)):
    w = _get_wallet(db, principal.user.id)
    return QuotaOut(
        role=principal.user.role,
        balance=billing.wallet_balance(w),
        daily_request_limit=principal.daily_request_limit,
        daily_token_limit=principal.daily_token_limit,
        rate_limit_per_minute=principal.rate_limit_per_minute,
    )


@router.get("/models")
def list_models(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """返回用户端可见的模型等级（仅 basic/standard/advanced，不暴露真实模型名）。"""
    rows = db.execute(select(Model).where(Model.enabled.is_(True))).scalars().all()
    levels = {}
    for m in rows:
        levels.setdefault(m.model_level, m.display_name or m.model_level)
    order = ["basic", "standard", "advanced"]
    return [{"model_level": lv, "display_name": levels[lv]} for lv in order if lv in levels]


# ---------- API Token 管理 ----------
@router.get("/tokens", response_model=List[ApiTokenOut])
def list_tokens(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(UserApiToken)
        .filter(UserApiToken.user_id == user.id)
        .order_by(UserApiToken.id.desc())
        .all()
    )


@router.post("/tokens", response_model=ApiTokenCreatedOut, status_code=status.HTTP_201_CREATED)
def create_token(
    payload: CreateTokenRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plaintext, token_hash, display = generate_api_token()
    token = UserApiToken(
        user_id=user.id,
        token_hash=token_hash,
        token_prefix=display,
        token_name=payload.token_name or "default",
        status="active",
        model_scope=payload.model_scope or "basic",
        allow_batch=bool(payload.allow_batch),
        expires_at=datetime.utcnow() + timedelta(days=180),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    out = ApiTokenCreatedOut.model_validate(token)
    out.plaintext_token = plaintext
    return out


@router.post("/tokens/{token_id}/reset", response_model=ApiTokenCreatedOut)
def reset_token(token_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = _owned_token(db, user, token_id)
    plaintext, token_hash, display = generate_api_token()
    token.token_hash = token_hash
    token.token_prefix = display
    token.status = "active"
    db.commit()
    db.refresh(token)
    out = ApiTokenCreatedOut.model_validate(token)
    out.plaintext_token = plaintext
    return out


@router.post("/tokens/{token_id}/disable", response_model=ApiTokenOut)
def disable_token(token_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = _owned_token(db, user, token_id)
    token.status = "disabled"
    db.commit()
    db.refresh(token)
    return token


def _owned_token(db: Session, user: User, token_id: int) -> UserApiToken:
    token = db.get(UserApiToken, token_id)
    if not token or token.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token 不存在")
    return token


# ---------- 用量 ----------
@router.get("/usage", response_model=List[UsageLogOut])
def usage(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    return (
        db.query(UsageLog)
        .filter(UsageLog.user_id == user.id)
        .order_by(UsageLog.id.desc())
        .limit(limit)
        .all()
    )


# ---------- 网页聊天 ----------
@router.post("/chat", response_model=ChatResponse)
async def web_chat(
    payload: ChatRequest,
    principal: Principal = Depends(web_principal),
    db: Session = Depends(get_db),
):
    messages = [{"role": m.role, "content": m.content} for m in payload.messages]
    result = await chat.run_chat(
        db,
        principal,
        payload.model_level,
        payload.task_type or "web_chat",
        messages,
        payload.max_tokens or 512,
        payload.temperature or 0.7,
    )
    return result
