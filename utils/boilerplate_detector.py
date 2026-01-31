"""Boilerplate Detector - Starter Kit Fingerprinting.

Phase C of founder_intel integration: Token-based fingerprinting to filter
starter kit noise using Jaccard similarity.

Key Concepts:
- Signatures: Predefined token sets for known boilerplate templates
- Tokens: Dependencies + config files extracted from project data
- Jaccard similarity: |intersection| / |union| (threshold 0.80 for match)
- SHADOW mode: Logs matches without suppressing signals

10 Default Signatures (from founder_intel_canonical):
1. nextjs_basic_template - Next.js basic
2. nextjs_tailwind_prisma_auth - Next.js + Tailwind + Prisma + Auth
3. t3_stack_like - T3-stack-like
4. supabase_nextjs_starter - Supabase + Next.js
5. expo_react_native_template - Expo React Native
6. react_native_router_template - RN app-router
7. stripe_checkout_starter - Stripe checkout
8. firebase_web_app_starter - Firebase web app
9. django_cookiecutter_like - Django cookiecutter
10. rails_starter_like - Rails starter

Usage:
    from utils.boilerplate_detector import BoilerplateDetector

    detector = BoilerplateDetector()

    # From token set
    tokens = {"next", "react", "react-dom"}
    result = detector.detect(tokens)
    if result and result.is_boilerplate:
        print(f"Matched: {result.signature_name} ({result.similarity:.0%})")

    # From GitHub raw_data
    result = detector.detect_from_raw_data(raw_data)

    # For SHADOW logging
    shadow_data = detector.get_shadow_log_data(tokens, result)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# =============================================================================
# Constants
# =============================================================================

DEFAULT_THRESHOLD = 0.80

# Config files that indicate project structure
CONFIG_FILE_PATTERNS = {
    "tsconfig.json",
    "jsconfig.json",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "tailwind.config.js",
    "tailwind.config.ts",
    "postcss.config.js",
    "webpack.config.js",
    "vite.config.js",
    "vite.config.ts",
    "rollup.config.js",
    "babel.config.js",
    ".babelrc",
    "eslint.config.js",
    ".eslintrc",
    ".eslintrc.json",
    "prettier.config.js",
    ".prettierrc",
    "jest.config.js",
    "vitest.config.ts",
    "playwright.config.ts",
    "prisma/schema.prisma",
    "drizzle.config.ts",
    "app.json",
    "expo.json",
    "metro.config.js",
    "Gemfile",
    "Gemfile.lock",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "manage.py",
    "settings.py",
    "docker-compose.yml",
    "Dockerfile",
    "Makefile",
    ".env.example",
    ".env.local.example",
}


# =============================================================================
# Dataclasses
# =============================================================================

@dataclass
class BoilerplateSignature:
    """A boilerplate template signature.

    Attributes:
        id: Unique identifier (snake_case)
        name: Human-readable name
        dependencies: List of package dependencies
        config_files: List of config files typical for this template
    """
    id: str
    name: str
    dependencies: List[str]
    config_files: List[str] = field(default_factory=list)

    @property
    def tokens(self) -> Set[str]:
        """Union of dependencies and config files as a set."""
        all_tokens = set(self.dependencies) | set(self.config_files)
        return {t.lower() for t in all_tokens}


@dataclass
class BoilerplateMatch:
    """Result of boilerplate detection.

    Attributes:
        signature_id: ID of matched signature
        signature_name: Human-readable name
        similarity: Jaccard similarity score (0-1)
        matched_tokens: List of tokens that matched
        is_boilerplate: True if similarity >= threshold
    """
    signature_id: str
    signature_name: str
    similarity: float
    matched_tokens: List[str]
    is_boilerplate: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "signature_id": self.signature_id,
            "signature_name": self.signature_name,
            "similarity": round(self.similarity, 4),
            "matched_tokens": self.matched_tokens,
            "is_boilerplate": self.is_boilerplate,
        }


# =============================================================================
# Default Signatures (from founder_intel_canonical)
# =============================================================================

# Signatures use KEY deps only (from founder_intel_canonical signatures.json)
# These are the distinguishing deps - not all possible deps
DEFAULT_SIGNATURES: List[BoilerplateSignature] = [
    BoilerplateSignature(
        id="nextjs_basic_template",
        name="Next.js basic",
        # Core trio that defines a basic Next.js app
        dependencies=["next", "react", "react-dom"],
        config_files=[],
    ),
    BoilerplateSignature(
        id="nextjs_tailwind_prisma_auth",
        name="Next.js + Tailwind + Prisma + Auth",
        # Key differentiating deps from basic Next.js
        dependencies=["tailwindcss", "prisma", "next-auth"],
        config_files=[],
    ),
    BoilerplateSignature(
        id="t3_stack_like",
        name="T3-stack-like",
        # T3's distinctive deps: tRPC + Zod + Prisma + Auth
        dependencies=["zod", "@trpc/server", "@trpc/client", "prisma", "next-auth"],
        config_files=[],
    ),
    BoilerplateSignature(
        id="supabase_nextjs_starter",
        name="Supabase + Next.js",
        # Supabase's distinctive dep
        dependencies=["@supabase/supabase-js"],
        config_files=[],
    ),
    BoilerplateSignature(
        id="expo_react_native_template",
        name="Expo React Native",
        # Core Expo deps
        dependencies=["expo", "react-native"],
        config_files=[],
    ),
    BoilerplateSignature(
        id="react_native_router_template",
        name="RN app-router",
        # expo-router is the distinguishing dep
        dependencies=["expo-router"],
        config_files=[],
    ),
    BoilerplateSignature(
        id="stripe_checkout_starter",
        name="Stripe checkout",
        # Stripe's client-side lib
        dependencies=["stripe"],
        config_files=[],
    ),
    BoilerplateSignature(
        id="firebase_web_app_starter",
        name="Firebase web app",
        # Firebase core dep
        dependencies=["firebase"],
        config_files=[],
    ),
    BoilerplateSignature(
        id="django_cookiecutter_like",
        name="Django cookiecutter",
        # Django core
        dependencies=["django"],
        config_files=[],
    ),
    BoilerplateSignature(
        id="rails_starter_like",
        name="Rails starter",
        # Rails core
        dependencies=["rails"],
        config_files=[],
    ),
]


# =============================================================================
# Jaccard Similarity
# =============================================================================

def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Calculate Jaccard similarity between two sets.

    Jaccard = |intersection| / |union|

    Args:
        set_a: First set
        set_b: Second set

    Returns:
        Similarity score between 0.0 and 1.0
    """
    if not set_a or not set_b:
        return 0.0

    intersection = set_a & set_b
    union = set_a | set_b

    if not union:
        return 0.0

    return len(intersection) / len(union)


def containment_similarity(project_tokens: Set[str], signature_tokens: Set[str]) -> float:
    """Calculate containment: what fraction of signature tokens are in project.

    This is more suitable for boilerplate detection than Jaccard because:
    - A project that contains ALL of a signature's deps is a match
    - Extra deps in the project shouldn't reduce the score

    Containment = |intersection| / |signature|

    Args:
        project_tokens: Tokens from the project being checked
        signature_tokens: Tokens from the boilerplate signature

    Returns:
        Score between 0.0 and 1.0
    """
    if not project_tokens or not signature_tokens:
        return 0.0

    intersection = project_tokens & signature_tokens
    return len(intersection) / len(signature_tokens)


# =============================================================================
# BoilerplateDetector
# =============================================================================

class BoilerplateDetector:
    """Detect boilerplate/starter kit projects using token fingerprinting.

    Uses Jaccard similarity between project tokens and known boilerplate
    signatures to identify template-based projects.
    """

    def __init__(
        self,
        signatures: Optional[List[BoilerplateSignature]] = None,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        """Initialize detector.

        Args:
            signatures: List of signatures to match against (defaults to DEFAULT_SIGNATURES)
            threshold: Jaccard similarity threshold for match (default 0.80)
        """
        self.signatures = signatures if signatures is not None else DEFAULT_SIGNATURES
        self.threshold = threshold

    def detect(self, tokens: Optional[Set[str]]) -> Optional[BoilerplateMatch]:
        """Detect if tokens match a boilerplate signature.

        Uses containment similarity: what fraction of signature deps are present.
        This is better than Jaccard because extra deps shouldn't reduce match score.

        When multiple signatures have same similarity, prefer the more specific
        one (more matched tokens).

        Args:
            tokens: Set of tokens (dependencies + config files)

        Returns:
            Best matching BoilerplateMatch if similarity >= threshold, else None
        """
        if not tokens:
            return None

        # Normalize tokens
        normalized = {t.lower() for t in tokens}

        best_match: Optional[BoilerplateMatch] = None
        best_similarity = 0.0
        best_matched_count = 0

        for sig in self.signatures:
            sig_tokens = sig.tokens

            # Use containment: what % of signature tokens are in project
            similarity = containment_similarity(normalized, sig_tokens)
            matched = normalized & sig_tokens
            matched_count = len(matched)

            # Prefer higher similarity, then more matched tokens (more specific)
            is_better = (
                similarity > best_similarity or
                (similarity == best_similarity and matched_count > best_matched_count)
            )

            if is_better:
                best_similarity = similarity
                best_matched_count = matched_count
                best_match = BoilerplateMatch(
                    signature_id=sig.id,
                    signature_name=sig.name,
                    similarity=similarity,
                    matched_tokens=sorted(matched),
                    is_boilerplate=similarity >= self.threshold,
                )

        return best_match

    def detect_all(self, tokens: Optional[Set[str]]) -> List[BoilerplateMatch]:
        """Detect all matching signatures, sorted by similarity descending.

        Args:
            tokens: Set of tokens (dependencies + config files)

        Returns:
            List of all matches sorted by similarity (highest first)
        """
        if not tokens:
            return []

        normalized = {t.lower() for t in tokens}
        matches = []

        for sig in self.signatures:
            sig_tokens = sig.tokens
            similarity = containment_similarity(normalized, sig_tokens)

            if similarity > 0:
                matched = list(normalized & sig_tokens)
                matches.append(BoilerplateMatch(
                    signature_id=sig.id,
                    signature_name=sig.name,
                    similarity=similarity,
                    matched_tokens=sorted(matched),
                    is_boilerplate=similarity >= self.threshold,
                ))

        # Sort by similarity descending
        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches

    def extract_tokens(self, raw_data: Dict[str, Any]) -> Set[str]:
        """Extract tokens from raw data.

        Supports:
        - package.json format (dependencies, devDependencies)
        - requirements list (Python)
        - gems list (Ruby)
        - config_files list
        - files list (extracts config files)

        Args:
            raw_data: Raw data from collector

        Returns:
            Set of normalized tokens
        """
        tokens: Set[str] = set()

        # Extract from package.json
        if "package_json" in raw_data:
            pkg = raw_data["package_json"]
            if isinstance(pkg, dict):
                # Dependencies
                for dep_key in ("dependencies", "devDependencies", "peerDependencies"):
                    deps = pkg.get(dep_key, {})
                    if isinstance(deps, dict):
                        tokens.update(deps.keys())

        # Extract from requirements (Python)
        if "requirements" in raw_data:
            reqs = raw_data["requirements"]
            if isinstance(reqs, list):
                for req in reqs:
                    # Strip version specifiers: django==4.0 -> django
                    pkg_name = re.split(r'[<>=!~\[]', str(req))[0].strip()
                    if pkg_name:
                        tokens.add(pkg_name)

        # Extract from gems (Ruby)
        if "gems" in raw_data:
            gems = raw_data["gems"]
            if isinstance(gems, list):
                tokens.update(str(g) for g in gems)

        # Extract from config_files list
        if "config_files" in raw_data:
            cfg_files = raw_data["config_files"]
            if isinstance(cfg_files, list):
                tokens.update(str(f) for f in cfg_files)

        # Extract config files from general files list
        if "files" in raw_data:
            files = raw_data["files"]
            if isinstance(files, list):
                for f in files:
                    filename = str(f).split("/")[-1]  # Get basename
                    if filename in CONFIG_FILE_PATTERNS:
                        tokens.add(filename)

        # Normalize: lowercase
        return {t.lower() for t in tokens if t}

    def detect_from_raw_data(self, raw_data: Dict[str, Any]) -> Optional[BoilerplateMatch]:
        """Detect boilerplate from collector raw_data.

        Args:
            raw_data: Raw data from collector (e.g., GitHub)

        Returns:
            BoilerplateMatch if detected, else None
        """
        tokens = self.extract_tokens(raw_data)
        return self.detect(tokens)

    def get_shadow_log_data(
        self,
        tokens: Optional[Set[str]],
        result: Optional[BoilerplateMatch],
    ) -> Dict[str, Any]:
        """Get data suitable for shadow_log storage.

        Args:
            tokens: Input tokens
            result: Detection result (can be None)

        Returns:
            Dict suitable for JSON serialization and shadow logging
        """
        return {
            "input_token_count": len(tokens) if tokens else 0,
            "input_tokens_sample": sorted(list(tokens))[:20] if tokens else [],
            "best_match": result.to_dict() if result else None,
            "threshold": self.threshold,
        }


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI for testing boilerplate detector."""
    import json

    detector = BoilerplateDetector()

    # Test cases
    test_cases = [
        ("Next.js basic", {"next", "react", "react-dom"}),
        ("T3 stack", {
            "next", "react", "react-dom", "typescript",
            "zod", "@trpc/server", "@trpc/client", "@trpc/react-query",
            "prisma", "@prisma/client", "next-auth",
        }),
        ("Expo app", {"expo", "react-native", "react", "expo-status-bar"}),
        ("Django project", {"django", "celery", "redis", "psycopg2", "gunicorn"}),
        ("Custom project", {"unique-lib", "proprietary-sdk", "custom-framework"}),
    ]

    print("=" * 70)
    print("BOILERPLATE DETECTOR TEST")
    print(f"Threshold: {detector.threshold}")
    print("=" * 70)

    for name, tokens in test_cases:
        result = detector.detect(tokens)

        if result and result.is_boilerplate:
            print(f"\n[BOILERPLATE] {name}")
            print(f"   Matched: {result.signature_name}")
            print(f"   Similarity: {result.similarity:.1%}")
            print(f"   Tokens: {', '.join(result.matched_tokens[:5])}")
        else:
            similarity = result.similarity if result else 0
            print(f"\n[UNIQUE] {name}")
            print(f"   Best similarity: {similarity:.1%}")

    print("\n" + "=" * 70)
    print(f"\nSignatures ({len(DEFAULT_SIGNATURES)}):")
    for sig in DEFAULT_SIGNATURES:
        print(f"  - {sig.id}: {sig.name}")


if __name__ == "__main__":
    main()
