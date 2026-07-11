---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/1926
last_comment_id: none
---

# Post-teardown scar-tissue removal: happy-path liveness + Sentry reporting + removed-defenses ledger

## Problem

The granite PTY teardown (#1924, merged as PR #1930 on 2026-07-07) deleted the
PTY substrate and cut every session over to headless `claude -p` execution. What
it did NOT delete is the accreted failure-handling machinery built to defend that
fragile PTY substrate: a stall-classifier taxonomy, a crash-signature library,
three watchdogs, escalation ladders, and the bridge nudge auto-continue counter.
Much of that machinery classifies and guards failure modes that **cannot occur
under protocol-driven headless execution** — the harness now emits a structured
`result` event per turn, so "did the agent stop producing output" is answered by
the protocol, not by scraping a PTY master fd.

**Current behavior:**
Dead PTY-era rationale still ships in the codebase (e.g. the `worker_watchdog`
U-state "kill the PTY master fds" narrative), and the reporting-layer taxonomies
(`session_stall_classifier`, `crash_signature`) still enumerate classes that no
longer occur. There is no single written record of *what each removed defense
guarded against*, so a future targeted fix has no map back to the gotcha.

**Desired outcome:**
Keep the happy path plus Sentry error reporting. Trim PTY-era rationale and
pare the reporting taxonomies to classes actually observed post-cutover. For
every deletion, add one entry to a **removed-defenses ledger**
(`docs/removed-defenses.md`) naming the gotcha the machinery guarded against, so
that when a matching Sentry issue reappears we re-apply a *targeted* fix — never
the old blanket machinery. Make explicit, evidence-grounded keep-vs-cut calls on
the three "review, don't blindly delete" surfaces.

## Freshness Check

**Baseline commit:** `d7753e02` (worktree `session/dev-a1552d0a`); `main` at `11ad27fc`.
**Issue filed at:** 2026-07-06T08:07:09Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_runner/liveness.py::derive_sdk_ever_output` — the "one
  authoritative liveness signal" (owner directive 2026-07-07) already exists and
  is imported by `session_health` at its recovery-path derivation sites. The
  liveness *collapse* the issue lists as a scope candidate is therefore **largely
  already done** (#1843 Gap B, #2004 hygiene sweep). Remaining work is trimming
  residual duplicate inference, not rebuilding.
- `agent/session_health.py::_confirm_subprocess_dead` (line 1551) — the
  `TypeError: '<=' not supported between 'str' and 'int'` (Sentry VALOR-E1/D0)
  was **already fixed** by commit `b92d9a44` (2026-07-08, "Fix TypeError in
  _confirm_subprocess_dead on string claude_pid"): the defensive `int(pid)` cast
  now sits at lines 1597-1601 ahead of the `pid <= 0` comparison. VALOR-E1's
  events 9h ago are **deploy lag**, not a live defect on `main` (corroborated by
  VALOR-CT: "worker running 2f659c7f but HEAD is bcda4b1c"). **This bug is NOT in
  scope** — re-fixing an already-fixed defect would be building for a stale
  problem. (Correction to the intake instruction, which asked to fold it in.)
- `agent/output_router.py:39` `MAX_NUDGE_COUNT = 50` — still present and still
  wired live via `session_executor.send_to_chat -> route_session_output`.
- `monitoring/worker_watchdog.py::recover()` (lines 219-306) — kill ladder
  (W1-W5) and U-state fd narrative (docstring 8-21, 231-236) present as described.
- `monitoring/bridge_watchdog.py::execute_recovery` (line 675), `revert_last_commit`
  (line 636) — 5-level ladder + revert present as described.

**Cited sibling issues/PRs re-checked:**
- #1924 — closed 2026-07-07; delivered by PR #1930 (merged 2026-07-07). This is
  the enabling prerequisite; landed.

**Commits on main since issue was filed (touching referenced files):**
- `b92d9a44` (2026-07-08) — fixed the `_confirm_subprocess_dead` TypeError.
  **Already fixes** the VALOR-E1/D0 item; removed from scope.
- `ffed9ba0` (#2004 "Resilience hygiene sweep") — unified session evidence,
  enforced artifact freshness. **Partially addresses** the liveness-collapse
  candidate (introduced `has_demonstrable_activity` as the shared leaf). Reduces
  this plan's liveness scope to residual trimming.
- `39bf0fc7` (#2002 epoch-scope tool-timeout guard) — touched session_health;
  irrelevant to this scope.

**Active plans in `docs/plans/` overlapping this area:**
- `resilience-simplification-three-tier.md` (status: draft, tracking: none). A
  broader "resilience program" synthesized 2026-07-10 from 21 closed bugs. It
  overlaps thematically (liveness/recovery, degradation contract) but is a
  *program* plan whose items each ship as their own issue/PR. **Coordination, not
  a blocker:** #1926 is the narrower PTY-teardown scar-tissue slice. Where the two
  touch the same file (`session_health.py`), #1926 confines itself to removing
  PTY-dead rationale and does not implement the three-tier program's event-sourced
  stage log (T3.4) or unified evidence model (that's #2004's territory, already
  landed). Note the overlap in the PR body so the resilience program can subtract
  what #1926 ships.

**Notes:** The "few weeks of headless telemetry" prerequisite is only ~4 days
satisfied, but the PTY-vs-headless signal is already clean and decisive (see
Research / Recon), so the deletion decisions are evidence-grounded rather than
predicted.

## Prior Art

- **#1924 / PR #1930** — Granite PTY teardown: deleted the PTY substrate, cut all
  sessions to headless `claude -p`. This is the direct predecessor; #1926 removes
  the failure machinery #1930 left behind.
- **#1843** — Headless-runner zombie liveness: introduced the headless
  `last_stdout_at` liveness stamp replacing the PTY-era `last_pty_read_loop_at`
  (see `agent/session_runner/liveness.py` docstring). Established `derive_sdk_ever_output`.
- **PR #2004 / #2011 (`ffed9ba0`)** — Resilience hygiene sweep: unified session
  evidence via `has_demonstrable_activity`, the shared leaf both
  `session_stall_classifier` and `crash_signature` already import.
- **`b92d9a44`** — Fixed the `_confirm_subprocess_dead` string-pid TypeError
  (the VALOR-E1/D0 defect). Already on main.
- No prior attempt has written a removed-defenses ledger; this is greenfield for
  that artifact.

## Research

**Queries used (Sentry, yudame org / project 4511091961888768):**
- `is:unresolved` sorted by frequency, 14d window
- `is:unresolved firstSeen:-7d` sorted by frequency, 7d window

**Key findings (headless-failure telemetry, 2026-07-11):**
- **PTY-era failure classes stopped firing at the #1930 cutover boundary (~5 days
  ago).** Every one of these has `lastSeen ~5d ago` and no headless-era recurrence:
  `Watchdog W4/W5 U-state / "kill the PTY master fds"` (VALOR-B7/B8, 210 events
  each), `[pty-pool] slot stuck/spawn failed` (VALOR-BF/A4), `[granite-container]
  startup plateau` (VALOR-AX), `[granite-exit-anomaly]` (VALOR-A3), `[deadman]
  loop beacon stale` (VALOR-BE/BG), `[executor-guard] refusing empty container
  message` (VALOR-B5). → These are the machinery to trim/ledger.
- **Headless-era real failures (last 2-3 days) are a small, different set:**
  `pm turn subprocess exited 143 without a result event` (VALOR-E0 — SIGTERM
  during a turn, i.e. steering preempt), `Harness exited without a result event`
  (VALOR-2M, still active), `StatusConflictError` session-lifecycle race
  (VALOR-DZ), and Redis MISCONF/disk-I/O infra noise (VALOR-DB family — a Redis
  RDB-persistence outage 2 days ago, NOT scar tissue). → These are the classes the
  pruned taxonomy must retain.
- The surviving real-failure surface maps cleanly onto the target liveness model:
  subprocess-alive (exit 143 / exit-without-result) + turn-timeout + last-turn-age.
  No headless failure observed requires a stall *classifier taxonomy* richer than
  "started vs never-started" and "clean vs non-clean exit."

## Data Flow

Liveness / failure-handling data flow, post-headless:

1. **Turn boundary (entry):** `agent/session_runner/runner.py` spawns `claude -p`,
   stamps `last_stdout_at` on first output and `last_turn_at` on the harness
   `result` event (`agent.hooks.liveness_writers`). The runner OWNS subprocess
   spawn/kill and turn-timeout (`turn_timeout_for`, `role_driver.turn_timeout_s`).
2. **Authoritative signal:** `agent/session_runner/liveness.py::derive_sdk_ever_output`
   folds `{last_tool_use_at, last_turn_at, last_stdout_at}` into the single
   "has the SDK ever produced output" predicate. `has_demonstrable_activity`
   folds `{turn_count, last_tool_use_at}` into the progress predicate.
3. **Worker recovery (consumer):** `agent/session_health.py` reads those leaves at
   its recovery-path derivation sites; `_confirm_subprocess_dead` reaps orphans.
4. **Reporting (read-only, no writes):** `session_stall_classifier.classify_session_stall`
   and `crash_signature.extract_signature` consume the same leaves to produce
   advisory verdicts / signature keys for reflections + Sentry. Zero mutations.
5. **Watchdogs (out-of-process):** `monitoring/{bridge,worker,session}_watchdog.py`
   supervise the bridge/worker processes and pending-session stalls.
6. **Bridge output routing:** `agent/output_router.py::route_session_output` decides
   deliver-vs-nudge; `session_executor.send_to_chat` executes it, incrementing
   `AgentSession.auto_continue_count` against `MAX_NUDGE_COUNT`.

The fix layer for THIS plan is (4) and (5) — the reporting taxonomies and the
watchdog PTY rationale — plus documentation of the keep decisions on (5)/(6).

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none to public signatures. `derive_sdk_ever_output` /
  `has_demonstrable_activity` remain the authoritative leaves. Removing enum
  members / verdict reasons from the reporting modules is an internal-taxonomy
  change; callers already branch only on `level in {healthy, suspect, stalled}`
  and `resumable`.
- **Coupling:** decreases. Trimming dead PTY rationale and unobserved taxonomy
  classes reduces the surface that a future change must reason about. The single
  authoritative liveness signal (already owned by `session_runner`) is
  reaffirmed, not spread wider.
- **Data ownership:** unchanged — liveness stays owned by `session_runner`.
- **Reversibility:** high. Every deletion is recorded in the ledger with the
  exact gotcha and the Sentry class that would signal its return, so a targeted
  re-apply is a small, well-scoped change.

## Appetite

**Size:** Large

**Team:** Solo dev (fanned to builders), PM check-ins, 1 code reviewer.

**Interactions:**
- PM check-ins: 2-3 (keep-vs-cut sign-off on the nudge counter; overlap
  coordination with the resilience program plan)
- Review rounds: 1 (code review + cruft audit on a deletion-heavy diff)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #1924 landed | `gh pr view 1930 --json state -q .state` | Headless cutover must be merged (returns `MERGED`) |
| Sentry read access | `python -c "import os; assert os.environ.get('SENTRY_DSN') or True"` | Telemetry grounding (advisory; MCP used at plan time) |

## Solution

### Key Elements

- **Removed-defenses ledger (`docs/removed-defenses.md`)**: the durable artifact.
  One entry per deleted/trimmed defense: what it guarded against, why it's dead
  under headless, and the Sentry signature that would justify a targeted re-apply.
- **Worker watchdog PTY-rationale trim**: delete the dead "kill the PTY master
  fds / cross-process fd close" U-state *narrative* and any PTY-master-read code
  branches in `monitoring/worker_watchdog.py`; **keep** the SIGTERM->SIGKILL->
  bootout kill ladder (W1-W3) and launchd-respawn fallback, re-justified for
  headless subprocesses.
- **Stall-classifier taxonomy prune**: reduce `session_stall_classifier.py` to the
  classes actually observed post-cutover (never-started, idle/turn-timeout);
  delete unobserved corroboration branches. Module stays pure read-only reporting.
- **Crash-signature library prune**: reduce `crash_signature.py` normalization to
  the event shapes that occur under headless (`status_transition`,
  `idle_gap` bucketing, never-started); drop PTY-only signature classes.
- **Residual liveness inference trim**: in `session_health.py`, remove any
  remaining duplicated multi-field liveness inference that `derive_sdk_ever_output`
  already centralizes (no new signal, no wider spread — a subtraction only).
- **Keep decisions, documented**: `bridge_watchdog.py` 5-level ladder +
  revert-commit KEPT (bridge-process resilience, orthogonal to PTY); the 50-cap
  nudge counter KEPT as a runaway backstop (recommendation below).

### Flow

Deletion PR journey:
`main (headless, post-#1930)` → trim PTY-dead rationale + prune taxonomies →
write ledger entry per deletion → keep-decisions documented → narrow tests green →
cruft audit confirms no half-migrations → merge.

### Technical Approach

- **Ledger-first discipline:** no deletion lands without its ledger entry in the
  same commit. The ledger is the compensating control for aggressive cutting.
- **Trim, don't gut, the reporting modules:** `session_stall_classifier` and
  `crash_signature` remain — they are already pure, side-effect-free, and import
  the authoritative liveness leaf. The change removes *enum members / verdict
  reasons / signature classes* that map to PTY-era events with zero post-cutover
  occurrences, plus their now-dead threshold constants.
- **Watchdog: narrative vs mechanism split.** The PTY-fd U-state *reasoning* is
  dead (headless subprocesses are ordinary killable children, not PTY masters in
  uninterruptible sleep). The *kill ladder mechanism* (escalating signals +
  bootout + launchd respawn) is substrate-agnostic and stays. Rewrite the
  docstrings/comments to the headless status quo (NO_LEGACY_CODE: no "previously
  PTY..." archaeology in the code — that lives in the ledger).
- **No parallel-run artifacts:** fully delete; describe only the new status quo.

### Keep-vs-Cut Recommendations (issue-mandated, evidence-grounded)

**(1) 50-cap bridge nudge auto-continue counter (`MAX_NUDGE_COUNT`, `agent/output_router.py:39`) — RECOMMENDATION: KEEP (do not delete in this PR).**
Rationale: it is a happy-path *runaway backstop* (bounds infinite nudging so a
misbehaving session eventually delivers instead of looping), still wired live via
`session_executor.send_to_chat -> route_session_output`, and **orthogonal to PTY** —
it never read a PTY fd. Telemetry shows zero runaway-nudge Sentry issues in the
headless era, so there is no evidence justifying its removal, and deleting live
control-flow without evidence violates the "prune against evidence, not
predictions" mandate. The issue's phrase "the runner owns progression by
construction" is *aspirationally* true, but under the current headless
architecture the bridge nudge loop is still the mechanism that re-drives an idle
PM/eng session between turns — establishing that it is fully vestigial requires a
control-flow trace that is out of scope here. **Open decision flagged for the PM**
(see Open Questions): whether to schedule a *separate* investigation into
retiring the whole bridge nudge loop in favor of the steering list as the single
inbound channel. This PR keeps the counter and documents the reasoning.

**(2) worker_watchdog U-state W3-W5 fd-table narrative — RECOMMENDATION: TRIM the narrative, KEEP the kill ladder.**
Rationale: the U-state ("uninterruptible sleep" from a blocking PTY-master read
that cross-process fd-close cannot free) is a PTY-substrate phenomenon —
telemetry confirms W4/W5 "kill the PTY master fds" (VALOR-B7/B8) stopped firing at
the cutover. Headless `claude -p` subprocesses are ordinary process-group leaders
reaped by the runner's SIGTERM->SIGKILL->`killpg`. So the fd-table narrative and
any PTY-master-read escalation branches are dead and get trimmed + ledgered. The
**kill ladder itself** (W1 SIGTERM, W2 SIGKILL, W3 bootout, launchd respawn) is
substrate-agnostic worker resilience and **stays**, re-justified for headless.

**(3) stall classifier as pure reporting — RECOMMENDATION: KEEP as pure reporting, PRUNE taxonomy.**
Rationale: it is already zero-write and import-isolated from the kill/recovery
machinery (enforced by tests). It stays as the advisory/Sentry reporting layer.
Only its *taxonomy* is pruned to observed classes. Not ambiguous.

**Kept without change: `bridge_watchdog.py` 5-level escalation ladder + revert-commit.**
This supervises the *bridge process* (Telethon connectivity, hibernation,
auto-revert of a bad commit) — orthogonal to PTY and to session execution. No
change beyond a ledger note explaining why it is explicitly retained.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `session_stall_classifier.classify_session_stall` retains its fail-soft
  `except -> "healthy"` contract after the prune; test asserts a malformed event
  window still returns `healthy` (observable, not swallowed).
- [ ] `crash_signature.extract_signature` retains its `-> unclassifiable` fallback
  after the prune; test asserts an empty/garbage trace yields the sentinel.
- [ ] `worker_watchdog` kill-ladder branches retain observable logging on each
  rung after the narrative trim; test asserts the SIGKILL rung logs when SIGTERM
  fails to reap (no silent swallow).

### Empty/Invalid Input Handling
- [ ] Classifier + signature extractor tested with empty `events=[]`, `None`
  session, and whitespace/None fields — must not raise, must return the safe default.
- [ ] Confirm the nudge path's empty-output branch (`nudge_empty` /
  `deliver_fallback`) is unchanged by this PR (regression guard only — the counter
  is kept).

### Error State Rendering
- [ ] No new user-visible surface. The watchdog Sentry-report path must still fire
  on the kept kill-ladder rungs; test asserts a Sentry capture (or the log the
  reporter reads) is emitted when the ladder reaches its terminal rung.

## Test Impact

- [ ] `tests/**/test_session_stall_classifier*.py` — UPDATE: remove assertions on
  pruned verdict reasons / threshold constants; keep never-started + idle-timeout
  + fail-soft cases. (Exact file located at build time via
  `grep -rl session_stall_classifier tests/`.)
- [ ] `tests/**/test_crash_signature*.py` — UPDATE: drop cases asserting
  PTY-only signature classes; keep `status_transition` / `idle_gap` bucketing /
  never-started / unclassifiable cases.
- [ ] `tests/**/test_worker_watchdog*.py` — UPDATE: delete assertions on the
  removed U-state fd narrative / PTY-master-read branches; keep kill-ladder
  (SIGTERM->SIGKILL->bootout->respawn) assertions.
- [ ] `tests/**/test_output_router*.py` — VERIFY UNCHANGED (regression guard): the
  50-cap counter is kept, so these must continue to pass without edits. If any
  edit is needed, the keep-decision is wrong — stop and escalate.
- [ ] `tests/**/test_session_health*.py` — UPDATE only if residual-inference
  trimming touches a covered path; otherwise assert no behavior change. Do NOT
  touch `_confirm_subprocess_dead` tests (that bug is already fixed on main).
- [ ] Import-isolation guard tests (classifier/signature must not import
  `session_health`) — VERIFY UNCHANGED; the prune must not add the forbidden import.

Exact test files and cases are enumerated by the builder from `grep` at build
start; the dispositions above are binding.

## Rabbit Holes

- **Rewriting the liveness model from scratch.** It already exists
  (`derive_sdk_ever_output`, owner-directed, #1843/#2004). This plan *trims residual
  duplication*, it does not re-architect liveness. Do not spread inference wider.
- **Deleting the whole bridge nudge loop.** Tempting given "the runner owns
  progression," but establishing vestigiality needs a control-flow trace and
  risks removing live progression control with no telemetry backing. Out of scope;
  flagged as an open decision for a separate investigation.
- **Merging into the resilience-simplification three-tier program.** That is a
  broader program plan; #1926 is a focused slice. Coordinate, don't absorb.
- **"Consolidate toward one supervisor per process" as a rewrite.** The issue
  says *toward*; the watchdogs already map one-per-process (bridge/worker/session
  cover distinct processes). Do not collapse them into a single mega-watchdog —
  that is a re-architecture, not scar-tissue removal.
- **Re-fixing VALOR-E1/D0.** Already fixed by `b92d9a44`. Touching it is building
  for a stale problem.

## Risks

### Risk 1: Pruning a taxonomy class that DOES occur under headless (just rarely).
**Impact:** A real (rare) failure becomes unclassified/unreported.
**Mitigation:** Prune only classes with zero post-cutover Sentry occurrences over
the 14-day window; each pruned class gets a ledger entry naming the exact Sentry
signature to watch. Reporting modules keep their `unclassifiable`/`healthy`
fallbacks, so an unforeseen shape degrades to a safe default plus a generic
Sentry event rather than a crash.

### Risk 2: Trimming a worker_watchdog branch that the kill ladder still needs.
**Impact:** A worker fails to be reaped in some edge case.
**Mitigation:** Split strictly along narrative-vs-mechanism. Only PTY-master-read
/ fd-close *rationale and branches* are removed; every signal-delivery rung stays.
Tests assert the full SIGTERM->SIGKILL->bootout->respawn sequence post-trim.

### Risk 3: The kept 50-cap counter turns out to be genuinely vestigial.
**Impact:** Dead code remains (violates NO_LEGACY_CODE if truly unreachable).
**Mitigation:** It is demonstrably reachable today (`send_to_chat` call path).
"Kept and documented with rationale" is a deliberate scope decision, not
legacy tolerance; the open-decision flag routes the vestigiality question to a
separate, evidence-driven follow-up rather than a blind delete now.

## Race Conditions

No new race conditions introduced — this plan is subtractive (removing rationale,
taxonomy members, and dead branches) plus one additive doc. The kept kill ladder's
existing PID-reuse caveat and `run_in_executor` offload (already documented in
`_confirm_subprocess_dead`) are unchanged. The reporting modules are already
zero-write and race-free by construction.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1926] Retiring the entire bridge nudge loop / making the
  steering list the single inbound channel — flagged as an open decision requiring
  a control-flow trace; belongs to its own investigation, not this scar-tissue PR.
  (Tracked as an Open Question against this issue; if the PM greenlights, it gets
  its own issue before any code moves.)
- [SEPARATE-SLUG #1926] The `resilience-simplification-three-tier` program's Tier
  1-3 items (event-sourced stage log, degradation contract, unified evidence
  model) — separate program plan.
- Re-fixing `_confirm_subprocess_dead` VALOR-E1/D0 — already fixed on `main`
  (`b92d9a44`); nothing to do.

## Update System

No update system changes required — this feature is purely internal (deletion of
dead rationale + taxonomy prune + one new doc). No new dependencies, config files,
or migration steps. No Popoto model changes (the kept `auto_continue_count` field
is untouched), so `scripts/update/migrations.py` needs no new migration. The
`/update` skill propagates the code changes automatically on the next run; the new
`docs/removed-defenses.md` ships as an ordinary tracked file.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. No new
CLI entry point in `pyproject.toml [project.scripts]`, no new MCP surface in
`mcp_servers/` or `.mcp.json`, and the bridge (`bridge/telegram_bridge.py`) needs
no new import. The kept nudge counter and watchdog kill ladder are already wired;
this plan only trims dead rationale around them. Existing integration tests that
exercise the session lifecycle and the nudge/deliver path serve as the regression
surface.

## Documentation

### Feature Documentation
- [ ] Create `docs/removed-defenses.md` — the removed-defenses ledger. One entry
  per deletion: (a) the defense, (b) the gotcha it guarded against, (c) why it is
  dead under headless, (d) the Sentry signature that would justify a targeted
  re-apply. This is the load-bearing deliverable.
- [ ] Update `docs/features/bridge-worker-architecture.md` — reflect the trimmed
  worker_watchdog narrative and the headless liveness status quo (describe only
  the new status quo; no PTY archaeology in the doc body — that lives in the
  ledger).
- [ ] Update `docs/features/bridge-self-healing.md` — re-state the watchdog fleet
  section: bridge 5-level ladder KEPT, worker kill ladder KEPT (re-justified),
  U-state PTY-fd narrative REMOVED.
- [ ] Add a `docs/removed-defenses.md` entry to `docs/features/README.md` index
  table (or the appropriate index) so the ledger is discoverable.

### External Documentation Site
- No external docs site changes (internal system docs only).

### Inline Documentation
- [ ] Rewrite `monitoring/worker_watchdog.py` docstrings/comments to the headless
  status quo (remove PTY-master-fd rationale).
- [ ] Prune stale threshold-constant comments in `session_stall_classifier.py` /
  `crash_signature.py` for any removed class.

## Success Criteria

- [ ] `docs/removed-defenses.md` exists with one entry per deletion, each naming
  the guarded gotcha + the Sentry signature to watch for re-apply.
- [ ] `monitoring/worker_watchdog.py` contains no PTY-master-fd / U-state fd-close
  rationale; the SIGTERM->SIGKILL->bootout->respawn kill ladder remains and its
  tests pass.
- [ ] `session_stall_classifier.py` and `crash_signature.py` enumerate only
  post-cutover-observed classes; both remain pure (zero writes) and neither
  imports `session_health` (isolation guard still green).
- [ ] `agent/output_router.py` `MAX_NUDGE_COUNT` and `AgentSession.auto_continue_count`
  are unchanged (kept-with-rationale); `test_output_router*` passes without edits.
- [ ] No commented-out code, no "previously PTY" archaeology in code bodies, no
  parallel-run artifacts (cruft audit clean).
- [ ] Narrow-scope tests pass (`/do-test` on the touched files).
- [ ] Documentation updated (`/do-docs`).
- [ ] `grep -rn "master fd\|pty master\|uninterruptible sleep" monitoring/ agent/`
  returns no code-body matches (rationale fully removed).

## Team Orchestration

The lead (Dev) orchestrates; builders execute disjoint file sets in the single
`session/dev-a1552d0a` worktree so commits never interleave.

### Team Members

- **Builder (watchdog-trim)**
  - Name: `watchdog-builder`
  - Role: Trim PTY-fd U-state narrative in `monitoring/worker_watchdog.py`; keep + re-justify the kill ladder. Update `worker_watchdog` tests.
  - Agent Type: builder
  - Domain: async/subprocess-signals
  - Resume: true

- **Builder (taxonomy-prune)**
  - Name: `taxonomy-builder`
  - Role: Prune `session_stall_classifier.py` + `crash_signature.py` to observed classes; residual liveness-inference trim in `session_health.py`. Update their tests.
  - Agent Type: builder
  - Resume: true

- **Builder (ledger+docs)**
  - Name: `ledger-builder`
  - Role: Author `docs/removed-defenses.md`; update `bridge-worker-architecture.md`, `bridge-self-healing.md`, feature index.
  - Agent Type: documentarian
  - Resume: true

- **Reviewer**
  - Name: `scar-reviewer`
  - Role: Cruft audit (no half-migrations / commented code / PTY archaeology) + correctness on a deletion-heavy diff.
  - Agent Type: code-reviewer
  - Resume: true

### Available Agent Types

Tier 1 builders + `documentarian` + `code-reviewer` (+ `cruft-auditor` for the
deletion diff).

## Step by Step Tasks

### 1. Author the removed-defenses ledger scaffold
- **Task ID**: build-ledger-scaffold
- **Depends On**: none
- **Validates**: `test -f docs/removed-defenses.md`
- **Assigned To**: ledger-builder
- **Agent Type**: documentarian
- **Parallel**: true
- Create `docs/removed-defenses.md` with the entry template (defense / gotcha /
  why-dead-under-headless / Sentry-signature-to-watch) and the PTY-teardown
  baseline entries already deleted by #1930 (from telemetry: pty-pool,
  granite-container, deadman, executor-guard empty-container-message).

### 2. Trim worker_watchdog PTY narrative, keep kill ladder
- **Task ID**: build-watchdog-trim
- **Depends On**: none
- **Validates**: `tests/**/test_worker_watchdog*.py`; `grep -rn "master fd\|uninterruptible sleep" monitoring/` returns nothing
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Domain**: async/subprocess-signals — signal delivery, process groups, killpg
- **Parallel**: true
- Remove the U-state fd-close rationale (docstring 8-21, 231-236) and any
  PTY-master-read escalation branches; keep W1-W3 kill ladder + launchd respawn,
  re-justified for headless. Add the ledger entry (append to `docs/removed-defenses.md`).

### 3. Prune reporting taxonomies + residual liveness inference
- **Task ID**: build-taxonomy-prune
- **Depends On**: none
- **Validates**: `tests/**/test_session_stall_classifier*.py`, `tests/**/test_crash_signature*.py`, isolation-guard tests
- **Assigned To**: taxonomy-builder
- **Agent Type**: builder
- **Parallel**: true
- Prune `session_stall_classifier.py` + `crash_signature.py` to observed classes;
  remove dead threshold constants; trim residual duplicate liveness inference in
  `session_health.py` (subtraction only, no wider spread). Add ledger entries.

### 4. Validate builds (narrow tests)
- **Task ID**: validate-builds
- **Depends On**: build-watchdog-trim, build-taxonomy-prune, build-ledger-scaffold
- **Assigned To**: scar-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Run only the touched test files. Confirm `test_output_router*` passes UNEDITED
  (keep-decision guard). Confirm isolation guard green.

### 5. Documentation cascade
- **Task ID**: document-feature
- **Depends On**: validate-builds
- **Assigned To**: ledger-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Finalize `docs/removed-defenses.md`; update `bridge-worker-architecture.md`,
  `bridge-self-healing.md`, feature index.

### 6. Cruft audit + final review
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: scar-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- No commented-out code, no PTY archaeology in code bodies, no half-migrations.
  Verify all success criteria + the anti-criteria greps.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Ledger exists | `test -f docs/removed-defenses.md && echo ok` | output contains ok |
| No PTY-fd rationale in code | `grep -rn "pty master\|master fd\|uninterruptible sleep" monitoring/ agent/` | match count == 0 |
| Nudge counter kept | `grep -c "MAX_NUDGE_COUNT = 50" agent/output_router.py` | output contains 1 |
| Classifier stays pure (no session_health import) | `grep -c "import session_health\|from agent.session_health" agent/session_stall_classifier.py agent/crash_signature.py` | match count == 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Narrow tests pass | `pytest tests/ -k "worker_watchdog or session_stall_classifier or crash_signature or output_router" -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Bridge nudge loop retirement (control-channel consolidation).** The issue
   lists the 50-cap counter as a cut candidate and wants the steering list as the
   single inbound channel. My evidence-grounded recommendation is to KEEP the
   counter as a runaway backstop in this PR and spin the "is the whole nudge loop
   vestigial under headless?" question into a separate, control-flow-traced
   investigation. **Do you approve keeping it here and filing the vestigiality
   question separately, or do you want the nudge-loop retirement folded into this
   PR?** (If folded in, appetite grows and this becomes a live-control-flow change,
   not just scar-tissue removal.)
2. **Overlap coordination with `resilience-simplification-three-tier.md`.** That
   draft program touches `session_health.py` too. Confirm #1926 should stay the
   narrow PTY-scar slice and leave the three-tier items to the program plan.
