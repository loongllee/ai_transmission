"""端到端冒烟测试：认证 → 网页聊天 → API Token 调用 → 钱包/额度 → 管理后台。

测试环境由 tests/conftest.py 统一设定（独立 SQLite 库 + 内置 mock 供应商）。
运行：pytest -q
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_register_and_login(client):
    r = client.post("/api/auth/register", json={"username": "alice", "password": "secret123", "email": "a@x.com"})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "student"

    r = client.post("/api/auth/login", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_duplicate_register_rejected(client):
    r = client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 409


def _login(client, u, p):
    return client.post("/api/auth/login", json={"username": u, "password": p}).json()["access_token"]


def test_web_chat_and_billing(client):
    jwt = _login(client, "alice", "secret123")

    # 初始余额
    w0 = client.get("/api/web/wallet", headers=_auth(jwt)).json()
    assert w0["balance"] > 0

    # 网页聊天（mock 供应商）
    r = client.post(
        "/api/web/chat",
        headers=_auth(jwt),
        json={"model_level": "basic", "messages": [{"role": "user", "content": "你好，介绍一下中转站"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"]
    assert body["usage"]["points_used"] >= 1

    # 扣费生效
    w1 = client.get("/api/web/wallet", headers=_auth(jwt)).json()
    assert w1["balance"] == w0["balance"] - body["usage"]["points_used"]


def test_model_scope_enforced(client):
    jwt = _login(client, "alice", "secret123")
    # student 默认 scope=basic，调用 advanced 应被拒
    r = client.post(
        "/api/web/chat",
        headers=_auth(jwt),
        json={"model_level": "advanced", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 403


def test_api_token_flow(client):
    jwt = _login(client, "alice", "secret123")

    # 创建平台内部 API Token
    r = client.post("/api/web/tokens", headers=_auth(jwt), json={"token_name": "sci", "model_scope": "basic"})
    assert r.status_code == 201, r.text
    plaintext = r.json()["plaintext_token"]
    assert plaintext.startswith("sk-relay-")

    # 列表不应返回明文
    lst = client.get("/api/web/tokens", headers=_auth(jwt)).json()
    assert all("plaintext_token" not in t for t in lst)

    # 用 API Token 调 v1 接口
    r = client.post(
        "/api/v1/llm/chat",
        headers=_auth(plaintext),
        json={"model_level": "basic", "messages": [{"role": "user", "content": "批量摘要测试"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["content"]

    # v1 钱包/额度/用量
    assert client.get("/api/v1/wallet/me", headers=_auth(plaintext)).status_code == 200
    assert client.get("/api/v1/quota/me", headers=_auth(plaintext)).status_code == 200
    usage = client.get("/api/v1/usage/me", headers=_auth(plaintext)).json()
    assert len(usage) >= 1

    # completions 接口
    r = client.post(
        "/api/v1/llm/completions",
        headers=_auth(plaintext),
        json={"model_level": "basic", "prompt": "翻译：hello world"},
    )
    assert r.status_code == 200, r.text


def test_invalid_api_token_rejected(client):
    r = client.post(
        "/api/v1/llm/chat",
        headers=_auth("sk-relay-deadbeef"),
        json={"model_level": "basic", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 401


def test_admin_backend(client):
    admin_jwt = _login(client, "admin", "admin12345")

    # 非管理员被拒
    alice_jwt = _login(client, "alice", "secret123")
    assert client.get("/api/v1/admin/users", headers=_auth(alice_jwt)).status_code == 403

    # 管理员可访问
    stats = client.get("/api/v1/admin/stats", headers=_auth(admin_jwt)).json()
    assert stats["total_users"] >= 2

    users = client.get("/api/v1/admin/users", headers=_auth(admin_jwt)).json()
    assert any(u["username"] == "alice" for u in users)

    # 添加一个 OpenAI 兼容 Key（加密保存，列表不可见明文）
    r = client.post(
        "/api/v1/admin/keys",
        headers=_auth(admin_jwt),
        json={"provider": "openai", "base_url": "https://example.com/v1", "api_key": "sk-real-secret-123"},
    )
    assert r.status_code == 201, r.text
    keys = client.get("/api/v1/admin/keys", headers=_auth(admin_jwt)).json()
    blob = str(keys)
    assert "sk-real-secret-123" not in blob  # 明文绝不外泄

    # 发放补贴额度
    alice_id = next(u["id"] for u in users if u["username"] == "alice")
    r = client.post(
        f"/api/v1/admin/users/{alice_id}/grant",
        headers=_auth(admin_jwt),
        json={"bucket": "subsidy", "points": 500},
    )
    assert r.status_code == 200
    assert r.json()["added"] == 500
