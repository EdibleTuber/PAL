"""Shared test fixtures for PAL tests."""
import asyncio
import json
import socket
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse, JSONResponse, Response
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


async def mock_collection_search(request: Request):
    """Mock POST /collections/{collection_id}/search endpoint."""
    body = await request.json()
    query = body.get("query", "")
    limit = body.get("limit", 5)
    results = [
        {
            "id": f"doc-{i}",
            "name": f"Document {i}",
            "collection": request.path_params["collection_id"],
            "summary": f"Summary for {query} result {i}",
            "tags": ["mock"],
            "score": 0.9 - (i * 0.1),
        }
        for i in range(min(limit, 3))
    ]
    return JSONResponse({"results": results})


async def mock_collection_get_doc(request: Request):
    """Mock GET /collections/{collection_id}/docs/{doc_id} endpoint."""
    doc_id = request.path_params["doc_id"]
    collection_id = request.path_params["collection_id"]
    if doc_id == "missing":
        return JSONResponse({"error": f"Document not found: {doc_id}"}, status_code=404)
    return JSONResponse({
        "id": doc_id,
        "name": f"Name of {doc_id}",
        "collection": collection_id,
        "summary": f"Summary of {doc_id}",
        "content": f"# {doc_id}\n\nFull content of {doc_id}.\n",
        "metadata": {"tags": ["mock"]},
    })


async def mock_searxng_search(request: Request):
    """Mock SearxNG /search endpoint."""
    query = request.query_params.get("q", "")
    return JSONResponse({
        "query": query,
        "results": [
            {
                "url": f"https://wikipedia.org/wiki/{query.replace(' ', '_')}",
                "title": f"{query} - Wikipedia",
                "content": f"Wikipedia snippet about {query}.",
            },
            {
                "url": f"https://arxiv.org/abs/2301.00001",
                "title": f"Research on {query}",
                "content": f"Abstract mentioning {query}.",
            },
            {
                "url": "https://evil.example.com/junk",
                "title": "Not allowlisted",
                "content": "Should be filtered by allowlist.",
            },
        ],
    })


async def mock_page_html(request: Request):
    """Return a basic HTML page for fetcher tests."""
    return Response(
        "<html><head><title>Test Page</title></head>"
        "<body>"
        "<nav id=\"navigation\"><ul><li>Home</li><li>About</li><li>Nav junk</li></ul></nav>"
        "<main><article id=\"content\">"
        "<h1>Test Article</h1>"
        "<p>This is the main content. Extract me. This paragraph contains important information.</p>"
        "<p>Second paragraph with more main content for the article body extraction test.</p>"
        "<p>Third paragraph with additional content to ensure trafilatura picks this as main.</p>"
        "<p>Fourth paragraph confirming this is the primary content zone of the page.</p>"
        "</article></main>"
        "<footer id=\"footer\"><p>Footer junk copyright 2024</p></footer>"
        "</body></html>",
        media_type="text/html",
    )


async def mock_page_too_large(request: Request):
    """Return a response with a too-large Content-Length."""
    return Response(
        "tiny body",
        media_type="text/html",
        headers={"Content-Length": "999999999"},
    )


async def mock_page_binary(request: Request):
    """Return a binary content-type."""
    return Response(
        b"\x00\x01\x02\x03",
        media_type="application/octet-stream",
    )


async def mock_page_404(request: Request):
    return Response("not found", status_code=404)


mock_app = Starlette(routes=[
    Route("/v1/chat/completions", mock_chat_completions, methods=["POST"]),
    Route("/collections/{collection_id}/search", mock_collection_search, methods=["POST"]),
    Route("/collections/{collection_id}/docs/{doc_id:path}", mock_collection_get_doc, methods=["GET"]),
    Route("/search", mock_searxng_search, methods=["GET"]),
    Route("/page.html", mock_page_html, methods=["GET"]),
    Route("/too-large", mock_page_too_large, methods=["GET"]),
    Route("/binary", mock_page_binary, methods=["GET"]),
    Route("/missing", mock_page_404, methods=["GET"]),
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
