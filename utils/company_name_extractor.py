"""
Shared Company Name Extraction Module

Extracts company names and domains from news article titles/descriptions
using a pipeline of: regex → NER → URL extraction → domain promotion.

Controlled by COMPANY_EXTRACTION_MODE env var:
  - baseline (default): regex only, identical to pre-refactor behavior
  - url_promote: regex + URL extraction + domain promotion
  - ner_active: full pipeline including spaCy NER

Usage:
    from utils.company_name_extractor import extract_company_info, ExtractionResult

    result = extract_company_info(
        title="Acme (acme.ai) raises $5M Series A",
        description="Consumer startup Acme announced...",
        url="https://techcrunch.com/2024/acme",
        mode="ner_active",
    )
    print(result.company_name)       # "Acme"
    print(result.promoted_domain)    # "acme.ai"
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from utils.canonical_keys import NEWS_PUBLISHER_DOMAINS, normalize_domain

logger = logging.getLogger(__name__)

# =============================================================================
# TYPES
# =============================================================================

ExtractionMethod = Literal["regex", "ner", "url_derived"]
DomainMethod = Literal["text_url"]
ExtractionMode = Literal["baseline", "url_promote", "ner_active"]

# Ordered from least to most permissive
_MODE_ORDER = {"baseline": 0, "url_promote": 1, "ner_active": 2}


def _get_extraction_mode() -> ExtractionMode:
    """Read COMPANY_EXTRACTION_MODE from env, default 'baseline'."""
    raw = os.getenv("COMPANY_EXTRACTION_MODE", "baseline").strip().lower()
    if raw in _MODE_ORDER:
        return raw  # type: ignore[return-value]
    logger.warning(
        "Invalid COMPANY_EXTRACTION_MODE=%r, falling back to 'baseline'", raw
    )
    return "baseline"


# =============================================================================
# RESULT DATACLASS
# =============================================================================


@dataclass
class ExtractionResult:
    """Result of company name/domain extraction from article text."""

    company_name: Optional[str] = None
    company_name_method: Optional[ExtractionMethod] = None
    candidate_domains: List[str] = field(default_factory=list)
    promoted_domain: Optional[str] = None
    domain_method: Optional[DomainMethod] = None


# =============================================================================
# BLOCKLISTS
# =============================================================================

# Suffix-matched blocklist for URL extraction (not just exact match)
_BLOCKED_DOMAIN_SUFFIXES: tuple[str, ...] = (
    # URL shorteners
    "t.co", "bit.ly", "ow.ly", "tinyurl.com", "lnkd.in",
    # Social platforms
    "linkedin.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "youtube.com", "reddit.com",
    # App stores
    "apps.apple.com", "play.google.com",
    # Cloud/dev
    "amazonaws.com", "googleusercontent.com", "cloud.google.com",
    "npmjs.com", "github.com", "github.io",
)

# Publisher ORG names (separate from domain set) — lowercase
NEWS_PUBLISHER_NAMES: frozenset[str] = frozenset({
    "techcrunch", "forbes", "bloomberg", "wsj", "reuters",
    "cnbc", "bbc", "cnn", "wired", "axios", "fortune", "inc",
    "venturebeat", "the verge", "business insider", "the information",
    "wall street journal", "new york times", "washington post",
    "the guardian", "associated press", "pr newswire", "globenewswire",
    "product hunt", "crunchbase", "pitchbook",
})

# Legal suffixes for normalization (same values as phase_g_entity_resolver.py:68-72
# but avoids import coupling)
LEGAL_SUFFIXES: frozenset[str] = frozenset({
    "inc", "inc.", "llc", "ltd", "ltd.", "corp", "corp.",
    "co", "co.", "plc", "gmbh", "ag", "sa", "srl",
    "limited", "incorporated", "corporation", "company",
})


# =============================================================================
# 1. REGEX EXTRACTION (moved from NewsArticle.extract_company_name)
# =============================================================================

# Common words to filter out
_COMMON_WORDS = frozenset({"the", "a", "an", "this", "new", "how", "why", "what", "when"})

# Verb alternation (case-insensitive via inline flag)
_VERBS = r"(?i:raises|raised|announces|announced|launches|launched|unveils|secures|secured|closes|closed|wins|won|makes|debuts|expands|expanded|partners|partnered)"


def extract_via_regex(title: str) -> Optional[str]:
    """
    Extract company name from article title using regex patterns.

    Identical patterns to the original NewsArticle.extract_company_name().
    Pure function, no side effects.

    Patterns (in priority order):
    - "CompanyName raises/announces/launches/unveils/secures/closes..."
    - Multi-word: "Oura Ring raises..." or "Daily Harvest raises..."
    - "... backs CompanyName in ..." / "invests in CompanyName"
    - Quoted: "'CompanyName' raises..." / '"CompanyName" launches...'
    - "startup CompanyName raises..."
    """
    if not title:
        return None

    # Group 1: Single-word company at start of title
    single_word_patterns = [
        rf"^([A-Z][a-zA-Z0-9]+)\s+{_VERBS}",
    ]

    # Group 2: Multi-word company at start (up to 4 words before verb)
    multi_word_patterns = [
        rf"^([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]*){{0,3}}?)\s+{_VERBS}",
    ]

    # Group 3: "backs X" / "invests in X" patterns (company in middle)
    mid_sentence_patterns = [
        r"(?i:backs|invests\s+in)\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]*){0,2}?)(?:\s+in\b|\s+with\b|\s*,|\s*$)",
    ]

    # Group 4: Quoted company names
    quoted_patterns = [
        rf"""['\u2018\u201C"]([A-Z][a-zA-Z0-9\s]{{1,25}}?)['\u2019\u201D"]\s+{_VERBS}""",
    ]

    # Group 5: "startup X raises..."
    startup_prefix_patterns = [
        rf"(?i:startup|company|brand|firm)\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]*){{0,2}}?)\s+{_VERBS}",
    ]

    # Try each group in priority order
    for patterns in [
        single_word_patterns,
        multi_word_patterns,
        mid_sentence_patterns,
        quoted_patterns,
        startup_prefix_patterns,
    ]:
        for pattern in patterns:
            match = re.search(pattern, title)
            if match:
                company = match.group(1).strip()
                if company.lower() not in _COMMON_WORDS and len(company) >= 2:
                    return company

    return None


# =============================================================================
# 2. URL EXTRACTION FROM TEXT
# =============================================================================

# Regex for URLs and bare domain mentions
_URL_PATTERN = re.compile(
    r"https?://[^\s)<>\"']+|"             # full URLs
    r"\(([a-zA-Z0-9][\w.-]+\.[a-z]{2,})\)|"  # parenthetical domains: (acme.ai)
    r"\s[—–-]\s([a-zA-Z0-9][\w.-]+\.[a-z]{2,})"  # dash-separated: — acme.ai
)


def _is_blocked_domain(domain: str) -> bool:
    """Check if domain matches blocklist via suffix matching."""
    if not domain:
        return True
    dl = domain.lower()
    # Check NEWS_PUBLISHER_DOMAINS (suffix match for subdomains like m.techcrunch.com)
    for pub in NEWS_PUBLISHER_DOMAINS:
        pub = pub.lstrip(".")  # safety strip
        if dl == pub or dl.endswith("." + pub):
            return True
    # Check suffix blocklist
    for suffix in _BLOCKED_DOMAIN_SUFFIXES:
        suffix = suffix.lstrip(".")  # safety strip
        if dl == suffix or dl.endswith("." + suffix):
            return True
    return False


def extract_urls_from_text(text: str) -> List[str]:
    """
    Extract and filter domains from article text.

    Returns normalized, blocklist-filtered, position-sorted candidate domains.
    First-mentioned = highest priority.
    """
    if not text:
        return []

    candidates: list[tuple[int, str]] = []  # (position, domain)

    for match in _URL_PATTERN.finditer(text):
        pos = match.start()

        # Full URL match (group 0 starting with http)
        full_match = match.group(0)
        if full_match.startswith("http"):
            domain = normalize_domain(full_match)
            if domain:
                candidates.append((pos, domain))
        else:
            # Parenthetical or dash-separated bare domain
            bare = match.group(1) or match.group(2)
            if bare:
                domain = normalize_domain(bare)
                if domain:
                    candidates.append((pos, domain))

    # Filter, dedupe, sort by position
    seen: set[str] = set()
    result: list[str] = []
    for _pos, domain in sorted(candidates, key=lambda x: x[0]):
        if domain in seen:
            continue
        seen.add(domain)
        if not _is_blocked_domain(domain):
            result.append(domain)

    return result


# =============================================================================
# 3. DOMAIN PROMOTION (GATING)
# =============================================================================

# Patterns for strong contextual association
_PAREN_DOMAIN_RE = re.compile(
    r"([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]*){0,3})\s*\(([a-zA-Z0-9][\w.-]+\.[a-z]{2,})\)"
)
_DASH_DOMAIN_RE = re.compile(
    r"([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]*){0,3})\s+[—–-]\s+([a-zA-Z0-9][\w.-]+\.[a-z]{2,})"
)


def _domain_label(domain: str) -> str:
    """Get the label part of a domain, skipping common subdomains."""
    _SKIP = {"app", "blog", "docs", "api", "m", "my", "dev", "web"}
    parts = domain.lower().split(".")
    for part in parts[:-1]:  # skip TLD
        if part not in _SKIP:
            return part
    return parts[0]  # fallback


def _name_tokens(name: str) -> set[str]:
    """Tokenize company name for overlap checking."""
    return {t.lower() for t in re.split(r"[\s\-_]+", name) if len(t) >= 2}


def score_and_promote_domain(
    candidate_domains: List[str],
    company_name: Optional[str],
    text: str,
    *,
    allow_lone_domain: bool = False,
) -> Optional[str]:
    """
    Apply strict gating to decide if a candidate domain should be promoted.

    Rules:
    - If company_name exists AND domain label overlaps with name tokens → promote
    - If no company_name: only promote if domain has strong context pattern
      (parenthetical or dash-separated) — bare URL mention is NOT enough
    - If allow_lone_domain=True AND exactly 1 non-blocked candidate AND no
      company_name: promote that domain (relaxation for RSS collector)
    - Returns None if no domain passes gating
    """
    if not candidate_domains:
        return None

    name_toks = _name_tokens(company_name) if company_name else set()

    # Check for context patterns in original text
    context_domains: set[str] = set()
    for pattern in (_PAREN_DOMAIN_RE, _DASH_DOMAIN_RE):
        for match in pattern.finditer(text):
            raw_domain = match.group(2)
            normalized = normalize_domain(raw_domain)
            if normalized and not _is_blocked_domain(normalized):
                context_domains.add(normalized)

    for domain in candidate_domains:
        label = _domain_label(domain)

        # Cross-check with company_name
        if name_toks and label in name_toks:
            return domain

        # Context pattern (parenthetical or dash-separated)
        if domain in context_domains:
            return domain

    # Lone-domain relaxation: exactly 1 non-blocked candidate, no company_name
    if allow_lone_domain and not company_name and len(candidate_domains) == 1:
        if not _is_blocked_domain(candidate_domains[0]):
            return candidate_domains[0]

    return None


# =============================================================================
# 4. NER EXTRACTION (spaCy)
# =============================================================================

_nlp = None  # Lazy-loaded singleton


def warmup_ner() -> bool:
    """
    Pre-load the spaCy model. Call at start of collect() when mode=ner_active.

    Returns True if model loaded successfully, False otherwise.
    """
    global _nlp
    if _nlp is not None:
        return True
    try:
        import spacy
        logger.info("Loading spaCy model (one-time, ~13MB)...")
        _nlp = spacy.load("en_core_web_sm")
        logger.info("spaCy model loaded successfully")
        return True
    except OSError:
        logger.warning(
            "spaCy model 'en_core_web_sm' not found. "
            "Install with: python -m spacy download en_core_web_sm"
        )
        return False
    except Exception as e:
        logger.warning("Failed to load spaCy model: %s", e)
        return False


def extract_via_ner(
    title: str, description: str = ""
) -> Optional[str]:
    """
    Extract company name via spaCy NER (ORG entities).

    Prioritizes title over description; returns earliest ORG in title
    after filtering publishers and short names.

    Returns None if model not loaded or no valid ORG found.
    """
    global _nlp
    if _nlp is None:
        if not warmup_ner():
            return None

    # Prioritize title, fall back to description
    for text in [title, description]:
        if not text:
            continue
        try:
            doc = _nlp(text)
        except Exception as e:
            logger.warning("spaCy processing error: %s", e)
            continue

        for ent in doc.ents:
            if ent.label_ != "ORG":
                continue
            name = ent.text.strip()
            # Filter: min length, not a publisher
            if len(name) < 2:
                continue
            if name.lower() in NEWS_PUBLISHER_NAMES:
                continue
            return name

    return None


# =============================================================================
# 5. COMPANY NAME NORMALIZATION
# =============================================================================


def normalize_company_name(name: str) -> str:
    """
    Normalize a company name: lowercase, strip punctuation, remove legal suffixes.

    Examples:
        "Acme Inc" → "acme"
        "HealthCo LLC" → "healthco"
        "Daily Harvest, Inc." → "daily harvest"
    """
    if not name:
        return ""

    result = name.lower().strip()
    # Remove common punctuation
    result = re.sub(r"[,.'\"!?()]+", "", result)
    # Collapse whitespace
    result = re.sub(r"\s+", " ", result).strip()

    # Remove legal suffixes (from end)
    tokens = result.split()
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()

    return " ".join(tokens).strip()


# =============================================================================
# 6. MAIN ENTRY POINT
# =============================================================================


def extract_company_info(
    title: str,
    description: str = "",
    url: str = "",
    mode: Optional[ExtractionMode] = None,
    *,
    allow_lone_domain: bool = False,
) -> ExtractionResult:
    """
    Main extraction pipeline. Mode controls which stages run.

    Pipeline order:
    1. extract_via_regex(title)
    2. (ner_active only) extract_via_ner(title, description)
    3. (url_promote+) extract_urls_from_text(title + description)
    4. (url_promote+) score_and_promote_domain(candidates, company_name, text)
    5. If no company_name but promoted_domain → derive name from domain label

    Args:
        title: Article title
        description: Article description/summary
        url: Article URL (not currently used in extraction, reserved)
        mode: Override extraction mode (default: from env var)
        allow_lone_domain: If True, promote a single non-blocked domain even
            without context patterns (used by RSS collector)

    Returns:
        ExtractionResult with company_name, method, candidates, promoted_domain
    """
    if mode is None:
        mode = _get_extraction_mode()

    mode_level = _MODE_ORDER.get(mode, 0)
    result = ExtractionResult()

    # Step 1: Regex extraction (always runs)
    regex_name = extract_via_regex(title)
    if regex_name:
        result.company_name = regex_name
        result.company_name_method = "regex"

    # Step 2: NER extraction (only if mode >= ner_active AND no name yet)
    if mode_level >= 2 and result.company_name is None:
        ner_name = extract_via_ner(title, description)
        if ner_name:
            result.company_name = ner_name
            result.company_name_method = "ner"

    # Step 3: URL extraction (only if mode >= url_promote)
    if mode_level >= 1:
        combined_text = f"{title} {description}".strip()
        result.candidate_domains = extract_urls_from_text(combined_text)

    # Step 4: Domain promotion (only if mode >= url_promote)
    if mode_level >= 1 and result.candidate_domains:
        combined_text = f"{title} {description}".strip()
        promoted = score_and_promote_domain(
            result.candidate_domains, result.company_name, combined_text,
            allow_lone_domain=allow_lone_domain,
        )
        if promoted:
            result.promoted_domain = promoted
            result.domain_method = "text_url"

    # Step 5: Derive name from domain if still no company_name
    if result.company_name is None and result.promoted_domain:
        label = _domain_label(result.promoted_domain)
        if label and len(label) >= 2:
            result.company_name = label.title()
            result.company_name_method = "url_derived"

    # Telemetry logging
    logger.info(
        "company_extraction",
        extra={
            "title": title[:80],
            "mode": mode,
            "company_name_method": result.company_name_method,
            "has_promoted_domain": result.promoted_domain is not None,
            "candidate_count": len(result.candidate_domains),
        },
    )

    return result
