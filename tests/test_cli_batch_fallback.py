"""Tests for CLI handling of BatchFallbackProposal."""
import json

import pytest

from pal.cli import format_batch_fallback_proposal
from pal.protocol import (
    BatchFallbackApprovalMessage,
    BatchFallbackProposal,
    decode_message,
    encode_message,
)


def test_format_batch_fallback_proposal_includes_caller_and_context():
    msg = BatchFallbackProposal(
        proposal_id="p1",
        caller="categorizer",
        context="categorizing compile for raw/summaries/X.md",
        original_request={"messages": [{"role": "user", "content": "hi"}]},
    )
    text = format_batch_fallback_proposal(msg)
    assert "categorizer" in text
    assert "raw/summaries/X.md" in text
    assert "[r]" in text.lower()
    assert "[m]" in text.lower()
    assert "[s]" in text.lower()


def test_format_batch_fallback_proposal_llm_toc_caller():
    msg = BatchFallbackProposal(
        proposal_id="p2",
        caller="llm_toc",
        context="detecting chapters for book.pdf",
        original_request={},
    )
    text = format_batch_fallback_proposal(msg)
    assert "llm_toc" in text
    assert "book.pdf" in text


@pytest.mark.asyncio
async def test_client_send_batch_fallback_approval_writes_correct_wire():
    """Verify the client sends a well-formed BatchFallbackApprovalMessage
    over the wire when the user picks `retry`.
    """
    from pal.client import PalClient

    class FakeWriter:
        def __init__(self) -> None:
            self.writes: list[bytes] = []
            self.drained = 0

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        async def drain(self) -> None:
            self.drained += 1

    client = PalClient.__new__(PalClient)
    client._writer = FakeWriter()
    msg = BatchFallbackApprovalMessage(proposal_id="pid123", choice="retry")
    await client.send(msg)

    assert client._writer.drained == 1
    assert len(client._writer.writes) == 1
    wire = client._writer.writes[0]
    data = json.loads(wire.decode().strip())
    assert data["type"] == "batch_fallback_approval"
    assert data["proposal_id"] == "pid123"
    assert data["choice"] == "retry"


@pytest.mark.asyncio
async def test_client_send_batch_fallback_approval_main_and_skip():
    """Same as above but for the `main` and `skip` choices."""
    from pal.client import PalClient

    class FakeWriter:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        async def drain(self) -> None:
            pass

    for choice in ("main", "skip"):
        client = PalClient.__new__(PalClient)
        client._writer = FakeWriter()
        await client.send(BatchFallbackApprovalMessage(proposal_id="pX", choice=choice))
        data = json.loads(client._writer.writes[0].decode().strip())
        assert data["type"] == "batch_fallback_approval"
        assert data["choice"] == choice


def test_batch_fallback_approval_message_wire_round_trip():
    """Ensure the approval message survives encode/decode unchanged."""
    msg = BatchFallbackApprovalMessage(proposal_id="roundtrip", choice="main")
    restored = decode_message(encode_message(msg))
    assert isinstance(restored, BatchFallbackApprovalMessage)
    assert restored.proposal_id == "roundtrip"
    assert restored.choice == "main"
