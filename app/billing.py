"""模型路由、Key 池调度与点数计费（方案第十、十二节）。"""
import math
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import ApiKeyPool, Model, WalletAccount

# 模型等级排序，用于 model_scope 权限判断
LEVEL_ORDER = {"basic": 1, "standard": 2, "advanced": 3}

# 扣费顺序（方案 10.2）
WEB_DEDUCT_ORDER = ["free_points", "subsidy_points", "paid_points"]
API_DEDUCT_ORDER = ["project_points", "subsidy_points", "free_points", "paid_points"]


def level_allowed(requested_level: str, max_scope: str) -> bool:
    return LEVEL_ORDER.get(requested_level, 99) <= LEVEL_ORDER.get(max_scope, 0)


def select_model(db: Session, model_level: str) -> Optional[Model]:
    """按用户端等级选择一个启用的模型。"""
    stmt = (
        select(Model)
        .where(Model.model_level == model_level, Model.enabled.is_(True))
        .order_by(Model.id.asc())
    )
    return db.execute(stmt).scalars().first()


def select_key(db: Session, model: Model) -> Optional[ApiKeyPool]:
    """按资源池优先级选择一个可用 Key（方案 8.x / 9.5 调度优先级）。

    优先级：先按 priority 升序，再按 school -> group -> contributed 资源池顺序。
    mock 供应商不需要真实 Key，这里仍返回其占位 Key 以统一记录。
    """
    pool_rank = {"school": 0, "group": 1, "contributed": 2}
    stmt = select(ApiKeyPool).where(
        ApiKeyPool.provider == model.provider,
        ApiKeyPool.status == "active",
    )
    candidates: List[ApiKeyPool] = list(db.execute(stmt).scalars().all())

    def supports(k: ApiKeyPool) -> bool:
        if not k.supported_models:
            return True
        names = [s.strip() for s in k.supported_models.split(",") if s.strip()]
        return model.model_name in names

    def budget_ok(k: ApiKeyPool) -> bool:
        if k.monthly_budget is not None and k.used_budget_month is not None:
            if float(k.used_budget_month) >= float(k.monthly_budget):
                return False
        if k.daily_token_limit and k.used_tokens_today is not None:
            if int(k.used_tokens_today) >= int(k.daily_token_limit):
                return False
        return True

    usable = [k for k in candidates if supports(k) and budget_ok(k)]
    usable.sort(key=lambda k: (k.priority or 0, pool_rank.get(k.resource_pool_type, 9), k.id))
    return usable[0] if usable else None


def estimate_points(multiplier: float, input_tokens: int, output_tokens: int) -> int:
    total = input_tokens + output_tokens
    raw = (total / 1000.0) * settings.base_points_per_1k_tokens * float(multiplier or 1)
    return max(1, int(math.ceil(raw)))


def estimate_cost(model: Model, input_tokens: int, output_tokens: int) -> float:
    cost = (input_tokens / 1000.0) * float(model.input_price or 0) + (
        output_tokens / 1000.0
    ) * float(model.output_price or 0)
    return round(cost, 4)


def wallet_balance(wallet: WalletAccount) -> int:
    return int(
        (wallet.free_points or 0)
        + (wallet.subsidy_points or 0)
        + (wallet.project_points or 0)
        + (wallet.paid_points or 0)
    )


def deduct_points(wallet: WalletAccount, points: int, order: List[str]) -> int:
    """按给定桶顺序扣点，返回实际扣除点数（余额不足时尽量扣，并钳制到 0）。"""
    remaining = points
    for bucket in order:
        if remaining <= 0:
            break
        available = int(getattr(wallet, bucket) or 0)
        if available <= 0:
            continue
        take = min(available, remaining)
        setattr(wallet, bucket, available - take)
        remaining -= take
    actually = points - remaining
    wallet.total_used_points = int(wallet.total_used_points or 0) + actually
    return actually
