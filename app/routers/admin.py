"""管理员后台接口（方案第十五节）。所有接口需 admin 角色。

真实供应商 Key 只写不读：列表/详情绝不返回明文或密文。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from datetime import datetime

from sqlalchemy import func

from .. import store
from ..database import get_db
from ..deps import require_admin
from ..models import (
    Alert,
    ApiKeyPool,
    BillingRecord,
    ContributedApiKey,
    Job,
    Model,
    Order,
    Package,
    ResearchGroup,
    UsageLog,
    User,
    UserApiToken,
    WalletAccount,
)
from ..schemas import (
    AddMemberRequest,
    AlertOut,
    CompensationOut,
    ContributionOut,
    GrantPointsRequest,
    GroupGrantRequest,
    GroupIn,
    GroupOut,
    GroupStatsOut,
    KeyIn,
    KeyOut,
    ModelIn,
    ModelOut,
    OrderOut,
    PackageIn,
    PackageOut,
    UpdateUserRequest,
    UsageLogOut,
    UserOut,
)
from ..security import encrypt_secret

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ---------- 用户管理 ----------
@router.get("/users", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).order_by(User.id.asc()).all()


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UpdateUserRequest, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if payload.role is not None:
        user.role = payload.role
    if payload.status is not None:
        user.status = payload.status
    if payload.group_id is not None:
        user.group_id = payload.group_id
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/grant", response_model=dict)
def grant_points(user_id: int, payload: GrantPointsRequest, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    wallet = db.query(WalletAccount).filter(WalletAccount.user_id == user_id).first()
    if not wallet:
        wallet = WalletAccount(user_id=user_id)
        db.add(wallet)
        db.flush()
    field = {
        "free": "free_points",
        "paid": "paid_points",
        "subsidy": "subsidy_points",
        "project": "project_points",
    }.get(payload.bucket)
    if not field:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "无效的额度类型")
    setattr(wallet, field, int(getattr(wallet, field) or 0) + int(payload.points))
    db.commit()
    return {"ok": True, "user_id": user_id, "bucket": payload.bucket, "added": payload.points}


# ---------- 模型管理 ----------
@router.get("/models", response_model=List[ModelOut])
def list_models(db: Session = Depends(get_db)):
    return db.query(Model).order_by(Model.id.asc()).all()


@router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
def create_model(payload: ModelIn, db: Session = Depends(get_db)):
    model = Model(**payload.model_dump())
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


@router.patch("/models/{model_id}", response_model=ModelOut)
def update_model(model_id: int, payload: ModelIn, db: Session = Depends(get_db)):
    model = db.get(Model, model_id)
    if not model:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型不存在")
    for k, v in payload.model_dump().items():
        setattr(model, k, v)
    db.commit()
    db.refresh(model)
    return model


# ---------- Key 池管理 ----------
@router.get("/keys", response_model=List[KeyOut])
def list_keys(db: Session = Depends(get_db)):
    return db.query(ApiKeyPool).order_by(ApiKeyPool.priority.asc(), ApiKeyPool.id.asc()).all()


@router.post("/keys", response_model=KeyOut, status_code=status.HTTP_201_CREATED)
def create_key(payload: KeyIn, db: Session = Depends(get_db)):
    key = ApiKeyPool(
        resource_pool_type=payload.resource_pool_type,
        provider=payload.provider,
        account_name=payload.account_name,
        base_url=payload.base_url,
        encrypted_api_key=encrypt_secret(payload.api_key),
        supported_models=payload.supported_models,
        status=payload.status,
        priority=payload.priority,
        monthly_budget=payload.monthly_budget,
        daily_token_limit=payload.daily_token_limit,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return key


@router.post("/keys/{key_id}/disable", response_model=KeyOut)
def disable_key(key_id: int, db: Session = Depends(get_db)):
    key = db.get(ApiKeyPool, key_id)
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key 不存在")
    key.status = "disabled"
    db.commit()
    db.refresh(key)
    return key


@router.delete("/keys/{key_id}", response_model=dict)
def delete_key(key_id: int, db: Session = Depends(get_db)):
    key = db.get(ApiKeyPool, key_id)
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key 不存在")
    db.delete(key)
    db.commit()
    return {"ok": True, "deleted": key_id}


# ---------- 调用日志 ----------
@router.get("/logs", response_model=List[UsageLogOut])
def list_logs(
    limit: int = 100,
    user_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    q = db.query(UsageLog)
    if user_id:
        q = q.filter(UsageLog.user_id == user_id)
    if status_filter:
        q = q.filter(UsageLog.status == status_filter)
    return q.order_by(UsageLog.id.desc()).limit(limit).all()


# ---------- Token 封禁（异常 API 调用封禁，方案第二十节）----------
@router.post("/tokens/{token_id}/disable", response_model=dict)
def admin_disable_token(token_id: int, db: Session = Depends(get_db)):
    token = db.get(UserApiToken, token_id)
    if not token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token 不存在")
    token.status = "disabled"
    db.commit()
    return {"ok": True, "token_id": token_id, "status": "disabled"}


# ---------- 课题组 / 项目额度（方案第八、十节）----------
@router.get("/groups", response_model=List[GroupOut])
def list_groups(db: Session = Depends(get_db)):
    return db.query(ResearchGroup).order_by(ResearchGroup.id.asc()).all()


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupIn, db: Session = Depends(get_db)):
    if db.query(ResearchGroup).filter(ResearchGroup.name == payload.name).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "课题组名称已存在")
    group = ResearchGroup(
        name=payload.name,
        owner_user_id=payload.owner_user_id,
        project_points=payload.project_points,
        status="active",
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.post("/groups/{group_id}/grant", response_model=GroupOut)
def grant_group(group_id: int, payload: GroupGrantRequest, db: Session = Depends(get_db)):
    group = db.get(ResearchGroup, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "课题组不存在")
    group.project_points = int(group.project_points or 0) + int(payload.points)
    db.commit()
    db.refresh(group)
    return group


@router.post("/groups/{group_id}/members", response_model=dict)
def add_group_member(group_id: int, payload: AddMemberRequest, db: Session = Depends(get_db)):
    group = db.get(ResearchGroup, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "课题组不存在")
    user = db.get(User, payload.user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    user.group_id = group_id
    db.commit()
    return {"ok": True, "group_id": group_id, "user_id": payload.user_id}


@router.get("/groups/{group_id}/stats", response_model=GroupStatsOut)
def group_stats(group_id: int, db: Session = Depends(get_db)):
    group = db.get(ResearchGroup, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "课题组不存在")
    members = db.query(User).filter(User.group_id == group_id).count()
    total_calls = db.query(UsageLog).filter(UsageLog.group_id == group_id).count()
    total_tokens = int(
        db.query(func.coalesce(func.sum(UsageLog.input_tokens + UsageLog.output_tokens), 0))
        .filter(UsageLog.group_id == group_id)
        .scalar()
        or 0
    )
    return GroupStatsOut(
        group_id=group_id,
        name=group.name,
        members=members,
        project_points_remaining=int(group.project_points or 0),
        total_used_points=int(group.total_used_points or 0),
        total_calls=total_calls,
        total_tokens=total_tokens,
    )


# ---------- 异常告警（方案第十五节）----------
@router.get("/alerts", response_model=List[AlertOut])
def list_alerts(
    limit: int = 100,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    q = db.query(Alert)
    if status_filter:
        q = q.filter(Alert.status == status_filter)
    return q.order_by(Alert.id.desc()).limit(limit).all()


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "告警不存在")
    alert.status = "resolved"
    alert.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(alert)
    return alert


# ---------- 套餐管理（第三阶段）----------
@router.get("/packages", response_model=List[PackageOut])
def admin_list_packages(db: Session = Depends(get_db)):
    return db.query(Package).order_by(Package.sort.asc(), Package.id.asc()).all()


@router.post("/packages", response_model=PackageOut, status_code=status.HTTP_201_CREATED)
def admin_create_package(payload: PackageIn, db: Session = Depends(get_db)):
    if db.query(Package).filter(Package.code == payload.code).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "套餐 code 已存在")
    pkg = Package(**payload.model_dump())
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    return pkg


# ---------- 充值订单管理（第三阶段）----------
@router.get("/orders", response_model=List[OrderOut])
def admin_list_orders(
    limit: int = 100,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    q = db.query(Order)
    if status_filter:
        q = q.filter(Order.status == status_filter)
    return q.order_by(Order.id.desc()).limit(limit).all()


@router.post("/orders/{order_id}/confirm", response_model=OrderOut)
def admin_confirm_order(order_id: int, db: Session = Depends(get_db)):
    """学校财务确认收款后入账。"""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    return store.pay_order(db, order, "school_finance")


@router.post("/orders/{order_id}/refund", response_model=OrderOut)
def admin_refund_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    return store.refund_order(db, order)


# ---------- 付费意愿统计（方案 11.3，匿名化汇总）----------
@router.get("/stats/payment", response_model=dict)
def payment_stats(db: Session = Depends(get_db)):
    return store.payment_stats(db)


# ---------- 学生贡献账号管理与补偿（方案第九节）----------
@router.get("/contributions", response_model=List[ContributionOut])
def admin_list_contributions(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(ContributedApiKey)
    if status_filter:
        q = q.filter(ContributedApiKey.status == status_filter)
    return q.order_by(ContributedApiKey.id.desc()).all()


@router.post("/contributions/{cid}/disable", response_model=ContributionOut)
def admin_disable_contribution(cid: int, db: Session = Depends(get_db)):
    c = db.get(ContributedApiKey, cid)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "贡献记录不存在")
    c.status = "disabled"
    db.commit()
    db.refresh(c)
    return c


@router.get("/compensation", response_model=List[CompensationOut])
def admin_compensation(db: Session = Depends(get_db)):
    return store.compensation_stats(db)


# ---------- 简单统计 ----------
@router.get("/stats", response_model=dict)
def stats(db: Session = Depends(get_db)):
    return {
        "total_users": db.query(User).count(),
        "total_calls": db.query(UsageLog).count(),
        "total_billing_records": db.query(BillingRecord).count(),
        "total_jobs": db.query(Job).count(),
        "open_alerts": db.query(Alert).filter(Alert.status == "open").count(),
        "groups": db.query(ResearchGroup).count(),
        "paid_orders": db.query(Order).filter(Order.status == "paid").count(),
        "contributions": db.query(ContributedApiKey).filter(ContributedApiKey.status == "active").count(),
    }
