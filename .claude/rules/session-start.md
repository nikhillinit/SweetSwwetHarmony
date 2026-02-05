# Rule: Session Start Protocol

## Trigger
At the start of every new session or after a `/clear` command.

## Required Actions
1. **Read CLAUDE.md** - Understand project context, available workflows, and integrations
2. **Check for documented workflows** - Before interpreting user requests, cross-reference against:
   - `docs/claude/` reference docs
   - `integrations/` for automation
   - Available CLI commands
3. **Use documented automation** - Prefer existing workflows over manual processes

## Examples

### User says: "Forensic Engineer validation"
- WRONG: Manually read files and validate assumptions
- RIGHT: Recognize this as the Forensic Engineer workflow in `docs/claude/codex-collaboration.md`, invoke `python -m integrations.maestro forensic ...`

### User says: "run the pipeline"
- WRONG: Guess at commands
- RIGHT: Check CLAUDE.md Quick Commands section, use `python run_pipeline.py full ...`

## Anti-Pattern
Jumping straight into file reading or task execution without first orienting to project-documented capabilities.
