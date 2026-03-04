"""Lightweight CI checks for company_name_policy YAML/schema artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def test_company_name_policy_schema_is_valid_json():
    schema_path = Path(__file__).resolve().parents[2] / "config" / "company_name_policy.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    assert isinstance(schema, dict)
    assert "$schema" in schema


def test_company_name_policy_yaml_satisfies_schema_required_keys():
    root = Path(__file__).resolve().parents[2]
    schema_path = root / "config" / "company_name_policy.schema.json"
    policy_path = root / "config" / "company_name_policy.yaml"

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    with open(policy_path, "r", encoding="utf-8") as f:
        policy = yaml.safe_load(f)

    required = schema.get("required", [])
    for key in required:
        assert key in policy
