"""Tests for Web3 co-occurrence detector.

Covers all test cases from Phase 2 plan Task 2.5:
- Unambiguous crypto terms (immediate REJECT)
- Rescue phrase neutralization (local, not global)
- Co-occurrence detection (ambiguous + crypto context)
- Hyphenated rescue phrases
- Unambiguous overrides rescue
- Window boundary behavior
- Defensive guards (empty/None input)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from utils.web3_detector import Web3Detector, Web3DetectionResult


@pytest.fixture
def detector():
    return Web3Detector()


class TestUnambiguousCrypto:
    """Unambiguous crypto terms -> immediate REJECT."""

    def test_blockchain_startup(self, detector):
        result = detector.detect("blockchain startup")
        assert result.is_crypto is True
        assert result.matched_term == "blockchain"

    def test_cryptocurrency_mention(self, detector):
        result = detector.detect("a new cryptocurrency exchange")
        assert result.is_crypto is True

    def test_bitcoin(self, detector):
        result = detector.detect("mining Bitcoin for profit")
        assert result.is_crypto is True
        assert result.matched_term == "bitcoin"

    def test_ethereum(self, detector):
        result = detector.detect("built on ethereum network")
        assert result.is_crypto is True

    def test_nft(self, detector):
        result = detector.detect("selling nft art online")
        assert result.is_crypto is True

    def test_defi(self, detector):
        result = detector.detect("DeFi lending protocol")
        assert result.is_crypto is True

    def test_smart_contract(self, detector):
        result = detector.detect("deploy smart contract on chain")
        assert result.is_crypto is True

    def test_web3_marketplace(self, detector):
        """web3 is unambiguous per thesis exclusion."""
        result = detector.detect("web3 marketplace for food delivery")
        assert result.is_crypto is True
        assert result.matched_term == "web3"

    def test_web3_word_boundary(self, detector):
        """'web 3.0' != 'web3', should not trigger."""
        result = detector.detect("web 3.0 era consumer app")
        assert result.is_crypto is False

    def test_play_to_earn(self, detector):
        result = detector.detect("a play to earn gaming platform")
        assert result.is_crypto is True

    def test_yield_farming(self, detector):
        result = detector.detect("yield farming opportunities")
        assert result.is_crypto is True

    def test_staking(self, detector):
        result = detector.detect("staking rewards for holders")
        assert result.is_crypto is True


class TestRescuePhrases:
    """Rescue phrases neutralize specific ambiguous occurrences."""

    def test_access_tokens(self, detector):
        result = detector.detect("OAuth access tokens for API authentication")
        assert result.is_crypto is False

    def test_access_token_singular(self, detector):
        result = detector.detect("refresh the access token before expiry")
        assert result.is_crypto is False

    def test_bearer_token(self, detector):
        result = detector.detect("use a bearer token for auth")
        assert result.is_crypto is False

    def test_session_token(self, detector):
        result = detector.detect("session token stored in cookie")
        assert result.is_crypto is False

    def test_api_token(self, detector):
        result = detector.detect("generate an api token for the developer")
        assert result.is_crypto is False

    def test_loyalty_tokens(self, detector):
        result = detector.detect("loyalty tokens for customers")
        assert result.is_crypto is False

    def test_loyalty_token_singular(self, detector):
        result = detector.detect("earn a loyalty token with each purchase")
        assert result.is_crypto is False

    def test_tokenization(self, detector):
        """'tokenization' contains 'token' but rescue phrase handles it."""
        result = detector.detect("payment tokenization for PCI compliance")
        assert result.is_crypto is False

    def test_dao_pattern(self, detector):
        result = detector.detect("DAO pattern in code architecture")
        assert result.is_crypto is False

    def test_data_access_object(self, detector):
        result = detector.detect("implemented a data access object for the database")
        assert result.is_crypto is False

    def test_data_mining(self, detector):
        result = detector.detect("data mining for consumer insights")
        assert result.is_crypto is False

    def test_process_mining(self, detector):
        result = detector.detect("process mining to optimize workflows")
        assert result.is_crypto is False

    def test_text_mining(self, detector):
        result = detector.detect("text mining from customer reviews")
        assert result.is_crypto is False

    def test_digital_wallet(self, detector):
        result = detector.detect("digital wallet for payments")
        assert result.is_crypto is False

    def test_mobile_wallet(self, detector):
        result = detector.detect("mobile wallet app for groceries")
        assert result.is_crypto is False

    def test_ewallet(self, detector):
        result = detector.detect("ewallet platform for small businesses")
        assert result.is_crypto is False

    def test_hyphenated_access_token(self, detector):
        """access-token (hyphenated) should be rescued."""
        result = detector.detect("pass the access-token in the header")
        assert result.is_crypto is False

    def test_hyphenated_e_wallet(self, detector):
        """e-wallet should be rescued."""
        result = detector.detect("an e-wallet for daily purchases")
        assert result.is_crypto is False


class TestCooccurrence:
    """Ambiguous terms near crypto context -> REJECT."""

    def test_dao_governance_token(self, detector):
        result = detector.detect("DAO governance token for community voting")
        assert result.is_crypto is True

    def test_token_on_ethereum(self, detector):
        result = detector.detect("token on ethereum blockchain")
        assert result.is_crypto is True

    def test_decentralized_token_exchange(self, detector):
        result = detector.detect("decentralized token exchange platform")
        assert result.is_crypto is True

    def test_crypto_wallet(self, detector):
        """'crypto' is unambiguous, so this is REJECT regardless."""
        result = detector.detect("crypto wallet for storing coins")
        assert result.is_crypto is True

    def test_mining_with_blockchain(self, detector):
        result = detector.detect("mining operations using blockchain technology")
        assert result.is_crypto is True

    def test_metaverse_with_nft(self, detector):
        """'nft' is unambiguous, immediate REJECT."""
        result = detector.detect("metaverse world with nft marketplace")
        assert result.is_crypto is True

    def test_wallet_with_defi(self, detector):
        """'defi' is unambiguous, immediate REJECT."""
        result = detector.detect("wallet integration for defi protocols")
        assert result.is_crypto is True


class TestUnambiguousOverridesRescue:
    """Unambiguous terms override rescue phrases."""

    def test_bitcoin_access_token(self, detector):
        """'bitcoin' is unambiguous, overrides 'access token' rescue."""
        result = detector.detect("bitcoin access token for exchange")
        assert result.is_crypto is True
        assert result.matched_term == "bitcoin"

    def test_access_token_for_ethereum(self, detector):
        """'ethereum' is unambiguous, overrides 'access token' rescue."""
        result = detector.detect("access token for ethereum wallet API")
        assert result.is_crypto is True
        assert result.matched_term == "ethereum"

    def test_blockchain_cryptography_library(self, detector):
        """'blockchain' is unambiguous, overrides everything."""
        result = detector.detect("blockchain cryptography library for developers")
        assert result.is_crypto is True
        assert result.matched_term == "blockchain"


class TestNoFalsePositives:
    """Common startup descriptions that should NOT trigger."""

    def test_consumer_app_no_crypto(self, detector):
        result = detector.detect("A mobile app for healthy meal planning and grocery delivery")
        assert result.is_crypto is False

    def test_saas_platform(self, detector):
        result = detector.detect("B2B SaaS platform for enterprise resource planning")
        assert result.is_crypto is False

    def test_fintech_no_crypto(self, detector):
        result = detector.detect("Personal budgeting app with bank account integration")
        assert result.is_crypto is False

    def test_gaming_no_crypto(self, detector):
        result = detector.detect("Mobile gaming platform with in-app purchases")
        assert result.is_crypto is False

    def test_metaverse_no_crypto_context(self, detector):
        """'metaverse' alone without crypto context -> PASS."""
        result = detector.detect("metaverse experiences for virtual tourism")
        assert result.is_crypto is False

    def test_proof_of_concept(self, detector):
        """'proof of concept' should NOT be crypto context."""
        result = detector.detect("proof of concept token launch for loyalty program")
        assert result.is_crypto is False

    def test_mining_insights_standalone(self, detector):
        result = detector.detect("mining insights from user behavior data")
        assert result.is_crypto is False


class TestWindowBoundary:
    """Co-occurrence window boundary tests."""

    def test_term_at_start_of_text(self, detector):
        """Ambiguous term at very start of text."""
        result = detector.detect("token")
        assert result.is_crypto is False

    def test_term_at_end_of_text(self, detector):
        result = detector.detect("a simple token")
        assert result.is_crypto is False

    def test_context_outside_window(self, detector):
        """Crypto context far beyond the window should not trigger."""
        padding = "x " * 200  # >200 chars of padding
        text = f"token {padding} decentralized"
        result = detector.detect(text)
        assert result.is_crypto is False

    def test_context_inside_window(self, detector):
        """Crypto context within the window should trigger."""
        padding = "x " * 20  # ~40 chars of padding
        text = f"token {padding} blockchain"
        # "blockchain" is unambiguous, so this will REJECT at step 1
        # Use a context-only term instead
        text = f"token {padding} decentralized"
        result = detector.detect(text)
        assert result.is_crypto is True


class TestDefensiveGuards:
    """Defensive input handling."""

    def test_empty_string(self, detector):
        result = detector.detect("")
        assert result.is_crypto is False

    def test_none_input(self, detector):
        result = detector.detect(None)
        assert result.is_crypto is False

    def test_whitespace_only(self, detector):
        result = detector.detect("   \n\t  ")
        assert result.is_crypto is False


class TestResultDetails:
    """Verify result metadata is populated correctly."""

    def test_unambiguous_has_reason(self, detector):
        result = detector.detect("blockchain company")
        assert "Unambiguous" in result.reason
        assert result.matched_term == "blockchain"
        assert len(result.details) > 0

    def test_cooccurrence_has_reason(self, detector):
        result = detector.detect("decentralized token exchange")
        assert "co-occurs" in result.reason
        assert result.matched_term == "token"
        assert len(result.details) == 2

    def test_pass_has_no_reason(self, detector):
        result = detector.detect("healthy food delivery app")
        assert result.reason is None
        assert result.matched_term is None
        assert result.details == []
