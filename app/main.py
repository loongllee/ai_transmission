"""FastAPI 应用入口。

启动：uvicorn app.main:app --reload
默认 SQLite + 内置 mock 供应商，开箱即用。
"""
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, metrics, worker
from .config import settings
from .logging_config import configure_logging, logger
from .routers import admin, auth, v1, web
from .seed import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("启动中转站", extra={"event": "startup"})
    # 启动时初始化数据库（建表 + 初始管理员 + mock 模型/Key）
    init_db()
    # 启动应用内后台 Worker（处理批量异步任务）
    if settings.run_inprocess_worker:
        worker.start_background_worker()
    try:
        yield
    finally:
        worker.stop_background_worker()


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="实验组 AI 大模型 API 中转站",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    started = time.time()
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        logger.info(
            "request",
            extra={
                "event": "http",
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": int((time.time() - started) * 1000),
            },
        )
    return response

app.include_router(auth.router)
app.include_router(web.router)
app.include_router(v1.router)
app.include_router(admin.router)


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok", "app": settings.app_name, "version": __version__}


@app.get("/api/health/ready", tags=["meta"])
def ready():
    """就绪检查：验证数据库连通性（方案第四阶段运维）。"""
    from sqlalchemy import text

    from . import ratelimit
    from .database import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "ok", "ratelimit_backend": ratelimit.backend()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": str(exc)})


@app.get("/metrics", tags=["meta"], include_in_schema=False)
def prometheus_metrics():
    """Prometheus 文本格式指标（方案第十七节监控）。"""
    if not settings.metrics_enabled:
        return PlainTextResponse("metrics disabled\n", status_code=404)
    gauges = {}
    try:
        from .database import SessionLocal
        from .models import Job, UsageLog, User

        db = SessionLocal()
        try:
            gauges["relay_users"] = float(db.query(User).count())
            gauges["relay_usage_logs"] = float(db.query(UsageLog).count())
            gauges["relay_jobs"] = float(db.query(Job).count())
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass
    return PlainTextResponse(metrics.render(gauges), media_type="text/plain; version=0.0.4")


@app.get("/api/compliance", tags=["meta"])
def compliance():
    """使用规范与合规声明（方案第三、十九节）。"""
    return {
        "platform": settings.app_name,
        "purpose": "仅供实验组成员学习与科研辅助；非通用代理，不对外商业化。",
        "allowed": [
            "学生日常 AI 问答",
            "论文摘要与文献阅读",
            "英文论文翻译与润色",
            "代码解释、报错分析、算法辅助",
            "科研程序化 API 调用与批量文本处理",
        ],
        "forbidden": [
            "考试作弊、代写作业/论文",
            "伪造实验数据",
            "上传涉密资料或敏感个人信息",
            "对外转售平台 API 能力、公开共享 API Token",
            "恶意刷量或攻击平台",
        ],
        "notes": [
            "真实供应商 Key 后端加密存储，用户不可见、不可导出",
            "模型输出仅供辅助，重要内容须人工审核",
            "统计以匿名化、汇总化为主；付款/补偿走学校正规财务渠道",
        ],
    }


# ---------- 静态前端 ----------
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))


if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")
