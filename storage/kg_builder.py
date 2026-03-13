"""Architecture KG builder for the v50 knowledge graph.

This module implements the GraphRAG "Layer 2" architecture extraction in a
way that fits the repo *as it exists today*:

- Uses deterministic code/document introspection only (no LLM)
- Reuses the existing v50 `kg_nodes` / `kg_edges` schema without requiring a
  schema bump
- Encodes architecture subtypes in JSON properties (`layer=architecture`,
  `arch_kind=...`) so the graph can be populated immediately against current
  databases

Why this shape instead of the full 21-node/24-edge spec?
-------------------------------------------------------
The current v50 migration enforces a small enum surface for node_type and
edge_type.  Existing production/test databases therefore cannot accept brand
new architecture-specific enums without a follow-up migration.  To avoid
blocking on that migration, this builder stores:

- architecture node subtype in `kg_nodes.properties.arch_kind`
- architecture edge subtype in `kg_edges.properties.arch_edge_kind`
- live architecture edges under the existing `edge_type='has_evidence'`

That gives us an immediately usable graph with deterministic IDs, run
provenance, and recursive traversal, while keeping a later v51/v52 taxonomy
upgrade straightforward.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, TYPE_CHECKING

from monitoring.activation_gate import STEP_POLICY
from monitoring.feature_gate import _CONFIG_KEYS
from storage.kg_store import KGEdge, KGNode, KGStore
from verification.evidence_families import (
    VALID_FAMILIES,
    _SIGNAL_TYPE_FAMILIES,
    _SOURCE_API_OVERRIDES,
)
from verification.verification_gate_v2 import (
    HALF_LIVES,
    HARD_KILL_SIGNALS,
    NEGATIVE_MULTIPLIERS,
    SIGNAL_WEIGHTS,
    VerificationGate,
)
from workflows.delivery_policy import DeliveryIntent, DeliveryMode, _ALLOWED_INTENTS
from workflows.feature_guards import _FEATURE_ENV_MAP, _VALID_MODES

if TYPE_CHECKING:  # pragma: no cover
    import aiosqlite

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# v50-compatible architecture encoding
# ---------------------------------------------------------------------------

ARCH_LAYER = "architecture"
ARCH_SOURCE_TABLE = "architecture"
ARCH_EDGE_TYPE = "has_evidence"  # existing v50 enum, semantics carried in JSON

REPO_ROOT = Path(__file__).resolve().parents[1].resolve()

# Main linear path used for lineage traversals.
PIPELINE_STAGE_MANIFEST: list[tuple[str, str, str]] = [
    ("pipeline_stage:collectors", "Collectors", "Runs the configured collectors"),
    ("pipeline_stage:processing", "Signal Processing", "Groups, enriches, filters, and scores pending signals"),
    ("pipeline_stage:review_queue", "Review Queue", "Stores approved/tracking prospects for operator workflows"),
    ("pipeline_stage:notion_outbox", "Notion Outbox", "Queues idempotent Notion write payloads"),
    ("pipeline_stage:notion_delivery", "Notion Delivery", "Applies delivery policy and drains writes"),
    ("organization:notion_crm", "Notion CRM", "Final external CRM sink"),
]

PIPELINE_STAGE_EDGES: list[tuple[str, str, str]] = [
    ("pipeline_stage:collectors", "pipeline_stage:processing", "feeds_into"),
    ("pipeline_stage:processing", "pipeline_stage:review_queue", "feeds_into"),
    ("pipeline_stage:review_queue", "pipeline_stage:notion_outbox", "feeds_into"),
    ("pipeline_stage:notion_outbox", "pipeline_stage:notion_delivery", "feeds_into"),
    ("pipeline_stage:notion_delivery", "organization:notion_crm", "feeds_into"),
]

DECISION_GATES: list[dict[str, Any]] = [
    {
        "id": "decision_gate:thesis_filter",
        "label": "Thesis Filter",
        "source_ref": "workflows/pipeline.py::_process_company",
        "properties": {
            "layer": ARCH_LAYER,
            "arch_kind": "decision_gate",
            "gate_name": "thesis_filter",
            "module": "workflows.pipeline",
            "description": "Keyword + LLM thesis routing before verification and delivery",
        },
    },
    {
        "id": "decision_gate:verification_gate",
        "label": "Verification Gate",
        "source_ref": "verification/verification_gate_v2.py::VerificationGate",
        "properties": {
            "layer": ARCH_LAYER,
            "arch_kind": "decision_gate",
            "gate_name": "verification_gate",
            "module": "verification.verification_gate_v2",
            "policy_version": VerificationGate.POLICY_VERSION,
            "high_confidence_threshold": VerificationGate.HIGH_CONFIDENCE_THRESHOLD,
            "medium_confidence_threshold": VerificationGate.MEDIUM_CONFIDENCE_THRESHOLD,
            "min_sources_for_auto_push": VerificationGate.MIN_SOURCES_FOR_AUTO_PUSH,
            "score_recalibration_factor": VerificationGate.SCORE_RECALIBRATION_FACTOR,
        },
    },
    {
        "id": "decision_gate:activation_gate",
        "label": "Activation Gate",
        "source_ref": "monitoring/activation_gate.py::STEP_POLICY",
        "properties": {
            "layer": ARCH_LAYER,
            "arch_kind": "decision_gate",
            "gate_name": "activation_gate",
            "module": "monitoring.activation_gate",
            "steps": sorted(STEP_POLICY.keys()),
        },
    },
    {
        "id": "decision_gate:delivery_policy",
        "label": "Delivery Policy",
        "source_ref": "workflows/delivery_policy.py::_ALLOWED_INTENTS",
        "properties": {
            "layer": ARCH_LAYER,
            "arch_kind": "decision_gate",
            "gate_name": "delivery_policy",
            "module": "workflows.delivery_policy",
            "default_mode": DeliveryMode.STAGING_ONLY.value,
        },
    },
    {
        "id": "decision_gate:feature_guards",
        "label": "Feature Guards",
        "source_ref": "workflows/feature_guards.py::_FEATURE_ENV_MAP",
        "properties": {
            "layer": ARCH_LAYER,
            "arch_kind": "decision_gate",
            "gate_name": "feature_guards",
            "module": "workflows.feature_guards",
            "guarded_write_features": sorted(f.value for f in _FEATURE_ENV_MAP),
        },
    },
]

COMPONENT_MANIFEST: list[dict[str, Any]] = [
    {
        "id": "organization:thin_files",
        "label": "Thin Files",
        "component_name": "thin_files",
        "description": "Promotes multi-source signals into company_files thin records",
    },
    {
        "id": "organization:claim_facts",
        "label": "Claim Facts",
        "component_name": "claim_facts",
        "description": "Extracts bi-temporal claim facts after consolidation",
    },
    {
        "id": "organization:entity_resolution",
        "label": "Entity Resolution",
        "component_name": "entity_resolution",
        "description": "Phase G blocking-first fuzzy entity resolution",
    },
    {
        "id": "organization:shadow_entity_resolution",
        "label": "Shadow Entity Resolution",
        "component_name": "shadow_entity_resolution",
        "description": "Read-only comparison path for identity resolution",
    },
    {
        "id": "organization:drift_monitoring",
        "label": "Drift Monitoring",
        "component_name": "drift_monitoring",
        "description": "SPC metrics and drift alert lifecycle",
    },
    {
        "id": "organization:merge_writes",
        "label": "Merge Writes",
        "component_name": "merge_writes",
        "description": "Applies entity merges after activation",
    },
    {
        "id": "organization:bulk_triage",
        "label": "Bulk Triage",
        "component_name": "bulk_triage",
        "description": "Bulk approve/reject review workflows",
    },
    {
        "id": "organization:hunter_promote",
        "label": "Hunter Promote",
        "component_name": "hunter_promote",
        "description": "Promotes hunter results into the review queue",
    },
    {
        "id": "organization:ml_scoring",
        "label": "ML Scoring",
        "component_name": "ml_scoring",
        "description": "Optional ML scoring path controlled by ML_ENABLEMENT",
    },
]

FEATURE_FLAG_DEFAULTS: dict[str, Any] = {
    "LLM_THESIS_MODE": "off",
    "ML_ENABLEMENT": "disabled",
    "MERGE_WRITES_ENABLED": "disabled",
    "USE_SHADOW_ENTITY_RESOLUTION": "false",
    "DRIFT_MONITORING_ENABLED": "disabled",
    "USE_THIN_FILES": "false",
    "V2_ENABLEMENT": "shadow",
    "DELIVERY_MODE": "staging_only",
    "BULK_TRIAGE_ENABLED": "disabled",
    "HUNTER_PROMOTE_ENABLED": "disabled",
    "USE_PHASE_G_IDENTITY_RESOLUTION": "false",
    "USE_CLAIM_FACTS": "false",
}

FEATURE_FLAG_ALLOWED_VALUES: dict[str, list[str]] = {
    "LLM_THESIS_MODE": ["off", "shadow", "active"],
    "ML_ENABLEMENT": ["disabled", "shadow", "live"],
    "USE_SHADOW_ENTITY_RESOLUTION": ["false", "true"],
    "DRIFT_MONITORING_ENABLED": ["disabled", "active"],
    "USE_THIN_FILES": ["false", "true"],
    "V2_ENABLEMENT": ["disabled", "shadow", "live"],
    "DELIVERY_MODE": [m.value for m in DeliveryMode],
    "BULK_TRIAGE_ENABLED": ["disabled", "active"],
    "HUNTER_PROMOTE_ENABLED": ["disabled", "active"],
    "USE_PHASE_G_IDENTITY_RESOLUTION": ["false", "true"],
    "USE_CLAIM_FACTS": ["false", "true"],
}
for feature, modes in _VALID_MODES.items():
    FEATURE_FLAG_ALLOWED_VALUES[_FEATURE_ENV_MAP[feature]] = sorted(modes)

# ---------------------------------------------------------------------------
# Curated ontology manifests
#
# These dicts are hand-maintained and encode relationships that cannot be
# reliably extracted from code or docs via AST alone.  They should be
# reviewed when activation runbooks or feature-flag wiring changes.
#
# Edges sourced from these manifests carry provenance_type="curated_ontology"
# so that downstream validation can distinguish them from code-extracted edges.
# ---------------------------------------------------------------------------

# What each activation step actually unlocks in the current runbook.
# Source: docs/runbooks/feature-activation.md
ACTIVATION_UNLOCKS: dict[int, list[dict[str, Any]]] = {
    1: [
        {"flag": "LLM_THESIS_MODE", "recommended_value": "shadow"},
        {"flag": "ML_ENABLEMENT", "recommended_value": "shadow"},
        {"flag": "MERGE_WRITES_ENABLED", "recommended_value": "shadow"},
        {"flag": "USE_SHADOW_ENTITY_RESOLUTION", "recommended_value": "true"},
    ],
    2: [
        {"flag": "DRIFT_MONITORING_ENABLED", "recommended_value": "active"},
        {"flag": "USE_THIN_FILES", "recommended_value": "true"},
        {"flag": "V2_ENABLEMENT", "recommended_value": "live"},
    ],
    3: [
        {"flag": "DELIVERY_MODE", "recommended_value": "manual_publish"},
        {"flag": "BULK_TRIAGE_ENABLED", "recommended_value": "active"},
        {"flag": "HUNTER_PROMOTE_ENABLED", "recommended_value": "active"},
    ],
    4: [
        {"flag": "DELIVERY_MODE", "recommended_value": "batch_publish", "subphase": "4A"},
        {"flag": "MERGE_WRITES_ENABLED", "recommended_value": "shadow", "subphase": "4A"},
        {"flag": "MERGE_WRITES_ENABLED", "recommended_value": "active", "subphase": "4B"},
        {"flag": "USE_PHASE_G_IDENTITY_RESOLUTION", "recommended_value": "true", "subphase": "4B"},
        {"flag": "USE_CLAIM_FACTS", "recommended_value": "true", "subphase": "4B"},
    ],
}

# Which gates/components each feature flag controls or enables.
# Source: repo feature wiring (feature_guards.py, delivery_policy.py, etc.)
FEATURE_FLAG_TARGETS: dict[str, list[dict[str, str]]] = {
    "LLM_THESIS_MODE": [{"target": "decision_gate:thesis_filter", "relation": "controls"}],
    "ML_ENABLEMENT": [{"target": "organization:ml_scoring", "relation": "enables"}],
    "MERGE_WRITES_ENABLED": [
        {"target": "decision_gate:feature_guards", "relation": "controls"},
        {"target": "organization:merge_writes", "relation": "enables"},
    ],
    "USE_SHADOW_ENTITY_RESOLUTION": [
        {"target": "organization:shadow_entity_resolution", "relation": "enables"},
    ],
    "DRIFT_MONITORING_ENABLED": [
        {"target": "decision_gate:activation_gate", "relation": "controls"},
        {"target": "organization:drift_monitoring", "relation": "enables"},
    ],
    "USE_THIN_FILES": [{"target": "organization:thin_files", "relation": "enables"}],
    "V2_ENABLEMENT": [{"target": "decision_gate:verification_gate", "relation": "controls"}],
    "DELIVERY_MODE": [
        {"target": "decision_gate:delivery_policy", "relation": "controls"},
        {"target": "pipeline_stage:notion_delivery", "relation": "enables"},
    ],
    "BULK_TRIAGE_ENABLED": [
        {"target": "decision_gate:feature_guards", "relation": "controls"},
        {"target": "organization:bulk_triage", "relation": "enables"},
    ],
    "HUNTER_PROMOTE_ENABLED": [
        {"target": "decision_gate:feature_guards", "relation": "controls"},
        {"target": "organization:hunter_promote", "relation": "enables"},
    ],
    "USE_PHASE_G_IDENTITY_RESOLUTION": [
        {"target": "organization:entity_resolution", "relation": "enables"},
    ],
    "USE_CLAIM_FACTS": [{"target": "organization:claim_facts", "relation": "enables"}],
}

# Which pipeline stages each decision gate controls.
# Source: pipeline.py wiring + runbook docs
GATE_TARGETS: dict[str, list[str]] = {
    "decision_gate:thesis_filter": ["pipeline_stage:processing"],
    "decision_gate:verification_gate": ["pipeline_stage:review_queue"],
    "decision_gate:activation_gate": ["pipeline_stage:notion_delivery"],
    "decision_gate:delivery_policy": ["pipeline_stage:notion_delivery"],
    "decision_gate:feature_guards": [
        "organization:merge_writes",
        "organization:bulk_triage",
        "organization:hunter_promote",
        "organization:drift_monitoring",
    ],
}

KNOWN_SIGNAL_TYPES_EXTRA = {
    "community_mention",
    "feedback_request",
    "funding_news",
}

SUPPORTED_COLLECTOR_FILES = {
    "github": "collectors/github.py",
    "sec_edgar": "collectors/sec_edgar.py",
    "companies_house": "collectors/companies_house.py",
    "domain_whois": "collectors/domain_whois.py",
    "product_hunt": "collectors/product_hunt.py",
    "hacker_news": "collectors/hacker_news.py",
    "arxiv": "collectors/arxiv.py",
    "job_postings": "collectors/job_postings.py",
    "github_activity": "collectors/github_activity.py",
    "linkedin": "collectors/linkedin.py",
    "crunchbase": "collectors/crunchbase.py",
    "uspto": "collectors/uspto.py",
    "opencorporates": "collectors/opencorporates.py",
    "telegram": "collectors/telegram.py",
    "discord": "collectors/discord.py",
    "news_api": "collectors/news_api.py",
    "rss_feeds": "collectors/rss_feeds.py",
}


# ---------------------------------------------------------------------------
# Small data helpers
# ---------------------------------------------------------------------------

@dataclass
class BuildSourcePayload:
    source_name: str
    nodes: list[KGNode] = field(default_factory=list)
    edges: list[KGEdge] = field(default_factory=list)
    evidence: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class KGArchitectureBuildReport:
    run_id: str
    status: str
    nodes_upserted: int
    edges_upserted: int
    nodes_tombstoned: int
    edges_expired: int
    source_rows: dict[str, dict[str, int]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "nodes_upserted": self.nodes_upserted,
            "edges_upserted": self.edges_upserted,
            "nodes_tombstoned": self.nodes_tombstoned,
            "edges_expired": self.edges_expired,
            "source_rows": self.source_rows,
            "warnings": self.warnings,
            "details": self.details,
        }


def _arch_edge_id(kind: str, source_node_id: str, target_node_id: str) -> str:
    seed = f"{kind}|{source_node_id}|{target_node_id}".encode("utf-8")
    digest = hashlib.sha1(seed).hexdigest()[:20]
    return f"arch_edge:{digest}"


def _arch_edge(
    *,
    relation: str,
    source_node_id: str,
    target_node_id: str,
    source_ref: str,
    weight: float = 1.0,
    provenance_type: str = "code_extracted",
    **properties: Any,
) -> KGEdge:
    props = {
        "layer": ARCH_LAYER,
        "arch_edge_kind": relation,
        "source_ref": source_ref,
        "provenance_type": provenance_type,
        **properties,
    }
    return KGEdge(
        id=_arch_edge_id(relation, source_node_id, target_node_id),
        edge_type=ARCH_EDGE_TYPE,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        weight=weight,
        properties=props,
    )


def _arch_node(
    *,
    node_id: str,
    node_type: str,
    label: str,
    source_ref: str,
    **properties: Any,
) -> KGNode:
    props = {
        "layer": ARCH_LAYER,
        "source_ref": source_ref,
        **properties,
    }
    return KGNode(
        id=node_id,
        node_type=node_type,
        label=label,
        properties=props,
        source_table=ARCH_SOURCE_TABLE,
        source_id=node_id,
    )


def _read_ast(path: Path) -> ast.AST:
    text = path.read_text(encoding="utf-8-sig")
    return ast.parse(text)


def _iter_string_constants(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def _literal_string_values(node: Optional[ast.AST]) -> set[str]:
    """Extract literal string values from a small subset of AST nodes.

    This intentionally handles only the forms collectors use for signal-type
    declarations: string constants, list/tuple/set literals, dict values, and
    simple ternary expressions.
    """
    if node is None:
        return set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: set[str] = set()
        for elt in node.elts:
            values.update(_literal_string_values(elt))
        return values
    if isinstance(node, ast.Dict):
        values = set()
        for value in node.values:
            values.update(_literal_string_values(value))
        return values
    if isinstance(node, ast.IfExp):
        return _literal_string_values(node.body) | _literal_string_values(node.orelse)
    if isinstance(node, ast.Call):
        values = set()
        for arg in node.args:
            values.update(_literal_string_values(arg))
        for keyword in node.keywords:
            values.update(_literal_string_values(keyword.value))
        return values
    return set()


def _target_name(target: ast.AST) -> Optional[str]:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _looks_like_signal_type(value: str) -> bool:
    """Heuristic filter for signal identifiers used in collectors.

    Signal types in this repo are lowercase snake_case identifiers.  Keeping
    this conservative avoids sweeping up unrelated UI strings and docs text
    while still allowing novel signal names to surface.
    """
    if not value:
        return False
    if value != value.lower():
        return False
    return all(ch.islower() or ch.isdigit() or ch == "_" for ch in value)


def _is_signal_mapping_name(name: str) -> bool:
    normalized = name.lower()
    return normalized.endswith("signals") or "signal_types" in normalized


class _SignalTypeLiteralExtractor(ast.NodeVisitor):
    """Extract signal-type literals from collector source without filtering.

    The previous implementation only returned strings already present in the
    known signal catalog, which made the caller's defensive branch for novel
    collector-emitted signal types unreachable.  This visitor focuses on the
    syntactic positions that actually define signal types in collectors.
    """

    def __init__(self) -> None:
        self.signal_types: set[str] = set()
        self._function_stack: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_stack.append(node.name.lower())
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_stack.append(node.name.lower())
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg in {"signal_type", "signal_types"}:
                self.signal_types.update(_literal_string_values(keyword.value))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            target_name = _target_name(target)
            if not target_name:
                continue
            normalized = target_name.lower()
            if normalized in {"signal_type", "signal_types"}:
                self.signal_types.update(_literal_string_values(node.value))
            elif _is_signal_mapping_name(target_name) and isinstance(node.value, ast.Dict):
                self.signal_types.update(_literal_string_values(node.value))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        target_name = _target_name(node.target)
        if target_name:
            normalized = target_name.lower()
            if normalized in {"signal_type", "signal_types"}:
                self.signal_types.update(_literal_string_values(node.value))
            elif _is_signal_mapping_name(target_name) and isinstance(node.value, ast.Dict):
                self.signal_types.update(_literal_string_values(node.value))
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        current_function = self._function_stack[-1] if self._function_stack else ""
        if "signal_type" in current_function or current_function.endswith("_signals"):
            self.signal_types.update(_literal_string_values(node.value))
        self.generic_visit(node)

    def extracted(self) -> list[str]:
        return sorted(value for value in self.signal_types if _looks_like_signal_type(value))


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class KGArchitectureBuilder:
    """Deterministic architecture graph materializer."""

    def __init__(self, db: "aiosqlite.Connection", repo_root: Optional[Path] = None):
        self._db = db
        self._kg = KGStore(db)
        resolved_root = Path(repo_root).resolve() if repo_root else REPO_ROOT
        if resolved_root != REPO_ROOT:
            raise ValueError(
                "Cross-checkout graph builds are not supported. "
                "--repo-root must resolve to the running checkout to avoid "
                "mixing imported policy constants with AST data from another repo."
            )
        self._repo_root = resolved_root

    async def build(self) -> KGArchitectureBuildReport:
        """Build or refresh the architecture layer in kg_* tables."""
        await self._kg.recover_stale_runs()
        run = await self._kg.create_run(mode="full")
        warnings: list[str] = []
        current_node_ids: set[str] = set()
        current_edge_ids: set[str] = set()
        nodes_upserted = 0
        edges_upserted = 0
        source_rows: dict[str, dict[str, int]] = {}

        payloads = [
            self._build_core_architecture_payload(),
            self._build_feature_payload(),
            self._build_signal_model_payload(),
            self._build_collector_payload(),
            self._build_activation_payload(),
        ]

        try:
            for payload in payloads:
                node_count = len(payload.nodes)
                edge_count = len(payload.edges)
                evidence_count = len(payload.evidence)
                source_id = await self._kg.create_run_source(
                    run.run_id,
                    payload.source_name,
                    refresh_strategy="full_recompute",
                )
                await self._db.execute("BEGIN")
                try:
                    for node in payload.nodes:
                        await self._kg.upsert_node(node, run_id=run.run_id, conn=self._db)
                        await self._kg.log_provenance(
                            run.run_id,
                            "update",
                            node_id=node.id,
                            detail={
                                "source_name": payload.source_name,
                                "layer": ARCH_LAYER,
                                "arch_kind": (node.properties or {}).get("arch_kind"),
                            },
                            conn=self._db,
                        )
                        current_node_ids.add(node.id)
                    for edge in payload.edges:
                        await self._kg.upsert_edge(edge, run_id=run.run_id, conn=self._db)
                        await self._kg.log_provenance(
                            run.run_id,
                            "update",
                            edge_id=edge.id,
                            detail={
                                "source_name": payload.source_name,
                                "layer": ARCH_LAYER,
                                "arch_edge_kind": (edge.properties or {}).get("arch_edge_kind"),
                            },
                            conn=self._db,
                        )
                        current_edge_ids.add(edge.id)
                    for edge_id, source, detail in payload.evidence:
                        await self._kg.add_edge_evidence(edge_id, source, detail=detail, conn=self._db)
                    await self._db.commit()
                except Exception as payload_exc:
                    await self._db.rollback()
                    await self._kg.complete_run_source(
                        source_id,
                        status="failed",
                        rows_scanned=node_count + edge_count,
                        rows_written=0,
                        error_text=str(payload_exc),
                    )
                    raise

                nodes_upserted += node_count
                edges_upserted += edge_count
                warnings.extend(payload.warnings)
                source_rows[payload.source_name] = {
                    "nodes": node_count,
                    "edges": edge_count,
                    "evidence_rows": evidence_count,
                }
                await self._kg.complete_run_source(
                    source_id,
                    status="completed",
                    rows_scanned=node_count + edge_count,
                    rows_written=node_count + edge_count + evidence_count,
                )

            nodes_tombstoned = await self._tombstone_stale_nodes(run.run_id, current_node_ids)
            edges_expired = await self._expire_stale_edges(run.run_id, current_edge_ids)

            # Ontology drift warnings
            warnings.extend(self._ontology_drift_warnings())

            await self._kg.complete_run(
                run.run_id,
                status="completed",
                nodes_upserted=nodes_upserted,
                edges_upserted=edges_upserted,
                nodes_tombstoned=nodes_tombstoned,
                edges_expired=edges_expired,
            )

            report = KGArchitectureBuildReport(
                run_id=run.run_id,
                status="completed",
                nodes_upserted=nodes_upserted,
                edges_upserted=edges_upserted,
                nodes_tombstoned=nodes_tombstoned,
                edges_expired=edges_expired,
                source_rows=source_rows,
                warnings=sorted(dict.fromkeys(warnings)),
                details={
                    "collector_count": self._collector_count_hint(),
                    "weighted_signal_types": len(SIGNAL_WEIGHTS),
                },
            )
            logger.info(
                "KG architecture build complete: run_id=%s nodes=%d edges=%d tombstoned=%d expired=%d",
                report.run_id,
                report.nodes_upserted,
                report.edges_upserted,
                report.nodes_tombstoned,
                report.edges_expired,
            )
            return report
        except Exception as exc:
            logger.exception("KG architecture build failed")
            await self._kg.complete_run(run.run_id, status="failed")
            raise RuntimeError(f"KG architecture build failed: {exc}") from exc

    # -- payload builders ----------------------------------------------------

    def _build_core_architecture_payload(self) -> BuildSourcePayload:
        payload = BuildSourcePayload(source_name="architecture_core")

        # Main stages / sink
        for node_id, label, description in PIPELINE_STAGE_MANIFEST:
            node_type = "organization" if node_id.startswith("organization:") else "organization"
            arch_kind = "sink" if node_id.startswith("organization:") else "pipeline_stage"
            payload.nodes.append(
                _arch_node(
                    node_id=node_id,
                    node_type=node_type,
                    label=label,
                    source_ref="docs/architecture-overview.md + workflows/pipeline.py",
                    arch_kind=arch_kind,
                    description=description,
                )
            )

        # Decision gates
        for gate in DECISION_GATES:
            payload.nodes.append(
                KGNode(
                    id=gate["id"],
                    node_type="organization",
                    label=gate["label"],
                    properties=gate["properties"],
                    source_table=ARCH_SOURCE_TABLE,
                    source_id=gate["id"],
                )
            )

        # Optional components / sidecars
        for component in COMPONENT_MANIFEST:
            payload.nodes.append(
                _arch_node(
                    node_id=component["id"],
                    node_type="organization",
                    label=component["label"],
                    source_ref="workflows/pipeline.py + feature flags",
                    arch_kind="component",
                    component_name=component["component_name"],
                    description=component["description"],
                )
            )

        # Main stage order
        for src, tgt, relation in PIPELINE_STAGE_EDGES:
            edge = _arch_edge(
                relation=relation,
                source_node_id=src,
                target_node_id=tgt,
                source_ref="workflows/pipeline.py::run_full_pipeline",
            )
            payload.edges.append(edge)
            payload.evidence.append((
                edge.id,
                "workflows/pipeline.py::run_full_pipeline",
                {"relation": relation, "source": src, "target": tgt},
            ))

        # Gate -> target wiring
        for gate_id, targets in GATE_TARGETS.items():
            for target in targets:
                edge = _arch_edge(
                    relation="gates",
                    source_node_id=gate_id,
                    target_node_id=target,
                    source_ref="repo static manifest",
                    provenance_type="curated_ontology",
                )
                payload.edges.append(edge)
                payload.evidence.append((
                    edge.id,
                    "repo static manifest",
                    {"relation": "gates", "source": gate_id, "target": target},
                ))

        return payload

    def _build_feature_payload(self) -> BuildSourcePayload:
        payload = BuildSourcePayload(source_name="feature_flags_and_delivery")

        relevant_flags = sorted(
            set(_CONFIG_KEYS)
            | set(FEATURE_FLAG_DEFAULTS)
            | set(FEATURE_FLAG_TARGETS)
            | {"USE_PHASE_G_IDENTITY_RESOLUTION", "USE_CLAIM_FACTS"}
        )

        for flag in relevant_flags:
            allowed = FEATURE_FLAG_ALLOWED_VALUES.get(flag, [])
            payload.nodes.append(
                _arch_node(
                    node_id=f"feature_flag:{flag}",
                    node_type="skill",
                    label=flag,
                    source_ref="monitoring/feature_gate.py::_CONFIG_KEYS + docs/runbooks/feature-activation.md",
                    arch_kind="feature_flag",
                    flag_name=flag,
                    default_value=FEATURE_FLAG_DEFAULTS.get(flag),
                    allowed_values=allowed,
                )
            )

        # Delivery modes / intents are the most concrete policy lattice today.
        for mode in DeliveryMode:
            payload.nodes.append(
                _arch_node(
                    node_id=f"delivery_mode:{mode.value}",
                    node_type="skill",
                    label=mode.value,
                    source_ref="workflows/delivery_policy.py::DeliveryMode",
                    arch_kind="delivery_mode",
                    mode_name=mode.value,
                )
            )
            edge = _arch_edge(
                relation="has_option",
                source_node_id="feature_flag:DELIVERY_MODE",
                target_node_id=f"delivery_mode:{mode.value}",
                source_ref="workflows/delivery_policy.py::DeliveryMode",
            )
            payload.edges.append(edge)
            payload.evidence.append((
                edge.id,
                "workflows/delivery_policy.py::DeliveryMode",
                {"relation": "has_option", "mode": mode.value},
            ))

        for intent in DeliveryIntent:
            payload.nodes.append(
                _arch_node(
                    node_id=f"delivery_intent:{intent.value}",
                    node_type="skill",
                    label=intent.value,
                    source_ref="workflows/delivery_policy.py::DeliveryIntent",
                    arch_kind="delivery_intent",
                    intent_name=intent.value,
                )
            )

        monotonic_ok = self._delivery_lattice_monotonic()
        if not monotonic_ok:
            payload.warnings.append("Delivery policy lattice is not monotonic")

        for mode, intents in _ALLOWED_INTENTS.items():
            for intent in sorted(intents, key=lambda i: i.value):
                edge = _arch_edge(
                    relation="permits",
                    source_node_id=f"delivery_mode:{mode.value}",
                    target_node_id=f"delivery_intent:{intent.value}",
                    source_ref="workflows/delivery_policy.py::_ALLOWED_INTENTS",
                )
                payload.edges.append(edge)
                payload.evidence.append((
                    edge.id,
                    "workflows/delivery_policy.py::_ALLOWED_INTENTS",
                    {"relation": "permits", "mode": mode.value, "intent": intent.value},
                ))

        for flag, targets in FEATURE_FLAG_TARGETS.items():
            for item in targets:
                edge = _arch_edge(
                    relation=item["relation"],
                    source_node_id=f"feature_flag:{flag}",
                    target_node_id=item["target"],
                    source_ref="repo feature manifest",
                    provenance_type="curated_ontology",
                )
                payload.edges.append(edge)
                payload.evidence.append((
                    edge.id,
                    "repo feature manifest",
                    {"relation": item["relation"], "flag": flag, "target": item["target"]},
                ))

        return payload

    def _build_signal_model_payload(self) -> BuildSourcePayload:
        payload = BuildSourcePayload(source_name="signal_model")

        all_signal_types = self._all_known_signal_types()
        payload.warnings.extend(self._signal_coverage_warnings())

        for signal_type in sorted(all_signal_types):
            weight = SIGNAL_WEIGHTS.get(signal_type)
            family = _SIGNAL_TYPE_FAMILIES.get(signal_type)
            payload.nodes.append(
                _arch_node(
                    node_id=f"signal_type:{signal_type}",
                    node_type="signal",
                    label=signal_type,
                    source_ref="verification/verification_gate_v2.py + verification/evidence_families.py",
                    arch_kind="signal_type",
                    signal_type=signal_type,
                    weight=weight,
                    half_life_days=HALF_LIVES.get(signal_type),
                    evidence_family=family or "unknown",
                    is_weighted=signal_type in SIGNAL_WEIGHTS,
                    has_family_mapping=signal_type in _SIGNAL_TYPE_FAMILIES,
                    is_negative_signal=signal_type in NEGATIVE_MULTIPLIERS,
                    is_hard_kill=signal_type in HARD_KILL_SIGNALS,
                )
            )

            # Current static contribution to verification gate.
            if weight is not None or signal_type in NEGATIVE_MULTIPLIERS or signal_type in HARD_KILL_SIGNALS:
                edge = _arch_edge(
                    relation="contributes_to",
                    source_node_id=f"signal_type:{signal_type}",
                    target_node_id="decision_gate:verification_gate",
                    source_ref="verification/verification_gate_v2.py::SIGNAL_WEIGHTS",
                    weight=float(weight or 1.0),
                    signal_weight=weight,
                    half_life_days=HALF_LIVES.get(signal_type),
                    negative_multiplier=NEGATIVE_MULTIPLIERS.get(signal_type),
                    is_hard_kill=signal_type in HARD_KILL_SIGNALS,
                )
                payload.edges.append(edge)
                payload.evidence.append((
                    edge.id,
                    "verification/verification_gate_v2.py::SIGNAL_WEIGHTS",
                    {
                        "relation": "contributes_to",
                        "signal_type": signal_type,
                        "signal_weight": weight,
                        "half_life_days": HALF_LIVES.get(signal_type),
                        "negative_multiplier": NEGATIVE_MULTIPLIERS.get(signal_type),
                        "is_hard_kill": signal_type in HARD_KILL_SIGNALS,
                    },
                ))

            family_node_id = f"ef:{family}" if family in VALID_FAMILIES else "ef:unknown"
            edge = _arch_edge(
                relation="classified_as",
                source_node_id=f"signal_type:{signal_type}",
                target_node_id=family_node_id,
                source_ref="verification/evidence_families.py::_SIGNAL_TYPE_FAMILIES",
            )
            payload.edges.append(edge)
            payload.evidence.append((
                edge.id,
                "verification/evidence_families.py::_SIGNAL_TYPE_FAMILIES",
                {
                    "relation": "classified_as",
                    "signal_type": signal_type,
                    "family": family or "unknown",
                    "override": self._source_override_sources(signal_type),
                },
            ))

        return payload

    def _build_collector_payload(self) -> BuildSourcePayload:
        payload = BuildSourcePayload(source_name="collector_registry")
        collector_names = self._extract_pipeline_collectors()
        known_signal_types = self._all_known_signal_types()

        for collector_name in collector_names:
            collector_id = f"collector:{collector_name}"
            file_rel = SUPPORTED_COLLECTOR_FILES.get(collector_name)
            payload.nodes.append(
                _arch_node(
                    node_id=collector_id,
                    node_type="organization",
                    label=collector_name,
                    source_ref="workflows/pipeline.py::_run_single_collector",
                    arch_kind="collector",
                    collector_name=collector_name,
                    collector_file=file_rel,
                    discovery_method="ast_from_pipeline_dispatch",
                )
            )

            emit_edge = _arch_edge(
                relation="emits_to",
                source_node_id=collector_id,
                target_node_id="pipeline_stage:collectors",
                source_ref="workflows/pipeline.py::_run_collectors_stage",
            )
            payload.edges.append(emit_edge)
            payload.evidence.append((
                emit_edge.id,
                "workflows/pipeline.py::_run_collectors_stage",
                {"relation": "emits_to", "collector": collector_name},
            ))

            signal_types = self._extract_signal_types_for_collector(collector_name, known_signal_types)
            for signal_type in signal_types:
                if signal_type not in known_signal_types:
                    # Defensive: if a collector references a novel signal string, still capture it.
                    payload.nodes.append(
                        _arch_node(
                            node_id=f"signal_type:{signal_type}",
                            node_type="signal",
                            label=signal_type,
                            source_ref=file_rel or "collector source",
                            arch_kind="signal_type",
                            signal_type=signal_type,
                            is_weighted=signal_type in SIGNAL_WEIGHTS,
                            has_family_mapping=signal_type in _SIGNAL_TYPE_FAMILIES,
                        )
                    )
                edge = _arch_edge(
                    relation="produces",
                    source_node_id=collector_id,
                    target_node_id=f"signal_type:{signal_type}",
                    source_ref=file_rel or "collector source",
                )
                payload.edges.append(edge)
                payload.evidence.append((
                    edge.id,
                    file_rel or "collector source",
                    {"relation": "produces", "collector": collector_name, "signal_type": signal_type},
                ))

        return payload

    def _build_activation_payload(self) -> BuildSourcePayload:
        payload = BuildSourcePayload(source_name="activation_policy")

        metrics_seen: set[str] = set()
        for step in sorted(STEP_POLICY):
            policy = STEP_POLICY[step]
            step_id = f"activation_step:{step}"
            payload.nodes.append(
                _arch_node(
                    node_id=step_id,
                    node_type="skill",
                    label=f"Step {step}",
                    source_ref="monitoring/activation_gate.py::STEP_POLICY",
                    arch_kind="activation_step",
                    step=step,
                    policy=policy,
                )
            )

            for metric in list(policy.get("required_spc_metrics", [])) + list(policy.get("optional_spc_metrics", [])):
                if metric not in metrics_seen:
                    metrics_seen.add(metric)
                    payload.nodes.append(
                        _arch_node(
                            node_id=f"spc_metric:{metric}",
                            node_type="skill",
                            label=metric,
                            source_ref="monitoring/activation_gate.py::STEP_POLICY",
                            arch_kind="spc_metric",
                            metric_name=metric,
                        )
                    )

            for metric in policy.get("required_spc_metrics", []):
                edge = _arch_edge(
                    relation="requires",
                    source_node_id=step_id,
                    target_node_id=f"spc_metric:{metric}",
                    source_ref="monitoring/activation_gate.py::STEP_POLICY",
                    required=True,
                )
                payload.edges.append(edge)
                payload.evidence.append((
                    edge.id,
                    "monitoring/activation_gate.py::STEP_POLICY",
                    {"relation": "requires", "step": step, "metric": metric, "required": True},
                ))

            for metric in policy.get("optional_spc_metrics", []):
                edge = _arch_edge(
                    relation="observes",
                    source_node_id=step_id,
                    target_node_id=f"spc_metric:{metric}",
                    source_ref="monitoring/activation_gate.py::STEP_POLICY",
                    required=False,
                )
                payload.edges.append(edge)
                payload.evidence.append((
                    edge.id,
                    "monitoring/activation_gate.py::STEP_POLICY",
                    {"relation": "observes", "step": step, "metric": metric, "required": False},
                ))

            # Mod 2: Use intermediate subphase nodes instead of variant arrays
            for unlock in ACTIVATION_UNLOCKS.get(step, []):
                flag_id = f"feature_flag:{unlock['flag']}"
                subphase = unlock.get("subphase")
                if subphase:
                    sub_id = f"activation_subphase:{subphase}"
                    # Create subphase node (deduplicated across unlocks for same subphase)
                    if not any(n.id == sub_id for n in payload.nodes):
                        payload.nodes.append(_arch_node(
                            node_id=sub_id,
                            node_type="skill",
                            label=f"Step {subphase}",
                            source_ref="docs/runbooks/feature-activation.md",
                            arch_kind="activation_subphase",
                            parent_step=step,
                            subphase_id=subphase,
                        ))
                    # Step -> subphase edge (deduplicated)
                    contains_edge_id = _arch_edge_id("contains_subphase", step_id, sub_id)
                    if not any(e.id == contains_edge_id for e in payload.edges):
                        contains_edge = _arch_edge(
                            relation="contains_subphase",
                            source_node_id=step_id,
                            target_node_id=sub_id,
                            source_ref="docs/runbooks/feature-activation.md",
                            provenance_type="curated_ontology",
                        )
                        payload.edges.append(contains_edge)
                        payload.evidence.append((
                            contains_edge.id,
                            "docs/runbooks/feature-activation.md",
                            {"relation": "contains_subphase", "step": step, "subphase": subphase},
                        ))
                    # Subphase -> flag edge (unique source_node_id avoids collision)
                    edge = _arch_edge(
                        relation="unlocks",
                        source_node_id=sub_id,
                        target_node_id=flag_id,
                        source_ref="docs/runbooks/feature-activation.md",
                        recommended_value=unlock["recommended_value"],
                        provenance_type="curated_ontology",
                    )
                    payload.edges.append(edge)
                    payload.evidence.append((
                        edge.id,
                        "docs/runbooks/feature-activation.md",
                        {
                            "relation": "unlocks",
                            "step": step,
                            "subphase": subphase,
                            "flag": unlock["flag"],
                            "recommended_value": unlock["recommended_value"],
                        },
                    ))
                else:
                    # Direct step -> flag edge (no collision for non-subphased unlocks)
                    edge = _arch_edge(
                        relation="unlocks",
                        source_node_id=step_id,
                        target_node_id=flag_id,
                        source_ref="docs/runbooks/feature-activation.md",
                        recommended_value=unlock["recommended_value"],
                        provenance_type="curated_ontology",
                    )
                    payload.edges.append(edge)
                    payload.evidence.append((
                        edge.id,
                        "docs/runbooks/feature-activation.md",
                        {
                            "relation": "unlocks",
                            "step": step,
                            "flag": unlock["flag"],
                            "recommended_value": unlock["recommended_value"],
                        },
                    ))

        return payload

    # -- extraction helpers --------------------------------------------------

    def _all_known_signal_types(self) -> set[str]:
        types = set(SIGNAL_WEIGHTS)
        types.update(_SIGNAL_TYPE_FAMILIES)
        types.update(HARD_KILL_SIGNALS)
        types.update(NEGATIVE_MULTIPLIERS)
        types.update(KNOWN_SIGNAL_TYPES_EXTRA)
        # Collector docs / source may mention additional emitted types.
        for collector_name in self._extract_pipeline_collectors():
            types.update(self._extract_signal_types_for_collector(collector_name, types))
        return types

    def _extract_pipeline_collectors(self) -> list[str]:
        pipeline_path = self._repo_root / "workflows" / "pipeline.py"
        tree = _read_ast(pipeline_path)
        ordered: list[str] = []
        seen: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "collector_name"
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and isinstance(test.comparators[0].value, str)
            ):
                name = test.comparators[0].value
                if name not in seen:
                    ordered.append(name)
                    seen.add(name)
        return ordered

    def _collector_count_hint(self) -> int:
        return len(self._extract_pipeline_collectors())

    def _extract_signal_types_for_collector(
        self,
        collector_name: str,
        known_signal_types: Iterable[str],
    ) -> list[str]:
        rel = SUPPORTED_COLLECTOR_FILES.get(collector_name)
        if not rel:
            return []
        path = self._repo_root / rel
        if not path.exists():
            return []
        tree = _read_ast(path)
        extractor = _SignalTypeLiteralExtractor()
        extractor.visit(tree)

        # Keep a tiny compatibility nudge for callers still passing the known set:
        # return all extracted signal identifiers, with any already-known values
        # retaining deterministic lexical order alongside novel strings.
        _ = set(known_signal_types)
        return extractor.extracted()

    def _signal_coverage_warnings(self) -> list[str]:
        warnings: list[str] = []
        weights_without_family = sorted(set(SIGNAL_WEIGHTS) - set(_SIGNAL_TYPE_FAMILIES))
        families_without_weight = sorted(set(_SIGNAL_TYPE_FAMILIES) - set(SIGNAL_WEIGHTS))
        if weights_without_family:
            warnings.append(
                "SIGNAL_WEIGHTS missing evidence-family mapping for: "
                + ", ".join(weights_without_family)
            )
        if families_without_weight:
            warnings.append(
                "Evidence-family mapping missing weights for: "
                + ", ".join(families_without_weight)
            )
        return warnings

    def _delivery_lattice_monotonic(self) -> bool:
        ordered_modes = [
            DeliveryMode.STAGING_ONLY,
            DeliveryMode.MANUAL_PUBLISH,
            DeliveryMode.BATCH_PUBLISH,
            DeliveryMode.AUTO_PUBLISH,
        ]
        previous: set[DeliveryIntent] = set()
        for mode in ordered_modes:
            current = set(_ALLOWED_INTENTS[mode])
            if not previous.issubset(current):
                return False
            previous = current
        return True

    def _source_override_sources(self, signal_type: str) -> list[str]:
        return sorted(source_api for (stype, source_api), _family in _SOURCE_API_OVERRIDES.items() if stype == signal_type)

    def _ontology_drift_warnings(self) -> list[str]:
        """Check curated manifests for drift against code-extracted constants."""
        warnings: list[str] = []

        # 1. ACTIVATION_UNLOCKS steps vs STEP_POLICY keys
        unlock_steps = set(ACTIVATION_UNLOCKS.keys())
        policy_steps = set(STEP_POLICY.keys())
        missing_in_policy = unlock_steps - policy_steps
        if missing_in_policy:
            warnings.append(
                f"ACTIVATION_UNLOCKS references steps not in STEP_POLICY: {sorted(missing_in_policy)}"
            )

        # 2. SUPPORTED_COLLECTOR_FILES vs AST-extracted collectors
        ast_collectors = set(self._extract_pipeline_collectors())
        map_collectors = set(SUPPORTED_COLLECTOR_FILES.keys())
        in_ast_not_map = ast_collectors - map_collectors
        if in_ast_not_map:
            warnings.append(
                f"Collectors found in pipeline AST but missing from SUPPORTED_COLLECTOR_FILES: {sorted(in_ast_not_map)}"
            )

        # 3. Curated vs extracted edge ratio
        all_edges: list[dict[str, Any]] = []
        for payload_fn in [
            self._build_core_architecture_payload,
            self._build_feature_payload,
            self._build_activation_payload,
        ]:
            p = payload_fn()
            for e in p.edges:
                all_edges.append(e.properties or {})
        curated = sum(1 for e in all_edges if e.get("provenance_type") == "curated_ontology")
        extracted = sum(1 for e in all_edges if e.get("provenance_type") == "code_extracted")
        if curated + extracted > 0:
            warnings.append(
                f"Ontology edge provenance: {curated} curated, {extracted} code-extracted"
            )

        return warnings

    # -- stale cleanup -------------------------------------------------------

    async def _tombstone_stale_nodes(self, run_id: str, current_node_ids: set[str]) -> int:
        existing = await self._kg.list_live_node_ids_by_source(ARCH_SOURCE_TABLE)
        stale = sorted(existing - current_node_ids)
        count = 0
        for node_id in stale:
            changed = await self._kg.tombstone_node(node_id, run_id=run_id)
            if changed:
                count += 1
                await self._kg.log_provenance(
                    run_id,
                    "tombstone",
                    node_id=node_id,
                    detail={"layer": ARCH_LAYER, "reason": "architecture_full_recompute"},
                )
        return count

    async def _expire_stale_edges(self, run_id: str, current_edge_ids: set[str]) -> int:
        existing = await self._kg.list_live_edge_ids_by_layer(ARCH_LAYER)
        stale = sorted(existing - current_edge_ids)
        count = 0
        for edge_id in stale:
            changed = await self._kg.expire_edge(edge_id, run_id=run_id)
            if changed:
                count += 1
                await self._kg.log_provenance(
                    run_id,
                    "expire",
                    edge_id=edge_id,
                    detail={"layer": ARCH_LAYER, "reason": "architecture_full_recompute"},
                )
        return count
