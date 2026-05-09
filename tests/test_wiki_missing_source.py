"""Tests for find_articles_missing_source helper."""

from pathlib import Path

from pal.wiki import find_articles_missing_source


def _write(path: Path, frontmatter: str, body: str = "## Overview\n\nbody\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}---\n\n{body}")


def test_returns_articles_with_all_empty_url_and_no_source_file(tmp_path):
    vault = tmp_path / "vault"

    _write(
        vault / "AI" / "good.md",
        "title: Good\nsources:\n  - url: 'https://example.com'\n    hash: abc\n",
    )
    _write(
        vault / "Hardware" / "bad.md",
        "title: Bad\nsources:\n  - url: ''\n    hash: ''\n",
    )
    _write(
        vault / "Hardware" / "alsobad.md",
        "title: AlsoBad\nsources:\n  - url: ''\n    hash: ''\n  - url: ''\n    hash: ''\n",
    )
    _write(
        vault / "Hardware" / "rescued.md",
        "title: Rescued\nsources:\n  - url: ''\n    source_file: 'raw/archived/x.pdf'\n    hash: ''\n",
    )

    results = find_articles_missing_source(vault)
    paths = sorted(p.relative_to(vault).as_posix() for p in results)

    assert paths == ["Hardware/alsobad.md", "Hardware/bad.md"]


def test_skips_system_directories(tmp_path):
    vault = tmp_path / "vault"

    _write(
        vault / "_wisdom" / "rule.md",
        "title: Rule\n",  # no sources at all
    )
    _write(
        vault / "raw" / "notes" / "scratch.md",
        "title: Scratch\nsources:\n  - url: ''\n    hash: ''\n",
    )

    results = find_articles_missing_source(vault)
    assert results == []  # both are in skipped dirs


def test_handles_articles_with_no_sources_array(tmp_path):
    vault = tmp_path / "vault"

    _write(
        vault / "AI" / "no-sources.md",
        "title: NoSources\n",  # no sources key at all
    )

    results = find_articles_missing_source(vault)
    # Articles with no sources array are not "missing source," they predate the convention.
    # Callers can decide what to do with them via a separate helper if needed.
    assert results == []
