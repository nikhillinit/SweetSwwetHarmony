"""Tests for Pattern Miner v1 — heuristic template extraction."""

import json
import pytest

from storage.signal_store import SignalStore
from intelligence.pattern_miner import (
    QueryTemplate,
    ManualSeed,
    extract_keywords,
    categorize_keywords,
    templates_from_seeds,
    mine_patterns,
    MIN_TP_FOR_TEMPLATES,
)


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = SignalStore(db_path)
    await s.initialize()
    yield s
    await s.close()


class TestExtractKeywords:
    def test_basic_extraction(self):
        text = "health food wellness health fitness nutrition"
        keywords = extract_keywords(text, top_n=3)
        assert "health" in keywords
        assert len(keywords) <= 3

    def test_stop_words_filtered(self):
        text = "the company is a new startup in the health space"
        keywords = extract_keywords(text)
        assert "the" not in keywords
        assert "health" in keywords

    def test_empty_text(self):
        assert extract_keywords("") == []
        assert extract_keywords(None) == []

    def test_short_words_filtered(self):
        text = "AI ML US UK food health"
        keywords = extract_keywords(text)
        assert "food" in keywords
        assert "health" in keywords


class TestCategorizeKeywords:
    def test_health_category(self):
        categories = categorize_keywords(["health", "fitness", "app"])
        assert "health_tech" in categories

    def test_cpg_category(self):
        categories = categorize_keywords(["food", "beverage", "brand"])
        assert "cpg" in categories

    def test_no_match_returns_general(self):
        categories = categorize_keywords(["quantum", "computing", "tech"])
        assert categories == ["general"]


class TestTemplatesFromSeeds:
    def test_empty_seeds(self):
        assert templates_from_seeds([]) == []

    def test_single_seed(self):
        seeds = [ManualSeed(company_name="HealthySnacks Co", category="cpg")]
        templates = templates_from_seeds(seeds)
        # 3 templates per category (news_api, hacker_news, github)
        assert len(templates) == 3
        collectors = {t.collector for t in templates}
        assert collectors == {"news_api", "hacker_news", "github"}
        assert all(t.categories == ["cpg"] for t in templates)
        assert all(t.template_version == 1 for t in templates)

    def test_multiple_categories(self):
        seeds = [
            ManualSeed(company_name="FoodBrand", category="cpg"),
            ManualSeed(company_name="FitApp", category="health_tech"),
        ]
        templates = templates_from_seeds(seeds)
        # 3 collectors x 2 categories = 6 templates
        assert len(templates) == 6
        categories = {t.categories[0] for t in templates}
        assert "cpg" in categories
        assert "health_tech" in categories

    def test_template_version_stamped(self):
        seeds = [ManualSeed(company_name="Test Co", category="general")]
        templates = templates_from_seeds(seeds)
        assert all(t.template_version == 1 for t in templates)


class TestMinePatterns:
    @pytest.mark.asyncio
    async def test_empty_tp_no_seeds_returns_empty(self, store):
        templates = await mine_patterns(store)
        assert templates == []

    @pytest.mark.asyncio
    async def test_low_tp_uses_bootstrap(self, store):
        # Add 5 TPs (< MIN_TP_FOR_TEMPLATES)
        for i in range(5):
            await store._db.execute(
                """INSERT INTO signals
                   (signal_type, source_api, canonical_key, company_name,
                    confidence, raw_data, detected_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"type_{i}", "github", f"domain:test{i}.ai", f"Company{i}",
                 0.8, "{}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            await store._db.execute(
                """INSERT INTO signal_quality_metrics
                   (signal_id, canonical_key, human_label, label_source, labeled_at)
                   VALUES (?, ?, 'TP', 'manual', ?)""",
                (i + 1, f"domain:test{i}.ai", "2026-01-01T00:00:00Z"),
            )
        await store._db.commit()

        seeds = [ManualSeed(company_name="Health Food Co", category="cpg")]
        templates = await mine_patterns(store, manual_seeds=seeds)
        # 3 templates: one per bootstrap collector (news_api, hacker_news, github)
        assert len(templates) == 3
        assert all(t.categories == ["cpg"] for t in templates)

    @pytest.mark.asyncio
    async def test_sufficient_tp_mines_from_db(self, store):
        # Add 25 TPs with company names
        for i in range(25):
            await store._db.execute(
                """INSERT INTO signals
                   (signal_type, source_api, canonical_key, company_name,
                    confidence, raw_data, detected_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("spike", "github", f"domain:health{i}.ai", f"HealthFood Company {i}",
                 0.8, json.dumps({"description": "healthy food and wellness brand"}),
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            await store._db.execute(
                """INSERT INTO signal_quality_metrics
                   (signal_id, canonical_key, human_label, label_source, labeled_at)
                   VALUES (?, ?, 'TP', 'manual', ?)""",
                (i + 1, f"domain:health{i}.ai", "2026-01-01T00:00:00Z"),
            )
        await store._db.commit()

        templates = await mine_patterns(store)
        assert len(templates) >= 1
        assert templates[0].collector == "github"
        assert len(templates[0].keywords) > 0

    @pytest.mark.asyncio
    async def test_template_to_dict(self, store):
        t = QueryTemplate(
            collector="github", keywords=["health", "food"],
            categories=["cpg"], priority=1, template_version=1,
        )
        d = t.to_dict()
        assert d["collector"] == "github"
        assert d["template_version"] == 1
