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


@pytest.mark.asyncio
async def test_backfill_skips_unreadable_file(vault):
    """Files that can't be decoded should count as skipped_error, not crash."""
    _write_article(vault, "AI/ok.md", title="a" * 120)
    bad = vault / "AI" / "bad.md"
    # Write bytes that aren't valid UTF-8.
    bad.write_bytes(b"---\ntitle: x\n---\n\n\xff\xfe invalid bytes\n")

    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="TITLE: Good Title"
    )
    wiki = WikiManager(vault)

    # Use apply=False to test that the backfill handles the bad file
    # without crashing. (Rebuilding the index would fail on the bad file.)
    report = await backfill_titles(
        vault=vault,
        wiki=wiki,
        inference=inference,
        apply=False,
    )

    # The valid article was processed and would update. The bad file
    # was counted as skipped_error. The function did not crash.
    assert report.updated == 1
    assert report.skipped_error >= 1


@pytest.mark.asyncio
async def test_backfill_skips_when_regenerate_returns_none(vault):
    """When regenerate_title returns None (bad model response), count as skipped_error."""
    _write_article(vault, "AI/long.md", title="a" * 120)

    inference = AsyncMock()
    # Response has no TITLE: prefix — regenerate_title returns None.
    inference.complete.return_value = MockInferenceResult(
        content="just a body with no title prefix"
    )
    wiki = WikiManager(vault)

    report = await backfill_titles(
        vault=vault,
        wiki=wiki,
        inference=inference,
        apply=True,
    )

    assert report.processed == 1
    assert report.updated == 0
    assert report.skipped_error == 1


@pytest.mark.asyncio
async def test_backfill_apply_creates_git_commit(vault):
    """Apply mode should produce one git commit describing the backfill."""
    # Initialize git in the vault so git_commit can work.
    import subprocess
    subprocess.run(["git", "init"], cwd=vault, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=vault, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=vault, check=True, capture_output=True)
    # Initial commit so we have a HEAD.
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=vault, check=True, capture_output=True)

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

    # Confirm a new commit was made.
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=vault,
        check=True,
        capture_output=True,
        text=True,
    )
    lines = [l for l in log.stdout.strip().split("\n") if l]
    assert len(lines) >= 2  # init + backfill
    assert "backfill" in lines[0].lower()


@pytest.mark.asyncio
async def test_backfill_refreshes_updated_timestamp(vault):
    """Regenerated articles should have their updated timestamp refreshed."""
    _write_article(vault, "AI/long.md", title="a" * 120)
    # Seed a specific 'updated' timestamp we can detect as stale.
    from pal.frontmatter import parse_frontmatter, serialize_frontmatter
    text = (vault / "AI/long.md").read_text()
    meta, body = parse_frontmatter(text)
    meta["updated"] = "2020-01-01T00:00:00+00:00"
    (vault / "AI/long.md").write_text(serialize_frontmatter(meta, body))

    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="TITLE: Fresh Title"
    )
    wiki = WikiManager(vault)

    await backfill_titles(
        vault=vault,
        wiki=wiki,
        inference=inference,
        apply=True,
    )

    meta, _ = parse_frontmatter((vault / "AI/long.md").read_text())
    assert meta["title"] == "Fresh Title"
    # Updated timestamp should be newer than the seeded 2020 value.
    assert not meta["updated"].startswith("2020")
