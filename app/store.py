"""第三阶段服务：套餐充值、学生贡献账号、补偿与付费意愿统计。

方案第九节（贡献与补偿）、第十一节（自愿购买）、11.3（付费意愿统计）。
"""
import uuid
from datetime import datetime, timedelta
from typing import List

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import billing
from .config import settings
from .models import ContributedApiKey, Order, Package, User, WalletAccount
from .security import encrypt_secret


# ----------------------------- 套餐 / 订单 -----------------------------
def _order_no() -> str:
    return "ORD" + uuid.uuid4().hex[:18].upper()


def create_order(db: Session, user: User, package_code: str) -> Order:
    pkg = db.query(Package).filter(Package.code == package_code, Package.enabled.is_(True)).first()
    if not pkg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "套餐不存在或已下架")
    if pkg.application_only:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该套餐为申请制，请联系管理员开通")
    if float(pkg.price or 0) <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "免费额度无需购买")
    order = Order(
        order_no=_order_no(),
        user_id=user.id,
        package_id=pkg.id,
        package_code=pkg.code,
        amount=pkg.price,
        points=pkg.points,
        status="pending",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def pay_order(db: Session, order: Order, pay_channel: str, external_ref: str = None) -> Order:
    """确认支付并入账（方案 11.1：真实支付走学校正规财务渠道，这里记录渠道与流水号）。"""
    if order.status == "paid":
        raise HTTPException(status.HTTP_409_CONFLICT, "订单已支付")
    if order.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"订单状态为 {order.status}，无法支付")
    order.status = "paid"
    order.pay_channel = pay_channel or "mock"
    order.external_ref = external_ref
    order.paid_at = datetime.utcnow()
    # 入账到个人自购额度
    wallet = billing.get_wallet(db, order.user_id)
    wallet.paid_points = int(wallet.paid_points or 0) + int(order.points or 0)
    db.commit()
    db.refresh(order)
    return order


def refund_order(db: Session, order: Order) -> Order:
    if order.status != "paid":
        raise HTTPException(status.HTTP_409_CONFLICT, "仅已支付订单可退款")
    order.status = "refunded"
    order.refunded_at = datetime.utcnow()
    # 从自购额度中扣回（钳制到 0）
    wallet = billing.get_wallet(db, order.user_id)
    wallet.paid_points = max(0, int(wallet.paid_points or 0) - int(order.points or 0))
    db.commit()
    db.refresh(order)
    return order


# ----------------------------- 学生贡献账号 -----------------------------
def create_contribution(db: Session, user: User, payload) -> ContributedApiKey:
    # 方案 9.1：知情同意为前提
    if not getattr(payload, "consent", False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "需勾选知情同意（自愿、可撤回、受补偿）后方可提交")
    c = ContributedApiKey(
        contributor_user_id=user.id,
        provider=payload.provider or "mock",
        encrypted_api_key=encrypt_secret(payload.api_key),
        allowed_model_levels=payload.allowed_model_levels or "basic",
        daily_cost_limit=payload.daily_cost_limit
        if payload.daily_cost_limit is not None
        else settings.contribution_default_daily_cost_limit,
        monthly_cost_limit=payload.monthly_cost_limit
        if payload.monthly_cost_limit is not None
        else settings.contribution_default_monthly_cost_limit,
        allowed_task_types=payload.allowed_task_types,
        allow_sensitive_data=bool(payload.allow_sensitive_data),
        status="active",
        consent_version=settings.consent_version,
        consent_time=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=180),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def revoke_contribution(db: Session, c: ContributedApiKey) -> ContributedApiKey:
    if c.status == "revoked":
        return c
    c.status = "revoked"
    c.revoked_at = datetime.utcnow()
    db.commit()
    db.refresh(c)
    return c


# ----------------------------- 统计 -----------------------------
def compensation_stats(db: Session) -> List[dict]:
    """按贡献者汇总实际消耗与建议补偿（方案 9.3：实际消耗 + 参与试点补贴）。"""
    rows = db.query(ContributedApiKey).all()
    by_user = {}
    for c in rows:
        agg = by_user.setdefault(
            c.contributor_user_id, {"accounts": 0, "total_cost": 0.0}
        )
        agg["accounts"] += 1
        agg["total_cost"] += float(c.used_cost_month or 0)
    out = []
    for uid, agg in by_user.items():
        user = db.get(User, uid)
        subsidy = settings.pilot_subsidy_per_contributor
        out.append(
            {
                "contributor_user_id": uid,
                "username": user.username if user else None,
                "accounts": agg["accounts"],
                "total_cost": round(agg["total_cost"], 4),
                "pilot_subsidy": subsidy,
                "suggested_compensation": round(agg["total_cost"] + subsidy, 4),
            }
        )
    out.sort(key=lambda x: x["contributor_user_id"])
    return out


def payment_stats(db: Session) -> dict:
    """付费意愿统计（方案 11.3）。匿名化、汇总化，不含个人排名/对话内容。"""
    total_users = db.query(User).count()
    free_exhausted = (
        db.query(WalletAccount).filter(WalletAccount.free_points <= 0).count()
    )
    paid_orders = db.query(Order).filter(Order.status == "paid").all()
    purchaser_ids = set(o.user_id for o in paid_orders)
    purchasers = len(purchaser_ids)
    total_revenue = round(sum(float(o.amount or 0) for o in paid_orders), 2)
    orders_per_user = {}
    for o in paid_orders:
        orders_per_user[o.user_id] = orders_per_user.get(o.user_id, 0) + 1
    repurchasers = sum(1 for v in orders_per_user.values() if v >= 2)
    package_dist = {}
    for o in paid_orders:
        package_dist[o.package_code] = package_dist.get(o.package_code, 0) + 1
    return {
        "total_users": total_users,
        "free_exhausted_users": free_exhausted,
        "purchasers": purchasers,
        "conversion_rate": round(purchasers / total_users, 4) if total_users else 0,
        "paid_orders": len(paid_orders),
        "total_revenue": total_revenue,
        "avg_purchase_amount": round(total_revenue / purchasers, 2) if purchasers else 0,
        "repurchasers": repurchasers,
        "package_distribution": package_dist,
    }
