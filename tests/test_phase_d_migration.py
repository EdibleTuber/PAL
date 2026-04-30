"""Tests for the Phase D server-side data migration script."""
import subprocess
import sys
from pathlib import Path


def _run_migration(vault: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/migrate_phase_d.py", str(vault)],
        capture_output=True, text=True, check=False,
    )


def test_migration_moves_existing_channels(tmp_path):
    channels = tmp_path / "_channels"
    channels.mkdir()
    (channels / "C1").mkdir()
    (channels / "C1" / "history.jsonl").write_text('{"role":"user","content":"hi"}\n')
    (channels / "C1" / "scratch.md").write_text("notes\n")
    (channels / "C2").mkdir()
    (channels / "C2" / "history.jsonl").write_text("")

    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr

    assert (channels / "pal" / "C1" / "history.jsonl").read_text().startswith('{"role":"user"')
    assert (channels / "pal" / "C1" / "scratch.md").read_text() == "notes\n"
    assert (channels / "pal" / "C2" / "history.jsonl").exists()
    assert not (channels / "C1").exists()
    assert not (channels / "C2").exists()


def test_migration_is_idempotent(tmp_path):
    channels = tmp_path / "_channels"
    channels.mkdir()
    (channels / "C1").mkdir()
    (channels / "C1" / "history.jsonl").write_text("data\n")

    _run_migration(tmp_path)
    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (channels / "pal" / "C1" / "history.jsonl").read_text() == "data\n"


def test_migration_skips_already_migrated_pal_dir(tmp_path):
    channels = tmp_path / "_channels"
    (channels / "pal" / "C1").mkdir(parents=True)
    (channels / "pal" / "C1" / "history.jsonl").write_text("already-migrated\n")

    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (channels / "pal" / "C1" / "history.jsonl").read_text() == "already-migrated\n"


def test_migration_refuses_overwrite_when_both_exist(tmp_path):
    """If both <vault>/_channels/C1/ and <vault>/_channels/pal/C1/ exist, refuse."""
    channels = tmp_path / "_channels"
    (channels / "C1").mkdir(parents=True)
    (channels / "C1" / "history.jsonl").write_text("legacy\n")
    (channels / "pal" / "C1").mkdir(parents=True)
    (channels / "pal" / "C1" / "history.jsonl").write_text("new\n")

    result = _run_migration(tmp_path)
    assert result.returncode != 0
    assert "would overwrite" in result.stderr.lower() or "would overwrite" in result.stdout.lower()
    # Both dirs untouched
    assert (channels / "C1" / "history.jsonl").read_text() == "legacy\n"
    assert (channels / "pal" / "C1" / "history.jsonl").read_text() == "new\n"


def test_migration_handles_empty_channels_dir(tmp_path):
    channels = tmp_path / "_channels"
    channels.mkdir()
    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr


def test_migration_skips_missing_channels_dir(tmp_path):
    """If _channels doesn't exist, exit cleanly."""
    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr
