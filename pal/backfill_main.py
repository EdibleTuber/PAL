"""Entry point for the `pal-backfill-titles` CLI.

Run with --apply to write changes. Default is dry-run.
"""
import argparse
import asyncio
import logging
import os
from pathlib import Path

from pal.backfill_titles import backfill_titles
from pal.config import load_config
from agent_core.inference import InferenceClient
from pal.wiki import WikiManager


# Backfill is best-served by gemma4 because its reasoning goes into
# reasoning_content (not main content), so TITLE: parsing is clean.
# Operator can still override via PAL_MODEL or the pal-backfill-titles
# invocation env.
_BACKFILL_DEFAULT_MODEL = "gemma-4-26b-a4b-it-q4_k_m"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Regenerate bad article titles in the PAL vault.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag, runs in dry-run mode.",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Vault path override. Defaults to PAL_VAULT_PATH env var or config default.",
    )
    args = parser.parse_args()

    config = load_config()

    # Force gemma4 for the backfill run unless the operator has explicitly
    # set PAL_MODEL in the invocation environment.
    if "PAL_MODEL" not in os.environ:
        config.model = _BACKFILL_DEFAULT_MODEL

    vault_path = args.vault or config.vault_path
    wiki = WikiManager(vault_path)
    inference = InferenceClient(
        base_url=config.inference_url,
        model=config.model,
    )

    async def run() -> None:
        report = await backfill_titles(
            vault=vault_path,
            wiki=wiki,
            inference=inference,
            apply=args.apply,
        )

        mode = "APPLIED" if args.apply else "DRY-RUN"
        print(f"\n=== {mode} ===")
        print(f"Processed:     {report.processed}")
        print(f"Updated:       {report.updated}")
        print(f"Skipped clean: {report.skipped_clean}")
        print(f"Skipped error: {report.skipped_error}")

        if report.changes:
            print("\nChanges:")
            for path, old, new in report.changes:
                print(f"  {path}")
                print(f"    - {old[:100]}")
                print(f"    + {new}")

        if not args.apply and report.updated > 0:
            print("\nRun again with --apply to write these changes.")

    asyncio.run(run())


if __name__ == "__main__":
    main()
