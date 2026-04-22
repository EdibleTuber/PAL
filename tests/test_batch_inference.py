"""Tests for batch-specific inference failure classification."""
import httpx
import pytest
from unittest.mock import AsyncMock

from pal.inference import InferenceClient, BatchUnavailableError
from pal.daemon import Daemon
from pal.config import Config


@pytest.mark.asyncio
async def test_batch_client_raises_batch_unavailable_on_connect_error(monkeypatch):
    client = InferenceClient(
        base_url="http://127.0.0.1:9999",
        model="test-batch-model",
        is_batch=True,
    )

    async def fake_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(BatchUnavailableError, match="connection refused"):
        await client.complete([{"role": "user", "content": "x"}])

    await client.close()


@pytest.mark.asyncio
async def test_batch_client_raises_batch_unavailable_on_503(monkeypatch):
    client = InferenceClient(
        base_url="http://127.0.0.1:9999",
        model="test-batch-model",
        is_batch=True,
    )

    async def fake_post(*args, **kwargs):
        request = httpx.Request("POST", "http://test/")
        return httpx.Response(status_code=503, request=request, text="batch slot unhealthy")

    monkeypatch.setattr(client._client, "post", fake_post)
    # Disable retry sleep to keep test fast.
    monkeypatch.setattr("pal.inference._INITIAL_BACKOFF", 0)
    monkeypatch.setattr("pal.inference._MAX_BACKOFF", 0)

    with pytest.raises(BatchUnavailableError):
        await client.complete([{"role": "user", "content": "x"}])

    await client.close()


@pytest.mark.asyncio
async def test_non_batch_client_raises_original_exception(monkeypatch):
    """A non-batch InferenceClient should raise the original httpx error,
    not wrap it as BatchUnavailableError."""
    client = InferenceClient(
        base_url="http://127.0.0.1:9999",
        model="test-main-model",
    )

    async def fake_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(httpx.ConnectError):
        await client.complete([{"role": "user", "content": "x"}])

    await client.close()


@pytest.mark.asyncio
async def test_batch_client_raises_batch_unavailable_on_remote_protocol_error(monkeypatch):
    """RemoteProtocolError (TCP RST mid-response, e.g. OOM kill) is a
    subclass of TransportError and should be classified as batch
    unavailable for batch clients."""
    client = InferenceClient(
        base_url="http://127.0.0.1:9999",
        model="test-batch-model",
        is_batch=True,
    )

    async def fake_post(*args, **kwargs):
        raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(BatchUnavailableError, match="RemoteProtocolError"):
        await client.complete([{"role": "user", "content": "x"}])

    await client.close()


@pytest.mark.asyncio
async def test_batch_client_raises_batch_unavailable_on_read_error(monkeypatch):
    """ReadError (NetworkError subclass) is a TransportError and should
    be classified as batch unavailable for batch clients."""
    client = InferenceClient(
        base_url="http://127.0.0.1:9999",
        model="test-batch-model",
        is_batch=True,
    )

    async def fake_post(*args, **kwargs):
        raise httpx.ReadError("read failed")

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(BatchUnavailableError, match="ReadError"):
        await client.complete([{"role": "user", "content": "x"}])

    await client.close()


def test_daemon_categorizer_uses_main_when_batch_disabled(tmp_path):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=False,
        channels_dir=tmp_path / "channels",
    )
    daemon = Daemon(cfg)
    assert daemon.categorizer.inference is daemon.inference
    assert daemon.batch_inference is None


def test_daemon_categorizer_uses_batch_when_enabled(tmp_path):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
        channels_dir=tmp_path / "channels",
    )
    daemon = Daemon(cfg)
    assert daemon.batch_inference is not None
    assert daemon.categorizer.inference is daemon.batch_inference
