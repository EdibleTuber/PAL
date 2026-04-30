"""PAL configuration: subclasses agent_core.config.BaseConfig with PAL-specific fields."""
from __future__ import annotations

from dataclasses import dataclass

from agent_core.config import BaseConfig
from agent_core.config import load_config as _load_base_config


@dataclass
class PALConfig(BaseConfig):
    """PAL-specific configuration. All BaseConfig fields are inherited; we only
    add fields PAL alone needs."""
    max_inference_body_chars: int = 20_000


# Backwards-compatible alias for any code still importing `Config`.
Config = PALConfig


def load_config() -> PALConfig:
    """Load PAL config from PAL_* environment variables."""
    return _load_base_config(PALConfig, agent_name="pal")
