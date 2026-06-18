"""大模型供应商适配层（方案第十二节模型路由 / 第五节供应商接入）。

- MockProvider：内置离线供应商，无需任何真实 Key 即可跑通全链路。
- OpenAICompatProvider：对接 OpenAI 兼容接口（/chat/completions）。
"""
import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from .config import settings


@dataclass
class ProviderResult:
    text: str
    input_tokens: int
    output_tokens: int
    raw: dict = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 字符约 1 token，其余约 1/4 token。

    仅用于扣费前预估与 mock 计量；真实供应商以其返回的 usage 为准。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return max(1, cjk + (other // 4 if other else 0))


def estimate_messages_tokens(messages: List[Dict[str, str]]) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in messages) + len(messages) * 3


class BaseProvider:
    async def chat(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> ProviderResult:
        raise NotImplementedError


class MockProvider(BaseProvider):
    """离线模拟供应商：用于试点/开发，无真实成本。"""

    async def chat(self, model_name, messages, max_tokens=512, temperature=0.7) -> ProviderResult:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        reply = (
            f"【中转站 Mock 回复 · {model_name}】\n"
            f"已收到你的消息（共 {len(messages)} 条）。这是离线模拟回复，"
            f"用于验证认证、路由、扣费与日志全链路是否打通。\n"
            f"你最后说的是：{last_user[:200]}"
        )
        in_tok = estimate_messages_tokens(messages)
        out_tok = estimate_tokens(reply)
        return ProviderResult(text=reply, input_tokens=in_tok, output_tokens=out_tok, raw={"mock": True})


class OpenAICompatProvider(BaseProvider):
    """OpenAI 兼容供应商（OpenAI / DeepSeek / 通义 / 本地 vLLM 等）。"""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key

    async def chat(self, model_name, messages, max_tokens=512, temperature=0.7) -> ProviderResult:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        retries = max(0, int(settings.openai_max_retries))
        last_exc = None
        async with httpx.AsyncClient(timeout=settings.openai_timeout) as client:
            for attempt in range(retries + 1):
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                    # 对 429/5xx 做退避重试
                    if resp.status_code == 429 or resp.status_code >= 500:
                        resp.raise_for_status()
                    resp.raise_for_status()
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    in_tok = usage.get("prompt_tokens") or estimate_messages_tokens(messages)
                    out_tok = usage.get("completion_tokens") or estimate_tokens(text)
                    return ProviderResult(text=text, input_tokens=in_tok, output_tokens=out_tok, raw=data)
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                    last_exc = exc
                    if attempt < retries:
                        await asyncio.sleep(0.5 * (2 ** attempt))  # 指数退避
                        continue
                    raise
        raise last_exc  # pragma: no cover


def get_provider(provider_name: str, base_url: Optional[str], api_key: Optional[str]) -> BaseProvider:
    if (provider_name or "").lower() == "mock":
        return MockProvider()
    return OpenAICompatProvider(base_url or "", api_key or "")
