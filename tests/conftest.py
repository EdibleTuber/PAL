"""Shared test fixtures for PAL tests."""
import asyncio
import json
import socket
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse, JSONResponse
from starlette.routing import Route
import uvicorn

from pal.config import Config
from pal.daemon import Daemon


async def mock_chat_completions(request: Request):
    """Mock OpenAI-compatible /v1/chat/completions endpoint."""
    body = await request.json()
    stream = body.get("stream", False)
    messages = body.get("messages", [])
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        "",
    )

    if not stream:
        return JSONResponse({
            "choices": [{"message": {"role": "assistant", "content": f"echo: {last_user}"}}]
        })

    async def generate():
        tokens = [t for t in f"echo: {last_user}".split(" ") if t]
        for i, token in enumerate(tokens):
            prefix = "" if i == 0 else " "
            chunk = {
                "choices": [{
                    "delta": {"content": prefix + token},
                    "finish_reason": None,
                }]
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


mock_app = Starlette(routes=[
    Route("/v1/chat/completions", mock_chat_completions, methods=["POST"]),
])


@pytest.fixture()
async def mock_inference_server() -> AsyncGenerator[str, None]:
    """Start a mock inference server, yield its base URL."""
    # Find a free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(mock_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)

    task = asyncio.create_task(server.serve())
    # Wait for server to start
    while not server.started:
        await asyncio.sleep(0.01)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    await task


@pytest.fixture()
def socket_path(tmp_path) -> Path:
    return tmp_path / "pal-test.sock"


@pytest.fixture()
async def running_daemon(
    socket_path, mock_inference_server
) -> AsyncGenerator[Daemon, None]:
    """Start a daemon with a mock inference backend, yield it, then shut down."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    # Wait for socket to appear
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon
    daemon.shutdown()
    await task
