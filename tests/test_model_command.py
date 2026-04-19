"""Tests for /model command status and dual-slot rendering."""
import asyncio
import pytest
from unittest.mock import AsyncMock

from pal.daemon import Daemon
from pal.config import Config


@pytest.mark.asyncio
async def test_model_status_text_shows_main_only_when_batch_disabled(tmp_path, monkeypatch):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=False,
    )
    daemon = Daemon(cfg)

    async def fake_status():
        return {
            "slots": {
                "main": {"loaded_model": "gemma-4-26b-a4b-it-q4_k_m", "healthy": True},
            }
        }

    monkeypatch.setattr(daemon, "_get_manager_status", fake_status)
    text = await daemon._model_status_text()
    assert "main: gemma-4-26b" in text
    assert "batch" not in text.lower()


@pytest.mark.asyncio
async def test_model_status_text_shows_both_when_batch_enabled(tmp_path, monkeypatch):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    daemon = Daemon(cfg)

    async def fake_status():
        return {
            "slots": {
                "main": {"loaded_model": "gemma-4-26b-a4b-it-q4_k_m", "healthy": True},
                "batch": {"loaded_model": "gemma-3-4b-it-q4_k_m", "healthy": True},
            }
        }

    monkeypatch.setattr(daemon, "_get_manager_status", fake_status)
    text = await daemon._model_status_text()
    assert "main: gemma-4-26b" in text
    assert "batch: gemma-3-4b" in text


@pytest.mark.asyncio
async def test_model_status_text_handles_missing_slots(tmp_path, monkeypatch):
    """If the manager doesn't expose slots (pre-Phase B server), fall back
    to showing the configured model name."""
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    daemon = Daemon(cfg)

    async def fake_status():
        return {}  # no slots

    monkeypatch.setattr(daemon, "_get_manager_status", fake_status)
    text = await daemon._model_status_text()
    assert "main:" in text
    assert "batch:" in text
    assert "slot info unavailable" in text


@pytest.mark.asyncio
async def test_model_status_text_unhealthy_slot_marked(tmp_path, monkeypatch):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    daemon = Daemon(cfg)

    async def fake_status():
        return {
            "slots": {
                "main": {"loaded_model": "gemma-4", "healthy": True},
                "batch": {"loaded_model": "gemma-3-4b", "healthy": False},
            }
        }

    monkeypatch.setattr(daemon, "_get_manager_status", fake_status)
    text = await daemon._model_status_text()
    assert "UNHEALTHY" in text
    assert "batch: gemma-3-4b (UNHEALTHY)" in text
