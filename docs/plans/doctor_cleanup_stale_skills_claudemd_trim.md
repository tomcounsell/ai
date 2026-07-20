---
status: docs_complete
type: chore
appetite: Small
owner: Tom Counsell
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2065
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-20T08:36:02Z
---

# Clean up stale skill hardlinks and trim duplicated content from root CLAUDE.md

## Problem

Running Claude Code's `/doctor` health check against this repo on 2026-07-13 surfaced two related hygiene problems: stale skill references that outlived their source, and always-loaded documentation that duplicates content already available on demand.

**Current behavior:**

1. Four skill names — `audit-next-tool`, `do-design-review`, `get-telegram-messages`, `searching-message-history` — are hardlinked into `~/.claude/skills/` on this machine and advertised as available skills, but none of them exist anywhere in the current repo tree (`.claude/skills/` or `.claude/skills-global/`). `scripts/update/hardlinks.py`'s `RENAMED_REMOVALS` list has no entries for them, so `/update` never cleans up the orphaned hardlinks on any machine that ever had them.
2. Root `CLAUDE.md` is 46,847 characters, over the ~40,000-char threshold where Claude Code's large-memory-file warning fires (`getMaxMemoryCharacterCount` — 5% of the model's context window in characters, floored at 40,000). Three sections (`## OfficeCLI`, `## Reading Telegram Messages`, `## Reading Email`) closely duplicate content already covered, in more depth, by skills that load on demand.
3. `## Plan Requirements (This Repo Only)` is entirely repo-specific, task-specific behavior for the `do-plan` skill — the exact category of content this repo's own documented skill-context-seam convention says belongs in `.claude/skill-context/do-plan.md`, not the always-loaded root file. That file doesn't exist yet, unlike 18 sibling skill-context files.

**Desired outcome:**

1. `~/.claude/skills/` on every machine no longer carries the four orphaned skill hardlinks after `/update`, and `RENAMED_REMOVALS` documents why so this doesn't regress.
2. Root `CLAUDE.md` drops to roughly 36,500 characters, under the warning threshold, with the OfficeCLI/Telegram/Email sections removed (their content remains reachable via the skills that already cover it).
3. `.claude/skill-context/do-plan.md` exists, contains the repo-specific plan-section requirements, and `do-plan`'s generic skill body picks it up automatically via the existing probe-sentence convention.

## Freshness Check

**Baseline commit:** `c0cc4190e74e1788d0fa378ddee7e08f9e775f2b`
**Issue filed at:** 2026-07-13T08:50:10Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `CLAUDE.md` section headers (`## OfficeCLI` at line 35, `## Reading Telegram Messages` at 89, `## Reading Email` at 114, `## Plan Requirements (This Repo Only)` at 518) — re-checked against the live file at plan time via `grep -n "^## " CLAUDE.md`; line numbers and total size (46,847 chars) are unchanged from what the issue cited.
- `scripts/update/hardlinks.py` `RENAMED_REMOVALS` (line 14) — confirmed still a `list[tuple[str, str]]` of `(kind, old_name)` pairs, with an existing precedent entry `("skills", "prepare-app")` (line 34, "Orphan hardlink from an old repo version — removed with no source remaining") that is the exact same category as this plan's four new entries.

**Cited sibling issues/PRs re-checked:**
- None cited directly in the issue body (the issue references historical commits, not open issues/PRs).

**Commits on main since issue was filed (touching referenced files):**
- `git log --since <issue-filed-time>` initially matched `e1ec8695` ("Centralize magic timeout/retry/TTL literals...") against `CLAUDE.md`, but its actual commit time (2026-07-13 00:29:53 UTC) predates the issue's filing time (08:50:10 UTC) — a `--since` timezone artifact, not real post-filing drift. This commit's `CLAUDE.md` insertion (5 lines under "Configuration Files", well before line 517) was already reflected in the line numbers cited in the issue and re-verified above. No other commits touched the relevant files since filing.

**Active plans in `docs/plans/` overlapping this area:** none found.

**Notes:** None of the referenced content has drifted; proceeding on the issue's original premises.

## Prior Art

- **#1783 / PR #1806**: "Generalize all global skills to be fully repo-agnostic" — introduced the skill-context seam (`.claude/skill-context/{skill}.md`) this plan's item 3 uses. Confirms the convention is established and the pattern for adding a new skill-context file is well-trodden.
- **PR #1894**: "Renovate skill fleet: descriptions, progressive disclosure, rot repair (60 skills)" (2026-05-06) — the likely origin of several currently-unused skills and a plausible point where `audit-next-tool` (added by #156, 2026-02-23) was folded into a renamed skill during the 60-skill renovation. No direct evidence of what it became; flagged as an open question below rather than assumed.
- **PR #156**: "Skills & agents reorganization: canonical template, command consolidation, hardlink scoping" — added the original `RENAMED_REMOVALS` mechanism and the `audit-next-tool` skill itself.
- **PR #2009**: "chore(update): sweep obsolete launchd jobs on every /update" — unrelated mechanism (launchd, not skill hardlinks) but confirms this repo has an established pattern of "sweep obsolete X on every /update" chores, which this plan's item 1 follows.

No prior attempt specifically targeted these four stale hardlinks or these three CLAUDE.md sections — this is new work, not a re-fix.

## Research

No relevant external findings — proceeding with codebase context and training data. This is a purely internal docs/config change with no external libraries, APIs, or ecosystem patterns involved.

## Solution

### Key Elements

- **`RENAMED_REMOVALS` entries**: four new `("skills", "<name>")` tuples in `scripts/update/hardlinks.py`, following the existing `("skills", "prepare-app")` precedent, so `/update` removes the orphaned hardlinks on every machine.
- **CLAUDE.md section removal**: delete `## OfficeCLI`, `## Reading Telegram Messages`, `## Reading Email` from root `CLAUDE.md` — their content is already covered by `.claude/skills/officecli/SKILL.md`, `.claude/skills/telegram/SKILL.md`, and `.claude/skill-context/email.md` respectively.
- **`do-plan` skill-context file**: new `.claude/skill-context/do-plan.md` carrying the current `## Plan Requirements (This Repo Only)` content, formatted like the existing `docs/sdlc/do-plan.md` addendum (this repo routes `do-plan`'s repo-specific behavior through `docs/sdlc/do-plan.md`, not `.claude/skill-context/do-plan.md` — see note below) — then delete that section from root `CLAUDE.md`.

### Flow

Root `CLAUDE.md` (always loaded) → move task-specific / duplicated content out → skill bodies and skill-context files (loaded on demand) already cover the same ground → `CLAUDE.md` shrinks, total resident context per session drops, nothing the agent needs becomes unreachable.

### Technical Approach

- **Where the Plan Requirements content actually belongs**: the issue's Solution Sketch proposed `.claude/skill-context/do-plan.md`, but this repo's `do-plan` skill already uses a *different* repo-context file — `docs/sdlc/do-plan.md` (confirmed by reading it during this plan's own execution; it is explicitly the seam `do-plan`'s generic body probes for, per its own header: "The context file is where a repo layers its planning automation onto this generic baseline... required plan sections and frontmatter fields"). `docs/sdlc/{skill}.md` is the SDLC-pipeline-stage variant of the skill-context convention (see `CLAUDE.md`'s own "Repo-specific behavior via the skill-context seam" paragraph, which names both `.claude/skill-context/{skill}.md` for non-SDLC skills *and* `docs/sdlc/{skill}.md` for SDLC pipeline skills — `do-plan` is an SDLC skill). So the correct destination is **`docs/sdlc/do-plan.md`** (append a new "## Required Plan Sections" section there, mirroring the existing "Required Plan Sections" content already partially present at its line 72-81) rather than a new `.claude/skill-context/do-plan.md`. `docs/sdlc/do-plan.md` currently documents the four required sections only as a one-paragraph-per-section summary (lines 72-81); the fuller version in root `CLAUDE.md` (with schema/examples) should be merged in, replacing the terser existing text, so there is one authoritative copy instead of two.
- Deleting the three CLAUDE.md sections (`## OfficeCLI`, `## Reading Telegram Messages`, `## Reading Email`) is a straight removal — no merge target needed since the destination skills already independently contain equal-or-greater detail. A quick pass should confirm neither skill body needs a small addition to close any gap the CLAUDE.md summary uniquely had (e.g., the `## Reading Email` summary's line "if delivery seems stuck, check `./scripts/valor-service.sh email-status`" — confirm this tip exists in `.claude/skill-context/email.md` or the `email` skill body; if not, port it over before deleting).
- `RENAMED_REMOVALS` additions: for `get-telegram-messages` and `searching-message-history`, the commit trail is unambiguous (both replaced by the `telegram` skill on 2026-02-14). For `do-design-review`, last touched 2026-04-03 by a skill-naming-standardization PR; for `audit-next-tool`, last touched 2026-04-07 (a WIP stash, not a real commit) after being added 2026-02-23 — neither has a clean single "this became that" commit. Treat both as genuine orphans per the existing `("skills", "prepare-app")` precedent's own comment ("Orphan hardlink from an old repo version — removed with no source remaining") rather than trying to force a rename mapping that the history doesn't clearly support.

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — this work touches a static tuple list and two documentation files, no runtime exception paths.

### Empty/Invalid Input Handling
N/A — no new functions or input handling introduced.

### Error State Rendering
N/A — no user-facing error states introduced.

## Test Impact

- [ ] `tests/unit/test_update_hardlinks.py` — CREATE: `test_hardlinks.py` does not exist and `test_update_hardlinks.py` has zero coverage of `RENAMED_REMOVALS`/`_cleanup_renamed`, so this is net-new coverage (not an update against a passing baseline). Add a test asserting the four new `RENAMED_REMOVALS` entries are present and that `_cleanup_renamed` removes a synthetic orphaned hardlink for each `kind`/`old_name` pair when unguarded by a live project source (respecting the inode guard at `hardlinks.py` ~lines 349-355)
- [ ] Any test asserting the current `CLAUDE.md` char count or checking for `## OfficeCLI` / `## Reading Telegram Messages` / `## Reading Email` presence (grep the test suite before starting; none is expected to exist, but confirm) — UPDATE or DELETE if found
- [ ] Plan-section validator hooks (`validate_documentation_section.py`, `validate_test_impact_section.py`, `validate_no_gos_justification.py`, and the `validate_file_contains.py` invocation in `.claude/settings.json`) — no code change, but re-run against a scratch plan file to confirm they still pass now that the canonical "Required Plan Sections" text lives in `docs/sdlc/do-plan.md` instead of root `CLAUDE.md` (the hooks read the *plan* file's content, not `CLAUDE.md`, so this should be a no-op — verify, don't assume)

## Rabbit Holes

- Do not attempt to trace a definitive rename mapping for `audit-next-tool` or `do-design-review` through PR #1894's 60-skill renovation commit — the diff is large and the mapping is genuinely ambiguous from git history alone. Treat both as clean orphan removals per the `prepare-app` precedent instead.
- Do not expand this plan into auditing the 21 separately-identified zero-usage skills (audit-hooks, audit-models, etc.) — that's a machine-local `~/.claude/settings.json` change with no code to ship, explicitly out of scope per the tracking issue's Recon Summary.
- Do not use this plan as an excuse to re-read and re-trim the rest of `CLAUDE.md` (e.g., the 109-line Quick Commands table) — the issue's Downstream section explicitly says the `gws` section and Quick Commands table were evaluated and found not to be duplicates; leave them alone.

## Risks

### Risk 1: A skill or script elsewhere still references the deleted CLAUDE.md sections by name or anchor link
**Impact:** A dangling link (e.g., `CLAUDE.md#reading-email`) somewhere in `docs/` would break.
**Mitigation:** `grep -rn "Reading Telegram Messages\|Reading Email\|## OfficeCLI" --include="*.md"` across the repo before deleting, and fix or note any inbound references found.

### Risk 2: The `_cleanup_renamed` inode guard preserves a hardlink this plan expects to remove
**Impact:** If any of the four orphaned skills are, in fact, still hardlinked to a *live* source under `.claude/skills-global/` on some machine (contradicting this plan's `ls` findings on this machine), `_cleanup_renamed`'s guard (see `scripts/update/hardlinks.py` lines 349-355) will correctly and silently preserve it rather than delete a legitimately-synced skill. This is a safety feature, not a bug, but it means "add the tuple" alone might not visibly remove anything on a machine where the guard fires.
**Mitigation:** No action needed beyond noting this in the PR description — the guard is intentional and this plan should not try to bypass it.

### Risk 3: Thin char-budget margin — CLAUDE.md could silently re-cross the 40,000 threshold
**Impact:** Post-cleanup CLAUDE.md lands at ~36,745 bytes, only ~8% under the 40,000 warning threshold. The Freshness Check already observed +226 bytes of drift within hours of filing; routine future additions could re-cross the line with no automated signal.
**Mitigation (tech-debt, optional / out of this PR's critical path):** Consider adding a `wc -c CLAUDE.md` check to `python -m tools.doctor`'s check set that warns above ~38,000 bytes (before the hard 40,000 cutoff). Reviewer may accept this as follow-up tech-debt rather than in-scope work; not gated by the Success Criteria.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2065] Disabling the 21 zero-usage-but-still-existing skills via `~/.claude/settings.json` `skillOverrides` — already filed as part of the tracking issue's context but explicitly excluded from this plan's acceptance criteria (it's a machine-local settings change with no repo code to ship).
- [EXTERNAL] Verifying the cleanup actually takes effect on *other* machines running `/update` — this plan can only verify the `RENAMED_REMOVALS` mechanism works correctly on this machine; confirming it fires correctly fleet-wide requires those machines to run `/update`, which is a human/operational action outside this plan's control.

## Update System

This IS the update system change: `scripts/update/hardlinks.py`'s `RENAMED_REMOVALS` list gains four entries, which take effect the next time any machine runs `/update` (no separate migration step — `_cleanup_renamed` runs unconditionally as part of the existing hardlink sync). No other update-system changes needed.

## Agent Integration

No agent integration required — this is a documentation and update-tooling change with no new tool surface, MCP server, or CLI entry point.

## Documentation

- [ ] Update `docs/sdlc/do-plan.md` to fold in the fuller "Required Plan Sections" content (schema + examples) currently in root `CLAUDE.md`, replacing its terser existing summary (lines 72-81)
- [ ] No `docs/features/*.md` entry needed — this is a docs/config hygiene change, not a new feature

## Success Criteria

- [ ] `scripts/update/hardlinks.py` `RENAMED_REMOVALS` contains entries for `audit-next-tool`, `do-design-review`, `get-telegram-messages`, and `searching-message-history`
- [ ] Root `CLAUDE.md` no longer contains `## OfficeCLI`, `## Reading Telegram Messages`, `## Reading Email`, or `## Plan Requirements (This Repo Only)`
- [ ] `docs/sdlc/do-plan.md` contains the full plan-section requirements (schema + examples) previously in root `CLAUDE.md`
- [ ] Root `CLAUDE.md` total size is under 40,000 characters (`wc -c CLAUDE.md`)
- [ ] The plan-section validator hooks still pass against a real scratch plan created with `/do-plan` after the change
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hardlinks-and-docs)**
  - Name: hardlinks-builder
  - Role: Add the four `RENAMED_REMOVALS` entries, delete the three duplicated CLAUDE.md sections, move Plan Requirements content into `docs/sdlc/do-plan.md`
  - Agent Type: builder
  - Resume: true

- **Validator (cleanup-validator)**
  - Name: cleanup-validator
  - Role: Confirm no dangling references to deleted sections, confirm CLAUDE.md size is under threshold, confirm plan-validator hooks still pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add RENAMED_REMOVALS entries and verify cleanup logic
- **Task ID**: build-hardlinks
- **Depends On**: none
- **Validates**: `tests/unit/test_update_hardlinks.py`
- **Assigned To**: hardlinks-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `("skills", "audit-next-tool")`, `("skills", "do-design-review")`, `("skills", "get-telegram-messages")`, `("skills", "searching-message-history")` to `RENAMED_REMOVALS` in `scripts/update/hardlinks.py`, with a comment following the existing style (e.g., grouped under a new comment "# Orphan hardlinks — source deleted, no live replacement (issue #2065)")
- CREATE a test function in `tests/unit/test_update_hardlinks.py` that, for each of the four new `(kind, old_name)` pairs, sets up a synthetic `~/.claude/skills/<name>` hardlink NOT backed by a live `.claude/skills-global/<name>` source and calls `_cleanup_renamed`, asserting the orphaned hardlink is removed. Respect and exercise the inode guard at `hardlinks.py` ~lines 349-355 (a hardlink still backed by a live source must be preserved, not deleted)
- Also assert the four new `RENAMED_REMOVALS` entries are present with the correct `(kind, old_name)` tuple shape
- Run `tests/unit/test_update_hardlinks.py` and confirm the new test passes

### 2. Grep for dangling references before deleting CLAUDE.md sections
- **Task ID**: build-grep-refs
- **Depends On**: none
- **Assigned To**: hardlinks-builder
- **Agent Type**: builder
- **Parallel**: true
- `grep -rn "Reading Telegram Messages\|Reading Email\|## OfficeCLI\|Plan Requirements (This Repo Only)" --include="*.md" .` across the repo
- Fix or note any inbound references found before proceeding

### 3. Delete duplicated CLAUDE.md sections
- **Task ID**: build-claudemd-trim
- **Depends On**: build-grep-refs
- **Assigned To**: hardlinks-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `## OfficeCLI` (lines ~35-88), `## Reading Telegram Messages` (~89-113), `## Reading Email` (~114-133) from root `CLAUDE.md` — re-check line numbers at execution time since earlier deletions shift later ones
- Confirm the "if delivery seems stuck, check `./scripts/valor-service.sh email-status`" tip from the deleted `## Reading Email` section exists somewhere in `.claude/skill-context/email.md` or the `email` skill body; port it over if missing
- Confirm the officecli/telegram skill bodies cover everything the deleted summaries did; port over any unique detail before finalizing the deletion

### 4. Migrate Plan Requirements into docs/sdlc/do-plan.md
- **Task ID**: build-plan-requirements-migrate
- **Depends On**: build-claudemd-trim
- **Assigned To**: hardlinks-builder
- **Agent Type**: builder
- **Parallel**: false
- **Destination (Open Question 1 resolved — default-and-flag):** Land the content in `docs/sdlc/do-plan.md` per the Technical Approach reasoning (`do-plan` is an SDLC skill, so `docs/sdlc/{skill}.md` is the correct seam, not `.claude/skill-context/do-plan.md`). Flag this deviation from the issue's original Solution Sketch explicitly in the PR description for reviewer sign-off.
- Replace the terse "Required Plan Sections" summary in `docs/sdlc/do-plan.md` (currently lines 72-81) with the fuller version (schema, examples) from root `CLAUDE.md`'s `## Plan Requirements (This Repo Only)` section
- Delete `## Plan Requirements (This Repo Only)` from root `CLAUDE.md`
- Confirm `docs/sdlc/do-plan.md` stays under its documented 300-line cap (per its own header comment)

### 5. Validate the full change
- **Task ID**: validate-all
- **Depends On**: build-hardlinks, build-claudemd-trim, build-plan-requirements-migrate
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `wc -c CLAUDE.md` is under 40,000
- Confirm none of the four deleted section headers remain in `CLAUDE.md`
- Confirm the four `RENAMED_REMOVALS` entries are present with correct tuple format
- Create a scratch plan via `/do-plan` (or manually construct a minimal plan file with all four required sections) and confirm the plan-section validator hooks still fire correctly
- Run full test suite and report pass/fail

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| CLAUDE.md under threshold | `wc -c CLAUDE.md \| awk '{print $1}'` | output < 40000 |
| OfficeCLI section removed | `grep -c "^## OfficeCLI" CLAUDE.md` | match count == 0 |
| Telegram section removed | `grep -c "^## Reading Telegram Messages" CLAUDE.md` | match count == 0 |
| Email section removed | `grep -c "^## Reading Email" CLAUDE.md` | match count == 0 |
| Plan Requirements section removed | `grep -c "^## Plan Requirements" CLAUDE.md` | match count == 0 |
| RENAMED_REMOVALS entries present | `grep -c "audit-next-tool\|do-design-review\|get-telegram-messages\|searching-message-history" scripts/update/hardlinks.py` | output > 0 |
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Verdict: READY TO BUILD (WITH CONCERNS) — 0 blockers, 3 concerns, 1 nit. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness + History & Consistency | Test disposition was UPDATE against non-existent `test_hardlinks.py`; real file is `test_update_hardlinks.py` with zero `RENAMED_REMOVALS`/`_cleanup_renamed` coverage | Test Impact item 1 + Task 1 | Changed to CREATE against `tests/unit/test_update_hardlinks.py`; folded the four-pair `_cleanup_renamed` test-authoring instruction (with inode-guard exercise) into Task 1 |
| CONCERN | Scope & Value | Open Question 1 unresolved but Task 4 already committed to `docs/sdlc/do-plan.md` | Task 4 precondition + Open Questions | Resolved default-and-flag: land in `docs/sdlc/do-plan.md`, flag deviation in PR description for reviewer sign-off |
| CONCERN | Risk & Robustness | Thin ~8% char-budget margin below 40k threshold, no regression guard | Risk 3 | Added optional tech-debt note: `wc -c CLAUDE.md` warn-at-38k check in `python -m tools.doctor`; not gated by Success Criteria |
| NIT | Risk & Robustness | Freshness Check cited Plan Requirements at line 517; actual is 518 | Freshness Check | Corrected to 518 |

---

## Open Questions

Both prior open questions are resolved as of the post-critique revision:

1. **Destination for the "Plan Requirements" content — RESOLVED (default-and-flag).** Content lands in `docs/sdlc/do-plan.md` per the Technical Approach reasoning (`do-plan` is an SDLC skill, so `docs/sdlc/{skill}.md` is its repo-context seam, not `.claude/skill-context/do-plan.md`). The deviation from the issue's original Solution Sketch will be flagged in the PR description for reviewer sign-off (see Task 4).
2. **Orphan-removal treatment for `audit-next-tool` / `do-design-review` — RESOLVED.** Treated as clean orphan removals per the existing `("skills", "prepare-app")` precedent; tracing a definitive rename mapping is explicitly a Rabbit Hole. The `_cleanup_renamed` inode guard makes this safe: if any of these names is in fact still backed by a live source on some machine, the guard preserves it.
