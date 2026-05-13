"""Sprint 0 architecture lint ratchet.

This is deliberately test-only scaffolding. Existing findings are recorded in
architecture_lint_baseline.json; new files or per-file count increases fail.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).with_name("architecture_lint_baseline.json")
PRODUCTION_ROOTS = (
    "api",
    "collectors",
    "connectors",
    "discovery_engine",
    "governance",
    "integrations",
    "intelligence",
    "monitoring",
    "ops",
    "services",
    "storage",
    "utils",
    "workflows",
)
MUTATING_ROUTE_METHODS = {"post", "put", "patch", "delete"}
HTTP_EXCEPTION_ALLOWED_EXACT = {
    "api/contracts.py",
    "api/db.py",
    "api/main.py",
    "api/middleware.py",
}
HTTP_EXCEPTION_ALLOWED_PREFIXES = (
    "api/auth/",
    "api/routers/",
)
PLACEHOLDER_CONSENSUS_MARKERS = (
    "[Claude's analysis",
    "would be provided by Claude Code",
    "Recommendations based on codebase context",
    "Considerations from Claude",
)


@dataclass(frozen=True)
class Finding:
    rule_id: str
    path: str
    line: int
    message: str

    @property
    def count_key(self) -> tuple[str, str]:
        return self.rule_id, self.path

    def format(self) -> str:
        return f"{self.rule_id}: {self.path}:{self.line}: {self.message}"


Detector = Callable[[Path, Sequence[Path]], list[Finding]]


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_production_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for rel_root in PRODUCTION_ROOTS:
        scan_root = root / rel_root
        if not scan_root.exists():
            continue
        files.extend(
            path
            for path in scan_root.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    return sorted(files)


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _decorator_method_names(node: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str]:
    methods: set[str] = set()
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        func = call.func if call is not None else decorator
        if isinstance(func, ast.Attribute):
            methods.add(func.attr.lower())
    return methods


def _annotation_name(annotation: ast.expr | None) -> str | None:
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    return None


def _class_base_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _keyword_string(node: ast.Call, keyword_name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg != keyword_name:
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def _string_literals(node: ast.AST) -> list[str]:
    values: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.append(child.value)
        elif isinstance(child, ast.JoinedStr):
            for value in child.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    values.append(value.value)
    return values


def _http_exception_allowed(relpath: str) -> bool:
    if relpath in HTTP_EXCEPTION_ALLOWED_EXACT:
        return True
    return any(relpath.startswith(prefix) for prefix in HTTP_EXCEPTION_ALLOWED_PREFIXES)


def detect_threading_lock_in_api(root: Path, files: Sequence[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        relpath = _relative_path(root, path)
        if not relpath.startswith("api/"):
            continue

        tree = _parse(path)
        threading_aliases = {"threading"}
        lock_aliases: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                threading_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "threading"
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "threading":
                lock_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "Lock"
                )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in threading_aliases
                and node.func.attr == "Lock"
            ) or (
                isinstance(node.func, ast.Name)
                and node.func.id in lock_aliases
            ):
                findings.append(
                    Finding(
                        "api-no-threading-lock-in-async-middleware",
                        relpath,
                        node.lineno,
                        "threading.Lock() in API async request path",
                    )
                )
    return findings


def detect_body_actor_authority(root: Path, files: Sequence[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        relpath = _relative_path(root, path)
        if not relpath.startswith("api/routers/"):
            continue

        tree = _parse(path)
        body_models_with_actor: set[str] = set()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if "BaseModel" not in _class_base_names(node):
                continue
            for statement in node.body:
                if (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id == "actor"
                ):
                    body_models_with_actor.add(node.name)

        if not body_models_with_actor:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if not (_decorator_method_names(node) & MUTATING_ROUTE_METHODS):
                continue

            actor_body_params = {
                arg.arg: _annotation_name(arg.annotation)
                for arg in node.args.args
                if _annotation_name(arg.annotation) in body_models_with_actor
            }
            if not actor_body_params:
                continue

            for child in ast.walk(node):
                if not isinstance(child, ast.Attribute) or child.attr != "actor":
                    continue
                if not isinstance(child.value, ast.Name):
                    continue
                model_name = actor_body_params.get(child.value.id)
                if model_name is None:
                    continue
                findings.append(
                    Finding(
                        "api-no-body-actor-authority",
                        relpath,
                        child.lineno,
                        f"{node.name} reads {child.value.id}.actor from {model_name}",
                    )
                )
    return findings


def detect_http_exception_below_boundary(
    root: Path,
    files: Sequence[Path],
) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        relpath = _relative_path(root, path)
        if _http_exception_allowed(relpath):
            continue

        tree = _parse(path)
        fastapi_aliases = {"fastapi"}
        http_exception_names = {"HTTPException"}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                fastapi_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "fastapi"
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "fastapi":
                for alias in node.names:
                    if alias.name == "HTTPException":
                        http_exception_names.add(alias.asname or alias.name)
                        findings.append(
                            Finding(
                                "api-http-exception-below-boundary",
                                relpath,
                                node.lineno,
                                "FastAPI HTTPException imported below API boundary",
                            )
                        )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in http_exception_names:
                findings.append(
                    Finding(
                        "api-http-exception-below-boundary",
                        relpath,
                        node.lineno,
                        "FastAPI HTTPException called below API boundary",
                    )
                )
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in fastapi_aliases
                and node.func.attr == "HTTPException"
            ):
                findings.append(
                    Finding(
                        "api-http-exception-below-boundary",
                        relpath,
                        node.lineno,
                        "FastAPI HTTPException called below API boundary",
                    )
                )
    return findings


def detect_placeholder_consensus(root: Path, files: Sequence[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        relpath = _relative_path(root, path)
        if not relpath.startswith(("integrations/", "services/", "workflows/")):
            continue

        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node) != "LLMPerspective":
                continue
            if _keyword_string(node, "source") != "claude":
                continue
            if not any(
                marker in literal
                for literal in _string_literals(node)
                for marker in PLACEHOLDER_CONSENSUS_MARKERS
            ):
                continue
            findings.append(
                Finding(
                    "strategy-no-placeholder-consensus",
                    relpath,
                    node.lineno,
                    "LLMPerspective returns static Claude placeholder analysis",
                )
            )
    return findings


def detect_empty_service_modules(root: Path, files: Sequence[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        relpath = _relative_path(root, path)
        if not relpath.startswith("services/") or path.name == "__init__.py":
            continue
        if path.stat().st_size != 0:
            continue
        findings.append(
            Finding(
                "services-no-empty-module",
                relpath,
                1,
                "service module is empty",
            )
        )
    return findings


def detect_listmeta_cursor_keyword(root: Path, files: Sequence[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        relpath = _relative_path(root, path)
        if not relpath.startswith("api/"):
            continue

        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "ListMeta":
                continue
            for keyword in node.keywords:
                if keyword.arg == "cursor":
                    findings.append(
                        Finding(
                            "api-listmeta-next-cursor",
                            relpath,
                            node.lineno,
                            "ListMeta(cursor=...) should be ListMeta(next_cursor=...)",
                        )
                    )
    return findings


DETECTORS: dict[str, Detector] = {
    "api-no-threading-lock-in-async-middleware": detect_threading_lock_in_api,
    "api-no-body-actor-authority": detect_body_actor_authority,
    "api-http-exception-below-boundary": detect_http_exception_below_boundary,
    "strategy-no-placeholder-consensus": detect_placeholder_consensus,
    "services-no-empty-module": detect_empty_service_modules,
    "api-listmeta-next-cursor": detect_listmeta_cursor_keyword,
}


def _load_baseline() -> dict[str, Any]:
    with BASELINE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _scan_findings(root: Path) -> list[Finding]:
    files = _iter_production_python_files(root)
    findings: list[Finding] = []
    for rule_id, detector in DETECTORS.items():
        for finding in detector(root, files):
            assert finding.rule_id == rule_id
            findings.append(finding)
    return findings


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


def _format_count_violations(violations: Sequence[str]) -> str:
    return "\n".join(sorted(violations))


def test_architecture_lint_baseline_metadata_is_consistent() -> None:
    baseline = _load_baseline()
    assert baseline["schema_version"] == 1
    assert set(baseline["rules"]) == set(DETECTORS)

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


def test_rate_limit_threading_lock_baseline_is_cleared() -> None:
    rule = _load_baseline()["rules"]["api-no-threading-lock-in-async-middleware"]

    assert rule["metadata"] == {
        "total_files": 0,
        "total_occurrences": 0,
    }
    assert rule["files"] == {}


def test_architecture_lint_has_no_new_files_or_count_increases() -> None:
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
                if finding.rule_id == rule_id and finding.path == path
            ]
            violations.append(
                f"{rule_id}: {path}: new file with {current} finding(s)\n"
                + _format_findings(details)
            )
        elif current > allowed:
            violations.append(
                f"{rule_id}: {path}: count increased baseline={allowed}, actual={current}"
            )

    assert not violations, (
        "Architecture lint ratchet found new findings or count increases:\n"
        + _format_count_violations(violations)
    )


def test_architecture_lint_totals_do_not_exceed_baseline() -> None:
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
                f"{rule_id}: total_occurrences baseline={baseline_total_occurrences}, "
                f"actual={actual_total_occurrences}"
            )

    assert not violations, (
        "Architecture lint totals exceeded the baseline:\n"
        + _format_count_violations(violations)
    )


def test_architecture_lint_detectors_catch_synthetic_violations(tmp_path: Path) -> None:
    files = {
        "api/middleware.py": "import threading\nlock = threading.Lock()\n",
        "api/routers/actions.py": """
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class TrackRequest(BaseModel):
    actor: str | None = None

@router.post("/track")
async def track_company(request: TrackRequest):
    return request.actor
""",
        "services/bad.py": "from fastapi import HTTPException\nraise HTTPException()\n",
        "api/routers/allowed_http_exception.py": """
from fastapi import HTTPException

def handler():
    raise HTTPException(status_code=404)
""",
        "api/auth/allowed_http_exception.py": """
from fastapi import HTTPException

def handler():
    raise HTTPException(status_code=401)
""",
        "api/db.py": """
from fastapi import HTTPException

def get_store():
    raise HTTPException(status_code=500)
""",
        "integrations/strategy_iterator.py": """
def build_perspective(question):
    return LLMPerspective(
        source="claude",
        content=f"[Claude's analysis of: {question}]",
    )
""",
        "services/classification_service.py": "",
        "services/implemented_service.py": "class ClassificationService:\n    pass\n",
        "api/routers/pagination.py": """
def list_items():
    return ListMeta(cursor=next_cursor)
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
            ("api-no-threading-lock-in-async-middleware", "api/middleware.py"): 1,
            ("api-no-body-actor-authority", "api/routers/actions.py"): 1,
            ("api-http-exception-below-boundary", "services/bad.py"): 2,
            ("strategy-no-placeholder-consensus", "integrations/strategy_iterator.py"): 1,
            ("services-no-empty-module", "services/classification_service.py"): 1,
            ("api-listmeta-next-cursor", "api/routers/pagination.py"): 1,
        }
    )
    assert all("allowed_http_exception.py" not in finding.path for finding in findings)
    assert all("implemented_service.py" not in finding.path for finding in findings)
    assert all(finding.message for finding in findings)
