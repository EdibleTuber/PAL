"""Tests for Scratchpad — per-channel free-form markdown file in the vault."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from pal.scratchpad import Scratchpad, ScratchpadTooLarge


@pytest.fixture
def wiki_mock():
    m = MagicMock()
    m.git_commit = MagicMock()
    return m


def test_read_returns_empty_when_file_missing(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    assert sp.read() == ""


def test_write_creates_directory_and_file(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    sp.write("# hello\n")

    expected_path = tmp_path / "_channels" / "C1" / "scratch.md"
    assert expected_path.exists()
    assert expected_path.read_text() == "# hello\n"


def test_write_calls_wiki_commit(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    sp.write("# hello\n")
    wiki_mock.git_commit.assert_called_once()
    args = wiki_mock.git_commit.call_args[0]
    assert "C1" in args[0]


def test_read_after_write_round_trip(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    sp.write("content")
    assert sp.read() == "content"


def test_write_raises_when_over_cap(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=10)
    with pytest.raises(ScratchpadTooLarge) as exc_info:
        sp.write("x" * 11)
    assert "11" in str(exc_info.value)
    assert "10" in str(exc_info.value)
    assert not (tmp_path / "_channels" / "C1" / "scratch.md").exists()
    wiki_mock.git_commit.assert_not_called()


def test_append_adds_to_existing_content(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    sp.write("line1\n")
    sp.append("line2\n")
    assert sp.read() == "line1\nline2\n"


def test_append_respects_cap(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=10)
    sp.write("short")
    with pytest.raises(ScratchpadTooLarge):
        sp.append(" and more bytes than allowed")
    assert sp.read() == "short"


def test_read_unreadable_file_returns_empty(tmp_path, wiki_mock, monkeypatch, caplog):
    import logging
    scratch_path = tmp_path / "_channels" / "C1" / "scratch.md"
    scratch_path.parent.mkdir(parents=True)
    scratch_path.write_text("hi")

    real_open = Path.open
    def patched_open(self, *args, **kwargs):
        if self == scratch_path and "r" in (args[0] if args else kwargs.get("mode", "r")):
            raise OSError("simulated")
        return real_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", patched_open)

    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    with caplog.at_level(logging.WARNING):
        assert sp.read() == ""
    assert any("unreadable" in rec.message.lower() for rec in caplog.records)
