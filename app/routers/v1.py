"""科研程序化 API（平台内部 API Token 认证）—— 方案第十三节接口设计。

所有接口：Authorization: Bearer <platform_api_token>
"""
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import billing, chat
from ..database import get_db
from ..deps import Principal, get_api_principal
from ..models import UsageLog, WalletAccount
from ..schemas import (
    ChatRequest,
    ChatResponse,
    CompletionRequest,
    QuotaOut,
    UsageLogOut,
    WalletOut,
)

router = APIRouter(prefix="/api/v1", tags=["v1-api"])


def _get_wallet(db: Session, user_id: int) -> WalletAccount:
    wallet = db.query(WalletAccount).filter(WalletAccount.user_id == user_id).first()
    if not wallet:
        wallet = WalletAccount(user_id=user_id)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return wallet


# 13.1 通用聊天接口
@router.post("/llm/chat", response_model=ChatResponse)
async def llm_chat(
    payload: ChatRequest,
    principal: Principal = Depends(get_api_principal),
    db: Session = Depends(get_db),
):
    messages = [{"role": m.role, "content": m.content} for m in payload.messages]
    return await chat.run_chat(
        db,
        principal,
        payload.model_level,
        payload.task_type or "research_chat",
        messages,
        payload.max_tokens or 512,
        payload.temperature or 0.7,
    )


# 13.2 文本生成接口
@router.post("/llm/completions", response_model=ChatResponse)
async def llm_completions(
    payload: CompletionRequest,
    principal: Principal = Depends(get_api_principal),
    db: Session = Depends(get_db),
):
    messages = [{"role": "user", "content": payload.prompt}]
    return await chat.run_chat(
        db,
        principal,
        payload.model_level,
        payload.task_type or "completion",
        messages,
        payload.max_tokens or 512,
        payload.temperature or 0.7,
    )


# 13.5 查询个人额度接口
@router.get("/quota/me", response_model=QuotaOut)
def quota_me(principal: Principal = Depends(get_api_principal), db: Session = Depends(get_db)):
    w = _get_wallet(db, principal.user.id)
    return QuotaOut(
        role=principal.user.role,
        balance=billing.wallet_balance(w),
        daily_request_limit=principal.daily_request_limit,
        daily_token_limit=principal.daily_token_limit,
        rate_limit_per_minute=principal.rate_limit_per_minute,
    )


# 13.6 查询调用记录接口
@router.get("/usage/me", response_model=List[UsageLogOut])
def usage_me(
    limit: int = 50,
    principal: Principal = Depends(get_api_principal),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    return (
        db.query(UsageLog)
        .filter(UsageLog.user_id == principal.user.id)
        .order_by(UsageLog.id.desc())
        .limit(limit)
        .all()
    )


# 13.7 查询点数账户接口
@router.get("/wallet/me", response_model=WalletOut)
def wallet_me(principal: Principal = Depends(get_api_principal), db: Session = Depends(get_db)):
    w = _get_wallet(db, principal.user.id)
    return WalletOut(
        free_points=w.free_points or 0,
        paid_points=w.paid_points or 0,
        subsidy_points=w.subsidy_points or 0,
        project_points=w.project_points or 0,
        total_used_points=w.total_used_points or 0,
        balance=billing.wallet_balance(w),
    )
