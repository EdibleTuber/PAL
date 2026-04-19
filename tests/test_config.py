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


def test_default_config_has_username():
    cfg = Config()
    assert cfg.username == "user"


def test_load_config_username_from_env(monkeypatch):
    monkeypatch.setenv("PAL_USERNAME", "edible")
    for key in ["PAL_INFERENCE_URL", "PAL_MODEL", "PAL_SOCKET_PATH", "PAL_HISTORY_DEPTH", "PAL_VAULT_PATH", "PAL_COLLECTION_ID"]:
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.username == "edible"


def test_default_config_has_web_settings():
    cfg = Config()
    assert cfg.searxng_url == "http://192.168.1.14:8080"
    assert cfg.fetch_max_bytes == 2_000_000
    assert cfg.fetch_timeout == 30


def test_load_config_web_settings_from_env(monkeypatch):
    monkeypatch.setenv("PAL_SEARXNG_URL", "http://localhost:9999")
    monkeypatch.setenv("PAL_FETCH_MAX_BYTES", "500000")
    monkeypatch.setenv("PAL_FETCH_TIMEOUT", "10")
    for key in ["PAL_INFERENCE_URL", "PAL_MODEL", "PAL_SOCKET_PATH", "PAL_HISTORY_DEPTH", "PAL_VAULT_PATH", "PAL_COLLECTION_ID", "PAL_USERNAME"]:
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.searxng_url == "http://localhost:9999"
    assert cfg.fetch_max_bytes == 500_000
    assert cfg.fetch_timeout == 10


def test_config_default_batch_disabled():
    cfg = Config()
    assert cfg.batch_enabled is False
    assert cfg.batch_inference_url == "http://192.168.1.14:11434"
    assert cfg.batch_model == "gemma-3-4b-it-q4_k_m"


def test_config_env_enables_batch(monkeypatch):
    monkeypatch.setenv("PAL_BATCH_ENABLED", "true")
    monkeypatch.setenv("PAL_BATCH_INFERENCE_URL", "http://localhost:9000")
    monkeypatch.setenv("PAL_BATCH_MODEL", "qwen3-4b-instruct")
    cfg = load_config()
    assert cfg.batch_enabled is True
    assert cfg.batch_inference_url == "http://localhost:9000"
    assert cfg.batch_model == "qwen3-4b-instruct"


def test_config_env_batch_enabled_falsy_values(monkeypatch):
    """PAL_BATCH_ENABLED only enables on explicit true-ish strings."""
    for v in ("false", "0", "no", ""):
        monkeypatch.setenv("PAL_BATCH_ENABLED", v)
        cfg = load_config()
        assert cfg.batch_enabled is False, f"value {v!r} should leave batch disabled"
    for v in ("true", "1", "yes", "TRUE"):
        monkeypatch.setenv("PAL_BATCH_ENABLED", v)
        cfg = load_config()
        assert cfg.batch_enabled is True, f"value {v!r} should enable batch"
