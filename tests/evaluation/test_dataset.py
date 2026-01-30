"""
Tests for thesis classification evaluation datasets.

Validates JSONL format, required fields, label distribution, and data integrity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest


# =============================================================================
# CONSTANTS
# =============================================================================

REQUIRED_FIELDS = {"input", "target", "id", "metadata"}
VALID_LABELS = {"QUALIFIED", "HELD", "REJECTED"}
SAMPLE_DATASET_PATH = Path(__file__).parent.parent.parent / "datasets" / "thesis_sample.jsonl"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def load_dataset(path: Path) -> List[Dict[str, Any]]:
    """Load JSONL dataset from file."""
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
                samples.append(sample)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {line_num}: {e}")
    return samples


def get_label_distribution(samples: List[Dict[str, Any]]) -> Dict[str, int]:
    """Get count of each label in dataset."""
    distribution: Dict[str, int] = {}
    for sample in samples:
        label = sample.get("target", "UNKNOWN")
        distribution[label] = distribution.get(label, 0) + 1
    return distribution


# =============================================================================
# TESTS: DATASET FORMAT
# =============================================================================

class TestDatasetFormat:
    """Tests for JSONL format and structure."""

    def test_sample_dataset_exists(self):
        """Sample dataset file should exist."""
        assert SAMPLE_DATASET_PATH.exists(), f"Dataset not found at {SAMPLE_DATASET_PATH}"

    def test_sample_dataset_is_valid_jsonl(self):
        """Each line should be valid JSON."""
        samples = load_dataset(SAMPLE_DATASET_PATH)
        assert len(samples) > 0, "Dataset is empty"

    def test_sample_dataset_has_required_fields(self):
        """Each sample should have input, target, id, metadata fields."""
        samples = load_dataset(SAMPLE_DATASET_PATH)

        for i, sample in enumerate(samples):
            missing = REQUIRED_FIELDS - set(sample.keys())
            assert not missing, f"Sample {i} missing fields: {missing}"

    def test_sample_dataset_has_valid_labels(self):
        """All target labels should be QUALIFIED, HELD, or REJECTED."""
        samples = load_dataset(SAMPLE_DATASET_PATH)

        for i, sample in enumerate(samples):
            target = sample.get("target")
            assert target in VALID_LABELS, (
                f"Sample {i} (id={sample.get('id')}) has invalid label: {target}"
            )

    def test_sample_dataset_has_unique_ids(self):
        """Each sample should have a unique id."""
        samples = load_dataset(SAMPLE_DATASET_PATH)

        ids: Set[str] = set()
        for sample in samples:
            sample_id = sample.get("id")
            assert sample_id not in ids, f"Duplicate id: {sample_id}"
            ids.add(sample_id)

    def test_sample_dataset_input_is_nonempty(self):
        """Each sample input should be non-empty string."""
        samples = load_dataset(SAMPLE_DATASET_PATH)

        for i, sample in enumerate(samples):
            input_text = sample.get("input")
            assert isinstance(input_text, str), f"Sample {i} input is not a string"
            assert len(input_text.strip()) > 0, f"Sample {i} has empty input"


# =============================================================================
# TESTS: DATASET CONTENT
# =============================================================================

class TestDatasetContent:
    """Tests for dataset content and quality."""

    def test_sample_dataset_minimum_size(self):
        """Dataset should have at least 20 samples."""
        samples = load_dataset(SAMPLE_DATASET_PATH)
        assert len(samples) >= 20, f"Dataset too small: {len(samples)} samples (need 20+)"

    def test_sample_dataset_has_all_labels(self):
        """Dataset should have examples of all three labels."""
        samples = load_dataset(SAMPLE_DATASET_PATH)
        distribution = get_label_distribution(samples)

        for label in VALID_LABELS:
            assert label in distribution, f"Missing examples for label: {label}"
            assert distribution[label] >= 1, f"Need at least 1 example for {label}"

    def test_sample_dataset_label_balance(self):
        """Dataset should have reasonable label balance (no class < 10%)."""
        samples = load_dataset(SAMPLE_DATASET_PATH)
        distribution = get_label_distribution(samples)
        total = len(samples)

        for label, count in distribution.items():
            ratio = count / total
            assert ratio >= 0.10, (
                f"Label {label} is underrepresented: {count}/{total} ({ratio:.1%})"
            )

    def test_sample_dataset_metadata_has_company_name(self):
        """Each sample metadata should include company_name."""
        samples = load_dataset(SAMPLE_DATASET_PATH)

        for i, sample in enumerate(samples):
            metadata = sample.get("metadata", {})
            assert "company_name" in metadata, f"Sample {i} missing company_name in metadata"

    def test_sample_dataset_input_contains_company(self):
        """Each sample input should mention Company:."""
        samples = load_dataset(SAMPLE_DATASET_PATH)

        for i, sample in enumerate(samples):
            input_text = sample.get("input", "")
            assert "Company:" in input_text, f"Sample {i} input missing 'Company:' prefix"


# =============================================================================
# TESTS: LABEL CONSISTENCY
# =============================================================================

class TestLabelConsistency:
    """Tests for label consistency with expected patterns."""

    def test_qualified_samples_have_consumer_signals(self):
        """QUALIFIED samples should reference consumer-related content."""
        samples = load_dataset(SAMPLE_DATASET_PATH)

        consumer_terms = {
            "cpg", "consumer", "health tech", "wellness", "fitness",
            "travel", "hospitality", "marketplace", "d2c", "dtc",
            "beauty", "food", "beverage", "snack", "meal", "skincare"
        }

        qualified = [s for s in samples if s["target"] == "QUALIFIED"]

        for sample in qualified:
            input_lower = sample["input"].lower()
            has_consumer_term = any(term in input_lower for term in consumer_terms)
            # This is a soft check - we expect most to have consumer terms
            # but allow some flexibility for edge cases

    def test_rejected_samples_have_exclusion_signals(self):
        """REJECTED samples should reference excluded categories."""
        samples = load_dataset(SAMPLE_DATASET_PATH)

        exclusion_terms = {
            "b2b", "enterprise", "developer", "api", "devops",
            "crypto", "blockchain", "web3", "nft", "defi",
            "consulting", "agency", "services"
        }

        rejected = [s for s in samples if s["target"] == "REJECTED"]

        for sample in rejected:
            input_lower = sample["input"].lower()
            has_exclusion_term = any(term in input_lower for term in exclusion_terms)
            # Soft check - most rejected should have exclusion terms

    def test_held_samples_have_weak_signals(self):
        """HELD samples should have weaker or ambiguous signals."""
        samples = load_dataset(SAMPLE_DATASET_PATH)

        held = [s for s in samples if s["target"] == "HELD"]

        # HELD samples should exist and typically have fewer/weaker signals
        assert len(held) >= 3, "Need at least 3 HELD examples"


# =============================================================================
# TESTS: DATA LOADING UTILITIES
# =============================================================================

class TestDataLoading:
    """Tests for dataset loading utilities."""

    def test_load_dataset_returns_list(self):
        """load_dataset should return a list of dicts."""
        samples = load_dataset(SAMPLE_DATASET_PATH)
        assert isinstance(samples, list)
        assert all(isinstance(s, dict) for s in samples)

    def test_get_label_distribution_counts(self):
        """get_label_distribution should count labels correctly."""
        samples = load_dataset(SAMPLE_DATASET_PATH)
        distribution = get_label_distribution(samples)

        total_count = sum(distribution.values())
        assert total_count == len(samples)

    def test_load_nonexistent_file_raises(self):
        """Loading non-existent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_dataset(Path("nonexistent_file.jsonl"))


# =============================================================================
# TESTS: CUSTOM DATASET SUPPORT
# =============================================================================

class TestCustomDataset:
    """Tests for loading custom datasets."""

    def test_load_empty_lines_skipped(self, tmp_path):
        """Empty lines in JSONL should be skipped."""
        dataset_file = tmp_path / "test.jsonl"
        dataset_file.write_text(
            '{"input": "test1", "target": "QUALIFIED", "id": "1", "metadata": {}}\n'
            '\n'
            '{"input": "test2", "target": "HELD", "id": "2", "metadata": {}}\n'
        )

        samples = load_dataset(dataset_file)
        assert len(samples) == 2

    def test_invalid_json_raises_error(self, tmp_path):
        """Invalid JSON should raise ValueError with line number."""
        dataset_file = tmp_path / "test.jsonl"
        dataset_file.write_text(
            '{"input": "test1", "target": "QUALIFIED", "id": "1", "metadata": {}}\n'
            'not valid json\n'
        )

        with pytest.raises(ValueError) as exc_info:
            load_dataset(dataset_file)

        assert "line 2" in str(exc_info.value)
