"""FastAPI 应用入口。

启动：uvicorn app.main:app --reload
默认 SQLite + 内置 mock 供应商，开箱即用。
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routers import admin, auth, v1, web
from .seed import init_db

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="实验组 AI 大模型 API 中转站 —— Phase 1 MVP",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(web.router)
app.include_router(v1.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}


# ---------- 静态前端 ----------
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))


if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")
