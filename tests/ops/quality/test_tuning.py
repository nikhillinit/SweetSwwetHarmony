"""Tier 3 Lower Risk -- Tuning proposal generation and application tests.

Verifies that generate_tuning_proposal() produces correct proposals from
detected patterns, and that apply_tuning_proposal() correctly applies or
skips actions in both dry_run and live modes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from ops.quality.tuning import apply_tuning_proposal, generate_tuning_proposal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy_file(tmp_path: Path, keywords: dict | None = None) -> Path:
    """Create a minimal negative_keyword_policy YAML and return its path."""
    policy = {"negative_keywords": keywords or {}}
    p = tmp_path / "negative_keyword_policy.yaml"
    p.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    return p


def _make_proposal_file(tmp_path: Path, actions: list, notes: list | None = None) -> Path:
    """Write a proposal YAML file and return its path."""
    proposal = {
        "version": 1,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "window_days": 30,
        "actions": actions,
        "notes": notes or [],
    }
    p = tmp_path / "proposal.yaml"
    p.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# generate_tuning_proposal tests
# ---------------------------------------------------------------------------

class TestGenerateProposal:
    """Tests for generate_tuning_proposal()."""

    def test_generate_proposal_empty_patterns(self, tmp_path):
        """Empty patterns list should produce a proposal with no actions and no notes."""
        policy_path = _make_policy_file(tmp_path)
        out_path = tmp_path / "out_proposal.yaml"

        result = generate_tuning_proposal(
            patterns=[],
            window_days=30,
            out_path=str(out_path),
            negative_policy_path=str(policy_path),
        )

        assert result["actions"] == []
        assert result["notes"] == []
        assert result["version"] == 1

    def test_generate_proposal_with_duplicate_fp_description(self, tmp_path):
        """Pattern with type duplicate_fp_description containing 'enterprise' should
        propose add_negative_keyword_v2 action for the 'enterprise' keyword."""
        policy_path = _make_policy_file(tmp_path)
        out_path = tmp_path / "out_proposal.yaml"

        patterns = [
            {
                "type": "duplicate_fp_description",
                "normalized_description": "enterprise solutions for b2b clients",
                "count": 15,
                "example_signal_ids": [1, 2, 3],
            }
        ]

        result = generate_tuning_proposal(
            patterns=patterns,
            window_days=30,
            out_path=str(out_path),
            negative_policy_path=str(policy_path),
        )

        # Should have actions for both 'enterprise' and 'b2b' (both are known candidates).
        action_keywords = [a["keyword"] for a in result["actions"]]
        assert "enterprise" in action_keywords

        # Verify the enterprise action structure.
        enterprise_action = next(a for a in result["actions"] if a["keyword"] == "enterprise")
        assert enterprise_action["type"] == "add_negative_keyword_v2"
        assert enterprise_action["category"] == "B2B_ENTERPRISE"
        assert enterprise_action["weight"] == 0.5
        assert enterprise_action["requires_review"] is True

    def test_generate_proposal_writes_yaml(self, tmp_path):
        """out_path file should be created and loadable by yaml.safe_load."""
        policy_path = _make_policy_file(tmp_path)
        out_path = tmp_path / "written_proposal.yaml"

        generate_tuning_proposal(
            patterns=[],
            window_days=14,
            out_path=str(out_path),
            negative_policy_path=str(policy_path),
        )

        assert out_path.exists()
        loaded = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict)
        assert "actions" in loaded
        assert "notes" in loaded
        assert loaded["window_days"] == 14

    def test_generate_proposal_existing_keyword_not_duplicated(self, tmp_path):
        """If policy already contains 'enterprise', no duplicate action should be proposed."""
        policy_path = _make_policy_file(
            tmp_path,
            keywords={"enterprise": {"weight": 0.5, "category": "B2B_ENTERPRISE"}},
        )
        out_path = tmp_path / "out_proposal.yaml"

        patterns = [
            {
                "type": "duplicate_fp_description",
                "normalized_description": "enterprise solutions for saas platforms",
                "count": 10,
            }
        ]

        result = generate_tuning_proposal(
            patterns=patterns,
            window_days=30,
            out_path=str(out_path),
            negative_policy_path=str(policy_path),
        )

        action_keywords = [a["keyword"] for a in result["actions"]]
        assert "enterprise" not in action_keywords

    def test_generate_proposal_notes_for_non_actionable_patterns(self, tmp_path):
        """source_api_fp_rate pattern should be added to notes, not actions."""
        policy_path = _make_policy_file(tmp_path)
        out_path = tmp_path / "out_proposal.yaml"

        patterns = [
            {
                "type": "source_api_fp_rate",
                "source_api": "github",
                "fp_rate": 0.45,
            }
        ]

        result = generate_tuning_proposal(
            patterns=patterns,
            window_days=30,
            out_path=str(out_path),
            negative_policy_path=str(policy_path),
        )

        assert result["actions"] == []
        assert len(result["notes"]) >= 1
        assert "High FP rate pattern" in result["notes"][0]


# ---------------------------------------------------------------------------
# apply_tuning_proposal tests
# ---------------------------------------------------------------------------

class TestApplyProposal:
    """Tests for apply_tuning_proposal()."""

    def test_apply_tuning_dry_run(self, tmp_path):
        """dry_run=True should not modify the original policy file."""
        policy_path = _make_policy_file(tmp_path)
        original_content = policy_path.read_text(encoding="utf-8")

        proposal_path = _make_proposal_file(
            tmp_path,
            actions=[
                {
                    "id": "abc123",
                    "type": "add_negative_keyword_v2",
                    "file": str(policy_path),
                    "keyword": "blockchain",
                    "weight": 0.5,
                    "category": "CRYPTO_WEB3",
                }
            ],
        )

        validation = MagicMock()
        validation.valid = True
        validation.errors = []
        validation.warnings = []

        with patch(
            "ops.quality.tuning.validate_negative_keyword_policy",
            return_value=validation,
        ):
            result = apply_tuning_proposal(
                proposal_path=str(proposal_path),
                repo_root=str(tmp_path),
                dry_run=True,
            )

        assert result["dry_run"] is True
        assert len(result["applied"]) == 1
        assert result["applied"][0]["keyword"] == "blockchain"
        assert result["applied"][0]["dry_run"] is True

        # Policy file should remain unchanged.
        assert policy_path.read_text(encoding="utf-8") == original_content

    def test_apply_tuning_live(self, tmp_path):
        """dry_run=False should update the policy file with the new keyword."""
        policy_path = _make_policy_file(tmp_path)

        proposal_path = _make_proposal_file(
            tmp_path,
            actions=[
                {
                    "id": "def456",
                    "type": "add_negative_keyword_v2",
                    "file": str(policy_path),
                    "keyword": "crypto",
                    "weight": 0.5,
                    "category": "CRYPTO_WEB3",
                }
            ],
        )

        validation = MagicMock()
        validation.valid = True
        validation.errors = []
        validation.warnings = []

        with patch(
            "ops.quality.tuning.validate_negative_keyword_policy",
            return_value=validation,
        ):
            result = apply_tuning_proposal(
                proposal_path=str(proposal_path),
                repo_root=str(tmp_path),
                dry_run=False,
            )

        assert result["dry_run"] is False
        assert len(result["applied"]) == 1
        assert result["applied"][0]["keyword"] == "crypto"
        assert result["applied"][0]["dry_run"] is False

        # Policy file should now contain the new keyword.
        updated_policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        assert "crypto" in updated_policy["negative_keywords"]
        entry = updated_policy["negative_keywords"]["crypto"]
        assert entry["weight"] == 0.5
        assert entry["category"] == "CRYPTO_WEB3"

    def test_apply_tuning_unsupported_action_type(self, tmp_path):
        """Action with unknown type should be skipped, not applied."""
        policy_path = _make_policy_file(tmp_path)

        proposal_path = _make_proposal_file(
            tmp_path,
            actions=[
                {
                    "id": "unk789",
                    "type": "unknown_type",
                    "file": str(policy_path),
                    "keyword": "test",
                }
            ],
        )

        result = apply_tuning_proposal(
            proposal_path=str(proposal_path),
            repo_root=str(tmp_path),
            dry_run=True,
        )

        assert len(result["applied"]) == 0
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["reason"] == "unsupported_action_type"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
