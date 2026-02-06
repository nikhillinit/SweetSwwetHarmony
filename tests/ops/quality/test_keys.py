"""Tier 3 Lower Risk -- Canonical key remediation helper tests.

Verifies domain extraction from raw_data, key-strengthening suggestions
for weak (name_loc) canonical keys, and markdown report generation.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from ops.quality.keys import (
    KeyStrengtheningSuggestion,
    _extract_domain,
    suggest_key_strengthening,
    suggestions_to_markdown,
)
from tests.ops.quality.conftest import _insert_signal, _utc_iso


# ---------------------------------------------------------------------------
# _extract_domain tests
# ---------------------------------------------------------------------------

class TestExtractDomain:
    """Tests for _extract_domain() helper."""

    def test_extract_domain_from_url(self):
        """Should extract hostname from a URL in the 'url' field."""
        result = _extract_domain({"url": "https://example.com/page"})
        assert result == "example.com"

    def test_extract_domain_plain(self):
        """Should return domain directly when 'domain' field is a plain domain string."""
        result = _extract_domain({"domain": "example.com"})
        assert result == "example.com"

    def test_extract_domain_none(self):
        """Should return None when raw_data is None."""
        result = _extract_domain(None)
        assert result is None


# ---------------------------------------------------------------------------
# suggest_key_strengthening tests
# ---------------------------------------------------------------------------

class TestSuggestKeyStrengthening:
    """Tests for suggest_key_strengthening()."""

    def test_suggest_key_strengthening_empty(self, quality_db):
        """No name_loc keys in the database should produce an empty suggestions list."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        suggestions = suggest_key_strengthening(conn, min_signals=1, limit=100)
        assert suggestions == []

        conn.close()

    def test_suggest_key_strengthening_with_data(self, quality_db):
        """Signals with name_loc canonical keys and domain in raw_data should
        produce suggestions with the extracted domain."""
        db_path, _store = quality_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # Insert 10 signals with the same weak canonical key.
        for i in range(10):
            _insert_signal(
                conn,
                signal_type=f"test_type_{i}",
                source_api="github",
                canonical_key="name_loc:Company X|San Francisco",
                company_name="Company X",
                confidence=0.6,
                raw_data=json.dumps({
                    "domain": "companyx.com",
                    "description": f"Company X product {i}",
                }),
                detected_at=_utc_iso(i),
            )

        suggestions = suggest_key_strengthening(conn, min_signals=5, limit=100)

        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.weak_canonical_key == "name_loc:Company X|San Francisco"
        assert s.suggested_domain == "companyx.com"
        assert s.suggested_canonical_key == "domain:companyx.com"
        assert len(s.supporting_signal_ids) > 0

        conn.close()


# ---------------------------------------------------------------------------
# suggestions_to_markdown tests
# ---------------------------------------------------------------------------

class TestSuggestionsToMarkdown:
    """Tests for suggestions_to_markdown()."""

    def test_suggestions_to_markdown(self):
        """Markdown output should contain the expected header and suggestion details."""
        suggestions = [
            KeyStrengtheningSuggestion(
                weak_canonical_key="name_loc:Acme Inc|New York",
                suggested_domain="acme.com",
                suggested_canonical_key="domain:acme.com",
                supporting_signal_ids=[1, 2, 3],
                notes="Domain extracted from raw_data; consider migrating.",
            ),
        ]

        md = suggestions_to_markdown(suggestions)

        assert "# Canonical Key Strengthening Suggestions" in md
        assert "name_loc:Acme Inc|New York" in md
        assert "`acme.com`" in md
        assert "`domain:acme.com`" in md
        assert "1, 2, 3" in md


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
