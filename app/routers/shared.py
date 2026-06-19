"""共享账户调用接口（成员侧）。

认证：Authorization: Bearer <共享账户 Token>，并用请求头 X-Member-Id 标识成员。
在 聚合每分钟速率 / 并发 / 每日次数 不超上限时，允许多名成员并发使用；
消耗统一计入账户拥有者钱包，按成员维度统计用量。
"""
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .. import billing, chat, ratelimit, shared
from ..database import get_db
from ..models import SharedCall, User
from ..schemas import ChatRequest, ChatResponse, SharedCallOut

router = APIRouter(prefix="/api/v1/shared", tags=["shared-account"])


def _bearer(authorization):
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    return None


@router.post("/chat", response_model=ChatResponse)
async def shared_chat(
    payload: ChatRequest,
    authorization: str = Header(default=None),
    x_member_id: str = Header(default=None),
    db: Session = Depends(get_db),
):
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少共享账户 Token")
    acct = shared.resolve_account(db, token)
    member = shared.resolve_member(db, acct, x_member_id)

    # 模型等级权限
    if not billing.level_allowed(payload.model_level, acct.model_scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"共享账户最高可用 {acct.model_scope} 模型")

    rl_key = f"shared:{acct.id}"
    # 聚合每日上限
    if not ratelimit.check_and_incr_daily(rl_key, acct.daily_request_limit):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "共享账户今日调用次数已达上限")
    # 聚合每分钟速率上限（核心：速率不超上限时允许多人共用）
    if not ratelimit.check_and_incr_minute(rl_key, acct.rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "共享账户调用速率已达上限，请稍后再试")
    # 并发上限
    if not ratelimit.acquire_slot(rl_key, acct.max_concurrency):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "共享账户并发已满，请稍后再试")

    owner = db.get(User, acct.owner_user_id)
    if not owner or owner.status != "active":
        ratelimit.release_slot(rl_key, acct.max_concurrency)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账户拥有者不可用")

    try:
        messages = [{"role": m.role, "content": m.content} for m in payload.messages]
        result = await chat.execute_chat(
            db,
            user=owner,
            token=None,
            source="shared",
            model_level=payload.model_level,
            task_type=payload.task_type or "shared_chat",
            messages=messages,
            max_tokens=payload.max_tokens or 512,
            temperature=payload.temperature or 0.7,
        )
    finally:
        ratelimit.release_slot(rl_key, acct.max_concurrency)

    # 成员维度用量 + 按成员隔离的对话记录（服务端据此区分不同成员，互不混淆）
    shared.record_usage(db, member, result["usage"]["input_tokens"] + result["usage"]["output_tokens"])
    last_user = next((m.content for m in reversed(payload.messages) if m.role == "user"), "")
    db.add(
        SharedCall(
            shared_account_id=acct.id,
            member_id=member.id,
            member_label=member.member_label,
            request_id=result["request_id"],
            model_level=result["model_level"],
            model_name=result["model"],
            prompt=last_user[:4000],
            response=(result["content"] or "")[:8000],
            input_tokens=result["usage"]["input_tokens"],
            output_tokens=result["usage"]["output_tokens"],
            points_used=result["usage"]["points_used"],
        )
    )
    db.commit()
    return result


@router.get("/history", response_model=List[SharedCallOut])
def shared_history(
    limit: int = 50,
    authorization: str = Header(default=None),
    x_member_id: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """返回**当前成员自己**的历史对话记录（按成员隔离，不会看到他人记录）。"""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少共享账户 Token")
    acct = shared.resolve_account(db, token)
    member = shared.resolve_member(db, acct, x_member_id)
    limit = max(1, min(limit, 200))
    return (
        db.query(SharedCall)
        .filter(SharedCall.shared_account_id == acct.id, SharedCall.member_id == member.id)
        .order_by(SharedCall.id.desc())
        .limit(limit)
        .all()
    )


@router.get("/me", response_model=dict)
def shared_me(
    authorization: str = Header(default=None),
    x_member_id: str = Header(default=None),
    db: Session = Depends(get_db),
):
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少共享账户 Token")
    acct = shared.resolve_account(db, token)
    member = shared.resolve_member(db, acct, x_member_id)
    return {
        "account": acct.name,
        "model_scope": acct.model_scope,
        "rate_limit_per_minute": acct.rate_limit_per_minute,
        "max_concurrency": acct.max_concurrency,
        "daily_request_limit": acct.daily_request_limit,
        "member": member.member_label,
        "my_request_count": int(member.request_count or 0),
        "my_token_count": int(member.token_count or 0),
    }
