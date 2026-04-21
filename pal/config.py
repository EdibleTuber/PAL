"""Configuration for PAL daemon and CLI.

Settings are read from environment variables with PAL_ prefix.
Defaults target the standard inference server setup.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_socket_path() -> Path:
    """XDG runtime dir socket, falls back to /run/user/<uid>."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime_dir) / "pal.sock"


@dataclass
class Config:
    inference_url: str = "http://192.168.1.14:11434"
    model: str = "Qwen3.5-35B-A3B-Q4_K_M"
    socket_path: Path = field(default_factory=_default_socket_path)
    history_depth: int = 50
    vault_path: Path = field(default_factory=lambda: Path.home() / "vault")
    collection_id: str = "vault"
    username: str = "user"
    searxng_url: str = "http://192.168.1.14:8080"
    fetch_max_bytes: int = 2_000_000
    fetch_timeout: int = 30
    max_inference_body_chars: int = 20_000
    batch_enabled: bool = False
    batch_inference_url: str = "http://192.168.1.14:11434"
    batch_model: str = "gemma-4-E4B-it-Q4_K_M"


def load_config() -> Config:
    """Load configuration from environment variables."""
    kwargs: dict = {}
    if url := os.environ.get("PAL_INFERENCE_URL"):
        kwargs["inference_url"] = url
    if model := os.environ.get("PAL_MODEL"):
        kwargs["model"] = model
    if sock := os.environ.get("PAL_SOCKET_PATH"):
        kwargs["socket_path"] = Path(sock)
    if depth := os.environ.get("PAL_HISTORY_DEPTH"):
        kwargs["history_depth"] = int(depth)
    if vault := os.environ.get("PAL_VAULT_PATH"):
        kwargs["vault_path"] = Path(vault)
    if cid := os.environ.get("PAL_COLLECTION_ID"):
        kwargs["collection_id"] = cid
    if user := os.environ.get("PAL_USERNAME"):
        kwargs["username"] = user
    if url := os.environ.get("PAL_SEARXNG_URL"):
        kwargs["searxng_url"] = url
    if mb := os.environ.get("PAL_FETCH_MAX_BYTES"):
        kwargs["fetch_max_bytes"] = int(mb)
    if ft := os.environ.get("PAL_FETCH_TIMEOUT"):
        kwargs["fetch_timeout"] = int(ft)
    if mib := os.environ.get("PAL_MAX_INFERENCE_BODY_CHARS"):
        kwargs["max_inference_body_chars"] = int(mib)
    if (v := os.environ.get("PAL_BATCH_ENABLED")) is not None:
        kwargs["batch_enabled"] = v.strip().lower() in ("true", "1", "yes")
    if url := os.environ.get("PAL_BATCH_INFERENCE_URL"):
        kwargs["batch_inference_url"] = url
    if model := os.environ.get("PAL_BATCH_MODEL"):
        kwargs["batch_model"] = model
    return Config(**kwargs)
