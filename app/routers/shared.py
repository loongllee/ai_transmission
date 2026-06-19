"""共享账户 —— 成员侧接口。

认证：Authorization: Bearer <member-token>（每名成员独立凭据，凭据即身份，不能冒充）。
限额两层：账户聚合上限 + 该成员单独上限。历史按成员隔离，成员只能管自己的记录。
"""
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .. import billing, chat, ratelimit, shared
from ..database import get_db
from ..models import SharedCall, User
from ..schemas import (
    ChatRequest,
    ChatResponse,
    MemberSettingsOut,
    SharedCallOut,
    UpdateMemberSettingsRequest,
)

router = APIRouter(prefix="/api/v1/shared", tags=["shared-account"])


def _bearer(authorization):
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    return None


def get_member(authorization: str = Header(default=None), db: Session = Depends(get_db)):
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少成员 Token")
    acct, member = shared.authenticate_member(db, token)
    return acct, member


@router.post("/chat", response_model=ChatResponse)
async def shared_chat(
    payload: ChatRequest,
    ctx=Depends(get_member),
    db: Session = Depends(get_db),
):
    acct, member = ctx

    # 模型等级权限（账户上限 ⊕ 成员上限）
    if not billing.level_allowed(payload.model_level, shared.effective_scope(acct, member)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"该成员最高可用 {shared.effective_scope(acct, member)} 模型"
        )

    # 成员累计 token 上限
    if member.token_limit and int(member.token_count or 0) >= int(member.token_limit):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "该成员 token 用量已达上限")

    acc_key = f"shared:{acct.id}"
    mem_key = f"shared:{acct.id}:m:{member.id}"

    # 1) 账户聚合：每日 / 每分钟
    if not ratelimit.check_and_incr_daily(acc_key, acct.daily_request_limit):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "共享账户今日调用次数已达上限")
    if not ratelimit.check_and_incr_minute(acc_key, acct.rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "共享账户调用速率已达上限，请稍后再试")
    # 2) 成员单独：每日 / 每分钟
    if not ratelimit.check_and_incr_daily(mem_key, shared.effective_daily(acct, member)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "你今日调用次数已达上限")
    if not ratelimit.check_and_incr_minute(mem_key, shared.effective_rpm(acct, member)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "你的调用速率已达上限，请稍后再试")
    # 3) 账户并发
    if not ratelimit.acquire_slot(acc_key, acct.max_concurrency):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "共享账户并发已满，请稍后再试")

    owner = db.get(User, acct.owner_user_id)
    if not owner or owner.status != "active":
        ratelimit.release_slot(acc_key, acct.max_concurrency)
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
        ratelimit.release_slot(acc_key, acct.max_concurrency)

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


# ----------------------------- 成员自己的历史 -----------------------------
@router.get("/history", response_model=List[SharedCallOut])
def shared_history(limit: int = 50, ctx=Depends(get_member), db: Session = Depends(get_db)):
    """只返回当前成员自己的历史（按 member_id 隔离）。"""
    acct, member = ctx
    limit = max(1, min(limit, 500))
    return (
        db.query(SharedCall)
        .filter(SharedCall.shared_account_id == acct.id, SharedCall.member_id == member.id)
        .order_by(SharedCall.id.desc())
        .limit(limit)
        .all()
    )


@router.delete("/history/{call_id}", response_model=dict)
def delete_my_call(call_id: int, ctx=Depends(get_member), db: Session = Depends(get_db)):
    _acct, member = ctx
    call = db.get(SharedCall, call_id)
    if not call or call.member_id != member.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "记录不存在")
    db.delete(call)
    db.commit()
    return {"ok": True, "deleted": call_id}


@router.delete("/history", response_model=dict)
def clear_my_history(ctx=Depends(get_member), db: Session = Depends(get_db)):
    _acct, member = ctx
    n = db.query(SharedCall).filter(SharedCall.member_id == member.id).delete()
    db.commit()
    return {"ok": True, "cleared": n}


# ----------------------------- 成员自助设置 -----------------------------
@router.get("/me", response_model=dict)
def shared_me(ctx=Depends(get_member)):
    acct, member = ctx
    return {
        "account": acct.name,
        "member": member.member_label,
        "display_name": member.display_name,
        "effective_model_scope": shared.effective_scope(acct, member),
        "my_rate_limit_per_minute": shared.effective_rpm(acct, member),
        "my_daily_request_limit": shared.effective_daily(acct, member),
        "my_token_limit": member.token_limit,
        "my_request_count": int(member.request_count or 0),
        "my_token_count": int(member.token_count or 0),
    }


@router.get("/me/settings", response_model=MemberSettingsOut)
def get_my_settings(ctx=Depends(get_member)):
    _acct, member = ctx
    return member


@router.patch("/me/settings", response_model=MemberSettingsOut)
def update_my_settings(payload: UpdateMemberSettingsRequest, ctx=Depends(get_member), db: Session = Depends(get_db)):
    acct, member = ctx
    return shared.update_prefs(db, acct, member, payload.model_dump(exclude_unset=True))


@router.post("/me/token/reset", response_model=dict)
def reset_my_token(ctx=Depends(get_member), db: Session = Depends(get_db)):
    _acct, member = ctx
    member, plaintext = shared.rotate_member_token(db, member)
    return {"ok": True, "plaintext_token": plaintext, "token_prefix": member.token_prefix}
