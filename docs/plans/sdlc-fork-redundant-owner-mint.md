---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-20
tracking: https://github.com/tomcounsell/ai/issues/2026
last_comment_id:
---

# SDLC Fork WS-F — Redundant `sdlc-local-{N}` Owner Mint from a Bridge Context

## Problem

When a bridge PM session routes SDLC work to its `dev` subagent and the dev drives the
pipeline, `sdlc-tool session-ensure` mints a **second, unlinked, top-level `sdlc-local-{N}`
AgentSession** for an issue the PM session already owns. Two independent Eng sessions then
race to own one issue — the split-brain that manufactures the very gate/verdict/lease races
the rest of #2026 (WS1–WS-E) exists to survive.

**Current behavior (observed live 2026-07-20):** Tom sent `SDLC 1312` to Eng: Valor. The
bridge minted PM session `tg_valor_-1003449100931_1192` (`session_type=eng`, `issue_url=None`,
`message_text="SDLC 1312"`). 37 seconds later a second session `sdlc-local-1312` appeared:
`parent_agent_session_id=None`, `slug=None`, `working_dir=/Users/valorengels/src/ai` (main
checkout, no worktree isolation). The two are unlinked; the dashboard, scheduler,
kill/cleanup, and telemetry all see two unrelated Eng sessions for issue #1312.

**Root cause (fully traced):** `ensure_session` (`tools/sdlc_session_ensure.py`) already has
an env-var short-circuit meant to adopt a live bridge PM session that owns the issue "without
creating anything" — the #1147 dedup path. `AGENT_SESSION_ID` **is** threaded into the PM's
`claude -p` process (`agent/session_runner/runner.py:410`, the `_session_env` overlay) and
inherited by the dev subagent's Bash subprocesses, so the env session resolves. Adoption
nonetheless fails because the PM session carries **no `issue_url`**: the ownership check
`env_issue_url.endswith("/issues/{n}")` is false, so it falls through to
`find_session_by_issue(n)`. That function's message_text fallback regex is
`\bissue\s*#?\s*{n}\b` — it requires the literal word **"issue"**. `"SDLC 1312"` has no
"issue", so no pass matches and `ensure_session` mints `sdlc-local-1312`.

**Desired outcome:** A bridge PM session that owns an issue is the single run owner. When its
dev subagent enters the pipeline, `session-ensure` **adopts the PM session** (stamps
`issue_url`, acquires the issue lock, binds the `run_id`, writes the supervised-run signal) and
mints nothing. Any later `/do-sdlc` or bare `session-ensure` for the same issue is refused with
`SUPERVISED_RUN_ACTIVE` and inherits the run_id. The dev drives the pipeline through `/sdlc`
(the single-stage router, which mints nothing) and never invokes `/do-sdlc` (the local-only
whole-loop supervisor stand-in).

## Freshness Check

**Baseline commit:** `8c6e93ea7168550738ef0e7b13c680e038547f7e`
**Issue filed at:** 2026-07-11T08:28:33Z (WS-F documented in issue comment 5019074000, 2026-07-20)
**Disposition:** Unchanged — all evidence re-verified live during this planning session.

**File:line references re-verified (live, this session):**
- `tools/sdlc_session_ensure.py:418-477` — env short-circuit + ownership check + fall-through — confirmed present, matches root cause.
- `tools/sdlc_session_ensure.py:335-359` — `_acquire_run_lock_and_bind` writes the supervised-run signal after binding — confirmed.
- `tools/sdlc_session_ensure.py:495` — `local_session_id = f"sdlc-local-{issue_number}"` is the exclusive mint site — confirmed.
- `tools/_sdlc_utils.py:186+` — `find_session_by_issue` message_text regex `\bissue\s*#?\s*{n}\b` requires "issue" — confirmed.
- `agent/session_runner/runner.py:410` — `_session_env` overlay carries `AGENT_SESSION_ID` into the harness process — confirmed.
- `.claude/agents/dev.md:24` — dev told to "invoke `/do-*` skills directly", no exclusion of `/do-sdlc` — confirmed.
- `.claude/skills-global/do-sdlc/SKILL.md:7-9,73` — self-describes as "local stand-in for the bridge PM session"; Step 2 `session-ensure` mints the tracking session — confirmed.
- Live session state: `tg_valor_-1003449100931_1192` (`issue_url=None`) and `sdlc-local-1312` (`parent=None`, main checkout) — confirmed via `valor_session inspect`.

**Cited sibling issues/PRs re-checked:**
- #2076 (WS1–WS5) — merged 2026-07-14; introduced the supervised-run signal + `SUPERVISED_RUN_ACTIVE`. WS-F reuses this machinery, does not redo it.
- #2124 / artifact-grounding guards (WS-A…WS-E) — merged 2026-07-20 (`0fa299066`, `6891ceb5e`). Distinct scope (stage-fork artifact grounding + push-ancestry). WS-F is pipeline-entry ownership; no overlap.
- #1147 — the env short-circuit dedup contract WS-F extends.
- #1671/#1672 — the divergent-env-session reconciliation WS-F must not regress.

**Active plans in `docs/plans/` overlapping this area:** none. Nearest neighbors
(`bridge-worker-liveness-reaction` = #1312, `simulated-bridge-dispatch-harness` = #2159)
touch the bridge intake/queue path, not `sdlc_session_ensure` ownership resolution.

## Prior Art

- **#2076 (merged)** — WS1 single-owner lease: introduced `agent/supervised_run.py`
  (signal write/read/status/clear) and the `SUPERVISED_RUN_ACTIVE` refusal in
  `ensure_session`. Established that fork inheritance is enforced in the tool, keyed on a
  lock-anchored signal. WS-F extends the *producer* side so a bridge-owned run publishes the
  same signal.
- **#2124 / artifact-grounding-guards (merged)** — WS-A…WS-E: stage-fork grounding and the
  push-ancestry merge-bypass guard. Same failure family (fork acting outside the supervisor's
  ownership model), different mechanism (mid-run stage forks vs. pipeline-entry mint).
- **#1147 (merged)** — bridge session dedup: the env short-circuit that returns the live PM
  session "without creating anything." WS-F closes the gap where that short-circuit silently
  falls through to a mint because the PM session never advertised `issue_url`.
- **#1671/#1672 (merged)** — env-vs-issue reconciliation: a forked subagent inheriting a
  parent's `VALOR_SESSION_ID` that points at a *different* issue must NOT adopt it. WS-F's
  adoption branch must preserve this: adopt only an **ownerless** eng session (empty
  `issue_url`), never one that owns a different issue.
- **#1558 (merged)** — the sessionless-local case `sdlc-local-{N}` was built for. Legitimate
  when there is genuinely no owning session; WS-F only suppresses the mint when a live bridge
  owner exists.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete for WS-F |
|-----------|-------------|-------------------------------|
| #1147 env short-circuit | Adopt the live PM env session when it OWNS the issue by `issue_url` | Bridge PM sessions built from raw message text (`"SDLC 1312"`) never get `issue_url` stamped, so the ownership check fails and it falls through to mint. The adoption path exists but its precondition is never satisfied on the bridge. |
| #2076 WS1 signal | Refuse a bare ensure under a LIVE supervised-run signal | The signal is written only *by* `session-ensure` after it mints/binds. On the bridge PM→dev path nothing had written a signal yet (the worker threads its run_id in-process), so the first dev-side `session-ensure` finds no signal and mints fresh. The guard protects intra-run forks, not the first pipeline-entry from an already-owned issue. |

**Root cause pattern:** The owning bridge session does not make its issue ownership *legible*
to `ensure_session` (no `issue_url`, no pre-published signal). Every downstream guard keys on
signals the bridge owner never emits, so the ownerless-looking issue mints a competitor.

## Data Flow

1. **Entry point:** Tom sends `"SDLC 1312"` → bridge mints PM eng session `tg_valor_…1192`
   (`issue_url=None`), injects `AGENT_SESSION_ID` into its `claude -p` env overlay.
2. **PM turn:** PM (per `prime-pm-role.md`) routes to a `dev` subagent — cannot call `/do-*`
   or `/sdlc` itself.
3. **Dev subagent:** drives the pipeline; runs `sdlc-tool session-ensure --issue-number 1312`
   (today via `/do-sdlc` Step 2; post-fix via `/sdlc` Step 1). Bash subprocess inherits
   `AGENT_SESSION_ID=tg_valor_…1192`.
4. **`ensure_session`:** env short-circuit resolves the PM session (live, eng, non-terminal)
   → ownership check `issue_url.endswith("/issues/1312")` **fails** (issue_url None)
   → `find_session_by_issue(1312)`: issue_url pass fails, deterministic-id pass (no
   `sdlc-local-1312` yet), message_text regex `\bissue\s*#?\s*1312\b` misses `"SDLC 1312"`
   → **mints `sdlc-local-1312`** (`tools/sdlc_session_ensure.py:495`).
5. **Output:** two unlinked Eng sessions own issue #1312.

**Post-fix flow at step 4:** the env short-circuit sees an ownerless (`issue_url` empty) live
eng session and **adopts it** — stamps `issue_url=…/issues/1312`, `_acquire_run_lock_and_bind`
acquires the lock + binds `run_id` + writes the supervised-run signal — returns
`{created: false}`. No mint. Any later bare ensure → `SUPERVISED_RUN_ACTIVE` → inherit.

## Architectural Impact

- **New dependencies:** none. Reuses `agent/supervised_run.py`, the issue lock, and the
  existing `_acquire_run_lock_and_bind` bind+signal path.
- **Interface changes:** none to `ensure_session`'s signature or return shape. One new
  internal branch in the env short-circuit; one Popoto field write (`issue_url`) on an
  existing field (no schema change).
- **Coupling:** slightly tightens the (already-existing) coupling between `ensure_session` and
  the bridge session's identity — but in the intended direction (the tool becomes the single
  place that reconciles "a live owner exists" into "do not mint").
- **Data ownership:** the bridge PM eng session becomes the durable run owner for its issue;
  `sdlc-local-{N}` is minted only when no live eng session owns the issue (the #1558 case it
  was built for).
- **Reversibility:** high. The adoption branch is guarded and additive; reverting restores the
  fall-through-to-mint behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the adoption-guard boundary: only ownerless eng sessions)
- Review rounds: 1 (concurrency-sensitive change to the mint decision point)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; assert r.ping()"` | Issue lock + supervised-run signal are Redis-backed |
| Repo `gh` auth | `gh auth status` | Verification steps read issue/session state |

Run via `python scripts/check_prerequisites.py docs/plans/sdlc-fork-redundant-owner-mint.md`.

## Spike Results

### spike-1: Confirm `AGENT_SESSION_ID` reaches the dev-context `session-ensure` Bash subprocess
- **Assumption:** "The dev subagent's `sdlc-tool session-ensure` invocation runs with
  `AGENT_SESSION_ID` set to the PM session id, so the env short-circuit resolves the PM
  session (and only the `issue_url` gap blocks adoption)."
- **Method:** code-read (`agent/session_runner/runner.py` `_session_env` overlay; harness env
  construction in `agent/session_runner/harness/claude.py`).
- **Finding:** `_session_env` (runner.py:409-414) carries `AGENT_SESSION_ID` into the harness
  process env; dev subagents run in-process under the PM's `claude -p` and inherit it for their
  Bash subprocesses. The observed mint with a resolvable env session but `issue_url=None`
  corroborates: resolution succeeded, ownership check failed. **Confidence: high.**
- **Impact on plan:** The structural fix can live entirely inside `ensure_session`'s env
  short-circuit (adopt the ownerless env session). No new env-threading work is required. A
  build-time integration test still asserts the end-to-end env presence (belt-and-suspenders).

## Solution

### Key Elements

- **Ownerless-session adoption (structural, load-bearing)** — in `ensure_session`'s env
  short-circuit, when the resolved env session is a live, non-terminal eng session whose
  `issue_url` is empty/None, adopt it for `issue_number`: stamp `issue_url`, then
  `_acquire_run_lock_and_bind` (acquires the lock, binds `run_id`, writes the supervised-run
  signal), and return `{session_id, created: false, run_id}`. Do **not** fall through to
  `find_session_by_issue`/mint.
- **Adoption guard (preserve #1671/#1672)** — adopt only when `issue_url` is empty. An env
  session that already owns a *different* issue keeps the existing fall-through (do not steal a
  divergent parent's session).
- **Dev drives via `/sdlc`, never `/do-sdlc` (instruction)** — `.claude/agents/dev.md`: the dev
  is itself the supervision loop, so it drives the pipeline through `/sdlc` (single-stage
  router, mints nothing) or the individual stage `/do-*` skills, and must not invoke `/do-sdlc`
  (the local-only PM stand-in).
- **Reciprocal negative note (instruction)** — `.claude/skills-global/do-sdlc/SKILL.md`: if a
  bridge PM/dev context already owns this issue (a live eng session or a live supervised-run
  signal), `/do-sdlc` is redundant — do not run it; drive via `/sdlc`.

### Flow

Dev enters pipeline → `sdlc-tool session-ensure --issue-number N` → env short-circuit resolves
the live PM eng session → **ownerless? adopt it** (stamp `issue_url`, acquire lock, bind
run_id, write signal) → returns `{created:false}` → dev drives stages via `/sdlc` under the
PM session's run_id → any stray `/do-sdlc`/bare ensure → `SUPERVISED_RUN_ACTIVE` → inherit.
No `sdlc-local-{N}` minted; one owner for the issue.

### Technical Approach

- **Adoption branch placement:** in the env-short-circuit block
  (`tools/sdlc_session_ensure.py:435-474`), where an env session is live but does not own the
  issue by URL. Add: if `not env_issue_url` (ownerless), stamp
  `resolved.issue_url = issue_url or built-from-issue_number`, `resolved.save()` (Popoto,
  never raw Redis), then `_acquire_run_lock_and_bind(issue_number, resolved, reuse_run_id=…)`
  and return `created: false`. Keep the existing `find_session_by_issue` fall-through only for
  the divergent-owner case (`env_issue_url` set but pointing elsewhere).
- **issue_url construction:** prefer the `issue_url` argument already passed to
  `ensure_session`; fall back to building `…/issues/{issue_number}` against the resolved repo
  slug if the arg is absent. (The CLI already passes `--issue-url` from `/sdlc`/`/do-sdlc`
  Step 1/2.)
- **Signal correctness:** `_acquire_run_lock_and_bind` already writes the supervised-run signal
  keyed on the bound `run_id` and the session's `working_dir` (lines 335-349). Adoption
  therefore publishes the signal against the PM session automatically — no separate signal call
  needed.
- **Instruction edits:** one negative rule in `dev.md` (drive via `/sdlc`; never `/do-sdlc`),
  one reciprocal note in `do-sdlc/SKILL.md`. Keep them terse; do not enumerate the space of
  wrong skills — state the correct path (`feedback_skills_encourage_do`).
- **Regex broadening is explicitly NOT the fix** (see Rabbit Holes): stamping `issue_url` makes
  `find_session_by_issue`'s issue_url pass authoritative; loosening the message_text regex to
  match bare `"SDLC 1312"` invites version-number false positives.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The env short-circuit is wrapped in `try/except … fall through` (line 475). The new
  adoption branch must not swallow a failed `resolved.save()` or a `RUN_BIND_FAILED` silently:
  a bind failure returns the existing error dict (surfaced to the caller), and a save failure
  logs at debug and falls through to the legacy path (never a silent mint under a half-stamped
  session). Add tests asserting both observable outcomes.
- [ ] `_acquire_run_lock_and_bind` already returns `(None, error_dict)` on lock contention /
  readback mismatch — assert the adoption branch propagates that error verbatim (no mint).

### Empty/Invalid Input Handling
- [ ] `issue_number < 1` → existing early return `{}` (unchanged); add a regression assert.
- [ ] Env session with `issue_url=""` vs `None` vs whitespace → all treated as ownerless
  (adopt). Env session with a *different* `/issues/M` → not adopted (fall through).
- [ ] Env var unset → adoption branch never reached; legacy path unchanged.

### Error State Rendering
- [ ] On `ISSUE_LOCKED` (foreign run owns the lock during adoption), `ensure_session` returns
  the `{"blocked": true, "reason": "ISSUE_LOCKED", …}` shape — assert it surfaces rather than
  minting a competitor.

## Test Impact

- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE: the env-short-circuit tests that
  currently assert fall-through-to-`find_session_by_issue`/mint for a live eng session that
  does not own the issue by URL must split on `issue_url` emptiness: ownerless → adopt
  (`created: false`, `issue_url` now stamped, run_id bound, signal written); divergent owner →
  unchanged fall-through. Add new cases for the adoption branch.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — UPDATE/ADD: end-to-end with
  a real Redis PM eng session (`issue_url=None`) + `AGENT_SESSION_ID` set → assert no
  `sdlc-local-{N}` is created and the PM session holds the lock + signal. Project-scoped test
  session cleanup afterward (`feedback_test_redis_isolation`).
- [ ] `tests/unit/test_sdlc_utils.py` — VERIFY (likely no change): confirm no test asserts that
  `find_session_by_issue` is the reachable path for the ownerless-bridge case (that path is now
  short-circuited upstream by adoption). If such an assertion exists, UPDATE it.
- [ ] Skill/agent doc guards: if a skills-audit test enforces probe/coupling rules on
  `dev.md` / `do-sdlc/SKILL.md`, re-run it after the instruction edits (no new failures).

## Rabbit Holes

- **Broadening `find_session_by_issue`'s message_text regex** to match bare `"SDLC 1312"`,
  `"#1312"`, `"ship 1312"`. Tempting (it's the literal miss), but bare numbers in eng messages
  are ambiguous (versions, PR numbers, counts) → false-positive adoption of the wrong session.
  Stamping `issue_url` is the robust fix; leave the regex alone.
- **Stamping `issue_url` at the bridge/worker layer** by parsing issue numbers out of raw
  message text at session creation. Duplicates the ambiguity problem and spreads issue
  resolution across another component. Adoption inside `ensure_session` (which already receives
  a verified `issue_number`) is the single correct seam.
- **Reaping the 3 stale `sdlc-local-{2052,474,2083}` sessions** (running since 2026-07-17) and
  killing the live `sdlc-local-1312`. Real operational cleanup, but a separate concern from the
  code fix — see No-Gos.
- **Re-architecting `/do-sdlc` and `/sdlc` into one skill.** Out of scope; the two-skill split
  (loop vs single-stage) is deliberate. WS-F only keeps the dev on the correct one.

## Risks

### Risk 1: Adoption steals a PM session that legitimately handles a different issue
**Impact:** A divergent-owner env session gets its `issue_url` overwritten, corrupting #1671/#1672 reconciliation.
**Mitigation:** Adopt only when `issue_url` is empty/None/whitespace. A set-but-different
`issue_url` keeps the existing fall-through. Unit-test both branches explicitly.

### Risk 2: Concurrent dev-context ensures for the same issue race to adopt
**Impact:** Two callers both see an ownerless PM session and both try to bind.
**Mitigation:** `_acquire_run_lock_and_bind` already gates on the `SET NX` issue-lock contest
with post-save readback (lines 300-333); the loser gets `RUN_BIND_FAILED`/`ISSUE_LOCKED`, not
a second owner. Adoption inherits that guard unchanged.

### Risk 3: `resolved.save()` stamps `issue_url` but the subsequent bind fails
**Impact:** PM session left with `issue_url` set but no lock/run_id — a later ensure would then
take the issue_url ownership pass and try to bind (fine), but a half-state is surprising.
**Mitigation:** Stamp `issue_url` only after a successful `_acquire_run_lock_and_bind`, or
treat a stamped-but-unbound session as adoptable on the next call (the issue_url pass leads
back to the same session, which re-binds idempotently). Choose stamp-after-bind; test the
bind-fails-first ordering.

## Race Conditions

### Race 1: Two dev-context `session-ensure` calls adopt the same ownerless PM session
**Location:** `tools/sdlc_session_ensure.py` env short-circuit adoption branch + `_acquire_run_lock_and_bind` (300-359).
**Trigger:** A resumed turn and a fresh turn both call ensure for issue N before either binds.
**Data prerequisite:** The PM session record exists and is resolvable via `AGENT_SESSION_ID`.
**State prerequisite:** The issue lock `session:issuelock:{N}` is free at first contest.
**Mitigation:** The `SET NX` lock contest + post-save readback is the serialization point; the
loser receives an error dict, never a second bind. Adoption adds no new shared-state write
ahead of the lock contest except the idempotent `issue_url` stamp (stamp-after-bind ordering
keeps it single-writer).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2026] Reaping the 3 stale `sdlc-local-{2052,474,2083}` sessions and killing
  the live `sdlc-local-1312` — operational cleanup, tracked under this umbrella issue's live
  triage, not part of the code fix. (Handled as a manual `valor-session kill` pass, project-scoped.)
- [SEPARATE-SLUG #2159] Changes to the bridge intake dispatch decision (steer/resume/new) —
  owned by the simulated-bridge-dispatch-harness plan.

## Update System

No update system changes required — this is a pure code + skill-doc change inside the ai repo.
`dev.md` and `do-sdlc/SKILL.md` propagate through the existing skill-sync wiring (`do-sdlc` is a
`skills-global/` skill already hardlinked by `/update`; `dev.md` is a `.claude/agents/`
definition read in place). No new deps, config, or migration.

## Agent Integration

No new agent-integration surface required — `sdlc-tool session-ensure` is already the entry
point the agent (via `/sdlc`/`/do-sdlc`) invokes through Bash; this changes its internal
resolution logic only. The instruction edits (`dev.md`, `do-sdlc/SKILL.md`) change how the
existing dev subagent drives the pipeline. Integration coverage is the
`test_sdlc_session_ensure_integration.py` end-to-end assertion that a bridge PM eng session is
adopted rather than duplicated.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-issue-keyed-stage-ledger.md` (or the nearest run-identity doc,
  `docs/features/sdlc-run-identity-self-heal.md`) with a short "bridge-owned run adoption"
  subsection: how `ensure_session` adopts an ownerless bridge PM eng session and why no
  `sdlc-local-{N}` is minted in that path.
- [ ] Add/verify an entry in `docs/features/README.md` index if a new subsection warrants it.

### Inline Documentation
- [ ] Comment the adoption branch in `ensure_session` explaining the ownerless-adoption guard
  and its relationship to #1147 (dedup) and #1671/#1672 (divergent-owner protection).
- [ ] The `dev.md` / `do-sdlc/SKILL.md` edits are themselves the instruction documentation.

## Success Criteria

- [ ] A bridge PM eng session with `issue_url=None` + `AGENT_SESSION_ID` set, whose dev context
  runs `session-ensure --issue-number N`, yields **no** `sdlc-local-N` record; the PM session
  is bound as run owner with `issue_url` stamped and the supervised-run signal written.
- [ ] A subsequent bare `session-ensure --issue-number N` returns `SUPERVISED_RUN_ACTIVE`
  carrying the PM session's run_id.
- [ ] An env session that owns a *different* issue is NOT adopted (fall-through preserved;
  #1671/#1672 regression tests green).
- [ ] `.claude/agents/dev.md` instructs driving via `/sdlc` / stage `/do-*` skills and not
  `/do-sdlc`; `do-sdlc/SKILL.md` carries the reciprocal redundant-context note.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (session-ensure adoption)**
  - Name: ensure-builder
  - Role: Implement the ownerless-adoption branch in `ensure_session` + inline docs
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Builder (instruction edits)**
  - Name: skill-doc-builder
  - Role: Edit `dev.md` and `do-sdlc/SKILL.md`; disjoint file set from ensure-builder
  - Agent Type: builder
  - Resume: true

- **Test engineer (adoption coverage)**
  - Name: ensure-tester
  - Role: Unit + integration tests for adoption / divergent-owner / bind-fail / lock-contest
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: ws-f-validator
  - Role: Verify success criteria, no `sdlc-local-N` minted, #1671/#1672 not regressed
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Implement ownerless-adoption branch
- **Task ID**: build-adoption
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_session_ensure.py, tests/integration/test_sdlc_session_ensure_integration.py
- **Informed By**: spike-1 (env threading confirmed; fix lives in ensure_session)
- **Assigned To**: ensure-builder
- **Agent Type**: builder
- **Domain**: async/concurrency, Redis/Popoto data
- **Parallel**: true
- In `tools/sdlc_session_ensure.py` env short-circuit, add the ownerless-adoption branch (stamp `issue_url` after successful `_acquire_run_lock_and_bind`, return `created: false`).
- Preserve the divergent-owner fall-through; use `instance.save()` (never raw Redis).
- Comment the branch with the #1147 / #1671 / #1672 relationship.

### 2. Instruction edits (dev.md + do-sdlc SKILL.md)
- **Task ID**: build-instructions
- **Depends On**: none
- **Assigned To**: skill-doc-builder
- **Agent Type**: builder
- **Parallel**: true
- Add to `.claude/agents/dev.md`: drive the pipeline via `/sdlc` or stage `/do-*` skills; never invoke `/do-sdlc` (local-only PM stand-in).
- Add reciprocal note to `.claude/skills-global/do-sdlc/SKILL.md`: refuse/skip when a live bridge owner or supervised-run signal exists for the issue.

### 3. Tests for adoption + guards
- **Task ID**: build-tests
- **Depends On**: build-adoption
- **Assigned To**: ensure-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: ownerless adopt, divergent-owner fall-through, empty vs None vs whitespace issue_url, bind-fail ordering, ISSUE_LOCKED surfacing.
- Integration: real-Redis PM eng session → no `sdlc-local-N`, lock + signal held; project-scoped cleanup.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-adoption, build-instructions
- **Assigned To**: ws-f-validator (documentarian pass)
- **Agent Type**: documentarian
- **Parallel**: false
- Update the run-identity feature doc with the bridge-owned adoption subsection; refresh the index if needed.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: ws-f-validator
- **Agent Type**: validator
- **Parallel**: false
- Run verification table; confirm no `sdlc-local-N` mint in the bridge path; confirm #1671/#1672 regressions green.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Adoption unit tests pass | `pytest tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Ensure integration tests pass | `pytest tests/integration/test_sdlc_session_ensure_integration.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/sdlc_session_ensure.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/sdlc_session_ensure.py` | exit code 0 |
| dev.md forbids /do-sdlc | `grep -c "do-sdlc" .claude/agents/dev.md` | output > 0 |
| do-sdlc note present | `grep -ci "bridge" .claude/skills-global/do-sdlc/SKILL.md` | output > 0 |
| No raw-Redis write in the change | `grep -nE "\.hset\(\|\.delete\(\|\.srem\(\|\.sadd\(" tools/sdlc_session_ensure.py` | match count == 0 |

## Open Questions

1. **`issue_url` stamp ordering under bind failure** — plan proposes stamp-after-successful-bind
   (Risk 3 mitigation). Confirm this is preferred over stamp-first-then-bind (which leaves a
   findable-but-unbound session that re-binds idempotently next call). Either is safe; stamp-after
   is cleaner. Agree?
2. **Instruction leg necessity** — with adoption in place, a stray `/do-sdlc` no longer mints a
   second session (it adopts). Do we still want the `dev.md` "never `/do-sdlc`" rule, or is
   avoiding the nested-supervisor *semantics* (double loop, `sdlc-local` teardown behaviors)
   reason enough to keep it? Plan keeps both; confirm.
3. **Stale-session cleanup** — reap `sdlc-local-{2052,474,2083}` + live `sdlc-local-1312` now
   (manual, project-scoped kill), or fold into a follow-up? Plan defers as a No-Go.
