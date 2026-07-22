---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2190
last_comment_id:
---

# WS-F duplicate sdlc-local mint recurs: AGENT_SESSION_ID resolver identifier-type mismatch

## Problem

Filing "SDLC N" to the Telegram bridge produces **two** eng `AgentSession` records for one issue instead of one. The live bridge PM session (`tg_valor_<chat>_<msg>`, `is_ledger=False`) is the real owner; ~2 minutes later a duplicate `sdlc-local-<N>` anchor (`is_ledger=True`, no heartbeat) is minted. Two independent eng sessions then race to own one issue — exactly the gate/verdict/lease contention that WS1–WS-E were built to survive, and that WS-F ([PR #2187](https://github.com/tomcounsell/ai/pull/2187), commit `b681c541e`) was meant to close. It recurred **after that fix shipped and was live** (fix merged 07:39 UTC, bridge restarted 08:13 UTC, duplicate minted 08:24 UTC — issue #2065, 2026-07-20).

**Current behavior:** WS-F's ownerless-adopt branch is structurally unreachable for its exact target. The headless runner injects `AGENT_SESSION_ID = session.agent_session_id` (per-run hex) into the `session-ensure` subprocess env. `ensure_session`'s env short-circuit resolves that value via `find_session(session_id=env_value)` → `AgentSession.query.filter(session_id=env_value)`, which queries the `session_id` field. For a bridge PM session `session_id != agent_session_id`, so the lookup returns `None`, the entire ownerless-adopt block (guarded by `if resolved is not None:`) is skipped, `find_session_by_issue` also misses (no `issue_url`, "SDLC 2065" fails the `\bissue\s*#?\s*2065\b` regex), and control reaches the **create** branch → mints `sdlc-local-<N>`.

**Desired outcome:** A live ownerless bridge PM session for the issue is **adopted** (bind `run_id` + write the supervised-run signal + stamp `issue_url`), and **no** second `sdlc-local-<N>` is minted — exactly one eng `AgentSession` per issue. WS-F's existing guards (divergent-owner fall-through, bind-first/stamp-last, ISSUE_LOCKED-returns-error-never-mints) are preserved. A regression test reproduces the env-injected-`agent_session_id` case that WS-F's tests missed.

## Freshness Check

**Baseline commit:** e2e48e763 (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-20T08:58:31Z
**Disposition:** Minor drift (line numbers moved; one new, load-bearing finding surfaced)

**File:line references re-verified:**
- `agent/session_executor.py:1847` (issue) — export `AGENT_SESSION_ID = session.agent_session_id` — **drifted to `agent/session_executor.py:1940`** (`"AGENT_SESSION_ID": session.agent_session_id or ""`). Claim still holds.
- `agent/session_executor.py:1948` (issue) — propagation as `session_env` — **drifted**; the `_harness_env` dict at `:1939-1941` is the injection site. `VALOR_PARENT_SESSION_ID` is set to `session.agent_session_id` at `:1949`.
- `tools/sdlc_session_ensure.py:457` — env short-circuit `resolved = find_session(session_id=env_session_id)` — **still at `:457`**. Env var read at `:452`: `os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")`. Ownerless branch guarded by `if not env_issue_url.strip():` at `:510`.
- `tools/_sdlc_utils.py:398` — `find_session` filters `session_id` — **confirmed** (`AgentSession.query.filter(session_id=session_id)` at `:398`).
- `agent/hooks/pre_tool_use.py:396` — resolves `AGENT_SESSION_ID` via `filter(session_id=…)` — **confirmed** (reads env at `:391`, filters at `:396`).
- `agent/hooks/liveness_writers.py` — same mismatch — **confirmed** (`filter(session_id=session_id)` at `:86`, `:193`, `:229`; env read at `:127`, `:175`, `:213`).

**New load-bearing finding (freshness surfaced, not in the issue):** The executor sets **only** `AGENT_SESSION_ID` — it never sets `VALOR_SESSION_ID`. Yet `sdlc_session_ensure.py:452` reads `VALOR_SESSION_ID` **first** and its own comment at `:449-451` claims "bridge-initiated sessions inject `VALOR_SESSION_ID`". That contract is **unwired** — nothing injects it. This is a latent third seam (see Solution → Seam B2) and materially changes the fix menu.

**Second refinement of the issue's Seam A:** the issue says a dual-match "needs a resolver/scan helper" because `agent_session_id` is not a filterable field. That is over-pessimistic. `agent_session_id` is the Popoto AutoKey primary key (`id`); `AgentSession.get_by_id(x)` resolves it via `filter(id=x)` (`models/agent_session.py:1025-1060`). `tools/valor_session.py::_find_session` (`:655`) is the exact precedent: try `session_id` first, fall back to `get_by_id`. Seam A can reuse `get_by_id` — no scan required.

**Cited sibling issues/PRs re-checked:** #1954 / #2003 / #2012 / #2026 are the race-sensitive ownership lineage; all landed. PR #2187 (`b681c541e`, WS-F) merged and is the fix that recurred. No newer PR has touched the resolver seam.

**Commits on main since issue was filed (touching referenced files):**
- `f719a63d1` "Suppress drafter-fallback re-enqueue on terminal self-draft deferral (#2197)" — touched `session_executor.py` but only the drafter-fallback path; **irrelevant** to the `_harness_env` injection. Accounts for the line-number drift.

**Active plans in `docs/plans/` overlapping this area:** none. (`docs/plans/` has no open session-ensure / WS-F / ownership plan; nearest are the completed WS-series plans under `docs/plans/completed/`.)

**Notes:** Bug reproduced against current main by code-path reading (a full live repro requires the bridge + a real issue). The defect is unambiguously present: the executor injects the hex, the resolver filters by `session_id`, and no test exercises that exact combination (see Test Impact).

## Prior Art

- **PR #2187 (WS-F, `b681c541e`)**: Added the ownerless-adopt branch to `ensure_session` (bind `run_id` + supervised-run signal + stamp `issue_url` on a live ownerless bridge session; divergent-owner fall-through; ISSUE_LOCKED-not-mint). **Outcome: incomplete** — the adopt *logic* is correct but gated behind a lookup (`find_session(session_id=<hex>)`) that can never succeed for its target under the headless runner. This plan closes the gate, not the logic.
- **#2042 (`is_ledger`)**: Marks non-executable ledger anchors so a worker never runs them. The duplicate `sdlc-local-<N>` is `is_ledger=True` — inert to the worker but still a rival owner and dashboard-visible. Relevant: it explains why the duplicate is "inert but harmful."
- **#1671 / #1672**: issue-number read/write convergence in `find_session`. WS-F preserves these; this plan must not regress them (the resolver's Step-2 issue-based path stays intact).
- **#1954 / #2003 / #2012 / #2026**: the race-sensitive ownership lineage (run_id, gates, verdict leases). This bug lives in that territory; a wrong resolver change manufactures duplicate ownership or cross-session contamination.
- No prior *merged* PR attempted the resolver identifier-type fix specifically (`gh pr list --search "duplicate session ensure adopt ownerless"` → empty).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2187 (WS-F) | Added ownerless-adopt logic inside `if resolved is not None:` in `ensure_session` | Correct adoption logic gated behind `find_session(session_id=<env_value>)`. The runner injects `AGENT_SESSION_ID = agent_session_id` (hex), but the resolver filters the `session_id` field. For a bridge PM session the two namespaces differ, so `resolved` is always `None` and the block never runs. |

**Root cause pattern:** The fix and its trigger were validated with **different identifier types than production uses.** WS-F's tests set `VALOR_SESSION_ID` to a `session_id`-shaped value (`tg_valor_…`) and deleted `AGENT_SESSION_ID` (`tests/unit/test_sdlc_session_ensure.py:265,299,335,370,396`). Production injects only `AGENT_SESSION_ID` = hex. The test's env shape made the resolver succeed where production's env shape makes it fail. The bug is an **identifier-type mismatch masked by a test-fixture identifier-type mismatch.**

## Data Flow

1. **Entry point:** Human sends "SDLC N" to the Telegram bridge. Bridge creates/loads a live eng PM `AgentSession` with `session_id = tg_valor_<chat>_<msg>`, `is_ledger=False`, no `issue_url`.
2. **Executor:** `agent/session_executor.py:1939-1941` builds `_harness_env` with `AGENT_SESSION_ID = session.agent_session_id` (per-run hex). `VALOR_SESSION_ID` is **not** set.
3. **Harness subprocess:** the PM's turn runs `sdlc-tool session-ensure` (or a heal path: stage-marker / verdict / dispatch) in a subprocess that inherits `_harness_env`.
4. **`ensure_session`** (`tools/sdlc_session_ensure.py:452`): reads `env_session_id = VALOR_SESSION_ID or AGENT_SESSION_ID` → gets the hex. Calls `find_session(session_id=<hex>)` (`:457`).
5. **`find_session`** (`tools/_sdlc_utils.py:398`): `AgentSession.query.filter(session_id=<hex>)` → **0 matches** (hex is not a `session_id`). Returns `None`.
6. **Fall-through:** ownerless-adopt block skipped (`resolved is None`); `find_session_by_issue(N)` misses (no `issue_url`; "SDLC N" fails the regex); reaches **create** branch.
7. **Output:** a second `AgentSession` `sdlc-local-N` (`is_ledger=True`) is minted. Two rival owners for issue N.

The fix lands at **step 4/5** (resolve the injected id correctly) or **step 2** (inject an id the resolver already understands). The plan must not move it to a later layer (masking a symptom).

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer (race-sensitive ownership code demands a review round)

**Interactions:**
- PM check-ins: 1-2 (the seam decision — see Open Questions — needs sign-off before the fix task starts)
- Review rounds: 1

Medium, not Small: the code change itself is a few lines, but this is race-sensitive SDLC-ownership territory (#1954/#2003/#2012/#2026). The cost is in choosing the seam without regressing WS-F's guards, proving the guards still hold, and covering the secondary hooks mismatch — not in typing.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable (Popoto query backend) | `python -c "from models.agent_session import AgentSession; list(AgentSession.query.filter(session_id='__none__'))"` | `find_session` / `get_by_id` resolution must run |
| `sdlc-tool` resolves | `sdlc-tool --help >/dev/null` | session-ensure entry point present |

## Solution

### Key Elements

- **Resolver identifier-type fix**: make the env-var resolution understand that the injected `AGENT_SESSION_ID` may be an `agent_session_id` (hex), not a `session_id`. The chosen seam (below) is the one open decision.
- **WS-F guard preservation**: whichever seam, the ownerless-adopt path must still be gated by "session is live, ownerless (`is_ledger=False`, no `issue_url`), and not a divergent owner"; bind-first/stamp-last ordering unchanged; ISSUE_LOCKED returns an error and never mints.
- **Regression test**: reproduce the exact production env shape — `AGENT_SESSION_ID` set to a hex `agent_session_id`, `VALOR_SESSION_ID` unset — and assert adoption + zero `sdlc-local-<N>` mint.
- **Secondary-finding decision**: the `pre_tool_use.py` / `liveness_writers.py` hooks resolve `AGENT_SESSION_ID` the same wrong way — fix in the same PR (if the seam makes it cheap) or explicitly defer with a filed issue.

### Flow

Bridge "SDLC N" → live ownerless PM session (`tg_…`, hex `agent_session_id`) → PM turn runs `session-ensure` with `AGENT_SESSION_ID=<hex>` → **resolver resolves the hex to the live PM session** → ownerless-adopt: bind `run_id`, write supervised-run signal, stamp `issue_url` → return that session → **no `sdlc-local-N` minted** → one eng session owns issue N.

### Technical Approach

The root cause is confirmed; the **fix seam is the one open decision** and is deliberately not pre-committed. Three seams, with tradeoffs, for the critique/supervisor to choose:

**Seam A — narrow: dual-match in the resolver.** Change `find_session` (and the `ensure_session` short-circuit that calls it) to resolve the env value by `session_id` **first**, then fall back to `AgentSession.get_by_id(env_value)` (the AutoKey/`id` lookup — the freshness check confirmed no scan helper is needed; this is exactly `tools/valor_session.py::_find_session`'s pattern). Smallest blast radius; changes only resolution, not what any env value *means*. Leaves the hooks' `filter(session_id=…)` mismatch in place unless also patched. **Risk:** `find_session` is called on other paths — the `session_id`-first ordering must be preserved so existing `session_id` callers are unaffected, and the `get_by_id` fallback must still respect the eng-type / liveness filtering `find_session` applies.

**Seam B1 — structural: inject `session_id` into `AGENT_SESSION_ID`.** Change `session_executor.py:1940` to `AGENT_SESSION_ID = session.session_id`. Every consumer (this resolver *and* the `pre_tool_use` / `liveness_writers` hooks) then queries by a value that matches. Fixes the secondary finding for free. **Risk:** changes what *every* `AGENT_SESSION_ID` consumer sees from hex to `session_id`; needs an audit of everything that reads `AGENT_SESSION_ID` expecting the hex (e.g. anything correlating on the per-run UUID). Higher blast radius.

**Seam B2 — structural, minimal (freshness-surfaced): wire the already-expected `VALOR_SESSION_ID`.** Set `VALOR_SESSION_ID = session.session_id` in `_harness_env`, honoring the resolver's existing-but-unwired contract (`sdlc_session_ensure.py:449-452` already prefers `VALOR_SESSION_ID`). `AGENT_SESSION_ID` stays the hex (hooks and per-run correlation untouched). The `ensure_session` resolver then succeeds with **no resolver change at all**. **Risk:** does *not* fix the hooks' secondary mismatch (they read `AGENT_SESSION_ID`, still hex); must pair with an explicit decision on the hooks. Also: verify no other code path assumes `VALOR_SESSION_ID` is absent for bridge sessions.

**Cross-cutting requirement (all seams):** a foreign live session must never be adopted. WS-F's divergent-owner fall-through, bind-first/stamp-last, and ISSUE_LOCKED-not-mint guards are load-bearing and must be re-asserted by tests after the seam lands.

**Recommendation (non-binding, for critique):** Seam A + apply the identical `session_id`-first / `get_by_id`-fallback resolution to the two hooks via a shared helper. This fixes both the primary and secondary findings, changes no env-value *meaning* (lowest semantic blast radius), and reuses a proven pattern. Seam B2 is the smallest diff but leaves the hooks broken. Seam B1 is the most complete but the highest-risk. **The final choice is an Open Question — do not build until it is signed off in critique.**

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `sdlc_session_ensure.py:566` catches env-short-circuit exceptions and logs at debug (`env short-circuit failed`). A test must assert that when the resolver raises (Redis error), the code falls through **without minting** rather than swallowing into the create path — i.e. an infra error must not manufacture a duplicate.
- [ ] `find_session`'s per-branch `except Exception` blocks (`_sdlc_utils.py`) each log at debug; the added `get_by_id` fallback (Seam A) must be wrapped identically so a fallback failure degrades to "not found," not a crash.

### Empty/Invalid Input Handling
- [ ] `AGENT_SESSION_ID` empty string / whitespace must NOT short-circuit (existing `test_empty_env_var_does_not_short_circuit` covers `VALOR_SESSION_ID=""`; add the `AGENT_SESSION_ID=""` twin).
- [ ] A hex value that matches **no** session (stale env) must fall through to the issue path and ultimately create — assert this still holds (don't over-adopt).

### Error State Rendering
- [ ] Not user-visible. `session-ensure` emits JSON to stdout; assert the adopt path emits the adopted session's id and the create path is not taken. If `bind` fails, `ensure_session` must return an error (never a silently-minted session) — assert the ISSUE_LOCKED / bind-fail contract.

## Test Impact

- [ ] `tests/unit/test_sdlc_session_ensure.py::TestEnvShortCircuit::test_short_circuit_returns_env_session_when_live_eng` — UPDATE: this fixture sets `VALOR_SESSION_ID` to a `session_id`. Add a sibling that sets **`AGENT_SESSION_ID` to a hex `agent_session_id` with `VALOR_SESSION_ID` unset** — the exact production shape. This is the regression test the acceptance criteria demand.
- [ ] `tests/unit/test_sdlc_session_ensure.py::TestEnvShortCircuit::test_short_circuit_falls_through_when_env_session_missing` — UPDATE (or add twin): assert a stale **hex** env value falls through, not just a stale `session_id`.
- [ ] `tests/unit/test_sdlc_session_ensure.py::TestEnvShortCircuit::test_empty_env_var_does_not_short_circuit` — UPDATE: add `AGENT_SESSION_ID=""` twin.
- [ ] `tests/unit/test_sdlc_session_ensure.py::TestEnvShortCircuit::test_non_owning_env_session_prefers_existing_issue_session` and `test_non_owning_env_session_creates_when_no_issue_session` — UPDATE: re-assert the divergent-owner guards hold when the env value is a **hex** (Seam A/B1) so WS-F's fall-through is proven under the new resolution.
- [ ] `tests/unit/test_sdlc_session_ensure_integration.py` — UPDATE if Seam A changes `find_session` signature/behavior; add an integration case exercising the hex-injected adopt end-to-end.
- [ ] If Seam B1/B2: add an assertion in a `session_executor` test (or new `tests/unit/test_session_executor_*`) that `_harness_env` carries the expected identifier (`session_id`) so the injection contract is pinned.
- [ ] If the hooks are fixed (Seam A shared helper / B1): add/UPDATE tests for `pre_tool_use._resolve_session()` and `liveness_writers` resolving a hex env value.

No existing test currently exercises the `AGENT_SESSION_ID`-only-with-hex path — that absence is the coverage gap this bug exploited.

## Rabbit Holes

- **Rewriting the whole env-identifier scheme.** Do not attempt to unify `session_id` / `agent_session_id` / `VALOR_SESSION_ID` / `AGENT_SESSION_ID` / `VALOR_PARENT_SESSION_ID` into one canonical env var. That is a separate architectural project; this fix targets one resolution mismatch.
- **Auditing every `AGENT_SESSION_ID` reader (only relevant to Seam B1).** If Seam B1 is chosen, the audit is in scope but bounded to consumers that depend on the value being the per-run hex; do not turn it into a general env-var census.
- **The "SDLC N" issue-number regex.** Tempting to also make `find_session_by_issue` match "SDLC N" text. That is a different miss on a different path and would mask, not fix, the resolver bug. Leave it out of scope.
- **Live bridge repro harness.** A full end-to-end bridge repro is expensive; the unit regression (production env shape) plus the existing integration test is sufficient proof. Do not build a bridge simulator for this.

## Risks

### Risk 1: Over-adoption — a foreign live session gets adopted
**Impact:** Cross-session contamination; two issues share one owner. Worse than the duplicate this fixes.
**Mitigation:** Preserve WS-F's divergent-owner fall-through and ownerless gate (`is_ledger=False` + no `issue_url` + issue-match). Add tests that a session already owning a *different* issue is NOT adopted when its hex is injected.

### Risk 2: Seam A changes `find_session` behavior for unrelated callers
**Impact:** Other `find_session` call sites (recovery, heal paths) could resolve differently.
**Mitigation:** `session_id`-first ordering is preserved (identical to `_find_session`); `get_by_id` fires only on a `session_id` miss. Enumerate `find_session` callers and assert unchanged behavior for `session_id` inputs.

### Risk 3: Seam B1 changes the meaning of `AGENT_SESSION_ID` system-wide
**Impact:** Any consumer correlating on the per-run hex (telemetry, hooks expecting UUID) breaks.
**Mitigation:** If B1 is chosen, audit `grep -rn AGENT_SESSION_ID` consumers; only proceed if none depend on the hex value. Otherwise prefer Seam A or B2.

### Risk 4: Secondary hooks mismatch left unfixed silently degrades liveness/budget hooks
**Impact:** `pre_tool_use` budget backstop and `liveness_writers` silently no-op for bridge sessions (best-effort, fail-silent — already the status quo, but the fix is an opportunity).
**Mitigation:** Make an explicit, recorded decision (fix via shared helper, or defer via a filed issue). Acceptance criteria require this decision to be documented.

## Race Conditions

### Race 1: Two subprocesses call `session-ensure` for the same issue near-simultaneously
**Location:** `tools/sdlc_session_ensure.py` ownerless-adopt/bind path
**Trigger:** The PM turn and a heal path (stage-marker/verdict/dispatch) both cold-invoke `session-ensure` before either binds `run_id`.
**Data prerequisite:** The live PM session must be resolvable (the fix) *and* the `run_id` bind must be atomic/idempotent so only one binder wins.
**State prerequisite:** WS-F's bind-first/stamp-last ordering — bind `run_id` before stamping `issue_url`, so a loser sees ISSUE_LOCKED and returns an error rather than minting.
**Mitigation:** Do not alter the bind/lock mechanism; the fix only makes the session *resolvable*. Assert (test) that a second concurrent ensure with the same hex returns the already-bound session or an ISSUE_LOCKED error, never a mint.

### Race 2: `rebuild_indexes()` transiently empties the class set during resolution
**Location:** `find_session` / `get_by_id` query path
**Trigger:** A concurrent Popoto `rebuild_indexes()` empties `$Class:AgentSession`, making `query.filter` return empty for a live session (issue #1720).
**Data prerequisite:** The session exists but is transiently invisible.
**State prerequisite:** A retry window that outlasts the rebuild.
**Mitigation:** `_find_session` already implements a bounded retry for this (`_CLASS_SET_RETRY_ATTEMPTS`). If Seam A adds a `get_by_id` fallback, consider whether the same transient applies to the `id`-keyed lookup; at minimum do not introduce a new unguarded single-shot filter on the hot path. Flag for critique.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] The general env-identifier unification (single canonical session env var) is not attempted here. *(If deferred rather than done, file an issue and convert this to `[SEPARATE-SLUG #NNN]` before build — otherwise the hooks decision below must be resolved in-PR.)*
- The "SDLC N" free-text → issue-number matching in `find_session_by_issue` is out of scope — it is a different path and fixing it would mask the resolver bug.

Note: the secondary hooks mismatch is **not** a No-Go by default — the acceptance criteria require an explicit in-PR decision (fix or file-and-defer). It is only deferred if a tracking issue is filed.

## Update System

No update system changes required — this is a purely internal resolver/env-injection fix. No new dependencies, config files, or migration steps. The change ships with the normal `/update` code sync; no `scripts/update/` or `/update` skill edits needed.

## Agent Integration

No new agent integration required — this is a bridge/worker-internal change to session resolution. The `sdlc-tool session-ensure` entry point and the headless runner already exist; this fix corrects how they resolve an injected identifier. Integration coverage is via `tests/unit/test_sdlc_session_ensure_integration.py` (the agent path that invokes `session-ensure` is exercised there), not a new tool surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/` WS-F / session-ownership doc (or the session-ensure feature doc) to record the identifier-type contract: which env var carries which identifier, and which field the resolver queries. If no single doc owns this, add a short section to the bridge-worker or session-lifecycle doc.
- [ ] Add a one-line note to the WS-F / duplicate-ownership history: "recurred via AGENT_SESSION_ID identifier-type mismatch, fixed in #2190."

### Inline Documentation
- [ ] If Seam B2 wires `VALOR_SESSION_ID`, update the now-accurate comment at `sdlc_session_ensure.py:449-451` (currently claims injection that didn't exist).
- [ ] Docstring on the resolver change (Seam A) documenting the `session_id`-first / `get_by_id`-fallback contract, mirroring `_find_session`.

## Success Criteria

- [ ] A cold `session-ensure` invoked from a bridge PM session's subprocess (env: `AGENT_SESSION_ID=<hex>`, `VALOR_SESSION_ID` unset) **adopts** the ownerless session and mints **no** `sdlc-local-<N>`.
- [ ] Filing "SDLC N" to the bridge yields exactly **one** eng `AgentSession` for issue N (validated by the regression test at the unit level; the integration test exercises the ensure path).
- [ ] WS-F's protections still hold: divergent-owner not adopted; bind-fail/ISSUE_LOCKED returns an error and never mints (#1671/#1672 convergence intact) — asserted by tests using the **hex** env shape.
- [ ] The regression test reproduces the env-injected-`agent_session_id` case (the gap WS-F's tests missed by passing `session_id`).
- [ ] A decision on the hooks' secondary mismatch (`pre_tool_use.py`, `liveness_writers.py`) is recorded: fixed in this PR, or deferred with a filed issue number.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms the resolver no longer filters *only* by `session_id` for the injected env id (Seam A), or the executor injects `session_id` into the resolved env var (Seam B) — whichever seam is chosen.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (resolver-fix)**
  - Name: resolver-builder
  - Role: Implement the chosen seam + the regression test; preserve WS-F guards
  - Agent Type: builder
  - Domain: async/concurrency + Redis/Popoto
  - Resume: true

- **Reviewer (ownership-safety)**
  - Name: ownership-reviewer
  - Role: Review for over-adoption / cross-session contamination; verify WS-F guards intact
  - Agent Type: code-reviewer
  - Resume: true

- **Validator (regression)**
  - Name: regression-validator
  - Role: Verify the regression test reproduces the bug (red before fix, green after) and all guards hold
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Update the identifier-type contract docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Write the failing regression test (seam-independent)
- **Task ID**: build-regression-test
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_session_ensure.py` (new case), must FAIL against current main (red-state proof)
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto — paste the Popoto/data rules from `DOMAIN_FRAMING.md`
- **Parallel**: false
- Add a test: live ownerless eng PM session (`session_id=tg_valor_…`, distinct hex `agent_session_id`, `is_ledger=False`, no `issue_url`); env has `AGENT_SESSION_ID=<hex>`, `VALOR_SESSION_ID` unset.
- Assert: `ensure_session` **adopts** that session (returns it, binds run_id, stamps issue_url) and does NOT create `sdlc-local-<N>`.
- Confirm it FAILS on current main; paste the failure into the PR as red-state proof.

### 2. Implement the chosen seam
- **Task ID**: build-seam-fix
- **Depends On**: build-regression-test, **AND seam decision signed off (Open Question 1)**
- **Validates**: the regression test now passes; `tests/unit/test_sdlc_session_ensure.py`, `tests/unit/test_sdlc_session_ensure_integration.py`
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Domain**: async/concurrency + Redis/Popoto
- **Parallel**: false
- Implement the seam selected in critique (A: resolver dual-match via `get_by_id` fallback; B1: inject `session_id` into `AGENT_SESSION_ID`; B2: wire `VALOR_SESSION_ID=session_id`).
- Preserve WS-F guards; do not touch the bind/lock mechanism.
- Update the divergent-owner / fall-through tests to use the hex env shape.

### 3. Resolve the secondary hooks mismatch (decision-gated)
- **Task ID**: build-hooks-decision
- **Depends On**: build-seam-fix
- **Validates**: `agent/hooks/pre_tool_use.py`, `agent/hooks/liveness_writers.py` (if fixed) + their tests; OR a filed issue link
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- If Seam A/B1 makes it cheap, apply the same dual resolution to the hooks (shared helper) and add tests.
- If deferring, file an issue and record the number in the plan No-Gos + Success Criteria.

### 4. Ownership-safety review
- **Task ID**: review-ownership
- **Depends On**: build-seam-fix, build-hooks-decision
- **Assigned To**: ownership-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify no over-adoption path; divergent-owner guard intact; bind-fail never mints; `find_session` unchanged for `session_id` callers.

### 5. Regression validation
- **Task ID**: validate-regression
- **Depends On**: review-ownership
- **Assigned To**: regression-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the regression test is red-before / green-after; run the full ensure test module; verify guard tests use the hex shape.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-regression
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Record the identifier-type contract; fix the stale `VALOR_SESSION_ID` comment if Seam B2; note the recurrence+fix in WS-F history.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: regression-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; confirm every Success Criterion including the hooks decision record.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_session_ensure.py tests/unit/test_sdlc_session_ensure_integration.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Regression case exists | `grep -rn "AGENT_SESSION_ID" tests/unit/test_sdlc_session_ensure.py` | output contains AGENT_SESSION_ID |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_sdlc_session_ensure.py \| grep -v '# open bug'` | exit code 1 |
| Anti-criterion: resolver not filtering only by session_id for env id (Seam A) | `grep -n "get_by_id\|session_id" tools/_sdlc_utils.py \| grep -c get_by_id` | output > 0 |

*(The last row is seam-conditional — replace with the seam-appropriate check once the seam is chosen; e.g. for Seam B2, assert `grep -n VALOR_SESSION_ID agent/session_executor.py` is non-empty.)*

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Seam selection (blocking build).** Seam A (resolver dual-match via `get_by_id` fallback + shared helper for hooks), Seam B1 (inject `session_id` into `AGENT_SESSION_ID` — highest completeness, highest blast radius), or Seam B2 (wire `VALOR_SESSION_ID=session_id` — smallest diff, leaves hooks broken)? Recommendation: **Seam A + shared hooks helper** (lowest semantic blast radius, fixes both findings). Needs sign-off before build-seam-fix starts.
2. **Hooks secondary mismatch:** fix in this PR (per the recommendation) or defer with a filed issue? If deferred, we must file it and tag the No-Go `[SEPARATE-SLUG #NNN]`.
3. **Class-set retry on the `get_by_id` fallback (Seam A):** should the hex `id` lookup carry the same #1720 bounded-retry as `_find_session`, or is the `id`-keyed path not subject to the `$Class` transient? (Popoto/critique call.)
