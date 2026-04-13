---
status: Build
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-04-13
tracking: https://github.com/tomcounsell/ai/issues/927
last_comment_id:
---

# SDLC Repo Addenda: Per-Stage docs/sdlc/ Notes

## Problem

The global SDLC skills (`do-plan`, `do-build`, `do-test`, etc.) are intentionally generic — they work across any repo. But this repo has accumulated unique patterns: Popoto schema migration requirements, plan section validators, `## Update System` backfill notes, transport-keyed callback conventions. Today that knowledge lives only in human memory or buried in CLAUDE.md noise.

**Current behavior:** Running `/do-plan` in this repo gives the same experience as any other repo. The skill has no awareness of this repo's Popoto models, required plan sections, or migration conventions. Developers re-discover these constraints every session or skip them and create rework.

**Desired outcome:** A `docs/sdlc/` directory holds one lightweight addendum per SDLC stage. Each file contains only what is unique to this repo. The update script ensures these files always exist. SDLC skills read the relevant addendum at startup. A reflection agent reviews merged PRs every 3 days and makes targeted updates to keep the addenda current.

## Freshness Check

**Baseline commit:** `7aec2ebc46692b4238fc7a1771a731fc8d4b04da`
**Issue filed at:** 2026-04-13T05:03:35Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `~/.claude/skills/do-plan/SKILL.md` — exists, no addendum check present — confirmed
- `scripts/update/run.py` — exists, modular structure, imports from `scripts/update/` — confirmed
- `scripts/update/migrations.py` — exists, idempotent migration pattern with `data/migrations_completed.json` — confirmed
- `scripts/reflections.py` — exists, daily maintenance script with 17 units — confirmed
- `.claude/hooks/validators/validate_documentation_section.py` — exists — confirmed

**Cited sibling issues/PRs re-checked:**
- No siblings cited

**Commits on main since issue was filed:** issue filed same day as plan — no intervening commits on referenced files

**Active plans in `docs/plans/` overlapping this area:** none

## Prior Art

No prior issues or PRs found related to SDLC repo addenda, per-stage docs, or skill customization for this repo.

## Architectural Impact

- **New directory:** `docs/sdlc/` — 8 stub markdown files
- **New migration step:** `scripts/update/migrations.py` gains a function to create missing stubs idempotently
- **Skill modifications:** One-line addendum check prepended to each of 8 global SDLC skills
- **New scheduled agent:** A `com.valor.sdlc-reflection.plist` or extension to reflections script runs every 3 days
- **Coupling:** Skills gain a soft dependency on `docs/sdlc/` files — missing files are a no-op (graceful degradation)
- **Reversibility:** All changes are additive. Removing addendum files restores skills to default behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on reflection agent design)
- Review rounds: 1 (code review)

## Prerequisites

No external credentials or infrastructure needed.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `~/.claude/skills/` writable | `ls ~/.claude/skills/do-plan/SKILL.md` | Skill file access |
| `scripts/update/migrations.py` exists | `test -f scripts/update/migrations.py` | Migration extension point |

## Solution

### Key Elements

- **`docs/sdlc/` directory:** 8 addendum files (one per SDLC stage). Each file has a comment header noting it should not duplicate the global skill. Initial `do-plan.md` ships with hand-authored content (Popoto migration reminder, required plan sections).
- **Update script migration:** `scripts/update/migrations.py` gains `_migrate_create_sdlc_stubs()`. Checks for each stub, creates missing ones. Idempotent — recorded in `data/migrations_completed.json` once all 8 exist.
- **Skill addendum check:** Each stage skill gets one instruction at the top: "Before starting, check if `docs/sdlc/do-X.md` exists. If it does, read it and incorporate its guidance as repo-specific addenda." This is a Read call — zero overhead when the file is absent or empty.
- **SDLC reflection agent:** A new cron entry (`com.valor.sdlc-reflection.plist`) runs every 3 days. It fetches recently merged PRs, extracts per-stage learnings, and proposes modest edits to the relevant `docs/sdlc/do-X.md` files. Stays under 300-line limit per file.

### Flow

Update script runs → creates missing `docs/sdlc/` stubs → developer runs `/do-plan` → skill reads `docs/sdlc/do-plan.md` → incorporates repo-specific context → plan includes Popoto migration notes → reflection agent runs every 3 days → reads merged PRs → updates addenda with new learnings

### Technical Approach

- Stubs created via `scripts/update/migrations.py` — idempotent, tracked in `data/migrations_completed.json`
- Skill addendum check: single line near top of each SKILL.md: "Check for `docs/sdlc/do-X.md` and read it if present"
- Reflection agent: new launchd plist with `StartCalendarInterval` every 3 days; agent uses `gh pr list --state merged` to get recent PRs; proposes edits via AgentSession (role=pm, model=sonnet)
- Initial `docs/sdlc/do-plan.md` content: Popoto schema migration note, required plan sections (Documentation, Update System, Agent Integration, Test Impact), CLAUDE.md conventions
- 300-line limit enforced by reflection agent prompt (trim before adding)

## Failure Path Test Strategy

### Exception Handling Coverage
- Addendum check in skills must gracefully handle missing file (no error if `docs/sdlc/do-X.md` absent)
- Reflection agent must handle empty PR list (no PRs since last run) without crashing

### Empty/Invalid Input Handling
- Stub creation: if `docs/sdlc/` directory doesn't exist, migration creates it
- Reflection agent: if no PRs merged since last run, agent exits cleanly with "no updates needed"

### Error State Rendering
- Migration failure (file write error): logged in update output, does not block other update steps
- Reflection agent failure: logged to `logs/reflections.log`, does not block daily reflections run

## Test Impact

No existing tests affected — this is a greenfield feature adding a new directory, a new migration function, and a new cron schedule. No existing behavior is modified; skill files gain a read-only addendum check that is a no-op when the file is absent.

New tests to create:
- `tests/unit/test_sdlc_stubs.py` — verifies migration creates all 8 stubs, idempotent on re-run
- `tests/unit/test_sdlc_addendum_graceful.py` — verifies missing addendum file does not raise

## Rabbit Holes

- **Backfilling addenda from historical PRs** — out of scope; agent starts fresh from install date
- **Automated enforcement via hooks** — reflection agent is advisory, not blocking; hooks already enforce plan sections
- **Changing the global skill content** — skills stay generic; all repo-specific content goes in `docs/sdlc/`
- **Version-controlling addendum changes** — reflection agent commits directly; no approval gate (keeps it lightweight)

## Risks

### Risk 1: Skill file modification breaks other repos
**Impact:** If the addendum check is poorly written, it may error on repos where `docs/sdlc/` doesn't exist  
**Mitigation:** Check uses `Read` with graceful fallback — if file absent, skip silently. Test on clean checkout.

### Risk 2: Reflection agent produces low-quality updates
**Impact:** Addenda accumulate noise, reducing their value  
**Mitigation:** 300-line cap; agent prompt explicitly says "unique, not duplicative." Human can review diffs before auto-commit or gate behind PR.

### Risk 3: Migration runs on every update on machines that already have stubs
**Impact:** Wasted work  
**Mitigation:** Migration is idempotent and skipped once recorded in `data/migrations_completed.json`.

## Race Conditions

**Stub creation:** This repo runs on multiple machines (multi-instance deployment). Two machines running `/update` simultaneously would both attempt to write `docs/sdlc/` stubs. This is benign — stub content is deterministic (same template), so the last writer wins with identical content. The `if not path.exists():` guard reduces redundant writes but does not fully prevent them. No data loss or corruption risk.

**Reflection agent:** Runs on a fixed 3-day schedule via `StartCalendarInterval` (Mon + Thu). On multi-machine deployments both plists can fire near-simultaneously. Guard condition at script startup: `gh pr list --state open --label sdlc-reflection` — if an open PR already exists, the script exits cleanly without writing. The opening machine's PR is self-enforcing; the second machine skips automatically. The `sdlc-reflection` label is applied when the PR is created.

## No-Gos (Out of Scope)

- Changing global SDLC skill content (stays generic)
- Automated enforcement of addendum content via hooks (advisory only)
- Backfilling addenda from historical PRs
- Addendum files for non-SDLC scripts (reflections, autoexperiment, etc.)

## Update System

The update script (`scripts/update/run.py` → `migrations.py`) gains a new migration:

```python
def _migrate_create_sdlc_stubs(project_dir: Path) -> str | None:
    """Create docs/sdlc/ stub files if missing."""
    stubs = ["do-plan", "do-plan-critique", "do-build", "do-test",
             "do-patch", "do-review", "do-docs", "do-merge"]
    sdlc_dir = project_dir / "docs" / "sdlc"
    sdlc_dir.mkdir(parents=True, exist_ok=True)
    for name in stubs:
        path = sdlc_dir / f"{name}.md"
        if not path.exists():
            path.write_text(STUB_TEMPLATE.format(name=name))
    # Verify all stubs were written — if any are missing, return error so
    # run_pending_migrations() does NOT mark this migration complete and retries next run
    missing = [n for n in stubs if not (sdlc_dir / f"{n}.md").exists()]
    if missing:
        return f"missing stubs: {', '.join(missing)}"
    return None  # All present — mark complete
```

This migration runs once per machine and is idempotent. Running `/update` on a machine that already has all 8 files is a no-op. If a write fails (permissions, disk full), the migration returns an error string so `run_pending_migrations()` does not mark it complete — it retries on the next run.

## Agent Integration

No MCP server changes needed. The reflection agent runs as a scheduled `AgentSession` (role=pm) — it uses existing `gh` CLI and file tools available in the agent environment. No new tools required.

The agent's prompt is embedded in its plist invocation or a companion script at `scripts/sdlc_reflection.py`.

No `.mcp.json` changes needed.

## Documentation

- [ ] Create `docs/features/sdlc-repo-addenda.md` describing the `docs/sdlc/` system and reflection agent
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `CLAUDE.md` quick reference table to include `docs/sdlc/` as a resource and `tail -f logs/sdlc_reflection.log` as a log command
- [ ] Add comment header to each `docs/sdlc/do-X.md` stub per spec
- [ ] Document that adding a 9th SDLC stage requires a new named migration entry in `scripts/update/migrations.py`

## Success Criteria

- [ ] `docs/sdlc/` exists with 8 stub files after running `/update` on a clean machine
- [ ] Running `/update` on a machine with all 8 files is a no-op (idempotent)
- [ ] `/do-plan` reads `docs/sdlc/do-plan.md` when present and incorporates its content
- [ ] Same addendum check exists in do-build, do-test, do-patch, do-pr-review, do-docs, do-merge
- [ ] Reflection agent cron entry runs every 3 days
- [ ] Each stub file has a comment header: "Do not duplicate content from the global skill"
- [ ] No addendum file exceeds 300 lines (enforced by reflection agent prompt)
- [ ] `docs/sdlc/do-plan.md` ships with hand-authored content (Popoto migration note, required plan sections)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (stubs-and-migration)**
  - Name: stub-builder
  - Role: Create `docs/sdlc/` stubs and update `scripts/update/migrations.py`
  - Agent Type: builder
  - Resume: true

- **Builder (skill-addendum-check)**
  - Name: skill-builder
  - Role: Add addendum check to each of the 8 global SDLC skill files
  - Agent Type: builder
  - Resume: true

- **Builder (reflection-agent)**
  - Name: reflection-builder
  - Role: Create `scripts/sdlc_reflection.py` and `com.valor.sdlc-reflection.plist`
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify all components work together end-to-end
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: feature-documentarian
  - Role: Create `docs/features/sdlc-repo-addenda.md` and update index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create docs/sdlc/ stubs and migration
- **Task ID**: build-stubs
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_stubs.py` (create)
- **Assigned To**: stub-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `docs/sdlc/` directory
- Create 8 stub files (do-plan.md, do-plan-critique.md, do-build.md, do-test.md, do-patch.md, do-pr-review.md, do-docs.md, do-merge.md) with comment headers
- Hand-author initial content for `docs/sdlc/do-plan.md`: Popoto migration requirement, required plan sections (Documentation, Update System, Agent Integration, Test Impact), `docs/plans/` commit-on-main rule
- Add `_migrate_create_sdlc_stubs()` to `scripts/update/migrations.py`
- Register migration in `MIGRATIONS` dict (required — `run_pending_migrations()` iterates `MIGRATIONS`)
- Write `tests/unit/test_sdlc_stubs.py`

### 2. Add addendum check to SDLC skills
- **Task ID**: build-skill-checks
- **Depends On**: build-stubs
- **Validates**: manual verification that each skill file contains addendum check
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Add one instruction near the top of each SKILL.md: "Before starting, check if `docs/sdlc/do-X.md` exists in the current repo. If it does, read it and incorporate its guidance as repo-specific addenda to these instructions."
- Global skills to modify (in `~/.claude/skills/`): `do-plan/SKILL.md`, `do-build/SKILL.md`, `do-test/SKILL.md`, `do-patch/SKILL.md`, `do-pr-review/SKILL.md`, `do-docs/SKILL.md`, `do-plan-critique/SKILL.md`
- Repo-local command (different format, different path): `.claude/commands/do-merge.md` — prepend same addendum check note at top of this file (it is a markdown command file, not a SKILL.md; treat the same way — add a one-line instruction at the top)

### 3. Create SDLC reflection agent
- **Task ID**: build-reflection-agent
- **Depends On**: build-stubs
- **Validates**: `scripts/sdlc_reflection.py` runs without error; plist is valid
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/sdlc_reflection.py`: fetches merged PRs since last run, extracts per-stage patterns, proposes targeted edits to `docs/sdlc/do-X.md` files, enforces 300-line cap
- Guard against multi-machine PR collision: at startup, check `gh pr list --state open --label sdlc-reflection`; if a PR already exists, exit cleanly with "reflection PR already open, skipping"
- Create `com.valor.sdlc-reflection.plist` scheduled every 3 days using `StartCalendarInterval` (Mon + Thu, matching `com.valor.reflections.plist` pattern — do NOT use `StartInterval`)
  - Plist must write to **`logs/sdlc_reflection.log`** and **`logs/sdlc_reflection_error.log`** (dedicated files — do NOT share `logs/reflections.log`)
  - Use `<key>StandardOutPath</key><string>__PROJECT_DIR__/logs/sdlc_reflection.log</string>` and matching ErrorPath
- Create `scripts/install_sdlc_reflection.sh` matching pattern of `scripts/install_reflections.sh`
- Store last-run timestamp in `data/sdlc_reflection_last_run.json`

### 4. Integration validation
- **Task ID**: validate-integration
- **Depends On**: build-stubs, build-skill-checks, build-reflection-agent
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all 8 `docs/sdlc/` stubs exist with correct headers
- Verify migration function is registered and idempotent
- Verify each skill file contains addendum check instruction
- Verify reflection script runs without error: `python scripts/sdlc_reflection.py --dry-run`
- Run unit tests: `pytest tests/unit/test_sdlc_stubs.py -v`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: feature-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-repo-addenda.md`
- Add entry to `docs/features/README.md`
- Update `CLAUDE.md` quick reference to mention `docs/sdlc/`

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Run lint and format checks
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Stubs exist | `ls docs/sdlc/*.md \| wc -l` | output contains 8 |
| do-plan has addendum check | `grep -l "docs/sdlc" ~/.claude/skills/do-plan/SKILL.md` | exit code 0 |
| Reflection script runnable | `python scripts/sdlc_reflection.py --dry-run` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Adversary | Migration silently succeeds on partial stub write failure — `return None` regardless of whether writes succeeded; recorded as complete, never retries | Fixed in Update System section | Return `f"missing stubs: {', '.join(missing)}"` when any stub is absent after loop; `run_pending_migrations()` treats non-None as failure, skips recording, retries next run |
| BLOCKER | Operator | Reflection agent log file collision — plan did not specify a log file; default would share `logs/reflections.log`, interleaving output | Fixed in Task 3 | Plist must write to `logs/sdlc_reflection.log` and `logs/sdlc_reflection_error.log`; keys `StandardOutPath` / `StandardErrorPath` added to plist spec |
| CONCERN | Operator, Skeptic | Plist timing key inconsistency — Solution says `StartCalendarInterval`, Task 3 said `StartInterval` (uptime-relative, different semantics) | Fixed in Task 3 | Task 3 now specifies `StartCalendarInterval` (Mon + Thu) matching `com.valor.reflections.plist`; `StartInterval` reference removed |
| CONCERN | Adversary | Multi-machine PR race — both machines can fire and open conflicting PRs on the same addendum files | Fixed in Race Conditions + Task 3 | Guard condition: check `gh pr list --state open --label sdlc-reflection`; if PR exists, exit cleanly; PR opened with `sdlc-reflection` label |
| CONCERN | Skeptic | Future SDLC stage stubs not auto-created — migration recorded once; new stage added later requires a new migration entry | Documented | Documented explicitly: adding a 9th stage requires a new named migration entry in `MIGRATIONS` dict |
| NIT | Simplifier | Team Orchestration over-specified — 5 named agents for straightforward sequential work | Noted | Retained for compatibility with `/do-build` format; builder may consolidate in practice |
| NIT | Skeptic | `test_sdlc_addendum_graceful.py` tests an if-statement, not behavior | Noted | Replace with a test that verifies skill output changes when addendum is present vs absent |

---

## Open Questions

1. ~~Should the reflection agent auto-commit its edits directly to main, or open a PR for human review?~~ **Resolved:** Reflection agent opens a PR and schedules a PM session to run the remaining SDLC stages (pr review, patch, docs, merge) in parallel.
2. ~~Should `do-plan-critique` addendum read `docs/sdlc/do-critique.md` or `docs/sdlc/do-plan-critique.md`?~~ **Resolved:** Use `docs/sdlc/do-plan-critique.md` — match the global skill name.
