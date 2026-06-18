---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-18
tracking: https://github.com/tomcounsell/ai/issues/1731
last_comment_id:
revision_applied: true
---

# SDLC forked stage skills divert verdicts/markers — `$ISSUE_NUMBER` never assigned

## Problem

In a local `/do-sdlc {N}` run, the supervisor spawns each SDLC stage inside an Agent-tool
subagent that invokes one `context: fork` stage skill (`/do-plan-critique`, `/do-pr-review`).
Those skills are supposed to record their verdict and stage marker against issue `N`'s session
(`sdlc-local-{N}` or the owning bridge PM session), selected by `--issue-number N`.

**Current behavior:**
The forked skills interpolate `$ISSUE_NUMBER` into every `sdlc-tool verdict record` /
`stage-marker` call, but **never assign that variable**:

- `do-plan-critique/SKILL.md` Plan Resolution (line 53) assigns `ISSUE_NUM`, while every
  downstream consumer references `$ISSUE_NUMBER` (lines 148, 202, 405, 413, 434) — a variable
  that is never set. `grep 'ISSUE_NUMBER='` on the file returns zero hits.
- `do-pr-review/SKILL.md` context-resolution block (lines 220-223) sets `$SDLC_ISSUE_NUMBER`
  (from env) and `$PR_NUMBER`, while the verdict/marker block uses bare `$ISSUE_NUMBER`
  (lines 178, 190, 702-725) — also never assigned. The env table (line 132) claims
  `$SDLC_ISSUE_NUMBER` is "extracted from PR body" but no code performs that extraction.

Consequences, both observed in the real `/do-sdlc 1720` run:

1. If `$ISSUE_NUMBER` is empty and unquoted, the trailing `--issue-number` token has no value.
   Because `--issue-number` is `type=int` (`tools/sdlc_verdict.py:374`,
   `tools/sdlc_stage_marker.py:206`), argparse exits code 2 (`error: argument --issue-number:
   expected one argument`). On marker calls (`2>/dev/null || true`) this silently no-ops →
   marker stuck `in_progress`. On the verdict-record call (no `|| true`) it errors → nothing recorded.
2. If the fork's environment already holds a stale `ISSUE_NUMBER` from a prior context, the value
   diverts the verdict to a *different* issue's session (the observed "latched onto #1724").

Either way the router (`agent/sdlc_router.py:1150`) sees no matching verdict/marker for issue
`N` and returns `Blocked('no matching dispatch rule')` — which reads as a hard pipeline blocker
even though it is just diverted/missing state. Recovery today is manual marker backfilling.

**Live corroboration (reproduced during THIS plan's own `/do-sdlc 1731` run):**
While critiquing this very plan, the `do-plan-critique` subagent computed verdict
`NEEDS REVISION` (artifact_hash `sha256:c8bcdee…`), but the write did **not** persist to the
`sdlc-local-1731` session that the router reads — `sdlc-tool verdict get --stage CRITIQUE
--issue-number 1731` returned `{}`, and the supervisor had to **manually backfill** the verdict
to unblock the router. This is the exact failure mode #1731 describes, reproduced live, and it
directly strengthens Blocker-1 below: a swallowed/diverted recorder write left the router
stalled with no visible error. The fix must **surface** recorder failures, not swallow them.

**Desired outcome:**
A forked stage skill **always** records its verdict and stage marker against the issue the
supervisor dispatched it for — or fails loudly (visible non-zero exit surfaced to the subagent
report) if it genuinely cannot resolve a session — so the router never silently stalls on
diverted/missing state. No manual marker backfilling.

## Freshness Check

**Baseline commit:** `f9ad2e6cbac810b93328810ac0fda78a96ed893c`
**Issue filed at:** 2026-06-18T08:18:10Z
**Disposition:** Unchanged

**File:line references re-verified (all confirmed against baseline):**
- `do-plan-critique/SKILL.md:53` — assigns `ISSUE_NUM`, not `ISSUE_NUMBER` — still holds.
- `do-plan-critique/SKILL.md:405,413,434` — references unassigned `$ISSUE_NUMBER` — still holds.
- `do-pr-review/SKILL.md:220-223` — resolution block sets `$SDLC_ISSUE_NUMBER`/`$PR_NUMBER`, never `ISSUE_NUMBER` — still holds.
- `do-pr-review/SKILL.md:178,190,701-725` — references unassigned `$ISSUE_NUMBER` — still holds.
- `do-sdlc/SKILL.md:87-89` — §3c prompt template passes issue number in prose + `args`, no env export — still holds.
- `tools/sdlc_verdict.py:374`, `tools/sdlc_stage_marker.py:206` — `--issue-number type=int default=None` — still holds.
- `tools/_sdlc_utils.py:283,293` — `issue_number` lookup runs before env-var fallback (the #1671/#1672 fix) — intact, must not be touched.

**Cited sibling issues/PRs re-checked:**
- #1671, #1672 — both CLOSED (fixed by PR #1673, `find_session()` precedence: issue-number beats env-var on writes). Their fix is at the *recorder* layer and is intact.
- #1043, #1042 — both CLOSED; same class (stage state not landing where router reads), not superseding this issue.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** `sdlc-1671-1672.md` (shipped, in
`completed/`) addresses the *recorder precedence* layer; this plan addresses the distinct
*skill arg/env-passing* layer. No active (non-completed) overlap.

**Notes:** The bug requires a local `/do-sdlc` run to reproduce end-to-end, but the root cause
(unassigned `$ISSUE_NUMBER` in skill markdown + argparse `type=int` failure on empty token) is
provable by static reading and a one-line argparse repro, both done during recon. The defect is
present on current main.

## Prior Art

- **#1671 / #1672 (PR #1673)**: "Fix SDLC session-resolution skew: issue-number beats env-var on
  writes." Established that `find_session()` must honor `--issue-number` before the
  `VALOR_SESSION_ID`/`AGENT_SESSION_ID` env-var fallback. **Outcome: succeeded** — the precedence
  fix is intact at `tools/_sdlc_utils.py:283`. But it only helps *when a value reaches the
  recorder*; it cannot save a call where the skill never assigned `$ISSUE_NUMBER`.
- **#1043**: "SDLC dispatches /do-pr-review 8 times on mergeable PR — no terminal state detection."
  Same class (state not landing where the router reads), different mechanism (terminal-state
  detection). Closed. Relevant as a sibling symptom of mis-targeted REVIEW state.
- **#1042**: "SDLC skill audit: close the five blind spots." Closed. Process-level audit; this is
  one concrete recurrence of the "fork loses session context" blind spot.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1673 (#1671/#1672) | Made `find_session()` prefer `issue_number` over env vars on the WRITE path | Fixed the *recorder* layer only. It assumes the caller passes a real `--issue-number` value. It cannot help when the skill markdown interpolates an **unassigned** `$ISSUE_NUMBER` — the value is empty (argparse error) or stale-from-env (divert) before `find_session()` is ever reached. |

**Root cause pattern:** The fix was applied one layer too deep. #1671 hardened *session
resolution given a value*; the actual recurring failure is *the value never being produced* in
the forked skill's shell environment. The skill markdown is the missing link: it references
`$ISSUE_NUMBER` without ever assigning it from `$ARGUMENTS` or an inherited env var.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** Optionally a stricter exit contract for `sdlc-tool verdict record` /
  `stage-marker` when no session can be resolved (loud non-zero instead of silent env fallback to
  a non-owning session). This is additive — existing valid calls are unaffected.
- **Coupling:** Slightly *reduces* coupling between forks by making each skill self-resolve its
  issue number from `$ARGUMENTS`/env instead of depending on an ambient inherited variable.
- **Data ownership:** unchanged — sessions still own verdicts/markers keyed by issue.
- **Reversibility:** High. Changes are localized to three skill markdown files and (optionally)
  two `tools/sdlc_*.py` CLIs; trivially revertible.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the loud-fail-vs-silent-skip decision for the recorder guard)
- Review rounds: 1

The fix itself is small (assign a variable in two skills, harden one dispatch template). The
appetite is Medium rather than Small because the change touches durable pipeline state and must
not regress the #1671 precedence behavior — verification needs a real local `/do-sdlc`-shaped
resolution test plus convergence tests, not just a one-line edit.

## Prerequisites

No prerequisites — this work modifies repo-local skill markdown and `tools/sdlc_*.py`; it has no
external service dependencies. (A live Redis is needed only to exercise the integration test that
asserts a verdict lands on the correct session; the unit tests mock/stub the ORM.)

## Solution

### Key Elements

- **Issue-number resolution in `do-plan-critique`**: Assign `ISSUE_NUMBER` **unconditionally** in
  the Plan Resolution block — parse it directly from `$ARGUMENTS` (the numeric form) on every
  invocation, and if `$ARGUMENTS` is a plan path, recover the tracking issue from the plan
  frontmatter / the issue body. Replace the orphaned `ISSUE_NUM` with the canonical `ISSUE_NUMBER`
  so the variable that downstream calls reference is actually populated. The assignment must
  **clobber** any inherited value — do NOT use `${ISSUE_NUMBER:-…}` deferral, because a
  valid-but-wrong inherited integer (e.g. a stale `1724` latched in from a prior context) would
  survive deferral and divert the write. Direct assignment from the parsed argument on every run
  is the only safe form; quoting alone is inert against a non-empty wrong value (Concern 4).
- **Issue-number resolution in `do-pr-review`**: Assign `ISSUE_NUMBER` **unconditionally** in the
  context-resolution block, sourced from `$SDLC_ISSUE_NUMBER` env first, then fall back to
  extracting the tracking issue from the PR body (`Closes #N` / `tracking:` link) — the extraction
  the env table already promises but never performs.
- **Positive-integer assertion after EVERY resolution path (Blocker 2)**: Both fallback paths can
  legitimately produce an *empty* value — `do-plan-critique`'s plan-frontmatter recovery yields
  nothing if the frontmatter lacks a tracking link, and `do-pr-review`'s `Closes #N` grep yields
  nothing if the PR body omits the keyword. An empty `ISSUE_NUMBER` re-enters the wrong-session
  divert. Immediately after each resolution path completes — and **before any recorder call** —
  assert the value is a positive integer:
  ```bash
  [[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || { echo "do-<skill>: could not resolve a positive-integer ISSUE_NUMBER (got: '${ISSUE_NUMBER}')" >&2; exit 1; }
  ```
  This converts an unresolvable issue number into a loud non-zero exit the subagent reports
  upward, instead of a swallowed divert.
- **Robust hand-off across the fork boundary in `do-sdlc` §3c — args-only (Blocker 3 / Q2
  resolved)**: The dispatch prompt template guarantees the fork receives the issue number as the
  skill `args` and the skill re-parses it from `$ARGUMENTS`. **Do NOT export an ambient
  `SDLC_ISSUE_NUMBER` env var** into the subagent context. Rationale: `find_session()` already
  consults `issue_number` *before* any env-var fallback (`tools/_sdlc_utils.py:283`, the
  #1671/#1672 precedence), so an exported env var is dead weight at best — and at worst it is
  itself a divert vector, since an ambient env value is exactly the "latched onto #1724" mechanism
  this issue is fixing. Args-only keeps the resolution path single-sourced and ambient-free. (Note:
  `do-pr-review` still *reads* `$SDLC_ISSUE_NUMBER` if the env happens to carry it, but the `do-sdlc`
  supervisor does not *set* it — the authoritative path is `$ARGUMENTS` → PR-body extraction.)
- **Loud-fail recorder guard — DEFERRED (Concern 5 / Q1 resolved)**: A recorder-level guard that
  exits non-zero when no owning session can be resolved is **deferred to a follow-up issue**, not
  built here. The skill-side fixes (assign + clobber + positive-integer assertion + de-swallowed
  markers) fully resolve the reported divert. If the guard is ever added later, it MUST gate on the
  session's **ownership of the artifact**, not on a bare `None`/missing `--issue-number` — a
  `None`-only guard would fire on every legitimate `VALOR_SESSION_ID`-based bridge call that
  intentionally omits the flag. File the follow-up issue during build (task 3).

### Flow

`/do-sdlc {N}` → spawns stage subagent with issue number in args + env →
forked skill assigns `$ISSUE_NUMBER` from args/env →
`sdlc-tool verdict record --issue-number "$ISSUE_NUMBER"` →
`find_session(issue_number=N)` resolves `sdlc-local-{N}` (issue-number wins, #1671 intact) →
verdict + marker land on issue N's session →
router reads matching state → advances to next stage (no `no matching dispatch rule`).

### Technical Approach

- **Single source of truth for the variable name.** Standardize on `ISSUE_NUMBER` (the name every
  downstream call already uses). In `do-plan-critique`, the Plan Resolution block currently
  computes `ISSUE_NUM` for the GitHub lookup — make it set `ISSUE_NUMBER` and use that everywhere.
- **Unconditional clobber, not deferral (Concern 4).** Assign `ISSUE_NUMBER` directly from the
  parsed argument on *every* invocation. Quoting is necessary but **not sufficient**: it only
  defends against the empty case; a non-empty but wrong inherited integer (the "latched onto #1724"
  symptom) passes `type=int` and diverts silently. Therefore the assignment must overwrite any
  inherited value — never `${ISSUE_NUMBER:-…}`.
- **Strip the failure-swallow from stage-marker calls (Blocker 1).** The current marker calls use
  `--issue-number $ISSUE_NUMBER 2>/dev/null || true` (`do-plan-critique/SKILL.md:413,434`; the
  marker templates at `:15,:22`; and the do-pr-review marker block). The `2>/dev/null || true`
  hides argparse/recorder failures, so a failed marker silently stays `in_progress` — which is the
  exact "router never silently stalls" outcome this plan must *not* leave broken (and is precisely
  what the live corroboration above shows). **Remove the `2>/dev/null || true` swallow from every
  stage-marker call** so a marker failure surfaces as a visible non-zero exit in the subagent
  report. (do-pr-review already warns against blanket suppression at `:174`; make the
  do-plan-critique markers match.)
- **Always quote `--issue-number "$ISSUE_NUMBER"`** in both skills. With quoting, an empty value
  yields `--issue-number ""`, which argparse `type=int` rejects with a clear error (vs. the
  current unquoted form that drops the token and steals the next flag or errors confusingly).
  Quoting handles the empty case at the call site; the positive-integer assertion (Key Elements)
  catches it one step earlier, before the recorder is even invoked.
- **Do NOT touch `tools/_sdlc_utils.find_session()` precedence.** The #1671/#1672 ordering
  (issue_number before env) is correct and load-bearing; the regression test for it must keep
  passing.
- **`do-pr-review` review-mode is a red herring for persistence.** Re-reading the skill: the
  `CLAUDE_AGENT_REVIEW` branch only governs *which gh token posts the review comment*
  (lines 39-53, 69-70). It does NOT gate verdict/marker persistence — persistence always runs
  (lines 701-725). So the "ran in local-developer mode and never persisted" symptom is actually
  the empty-`$ISSUE_NUMBER` failure, not a review-mode branch skipping persistence. The fix is the
  same variable-assignment fix; the plan does not need to change persistence gating. (Confirm
  during the build spike — see spike-2.)

## Spike Results

### spike-1: Trace the value of `$ISSUE_NUMBER` inside both forked skills
- **Assumption**: "`$ISSUE_NUMBER` arrives empty/wrong inside the fork because the skill markdown never assigns it."
- **Method**: code-read + argparse repro
- **Finding**: CONFIRMED. `do-plan-critique` assigns `ISSUE_NUM` (line 53) but references
  `$ISSUE_NUMBER` (lines 148/202/405/413/434), never assigned. `do-pr-review` sets
  `$SDLC_ISSUE_NUMBER`/`$PR_NUMBER` (lines 220-223) but references `$ISSUE_NUMBER`
  (lines 178/190/701-725), never assigned. Reproduced argparse failure: with the unquoted empty
  variable, `verdict record --issue-number` exits code 2 (`expected one argument`).
- **Confidence**: high
- **Impact on plan**: The fix is squarely in the skill markdown (assign + quote the variable),
  plus a robustness hardening in the `do-sdlc` dispatch template. Recorder precedence (#1671) is
  out of scope.

### spike-2: Why did `/do-pr-review` "enter local-developer mode"?
- **Assumption**: "An SDLC review-agent env signal (`CLAUDE_AGENT_REVIEW`/`SDLC_*`) is not
  propagated into the fork, causing it to skip durable persistence."
- **Method**: code-read
- **Finding**: PARTIALLY REFUTED. `CLAUDE_AGENT_REVIEW` only switches the gh token used to *post*
  the review (lines 39-53); it does NOT gate verdict/marker persistence, which runs unconditionally
  (lines 701-725). The "never persisted" symptom is explained by the empty-`$ISSUE_NUMBER` defect,
  not a persistence-gating branch. The build should re-confirm there is no other persistence gate
  before finalizing.
- **Confidence**: medium (static read; a live `/do-sdlc` run would raise to high)
- **Impact on plan**: No persistence-gating change needed. Keeps scope to variable assignment +
  dispatch hardening. **Caveat discharge (Concern 6):** the "re-confirm no other persistence gate
  in do-pr-review" caveat is converted into an explicit BUILD task — see task `verify-no-persistence-gate`
  below — so the medium-confidence read is positively closed before the skill edits land, not left
  as an open assumption.

## Data Flow

1. **Entry point**: `/do-sdlc {N}` supervisor loop calls `sdlc-tool next-skill --issue-number N`,
   records the dispatch, then spawns a stage subagent (`do-sdlc/SKILL.md` §3c).
2. **Fork boundary**: The subagent invokes the stage skill with `args "{N}"`. The fork's shell
   environment may or may not carry `ISSUE_NUMBER`/`SDLC_ISSUE_NUMBER`.
3. **Stage skill resolution**: The skill *should* assign `$ISSUE_NUMBER` from `$ARGUMENTS`/env, then
   run `sdlc-tool verdict record --issue-number "$ISSUE_NUMBER"` and `stage-marker ...`.
4. **Recorder**: `tools/sdlc_verdict.py` / `tools/sdlc_stage_marker.py` call
   `find_session(issue_number=N, ensure=True)` → `tools/_sdlc_utils.py:283` resolves
   `sdlc-local-{N}` before any env-var fallback.
5. **Output**: verdict + marker persist on issue N's session; `agent/sdlc_router.py` reads them via
   `next-skill --issue-number N` and advances.

The break is at step 3: `$ISSUE_NUMBER` is never assigned, so step 4 receives an empty/stale value.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The stage-marker calls currently swallow failures with `2>/dev/null || true`. After this work
      strips that swallow, add a test that a marker failure (e.g. unresolvable session) surfaces as a
      non-zero exit / visible stderr, and that after a *successful* resolution the marker is
      observably written to the correct session (state change). A silent no-op must not be able to
      pass unnoticed.
- [ ] `sdlc-tool verdict record` exits non-zero on Redis failure (documented at
      do-pr-review:729). The loud-fail recorder guard is DEFERRED (Q1); no recorder-exit test is in
      scope for this plan — but the positive-integer assertion in each skill must be tested (next bullet).
- [ ] The positive-integer assertion (`[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || exit 1`) is exercised:
      a resolution path that produces an empty value must exit non-zero before any recorder call,
      not divert.
- [ ] No `except Exception: pass` blocks are introduced by this work — state explicitly in the PR.

### Empty/Invalid Input Handling
- [ ] Test `sdlc-tool verdict record --issue-number ""` (quoted empty) → argparse rejects with a
      clear error (current `type=int` behavior; assert exit code 2).
- [ ] Test the skill resolution producing an empty `$ISSUE_NUMBER` when neither args nor env supply
      it → the positive-integer assertion must exit non-zero, not silently divert.
- [ ] Test that a numeric `$ARGUMENTS` and a plan-path `$ARGUMENTS` both resolve `$ISSUE_NUMBER`
      correctly in `do-plan-critique`, AND that a plan-path with no tracking link in frontmatter
      trips the assertion (empty → exit 1).
- [ ] Test that an inherited stale `ISSUE_NUMBER` in the environment (e.g. `1724`) is **clobbered**
      by the parsed argument — assign-then-check that the recorded issue number equals the argument,
      not the inherited value (Concern 4 regression guard).

### Error State Rendering
- [ ] The subagent report (`outcome/verdict/failures`) must surface a resolution failure verbatim
      so the supervisor sees an actionable signal instead of a downstream `no matching dispatch rule`.

## Test Impact

- [ ] `tests/` SDLC session-resolution / convergence tests from #1671/#1672 (search:
      `grep -rln "find_session\|issue_number.*env\|sdlc.*convergence" tests/`) — UPDATE if needed:
      these MUST keep passing; add cases for the empty/quoted-empty `--issue-number` path. Do not
      weaken the issue-number-beats-env assertion.
- [ ] Any test asserting `do-plan-critique` / `do-pr-review` argument parsing
      (`grep -rln "do_plan_critique\|do-plan-critique\|do_pr_review\|do-pr-review" tests/`) —
      UPDATE: add assertions that `$ISSUE_NUMBER` is populated and quoted in the recorded calls.
- [ ] New: integration test asserting a forked-style verdict record with `--issue-number N` lands
      on `sdlc-local-{N}` even when `VALOR_SESSION_ID` points elsewhere (regression guard for the
      exact divert in this issue) — REPLACE/extend the #1671 convergence test rather than duplicating.

The exact affected files must be enumerated by the builder via the greps above before editing;
this repo's SDLC test files live under `tests/` with `sdlc`-related names.

## Rabbit Holes

- **Rewriting `find_session()` precedence.** It is already correct (#1671). Touching it risks
  regressing the shipped fix. Out of scope.
- **Redesigning the `do-sdlc` fork-passing mechanism wholesale** (e.g., a structured context
  object passed to every fork). The minimal, robust fix is: assign + quote the variable in the
  skills, and guarantee the dispatch template hands the number across. A broader harness redesign
  is a separate effort.
- **Changing `--issue-number` from `type=int` to `type=str` to "tolerate" empty values.** That
  would *mask* the bug by accepting garbage. The correct direction is to make empty loud, not
  silently tolerated.
- **Reworking `do-pr-review` review-mode (`CLAUDE_AGENT_REVIEW`) gating.** spike-2 shows it does
  not gate persistence; chasing it is a distraction.

## Risks

### Risk 1: Regressing the #1671/#1672 precedence fix
**Impact:** Verdicts again divert to env-var sessions when an inherited `VALOR_SESSION_ID` is present.
**Mitigation:** Do not modify `find_session()`. Keep its convergence/precedence tests green as a
hard gate in Verification.

### Risk 2: Quoting changes break a currently-working call path
**Impact:** A call that relied on the unquoted token-drop behavior could change shape.
**Mitigation:** Every recorder call already expects a real value; quoting only changes the
empty-value case (which is the bug). Add unit tests for both numeric and empty cases.

### Risk 3: A future loud-fail recorder guard breaks legitimate "issue genuinely unknown" calls
**Impact:** Some callers intentionally omit `--issue-number` and rely on `VALOR_SESSION_ID` env
resolution (e.g. bridge PM sessions). A naive guard that fires on a bare missing/`None`
`--issue-number` would break every one of those legitimate calls.
**Mitigation:** The guard is **DEFERRED to a follow-up issue** (Q1 resolved — DEFER); this plan
ships the skill-side fix only, which alone resolves the reported divert. **If** the follow-up ever
builds the guard, it MUST gate on the session's *ownership of the artifact being recorded*, never
on a bare `None`/missing issue number — otherwise it fires for every legit env-based bridge call.

## Race Conditions

No race conditions identified — the change is to synchronous shell-variable assignment in skill
markdown and synchronous argparse/CLI resolution. The session-write path is already serialized by
the existing recorder; this plan does not introduce new concurrency.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1043] Terminal-state detection for repeated `/do-pr-review` dispatch on a
  mergeable PR — related symptom class, already tracked separately; not fixed here.
- Re-touching `tools/_sdlc_utils.find_session()` precedence — already correct via #1671/#1672;
  modifying it is explicitly excluded to avoid regression.

## Update System

This feature changes global skills (`.claude/skills-global/do-plan-critique`,
`do-pr-review`, `do-sdlc`) which are hardlinked to `~/.claude/skills/` on every machine by
`scripts/update/hardlinks.py::sync_claude_dirs()`. No new directory or `RENAMED_REMOVALS` entry is
needed — the files already exist and are already synced; editing them in place propagates via the
existing `/update` wiring. If the recorder guard is added, `tools/sdlc_*.py` ships with the repo
and needs no extra update-system step. **No update-script changes required beyond the standard
pull-and-sync.**

## Agent Integration

The agent reaches `sdlc-tool` via the resolver at `~/.local/bin/sdlc-tool` (see
`docs/features/sdlc-tool-resolver.md`), invoked through the Bash tool — no MCP or
`pyproject.toml [project.scripts]` entry change is needed; the CLI already exists. The forked
stage skills are invoked via the Skill tool inside Agent-tool subagents. No bridge import change.
Integration coverage is the SDLC resolution test asserting a forked-style verdict record lands on
the correct `sdlc-local-{N}` session. **No new agent entry point required — this hardens existing
skill + CLI paths.**

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-tool-resolver.md` (or the closest SDLC pipeline doc) with the
      rule: "Forked stage skills MUST assign and quote `$ISSUE_NUMBER` before passing
      `--issue-number`; the recorder fails loudly when no owning session can be resolved."
- [ ] Add/extend a note in the SDLC pipeline feature doc cross-referencing #1671/#1672 (recorder
      precedence) vs. #1731 (skill arg/env passing) so the two layers are not conflated again.

### Inline Documentation
- [ ] Add a short comment block in both skills near the Issue Resolution step explaining the
      variable name (`ISSUE_NUMBER`) and the quoting requirement, citing #1731.
- [ ] If the recorder guard is added, docstring the guard condition in `tools/sdlc_verdict.py` /
      `tools/sdlc_stage_marker.py`.

## Success Criteria

- [ ] `do-plan-critique/SKILL.md` assigns `ISSUE_NUMBER` (numeric `$ARGUMENTS` and plan-path forms
      both resolve it) and every recorder call uses `--issue-number "$ISSUE_NUMBER"` (quoted).
- [ ] `do-pr-review/SKILL.md` assigns `ISSUE_NUMBER` (from `$SDLC_ISSUE_NUMBER` env, falling back to
      PR-body tracking-issue extraction) and every recorder call uses `--issue-number "$ISSUE_NUMBER"`.
- [ ] `do-sdlc/SKILL.md` §3c dispatch template guarantees the fork receives the issue number via
      both `args` and an explicit env hand-off (e.g. `SDLC_ISSUE_NUMBER`).
- [ ] `grep -n 'ISSUE_NUMBER=' .claude/skills-global/do-plan-critique/SKILL.md` and the
      do-pr-review equivalent each return at least one assignment.
- [ ] No `--issue-number $ISSUE_NUMBER 2>/dev/null || true` swallow remains on any stage-marker
      call: `grep -n '2>/dev/null || true' .claude/skills-global/do-plan-critique/SKILL.md` returns
      no stage-marker line (Blocker 1).
- [ ] Both skills assert `[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]]` after every resolution path, before any
      recorder call (Blocker 2): `grep -c '=~ \^\[0-9\]' SKILL.md` returns ≥1 in each skill.
- [ ] `do-sdlc/SKILL.md` §3c passes the issue number via `args` only and does NOT export
      `SDLC_ISSUE_NUMBER`: `grep -c 'export SDLC_ISSUE_NUMBER' .claude/skills-global/do-sdlc/SKILL.md`
      returns 0 (Blocker 3 / Q2).
- [ ] Regression: an integration test confirms a forked-style `verdict record --issue-number N`
      lands on `sdlc-local-{N}` even when `VALOR_SESSION_ID` points to a different session.
- [ ] **E2E read-back (executable):** run a forked critique against issue A (with a *conflicting*
      env session set), then verify the verdict is readable on issue A:
      `sdlc-tool verdict get --stage CRITIQUE --issue-number A` returns a non-empty record for issue
      A — not `{}` and not a record on the env-session's issue. This is the executable proof the
      live-corroboration failure is fixed (Nit 7).
- [ ] The #1671/#1672 precedence/convergence tests still pass unchanged.
- [ ] A follow-up issue exists for the deferred ownership-gated loud-fail recorder guard (Q1 = DEFER).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skills)**
  - Name: skills-builder
  - Role: Edit `do-plan-critique`, `do-pr-review`, `do-sdlc` markdown to assign + quote `ISSUE_NUMBER` and harden the dispatch template
  - Agent Type: builder
  - Resume: true

- **Builder (follow-up issue + spike-2 discharge)**
  - Name: recorder-builder
  - Role: File the deferred loud-fail-guard follow-up issue (ownership-gated, NOT `None`-gated); re-confirm no other persistence gate exists in `do-pr-review` (spike-2 caveat discharge)
  - Agent Type: builder
  - Resume: true

- **Test engineer (resolution)**
  - Name: resolution-tester
  - Role: Add the divert-regression integration test + empty/quoted-empty unit cases; keep #1671 tests green
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: sdlc-validator
  - Role: Verify success criteria, run greps + tests, confirm no `find_session()` change
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Fix issue-number assignment in the two forked skills
- **Task ID**: build-skills-resolution
- **Depends On**: none
- **Validates**: `grep -n 'ISSUE_NUMBER=' do-plan-critique/SKILL.md` and do-pr-review return ≥1 each
- **Informed By**: spike-1 (confirmed: `ISSUE_NUMBER` never assigned), spike-2 (review-mode does not gate persistence)
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- In `do-plan-critique/SKILL.md`: replace `ISSUE_NUM` with `ISSUE_NUMBER` in Plan Resolution; assign it **unconditionally** (clobber, never `${VAR:-…}`); ensure both numeric and plan-path `$ARGUMENTS` populate it (recover issue from plan frontmatter/issue body for the path case).
- In `do-pr-review/SKILL.md`: assign `ISSUE_NUMBER` unconditionally from `$SDLC_ISSUE_NUMBER`, falling back to extracting the tracking issue (`Closes #N`) from the PR body.
- **After every resolution path in both skills**, add the positive-integer assertion `[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || { echo "...">&2; exit 1; }` **before any recorder call** (Blocker 2).
- **Strip `2>/dev/null || true` from every stage-marker call** in `do-plan-critique` (lines ~15, ~22, ~413, ~434) so marker failures surface (Blocker 1). Ensure do-pr-review markers do not swallow either.
- Quote every `--issue-number "$ISSUE_NUMBER"` in both skills.

### 2. Harden the do-sdlc dispatch hand-off (args-only)
- **Task ID**: build-dispatch-handoff
- **Depends On**: none
- **Validates**: §3c template passes the issue number via `args`; does NOT export `SDLC_ISSUE_NUMBER`
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `do-sdlc/SKILL.md` §3c so the fork receives the issue number via the skill `args` and re-parses it from `$ARGUMENTS`. **Do NOT add an ambient `SDLC_ISSUE_NUMBER` export** — `find_session()` already prefers issue-number over env, so the export is dead weight / a divert vector (Blocker 3 / Q2 resolved: args-only).

### 3. File the deferred guard follow-up + discharge spike-2 caveat
- **Task ID**: file-guard-followup
- **Depends On**: none
- **Validates**: a follow-up issue exists describing the ownership-gated loud-fail recorder guard; a build note confirms no other persistence gate in do-pr-review
- **Assigned To**: recorder-builder
- **Agent Type**: builder
- **Parallel**: true
- File a follow-up GitHub issue for the loud-fail recorder guard (Q1 = DEFER). The issue MUST state the guard gates on **artifact ownership**, not on a bare `None`/missing `--issue-number` (else it fires for every legit `VALOR_SESSION_ID` bridge call).
- **Discharge spike-2 caveat (Concern 6):** re-read `do-pr-review/SKILL.md` and confirm in the PR description that NO branch other than the (already-ruled-out) `CLAUDE_AGENT_REVIEW` token switch gates verdict/marker persistence — i.e. persistence runs unconditionally (lines ~701-725). Record the confirmation as a checked task.

### 3b. Re-confirm do-pr-review persistence is ungated
- **Task ID**: verify-no-persistence-gate
- **Depends On**: none
- **Validates**: PR description records that verdict/marker persistence in do-pr-review is unconditional (no gate beyond the CLAUDE_AGENT_REVIEW token switch)
- **Assigned To**: recorder-builder
- **Agent Type**: builder
- **Parallel**: true
- This is the explicit discharge of spike-2's medium-confidence caveat. Grep do-pr-review for any conditional wrapping the `verdict record` / `stage-marker` calls; confirm none gate persistence.

### 4. Tests
- **Task ID**: build-tests
- **Depends On**: build-skills-resolution, build-dispatch-handoff
- **Validates**: new divert-regression test + empty/quoted-empty cases; #1671 tests green
- **Assigned To**: resolution-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add integration test: forked-style `verdict record --issue-number N` lands on `sdlc-local-{N}` despite a conflicting `VALOR_SESSION_ID`.
- Add unit tests for `--issue-number ""` (quoted empty) rejection and skill variable population.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-skills-resolution, build-dispatch-handoff, build-tests
- **Assigned To**: sdlc-validator (or documentarian)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-tool-resolver.md` / SDLC pipeline doc with the variable-assignment + quoting rule and the #1671-vs-#1731 layer distinction.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: sdlc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all greps + tests; confirm `find_session()` untouched; verify every success criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| critique skill assigns ISSUE_NUMBER | `grep -c 'ISSUE_NUMBER=' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| pr-review skill assigns ISSUE_NUMBER | `grep -c 'ISSUE_NUMBER=' .claude/skills-global/do-pr-review/SKILL.md` | output > 0 |
| recorder calls are quoted (critique) | `grep -c 'issue-number "\$ISSUE_NUMBER"' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| no stage-marker swallow remains | `grep -c '2>/dev/null \|\| true' .claude/skills-global/do-plan-critique/SKILL.md` | 0 (Blocker 1) |
| positive-int assertion present (critique) | `grep -c '=~ \^\[0-9\]' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 (Blocker 2) |
| positive-int assertion present (pr-review) | `grep -c '=~ \^\[0-9\]' .claude/skills-global/do-pr-review/SKILL.md` | output > 0 (Blocker 2) |
| no ambient env export in do-sdlc | `grep -c 'export SDLC_ISSUE_NUMBER' .claude/skills-global/do-sdlc/SKILL.md` | 0 (Blocker 3 / Q2) |
| find_session precedence untouched | `git diff --quiet HEAD -- tools/_sdlc_utils.py` | exit code 0 |
| Tests pass | `pytest tests/ -x -q -k sdlc` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

_Both open questions were resolved during the revision pass (NEEDS REVISION critique). Recorded
here for the trail:_

1. **Loud-fail recorder guard: ship it, or skill-side fix only?** — **RESOLVED: DEFER.** 2 of 3
   critics favored deferral. The skill-side fix (assign + clobber + positive-integer assertion +
   de-swallowed markers) fully resolves the reported divert. The recorder guard is filed as a
   follow-up issue (task 3). If it is ever built, it MUST gate on the recording session's
   **ownership of the artifact**, never on a bare `None`/missing `--issue-number` — a `None`-only
   guard would fire for every legitimate `VALOR_SESSION_ID` bridge call.
2. **Dispatch hand-off mechanism: `args`-only, or also export `SDLC_ISSUE_NUMBER`?** — **RESOLVED:
   args-only.** Do NOT export an ambient `SDLC_ISSUE_NUMBER` env var. `find_session()` consults
   `issue_number` before env (`tools/_sdlc_utils.py:283`), so the export is dead weight at best and
   a divert vector at worst (it is the same ambient-env mechanism that produced the original
   "latched onto #1724" symptom). The skill re-parses the number from `$ARGUMENTS`.
