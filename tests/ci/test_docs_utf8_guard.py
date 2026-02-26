"""Tests for scripts/ci/check_docs_utf8.py.

Verifies:
- Clean fixture passes
- Mojibake fixture fails
"""

import pytest

from scripts.ci.check_docs_utf8 import check_file, main


class TestCheckFile:
    def test_clean_file_passes(self, tmp_path):
        """Clean UTF-8 file produces no errors."""
        f = tmp_path / "clean.md"
        f.write_text("# Hello World\n\nThis is clean UTF-8.\n", encoding="utf-8")
        errors = check_file(f)
        assert errors == []

    def test_replacement_char_detected(self, tmp_path):
        """File with U+FFFD is detected as mojibake."""
        f = tmp_path / "bad.md"
        f.write_text("This has a \ufffd replacement character.\n", encoding="utf-8")
        errors = check_file(f)
        assert len(errors) > 0
        assert "mojibake" in errors[0].lower()


class TestMain:
    def test_clean_directory_passes(self, tmp_path):
        """Directory with clean files returns 0."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "readme.md").write_text("# OK\n", encoding="utf-8")
        assert main(str(docs)) == 0

    def test_mojibake_directory_fails(self, tmp_path):
        """Directory with mojibake file returns 1."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "bad.md").write_text("Bad \ufffd data\n", encoding="utf-8")
        assert main(str(docs)) == 1

    def test_missing_directory_passes(self, tmp_path):
        """Missing directory returns 0 with warning."""
        assert main(str(tmp_path / "nonexistent")) == 0
