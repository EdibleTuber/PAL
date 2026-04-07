"""Entry point for the PAL Discord adapter."""
import logging
import os
import sys

from pal.config import load_config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = os.environ.get("PAL_DISCORD_TOKEN")
    if not token:
        print("Error: PAL_DISCORD_TOKEN environment variable is required.")
        sys.exit(1)

    allowed_str = os.environ.get("PAL_DISCORD_ALLOWED_USERS", "")
    allowed_users = {uid.strip() for uid in allowed_str.split(",") if uid.strip()}
    if not allowed_users:
        print("Warning: PAL_DISCORD_ALLOWED_USERS is empty. Bot will not respond to anyone.")

    config = load_config()

    from pal.discord_adapter import PalDiscordBot

    bot = PalDiscordBot(
        allowed_users=allowed_users,
        socket_path=config.socket_path,
    )
    bot.run(token)


if __name__ == "__main__":
    main()
