"""批量异步任务服务（方案 13.3 / 第十四节）。

流程：创建(估算点数) → 用户确认 → 入队 → Worker 处理 → 查询/下载结果。
"""
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from . import billing
from .config import settings
from .models import Job, JobItem, User, UserApiToken
from .providers import estimate_tokens

# 支持的批量任务类型及其提示词模板
PROMPT_TEMPLATES: Dict[str, str] = {
    "batch_summary": "请用中文简要总结以下内容的核心要点（3-5 条）：\n\n{text}",
    "batch_translate": "请翻译以下内容（中文→英文，英文→中文），只输出译文：\n\n{text}",
    "batch_classify": "请判断以下文本所属类别，只返回一个类别名称：\n\n{text}",
    "batch_code_explain": "请解释以下代码的功能、关键逻辑与潜在问题：\n\n{text}",
    "batch_completion": "{text}",
}
JOB_TYPES = set(PROMPT_TEMPLATES.keys())


def build_prompt(job_type: str, text: str) -> str:
    return PROMPT_TEMPLATES.get(job_type, "{text}").format(text=text)


def estimate(db: Session, model_level: str, items: List[dict], max_tokens: int) -> dict:
    """费用预估（方案第十四节“估算点数消耗”）。"""
    model = billing.select_model(db, model_level)
    multiplier = float(model.multiplier) if model else 1.0
    total_in = sum(estimate_tokens((it.get("text") or "")) for it in items)
    est_out = (max_tokens or 256) * len(items)
    est_points = billing.estimate_points(multiplier, total_in, est_out)
    return {
        "items": len(items),
        "estimated_input_tokens": total_in,
        "estimated_points": est_points,
        "model_available": model is not None,
    }


def create_job(
    db: Session,
    *,
    user: User,
    token: Optional[UserApiToken],
    source: str,
    model_scope: str,
    job_type: str,
    model_level: str,
    task_type: Optional[str],
    items: List[dict],
    max_tokens: int,
    auto_confirm: bool,
) -> Job:
    if job_type not in JOB_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"不支持的任务类型：{job_type}")
    if not items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "items 不能为空")
    if len(items) > settings.batch_max_items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"批量条目数超过上限 {settings.batch_max_items}")
    if not billing.level_allowed(model_level, model_scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"当前权限最高可用 {model_scope} 模型")

    est = estimate(db, model_level, items, max_tokens)
    if not est["model_available"]:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"暂无可用的 {model_level} 模型")

    job = Job(
        user_id=user.id,
        group_id=user.group_id,
        token_id=token.id if token else None,
        source=source,
        job_type=job_type,
        model_level=model_level,
        task_type=task_type or job_type,
        status="pending_confirm",
        total_items=len(items),
        estimated_points=est["estimated_points"],
        max_tokens=max_tokens or 256,
    )
    db.add(job)
    db.flush()
    for i, it in enumerate(items):
        db.add(
            JobItem(
                job_id=job.id,
                item_ref=str(it.get("id") if it.get("id") is not None else i),
                seq=i,
                input_text=it.get("text") or "",
                status="pending",
            )
        )
    if auto_confirm:
        _confirm(db, job, user, source)
    db.commit()
    db.refresh(job)
    return job


def confirm_job(db: Session, job: Job, user: User, source: str) -> Job:
    _confirm(db, job, user, source)
    db.commit()
    db.refresh(job)
    return job


def _confirm(db: Session, job: Job, user: User, source: str) -> None:
    if job.status != "pending_confirm":
        raise HTTPException(status.HTTP_409_CONFLICT, f"任务当前状态为 {job.status}，无法确认")
    # 批量任务属“科研 API”计费类别，余额按 API 扣费顺序（含课题组共享额度）核算
    if billing.available_balance(db, user, "job") < int(job.estimated_points or 0):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"点数余额不足：预计需 {job.estimated_points} 点",
        )
    job.status = "queued"
    job.confirmed_at = datetime.utcnow()


def cancel_job(db: Session, job: Job) -> Job:
    if job.status in ("completed", "failed", "canceled"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"任务已结束（{job.status}），无法取消")
    job.status = "canceled"
    job.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job
