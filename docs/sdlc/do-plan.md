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

Plans created with `/do-plan` must include four required sections. These are enforced by hooks that block plan creation if sections are missing or empty.

### ## Documentation (Required)

Every plan must include a **## Documentation** section with actionable tasks specifying which docs to create or update. This is enforced by `.claude/hooks/validators/validate_documentation_section.py`.

The **## Documentation** section must contain:
- At least one checkbox task (`- [ ]`)
- A target documentation path (e.g., `docs/features/my-feature.md`)
- If genuinely no docs needed, explicitly state "No documentation changes needed" with justification

Example:
```markdown
## Documentation
- [ ] Create `docs/features/my-feature.md` describing the new capability
- [ ] Add entry to `docs/features/README.md` index table
```

The `/do-build` workflow validates that these docs were actually created before allowing PR merge.

### ## Update System (Required)

Include an **## Update System** section after **## No-Gos**. This system is deployed across multiple machines via the `/update` skill (`scripts/remote-update.sh`, `.claude/skills/update/`). New features frequently require complementary changes to the update process.

The **## Update System** section should cover:
- Whether the update script or update skill needs changes
- New dependencies or config files that must be propagated
- Migration steps for existing installations
- If no update changes are needed, state that explicitly (e.g., "No update system changes required — this feature is purely internal")

### ## Agent Integration (Required)

Include an **## Agent Integration** section after **## Update System**. The agent receives Telegram messages via the bridge (`bridge/telegram_bridge.py`) and reaches new functionality through one of two surfaces: a CLI entry point declared in `pyproject.toml [project.scripts]` (invoked via the agent's Bash tool), or a direct Python import the bridge calls internally. New Python functions in `tools/` are invisible to the agent until wired into one of those two paths.

The **## Agent Integration** section should cover:
- Whether a new CLI entry point is required in `pyproject.toml [project.scripts]` (e.g. `valor-tts = "tools.tts.cli:main"`)
- Whether the bridge itself needs to import/call the new code directly
- Integration tests that verify the agent can actually invoke the new tools
- If no agent integration is needed, state that explicitly (e.g., "No agent integration required — this is a bridge-internal change")

### ## Test Impact (Required)

Include a **## Test Impact** section after **## Failure Path Test Strategy** and before **## Rabbit Holes**. This section audits existing tests that will break or need changes due to the planned work. It is enforced by `.claude/hooks/validators/validate_test_impact_section.py`.

The **## Test Impact** section must contain:
- Checklist items listing affected test files/cases with dispositions: UPDATE, DELETE, or REPLACE
- If no existing tests are affected, explicitly state "No existing tests affected" with justification (50+ chars)

Example:
```markdown
## Test Impact
- [ ] `tests/unit/test_example.py::test_old_behavior` — UPDATE: assert new return value
- [ ] `tests/integration/test_flow.py::test_end_to_end` — REPLACE: rewrite for new API
```

Or for greenfield work:
```markdown
## Test Impact
No existing tests affected — this is a greenfield feature with no prior test coverage.
```

Missing any of these sections will fail the pre-commit hook and block plan creation.

## docs/plans/ Commit-on-Main Rule

Plan documents must always be committed directly on `main`, never on feature branches. This is enforced by memory and convention. Use `git checkout main` before creating or updating plan files.

## Transport-Keyed Callback Convention

When adding output callbacks to the bridge, key them by transport type (e.g., `"telegram"`, `"local"`) not by session ID. See `bridge/telegram_bridge.py` and `agent/output_handler.py` for the pattern.

## Slug Conventions

- Slugs are kebab-case, derived from the plan filename (without `.md`)
- Slugs tie together: plan doc, GitHub issue, worktree at `.worktrees/{slug}/`, branch `session/{slug}`, task list
- Create the slug from the GitHub issue title, not from a description of the work
