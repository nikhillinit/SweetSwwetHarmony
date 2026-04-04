"""Test disagreement report generation."""
import sqlite3
from pathlib import Path
import pytest
from ops.quality.thesis import generate_disagreement_report


@pytest.fixture
def test_db(tmp_path):
    """Create a test database with signals and thesis classifications."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Create tables
    conn.execute("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY,
            signal_type TEXT,
            source_api TEXT,
            canonical_key TEXT,
            company_name TEXT,
            confidence REAL,
            raw_data TEXT,
            detected_at TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE thesis_classifications (
            id INTEGER PRIMARY KEY,
            signal_id INTEGER,
            canonical_key TEXT,
            keyword_score REAL,
            keyword_category TEXT,
            thesis_match BOOLEAN,
            thesis_fit_score REAL,
            category TEXT,
            classification_status TEXT DEFAULT 'success',
            confidence TEXT,
            disagreement_detected BOOLEAN DEFAULT 0,
            classified_at TEXT
        )
    """)

    # Insert test data
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    # Signal 1: Disagreement (keyword high, LLM low)
    conn.execute(
        "INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, confidence, raw_data, detected_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "test", "github", "domain:test1.com", "Test Co 1", 0.8, "{}", now, now)
    )
    conn.execute(
        """
        INSERT INTO thesis_classifications (
            id, signal_id, canonical_key, keyword_score, keyword_category,
            thesis_match, thesis_fit_score, category, classification_status,
            confidence, disagreement_detected, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, "domain:test1.com", 0.85, "consumer_cpg", 0, 0.25, "excluded", "success", "high", 1, now)
    )

    # Signal 2: Disagreement (keyword low, LLM high)
    conn.execute(
        "INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, confidence, raw_data, detected_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (2, "test", "github", "domain:test2.com", "Test Co 2", 0.8, "{}", now, now)
    )
    conn.execute(
        """
        INSERT INTO thesis_classifications (
            id, signal_id, canonical_key, keyword_score, keyword_category,
            thesis_match, thesis_fit_score, category, classification_status,
            confidence, disagreement_detected, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (2, 2, "domain:test2.com", 0.25, "other", 1, 0.85, "consumer_cpg", "success", "high", 1, now)
    )

    # Signal 3: Agreement (both high)
    conn.execute(
        "INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, confidence, raw_data, detected_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (3, "test", "github", "domain:test3.com", "Test Co 3", 0.8, "{}", now, now)
    )
    conn.execute(
        """
        INSERT INTO thesis_classifications (
            id, signal_id, canonical_key, keyword_score, keyword_category,
            thesis_match, thesis_fit_score, category, classification_status,
            confidence, disagreement_detected, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (3, 3, "domain:test3.com", 0.85, "consumer_cpg", 1, 0.80, "consumer_cpg", "success", "high", 0, now)
    )

    # Signal 4: Another disagreement (keyword high, LLM low)
    conn.execute(
        "INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, confidence, raw_data, detected_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (4, "test", "sec_edgar", "domain:test4.com", "Test Co 4", 0.8, "{}", now, now)
    )
    conn.execute(
        """
        INSERT INTO thesis_classifications (
            id, signal_id, canonical_key, keyword_score, keyword_category,
            thesis_match, thesis_fit_score, category, classification_status,
            confidence, disagreement_detected, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (4, 4, "domain:test4.com", 0.90, "consumer_health_tech", 0, 0.15, "excluded", "success", "high", 1, now)
    )

    conn.commit()
    yield conn
    conn.close()


def test_disagreement_report_uses_column(test_db):
    """Test that report uses disagreement_detected column."""
    report = generate_disagreement_report(test_db, days=1)

    # Check summary stats
    assert "**Total classified**: 4" in report
    assert "**Total disagreements**: 3" in report  # 3 out of 4 have disagreement=1
    assert "75.0%" in report  # Disagreement rate

    # Check false positive count
    assert "**Keyword false positives**: 2" in report  # Signal 1 and 4

    # Check false negative count
    assert "**Keyword false negatives**: 1" in report  # Signal 2


def test_disagreement_report_by_category(test_db):
    """Test that report shows breakdown by category."""
    report = generate_disagreement_report(test_db, days=1)

    # Check category breakdown for false positives
    assert "False Positives by Keyword Category" in report
    assert "consumer_cpg: 1" in report
    assert "consumer_health_tech: 1" in report

    # Check category breakdown for false negatives
    assert "False Negatives by LLM Category" in report
    assert "consumer_cpg: 1" in report


def test_disagreement_report_details(test_db):
    """Test that report includes detailed signal listings."""
    report = generate_disagreement_report(test_db, days=1)

    # Check false positive details
    assert "signal_id=1" in report
    assert "kw=0.85" in report
    assert "llm_fit=0.25" in report
    assert "Test Co 1" in report

    # Check false negative details
    assert "signal_id=2" in report
    assert "kw=0.25" in report
    assert "llm_fit=0.85" in report


def test_disagreement_report_empty_categories(test_db):
    """Test that report handles empty categories gracefully."""
    # Delete all signals except the agreement one
    test_db.execute("DELETE FROM thesis_classifications WHERE disagreement_detected = 1")
    test_db.commit()

    report = generate_disagreement_report(test_db, days=1)

    # Should show zero disagreements
    assert "**Total disagreements**: 0" in report
    assert "*(none)*" in report


def test_disagreement_report_output_file(test_db, tmp_path):
    """Test that report can be written to file."""
    out_path = tmp_path / "disagreement_report.md"

    report = generate_disagreement_report(test_db, days=1, out_path=str(out_path))

    # Check file was created
    assert out_path.exists()

    # Check content matches returned report
    file_content = out_path.read_text(encoding="utf-8")
    assert file_content == report

    # Check basic content
    assert "**Total disagreements**:" in file_content
