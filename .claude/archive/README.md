# .claude Archive

Files moved here on 2026-04-08 by the Harmonic exhaustive-clean audit.

## Why archived (not deleted)

These files were identified as orphans (not referenced from CLAUDE.md, docs/claude/, or any other tracked file) by the .claude/ hygiene audit. They were preserved here in case the audit's reference scan missed something.

## Restoration

To restore any file to active status, move it back to `.claude/skills/` or `.claude/agents/` and add a reference to it from CLAUDE.md or docs/claude/. After 2026-05-08 with no restoration requests, these can be deleted permanently.

## Inventory

**skills/** (5 flat files, originally in `.claude/skills/`):
- `airflow-dag-patterns.md` — out-of-scope (no Airflow in this project)
- `data-transformers.md` — orphan, never referenced
- `founder_evaluation.md` — orphan
- `investment_memo.md` — orphan
- `technical_due_diligence.md` — orphan

**agents/** (7 files, originally in `.claude/agents/`):
- `collector_specialist.md`
- `crm_specialist.md`
- `due_diligence_coordinator.md`
- `market_intelligence.md`
- `ranking_specialist.md`
- `research_analyst.md`
- `secops_governor.md`

Only `sqlite-expert.md` was retained in `.claude/agents/` (verified in active use).

## Skills retained in `.claude/skills/`

Per the audit, these flat-file skills had references in tracked docs and were kept active:
- `ranking_explanation.md`
- `red_flag_detection.md`
- `signal_quality.md`
- `strategy_synthesis.md`
- `thesis_matching.md`

Plus the structured skills under `.claude/skills/<name>/SKILL.md`.
