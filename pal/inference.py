"""HTTP client for the inference server's OpenAI-compatible API.

Supports both streaming (SSE) and non-streaming completions via
POST /v1/chat/completions.
"""
import json
from collections.abc import AsyncGenerator

import httpx


class InferenceClient:
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=120.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def complete(self, messages: list[dict[str, str]]) -> str:
        """Send a non-streaming completion request, return the full response text."""
        payload = {"model": self.model, "messages": messages, "stream": False}
        resp = await self._client.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def stream(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        """Send a streaming completion request, yield tokens as they arrive."""
        payload = {"model": self.model, "messages": messages, "stream": True}
        async with self._client.stream(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                delta = chunk["choices"][0].get("delta", {})
                if "content" in delta:
                    yield delta["content"]
