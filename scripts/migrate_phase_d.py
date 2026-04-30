#!/usr/bin/env python3
"""One-shot migration: move <vault>/_channels/<channel_id>/ entries into
<vault>/_channels/pal/<channel_id>/.

Usage:
    python scripts/migrate_phase_d.py /path/to/vault

Idempotent. Refuses to overwrite if both old and new locations exist for the
same channel id.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

_CHANNEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def migrate(vault: Path) -> int:
    channels = vault / "_channels"
    if not channels.exists():
        print(f"_channels directory does not exist at {channels}, nothing to migrate.")
        return 0

    target_dir = channels / "pal"
    target_dir.mkdir(exist_ok=True)

    errors = 0
    moved = 0
    skipped = 0

    for entry in sorted(channels.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name == "pal":
            continue
        if not _CHANNEL_ID_PATTERN.match(entry.name):
            print(f"  skipping non-channel directory: {entry.name}")
            continue

        new_path = target_dir / entry.name
        if new_path.exists():
            print(
                f"ERROR: would overwrite {new_path} when moving {entry}.",
                file=sys.stderr,
            )
            errors += 1
            continue

        print(f"  moving {entry} -> {new_path}")
        try:
            shutil.move(str(entry), str(new_path))
            moved += 1
        except OSError as exc:
            print(f"ERROR: move failed for {entry}: {exc}", file=sys.stderr)
            errors += 1

    print(f"Done. moved={moved} skipped={skipped} errors={errors}")
    return 1 if errors else 0


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_phase_d.py <vault_path>", file=sys.stderr)
        return 2
    vault = Path(sys.argv[1]).resolve()
    if not vault.is_dir():
        print(f"vault path is not a directory: {vault}", file=sys.stderr)
        return 2
    return migrate(vault)


if __name__ == "__main__":
    sys.exit(main())
