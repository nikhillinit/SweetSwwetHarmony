"""
Capability router for local skills, agents, and plugins.

Builds a bounded manifest from known asset roots, then recommends a small set
of relevant capabilities for a task. Plugin inventory is intentionally derived
from installed plugin manifests instead of recursively walking cache contents.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


CapabilityKind = Literal["skill", "agent", "plugin"]
CapabilitySource = Literal["user", "repo", "plugin"]
InvocationMode = Literal["skill_context", "agent_prompt", "plugin_command"]
ExecutionDisposition = Literal[
    "auto_applied",
    "prepared",
    "approved_manual_invoke",
    "manual_review_required",
    "blocked",
]

_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "build", "for", "from", "in",
    "into", "is", "it", "need", "of", "on", "or", "plus", "that", "the",
    "this", "to", "use", "with",
})
_SKILL_FILE_EXCLUDES = frozenset({"README.md", "CONTRIBUTING.md"})
_AGENT_FILE_EXCLUDES = frozenset({
    "README.md",
    "CONTRIBUTING.md",
    "QUICKSTART.md",
    "EXECUTIVE-BRIEF.md",
})
_AGENT_DIR_EXCLUDES = frozenset({
    "examples",
    "integrations",
    "strategy",
    "coordination",
    "playbooks",
    "runbooks",
})
_DIR_EXCLUDES = frozenset({".pytest_cache", "__pycache__", "node_modules"})
_QUERY_EXPANSIONS = {
    "kg": {"knowledge", "graph"},
    "db": {"database"},
    "ui": {"interface", "frontend"},
    "ux": {"experience"},
}
_SAFE_PLUGIN_COMMANDS = frozenset({"help", "plan", "observe"})


@dataclass(frozen=True)
class CapabilityRoot:
    kind: CapabilityKind
    source: CapabilitySource
    path: Path


@dataclass
class CapabilityAsset:
    id: str
    kind: CapabilityKind
    source: CapabilitySource
    name: str
    path: str
    category: str | None = None
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityAsset":
        return cls(**data)


@dataclass
class CapabilityManifest:
    manifest_version: int
    scanned_at: str
    roots: list[str]
    counts: dict[str, int]
    assets: list[CapabilityAsset]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "scanned_at": self.scanned_at,
            "roots": list(self.roots),
            "counts": dict(self.counts),
            "assets": [asset.to_dict() for asset in self.assets],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityManifest":
        return cls(
            manifest_version=int(data["manifest_version"]),
            scanned_at=data["scanned_at"],
            roots=list(data["roots"]),
            counts=dict(data["counts"]),
            assets=[CapabilityAsset.from_dict(item) for item in data["assets"]],
        )


@dataclass
class RecommendationItem:
    asset: CapabilityAsset
    score: float
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset.to_dict(),
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
        }


@dataclass
class CapabilityRecommendation:
    task: str
    skills: list[RecommendationItem]
    agents: list[RecommendationItem]
    plugins: list[RecommendationItem]
    notes: list[str]
    policy: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "skills": [item.to_dict() for item in self.skills],
            "agents": [item.to_dict() for item in self.agents],
            "plugins": [item.to_dict() for item in self.plugins],
            "notes": list(self.notes),
            "policy": dict(self.policy),
        }


@dataclass
class PluginCommandInvocation:
    plugin_name: str
    command_name: str
    invocation: str
    path: str
    description: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_name": self.plugin_name,
            "command_name": self.command_name,
            "invocation": self.invocation,
            "path": self.path,
            "description": self.description,
            "allowed_tools": list(self.allowed_tools),
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
        }


@dataclass
class InvokedAsset:
    asset: CapabilityAsset
    mode: InvocationMode
    score: float
    reasons: list[str]
    content: str | None = None
    content_path: str | None = None
    command: PluginCommandInvocation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset.to_dict(),
            "mode": self.mode,
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
            "content": self.content,
            "content_path": self.content_path,
            "command": self.command.to_dict() if self.command else None,
        }


@dataclass
class CapabilityInvocationBundle:
    task: str
    prompt: str
    skills: list[InvokedAsset]
    agents: list[InvokedAsset]
    plugins: list[InvokedAsset]
    notes: list[str]
    execution_brief: str
    policy: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "prompt": self.prompt,
            "skills": [item.to_dict() for item in self.skills],
            "agents": [item.to_dict() for item in self.agents],
            "plugins": [item.to_dict() for item in self.plugins],
            "notes": list(self.notes),
            "execution_brief": self.execution_brief,
            "policy": dict(self.policy),
        }


@dataclass
class ExecutionAction:
    item: InvokedAsset
    disposition: ExecutionDisposition
    runnable: bool
    next_step: str | None = None
    policy_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item.to_dict(),
            "disposition": self.disposition,
            "runnable": self.runnable,
            "next_step": self.next_step,
            "policy_notes": list(self.policy_notes),
        }


@dataclass
class CapabilityExecutionPlan:
    task: str
    prompt: str
    skill_actions: list[ExecutionAction]
    agent_actions: list[ExecutionAction]
    plugin_actions: list[ExecutionAction]
    notes: list[str]
    execution_brief: str
    policy: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "prompt": self.prompt,
            "skill_actions": [item.to_dict() for item in self.skill_actions],
            "agent_actions": [item.to_dict() for item in self.agent_actions],
            "plugin_actions": [item.to_dict() for item in self.plugin_actions],
            "notes": list(self.notes),
            "execution_brief": self.execution_brief,
            "policy": dict(self.policy),
        }


def default_capability_roots(
    *,
    user_home: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[CapabilityRoot, ...]:
    """Return default roots for user-global and repo-local capability assets."""
    if user_home is None:
        user_home = Path.home()
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[1]

    user_claude = user_home / ".claude"
    repo_claude = repo_root / ".claude"
    return (
        CapabilityRoot("agent", "user", user_claude / "agents"),
        CapabilityRoot("plugin", "plugin", user_claude / "plugins"),
        CapabilityRoot("skill", "user", user_claude / "skills"),
        CapabilityRoot("skill", "repo", repo_claude / "skills"),
        CapabilityRoot("agent", "repo", repo_claude / "agents"),
    )


def build_capability_manifest(
    roots: tuple[CapabilityRoot, ...] | None = None,
) -> CapabilityManifest:
    """Inventory skills, agents, and plugins from the configured roots."""
    if roots is None:
        roots = default_capability_roots()

    assets: list[CapabilityAsset] = []
    for root in roots:
        if not root.path.exists():
            continue
        if root.kind == "skill":
            assets.extend(_collect_skills(root))
        elif root.kind == "agent":
            assets.extend(_collect_agents(root))
        elif root.kind == "plugin":
            assets.extend(_collect_plugins(root))

    counts = {
        "skill": sum(1 for asset in assets if asset.kind == "skill"),
        "agent": sum(1 for asset in assets if asset.kind == "agent"),
        "plugin": sum(1 for asset in assets if asset.kind == "plugin"),
    }
    return CapabilityManifest(
        manifest_version=1,
        scanned_at=datetime.now(timezone.utc).isoformat(),
        roots=[str(root.path) for root in roots],
        counts=counts,
        assets=sorted(assets, key=lambda asset: (asset.kind, asset.source, asset.name.lower())),
    )


def write_manifest(path: str | Path, manifest: CapabilityManifest) -> None:
    """Persist a manifest as JSON."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")


def load_manifest(path: str | Path) -> CapabilityManifest:
    """Load a manifest from JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return CapabilityManifest.from_dict(data)


def recommend_capabilities(
    task: str,
    manifest: CapabilityManifest,
    *,
    max_skills: int = 3,
    max_agents: int = 2,
    max_plugins: int = 2,
) -> CapabilityRecommendation:
    """Recommend a small set of relevant capabilities for a task."""
    task_text = task.strip().lower()
    task_tokens = _expand_query_tokens(_tokenize(task_text))

    ranked: list[RecommendationItem] = []
    for asset in manifest.assets:
        score, reasons = _score_asset(task_text, task_tokens, asset)
        if score <= 0:
            continue
        ranked.append(RecommendationItem(asset=asset, score=score, reasons=reasons))

    deduped = _dedupe_ranked_assets(ranked)
    skills = _take_kind(deduped, "skill", max_skills)
    agents = _take_kind(deduped, "agent", max_agents)
    plugins = [item for item in _take_kind(deduped, "plugin", max_plugins) if item.score >= 2.5]

    notes = [
        "Repo-local assets outrank user-global assets when names overlap.",
        "Plugins are recommend-only in this router; execution still needs a separate allow/policy gate.",
        "Plugin inventory is bounded to installed plugin manifests and plugin.json entrypoints.",
    ]
    policy = {
        "max_skills": max_skills,
        "max_agents": max_agents,
        "max_plugins": max_plugins,
        "plugin_execution_mode": "recommend_only",
    }
    return CapabilityRecommendation(
        task=task,
        skills=skills,
        agents=agents,
        plugins=plugins,
        notes=notes,
        policy=policy,
    )


def invoke_capabilities(
    task: str,
    prompt: str,
    manifest: CapabilityManifest,
    *,
    max_skills: int = 3,
    max_agents: int = 2,
    max_plugins: int = 2,
    max_content_chars: int = 4000,
) -> CapabilityInvocationBundle:
    """
    Materialize selected assets into an invocation bundle.

    This does not auto-run plugins or spawn agents. Instead it:
    - loads selected skill content as execution context
    - loads selected agent definitions as delegate prompts
    - derives plugin command invocations from the plugin's own command docs
    """
    recommendation = recommend_capabilities(
        task,
        manifest,
        max_skills=max_skills,
        max_agents=max_agents,
        max_plugins=max_plugins,
    )
    combined_text = f"{task}\n{prompt}".strip()

    skills = [
        _materialize_text_asset(item, "skill_context", max_content_chars=max_content_chars)
        for item in recommendation.skills
    ]
    agents = [
        _materialize_text_asset(item, "agent_prompt", max_content_chars=max_content_chars)
        for item in recommendation.agents
    ]
    plugins = [
        _materialize_plugin_asset(item, combined_text)
        for item in recommendation.plugins
    ]

    execution_brief = _build_execution_brief(task, prompt, skills, agents, plugins)
    notes = list(recommendation.notes)
    notes.append("Invocation bundle is actionable context, not autonomous execution.")
    policy = dict(recommendation.policy)
    policy["invocation_mode"] = "materialize_context_and_command_suggestions"
    policy["max_content_chars"] = max_content_chars

    return CapabilityInvocationBundle(
        task=task,
        prompt=prompt,
        skills=skills,
        agents=agents,
        plugins=plugins,
        notes=notes,
        execution_brief=execution_brief,
        policy=policy,
    )


def execute_capabilities(
    task: str,
    prompt: str,
    manifest: CapabilityManifest,
    *,
    max_skills: int = 3,
    max_agents: int = 2,
    max_plugins: int = 2,
    max_content_chars: int = 4000,
    allow_plugins: list[str] | None = None,
    allow_plugin_commands: list[str] | None = None,
    allow_plugin_tools: list[str] | None = None,
    allow_all_plugins: bool = False,
    allow_all_plugin_tools: bool = False,
) -> CapabilityExecutionPlan:
    """
    Produce a policy-vetted execution plan from selected capabilities.

    This applies low-risk assets directly as context and prompt material, while
    classifying plugin commands as approved, review-required, or blocked based
    on command allowlists and declared tool requirements.
    """
    bundle = invoke_capabilities(
        task,
        prompt,
        manifest,
        max_skills=max_skills,
        max_agents=max_agents,
        max_plugins=max_plugins,
        max_content_chars=max_content_chars,
    )

    plugin_allow = {value.strip().lower() for value in (allow_plugins or []) if value.strip()}
    command_allow = {
        value.strip().lower() for value in (allow_plugin_commands or []) if value.strip()
    }
    tool_allow = {value.strip().lower() for value in (allow_plugin_tools or []) if value.strip()}

    skill_actions = [
        ExecutionAction(
            item=item,
            disposition="auto_applied",
            runnable=False,
            next_step=f"Load skill context from {item.content_path}",
            policy_notes=["Skill content is safe to auto-apply as local working context."],
        )
        for item in bundle.skills
    ]
    agent_actions = [
        ExecutionAction(
            item=item,
            disposition="prepared",
            runnable=False,
            next_step=f"Use {item.asset.name} as a delegation prompt if delegation is warranted.",
            policy_notes=["Agent prompts are prepared but not auto-spawned by this router."],
        )
        for item in bundle.agents
    ]
    plugin_actions = [
        _plan_plugin_action(
            item,
            allow_all_plugins=allow_all_plugins,
            allow_all_plugin_tools=allow_all_plugin_tools,
            allowed_plugins=plugin_allow,
            allowed_commands=command_allow,
            allowed_tools=tool_allow,
        )
        for item in bundle.plugins
    ]

    notes = [
        note
        for note in bundle.notes
        if "Plugins are recommend-only in this router" not in note
    ]
    notes.append(
        "Plugin commands are policy-vetted here and never auto-run; approved entries are manual invocations."
    )
    policy = dict(bundle.policy)
    policy.update(
        {
            "execution_mode": "policy_vetted",
            "plugin_execution_mode": "manual_review_or_blocked",
            "plugin_identity_contract": "CapabilityAsset.id",
            "plugin_identity_format": "plugin:{plugin_id}",
            "plugin_command_identity_format": "plugin:{plugin_id}:{command_name}",
            "allow_all_plugins": allow_all_plugins,
            "allow_all_plugin_tools": allow_all_plugin_tools,
            "allowed_plugins": sorted(plugin_allow),
            "allowed_plugin_commands": sorted(command_allow),
            "allowed_plugin_tools": sorted(tool_allow),
            "safe_plugin_commands": sorted(_SAFE_PLUGIN_COMMANDS),
        }
    )

    return CapabilityExecutionPlan(
        task=task,
        prompt=prompt,
        skill_actions=skill_actions,
        agent_actions=agent_actions,
        plugin_actions=plugin_actions,
        notes=notes,
        execution_brief=bundle.execution_brief,
        policy=policy,
    )


def _collect_skills(root: CapabilityRoot) -> list[CapabilityAsset]:
    assets: list[CapabilityAsset] = []

    for path in sorted(root.path.glob("*.md")):
        if path.name in _SKILL_FILE_EXCLUDES:
            continue
        assets.append(_markdown_asset(path, kind="skill", source=root.source, root_path=root.path))

    for skill_file in sorted(root.path.rglob("SKILL.md")):
        rel_parts = skill_file.relative_to(root.path).parts
        if any(part in _DIR_EXCLUDES for part in rel_parts):
            continue
        assets.append(_markdown_asset(skill_file, kind="skill", source=root.source, root_path=root.path))

    return assets


def _collect_agents(root: CapabilityRoot) -> list[CapabilityAsset]:
    assets: list[CapabilityAsset] = []
    for path in sorted(root.path.rglob("*.md")):
        rel_parts = path.relative_to(root.path).parts
        if path.name in _AGENT_FILE_EXCLUDES:
            continue
        if any(part in _AGENT_DIR_EXCLUDES for part in rel_parts[:-1]):
            continue
        if any(part in _DIR_EXCLUDES for part in rel_parts[:-1]):
            continue
        assets.append(_markdown_asset(path, kind="agent", source=root.source, root_path=root.path))
    return assets


def _collect_plugins(root: CapabilityRoot) -> list[CapabilityAsset]:
    assets: list[CapabilityAsset] = []
    installed_manifest = root.path / "installed_plugins.json"
    plugin_records: dict[str, dict[str, Any]] = {}
    if installed_manifest.exists():
        plugin_records = _load_json(installed_manifest).get("plugins", {})

    for plugin_id, installs in sorted(plugin_records.items()):
        install = _select_latest_install(installs)
        install_path = Path(install.get("installPath", ""))
        plugin_json = install_path / "plugin.json"
        plugin_meta = _load_json(plugin_json) if plugin_json.exists() else {}
        assets.append(
            CapabilityAsset(
                id=f"plugin:{plugin_id}",
                kind="plugin",
                source=root.source,
                name=str(plugin_meta.get("name") or plugin_id.split("@", 1)[0]),
                path=str(plugin_json if plugin_json.exists() else install_path),
                category=_plugin_marketplace(plugin_id),
                summary=plugin_meta.get("description"),
                tags=_unique_strings(
                    [*_as_list(plugin_meta.get("keywords")), _plugin_marketplace(plugin_id)]
                ),
                triggers=_unique_strings(
                    [
                        plugin_id.replace("@", " "),
                        plugin_meta.get("name"),
                        *[skill.get("name") for skill in _as_list(plugin_meta.get("skills")) if isinstance(skill, dict)],
                        *_as_list(plugin_meta.get("keywords")),
                    ]
                ),
                metadata={
                    "plugin_id": plugin_id,
                    "version": install.get("version"),
                    "scope": install.get("scope"),
                    "install_path": str(install_path),
                    "author": plugin_meta.get("author"),
                    "commands": [cmd.get("name", "") for cmd in _as_list(plugin_meta.get("commands")) if isinstance(cmd, dict)],
                    "hooks": sorted((plugin_meta.get("hooks") or {}).keys()),
                    "skills": [skill.get("name", "") for skill in _as_list(plugin_meta.get("skills")) if isinstance(skill, dict)],
                    "installed": True,
                },
            )
        )

    return assets


def _markdown_asset(
    path: Path,
    *,
    kind: CapabilityKind,
    source: CapabilitySource,
    root_path: Path,
) -> CapabilityAsset:
    name, summary = _extract_markdown_metadata(path)
    rel_path = path.relative_to(root_path)
    category = rel_path.parts[0] if len(rel_path.parts) > 1 else "general"
    path_phrase = (
        rel_path.parent.name.replace("-", " ").replace("_", " ")
        if path.name == "SKILL.md"
        else path.stem.replace("-", " ").replace("_", " ")
    )
    triggers = _unique_strings([name, path.stem.replace("-", " ").replace("_", " "), category])
    triggers = _unique_strings([name, path_phrase, category])
    tags = _unique_strings(
        [category, *[part for part in rel_path.parts[:-1] if part not in _DIR_EXCLUDES]]
    )
    asset_name = name or path.stem
    return CapabilityAsset(
        id=f"{kind}:{source}:{_slug(asset_name)}",
        kind=kind,
        source=source,
        name=asset_name,
        path=str(path),
        category=category,
        summary=summary,
        tags=tags,
        triggers=triggers,
        metadata={"relative_path": str(rel_path)},
    )


def _extract_markdown_metadata(path: Path) -> tuple[str, str | None]:
    entry = _read_markdown_entry(path)
    name = entry["name"]
    description = entry["summary"]
    return name, description


def _read_markdown_entry(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter = _parse_frontmatter(text)
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not name:
        heading_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        if heading_match:
            name = heading_match.group(1).strip()
    if not description:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped == "---":
                continue
            description = stripped
            break
    body = _strip_frontmatter(text).strip()
    return {
        "name": name or path.stem,
        "summary": description,
        "frontmatter": frontmatter,
        "body": body,
        "text": text,
    }


def _select_latest_install(installs: Any) -> dict[str, Any]:
    install_list = [item for item in installs if isinstance(item, dict)]
    if not install_list:
        return {}
    install_list.sort(
        key=lambda item: item.get("lastUpdated") or item.get("installedAt") or "",
    )
    return install_list[-1]


def _plugin_marketplace(plugin_id: str) -> str:
    return plugin_id.split("@", 1)[1] if "@" in plugin_id else "unknown"


def _score_asset(
    task_text: str,
    task_tokens: set[str],
    asset: CapabilityAsset,
) -> tuple[float, list[str]]:
    haystack_parts = [
        asset.name,
        asset.summary or "",
        asset.category or "",
        " ".join(asset.tags),
        " ".join(asset.triggers),
        " ".join(_flatten_metadata_strings(asset.metadata)),
    ]
    haystack_text = " ".join(part for part in haystack_parts if part).lower()
    haystack_tokens = _tokenize(haystack_text)
    name_tokens = _tokenize(asset.name)

    score = 0.0
    reasons: list[str] = []

    if asset.name.lower() in task_text:
        score += 8.0
        reasons.append("explicit name match")

    phrase_hits = []
    for trigger in asset.triggers:
        trigger_lc = trigger.lower().strip()
        if len(trigger_lc) < 4:
            continue
        if " " in trigger_lc and trigger_lc in task_text:
            score += 4.0
            phrase_hits.append(trigger)
    if phrase_hits:
        reasons.append(f"matched phrases: {', '.join(phrase_hits[:3])}")

    token_hits = sorted(task_tokens & haystack_tokens)
    if token_hits:
        score += len(token_hits) * 1.5
        reasons.append(f"matched tokens: {', '.join(token_hits[:5])}")

    name_hits = sorted(task_tokens & name_tokens)
    if name_hits:
        score += len(name_hits) * 2.0
        reasons.append("name/category overlap")

    if asset.source == "repo":
        score += 1.0
    if asset.kind == "plugin":
        score -= 0.25

    return score, reasons


def _dedupe_ranked_assets(items: list[RecommendationItem]) -> list[RecommendationItem]:
    best: dict[tuple[str, str], RecommendationItem] = {}
    for item in sorted(items, key=lambda entry: entry.score, reverse=True):
        key = _dedupe_key(item.asset)
        current = best.get(key)
        if current is None or _is_better(item, current):
            best[key] = item
    return sorted(best.values(), key=lambda entry: entry.score, reverse=True)


def _dedupe_key(asset: CapabilityAsset) -> tuple[str, str]:
    if asset.kind == "plugin":
        return (asset.kind, asset.id.lower())
    return (asset.kind, _slug(asset.name))


def _is_better(candidate: RecommendationItem, current: RecommendationItem) -> bool:
    if candidate.score != current.score:
        return candidate.score > current.score
    if candidate.asset.source != current.asset.source:
        return candidate.asset.source == "repo"
    return candidate.asset.path < current.asset.path


def _take_kind(
    items: list[RecommendationItem],
    kind: CapabilityKind,
    limit: int,
) -> list[RecommendationItem]:
    return [item for item in items if item.asset.kind == kind][:limit]


def _flatten_metadata_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_flatten_metadata_strings(item))
        return strings
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(_flatten_metadata_strings(item))
        return strings
    return []


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return {token for token in tokens if len(token) > 1 and token not in _STOPWORDS}


def _expand_query_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in list(tokens):
        expanded.update(_QUERY_EXPANSIONS.get(token, set()))
    return expanded


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower()))


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(cleaned)
    return result


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _parse_frontmatter(text: str) -> dict[str, str]:
    frontmatter: dict[str, str] = {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        return frontmatter

    lines = match.group(1).splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if ":" not in line or line.startswith((" ", "\t")):
            index += 1
            continue
        key, value = line.split(":", 1)
        cleaned_value = value.strip()
        if cleaned_value in {">", "|"}:
            block: list[str] = []
            index += 1
            while index < len(lines):
                next_line = lines[index]
                if next_line.startswith((" ", "\t")) or not next_line.strip():
                    if next_line.strip():
                        block.append(next_line.strip())
                    index += 1
                    continue
                break
            frontmatter[key.strip().lower()] = " ".join(block).strip()
            continue
        frontmatter[key.strip().lower()] = cleaned_value
        index += 1
    return frontmatter


def _strip_frontmatter(text: str) -> str:
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)


def _materialize_text_asset(
    item: RecommendationItem,
    mode: InvocationMode,
    *,
    max_content_chars: int,
) -> InvokedAsset:
    path = Path(item.asset.path)
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > max_content_chars:
        content = content[:max_content_chars].rstrip() + "\n...[truncated]"
    return InvokedAsset(
        asset=item.asset,
        mode=mode,
        score=item.score,
        reasons=list(item.reasons),
        content=content,
        content_path=str(path),
    )


def _materialize_plugin_asset(
    item: RecommendationItem,
    task_and_prompt: str,
) -> InvokedAsset:
    command = _select_plugin_command(item.asset, task_and_prompt)
    return InvokedAsset(
        asset=item.asset,
        mode="plugin_command",
        score=item.score,
        reasons=list(item.reasons),
        content_path=item.asset.path,
        command=command,
    )


def _select_plugin_command(
    asset: CapabilityAsset,
    task_and_prompt: str,
) -> PluginCommandInvocation | None:
    install_path = Path(str(asset.metadata.get("install_path", "")))
    commands_dir = install_path / "commands"
    if not commands_dir.exists():
        return None

    task_text = task_and_prompt.lower()
    task_tokens = _expand_query_tokens(_tokenize(task_text))
    best: PluginCommandInvocation | None = None

    for command_path in sorted(commands_dir.glob("*.md")):
        entry = _read_markdown_entry(command_path)
        frontmatter = entry["frontmatter"]
        description = entry["summary"]
        command_name = command_path.stem
        score, reasons = _score_command(
            task_text,
            task_tokens,
            asset.name,
            command_name,
            description or "",
            entry["body"],
        )
        invocation = f"/{asset.name}:{command_name} {task_and_prompt}".strip()
        command = PluginCommandInvocation(
            plugin_name=asset.name,
            command_name=command_name,
            invocation=invocation,
            path=str(command_path),
            description=description,
            allowed_tools=_split_csv(frontmatter.get("allowed-tools")),
            score=score,
            reasons=reasons,
        )
        if best is None or command.score > best.score:
            best = command

    return best


def _score_command(
    task_text: str,
    task_tokens: set[str],
    plugin_name: str,
    command_name: str,
    description: str,
    body: str,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    haystack = f"{plugin_name} {command_name} {description} {body[:1200]}".lower()
    haystack_tokens = _expand_query_tokens(_tokenize(haystack))

    command_phrase = f"{plugin_name} {command_name}".lower()
    if command_name.lower() in task_text or command_phrase in task_text:
        score += 6.0
        reasons.append("explicit command match")

    token_hits = sorted(task_tokens & haystack_tokens)
    if token_hits:
        score += len(token_hits) * 1.25
        reasons.append(f"matched tokens: {', '.join(token_hits[:5])}")

    # Nudge toward planning/orchestration commands when the task sounds operational.
    if command_name in {"plan", "call", "observe"} and {"workflow", "orchestration"} & task_tokens:
        score += 2.0
        reasons.append("workflow-oriented command")

    return score, reasons


def _build_execution_brief(
    task: str,
    prompt: str,
    skills: list[InvokedAsset],
    agents: list[InvokedAsset],
    plugins: list[InvokedAsset],
) -> str:
    lines = [
        f"Task: {task}",
        f"Prompt: {prompt}",
        "",
        "Execution guidance:",
    ]
    if skills:
        lines.append("Load these skills as working context first:")
        for item in skills:
            lines.append(f"- {item.asset.name} ({item.content_path})")
    if agents:
        lines.append("Use these agent prompts when delegation is warranted:")
        for item in agents:
            lines.append(f"- {item.asset.name} ({item.content_path})")
    if plugins:
        lines.append("Suggested plugin invocations:")
        for item in plugins:
            if item.command:
                lines.append(f"- {item.command.invocation}")
    return "\n".join(lines)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _plan_plugin_action(
    item: InvokedAsset,
    *,
    allow_all_plugins: bool,
    allow_all_plugin_tools: bool,
    allowed_plugins: set[str],
    allowed_commands: set[str],
    allowed_tools: set[str],
) -> ExecutionAction:
    if item.command is None:
        return ExecutionAction(
            item=item,
            disposition="blocked",
            runnable=False,
            next_step="No command documentation was found for this plugin.",
            policy_notes=["Plugin was selected, but no command entrypoint could be materialized."],
        )

    plugin_identity = item.asset.id.lower()
    plugin_name = item.asset.name.lower()
    command_name = item.command.command_name.lower()
    plugin_command_key = f"{plugin_identity}:{command_name}"

    plugin_allowed = allow_all_plugins or plugin_identity in allowed_plugins
    command_allowed = plugin_command_key in allowed_commands
    safe_by_default = command_name in _SAFE_PLUGIN_COMMANDS

    if not (plugin_allowed or command_allowed or safe_by_default):
        return ExecutionAction(
            item=item,
            disposition="blocked",
            runnable=False,
            next_step=f"Add {plugin_command_key} to the allowlist before invoking it.",
            policy_notes=[
                "Plugin command is not in the safe default set and was not explicitly allowlisted by canonical plugin identity."
            ],
        )

    command_tools = [tool.strip() for tool in item.command.allowed_tools if tool.strip()]
    normalized_tools = {tool.lower() for tool in command_tools}
    if command_tools and not allow_all_plugin_tools:
        missing_tools = sorted(normalized_tools - allowed_tools)
        if missing_tools:
            return ExecutionAction(
                item=item,
                disposition="manual_review_required",
                runnable=False,
                next_step=f"Review tool requirements for {plugin_command_key} before invoking it.",
                policy_notes=[
                    "Plugin command is otherwise allowed, but its declared tools exceed the approved tool set.",
                    f"Missing tool approvals: {', '.join(missing_tools)}",
                ],
            )

    approval_basis = "safe default command"
    if command_allowed:
        approval_basis = "explicit command allowlist"
    elif plugin_allowed:
        approval_basis = "explicit plugin allowlist"

    return ExecutionAction(
        item=item,
        disposition="approved_manual_invoke",
        runnable=False,
        next_step=item.command.invocation,
        policy_notes=[
            f"Approved for manual invocation via {approval_basis}.",
            "This router does not execute plugin slash commands directly.",
        ],
    )
