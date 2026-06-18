"""生产化加固测试：Prometheus 指标 / 就绪检查 / 限流回退 / 文件密钥注入。

测试环境由 tests/conftest.py 统一设定，本模块按字母序最后运行。
"""
import os

import pytest
from fastapi.testclient import TestClient

from app import metrics, ratelimit
from app.config import Settings
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_metrics_endpoint(client):
    # 前序阶段已产生多次成功调用，计数器应已累计
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "# TYPE relay_requests_total counter" in body
    assert "relay_requests_total" in body
    assert "relay_users" in body  # 网关 gauge


def test_ready_reports_backend(client):
    r = client.get("/api/health/ready").json()
    assert r["status"] == "ready"
    assert r["ratelimit_backend"] == "memory"  # 未配置 REDIS_URL -> 进程内回退


def test_ratelimit_memory_fallback():
    ratelimit.reset_all()
    key = "unit:rl:test"
    assert ratelimit.check_and_incr_minute(key, 2) is True
    assert ratelimit.check_and_incr_minute(key, 2) is True
    assert ratelimit.check_and_incr_minute(key, 2) is False  # 超过每分钟 2 次


def test_metrics_counter_inc():
    metrics.reset()
    metrics.inc("relay_requests_total", source="api", status="success")
    metrics.inc("relay_requests_total", value=2, source="api", status="success")
    out = metrics.render({"relay_users": 3})
    assert 'relay_requests_total{source="api",status="success"} 3' in out
    assert "relay_users 3" in out


def test_encryption_secret_from_file(tmp_path):
    """机密托管：*_FILE 注入应覆盖明文密钥（Vault/KMS/K8s Secret 模式）。"""
    f = tmp_path / "enc_secret.txt"
    f.write_text("super-secret-from-file-xyz\n", encoding="utf-8")
    os.environ["ENCRYPTION_SECRET_FILE"] = str(f)
    try:
        s = Settings()
        assert s.encryption_secret == "super-secret-from-file-xyz"
    finally:
        os.environ.pop("ENCRYPTION_SECRET_FILE", None)
