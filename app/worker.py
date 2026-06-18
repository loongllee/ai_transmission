"""异步任务 Worker（方案第十四节 / 第十七节 Worker 节点）。

两种运行方式：
  1) 应用内后台线程：lifespan 启动时按 settings.run_inprocess_worker 自动拉起（试点零依赖）。
  2) 独立进程：python -m app.worker  （生产可水平扩展多个 Worker）

队列基于数据库 jobs.status（queued → running → completed/failed）。
生产可替换为 RabbitMQ / Kafka / Redis Stream，对外接口不变。
"""
import asyncio
import threading
from datetime import datetime
from typing import Optional

from . import chat, jobs as jobs_service
from .config import settings
from .database import SessionLocal
from .models import Job, JobItem, User, UserApiToken

_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


def _claim_one_job(db) -> Optional[Job]:
    """原子地认领一个排队任务（queued → running）。"""
    candidate = (
        db.query(Job).filter(Job.status == "queued").order_by(Job.id.asc()).first()
    )
    if not candidate:
        return None
    updated = (
        db.query(Job)
        .filter(Job.id == candidate.id, Job.status == "queued")
        .update({"status": "running", "updated_at": datetime.utcnow()})
    )
    db.commit()
    if not updated:
        return None  # 被其他 Worker 抢先
    return db.get(Job, candidate.id)


async def _process_job(db, job: Job) -> None:
    user = db.get(User, job.user_id)
    token = db.get(UserApiToken, job.token_id) if job.token_id else None
    items = (
        db.query(JobItem)
        .filter(JobItem.job_id == job.id, JobItem.status == "pending")
        .order_by(JobItem.seq.asc())
        .all()
    )
    stopped_for_balance = False
    for it in items:
        # 任务可能被取消
        db.refresh(job)
        if job.status == "canceled":
            return
        prompt = jobs_service.build_prompt(job.job_type, it.input_text or "")
        try:
            res = await chat.execute_chat(
                db,
                user=user,
                token=token,
                source="job",
                model_level=job.model_level,
                task_type=job.task_type or job.job_type,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=job.max_tokens or 256,
                temperature=0.3,
            )
            it.status = "done"
            it.output_text = res["content"]
            it.input_tokens = res["usage"]["input_tokens"]
            it.output_tokens = res["usage"]["output_tokens"]
            it.points_used = res["usage"]["points_used"]
            job.processed_items = int(job.processed_items or 0) + 1
            job.points_used = int(job.points_used or 0) + res["usage"]["points_used"]
        except Exception as exc:  # noqa: BLE001  (HTTPException 等)
            detail = getattr(exc, "detail", None) or str(exc)
            status_code = getattr(exc, "status_code", None)
            it.status = "error"
            it.error = str(detail)[:250]
            job.failed_items = int(job.failed_items or 0) + 1
            if status_code == 402:  # 余额耗尽，停止后续条目
                stopped_for_balance = True
                db.commit()
                break
        db.commit()

    if stopped_for_balance:
        for it in db.query(JobItem).filter(JobItem.job_id == job.id, JobItem.status == "pending").all():
            it.status = "error"
            it.error = "余额不足，未执行"
            job.failed_items = int(job.failed_items or 0) + 1
        job.error = "点数余额不足，部分条目未执行"

    db.refresh(job)
    if job.status != "canceled":
        if int(job.processed_items or 0) == 0:
            job.status = "failed"
        else:
            job.status = "completed"
        job.finished_at = datetime.utcnow()
    db.commit()


def drain(max_jobs: int = 100) -> int:
    """同步处理当前所有排队任务（供 Worker 循环与测试调用）。返回处理的任务数。"""
    processed = 0
    while processed < max_jobs:
        db = SessionLocal()
        try:
            job = _claim_one_job(db)
            if not job:
                break
            asyncio.run(_process_job(db, job))
            processed += 1
        finally:
            db.close()
    return processed


def _loop() -> None:
    while not _stop_event.is_set():
        try:
            drain(max_jobs=settings.worker_batch_per_tick)
        except Exception:  # noqa: BLE001  后台循环不因单次异常退出
            pass
        _stop_event.wait(settings.worker_poll_interval)


def start_background_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="job-worker", daemon=True)
    _thread.start()


def stop_background_worker() -> None:
    _stop_event.set()


if __name__ == "__main__":
    # 独立 Worker 进程
    from .seed import init_db

    init_db()
    print("[worker] 独立 Worker 启动，轮询间隔 %ss" % settings.worker_poll_interval)
    try:
        _loop()
    except KeyboardInterrupt:
        print("[worker] 退出")
