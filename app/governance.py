"""学校级治理：预算熔断、操作审计、统计报表（方案第四阶段）。"""
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import org
from .models import (
    AuditLog,
    BillingRecord,
    Budget,
    ContributedApiKey,
    Order,
    ResearchGroup,
    UsageLog,
    User,
)


# ----------------------------- 预算熔断 -----------------------------
def get_active_school_budget(db: Session) -> Optional[Budget]:
    return (
        db.query(Budget)
        .filter(Budget.scope == "school", Budget.status.in_(("active", "tripped")))
        .order_by(Budget.id.desc())
        .first()
    )


def budget_blocked(db: Session) -> Optional[Budget]:
    """若学校预算已熔断（tripped 或用量达上限）返回该 Budget，否则 None。"""
    b = get_active_school_budget(db)
    if not b:
        return None
    if b.status == "tripped":
        return b
    if int(b.used_points or 0) >= int(b.limit_points or 0) > 0:
        b.status = "tripped"
        db.commit()
        return b
    return None


def consume_budget(db: Session, points: int) -> None:
    """成功调用后累加学校预算用量，达上限即熔断。"""
    b = get_active_school_budget(db)
    if not b:
        return
    b.used_points = int(b.used_points or 0) + int(points or 0)
    if int(b.used_points) >= int(b.limit_points or 0) > 0:
        b.status = "tripped"
    db.commit()


def set_school_budget(db: Session, limit_points: int, period_key: str = None, note: str = None) -> Budget:
    """新建/重置学校预算（保留历史，置旧的为 disabled）。"""
    for old in db.query(Budget).filter(Budget.scope == "school", Budget.status.in_(("active", "tripped"))).all():
        old.status = "disabled"
    b = Budget(
        scope="school",
        period_key=period_key,
        limit_points=int(limit_points),
        used_points=0,
        status="active",
        note=note,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def reset_budget(db: Session, budget: Budget) -> Budget:
    budget.used_points = 0
    budget.status = "active"
    db.commit()
    db.refresh(budget)
    return budget


# ----------------------------- 操作审计 -----------------------------
def audit(
    db: Session,
    actor: Optional[User],
    action: str,
    target_type: str = None,
    target_id=None,
    detail: str = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor.id if actor else None,
            actor_username=actor.username if actor else "system",
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            detail=detail,
            created_at=datetime.utcnow(),
        )
    )
    db.commit()


def list_audit(db: Session, limit: int = 200) -> List[AuditLog]:
    limit = max(1, min(limit, 1000))
    return db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()


# ----------------------------- 统计报表 -----------------------------
def overview(db: Session, days: Optional[int] = None) -> dict:
    q = db.query(UsageLog)
    if days:
        since = datetime.utcnow() - timedelta(days=days)
        q = q.filter(UsageLog.created_at >= since)
    logs_count = q.count()
    tokens = int(
        q.with_entities(func.coalesce(func.sum(UsageLog.input_tokens + UsageLog.output_tokens), 0)).scalar() or 0
    )
    cost = float(
        q.with_entities(func.coalesce(func.sum(UsageLog.estimated_cost), 0)).scalar() or 0
    )

    def group_count(col):
        rows = q.with_entities(col, func.count()).group_by(col).all()
        return {str(k or "unknown"): int(v) for k, v in rows}

    paid_orders = db.query(Order).filter(Order.status == "paid")
    budget = get_active_school_budget(db)
    return {
        "period_days": days,
        "total_users": db.query(User).count(),
        "total_calls": logs_count,
        "total_tokens": tokens,
        "total_cost": round(cost, 4),
        "total_points_used": int(
            db.query(func.coalesce(func.sum(BillingRecord.points_used), 0)).scalar() or 0
        ),
        "calls_by_model_level": group_count(UsageLog.model_level),
        "calls_by_source": group_count(UsageLog.source),
        "groups": db.query(ResearchGroup).count(),
        "paid_orders": paid_orders.count(),
        "revenue": round(float(paid_orders.with_entities(func.coalesce(func.sum(Order.amount), 0)).scalar() or 0), 2),
        "active_contributions": db.query(ContributedApiKey).filter(ContributedApiKey.status == "active").count(),
        "school_budget": None
        if not budget
        else {
            "limit_points": int(budget.limit_points or 0),
            "used_points": int(budget.used_points or 0),
            "status": budget.status,
            "remaining_points": max(0, int(budget.limit_points or 0) - int(budget.used_points or 0)),
        },
    }


def by_org(db: Session) -> List[dict]:
    return org.rollup_stats(db)
