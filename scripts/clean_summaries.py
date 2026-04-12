"""Clean existing summaries of model output artifacts.

Reads every summary in raw/summaries/, backs up the original as
<file>.dirty, then rewrites the summary with cleaned body content.
Frontmatter is preserved.

Usage:
    python scripts/clean_summaries.py <vault-path>

Example:
    python scripts/clean_summaries.py ~/vault
"""
import sys
from pathlib import Path

from pal.frontmatter import parse_frontmatter, serialize_frontmatter
from pal.model_output import clean_model_output


def clean_summary_file(path: Path) -> tuple[bool, int]:
    """Clean a single summary file. Returns (changed, original_length)."""
    original = path.read_text()
    meta, body = parse_frontmatter(original)

    cleaned_body = clean_model_output(body)

    if cleaned_body.strip() == body.strip():
        return False, len(original)

    # Backup original
    backup_path = path.with_suffix(path.suffix + ".dirty")
    backup_path.write_text(original)

    # Write cleaned version
    path.write_text(serialize_frontmatter(meta, cleaned_body + "\n"))
    return True, len(original)


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/clean_summaries.py <vault-path>")
        sys.exit(1)

    vault = Path(sys.argv[1]).expanduser()
    summaries_dir = vault / "raw" / "summaries"

    if not summaries_dir.exists():
        print(f"Summaries directory not found: {summaries_dir}")
        sys.exit(1)

    # Only .md files, skip any existing .dirty backups
    summary_files = sorted(
        p for p in summaries_dir.glob("*.md")
        if not p.name.endswith(".dirty.md") and not p.name.endswith(".md.dirty")
    )

    print(f"Found {len(summary_files)} summaries in {summaries_dir}")
    print()

    changed_count = 0
    total_saved = 0
    for path in summary_files:
        try:
            changed, original_len = clean_summary_file(path)
        except Exception as exc:
            print(f"  ERROR {path.name}: {exc}")
            continue

        if changed:
            new_len = len(path.read_text())
            saved = original_len - new_len
            total_saved += saved
            changed_count += 1
            print(f"  cleaned {path.name} (-{saved} bytes)")

    print()
    print(f"Cleaned: {changed_count} / {len(summary_files)}")
    print(f"Total bytes removed: {total_saved}")
    print(f"Originals backed up to *.md.dirty files")


if __name__ == "__main__":
    main()
