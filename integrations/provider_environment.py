"""Provider-scoped child environments for CLI-backed model execution.

Child processes receive a fresh allowlisted mapping.  Provider credentials are
selected by provider identity; non-model credentials require an explicit typed
tool capability.  Prompt text is intentionally absent from this API and cannot
grant credential access.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("provider-environment")


class ProviderIdentity(str, Enum):
    CODEX = "codex"
    KIMI = "kimi"
    GEMINI = "gemini"
    ANTIGRAVITY = "antigravity"


class ToolCapability(str, Enum):
    """Non-model service/tool credentials a task may explicitly request."""

    GITHUB = "github"
    NOTION = "notion"
    MCP = "mcp"
    NETWORK_PROXY = "network_proxy"
    CUSTOM_CA = "custom_ca"


@dataclass(frozen=True, slots=True)
class ChildExecutionContext:
    """Machine-readable credential capabilities for one wrapper invocation."""

    tool_capabilities: frozenset[ToolCapability] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.tool_capabilities, frozenset) or any(
            not isinstance(capability, ToolCapability)
            for capability in self.tool_capabilities
        ):
            raise TypeError("tool_capabilities must contain only ToolCapability values")


_PROCESS_ENV_KEYS = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "COLORTERM",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
    }
)

_PROVIDER_CREDENTIAL_KEYS: dict[ProviderIdentity, frozenset[str]] = {
    ProviderIdentity.CODEX: frozenset(
        {"OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"}
    ),
    ProviderIdentity.KIMI: frozenset({"KIMI_API_KEY", "MOONSHOT_API_KEY"}),
    ProviderIdentity.GEMINI: frozenset(
        {"GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"}
    ),
    ProviderIdentity.ANTIGRAVITY: frozenset(
        {"GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"}
    ),
}

_PROVIDER_CONFIG_KEYS: dict[ProviderIdentity, frozenset[str]] = {
    ProviderIdentity.CODEX: frozenset(
        {
            "CODEX_HOME",
            "CODEX_MODEL",
            "CODEX_APPROVAL",
            "CODEX_MANAGED_BY_NPM",
            "CODEX_MANAGED_PACKAGE_ROOT",
            "OPENAI_BASE_URL",
        }
    ),
    ProviderIdentity.KIMI: frozenset(
        {"KIMI_HOME", "KIMI_CONFIG_DIR", "KIMI_BASE_URL", "MOONSHOT_BASE_URL"}
    ),
    ProviderIdentity.GEMINI: frozenset(
        {"GEMINI_CLI_HOME", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"}
    ),
    ProviderIdentity.ANTIGRAVITY: frozenset(
        {"AGY_HOME", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"}
    ),
}

_CAPABILITY_ENV_KEYS: dict[ToolCapability, frozenset[str]] = {
    ToolCapability.GITHUB: frozenset({"GITHUB_TOKEN", "GH_TOKEN"}),
    ToolCapability.NOTION: frozenset({"NOTION_API_KEY", "NOTION_DATABASE_ID"}),
    ToolCapability.MCP: frozenset({"MCP_API_KEY", "MCP_TOKEN", "MCP_SERVER_URL"}),
    ToolCapability.NETWORK_PROXY: frozenset(
        {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        }
    ),
    ToolCapability.CUSTOM_CA: frozenset(
        {
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "NODE_EXTRA_CA_CERTS",
        }
    ),
}


def build_provider_environment(
    provider: ProviderIdentity,
    *,
    source_env: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
    execution_context: ChildExecutionContext | None = None,
) -> dict[str, str]:
    """Build a fresh fail-closed child environment for ``provider``."""

    if not isinstance(provider, ProviderIdentity):
        raise TypeError("provider must be a ProviderIdentity")
    if execution_context is not None and not isinstance(
        execution_context, ChildExecutionContext
    ):
        raise TypeError("execution_context must be a ChildExecutionContext")

    candidates = dict(os.environ if source_env is None else source_env)
    if overrides:
        candidates.update(overrides)

    allowed_keys = set(_PROCESS_ENV_KEYS)
    allowed_keys.update(_PROVIDER_CREDENTIAL_KEYS[provider])
    allowed_keys.update(_PROVIDER_CONFIG_KEYS[provider])
    for capability in (
        execution_context.tool_capabilities if execution_context else frozenset()
    ):
        allowed_keys.update(_CAPABILITY_ENV_KEYS[capability])

    child = {
        key: value
        for key, value in candidates.items()
        if key in allowed_keys and isinstance(value, str)
    }
    child.setdefault("PATH", os.defpath)

    logger.debug(
        "provider child environment authorized key names: provider=%s keys=%s",
        provider.value,
        sorted(child),
    )
    return child
