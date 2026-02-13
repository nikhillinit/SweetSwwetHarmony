"""Deterministic Web3/crypto co-occurrence detector.

Instead of flagging any mention of "token", "dao", etc., checks if these
ambiguous terms appear NEAR crypto-specific context.

Import constraint: this module must remain self-contained.
No imports from consumer/, workflows/, or storage/. Only stdlib.
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass
class Web3DetectionResult:
    """Result of Web3 detection."""
    is_crypto: bool
    matched_term: Optional[str] = None
    reason: Optional[str] = None
    details: List[str] = field(default_factory=list)


class Web3Detector:
    """Deterministic Web3/crypto co-occurrence detector.

    Instead of flagging any mention of "token", "dao", etc.,
    checks if these ambiguous terms appear NEAR crypto-specific context.

    NOTE: dao, mining, metaverse are AMBIGUOUS (co-occurrence required),
    not unambiguous. This is intentional -- "DAO pattern", "data mining",
    and "metaverse experiences" are legitimate non-crypto uses.
    """

    # Always crypto (no co-occurrence needed)
    UNAMBIGUOUS_CRYPTO: Set[str] = {
        "blockchain", "cryptocurrency", "bitcoin", "btc",
        "ethereum", "eth", "solana", "nft", "defi",
        "tokenomics", "smart contract", "dapp",
        "play to earn", "p2e", "yield farming",
        "metamask", "opensea", "staking",
        "web3",  # Thesis excludes Web3 categorically
    }

    # Ambiguous -- only crypto if near CRYPTO_CONTEXT terms
    # (or if not neutralized by a rescue phrase)
    AMBIGUOUS_TERMS: Set[str] = {
        "token", "tokens",
        "dao",
        "wallet",
        "mining",
        "metaverse",
    }

    # Rescue phrases: when an ambiguous term appears inside one of
    # these phrases, that specific occurrence is neutralized.
    # Matched with \b word boundaries; supports pluralization.
    RESCUE_PHRASES = {
        "token": ["access token", "access tokens", "auth token", "bearer token",
                   "session token", "refresh token", "api token", "loyalty token",
                   "loyalty tokens", "token ring", "tokenization"],
        "tokens": ["access tokens", "loyalty tokens"],
        "dao": ["dao pattern", "data access object"],
        "mining": ["data mining", "mining insights", "process mining", "text mining"],
        "wallet": ["digital wallet", "mobile wallet", "e-wallet", "ewallet"],
    }

    # Context terms that make ambiguous terms crypto.
    # NOTE: Use specific phrases to avoid false positives
    # ("proof of concept", "mint condition" are non-crypto).
    CRYPTO_CONTEXT: Set[str] = {
        "blockchain", "ethereum", "solana", "crypto",
        "nft", "defi", "decentralized", "on-chain",
        "smart contract", "ledger", "consensus",
        "proof of work", "proof of stake",
        "gas fee", "mint nft", "minting tokens",
        "governance token", "governance tokens",
    }

    COOCCURRENCE_WINDOW: int = int(os.environ.get("WEB3_COOCCURRENCE_WINDOW", "100"))

    def detect(self, text: Optional[str]) -> Web3DetectionResult:
        """Run the 5-step detection algorithm.

        Steps:
        1. Unambiguous crypto terms (global scan) -> immediate REJECT
        2. Identify ambiguous term occurrences
        3. Apply local rescue -- neutralize rescued occurrences
        4. Co-occurrence check -- scan +/-WINDOW for CRYPTO_CONTEXT
        5. Otherwise -> PASS
        """
        # Defensive guard
        if not text:
            return Web3DetectionResult(is_crypto=False)

        text_lower = text.lower()

        # Step 1: Unambiguous crypto terms -> immediate REJECT
        for term in self.UNAMBIGUOUS_CRYPTO:
            pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
            match = pattern.search(text_lower)
            if match:
                return Web3DetectionResult(
                    is_crypto=True,
                    matched_term=term,
                    reason=f"Unambiguous crypto term: '{term}'",
                    details=[f"Found '{term}' at position {match.start()}"],
                )

        # Step 2: Identify ambiguous term occurrences
        ambiguous_occurrences = []  # list of (term, match_start, match_end)
        for term in self.AMBIGUOUS_TERMS:
            pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
            for match in pattern.finditer(text_lower):
                ambiguous_occurrences.append((term, match.start(), match.end()))

        if not ambiguous_occurrences:
            return Web3DetectionResult(is_crypto=False)

        # Step 3: Apply local rescue -- neutralize rescued occurrences
        surviving = []
        for term, start, end in ambiguous_occurrences:
            if self._is_rescued(text_lower, term, start, end):
                continue
            surviving.append((term, start, end))

        if not surviving:
            return Web3DetectionResult(is_crypto=False)

        # Step 4: Co-occurrence check
        for term, start, end in surviving:
            window_start = max(0, start - self.COOCCURRENCE_WINDOW)
            window_end = min(len(text_lower), end + self.COOCCURRENCE_WINDOW)
            window_text = text_lower[window_start:window_end]

            for ctx_term in self.CRYPTO_CONTEXT:
                ctx_pattern = re.compile(r"\b" + re.escape(ctx_term) + r"\b", re.IGNORECASE)
                ctx_match = ctx_pattern.search(window_text)
                if ctx_match:
                    return Web3DetectionResult(
                        is_crypto=True,
                        matched_term=term,
                        reason=f"Ambiguous term '{term}' co-occurs with crypto context '{ctx_term}'",
                        details=[
                            f"Ambiguous '{term}' at position {start}",
                            f"Crypto context '{ctx_term}' within {self.COOCCURRENCE_WINDOW}-char window",
                        ],
                    )

        # Step 5: PASS
        return Web3DetectionResult(is_crypto=False)

    def _is_rescued(self, text_lower: str, term: str, start: int, end: int) -> bool:
        """Check if an ambiguous term occurrence falls within a rescue phrase.

        Rescue phrases are matched with word boundaries. Hyphenated forms
        (e.g., "access-token") are handled by normalizing hyphens to spaces
        during rescue phrase matching only (not applied to the full input).
        """
        rescue_list = self.RESCUE_PHRASES.get(term, [])
        if not rescue_list:
            return False

        # Extract a local window around the term for rescue matching
        # Use a generous window to capture multi-word rescue phrases
        rescue_window_size = 50
        local_start = max(0, start - rescue_window_size)
        local_end = min(len(text_lower), end + rescue_window_size)
        local_text = text_lower[local_start:local_end]

        # Also check hyphen-normalized version (for "access-token" -> "access token")
        local_text_normalized = local_text.replace("-", " ")

        for phrase in rescue_list:
            pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
            if pattern.search(local_text) or pattern.search(local_text_normalized):
                return True

        return False
