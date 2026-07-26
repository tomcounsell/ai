---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-24
tracking: https://github.com/tomcounsell/ai/issues/2219
last_comment_id: 5066827821
revision_applied: true
revision_applied_at: 2026-07-24T10:12:07Z
---

# Split the "Harness exited without a result event" Sentry bucket by exit class

## Problem

Sentry issue VALOR-2M ("Harness exited without a result event and no accumulated
text") is the highest-volume still-active in-repo error: 682 events, first seen
2026-04-29, last seen 2026-07-22. Every distinct root cause — a killed CLI child,
a TLS-trust early exit, an empty drafter turn that streams nothing and exits 0 —
collapses into this single issue, so the bucket is un-actionable: you cannot tell
which cause dominates or whether any single cause is worth fixing.

**Current behavior:**
- `agent/session_runner/harness/claude.py:1341` emits a bare
  `logger.error("Harness exited without a result event and no accumulated text")`
  in the terminal BRANCH C (no `result` event fired **and** no streamed text
  accumulated), then returns `text=None`.
- The `exit_class` (`HarnessExitClass`) is already computed ~50 lines above (via
  `classify_harness_early_exit`, claude.py ~1294) but is **discarded** at the log
  site — it is used only for the TLS-trust warning and the `on_early_exit_class`
  callback.
- Sentry's `LoggingIntegration` (`monitoring/sentry_config.py`) encodes the bare
  `logger.error` as one `logentry` event with no tags, no context, no fingerprint.
  All causes therefore share one fingerprint and one Sentry issue.
- A **clean exit-0 empty turn** (returncode 0, `init_seen` True, no result, no
  text) is logged at `error` level identically to a genuine failure, and is
  misclassified by `classify_harness_early_exit` as `GENERIC_NONZERO` (a misnomer,
  since returncode is 0). `stderr_snippet` is populated only when `returncode != 0`
  (claude.py ~1273), so this case carries no stderr context at all.

**Desired outcome:**
- The 682-event bucket splits by real cause, so each cause can be triaged,
  resolved, or ignored independently.
- Clean exit-0 empty turns stop paging as errors (they are benign — the caller
  already handles `text=None`), removing the dominant noise source from Sentry.
- Genuine failures (BINARY_MISSING, GENERIC_NONZERO nonzero, STALE_UUID,
  AUTH_UNAVAILABLE) stay error-level and now carry structured tags + context.

## Freshness Check

**Baseline commit:** `35cc1ce6c` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-23T03:02:04Z (triage comment 2026-07-24T06:29:18Z)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_runner/harness/claude.py:1341` — issue claims the `logger.error` here — **still holds**, exact string + line confirmed.
- `agent/session_runner/harness/claude_diagnostics.py:207` — `classify_harness_early_exit` with the five-member `HarnessExitClass` — **still holds**.
- `monitoring/sentry_config.py` — `LoggingIntegration` captures `logger.error` as a `logentry` event; `drop_orphan_noise` `before_send` filter present — **still holds**.

**Cited sibling issues/PRs re-checked:**
- #2100 — the origin of `HarnessExitClass` / `classify_harness_early_exit` (§2). Merged; the classifier is live.
- #1835 (`drop_orphan_noise`) — merged; the `before_send` noise-filter pattern is the precedent for Sentry-side event shaping in this repo.

**Commits on main since issue was filed (touching referenced files):** none — `git log --since` over `claude.py`, `claude_diagnostics.py`, `sentry_config.py` is empty. No drift.

**Active plans in `docs/plans/` overlapping this area:** `issue-2331-sentry-scope-filter.md` (issue #2331) touches Sentry, but on the **triage-reflection scoping** side (`reflections/sentry_triage.py` — which projects' issues get filed as GitHub issues). It does not touch `claude.py` or the harness error site. No file overlap; coordination-awareness only, not a blocker.

**Notes:** Bug is confirmed present against current main by code reading. Reproduction of the live event requires a production CLI-child failure, which is not deterministically reproducible locally; the code path and the missing tags/level-selection are verified statically, which is sufficient for this fix.

## Prior Art

- **Issue #2100**: introduced `HarnessExitClass` + `classify_harness_early_exit`. Succeeded — the classifier is the seam this plan reuses. It stopped short of feeding the class into the Sentry event.
- **Issue #1835 / `drop_orphan_noise`**: added a `before_send` filter to drop benign Popoto orphan-index errors that flooded Sentry (VALOR-S). Directly relevant precedent: it shows this repo shapes Sentry error volume at the source rather than tolerating noisy buckets. This plan follows the same philosophy (downgrade + tag) but at the emit site rather than in `before_send`.
- **Issue #1460**: "degraded-fallback cascade fires too often (1,650+ events/period)" — closed. Same class of problem (a single over-broad error path dominating Sentry volume); confirms the pattern of fixing noisy catch-all log sites.
- No prior PR attempted to tag or split *this specific* error. This is the first fix for VALOR-2M.

## Research

No external WebSearch required — the change is purely internal (in-repo logging +
Sentry SDK usage). One local fact verified instead: `pyproject.toml:29` declares a
floor of `sentry-sdk>=2.0.0` (not a pin), and `sentry_sdk.new_scope()` — the
2.x-preferred, non-deprecated isolation-scope context manager — is available across
the entire `>=2.0.0` range, so the implementation is safe against any installed 2.x
build. `push_scope()` still exists but is deprecated in 2.x; the implementation uses
`new_scope()`.

## Data Flow

1. **Entry point**: a `claude -p` turn runs inside `_run_harness_subprocess`
   (`claude.py:889`). It streams stream-json events and tracks `result_text`,
   `full_text` (accumulated assistant text), `returncode`, `init_seen`,
   `stderr_snippet`.
2. **Exit classification**: after the subprocess exits, `classify_harness_early_exit`
   (claude.py ~1294) computes `exit_class` from `(returncode, stderr_snippet,
   init_seen, result_event_fired)`.
3. **Terminal branch selection** (claude.py ~1313-1350):
   - `result_text is not None` → BRANCH A (return result).
   - `full_text` non-empty → BRANCH B (`logger.warning`, return accumulated text).
   - else → **BRANCH C**: `logger.error(...)`, return `text=None`. **This is the
     defect site.**
4. **Sentry capture**: `LoggingIntegration` turns the BRANCH-C `logger.error` into
   a `logentry` event (error level, no tags) → grouped into the single VALOR-2M
   issue.
5. **Caller**: `_run_harness_subprocess` returns `text=None`; the session-runner
   role driver handles the empty turn (retry / empty-turn logic is unchanged by
   this plan — the return contract is untouched).

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (the fingerprint-split vs tags-only decision was settled at critique — see Resolved Decisions)
- Review rounds: 1

## Prerequisites

No prerequisites — `sentry_sdk` is already a dependency and no external service or
secret is required.

**Testability note (resolves critique tech-debt).** The repo's `configure_sentry`
early-returns under pytest (the `PYTEST_CURRENT_TEST` guard), so the *production*
Sentry client is inert during tests and you cannot prove event attachment by
letting the real integration fire. Two layers cover the gap:

1. **Outcome-level (guaranteed provable):** the pure helper
   `describe_harness_exit_for_sentry` returns the `(level, {tags, context,
   fingerprint})` payload that BRANCH C applies verbatim. Asserting that payload is
   a deterministic, dependency-free check that proves *what* gets attached. This is
   the load-bearing success criterion.
2. **Integration-level (proves the wiring, not just the payload):** a test spins up
   an **isolated** `sentry_sdk` client with an in-memory `CapturingTransport`
   (constructed directly in the test — NOT via `configure_sentry`, which is inert
   under pytest — using `sentry_sdk.Client(transport=<capturing>, ...)` bound to a
   fresh `sentry_sdk.Scope`/`use_client`), drives the BRANCH-C scope+`logger.error`
   path (or a thin extracted helper that opens `new_scope()`, applies the payload,
   and logs), and asserts the *captured event* carries `tags["harness_exit_class"]`
   and the per-class `fingerprint`. This proves `new_scope()`→event attachment end
   to end without depending on the production init path. If the `LoggingIntegration`
   capture proves awkward to drive in isolation, capture via an explicit
   `sentry_sdk.capture_message` inside the same scope as the fallback assertion —
   the scope/tag/fingerprint mechanics are identical. The outcome-level assertion
   (layer 1) is the criterion that must pass; the CapturingTransport test is the
   stronger proof and is expected to pass, but its exact capture mechanism is a
   builder implementation detail.

## Solution

### Key Elements

- **`HarnessExitClass.CLEAN_NO_OUTPUT`**: a new enum member for the exit-0 empty
  turn, so returncode-0 stops masquerading as `GENERIC_NONZERO`.
- **Per-class log level at BRANCH C**: `CLEAN_NO_OUTPUT` downgrades to
  `logger.warning` (below Sentry's error threshold → drops out of the bucket);
  every other class stays `logger.error`.
- **Structured Sentry scope on the error branch**: tags (`harness_exit_class`,
  `harness_returncode`), a `harness_exit` context (returncode, init_seen,
  stderr_snippet), and a per-class `fingerprint` so the one Sentry issue splits
  into one-issue-per-class.
- **Small testable helper**: the level-selection + scope-population logic lives in
  a pure helper in `claude_diagnostics.py`, unit-testable without driving the whole
  subprocess.

### Flow

`claude -p` turn exits → classify exit → BRANCH C reached (no result, no text) →
select level by class → if `CLEAN_NO_OUTPUT`: `logger.warning` (no Sentry event);
else: open `sentry_sdk.new_scope()`, set tags + context + per-class fingerprint,
`logger.error` inside the scope → Sentry receives a class-specific, tagged issue.

### Technical Approach

1. **`agent/session_runner/harness/claude_diagnostics.py`**
   - Add `CLEAN_NO_OUTPUT = "clean_no_output"` to `HarnessExitClass`.
   - In `classify_harness_early_exit`, insert `if returncode == 0: return
     HarnessExitClass.CLEAN_NO_OUTPUT` as the **last** guard — immediately after
     the existing `if not init_seen: return HarnessExitClass.STALE_UUID` check and
     immediately before the `return HarnessExitClass.GENERIC_NONZERO` default.
     **This ordering is load-bearing (resolves the critique blocker).** `STALE_UUID`'s
     condition (`not init_seen`) is returncode-independent, so a
     `(returncode=0, init_seen=False, stderr_snippet=None)` exit lands in
     `STALE_UUID` today. Placing the new guard *earlier* (e.g. right after the
     `BINARY_MISSING` check, as an earlier draft proposed) would let it steal that
     exit and silently downgrade it from error-level `STALE_UUID` to
     warning-level `CLEAN_NO_OUTPUT`, contradicting this plan's own promise that
     `STALE_UUID` stays error-level. Putting the guard *after* the `STALE_UUID`
     check preserves that promise: `STALE_UUID` keeps first claim on every
     `init_seen=False` exit regardless of returncode, and the only case the new
     branch reclassifies is the previously-`GENERIC_NONZERO`
     `(returncode=0, init_seen=True)` exit. The `result_event_fired`,
     `BINARY_MISSING`, `TLS_TRUST`, `AUTH_UNAVAILABLE`, and `STALE_UUID` branches
     all run before the new guard and are untouched. (`stderr_snippet` is
     `None`/empty for returncode 0 — set only when `returncode != 0` — so a
     returncode-0 exit can never match a TLS/auth token anyway; the guard's
     position relative to `STALE_UUID`, not the token checks, is what matters.)
   - Add a pure helper, e.g. `describe_harness_exit_for_sentry(exit_class,
     returncode, init_seen, stderr_snippet) -> tuple[int, dict]` returning
     `(log_level, {tags, context, fingerprint})`, so the level choice and the
     scope payload are unit-testable in isolation. `log_level` is
     `logging.WARNING` for `CLEAN_NO_OUTPUT`, else `logging.ERROR`.

2. **`agent/session_runner/harness/claude.py` (BRANCH C, ~1341)**
   - Replace the bare `logger.error(...)` with: consult the helper for the level;
     if WARNING, `logger.warning("Harness exited cleanly (rc=0) with no result
     event and no streamed text; treating as empty turn")`; else open
     `sentry_sdk.new_scope()`, apply tags/context/fingerprint, and emit the
     existing `logger.error` message inside the scope.
   - Wrap the `sentry_sdk` import + scope block best-effort (a tagging/import
     failure must never mask the log line). Local import (mirrors
     `agent/index_drift.py`'s local `import sentry_sdk`). `set_tag` is a safe no-op
     when Sentry is uninitialized (dev/tests).
   - The return contract (`text=None`, `returncode`, …) is **unchanged** — no
     caller behavior changes.

3. **`on_early_exit_class` consumer audit (no code change required).** The new
   enum member was audited against every consumer of the classifier's result. The
   sole `on_early_exit_class` consumer is `_handle_early_exit_class`
   (`claude.py:506`), which special-cases **only** `HarnessExitClass.TLS_TRUST`
   (INCR the per-session TLS-streak key); every other member — including the new
   `CLEAN_NO_OUTPUT` — falls to the `else` branch that **resets** the streak
   (`_R.delete`). That is the correct behavior for a clean empty turn: a
   non-TLS exit legitimately clears any accumulated TLS streak. The TLS
   suppression gate (`claude.py:637`, `_tls_state["last_class"] ==
   HarnessExitClass.TLS_TRUST and streak >= HARNESS_TLS_CONSECUTIVE_SUPPRESS`) also
   matches only `TLS_TRUST`, so `CLEAN_NO_OUTPUT` cannot perturb retry suppression.
   No consumer enumerates the enum exhaustively (no `match`/`if-elif` chain that
   would raise or mis-route on an unknown member), so adding a member is
   non-breaking. **Add a regression assertion** (see Tests) that
   `_handle_early_exit_class(CLEAN_NO_OUTPUT)` resets the streak, to lock this
   audit conclusion in place.

4. **Fingerprint decision** (settled — see Resolved Decisions):
   `fingerprint = ["harness-exit-no-result", str(exit_class)]` so each class
   becomes its own Sentry issue. Tags alone would keep one issue with a
   tag-breakdown drill-down. Fingerprint is the more direct reading of "split the
   bucket," and lets each class be resolved/ignored independently.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `sentry_sdk` scope block at BRANCH C is wrapped best-effort; add a test asserting that when `sentry_sdk` tagging raises, the `logger.error` still fires (observable behavior preserved). If a full-subprocess test is infeasible, assert the helper is pure and the caller's try/except is present via a targeted unit test around an injected failing scope.
- [ ] `classify_harness_early_exit` has no exception handlers (pure function) — state: covered by direct unit tests.

### Empty/Invalid Input Handling
- [ ] `describe_harness_exit_for_sentry` handles `stderr_snippet=None` (the returncode-0 case) without error — assert the context dict tolerates `None`.
- [ ] The empty-turn path (`text=None` return) is the existing contract; verify BRANCH C still returns `None` and does not loop — no change to the return, so the existing caller-side empty-turn handling stays correct.

### Error State Rendering
- [ ] Not user-visible output — this is internal logging/telemetry. Verify the Sentry event carries the class tag at two levels: (a) **outcome-level** — assert the helper's returned `(level, {tags, context, fingerprint})` payload (deterministic, always provable); (b) **integration-level** — an isolated `CapturingTransport` test asserts the *captured event* carries `harness_exit_class` + the per-class fingerprint, proving `new_scope()`→event attachment despite `configure_sentry` being inert under pytest. The `logger.error` message string is unchanged so existing log consumers are unaffected.

## Test Impact
- [ ] `tests/unit/test_claude_diagnostics.py::TestClassifyHarnessEarlyExit::test_nonzero_with_init_no_tokens_is_generic` — no change: uses `returncode=2`, unaffected by the new returncode-0 branch. Keep as-is (documents that nonzero-with-init stays `GENERIC_NONZERO`).
- [ ] `tests/unit/test_claude_diagnostics.py::TestClassifyHarnessEarlyExit::test_normal_completion_returns_none` — no change: `result_event_fired=True` short-circuits before the new branch. Keep as-is.
- [ ] `tests/unit/test_claude_diagnostics.py` — ADD: `test_returncode_zero_no_result_is_clean_no_output` (returncode=0, init_seen True, result_event_fired False → `CLEAN_NO_OUTPUT`).
- [ ] `tests/unit/test_claude_diagnostics.py` — ADD (**critique blocker regression**): `test_returncode_zero_no_init_stays_stale_uuid` — `(returncode=0, init_seen=False, stderr_snippet=None, result_event_fired=False)` must return `STALE_UUID`, not `CLEAN_NO_OUTPUT`. This pins the insertion-order fix: the new guard runs after the `STALE_UUID` check, so `init_seen=False` keeps error-level `STALE_UUID` regardless of returncode.
- [ ] `tests/unit/test_claude_diagnostics.py` — ADD: tests for `describe_harness_exit_for_sentry` — level is WARNING for `CLEAN_NO_OUTPUT` and ERROR for each other class; payload carries `harness_exit_class`/`harness_returncode` tags, the `harness_exit` context, and the per-class fingerprint; tolerates `stderr_snippet=None`.
- [ ] `tests/unit/test_claude_diagnostics.py` (or the claude-harness test module) — ADD: an isolated `CapturingTransport` test asserting the BRANCH-C `new_scope()` path attaches `harness_exit_class` + the per-class fingerprint to the *captured* Sentry event (proves attachment despite the pytest-inert `configure_sentry`).
- [ ] `tests/unit/` (harness callback coverage) — ADD: assert `_handle_early_exit_class(HarnessExitClass.CLEAN_NO_OUTPUT)` resets the TLS-streak key (falls to the non-TLS `else` branch), locking the consumer-audit conclusion.

No existing test asserts the previous returncode-0 → `GENERIC_NONZERO` behavior, so the classification change introduces no regression to fix.

## Rabbit Holes

- **Rewriting the whole `HarnessExitClass` precedence.** Only add one member and one guard; do not reorder or rename existing classes — `on_early_exit_class` / TLS-streak bookkeeping depends on the current members.
- **Moving the logic into `before_send`.** Tempting to mirror `drop_orphan_noise`, but the exit class is not reconstructable from the `logentry` string alone; tag at the emit site where `exit_class` is already in scope.
- **Adding a retry loop for empty turns.** Out of scope. This plan changes logging/telemetry only; the `text=None` return and the caller's existing empty-turn handling stay exactly as they are.
- **Back-tagging the BRANCH-B `full_text` warning.** It already logs at `warning` (below Sentry's error threshold), so it produces no Sentry event — no tagging needed.

## Risks

### Risk 1: Fingerprinting fragments historical continuity of VALOR-2M
**Impact:** New per-class Sentry issues appear; the old VALOR-2M stops receiving new events and looks "resolved" while causes continue under new fingerprints.
**Mitigation:** Expected and desirable (that is the split). Document the new fingerprint scheme in the feature doc so triagers know to look for `harness-exit-no-result:<class>` issues. The tags-only variant that would keep one issue was considered and rejected at critique (see Resolved Decisions); fingerprint-split is intentional.

### Risk 2: Downgrading `CLEAN_NO_OUTPUT` to warning hides a real regression
**Impact:** If a genuine bug ever manifests as a clean exit-0 empty turn, it now logs at warning and never reaches Sentry.
**Mitigation:** The caller already treats `text=None` as an empty turn regardless of level, so behavior is unchanged; only Sentry visibility drops. The warning is still emitted to logs. Acceptance: an exit-0 turn that produced no output is, by the classifier's contract, a benign empty turn, not a crash.

### Risk 3: `sentry_sdk.new_scope()` misuse leaks tags to unrelated events
**Impact:** Using the global scope instead of an isolated one could attach `harness_exit_*` tags to later events on the same thread.
**Mitigation:** Use the `new_scope()` context manager (isolated scope, auto-popped on exit) and set tags only inside the `with` block that wraps the single `logger.error`.

## Race Conditions

No race conditions identified. The change is confined to a single synchronous
branch executed after the subprocess has already exited; `exit_class`,
`returncode`, `init_seen`, and `stderr_snippet` are all fully materialized before
BRANCH C runs. The `new_scope()` context manager is thread-isolated by design.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item is in scope for this plan. The fix is
  localized to two harness files plus their unit tests; there is no follow-up work
  that could be folded in and isn't.

## Update System

No update system changes required — this is a purely internal logging/telemetry
change. No new dependency (`sentry_sdk` is already installed), config file, or
migration is introduced, so `/update` and `scripts/remote-update.sh` need no edits.

## Agent Integration

No agent integration required — this is a worker-internal change to the headless
session runner's harness. No CLI entry point, MCP tool, or bridge import is added;
the agent surface is untouched.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/headless-session-runner.md` (or the closest harness-diagnostics doc) with a short subsection: the BRANCH-C exit-class Sentry tagging + fingerprint scheme, the `CLEAN_NO_OUTPUT` class, and the "empty exit-0 turn logs at warning" rule.
- [ ] If a dedicated harness-diagnostics doc index entry exists in `docs/features/README.md`, note the new `HarnessExitClass` member there.

### Inline Documentation
- [ ] Docstring on the new `describe_harness_exit_for_sentry` helper.
- [ ] Update the `classify_harness_early_exit` docstring precedence list to include `CLEAN_NO_OUTPUT` (returncode == 0).
- [ ] Comment at BRANCH C explaining the per-class level choice and the fingerprint rationale.

## Success Criteria

- [ ] `HarnessExitClass.CLEAN_NO_OUTPUT` exists and `classify_harness_early_exit` returns it for `(returncode=0, init_seen=True, result_event_fired=False)`.
- [ ] `classify_harness_early_exit` returns `STALE_UUID` (not `CLEAN_NO_OUTPUT`) for `(returncode=0, init_seen=False, stderr_snippet=None, result_event_fired=False)` — the insertion-order blocker regression.
- [ ] **Outcome-level (must pass):** `describe_harness_exit_for_sentry` returns `logging.WARNING` for `CLEAN_NO_OUTPUT` and `logging.ERROR` for every other class, with a payload carrying `harness_exit_class` + `harness_returncode` tags, the `harness_exit` context, and `fingerprint = ["harness-exit-no-result", str(exit_class)]`; tolerates `stderr_snippet=None`.
- [ ] **Integration-level (proves the wiring):** an isolated `CapturingTransport` test confirms the BRANCH-C `new_scope()` path attaches `harness_exit_class` + the per-class fingerprint to the captured Sentry event (works around the pytest-inert `configure_sentry`).
- [ ] A `CLEAN_NO_OUTPUT` exit logs at `warning`, not `error` (no Sentry event).
- [ ] The BRANCH-C return contract (`text=None`, returncode, usage, …) is byte-for-byte unchanged; no caller behavior changes.
- [ ] `_handle_early_exit_class(CLEAN_NO_OUTPUT)` resets the TLS-streak key (consumer-audit regression).
- [ ] Sentry-tagging failure never suppresses the `logger.error` (best-effort guard tested).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

### Post-deploy operator verification (tech-debt: prove the split actually happens)
The unit + integration tests prove the *mechanism*; the following confirms the
*outcome* in production Sentry after the fix ships. Owner: the operator who deploys
the change (record the result in the tracking issue).
- [ ] Within ~7 days of deploy, confirm in Sentry that **VALOR-2M stops accruing new
  events** (its last-seen timestamp goes stale) while new per-class issues fingerprinted
  `harness-exit-no-result:<class>` (e.g. `…:generic_nonzero`, `…:tls_trust`,
  `…:stale_uuid`) begin appearing — i.e. the 682-event bucket has visibly split.
- [ ] Confirm `CLEAN_NO_OUTPUT` produces **no** new Sentry issue (it logs at warning),
  verifying the dominant benign-noise source dropped out of the bucket.
- [ ] If VALOR-2M keeps accruing events post-deploy, the tagging/fingerprint path is not
  firing in production — open a follow-up rather than closing the issue.

## Team Orchestration

### Team Members

- **Builder (harness-sentry)**
  - Name: harness-sentry-builder
  - Role: Implement the enum member, classifier branch, the pure helper, and the BRANCH-C scope/level change.
  - Agent Type: builder
  - Domain: async / logging-observability
  - Resume: true

- **Validator (harness-sentry)**
  - Name: harness-sentry-validator
  - Role: Verify classification, level selection, tag/fingerprint payload, and the unchanged return contract.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: harness-sentry-doc
  - Role: Update the harness feature doc + docstrings.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add classification member + branch + helper
- **Task ID**: build-diagnostics
- **Depends On**: none
- **Validates**: tests/unit/test_claude_diagnostics.py
- **Assigned To**: harness-sentry-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `CLEAN_NO_OUTPUT = "clean_no_output"` to `HarnessExitClass`.
- Insert `if returncode == 0: return HarnessExitClass.CLEAN_NO_OUTPUT` as the **last** guard in `classify_harness_early_exit` — after the `if not init_seen: return STALE_UUID` check and immediately before the `return GENERIC_NONZERO` default (see Technical Approach step 1 for why the order is load-bearing). Update the docstring precedence list to place `CLEAN_NO_OUTPUT` after `STALE_UUID`.
- Add pure helper `describe_harness_exit_for_sentry(exit_class, returncode, init_seen, stderr_snippet) -> tuple[int, dict]` returning `(log_level, {"tags": {...}, "context": {...}, "fingerprint": [...]})`.

### 2. Wire BRANCH C to use the level + scope
- **Task ID**: build-branch-c
- **Depends On**: build-diagnostics
- **Validates**: tests/unit/test_claude_diagnostics.py
- **Assigned To**: harness-sentry-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the bare `logger.error` at BRANCH C with: consult the helper; WARNING → `logger.warning` (empty-turn message); else open `sentry_sdk.new_scope()`, apply tags/context/fingerprint, emit the existing `logger.error` inside the scope.
- Local, best-effort `import sentry_sdk`; the scope block must never mask the log line.
- Leave the return tuple unchanged.

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-diagnostics, build-branch-c
- **Validates**: tests/unit/test_claude_diagnostics.py
- **Assigned To**: harness-sentry-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `test_returncode_zero_no_result_is_clean_no_output`.
- Add `test_returncode_zero_no_init_stays_stale_uuid` (critique-blocker regression: `init_seen=False` returncode-0 stays `STALE_UUID`).
- Add `describe_harness_exit_for_sentry` tests: level per class, tag/context/fingerprint payload, `stderr_snippet=None` tolerance.
- Add the isolated `CapturingTransport` integration test: BRANCH-C `new_scope()` path attaches `harness_exit_class` + per-class fingerprint to the captured event.
- Add a consumer-audit regression: `_handle_early_exit_class(CLEAN_NO_OUTPUT)` resets the TLS-streak key.
- Add a best-effort-guard test: a raising scope does not suppress the log.

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: harness-sentry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_claude_diagnostics.py -q` and the success-criteria checks.
- Confirm the BRANCH-C return contract is unchanged (grep the return tuple; diff the tuple shape).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: harness-sentry-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/headless-session-runner.md` + `docs/features/README.md` index; docstrings.

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: harness-sentry-validator
- **Agent Type**: validator
- **Parallel**: false
- Full unit run + ruff; verify all success criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Diagnostics tests pass | `pytest tests/unit/test_claude_diagnostics.py -q` | exit code 0 |
| New class exists | `grep -c "CLEAN_NO_OUTPUT" agent/session_runner/harness/claude_diagnostics.py` | output > 0 |
| Branch C uses scope | `grep -c "new_scope" agent/session_runner/harness/claude.py` | output > 0 |
| Class tag present | `grep -c "harness_exit_class" agent/session_runner/harness/claude.py` | output > 0 |
| Return contract unchanged | `grep -c "Harness exited without a result event and no accumulated text" agent/session_runner/harness/claude.py` | output > 0 |
| Stale-UUID order preserved | `pytest tests/unit/test_claude_diagnostics.py -q -k stale_uuid` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_runner/harness/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_runner/harness/` | exit code 0 |
| Bucket split (post-deploy, manual) | Inspect Sentry: VALOR-2M stale, `harness-exit-no-result:<class>` issues appearing | operator confirms in tracking issue |

## Resolved Decisions

Both prior open questions were settled during plan critique; recorded here so build
proceeds without reopening them.

1. **Fingerprint-split (not tags-only) — SETTLED.** BRANCH C sets
   `fingerprint = ["harness-exit-no-result", str(exit_class)]` so each cause becomes
   its own Sentry issue (independent resolve/ignore). VALOR-2M continuity is
   intentionally not preserved — the split *is* the goal (see Risk 1).
2. **`AUTH_UNAVAILABLE` / `TLS_TRUST` stay error-level — SETTLED.** Both remain at
   `error` in BRANCH C so they stay visible, now carrying class tags + fingerprint.
   Only `CLEAN_NO_OUTPUT` downgrades to `warning`.
