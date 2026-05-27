# Hermes DEV BRAIN

Hermes uses Proposal B's incremental adapter architecture:

- Pydantic validates `.claude/hermes/model-routing.json`.
- Internal runtime state uses frozen dataclasses.
- Codex and Kimi execution goes through existing wrappers.
- Locks follow the repo's atomic file-lock pattern.
- Ledgers and repair prompts are redacted before writing text artifacts.

Deferred work:

- Gemini CLI execution remains deferred until an adapter and tests exist.
- Antigravity execution remains deferred until its local contract is defined.
- Vertex or hosted-provider execution belongs in a separate adapter slice.
- Network probes for provider doctor require an explicit future flag.
- Repo-wide UTC lint cleanup should be its own branch if desired.
