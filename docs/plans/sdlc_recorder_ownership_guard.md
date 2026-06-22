---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1735
last_comment_id: IC_kwDOEYGa088AAAABHCMnwA
revision_applied: true
---

# SDLC Recorder Ownership Guard (loud-fail on diverted artifact write)

## Problem

The forked CRITIQUE/REVIEW SDLC skills write verdicts and stage markers through two recorder
CLIs — `tools/sdlc_verdict.py record` and `tools/sdlc_stage_marker.py`. Both resolve the
owning session via `tools._sdlc_utils.find_session(issue_number=N, ensure=True)` and then write.

#1731 (PR #1736, merged) fixed the *skill* layer so a forked critique/review skill always passes a
real `--issue-number N`. The recorder precedence fix #1671/#1672 (PR #1673) ensures that when a real
`N` is passed, issue-based resolution beats inherited `VALOR_SESSION_ID`. But there remains a second,
deferred failure path: if neither an `issue_url`-owning session nor a deterministic `sdlc-local-{N}`
record exists for `N`, `find_session` falls back to the env-var session (or the `ensure` path) and
**writes the artifact to a session that does not own issue N** — silently, with exit 0. The forked
subagent reports success; the router reads `--issue-number N` later, sees nothing, and loops on a
dispatch guard.

**Current behavior:**
Recorder resolves to a non-owning session (env-var fallback) and writes the verdict/marker there.
Exit 0. The divert is invisible to the subagent report and to the operator.

**Desired outcome:**
When `--issue-number N` is **explicitly passed** but the resolved session does **not own** issue N,
the recorder exits non-zero with a clear stderr diagnostic so the divert surfaces in the subagent
report. Calls that **omit** `--issue-number` (legitimate bridge PM sessions relying on env-var
resolution) are completely unaffected.

## Freshness Check

**Baseline commit:** `bfe3b0a6bbc1bc4620b3694419dd866f52d4f0d8`
**Issue filed at:** 2026-06-18T12:11:48Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/sdlc_verdict.py::_cli_record` (line 324) — issue claims it resolves then records with no ownership check — still holds.
- `tools/sdlc_stage_marker.py::write_marker` (line 145) — issue claims it resolves then writes with no ownership check — still holds.
- `tools/_sdlc_utils.py::find_session_by_issue` (line 114) — re-verified against baseline: it encodes **THREE** ownership predicates, not two — (1) `issue_url` endswith `/issues/{N}` (lines 170-175), (2) deterministic `sdlc-local-{N}` id (lines 180-190), (3) `message_text` regex `\bissue\s*#?\s*{N}\b` case-insensitive for bridge-originated sessions with no `issue_url` (lines 194-201). The guard's `session_owns_issue` must mirror ALL THREE to avoid falsely rejecting a legitimate bridge eng session that owns the issue via the third pass.
- `tools/_sdlc_utils.py::find_session` precedence (lines 268-327) — issue-number lookup before env-var fallback (#1671/#1672) — still holds; the guard must not alter this order.

**Cited sibling issues/PRs re-checked:**
- #1731 — closed; skill-side fix delivered by PR #1736 (merged 2026-06-18T12:51:39Z). Prerequisite satisfied.
- #1734 — closed 2026-06-22 as duplicate, consolidated into #1735. Its one extra constraint (preserve #1671/#1672/#1673 precedence) is folded into this plan.
- #1671/#1672 (PR #1673) — the precedence fix that this guard sits *after*; must remain intact.

**Commits on main since issue was filed (touching referenced files):** none touching `tools/sdlc_verdict.py`, `tools/sdlc_stage_marker.py`, or `tools/_sdlc_utils.py`.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/sdlc-fork-issue-number-divert.md` (the #1731 skill-side plan, already shipped). No active overlap — this plan is the additive recorder-layer follow-up it explicitly deferred.

**Notes:** No drift. All file:line references are current against baseline `bfe3b0a6`.

## Prior Art

- **#1731 (PR #1736)**: "assign ISSUE_NUMBER unconditionally in fork-skill invocations" — fixed the skill-arg layer so a real `--issue-number N` always reaches the recorder. Merged. This is the prerequisite; the current guard is the deferred recorder-layer half.
- **#1671/#1672 (PR #1673)**: recorder session-resolution precedence — explicit `--issue-number N` beats inherited `VALOR_SESSION_ID`. Merged. The ownership guard sits *after* this resolution and must not alter the resolution order.
- **#1734**: identical-scope duplicate of this issue, closed and consolidated into #1735. No separate work.
- **#1558**: introduced the deterministic `sdlc-local-{N}` session for the sessionless-local write/read path. Defines the second ownership predicate the guard reuses.

## Why Previous Fixes Failed

The prior fix (#1731 / PR #1736) was correct but addressed only the skill arg/env layer. It was
explicitly scoped to *not* touch the recorder, deferring the recorder-layer guard to this issue. So
there is no "failed fix" — the recorder path was a known, deliberately-deferred gap, not a botched
attempt. The two layers are complementary (see `docs/features/sdlc-tool-resolver.md` table): the
skill layer guarantees a real `N` is produced; this guard guarantees that a write keyed by `N` that
cannot find an owning session fails loudly instead of diverting.

## Data Flow

1. **Entry point**: A forked CRITIQUE/REVIEW subagent runs `python -m tools.sdlc_verdict record --issue-number N ...` or `sdlc-tool stage-marker --stage X --status Y --issue-number N`.
2. **CLI parse**: `--issue-number` parses to `int` `N`, or `None` when omitted.
3. **Resolution**: `_cli_record` / `write_marker` calls `find_session(session_id, issue_number=N, ensure=True)`. Precedence (#1671/#1672): explicit id → issue-based (`find_session_by_issue`) → env-var → auto-ensure.
4. **[NEW] Ownership gate**: when `N` was explicitly passed (`issue_number is not None`), check whether the resolved session owns `N` via the new `session_owns_issue(session, N)` helper. If it does not, return a non-zero exit + stderr diagnostic and DO NOT write.
5. **Write**: only reached when ownership holds, or when `--issue-number` was omitted (env-var path, unchanged).
6. **Output**: success JSON + exit 0, or `{}` + stderr + exit 1 on the divert case.

## Architectural Impact

- **New dependencies**: none (pure-Python, stdlib only).
- **Interface changes**: one new public helper `session_owns_issue(session, issue_number) -> bool` in `tools/_sdlc_utils.py`. No change to `find_session` / `find_session_by_issue` signatures or behavior.
- **Coupling**: keeps ownership logic in one module (`_sdlc_utils`) that both recorders already import — no new cross-module coupling.
- **Data ownership**: unchanged. The guard is read-only validation of the resolution result; it never mutates session resolution order.
- **Reversibility**: trivial — the guard is a single early-return branch in each CLI path plus one helper; easily revertible.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No external prerequisites — internal Python change. #1736 (skill-side fix) is already merged, satisfying the stated dependency.

## Solution

### Key Elements

- **`session_owns_issue(session, issue_number)` helper** (`tools/_sdlc_utils.py`): returns True iff the session owns the issue by the **three** predicates `find_session_by_issue` resolves on — (1) `session.issue_url` endswith `/issues/{issue_number}`, OR (2) `session.session_id == f"sdlc-local-{issue_number}"`, OR (3) `session.message_text` matches the case-insensitive regex `\bissue\s*#?\s*{issue_number}\b`. Mirrors `find_session_by_issue`'s **complete** ownership semantics so there is a single source of truth for "ownership." Omitting predicate (3) would falsely reject a bridge eng session that owns the issue only via its message text — the exact spurious exit-1 the guard must not produce (see Risk 4).
- **Verdict CLI guard** (`tools/sdlc_verdict.py::_cli_record`): after resolution, if `args.issue_number is not None` and the resolved session does not own that issue, return a sentinel/raise so `main()` emits stderr and exits 1.
- **Stage-marker CLI guard** (`tools/sdlc_stage_marker.py::write_marker`): after resolution, if `issue_number is not None` and the resolved session does not own that issue, return `({}, 1)` with a clear stderr diagnostic in `main()`.

### Flow

Forked skill runs recorder `--issue-number N` → recorder resolves session (unchanged precedence) →
**ownership gate**: session owns N? → yes: write + exit 0 / no: stderr diagnostic + exit 1 (no write).
Recorder run with no `--issue-number` → ownership gate skipped entirely → env-var path + write + exit 0 (unchanged).

### Technical Approach

- Add `session_owns_issue(session, issue_number) -> bool` to `tools/_sdlc_utils.py`. Guard against `None`/falsy issue_number (return False), read `issue_url`/`session_id`/`message_text` defensively via `getattr` (default `""`/`None`), never raise (wrap the whole body in `try/except Exception: return False`) — consistent with the module's graceful-failure contract. The three predicates are OR'd in the same order `find_session_by_issue` checks them: `issue_url.endswith(f"/issues/{N}")`, then `session_id == f"sdlc-local-{N}"`, then `re.search(rf"\bissue\s*#?\s*{N}\b", message_text, re.IGNORECASE)`. Reuse the identical regex so the helper and the resolver can never diverge. To keep that single-source-of-truth concrete, the builder should reference (or, if cheap, factor out) the exact pattern used in `find_session_by_issue` lines 194-201 rather than re-deriving it.
- **Gate only when `issue_number is not None`.** This is the load-bearing distinction: an omitted `--issue-number` (the bridge PM case) must skip the gate entirely. Do NOT gate on the resolved session being None alone — that is the forbidden bare-`None` guard.
- The gate runs **after** `find_session` returns, so the #1671/#1672/#1673 resolution precedence is untouched. The guard validates the *result*, it does not change *how* the result is chosen.
- **`sdlc_verdict.py`**: `_cli_record` currently returns `{}` (exit 0) on `session is None`. Introduce a guarded path: when `issue_number is not None` and the session does not own it, surface a loud failure. Simplest mechanism that fits the existing `main()` shape: have `_cli_record` raise a dedicated exception (e.g. a module-level `OwnershipError`) that `main()`'s existing `except Exception` block already converts to stderr + exit 1 — OR return a `(result, failed)` signal. Builder picks the lower-churn option; the existing `main()` already does `sys.exit(1 if failed else 0)` and prints stderr on exception, so raising is the cleanest. The stderr message must name the issue number and the resolved session id.
- **`sdlc_stage_marker.py`**: `write_marker` already returns `(result, exit_code)`. Add the gate after the `find_session` call (line 145 area): if `issue_number is not None and not session_owns_issue(session, issue_number)`, return `({}, 1)`. `main()` already prints a loud stderr diagnostic for `exit_code != 0` — extend/branch that message so the divert case reads clearly (distinct from the existing "state-machine write rejected" message). The existing degraded/quiet paths (substrate absent, no-session-with-no-issue) stay exit 0.
- **Interaction with `ensure=True`**: both writers pass `ensure=True`, which may auto-create an `sdlc-local-{N}` session for issue `N`. That created session DOES own `N` (its id is `sdlc-local-{N}`), so the gate correctly passes for legitimate cold-start writes. The gate only fires when resolution lands on a session whose id/issue_url belongs to a *different* issue — the true divert.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `session_owns_issue` must never raise: wrap attribute reads so a malformed session returns False, not an exception. Add a test passing a session-like object whose `getattr` raises.
- [ ] If the verdict guard is implemented by raising `OwnershipError`, assert `main()`'s existing `except Exception` path converts it to exit 1 + stderr (no `except Exception: pass` is being added).

### Empty/Invalid Input Handling
- [ ] `session_owns_issue(None, N)` → False. `session_owns_issue(session, None)` → False. `session_owns_issue(session, 0)` → False.
- [ ] Recorder with `--issue-number` omitted → gate skipped, write proceeds, exit 0 (the critical no-regression case for bridge PM sessions).

### Ownership Predicate Parity (three predicates)
- [ ] `session_owns_issue` returns True for a session owning issue N via predicate (3) `message_text` only — e.g. a bridge eng session with `issue_url=None`, `session_id` unrelated, and `message_text="SDLC issue #N: ..."`. This is the predicate the critique blocker flagged as missing.
- [ ] Word-boundary correctness: `message_text="tissue N"` or `"issue N7"` must NOT match (mirrors the resolver's `\b...\b` guard); `"issue N"`, `"issue #N"`, `"SDLC issue N"` must match (case-insensitive).
- [ ] **CLI-level no-regression**: recorder with `--issue-number N` whose resolved session owns N via `message_text` → exit 0, write performed. This proves the guard does not spuriously fail a legitimate bridge eng session that `find_session_by_issue` would have correctly returned.

### Error State Rendering
- [ ] Divert case stderr must name the issue number and resolved session id so the operator/subagent report can diagnose. Assert the stderr text in a CLI-level test.
- [ ] stdout still emits `{}` (or the degraded JSON) on the loud-fail path so JSON-parsing callers don't choke; the non-zero exit is the loud signal (mirrors the existing `sdlc_verdict.main()` contract).

## Test Impact

- [ ] `tests/unit/test_sdlc_verdict.py::test_cli_record_passes_ensure_true` — UPDATE: confirm it still passes; the fixture session must own the issue it records for (or omit `--issue-number`) so the new gate doesn't trip it. Adjust fixture `issue_url`/`session_id` if needed.
- [ ] `tests/unit/test_sdlc_verdict.py::TestVerdictLandsOnIssueSession::test_verdict_lands_on_issue_session_not_env` — UPDATE: this is the #1671/#1672 precedence test; verify the new gate does not change its outcome (the resolved session owns the issue, so the gate passes). Add an assertion that exit is success.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: existing degraded/no-session/idempotent tests must still pass; add coverage rather than rewrite. Verify the no-`issue_number` and owning-session paths stay exit 0.
- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE (additive): add a `TestSessionOwnsIssue` class alongside `TestFindSessionByIssue`; no existing test changes. Cover all three predicates (issue_url, sdlc-local-{N}, message_text) plus non-owner, None/0 inputs, malformed session (no raise), and word-boundary correctness for the message_text predicate.
- [ ] New cases to add (per acceptance criteria): explicit issue number + non-owning session → exit 1; explicit issue number + owning session (via `issue_url`) → exit 0; explicit issue number + owning session (via `sdlc-local-{N}`) → exit 0; **explicit issue number + owning session (via `message_text` only) → exit 0** (the critique-blocker case); no issue number + env session → exit 0; `find_session()` precedence tests pass unchanged.

## Rabbit Holes

- Do NOT refactor `find_session` / `find_session_by_issue` resolution order — the guard is strictly additive and runs after resolution. Touching precedence risks regressing #1671/#1672.
- Do NOT add ownership gating to `sdlc_dispatch.py`, `sdlc_meta_set.py`, or the read paths (`get`, `stage-query`). The issue scopes the guard to the two named recorders (`sdlc_verdict record`, `sdlc_stage_marker`). Other writers are out of scope.
- Do NOT introduce a new "ownership" abstraction beyond the three existing predicates `find_session_by_issue` already uses. Reuse `find_session_by_issue`'s exact conditions (issue_url suffix, sdlc-local-{N} id, message_text regex) — no more, no less.
- Do NOT make the no-`--issue-number` path noisy in any way — it must be byte-for-byte unchanged.

## Risks

### Risk 1: Gate trips legitimate bridge PM sessions
**Impact:** Every bridge PM critique/review that omits `--issue-number` would start failing — exactly the breakage the issue warns against.
**Mitigation:** Gate strictly on `issue_number is not None`. A dedicated test asserts the omitted-issue-number path is exit 0 with the write performed. This is the primary acceptance criterion.

### Risk 2: `ensure=True` auto-created session falsely flagged as non-owning
**Impact:** Cold-start sessionless-local writes (#1558) could spuriously fail.
**Mitigation:** The auto-ensured session is keyed `sdlc-local-{N}`, which the ownership predicate accepts. Add an explicit test for the `sdlc-local-{N}` ownership case.

### Risk 3: Verdict `main()` loud-fail mechanism diverges from stage-marker's
**Impact:** Inconsistent exit/stderr behavior between the two recorders confuses operators.
**Mitigation:** Both must emit a clear stderr line naming issue + session id and exit 1, stdout `{}`. A parity test (or matching assertions in both test files) confirms the contract.

### Risk 4: `session_owns_issue` under-mirrors `find_session_by_issue`, falsely rejecting a legitimate write (CRITIQUE BLOCKER)
**Impact:** `find_session_by_issue` resolves ownership through THREE passes; the third matches `message_text` (`\bissue\s*#?\s*{N}\b`) for bridge-originated eng sessions that have no `issue_url`. If `session_owns_issue` mirrors only the first two predicates, a bridge eng session that legitimately owns issue N via the message_text pass is correctly *returned* by `find_session` but then *falsely rejected* by the gate — producing a spurious exit-1 on a legitimate write. This is the same Risk 1 breakage class (legitimate bridge sessions failing), reached through resolution-divergence rather than the omitted-`--issue-number` path.
**Mitigation:** `session_owns_issue` mirrors **all three** `find_session_by_issue` predicates, reusing the identical regex, so the helper can never reject a session the resolver would return. A dedicated CLI-level test asserts `--issue-number N` + a session owning N via `message_text` only → exit 0 with the write performed (see Failure Path Test Strategy → Ownership Predicate Parity).

## Race Conditions

No race conditions identified — both recorder CLI paths are synchronous, single-process invocations. The ownership check is a pure read of already-resolved session attributes with no shared mutable state and no concurrency.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1731] Skill arg/env ISSUE_NUMBER assignment — already shipped via PR #1736; this plan only adds the complementary recorder-layer guard.

Beyond the above, nothing is deferred — the guard, helper, tests, and docs are all in scope for this plan.

## Update System

No update system changes required — this is a purely internal Python change to two existing tool modules. No new dependencies, config files, services, or migration steps. The `sdlc-tool` wrapper and CLI entry points already exist and are unchanged.

## Agent Integration

No new agent integration required. Both recorders are already reachable: `sdlc-tool verdict record` and `sdlc-tool stage-marker` are existing CLI entry points invoked by the forked SDLC skills via Bash. The change is internal to those existing commands. The integration surface (exit code + stderr) is exactly what the forked subagent report already observes — that observability is the whole point of the guard. No `.mcp.json` or bridge import changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-tool-resolver.md` § "Forked-skill issue-number passing" — add a subsection documenting the recorder-layer ownership guard: when it fires (explicit `--issue-number N` + non-owning resolved session), when it does not (omitted `--issue-number`), and how it complements the #1731 skill-layer and #1671/#1672 precedence layers. Extend the two-layer table to a three-layer table.

### Inline Documentation
- [ ] Docstring for `session_owns_issue` in `tools/_sdlc_utils.py` stating the three ownership predicates (mirroring `find_session_by_issue`) and the never-raise contract.
- [ ] Update `tools/sdlc_verdict.py` and `tools/sdlc_stage_marker.py` module/CLI docstrings to note the ownership gate and its exit-1 semantics.

## Success Criteria

- [ ] `tools/sdlc_verdict.py record` exits non-zero (1) when `--issue-number N` is passed but the resolved session does not own issue N; no write occurs.
- [ ] `tools/sdlc_stage_marker.py` exits non-zero (1) under the same condition; no marker write occurs.
- [ ] Calls omitting `--issue-number` are unaffected — exit 0, write performed (bridge PM session case).
- [ ] `session_owns_issue` returns True for `issue_url` suffix match, for `sdlc-local-{N}` id match, AND for `message_text` regex match (`\bissue\s*#?\s*{N}\b`, case-insensitive); False otherwise; never raises. It mirrors all three `find_session_by_issue` predicates.
- [ ] Existing `find_session()` / `find_session_by_issue` precedence tests pass unchanged.
- [ ] Ownership-gate edge-case tests added: explicit N + non-owning → exit 1; explicit N + owning (issue_url) → exit 0; explicit N + owning (sdlc-local) → exit 0; explicit N + owning (message_text only) → exit 0; no N + env session → exit 0.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms both recorders reference `session_owns_issue`.

## Team Orchestration

### Team Members

- **Builder (recorder-guard)**
  - Name: recorder-guard-builder
  - Role: Add `session_owns_issue` helper + ownership gate to both recorder CLIs
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: recorder-guard-tester
  - Role: Add ownership-gate edge-case tests to the three test files
  - Agent Type: test-engineer
  - Resume: true

- **Validator (recorder-guard)**
  - Name: recorder-guard-validator
  - Role: Verify all success criteria, especially the no-regression omitted-issue-number path
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: recorder-guard-docs
  - Role: Update sdlc-tool-resolver.md + inline docstrings
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(See repo default agent roster.)

## Step by Step Tasks

### 1. Add ownership helper + gates
- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_utils.py, tests/unit/test_sdlc_verdict.py, tests/unit/test_sdlc_stage_marker.py
- **Assigned To**: recorder-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `session_owns_issue(session, issue_number) -> bool` to `tools/_sdlc_utils.py` (THREE predicates mirroring `find_session_by_issue`: `issue_url` endswith `/issues/{N}`, OR `session_id == sdlc-local-{N}`, OR `message_text` matches `\bissue\s*#?\s*{N}\b` case-insensitive; reuse the resolver's exact regex; never raises; False on None/0/error).
- Add ownership gate to `tools/sdlc_verdict.py::_cli_record`: when `args.issue_number is not None` and the resolved session does not own it, surface a loud failure routed through `main()` to exit 1 + stderr naming issue + session id.
- Add ownership gate to `tools/sdlc_stage_marker.py::write_marker` after the `find_session` call: when `issue_number is not None and not session_owns_issue(...)`, return `({}, 1)`; extend `main()`'s stderr diagnostic to cover the divert case distinctly.
- Update inline docstrings in all three files.

### 2. Add ownership-gate tests
- **Task ID**: build-tests
- **Depends On**: build-guard
- **Assigned To**: recorder-guard-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- `tests/unit/test_sdlc_utils.py`: `TestSessionOwnsIssue` — issue_url match, sdlc-local match, message_text match (incl. word-boundary negatives like "tissue N"/"issue N7" and case-insensitive positives), non-owner, None session, None/0 issue, malformed session (no raise).
- `tests/unit/test_sdlc_verdict.py`: explicit N + non-owning → exit 1 / no write; explicit N + owning (issue_url) → exit 0 / write; explicit N + owning (message_text only) → exit 0 / write; no N + env session → exit 0 / write. Update `test_cli_record_passes_ensure_true` fixture to own its issue.
- `tests/unit/test_sdlc_stage_marker.py`: same matrix for the marker path; preserve existing degraded/idempotent exit-0 behavior.

### 3. Validate
- **Task ID**: validate-guard
- **Depends On**: build-guard, build-tests
- **Assigned To**: recorder-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the three test files; confirm all pass and the omitted-`--issue-number` no-regression case is green.
- grep both recorders for `session_owns_issue`.
- Run ruff check/format.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-guard
- **Assigned To**: recorder-guard-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-tool-resolver.md` § "Forked-skill issue-number passing" with the recorder-layer guard subsection + three-layer table.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: recorder-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run all checks; verify every success criterion including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Utils tests pass | `pytest tests/unit/test_sdlc_utils.py -q` | exit code 0 |
| Verdict tests pass | `pytest tests/unit/test_sdlc_verdict.py -q` | exit code 0 |
| Stage-marker tests pass | `pytest tests/unit/test_sdlc_stage_marker.py -q` | exit code 0 |
| Verdict references helper | `grep -n 'session_owns_issue' tools/sdlc_verdict.py` | output contains session_owns_issue |
| Marker references helper | `grep -n 'session_owns_issue' tools/sdlc_stage_marker.py` | output contains session_owns_issue |
| Lint clean | `python -m ruff check tools/_sdlc_utils.py tools/sdlc_verdict.py tools/sdlc_stage_marker.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/_sdlc_utils.py tools/sdlc_verdict.py tools/sdlc_stage_marker.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Critique (NEEDS REVISION) | `session_owns_issue` omitted the `message_text` ownership predicate that `find_session_by_issue` resolves on. The plan defined only two predicates (issue_url suffix, sdlc-local-{N} id), but `find_session_by_issue` resolves through THREE passes — the third being a `message_text` regex `\bissue\s*#?\s*{N}\b` for bridge-originated eng sessions with no `issue_url`. A bridge eng session that legitimately owns issue N via that third pass would be correctly returned by `find_session` but FALSELY REJECTED by the guard, producing a spurious exit-1 on a legitimate write (exactly the Risk 1 breakage). | Solution → Key Elements, Technical Approach, Failure Path Test Strategy, Test Impact, Success Criteria, Risks (new Risk 4), Step by Step Tasks | `session_owns_issue` extended to mirror ALL THREE predicates of `find_session_by_issue` (issue_url suffix OR sdlc-local-{N} id OR case-insensitive `message_text` regex `\bissue\s*#?\s*{N}\b`), preserving the never-raise contract via getattr defaults / try-except → False. New test case added: legitimate session owning issue N via message_text → exit 0. |

---

## Open Questions

1. **Verdict loud-fail mechanism:** raise a dedicated `OwnershipError` (caught by the existing `main()` `except Exception` → exit 1) vs. thread a `failed` flag back through `_cli_record`/`main()`. Both satisfy the criteria; the plan leans toward raising as lowest-churn given `main()`'s current shape. Confirm the preference, or leave to builder discretion.
