"""Tests for configuration loading."""
import os
from pathlib import Path

from pal.config import Config, load_config


def test_default_config():
    cfg = Config()
    assert cfg.inference_url == "http://192.168.1.14:11434"
    assert cfg.model == "Qwen3.5-35B-A3B-Q4_K_M"
    assert cfg.socket_path == Path("/run/user") / str(os.getuid()) / "pal.sock"
    assert cfg.history_depth == 50
    assert cfg.vault_path == Path.home() / "vault"


def test_load_config_from_env(monkeypatch, tmp_path):
    sock = tmp_path / "test.sock"
    monkeypatch.setenv("PAL_INFERENCE_URL", "http://localhost:9999")
    monkeypatch.setenv("PAL_MODEL", "llama3")
    monkeypatch.setenv("PAL_SOCKET_PATH", str(sock))
    monkeypatch.setenv("PAL_HISTORY_DEPTH", "20")
    monkeypatch.setenv("PAL_VAULT_PATH", "/tmp/testvault")
    cfg = load_config()
    assert cfg.inference_url == "http://localhost:9999"
    assert cfg.model == "llama3"
    assert cfg.socket_path == sock
    assert cfg.history_depth == 20
    assert cfg.vault_path == Path("/tmp/testvault")


def test_load_config_defaults_without_env(monkeypatch):
    for key in ["PAL_INFERENCE_URL", "PAL_MODEL", "PAL_SOCKET_PATH", "PAL_HISTORY_DEPTH", "PAL_VAULT_PATH"]:
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.inference_url == "http://192.168.1.14:11434"
    assert cfg.model == "Qwen3.5-35B-A3B-Q4_K_M"


def test_default_config_has_collection_id():
    cfg = Config()
    assert cfg.collection_id == "vault"


def test_load_config_collection_id_from_env(monkeypatch):
    monkeypatch.setenv("PAL_COLLECTION_ID", "my-vault")
    for key in ["PAL_INFERENCE_URL", "PAL_MODEL", "PAL_SOCKET_PATH", "PAL_HISTORY_DEPTH", "PAL_VAULT_PATH"]:
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.collection_id == "my-vault"
