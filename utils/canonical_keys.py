"""
Canonical Key Helper for Discovery Engine

Generates deterministic, normalized keys for company deduplication across
multiple identity sources (domain, Companies House, Crunchbase, GitHub, etc.)

Key features:
- Strict priority order for canonical key selection
- Multi-candidate support for dedupe/stub promotion
- Proper URL parsing and normalization
- Fallback name+location keys for stealth companies

Priority order:
1. domain (most stable - companies keep their domain)
2. companies_house (authoritative for UK)
3. crunchbase (widely used, stable IDs)
4. pitchbook (if you have access)
5. github_org (for dev-tool companies)
6. github_repo (more specific than org)
7. name_loc (fallback - not stable enough for auto-merge)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse


# Priority order for canonical key selection
_CANONICAL_PREFIX_ORDER = (
    "domain",
    "companies_house",
    "crunchbase",
    "pitchbook",
    "github_org",
    "github_repo",
    "name_loc",
)

_slug_re = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    """Lowercase, keep [a-z0-9], collapse separators to '-'."""
    s = (s or "").strip().lower()
    s = _slug_re.sub("-", s)
    s = s.strip("-")
    return s


# =============================================================================
# NORMALIZERS
# =============================================================================

def normalize_domain(value: str) -> str:
    """
    Normalize a website/domain into a stable root domain key.

    Examples:
      - "https://www.Example.com/path?q=1" -> "example.com"
      - "example.com/" -> "example.com"
      - "http://EXAMPLE.COM" -> "example.com"
      - "www.example.com" -> "example.com"
    """
    if not value:
        return ""

    v = value.strip()
    if not v:
        return ""

    # If it looks like a URL, parse it; else treat as domain-ish
    if "://" in v:
        p = urlparse(v)
        host = p.netloc
    else:
        # Handle values like "example.com/path" or "www.example.com"
        p = urlparse("https://" + v)
        host = p.netloc

    host = (host or "").strip().lower()

    # Strip auth/port if present
    if "@" in host:
        host = host.split("@", 1)[-1]
    if ":" in host:
        host = host.split(":", 1)[0]

    # Common normalization
    host = host.lstrip(".")
    if host.startswith("www."):
        host = host[4:]

    # Basic sanity: must contain a dot to be a domain
    if "." not in host:
        return ""

    return host


def normalize_companies_house_number(value: str) -> str:
    """
    Normalize UK Companies House numbers (and similar).
    Keeps alphanumerics, lowercases.

    Examples:
      - "  12345678 " -> "12345678"
      - "SC123456" -> "sc123456"
      - "NI-123456" -> "ni123456"
    """
    v = (value or "").strip()
    v = re.sub(r"[^A-Za-z0-9]", "", v)
    return v.lower()


def normalize_crunchbase_id(value: str) -> str:
    """
    Crunchbase uses UUID-like IDs or slugs. Normalize to lowercase.
    
    Examples:
      - "Anthropic" -> "anthropic"
      - "anthropic-ai" -> "anthropic-ai"
    """
    return (value or "").strip().lower()


def normalize_pitchbook_id(value: str) -> str:
    """
    PitchBook IDs vary by export. Normalize to lowercase trimmed.
    """
    return (value or "").strip().lower()


def normalize_github_org(value: str) -> str:
    """
    Normalize GitHub organization name.
    
    Examples:
      - "Anthropic-AI" -> "anthropic-ai"
      - "  OpenAI  " -> "openai"
    """
    return _slug(value)


def normalize_github_repo(value: str) -> str:
    """
    Normalize GitHub repo to 'org/repo' format.
    Accepts 'Org/Repo' or full URL.
    
    Examples:
      - "Anthropic/claude" -> "anthropic/claude"
      - "https://github.com/OpenAI/gpt-4" -> "openai/gpt-4"
    """
    v = (value or "").strip()
    if not v:
        return ""

    if "github.com" in v:
        p = urlparse(v if "://" in v else "https://" + v)
        path = (p.path or "").strip("/")
        parts = [x for x in path.split("/") if x]
        if len(parts) >= 2:
            org, repo = parts[0], parts[1]
        else:
            return ""
    else:
        parts = [x for x in v.split("/") if x]
        if len(parts) >= 2:
            org, repo = parts[0], parts[1]
        else:
            return ""

    org_s = _slug(org)
    repo_s = _slug(repo)
    if not org_s or not repo_s:
        return ""
    return f"{org_s}/{repo_s}"


# =============================================================================
# CANONICAL KEY BUILDERS
# =============================================================================

def build_canonical_key(
    *,
    domain_or_website: str = "",
    companies_house_number: str = "",
    crunchbase_id: str = "",
    pitchbook_id: str = "",
    github_org: str = "",
    github_repo: str = "",
    fallback_company_name: str = "",
    fallback_region: str = "",
) -> str:
    """
    Return ONE canonical key using strict priority order.

    Priority:
      1) domain
      2) companies_house
      3) crunchbase
      4) pitchbook
      5) github_org
      6) github_repo
      7) name_loc (fallback)

    Returns canonical key in format: "<prefix>:<normalized-value>"
    
    Example:
        build_canonical_key(
            domain_or_website="https://acme.ai",
            companies_house_number="12345678"
        )
        # Returns: "domain:acme.ai"
    """
    candidates = build_canonical_key_candidates(
        domain_or_website=domain_or_website,
        companies_house_number=companies_house_number,
        crunchbase_id=crunchbase_id,
        pitchbook_id=pitchbook_id,
        github_org=github_org,
        github_repo=github_repo,
        fallback_company_name=fallback_company_name,
        fallback_region=fallback_region,
    )
    return candidates[0] if candidates else ""


def build_canonical_key_candidates(
    *,
    domain_or_website: str = "",
    companies_house_number: str = "",
    crunchbase_id: str = "",
    pitchbook_id: str = "",
    github_org: str = "",
    github_repo: str = "",
    fallback_company_name: str = "",
    fallback_region: str = "",
) -> List[str]:
    """
    Return candidate keys in priority order (best first), de-duped.
    
    Useful for:
      - Dedupe checks (try multiple identifiers against existing records)
      - Stub promotion (match on any strong key)
      - Multi-key lookup in suppression cache
    
    Example:
        candidates = build_canonical_key_candidates(
            domain_or_website="https://acme.ai",
            companies_house_number="12345678",
            github_org="acme-ai"
        )
        # Returns: ["domain:acme.ai", "companies_house:12345678", "github_org:acme-ai"]
    """
    out: List[str] = []

    dom = normalize_domain(domain_or_website)
    if dom:
        out.append(f"domain:{dom}")

    ch = normalize_companies_house_number(companies_house_number)
    if ch:
        out.append(f"companies_house:{ch}")

    cb = normalize_crunchbase_id(crunchbase_id)
    if cb:
        out.append(f"crunchbase:{cb}")

    pb = normalize_pitchbook_id(pitchbook_id)
    if pb:
        out.append(f"pitchbook:{pb}")

    org = normalize_github_org(github_org)
    if org:
        out.append(f"github_org:{org}")

    repo = normalize_github_repo(github_repo)
    if repo:
        out.append(f"github_repo:{repo}")

    # Last-resort fallback: not stable enough for auto-merge, but useful for review queues
    name = _slug(fallback_company_name)
    region = _slug(fallback_region)
    if name:
        out.append(f"name_loc:{name}" + (f"|{region}" if region else ""))

    # De-dupe while preserving order
    seen = set()
    deduped: List[str] = []
    for k in out:
        if k not in seen:
            seen.add(k)
            deduped.append(k)

    return deduped


# =============================================================================
# CONVENIENCE WRAPPERS
# =============================================================================

@dataclass(frozen=True)
class CanonicalKeyResult:
    """Result of canonical key generation with all candidates"""
    canonical_key: str
    candidates: List[str]
    
    @property
    def has_strong_key(self) -> bool:
        """True if we have a key stronger than name_loc fallback"""
        if not self.canonical_key:
            return False
        return not self.canonical_key.startswith("name_loc:")
    
    @property
    def key_type(self) -> Optional[str]:
        """Extract the prefix (domain, companies_house, etc.)"""
        if not self.canonical_key:
            return None
        return self.canonical_key.split(":")[0]


def canonical_key_from_external_refs(
    external_refs: Dict[str, str],
    *,
    fallback_company_name: str = "",
    fallback_region: str = "",
) -> CanonicalKeyResult:
    """
    Generate canonical key from a generic external_refs dict.
    
    Expected keys in external_refs:
      - domain / website
      - companies_house_number
      - crunchbase_id
      - pitchbook_id
      - github_org
      - github_repo
    
    Example:
        refs = {
            "website": "https://www.Example.com/product",
            "github_repo": "https://github.com/ExampleLabs/stealth-repo",
            "companies_house_number": "SC123456",
        }
        
        result = canonical_key_from_external_refs(refs, fallback_company_name="Example Labs")
        print(result.canonical_key)   # domain:example.com
        print(result.candidates)      # ["domain:example.com", "companies_house:sc123456", "github_repo:examplelabs/stealth-repo"]
    """
    candidates = build_canonical_key_candidates(
        domain_or_website=external_refs.get("domain") or external_refs.get("website", ""),
        companies_house_number=external_refs.get("companies_house_number", ""),
        crunchbase_id=external_refs.get("crunchbase_id", ""),
        pitchbook_id=external_refs.get("pitchbook_id", ""),
        github_org=external_refs.get("github_org", ""),
        github_repo=external_refs.get("github_repo", ""),
        fallback_company_name=fallback_company_name,
        fallback_region=fallback_region,
    )
    return CanonicalKeyResult(
        canonical_key=(candidates[0] if candidates else ""),
        candidates=candidates
    )


def canonical_key_from_signal(
    signal_type: str,
    signal_data: Dict[str, str],
    *,
    fallback_company_name: str = "",
    fallback_region: str = "",
) -> CanonicalKeyResult:
    """
    Generate canonical key from a signal's raw data.
    
    Maps signal types to their expected identifier fields:
      - github_spike: github_org
      - incorporation: companies_house_number + jurisdiction
      - domain_registration: domain
      - funding_event: crunchbase_id or domain
      - patent_filing: domain (from applicant)
    
    Example:
        result = canonical_key_from_signal(
            "github_spike",
            {"github_org": "anthropic", "repo": "claude"}
        )
        print(result.canonical_key)  # github_org:anthropic
    """
    # Build external_refs from signal data based on signal type
    external_refs: Dict[str, str] = {}
    
    # Always try to extract common fields
    if "domain" in signal_data:
        external_refs["domain"] = signal_data["domain"]
    if "website" in signal_data:
        external_refs["website"] = signal_data["website"]
    
    # Signal-type specific extraction
    if signal_type == "github_spike":
        if "github_org" in signal_data:
            external_refs["github_org"] = signal_data["github_org"]
        if "github_repo" in signal_data:
            external_refs["github_repo"] = signal_data["github_repo"]
    
    elif signal_type == "incorporation":
        if "company_number" in signal_data:
            external_refs["companies_house_number"] = signal_data["company_number"]
        elif "companies_house_number" in signal_data:
            external_refs["companies_house_number"] = signal_data["companies_house_number"]
    
    elif signal_type == "funding_event":
        if "crunchbase_id" in signal_data:
            external_refs["crunchbase_id"] = signal_data["crunchbase_id"]
        if "pitchbook_id" in signal_data:
            external_refs["pitchbook_id"] = signal_data["pitchbook_id"]
    
    return canonical_key_from_external_refs(
        external_refs,
        fallback_company_name=fallback_company_name,
        fallback_region=fallback_region
    )


# =============================================================================
# KEY STRENGTH HELPERS
# =============================================================================

# Keys that are stable enough for automatic merge/dedupe
STRONG_KEY_PREFIXES = {"domain", "companies_house", "crunchbase", "pitchbook"}

# Keys that need human review before merge
WEAK_KEY_PREFIXES = {"github_org", "github_repo", "name_loc"}


def is_strong_key(canonical_key: str) -> bool:
    """
    Check if a canonical key is strong enough for automatic merge.
    
    Strong keys (auto-merge OK):
      - domain:*
      - companies_house:*
      - crunchbase:*
      - pitchbook:*
    
    Weak keys (needs human review):
      - github_org:* (companies change orgs)
      - github_repo:* (repos get renamed)
      - name_loc:* (names are ambiguous)
    """
    if not canonical_key:
        return False
    prefix = canonical_key.split(":")[0]
    return prefix in STRONG_KEY_PREFIXES


def get_key_strength_score(canonical_key: str) -> int:
    """
    Return a strength score for canonical key (higher = more reliable).
    
    Useful for choosing which key to trust when merging records.
    
    Scores:
      - domain: 100 (most stable)
      - companies_house: 95 (authoritative for UK)
      - crunchbase: 80 (widely used)
      - pitchbook: 80 (if you have access)
      - github_org: 50 (can change)
      - github_repo: 40 (can change/rename)
      - name_loc: 10 (ambiguous)
    """
    if not canonical_key:
        return 0
    
    prefix = canonical_key.split(":")[0]
    
    scores = {
        "domain": 100,
        "companies_house": 95,
        "crunchbase": 80,
        "pitchbook": 80,
        "github_org": 50,
        "github_repo": 40,
        "name_loc": 10,
    }
    
    return scores.get(prefix, 0)


# =============================================================================
# TESTS
# =============================================================================

def _test():
    """Run basic tests"""
    
    # Test domain normalization
    assert normalize_domain("https://www.Example.com/path?q=1") == "example.com"
    assert normalize_domain("example.com/") == "example.com"
    assert normalize_domain("http://EXAMPLE.COM") == "example.com"
    assert normalize_domain("www.example.com") == "example.com"
    assert normalize_domain("") == ""
    
    # Test Companies House normalization
    assert normalize_companies_house_number("  12345678 ") == "12345678"
    assert normalize_companies_house_number("SC123456") == "sc123456"
    assert normalize_companies_house_number("NI-123-456") == "ni123456"
    
    # Test GitHub repo normalization
    assert normalize_github_repo("Anthropic/claude") == "anthropic/claude"
    assert normalize_github_repo("https://github.com/OpenAI/gpt-4") == "openai/gpt-4"
    
    # Test canonical key building
    key = build_canonical_key(
        domain_or_website="https://acme.ai",
        companies_house_number="12345678"
    )
    assert key == "domain:acme.ai"  # Domain takes priority
    
    # Test candidates
    candidates = build_canonical_key_candidates(
        domain_or_website="https://acme.ai",
        companies_house_number="12345678",
        github_org="acme-ai"
    )
    assert candidates == ["domain:acme.ai", "companies_house:12345678", "github_org:acme-ai"]
    
    # Test external refs
    refs = {
        "website": "https://www.Example.com/product",
        "github_repo": "https://github.com/ExampleLabs/stealth-repo",
        "companies_house_number": "SC123456",
    }
    result = canonical_key_from_external_refs(refs, fallback_company_name="Example Labs", fallback_region="UK-Scotland")
    assert result.canonical_key == "domain:example.com"
    assert result.has_strong_key == True
    assert "companies_house:sc123456" in result.candidates
    assert "github_repo:examplelabs/stealth-repo" in result.candidates
    
    # Test key strength
    assert is_strong_key("domain:acme.ai") == True
    assert is_strong_key("companies_house:12345678") == True
    assert is_strong_key("github_org:acme") == False
    assert is_strong_key("name_loc:acme|uk") == False
    
    print("✅ All tests passed")


if __name__ == "__main__":
    _test()
