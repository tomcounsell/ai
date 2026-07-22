---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2190
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-22T05:29:13Z
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
- PM check-ins: 0-1 (seam is committed — B2; no pre-build decision gate remains)
- Review rounds: 1

Medium, not Small: the code change is a single line, but this is race-sensitive SDLC-ownership territory (#1954/#2003/#2012/#2026). The cost is in proving WS-F's guards still hold under the newly-resolvable env shape and pinning the regression test — not in typing.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable (Popoto query backend) | `python -c "from models.agent_session import AgentSession; list(AgentSession.query.filter(session_id='__none__'))"` | `find_session` / `get_by_id` resolution must run |
| `sdlc-tool` resolves | `sdlc-tool --help >/dev/null` | session-ensure entry point present |

## Solution

### Key Elements

- **Identifier-injection fix (Seam B2, committed)**: inject `VALOR_SESSION_ID = session.session_id` into `_harness_env` so the resolver's existing `VALOR_SESSION_ID`-first read resolves the live PM session. No resolver code changes.
- **WS-F guard preservation**: the ownerless-adopt path is reached unchanged — still gated by "session is live, ownerless (`is_ledger=False`, no `issue_url`), and not a divergent owner"; bind-first/stamp-last ordering unchanged; ISSUE_LOCKED returns an error and never mints. Tests must re-assert these fire under the new (now-resolvable) env shape.
- **Regression test**: reproduce the exact production env shape — `AGENT_SESSION_ID` set to a hex `agent_session_id` **and `VALOR_SESSION_ID` set to the session's `session_id`** (the shape B2 produces) — and assert adoption + zero `sdlc-local-<N>` mint. Also keep a red-state proof: the *pre-fix* shape (`AGENT_SESSION_ID` hex, `VALOR_SESSION_ID` unset) must mint before the injection lands.
- **Secondary-finding decision (recorded)**: the `pre_tool_use.py` / `liveness_writers.py` hooks resolve `AGENT_SESSION_ID` (hex) via `filter(session_id=…)` and continue to miss for bridge sessions. B2 does not touch them — **DEFER via a filed follow-up issue** (best-effort fail-silent status quo; separate blast radius).

### Flow

Bridge "SDLC N" → live ownerless PM session (`tg_…`, `session_id=tg_valor_…`, hex `agent_session_id`) → PM turn runs `session-ensure` with `VALOR_SESSION_ID=tg_valor_…` (B2) + `AGENT_SESSION_ID=<hex>` → **resolver's `VALOR_SESSION_ID`-first read resolves the live PM session via `find_session(session_id=tg_valor_…)`** → ownerless-adopt (unchanged WS-F block): bind `run_id`, write supervised-run signal, stamp `issue_url` → return that session → **no `sdlc-local-N` minted** → one eng session owns issue N.

### Technical Approach

**COMMITTED SEAM: B2 — wire the already-expected `VALOR_SESSION_ID`.** The critique resolved the seam decision (OQ1). The fix is a **one-line addition** to `_harness_env` in `agent/session_executor.py:1938-1941`:

```python
_harness_env: dict[str, str] = {
    "AGENT_SESSION_ID": session.agent_session_id or "",
    "VALOR_SESSION_ID": session.session_id or "",   # <-- add this line
    "CLAUDE_CODE_TASK_LIST_ID": task_list_id or "",
}
```

**Why B2 is correct and complete for this bug:**

1. **It honors a pre-existing, load-bearing contract.** `tools/sdlc_session_ensure.py:449-452` already reads `os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")` — `VALOR_SESSION_ID` **first** — and its comment ("bridge-initiated sessions inject `VALOR_SESSION_ID`") documents a contract that nothing ever wired. B2 wires it. Once `VALOR_SESSION_ID = session.session_id` is present, `find_session(session_id=env_session_id)` at `:457` matches the live PM session on its first try. `AGENT_SESSION_ID` stays the per-run hex, so the resolver's `AGENT_SESSION_ID` fallback (for genuinely `agent_session_id`-shaped values) is untouched.

2. **No resolver change at all.** The entire WS-F ownerless-adopt block, divergent-owner fall-through, bind-first/stamp-last ordering, and ISSUE_LOCKED-not-mint guard are reached *unchanged* — they simply now execute for their real target because `resolved` is no longer `None`. `find_session` and `_sdlc_utils.py` are not modified.

3. **It dissolves three of the five critique concerns:**
   - The `get_by_id` eng-gate concern — moot; no `get_by_id` fallback is added (that was Seam A).
   - The Step-3 second-site concern — moot; the resolution is a single injection, not a dual-match spread across call sites.
   - The `rebuild_indexes()` transient (Race 2 / #1720) — moot; no new query path is introduced on the hot path. The existing `_find_session` bounded retry (`_CLASS_SET_RETRY_ATTEMPTS`) still guards the single `find_session(session_id=…)` call, exactly as it does today.

**Rejected alternatives (recorded for provenance):**

- **Seam A (resolver dual-match via `get_by_id` fallback).** Changes `find_session` resolution semantics for *all* callers (recovery, heal paths) and requires the eng-type/liveness filtering to be replicated on the `id`-keyed fallback — higher semantic blast radius than a one-line injection that reuses the resolver as-is. Rejected: B2 achieves the same adoption with zero resolver change.
- **Seam B1 (inject `session_id` into `AGENT_SESSION_ID`).** Would change what *every* `AGENT_SESSION_ID` consumer sees from hex to `session_id`, requiring a full audit of per-run-hex correlators (telemetry, hooks). Rejected: unnecessarily broad; B2 leaves `AGENT_SESSION_ID`'s meaning intact.

**Secondary hooks mismatch — decision (OQ2, resolved):** B2 does **not** touch `agent/hooks/pre_tool_use.py` or `agent/hooks/liveness_writers.py`; they continue to read `AGENT_SESSION_ID` (still the hex) and resolve it via `filter(session_id=…)`, which misses for bridge PM sessions exactly as before. This is **pre-existing, best-effort, fail-silent** behavior (budget backstop / liveness stamps no-op for bridge sessions) — B2 neither fixes nor worsens it. **Decision: DEFER via a filed follow-up issue** (filed during build, number recorded in No-Gos + Success Criteria). Fixing the hooks is a separate identifier-type change with its own blast radius and is explicitly out of scope for this minimal duplicate-mint fix.

**Cross-cutting requirement:** a foreign live session must never be adopted. WS-F's divergent-owner fall-through, bind-first/stamp-last, and ISSUE_LOCKED-not-mint guards are load-bearing and must be re-asserted by tests after B2 lands (they are reached unchanged, but the regression suite must prove they still fire under the newly-resolvable `VALOR_SESSION_ID` env shape).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `sdlc_session_ensure.py:566` catches env-short-circuit exceptions and logs at debug (`env short-circuit failed`), then **falls through to `find_session_by_issue` → create**. This is pre-existing WS-F control flow and B2 does not change it. **Criterion (corrected — the earlier "no-mint-on-infra-error" claim contradicted this control flow):** a test asserts that when the resolver raises (e.g. a Redis error during `find_session`), the code **logs at debug and does not crash** — it degrades to the legacy issue-lookup/create path. We do **not** claim "never mints" for the infra-error case: with the env session unresolvable due to an exception, and no issue-scoped session found, the create path (and hence a `sdlc-local-<N>` mint) is the intended fallback, not a defect. Making an env-resolution exception return an error dict (never mint) is a resolver behavioral change **out of scope** for this minimal identifier-injection fix. The duplicate-mint defect this plan fixes is the *silent-miss* path (resolver returns `None`, no exception), which B2 closes by making the session resolvable — not the exception path.
- [ ] B2 adds no new query branch to `find_session`; its existing per-branch `except Exception` blocks (`_sdlc_utils.py`) are unchanged. No new fallback wrapping is required.

### Empty/Invalid Input Handling
- [ ] `VALOR_SESSION_ID` / `AGENT_SESSION_ID` empty string / whitespace must NOT short-circuit (existing `test_empty_env_var_does_not_short_circuit` covers `VALOR_SESSION_ID=""`; B2 sets `VALOR_SESSION_ID = session.session_id or ""`, so a session with a falsy `session_id` yields `""` — assert the empty case still falls through, not adopts).
- [ ] A `VALOR_SESSION_ID` value that matches **no** session (stale env) must fall through to the issue path and ultimately create — assert this still holds (don't over-adopt).

### Error State Rendering
- [ ] Not user-visible. `session-ensure` emits JSON to stdout; assert the adopt path emits the adopted session's id and the create path is not taken. If `bind` fails, `ensure_session` must return an error (never a silently-minted session) — assert the ISSUE_LOCKED / bind-fail contract.

## Test Impact

- [ ] `tests/unit/test_sdlc_session_ensure.py::TestEnvShortCircuit::test_short_circuit_returns_env_session_when_live_eng` — UPDATE: add a sibling that sets **`VALOR_SESSION_ID` to the live PM session's `session_id` and `AGENT_SESSION_ID` to the distinct hex `agent_session_id`** — the exact shape B2 produces — and asserts adoption. This is the regression test the acceptance criteria demand.
- [ ] Red-state proof: a test at the *pre-fix* env shape (`AGENT_SESSION_ID` hex, `VALOR_SESSION_ID` unset) that shows the resolver misses and the create/mint path is taken. It documents the bug; after B2 injects `VALOR_SESSION_ID` the production path no longer reaches that shape.
- [ ] `tests/unit/test_sdlc_session_ensure.py::TestEnvShortCircuit::test_short_circuit_falls_through_when_env_session_missing` — UPDATE (or add twin): assert a stale **`VALOR_SESSION_ID`** value falls through, not just a stale `AGENT_SESSION_ID`.
- [ ] `tests/unit/test_sdlc_session_ensure.py::TestEnvShortCircuit::test_empty_env_var_does_not_short_circuit` — UPDATE: assert `VALOR_SESSION_ID=""` (the `session.session_id or ""` empty case B2 can produce) does not short-circuit.
- [ ] `tests/unit/test_sdlc_session_ensure.py::TestEnvShortCircuit::test_non_owning_env_session_prefers_existing_issue_session` and `test_non_owning_env_session_creates_when_no_issue_session` — UPDATE: re-assert the divergent-owner guards hold when the resolvable env value arrives via `VALOR_SESSION_ID` so WS-F's fall-through is proven under B2's env shape.
- [ ] `tests/unit/test_sdlc_session_ensure.py` — ADD a **behavioral-equivalence** case over the self-owned eng population (Risk 4): (a) a live self-owned eng session that already owns issue N resolves via `VALOR_SESSION_ID` short-circuit and returns the *same* session with no re-bind/re-stamp/mint (outcome identical to the pre-B2 issue-based path); (b) a **terminal-status** (completed/killed/failed) self-owned session for issue N is NOT adopted/resurrected via the `VALOR_SESSION_ID` short-circuit — it falls through to WS-F's liveness/ownership guard exactly as before B2.
- [ ] `tests/unit/test_sdlc_session_ensure.py` — ADD a **namespace-disjointness fixture assertion** (Risk 5): assert the test's `session_id` (`tg_valor_…` / `sdlc-local-…`) and `agent_session_id` (32-hex) values cannot collide, so a `VALOR_SESSION_ID`-first resolve can never match a session by the *wrong* identifier. This pins the load-bearing invariant B2 relies on.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — add an integration case exercising the B2-injected adopt end-to-end (`VALOR_SESSION_ID`=session_id resolves the live PM session, no mint). No `find_session` signature change under B2.
- [ ] Add an assertion in a `session_executor` test (or new `tests/unit/test_session_executor_*`) that `_harness_env` carries `VALOR_SESSION_ID == session.session_id` so the B2 injection contract is pinned.
- [ ] Hooks tests: **not in scope** under B2 (hooks unchanged; deferred via filed follow-up issue).

No existing test exercises the shape where the resolver misses because `VALOR_SESSION_ID` was never injected — that absence is the coverage gap this bug exploited.

## Rabbit Holes

- **Rewriting the whole env-identifier scheme.** Do not attempt to unify `session_id` / `agent_session_id` / `VALOR_SESSION_ID` / `AGENT_SESSION_ID` / `VALOR_PARENT_SESSION_ID` into one canonical env var. That is a separate architectural project; this fix targets one resolution mismatch.
- **Auditing every `AGENT_SESSION_ID` reader.** Not needed under B2 — `AGENT_SESSION_ID` keeps its per-run-hex meaning; only the new, additive `VALOR_SESSION_ID` is introduced. Do not launch an env-var census.
- **The "SDLC N" issue-number regex.** Tempting to also make `find_session_by_issue` match "SDLC N" text. That is a different miss on a different path and would mask, not fix, the resolver bug. Leave it out of scope.
- **Live bridge repro harness.** A full end-to-end bridge repro is expensive; the unit regression (production env shape) plus the existing integration test is sufficient proof. Do not build a bridge simulator for this.

## Risks

### Risk 1: Over-adoption — a foreign live session gets adopted
**Impact:** Cross-session contamination; two issues share one owner. Worse than the duplicate this fixes.
**Mitigation:** B2 reaches WS-F's ownerless-adopt block unchanged. Its divergent-owner fall-through and ownerless gate (`is_ledger=False` + no `issue_url` + issue-match) are load-bearing. Add tests that a session already owning a *different* issue is NOT adopted when its `session_id` arrives via `VALOR_SESSION_ID`.

### Risk 2: `VALOR_SESSION_ID` injection has an unexpected consumer
**Impact:** Some other code path might assume `VALOR_SESSION_ID` is absent for bridge sessions and branch on that.
**Mitigation:** `grep -rn VALOR_SESSION_ID` to enumerate readers before build. The resolver already *expects* it (reads it first) — the contract was documented and simply unwired. Confirm no reader treats presence-of-`VALOR_SESSION_ID` as a "not a bridge session" signal. (Low risk: `AGENT_SESSION_ID` — the value most consumers key on — is untouched.)

### Risk 3: Secondary hooks mismatch left unfixed silently degrades liveness/budget hooks
**Impact:** `pre_tool_use` budget backstop and `liveness_writers` silently no-op for bridge sessions (best-effort, fail-silent — already the status quo; B2 neither fixes nor worsens it).
**Mitigation:** Decision recorded (Technical Approach → OQ2): **DEFER via a filed follow-up issue**. Acceptance criteria require the issue number to be recorded in No-Gos + Success Criteria.

### Risk 4: Blast radius is every self-invoked eng/teammate session-ensure, not just bridge PM adoption
**Impact:** `_harness_env` is built for *every* headless session turn (eng and teammate), so B2 injects `VALOR_SESSION_ID = session.session_id` for the entire self-invoked population — not only the ownerless bridge-PM case this bug targets. For a self-owned eng session that **already owns its issue**, the env short-circuit now resolves the session *directly* (via `find_session(session_id=…)`) instead of arriving through `find_session_by_issue`. The two paths must reach the **same** outcome (return the same already-owning session; no re-bind, no re-stamp, no duplicate). The dangerous sub-case is a **terminal-status** self-owned session (completed / killed / failed): B2 must not let a stale `session_id` in the inherited env resurrect or wrongly adopt a terminal session.
**Mitigation:** Add a **behavioral-equivalence test** over the self-owned eng population: (a) a live self-owned eng session that already owns issue N — assert env-short-circuit returns the *same* session and does not re-bind/re-stamp or mint; (b) the **terminal-status** case — a completed/killed self-owned session for issue N — assert the resolver does not adopt/resurrect it (it falls through to the WS-F liveness/ownership guard exactly as before B2). The outcome under B2's `VALOR_SESSION_ID` env shape must be identical to the pre-B2 issue-based path for these self-owned cases.

### Risk 5: `session_id` / `agent_session_id` namespace-disjointness is now load-bearing
**Impact:** B2 resolves via `VALOR_SESSION_ID`-first → `find_session(session_id=<session_id>)`. This is only safe because a `session_id` value (`tg_valor_<chat>_<msg>`, `sdlc-local-<N>`, `local-…`) can **never** collide with an `agent_session_id` value (a 32-char Popoto AutoKey hex). If the two namespaces could overlap, a `VALOR_SESSION_ID` short-circuit could resolve the *wrong* session, or the `AGENT_SESSION_ID` fallback could match a session by the wrong field. Today this disjointness holds by construction but is **unstated and untested** — a future `session_id` scheme that emitted bare hex would silently break the resolver.
**Mitigation:** Pin the invariant with a fixture assertion (Test Impact) that the fixtures' `session_id` and `agent_session_id` shapes are disjoint, and add a one-line note to the identifier-type contract doc (Documentation) stating that `session_id` prefixes and the hex `agent_session_id` namespace must remain disjoint for `VALOR_SESSION_ID`-first resolution to be sound.

## Race Conditions

### Race 1: Two subprocesses call `session-ensure` for the same issue near-simultaneously
**Location:** `tools/sdlc_session_ensure.py` ownerless-adopt/bind path
**Trigger:** The PM turn and a heal path (stage-marker/verdict/dispatch) both cold-invoke `session-ensure` before either binds `run_id`.
**Data prerequisite:** The live PM session must be resolvable (the fix) *and* the `run_id` bind must be atomic/idempotent so only one binder wins.
**State prerequisite:** WS-F's bind-first/stamp-last ordering — bind `run_id` before stamping `issue_url`, so a loser sees ISSUE_LOCKED and returns an error rather than minting.
**Mitigation:** Do not alter the bind/lock mechanism; the fix only makes the session *resolvable*. Assert (test) that a second concurrent ensure with the same hex returns the already-bound session or an ISSUE_LOCKED error, never a mint.

### Race 2: `rebuild_indexes()` transiently empties the class set during resolution
**Location:** `find_session` query path
**Trigger:** A concurrent Popoto `rebuild_indexes()` empties `$Class:AgentSession`, making `query.filter` return empty for a live session (issue #1720).
**Status under B2: DISSOLVED (OQ3 resolved).** B2 adds no new query path — it injects one env value and the resolver runs `find_session(session_id=…)` exactly as it does today, guarded by `_find_session`'s existing `_CLASS_SET_RETRY_ATTEMPTS` bounded retry. No `get_by_id` fallback is introduced (that was Seam A), so there is no new unguarded single-shot filter on the hot path. This race requires no new mitigation for this fix.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] The general env-identifier unification (single canonical session env var) is not attempted here.
- **[SEPARATE-SLUG #2205] The secondary hooks mismatch** (`pre_tool_use.py` / `liveness_writers.py` read `AGENT_SESSION_ID` (hex) and resolve via `filter(session_id=…)`, missing for bridge sessions) is **deferred under B2** — B2 does not touch the hooks. This is pre-existing best-effort fail-silent behavior; B2 does not regress it.
- The "SDLC N" free-text → issue-number matching in `find_session_by_issue` is out of scope — it is a different path and fixing it would mask the resolver bug.

## Update System

No update system changes required — this is a purely internal resolver/env-injection fix. No new dependencies, config files, or migration steps. The change ships with the normal `/update` code sync; no `scripts/update/` or `/update` skill edits needed.

## Agent Integration

No new agent integration required — this is a bridge/worker-internal change to session resolution. The `sdlc-tool session-ensure` entry point and the headless runner already exist; this fix corrects how they resolve an injected identifier. Integration coverage is via `tests/integration/test_sdlc_session_ensure_integration.py` (the agent path that invokes `session-ensure` is exercised there), not a new tool surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/` WS-F / session-ownership doc (or the session-ensure feature doc) to record the identifier-type contract: which env var carries which identifier, and which field the resolver queries. If no single doc owns this, add a short section to the bridge-worker or session-lifecycle doc.
- [ ] Add a one-line note to the WS-F / duplicate-ownership history: "recurred because the resolver's `VALOR_SESSION_ID` contract was never wired — the executor injected only the per-run hex `AGENT_SESSION_ID`; fixed in #2190 by injecting `VALOR_SESSION_ID = session.session_id`."
- [ ] Record the **namespace-disjointness requirement** (Risk 5) in the identifier-type contract doc: `session_id` prefixes (`tg_valor_…`, `sdlc-local-…`, `local-…`) and the hex `agent_session_id` namespace must remain disjoint, because `VALOR_SESSION_ID`-first resolution relies on a `session_id` value never matching an `agent_session_id`-shaped one.

### Inline Documentation
- [ ] **Do NOT edit `sdlc_session_ensure.py:449-451`.** Its existing comment ("bridge-initiated sessions inject `VALOR_SESSION_ID`") is *already accurate* once B2 wires the injection — no wording change is needed, and leaving it untouched preserves the byte-identical-resolver invariant the Verification table asserts (empty `git diff main` on both resolver files). The "where the injection lives" detail belongs at the injection site, below — not in the resolver.
- [ ] Add a short comment at the `_harness_env` injection site (`agent/session_executor.py:1940`) noting `VALOR_SESSION_ID = session.session_id` is the resolver's primary identifier for ownerless-adopt (issue #2190), distinct from the per-run hex `AGENT_SESSION_ID`. This is the *sole* comment/code change outside the test suite; the two resolver files stay untouched.

## Success Criteria

- [ ] A cold `session-ensure` invoked from a bridge PM session's subprocess (env: `VALOR_SESSION_ID=<session_id>`, `AGENT_SESSION_ID=<hex>` — the shape B2 produces) **adopts** the ownerless session and mints **no** `sdlc-local-<N>`.
- [ ] Filing "SDLC N" to the bridge yields exactly **one** eng `AgentSession` for issue N (validated by the regression test at the unit level; the integration test exercises the ensure path).
- [ ] WS-F's protections still hold: divergent-owner not adopted; bind-fail/ISSUE_LOCKED returns an error and never mints (#1671/#1672 convergence intact) — asserted by tests using the B2 env shape (`VALOR_SESSION_ID`=session_id).
- [ ] The regression test reproduces the production shape and includes a red-state proof at the *pre-fix* shape (`VALOR_SESSION_ID` unset, only `AGENT_SESSION_ID=<hex>`) — the gap WS-F's tests missed.
- [ ] On an env-resolution **exception** (Redis error in `find_session`), `session-ensure` logs at debug and does not crash, degrading to the legacy issue-lookup/create path (corrected criterion — the infra-error case is not a "never mint" guarantee; see Failure Path Test Strategy).
- [ ] The hooks' secondary mismatch is **deferred**: follow-up issue #2205 filed (No-Gos and here).
- [ ] **Behavioral equivalence across the self-owned eng population (Risk 4):** a live self-owned eng session that already owns issue N resolves via `VALOR_SESSION_ID` and returns the *same* session with no re-bind/re-stamp/mint; a **terminal-status** self-owned session is NOT adopted/resurrected — outcome identical to the pre-B2 issue-based path (asserted by test).
- [ ] **Namespace disjointness pinned (Risk 5):** a fixture assertion proves `session_id` shapes and the hex `agent_session_id` namespace cannot collide; the contract doc records the disjointness requirement.
- [ ] **Post-deploy production check passes (manual):** after deploy, one live `SDLC N` yields exactly one eng `AgentSession` and zero `sdlc-local-<N>` mint within the recurrence window; result recorded in the PR before merge (see Verification → Post-Deploy Production Check).
- [ ] The resolver files are byte-identical to main — `git diff main -- tools/_sdlc_utils.py tools/sdlc_session_ensure.py` is empty (the 449-451 comment is left untouched; it is already accurate under B2).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -n VALOR_SESSION_ID agent/session_executor.py` is non-empty — confirms the executor injects `VALOR_SESSION_ID` into `_harness_env` (the B2 fix).

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (resolver-fix)**
  - Name: resolver-builder
  - Role: Implement Seam B2 (inject `VALOR_SESSION_ID`) + the regression test; preserve WS-F guards
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

### 1. Write the failing regression test
- **Task ID**: build-regression-test
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_session_ensure.py` (new case), must FAIL against current main (red-state proof)
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto — paste the Popoto/data rules from `DOMAIN_FRAMING.md`
- **Parallel**: false
- Add the red-state test: live ownerless eng PM session (`session_id=tg_valor_…`, distinct hex `agent_session_id`, `is_ledger=False`, no `issue_url`); env has **only** `AGENT_SESSION_ID=<hex>`, `VALOR_SESSION_ID` unset. Assert `ensure_session` mints `sdlc-local-<N>` (documents the bug).
- Add the green-state test at the B2 shape: env has `VALOR_SESSION_ID=<session_id>` + `AGENT_SESSION_ID=<hex>`. Assert `ensure_session` **adopts** the session (returns it, binds run_id, stamps issue_url) and does NOT create `sdlc-local-<N>`.
- Confirm the green-state test FAILS on current main (no injection yet); paste the failure into the PR as red-state proof.

### 2. Implement Seam B2 (inject `VALOR_SESSION_ID`)
- **Task ID**: build-seam-fix
- **Depends On**: build-regression-test
- **Validates**: the green-state regression test now passes; `tests/unit/test_sdlc_session_ensure.py`, `tests/integration/test_sdlc_session_ensure_integration.py`
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Domain**: async/concurrency + Redis/Popoto
- **Parallel**: false
- Add one line to `_harness_env` in `agent/session_executor.py:1938-1941`: `"VALOR_SESSION_ID": session.session_id or "",`.
- Do NOT change `tools/sdlc_session_ensure.py` or `tools/_sdlc_utils.py` — the resolver already reads `VALOR_SESSION_ID` first. Preserve WS-F guards; do not touch the bind/lock mechanism.
- Update the divergent-owner / fall-through tests to route the resolvable env value via `VALOR_SESSION_ID`.
- Add the `session_executor` test that pins `_harness_env["VALOR_SESSION_ID"] == session.session_id`.
- Add the **behavioral-equivalence tests** over the self-owned eng population (Risk 4): live-self-owned returns the same session (no re-bind/re-stamp/mint); terminal-status self-owned is not adopted/resurrected.
- Add the **namespace-disjointness fixture assertion** (Risk 5): `session_id` shapes vs. hex `agent_session_id` cannot collide.
- `grep -rn VALOR_SESSION_ID` to confirm no reader treats its presence as a "not a bridge session" signal (Risk 2).

### 3. File the hooks-deferral follow-up issue
- **Task ID**: build-hooks-decision
- **Depends On**: build-seam-fix
- **Validates**: a filed issue link (#2205); No-Gos + Success Criteria updated with the number
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- File a follow-up issue: `pre_tool_use.py` / `liveness_writers.py` resolve `AGENT_SESSION_ID` (hex) via `filter(session_id=…)`, missing for bridge sessions (best-effort fail-silent). Record the number in the plan No-Gos + Success Criteria. No hooks code changes in this PR.

### 4. Ownership-safety review
- **Task ID**: review-ownership
- **Depends On**: build-seam-fix, build-hooks-decision
- **Assigned To**: ownership-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify no over-adoption path; divergent-owner guard intact; bind-fail never mints; the WS-F ownerless-adopt block is reached unchanged; no unexpected `VALOR_SESSION_ID` consumer.

### 5. Regression validation
- **Task ID**: validate-regression
- **Depends On**: review-ownership
- **Assigned To**: regression-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the green-state test is red-before / green-after; run the full ensure test module; verify guard tests use the B2 env shape.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-regression
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Record the identifier-injection contract; add the injection-site comment at `agent/session_executor.py:1940`; note the recurrence+fix in WS-F history. **Do not touch `sdlc_session_ensure.py:449-451`** — its comment is already accurate and the resolver files must stay byte-identical (Verification empty-diff invariant).

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
| Tests pass | `pytest tests/unit/test_sdlc_session_ensure.py tests/integration/test_sdlc_session_ensure_integration.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Regression case exists | `grep -rn "VALOR_SESSION_ID" tests/unit/test_sdlc_session_ensure.py` | output contains VALOR_SESSION_ID |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_sdlc_session_ensure.py \| grep -v '# open bug'` | exit code 1 |
| B2 fix present: executor injects `VALOR_SESSION_ID` | `grep -n VALOR_SESSION_ID agent/session_executor.py` | non-empty (injection site) |
| Resolver unchanged: no new `get_by_id` in resolver | `git diff main -- tools/_sdlc_utils.py tools/sdlc_session_ensure.py` | empty (B2 touches neither — resolver files byte-identical, comment at 449-451 left as-is) |

### Post-Deploy Production Check (manual — do NOT skip)

WS-F recurred with green tests and broken prod, so tests alone are insufficient. After the fix is deployed to the bridge machine (`/update` + bridge restart), run one **live single-session assertion**:

1. File a real `SDLC <N>` (a throwaway/low-stakes issue) to the Telegram bridge.
2. Wait ~3 minutes (longer than the ~2-min duplicate-mint window observed in #2065).
3. Assert **exactly one** eng `AgentSession` exists for issue N and **no** `sdlc-local-<N>` anchor was minted:
   ```bash
   python -m tools.valor_session list | grep -E "sdlc-local-<N>|<N>"   # expect: no sdlc-local-<N> row
   curl -s localhost:8500/dashboard.json | python3 -c "import json,sys; d=json.load(sys.stdin); ss=[s for s in d['sessions'] if '<N>' in (s.get('slug') or '')+(s.get('session_id') or '')]; print(len(ss), 'sessions for issue <N>')"
   ```
   Expected: exactly one eng session, zero `sdlc-local-<N>`.
4. Record the result (session id + count) in the PR before merge. If a duplicate appears, the fix did not close the recurrence — do NOT merge.

## Critique Results

Critique returned **NEEDS REVISION**. Revision applied (this pass):

| Severity | Finding | Addressed By |
|----------|---------|--------------|
| Blocker (OQ1) | Plan presented seams as a menu; build was gated on an unresolved decision. | **Committed to Seam B2** — inject `VALOR_SESSION_ID = session.session_id` in `_harness_env` (`agent/session_executor.py:1940`). Technical Approach rewritten; task 2 is now a one-line change; A/B1 recorded as rejected alternatives. |
| Blocker | "No-mint-on-infra-error" test requirement contradicted the actual control flow — the env-short-circuit `except` at `sdlc_session_ensure.py:566` falls through to `find_session_by_issue` → create/mint. | **Softened the criterion** to "logs at debug and does not crash; degrades to legacy issue-lookup/create path." Dropped the no-mint claim for the infra-error case (the defect fixed is the *silent-miss* path, not the exception path). Updated Failure Path Test Strategy + Success Criteria. |
| Concern (OQ2) | Hooks mismatch fix vs. defer undecided. | **Decision recorded: DEFER** via a filed follow-up issue (B2 does not touch hooks; pre-existing best-effort fail-silent). No-Go tagged `[SEPARATE-SLUG #2205]`; task 3 filed it. |
| Concern (OQ3) | `rebuild_indexes()` transient on a new `get_by_id` fallback. | **Dissolved** — B2 adds no new query path; the existing `_find_session` bounded retry still guards the single `find_session` call. Race 2 marked dissolved. |
| Non-blocking | Mislabeled test path (`tests/unit/test_sdlc_session_ensure_integration.py`). | Corrected to `tests/integration/test_sdlc_session_ensure_integration.py` in Test Impact, Agent Integration, and Verification. |

**Re-critique (second revision, this pass) — NEEDS REVISION → addressed:**

| Severity | Finding | Addressed By |
|----------|---------|--------------|
| Blocker (self-contradiction) | Verification empty-diff check required `git diff main` on both resolver files to be empty, but Inline Documentation task 6 mandated editing a comment inside `sdlc_session_ensure.py:449-451`. | **Dropped the doc-comment edit** — the comment is already accurate once B2 wires the injection, so no edit is needed. Resolver files stay byte-identical; Inline Documentation and task 6 now explicitly say "do not touch 449-451." Internally consistent with the empty-diff invariant. |
| Concern 1 | B2's env injection changes the resolution branch for ALL self-invoked eng/teammate session-ensure (blast radius wider than "bridge PM adoption"). | **Added Risk 4 + behavioral-equivalence tests** over the self-owned eng population, including the **terminal-status** case (must not adopt/resurrect); outcome must equal the pre-B2 issue-based path. Wired into Test Impact, task 2, Success Criteria. |
| Concern 2 | No post-deploy production check — tests-only repeats WS-F's "green tests, broken prod." | **Added a manual Post-Deploy Production Check** (Verification): one live `SDLC N`, assert exactly one eng session + zero `sdlc-local-<N>` within the recurrence window, recorded in the PR before merge. Added as a Success Criterion. |
| Concern 3 | `session_id` vs `agent_session_id` namespace-disjointness is now load-bearing but unstated/untested. | **Added Risk 5 + a fixture-assertion** (Test Impact, task 2) pinning disjointness, and a contract-doc note (Documentation) + Success Criterion. |

---

## Resolved Decisions (were Open Questions)

1. **Seam selection — RESOLVED: Seam B2.** Inject `VALOR_SESSION_ID = session.session_id` into `_harness_env`, honoring the resolver's pre-existing `VALOR_SESSION_ID`-first contract (`sdlc_session_ensure.py:449-452`). No resolver code change. Rejected: Seam A (higher semantic blast radius) and Seam B1 (changes `AGENT_SESSION_ID` meaning system-wide).
2. **Hooks secondary mismatch — RESOLVED: DEFER.** B2 does not touch `pre_tool_use.py` / `liveness_writers.py`; they stay best-effort fail-silent (status quo). Follow-up issue #2205 filed; number recorded in No-Gos + Success Criteria.
3. **Class-set retry — RESOLVED: not applicable.** B2 introduces no `get_by_id` fallback and no new query path; the existing `_find_session` `_CLASS_SET_RETRY_ATTEMPTS` retry is unchanged.
