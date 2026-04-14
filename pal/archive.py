"""Shared archive helpers for raw and summary files after compile/import."""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ARCHIVE_MAX_AGE_DAYS = 30

# Most Linux filesystems cap a single filename component at 255 bytes.
# Keep headroom for ".md" plus any future prefixing.
MAX_SLUG_BYTES = 200


def archive_raw_files(
    vault_path: Path,
    raw_path: str,
    summary_path: str | None = None,
) -> None:
    """Move raw and summary files to raw/archived/ after successful compile."""
    archive_dir = vault_path / "raw" / "archived"
    archive_dir.mkdir(parents=True, exist_ok=True)

    raw_full = vault_path / raw_path
    if raw_full.exists():
        dest = archive_dir / raw_full.name
        raw_full.rename(dest)
        logger.info("Archived %s -> raw/archived/%s", raw_path, raw_full.name)

    if summary_path:
        summary_full = vault_path / summary_path
        if summary_full.exists():
            # Use .summary.md suffix to avoid name collision with the raw file
            dest_name = summary_full.stem + ".summary.md"
            dest = archive_dir / dest_name
            summary_full.rename(dest)
            logger.info("Archived %s -> raw/archived/%s", summary_path, dest_name)


def cleanup_archived(vault_path: Path, max_age_days: int = ARCHIVE_MAX_AGE_DAYS) -> None:
    """Delete archived files older than max_age_days."""
    archive_dir = vault_path / "raw" / "archived"
    if not archive_dir.exists():
        return

    cutoff = time.time() - (max_age_days * 86400)
    for f in archive_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink()
            logger.info("Cleaned up archived file: %s (older than %d days)", f.name, max_age_days)
