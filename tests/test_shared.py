"""共享账户测试（每成员独立凭据版）：

  步骤1 身份：账户创建不下发使用 Token；拥有者为每成员签发独立 member-token，凭据即身份。
  步骤2 限额：账户聚合上限 + 每成员单独上限（rpm/每日/token）。
  步骤3 拥有者可见：拥有者可查看任一成员/全账户的历史（含对话原文）。
  步骤4 成员自助：偏好设置、重置自己的凭据、删除/清空自己的历史。
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


def _ah(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", autouse=True)
def _setup(client):
    """放开学校预算并确保有可用学校 Key；准备拥有者 company。"""
    admin = _login(client, "admin", "admin12345")
    client.post("/api/v1/admin/budget", headers=_ah(admin), json={"limit_points": 100000000})
    client.post("/api/v1/admin/keys", headers=_ah(admin), json={"provider": "mock", "api_key": "x", "resource_pool_type": "school"})
    client.post("/api/auth/register", json={"username": "company", "password": "secret123"})


def _owner(client):
    return _login(client, "company", "secret123")


def _create_account(client, jwt=None, **kw):
    if jwt is None:
        jwt = _owner(client)
    body = {"name": "团队账户", "rate_limit_per_minute": 100, "max_concurrency": 5, "daily_request_limit": 5000}
    body.update(kw)
    r = client.post("/api/web/shared", headers=_ah(jwt), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _add_member(client, jwt, acct_id, label, **kw):
    r = client.post(f"/api/web/shared/{acct_id}/members", headers=_ah(jwt), json={"member_label": label, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _chat(client, member_token, content):
    return client.post(
        "/api/v1/shared/chat", headers=_ah(member_token),
        json={"model_level": "basic", "messages": [{"role": "user", "content": content}]},
    )


# ---------- 步骤1：身份 ----------
def test_account_creation_returns_no_usage_token(client):
    jwt = _owner(client)
    acct = _create_account(client, jwt, default_member_rpm=50)
    assert "plaintext_token" not in acct          # 账户不再下发统一使用 Token
    assert acct["default_member_rpm"] == 50


def test_member_gets_own_token(client):
    jwt = _owner(client)
    acct = _create_account(client)
    m = _add_member(client, jwt, acct["id"], "alice", display_name="Alice")
    assert m["plaintext_token"].startswith("sk-relay-")
    assert m["display_name"] == "Alice"
    # 列表不泄露任何 Token 明文/哈希
    lst = client.get(f"/api/web/shared/{acct['id']}/members", headers=_ah(jwt)).json()
    assert "plaintext_token" not in str(lst) and "token_hash" not in str(lst)
    # 无效 member-token 被拒
    assert _chat(client, "sk-relay-deadbeef", "x").status_code == 401


def test_members_isolated_by_credential(client):
    jwt = _owner(client)
    acct = _create_account(client)
    a = _add_member(client, jwt, acct["id"], "a")["plaintext_token"]
    b = _add_member(client, jwt, acct["id"], "b")["plaintext_token"]

    assert _chat(client, a, "a-1").status_code == 200
    assert _chat(client, a, "a-2").status_code == 200
    assert _chat(client, b, "b-1").status_code == 200

    a_hist = client.get("/api/v1/shared/history", headers=_ah(a)).json()
    assert sorted(x["prompt"] for x in a_hist) == ["a-1", "a-2"]
    assert "b-1" not in str(a_hist)              # 不会混入他人对话
    b_hist = client.get("/api/v1/shared/history", headers=_ah(b)).json()
    assert [x["prompt"] for x in b_hist] == ["b-1"]
    assert client.get("/api/v1/shared/me", headers=_ah(a)).json()["my_request_count"] == 2


# ---------- 步骤2：限额 ----------
def test_per_member_rate_limit(client):
    ratelimit.reset_all()
    jwt = _owner(client)
    acct = _create_account(client, rate_limit_per_minute=100)   # 账户很宽松
    tok = _add_member(client, jwt, acct["id"], "lim", rpm_limit=2)["plaintext_token"]  # 该成员每分钟仅 2
    assert _chat(client, tok, "1").status_code == 200
    assert _chat(client, tok, "2").status_code == 200
    assert _chat(client, tok, "3").status_code == 429           # 触发成员级限速


def test_account_aggregate_limit(client):
    ratelimit.reset_all()
    jwt = _owner(client)
    acct = _create_account(client, rate_limit_per_minute=2)     # 账户聚合每分钟 2
    a = _add_member(client, jwt, acct["id"], "x1")["plaintext_token"]
    b = _add_member(client, jwt, acct["id"], "x2")["plaintext_token"]
    assert _chat(client, a, "1").status_code == 200
    assert _chat(client, b, "2").status_code == 200
    assert _chat(client, a, "3").status_code == 429             # 不同成员也计入账户聚合


def test_owner_update_member_limit(client):
    ratelimit.reset_all()
    jwt = _owner(client)
    acct = _create_account(client, rate_limit_per_minute=100)
    tok = _add_member(client, jwt, acct["id"], "u")["plaintext_token"]
    # 拥有者把该成员限到每分钟 1
    r = client.patch(f"/api/web/shared/{acct['id']}/members/u", headers=_ah(jwt), json={"rpm_limit": 1})
    assert r.status_code == 200 and r.json()["rpm_limit"] == 1
    assert _chat(client, tok, "1").status_code == 200
    assert _chat(client, tok, "2").status_code == 429


# ---------- 步骤3：拥有者可见全部数据 ----------
def test_owner_sees_member_data(client):
    jwt = _owner(client)
    acct = _create_account(client)
    tok = _add_member(client, jwt, acct["id"], "viewme")["plaintext_token"]
    _chat(client, tok, "secret question")

    mh = client.get(f"/api/web/shared/{acct['id']}/members/viewme/history", headers=_ah(jwt)).json()
    assert any(x["prompt"] == "secret question" and x["response"] for x in mh)   # 拥有者可见对话原文
    ah = client.get(f"/api/web/shared/{acct['id']}/history", headers=_ah(jwt)).json()
    assert any(x["member_label"] == "viewme" for x in ah)


# ---------- 步骤4：成员自助 ----------
def test_member_self_service(client):
    jwt = _owner(client)
    acct = _create_account(client)
    tok = _add_member(client, jwt, acct["id"], "self")["plaintext_token"]

    # 偏好
    s = client.patch("/api/v1/shared/me/settings", headers=_ah(tok), json={"display_name": "我", "default_model_level": "basic", "default_max_tokens": 256}).json()
    assert s["display_name"] == "我" and s["default_max_tokens"] == 256
    # 超出账户等级的默认值被拒
    assert client.patch("/api/v1/shared/me/settings", headers=_ah(tok), json={"default_model_level": "advanced"}).status_code == 403

    # 历史删除/清空
    _chat(client, tok, "h1")
    _chat(client, tok, "h2")
    hist = client.get("/api/v1/shared/history", headers=_ah(tok)).json()
    assert len(hist) == 2
    assert client.delete(f"/api/v1/shared/history/{hist[0]['id']}", headers=_ah(tok)).status_code == 200
    assert len(client.get("/api/v1/shared/history", headers=_ah(tok)).json()) == 1
    assert client.delete("/api/v1/shared/history", headers=_ah(tok)).json()["cleared"] == 1
    assert client.get("/api/v1/shared/history", headers=_ah(tok)).json() == []

    # 重置自己的凭据：旧 Token 失效、新 Token 可用
    new_tok = client.post("/api/v1/shared/me/token/reset", headers=_ah(tok)).json()["plaintext_token"]
    assert new_tok != tok
    assert client.get("/api/v1/shared/me", headers=_ah(tok)).status_code == 401
    assert client.get("/api/v1/shared/me", headers=_ah(new_tok)).status_code == 200


def test_concurrency_limiter_unit():
    ratelimit.reset_all()
    key = "unit:conc"
    assert ratelimit.acquire_slot(key, 2) is True
    assert ratelimit.acquire_slot(key, 2) is True
    assert ratelimit.acquire_slot(key, 2) is False
    ratelimit.release_slot(key, 2)
    assert ratelimit.acquire_slot(key, 2) is True
