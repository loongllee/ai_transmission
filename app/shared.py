"""共享账户服务：多人共用一个账户，受聚合速率/并发/每日上限约束。

成员通过请求头 X-Member-Id 标识。账户在 速率/并发 不超上限时允许多名成员并发使用；
消耗统一计入账户拥有者钱包，并按成员维度统计用量。
"""
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .models import SharedAccount, SharedMember
from .security import generate_api_token, hash_api_token


def create_account(
    db: Session,
    owner,
    name: str,
    rate_limit_per_minute: int,
    max_concurrency: int,
    daily_request_limit: int,
    model_scope: str = "basic",
    restrict_members: bool = False,
) -> Tuple[SharedAccount, str]:
    plaintext, token_hash, display = generate_api_token()
    acct = SharedAccount(
        name=name,
        owner_user_id=owner.id,
        token_hash=token_hash,
        token_prefix=display,
        model_scope=model_scope or "basic",
        rate_limit_per_minute=max(1, int(rate_limit_per_minute)),
        max_concurrency=max(0, int(max_concurrency)),
        daily_request_limit=max(1, int(daily_request_limit)),
        restrict_members=bool(restrict_members),
        status="active",
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct, plaintext


def resolve_account(db: Session, token: str) -> SharedAccount:
    acct = db.query(SharedAccount).filter(SharedAccount.token_hash == hash_api_token(token)).first()
    if not acct:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的共享账户 Token")
    if acct.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "共享账户已停用")
    return acct


def resolve_member(db: Session, acct: SharedAccount, member_label: str) -> SharedMember:
    """解析（必要时自动登记）成员。受限账户仅允许白名单成员。"""
    label = (member_label or "").strip() or "anonymous"
    member = (
        db.query(SharedMember)
        .filter(SharedMember.shared_account_id == acct.id, SharedMember.member_label == label)
        .first()
    )
    if member:
        if member.status != "active":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "该成员已被禁用")
        return member
    if acct.restrict_members:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该成员不在共享账户白名单内")
    member = SharedMember(shared_account_id=acct.id, member_label=label, status="active")
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def record_usage(db: Session, member: SharedMember, tokens: int) -> None:
    member.request_count = int(member.request_count or 0) + 1
    member.token_count = int(member.token_count or 0) + int(tokens or 0)
    member.last_used_at = datetime.utcnow()
    db.commit()


def add_member(db: Session, acct: SharedAccount, member_label: str) -> SharedMember:
    label = (member_label or "").strip()
    if not label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "成员标识不能为空")
    existing = (
        db.query(SharedMember)
        .filter(SharedMember.shared_account_id == acct.id, SharedMember.member_label == label)
        .first()
    )
    if existing:
        existing.status = "active"
        db.commit()
        db.refresh(existing)
        return existing
    member = SharedMember(shared_account_id=acct.id, member_label=label, status="active")
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def set_member_status(db: Session, acct: SharedAccount, member_label: str, new_status: str) -> SharedMember:
    member = (
        db.query(SharedMember)
        .filter(SharedMember.shared_account_id == acct.id, SharedMember.member_label == member_label)
        .first()
    )
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成员不存在")
    member.status = new_status
    db.commit()
    db.refresh(member)
    return member


def list_members(db: Session, acct: SharedAccount) -> List[SharedMember]:
    return (
        db.query(SharedMember)
        .filter(SharedMember.shared_account_id == acct.id)
        .order_by(SharedMember.id.asc())
        .all()
    )


def owned_account(db: Session, owner_id: int, account_id: int) -> SharedAccount:
    acct = db.get(SharedAccount, account_id)
    if not acct or acct.owner_user_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "共享账户不存在")
    return acct
