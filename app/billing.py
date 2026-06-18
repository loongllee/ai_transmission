"""模型路由、Key 池调度与点数计费（方案第十、十二节）。"""
import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import ApiKeyPool, ContributedApiKey, Model, ResearchGroup, WalletAccount

# 模型等级排序，用于 model_scope 权限判断
LEVEL_ORDER = {"basic": 1, "standard": 2, "advanced": 3}

# 扣费顺序（方案 10.2）
# 网页聊天：免费 → 学校补贴 → 个人自购
WEB_DEDUCT_ORDER = ["free_points", "subsidy_points", "paid_points"]
# 科研 API：个人项目 → 课题组共享 → 学校补贴 → 个人免费 → 个人自购
#   （课题组共享额度在 _deduction_targets 中按 group 单独插入）


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


# --------------------------------------------------------------------------
# 钱包 / 课题组 共享额度的统一扣费（方案第十节）
# --------------------------------------------------------------------------
def get_wallet(db: Session, user_id: int) -> WalletAccount:
    wallet = db.query(WalletAccount).filter(WalletAccount.user_id == user_id).first()
    if not wallet:
        wallet = WalletAccount(user_id=user_id)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return wallet


def get_group(db: Session, group_id: Optional[int]) -> Optional[ResearchGroup]:
    if not group_id:
        return None
    return db.get(ResearchGroup, group_id)


def _deduction_targets(db: Session, user, source: str):
    """构造有序扣费目标。

    每个目标是 ('wallet', bucket名) 或 ('group', ResearchGroup 对象)。
    """
    wallet = get_wallet(db, user.id)
    group = get_group(db, getattr(user, "group_id", None))
    if source == "web":
        targets: List[Tuple[str, object]] = [("wallet", b) for b in WEB_DEDUCT_ORDER]
    else:  # api / job：个人项目 → 课题组共享 → 学校补贴 → 个人免费 → 个人自购
        targets = [("wallet", "project_points")]
        if group and group.status == "active":
            targets.append(("group", group))
        targets += [("wallet", "subsidy_points"), ("wallet", "free_points"), ("wallet", "paid_points")]
    return wallet, group, targets


def available_balance(db: Session, user, source: str) -> int:
    """可用余额（API 来源含个人项目额度与课题组共享额度）。"""
    _wallet, _group, targets = _deduction_targets(db, user, source)
    total = 0
    for kind, ref in targets:
        if kind == "wallet":
            total += int(getattr(_wallet, ref) or 0)
        else:
            total += int(ref.project_points or 0)
    return total


def charge(db: Session, user, points: int, source: str) -> dict:
    """按扣费顺序扣点，返回 {charged, from_group, balance_after}。余额不足时尽量扣。"""
    wallet, _group, targets = _deduction_targets(db, user, source)
    remaining = points
    from_group = 0
    for kind, ref in targets:
        if remaining <= 0:
            break
        if kind == "wallet":
            avail = int(getattr(wallet, ref) or 0)
            take = min(avail, remaining)
            if take:
                setattr(wallet, ref, avail - take)
                remaining -= take
        else:  # group
            avail = int(ref.project_points or 0)
            take = min(avail, remaining)
            if take:
                ref.project_points = avail - take
                ref.total_used_points = int(ref.total_used_points or 0) + take
                from_group += take
                remaining -= take
    charged = points - remaining
    wallet.total_used_points = int(wallet.total_used_points or 0) + (charged - from_group)
    return {
        "charged": charged,
        "from_group": from_group,
        "balance_after": available_balance(db, user, source),
    }


# --------------------------------------------------------------------------
# 统一 Key 句柄：兼容学校/课题组 Key 池与学生贡献备用账号（方案 8.3 / 9.5）
# --------------------------------------------------------------------------
@dataclass
class KeyHandle:
    kind: str  # "pool" | "contributed"
    id: int
    provider: str
    base_url: Optional[str]
    encrypted_api_key: str
    obj: object  # 原始 ORM 对象，便于记账时更新用量


def _contributed_candidates(db: Session, model: Model) -> List[ContributedApiKey]:
    now = datetime.utcnow()
    rows = (
        db.query(ContributedApiKey)
        .filter(
            ContributedApiKey.provider == model.provider,
            ContributedApiKey.status == "active",
            ContributedApiKey.revoked_at.is_(None),
        )
        .order_by(ContributedApiKey.id.asc())
        .all()
    )

    def usable(c: ContributedApiKey) -> bool:
        if c.expires_at and c.expires_at < now:
            return False
        # 允许的模型等级（为空时默认仅 basic）
        levels = [s.strip() for s in (c.allowed_model_levels or "basic").split(",") if s.strip()]
        if model.model_level not in levels:
            return False
        # 每日/每月消耗上限（方案 9.4 额度受限）
        if c.daily_cost_limit is not None and float(c.used_cost_today or 0) >= float(c.daily_cost_limit):
            return False
        if c.monthly_cost_limit is not None and float(c.used_cost_month or 0) >= float(c.monthly_cost_limit):
            return False
        return True

    return [c for c in rows if usable(c)]


def resolve_key(
    db: Session,
    model: Model,
    *,
    allow_contributed: bool = False,
    sensitive: bool = False,
) -> Optional[KeyHandle]:
    """按 学校/课题组 → 学生贡献备用 的优先级解析一个可用 Key（方案 9.5）。

    贡献备用池只在主资源池无可用 Key、且任务为低风险（非敏感）时降级启用。
    """
    pool_key = select_key(db, model)
    if pool_key:
        return KeyHandle(
            kind="pool",
            id=pool_key.id,
            provider=pool_key.provider,
            base_url=pool_key.base_url,
            encrypted_api_key=pool_key.encrypted_api_key,
            obj=pool_key,
        )
    if allow_contributed and settings.allow_contributed_pool and not sensitive:
        cands = _contributed_candidates(db, model)
        if cands:
            c = cands[0]
            return KeyHandle(
                kind="contributed",
                id=c.id,
                provider=c.provider,
                base_url=None,
                encrypted_api_key=c.encrypted_api_key,
                obj=c,
            )
    return None
