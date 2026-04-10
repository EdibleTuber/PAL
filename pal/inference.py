"""HTTP client for the inference server's OpenAI-compatible API.

Supports both streaming (SSE) and non-streaming completions via
POST /v1/chat/completions. Tool-aware: can pass tool definitions
and parse tool-call responses.
"""
import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_INITIAL_BACKOFF = 2.0
_MAX_BACKOFF = 30.0


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class CompletionResult:
    type: str  # "text" or "tool_calls"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class InferenceClient:
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=600.0)

    async def close(self) -> None:
        await self._client.aclose()

    @asynccontextmanager
    async def _stream_with_retry(
        self, url: str, payload: dict
    ) -> AsyncGenerator[httpx.Response, None]:
        """Open a streaming POST, retrying on 503 before yielding."""
        backoff = _INITIAL_BACKOFF
        for attempt in range(_MAX_RETRIES):
            async with self._client.stream("POST", url, json=payload) as resp:
                if resp.status_code != 503:
                    resp.raise_for_status()
                    yield resp
                    return
                retry_after = resp.headers.get("Retry-After")
            wait = min(float(retry_after) if retry_after else backoff, _MAX_BACKOFF)
            logger.warning(
                "503 from inference server on stream (attempt %d/%d), retrying in %.1fs",
                attempt + 1, _MAX_RETRIES, wait,
            )
            await asyncio.sleep(wait)
            backoff = min(backoff * 2, _MAX_BACKOFF)
        # Final attempt
        async with self._client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            yield resp

    async def _post_with_retry(self, payload: dict) -> httpx.Response:
        """POST to /v1/chat/completions with exponential backoff on 503."""
        url = f"{self.base_url}/v1/chat/completions"
        backoff = _INITIAL_BACKOFF
        for attempt in range(_MAX_RETRIES):
            resp = await self._client.post(url, json=payload)
            if resp.status_code != 503:
                resp.raise_for_status()
                return resp
            retry_after = float(resp.headers.get("Retry-After", backoff))
            wait = min(retry_after, _MAX_BACKOFF)
            logger.warning(
                "503 from inference server (attempt %d/%d), retrying in %.1fs",
                attempt + 1, _MAX_RETRIES, wait,
            )
            await asyncio.sleep(wait)
            backoff = min(backoff * 2, _MAX_BACKOFF)
        # Final attempt - let it raise on any error
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        return resp

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> CompletionResult:
        """Send a non-streaming completion request.

        Returns a CompletionResult indicating either a text response
        or a list of tool calls the model wants to make.
        """
        payload: dict = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        resp = await self._post_with_retry(payload)
        data = resp.json()
        message = data["choices"][0]["message"]

        raw_calls = message.get("tool_calls")
        if raw_calls:
            parsed = []
            for tc in raw_calls:
                func = tc["function"]
                args = func["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                parsed.append(ToolCall(
                    id=tc["id"],
                    name=func["name"],
                    arguments=args,
                ))
            return CompletionResult(type="tool_calls", tool_calls=parsed)

        return CompletionResult(type="text", content=message.get("content", ""))

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncGenerator[str | list[ToolCall], None]:
        """Send a streaming completion request.

        Yields str tokens for text responses. If the model returns tool calls
        instead, accumulates all tool-call deltas and yields a single
        list[ToolCall] as the only item.
        """
        payload: dict = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # Accumulators for tool-call deltas
        tool_call_acc: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        is_tool_response = False
        url = f"{self.base_url}/v1/chat/completions"

        async with self._stream_with_retry(url, payload) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                delta = chunk["choices"][0].get("delta", {})

                # Check for tool call deltas
                tc_deltas = delta.get("tool_calls")
                if tc_deltas is not None:
                    is_tool_response = True
                    for tcd in tc_deltas:
                        idx = tcd.get("index", 0)
                        if idx not in tool_call_acc:
                            tool_call_acc[idx] = {
                                "id": tcd.get("id", ""),
                                "name": "",
                                "arguments_str": "",
                            }
                        acc = tool_call_acc[idx]
                        if tcd.get("id"):
                            acc["id"] = tcd["id"]
                        func = tcd.get("function", {})
                        if func.get("name"):
                            acc["name"] = func["name"]
                        if func.get("arguments"):
                            acc["arguments_str"] += func["arguments"]
                    continue

                # Regular text content
                content = delta.get("content")
                if content is not None:
                    yield content

        # If we accumulated tool calls, yield them as a single list
        if is_tool_response and tool_call_acc:
            calls = []
            for idx in sorted(tool_call_acc):
                acc = tool_call_acc[idx]
                args = json.loads(acc["arguments_str"]) if acc["arguments_str"] else {}
                calls.append(ToolCall(
                    id=acc["id"],
                    name=acc["name"],
                    arguments=args,
                ))
            yield calls
