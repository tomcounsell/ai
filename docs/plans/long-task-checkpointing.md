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

- Dev sessions externalize working state in two mutually-reinforcing channels: (a) **frequent git commits** — every meaningful unit of work is committed, not only at failure boundaries; (b) a **top-level `PROGRESS.md` file** in the worktree tracking Done / In-progress / Left, re-read on session start and updated at checkpoints.
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
2. **First turn (after reading plan)** — Agent follows the new "Working-state externalization" prompt section: creates `PROGRESS.md` at worktree root with three sections (Done / In-progress / Left), populated from the plan's Step by Step Tasks.
3. **During work** — After each meaningful unit (completed task, completed sub-step), agent (a) commits to the session branch, (b) moves the task entry from "In-progress" to "Done" in PROGRESS.md and updates "In-progress" to the next item. Update batched with the code commit when possible.
4. **Compaction fires** — PreCompact hook takes the JSONL backup (existing, #1127). Compacted summary replaces conversation history.
5. **First turn post-compaction** — The new prompt instruction "On session start or resumption, re-read `PROGRESS.md` and `git log --oneline main..HEAD`" fires. Agent re-orients from the file + git log, not the compacted summary.
6. **Session completion** — `/do-build` Step 5 (Definition of Done) now runs a soft check: `test -f .worktrees/{slug}/PROGRESS.md && grep -q '^## Done' PROGRESS.md`. If missing, log a warning but do not block PR creation.

## Architectural Impact

- **No new dependencies.** Prompt edits only, plus a soft-check shell line in `/do-build`.
- **Interface changes**: none. PROGRESS.md is a convention, not an interface.
- **Coupling**: decreases — making post-compaction recovery independent of SDK summary fidelity.
- **Data ownership**: PROGRESS.md lives inside the worktree alongside the plan. Same ownership as the code — the session branch owns it.
- **Reversibility**: trivially reversible. Prompt edits are revertable in one commit. No state migration.

## Appetite

**Size:** Small

**Team:** Solo dev + code reviewer (the dev session doing the prompt edits; reviewer at /do-pr-review).

**Interactions:**
- PM check-ins: 1 (plan-doc-review before /do-plan-critique; Tom answers Open Questions)
- Review rounds: 1 (standard PR review)

**Why Small:** four prompt edits (builder.md, do-build SKILL.md, developer persona overlay, dev-session.md if warranted), one feature doc, one integration test. Zero new code paths, zero dependencies, zero schema changes. The hardest part is keeping the prompt deltas terse enough to not inflate token counts.

## Prerequisites

No prerequisites — all touched files exist and are readable.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Builder prompt present | `test -f .claude/agents/builder.md` | Target of the primary prompt edit |
| Do-build skill present | `test -f .claude/skills/do-build/SKILL.md` | Target of soft-check addition |
| Dev persona overlay present | `test -f ~/Desktop/Valor/personas/developer.md || test -f config/personas/developer.md` | Target of persona edit (authoritative source is ~/Desktop/Valor/) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/long-task-checkpointing.md`

## Solution

### Key Elements

- **Working-state externalization prompt section** — added to `.claude/agents/builder.md` (affects builder sub-agents spawned by `/do-build` team-lead orchestration). ~40-60 lines, written tersely.
- **PROGRESS.md convention** — a single top-level file in the worktree with three H2 sections: `## Done`, `## In progress`, `## Left`. Committed to the session branch alongside code. Auto-created by the dev session on first turn.
- **Post-compaction re-orientation instruction** — in the same prompt section, an explicit "on session start or resumption, `cat PROGRESS.md` and `git log --oneline main..HEAD` before continuing."
- **Soft gate in `/do-build`** — a warn-not-block check that PROGRESS.md exists at PR time. Runs after Definition of Done, before PR creation.
- **Feature doc** — `docs/features/long-task-checkpointing.md` with the example PROGRESS.md skeleton, rationale, and cross-link to `docs/features/compaction-hardening.md`.
- **Integration test** — reads `.claude/agents/builder.md` and asserts presence of the new section markers (e.g., `## Working-state externalization`, `PROGRESS.md`). Token-count test asserts the delta is under a threshold. A separate integration test stubs the /do-build soft-check path.

### Flow

**Dev session spawns in worktree** → reads plan + builder prompt → creates `PROGRESS.md` with plan tasks populated → commits `[WIP] scaffold` → works on first task → commits with PROGRESS.md update → compaction fires → (JSONL backup takes, summary replaces history) → agent's next turn reads `PROGRESS.md` + `git log` → continues from recorded in-progress line → finishes → /do-build soft-check sees PROGRESS.md → PR opens.

### Technical Approach

1. **Prompt edit to `.claude/agents/builder.md`** — add `## Working-state externalization` section after the existing "Safety Net — Commit Before Exit" block (line ~169). Content covers:
   - Create `PROGRESS.md` at worktree root on session start if absent.
   - Three H2 sections: Done, In progress, Left.
   - Commit after each meaningful unit of work — both code and PROGRESS.md in the same commit when possible. `[WIP]` prefix allowed for partial steps.
   - On session start or post-compaction resumption, re-read `PROGRESS.md` and `git log --oneline main..HEAD` before acting on anything else.
   - Target 40-60 lines. The cumulative token budget delta for `builder.md` must stay under +800 tokens (verified by a deterministic test that counts characters, not a tokenizer — see Test Impact).

2. **Note in `.claude/skills/do-build/SKILL.md`** — add a one-line bullet in the "Critical Rules" list pointing to PROGRESS.md as the standard handoff artifact, plus a soft-check step between Step 5 (Definition of Done) and Step 7 (PR):
   ```
   **5.6 PROGRESS.md soft check** — `[ -f .worktrees/{slug}/PROGRESS.md ] || echo "[warn] No PROGRESS.md at session HEAD — not blocking, but recovery from compaction may be degraded next run."`
   ```
   The check does not gate PR creation.

3. **Optional cross-reference in developer persona overlay** (`~/Desktop/Valor/personas/developer.md` — authoritative source, iCloud-synced; `config/personas/developer.md` is a fallback that does not exist in-repo). Since the persona overlay lives outside the repo on Tom's machine, the edit lands through a synchronized copy. **Deferred — covered in Open Questions.** Default plan: do NOT edit the persona overlay; rely on `builder.md` (which is loaded for sub-agents via `agent/agent_definitions.py`) and the `do-build` SKILL.md guidance for the orchestrator. If post-ship observation shows PROGRESS.md adoption is spotty in top-level dev sessions (where the persona overlay dominates), add a follow-up issue.

4. **Feature doc `docs/features/long-task-checkpointing.md`** — includes:
   - Rationale (the semantic vs. crash-safe distinction vs. #1127)
   - Example PROGRESS.md skeleton (exact markdown)
   - Cross-reference to `docs/features/compaction-hardening.md`
   - Index entry added to `docs/features/README.md`

5. **Integration test `tests/integration/test_long_task_checkpointing.py`** — three checks:
   - `test_builder_prompt_has_externalization_section`: asserts `"## Working-state externalization"` appears in `.claude/agents/builder.md`.
   - `test_builder_prompt_token_budget`: asserts character count of `builder.md` is under a ceiling (current + 3000 chars as upper bound, roughly +750 tokens). Acts as a regression fence against unbounded prompt growth.
   - `test_progress_md_in_build_soft_check`: reads `.claude/skills/do-build/SKILL.md` and asserts the soft-check line is present. This verifies the /do-build prompt references PROGRESS.md as a handoff artifact.

   These are structural assertions, not behavioral — we are not spinning up a full dev session in CI (too slow, too flaky). The structural assertion proves the prompts contain the required guidance; the behavioral outcome (agents actually create PROGRESS.md) is monitored post-merge via the soft-check log line.

## Failure Path Test Strategy

### Exception Handling Coverage

- [x] No new exception handlers introduced. The only new code path is the soft-check shell line in /do-build, which uses `|| echo` — errors become warnings by construction. No `except Exception: pass` blocks added.

### Empty/Invalid Input Handling

- [ ] Test: if `builder.md` is missing entirely, `_parse_agent_markdown` (agent/agent_definitions.py:49-57) already handles this with a fallback prompt and a warning log. Our changes do not alter that path — no new test needed.
- [ ] Test: if `PROGRESS.md` is present but empty, the soft-check in /do-build (`[ -f ... ] || echo ...`) still passes (file exists). Deliberate — empty PROGRESS.md is degraded but not broken. A future follow-up could assert non-empty; out of scope here.

### Error State Rendering

- [ ] The soft-check's warning output appears in the /do-build orchestrator log stream. If PROGRESS.md is missing, the warning is visible in the PR comment (via the orchestrator's completion message). Test: add a stub /do-build invocation in an integration test that runs the soft-check line against a fake empty worktree and asserts the warning is printed. (This test is in Step 6 of Step by Step Tasks.)

## Test Impact

- [ ] `tests/integration/test_long_task_checkpointing.py` — CREATE: three structural assertions per Technical Approach Step 5.
- [ ] `tests/unit/test_agent_definitions.py` — UPDATE if a test asserts exact line count of `builder.md`; otherwise leave untouched. Verified at plan time: `grep -n 'line.count\|wc -l\|len(.*splitlines' tests/unit/test_agent_definitions.py` returns no exact-count assertions. No update required.
- [ ] No other test files are affected. Changes are prompt text and one skill line; behavior at the Python layer is unchanged.

## Rabbit Holes

- **Hard-fail the build if PROGRESS.md is missing.** Tempting as "rigor" but creates a new failure mode (builds abort on sessions where the agent simply forgot to create the file) without actually fixing working-state drift. The soft-check is strictly better: it nudges without introducing a new red light. If six months of data shows adoption is catastrophically low, revisit.
- **A dedicated `tools/progress_md.py` helper.** Tempting as "structured writing" but the whole point of PROGRESS.md is that it is a plain markdown file the agent writes to directly. Adding a tool introduces a new API surface and an indirection the agent has to remember to use. Direct Write/Edit is the right call.
- **Auto-injection of "re-read PROGRESS.md" after compaction via a PostCompact hook.** This overlaps with compaction-hardening territory (#1127 owns that hook). Leaving it in-prompt makes the instruction part of the agent's baseline behavior; injecting via a hook creates timing coupling with the nudge loop. Deferred explicitly — see Open Questions.
- **Extending PROGRESS.md with YAML frontmatter (status, owner, created_at).** Violates the "simple file the agent writes freely" principle. Adds maintenance burden for near-zero benefit. Deferred.
- **Multi-worktree PROGRESS.md aggregation** (e.g., a `docs/PROGRESS.md` that tracks all worktrees). Out of scope; solves a different problem (fleet visibility).

## Risks

### Risk 1: Prompt token inflation degrades dev-session performance

**Impact:** Every additional token in the builder prompt consumes context budget on every turn. A 60-line section is ~600-800 tokens; across a multi-hour dev session with many turns, this is non-trivial. The issue body calls out: "Prompt changes must not materially inflate system-prompt token count."

**Mitigation:** The test `test_builder_prompt_token_budget` caps character count at current + 3000 chars (~750 tokens). Actual added content is targeted at 40-60 lines of terse, imperative English. No examples, no rationale paragraphs — the rationale lives in the feature doc, the prompt carries only the instructions.

### Risk 2: Agents create `PROGRESS.md` once and never update it

**Impact:** A stale PROGRESS.md is worse than none — the post-compaction agent reads it and gets misleading "In-progress" state. Agent drifts anyway.

**Mitigation:** The prompt pairs "commit after each meaningful unit" with "update PROGRESS.md in the same commit when possible" — linking the two acts reduces the chance of one without the other. Post-merge, monitor via the soft-check log to see how many sessions close with a non-trivial PROGRESS.md (file exists AND has been updated at least once after creation). If adoption is weak, tighten the prompt in a follow-up.

### Risk 3: Developer persona overlay edits are blocked by the "no edits from plain CLI" constraint

**Impact:** The issue warns that `.claude/agents/*.md` and `.claude/commands/*.md` edits "must run inside an Agent-SDK-powered dev session" because the harness blocks self-modification from a plain Claude Code CLI. If this plan is executed from a plain CLI by accident, the edits fail silently.

**Mitigation:** This plan is routed through `/do-build`, which spawns an Agent-SDK-powered dev session in an isolated worktree. The harness block only triggers in non-SDK Claude Code runs. The Test Impact integration test verifies post-edit that the expected content landed — any silent edit failure is caught by that test failing.

## Race Conditions

No race conditions identified — all operations are synchronous (prompt file edits, test assertions, a single soft-check shell line). PROGRESS.md writes happen from within a single dev session that owns its worktree exclusively, so no concurrent-writer hazard exists. The /do-build soft-check reads PROGRESS.md at a well-defined step (post-Definition-of-Done, pre-PR), not concurrently with the dev session.

## No-Gos (Out of Scope)

- **Hard-fail if PROGRESS.md absent.** Soft warn only — see Rabbit Holes. If adoption data warrants hardening, that is a separate issue.
- **PostCompact hook auto-injecting "re-read PROGRESS.md" nudge.** Belongs to compaction-hardening follow-up territory if pursued. This plan is prompt-only.
- **Per-worktree or per-session dashboards showing PROGRESS.md contents.** Not the problem being solved. A future observability feature.
- **Auto-generating PROGRESS.md from the plan document.** The agent writes it. If we auto-generate, we lose the forcing function of the agent having to reason about its own working state.
- **Editing the developer persona overlay** (`~/Desktop/Valor/personas/developer.md`). See Open Questions — default is to defer. Builder.md is loaded for sub-agents via `agent_definitions.py`; the do-build SKILL.md covers orchestrator-level guidance. That combination should be sufficient for the first pass.
- **Migrating existing in-flight worktrees to have PROGRESS.md.** Agents create their own on first turn of new sessions. No backfill needed.

## Update System

No update system changes required. This feature is purely internal — prompt edits and one test file. The `/update` skill propagates git pulls; when users update, the new prompts ship automatically via the pulled `.claude/agents/builder.md`. No new dependencies, no config propagation, no migration steps.

## Agent Integration

No agent integration required — this is a prompt-engineering change. The agent already reads `builder.md` via `agent/agent_definitions.py` for sub-agent spawning and the developer persona overlay via `agent/sdk_client.py:load_persona_prompt()` for top-level dev sessions. The new prompt content flows through the existing loaders. No MCP server, no `.mcp.json` change, no bridge change, no new tool.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/long-task-checkpointing.md` describing:
  - What problem PROGRESS.md solves (post-compaction semantic drift)
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

- [ ] `.claude/agents/builder.md` contains a `## Working-state externalization` section with commit-frequency guidance and PROGRESS.md creation/update/re-read instructions.
- [ ] `.claude/skills/do-build/SKILL.md` references PROGRESS.md as the standard handoff artifact AND contains a soft-check step that warns (does not fail) if PROGRESS.md is missing at PR time.
- [ ] `docs/features/long-task-checkpointing.md` exists and contains an example PROGRESS.md skeleton.
- [ ] `docs/features/README.md` has a new entry linking to the feature doc.
- [ ] `tests/integration/test_long_task_checkpointing.py` passes and includes the three structural assertions (section presence, token-budget ceiling, soft-check line presence).
- [ ] `builder.md` character count has grown by no more than +3000 chars from baseline.
- [ ] Tests pass (`pytest tests/integration/test_long_task_checkpointing.py -v`).
- [ ] `python -m ruff check .` and `python -m ruff format --check .` pass.
- [ ] Documentation gate in /do-build passes (feature doc + index entry present).

## Team Orchestration

### Team Members

- **Prompt Editor**
  - Name: `prompt-editor`
  - Role: Edit `.claude/agents/builder.md` and `.claude/skills/do-build/SKILL.md` to add PROGRESS.md guidance and soft-check.
  - Agent Type: builder
  - Resume: true

- **Feature Doc Writer**
  - Name: `feature-doc-writer`
  - Role: Create `docs/features/long-task-checkpointing.md` and update `docs/features/README.md` index.
  - Agent Type: documentarian
  - Resume: true

- **Test Author**
  - Name: `test-author`
  - Role: Write `tests/integration/test_long_task_checkpointing.py` with three structural assertions.
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
  - "On session start, if `PROGRESS.md` does not exist at the worktree root, create it with three H2 sections: `## Done` (empty), `## In progress` (first task from the plan), `## Left` (remaining tasks from the plan)."
  - "After each meaningful unit of work — a completed task, a passing test, a validated sub-step — commit to the session branch. Update `PROGRESS.md` in the same commit when possible: move the completed item from `## In progress` to `## Done` and advance `## In progress` to the next item."
  - "`[WIP]` commit prefix is allowed and encouraged for partial steps. Frequent small commits are preferred over large batched commits."
  - "On session start or resumption (including after context compaction), read `PROGRESS.md` and `git log --oneline main..HEAD` BEFORE acting on any other instruction. The compacted summary may be lossy — the file and the git log are authoritative."
  - "`PROGRESS.md` is committed to the session branch. It is worktree-local and does not survive worktree cleanup — that is intentional."
- Verify character delta is under +3000.

### 2. Add PROGRESS.md soft-check to /do-build

- **Task ID**: build-dobuild-edit
- **Depends On**: none
- **Validates**: `tests/integration/test_long_task_checkpointing.py::test_progress_md_in_build_soft_check` (create)
- **Assigned To**: prompt-editor
- **Agent Type**: builder
- **Parallel**: true
- Edit `.claude/skills/do-build/SKILL.md`:
  - In the "Critical Rules" section (line ~193), add a bullet: `**PROGRESS.md is the standard handoff artifact** — dev sessions maintain it at the worktree root per builder.md's "Working-state externalization" section. Missing PROGRESS.md is a warning, not a blocker.`
  - Add a new step 5.6 between Step 5 (Definition of Done) and Step 5.5 (CWD Safety Reset): a soft-check shell line that warns if PROGRESS.md is absent at `.worktrees/{slug}/PROGRESS.md`. Use `[ -f ... ] || echo "..."` form so the check never returns nonzero.

### 3. Write feature doc

- **Task ID**: build-feature-doc
- **Depends On**: none
- **Validates**: docs gate in /do-build (Step 6.1: `validate_docs_changed.py`)
- **Assigned To**: feature-doc-writer
- **Agent Type**: documentarian
- **Parallel**: true
- Create `docs/features/long-task-checkpointing.md`:
  - Opening paragraph: what and why, distinguishing from compaction-hardening.
  - Section: "The PROGRESS.md convention" — the three-H2 structure with an exact markdown skeleton.
  - Section: "How it works" — dev session creates on first turn, updates at checkpoints, re-reads on resumption.
  - Section: "Soft-check in /do-build" — what the warning looks like, when it fires.
  - Section: "See also" — link to `docs/features/compaction-hardening.md` and `.claude/agents/builder.md`.
- Update `docs/features/README.md`: add a new row to the index table with the title, a one-line description, and the link.

### 4. Write integration test

- **Task ID**: build-test
- **Depends On**: none
- **Assigned To**: test-author
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/integration/test_long_task_checkpointing.py` with three tests:
  - `test_builder_prompt_has_externalization_section` — reads `.claude/agents/builder.md` and asserts `"## Working-state externalization"` appears, plus asserts `"PROGRESS.md"` appears at least 3 times (create/update/re-read mentions).
  - `test_builder_prompt_token_budget` — asserts `len(builder_md_text) < BASELINE_CHARS + 3000`. Hardcode the baseline as a constant at test-write time (approximately current length + safety margin).
  - `test_progress_md_in_build_soft_check` — reads `.claude/skills/do-build/SKILL.md` and asserts a line matching the pattern `\[ -f .*PROGRESS\.md .* \] \|\| echo` is present (the soft-check line).
- Mark all three with `@pytest.mark.integration` and `@pytest.mark.sdlc`.

### 5. Final validation

- **Task ID**: validate-all
- **Depends On**: build-prompt-edit, build-dobuild-edit, build-feature-doc, build-test
- **Assigned To**: checkpoint-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_long_task_checkpointing.py -v` — all three tests pass.
- Run `python -m ruff check .` — zero errors.
- Run `python -m ruff format --check .` — zero format issues.
- Verify `.claude/agents/builder.md` ends with trailing newline and no broken markdown.
- Verify `docs/features/README.md` new row is well-formed (no broken table syntax).
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Builder prompt has externalization section | `grep -c "## Working-state externalization" .claude/agents/builder.md` | output > 0 |
| Builder prompt mentions PROGRESS.md | `grep -c "PROGRESS.md" .claude/agents/builder.md` | output > 2 |
| Do-build references PROGRESS.md | `grep -c "PROGRESS.md" .claude/skills/do-build/SKILL.md` | output > 1 |
| Soft-check line present | `grep -c '\[ -f.*PROGRESS.md' .claude/skills/do-build/SKILL.md` | output > 0 |
| Feature doc exists | `test -f docs/features/long-task-checkpointing.md` | exit code 0 |
| Index entry exists | `grep -c "long-task-checkpointing" docs/features/README.md` | output > 0 |
| Integration tests pass | `pytest tests/integration/test_long_task_checkpointing.py -v -x` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Builder prompt size bound | `python -c "import sys; sys.exit(0 if len(open('.claude/agents/builder.md').read()) < 14000 else 1)"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

These architectural decisions need Tom's input before /do-plan-critique or /do-build. Defaults are stated; confirm or override.

1. **Hard requirement vs. soft nudge: does missing PROGRESS.md block the build?**
   - **Default:** Soft nudge. `/do-build` logs a warning if PROGRESS.md is absent at PR time, does not block PR creation. Rationale in Rabbit Holes: a hard fail creates a new failure mode without fixing drift.
   - **Alternative:** Hard fail. `/do-build` aborts PR creation if PROGRESS.md is missing. Forces agents to comply but introduces a new red light.
   - **Your call:** soft nudge (default) or hard fail?

2. **Commit PROGRESS.md to the session branch, or .gitignore as working state?**
   - **Default:** Commit it. PROGRESS.md lives on the session branch, travels with the worktree, is readable post-compaction via `git show HEAD:PROGRESS.md` even if the file was just edited. When the worktree is cleaned up after merge, the file goes with it. The session branch history preserves the journey.
   - **Alternative:** .gitignore it. Keeps PR diffs cleaner. Downside: the file is ephemeral in the true sense — if the worktree disappears, the record is gone.
   - **Your call:** commit (default) or gitignore?

3. **Scope overlap with compaction-hardening: should the post-compaction "re-read PROGRESS.md" nudge be a prompt instruction or a PostCompact hook?**
   - **Default:** Prompt instruction only (this plan). Keeps the behavior as an intrinsic part of the dev agent's baseline — the agent does it because the prompt says so, not because a hook fires. Consistent with the "intelligence over rigid patterns" principle in CLAUDE.md.
   - **Alternative:** Add a PostCompact hook that auto-injects a "re-read PROGRESS.md" reminder into the agent's context immediately after compaction finishes. Lands in #1127's territory; would need a separate issue.
   - **Your call:** prompt-only (default) or also open a follow-up for a PostCompact hook?

4. **Should the developer persona overlay (`~/Desktop/Valor/personas/developer.md`) also be edited, or is builder.md + do-build SKILL.md sufficient?**
   - **Default:** Do NOT edit the persona overlay. The builder prompt covers sub-agents spawned by the team-lead `/do-build`; the do-build SKILL.md covers the orchestrator. Top-level dev sessions use the developer persona, but the work orchestrated by /do-build spawns builders that DO load builder.md. The typical long-task case is under /do-build, so builder.md is where the guidance belongs.
   - **Alternative:** Also edit the developer persona overlay to cover top-level dev sessions that don't go through /do-build (e.g., direct "fix this bug" conversations). This requires editing a file outside the repo (iCloud-synced); the change propagates but is harder to audit.
   - **Your call:** skip persona edit (default) or add it to scope?

5. **Token budget ceiling for the builder.md delta: what is the acceptable upper bound?**
   - **Default:** +3000 characters (~+750 tokens). Cap enforced by `test_builder_prompt_token_budget`. Rationale: the current `builder.md` is 267 lines / ~9300 chars; a +3000 cap gives room for 40-60 lines of terse guidance while preventing future unbounded growth.
   - **Alternative tighter:** +2000 chars (~+500 tokens). Forces very terse prose.
   - **Alternative looser:** +5000 chars (~+1250 tokens). More rationale/examples allowed.
   - **Your call:** +3000 (default), tighter, or looser?
