from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def minimal_config_dict() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "executors": {
            "codex": {
                "provider": "codex",
                "displayName": "Codex CLI",
                "enabled": True,
                "required": True,
                "binary": "codex",
                "env": [],
            },
            "kimi": {
                "provider": "kimi",
                "displayName": "Kimi",
                "enabled": True,
                "required": False,
                "env": ["KIMI_API_KEY"],
            },
        },
        "deferredExecutors": {
            "gemini": {
                "provider": "gemini",
                "reason": "Gemini CLI execution is deferred until adapter coverage exists.",
            }
        },
        "phases": {
            "planning": {
                "riskOrder": ["low", "medium", "high"],
                "preferredExecutors": ["codex"],
                "fallbackExecutors": ["kimi"],
            },
            "production": {
                "riskOrder": ["high", "medium", "low"],
                "preferredExecutors": ["codex"],
                "fallbackExecutors": ["kimi"],
            },
        },
        "specialists": {
            "schema": {
                "keywords": ["schema", "migration", "database"],
                "risk": "high",
                "preferredExecutors": ["codex"],
                "fallbackExecutors": ["kimi"],
            },
            "thesis": {
                "keywords": ["thesis", "filter", "classifier"],
                "risk": "medium",
                "preferredExecutors": ["kimi"],
                "fallbackExecutors": ["codex"],
            },
        },
        "riskDefaults": {
            "noSpecialist": "medium",
            "highRiskKeywords": ["migration", "schema", "production"],
        },
        "routing": {
            "manualOverrideAllowed": True,
            "fallbackOrder": ["codex", "kimi"],
            "unknownTaskExecutor": "codex",
        },
        "gates": {
            "preflight": [
                {
                    "name": "hermes-tests",
                    "command": ["python", "-m", "pytest", "tests/ops/hermes/", "-q"],
                    "timeoutSeconds": 120,
                }
            ],
            "postflight": [],
        },
        "ledger": {
            "root": "ai-logs/hermes",
            "redactionPatterns": [
                "sk-[A-Za-z0-9_-]+",
                "(?i)(api[_-]?key|token|secret)=([^\\s]+)",
            ],
            "lockPath": "ai-logs/hermes/hermes.lock",
        },
        "modes": ["plan-only", "dry-run", "preflight-only", "execute"],
    }


@pytest.fixture()
def minimal_config_path(tmp_path: Path) -> Path:
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(minimal_config_dict(), indent=2), encoding="utf-8")
    return path

