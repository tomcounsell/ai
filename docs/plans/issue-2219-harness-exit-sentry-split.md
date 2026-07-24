---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-24
tracking: https://github.com/tomcounsell/ai/issues/2219
last_comment_id: 5066827821
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
Sentry SDK usage). One local fact verified instead: `sentry_sdk` is pinned at
**2.57.0**, which exposes `sentry_sdk.new_scope()` (the 2.x-preferred, non-deprecated
isolation-scope context manager). `push_scope()` still exists but is deprecated in
2.x — the implementation uses `new_scope()`.

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
- PM check-ins: 1 (confirm the fingerprint-split vs tags-only decision — see Open Questions)
- Review rounds: 1

## Prerequisites

No prerequisites — `sentry_sdk` is already a dependency and no external service or
secret is required. Sentry is inert under tests (the `PYTEST_CURRENT_TEST` guard in
`configure_sentry`), so tests exercise the tag-attachment logic through a small pure
helper rather than a live Sentry client.

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
     HarnessExitClass.CLEAN_NO_OUTPUT` immediately after the `returncode is None →
     BINARY_MISSING` check and before the TLS/auth token matching. This is safe:
     `stderr_snippet` is `None`/empty for returncode 0 (set only when
     `returncode != 0`), so no TLS/auth token could ever match a returncode-0 exit
     — the new branch changes only the previously-`GENERIC_NONZERO` returncode-0
     case. The `result_event_fired` short-circuit (returns `None`) stays first, so
     normal completions are unaffected.
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

3. **Fingerprint decision** (see Open Questions): recommended
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
- [ ] Not user-visible output — this is internal logging/telemetry. Verify the Sentry event carries the class tag by asserting the helper's returned payload; the `logger.error` message string is unchanged so existing log consumers are unaffected.

## Test Impact
- [ ] `tests/unit/test_claude_diagnostics.py::TestClassifyHarnessEarlyExit::test_nonzero_with_init_no_tokens_is_generic` — no change: uses `returncode=2`, unaffected by the new returncode-0 branch. Keep as-is (documents that nonzero-with-init stays `GENERIC_NONZERO`).
- [ ] `tests/unit/test_claude_diagnostics.py::TestClassifyHarnessEarlyExit::test_normal_completion_returns_none` — no change: `result_event_fired=True` short-circuits before the new branch. Keep as-is.
- [ ] `tests/unit/test_claude_diagnostics.py` — ADD: `test_returncode_zero_no_result_is_clean_no_output` (returncode=0, init_seen True, result_event_fired False → `CLEAN_NO_OUTPUT`).
- [ ] `tests/unit/test_claude_diagnostics.py` — ADD: tests for `describe_harness_exit_for_sentry` — level is WARNING for `CLEAN_NO_OUTPUT` and ERROR for each other class; payload carries `harness_exit_class`/`harness_returncode` tags, the `harness_exit` context, and the per-class fingerprint; tolerates `stderr_snippet=None`.

No existing test asserts the previous returncode-0 → `GENERIC_NONZERO` behavior, so the classification change introduces no regression to fix.

## Rabbit Holes

- **Rewriting the whole `HarnessExitClass` precedence.** Only add one member and one guard; do not reorder or rename existing classes — `on_early_exit_class` / TLS-streak bookkeeping depends on the current members.
- **Moving the logic into `before_send`.** Tempting to mirror `drop_orphan_noise`, but the exit class is not reconstructable from the `logentry` string alone; tag at the emit site where `exit_class` is already in scope.
- **Adding a retry loop for empty turns.** Out of scope. This plan changes logging/telemetry only; the `text=None` return and the caller's existing empty-turn handling stay exactly as they are.
- **Back-tagging the BRANCH-B `full_text` warning.** It already logs at `warning` (below Sentry's error threshold), so it produces no Sentry event — no tagging needed.

## Risks

### Risk 1: Fingerprinting fragments historical continuity of VALOR-2M
**Impact:** New per-class Sentry issues appear; the old VALOR-2M stops receiving new events and looks "resolved" while causes continue under new fingerprints.
**Mitigation:** Expected and desirable (that is the split). Document the new fingerprint scheme in the feature doc so triagers know to look for `harness-exit-no-result:<class>` issues. If continuity is preferred, the tags-only variant (Open Question) keeps one issue.

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

- [ ] `HarnessExitClass.CLEAN_NO_OUTPUT` exists and `classify_harness_early_exit` returns it for `(returncode=0, result_event_fired=False)`.
- [ ] BRANCH C at `claude.py` attaches `harness_exit_class` + `harness_returncode` tags, a `harness_exit` context, and a per-class fingerprint to the Sentry scope for non-`CLEAN_NO_OUTPUT` classes.
- [ ] A `CLEAN_NO_OUTPUT` exit logs at `warning`, not `error` (no Sentry event).
- [ ] The BRANCH-C return contract (`text=None`, returncode, usage, …) is byte-for-byte unchanged; no caller behavior changes.
- [ ] Sentry-tagging failure never suppresses the `logger.error` (best-effort guard tested).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

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
- Insert `if returncode == 0: return HarnessExitClass.CLEAN_NO_OUTPUT` after the `returncode is None → BINARY_MISSING` check in `classify_harness_early_exit`; update its docstring precedence list.
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
- Add `describe_harness_exit_for_sentry` tests: level per class, tag/context/fingerprint payload, `stderr_snippet=None` tolerance.
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
| Lint clean | `python -m ruff check agent/session_runner/harness/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_runner/harness/` | exit code 0 |

## Open Questions

1. **Fingerprint-split vs tags-only.** Recommended: `fingerprint =
   ["harness-exit-no-result", str(exit_class)]` so each cause becomes its own
   Sentry issue (cleanest "split the bucket," independent resolve/ignore). The
   alternative keeps one Sentry issue and relies on the `harness_exit_class` tag
   for drill-down (preserves VALOR-2M continuity, less issue proliferation). Which
   do you want? Default to fingerprint-split unless you say otherwise.
2. **Should `AUTH_UNAVAILABLE` / `TLS_TRUST` also downgrade below error?** They are
   already special-cased elsewhere (TLS emits its own warning up-branch). Current
   plan keeps them at `error` in BRANCH C so they remain visible with tags. OK to
   leave error-level, or downgrade any of them too?
