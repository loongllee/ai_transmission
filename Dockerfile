# ===== 实验组 AI 大模型 API 中转站 — 生产镜像 =====
FROM python:3.11-slim

# 不写 .pyc、日志实时输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # 默认 SQLite，数据落在挂载卷 /data，便于持久化
    DATABASE_URL=sqlite:////data/relay.db

WORKDIR /app

# 选择依赖清单：默认 requirements.txt；生产可 --build-arg REQUIREMENTS=requirements-prod.txt
ARG REQUIREMENTS=requirements.txt

# 先装依赖以利用层缓存（requirements*.txt 全部拷入，便于 -r 互相引用）
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r ${REQUIREMENTS}

# 拷贝应用代码与前端
COPY app ./app
COPY frontend ./frontend

# 创建数据目录与非 root 用户
RUN mkdir -p /data \
    && adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

# 容器健康检查（slim 镜像无 curl，用 python 标准库）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
