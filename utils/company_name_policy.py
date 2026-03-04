"""Config-driven company-name write policy decisions.

This module centralizes guardrails for canonical company_name writes and
candidate handling so pipelines can enforce a deny-by-default policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import os
import re
import unicodedata

import yaml


DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "company_name_policy.yaml"


class CompanyNamePolicyError(RuntimeError):
    """Raised when company name policy configuration is invalid."""


@dataclass(frozen=True)
class CanonicalState:
    """Current canonical company-name state for one record."""

    name: Optional[str]
    normalized: Optional[str]
    source: Optional[str]
    locked: bool = False


@dataclass(frozen=True)
class Candidate:
    """Candidate company-name value from one extraction source."""

    name: str
    source: str
    source_version: str
    confidence: Optional[float] = None
    evidence: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class Decision:
    """Policy decision describing whether and how to write."""

    allowed: bool
    action: str
    reason: str
    write_payload: Optional[Dict[str, Any]] = None


def _is_empty(value: Optional[str]) -> bool:
    return value is None or not value.strip()


def resolve_policy_path(explicit_path: Optional[str] = None) -> Path:
    """Resolve policy path from explicit arg, env var, or default location."""

    if explicit_path:
        return Path(explicit_path)
    from_env = os.environ.get("COMPANY_NAME_POLICY_PATH", "").strip()
    if from_env:
        return Path(from_env)
    return DEFAULT_POLICY_PATH


def _require_mapping(value: Any, context: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise CompanyNamePolicyError(f"{context} must be a mapping")
    return value


def validate_company_name_policy(policy: Dict[str, Any]) -> None:
    """Validate required policy structure used by the decision engine."""

    required_top_level = {
        "policy_id",
        "policy_version",
        "default_deny",
        "entities",
        "sources",
        "actors",
        "auto_write_rules",
        "candidate_rules",
        "review_rules",
        "normalization",
        "validation",
    }
    missing = sorted(required_top_level - set(policy.keys()))
    if missing:
        raise CompanyNamePolicyError(f"Missing required policy keys: {missing}")

    entities = _require_mapping(policy["entities"], "entities")
    company_entity = _require_mapping(entities.get("company_name"), "entities.company_name")
    _require_mapping(company_entity.get("canonical_fields"), "entities.company_name.canonical_fields")
    _require_mapping(company_entity.get("candidate_fields"), "entities.company_name.candidate_fields")

    sources = _require_mapping(policy["sources"], "sources")
    for source_name, source_cfg in sources.items():
        source_cfg = _require_mapping(source_cfg, f"sources.{source_name}")
        for key in ("precedence", "authoritative", "auto_write_allowed", "auto_overwrite_allowed"):
            if key not in source_cfg:
                raise CompanyNamePolicyError(f"Missing sources.{source_name}.{key}")

    actors = policy["actors"]
    if not isinstance(actors, list) or not actors:
        raise CompanyNamePolicyError("actors must be a non-empty list")
    for actor in actors:
        actor_cfg = _require_mapping(actor, "actors[]")
        for key in ("actor_id", "type", "allowed_actions", "allowed_sources"):
            if key not in actor_cfg:
                raise CompanyNamePolicyError(f"Missing actor key: {key}")

    auto_rules = _require_mapping(policy["auto_write_rules"], "auto_write_rules")
    canonical_rules = _require_mapping(auto_rules.get("canonical"), "auto_write_rules.canonical")
    for key in (
        "fill_only_when_empty",
        "never_overwrite_non_empty",
        "equivalence_noop_on_normalized_match",
        "allowed_sources_for_auto_fill",
    ):
        if key not in canonical_rules:
            raise CompanyNamePolicyError(f"Missing auto_write_rules.canonical.{key}")

    validation = _require_mapping(policy["validation"], "validation")
    validators = validation.get("validators")
    if not isinstance(validators, list):
        raise CompanyNamePolicyError("validation.validators must be a list")


def load_company_name_policy(policy_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate company-name policy from YAML."""

    resolved = resolve_policy_path(policy_path)
    if not resolved.exists():
        raise FileNotFoundError(f"Company name policy file not found: {resolved}")

    with open(resolved, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    if not isinstance(loaded, dict):
        raise CompanyNamePolicyError("Policy YAML must parse to a mapping")

    validate_company_name_policy(loaded)
    return loaded


def load_actor_policy(policy: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    """Resolve actor policy entry with wildcard support (for reviewer:*)."""

    for actor in policy.get("actors", []):
        spec = str(actor.get("actor_id", ""))
        if spec == actor_id:
            return actor
        if spec.endswith("*") and actor_id.startswith(spec[:-1]):
            return actor
    return {
        "actor_id": actor_id,
        "type": "unknown",
        "allowed_actions": [],
        "allowed_sources": [],
    }


def normalize_company_name(name: Optional[str], policy: Dict[str, Any]) -> str:
    """Normalize company names according to policy.normalization config."""

    if not name:
        return ""
    cfg = policy.get("normalization", {})

    value = str(name)
    if cfg.get("unicode_nfkc", False):
        value = unicodedata.normalize("NFKC", value)
    if cfg.get("trim", True):
        value = value.strip()
    if cfg.get("collapse_whitespace", True):
        value = re.sub(r"\s+", " ", value)

    if cfg.get("remove_url_fragments", True):
        value = re.sub(r"(?i)https?://", "", value)
        value = re.sub(r"(?i)www\.", "", value)

    if cfg.get("lowercase", True):
        value = value.lower()

    if cfg.get("strip_punctuation", True):
        keep_ampersand = cfg.get("keep_ampersand", True)
        if keep_ampersand:
            value = re.sub(r"[^A-Za-z0-9&\s]", "", value)
        else:
            value = re.sub(r"[^A-Za-z0-9\s]", "", value)

    suffix_cfg = cfg.get("suffix_standardization", {})
    if isinstance(suffix_cfg, dict) and suffix_cfg.get("enabled", False):
        mapping = {
            str(k).lower(): str(v).lower()
            for k, v in dict(suffix_cfg.get("map", {})).items()
        }
        tokens = value.split()
        standardized = [mapping.get(token.lower(), token) for token in tokens]
        value = " ".join(standardized)

    if cfg.get("collapse_whitespace", True):
        value = re.sub(r"\s+", " ", value)
    return value.strip()


def validate_company_name_candidate(name: str, policy: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate candidate company name against policy.validation validators."""

    validation = policy.get("validation", {})
    if not validation.get("enabled", True):
        return True, "VALIDATION_DISABLED"

    raw = (name or "").strip()
    normalized = normalize_company_name(raw, policy)
    validators = validation.get("validators", [])

    for validator in validators:
        vtype = validator.get("type")
        if vtype == "length_range":
            min_len = int(validator.get("min", 0))
            max_len = int(validator.get("max", 10_000))
            if len(raw) < min_len or len(raw) > max_len:
                return False, f"INVALID_LENGTH({len(raw)})"

        elif vtype == "contains_any":
            raw_lower = raw.lower()
            for token in validator.get("tokens", []):
                if str(token).lower() in raw_lower:
                    return False, f"CONTAINS_TOKEN({token})"

        elif vtype == "generic_terms_only":
            stop_terms = {str(t).lower() for t in validator.get("stoplist", [])}
            tokens = [t for t in normalized.split() if t]
            if tokens and all(t in stop_terms for t in tokens):
                return False, "GENERIC_TERMS_ONLY"

        elif vtype == "starts_with_any":
            tokens = normalized.split()
            starts = {str(t).lower() for t in validator.get("tokens", [])}
            if tokens and tokens[0].lower() in starts:
                return False, f"STARTS_WITH({tokens[0].lower()})"

        elif vtype == "non_alpha_ratio_max":
            if raw:
                non_alpha = sum(1 for ch in raw if not ch.isalpha() and not ch.isspace())
                ratio = non_alpha / max(1, len(raw))
                max_ratio = float(validator.get("max_ratio", 1.0))
                if ratio > max_ratio:
                    return False, f"NON_ALPHA_RATIO({ratio:.2f})"

    return True, "VALID"


def _canonical_fields(policy: Dict[str, Any]) -> Dict[str, str]:
    return policy["entities"]["company_name"]["canonical_fields"]


def _candidate_fields(policy: Dict[str, Any]) -> Dict[str, str]:
    return policy["entities"]["company_name"]["candidate_fields"]


def decide_write_candidate(actor_id: str, candidate: Candidate, policy: Dict[str, Any]) -> Decision:
    """Decide whether candidate rows may be written for this actor/source."""

    actor = load_actor_policy(policy, actor_id)
    if "write_candidates" not in actor.get("allowed_actions", []):
        return Decision(False, "deny", "ACTOR_NOT_ALLOWED_WRITE_CANDIDATES")
    if candidate.source not in actor.get("allowed_sources", []):
        return Decision(False, "deny", "CANDIDATE_SOURCE_NOT_ALLOWED_FOR_ACTOR")
    if not policy.get("candidate_rules", {}).get("store_candidates", True):
        return Decision(False, "deny", "CANDIDATE_STORAGE_DISABLED")

    valid, reason = validate_company_name_candidate(candidate.name, policy)
    drop_invalid = policy.get("candidate_rules", {}).get("drop_invalid_candidates", True)
    if not valid and drop_invalid:
        return Decision(False, "deny", f"CANDIDATE_INVALID_DROPPED:{reason}")

    candidate_fields = _candidate_fields(policy)
    status = "new"
    if candidate.source == "ner":
        status = policy.get("candidate_rules", {}).get("ner", {}).get("write_status", "new")
    payload = {
        candidate_fields["name"]: candidate.name,
        candidate_fields["normalized"]: normalize_company_name(candidate.name, policy),
        candidate_fields["source"]: candidate.source,
        candidate_fields["source_version"]: candidate.source_version,
        candidate_fields["confidence"]: candidate.confidence,
        candidate_fields["evidence"]: candidate.evidence or {},
        candidate_fields["status"]: status,
    }
    return Decision(True, "write_candidate", "OK", payload)


def decide_write_canonical_auto(
    actor_id: str,
    existing: CanonicalState,
    candidate: Candidate,
    policy: Dict[str, Any],
) -> Decision:
    """Decide whether an automated actor may write canonical company_name."""

    actor = load_actor_policy(policy, actor_id)
    rules = policy.get("auto_write_rules", {}).get("canonical", {})

    if "canonical_auto_fill" not in actor.get("allowed_actions", []):
        return Decision(False, "deny", "ACTOR_NOT_ALLOWED_CANONICAL_AUTO_FILL")
    if candidate.source not in actor.get("allowed_sources", []):
        return Decision(False, "deny", "CANDIDATE_SOURCE_NOT_ALLOWED_FOR_ACTOR")
    if candidate.source not in rules.get("allowed_sources_for_auto_fill", []):
        return Decision(False, "deny", "CANDIDATE_SOURCE_NOT_ALLOWED_FOR_AUTO_FILL")

    source_cfg = policy.get("sources", {}).get(candidate.source)
    if not isinstance(source_cfg, dict):
        return Decision(False, "deny", f"UNKNOWN_SOURCE({candidate.source})")
    if not source_cfg.get("auto_write_allowed", False):
        return Decision(False, "deny", "SOURCE_AUTO_WRITE_NOT_ALLOWED")

    if existing.locked:
        return Decision(False, "deny", "EXISTING_LOCKED")

    existing_non_empty = not _is_empty(existing.name)
    if rules.get("never_overwrite_non_empty", True) and existing_non_empty:
        return Decision(False, "deny", "EXISTING_NON_EMPTY_NO_OVERWRITE")
    if rules.get("fill_only_when_empty", True) and existing_non_empty:
        return Decision(False, "deny", "FILL_ONLY_WHEN_EMPTY")

    protected_sources = set(rules.get("protected_existing_sources", []))
    if existing_non_empty and existing.source in protected_sources:
        return Decision(False, "deny", f"EXISTING_SOURCE_PROTECTED({existing.source})")

    regression_guard = rules.get("regression_guard", {})
    guarded_sources = set(regression_guard.get("reject_if_existing_source_in", []))
    if existing_non_empty and existing.source in guarded_sources:
        return Decision(False, "deny", f"REGRESSION_GUARD_BLOCK({existing.source})")

    valid, reason = validate_company_name_candidate(candidate.name, policy)
    if not valid:
        return Decision(False, "deny", f"CANDIDATE_INVALID:{reason}")

    candidate_normalized = normalize_company_name(candidate.name, policy)
    existing_normalized = existing.normalized or normalize_company_name(existing.name, policy)
    if (
        rules.get("equivalence_noop_on_normalized_match", True)
        and candidate_normalized
        and candidate_normalized == existing_normalized
    ):
        return Decision(True, "noop", "EQUIVALENT_NORMALIZED_MATCH")

    fields = _canonical_fields(policy)
    payload = {
        fields["name"]: candidate.name,
        fields["normalized"]: candidate_normalized,
        fields["source"]: candidate.source,
        fields["source_version"]: candidate.source_version,
        fields["confidence"]: candidate.confidence,
        fields["updated_by"]: actor_id,
    }
    return Decision(True, "write_canonical", "OK", payload)


def decide_promote_candidate_review(
    actor_id: str,
    existing: CanonicalState,
    candidate: Candidate,
    reason: Optional[str],
    policy: Dict[str, Any],
) -> Decision:
    """Decide whether reviewer may promote candidate to canonical."""

    actor = load_actor_policy(policy, actor_id)
    promotion = policy.get("review_rules", {}).get("promotion", {})

    if actor.get("type") != "reviewer":
        return Decision(False, "deny", "NOT_A_REVIEWER")
    if "approve_candidate" not in actor.get("allowed_actions", []):
        return Decision(False, "deny", "REVIEWER_NOT_ALLOWED_APPROVE")
    if promotion.get("require_reason", True) and _is_empty(reason):
        return Decision(False, "deny", "REASON_REQUIRED")

    allowed_sources = set(promotion.get("allowed_candidate_sources", []))
    if candidate.source not in allowed_sources:
        return Decision(False, "deny", "CANDIDATE_SOURCE_NOT_ALLOWED_FOR_PROMOTION")

    valid, valid_reason = validate_company_name_candidate(candidate.name, policy)
    if not valid:
        return Decision(False, "deny", f"CANDIDATE_INVALID:{valid_reason}")

    overriding = not _is_empty(existing.name)
    if overriding:
        if "override_canonical" not in actor.get("allowed_actions", []):
            return Decision(False, "deny", "REVIEWER_NOT_ALLOWED_OVERRIDE")
        allowed_override_sources = set(promotion.get("allow_override_when_existing_source_in", []))
        if existing.source not in allowed_override_sources:
            return Decision(
                False,
                "deny",
                f"OVERRIDE_NOT_ALLOWED_FOR_EXISTING_SOURCE({existing.source})",
            )

    target_source = candidate.source
    if candidate.source == "ner":
        target_source = str(promotion.get("resulting_canonical_source_for_ner", "ner_approved"))

    fields = _canonical_fields(policy)
    payload = {
        fields["name"]: candidate.name,
        fields["normalized"]: normalize_company_name(candidate.name, policy),
        fields["source"]: target_source,
        fields["source_version"]: candidate.source_version,
        fields["confidence"]: candidate.confidence,
        fields["updated_by"]: actor_id,
        "review_reason": reason or "",
    }
    return Decision(True, "write_canonical", "REVIEW_APPROVED", payload)


__all__ = [
    "Candidate",
    "CanonicalState",
    "CompanyNamePolicyError",
    "Decision",
    "DEFAULT_POLICY_PATH",
    "decide_promote_candidate_review",
    "decide_write_candidate",
    "decide_write_canonical_auto",
    "load_actor_policy",
    "load_company_name_policy",
    "normalize_company_name",
    "resolve_policy_path",
    "validate_company_name_candidate",
    "validate_company_name_policy",
]
