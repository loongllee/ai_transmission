"""第二阶段端到端测试：批量任务 + 异步 Worker + 课题组额度 + 费用预估 + 异常封禁。

测试环境由 tests/conftest.py 统一设定（关闭应用内后台 Worker，测试中手动 drain）。
"""
import pytest
from fastapi.testclient import TestClient

from app import alerts, worker
from app.database import SessionLocal
from app.main import app
from app.models import Alert, UserApiToken


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _login(client, u, p):
    return client.post("/api/auth/login", json={"username": u, "password": p}).json()["access_token"]


def _new_api_token(client, jwt, allow_batch=True, scope="basic"):
    r = client.post(
        "/api/web/tokens",
        headers=_auth(jwt),
        json={"token_name": "p2", "model_scope": scope, "allow_batch": allow_batch},
    )
    assert r.status_code == 201, r.text
    return r.json()["plaintext_token"]


def test_estimate(client):
    jwt = _login(client, "admin", "admin12345")
    api = _new_api_token(client, jwt)
    r = client.post(
        "/api/v1/jobs/estimate",
        headers=_auth(api),
        json={"model_level": "basic", "items": [{"text": "hello world"}, {"text": "你好世界"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == 2
    assert body["estimated_points"] >= 1
    assert body["model_available"] is True


def test_batch_job_flow(client):
    jwt = _login(client, "admin", "admin12345")
    api = _new_api_token(client, jwt)

    # 提交批量摘要任务（自动确认入队）
    r = client.post(
        "/api/v1/jobs",
        headers=_auth(api),
        json={
            "job_type": "batch_summary",
            "model_level": "basic",
            "auto_confirm": True,
            "items": [
                {"id": "p1", "text": "大语言模型在科研中的应用越来越广泛。"},
                {"id": "p2", "text": "Experimental results show significant improvement."},
            ],
        },
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["total_items"] == 2
    assert job["status"] == "queued"
    job_id = job["id"]

    # 手动驱动 Worker 处理队列
    processed = worker.drain()
    assert processed >= 1

    # 查询任务状态
    got = client.get(f"/api/v1/jobs/{job_id}", headers=_auth(api)).json()
    assert got["status"] == "completed", got
    assert got["processed_items"] == 2
    assert got["points_used"] >= 1

    # 查询结果
    res = client.get(f"/api/v1/jobs/{job_id}/results", headers=_auth(api)).json()
    assert len(res["items"]) == 2
    assert all(it["status"] == "done" for it in res["items"])
    assert all(it["output_text"] for it in res["items"])


def test_job_requires_allow_batch(client):
    jwt = _login(client, "admin", "admin12345")
    api = _new_api_token(client, jwt, allow_batch=False)
    r = client.post(
        "/api/v1/jobs",
        headers=_auth(api),
        json={"job_type": "batch_summary", "model_level": "basic", "items": [{"text": "x"}]},
    )
    assert r.status_code == 403


def test_group_quota_priority(client):
    admin = _login(client, "admin", "admin12345")

    # 新建课题组并充值共享额度
    g = client.post(
        "/api/v1/admin/groups",
        headers=_auth(admin),
        json={"name": "实验组A", "project_points": 100000},
    )
    assert g.status_code == 201, g.text
    group_id = g.json()["id"]

    # 注册 bob 并加入课题组
    client.post("/api/auth/register", json={"username": "bob", "password": "secret123"})
    users = client.get("/api/v1/admin/users", headers=_auth(admin)).json()
    bob_id = next(u["id"] for u in users if u["username"] == "bob")
    m = client.post(
        f"/api/v1/admin/groups/{group_id}/members",
        headers=_auth(admin),
        json={"user_id": bob_id},
    )
    assert m.status_code == 200, m.text

    # bob 的初始个人钱包（注册赠送的 free 额度）
    bob = _login(client, "bob", "secret123")
    bob_api = _new_api_token(client, bob)
    w_before = client.get("/api/v1/wallet/me", headers=_auth(bob_api)).json()

    # bob 调用一次科研 API —— 个人 project=0，应优先扣课题组共享额度
    r = client.post(
        "/api/v1/llm/chat",
        headers=_auth(bob_api),
        json={"model_level": "basic", "messages": [{"role": "user", "content": "课题组额度测试"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["usage"]["from_group"] >= 1  # 确实从课题组扣了点

    # 个人 free 额度不变，课题组剩余减少
    w_after = client.get("/api/v1/wallet/me", headers=_auth(bob_api)).json()
    assert w_after["free_points"] == w_before["free_points"]
    stats = client.get(f"/api/v1/admin/groups/{group_id}/stats", headers=_auth(admin)).json()
    assert stats["members"] >= 1
    assert stats["project_points_remaining"] < 100000
    assert stats["total_used_points"] >= 1


def test_alert_auto_disable_token(client):
    """连续错误触发告警并自动停用 Token（异常 API 调用封禁）。"""
    jwt = _login(client, "admin", "admin12345")
    api = _new_api_token(client, jwt)
    # 取出该 Token 的 ORM 对象
    from app.security import hash_api_token

    db = SessionLocal()
    try:
        token = db.query(UserApiToken).filter(UserApiToken.token_hash == hash_api_token(api)).first()
        assert token is not None
        user_id = token.user_id
        # 阈值=3：连续 3 次错误即触发
        for _ in range(3):
            alerts.on_call_error(db, token, user_id, "provider_error")
        db.refresh(token)
        assert token.status == "disabled"
        assert db.query(Alert).filter(Alert.token_id == token.id, Alert.status == "open").count() >= 1
    finally:
        db.close()

    # 被封禁的 Token 调用应被拒
    r = client.post(
        "/api/v1/llm/chat",
        headers=_auth(api),
        json={"model_level": "basic", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 403
