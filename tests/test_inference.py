"""Tests for the inference server HTTP client."""
import pytest

from pal.inference import InferenceClient


@pytest.mark.asyncio
async def test_complete_non_streaming(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "hello world"}],
        stream=False,
    )
    assert result == "echo: hello world"


@pytest.mark.asyncio
async def test_complete_streaming(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    tokens = []
    async for token in client.stream(
        messages=[{"role": "user", "content": "hello world"}],
    ):
        tokens.append(token)
    full = "".join(tokens)
    assert full == "echo: hello world"


@pytest.mark.asyncio
async def test_complete_streaming_empty_response(mock_inference_server):
    """Streaming an empty user message still produces output."""
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    tokens = []
    async for token in client.stream(
        messages=[{"role": "user", "content": ""}],
    ):
        tokens.append(token)
    full = "".join(tokens)
    assert full == "echo:"
