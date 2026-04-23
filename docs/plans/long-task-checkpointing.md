---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1130
last_comment_id:
---

# Long-Task Checkpointing — PROGRESS.md + Commit-Frequency Guidance in Dev Prompts

## Problem

Long-running dev sessions (multi-hour builds for Medium/Large plans) routinely cross context-compaction boundaries. When compaction fires, the SDK replaces the raw conversation history with a summary — and that summary rarely preserves high-fidelity working state ("I was mid-edit of `process_batch()` in `agent/session_executor.py:214`; next step was to add the `num_turns` snapshot before the `await`"). The agent resumes the session against the summary, drifts, and repeats work that was already done, or abandons it.

The infrastructure side of compaction was just hardened by #1127 — PreCompact hook writes a JSONL backup, a 5-minute cooldown prevents thrashing, a 30-second nudge-guard prevents the nudge loop from racing the compaction. That prevents a mid-compact crash from losing the session. It does **not** prevent semantic drift across a compaction.

The remaining gap is prompt-level: the dev session prompt does not instruct the agent to externalize its own working state into durable, post-compaction-readable artifacts. The only existing guidance is a single `[WIP]` commit example in `builder.md:159-169` (safety-net commits before limits) — there is no systematic instruction to commit frequently during normal work, and no instruction to maintain any form of externalized progress journal.

**Current behavior:**

- `.claude/agents/builder.md:156-169` has a "Safety Net — Commit Before Exit" section but it fires only at failure / turn-limit / context-limit edges. Normal working state is not committed.
- `.claude/skills/do-build/SKILL.md:203` says "Commits at logical checkpoints throughout Implement — not batched at end" — this is a one-line reminder, not systematic guidance, and the builder prompt does not echo it.
- Zero references to `PROGRESS.md` or any equivalent externalized-state file anywhere in `.claude/` (verified via grep: "No files found").
- The dev session persona (`~/Desktop/Valor/personas/developer.md`, loaded via `load_persona_prompt("developer")` at `agent/sdk_client.py:699`) provides escalation rules and autonomous-execution guidance but says nothing about compaction survival.
- Compaction-hardening ships JSONL backups at `~/.claude/projects/{proj}/sessions/backups/{uuid}-{ts}.jsonl.bak` — these recover the *session transcript* (replayable via `--resume`), but the agent still re-enters against a compacted summary, not the backup. The backup is a crash-recovery artifact, not an in-flight memory aid.

**Desired outcome:**

- Dev sessions externalize working state in two mutually-reinforcing channels: (a) **frequent git commits** — every meaningful unit of work is committed, not only at failure boundaries; (b) a **top-level `PROGRESS.md` file** in the worktree — a plain-text scratchpad tracking Done / In-progress / Left, re-read on session start and updated at checkpoints.
- `PROGRESS.md` is a **working-memory scratchpad only**, not ground truth. Ground truth for progress = the plan doc + `git log --oneline main..HEAD`. Ground truth for scope = the plan doc. `PROGRESS.md` helps the agent pick up where it left off after compaction; it supplements but never replaces file-readable signals.
- `PROGRESS.md` is **gitignored** — it stays in the worktree filesystem during the session but does not pollute PR diffs or session-branch history.
- After a compaction, the agent's next turn re-reads `PROGRESS.md` and `git log --oneline main..HEAD` to re-anchor itself, instead of relying on the (lossy) compacted summary.
- `/do-build` has a soft gate: if `PROGRESS.md` is missing at the session-branch HEAD when build reports "implementation complete," log a warning (not a hard fail). This treats PROGRESS.md as a nudge, not a blocker — the worker output still ships.
- The example `PROGRESS.md` shape is documented in `docs/features/long-task-checkpointing.md` so future contributors can reproduce it.

## Freshness Check

**Baseline commit:** `ceedbe68b76337baa317a719ef217e13f3b82852` (worktree HEAD on branch `session/long-task-checkpointing`)
**Main baseline:** `736e09ac` (latest main)
**Issue filed at:** 2026-04-22T17:00:29Z (~17 hours before plan time)
**Disposition:** Unchanged

**File:line references re-verified at `ceedbe68`:**
- `.claude/agents/builder.md:167` — the issue cites "a single `[WIP]` commit example but no systematic guidance." Verified: the `[WIP]` example is at line 159-169 (not 167; line number drifted slightly but claim holds). Updated reference.
- `.claude/skills/do-build/SKILL.md:203` — "Commits at logical checkpoints" rule. Verified present.
- `agent/sdk_client.py:699` — `load_persona_prompt("developer")` — verified exact.
- `agent/agent_definitions.py:92-99` — builder definition loads `builder.md` body verbatim. Verified exact.

**Cited sibling issues re-checked:**
- **#1107** — closed 2026-04-22T17:01:25Z as superseded by this issue. No code shipped.
- **#1127** ("compaction hardening") — closed 2026-04-22T21:41:24Z, merged as PR #1135 (`a13b7470`). Shipped JSONL backup, 5-minute cooldown, 30-second nudge guard. This is the *infrastructure* side of compaction survival; #1130 is the *prompt* side. No overlap — complementary work.

**Commits on main since issue was filed (touching referenced files):** none.
`git log --since=2026-04-22T17:00:29Z -- .claude/agents/builder.md .claude/commands/ .claude/skills/do-build/ agent/sdk_client.py agent/agent_definitions.py` returned empty.

**Active plans in `docs/plans/` overlapping this area:** none.
`grep -l "PROGRESS.md\|compaction\|long-task" docs/plans/*.md` returned nothing relevant. The `watchdog-hardening` plan (#1136, in progress) touches the session executor but is orthogonal — it concerns idle-probe detection and per-session token tracking at the queue layer, not prompt content.

**Notes:** `.claude/commands/do-build.md` referenced in the issue body does not exist in this repo — the do-build skill lives at `.claude/skills/do-build/SKILL.md` (plus `WORKFLOW.md` and `PR_AND_CLEANUP.md`). The plan updates the skill path to the real location. Acceptance criteria wording in the issue remains valid.

## Prior Art

Closed issues touching the same concern:

- **#1107** — "Reliability risk: long-running sessions — no progress checkpointing to survive context compaction." Closed 2026-04-22 as the seed issue for #1130. No code shipped. The scope was refined in #1130 into two concrete artifacts (PROGRESS.md + commit-frequency guidance) with testable acceptance criteria.
- **#1127** — "Reliability: compaction hardening — JSONL backup, cooldown, post-compact nudge guard." Shipped 2026-04-22 in PR #1135 (`a13b7470`). **Complementary, not overlapping.** #1127 makes compaction *crash-safe* at the SDK layer (the backup). #1130 makes compaction *semantically survivable* at the prompt layer (the agent knows what to do post-compact). Neither is sufficient alone.
- **#1102 / #1103** — earlier reliability-risk scouts (closed 2026-04-22). Both subsumed by #1127.

No prior attempt at prompt-level checkpointing exists. This is greenfield.

## Research

External research is not required — this is an internal prompt-engineering change. Every signal (builder prompt content, persona loader wiring, compaction behavior) is visible in the repo. Proceeding with codebase context.

**Background worth naming:**
- The "externalize working state" pattern is a recognized mitigation in long-horizon agent setups (e.g., Anthropic's Claude Code docs recommend frequent commits; OpenAI Agents SDK patterns for long tasks similarly rely on durable files). Concrete enforcement via prompt + soft gate is the incremental step here.
- The compaction-hardening doc (`docs/features/compaction-hardening.md`) documents the backup/cooldown/guard contract. This plan's feature doc will cross-reference it so the combined mechanism is discoverable.

## Data Flow

For reference — the PROGRESS.md lifecycle inside a dev session:

1. **Session start** — Dev session spawned by PM session via `python -m tools.valor_session create --role dev --slug {slug}`. Worker harness sets CWD to `.worktrees/{slug}/`. System prompt includes `builder.md` body (for sub-agents) and developer persona overlay (for top-level session).
2. **First turn (after reading plan)** — Agent follows the new "Working-state externalization" prompt section: creates `PROGRESS.md` at worktree root with three sections (Done / In-progress / Left), populated from the plan's Step by Step Tasks. `PROGRESS.md` is already gitignored — it never appears in `git status` output.
3. **During work** — After each meaningful unit (completed task, completed sub-step), agent (a) commits code to the session branch, (b) moves the task entry from "In-progress" to "Done" in PROGRESS.md and updates "In-progress" to the next item. PROGRESS.md update is a filesystem write only; it is not added to git commits.
4. **Compaction fires** — PreCompact hook takes the JSONL backup (existing, #1127). Compacted summary replaces conversation history.
5. **First turn post-compaction** — The new prompt instruction "On session start or resumption, re-read `PROGRESS.md` and `git log --oneline main..HEAD`" fires. Agent re-orients from the scratchpad + git log, not the compacted summary. If PROGRESS.md is absent (e.g., worktree was recreated), agent falls back to the plan doc and `git log --oneline main..HEAD` — both are directly accessible from the worktree and authoritative.
6. **Session completion** — `/do-build` Step 5 (Definition of Done) now runs a soft check: `[ -f .worktrees/{slug}/PROGRESS.md ]`. If missing, log a warning but do not block PR creation.

## Architectural Impact

- **No new dependencies.** Prompt edits only, plus a soft-check shell line in `/do-build`, plus a .gitignore entry.
- **Interface changes**: none. PROGRESS.md is a convention, not an interface.
- **Coupling**: decreases — making post-compaction recovery independent of SDK summary fidelity.
- **Data ownership**: PROGRESS.md lives inside the worktree filesystem only. It is explicitly NOT committed to the session branch — it is ephemeral working memory, not a source of truth.
- **Reversibility**: trivially reversible. Prompt edits are revertable in one commit. No state migration.

## Appetite

**Size:** Small

**Team:** Solo dev + code reviewer (the dev session doing the prompt edits; reviewer at /do-pr-review).

**Interactions:**
- PM check-ins: 1 (plan-doc-review before /do-plan-critique; Tom answers Open Questions)
- Review rounds: 1 (standard PR review)

**Why Small:** five deliverables (builder.md, developer persona overlay, do-build SKILL.md, .gitignore entry, feature doc), one integration test. Zero new code paths, zero dependencies, zero schema changes. The hardest part is keeping the prompt deltas terse enough to not inflate token counts.

## Prerequisites

No prerequisites — all touched files exist and are readable.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Builder prompt present | `test -f .claude/agents/builder.md` | Target of the primary prompt edit |
| Do-build skill present | `test -f .claude/skills/do-build/SKILL.md` | Target of soft-check addition |
| Dev persona overlay present | `test -f ~/Desktop/Valor/personas/developer.md` | Target of consistent persona edit (authoritative source is ~/Desktop/Valor/) |
| .gitignore present | `test -f .gitignore` | Target of PROGRESS.md entry |

Run all checks: `python scripts/check_prerequisites.py docs/plans/long-task-checkpointing.md`

## Solution

### Key Elements

- **Working-state externalization prompt section** — added to `.claude/agents/builder.md` (affects builder sub-agents spawned by `/do-build` team-lead orchestration). ~40-60 lines, written tersely.
- **Consistent update to developer persona overlay** — `~/Desktop/Valor/personas/developer.md` receives the same working-state externalization principles as `builder.md`. Content is consistent but not verbatim duplicate. Persona overlay is iCloud-synced; the change propagates automatically. Both files must align because a PM agent may spawn the dev session as either a top-level developer persona or a builder sub-agent — the behavior should be identical.
- **PROGRESS.md convention** — a single top-level file in the worktree with three H2 sections: `## Done`, `## In progress`, `## Left`. **Gitignored** (never committed). A working-memory scratchpad only, not a source of truth. If PROGRESS.md is absent, the agent falls back to the plan doc and `git log --oneline main..HEAD` — both are directly readable from the worktree.
- **Post-compaction re-orientation instruction** — in the same prompt section, an explicit "on session start or resumption, `cat PROGRESS.md` and `git log --oneline main..HEAD` before continuing."
- **.gitignore entry** — `PROGRESS.md` added to the repo `.gitignore` so it never accidentally appears in commits or PR diffs.
- **Soft gate in `/do-build`** — a warn-not-block check that PROGRESS.md exists at PR time. Runs after Definition of Done, before PR creation.
- **Feature doc** — `docs/features/long-task-checkpointing.md` with the example PROGRESS.md skeleton, rationale, scratchpad vs. ground-truth distinction, and cross-link to `docs/features/compaction-hardening.md`.
- **Integration test** — reads `.claude/agents/builder.md` and asserts presence of the new section markers (e.g., `## Working-state externalization`, `PROGRESS.md`). Token-count tests assert deltas are within soft and hard limits. A separate integration test stubs the /do-build soft-check path.

### Flow

**Dev session spawns in worktree** → reads plan + builder prompt → creates `PROGRESS.md` (gitignored, scratchpad only) with plan tasks populated → works on first task → commits code only (PROGRESS.md stays local) → updates PROGRESS.md in-place → compaction fires → (JSONL backup takes, summary replaces history) → agent's next turn reads `PROGRESS.md` + `git log` → continues from recorded in-progress line → finishes → /do-build soft-check sees PROGRESS.md → PR opens.

### Technical Approach

1. **Prompt edit to `.claude/agents/builder.md`** — add `## Working-state externalization` section after the existing "Safety Net — Commit Before Exit" block (line ~169). Content covers:
   - Create `PROGRESS.md` at worktree root on session start if absent. `PROGRESS.md` is a scratchpad — gitignored, never committed. Ground truth for working state is the plan doc and `git log --oneline main..HEAD`; PROGRESS.md is a convenience aid, not authoritative.
   - Three H2 sections: Done, In progress, Left.
   - Commit **code** after each meaningful unit of work. Update `PROGRESS.md` in the same turn but do NOT add it to the commit (it is gitignored — `git add -A` will silently omit it).
   - On session start or post-compaction resumption, read `PROGRESS.md` and `git log --oneline main..HEAD` BEFORE acting on anything else. If PROGRESS.md is absent, fall back to the plan doc and `git log` — both are directly readable from the worktree. The compacted summary may be lossy.
   - Target 40-60 lines of terse imperative English.
   - **Soft limit: +5000 chars** over current baseline — a lint-style warning asserted in a separate test.
   - **Hard limit: +10000 chars** over current baseline — test failure if exceeded.

2. **Consistent edit to developer persona overlay** (`~/Desktop/Valor/personas/developer.md`) — add the same working-state externalization principles as `builder.md`. The content need not be verbatim duplicate but must convey the same behavior: create PROGRESS.md scratchpad on session start, commit code frequently, re-read PROGRESS.md + git log on resumption. This covers top-level dev sessions that don't load `builder.md` (e.g., direct "build this" conversations routed to the developer persona rather than a builder sub-agent). Persona overlay is iCloud-synced; the change propagates automatically.

3. **Add `PROGRESS.md` to `.gitignore`** — a single line in the repo root `.gitignore`. This prevents accidental commits of the scratchpad file. Verify with `git check-ignore -v PROGRESS.md` returning a match.

4. **Note in `.claude/skills/do-build/SKILL.md`** — add a one-line bullet in the "Critical Rules" list pointing to PROGRESS.md as the standard in-session scratchpad (gitignored working memory, not authoritative progress record), plus a soft-check step inserted after Step 5.5 (CWD Safety Reset) and before Step 6 (Documentation Gate):
   ```
   **5.6 PROGRESS.md soft check** — `[ -f .worktrees/{slug}/PROGRESS.md ] || echo "[warn] No PROGRESS.md at worktree root — not blocking, but recovery from compaction may be degraded next run."`
   ```
   The check does not gate PR creation.

5. **Feature doc `docs/features/long-task-checkpointing.md`** — includes:
   - Rationale (the semantic vs. crash-safe distinction vs. #1127)
   - The scratchpad vs. ground-truth distinction: PROGRESS.md is ephemeral working memory; SDLC stages are authoritative progress; plan doc is authoritative scope
   - Example PROGRESS.md skeleton (exact markdown)
   - Cross-reference to `docs/features/compaction-hardening.md`
   - Index entry added to `docs/features/README.md`

6. **Unit test `tests/unit/test_long_task_checkpointing.py`** — four checks (pure file-read string assertions; placed in `tests/unit/` for fast CI feedback with no subprocess or external dependency):
   - `test_builder_prompt_has_externalization_section`: asserts `"## Working-state externalization"` appears in `.claude/agents/builder.md` and `"PROGRESS.md"` appears at least 3 times.
   - `test_builder_prompt_soft_limit`: asserts `len(builder_md_text) < BASELINE_CHARS + 5000`. Emits a warning-style assert message if exceeded. `BASELINE_CHARS = 11029` (pinned at commit `ceedbe68`).
   - `test_builder_prompt_hard_limit`: asserts `len(builder_md_text) < BASELINE_CHARS + 10000`. Test fails if exceeded.
   - `test_progress_md_in_build_soft_check`: reads `.claude/skills/do-build/SKILL.md` and asserts a line matching the pattern `\[ -f .*PROGRESS\.md .* \] \|\| echo` is present (the soft-check line).

   These are structural assertions, not behavioral — we are not spinning up a full dev session in CI (too slow, too flaky). The structural assertion proves the prompts contain the required guidance; the behavioral outcome (agents actually create PROGRESS.md) is monitored post-merge via the soft-check log line.

## Failure Path Test Strategy

### Exception Handling Coverage

- [x] No new exception handlers introduced. The only new code path is the soft-check shell line in /do-build, which uses `|| echo` — errors become warnings by construction. No `except Exception: pass` blocks added.

### Empty/Invalid Input Handling

- [ ] Test: if `builder.md` is missing entirely, `_parse_agent_markdown` (agent/agent_definitions.py:49-57) already handles this with a fallback prompt and a warning log. Our changes do not alter that path — no new test needed.
- [ ] Test: if `PROGRESS.md` is present but empty, the soft-check in /do-build (`[ -f ... ] || echo ...`) still passes (file exists). Deliberate — empty PROGRESS.md is degraded but not broken. A future follow-up could assert non-empty; out of scope here.
- [ ] Test: if `PROGRESS.md` is absent (deleted or never created), agent falls back to plan doc + SDLC stages. The prompt explicitly lists the fallback chain. Structural test (`test_builder_prompt_has_externalization_section`) verifies the fallback instruction is present in the prompt.

### Error State Rendering

- [ ] The soft-check's warning output appears in the /do-build orchestrator log stream. If PROGRESS.md is missing, the warning is visible in the PR comment (via the orchestrator's completion message). Test: add a stub /do-build invocation in an integration test that runs the soft-check line against a fake empty worktree and asserts the warning is printed. (This test is in Step 6 of Step by Step Tasks.)

## Test Impact

- [ ] `tests/unit/test_long_task_checkpointing.py` — CREATE: four structural file-read assertions per Technical Approach Step 6 (placed in `tests/unit/` since these are pure string assertions with no external dependencies; token budget changed from +3000 to soft 5K / hard 10K; `BASELINE_CHARS = 11029` pinned at commit `ceedbe68`).
- [ ] `tests/unit/test_agent_definitions.py` — UPDATE if a test asserts exact line count of `builder.md`; otherwise leave untouched. Verified at plan time: `grep -n 'line.count\|wc -l\|len(.*splitlines' tests/unit/test_agent_definitions.py` returns no exact-count assertions. No update required.
- [ ] No other test files are affected. Changes are prompt text, a .gitignore line, and one skill line; behavior at the Python layer is unchanged.

## Rabbit Holes

- **Hard-fail the build if PROGRESS.md is missing.** Tempting as "rigor" but creates a new failure mode (builds abort on sessions where the agent simply forgot to create the file) without actually fixing working-state drift. The soft-check is strictly better: it nudges without introducing a new red light. If six months of data shows adoption is catastrophically low, revisit.
- **A dedicated `tools/progress_md.py` helper.** Tempting as "structured writing" but the whole point of PROGRESS.md is that it is a plain markdown file the agent writes to directly. Adding a tool introduces a new API surface and an indirection the agent has to remember to use. Direct Write/Edit is the right call.
- **Auto-injection of "re-read PROGRESS.md" after compaction via a PostCompact hook.** Tom endorsed this as "the right nudge" — filed as **#1139** (backlog). This plan stays scoped to prompt-instruction-only (#1130). The PostCompact hook follow-up is tracked in #1139 and can be implemented independently without blocking this plan. Reference: if #1139 ships, the hook and the prompt instruction are complementary — the prompt establishes baseline behavior; the hook reinforces it at the exact moment compaction fires.
- **Extending PROGRESS.md with YAML frontmatter (status, owner, created_at).** Violates the "simple scratchpad the agent writes freely" principle. Adds maintenance burden for near-zero benefit. Deferred.
- **Multi-worktree PROGRESS.md aggregation** (e.g., a `docs/PROGRESS.md` that tracks all worktrees). Out of scope; solves a different problem (fleet visibility).
- **Using PROGRESS.md as the source of truth for SDLC progress reporting.** Explicitly rejected by Tom: the plan doc + git log are ground truth; PROGRESS.md is only a working-memory aid. Wiring PROGRESS.md into any progress-reporting path would create conflicting authority.

## Risks

### Risk 1: Prompt token inflation degrades dev-session performance

**Impact:** Every additional token in the builder prompt consumes context budget on every turn. A 60-line section is ~600-800 tokens; across a multi-hour dev session with many turns, this is non-trivial. The issue body calls out: "Prompt changes must not materially inflate system-prompt token count."

**Mitigation:** Two-tier budget enforcement: soft limit (+5000 chars / ~1250 tokens) triggers a test warning; hard limit (+10000 chars / ~2500 tokens) fails the test. Tom confirmed: "builder can have a lot of leeway especially when combined with strong models like Opus." Actual added content is targeted at 40-60 lines of terse, imperative English. No examples, no rationale paragraphs — the rationale lives in the feature doc, the prompt carries only the instructions.

### Risk 2: Agents create `PROGRESS.md` once and never update it

**Impact:** A stale PROGRESS.md is worse than none — the post-compaction agent reads it and gets misleading "In-progress" state. Agent drifts anyway.

**Mitigation:** The prompt pairs "commit after each meaningful unit" with "update PROGRESS.md in the same turn when possible" — linking the two acts reduces the chance of one without the other. Post-merge, monitor via the soft-check log to see how many sessions close with a non-trivial PROGRESS.md (file exists AND has been updated at least once after creation). If adoption is weak, tighten the prompt in a follow-up.

### Risk 3: Developer persona overlay edits are blocked by the "no edits from plain CLI" constraint

**Impact:** The issue warns that `.claude/agents/*.md` and `.claude/commands/*.md` edits "must run inside an Agent-SDK-powered dev session" because the harness blocks self-modification from a plain Claude Code CLI. If this plan is executed from a plain CLI by accident, the edits fail silently.

**Mitigation:** This plan is routed through `/do-build`, which spawns an Agent-SDK-powered dev session in an isolated worktree. The harness block only triggers in non-SDK Claude Code runs. The Test Impact integration test verifies post-edit that the expected content landed — any silent edit failure is caught by that test failing.

### Risk 4: PROGRESS.md gitignore entry causes confusion if an agent tries to commit it

**Impact:** `git add PROGRESS.md` will be silently ignored; `git add -A` will omit it. If the agent explicitly tries to stage PROGRESS.md (e.g., includes it in a commit), the gitignore prevents it. This is intentional but could confuse an agent that expects the file to appear in `git status`.

**Mitigation:** The prompt explicitly states "PROGRESS.md is gitignored — do NOT attempt to add it to git commits." The gitignore acts as a hard guardrail. The feature doc explains the rationale. If an agent is confused by the missing file in `git status`, that's expected behavior.

## Race Conditions

No race conditions identified — all operations are synchronous (prompt file edits, test assertions, a single soft-check shell line). PROGRESS.md writes happen from within a single dev session that owns its worktree exclusively, so no concurrent-writer hazard exists. The /do-build soft-check reads PROGRESS.md at a well-defined step (post-Definition-of-Done, pre-PR), not concurrently with the dev session.

## No-Gos (Out of Scope)

- **Hard-fail if PROGRESS.md absent.** Soft warn only — see Rabbit Holes. If adoption data warrants hardening, that is a separate issue.
- **PostCompact hook auto-injecting "re-read PROGRESS.md" nudge.** Tracked as #1139 (backlog). This plan is prompt-only (#1130). See Rabbit Holes for the reasoning and cross-reference.
- **Per-worktree or per-session dashboards showing PROGRESS.md contents.** Not the problem being solved. A future observability feature.
- **Auto-generating PROGRESS.md from the plan document.** The agent writes it. If we auto-generate, we lose the forcing function of the agent having to reason about its own working state.
- **Committing PROGRESS.md to the session branch.** PROGRESS.md is gitignored by design. It is ephemeral working memory, not a historical record. If the worktree is cleaned up, the scratchpad goes with it — that is intentional.
- **Migrating existing in-flight worktrees to have PROGRESS.md.** Agents create their own on first turn of new sessions. No backfill needed.

## Update System

No update system changes required. This feature is purely internal — prompt edits, a .gitignore line, and one test file. The `/update` skill propagates git pulls; when users update, the new prompts ship automatically via the pulled `.claude/agents/builder.md`. No new dependencies, no config propagation, no migration steps.

## Agent Integration

No agent integration required — this is a prompt-engineering change. The agent already reads `builder.md` via `agent/agent_definitions.py` for sub-agent spawning and the developer persona overlay via `agent/sdk_client.py:load_persona_prompt()` for top-level dev sessions. The new prompt content flows through the existing loaders. No MCP server, no `.mcp.json` change, no bridge change, no new tool.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/long-task-checkpointing.md` describing:
  - What problem PROGRESS.md solves (post-compaction semantic drift)
  - The scratchpad vs. ground-truth distinction (PROGRESS.md is ephemeral; SDLC stages are authoritative progress; plan doc is authoritative scope)
  - How it complements `docs/features/compaction-hardening.md` (crash-safe vs. semantically-safe)
  - The exact PROGRESS.md skeleton agents should produce
  - How the /do-build soft-check works
  - Pointer to the relevant prompt section in `.claude/agents/builder.md`
- [ ] Add entry to `docs/features/README.md` index table, linking to the new doc.

### Inline Documentation

- [ ] Update the docstring on `load_system_prompt()` in `agent/sdk_client.py:681` if the new prompt section warrants a mention (likely not — the docstring stays generic).

### External Documentation Site

- This repo does not use Sphinx/MkDocs externally. Skip.

## Success Criteria

- [ ] `.claude/agents/builder.md` contains a `## Working-state externalization` section with commit-frequency guidance and PROGRESS.md creation/update/re-read instructions. Scratchpad nature and gitignore status are explicitly stated.
- [ ] `~/Desktop/Valor/personas/developer.md` contains consistent working-state externalization principles — same behavior, possibly different prose.
- [ ] `.gitignore` contains a `PROGRESS.md` entry. `git check-ignore -v PROGRESS.md` returns a match.
- [ ] `.claude/skills/do-build/SKILL.md` references PROGRESS.md as the standard in-session scratchpad AND contains a soft-check step that warns (does not fail) if PROGRESS.md is missing at PR time.
- [ ] `docs/features/long-task-checkpointing.md` exists and contains an example PROGRESS.md skeleton and the scratchpad-vs-ground-truth distinction.
- [ ] `docs/features/README.md` has a new entry linking to the feature doc.
- [ ] `tests/unit/test_long_task_checkpointing.py` passes and includes the four structural assertions (section presence, soft-limit warning, hard-limit ceiling, soft-check line presence). `BASELINE_CHARS = 11029` hardcoded.
- [ ] `builder.md` character count has grown by no more than **+10000 chars** from baseline (hard limit); soft warning if over **+5000 chars**.
- [ ] Tests pass (`pytest tests/unit/test_long_task_checkpointing.py -v`).
- [ ] `python -m ruff check .` and `python -m ruff format --check .` pass.
- [ ] Documentation gate in /do-build passes (feature doc + index entry present).

## Team Orchestration

### Team Members

- **Prompt Editor**
  - Name: `prompt-editor`
  - Role: Edit `.claude/agents/builder.md`, `~/Desktop/Valor/personas/developer.md`, and `.claude/skills/do-build/SKILL.md` to add PROGRESS.md guidance and soft-check. Also adds PROGRESS.md to `.gitignore`.
  - Agent Type: builder
  - Resume: true

- **Feature Doc Writer**
  - Name: `feature-doc-writer`
  - Role: Create `docs/features/long-task-checkpointing.md` and update `docs/features/README.md` index.
  - Agent Type: documentarian
  - Resume: true

- **Test Author**
  - Name: `test-author`
  - Role: Write `tests/integration/test_long_task_checkpointing.py` with four structural assertions.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `checkpoint-validator`
  - Role: Run tests, verify prompt edits landed, verify feature doc renders.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Edit builder prompt

- **Task ID**: build-prompt-edit
- **Depends On**: none
- **Validates**: `tests/integration/test_long_task_checkpointing.py::test_builder_prompt_has_externalization_section` (create)
- **Assigned To**: prompt-editor
- **Agent Type**: builder
- **Parallel**: true
- Add `## Working-state externalization` H2 section to `.claude/agents/builder.md`, immediately after the existing "Safety Net — Commit Before Exit" block.
- Section content (target: 40-60 lines, terse imperative English, no examples):
  - "On session start, if `PROGRESS.md` does not exist at the worktree root, create it with three H2 sections: `## Done` (empty), `## In progress` (first task from the plan), `## Left` (remaining tasks from the plan). `PROGRESS.md` is a scratchpad — gitignored, never committed. Ground truth for working state is the plan doc and `git log --oneline main..HEAD`; PROGRESS.md is a convenience aid only."
  - "After each meaningful unit of work — a completed task, a passing test, a validated sub-step — commit **code** to the session branch. Update `PROGRESS.md` in the same turn but do NOT add it to the commit (it is gitignored and will be silently omitted from `git add -A`)."
  - "`[WIP]` commit prefix is allowed and encouraged for partial steps. Frequent small commits are preferred over large batched commits."
  - "On session start or resumption (including after context compaction), read `PROGRESS.md` and `git log --oneline main..HEAD` BEFORE acting on any other instruction. If `PROGRESS.md` is absent, fall back to the plan doc and `git log` — both are directly readable from the worktree. The compacted summary may be lossy."
- Verify character delta is under +5000 (soft) and +10000 (hard).

### 2. Edit developer persona overlay

- **Task ID**: build-persona-edit
- **Depends On**: none
- **Validates**: Success criterion for developer.md
- **Assigned To**: prompt-editor
- **Agent Type**: builder
- **Parallel**: true
- Edit `~/Desktop/Valor/personas/developer.md` to add working-state externalization principles consistent with the builder.md section above.
- Content must convey the same behavior: create PROGRESS.md scratchpad on session start (gitignored, not ground truth), commit code frequently, re-read PROGRESS.md + git log on resumption, fall back to plan doc + git log if PROGRESS.md absent.
- Prose can differ from builder.md — this is a persona overlay, not a builder prompt. Keep it terse (≤20 lines). No duplication needed; consistency required.

### 3. Add PROGRESS.md to .gitignore

- **Task ID**: build-gitignore-edit
- **Depends On**: none
- **Validates**: `git check-ignore -v PROGRESS.md` returns a match
- **Assigned To**: prompt-editor
- **Agent Type**: builder
- **Parallel**: true
- Add `PROGRESS.md` to the repo root `.gitignore` (or `/.gitignore` for a root-only pattern).
- Verify the entry is present and `git check-ignore -v PROGRESS.md` returns a non-empty result.

### 4. Add PROGRESS.md soft-check to /do-build

- **Task ID**: build-dobuild-edit
- **Depends On**: none
- **Validates**: `tests/integration/test_long_task_checkpointing.py::test_progress_md_in_build_soft_check` (create)
- **Assigned To**: prompt-editor
- **Agent Type**: builder
- **Parallel**: true
- Edit `.claude/skills/do-build/SKILL.md`:
  - In the "Critical Rules" section (line ~193), add a bullet: `**PROGRESS.md is the standard in-session scratchpad** — dev sessions maintain it at the worktree root per builder.md's "Working-state externalization" section. It is gitignored (not committed). Missing PROGRESS.md is a warning, not a blocker. The plan doc and git log remain the authoritative progress record.`
  - Add a new step 5.6 inserted **after** Step 5.5 (CWD Safety Reset) and **before** Step 6 (Documentation Gate): a soft-check shell line that warns if PROGRESS.md is absent at `.worktrees/{slug}/PROGRESS.md`. Use `[ -f ... ] || echo "..."` form so the check never returns nonzero. (In SKILL.md, CWD Safety Reset is ~line 333; Documentation Gate starts ~line 343 — insert between them.)

### 5. Write feature doc

- **Task ID**: build-feature-doc
- **Depends On**: none
- **Validates**: docs gate in /do-build (Step 6.1: `validate_docs_changed.py`)
- **Assigned To**: feature-doc-writer
- **Agent Type**: documentarian
- **Parallel**: true
- **Before writing**: verify `docs/features/compaction-hardening.md` exists in the worktree (it shipped on main in PR #1135 but may not be in the branch yet). If absent, run `git fetch origin main && git checkout origin/main -- docs/features/compaction-hardening.md` to pull it in before writing the cross-link.
- Create `docs/features/long-task-checkpointing.md`:
  - Opening paragraph: what and why, distinguishing from compaction-hardening.
  - Section: "The PROGRESS.md convention" — scratchpad nature, gitignored, not ground truth; the three-H2 structure with an exact markdown skeleton.
  - Section: "Source of truth hierarchy" — plan doc + git log (progress), plan doc (scope), PROGRESS.md (working-memory scratchpad only — never authoritative).
  - Section: "How it works" — dev session creates on first turn, updates at checkpoints, re-reads on resumption.
  - Section: "Soft-check in /do-build" — what the warning looks like, when it fires.
  - Section: "See also" — link to `docs/features/compaction-hardening.md`, `.claude/agents/builder.md`, and issue #1139 (PostCompact hook follow-up).
- Update `docs/features/README.md`: add a new row to the index table with the title, a one-line description, and the link.

### 6. Write integration test

- **Task ID**: build-test
- **Depends On**: none
- **Assigned To**: test-author
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_long_task_checkpointing.py` with four tests (structural file-read assertions — placed in `tests/unit/` for fast CI feedback):
  - `test_builder_prompt_has_externalization_section` — reads `.claude/agents/builder.md` and asserts `"## Working-state externalization"` appears, plus asserts `"PROGRESS.md"` appears at least 3 times (create/update/re-read mentions).
  - `test_builder_prompt_soft_limit` — asserts `len(builder_md_text) < BASELINE_CHARS + 5000`. Emits a descriptive assert message on failure to indicate soft-limit violation.
  - `test_builder_prompt_hard_limit` — asserts `len(builder_md_text) < BASELINE_CHARS + 10000`. Hard failure — test fails if exceeded.
  - `test_progress_md_in_build_soft_check` — reads `.claude/skills/do-build/SKILL.md` and asserts a line matching the pattern `\[ -f .*PROGRESS\.md .* \] \|\| echo` is present (the soft-check line).
- Mark all four with `@pytest.mark.unit` and `@pytest.mark.sdlc`.
- Use `BASELINE_CHARS = 11029` (pinned: pre-edit size of `builder.md` at commit `ceedbe68`). Do NOT measure current file size at test-write time — tasks are parallel and builder.md may already be edited by the time this test is written.

### 7. Final validation

- **Task ID**: validate-all
- **Depends On**: build-prompt-edit, build-persona-edit, build-gitignore-edit, build-dobuild-edit, build-feature-doc, build-test
- **Assigned To**: checkpoint-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_long_task_checkpointing.py -v` — all four tests pass.
- Run `python -m ruff check .` — zero errors.
- Run `python -m ruff format --check .` — zero format issues.
- Verify `.claude/agents/builder.md` ends with trailing newline and no broken markdown.
- Verify `docs/features/README.md` new row is well-formed (no broken table syntax).
- Verify `git check-ignore -v PROGRESS.md` returns a match.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Builder prompt has externalization section | `grep -c "## Working-state externalization" .claude/agents/builder.md` | output > 0 |
| Builder prompt mentions PROGRESS.md | `grep -c "PROGRESS.md" .claude/agents/builder.md` | output > 2 |
| Builder prompt mentions scratchpad | `grep -c "scratchpad\|gitignore" .claude/agents/builder.md` | output > 0 |
| Do-build references PROGRESS.md | `grep -c "PROGRESS.md" .claude/skills/do-build/SKILL.md` | output > 1 |
| Soft-check line present | `grep -c '\[ -f.*PROGRESS.md' .claude/skills/do-build/SKILL.md` | output > 0 |
| .gitignore has PROGRESS.md | `git check-ignore -v PROGRESS.md` | non-empty output |
| Feature doc exists | `test -f docs/features/long-task-checkpointing.md` | exit code 0 |
| Index entry exists | `grep -c "long-task-checkpointing" docs/features/README.md` | output > 0 |
| Unit tests pass | `pytest tests/unit/test_long_task_checkpointing.py -v -x` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Builder prompt hard size bound | `python -c "import sys; sys.exit(0 if len(open('.claude/agents/builder.md').read()) < BASELINE + 10000 else 1)"` | exit code 0 |

## Critique Results

**Critique run**: 2026-04-23
**Verdict**: READY TO BUILD (with concerns)
**Findings**: 5 total (0 blockers, 3 concerns, 2 nits)

### Concerns

**C1: Step number contradiction for soft-check placement**
- Solution section says: add "Step 5.6 between Step 5 (DoD) and Step 7 (PR)"
- Task 4 says: add "step 5.6 between Step 5 (DoD) and Step 5.5 (CWD Safety Reset)"
- do-build SKILL.md step sequence is: 5 → 5.1 → 5.5 → 6 → 7
- A step labeled "5.6" cannot go between steps 5 and 5.5 (would be 5.2 or 5.4 at most)
- **Fix**: Resolve to one location. The Solution section wording (between 5.5 and 6) is more operationally correct since the soft-check needs the worktree to already be fully built. Rename to step 5.6 between 5.5 and 6, or simply insert as step 5.9 (last sub-step before 6). Update Task 4 to match.
- **Implementation note**: In SKILL.md, the CWD Safety Reset (5.5) runs at line 333. Insert the soft-check block after 5.5 and before Step 6 (Documentation Gate, line 343). The label "5.6" is available there — no collision.

**C2: Stale field reference — `AgentSession.sdlc_stages` does not exist**
- The plan repeatedly uses `AgentSession.sdlc_stages` as the authoritative fallback for SDLC progress (e.g., "Ground truth for progress is `AgentSession.sdlc_stages`" in Technical Approach, Task 1, Solution section, and Data Flow).
- This field was removed as part of the completed `agent-session-field-cleanup` plan — it was consolidated into `stage_states` (and subsequently `session_events`). Running `grep -rn "sdlc_stages" . --include="*.py"` returns zero results.
- A dev agent reading the new prompt guidance and following the fallback chain will look for `AgentSession.sdlc_stages` — which doesn't exist and can't be queried from inside a worktree dev session anyway.
- **Fix**: Replace all references to `AgentSession.sdlc_stages` with `AgentSession.stage_states` (the current field) — or better, since a dev agent can't directly query the ORM from a worktree, replace the instruction with "fall back to the plan doc and `git log --oneline main..HEAD`" which are both directly accessible. The SDLC env vars (`SDLC_CURRENT_STAGE` etc.) may also be available via environment.
- **Implementation note**: Replace the phrase "Ground truth for progress is `AgentSession.sdlc_stages`" with "Fall back to the plan doc and `git log --oneline main..HEAD` for authoritative progress state" in builder.md, developer.md, and all plan sections that repeat this instruction. The dev agent has no ORM access in the worktree — file and git-based fallbacks are the only actionable options.

**C3: BASELINE_CHARS may be measured post-edit due to parallel task execution**
- All six tasks have `Parallel: true` and `Depends On: none`. Task 6 (test-author) may therefore run after Task 1 (prompt-editor) has already edited `builder.md`.
- The plan instructs: "Hardcode `BASELINE_CHARS` as a constant at test-write time (approximately current `builder.md` length)." If test-author runs after the prompt edit, they will observe the post-edit size (~14K chars) and hardcode that as `BASELINE_CHARS`. The test then asserts `14000 < 14000 + 5000` — trivially true, making the budget guard useless.
- **Fix**: Pin the pre-edit baseline explicitly in the plan: `BASELINE_CHARS = 11029` (current `builder.md` size at plan-write time, verified). Task 6 should hardcode this value regardless of execution order.
- **Implementation note**: Add `BASELINE_CHARS = 11029  # pre-edit size of builder.md at commit ceedbe68` as a module-level constant in the test file. The test author must NOT measure the current file size at test-write time — use the pinned constant.

### Nits

**N1: Test classification mismatch — file-read assertions placed in `tests/integration/`**
- All four proposed tests (`test_builder_prompt_has_externalization_section`, `test_builder_prompt_soft_limit`, `test_builder_prompt_hard_limit`, `test_progress_md_in_build_soft_check`) are pure file-read string assertions. They open a markdown file, call `len()` or `in`, and assert. No Redis, no API, no subprocess, no Popoto model, no cross-process boundary.
- The plan itself acknowledges "These are structural assertions, not behavioral." They belong in `tests/unit/` for fast CI feedback.
- This is a NIT — placing them in `tests/integration/` works but slows CI unnecessarily for trivially fast checks.

**N2: `docs/features/compaction-hardening.md` is missing from the worktree branch**
- The plan references and cross-links to `docs/features/compaction-hardening.md` in the feature doc (Task 5). This file does not exist in the current worktree branch (`session/long-task-checkpointing`).
- It does exist on `main` (shipped with PR #1135). The worktree was cut from a commit before that merge — the file will be available once the worktree is rebased or when the feature doc links to it at PR merge time.
- No action needed at plan level, but the feature-doc-writer agent should verify the file exists in the worktree before writing the cross-link (the worktree may need a `git merge main` or `git rebase main` first).

---

## Resolved Questions

All five open questions answered by Tom on 2026-04-23:

1. **Hard requirement vs. soft nudge** → **Soft nudge** (confirmed). No plan change.

2. **Commit PROGRESS.md or gitignore?** → **Gitignore**. PROGRESS.md is a working-memory scratchpad, not ground truth. SDLC stages are authoritative progress; plan doc is authoritative scope. Added to .gitignore. Skill files note that PROGRESS.md must be recreated at session start if absent (worktrees may be recreated). Prompt wording clarifies scratchpad status.

3. **Post-compact: prompt vs. PostCompact hook?** → **Prompt-only for #1130**. PostCompact hook endorsed as "the right nudge" but tracked separately as **#1139** (backlog). Cross-referenced in Rabbit Holes.

4. **Persona overlay: skip or edit?** → **Edit both** (`builder.md` AND `~/Desktop/Valor/personas/developer.md`), keeping them consistent. A PM agent may spawn a dev session as either persona; both should exhibit the same working-state externalization behavior. Content should be consistent, not verbatim duplicate.

5. **Token budget ceiling** → **Soft: +5000 chars, Hard: +10000 chars** (overrides original +3000 default). Tom: "builder can have a lot of leeway especially when combined with strong models like Opus." Two-tier budget enforced by two separate test assertions.
