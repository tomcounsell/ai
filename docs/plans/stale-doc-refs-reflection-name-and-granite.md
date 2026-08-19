---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2853
last_comment_id: 5337658489
companion_tracking: https://github.com/tomcounsell/ai/issues/2839
companion_last_comment_id: 5324617995
---

# Stale doc/prose references: wrong reflection name, deleted granite package

Two-issue lane. Anchor **#2853** (wrong reflection name in prose), companion **#2839**
(doc points at a deleted package path). Both are prose-only corrections to
already-correct systems. The implementation PR body carries **`Closes #2853`** and
**`Closes #2839`**.

## Problem

Two independent classes of stale prose, both discovered by audit rather than by
breakage. Neither changes runtime behavior; both mislead the next reader (human or
agent) into a wrong mental model of a live subsystem.

**Current behavior:**

*#2853 — wrong reflection name.* Four comment/docstring sites name the popoto index
cleanup reflection `popoto-index-cleanup`. No reflection by that name is registered.
The registered name is `redis-index-cleanup` (`config/reflections.yaml:53`). A reader
who greps `popoto-index-cleanup` in `config/reflections.yaml` finds nothing and
concludes the cleanup is unscheduled — which is exactly the wrong conclusion that
produced issue #2853's own false premise.

*#2839 — deleted target.* `docs/features/standardized-enums.md:19` describes
`SessionType.GRANITE` as labelling "Direct invocations of the standalone
`valor-granite-loop` CLI (`tools/granite_interactive_tui_poc/cli.py`)". That package
does not exist, no successor exists (`tools/granite_loop/` was the #1643 rename target
and it is gone too), and `pyproject.toml [project.scripts]` has no `valor-granite-loop`
entry. The same cell also claims "Bridge sessions that run through the granite PTY
container are typed `ENG`" — the PTY container was deleted with plan #1924.

**Desired outcome:**

- Every live prose site naming the cleanup reflection uses `redis-index-cleanup`.
- The daily-cadence prose **stays** everywhere it appears — it is correct and is
  asserted by a tracked test.
- `standardized-enums.md`'s `SessionType.GRANITE` row says what `config/enums.py`'s
  own `SessionType` docstring says: a historical value whose producer was deleted,
  retained so pre-cutover Redis records keep hydrating.
- Closure is proved by repo-wide token sweeps, not by an enumerated site list.

## Freshness Check

**Baseline commit:** `f491306c5`
**Issues filed at:** #2853 — 2026-08-18T03:03:37Z; #2839 — 2026-08-17T08:08:36Z
**Disposition:** Minor drift — the code is unchanged, but #2853's stated premise was
already false at filing time and its site inventory was incomplete. Corrected below and
recorded as a `## Recon Summary` on both issues.

**File:line references re-verified:**

| Reference | Issue's claim | Verified state |
|---|---|---|
| `worker/__main__.py:743-749` | Sole production caller of `run_cleanup()`, once per worker start | **Holds.** |
| `config/reflections.yaml` | `redis-index-cleanup` calls `agent.agent_session_queue.cleanup_corrupted_agent_sessions` | **FALSE.** Lines 53-62 carry `execution_type: function`, `callable: "scripts.popoto_index_cleanup.run_cleanup"`, `every: 86400s`, `enabled: true`. |
| `~/Desktop/Valor/reflections.yaml` (resolved ahead of the checkout copy by `agent/reflection_scheduler.py:183`) | not checked by the issue | **Byte-identical to `config/reflections.yaml`** (`diff` clean). Both register `run_cleanup` daily. |
| `tests/integration/test_reflections_redis.py:199,204` | not cited | Asserts `callable == "scripts.popoto_index_cleanup.run_cleanup"` and `parse_every_duration(entry["every"]) == 86400`. The daily cadence is a **committed contract**. |
| `scripts/popoto_index_cleanup.py:4-5` | stale | **Accurate.** Leave alone. |
| `scripts/popoto_index_cleanup.py:307` | stale | **Stale — wrong name only.** Cadence claim correct. |
| `docs/features/popoto-index-hygiene.md:35, 54, 61, 63` | stale | **Accurate.** All name `redis-index-cleanup`. Leave alone. |
| `bridge/email_bridge.py:1087` | not in the issue's list | **Stale — wrong name.** |
| `models/ghost_reconcile.py:23` | not in the issue's list | **Stale — wrong name.** |
| `models/dedup.py:70` | not in the issue's list | **Stale — wrong name.** |
| `docs/features/standardized-enums.md:19` | points at `tools/granite_interactive_tui_poc/cli.py` | **Holds.** `ls -d tools/granite*` → no match; no `valor-granite-loop` in `pyproject.toml`. |
| `config/enums.py:20-25` | not cited | Correct historical framing; the model wording to copy. Its `"Removal is #1927's scope"` pointer is itself stale — see Task 3. |
| `docs/features/bridge-worker-architecture.md:564` | not cited | Already correct ("the standalone CLI ... no longer exists"). No change. |
| `models/agent_session.py:4` and `:102` | raised in #2839 comment `5324617995` | Already correct: `"granite" persists on historical records only`. Third in-repo wording precedent. No change. |

**Cited sibling issues/PRs re-checked:**
- PR **#2833** — MERGED 2026-08-18T02:57:37Z. Fixed only its own PR-introduced "daily"
  claims and deferred the pre-existing prose here. Its diff touched none of the four
  stale sites.
- Issue **#2636** — CLOSED 2026-08-18T02:57:38Z by #2833. Landscape unchanged for this work.
- Issue **#1927** (AgentSession schema diet) — **CLOSED** without removing
  `SessionType.GRANITE`. This is why the new doc prose must not inherit `enums.py`'s
  `"Removal is #1927's scope"` pointer.
- Plan **#1924** (granite PTY teardown) — completed; `docs/plans/completed/granite-pty-teardown.md`
  confirms `tools/granite_loop/` and the `valor-granite-loop` entry point were deleted.

**Commits on main since the issues were filed (touching referenced files):**
`git log --since=<createdAt> -- scripts/popoto_index_cleanup.py docs/features/popoto-index-hygiene.md config/reflections.yaml bridge/email_bridge.py models/ghost_reconcile.py models/dedup.py docs/features/standardized-enums.md config/enums.py`
returns **no commits**. Nothing has moved.

**Active plans in `docs/plans/` overlapping this area:** none. `move-bridge-utc-to-utils.md`
is the only in-flight plan touching `bridge/`, and it touches `bridge/telegram_bridge.py`
time handling, not `email_bridge.py:1087`'s comment block.

**Notes:** `granite` alone is **not** a dead token — it also names the live local
ollama classifier model (`tools/classifier.py`, `agent.llm.run_typed_local`). Sweeps
must key on the deleted-package tokens (`granite_interactive_tui_poc`, `granite_loop`,
`valor-granite-loop`), never on bare `granite`.

## Prior Art

- **PR #2833** — "Bound `Job.recent_for_room` via a direct bounded reverse-range read
  (#2636)". During its review rounds, judges audited the popoto cleanup cadence and
  produced the (incorrect) finding that seeded #2853. It fixed its own introduced
  claims and explicitly deferred the pre-existing prose to #2853. **Relevant:** this
  plan is that deferral, but on the corrected premise.
- **PR #2671** — "Register Job in the guarded index-repair sweep (#2640)". Established
  the `_GUARDED_ELSEWHERE` two-part contract documented at
  `docs/features/popoto-index-hygiene.md:48-58`. Not modified here.
- **Plan #1643** (`docs/plans/completed/sdlc-1643.md`) — renamed
  `tools/granite_interactive_tui_poc/` → `tools/granite_loop/`. **Directly relevant:**
  it invented the token-driven sweep gate this plan reuses, precisely because its own
  line-enumerated task list missed a stray site twice. Its closure grep excluded
  `docs/plans/completed/` — the same exclusion applies here.
- **Plan #1924** (`docs/plans/completed/granite-pty-teardown.md`) — deleted the PTY
  substrate and `tools/granite_loop/`. It noted `valor-granite-loop` in `pyproject.toml`
  was "already dangling" and removed it. `standardized-enums.md:19` is the residue that
  teardown missed.
- No prior attempt exists to fix either stale-prose site. This is the first pass.

## Research

No relevant external findings — proceeding with codebase context. This is a
prose-only correction to internal documentation with no external libraries, APIs, or
ecosystem patterns involved. Phase 0.7 WebSearch was skipped for that reason.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2833 (#2636) | Corrected PR-introduced cadence prose; deferred pre-existing prose to #2853 | Its closure criterion was a **diff-scoped** grep (`git diff main...HEAD -U0 \| grep '^+' \| grep -i daily`). A diff-scoped gate can never see pre-existing text, so the residue survived by construction. Worse, the reviewers' cadence finding was itself wrong — a grep that stopped six lines short of `callable:` in `reflections.yaml`. |
| Plan #1924 (PR #1930) | Deleted `tools/granite_loop/`, `agent/granite_container/`, the `valor-granite-loop` entry point | Swept source and config, not `docs/features/`. `standardized-enums.md:19` was never in its inventory. |
| Plan #1643 | Renamed the granite package; ran a token-driven sweep to exit 1 | Its sweep **excluded `docs/plans/completed/`** and ran before #1924 deleted the package, so `standardized-enums.md:19` was correct at the time and legitimately untouched. |

**Root cause pattern:** every prior gate was scoped narrower than the claim it was
meant to certify — diff-scoped instead of tree-scoped (#2833), source-scoped instead
of docs-inclusive (#1924). This plan's Verification rows are **repo-wide token sweeps**
so closure cannot outrun the claim.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none. No function signature, enum member, config key, or
  Redis schema changes. `SessionType.GRANITE` is untouched.
- **Coupling:** unchanged.
- **Data ownership:** unchanged.
- **Reversibility:** trivially reversible — every edit is a comment, docstring, or
  markdown table cell.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (both premises re-verified at plan time; nothing needs a human call)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Every edit is text in files
already tracked in the checkout.

## Solution

### Key Elements

- **Reflection-name correction (#2853)**: four sites that say `popoto-index-cleanup`
  say `redis-index-cleanup`. The daily/24h cadence language in each is preserved
  verbatim — it is correct.
- **Granite row rewrite (#2839)**: the `SessionType.GRANITE` table row in
  `standardized-enums.md` is restated as a historical value, mirroring the
  `SessionType` docstring in `config/enums.py`, with no file path and no PTY-container
  claim.
- **Stale-pointer removal in the authority**: `config/enums.py`'s docstring points
  removal at #1927, which closed without removing GRANITE. The pointer is dropped so
  the doc rewrite has a clean source to mirror.
- **Token-driven closure**: two repo-wide greps, each excluding `.worktrees/` and
  `docs/plans/`, are the gate. Named sites are illustrative; the greps are normative.
  The granite sweep keys on the **package path** tokens (`granite_interactive_tui_poc`,
  `tools/granite_loop`), never on the command name `valor-granite-loop` — that name is
  legitimately mentioned in past tense by `config/enums.py:21` and
  `docs/features/bridge-worker-architecture.md:564`, and by the replacement doc row
  itself. Sweeping on the command name is unsatisfiable by construction and would
  pressure a builder into deleting correct prose.

### Flow

Reader greps `popoto-index-cleanup` → **finds nothing outside plan archives** → greps
`redis-index-cleanup` → finds `config/reflections.yaml:53` **and** every prose site
that references it → mental model matches the config.

Reader opens `standardized-enums.md`, sees `SessionType.GRANITE` → **historical value,
producer deleted, retained for record hydration** → does not go looking for a CLI that
does not exist.

### Technical Approach

Corrected file:line references (re-verified at `f491306c5`; see Freshness Check):

**#2853 — replace `popoto-index-cleanup` with `redis-index-cleanup`, keep the cadence claim:**

| Site | Current | Note |
|---|---|---|
| `scripts/popoto_index_cleanup.py:307` | "``run_cleanup`` runs on the daily ``popoto-index-cleanup`` reflection plus once per worker start." | Keep "daily" and "plus once per worker start"; swap the name only. |
| `bridge/email_bridge.py:1087` | "waiting for the nightly popoto-index-cleanup sweep" | Swap the name. Also `nightly` → `daily`: the reflection is `every: 86400s` (a rolling 24h interval), not clock-pinned to night. |
| `models/ghost_reconcile.py:23` | "only cleaned by the nightly ``popoto-index-cleanup`` reflection (``scripts/popoto_index_cleanup.py``, once/24h)" | Swap the name; `nightly` → `daily`. `once/24h` is correct, keep it. |
| `models/dedup.py:70` | "waiting up to 24h for the nightly popoto-index-cleanup reflection" | Swap the name; `nightly` → `daily`. |

Explicitly **not** touched (verified accurate): `scripts/popoto_index_cleanup.py:4-5`,
`docs/features/popoto-index-hygiene.md:35, 54, 61, 63`.

**#2839 — rewrite `docs/features/standardized-enums.md:19`:**

The replacement row states, in the doc's own register: `SessionType.GRANITE` is a
historical value; its sole producer (the standalone `valor-granite-loop` CLI) was
deleted with the PTY substrate (plan #1924); nothing creates new sessions with it; it
is retained so pre-cutover Redis records carrying `session_type="granite"` keep
hydrating and rendering. No file path (none exists). No PTY-container sentence (the
container is gone). Three in-repo precedents already say exactly this, and the new row
must contradict none of them: `config/enums.py:20-25`,
`docs/features/bridge-worker-architecture.md:564`, and `models/agent_session.py:4, 102`
(`"granite" persists on historical records only`).

Line 11's lead-in ("Discriminator for AgentSession: eng, teammate, or granite") is left
as an accurate enumeration of the enum's members, matching `config/enums.py`.

**Adjacent, in scope: `config/enums.py:24`** — "Removal is #1927's (schema diet)
scope." #1927 is CLOSED and did not remove GRANITE. Drop the sentence. This is the
authority the doc mirrors; leaving a stale pointer there and omitting it from the doc
would leave the two divergent for no reason, and re-copying it would plant a fresh
stale reference. One sentence, no behavior change.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] No exception handlers in scope. Every edit is a comment, docstring, or markdown
      table cell. No `try`/`except` block is added, removed, or modified — the diff
      touches zero executable statements.

### Empty/Invalid Input Handling
- [x] Not applicable. No function is created or modified; no input path changes.

### Error State Rendering
- [x] Not applicable. No user-visible output path changes. The one user-visible
      artifact touched is a documentation table.

## Test Impact

- [ ] `tests/integration/test_reflections_redis.py::TestReflectionConfig::test_reflection_entry_structure` — **NO CHANGE, but it is the guard.** It already asserts `callable == "scripts.popoto_index_cleanup.run_cleanup"`, `every` parses to 86400, and `enabled is True`. It must stay green, and it is the reason the daily-cadence prose is preserved rather than deleted. Any build that "fixes" the cadence claim will contradict this test.
- [ ] `tests/unit/test_session_executor_runner_dispatch.py::test_executor_module_has_no_granite_imports` — **NO CHANGE.** Scoped to `inspect.getsource(agent.session_executor)`; it never reads docs or `config/enums.py`, so the granite row rewrite cannot affect it.

No other existing tests are affected — no test in `tests/` greps for `popoto-index-cleanup`, for `standardized-enums.md`, or for the `SessionType` docstring text (verified: `grep -rln "popoto-index-cleanup\|standardized-enums" tests/` returns nothing). The diff modifies no executable line, so no behavioral test can observe it.

## Rabbit Holes

- **Rewriting the cadence story.** The issue's original acceptance criterion asks for
  the daily claims to be swept out. They are correct. Deleting them puts the docs at
  odds with a green tracked test. Fix the *name*, keep the *cadence*.
- **Bare `granite` sweeps.** `granite` is a live token (the local ollama classifier
  model). Only the deleted-package tokens are dead. A `grep -i granite` returns
  hundreds of legitimate hits and will drown the build.
- **The wider PTY-teardown prose residue.** `tools/valor_session.py:867` and
  `tests/unit/test_valor_session_cli.py:257` still say "The granite PTY container
  **owns** the PM/Dev split" in the present tense. That is a real defect, but it belongs
  to the #1924 teardown family, not to either issue in this lane, and it sits in a
  stopgap comment block whose surrounding logic needs its own read. Out of scope here —
  see No-Gos.
- **Removing `SessionType.GRANITE`.** Tempting once the doc calls it historical. It is
  load-bearing for pre-cutover Redis record hydration and removing it is a schema
  change, not a prose change.
- **`docs/plans/` archives.** `docs/plans/done/popoto-redis-hygiene.md` and
  `docs/plans/completed/*` are full of both dead tokens. They are the historical record
  of what was planned at the time. Do not edit them; exclude them from every sweep.

## Risks

### Risk 1: The build "corrects" the daily-cadence prose out of existence
**Impact:** Docs contradict `tests/integration/test_reflections_redis.py`, and the next
reader concludes the sweep only runs on worker start — reintroducing the exact false
premise this lane exists to correct.
**Mitigation:** Verification row **"Daily cadence preserved"** asserts the phrase
survives in `scripts/popoto_index_cleanup.py`; the Technical Approach table states
"keep the cadence claim" per site; and the reflections test stays in the suite run.

### Risk 2: A stray token site is missed because the task list enumerates lines
**Impact:** Closure grep can't reach exit 1, or worse, passes because it was scoped to
`scripts/` and `docs/features/` as the issue's criterion proposed — leaving `bridge/`
and `models/` stale. This is exactly how #1643 burned two review cycles.
**Mitigation:** Task 4 is a self-completing token-driven sweep: run the two greps over
the whole worktree, resolve every remaining hit by meaning, re-run until both return
exit 1. The named-line tables are illustrative, not normative.

### Risk 3: `nightly` → `daily` reads as scope creep in review
**Impact:** A reviewer blocks on an unrequested wording change.
**Mitigation:** It is on the same three lines already being edited for the name, and it
is an accuracy fix: `every: 86400s` is a rolling 24h interval with no clock pinning.
The rationale is stated per-site in the Technical Approach table so a reviewer sees the
justification without asking.

## Race Conditions

No race conditions identified. Every change is a static text edit to a comment,
docstring, or markdown file. Nothing is read at runtime, nothing is concurrent, and no
shared mutable state is touched.

## No-Gos (Out of Scope)

- `[EXTERNAL]` Correcting the present-tense "The granite PTY container owns the PM/Dev
  split" prose at `tools/valor_session.py:867` and `tests/unit/test_valor_session_cli.py:257`.
  These are #1924 teardown residue in a stopgap-gate comment block whose surrounding
  refusal logic (`models/child_session_gate.py`) needs a human call on whether the
  stopgap itself still applies now that the container is gone. That judgement is not
  the agent's to make inside a prose lane; it needs its own issue filed by a human who
  can decide the gate's fate.
- `[SEPARATE-SLUG #1927]` Removing the `SessionType.GRANITE` enum member. Schema change,
  not a prose change; #1927 is the schema-diet slug that owns it (now closed without
  acting, so a fresh decision is needed before any removal — this plan takes none).

Everything else relevant is in scope for this plan.

## Update System

No update system changes required. No new dependency, config file, secret, entry point,
or migration is introduced. `/update` propagates these files by `git pull` alone. There
is no `reflections.yaml` change, so `env_sync.py::sync_reflections_yaml` is unaffected.

## Agent Integration

No agent integration required. No CLI entry point is added to
`pyproject.toml [project.scripts]`, and the bridge imports nothing new. The changes are
comments, a docstring, and a markdown table — the agent already reaches them through
the ordinary Read/Grep path.

## Documentation

- [ ] Update `docs/features/standardized-enums.md` — rewrite the `SessionType.GRANITE`
      table row (line 19) to the historical framing; remove the dead
      `tools/granite_interactive_tui_poc/cli.py` path and the granite-PTY-container
      sentence. **This edit is the #2839 fix itself**, not a follow-on.
- [ ] No change to `docs/features/popoto-index-hygiene.md` — verified accurate at
      lines 35, 54, 61, 63; it already names `redis-index-cleanup`. Recording the
      explicit no-change decision so a later reader does not re-open it.
- [ ] No change to `docs/features/README.md` — no feature doc is added or renamed, so
      the index table is unaffected.
- [ ] Inline: `scripts/popoto_index_cleanup.py:307` docstring, `bridge/email_bridge.py:1087`
      comment, `models/ghost_reconcile.py:23` docstring, `models/dedup.py:70` comment,
      `config/enums.py` `SessionType` docstring.

## Success Criteria

- [ ] No live site (outside `.worktrees/` and `docs/plans/`) names `popoto-index-cleanup`
      as a registered reflection.
- [ ] `scripts/popoto_index_cleanup.py`, `docs/features/popoto-index-hygiene.md`, and
      the three comment sites still describe the sweep as running daily / once per 24h.
- [ ] No live site (outside `.worktrees/` and `docs/plans/`) references the deleted
      package path `granite_interactive_tui_poc` or `tools/granite_loop`. The command
      name `valor-granite-loop` still appears, correctly, in past tense.
- [ ] `docs/features/standardized-enums.md`'s `SessionType.GRANITE` row describes a
      historical value with a deleted producer, consistent with `config/enums.py`.
- [ ] `config/enums.py` no longer points GRANITE removal at the closed #1927.
- [ ] The diff contains no executable-line change (comments, docstrings, and markdown only).
- [ ] Tests pass (`/do-test`), including
      `tests/integration/test_reflections_redis.py`.
- [ ] Documentation updated (`/do-docs`).
- [ ] PR body carries both `Closes #2853` and `Closes #2839`.
- [ ] No xfail conversions apply — no expected-failure marker relates to either issue
      (`grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` surfaces nothing touching
      popoto index cleanup or granite enums).

## Team Orchestration

Small, single-file-class, prose-only work. One builder, one validator.

### Team Members

- **Builder (prose)**
  - Name: `prose-builder`
  - Role: Apply all six text edits and run the token sweeps to exit 1
  - Agent Type: builder
  - Resume: true

- **Validator (sweep)**
  - Name: `sweep-validator`
  - Role: Independently re-run every Verification row and confirm the diff touches no
    executable line
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Correct the reflection name at all four sites (#2853)
- **Task ID**: build-reflection-name
- **Depends On**: none
- **Validates**: `tests/integration/test_reflections_redis.py`
- **Assigned To**: prose-builder
- **Agent Type**: builder
- **Parallel**: true
- In `scripts/popoto_index_cleanup.py:307`, change ``popoto-index-cleanup`` to
  ``redis-index-cleanup``. **Keep** "the daily" and "plus once per worker start" — both
  are correct.
- In `bridge/email_bridge.py:1087`, change "the nightly popoto-index-cleanup sweep" to
  "the daily redis-index-cleanup sweep".
- In `models/ghost_reconcile.py:23`, change "the nightly ``popoto-index-cleanup``
  reflection" to "the daily ``redis-index-cleanup`` reflection". **Keep** the
  ``scripts/popoto_index_cleanup.py``, once/24h parenthetical — the module path is real
  and the interval is correct.
- In `models/dedup.py:70`, change "the nightly popoto-index-cleanup reflection" to "the
  daily redis-index-cleanup reflection". **Keep** "waiting up to 24h for".
- Do **not** touch `scripts/popoto_index_cleanup.py:4-5` or
  `docs/features/popoto-index-hygiene.md` — verified accurate.

### 2. Rewrite the SessionType.GRANITE doc row (#2839)
- **Task ID**: build-granite-row
- **Depends On**: none
- **Assigned To**: prose-builder
- **Agent Type**: builder
- **Parallel**: true
- In `docs/features/standardized-enums.md`, replace the Usage cell of the
  `SessionType.GRANITE` row (line 19) with the historical framing: a historical value;
  its sole producer, the standalone `valor-granite-loop` CLI, was deleted with the PTY
  substrate (plan #1924); nothing creates new sessions with it; retained so pre-cutover
  Redis records carrying `session_type="granite"` keep hydrating and rendering.
- Remove the `tools/granite_interactive_tui_poc/cli.py` path entirely — do not repoint
  it, no successor file exists.
- Remove the sentence "Bridge sessions that run through the granite PTY container are
  typed `ENG`." The container is gone.
- Do **not** carry over `config/enums.py`'s "Removal is #1927's scope" pointer — #1927
  is closed and did not remove the member.
- Leave line 11 as-is; "eng, teammate, or granite" is an accurate member enumeration.

### 3. Drop the closed-issue removal pointer in the enum docstring
- **Task ID**: build-enums-pointer
- **Depends On**: none
- **Assigned To**: prose-builder
- **Agent Type**: builder
- **Parallel**: true
- In `config/enums.py`, delete the `SessionType` docstring sentence "Removal is #1927's
  (schema diet) scope." — verified CLOSED without removing GRANITE.
- Change nothing else in the docstring and no enum member. `GRANITE = "granite"` stays.

### 4. Token-driven sweep to closure
- **Task ID**: build-sweep
- **Depends On**: build-reflection-name, build-granite-row, build-enums-pointer
- **Assigned To**: prose-builder
- **Agent Type**: builder
- **Parallel**: false
- Run both Verification greps below over the whole worktree. Do **not** rely on the
  named-line lists in tasks 1-3.
- For every remaining hit, resolve it by meaning: reflection name → `redis-index-cleanup`;
  deleted package path → historical framing with no path. If a hit is inside
  `.worktrees/` or `docs/plans/`, it is out of scope by exclusion, not by editing.
- Sweep the granite side on the **package path** tokens only
  (`granite_interactive_tui_poc`, `tools/granite_loop`). Do **not** sweep on
  `valor-granite-loop`: that command name is correctly named in past tense by
  `config/enums.py:21`, by `docs/features/bridge-worker-architecture.md:564`, and by the
  replacement row from Task 2. A sweep on it cannot reach exit 1 without deleting
  accurate prose.
- Re-run until both greps return exit 1.
- Confirm the diff touches no executable line: every `+`/`-` line is inside a comment, a
  docstring, or a markdown file.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-sweep
- **Assigned To**: sweep-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run every row of the Verification table independently.
- Confirm `tests/integration/test_reflections_redis.py` is green.
- Confirm the daily-cadence prose survived (it is an easy casualty of a careless sweep).
- Report pass/fail per row.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No stale reflection name | `grep -rn 'popoto-index-cleanup' . --include='*.py' --include='*.md' --include='*.yaml' --include='*.toml' \| grep -v '.worktrees' \| grep -v 'docs/plans'` | exit code 1 |
| No deleted granite package path | `grep -rnE 'granite_interactive_tui_poc\|tools/granite_loop' . --include='*.py' --include='*.md' --include='*.toml' \| grep -v '.worktrees' \| grep -v 'docs/plans'` | exit code 1 |
| Accurate past-tense CLI mentions survive (anti-criterion) | `grep -l 'valor-granite-loop' config/enums.py docs/features/bridge-worker-architecture.md \| wc -l` | output contains 2 |
| Correct reflection name is used | `grep -rn 'redis-index-cleanup' scripts/popoto_index_cleanup.py bridge/email_bridge.py models/ghost_reconcile.py models/dedup.py` | exit code 0 |
| Daily cadence preserved (anti-criterion) | `grep -c 'daily' scripts/popoto_index_cleanup.py` | output > 2 |
| Hygiene doc cadence preserved (anti-criterion) | `grep -c 'daily' docs/features/popoto-index-hygiene.md` | output > 3 |
| Reflection registration untouched (anti-criterion) | `git diff main...HEAD --stat -- config/reflections.yaml \| wc -l` | output contains 0 |
| GRANITE member not removed (anti-criterion) | `grep -c 'GRANITE = "granite"' config/enums.py` | output > 0 |
| Granite row states historical framing | `grep -n 'SessionType.GRANITE' docs/features/standardized-enums.md` | output contains historical |
| Closed-issue pointer dropped | `grep -c "#1927" config/enums.py` | match count == 0 |
| Hygiene doc untouched (anti-criterion) | `git diff main...HEAD --stat -- docs/features/popoto-index-hygiene.md \| wc -l` | output contains 0 |
| Reflection scheduler untouched (anti-criterion) | `git diff main...HEAD --stat -- agent/reflection_scheduler.py agent/reflection_schedule.py \| wc -l` | output contains 0 |
| Reflections config contract green | `scripts/pytest-clean.sh tests/integration/test_reflections_redis.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. Both issues' premises were re-verified against main `f491306c5` at plan time, the
one false premise (#2853's "actual cadence is worker startup only") was corrected and
recorded as a `## Recon Summary` on the issue, and the site inventory was replaced with
a repo-wide token sweep. No decision requires human input.

**Issue comments synced.** #2853 comment `5337658489` (the supervisor's premise
correction) is folded into the Freshness Check and the Technical Approach. #2839
comments `5324500746` and `5324617995` are folded in as well: both ask "does
`SessionType.GRANITE` still have live producers before you repoint the path?" and answer
their own question — it does not, the member stays for historical-record hydration, and
the fix is a one-row doc edit. `5324617995` also surfaced `models/agent_session.py:4, 102`
as a third in-repo wording precedent, now cited in the Technical Approach. Its closing
recommendation ("no plan needed, a direct commit on `main`") is superseded: #2839 is
being carried through the pipeline as the companion of #2853 in one two-issue lane, so
it lands via the lane's PR with `Closes #2839` rather than as a hotfix.
