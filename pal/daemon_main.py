"""Entry point for the PAL daemon process.

Constructs PALAgent, hands it to agent_core.runtime.run_daemon, blocks on the
daemon's serve loop. Signal handling (SIGINT/SIGTERM) is handled by asyncio's
default behavior inside run_daemon.
"""
from agent_core.runtime import run_daemon

from pal.agent import PALAgent
from pal.config import PALConfig


def main() -> None:
    run_daemon(PALAgent(), config_cls=PALConfig)


if __name__ == "__main__":
    main()
