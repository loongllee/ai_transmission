"""管理员后台接口（方案第十五节）。所有接口需 admin 角色。

真实供应商 Key 只写不读：列表/详情绝不返回明文或密文。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import ApiKeyPool, BillingRecord, Model, UsageLog, User, WalletAccount
from ..schemas import (
    GrantPointsRequest,
    KeyIn,
    KeyOut,
    ModelIn,
    ModelOut,
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


# ---------- 简单统计 ----------
@router.get("/stats", response_model=dict)
def stats(db: Session = Depends(get_db)):
    total_users = db.query(User).count()
    total_calls = db.query(UsageLog).count()
    total_points = db.query(BillingRecord).count()
    return {
        "total_users": total_users,
        "total_calls": total_calls,
        "total_billing_records": total_points,
    }
