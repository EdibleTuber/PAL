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
    model: str = "qwen3.5"
    socket_path: Path = field(default_factory=_default_socket_path)
    history_depth: int = 50
    vault_path: Path = field(default_factory=lambda: Path.home() / "vault")


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
    return Config(**kwargs)
