"""CI ratchet for CLI-exclusive LLM generation.

Existing API-backed generation callers are recorded in
cli_exclusive_generation_baseline.json. New files or per-file count increases
fail so provider migrations can reduce the baseline over time.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).with_name("cli_exclusive_generation_baseline.json")
RULE_ID = "llm-generation-api-surface"

PRODUCTION_ROOTS = (
    "api",
    "collectors",
    "connectors",
    "consumer",
    "dashboard",
    "discovery_engine",
    "distribution",
    "enrichment",
    "governance",
    "importers",
    "integrations",
    "intelligence",
    "monitoring",
    "ops",
    "profilers",
    "scripts",
    "services",
    "storage",
    "utils",
    "verification",
    "visualization",
    "workflows",
)
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".worktrees",
    "__pycache__",
    "artifacts",
    "docs",
    "fixtures",
    "node_modules",
    "tests",
}
OPENAI_CLIENT_IMPORTS = {
    "OpenAI",
    "AsyncOpenAI",
    "AzureOpenAI",
    "AsyncAzureOpenAI",
}
ANTHROPIC_CLIENT_IMPORTS = {"Anthropic", "AsyncAnthropic"}
OPENAI_COMPATIBLE_CLIENT_CALLS = OPENAI_CLIENT_IMPORTS | {
    "KimiClient",
    "OpenAIMCPServer",
}
ANTHROPIC_CLIENT_CALLS = ANTHROPIC_CLIENT_IMPORTS
GOOGLE_GENAI_IMPORT_MODULES = {
    "google.genai",
    "google.genai.types",
}
FORBIDDEN_ENDPOINT_LITERALS = (
    "api.moonshot.cn/v1",
    "gemini.generate_content",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    message: str

    @property
    def count_key(self) -> tuple[str, str]:
        return RULE_ID, self.path

    def format(self) -> str:
        return f"{RULE_ID}: {self.path}:{self.line}: {self.kind}: {self.message}"


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_excluded(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    return any(part in EXCLUDED_PARTS or part.startswith(".") for part in rel_parts)


def _iter_production_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for rel_root in PRODUCTION_ROOTS:
        scan_root = root / rel_root
        if not scan_root.exists():
            continue
        files.extend(
            path
            for path in scan_root.rglob("*.py")
            if not _is_excluded(path, root)
        )
    return sorted(files)


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _attribute_chain(node: ast.AST) -> list[str]:
    chain: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        chain.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        chain.append(current.id)
    return list(reversed(chain))


def _call_leaf(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _call_chain(node: ast.Call) -> list[str]:
    return _attribute_chain(node.func)


def _add(
    findings: list[Finding],
    relpath: str,
    node: ast.AST,
    kind: str,
    message: str,
) -> None:
    findings.append(
        Finding(
            path=relpath,
            line=getattr(node, "lineno", 1),
            kind=kind,
            message=message,
        )
    )


def _detect_imports(tree: ast.AST, relpath: str, findings: list[Finding]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "openai":
                    _add(
                        findings,
                        relpath,
                        alias,
                        "openai-sdk-import",
                        "imports the OpenAI SDK instead of a CLI generation path",
                    )
                elif alias.name == "anthropic":
                    _add(
                        findings,
                        relpath,
                        alias,
                        "anthropic-sdk-import",
                        "imports the Anthropic SDK instead of a CLI generation path",
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if module == "openai" and alias.name in OPENAI_CLIENT_IMPORTS:
                    _add(
                        findings,
                        relpath,
                        alias,
                        "openai-client-import",
                        f"imports OpenAI generation client {alias.name}",
                    )
                elif module == "anthropic" and alias.name in ANTHROPIC_CLIENT_IMPORTS:
                    _add(
                        findings,
                        relpath,
                        alias,
                        "anthropic-client-import",
                        f"imports Anthropic generation client {alias.name}",
                    )
                elif module in GOOGLE_GENAI_IMPORT_MODULES:
                    _add(
                        findings,
                        relpath,
                        alias,
                        "google-genai-import",
                        f"imports {module} for API-backed generation",
                    )


def _detect_call(call: ast.Call, relpath: str, findings: list[Finding]) -> None:
    leaf = _call_leaf(call)
    chain = _call_chain(call)

    if leaf in OPENAI_COMPATIBLE_CLIENT_CALLS:
        _add(
            findings,
            relpath,
            call,
            "openai-compatible-client",
            f"constructs API-backed generation client {leaf}",
        )
    elif leaf in ANTHROPIC_CLIENT_CALLS:
        _add(
            findings,
            relpath,
            call,
            "anthropic-client",
            f"constructs API-backed generation client {leaf}",
        )

    if chain[-3:] == ["chat", "completions", "create"]:
        _add(
            findings,
            relpath,
            call,
            "openai-chat-completions",
            "calls chat.completions.create instead of a CLI generation path",
        )
    elif chain[-2:] == ["messages", "create"]:
        _add(
            findings,
            relpath,
            call,
            "anthropic-messages-create",
            "calls messages.create instead of a CLI generation path",
        )
    elif leaf == "generate_content":
        _add(
            findings,
            relpath,
            call,
            "google-generate-content",
            "calls Gemini generate_content instead of a CLI generation path",
        )
    elif leaf == "from_genai":
        _add(
            findings,
            relpath,
            call,
            "instructor-genai-wrapper",
            "wraps a Google GenAI client for structured API generation",
        )
    elif leaf == "create_with_completion":
        _add(
            findings,
            relpath,
            call,
            "instructor-completion-call",
            "calls Instructor structured completion over an API client",
        )


def _detect_literals(tree: ast.AST, relpath: str, findings: list[Finding]) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue

        value = node.value
        if value in GOOGLE_GENAI_IMPORT_MODULES:
            _add(
                findings,
                relpath,
                node,
                "google-genai-dynamic-import",
                f"dynamically imports {value} for structured API generation",
            )
            continue

        for literal in FORBIDDEN_ENDPOINT_LITERALS:
            if literal in value:
                _add(
                    findings,
                    relpath,
                    node,
                    "llm-generation-endpoint",
                    f"references API-backed generation endpoint {literal}",
                )
                break


def detect_cli_exclusive_generation(root: Path, files: Sequence[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        relpath = _relative_path(root, path)
        tree = _parse(path)

        _detect_imports(tree, relpath, findings)
        _detect_literals(tree, relpath, findings)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                _detect_call(node, relpath, findings)

    return findings


def _load_baseline() -> dict[str, Any]:
    with BASELINE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _scan_findings(root: Path) -> list[Finding]:
    return detect_cli_exclusive_generation(root, _iter_production_python_files(root))


def _finding_counts(findings: Sequence[Finding]) -> Counter[tuple[str, str]]:
    return Counter(finding.count_key for finding in findings)


def _baseline_counts(baseline: dict[str, Any]) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for rule_id, rule in baseline["rules"].items():
        for path, entry in rule["files"].items():
            counts[(rule_id, path)] = entry["count"]
    return counts


def _format_findings(findings: Sequence[Finding]) -> str:
    return "\n".join(finding.format() for finding in sorted(findings, key=Finding.format))


def _format_violations(violations: Sequence[str]) -> str:
    return "\n".join(sorted(violations))


def test_cli_generation_baseline_metadata_is_consistent() -> None:
    baseline = _load_baseline()
    assert baseline["schema_version"] == 1
    assert set(baseline["rules"]) == {RULE_ID}

    for rule_id, rule in baseline["rules"].items():
        files = rule["files"]
        metadata = rule["metadata"]
        assert metadata["total_files"] == len(files), rule_id
        assert metadata["total_occurrences"] == sum(
            entry["count"] for entry in files.values()
        ), rule_id
        for path, entry in files.items():
            assert path
            assert entry["count"] > 0
            assert entry["note"].strip()


def test_cli_generation_has_no_new_files_or_count_increases() -> None:
    baseline = _load_baseline()
    baseline_counts = _baseline_counts(baseline)
    current_findings = _scan_findings(REPO_ROOT)
    current_counts = _finding_counts(current_findings)
    violations: list[str] = []

    for rule_id, path in sorted(current_counts):
        current = current_counts[(rule_id, path)]
        allowed = baseline_counts.get((rule_id, path))
        if allowed is None:
            details = [
                finding
                for finding in current_findings
                if finding.path == path
            ]
            violations.append(
                f"{rule_id}: {path}: new file with {current} finding(s)\n"
                + _format_findings(details)
            )
        elif current > allowed:
            violations.append(
                f"{rule_id}: {path}: count increased baseline={allowed}, "
                f"actual={current}"
            )

    assert not violations, (
        "CLI-exclusive generation ratchet found new API-backed generation "
        "surfaces or count increases:\n"
        + _format_violations(violations)
    )


def test_cli_generation_totals_do_not_exceed_baseline() -> None:
    baseline = _load_baseline()
    current_counts = _finding_counts(_scan_findings(REPO_ROOT))
    violations: list[str] = []

    for rule_id, rule in baseline["rules"].items():
        current_rule_counts = {
            path: count
            for (current_rule_id, path), count in current_counts.items()
            if current_rule_id == rule_id
        }
        actual_total_files = len(current_rule_counts)
        actual_total_occurrences = sum(current_rule_counts.values())
        baseline_total_files = rule["metadata"]["total_files"]
        baseline_total_occurrences = rule["metadata"]["total_occurrences"]

        if actual_total_files > baseline_total_files:
            violations.append(
                f"{rule_id}: total_files baseline={baseline_total_files}, "
                f"actual={actual_total_files}"
            )
        if actual_total_occurrences > baseline_total_occurrences:
            violations.append(
                f"{rule_id}: total_occurrences "
                f"baseline={baseline_total_occurrences}, "
                f"actual={actual_total_occurrences}"
            )

    assert not violations, (
        "CLI-exclusive generation totals exceeded the baseline:\n"
        + _format_violations(violations)
    )


def test_embedding_api_carve_out_is_not_counted() -> None:
    findings = _scan_findings(REPO_ROOT)
    assert all(
        finding.path != "utils/embedding_generator.py"
        for finding in findings
    )


def test_cli_generation_detector_catches_synthetic_violations(
    tmp_path: Path,
) -> None:
    files = {
        "integrations/forbidden_generation.py": """
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

async def run(openai_client, anthropic_client, gemini_client):
    await openai_client.chat.completions.create(model="gpt", messages=[])
    await anthropic_client.messages.create(model="claude", messages=[])
    return gemini_client.models.generate_content(model="gemini", contents="hi")

def build_wrappers():
    return AsyncOpenAI(), KimiClient(), OpenAIMCPServer()
""",
        "consumer/structured_helper.py": """
import importlib

def build(deps, wrapped_client):
    importlib.import_module("google.genai.types")
    deps.instructor.from_genai(object())
    return wrapped_client.create_with_completion(messages=[])
""",
        "utils/embedder.py": """
def embed(client):
    return client.models.embed_content(model="text-embedding-004", contents="ok")
""",
    }
    for relpath, source in files.items():
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    findings = _scan_findings(tmp_path)
    counts = _finding_counts(findings)

    assert counts == Counter(
        {
            (RULE_ID, "integrations/forbidden_generation.py"): 8,
            (RULE_ID, "consumer/structured_helper.py"): 3,
        }
    )
    assert all(finding.path != "utils/embedder.py" for finding in findings)
    assert {finding.kind for finding in findings} >= {
        "anthropic-client-import",
        "anthropic-messages-create",
        "google-generate-content",
        "google-genai-dynamic-import",
        "instructor-completion-call",
        "instructor-genai-wrapper",
        "openai-chat-completions",
        "openai-client-import",
        "openai-compatible-client",
    }
