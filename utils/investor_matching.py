"""
InvestorMatcher - Matches startups to relevant investors based on portfolio behavior.

Architecture:
1. Extract startup claims (sector, stage, geo, business model)
2. Stage 1: FTS5 candidate retrieval from investor_profile_fts (K=300)
3. Stage 2: Multi-factor scoring:
   - FTS score (BM25)
   - Embedding similarity (thesis vs company)
   - Stage distribution match
   - Sector distribution match
   - Preference constraints
4. Generate explanations with portfolio evidence
5. Return top N with match reasons

Sprint 5: Investor Matching v1.

Usage:
    matcher = InvestorMatcher(store)
    results = await matcher.match("domain:acme.ai", n=10)

    for inv in results.matches:
        print(f"{inv.investor_name}: {inv.match_score:.2f}")
        for reason in inv.explanations:
            print(f"  - {reason}")
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    from storage.relationship_store import RelationshipStore

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class PortfolioEvidence:
    """Evidence from investor's portfolio."""
    company_key: str
    company_name: Optional[str]
    round_type: Optional[str]
    round_date: Optional[str]
    relationship_type: str


@dataclass
class MatchExplanation:
    """Explanation for why an investor matches."""
    reason: str                          # Human-readable reason
    predicate: str                       # sector_preference, stage_preference, etc.
    value: str                           # fintech, seed, etc.
    lift_score: Optional[float]          # Log-odds vs baseline
    support_count: int                   # Portfolio companies supporting
    portfolio_examples: List[PortfolioEvidence] = field(default_factory=list)


@dataclass
class InvestorMatch:
    """A matched investor with scores and explanations."""
    investor_id: str
    investor_name: str
    investor_type: str
    hq_country: Optional[str]
    match_score: float                   # Combined score 0-1
    fts_score: float                     # BM25 component
    embedding_score: float               # Cosine similarity
    stage_score: float                   # Stage distribution match
    sector_score: float                  # Sector distribution match
    constraint_score: float              # Preference compliance
    explanations: List[MatchExplanation] = field(default_factory=list)
    is_cold_start: bool = False
    portfolio_count: int = 0
    rank: int = 0


@dataclass
class InvestorMatchResult:
    """Complete result from investor matching."""
    company_key: str
    matches: List[InvestorMatch]
    candidates_retrieved: int
    candidates_scored: int
    query_claims: Dict[str, str]         # Claims used for matching


# =============================================================================
# SCORING WEIGHTS
# =============================================================================

DEFAULT_WEIGHTS = {
    'fts': 0.20,           # BM25 keyword match
    'embedding': 0.25,     # Semantic similarity
    'stage': 0.20,         # Stage distribution fit
    'sector': 0.25,        # Sector distribution fit
    'constraint': 0.10,    # Preference compliance
}

# Cold-start penalty (investors with < 3 portfolio companies)
COLD_START_PENALTY = 0.15

# Minimum candidates to retrieve from FTS
FTS_CANDIDATE_K = 300


# =============================================================================
# SCORING FUNCTIONS
# =============================================================================

def compute_distribution_match(
    target_value: str,
    distribution: Dict[str, float],
    default_score: float = 0.3,
) -> float:
    """
    Compute how well a target value matches an investor's distribution.

    Args:
        target_value: The value to match (e.g., "seed", "fintech")
        distribution: Investor's distribution {value: probability}
        default_score: Score if value not in distribution

    Returns:
        Score 0-1 representing match quality
    """
    if not distribution or not target_value:
        return default_score

    # Exact match
    if target_value.lower() in {k.lower() for k in distribution}:
        for k, v in distribution.items():
            if k.lower() == target_value.lower():
                return min(v * 2, 1.0)  # Scale up (0.5 prob -> 1.0 score)

    # Partial match (check for substring)
    for k, v in distribution.items():
        if target_value.lower() in k.lower() or k.lower() in target_value.lower():
            return min(v * 1.5, 0.8)

    return default_score


def compute_constraint_score(
    company_claims: Dict[str, str],
    preferences: List[Dict[str, Any]],
) -> float:
    """
    Compute how well a company matches investor preferences.

    Args:
        company_claims: Company's claims {predicate: value}
        preferences: Investor's preferences from DB

    Returns:
        Score 0-1 (1.0 = fully compliant, 0.0 = hard_no triggered)
    """
    if not preferences:
        return 1.0  # No constraints = full compliance

    score = 1.0
    has_match = False

    for pref in preferences:
        pref_type = pref.get('preference_type', '')
        predicate = pref.get('predicate', '')
        value = pref.get('value', '')
        weight = pref.get('weight', 1.0)

        company_value = company_claims.get(predicate, '').lower()
        pref_value = value.lower()

        # Check if preference applies
        matches = company_value == pref_value or pref_value in company_value

        if pref_type == 'hard_no' and matches:
            return 0.0  # Immediate disqualification

        if pref_type == 'exclude' and matches:
            score -= 0.3 * weight

        if pref_type == 'penalize' and matches:
            score -= 0.15 * weight

        if pref_type == 'include' and matches:
            score += 0.1 * weight
            has_match = True

        if pref_type == 'boost' and matches:
            score += 0.2 * weight
            has_match = True

    return max(0.0, min(1.0, score))


def compute_final_score(
    fts_score: float,
    embedding_score: float,
    stage_score: float,
    sector_score: float,
    constraint_score: float,
    is_cold_start: bool,
    weights: Dict[str, float] = None,
) -> float:
    """
    Compute weighted final score.

    Args:
        fts_score: BM25 score (0-1)
        embedding_score: Cosine similarity (0-1)
        stage_score: Stage distribution match (0-1)
        sector_score: Sector distribution match (0-1)
        constraint_score: Preference compliance (0-1)
        is_cold_start: Whether investor has < 3 portfolio companies
        weights: Optional custom weights

    Returns:
        Final score 0-1
    """
    w = weights or DEFAULT_WEIGHTS

    score = (
        fts_score * w['fts'] +
        embedding_score * w['embedding'] +
        stage_score * w['stage'] +
        sector_score * w['sector'] +
        constraint_score * w['constraint']
    )

    if is_cold_start:
        score -= COLD_START_PENALTY

    return max(0.0, min(1.0, score))


# =============================================================================
# EXPLANATION GENERATION
# =============================================================================

def generate_explanation(
    claim: Dict[str, Any],
    portfolio_entries: List[Dict[str, Any]],
    max_examples: int = 2,
) -> MatchExplanation:
    """
    Generate a human-readable explanation from a claim.

    Args:
        claim: Investor profile claim from DB
        portfolio_entries: Investor's portfolio for evidence
        max_examples: Max portfolio examples to include

    Returns:
        MatchExplanation with formatted reason
    """
    predicate = claim.get('predicate', '')
    value = claim.get('value', '')
    lift_score = claim.get('lift_score')
    support_count = claim.get('support_count', 0)

    # Format predicate for display
    predicate_display = predicate.replace('_preference', '').replace('_', ' ').title()

    # Build reason string
    if lift_score and lift_score > 0.5:
        reason = f"Strong {predicate_display.lower()} fit: {value} (lift +{lift_score:.1f}, {support_count} portfolio companies)"
    elif lift_score and lift_score > 0:
        reason = f"Matches {predicate_display.lower()}: {value} ({support_count} portfolio companies)"
    else:
        reason = f"Invests in {predicate_display.lower()}: {value}"

    # Find portfolio examples
    examples = []
    for entry in portfolio_entries[:max_examples]:
        examples.append(PortfolioEvidence(
            company_key=entry.get('company_key', ''),
            company_name=entry.get('company_name'),
            round_type=entry.get('round_type'),
            round_date=entry.get('round_date'),
            relationship_type=entry.get('relationship_type', ''),
        ))

    return MatchExplanation(
        reason=reason,
        predicate=predicate,
        value=value,
        lift_score=lift_score,
        support_count=support_count,
        portfolio_examples=examples,
    )


# =============================================================================
# INVESTOR MATCHER
# =============================================================================

class InvestorMatcher:
    """
    Matches startups to relevant investors using portfolio forensics.

    Uses a hybrid FTS + multi-factor scoring approach:
    1. FTS5 keyword candidates from investor profile claims
    2. Embedding similarity for semantic matching
    3. Distribution matching for stage/sector fit
    4. Preference constraints for compliance
    5. Warmth boost from relationship data (Phase 4)
    """

    def __init__(
        self,
        store: "SignalStore",
        weights: Optional[Dict[str, float]] = None,
        candidate_k: int = FTS_CANDIDATE_K,
        relationship_store: Optional["RelationshipStore"] = None,
        user_email: Optional[str] = None,
    ):
        """
        Initialize matcher.

        Args:
            store: SignalStore instance with investor tables
            weights: Optional custom scoring weights
            candidate_k: Number of FTS candidates to retrieve
            relationship_store: Optional RelationshipStore for warmth boost
            user_email: User email for relationship lookup
        """
        self.store = store
        self.weights = weights or DEFAULT_WEIGHTS
        self.candidate_k = candidate_k
        self.relationship_store = relationship_store
        self.user_email = user_email

    async def match(
        self,
        company_key: str,
        company_claims: Optional[Dict[str, str]] = None,
        company_embedding: Optional[np.ndarray] = None,
        top_n: int = 10,
        save_results: bool = True,
    ) -> InvestorMatchResult:
        """
        Find matching investors for a company.

        Args:
            company_key: Canonical key of the company
            company_claims: Optional pre-extracted claims {predicate: value}
            company_embedding: Optional pre-computed embedding
            top_n: Number of top matches to return
            save_results: Whether to save matches to DB

        Returns:
            InvestorMatchResult with ranked matches and explanations
        """
        # Get company claims if not provided
        if company_claims is None:
            company_claims = await self._get_company_claims(company_key)

        if not company_claims:
            logger.warning(f"No claims found for {company_key}, using empty claims")
            company_claims = {}

        # Stage 1: FTS candidate retrieval
        candidates = await self._search_fts_candidates(company_claims)
        candidates_retrieved = len(candidates)

        if not candidates:
            logger.info(f"No FTS candidates for {company_key}")
            return InvestorMatchResult(
                company_key=company_key,
                matches=[],
                candidates_retrieved=0,
                candidates_scored=0,
                query_claims=company_claims,
            )

        # Stage 2: Score each candidate
        scored_matches = []
        for candidate in candidates:
            investor_id = candidate['investor_id']

            try:
                match = await self._score_candidate(
                    investor_id=investor_id,
                    company_claims=company_claims,
                    company_embedding=company_embedding,
                    fts_score=candidate.get('bm25_score', 0.5),
                )
                if match:
                    scored_matches.append(match)
            except Exception as e:
                logger.warning(f"Failed to score investor {investor_id}: {e}")

        # Sort by score and take top N
        scored_matches.sort(key=lambda m: m.match_score, reverse=True)
        top_matches = scored_matches[:top_n]

        # Assign ranks
        for i, match in enumerate(top_matches):
            match.rank = i + 1

        # Save to DB if requested
        if save_results and top_matches:
            await self._save_matches(company_key, top_matches)

        return InvestorMatchResult(
            company_key=company_key,
            matches=top_matches,
            candidates_retrieved=candidates_retrieved,
            candidates_scored=len(scored_matches),
            query_claims=company_claims,
        )

    async def _get_company_claims(self, company_key: str) -> Dict[str, str]:
        """Get company claims from the claims table."""
        if not self.store._db:
            return {}

        cursor = await self.store._db.execute(
            """
            SELECT predicate, value
            FROM claims
            WHERE entity_key = ? AND status = 'active'
            """,
            (company_key,),
        )
        rows = await cursor.fetchall()

        claims = {}
        for predicate, value in rows:
            # Map to investor matching predicates
            if predicate in ('industry', 'sector'):
                claims['sector'] = value
            elif predicate == 'stage':
                claims['stage'] = value
            elif predicate == 'location':
                claims['geo'] = value
            elif predicate == 'business_model':
                claims['business_model'] = value
            else:
                claims[predicate] = value

        return claims

    async def _search_fts_candidates(
        self,
        company_claims: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """
        Search investor_profile_fts for candidates matching company claims.

        Returns list of {investor_id, bm25_score}
        """
        if not self.store._db:
            return []

        # Build FTS query from claims
        query_parts = []
        for predicate, value in company_claims.items():
            if predicate in ('sector', 'stage', 'geo', 'business_model'):
                # Format: "sector:fintech" or just "fintech"
                query_parts.append(f"{predicate}:{value}")
                query_parts.append(value)

        if not query_parts:
            # Fallback: get all investors
            cursor = await self.store._db.execute(
                """
                SELECT id as investor_id, 0.5 as bm25_score
                FROM investors
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (self.candidate_k,),
            )
        else:
            # FTS query with OR
            fts_query = " OR ".join(set(query_parts))

            try:
                cursor = await self.store._db.execute(
                    """
                    SELECT
                        investor_id,
                        bm25(investor_profile_fts) as bm25_score
                    FROM investor_profile_fts
                    WHERE investor_profile_fts MATCH ?
                    ORDER BY bm25_score
                    LIMIT ?
                    """,
                    (fts_query, self.candidate_k),
                )
            except Exception as e:
                logger.warning(f"FTS search failed: {e}, falling back to all investors")
                cursor = await self.store._db.execute(
                    """
                    SELECT id as investor_id, 0.5 as bm25_score
                    FROM investors
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (self.candidate_k,),
                )

        rows = await cursor.fetchall()

        # Normalize BM25 scores to 0-1
        if rows:
            scores = [abs(row[1]) for row in rows]  # BM25 returns negative
            max_score = max(scores) if scores else 1.0
            return [
                {
                    'investor_id': row[0],
                    'bm25_score': abs(row[1]) / max_score if max_score > 0 else 0.5
                }
                for row in rows
            ]

        return []

    async def _score_candidate(
        self,
        investor_id: str,
        company_claims: Dict[str, str],
        company_embedding: Optional[np.ndarray],
        fts_score: float,
    ) -> Optional[InvestorMatch]:
        """Score a single investor candidate."""
        if not self.store._db:
            return None

        # Get investor info
        cursor = await self.store._db.execute(
            """
            SELECT i.id, i.name, i.investor_type, i.hq_country,
                   ip.stage_distribution, ip.sector_distribution,
                   ip.portfolio_count, ip.is_cold_start, ip.thesis_embedding
            FROM investors i
            LEFT JOIN investor_profiles ip ON i.id = ip.investor_id
            WHERE i.id = ?
            """,
            (investor_id,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        inv_id, name, inv_type, country = row[0], row[1], row[2], row[3]
        stage_dist_json, sector_dist_json = row[4], row[5]
        portfolio_count, is_cold_start, thesis_embedding_blob = row[6] or 0, bool(row[7]), row[8]

        # Parse distributions
        stage_dist = json.loads(stage_dist_json) if stage_dist_json else {}
        sector_dist = json.loads(sector_dist_json) if sector_dist_json else {}

        # Compute component scores
        stage_score = compute_distribution_match(
            company_claims.get('stage', ''),
            stage_dist,
        )
        sector_score = compute_distribution_match(
            company_claims.get('sector', ''),
            sector_dist,
        )

        # Embedding score
        embedding_score = 0.5  # Default
        if company_embedding is not None and thesis_embedding_blob:
            try:
                thesis_embedding = np.frombuffer(thesis_embedding_blob, dtype=np.float32)
                embedding_score = float(np.dot(company_embedding, thesis_embedding) / (
                    np.linalg.norm(company_embedding) * np.linalg.norm(thesis_embedding) + 1e-8
                ))
                embedding_score = (embedding_score + 1) / 2  # Normalize to 0-1
            except Exception as e:
                logger.debug(f"Embedding score failed for {investor_id}: {e}")

        # Get preferences and compute constraint score
        preferences = await self._get_investor_preferences(investor_id)
        constraint_score = compute_constraint_score(company_claims, preferences)

        # Final score
        final_score = compute_final_score(
            fts_score=fts_score,
            embedding_score=embedding_score,
            stage_score=stage_score,
            sector_score=sector_score,
            constraint_score=constraint_score,
            is_cold_start=is_cold_start,
            weights=self.weights,
        )

        # Get explanations from claims
        explanations = await self._get_explanations(investor_id, company_claims)

        return InvestorMatch(
            investor_id=inv_id,
            investor_name=name,
            investor_type=inv_type or 'vc',
            hq_country=country,
            match_score=final_score,
            fts_score=fts_score,
            embedding_score=embedding_score,
            stage_score=stage_score,
            sector_score=sector_score,
            constraint_score=constraint_score,
            explanations=explanations,
            is_cold_start=is_cold_start,
            portfolio_count=portfolio_count,
        )

    async def _get_investor_preferences(
        self,
        investor_id: str,
    ) -> List[Dict[str, Any]]:
        """Get investor preferences from DB."""
        if not self.store._db:
            return []

        cursor = await self.store._db.execute(
            """
            SELECT preference_type, predicate, value, weight
            FROM investor_preferences
            WHERE investor_id = ?
            """,
            (investor_id,),
        )
        rows = await cursor.fetchall()

        return [
            {
                'preference_type': row[0],
                'predicate': row[1],
                'value': row[2],
                'weight': row[3],
            }
            for row in rows
        ]

    async def _get_explanations(
        self,
        investor_id: str,
        company_claims: Dict[str, str],
        max_explanations: int = 3,
    ) -> List[MatchExplanation]:
        """Generate explanations from matching claims."""
        if not self.store._db:
            return []

        # Get investor's profile claims
        claims = await self.store.get_investor_profile_claims(investor_id)

        # Get portfolio for evidence
        portfolio = await self.store.get_investor_portfolio(investor_id)

        explanations = []
        for claim in claims[:max_explanations * 2]:  # Get more to filter
            predicate = claim.get('predicate', '')
            value = claim.get('value', '')

            # Check if this claim matches company
            claim_type = predicate.replace('_preference', '')
            company_value = company_claims.get(claim_type, '').lower()

            if company_value and (value.lower() in company_value or company_value in value.lower()):
                explanation = generate_explanation(claim, portfolio)
                explanations.append(explanation)

                if len(explanations) >= max_explanations:
                    break

        # If no matching explanations, add top claims by lift
        if not explanations and claims:
            for claim in sorted(claims, key=lambda c: c.get('lift_score', 0) or 0, reverse=True)[:max_explanations]:
                explanation = generate_explanation(claim, portfolio)
                explanations.append(explanation)

        return explanations

    async def _save_matches(
        self,
        company_key: str,
        matches: List[InvestorMatch],
    ) -> None:
        """Save matches to investor_matches table."""
        for match in matches:
            try:
                await self.store.save_investor_match(
                    company_key=company_key,
                    investor_id=match.investor_id,
                    match_score=match.match_score,
                    explanation=[e.reason for e in match.explanations],
                    rank=match.rank,
                    fts_score=match.fts_score,
                    embedding_score=match.embedding_score,
                    constraint_score=match.constraint_score,
                    evidence=[
                        {
                            'company_key': ex.company_key,
                            'round_type': ex.round_type,
                        }
                        for exp in match.explanations
                        for ex in exp.portfolio_examples
                    ] if match.explanations else None,
                )
            except Exception as e:
                logger.warning(f"Failed to save match {match.investor_id}: {e}")

    async def match_batch(
        self,
        company_keys: List[str],
        top_n: int = 10,
        save_results: bool = True,
    ) -> Dict[str, InvestorMatchResult]:
        """
        Match multiple companies to investors.

        Args:
            company_keys: List of company canonical keys
            top_n: Number of top matches per company
            save_results: Whether to save matches to DB

        Returns:
            Dict mapping company_key to InvestorMatchResult
        """
        results = {}
        for key in company_keys:
            try:
                result = await self.match(
                    company_key=key,
                    top_n=top_n,
                    save_results=save_results,
                )
                results[key] = result
            except Exception as e:
                logger.error(f"Batch match failed for {key}: {e}")
                results[key] = InvestorMatchResult(
                    company_key=key,
                    matches=[],
                    candidates_retrieved=0,
                    candidates_scored=0,
                    query_claims={},
                )

        return results


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def match_company_to_investors(
    store: "SignalStore",
    company_key: str,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """
    Convenience function to match a company to investors.

    Args:
        store: SignalStore instance
        company_key: Company canonical key
        top_n: Number of matches

    Returns:
        List of match dicts with investor info and explanations
    """
    matcher = InvestorMatcher(store)
    result = await matcher.match(company_key, top_n=top_n)

    return [
        {
            'investor_id': m.investor_id,
            'investor_name': m.investor_name,
            'investor_type': m.investor_type,
            'hq_country': m.hq_country,
            'match_score': m.match_score,
            'rank': m.rank,
            'explanations': [e.reason for e in m.explanations],
            'is_cold_start': m.is_cold_start,
        }
        for m in result.matches
    ]
