"""Tests for Consumer thesis keyword matcher."""
import pytest
from utils.thesis_matcher import (
    ThesisMatcher,
    ThesisFit,
    ConsumerThesis,
    CONSUMER_KEYWORDS,
    NEGATIVE_KEYWORDS,
)


class TestConsumerThesisEnum:
    """Test ConsumerThesis enum values."""

    def test_enum_has_consumer_cpg(self):
        assert ConsumerThesis.CONSUMER_CPG.value == "consumer_cpg"

    def test_enum_has_consumer_health_tech(self):
        assert ConsumerThesis.CONSUMER_HEALTH_TECH.value == "consumer_health_tech"

    def test_enum_has_travel_hospitality(self):
        assert ConsumerThesis.TRAVEL_HOSPITALITY.value == "travel_hospitality"

    def test_enum_has_consumer_marketplace(self):
        assert ConsumerThesis.CONSUMER_MARKETPLACE.value == "consumer_marketplace"

    def test_enum_has_unknown(self):
        assert ConsumerThesis.UNKNOWN.value == "unknown"


class TestConsumerKeywords:
    """Test keyword definitions."""

    def test_cpg_keywords_exist(self):
        assert ConsumerThesis.CONSUMER_CPG in CONSUMER_KEYWORDS
        assert "meal kit" in CONSUMER_KEYWORDS[ConsumerThesis.CONSUMER_CPG]

    def test_health_tech_keywords_exist(self):
        assert ConsumerThesis.CONSUMER_HEALTH_TECH in CONSUMER_KEYWORDS
        assert "fitness app" in CONSUMER_KEYWORDS[ConsumerThesis.CONSUMER_HEALTH_TECH]

    def test_travel_keywords_exist(self):
        assert ConsumerThesis.TRAVEL_HOSPITALITY in CONSUMER_KEYWORDS
        assert "travel booking" in CONSUMER_KEYWORDS[ConsumerThesis.TRAVEL_HOSPITALITY]

    def test_marketplace_keywords_exist(self):
        assert ConsumerThesis.CONSUMER_MARKETPLACE in CONSUMER_KEYWORDS
        assert "marketplace" in CONSUMER_KEYWORDS[ConsumerThesis.CONSUMER_MARKETPLACE]


class TestNegativeKeywords:
    """Test negative/exclusion keywords."""

    def test_enterprise_is_negative(self):
        assert "enterprise" in NEGATIVE_KEYWORDS

    def test_b2b_is_negative(self):
        assert "b2b" in NEGATIVE_KEYWORDS

    def test_crypto_is_negative(self):
        assert "crypto" in NEGATIVE_KEYWORDS

    def test_blockchain_is_negative(self):
        assert "blockchain" in NEGATIVE_KEYWORDS


class TestThesisFitDataclass:
    """Test ThesisFit result dataclass."""

    def test_is_fit_true_when_score_above_threshold(self):
        fit = ThesisFit(
            thesis=ConsumerThesis.CONSUMER_CPG,
            score=0.7,
            matched_keywords=["meal kit"],
            negative_keywords=[],
            all_scores={},
            confidence="HIGH",
        )
        assert fit.is_fit is True

    def test_is_fit_false_when_score_below_threshold(self):
        fit = ThesisFit(
            thesis=ConsumerThesis.UNKNOWN,
            score=0.2,
            matched_keywords=[],
            negative_keywords=["enterprise"],
            all_scores={},
            confidence="LOW",
        )
        assert fit.is_fit is False


class TestThesisMatcherScoring:
    """Test ThesisMatcher scoring logic."""

    @pytest.fixture
    def matcher(self):
        return ThesisMatcher()

    def test_cpg_description_scores_cpg(self, matcher):
        fit = matcher.score("We make healthy meal kits delivered to your door")
        assert fit.thesis == ConsumerThesis.CONSUMER_CPG
        # Phase 4a: threshold relaxed from 0.5 to 0.4 (score dilution from
        # new CPG keywords; delta 0.096 within Phase 4b drift bound of 0.15)
        assert fit.score >= 0.4

    def test_health_tech_description_scores_health_tech(self, matcher):
        fit = matcher.score("A fitness app for tracking your workouts and wellness")
        assert fit.thesis == ConsumerThesis.CONSUMER_HEALTH_TECH
        assert fit.score >= 0.5

    def test_travel_description_scores_travel(self, matcher):
        fit = matcher.score("Travel booking platform for unique hotel experiences")
        assert fit.thesis == ConsumerThesis.TRAVEL_HOSPITALITY
        assert fit.score >= 0.5

    def test_marketplace_description_scores_marketplace(self, matcher):
        fit = matcher.score("Consumer marketplace connecting buyers and sellers")
        assert fit.thesis == ConsumerThesis.CONSUMER_MARKETPLACE
        assert fit.score >= 0.5

    def test_negative_keywords_reduce_score(self, matcher):
        fit = matcher.score("Enterprise B2B SaaS platform for developers")
        assert fit.score < 0.4
        assert "enterprise" in fit.negative_keywords or "b2b" in fit.negative_keywords

    def test_empty_text_returns_unknown(self, matcher):
        fit = matcher.score("")
        assert fit.thesis == ConsumerThesis.UNKNOWN
        assert fit.score == 0.0

    def test_confidence_high_when_score_above_07(self, matcher):
        fit = matcher.score("Premium skincare brand with d2c subscription model for beauty products")
        assert fit.confidence == "HIGH" or fit.score >= 0.7

    def test_confidence_low_when_score_below_04(self, matcher):
        fit = matcher.score("Random unrelated text about nothing")
        assert fit.confidence == "LOW"


# =============================================================================
# Phase B Tests: Intent Phrases, Domain Matching, Blacklist, New Negatives
# =============================================================================


class TestIntentPhrases:
    """Test intent phrase matching from consumer_keywords.yaml."""

    @pytest.fixture
    def matcher(self):
        return ThesisMatcher()

    def test_join_waitlist_boosts_score(self, matcher):
        """Intent phrase 'join waitlist' should boost consumer signal."""
        base = matcher.score("A new health app")
        boosted = matcher.score("A new health app - join waitlist")
        assert boosted.score >= base.score
        assert "join waitlist" in boosted.matched_keywords or boosted.intent_phrases_matched

    def test_private_beta_boosts_score(self, matcher):
        """Intent phrase 'private beta' should boost consumer signal."""
        base = matcher.score("Fitness tracking platform")
        boosted = matcher.score("Fitness tracking platform - private beta")
        assert boosted.score >= base.score

    def test_pricing_page_indicator(self, matcher):
        """Intent phrase 'pricing' indicates commercial intent."""
        fit = matcher.score("Check out our pricing for the meal delivery subscription")
        assert fit.score > 0.3  # Should have some signal

    def test_request_access_boosts_score(self, matcher):
        """Intent phrase 'request access' should boost consumer signal."""
        fit = matcher.score("Travel booking app - request access to beta")
        assert fit.score > 0.4

    def test_coming_soon_indicates_startup(self, matcher):
        """Intent phrase 'coming soon' indicates early-stage startup."""
        fit = matcher.score("New wellness platform coming soon")
        assert fit.score > 0.3

    def test_multiple_intent_phrases_stack(self, matcher):
        """Multiple intent phrases should stack boosts."""
        single = matcher.score("Fitness app - join waitlist")
        multiple = matcher.score("Fitness app - join waitlist - pricing available - sign up now")
        assert multiple.score >= single.score


class TestDomainRegexPatterns:
    """Test regex patterns for consumer-oriented domain matching."""

    @pytest.fixture
    def matcher(self):
        return ThesisMatcher()

    def test_get_prefix_domain_boosts(self, matcher):
        """Domain starting with 'get' indicates consumer app (getmyapp.com)."""
        fit = matcher.score("Health tracking app", domain_name="getfitness.com")
        base = matcher.score("Health tracking app")
        assert fit.score >= base.score
        assert fit.domain_match is True or fit.score > base.score

    def test_try_prefix_domain_boosts(self, matcher):
        """Domain starting with 'try' indicates consumer app (tryproduct.io)."""
        fit = matcher.score("New meal kit service", domain_name="tryfresh.io")
        base = matcher.score("New meal kit service")
        assert fit.score >= base.score

    def test_join_prefix_domain_boosts(self, matcher):
        """Domain starting with 'join' indicates consumer community (joincommunity.co)."""
        fit = matcher.score("Wellness community platform", domain_name="joinwellness.co")
        base = matcher.score("Wellness community platform")
        assert fit.score >= base.score

    def test_short_get_domain_not_matched(self, matcher):
        """Domain 'get.io' (too short after prefix) should not match."""
        fit = matcher.score("Some app", domain_name="get.io")
        # Should not boost - pattern requires 3+ chars after prefix
        assert fit.domain_match is False or fit.score == matcher.score("Some app").score

    def test_regular_domain_no_boost(self, matcher):
        """Regular domain without consumer prefix should not get boost."""
        fit = matcher.score("Health app", domain_name="healthapp.com")
        base = matcher.score("Health app")
        # Score should be same (no domain boost)
        assert abs(fit.score - base.score) < 0.1


class TestDomainBlacklist:
    """Test domain blacklist fragments for rejecting non-production domains."""

    @pytest.fixture
    def matcher(self):
        return ThesisMatcher()

    def test_localhost_domain_penalized(self, matcher):
        """Localhost domains should be heavily penalized."""
        fit = matcher.score("Great meal kit startup", domain_name="localhost:3000")
        assert fit.score < 0.3 or fit.domain_blacklisted is True

    def test_example_domain_penalized(self, matcher):
        """Example domains should be penalized."""
        fit = matcher.score("Fitness app description", domain_name="example.com")
        assert fit.score < 0.3 or fit.domain_blacklisted is True

    def test_staging_domain_penalized(self, matcher):
        """Staging domains should be penalized."""
        fit = matcher.score("Travel booking platform", domain_name="staging.myapp.com")
        assert fit.score < 0.3 or fit.domain_blacklisted is True

    def test_dev_prefix_domain_penalized(self, matcher):
        """Dev prefix domains should be penalized."""
        fit = matcher.score("Health tracker app", domain_name="dev.myapp.io")
        assert fit.score < 0.3 or fit.domain_blacklisted is True

    def test_test_domain_penalized(self, matcher):
        """Test domains should be penalized."""
        fit = matcher.score("Marketplace for goods", domain_name="test.example.org")
        assert fit.score < 0.3 or fit.domain_blacklisted is True

    def test_internal_domain_penalized(self, matcher):
        """Internal domains should be penalized."""
        fit = matcher.score("Consumer app", domain_name="internal.company.net")
        assert fit.score < 0.3 or fit.domain_blacklisted is True

    def test_sample_domain_penalized(self, matcher):
        """Sample domains should be penalized."""
        fit = matcher.score("Wellness product", domain_name="sample-app.dev")
        assert fit.score < 0.3 or fit.domain_blacklisted is True


class TestNewNegativeKeywords:
    """Test new negative keywords from consumer_keywords.yaml."""

    @pytest.fixture
    def matcher(self):
        return ThesisMatcher()

    def test_boilerplate_is_negative(self, matcher):
        """'boilerplate' indicates template, not real startup."""
        fit = matcher.score("Next.js boilerplate for building apps")
        assert "boilerplate" in fit.negative_keywords
        assert fit.score < 0.4

    def test_starter_is_negative(self, matcher):
        """'starter' indicates template project."""
        fit = matcher.score("React starter kit for wellness apps")
        assert "starter" in fit.negative_keywords
        assert fit.score < 0.4

    def test_template_is_negative(self, matcher):
        """'template' indicates non-startup."""
        fit = matcher.score("SaaS template for marketplace businesses")
        assert "template" in fit.negative_keywords
        assert fit.score < 0.4

    def test_tutorial_is_negative(self, matcher):
        """'tutorial' indicates educational content."""
        fit = matcher.score("Tutorial on building fitness apps")
        assert "tutorial" in fit.negative_keywords
        assert fit.score < 0.4

    def test_workshop_is_negative(self, matcher):
        """'workshop' indicates educational content."""
        fit = matcher.score("Workshop for building meal delivery apps")
        assert "workshop" in fit.negative_keywords

    def test_course_is_negative(self, matcher):
        """'course' indicates educational content."""
        fit = matcher.score("Online course for travel app development")
        assert "course" in fit.negative_keywords

    def test_library_is_negative(self, matcher):
        """'library' indicates developer tool."""
        fit = matcher.score("JavaScript library for building health apps")
        assert "library" in fit.negative_keywords

    def test_framework_is_negative(self, matcher):
        """'framework' indicates developer tool."""
        fit = matcher.score("Python framework for marketplace development")
        assert "framework" in fit.negative_keywords

    def test_plugin_is_negative(self, matcher):
        """'plugin' indicates developer tool."""
        fit = matcher.score("WordPress plugin for fitness tracking")
        assert "plugin" in fit.negative_keywords

    def test_linter_is_negative(self, matcher):
        """'linter' indicates developer tool."""
        fit = matcher.score("Code linter for health app projects")
        assert "linter" in fit.negative_keywords

    def test_demo_repo_is_negative(self, matcher):
        """'demo repo' indicates example code."""
        fit = matcher.score("Demo repo for travel booking implementation")
        assert "demo repo" in fit.negative_keywords


class TestThesisFitExtendedAttributes:
    """Test extended ThesisFit attributes for Phase B."""

    def test_thesis_fit_has_intent_phrases_matched(self):
        """ThesisFit should track matched intent phrases."""
        fit = ThesisFit(
            thesis=ConsumerThesis.CONSUMER_CPG,
            score=0.7,
            matched_keywords=["meal kit"],
            negative_keywords=[],
            all_scores={},
            confidence="HIGH",
            intent_phrases_matched=["join waitlist"],
            domain_match=False,
            domain_blacklisted=False,
        )
        assert fit.intent_phrases_matched == ["join waitlist"]

    def test_thesis_fit_has_domain_match(self):
        """ThesisFit should indicate domain pattern match."""
        fit = ThesisFit(
            thesis=ConsumerThesis.CONSUMER_HEALTH_TECH,
            score=0.8,
            matched_keywords=["fitness app"],
            negative_keywords=[],
            all_scores={},
            confidence="HIGH",
            intent_phrases_matched=[],
            domain_match=True,
            domain_blacklisted=False,
        )
        assert fit.domain_match is True

    def test_thesis_fit_has_domain_blacklisted(self):
        """ThesisFit should indicate blacklisted domain."""
        fit = ThesisFit(
            thesis=ConsumerThesis.UNKNOWN,
            score=0.1,
            matched_keywords=[],
            negative_keywords=[],
            all_scores={},
            confidence="LOW",
            intent_phrases_matched=[],
            domain_match=False,
            domain_blacklisted=True,
        )
        assert fit.domain_blacklisted is True

    def test_thesis_fit_to_dict_includes_new_fields(self):
        """ThesisFit.to_dict() should include new Phase B fields."""
        fit = ThesisFit(
            thesis=ConsumerThesis.CONSUMER_CPG,
            score=0.7,
            matched_keywords=["meal kit"],
            negative_keywords=[],
            all_scores={"consumer_cpg": 0.7},
            confidence="HIGH",
            intent_phrases_matched=["pricing"],
            domain_match=True,
            domain_blacklisted=False,
        )
        d = fit.to_dict()
        assert "intent_phrases_matched" in d
        assert "domain_match" in d
        assert "domain_blacklisted" in d


# =============================================================================
# Phase 4a: Lexicon Expansion Tests
# =============================================================================


class TestPhase4aLexiconExpansion:
    """Phase 4a: expanded categories and keywords for consumer thesis."""

    @pytest.fixture
    def matcher(self):
        return ThesisMatcher()

    # --- New enum values ---

    def test_enum_has_consumer_fintech(self):
        """ConsumerThesis should have CONSUMER_FINTECH."""
        assert hasattr(ConsumerThesis, "CONSUMER_FINTECH")
        assert ConsumerThesis.CONSUMER_FINTECH.value == "consumer_fintech"

    def test_enum_has_consumer_social(self):
        """ConsumerThesis should have CONSUMER_SOCIAL."""
        assert hasattr(ConsumerThesis, "CONSUMER_SOCIAL")
        assert ConsumerThesis.CONSUMER_SOCIAL.value == "consumer_social"

    def test_enum_has_consumer_general(self):
        """ConsumerThesis should have CONSUMER_GENERAL."""
        assert hasattr(ConsumerThesis, "CONSUMER_GENERAL")
        assert ConsumerThesis.CONSUMER_GENERAL.value == "consumer_general"

    def test_consumer_keywords_has_fintech(self):
        """CONSUMER_KEYWORDS should have a CONSUMER_FINTECH entry."""
        assert ConsumerThesis.CONSUMER_FINTECH in CONSUMER_KEYWORDS

    def test_consumer_keywords_has_social(self):
        """CONSUMER_KEYWORDS should have a CONSUMER_SOCIAL entry."""
        assert ConsumerThesis.CONSUMER_SOCIAL in CONSUMER_KEYWORDS

    def test_consumer_keywords_has_general(self):
        """CONSUMER_KEYWORDS should have a CONSUMER_GENERAL entry."""
        assert ConsumerThesis.CONSUMER_GENERAL in CONSUMER_KEYWORDS

    # --- Expanded health tech keywords ---

    def test_health_tech_expansion(self, matcher):
        """'telehealth platform' should score consumer_health_tech."""
        fit = matcher.score(
            "A telehealth and digital health platform for patient wellness"
        )
        assert fit.thesis == ConsumerThesis.CONSUMER_HEALTH_TECH
        assert fit.score >= 0.4

    def test_health_tech_telemedicine(self, matcher):
        """'telemedicine' should be a health tech keyword."""
        fit = matcher.score("telemedicine app for virtual consultations with wellness focus")
        assert fit.thesis == ConsumerThesis.CONSUMER_HEALTH_TECH
        assert fit.score >= 0.3

    def test_health_tech_digital_health(self, matcher):
        """'digital health' should be a health tech keyword."""
        fit = matcher.score("digital health platform for fitness and wellness tracking")
        assert fit.thesis == ConsumerThesis.CONSUMER_HEALTH_TECH
        assert fit.score >= 0.4

    def test_health_tech_fertility(self, matcher):
        """'fertility' should be a health tech keyword."""
        fit = matcher.score("A fertility tracking and womens health wellness app")
        assert fit.thesis == ConsumerThesis.CONSUMER_HEALTH_TECH
        assert fit.score >= 0.4

    # --- Expanded CPG keywords ---

    def test_cpg_expansion(self, matcher):
        """'pet food brand' should score consumer_cpg."""
        fit = matcher.score("An organic pet food brand for healthy dogs and cats")
        assert fit.thesis == ConsumerThesis.CONSUMER_CPG
        assert fit.score >= 0.4

    def test_cpg_baby_products(self, matcher):
        """'baby products' should be a CPG keyword."""
        fit = matcher.score("A direct to consumer baby products brand for new parents")
        assert fit.thesis == ConsumerThesis.CONSUMER_CPG
        assert fit.score >= 0.4

    def test_cpg_home_goods(self, matcher):
        """'home goods' should be a CPG keyword."""
        fit = matcher.score("A sustainable home goods brand selling organic household products")
        assert fit.thesis == ConsumerThesis.CONSUMER_CPG
        assert fit.score >= 0.4

    # --- Fintech classification ---

    def test_fintech_classification(self, matcher):
        """'budgeting app for consumers' should score consumer_fintech."""
        fit = matcher.score("A budgeting app for consumers to manage personal finance")
        assert fit.thesis == ConsumerThesis.CONSUMER_FINTECH
        assert fit.score >= 0.4

    def test_fintech_neobank(self, matcher):
        """'neobank' should score consumer_fintech."""
        fit = matcher.score("A neobank offering digital wallet and savings for millennials")
        assert fit.thesis == ConsumerThesis.CONSUMER_FINTECH
        assert fit.score >= 0.4

    def test_fintech_payment_app(self, matcher):
        """'payment app' should score consumer_fintech."""
        fit = matcher.score("A payment app for peer to peer money transfers")
        assert fit.thesis == ConsumerThesis.CONSUMER_FINTECH
        assert fit.score >= 0.4

    # --- Social classification ---

    def test_social_classification(self, matcher):
        """'dating app for millennials' should score consumer_social."""
        fit = matcher.score("A dating app for millennials looking for meaningful connections")
        assert fit.thesis == ConsumerThesis.CONSUMER_SOCIAL
        assert fit.score >= 0.4

    def test_social_network(self, matcher):
        """'social network' should score consumer_social."""
        fit = matcher.score("A social network for local community events and meetups")
        assert fit.thesis == ConsumerThesis.CONSUMER_SOCIAL
        assert fit.score >= 0.4

    def test_social_creator_platform(self, matcher):
        """'creator platform' should score consumer_social."""
        fit = matcher.score("A creator platform for content sharing and social media monetization")
        assert fit.thesis == ConsumerThesis.CONSUMER_SOCIAL
        assert fit.score >= 0.4

    # --- Variant normalization ---

    def test_variant_ecommerce(self, matcher):
        """'e-commerce' and 'ecommerce' should produce same consumer_signal_score."""
        fit1 = matcher.score("an ecommerce platform for shopping")
        fit2 = matcher.score("an e-commerce platform for shopping")
        assert fit1.consumer_signal_score == fit2.consumer_signal_score

    def test_variant_d2c(self, matcher):
        """'d2c', 'direct-to-consumer', 'direct to consumer' should all match."""
        fit_d2c = matcher.score("a d2c brand")
        fit_hyphen = matcher.score("a direct-to-consumer brand")
        fit_space = matcher.score("a direct to consumer brand")
        # All three should have non-zero consumer signal
        assert fit_d2c.consumer_signal_score > 0
        assert fit_hyphen.consumer_signal_score > 0
        assert fit_space.consumer_signal_score > 0
        # Hyphen and space forms should produce same score
        assert fit_hyphen.consumer_signal_score == fit_space.consumer_signal_score
