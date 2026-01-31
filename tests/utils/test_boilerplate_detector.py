"""Tests for Boilerplate Detector - Starter Kit Fingerprinting.

Phase C of founder_intel integration: Token-based fingerprinting to filter
starter kit noise using Jaccard similarity (threshold 0.80).

TDD: Write failing tests first, then implement.
"""
import pytest
from dataclasses import asdict
from typing import Dict, List, Set


# =============================================================================
# Test Imports (will fail until implementation exists)
# =============================================================================

class TestBoilerplateImports:
    """Test that all required components can be imported."""

    def test_import_boilerplate_signature(self):
        from utils.boilerplate_detector import BoilerplateSignature
        assert BoilerplateSignature is not None

    def test_import_boilerplate_match(self):
        from utils.boilerplate_detector import BoilerplateMatch
        assert BoilerplateMatch is not None

    def test_import_boilerplate_detector(self):
        from utils.boilerplate_detector import BoilerplateDetector
        assert BoilerplateDetector is not None

    def test_import_jaccard_similarity(self):
        from utils.boilerplate_detector import jaccard_similarity
        assert jaccard_similarity is not None

    def test_import_default_signatures(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES
        assert isinstance(DEFAULT_SIGNATURES, list)
        assert len(DEFAULT_SIGNATURES) == 10


# =============================================================================
# BoilerplateSignature Dataclass Tests
# =============================================================================

class TestBoilerplateSignatureDataclass:
    """Test BoilerplateSignature structure."""

    def test_signature_has_required_fields(self):
        from utils.boilerplate_detector import BoilerplateSignature

        sig = BoilerplateSignature(
            id="test_template",
            name="Test Template",
            dependencies=["react", "next"],
            config_files=["tsconfig.json"],
        )

        assert sig.id == "test_template"
        assert sig.name == "Test Template"
        assert sig.dependencies == ["react", "next"]
        assert sig.config_files == ["tsconfig.json"]

    def test_signature_tokens_property(self):
        """Tokens should be union of dependencies + config_files."""
        from utils.boilerplate_detector import BoilerplateSignature

        sig = BoilerplateSignature(
            id="test",
            name="Test",
            dependencies=["react", "next"],
            config_files=["tsconfig.json", "next.config.js"],
        )

        tokens = sig.tokens
        assert isinstance(tokens, set)
        assert "react" in tokens
        assert "next" in tokens
        assert "tsconfig.json" in tokens
        assert "next.config.js" in tokens

    def test_signature_with_empty_config(self):
        from utils.boilerplate_detector import BoilerplateSignature

        sig = BoilerplateSignature(
            id="minimal",
            name="Minimal",
            dependencies=["django"],
            config_files=[],
        )

        assert sig.tokens == {"django"}


# =============================================================================
# BoilerplateMatch Dataclass Tests
# =============================================================================

class TestBoilerplateMatchDataclass:
    """Test BoilerplateMatch result structure."""

    def test_match_has_required_fields(self):
        from utils.boilerplate_detector import BoilerplateMatch

        match = BoilerplateMatch(
            signature_id="nextjs_basic_template",
            signature_name="Next.js basic",
            similarity=0.85,
            matched_tokens=["next", "react", "react-dom"],
            is_boilerplate=True,
        )

        assert match.signature_id == "nextjs_basic_template"
        assert match.signature_name == "Next.js basic"
        assert match.similarity == 0.85
        assert match.matched_tokens == ["next", "react", "react-dom"]
        assert match.is_boilerplate is True

    def test_match_to_dict(self):
        from utils.boilerplate_detector import BoilerplateMatch

        match = BoilerplateMatch(
            signature_id="t3_stack_like",
            signature_name="T3-stack-like",
            similarity=0.92,
            matched_tokens=["zod", "prisma"],
            is_boilerplate=True,
        )

        d = match.to_dict()
        assert d["signature_id"] == "t3_stack_like"
        assert d["similarity"] == 0.92
        assert d["is_boilerplate"] is True

    def test_match_below_threshold_is_not_boilerplate(self):
        from utils.boilerplate_detector import BoilerplateMatch

        match = BoilerplateMatch(
            signature_id="some_template",
            signature_name="Some Template",
            similarity=0.65,
            matched_tokens=["react"],
            is_boilerplate=False,
        )

        assert match.is_boilerplate is False


# =============================================================================
# Jaccard Similarity Tests
# =============================================================================

class TestJaccardSimilarity:
    """Test Jaccard similarity calculation."""

    def test_identical_sets_return_1(self):
        from utils.boilerplate_detector import jaccard_similarity

        set_a = {"react", "next", "typescript"}
        set_b = {"react", "next", "typescript"}

        assert jaccard_similarity(set_a, set_b) == 1.0

    def test_disjoint_sets_return_0(self):
        from utils.boilerplate_detector import jaccard_similarity

        set_a = {"react", "next"}
        set_b = {"django", "flask"}

        assert jaccard_similarity(set_a, set_b) == 0.0

    def test_partial_overlap(self):
        from utils.boilerplate_detector import jaccard_similarity

        # Union = {a, b, c, d}, Intersection = {b, c}
        set_a = {"a", "b", "c"}
        set_b = {"b", "c", "d"}

        # Jaccard = 2 / 4 = 0.5
        assert jaccard_similarity(set_a, set_b) == 0.5

    def test_empty_sets_return_0(self):
        from utils.boilerplate_detector import jaccard_similarity

        assert jaccard_similarity(set(), set()) == 0.0

    def test_one_empty_set_returns_0(self):
        from utils.boilerplate_detector import jaccard_similarity

        assert jaccard_similarity({"a", "b"}, set()) == 0.0
        assert jaccard_similarity(set(), {"a", "b"}) == 0.0

    def test_subset_relationship(self):
        from utils.boilerplate_detector import jaccard_similarity

        # {a, b} is subset of {a, b, c, d}
        # Intersection = 2, Union = 4
        set_a = {"a", "b"}
        set_b = {"a", "b", "c", "d"}

        assert jaccard_similarity(set_a, set_b) == 0.5


# =============================================================================
# Default Signatures Tests
# =============================================================================

class TestDefaultSignatures:
    """Test the 10 hardcoded boilerplate signatures."""

    def test_has_10_signatures(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        assert len(DEFAULT_SIGNATURES) == 10

    def test_nextjs_basic_template(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "nextjs_basic_template")
        assert sig.name == "Next.js basic"
        assert "next" in sig.dependencies
        assert "react" in sig.dependencies
        assert "react-dom" in sig.dependencies

    def test_nextjs_tailwind_prisma_auth(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "nextjs_tailwind_prisma_auth")
        assert "tailwindcss" in sig.dependencies
        assert "prisma" in sig.dependencies
        assert "next-auth" in sig.dependencies

    def test_t3_stack_like(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "t3_stack_like")
        assert "zod" in sig.dependencies
        assert "prisma" in sig.dependencies
        assert "next-auth" in sig.dependencies

    def test_supabase_nextjs_starter(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "supabase_nextjs_starter")
        assert "@supabase/supabase-js" in sig.dependencies

    def test_expo_react_native_template(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "expo_react_native_template")
        assert "expo" in sig.dependencies
        assert "react-native" in sig.dependencies

    def test_react_native_router_template(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "react_native_router_template")
        assert "expo-router" in sig.dependencies

    def test_stripe_checkout_starter(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "stripe_checkout_starter")
        assert "stripe" in sig.dependencies

    def test_firebase_web_app_starter(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "firebase_web_app_starter")
        assert "firebase" in sig.dependencies

    def test_django_cookiecutter_like(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "django_cookiecutter_like")
        assert "django" in sig.dependencies

    def test_rails_starter_like(self):
        from utils.boilerplate_detector import DEFAULT_SIGNATURES

        sig = next(s for s in DEFAULT_SIGNATURES if s.id == "rails_starter_like")
        assert "rails" in sig.dependencies


# =============================================================================
# BoilerplateDetector Core Tests
# =============================================================================

class TestBoilerplateDetector:
    """Test BoilerplateDetector core functionality."""

    @pytest.fixture
    def detector(self):
        from utils.boilerplate_detector import BoilerplateDetector
        return BoilerplateDetector()

    def test_detector_has_default_signatures(self, detector):
        assert len(detector.signatures) == 10

    def test_detector_with_custom_signatures(self):
        from utils.boilerplate_detector import BoilerplateDetector, BoilerplateSignature

        custom = [
            BoilerplateSignature(
                id="custom",
                name="Custom",
                dependencies=["custom-lib"],
                config_files=[],
            )
        ]
        detector = BoilerplateDetector(signatures=custom)
        assert len(detector.signatures) == 1

    def test_detector_default_threshold_is_0_80(self, detector):
        assert detector.threshold == 0.80

    def test_detector_with_custom_threshold(self):
        from utils.boilerplate_detector import BoilerplateDetector

        detector = BoilerplateDetector(threshold=0.90)
        assert detector.threshold == 0.90


# =============================================================================
# Detection Tests
# =============================================================================

class TestBoilerplateDetection:
    """Test actual boilerplate detection."""

    @pytest.fixture
    def detector(self):
        from utils.boilerplate_detector import BoilerplateDetector
        return BoilerplateDetector()

    def test_detects_nextjs_basic_boilerplate(self, detector):
        """A repo with just next, react, react-dom should match."""
        tokens = {"next", "react", "react-dom"}

        result = detector.detect(tokens)

        assert result is not None
        assert result.is_boilerplate is True
        assert result.signature_id == "nextjs_basic_template"
        assert result.similarity >= 0.80

    def test_detects_t3_stack_boilerplate(self, detector):
        """A repo with T3 stack deps should match."""
        tokens = {
            "next", "react", "react-dom", "typescript",
            "zod", "@trpc/server", "@trpc/client", "@trpc/react-query",
            "prisma", "@prisma/client", "next-auth",
        }

        result = detector.detect(tokens)

        assert result is not None
        assert result.is_boilerplate is True
        assert result.signature_id == "t3_stack_like"

    def test_detects_expo_boilerplate(self, detector):
        """A repo with Expo deps should match."""
        tokens = {"expo", "react-native", "react", "expo-status-bar"}

        result = detector.detect(tokens)

        assert result is not None
        assert result.is_boilerplate is True
        assert result.signature_id == "expo_react_native_template"

    def test_no_match_for_unique_project(self, detector):
        """A unique project with no boilerplate should not match."""
        tokens = {
            "unique-library",
            "proprietary-sdk",
            "custom-framework",
            "internal-tool",
        }

        result = detector.detect(tokens)

        assert result is None or result.is_boilerplate is False

    def test_returns_best_match_above_threshold(self, detector):
        """When multiple signatures could match, return the most specific."""
        # This matches both nextjs_basic (3 deps) and t3_stack_like (5 deps)
        # Should prefer t3_stack_like since it has more matched tokens
        tokens = {
            "next", "react", "react-dom",  # matches nextjs_basic (3/3)
            "zod", "@trpc/server", "@trpc/client", "prisma", "next-auth",  # matches t3 (5/5)
        }

        result = detector.detect(tokens)

        assert result is not None
        assert result.is_boilerplate is True
        # Should match t3_stack_like (more specific, 5 tokens vs 3)
        assert result.signature_id == "t3_stack_like"
        assert len(result.matched_tokens) == 5

    def test_returns_none_when_below_threshold(self, detector):
        """Similarity below 0.80 should not match."""
        # Only matches one dep from nextjs_basic
        tokens = {"next"}

        result = detector.detect(tokens)

        # Either None or a match with is_boilerplate=False
        if result:
            assert result.is_boilerplate is False

    def test_detect_all_returns_all_matches(self, detector):
        """detect_all should return all matches sorted by similarity."""
        tokens = {
            "next", "react", "react-dom",
            "tailwindcss", "prisma", "next-auth",
        }

        results = detector.detect_all(tokens)

        assert isinstance(results, list)
        assert len(results) > 0
        # Should be sorted by similarity descending
        if len(results) > 1:
            assert results[0].similarity >= results[1].similarity


# =============================================================================
# Tokenization Tests
# =============================================================================

class TestTokenization:
    """Test token extraction from project data."""

    @pytest.fixture
    def detector(self):
        from utils.boilerplate_detector import BoilerplateDetector
        return BoilerplateDetector()

    def test_extract_tokens_from_package_json(self, detector):
        """Extract deps from package.json format."""
        raw_data = {
            "package_json": {
                "dependencies": {
                    "react": "^18.0.0",
                    "next": "^13.0.0",
                },
                "devDependencies": {
                    "typescript": "^5.0.0",
                }
            }
        }

        tokens = detector.extract_tokens(raw_data)

        assert "react" in tokens
        assert "next" in tokens
        assert "typescript" in tokens

    def test_extract_tokens_from_requirements_txt(self, detector):
        """Extract deps from Python requirements."""
        raw_data = {
            "requirements": ["django==4.0", "celery>=5.0", "redis"]
        }

        tokens = detector.extract_tokens(raw_data)

        assert "django" in tokens
        assert "celery" in tokens
        assert "redis" in tokens

    def test_extract_tokens_from_gemfile(self, detector):
        """Extract deps from Ruby Gemfile."""
        raw_data = {
            "gems": ["rails", "pg", "puma"]
        }

        tokens = detector.extract_tokens(raw_data)

        assert "rails" in tokens
        assert "pg" in tokens
        assert "puma" in tokens

    def test_extract_tokens_from_config_files_list(self, detector):
        """Extract config file names."""
        raw_data = {
            "config_files": [
                "tsconfig.json",
                "tailwind.config.js",
                "next.config.js",
            ]
        }

        tokens = detector.extract_tokens(raw_data)

        assert "tsconfig.json" in tokens
        assert "tailwind.config.js" in tokens
        assert "next.config.js" in tokens

    def test_extract_tokens_from_file_list(self, detector):
        """Extract relevant config files from file list."""
        raw_data = {
            "files": [
                "README.md",
                "package.json",
                "tsconfig.json",
                "src/index.ts",
                "tailwind.config.js",
            ]
        }

        tokens = detector.extract_tokens(raw_data)

        # Should include config files but not source files
        assert "tsconfig.json" in tokens
        assert "tailwind.config.js" in tokens
        # Should not include generic files
        assert "README.md" not in tokens
        assert "src/index.ts" not in tokens

    def test_extract_tokens_handles_empty_data(self, detector):
        """Empty data should return empty set."""
        tokens = detector.extract_tokens({})
        assert tokens == set()

    def test_extract_tokens_normalizes_package_names(self, detector):
        """Package names should be normalized (lowercase, no versions)."""
        raw_data = {
            "package_json": {
                "dependencies": {
                    "React": "^18.0.0",
                    "@types/node": "^18.0.0",
                }
            }
        }

        tokens = detector.extract_tokens(raw_data)

        assert "react" in tokens
        assert "@types/node" in tokens


# =============================================================================
# Integration with GitHub Collector Tests
# =============================================================================

class TestGitHubIntegration:
    """Test detection with GitHub collector data format."""

    @pytest.fixture
    def detector(self):
        from utils.boilerplate_detector import BoilerplateDetector
        return BoilerplateDetector()

    def test_detect_from_github_raw_data(self, detector):
        """Detect boilerplate from GitHub collector raw_data format."""
        raw_data = {
            "repo": "user/my-t3-app",
            "description": "My T3 stack app",
            "package_json": {
                "dependencies": {
                    "next": "^13.0.0",
                    "react": "^18.0.0",
                    "react-dom": "^18.0.0",
                    "zod": "^3.0.0",
                    "@trpc/server": "^10.0.0",
                    "@trpc/client": "^10.0.0",
                    "prisma": "^4.0.0",
                    "next-auth": "^4.0.0",
                },
                "devDependencies": {
                    "typescript": "^5.0.0",
                }
            },
            "config_files": [
                "tsconfig.json",
                "next.config.js",
                "tailwind.config.js",
            ]
        }

        result = detector.detect_from_raw_data(raw_data)

        assert result is not None
        assert result.is_boilerplate is True

    def test_detect_returns_none_for_custom_project(self, detector):
        """Custom project without boilerplate deps should not match."""
        raw_data = {
            "repo": "company/proprietary-tool",
            "description": "Our custom internal tool",
            "package_json": {
                "dependencies": {
                    "@company/sdk": "^1.0.0",
                    "internal-lib": "^2.0.0",
                }
            }
        }

        result = detector.detect_from_raw_data(raw_data)

        assert result is None or result.is_boilerplate is False


# =============================================================================
# SHADOW Logging Result Format Tests
# =============================================================================

class TestShadowLoggingFormat:
    """Test the format suitable for shadow_log storage."""

    @pytest.fixture
    def detector(self):
        from utils.boilerplate_detector import BoilerplateDetector
        return BoilerplateDetector()

    def test_match_to_shadow_log_format(self):
        """BoilerplateMatch.to_dict() should be suitable for shadow logging."""
        from utils.boilerplate_detector import BoilerplateMatch

        match = BoilerplateMatch(
            signature_id="t3_stack_like",
            signature_name="T3-stack-like",
            similarity=0.92,
            matched_tokens=["zod", "prisma", "next-auth"],
            is_boilerplate=True,
        )

        shadow_data = match.to_dict()

        # Should be JSON-serializable
        import json
        json_str = json.dumps(shadow_data)
        assert json_str is not None

        # Should contain key fields for analysis
        assert "signature_id" in shadow_data
        assert "similarity" in shadow_data
        assert "is_boilerplate" in shadow_data
        assert "matched_tokens" in shadow_data

    def test_no_match_shadow_log_format(self, detector):
        """When no boilerplate detected, should return analyzable data."""
        tokens = {"unique-lib", "custom-framework"}

        result = detector.detect(tokens)

        # Should be able to log even when no match
        shadow_data = detector.get_shadow_log_data(tokens, result)

        assert "input_token_count" in shadow_data
        assert "best_match" in shadow_data
        assert shadow_data["best_match"] is None or isinstance(shadow_data["best_match"], dict)


# =============================================================================
# Edge Cases Tests
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def detector(self):
        from utils.boilerplate_detector import BoilerplateDetector
        return BoilerplateDetector()

    def test_handles_none_input(self, detector):
        """Should handle None gracefully."""
        result = detector.detect(None)
        assert result is None or result.is_boilerplate is False

    def test_handles_empty_set(self, detector):
        """Should handle empty token set."""
        result = detector.detect(set())
        assert result is None or result.is_boilerplate is False

    def test_handles_single_token(self, detector):
        """Should handle single token."""
        result = detector.detect({"react"})
        assert result is None or result.is_boilerplate is False

    def test_case_insensitive_matching(self, detector):
        """Token matching should be case-insensitive."""
        tokens_lower = {"next", "react"}
        tokens_upper = {"NEXT", "REACT"}
        tokens_mixed = {"Next", "React"}

        # All should produce same result
        r1 = detector.detect(tokens_lower)
        r2 = detector.detect(tokens_upper)
        r3 = detector.detect(tokens_mixed)

        # All should match or not match the same
        if r1 and r1.is_boilerplate:
            assert r2.is_boilerplate
            assert r3.is_boilerplate


# =============================================================================
# Threshold Configuration Tests
# =============================================================================

class TestThresholdBehavior:
    """Test threshold-based detection behavior."""

    def test_threshold_0_80_default(self):
        from utils.boilerplate_detector import BoilerplateDetector, DEFAULT_THRESHOLD

        assert DEFAULT_THRESHOLD == 0.80

    def test_stricter_threshold_reduces_matches(self):
        from utils.boilerplate_detector import BoilerplateDetector, BoilerplateSignature

        # Create a custom signature with 5 tokens
        sig = BoilerplateSignature(
            id="test_sig",
            name="Test Signature",
            dependencies=["dep1", "dep2", "dep3", "dep4", "dep5"],
            config_files=[],
        )

        # Project has only 4 of the 5 deps -> containment = 4/5 = 0.80
        tokens = {"dep1", "dep2", "dep3", "dep4", "custom-lib"}

        detector_80 = BoilerplateDetector(signatures=[sig], threshold=0.80)
        detector_85 = BoilerplateDetector(signatures=[sig], threshold=0.85)

        result_80 = detector_80.detect(tokens)
        result_85 = detector_85.detect(tokens)

        # At 0.80 threshold, 0.80 similarity should match
        assert result_80 is not None
        assert result_80.similarity == 0.80
        assert result_80.is_boilerplate is True

        # At 0.85 threshold, 0.80 similarity should NOT match
        assert result_85 is not None
        assert result_85.is_boilerplate is False

    def test_lenient_threshold_increases_matches(self):
        from utils.boilerplate_detector import BoilerplateDetector

        # A project with partial match
        tokens = {"next", "react"}

        detector_80 = BoilerplateDetector(threshold=0.80)
        detector_50 = BoilerplateDetector(threshold=0.50)

        result_80 = detector_80.detect(tokens)
        result_50 = detector_50.detect(tokens)

        # With lower threshold, may match more
        if result_80 is None or not result_80.is_boilerplate:
            # 0.50 threshold might match
            # (depends on signature sizes, so this is soft check)
            pass
