"""网页端接口（JWT 会话）：个人信息、钱包、额度、Token 管理、用量、聊天。"""
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import billing, chat
from .. import jobs as jobs_service
from .. import shared as shared_service
from .. import store
from ..database import get_db
from ..deps import Principal, get_current_user, web_principal
from ..models import (
    ContributedApiKey,
    Job,
    JobItem,
    Model,
    Order,
    Package,
    SharedAccount,
    SharedCall,
    User,
    UserApiToken,
    UsageLog,
    WalletAccount,
)
from ..schemas import (
    AddSharedMemberRequest,
    ApiTokenCreatedOut,
    ApiTokenOut,
    ChatRequest,
    ChatResponse,
    ContributionIn,
    ContributionOut,
    CreateOrderRequest,
    CreateSharedRequest,
    CreateTokenRequest,
    JobCreateRequest,
    JobOut,
    JobResultOut,
    OrderOut,
    PackageOut,
    PayOrderRequest,
    QuotaOut,
    SharedAccountOut,
    SharedCallOut,
    SharedMemberCreatedOut,
    SharedMemberOut,
    UpdateSharedAccountRequest,
    UpdateSharedMemberRequest,
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
    return QuotaOut(
        role=principal.user.role,
        balance=billing.available_balance(db, principal.user, "web"),
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
    base = ApiTokenOut.model_validate(token)
    return ApiTokenCreatedOut(**base.model_dump(), plaintext_token=plaintext)


@router.post("/tokens/{token_id}/reset", response_model=ApiTokenCreatedOut)
def reset_token(token_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = _owned_token(db, user, token_id)
    plaintext, token_hash, display = generate_api_token()
    token.token_hash = token_hash
    token.token_prefix = display
    token.status = "active"
    db.commit()
    db.refresh(token)
    base = ApiTokenOut.model_validate(token)
    return ApiTokenCreatedOut(**base.model_dump(), plaintext_token=plaintext)


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


# ---------- 批量任务（网页端，第二阶段）----------
@router.get("/jobs", response_model=List[JobOut])
def web_list_jobs(limit: int = 50, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    limit = max(1, min(limit, 200))
    return (
        db.query(Job).filter(Job.user_id == user.id).order_by(Job.id.desc()).limit(limit).all()
    )


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def web_create_job(
    payload: JobCreateRequest,
    principal: Principal = Depends(web_principal),
    db: Session = Depends(get_db),
):
    if not principal.allow_batch:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前角色无批量任务权限（需课题组成员及以上）")
    items = [it.model_dump() for it in payload.items]
    return jobs_service.create_job(
        db,
        user=principal.user,
        token=None,
        source="web",
        model_scope=principal.model_scope,
        job_type=payload.job_type,
        model_level=payload.model_level,
        task_type=payload.task_type,
        items=items,
        max_tokens=payload.max_tokens or 256,
        auto_confirm=True,  # 网页端提交即确认入队
    )


@router.get("/jobs/{job_id}/results", response_model=JobResultOut)
def web_job_results(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    items = db.query(JobItem).filter(JobItem.job_id == job.id).order_by(JobItem.seq.asc()).all()
    return {"job": job, "items": items}


# ---------- 套餐 / 充值订单（第三阶段，方案第十一节）----------
@router.get("/packages", response_model=List[PackageOut])
def list_packages(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return (
        db.query(Package)
        .filter(Package.enabled.is_(True))
        .order_by(Package.sort.asc(), Package.id.asc())
        .all()
    )


@router.get("/orders", response_model=List[OrderOut])
def list_orders(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Order).filter(Order.user_id == user.id).order_by(Order.id.desc()).all()


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(payload: CreateOrderRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return store.create_order(db, user, payload.package_code)


@router.post("/orders/{order_id}/pay", response_model=OrderOut)
def pay_order(
    order_id: int,
    payload: PayOrderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """模拟支付（试点演示）。真实支付应由学校财务渠道回调触发。"""
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    return store.pay_order(db, order, payload.pay_channel, payload.external_ref)


# ---------- 学生自愿贡献账号（第三阶段，方案第九节）----------
@router.get("/contributions", response_model=List[ContributionOut])
def list_contributions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(ContributedApiKey)
        .filter(ContributedApiKey.contributor_user_id == user.id)
        .order_by(ContributedApiKey.id.desc())
        .all()
    )


@router.post("/contributions", response_model=ContributionOut, status_code=status.HTTP_201_CREATED)
def create_contribution(payload: ContributionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return store.create_contribution(db, user, payload)


@router.post("/contributions/{cid}/revoke", response_model=ContributionOut)
def revoke_contribution(cid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.get(ContributedApiKey, cid)
    if not c or c.contributor_user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "贡献记录不存在")
    return store.revoke_contribution(db, c)


# ---------- 共享账户（拥有者管理侧）----------
@router.get("/shared", response_model=List[SharedAccountOut])
def list_shared(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(SharedAccount)
        .filter(SharedAccount.owner_user_id == user.id)
        .order_by(SharedAccount.id.desc())
        .all()
    )


@router.post("/shared", response_model=SharedAccountOut, status_code=status.HTTP_201_CREATED)
def create_shared(payload: CreateSharedRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return shared_service.create_account(
        db, user, payload.name,
        payload.rate_limit_per_minute, payload.max_concurrency, payload.daily_request_limit,
        payload.model_scope, payload.restrict_members,
        payload.daily_token_limit, payload.default_member_rpm, payload.default_member_daily,
    )


@router.patch("/shared/{account_id}", response_model=SharedAccountOut)
def update_shared(account_id: int, payload: UpdateSharedAccountRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acct = shared_service.owned_account(db, user.id, account_id)
    return shared_service.update_account(db, acct, payload.model_dump(exclude_unset=True))


@router.get("/shared/{account_id}/members", response_model=List[SharedMemberOut])
def list_shared_members(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acct = shared_service.owned_account(db, user.id, account_id)
    return shared_service.list_members(db, acct)


@router.post("/shared/{account_id}/members", response_model=SharedMemberCreatedOut, status_code=status.HTTP_201_CREATED)
def add_shared_member(
    account_id: int,
    payload: AddSharedMemberRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    acct = shared_service.owned_account(db, user.id, account_id)
    member, plaintext = shared_service.add_member(
        db, acct, payload.member_label,
        display_name=payload.display_name, rpm_limit=payload.rpm_limit,
        daily_request_limit=payload.daily_request_limit, token_limit=payload.token_limit,
        model_scope=payload.model_scope,
    )
    base = SharedMemberOut.model_validate(member)
    return SharedMemberCreatedOut(**base.model_dump(), plaintext_token=plaintext)


@router.patch("/shared/{account_id}/members/{label}", response_model=SharedMemberOut)
def update_shared_member(
    account_id: int, label: str, payload: UpdateSharedMemberRequest,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    acct = shared_service.owned_account(db, user.id, account_id)
    return shared_service.update_member(db, acct, label, payload.model_dump(exclude_unset=True))


@router.post("/shared/{account_id}/members/{label}/token", response_model=SharedMemberCreatedOut)
def rotate_shared_member_token(account_id: int, label: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acct = shared_service.owned_account(db, user.id, account_id)
    member = shared_service._member_by_label(db, acct, label)
    member, plaintext = shared_service.rotate_member_token(db, member)
    base = SharedMemberOut.model_validate(member)
    return SharedMemberCreatedOut(**base.model_dump(), plaintext_token=plaintext)


# 拥有者可查看成员数据（含对话原文）——请在产品隐私政策中向成员明示
@router.get("/shared/{account_id}/members/{label}/history", response_model=List[SharedCallOut])
def member_history_for_owner(account_id: int, label: str, limit: int = 100, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acct = shared_service.owned_account(db, user.id, account_id)
    member = shared_service._member_by_label(db, acct, label)
    limit = max(1, min(limit, 500))
    return (
        db.query(SharedCall)
        .filter(SharedCall.shared_account_id == acct.id, SharedCall.member_id == member.id)
        .order_by(SharedCall.id.desc())
        .limit(limit)
        .all()
    )


@router.get("/shared/{account_id}/history", response_model=List[SharedCallOut])
def account_history_for_owner(account_id: int, limit: int = 200, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acct = shared_service.owned_account(db, user.id, account_id)
    limit = max(1, min(limit, 1000))
    return (
        db.query(SharedCall)
        .filter(SharedCall.shared_account_id == acct.id)
        .order_by(SharedCall.id.desc())
        .limit(limit)
        .all()
    )
