# Convention: `## Knowledge Base (KB)` section

Every project's `CLAUDE.md` (or `README.md` if there is no `CLAUDE.md`) should include a `## Knowledge Base (KB)` section. Soft requirement — same shape as the existing `## Running` convention referenced from `/do-pr-review` and across `docs/features/`.

## Why

A project's knowledge lives in two distinct places, and agents/teammates conflate them constantly:

1. **The vault** — a curated Markdown corpus under `~/work-vault/<project>/`, iCloud-synced, human-written. This is the source of truth for business context, decisions, assets, notes.
2. **The memory system** — Redis-backed `Memory` records, partitioned by `project_key`, learned by the agent from conversations, PR merges, and corrections.

The KB section makes both sources explicit at the top of every project so anyone (or any agent) knows where to look and what `project_key` to scope memory queries to.

## Required fields

Every KB section names exactly two things:

1. **Vault directory** — absolute path under `~/work-vault/`, plus a pointer to its `README.md` index
2. **Memory project key** — the `project_key` from `config/projects.json`, plus the CLI command to search it

## Template

Copy this verbatim into a new project, replacing `<VAULT_DIR>` and `<PROJECT_KEY>`:

```markdown
## Knowledge Base (KB)

This project's knowledge has two sources. Pull from both before answering substantive questions.

**1. Vault (curated docs, iCloud-synced)**
- Location: `~/work-vault/<VAULT_DIR>/`
- Index: see that directory's `README.md` for the file index
- Source of truth for business context, project notes, decisions, and assets

**2. Memory system (Redis, agent-learned observations)**
- Project key: `<PROJECT_KEY>` (see `config/projects.json`)
- Search: `python -m tools.memory_search search "<query>" --project <PROJECT_KEY>`
- Save: `python -m tools.memory_search save "<content>" --project <PROJECT_KEY>`
- Status: `python -m tools.memory_search status --project <PROJECT_KEY>`

Curated vault = what humans wrote. Memory = what the agent learned. Both partition by project — don't leak cross-project context.
```

## Placement

- **If the project has a `CLAUDE.md`**: KB section lives there (agent-facing, near the bottom alongside the `## See Also` block)
- **If the project has only a `README.md`**: KB section lives in `README.md` (human + agent both read it)
- **Never both** — pick one to be authoritative; the other can link to it

## Where it gets referenced

The KB section is meant to be linked from any skill or tool that needs project-specific knowledge:

- `/do-plan` and `/do-build` should pull from the vault when scoping work
- `/do-pr-review` should check the vault for design briefs and acceptance criteria
- Memory searches in tools and reflections should use the documented `project_key`

This is the same pattern as the `## Running` convention, which is referenced from `.claude/skills-global/do-pr-review/SKILL.md` and `docs/features/review-workflow-screenshots.md`.

## Validation

A warn-only soft validator at `.claude/hooks/validators/validate_knowledge_base_section.py` checks for the section's presence and structure. It is **not** wired into any blocking hook — invoke manually:

```bash
uv run .claude/hooks/validators/validate_knowledge_base_section.py CLAUDE.md
uv run .claude/hooks/validators/validate_knowledge_base_section.py README.md
```

Exit code is always 0 (warnings on stderr). Wire into CI or a pre-commit hook per-project if you want enforcement.

## See also

- `config/projects.json` — declares each project's `knowledge_base` path and project key
- `docs/features/subconscious-memory.md` — memory system architecture, scoring, consolidation
- `docs/features/markitdown-ingestion.md` — `valor-ingest` for getting non-Markdown sources into the vault corpus
