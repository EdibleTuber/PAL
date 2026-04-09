"""Tests for raw file archival and cleanup."""
import time
import os
from pathlib import Path

import pytest

from pal.daemon import archive_raw_files, cleanup_archived


class TestArchiveRawFiles:
    def test_archives_raw_and_summary(self, tmp_path):
        vault = tmp_path / "vault"
        raw_file = vault / "raw" / "web" / "article-abc.md"
        summary_file = vault / "raw" / "summaries" / "article-abc.md"
        raw_file.parent.mkdir(parents=True)
        summary_file.parent.mkdir(parents=True)
        raw_file.write_text("raw content")
        summary_file.write_text("summary content")

        archive_raw_files(
            vault,
            raw_path="raw/web/article-abc.md",
            summary_path="raw/summaries/article-abc.md",
        )

        assert not raw_file.exists()
        assert not summary_file.exists()
        assert (vault / "raw" / "archived" / "article-abc.md").exists()
        assert (vault / "raw" / "archived" / "article-abc.summary.md").exists()

    def test_archives_raw_only_when_no_summary(self, tmp_path):
        vault = tmp_path / "vault"
        raw_file = vault / "raw" / "web" / "article-abc.md"
        raw_file.parent.mkdir(parents=True)
        raw_file.write_text("raw content")

        archive_raw_files(vault, raw_path="raw/web/article-abc.md")

        assert not raw_file.exists()
        assert (vault / "raw" / "archived" / "article-abc.md").exists()

    def test_skips_missing_files(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "raw" / "archived").mkdir(parents=True)

        # Should not raise
        archive_raw_files(vault, raw_path="raw/web/nonexistent.md")


class TestCleanupArchived:
    def test_deletes_old_files(self, tmp_path):
        vault = tmp_path / "vault"
        archive_dir = vault / "raw" / "archived"
        archive_dir.mkdir(parents=True)

        old_file = archive_dir / "old-article.md"
        old_file.write_text("old")
        # Set mtime to 31 days ago
        old_mtime = time.time() - (31 * 86400)
        os.utime(old_file, (old_mtime, old_mtime))

        cleanup_archived(vault, max_age_days=30)
        assert not old_file.exists()

    def test_keeps_recent_files(self, tmp_path):
        vault = tmp_path / "vault"
        archive_dir = vault / "raw" / "archived"
        archive_dir.mkdir(parents=True)

        recent_file = archive_dir / "recent-article.md"
        recent_file.write_text("recent")

        cleanup_archived(vault, max_age_days=30)
        assert recent_file.exists()

    def test_handles_missing_archive_dir(self, tmp_path):
        vault = tmp_path / "vault"
        # Should not raise
        cleanup_archived(vault, max_age_days=30)
