"""科研程序化 API（平台内部 API Token 认证）—— 方案第十三节接口设计。

所有接口：Authorization: Bearer <platform_api_token>
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import billing, chat
from .. import jobs as jobs_service
from ..database import get_db
from ..deps import Principal, get_api_principal
from ..models import Job, JobItem, UsageLog, WalletAccount
from ..schemas import (
    ChatRequest,
    ChatResponse,
    CompletionRequest,
    JobCreateRequest,
    JobEstimateOut,
    JobEstimateRequest,
    JobOut,
    JobResultOut,
    QuotaOut,
    UsageLogOut,
    WalletOut,
)

router = APIRouter(prefix="/api/v1", tags=["v1-api"])


def _get_wallet(db: Session, user_id: int) -> WalletAccount:
    wallet = db.query(WalletAccount).filter(WalletAccount.user_id == user_id).first()
    if not wallet:
        wallet = WalletAccount(user_id=user_id)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return wallet


# 13.1 通用聊天接口
@router.post("/llm/chat", response_model=ChatResponse)
async def llm_chat(
    payload: ChatRequest,
    principal: Principal = Depends(get_api_principal),
    db: Session = Depends(get_db),
):
    messages = [{"role": m.role, "content": m.content} for m in payload.messages]
    return await chat.run_chat(
        db,
        principal,
        payload.model_level,
        payload.task_type or "research_chat",
        messages,
        payload.max_tokens or 512,
        payload.temperature or 0.7,
    )


# 13.2 文本生成接口
@router.post("/llm/completions", response_model=ChatResponse)
async def llm_completions(
    payload: CompletionRequest,
    principal: Principal = Depends(get_api_principal),
    db: Session = Depends(get_db),
):
    messages = [{"role": "user", "content": payload.prompt}]
    return await chat.run_chat(
        db,
        principal,
        payload.model_level,
        payload.task_type or "completion",
        messages,
        payload.max_tokens or 512,
        payload.temperature or 0.7,
    )


# 13.3 批量任务提交接口
@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def submit_job(
    payload: JobCreateRequest,
    principal: Principal = Depends(get_api_principal),
    db: Session = Depends(get_db),
):
    if not principal.allow_batch:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前 Token 未开启批量任务权限（allow_batch）")
    items = [it.model_dump() for it in payload.items]
    job = jobs_service.create_job(
        db,
        user=principal.user,
        token=principal.token,
        source="api",
        model_scope=principal.model_scope,
        job_type=payload.job_type,
        model_level=payload.model_level,
        task_type=payload.task_type,
        items=items,
        max_tokens=payload.max_tokens or 256,
        auto_confirm=payload.auto_confirm,
    )
    return job


# 费用预估（方案第十四节）
@router.post("/jobs/estimate", response_model=JobEstimateOut)
def estimate_job(
    payload: JobEstimateRequest,
    principal: Principal = Depends(get_api_principal),
    db: Session = Depends(get_db),
):
    items = [it.model_dump() for it in payload.items]
    return jobs_service.estimate(db, payload.model_level, items, payload.max_tokens or 256)


@router.get("/jobs", response_model=List[JobOut])
def list_jobs(
    limit: int = 50,
    principal: Principal = Depends(get_api_principal),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    return (
        db.query(Job)
        .filter(Job.user_id == principal.user.id)
        .order_by(Job.id.desc())
        .limit(limit)
        .all()
    )


def _owned_job(db: Session, principal: Principal, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if not job or job.user_id != principal.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    return job


# 13.4 查询任务状态接口
@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, principal: Principal = Depends(get_api_principal), db: Session = Depends(get_db)):
    return _owned_job(db, principal, job_id)


@router.get("/jobs/{job_id}/results", response_model=JobResultOut)
def get_job_results(job_id: int, principal: Principal = Depends(get_api_principal), db: Session = Depends(get_db)):
    job = _owned_job(db, principal, job_id)
    items = db.query(JobItem).filter(JobItem.job_id == job.id).order_by(JobItem.seq.asc()).all()
    return {"job": job, "items": items}


@router.post("/jobs/{job_id}/confirm", response_model=JobOut)
def confirm_job(job_id: int, principal: Principal = Depends(get_api_principal), db: Session = Depends(get_db)):
    job = _owned_job(db, principal, job_id)
    return jobs_service.confirm_job(db, job, principal.user, "api")


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, principal: Principal = Depends(get_api_principal), db: Session = Depends(get_db)):
    job = _owned_job(db, principal, job_id)
    return jobs_service.cancel_job(db, job)


# 13.5 查询个人额度接口
@router.get("/quota/me", response_model=QuotaOut)
def quota_me(principal: Principal = Depends(get_api_principal), db: Session = Depends(get_db)):
    return QuotaOut(
        role=principal.user.role,
        balance=billing.available_balance(db, principal.user, "api"),
        daily_request_limit=principal.daily_request_limit,
        daily_token_limit=principal.daily_token_limit,
        rate_limit_per_minute=principal.rate_limit_per_minute,
    )


# 13.6 查询调用记录接口
@router.get("/usage/me", response_model=List[UsageLogOut])
def usage_me(
    limit: int = 50,
    principal: Principal = Depends(get_api_principal),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    return (
        db.query(UsageLog)
        .filter(UsageLog.user_id == principal.user.id)
        .order_by(UsageLog.id.desc())
        .limit(limit)
        .all()
    )


# 13.7 查询点数账户接口
@router.get("/wallet/me", response_model=WalletOut)
def wallet_me(principal: Principal = Depends(get_api_principal), db: Session = Depends(get_db)):
    w = _get_wallet(db, principal.user.id)
    return WalletOut(
        free_points=w.free_points or 0,
        paid_points=w.paid_points or 0,
        subsidy_points=w.subsidy_points or 0,
        project_points=w.project_points or 0,
        total_used_points=w.total_used_points or 0,
        balance=billing.available_balance(db, principal.user, "api"),
    )
