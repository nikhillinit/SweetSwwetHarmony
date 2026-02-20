"""Tests for Query Generator — per-collector formatters and negative keyword protection."""

import pytest
from datetime import datetime, timezone, timedelta

from intelligence.pattern_miner import QueryTemplate
from intelligence.query_generator import (
    PROTECTED_VOCABULARY,
    HunterQuery,
    compute_inputs_hash,
    filter_negative_keywords,
    format_github_query,
    format_hacker_news_query,
    format_news_api_query,
    generate_queries,
)


class TestFormatGithubQuery:
    def test_basic_format(self):
        q = format_github_query(["health", "food"], set(), category="cpg")
        assert "health food" in q
        assert "topic:food" in q
        assert "stars:>10" in q
        assert "created:>" in q

    def test_negative_keywords_included(self):
        q = format_github_query(["health"], {"crypto", "blockchain"})
        assert "-blockchain" in q
        assert "-crypto" in q

    def test_limits_keywords(self):
        keywords = [f"word{i}" for i in range(20)]
        q = format_github_query(keywords, set())
        # Only first 5 keywords
        assert "word0" in q
        assert "word4" in q
        assert "word5" not in q.split("stars:")[0]


class TestFormatHackerNewsQuery:
    def test_basic_format(self):
        q = format_hacker_news_query(["health", "food"], set())
        assert "search?query=health food" in q
        assert "tags=show_hn" in q

    def test_negative_keywords_not_in_query(self):
        # HN doesn't support negation in query
        q = format_hacker_news_query(["health"], {"crypto"})
        assert "-crypto" not in q


class TestFormatNewsApiQuery:
    def test_basic_format(self):
        q = format_news_api_query(["health", "food"], set())
        assert "search?q=health food" in q


class TestFilterNegativeKeywords:
    def test_filters_by_collector(self):
        nks = [
            {"keyword": "crypto", "collector": "github", "category": None, "review_required": False},
            {"keyword": "saas", "collector": "hacker_news", "category": None, "review_required": False},
        ]
        result = filter_negative_keywords(nks, collector="github")
        assert "crypto" in result
        assert "saas" not in result

    def test_protected_vocabulary_never_excluded(self):
        nks = [
            {"keyword": "health", "collector": None, "category": None, "review_required": False},
            {"keyword": "crypto", "collector": None, "category": None, "review_required": False},
        ]
        result = filter_negative_keywords(nks)
        assert "health" not in result  # Protected
        assert "crypto" in result

    def test_review_required_skipped(self):
        nks = [
            {"keyword": "enterprise", "collector": None, "category": None, "review_required": True},
        ]
        result = filter_negative_keywords(nks)
        assert "enterprise" not in result

    def test_global_keywords_included(self):
        nks = [
            {"keyword": "b2b", "collector": None, "category": None, "review_required": False},
        ]
        result = filter_negative_keywords(nks, collector="github")
        assert "b2b" in result


class TestGenerateQueries:
    def test_github_template(self):
        templates = [QueryTemplate(collector="github", keywords=["health", "food"], categories=["cpg"])]
        queries = generate_queries(templates, [])
        assert len(queries) == 1
        assert queries[0].collector == "github"
        assert "health food" in queries[0].query_text

    def test_hacker_news_template(self):
        templates = [QueryTemplate(collector="hacker_news", keywords=["wellness"], categories=["health_tech"])]
        queries = generate_queries(templates, [])
        assert len(queries) == 1
        assert "search?query=" in queries[0].query_text

    def test_news_api_template(self):
        templates = [QueryTemplate(collector="news_api", keywords=["travel"], categories=["travel"])]
        queries = generate_queries(templates, [])
        assert len(queries) == 1
        assert "search?q=" in queries[0].query_text

    def test_unsupported_collector_skipped(self):
        templates = [QueryTemplate(collector="unknown", keywords=["test"], categories=["general"])]
        queries = generate_queries(templates, [])
        assert len(queries) == 0

    def test_dedup_by_hash(self):
        templates = [
            QueryTemplate(collector="github", keywords=["health"], categories=["cpg"]),
            QueryTemplate(collector="github", keywords=["health"], categories=["cpg"]),
        ]
        queries = generate_queries(templates, [])
        # Second template should be deduped
        assert len(queries) == 1

    def test_existing_hashes_skipped(self):
        templates = [QueryTemplate(collector="github", keywords=["health"], categories=["cpg"])]
        queries_first = generate_queries(templates, [])
        existing = {q.inputs_hash for q in queries_first}
        queries_second = generate_queries(templates, [], existing_hashes=existing)
        assert len(queries_second) == 0

    def test_negative_keywords_applied(self):
        templates = [QueryTemplate(collector="github", keywords=["startup"], categories=["general"])]
        neg_kws = [
            {"keyword": "enterprise", "collector": None, "category": None, "review_required": False},
        ]
        queries = generate_queries(templates, neg_kws)
        assert len(queries) == 1
        assert "-enterprise" in queries[0].query_text

    def test_empty_templates(self):
        assert generate_queries([], []) == []

    def test_query_type_valid_for_check_constraint(self):
        """query_type must be 'pattern', 'bootstrap', or 'manual' (DB CHECK)."""
        valid_types = {"pattern", "bootstrap", "manual"}
        # Bootstrap template (priority=2)
        bootstrap = [QueryTemplate(
            collector="github", keywords=["health"], categories=["cpg"], priority=2,
        )]
        queries = generate_queries(bootstrap, [])
        assert len(queries) == 1
        assert queries[0].query_type in valid_types
        assert queries[0].query_type == "bootstrap"

    def test_mined_template_gets_pattern_type(self):
        """Mined templates (priority=1) should get query_type='pattern'."""
        mined = [QueryTemplate(
            collector="github", keywords=["food"], categories=["cpg"], priority=1,
        )]
        queries = generate_queries(mined, [])
        assert queries[0].query_type == "pattern"

    def test_query_type_never_uses_category_name(self):
        """query_type must not be a category name like 'cpg' or 'health_tech'."""
        templates = [
            QueryTemplate(collector="github", keywords=["health"], categories=["health_tech"], priority=2),
            QueryTemplate(collector="news_api", keywords=["food"], categories=["cpg"], priority=2),
        ]
        queries = generate_queries(templates, [])
        for q in queries:
            assert q.query_type not in {"cpg", "health_tech", "travel", "marketplace", "general"}


class TestInputsHash:
    def test_deterministic(self):
        h1 = compute_inputs_hash("github", "health food", "2026-01-01")
        h2 = compute_inputs_hash("github", "health food", "2026-01-01")
        assert h1 == h2

    def test_different_inputs(self):
        h1 = compute_inputs_hash("github", "health food", "2026-01-01")
        h2 = compute_inputs_hash("github", "health food", "2026-01-02")
        assert h1 != h2


class TestProtectedVocabulary:
    def test_thesis_words_are_protected(self):
        assert "health" in PROTECTED_VOCABULARY
        assert "food" in PROTECTED_VOCABULARY
        assert "travel" in PROTECTED_VOCABULARY
        assert "marketplace" in PROTECTED_VOCABULARY
        assert "beauty" in PROTECTED_VOCABULARY
        assert "fitness" in PROTECTED_VOCABULARY
