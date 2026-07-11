# do-plan addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-plan/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Substrate Tools & Commands (the generic body defers these here)

The leaned body refers to these abstractly; here are the concrete invocations.

**Stage markers.** Write `in_progress` at the very start, `completed` after the
plan is committed and pushed (end of Phase 4):

```bash
sdlc-tool stage-marker --stage PLAN --status in_progress --issue-number {issue_number} --run-id {run_id} 2>/dev/null || true
sdlc-tool stage-marker --stage PLAN --status completed   --issue-number {issue_number} --run-id {run_id} 2>/dev/null || true
```

Run identity (#2003): every state-mutating `sdlc-tool` call in this addendum
carries `--run-id {run_id}` — supplied by the invoking supervisor (`/do-sdlc`
or `/sdlc` carries it from `session-ensure`). When this skill is invoked
standalone (no supervisor), run
`sdlc-tool session-ensure --issue-number {issue_number}` once at the start and
use the emitted `run_id` (`ISSUE_LOCKED` means another live run owns the issue —
stop and report). Read-only calls (`stage-query`, `verdict get`, `next-skill`)
take no run-id.

**Cross-repo `gh` targeting.** `GH_REPO` is set automatically by `sdk_client.py`;
`gh` respects it — no `--repo` flags needed.

**Phase 0 recon-validation gate.** The ISSUE→PLAN gate runs:

```bash
python .claude/hooks/validators/validate_issue_recon.py ISSUE_NUMBER
```

If it fails, the issue needs a `## Recon Summary` via `/do-issue` Step 3.

**Phase 0.7 memory store.** Save valuable research findings for reuse:

```bash
"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.memory_search save "Finding: [desc with source URL]" --importance 5.0 --source agent
```

**Phase 1 blast-radius tool.** Run the code-impact finder:

```bash
"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.code_impact_finder "PROBLEM_STATEMENT_HERE"
```

**Plans / infra directories.** Plans live at `docs/plans/{slug}.md` (snake_case
slug). Conditional INFRA docs accumulate at `docs/infra/{slug}.md` (never
archived). Prerequisite checker: `python scripts/check_prerequisites.py docs/plans/{slug}.md`.

**Plan-revising lock (Phase 4 clear).** On a revision pass, after setting
`revision_applied: true` **and** `revision_applied_at: <ISO-8601 UTC timestamp>`
(the latter is the #1760 event-scoped convergence latch — see the global
SKILL.md Phase 4 step 2a for the exact `date -u` invocation) and pushing,
clear the lock so the router can route to build:

```bash
sdlc-tool meta-set --key plan_revising --value false --issue-number {issue_number} --run-id {run_id} 2>/dev/null || true
```

## Popoto Schema Migration Requirement

When a plan involves changes to any Popoto model (models in `agent/`, `bridge/`, `tools/`, or anywhere using `popoto`):

- Add a migration function to `scripts/update/migrations.py`
- Register it in the `MIGRATIONS` dict (required — `run_pending_migrations()` iterates `MIGRATIONS`)
- Migration must be idempotent; it is recorded once in `data/migrations_completed.json`
- Use subprocess to call a separate migration script if the logic is non-trivial
- Never write raw Redis ops (`r.delete`, `r.srem`, `r.sadd`, `r.zrem`) — use `instance.delete()`, `Model.rebuild_indexes()`, or `transition_status()`

## Required Plan Sections

Every plan in this repo must include all four of these sections (validated by `.claude/hooks/validators/`):

1. **## Documentation** — at least one checkbox task specifying a `docs/features/` path; or explicit "No documentation changes needed" with justification
2. **## Update System** — describe whether `scripts/update/run.py` or `migrations.py` needs changes; new deps to propagate; explicit "No update system changes required" if none
3. **## Agent Integration** — describe MCP server changes, `.mcp.json` changes, or explicit "No agent integration required"
4. **## Test Impact** — checklist of existing test files that will break (UPDATE/DELETE/REPLACE), or explicit "No existing tests affected" with 50+ char justification

Missing any of these sections will fail the pre-commit hook and block plan creation.

## docs/plans/ Commit-on-Main Rule

Plan documents must always be committed directly on `main`, never on feature branches. This is enforced by memory and convention. Use `git checkout main` before creating or updating plan files.

## Transport-Keyed Callback Convention

When adding output callbacks to the bridge, key them by transport type (e.g., `"telegram"`, `"local"`) not by session ID. See `bridge/telegram_bridge.py` and `agent/output_handler.py` for the pattern.

## Slug Conventions

- Slugs are kebab-case, derived from the plan filename (without `.md`)
- Slugs tie together: plan doc, GitHub issue, worktree at `.worktrees/{slug}/`, branch `session/{slug}`, task list
- Create the slug from the GitHub issue title, not from a description of the work
