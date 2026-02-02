"""
Tests for PolicyLoader (Phase 0A Stage 2).

Tests cover:
- PolicySpec and PolicyBundle dataclasses
- resolve_policy_dir() with explicit path, env vars, and discovery
- load_policy_bundle() with permissive and strict modes
- All 11 bug hazards from the plan
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from utils.policy_loader import (
    DEFAULT_V2_SPECS,
    PolicyBundle,
    PolicySpec,
    load_policy_bundle,
    resolve_policy_dir,
)


class TestPolicySpec:
    """Tests for PolicySpec dataclass."""

    def test_policy_spec_creation(self):
        """PolicySpec can be created with name, filename, required."""
        spec = PolicySpec(
            name="test_policy",
            filename="test_policy.yaml",
            required=True,
        )
        assert spec.name == "test_policy"
        assert spec.filename == "test_policy.yaml"
        assert spec.required is True

    def test_policy_spec_is_frozen(self):
        """PolicySpec is immutable (frozen=True)."""
        spec = PolicySpec(
            name="test_policy",
            filename="test_policy.yaml",
            required=True,
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            spec.name = "modified"  # type: ignore

    def test_policy_spec_hashable(self):
        """PolicySpec is hashable (can be used in sets/dicts)."""
        spec1 = PolicySpec(name="a", filename="a.yaml", required=True)
        spec2 = PolicySpec(name="a", filename="a.yaml", required=True)
        assert hash(spec1) == hash(spec2)
        assert {spec1, spec2} == {spec1}


class TestPolicyBundle:
    """Tests for PolicyBundle dataclass."""

    def test_policy_bundle_creation(self):
        """PolicyBundle can be created with base_dir."""
        bundle = PolicyBundle(base_dir=Path("/tmp"))
        assert bundle.base_dir == Path("/tmp")
        assert bundle.policies == {}
        assert bundle.missing_required == set()
        assert bundle.load_errors == {}
        assert bundle.loaded_at is None

    def test_policy_bundle_mutable(self):
        """PolicyBundle fields are mutable for aggregation."""
        bundle = PolicyBundle(base_dir=Path("/tmp"))
        bundle.policies["test"] = {"key": "value"}
        bundle.missing_required.add("missing_one")
        bundle.load_errors["error_one"] = "Failed"
        assert bundle.policies == {"test": {"key": "value"}}
        assert bundle.missing_required == {"missing_one"}
        assert bundle.load_errors == {"error_one": "Failed"}


class TestDefaultSpecs:
    """Tests for DEFAULT_V2_SPECS constant."""

    def test_default_specs_is_tuple(self):
        """DEFAULT_V2_SPECS is a tuple (immutable)."""
        assert isinstance(DEFAULT_V2_SPECS, tuple)

    def test_default_specs_contains_negative_keyword_policy(self):
        """DEFAULT_V2_SPECS includes negative_keyword_policy as required."""
        names = [spec.name for spec in DEFAULT_V2_SPECS]
        assert "negative_keyword_policy" in names

        spec = next(s for s in DEFAULT_V2_SPECS if s.name == "negative_keyword_policy")
        assert spec.filename == "negative_keyword_policy.yaml"
        assert spec.required is True


class TestResolvePolicyDir:
    """Tests for resolve_policy_dir() function."""

    def test_explicit_path_valid_directory(self, tmp_path):
        """Explicit path to valid directory returns that path."""
        result = resolve_policy_dir(explicit=str(tmp_path))
        assert result == tmp_path

    def test_explicit_path_not_exist_raises(self, tmp_path):
        """Explicit path that doesn't exist raises FileNotFoundError."""
        nonexistent = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_policy_dir(explicit=str(nonexistent))
        assert "does not exist" in str(exc_info.value)
        assert "explicit" in str(exc_info.value).lower()

    def test_explicit_path_not_directory_raises(self, tmp_path):
        """Explicit path to a file (not dir) raises NotADirectoryError."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")
        with pytest.raises(NotADirectoryError) as exc_info:
            resolve_policy_dir(explicit=str(file_path))
        assert "is not a directory" in str(exc_info.value)

    def test_env_v2_policy_dir(self, tmp_path, monkeypatch):
        """V2_POLICY_DIR env var takes precedence over discovery."""
        monkeypatch.setenv("V2_POLICY_DIR", str(tmp_path))
        result = resolve_policy_dir()
        assert result == tmp_path

    def test_env_v2_policy_dir_not_exist_raises(self, tmp_path, monkeypatch):
        """V2_POLICY_DIR pointing to nonexistent dir raises."""
        nonexistent = tmp_path / "nonexistent"
        monkeypatch.setenv("V2_POLICY_DIR", str(nonexistent))
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_policy_dir()
        assert "V2_POLICY_DIR" in str(exc_info.value)

    def test_env_harmonic_policy_dir(self, tmp_path, monkeypatch):
        """HARMONIC_POLICY_DIR env var is fallback after V2_POLICY_DIR."""
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.setenv("HARMONIC_POLICY_DIR", str(tmp_path))
        result = resolve_policy_dir()
        assert result == tmp_path

    def test_env_harmonic_policy_dir_not_directory_raises(self, tmp_path, monkeypatch):
        """HARMONIC_POLICY_DIR pointing to file raises NotADirectoryError."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.setenv("HARMONIC_POLICY_DIR", str(file_path))
        with pytest.raises(NotADirectoryError) as exc_info:
            resolve_policy_dir()
        assert "HARMONIC_POLICY_DIR" in str(exc_info.value)

    def test_discovery_prefers_v2_subdir(self, tmp_path, monkeypatch):
        """Discovery prefers config/v2 over config if marker exists in v2."""
        # Clear env vars
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.delenv("HARMONIC_POLICY_DIR", raising=False)

        # Create structure with marker in v2
        config_dir = tmp_path / "config"
        v2_dir = config_dir / "v2"
        v2_dir.mkdir(parents=True)
        (v2_dir / "negative_keyword_policy.yaml").write_text("version: 2.0")

        result = resolve_policy_dir(search_root=tmp_path)
        assert result == v2_dir

    def test_discovery_falls_back_to_config(self, tmp_path, monkeypatch):
        """Discovery uses config/ if marker only exists there."""
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.delenv("HARMONIC_POLICY_DIR", raising=False)

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "negative_keyword_policy.yaml").write_text("version: 1.0")

        result = resolve_policy_dir(search_root=tmp_path)
        assert result == config_dir

    def test_discovery_no_marker_raises(self, tmp_path, monkeypatch):
        """Discovery with no marker file raises FileNotFoundError."""
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.delenv("HARMONIC_POLICY_DIR", raising=False)

        # Create config dirs but no marker
        (tmp_path / "config" / "v2").mkdir(parents=True)

        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_policy_dir(search_root=tmp_path)
        assert "marker" in str(exc_info.value).lower()


class TestLoadPolicyBundle:
    """Tests for load_policy_bundle() function."""

    def test_load_single_required_policy_success(self, tmp_path):
        """Loading a valid required policy succeeds."""
        policy_content = {"version": "2.0", "rules": ["rule1"]}
        (tmp_path / "test.yaml").write_text(yaml.dump(policy_content))

        specs = [PolicySpec(name="test", filename="test.yaml", required=True)]
        bundle = load_policy_bundle(tmp_path, specs, "permissive")

        assert "test" in bundle.policies
        assert bundle.policies["test"] == policy_content
        assert len(bundle.missing_required) == 0
        assert len(bundle.load_errors) == 0
        assert bundle.loaded_at is not None

    def test_load_optional_policy_missing(self, tmp_path):
        """Missing optional policy is not an error."""
        specs = [PolicySpec(name="optional", filename="optional.yaml", required=False)]
        bundle = load_policy_bundle(tmp_path, specs, "permissive")

        assert "optional" not in bundle.policies
        assert len(bundle.missing_required) == 0
        assert len(bundle.load_errors) == 0

    def test_load_required_policy_missing_tracked(self, tmp_path):
        """Missing required policy is tracked in missing_required."""
        specs = [PolicySpec(name="required", filename="required.yaml", required=True)]
        bundle = load_policy_bundle(tmp_path, specs, "permissive")

        assert "required" not in bundle.policies
        assert "required" in bundle.missing_required
        assert len(bundle.load_errors) == 0

    def test_yaml_parse_error_tracked(self, tmp_path):
        """YAML parse error is tracked in load_errors, not raised."""
        (tmp_path / "bad.yaml").write_text("invalid: yaml: content: [")

        specs = [PolicySpec(name="bad", filename="bad.yaml", required=True)]
        bundle = load_policy_bundle(tmp_path, specs, "permissive")

        assert "bad" not in bundle.policies
        assert "bad" in bundle.load_errors
        assert "bad" not in bundle.missing_required  # Not missing, just errored

    def test_yaml_not_mapping_is_error(self, tmp_path):
        """YAML that parses to non-dict is tracked as error."""
        (tmp_path / "list.yaml").write_text("- item1\n- item2")

        specs = [PolicySpec(name="list", filename="list.yaml", required=True)]
        bundle = load_policy_bundle(tmp_path, specs, "permissive")

        assert "list" not in bundle.policies
        assert "list" in bundle.load_errors
        assert "mapping" in bundle.load_errors["list"].lower() or "dict" in bundle.load_errors["list"].lower()

    def test_strict_mode_raises_on_missing_required(self, tmp_path):
        """Strict mode raises after all specs processed if required missing."""
        specs = [
            PolicySpec(name="required1", filename="required1.yaml", required=True),
            PolicySpec(name="required2", filename="required2.yaml", required=True),
        ]

        with pytest.raises(RuntimeError) as exc_info:
            load_policy_bundle(tmp_path, specs, "strict")

        # Both missing should be in message
        assert "required1" in str(exc_info.value)
        assert "required2" in str(exc_info.value)

    def test_strict_mode_raises_on_load_errors(self, tmp_path):
        """Strict mode raises if there are load errors on required policies."""
        (tmp_path / "bad.yaml").write_text("invalid: yaml: [")

        specs = [PolicySpec(name="bad", filename="bad.yaml", required=True)]

        with pytest.raises(RuntimeError) as exc_info:
            load_policy_bundle(tmp_path, specs, "strict")

        assert "bad" in str(exc_info.value)

    def test_strict_mode_aggregates_all_errors(self, tmp_path):
        """Strict mode collects all errors before raising (Bug #7)."""
        (tmp_path / "bad.yaml").write_text("invalid: [")

        specs = [
            PolicySpec(name="missing", filename="missing.yaml", required=True),
            PolicySpec(name="bad", filename="bad.yaml", required=True),
        ]

        with pytest.raises(RuntimeError) as exc_info:
            load_policy_bundle(tmp_path, specs, "strict")

        # Both should be mentioned
        assert "missing" in str(exc_info.value)
        assert "bad" in str(exc_info.value)

    def test_generator_exhaustion_prevented(self, tmp_path):
        """Specs as generator doesn't cause issues (Bug #11)."""
        (tmp_path / "a.yaml").write_text("key: value")

        def gen_specs():
            yield PolicySpec(name="a", filename="a.yaml", required=True)

        bundle = load_policy_bundle(tmp_path, gen_specs(), "permissive")
        assert "a" in bundle.policies

    def test_multiple_policies_all_loaded(self, tmp_path):
        """Multiple policies are all loaded correctly."""
        (tmp_path / "a.yaml").write_text(yaml.dump({"a": 1}))
        (tmp_path / "b.yaml").write_text(yaml.dump({"b": 2}))

        specs = [
            PolicySpec(name="a", filename="a.yaml", required=True),
            PolicySpec(name="b", filename="b.yaml", required=False),
        ]

        bundle = load_policy_bundle(tmp_path, specs, "permissive")
        assert bundle.policies["a"] == {"a": 1}
        assert bundle.policies["b"] == {"b": 2}

    def test_loaded_at_timestamp_set(self, tmp_path):
        """Bundle has loaded_at timestamp after loading."""
        (tmp_path / "test.yaml").write_text("key: value")
        specs = [PolicySpec(name="test", filename="test.yaml", required=True)]

        bundle = load_policy_bundle(tmp_path, specs, "permissive")
        assert bundle.loaded_at is not None

    def test_permissive_mode_no_raise(self, tmp_path):
        """Permissive mode returns bundle even with errors."""
        specs = [PolicySpec(name="missing", filename="missing.yaml", required=True)]
        bundle = load_policy_bundle(tmp_path, specs, "permissive")

        # Should not raise, should return bundle with missing_required
        assert "missing" in bundle.missing_required


class TestBugHazardMitigations:
    """Tests specifically validating bug hazard mitigations."""

    def test_bug1_permissive_yaml_parse_no_crash(self, tmp_path):
        """Bug #1: Permissive mode doesn't crash on YAML errors."""
        (tmp_path / "bad.yaml").write_text("invalid: yaml: [")
        specs = [PolicySpec(name="bad", filename="bad.yaml", required=True)]

        # Should NOT raise
        bundle = load_policy_bundle(tmp_path, specs, "permissive")
        assert "bad" in bundle.load_errors

    def test_bug2_missing_vs_parse_error_separate(self, tmp_path):
        """Bug #2: Missing files vs parse errors are tracked separately."""
        (tmp_path / "parse_error.yaml").write_text("bad: [")

        specs = [
            PolicySpec(name="missing", filename="missing.yaml", required=True),
            PolicySpec(name="parse_error", filename="parse_error.yaml", required=True),
        ]

        bundle = load_policy_bundle(tmp_path, specs, "permissive")

        # Missing file → missing_required
        assert "missing" in bundle.missing_required

        # Parse error → load_errors (NOT missing_required)
        assert "parse_error" in bundle.load_errors
        assert "parse_error" not in bundle.missing_required

    def test_bug7_strict_no_early_exit(self, tmp_path):
        """Bug #7: Strict mode processes all specs before raising."""
        specs = [
            PolicySpec(name="a", filename="a.yaml", required=True),
            PolicySpec(name="b", filename="b.yaml", required=True),
            PolicySpec(name="c", filename="c.yaml", required=True),
        ]

        with pytest.raises(RuntimeError) as exc_info:
            load_policy_bundle(tmp_path, specs, "strict")

        # All three should be reported
        msg = str(exc_info.value)
        assert "a" in msg
        assert "b" in msg
        assert "c" in msg

    def test_bug8_explicit_path_validated_before_io(self, tmp_path):
        """Bug #8: Explicit path validated before any I/O attempt."""
        nonexistent = tmp_path / "does_not_exist"

        with pytest.raises(FileNotFoundError):
            resolve_policy_dir(explicit=str(nonexistent))

    def test_bug9_no_silent_fallback(self, tmp_path, monkeypatch):
        """Bug #9: No silent fallback when marker not found."""
        monkeypatch.delenv("V2_POLICY_DIR", raising=False)
        monkeypatch.delenv("HARMONIC_POLICY_DIR", raising=False)

        # Create empty config dirs
        (tmp_path / "config" / "v2").mkdir(parents=True)

        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_policy_dir(search_root=tmp_path)

        assert "marker" in str(exc_info.value).lower()

    def test_bug10_env_paths_validated_symmetrically(self, tmp_path, monkeypatch):
        """Bug #10: Both V2_POLICY_DIR and HARMONIC_POLICY_DIR validated same way."""
        nonexistent = tmp_path / "nonexistent"

        # V2_POLICY_DIR
        monkeypatch.setenv("V2_POLICY_DIR", str(nonexistent))
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_policy_dir()
        assert "V2_POLICY_DIR" in str(exc_info.value)

        # HARMONIC_POLICY_DIR
        monkeypatch.delenv("V2_POLICY_DIR")
        monkeypatch.setenv("HARMONIC_POLICY_DIR", str(nonexistent))
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_policy_dir()
        assert "HARMONIC_POLICY_DIR" in str(exc_info.value)

    def test_bug11_generator_specs_not_exhausted(self, tmp_path):
        """Bug #11: Generator specs are converted to tuple at entry."""
        (tmp_path / "policy.yaml").write_text("key: value")

        # Create a generator that would be exhausted if iterated twice
        def specs_gen():
            yield PolicySpec(name="policy", filename="policy.yaml", required=True)

        bundle = load_policy_bundle(tmp_path, specs_gen(), "permissive")
        assert "policy" in bundle.policies
