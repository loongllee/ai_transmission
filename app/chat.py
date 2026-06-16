"""聊天调用编排：限流 → 路由 → 余额检查 → 调用供应商 → 扣费 → 记账 → 审计。

被网页聊天(/api/web/chat)与科研 API(/api/v1/llm/*)共用。
"""
import time
import uuid
from datetime import date
from typing import Dict, List

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import billing, ratelimit
from .deps import Principal
from .models import BillingRecord, UsageLog, WalletAccount
from .providers import estimate_messages_tokens, get_provider
from .security import decrypt_secret


def _today_token_usage(db: Session, user_id: int) -> int:
    stmt = select(func.coalesce(func.sum(UsageLog.input_tokens + UsageLog.output_tokens), 0)).where(
        UsageLog.user_id == user_id,
        func.date(UsageLog.created_at) == date.today().isoformat(),
    )
    return int(db.execute(stmt).scalar() or 0)


def _get_wallet(db: Session, user_id: int) -> WalletAccount:
    wallet = db.query(WalletAccount).filter(WalletAccount.user_id == user_id).first()
    if not wallet:
        wallet = WalletAccount(user_id=user_id)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return wallet


async def run_chat(
    db: Session,
    principal: Principal,
    model_level: str,
    task_type: str,
    messages: List[Dict[str, str]],
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> dict:
    user = principal.user

    # 1) 权限：模型等级是否在 token/角色 scope 内
    if not billing.level_allowed(model_level, principal.model_scope):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"当前权限最高可用 {principal.model_scope} 模型，无法调用 {model_level}",
        )

    # 2) 限流（每分钟 + 每日请求数）
    if not ratelimit.check_and_incr_minute(principal.limit_key, principal.rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "请求过于频繁（每分钟限流）")
    if not ratelimit.check_and_incr_daily(principal.limit_key, principal.daily_request_limit):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "今日调用次数已达上限")

    # 3) 每日 token 限额（预估）
    est_input = estimate_messages_tokens(messages)
    if principal.daily_token_limit and _today_token_usage(db, user.id) + est_input > principal.daily_token_limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "今日 Token 用量已达上限")

    # 4) 选模型 + 选 Key
    model = billing.select_model(db, model_level)
    if not model:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"暂无可用的 {model_level} 模型")
    key = billing.select_key(db, model)
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "暂无可用的供应商 Key（资源池为空或额度用尽）")

    # 5) 余额预检查
    wallet = _get_wallet(db, user.id)
    est_points = billing.estimate_points(model.multiplier, est_input, max_tokens or 0)
    if billing.wallet_balance(wallet) < est_points:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"点数余额不足：预计需 {est_points} 点，当前余额 {billing.wallet_balance(wallet)} 点",
        )

    # 6) 调用真实/模拟供应商
    api_key_plain = ""
    if key.encrypted_api_key:
        try:
            api_key_plain = decrypt_secret(key.encrypted_api_key)
        except Exception:
            api_key_plain = ""
    provider = get_provider(model.provider, key.base_url, api_key_plain)

    started = time.time()
    request_id = uuid.uuid4().hex
    try:
        result = await provider.chat(model.model_name, messages, max_tokens or 512, temperature or 0.7)
    except httpx.HTTPStatusError as exc:
        _log_error(db, principal, model, key, task_type, est_input, exc.response.status_code, started)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"供应商返回错误：{exc.response.status_code}")
    except Exception as exc:  # noqa: BLE001
        _log_error(db, principal, model, key, task_type, est_input, "provider_error", started)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"供应商调用失败：{exc}")

    latency_ms = int((time.time() - started) * 1000)

    # 7) 实际扣费
    points = billing.estimate_points(model.multiplier, result.input_tokens, result.output_tokens)
    order = billing.WEB_DEDUCT_ORDER if principal.source == "web" else billing.API_DEDUCT_ORDER
    billing.deduct_points(wallet, points, order)
    balance_after = billing.wallet_balance(wallet)
    cost = billing.estimate_cost(model, result.input_tokens, result.output_tokens)

    # 8) 更新 Key 用量
    key.used_tokens_today = int(key.used_tokens_today or 0) + result.input_tokens + result.output_tokens
    if key.monthly_budget is not None:
        key.used_budget_month = float(key.used_budget_month or 0) + cost

    # 9) 记账 + 审计
    db.add(
        BillingRecord(
            user_id=user.id,
            request_id=request_id,
            model_level=model_level,
            model_name=model.model_name,
            task_type=task_type,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            points_used=points,
            estimated_cost=cost,
            balance_after=balance_after,
        )
    )
    db.add(
        UsageLog(
            user_id=user.id,
            group_id=user.group_id,
            token_id=principal.token.id if principal.token else None,
            source=principal.source,
            provider=model.provider,
            model_level=model_level,
            model_name=model.model_name,
            key_id=key.id,
            task_type=task_type,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost=cost,
            latency_ms=latency_ms,
            status="success",
        )
    )
    db.commit()

    return {
        "request_id": request_id,
        "model_level": model_level,
        "model": model.display_name or model.model_name,
        "content": result.text,
        "usage": {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "points_used": points,
        },
        "balance_after": balance_after,
    }


def _log_error(db, principal, model, key, task_type, est_input, error_code, started):
    db.add(
        UsageLog(
            user_id=principal.user.id,
            group_id=principal.user.group_id,
            token_id=principal.token.id if principal.token else None,
            source=principal.source,
            provider=model.provider,
            model_level=model.model_level,
            model_name=model.model_name,
            key_id=key.id,
            task_type=task_type,
            input_tokens=est_input,
            output_tokens=0,
            latency_ms=int((time.time() - started) * 1000),
            status="error",
            error_code=str(error_code),
        )
    )
    db.commit()
