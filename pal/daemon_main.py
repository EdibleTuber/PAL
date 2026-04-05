"""Entry point for the PAL daemon process."""
import asyncio
import logging
import signal

from pal.config import load_config
from pal.daemon import Daemon


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    daemon = Daemon(config)

    loop = asyncio.new_event_loop()

    def handle_signal() -> None:
        daemon.shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    try:
        loop.run_until_complete(daemon.serve())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
