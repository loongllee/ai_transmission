"""共享账户服务：一个账户多名成员共用，每名成员持有独立 member-token。

- 拥有者(JWT)：建账户、设账户/成员限额、签发/吊销/轮换成员凭据、查看全部数据。
- 成员(member-token)：调用、看/管自己的历史、改自己的偏好、重置自己的凭据。
- 限额两层：账户聚合上限 + 每成员单独上限（None 回退账户默认）。
"""
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .billing import LEVEL_ORDER
from .models import SharedAccount, SharedMember
from .security import generate_api_token, hash_api_token


# ----------------------------- 账户（拥有者侧）-----------------------------
def create_account(
    db: Session,
    owner,
    name: str,
    rate_limit_per_minute: int,
    max_concurrency: int,
    daily_request_limit: int,
    model_scope: str = "basic",
    restrict_members: bool = False,
    daily_token_limit: Optional[int] = None,
    default_member_rpm: Optional[int] = None,
    default_member_daily: Optional[int] = None,
) -> SharedAccount:
    plaintext, token_hash, display = generate_api_token()  # 账户内部标识
    acct = SharedAccount(
        name=name,
        owner_user_id=owner.id,
        token_hash=token_hash,
        token_prefix=display,
        model_scope=model_scope or "basic",
        rate_limit_per_minute=max(1, int(rate_limit_per_minute)),
        max_concurrency=max(0, int(max_concurrency)),
        daily_request_limit=max(1, int(daily_request_limit)),
        daily_token_limit=daily_token_limit,
        default_member_rpm=default_member_rpm,
        default_member_daily=default_member_daily,
        restrict_members=bool(restrict_members),
        status="active",
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct


def owned_account(db: Session, owner_id: int, account_id: int) -> SharedAccount:
    acct = db.get(SharedAccount, account_id)
    if not acct or acct.owner_user_id != owner_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "共享账户不存在")
    return acct


def update_account(db: Session, acct: SharedAccount, fields: dict) -> SharedAccount:
    allowed = {
        "name", "status", "model_scope", "rate_limit_per_minute", "max_concurrency",
        "daily_request_limit", "daily_token_limit", "default_member_rpm",
        "default_member_daily", "restrict_members",
    }
    for k, v in fields.items():
        if k in allowed and v is not None:
            setattr(acct, k, v)
    db.commit()
    db.refresh(acct)
    return acct


# ----------------------------- 成员（拥有者侧）-----------------------------
def add_member(db: Session, acct: SharedAccount, member_label: str, **limits) -> Tuple[SharedMember, str]:
    label = (member_label or "").strip()
    if not label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "成员标识不能为空")
    if db.query(SharedMember).filter(
        SharedMember.shared_account_id == acct.id, SharedMember.member_label == label
    ).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "该成员已存在")
    plaintext, token_hash, display = generate_api_token()
    member = SharedMember(
        shared_account_id=acct.id,
        member_label=label,
        token_hash=token_hash,
        token_prefix=display,
        status="active",
        rpm_limit=limits.get("rpm_limit"),
        daily_request_limit=limits.get("daily_request_limit"),
        token_limit=limits.get("token_limit"),
        model_scope=_clamp_scope(limits.get("model_scope"), acct.model_scope),
        display_name=limits.get("display_name") or label,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member, plaintext


def update_member(db: Session, acct: SharedAccount, label: str, fields: dict) -> SharedMember:
    member = _member_by_label(db, acct, label)
    simple = {"rpm_limit", "daily_request_limit", "token_limit", "note", "display_name", "expires_at", "status"}
    for k, v in fields.items():
        if k in simple and v is not None:
            setattr(member, k, v)
    if fields.get("model_scope") is not None:
        member.model_scope = _clamp_scope(fields["model_scope"], acct.model_scope)
    db.commit()
    db.refresh(member)
    return member


def rotate_member_token(db: Session, member: SharedMember) -> Tuple[SharedMember, str]:
    plaintext, token_hash, display = generate_api_token()
    member.token_hash = token_hash
    member.token_prefix = display
    member.status = "active"
    db.commit()
    db.refresh(member)
    return member, plaintext


def list_members(db: Session, acct: SharedAccount) -> List[SharedMember]:
    return (
        db.query(SharedMember)
        .filter(SharedMember.shared_account_id == acct.id)
        .order_by(SharedMember.id.asc())
        .all()
    )


def _member_by_label(db: Session, acct: SharedAccount, label: str) -> SharedMember:
    member = (
        db.query(SharedMember)
        .filter(SharedMember.shared_account_id == acct.id, SharedMember.member_label == label)
        .first()
    )
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成员不存在")
    return member


# ----------------------------- 成员认证（成员侧）-----------------------------
def authenticate_member(db: Session, member_token: str) -> Tuple[SharedAccount, SharedMember]:
    """用 member-token 解析（账户, 成员）。凭据即身份，无法冒充他人。"""
    member = db.query(SharedMember).filter(SharedMember.token_hash == hash_api_token(member_token)).first()
    if not member:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的成员 Token")
    if member.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该成员已被禁用")
    if member.expires_at and member.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该成员凭据已过期")
    acct = db.get(SharedAccount, member.shared_account_id)
    if not acct or acct.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "共享账户已停用")
    return acct, member


# ----------------------------- 有效限额（账户默认 ⊕ 成员覆盖）-----------------------------
def effective_rpm(acct: SharedAccount, member: SharedMember) -> int:
    return int(member.rpm_limit or acct.default_member_rpm or acct.rate_limit_per_minute)


def effective_daily(acct: SharedAccount, member: SharedMember) -> int:
    return int(member.daily_request_limit or acct.default_member_daily or acct.daily_request_limit)


def effective_scope(acct: SharedAccount, member: SharedMember) -> str:
    return member.model_scope or acct.model_scope or "basic"


def _clamp_scope(requested: Optional[str], account_scope: str) -> Optional[str]:
    """成员等级不得超过账户等级。"""
    if not requested:
        return None
    if LEVEL_ORDER.get(requested, 99) > LEVEL_ORDER.get(account_scope, 0):
        return account_scope
    return requested


def record_usage(db: Session, member: SharedMember, tokens: int) -> None:
    member.request_count = int(member.request_count or 0) + 1
    member.token_count = int(member.token_count or 0) + int(tokens or 0)
    member.last_used_at = datetime.utcnow()
    db.commit()


# ----------------------------- 成员自助偏好 -----------------------------
def update_prefs(db: Session, acct: SharedAccount, member: SharedMember, fields: dict) -> SharedMember:
    if fields.get("display_name") is not None:
        member.display_name = fields["display_name"]
    if fields.get("default_max_tokens") is not None:
        member.default_max_tokens = max(1, int(fields["default_max_tokens"]))
    if fields.get("default_temperature") is not None:
        member.default_temperature = fields["default_temperature"]
    if fields.get("default_model_level") is not None:
        lvl = fields["default_model_level"]
        if LEVEL_ORDER.get(lvl, 99) > LEVEL_ORDER.get(effective_scope(acct, member), 0):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"默认等级不能超过 {effective_scope(acct, member)}")
        member.default_model_level = lvl
    db.commit()
    db.refresh(member)
    return member
