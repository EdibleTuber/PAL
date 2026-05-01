"""Tests for per-channel routing in the daemon."""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_two_channels_get_separate_conversations(tmp_path):
    """Messages on C1 and C2 populate different Conversation instances."""
    from agent_core.channels import ChannelStore

    store = ChannelStore(vault_path=tmp_path, agent_name="pal", history_depth=50)
    conv1 = await store.get_or_create("C1")
    conv1.add_user("hello from C1")
    conv2 = await store.get_or_create("C2")
    conv2.add_user("hello from C2")

    assert conv1.messages != conv2.messages
    assert conv1.messages[0]["content"] == "hello from C1"
    assert conv2.messages[0]["content"] == "hello from C2"


@pytest.mark.asyncio
async def test_channel_id_none_falls_back_to_cli_default(tmp_path):
    from agent_core.daemon import resolve_channel_id
    assert resolve_channel_id(None) == "cli-default"
    assert resolve_channel_id("") == "cli-default"
    assert resolve_channel_id("C1") == "C1"


@pytest.mark.asyncio
async def test_channel_id_invalid_falls_back_to_cli_default_with_log(tmp_path, caplog):
    import logging
    from agent_core.daemon import resolve_channel_id
    with caplog.at_level(logging.WARNING):
        resolved = resolve_channel_id("../evil")
    assert resolved == "cli-default"
    assert any("invalid" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_daemon_restart_replays_history(tmp_path):
    """Simulate restart: create store, drop it, create a new store on same dir."""
    from agent_core.channels import ChannelStore

    store1 = ChannelStore(vault_path=tmp_path, agent_name="pal", history_depth=50)
    conv1 = await store1.get_or_create("C1")
    conv1.add_user("turn 1")
    conv1.add_assistant("turn 2")

    del store1
    store2 = ChannelStore(vault_path=tmp_path, agent_name="pal", history_depth=50)
    conv2 = await store2.get_or_create("C1")

    assert len(conv2.messages) == 2
    assert conv2.messages[0]["content"] == "turn 1"
    assert conv2.messages[1]["content"] == "turn 2"
