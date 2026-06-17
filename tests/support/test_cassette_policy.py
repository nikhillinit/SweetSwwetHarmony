import json
from pathlib import Path

import pytest

from tests.support.cassette_policy import CassettePolicy, StaleCassetteError


def test_fresh_cassette_passes(tmp_path):
    cassette = tmp_path / "github.yaml"
    cassette.write_text("interactions: []")
    fingerprint = tmp_path / "github.fp"
    fingerprint.write_text("abc123")
    policy = CassettePolicy(cassette, fingerprint)
    policy.assert_fresh()


def test_stale_cassette_raises(tmp_path):
    cassette = tmp_path / "github.yaml"
    cassette.write_text("interactions: []")
    meta = tmp_path / "github.yaml.meta.json"
    meta.write_text(json.dumps({"fingerprint": "old123"}))
    fingerprint = tmp_path / "github.fp"
    fingerprint.write_text("new456")
    policy = CassettePolicy(cassette, fingerprint, meta_path=meta)
    with pytest.raises(StaleCassetteError, match="stale"):
        policy.assert_fresh()


def test_cassette_does_not_mask_api_shape_changed(tmp_path):
    cassette = tmp_path / "github.yaml"
    cassette.write_text("""interactions:
- request:
    method: GET
    uri: https://api.github.com/repos/test/repo
  response:
    status: {code: 200}
    body:
      string: '{"name": "repo"}'
""")
    policy = CassettePolicy(cassette, tmp_path / "github.fp")
    assert '"stars_count"' not in cassette.read_text()
    policy.assert_no_synthetic_field_injection()


def test_cassette_storage_is_under_tests_cassettes(tmp_path):
    cassette = tmp_path / "tests" / "cassettes" / "github.yaml"
    cassette.parent.mkdir(parents=True)
    cassette.write_text("interactions: []")
    policy = CassettePolicy(cassette, tmp_path / "github.fp")
    assert "cassettes" in str(policy.cassette_path)
