"""共享账户测试：多人共用一个账户 + 按成员隔离历史 + 聚合速率上限 + 并发上限。

验证用户诉求：
  1) 多名用户共用一个账户；
  2) 每名用户只看到自己的历史使用记录；
  3) 服务端能区分不同成员的请求，互不混淆；
  4) 速率不超上限时允许多人并发使用，超过则限流。
本模块按字母序最后运行。
"""
import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, u, p):
    return client.post("/api/auth/login", json={"username": u, "password": p}).json()["access_token"]


def _h(token, member=None):
    h = {"Authorization": f"Bearer {token}"}
    if member is not None:
        h["X-Member-Id"] = member
    return h


@pytest.fixture(scope="module", autouse=True)
def _setup(client):
    """放开学校预算并确保有可用学校 Key，避免前序用例状态干扰共享调用。"""
    admin = _login(client, "admin", "admin12345")
    client.post("/api/v1/admin/budget", headers=_h(admin), json={"limit_points": 100000000})
    client.post("/api/v1/admin/keys", headers=_h(admin), json={"provider": "mock", "api_key": "x", "resource_pool_type": "school"})


def _create_account(client, **kw):
    company = _login(client, "company", "secret123")
    body = {"name": "团队共享账户", "rate_limit_per_minute": 100, "max_concurrency": 5, "daily_request_limit": 5000}
    body.update(kw)
    r = client.post("/api/web/shared", headers=_h(company), json=body)
    assert r.status_code == 201, r.text
    return company, r.json()


def test_owner_creates_account(client):
    client.post("/api/auth/register", json={"username": "company", "password": "secret123"})
    company, acct = _create_account(client)
    assert acct["plaintext_token"].startswith("sk-relay-")
    assert acct["rate_limit_per_minute"] == 100
    # 列表不返回明文
    lst = client.get("/api/web/shared", headers=_h(company)).json()
    assert "plaintext_token" not in str(lst)


def test_multiple_members_isolated_history(client):
    _, acct = _create_account(client)
    tok = acct["plaintext_token"]

    # 成员 alice 两次对话
    client.post("/api/v1/shared/chat", headers=_h(tok, "alice"), json={"model_level": "basic", "messages": [{"role": "user", "content": "alice-Q1"}]})
    client.post("/api/v1/shared/chat", headers=_h(tok, "alice"), json={"model_level": "basic", "messages": [{"role": "user", "content": "alice-Q2"}]})
    # 成员 bob 一次对话
    client.post("/api/v1/shared/chat", headers=_h(tok, "bob"), json={"model_level": "basic", "messages": [{"role": "user", "content": "bob-Q1"}]})

    # alice 只看到自己的历史
    a_hist = client.get("/api/v1/shared/history", headers=_h(tok, "alice")).json()
    a_prompts = sorted(x["prompt"] for x in a_hist)
    assert a_prompts == ["alice-Q1", "alice-Q2"]
    assert all(x["member_label"] == "alice" for x in a_hist)
    assert "bob-Q1" not in str(a_hist)  # 不会混入他人对话

    # bob 只看到自己的历史
    b_hist = client.get("/api/v1/shared/history", headers=_h(tok, "bob")).json()
    assert [x["prompt"] for x in b_hist] == ["bob-Q1"]
    assert all(x["member_label"] == "bob" for x in b_hist)

    # /me 按成员区分用量
    assert client.get("/api/v1/shared/me", headers=_h(tok, "alice")).json()["my_request_count"] == 2
    assert client.get("/api/v1/shared/me", headers=_h(tok, "bob")).json()["my_request_count"] == 1


def test_owner_sees_per_member_stats(client):
    company, acct = _create_account(client)
    tok = acct["plaintext_token"]
    client.post("/api/v1/shared/chat", headers=_h(tok, "u1"), json={"model_level": "basic", "messages": [{"role": "user", "content": "x"}]})
    client.post("/api/v1/shared/chat", headers=_h(tok, "u2"), json={"model_level": "basic", "messages": [{"role": "user", "content": "y"}]})
    members = client.get(f"/api/web/shared/{acct['id']}/members", headers=_h(company)).json()
    labels = {m["member_label"]: m for m in members}
    assert labels["u1"]["request_count"] == 1
    assert labels["u2"]["request_count"] == 1


def test_rate_cap_enforced(client):
    ratelimit.reset_all()
    _, acct = _create_account(client, name="限速账户", rate_limit_per_minute=2)
    tok = acct["plaintext_token"]
    assert client.post("/api/v1/shared/chat", headers=_h(tok, "m1"), json={"model_level": "basic", "messages": [{"role": "user", "content": "1"}]}).status_code == 200
    assert client.post("/api/v1/shared/chat", headers=_h(tok, "m2"), json={"model_level": "basic", "messages": [{"role": "user", "content": "2"}]}).status_code == 200
    # 第三次超过聚合每分钟上限 -> 429（不同成员也计入同一账户的聚合速率）
    assert client.post("/api/v1/shared/chat", headers=_h(tok, "m3"), json={"model_level": "basic", "messages": [{"role": "user", "content": "3"}]}).status_code == 429


def test_restricted_members(client):
    company, acct = _create_account(client, name="白名单账户", restrict_members=True)
    tok = acct["plaintext_token"]
    client.post(f"/api/web/shared/{acct['id']}/members", headers=_h(company), json={"member_label": "allowed"})
    # 白名单成员可用
    assert client.post("/api/v1/shared/chat", headers=_h(tok, "allowed"), json={"model_level": "basic", "messages": [{"role": "user", "content": "ok"}]}).status_code == 200
    # 非白名单成员被拒
    assert client.post("/api/v1/shared/chat", headers=_h(tok, "stranger"), json={"model_level": "basic", "messages": [{"role": "user", "content": "no"}]}).status_code == 403


def test_concurrency_limiter_unit():
    ratelimit.reset_all()
    key = "unit:conc"
    assert ratelimit.acquire_slot(key, 2) is True
    assert ratelimit.acquire_slot(key, 2) is True
    assert ratelimit.acquire_slot(key, 2) is False  # 已满
    ratelimit.release_slot(key, 2)
    assert ratelimit.acquire_slot(key, 2) is True   # 释放后可再获取
