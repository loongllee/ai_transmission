"""第四阶段端到端测试：SSO 统一身份认证 + 多级组织 + 预算熔断 + 审计 + 报表 + 运维。

测试环境由 tests/conftest.py 统一设定，本模块按字母序最后运行。
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


def test_sso_config(client):
    cfg = client.get("/api/auth/sso/config").json()
    assert cfg["enabled"] is True
    assert cfg["mode"] == "mock"


def test_sso_login_provisions_user(client):
    # 模拟学校 IdP 登录 -> 授权码
    code = client.post(
        "/api/auth/sso/mock/login",
        json={"username": "stu2024", "role": "student", "college": "信息学院", "major": "计算机", "group": "NLP组"},
    ).json()["code"]
    # 回调换取平台 JWT，首次自动开户
    cb = client.post("/api/auth/sso/callback", json={"code": code}).json()
    assert cb["created"] is True
    assert cb["username"] == "stu2024"
    jwt = cb["access_token"]
    me = client.get("/api/web/me", headers=_auth(jwt)).json()
    assert me["username"] == "stu2024" and me["role"] == "student"

    # 再次登录同一 subject -> 复用账号（created=False）
    code2 = client.post("/api/auth/sso/mock/login", json={"username": "stu2024"}).json()["code"]
    cb2 = client.post("/api/auth/sso/callback", json={"code": code2}).json()
    assert cb2["created"] is False

    # 自动建好的组织树可见
    admin = _login(client, "admin", "admin12345")
    units = client.get("/api/v1/admin/org/units", headers=_auth(admin)).json()
    names = {u["name"]: u["unit_type"] for u in units}
    assert names.get("信息学院") == "college"
    assert names.get("计算机") == "major"
    assert names.get("NLP组") == "group"


def test_org_management_and_reports(client):
    admin = _login(client, "admin", "admin12345")
    col = client.post("/api/v1/admin/org/units", headers=_auth(admin), json={"name": "理学院", "unit_type": "college"}).json()
    maj = client.post(
        "/api/v1/admin/org/units", headers=_auth(admin),
        json={"name": "物理系", "unit_type": "major", "parent_id": col["id"]},
    ).json()
    grp = client.post(
        "/api/v1/admin/org/units", headers=_auth(admin),
        json={"name": "凝聚态组", "unit_type": "group", "parent_id": maj["id"]},
    ).json()

    # 注册 frank 并分配到课题组
    client.post("/api/auth/register", json={"username": "frank", "password": "secret123"})
    users = client.get("/api/v1/admin/users", headers=_auth(admin)).json()
    frank_id = next(u["id"] for u in users if u["username"] == "frank")
    r = client.post("/api/v1/admin/org/assign", headers=_auth(admin), json={"user_id": frank_id, "org_unit_id": grp["id"]})
    assert r.status_code == 200

    rollup = client.get("/api/v1/admin/reports/by-org", headers=_auth(admin)).json()
    by_id = {u["id"]: u for u in rollup}
    # 课题组与其祖先学院都应统计到 frank（子树汇总）
    assert by_id[grp["id"]]["members"] >= 1
    assert by_id[col["id"]]["members"] >= 1


def test_budget_circuit_breaker(client):
    admin = _login(client, "admin", "admin12345")

    # 确保有可用学校 Key（前序用例可能禁用了 mock Key）
    client.post("/api/v1/admin/keys", headers=_auth(admin), json={"provider": "mock", "api_key": "x", "resource_pool_type": "school"})

    # 设置很小的学校预算：上限 2 点
    b = client.post("/api/v1/admin/budget", headers=_auth(admin), json={"limit_points": 2, "note": "test"}).json()
    assert b["status"] == "active" and b["limit_points"] == 2

    def chat():
        return client.post(
            "/api/web/chat", headers=_auth(admin),
            json={"model_level": "basic", "messages": [{"role": "user", "content": "预算测试"}]},
        )

    assert chat().status_code == 200  # used -> 1
    assert chat().status_code == 200  # used -> 2 -> tripped
    assert chat().status_code == 503  # 已熔断

    b2 = client.get("/api/v1/admin/budget", headers=_auth(admin)).json()
    assert b2["status"] == "tripped" and b2["used_points"] >= 2

    # 重置后恢复
    client.post("/api/v1/admin/budget/reset", headers=_auth(admin))
    assert chat().status_code == 200


def test_audit_log(client):
    admin = _login(client, "admin", "admin12345")
    audit = client.get("/api/v1/admin/audit", headers=_auth(admin)).json()
    actions = {a["action"] for a in audit}
    assert "budget.set" in actions
    assert "org.create" in actions


def test_reports_overview(client):
    admin = _login(client, "admin", "admin12345")
    ov = client.get("/api/v1/admin/reports/overview", headers=_auth(admin)).json()
    assert ov["total_users"] >= 1
    assert ov["total_calls"] >= 1
    assert ov["school_budget"] is not None  # 预算已设置


def test_ops_and_compliance(client):
    assert client.get("/api/health/ready").json()["status"] == "ready"
    comp = client.get("/api/compliance").json()
    assert any("作弊" in x for x in comp["forbidden"])
    admin = _login(client, "admin", "admin12345")
    sysinfo = client.get("/api/v1/admin/system", headers=_auth(admin)).json()
    assert sysinfo["sso_mode"] == "mock"
    assert sysinfo["org_units"] >= 3
