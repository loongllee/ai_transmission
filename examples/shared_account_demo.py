"""共享账户演示（每成员独立凭据版）。

用法：
    # 先启动服务： uvicorn app.main:app --port 8000
    python examples/shared_account_demo.py
    # 或指定地址： BASE_URL=https://your-product.example.com python examples/shared_account_demo.py

演示要点：
    1) 拥有者建账户（设聚合速率/并发上限），不下发统一使用 Token；
    2) 拥有者为每名成员签发**独立 member-token**（凭据即身份，不能冒充）；
    3) 多名成员各用自己的 member-token 并发调用，速率不超上限时全部放行；
    4) 每名成员只能拉到自己的历史；拥有者可统一查看所有成员的数据。
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


def main():
    with httpx.Client(timeout=30.0) as client:
        client.post(f"{BASE}/api/auth/register", json={"username": OWNER, "password": OWNER_PW})
        jwt = client.post(f"{BASE}/api/auth/login", json={"username": OWNER, "password": OWNER_PW}).json()["access_token"]
        oh = {"Authorization": f"Bearer {jwt}"}

        # 1) 建共享账户：聚合每分钟 60、并发 5
        acct = client.post(
            f"{BASE}/api/web/shared", headers=oh,
            json={"name": "实验室共享账户-" + uuid.uuid4().hex[:6], "rate_limit_per_minute": 60,
                  "max_concurrency": 5, "daily_request_limit": 5000, "model_scope": "basic"},
        ).json()
        jprint("共享账户已创建（不下发统一使用 Token）", acct["name"])

        # 2) 为每名成员签发独立 member-token
        tokens = {}
        for label in ("alice", "bob", "carol"):
            r = client.post(f"{BASE}/api/web/shared/{acct['id']}/members", headers=oh, json={"member_label": label}).json()
            tokens[label] = r["plaintext_token"]
        jprint("已为各成员签发独立凭据（前缀）", {k: v[:14] + "…" for k, v in tokens.items()})

        # 3) 各成员用自己的 member-token 并发调用
        def call(member, idx):
            r = httpx.post(
                f"{BASE}/api/v1/shared/chat",
                headers={"Authorization": f"Bearer {tokens[member]}"},
                json={"model_level": "basic", "messages": [{"role": "user", "content": f"{member} 的第 {idx} 个问题"}]},
                timeout=30.0,
            )
            return member, r.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(call, m, i) for m in tokens for i in (1, 2)]
            results = [f.result() for f in concurrent.futures.as_completed(futs)]
        jprint("并发调用结果（成员, 状态码）", results)

        # 4) 每名成员只看到自己的历史
        for m, tok in tokens.items():
            hist = httpx.get(f"{BASE}/api/v1/shared/history", headers={"Authorization": f"Bearer {tok}"}, timeout=30.0).json()
            jprint(f"成员 {m} 的历史（{len(hist)} 条，均属于自己）", [h["prompt"] for h in hist])

        # 5) 拥有者侧：各成员用量 + 可查看全账户历史
        stats = client.get(f"{BASE}/api/web/shared/{acct['id']}/members", headers=oh).json()
        jprint("拥有者视角：各成员用量", [(s["member_label"], s["request_count"]) for s in stats])
        allh = client.get(f"{BASE}/api/web/shared/{acct['id']}/history", headers=oh).json()
        jprint("拥有者视角：可查看全账户对话（条数）", len(allh))


if __name__ == "__main__":
    main()
