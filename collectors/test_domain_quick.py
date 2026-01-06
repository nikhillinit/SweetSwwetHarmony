"""
Quick test for Domain/WHOIS collector - validates core functionality
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone, timedelta
from collectors.domain_whois import DomainRegistration, TECH_TLDS

def test_domain_registration():
    """Test DomainRegistration dataclass"""

    # Create a fresh registration (10 days old)
    ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)

    reg = DomainRegistration(
        domain="startup.ai",
        tld="ai",
        registration_date=ten_days_ago,
        registrar="Cloudflare, Inc.",
        nameservers=["ns1.cloudflare.com", "ns2.cloudflare.com"],
        status=["ok"],
    )

    print("=" * 60)
    print("DOMAIN REGISTRATION TEST")
    print("=" * 60)
    print(f"Domain: {reg.domain}")
    print(f"TLD: {reg.tld}")
    print(f"Age: {reg.age_days} days (expected: ~10)")
    print(f"Is recently registered: {reg.is_recently_registered} (expected: True)")
    print(f"Is tech TLD: {reg.is_tech_tld} (expected: True)")
    print(f"Has premium registrar: {reg.has_premium_registrar} (expected: True)")
    print(f"Is active: {reg.is_active} (expected: True)")
    print(f"Signal score: {reg.calculate_signal_score():.2f} (expected: ~0.7)")

    # Assertions
    assert 9 <= reg.age_days <= 11, f"Age calculation failed: {reg.age_days}"
    assert reg.is_recently_registered, "Should be recently registered"
    assert reg.is_tech_tld, "Should be tech TLD"
    assert reg.has_premium_registrar, "Should have premium registrar"
    assert reg.is_active, "Should be active"
    assert 0.6 <= reg.calculate_signal_score() <= 0.8, f"Score out of range: {reg.calculate_signal_score()}"

    print("\n[OK] All basic tests passed!")

    # Test signal conversion
    signal = reg.to_signal()

    print("\n" + "=" * 60)
    print("SIGNAL CONVERSION TEST")
    print("=" * 60)
    print(f"Signal ID: {signal.id}")
    print(f"Signal type: {signal.signal_type}")
    print(f"Confidence: {signal.confidence:.2f}")
    print(f"Source API: {signal.source_api}")
    print(f"Canonical key: {signal.raw_data.get('canonical_key')}")

    assert signal.signal_type == "domain_registration"
    assert signal.source_api == "rdap"
    assert signal.confidence > 0
    assert "domain" in signal.raw_data
    assert signal.raw_data["canonical_key"] == "domain:startup.ai"

    print("\n[OK] Signal conversion tests passed!")

    # Test scoring variations
    print("\n" + "=" * 60)
    print("SCORING VARIATIONS TEST")
    print("=" * 60)

    # Very fresh domain
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    fresh_reg = DomainRegistration(
        domain="fresh.ai",
        tld="ai",
        registration_date=three_days_ago,
        registrar="Google Domains",
        status=["ok"],
    )
    fresh_score = fresh_reg.calculate_signal_score()
    print(f"Very fresh domain (3 days): {fresh_score:.2f} (expected: ~0.9)")
    assert fresh_score >= 0.8, "Fresh domain should score high"

    # Old domain
    old = datetime.now(timezone.utc) - timedelta(days=365)
    old_reg = DomainRegistration(
        domain="old.com",
        tld="com",
        registration_date=old,
        status=["ok"],
    )
    old_score = old_reg.calculate_signal_score()
    print(f"Old domain (365 days): {old_score:.2f} (expected: ~0.2)")
    assert old_score <= 0.3, "Old domain should score low"

    # Inactive domain
    inactive_reg = DomainRegistration(
        domain="inactive.com",
        tld="com",
        status=["pending delete"],
    )
    inactive_score = inactive_reg.calculate_signal_score()
    print(f"Inactive domain: {inactive_score:.2f} (expected: 0.0)")
    assert inactive_score == 0.0, "Inactive domain should score 0"

    print("\n[OK] All scoring tests passed!")

    # Test tech TLDs
    print("\n" + "=" * 60)
    print("TECH TLD TEST")
    print("=" * 60)
    print(f"Total tech TLDs: {len(TECH_TLDS)}")
    print(f"Tech TLDs: {sorted(TECH_TLDS)}")

    tech_examples = [".ai", ".io", ".tech", ".dev", ".ml"]
    for tld in tech_examples:
        assert tld in TECH_TLDS, f"{tld} should be in TECH_TLDS"

    print("\n[OK] Tech TLD tests passed!")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    test_domain_registration()
