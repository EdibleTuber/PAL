"""Tests for pal.backfill_titles — one-off article title cleanup."""
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pal.article import Article, serialize_article
from pal.backfill_titles import backfill_titles, BackfillReport
from pal.wiki import WikiManager


@dataclass
class MockInferenceResult:
    content: str
    reasoning: str = ""


def _write_article(vault: Path, path: str, title: str, body: str = "Body.\n") -> None:
    """Write a compiled-article-shaped file for backfill tests."""
    article = Article(
        meta={"title": title, "created": "2026-01-01T00:00:00+00:00"},
        compiled_truth=f"## Overview\n\n{body}\n",
        timeline=[],
    )
    full = vault / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(serialize_article(article))


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    (v / "_index.md").write_text("---\ntitle: Vault Index\n---\n\n# Vault Index\n")
    return v


@pytest.mark.asyncio
async def test_backfill_flags_and_regenerates_only_bad_titles(vault):
    _write_article(vault, "AI/clean.md", title="Clean Title")
    _write_article(vault, "AI/long.md", title="a" * 120)
    _write_article(vault, "AI/github.md", title="GitHub - foo/bar: does a thing")

    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="TITLE: Regenerated Clean"
    )

    wiki = WikiManager(vault)
    report = await backfill_titles(
        vault=vault,
        wiki=wiki,
        inference=inference,
        apply=True,
    )

    assert report.processed == 2
    assert report.updated == 2
    assert report.skipped_clean == 1
    assert report.skipped_error == 0

    # The two bad articles got overwritten with the new title.
    from pal.frontmatter import parse_frontmatter
    long_meta, _ = parse_frontmatter((vault / "AI/long.md").read_text())
    gh_meta, _ = parse_frontmatter((vault / "AI/github.md").read_text())
    clean_meta, _ = parse_frontmatter((vault / "AI/clean.md").read_text())
    assert long_meta["title"] == "Regenerated Clean"
    assert gh_meta["title"] == "Regenerated Clean"
    assert clean_meta["title"] == "Clean Title"


@pytest.mark.asyncio
async def test_backfill_dry_run_does_not_write(vault):
    _write_article(vault, "AI/long.md", title="a" * 120)

    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="TITLE: Would Regenerate"
    )
    wiki = WikiManager(vault)

    report = await backfill_titles(
        vault=vault,
        wiki=wiki,
        inference=inference,
        apply=False,
    )

    assert report.updated == 1  # counted as "would update"
    from pal.frontmatter import parse_frontmatter
    meta, _ = parse_frontmatter((vault / "AI/long.md").read_text())
    # Dry-run must not touch the file.
    assert meta["title"] == "a" * 120


@pytest.mark.asyncio
async def test_backfill_skips_inference_errors(vault):
    _write_article(vault, "AI/long.md", title="a" * 120)
    _write_article(vault, "AI/long2.md", title="b" * 120)

    inference = AsyncMock()
    # First call errors, second returns a clean title.
    inference.complete.side_effect = [
        RuntimeError("inference offline"),
        MockInferenceResult(content="TITLE: Second One"),
    ]
    wiki = WikiManager(vault)

    report = await backfill_titles(
        vault=vault,
        wiki=wiki,
        inference=inference,
        apply=True,
    )

    assert report.processed == 2
    assert report.updated == 1
    assert report.skipped_error == 1


@pytest.mark.asyncio
async def test_backfill_skips_system_directories(vault):
    # Files under _system directories should not be processed.
    sys_dir = vault / "_system"
    sys_dir.mkdir()
    (sys_dir / "bad.md").write_text(
        "---\ntitle: " + "z" * 120 + "\n---\n\n## Overview\n\nBody.\n"
    )
    _write_article(vault, "AI/long.md", title="a" * 120)

    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="TITLE: Only Touched One"
    )
    wiki = WikiManager(vault)

    report = await backfill_titles(
        vault=vault,
        wiki=wiki,
        inference=inference,
        apply=True,
    )

    assert report.processed == 1
    assert report.updated == 1
    # System file untouched.
    assert ("z" * 120) in (sys_dir / "bad.md").read_text()


@pytest.mark.asyncio
async def test_backfill_apply_rebuilds_index_once(vault):
    _write_article(vault, "AI/long.md", title="a" * 120)
    _write_article(vault, "AI/long2.md", title="b" * 120)

    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="TITLE: Clean Name"
    )
    wiki = WikiManager(vault)

    await backfill_titles(
        vault=vault,
        wiki=wiki,
        inference=inference,
        apply=True,
    )

    index_text = (vault / "_index.md").read_text()
    assert "Clean Name" in index_text
    # Ensure both articles are reflected.
    assert "AI/long.md" in index_text
    assert "AI/long2.md" in index_text
