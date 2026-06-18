"""第三阶段端到端测试：套餐充值 + 付费意愿统计 + 学生贡献账号 + 补偿 + 备用池调度。

测试环境由 tests/conftest.py 统一设定。本模块按字母序最后运行，
其中“备用池”用例会禁用学校 mock Key 以验证降级到学生贡献账号。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _login(client, u, p):
    return client.post("/api/auth/login", json={"username": u, "password": p}).json()["access_token"]


def _register_login(client, u, p="secret123"):
    client.post("/api/auth/register", json={"username": u, "password": p})
    return _login(client, u, p)


def test_packages_listed(client):
    jwt = _login(client, "admin", "admin12345")
    pkgs = client.get("/api/web/packages", headers=_auth(jwt)).json()
    codes = {p["code"]: p for p in pkgs}
    assert "standard" in codes and codes["standard"]["points"] == 1800
    assert "premium" in codes and codes["premium"]["application_only"] is True


def test_purchase_flow(client):
    jwt = _register_login(client, "carol")
    w0 = client.get("/api/web/wallet", headers=_auth(jwt)).json()

    order = client.post("/api/web/orders", headers=_auth(jwt), json={"package_code": "standard"})
    assert order.status_code == 201, order.text
    o = order.json()
    assert o["status"] == "pending" and o["points"] == 1800 and float(o["amount"]) == 15.0

    paid = client.post(f"/api/web/orders/{o['id']}/pay", headers=_auth(jwt), json={"pay_channel": "mock"})
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"

    w1 = client.get("/api/web/wallet", headers=_auth(jwt)).json()
    assert w1["paid_points"] == w0["paid_points"] + 1800

    orders = client.get("/api/web/orders", headers=_auth(jwt)).json()
    assert any(x["status"] == "paid" for x in orders)


def test_application_only_rejected(client):
    jwt = _login(client, "carol", "secret123")
    r = client.post("/api/web/orders", headers=_auth(jwt), json={"package_code": "premium"})
    assert r.status_code == 400


def test_payment_stats(client):
    admin = _login(client, "admin", "admin12345")
    s = client.get("/api/v1/admin/stats/payment", headers=_auth(admin)).json()
    assert s["purchasers"] >= 1
    assert s["paid_orders"] >= 1
    assert s["total_revenue"] >= 15.0
    assert "standard" in s["package_distribution"]


def test_contribution_consent_required(client):
    jwt = _register_login(client, "dave")
    # 未勾选知情同意 -> 拒绝
    r = client.post(
        "/api/web/contributions",
        headers=_auth(jwt),
        json={"provider": "mock", "api_key": "sk-contrib-xyz", "consent": False},
    )
    assert r.status_code == 400


def test_contribution_flow(client):
    jwt = _login(client, "dave", "secret123")
    r = client.post(
        "/api/web/contributions",
        headers=_auth(jwt),
        json={"provider": "mock", "api_key": "sk-contrib-secret-001", "allowed_model_levels": "basic", "consent": True},
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "active"
    # 列表不得返回任何 Key 字段
    lst = client.get("/api/web/contributions", headers=_auth(jwt)).json()
    assert "sk-contrib-secret-001" not in str(lst)
    assert all("encrypted_api_key" not in x and "api_key" not in x for x in lst)

    # 随时撤回
    rev = client.post(f"/api/web/contributions/{c['id']}/revoke", headers=_auth(jwt))
    assert rev.status_code == 200
    assert rev.json()["status"] == "revoked"


def test_contributed_backup_and_compensation(client):
    """禁用学校 Key 后，basic 调用应降级到学生贡献账号，并产生可补偿消耗。"""
    admin = _login(client, "admin", "admin12345")

    # 让 mock 模型产生非零成本（便于核算补偿金额）
    models = client.get("/api/v1/admin/models", headers=_auth(admin)).json()
    basic = next(m for m in models if m["model_level"] == "basic" and m["provider"] == "mock")
    # 价格设为每 1K token 2 元，确保单次消耗高于 Numeric(10,2) 的分位精度
    client.patch(
        f"/api/v1/admin/models/{basic['id']}",
        headers=_auth(admin),
        json={
            "provider": "mock",
            "model_name": basic["model_name"],
            "model_level": "basic",
            "input_price": 2.0,
            "output_price": 2.0,
            "multiplier": 1,
            "enabled": True,
        },
    )

    # 禁用所有学校 mock Key（强制降级到贡献备用池）
    keys = client.get("/api/v1/admin/keys", headers=_auth(admin)).json()
    for k in keys:
        if k["provider"] == "mock" and k["status"] == "active":
            client.post(f"/api/v1/admin/keys/{k['id']}/disable", headers=_auth(admin))

    # erin 贡献一个 mock 账号
    erin = _register_login(client, "erin")
    sub = client.post(
        "/api/web/contributions",
        headers=_auth(erin),
        json={"provider": "mock", "api_key": "sk-erin-backup", "allowed_model_levels": "basic", "consent": True},
    )
    assert sub.status_code == 201, sub.text

    # erin 发起一次 basic 聊天 -> 应由贡献账号承接
    chat = client.post(
        "/api/web/chat",
        headers=_auth(erin),
        json={"model_level": "basic", "messages": [{"role": "user", "content": "备用池测试"}]},
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["content"]

    # 贡献账号累计消耗 > 0
    mine = client.get("/api/web/contributions", headers=_auth(erin)).json()
    assert mine[0]["used_cost_month"] and mine[0]["used_cost_month"] > 0

    # 补偿统计：erin 出现，且建议补偿 = 实际消耗 + 试点补贴
    comp = client.get("/api/v1/admin/compensation", headers=_auth(admin)).json()
    erin_row = next((c for c in comp if c["username"] == "erin"), None)
    assert erin_row is not None
    assert erin_row["total_cost"] > 0
    assert erin_row["suggested_compensation"] >= erin_row["total_cost"] + erin_row["pilot_subsidy"] - 1e-6
