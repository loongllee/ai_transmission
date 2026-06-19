"""共享账户演示脚本：多人共用一个账户，速率不超上限时并发使用，历史按成员隔离。

用法：
    # 先启动服务： uvicorn app.main:app --port 8000
    python examples/shared_account_demo.py
    # 或指定地址： BASE_URL=https://your-product.example.com python examples/shared_account_demo.py

演示要点：
    1) 拥有者创建一个共享账户（设聚合每分钟速率与并发上限），拿到一个共享 Token；
    2) 多名成员用同一个 Token + 各自的 X-Member-Id 并发调用；
    3) 每名成员只能拉到自己的历史对话，服务端按成员区分、互不混淆。
"""
import concurrent.futures
import os
import uuid

import httpx

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
OWNER = os.environ.get("OWNER_USER", "demo_company")
OWNER_PW = os.environ.get("OWNER_PW", "secret123")


def jprint(title, obj):
    print(f"\n=== {title} ===")
    print(obj)


def login_or_register(client):
    client.post(f"{BASE}/api/auth/register", json={"username": OWNER, "password": OWNER_PW})
    r = client.post(f"{BASE}/api/auth/login", json={"username": OWNER, "password": OWNER_PW})
    r.raise_for_status()
    return r.json()["access_token"]


def main():
    with httpx.Client(timeout=30.0) as client:
        jwt = login_or_register(client)
        oh = {"Authorization": f"Bearer {jwt}"}

        # 1) 创建共享账户：聚合每分钟 60 次、最多 5 个并发
        acct = client.post(
            f"{BASE}/api/web/shared",
            headers=oh,
            json={
                "name": "实验室共享账户-" + uuid.uuid4().hex[:6],
                "rate_limit_per_minute": 60,
                "max_concurrency": 5,
                "daily_request_limit": 5000,
                "model_scope": "basic",
            },
        ).json()
        token = acct["plaintext_token"]
        jprint("共享账户已创建（Token 仅此一次可见）", f"{acct['name']} -> {acct['token_prefix']}")

        members = ["alice", "bob", "carol"]

        def call(member, idx):
            r = httpx.post(
                f"{BASE}/api/v1/shared/chat",
                headers={"Authorization": f"Bearer {token}", "X-Member-Id": member},
                json={"model_level": "basic", "messages": [{"role": "user", "content": f"{member} 的第 {idx} 个问题"}]},
                timeout=30.0,
            )
            return member, r.status_code

        # 2) 多名成员并发调用（速率不超上限 -> 全部放行）
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(call, m, i) for m in members for i in (1, 2)]
            results = [f.result() for f in concurrent.futures.as_completed(futs)]
        jprint("并发调用结果（成员, 状态码）", results)

        # 3) 每名成员只看到自己的历史，互不混淆
        for m in members:
            hist = httpx.get(
                f"{BASE}/api/v1/shared/history",
                headers={"Authorization": f"Bearer {token}", "X-Member-Id": m},
                timeout=30.0,
            ).json()
            jprint(f"成员 {m} 的历史（共 {len(hist)} 条，均属于自己）", [h["prompt"] for h in hist])

        # 4) 拥有者侧：按成员统计用量
        stats = client.get(f"{BASE}/api/web/shared/{acct['id']}/members", headers=oh).json()
        jprint("拥有者视角：各成员用量", [(s["member_label"], s["request_count"]) for s in stats])


if __name__ == "__main__":
    main()
