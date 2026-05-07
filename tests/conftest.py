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

from agent_core.allowlist import AllowlistManager
from agent_core.approval_registry import ApprovalRegistry
from agent_core.channels import ChannelStore
from agent_core.daemon import Daemon
from agent_core.inference import InferenceClient
from agent_core.learning import LearningManager
from agent_core.profile import ProfileManager
from agent_core.retrieval import RetrievalClient
from agent_core.runtime import _attach_registries
from agent_core.utils.fetcher import URLFetcher
from agent_core.websearch import WebSearchClient
from agent_core.wisdom import WisdomManager

from pal.agent import PALAgent
from pal.config import PALConfig as Config

# Captures every /v1/chat/completions body the daemon sends to the mock server.
# Tests that care about what model/payload hit the wire read from this list.
# Cleared automatically before each test by the autouse _clear_request_log fixture.
REQUEST_LOG: list[dict] = []


async def mock_chat_completions(request: Request):
    """Mock OpenAI-compatible /v1/chat/completions endpoint."""
    body = await request.json()
    REQUEST_LOG.append(body)
    stream = body.get("stream", False)
    messages = body.get("messages", [])
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        "",
    )

    # If the messages contain a tool result, respond with text (loop completion)
    has_tool_result = any(m.get("role") == "tool" for m in messages)
    if has_tool_result:
        tool_content = next(
            (m["content"] for m in messages if m.get("role") == "tool"), ""
        )
        summary = tool_content[:50] if tool_content else "no content"
        if not stream:
            return JSONResponse({
                "choices": [{"message": {"role": "assistant", "content": f"Tool result: {summary}"}}]
            })
        async def generate_after_tool():
            text = f"Tool result: {summary}"
            tokens = text.split(" ")
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
        return StreamingResponse(generate_after_tool(), media_type="text/event-stream")

    # If tools are provided and message starts with TOOLCALL:, return a tool call
    tools = body.get("tools", [])
    if tools and last_user.startswith("TOOLCALL:"):
        tool_name = last_user.split(":", 1)[1].strip()
        if not stream:
            return JSONResponse({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": '{"path": "Research/quantum.md"}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }]
            })
        async def generate_tool():
            chunk = {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": '{"path": "Res',
                            },
                        }]
                    },
                    "finish_reason": None,
                }]
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            chunk2 = {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "function": {
                                "arguments": 'earch/quantum.md"}',
                            },
                        }]
                    },
                    "finish_reason": None,
                }]
            }
            yield f"data: {json.dumps(chunk2)}\n\n"
            done_chunk = {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}]
            }
            yield f"data: {json.dumps(done_chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(generate_tool(), media_type="text/event-stream")

    # If message starts with REASON:, return a response with reasoning_content
    if last_user.startswith("REASON:"):
        actual_query = last_user.split(":", 1)[1].strip()
        if not stream:
            return JSONResponse({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": f"answer: {actual_query}",
                        "reasoning_content": f"thinking about {actual_query}",
                    },
                    "finish_reason": "stop",
                }]
            })

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
    """Mock SearxNG /search endpoint.

    Returns URLs pointing back to the mock server so research tests
    can actually fetch them.
    """
    query = request.query_params.get("q", "")
    base = str(request.base_url).rstrip("/")
    return JSONResponse({
        "query": query,
        "results": [
            {
                "url": f"{base}/page.html?topic={query.replace(' ', '-')}&src=1",
                "title": f"{query} - Overview",
                "content": f"Overview of {query}.",
            },
            {
                "url": f"{base}/page.html?topic={query.replace(' ', '-')}&src=2",
                "title": f"{query} - Tutorial",
                "content": f"Tutorial on {query}.",
            },
            {
                "url": f"{base}/page.html?topic={query.replace(' ', '-')}&src=3",
                "title": f"{query} - Reference",
                "content": f"Reference for {query}.",
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


async def mock_page_redirect(request: Request):
    return Response(
        "",
        status_code=302,
        headers={"location": "http://internal-service:9999/admin"},
    )


async def mock_page_no_content_type(request: Request):
    # Starlette sets a default content-type if we don't override — use raw 200
    return Response(
        "<html><body>no content-type</body></html>",
        media_type=None,
        headers={"content-type": ""},
    )


async def mock_list_models(request: Request):
    """Mock /v1/models endpoint."""
    return JSONResponse({
        "object": "list",
        "data": [
            {"id": "test-model", "object": "model", "created": 0, "owned_by": "local"},
            {"id": "gemma-4-26b-a4b-it-q4_k_m", "object": "model", "created": 0, "owned_by": "local"},
        ],
    })


async def mock_page_with_code(request: Request):
    """Return an HTML page containing a code block."""
    return Response(
        "<html><head><title>Code Example</title></head>"
        "<body>"
        "<article>"
        "<h1>Code Tutorial</h1>"
        "<p>This tutorial shows a simple function. Here is example code for a greeting function.</p>"
        "<p>The function below demonstrates basic Python syntax and string formatting.</p>"
        "<pre><code>def hello(name):\n    return f\"Hello, {name}!\"\n\nprint(hello(\"world\"))</code></pre>"
        "<p>This function takes a name parameter and returns a formatted greeting string.</p>"
        "<p>You can call it with any name to get a personalized greeting message.</p>"
        "</article>"
        "</body></html>",
        media_type="text/html",
    )


mock_app = Starlette(routes=[
    Route("/v1/chat/completions", mock_chat_completions, methods=["POST"]),
    Route("/v1/models", mock_list_models, methods=["GET"]),
    Route("/collections/{collection_id}/search", mock_collection_search, methods=["POST"]),
    Route("/collections/{collection_id}/docs/{doc_id:path}", mock_collection_get_doc, methods=["GET"]),
    Route("/search", mock_searxng_search, methods=["GET"]),
    Route("/page.html", mock_page_html, methods=["GET"]),
    Route("/too-large", mock_page_too_large, methods=["GET"]),
    Route("/binary", mock_page_binary, methods=["GET"]),
    Route("/missing", mock_page_404, methods=["GET"]),
    Route("/redirect", mock_page_redirect, methods=["GET"]),
    Route("/no-content-type", mock_page_no_content_type, methods=["GET"]),
    Route("/page-with-code.html", mock_page_with_code, methods=["GET"]),
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


def make_pal_agent(cfg: Config) -> PALAgent:
    """Construct a fully-wired PALAgent for tests, mimicking run_daemon's setup.

    Used by the ``running_daemon`` fixture and by per-file fixtures that need a
    Daemon-shaped object exposing PAL infrastructure (wiki, inference, compiler,
    tool_executor, etc.) post Phase E.
    """
    agent = PALAgent()
    agent.config = cfg
    agent.profile = ProfileManager(
        cfg.vault_path, agent_name="pal", username=cfg.username,
    )
    agent.wisdom = WisdomManager(cfg.vault_path, agent_name="pal")
    agent.learning = LearningManager(cfg.vault_path, agent_name="pal")
    agent.allowlist = AllowlistManager(cfg.vault_path, agent_name="pal")
    agent.approval_registry = ApprovalRegistry()
    agent.channels = ChannelStore(
        vault_path=cfg.vault_path,
        agent_name="pal",
        history_depth=cfg.history_depth,
    )
    agent.inference = InferenceClient(
        base_url=cfg.inference_url, model=cfg.model,
    )
    agent.retrieval = RetrievalClient(
        base_url=cfg.inference_url, collection_id=cfg.collection_id,
    )
    agent.websearch = WebSearchClient(base_url=cfg.searxng_url)
    # Phase F: _attach_registries requires fetcher to be present (FetchUrl.requires).
    # Mirror run_daemon's order: set fetcher, call _attach_registries, then setup().
    # PAL's setup() overwrites fetcher with its own instance; that's fine.
    agent.fetcher = URLFetcher(
        max_bytes=cfg.fetch_max_bytes,
        timeout=cfg.fetch_timeout,
    )
    # Mirror run_daemon's order: setup() first (builds domain managers like
    # researcher, compiler, etc.), then _attach_registries (validates
    # tool `requires` against the fully-built agent).
    agent.setup()
    _attach_registries(agent)
    # Old pal.daemon.Daemon.__init__ seeded the allowlist eagerly. Phase E
    # runtime does not (yet); preserve the legacy behaviour for tests so
    # the seeding-on-first-use coverage remains green.
    agent.allowlist.seed()
    return agent


async def start_pal_daemon(agent: PALAgent) -> asyncio.Task:
    """Start a Daemon serve loop for the given agent, return the task.

    The caller is responsible for waiting for ``agent.config.socket_path`` to
    appear and for cancelling the task on teardown.
    """
    daemon = Daemon(agent)
    return asyncio.create_task(daemon.serve())


@pytest.fixture()
async def running_daemon(
    socket_path, mock_inference_server, tmp_path
) -> AsyncGenerator[PALAgent, None]:
    """Start a daemon with a mock inference backend, yield the underlying agent.

    Phase E migration: the daemon is now in agent_core and is transport-only;
    PAL's chat/command/system-prompt logic lives on PALAgent. Tests that
    previously reached into ``daemon.tools`` / ``daemon.config`` etc. need the
    agent, so this fixture yields the agent. The serve task is cancelled on
    teardown.
    """
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
    )
    agent = make_pal_agent(cfg)
    task = await start_pal_daemon(agent)
    # Wait for socket to appear
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield agent

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.fixture(autouse=True)
def _clear_request_log():
    """Clear REQUEST_LOG before each test to prevent cross-test leakage."""
    REQUEST_LOG.clear()
    yield
