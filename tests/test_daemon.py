"""Tests for the daemon — unix socket server."""
import asyncio
import json

import pytest

from agent_core.protocol import (
    ChatMessage, StreamChunkMessage, ResponseMessage,
    ToolProgressMessage, ErrorMessage, encode_message, decode_message,
)


@pytest.mark.asyncio
async def test_daemon_accepts_connection(running_daemon, socket_path):
    """Daemon accepts a unix socket connection without error."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_daemon_chat_streams_response(running_daemon, socket_path):
    """Send a chat message, receive streaming chunks then a response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))

    msg = ChatMessage(text="hello world")
    writer.write(encode_message(msg))
    await writer.drain()

    chunks = []
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not line:
            break
        decoded = decode_message(line.strip())
        if isinstance(decoded, StreamChunkMessage):
            chunks.append(decoded.token)
        elif isinstance(decoded, ResponseMessage):
            break

    full_response = "".join(chunks)
    assert "echo: hello world" == full_response

    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_daemon_multiple_messages_share_history(running_daemon, socket_path):
    """Consecutive messages within a connection share conversation history."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))

    # Send first message
    writer.write(encode_message(ChatMessage(text="first")))
    await writer.drain()
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        decoded = decode_message(line.strip())
        if isinstance(decoded, ResponseMessage):
            break

    # Send second message
    writer.write(encode_message(ChatMessage(text="second")))
    await writer.drain()
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        decoded = decode_message(line.strip())
        if isinstance(decoded, ResponseMessage):
            # The response message carries the full accumulated text
            assert decoded.text == "echo: second"
            break

    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_daemon_chat_tool_use(running_daemon, socket_path):
    """Chat message that triggers tool use sends progress + final response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))

    msg = ChatMessage(text="TOOLCALL:read_file")
    writer.write(encode_message(msg))
    await writer.drain()

    received = []
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not line:
            break
        decoded = decode_message(line.strip())
        received.append(decoded)
        if isinstance(decoded, (ResponseMessage, ErrorMessage)):
            break

    writer.close()
    await writer.wait_closed()

    progress_msgs = [m for m in received if isinstance(m, ToolProgressMessage)]
    response_msgs = [m for m in received if isinstance(m, ResponseMessage)]
    assert len(progress_msgs) >= 1
    assert progress_msgs[0].tool == "read_file"
    assert len(response_msgs) == 1


# ---------- /model command tests ----------

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture()
async def model_switch_daemon(socket_path, mock_inference_server, tmp_path):
    """Daemon with a tmp vault, for /model + background-op end-to-end tests."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
        searxng_url=mock_inference_server,
        fetch_max_bytes=2_000_000,
        fetch_timeout=10,
        channels_dir=tmp_path / "channels",
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_model_shows_current(running_daemon, socket_path):
    """/model with no args shows the active model."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("model", "")
    assert "test-model" in resp.text
    assert "Model:" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_model_list_marks_active(running_daemon, socket_path):
    """/model list shows all available models with the active one marked."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("model", "list")
    assert "test-model" in resp.text
    assert "gemma-4-26b-a4b-it-q4_k_m" in resp.text
    assert "(active)" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_model_set_switches_active(running_daemon, socket_path):
    """/model <name> switches the active model for all subsequent inference."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("model", "gemma-4-26b-a4b-it-q4_k_m")
    assert "Model set to: gemma-4-26b-a4b-it-q4_k_m" in resp.text

    # Confirm the switch by asking /model again
    resp2 = await client.command("model", "")
    assert "gemma-4-26b-a4b-it-q4_k_m" in resp2.text
    assert "test-model" not in resp2.text.replace("gemma-4-26b-a4b-it-q4_k_m", "")

    # And the daemon's inference client should now report the new model
    assert running_daemon.inference.default_model == "gemma-4-26b-a4b-it-q4_k_m"

    await client.close()


@pytest.mark.asyncio
async def test_model_set_rejects_unknown(running_daemon, socket_path):
    """/model <unknown> returns an error without changing the active model."""
    client = PalClient(socket_path)
    await client.connect()

    original = running_daemon.inference.default_model
    with pytest.raises(RuntimeError, match="not found"):
        await client.command("model", "not-a-real-model")

    # Active model unchanged
    assert running_daemon.inference.default_model == original

    await client.close()


@pytest.mark.asyncio
async def test_model_default_resets_to_config(running_daemon, socket_path):
    """/model default restores the config startup default."""
    client = PalClient(socket_path)
    await client.connect()

    # Switch to a different model first
    await client.command("model", "gemma-4-26b-a4b-it-q4_k_m")
    assert running_daemon.inference.default_model == "gemma-4-26b-a4b-it-q4_k_m"

    # Reset to default
    resp = await client.command("model", "default")
    assert "reset to config default" in resp.text.lower()
    assert running_daemon.inference.default_model == "test-model"

    await client.close()


@pytest.mark.asyncio
async def test_model_switch_routes_summarize_to_new_model(model_switch_daemon, socket_path):
    """/model <name> must propagate to subsequent /summarize inference calls."""
    from agent_core.utils.frontmatter import serialize_frontmatter
    from tests.conftest import REQUEST_LOG

    daemon, vault = model_switch_daemon

    # Write a raw file to the vault for /summarize to consume
    raw_dir = vault / "raw" / "web"
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "source_url": "https://example.com/test",
        "title": "Test Article",
        "fetched_at": "2026-04-05T12:00:00+00:00",
        "content_hash": "abc123",
        "byte_size": 100,
        "status": "raw",
    }
    raw_path = raw_dir / "test.md"
    raw_path.write_text(serialize_frontmatter(meta, "This is the article body.\n"))

    client = PalClient(socket_path)
    await client.connect()

    # Switch the active model
    await client.command("model", "gemma-4-26b-a4b-it-q4_k_m")

    # Clear any requests captured during /model validation + switch
    REQUEST_LOG.clear()

    # Trigger /summarize - this should hit the inference server with the new model
    await client.command("summarize", "raw/web/test.md")

    await client.close()

    # Every captured chat/completions request must carry the switched model
    assert len(REQUEST_LOG) >= 1, "expected at least one inference request during /summarize"
    for entry in REQUEST_LOG:
        assert entry.get("model") == "gemma-4-26b-a4b-it-q4_k_m", (
            f"expected model=gemma-4-26b-a4b-it-q4_k_m, got {entry.get('model')}"
        )


@pytest.mark.asyncio
async def test_model_switch_routes_compile_to_new_model(model_switch_daemon, socket_path):
    """/model <name> must propagate across all inference calls inside /compile.

    /compile makes multiple inference calls: categorize, then compile (and
    topic-match when applicable). Every call must use the switched model.
    """
    from agent_core.utils.frontmatter import serialize_frontmatter
    from tests.conftest import REQUEST_LOG

    daemon, vault = model_switch_daemon

    # Write a summary file to the vault for /compile to consume
    summaries_dir = vault / "raw" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_meta = {
        "title": "Test Article",
        "source_url": "https://example.com/test",
        "source_raw": "raw/web/test.md",
        "source_hash": "abc123",
        "summarized_at": "2026-04-05T12:00:00+00:00",
        "sanitization_issues": [],
        "status": "summary",
    }
    summary_path = summaries_dir / "test.md"
    summary_path.write_text(serialize_frontmatter(summary_meta, "Summary body about the article.\n"))

    client = PalClient(socket_path)
    await client.connect()

    # Switch the active model
    await client.command("model", "gemma-4-26b-a4b-it-q4_k_m")

    # Clear any requests captured during /model validation + switch
    REQUEST_LOG.clear()

    # Trigger /compile - this hits inference server multiple times
    await client.command("compile", "raw/summaries/test.md")

    await client.close()

    # /compile must produce at least 2 inference requests (categorize + compile);
    # with an empty target directory, topic matching short-circuits without a model call.
    assert len(REQUEST_LOG) >= 2, (
        f"expected at least 2 inference requests during /compile, got {len(REQUEST_LOG)}"
    )
    for entry in REQUEST_LOG:
        assert entry.get("model") == "gemma-4-26b-a4b-it-q4_k_m", (
            f"expected model=gemma-4-26b-a4b-it-q4_k_m on every request, "
            f"got {entry.get('model')}"
        )
