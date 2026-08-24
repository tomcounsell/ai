---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-05-26
tracking: https://github.com/tomcounsell/ai/issues/1469
last_comment_id:
---

# Sentry Triage Auto-Action (Tiers A/B/E)

## Problem

The `sentry-issue-triage` reflection (`reflections/sentry_triage.py`, scheduled daily via `config/reflections.yaml`) pulls all unresolved Sentry issues, classifies them A–E, and emits a Telegram digest. The last live run surfaced 244 issues; only ~90 (C+D) needed human eyes.

**Current behavior:** Tiers A (noise), B (transient), and E (stale) are reported as "to ignore/archive/resolve" but no Sentry state change is performed — they reappear in the next digest, polluting the signal-to-noise.

**Desired outcome:** On the same run that classifies them, A/B/E issues get their Sentry state updated (A→`ignored`, B→`archived_until_escalating`, E→`resolved`). The digest shrinks to items needing human attention. The example baseline of 244 should drop to ~90 review items.

## Freshness Check

**Baseline commit:** `aac0912c2492340768b8386d08090f3f9f27ad85`
**Issue filed at:** 2026-05-26T07:11:36Z (~3 hours before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**
- `reflections/sentry_triage.py:36` — `DRY_RUN = True` module-level constant — still holds
- `reflections/sentry_triage.py:108-119` — `_get_auth_token()` reads `SENTRY_AUTH_TOKEN` from env or `.env` — still holds
- `reflections/sentry_triage.py:172-205` — `_classify_issue()` returns `(class, reason)` tuple, A–E — still holds
- `reflections/sentry_triage.py:314` — `run_sentry_triage()` takes no args, returns `{status, findings, summary}` — still holds (the docstring also matches)
- `reflections/sentry_triage.py:385-401` — `DRY_RUN` only gates Class C GitHub-issue filing today — still holds
- `config/reflections.yaml` — `sentry-issue-triage` entry, daily 86400s, enabled — still holds (verified in `~/Desktop/Valor/reflections.yaml`)

**Cited sibling issues/PRs re-checked:**
- PR #916 (closed/merged 2026-04-13) — sentry-cli integration, did not touch the triage classifier. No conflict.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=2026-05-26T07:11:36Z` on `reflections/sentry_triage.py`, `config/reflections.yaml`, and `tests/unit/test_sentry*.py` returned empty.

**Active plans in `docs/plans/` overlapping this area:** None.

## Prior Art

- **PR #916 (merged 2026-04-13)**: Sentry CLI integration. Wired `SENTRY_AUTH_TOKEN`, added the opt-in reflection. Created the triage classifier but did not implement auto-action. This plan extends that work directly.
- **Issue #841 (closed 2026-04-13)**: Original Sentry integration scope. No state-change behavior was specified.

No prior attempts to auto-action Sentry issue states — this is greenfield within the established triage callable.

## Research

**Queries used:**
- `Sentry API update issue status archived_until_escalating PUT issues endpoint 2026`

**Key findings:**
- **CRITICAL**: `PUT /api/0/issues/{id}/` with body `{"status": "ignored"}` defaults the substatus to `archived_forever`, NOT `archived_until_escalating`. To get the UI-default behavior (auto-unarchive when the issue escalates), the body must be `{"status": "ignored", "statusDetails": {"ignoreUntilEscalating": true}}`. Source: [sentry-mcp issue #878](https://github.com/getsentry/sentry-mcp/issues/878) and [Sentry "Update an Issue" API docs](https://docs.sentry.io/api/events/update-an-issue/). This directly shapes the tier B mapping below — naive `{"status": "ignored"}` is wrong.
- Tier E resolution uses plain `{"status": "resolved"}`. Tier A wants permanent ignore (`{"status": "ignored"}` with no `statusDetails` is acceptable, since A is "we never want to hear about this again").
- All three target states are reversible from the Sentry UI, so a misclassification is recoverable by hand.

## Architectural Impact

- **New dependencies**: None. `requests` is already imported.
- **Interface changes**: `run_sentry_triage()` signature unchanged (still `() -> dict`). Internal helper `_update_sentry_issue(issue_id, status, status_details=None) -> bool` added.
- **Coupling**: No new module-level imports. Sentry HTTP path centralized in one new helper.
- **Data ownership**: Sentry remains the source of truth for issue state. The reflection becomes a writer (it was read-only for A/B/E before).
- **Reversibility**: Atomically reversible — the apply gate is an env var, default off. Misclassified state changes are also reversible in Sentry's UI (`ignored`/`archived_until_escalating`/`resolved` are all non-destructive).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `SENTRY_AUTH_TOKEN` set | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('SENTRY_AUTH_TOKEN')"` | Sentry API auth (read + write) |

The same token is already used by the existing read path; no new credentials.

## Solution

### Key Elements

- **Apply gate**: Single env var `SENTRY_TRIAGE_APPLY` (default `"0"` = dry-run). When `"1"`, both the existing Class C GitHub-issue filing AND the new A/B/E Sentry state updates go live. The legacy module-level `DRY_RUN` constant is replaced with a function `_apply_enabled()` that reads the env var at call time, so both behaviors flip together.
- **Tier→state map**:
  - A → `PUT {"status": "ignored"}` (permanent ignore)
  - B → `PUT {"status": "ignored", "statusDetails": {"ignoreUntilEscalating": true}}` (auto-unarchive on escalation)
  - E → `PUT {"status": "resolved"}`
- **Update helper**: New `_update_sentry_issue(issue_id, auth_token, status, status_details=None) -> tuple[bool, str | None]`. Returns `(success, error_message)`. One bad ID does not abort the run — errors are collected and reported in the digest.
- **Digest split**: New `## Auto-Actioned` block in the Telegram summary showing A/B/E counts (and any failures) separately from the human-review pile (C + D). When in dry-run, the digest says "[DRY RUN — no Sentry state changes]" exactly as it does today for C.

### Flow

Reflection scheduler fires daily → `run_sentry_triage()` runs → fetch unresolved issues → classify each A–E → for A/B/E: if apply enabled, call `_update_sentry_issue()`, else mark "would action"; for C: existing GitHub-issue filing path (also gated by the same flag); for D: list as before → build digest with auto-actioned vs review-needed counts → send Telegram.

### Technical Approach

- Replace the `DRY_RUN = True` module constant with a function `_apply_enabled() -> bool` that returns `os.environ.get("SENTRY_TRIAGE_APPLY", "0") == "1"`. Update both existing C-tier dry-run check (line 385) and new A/B/E calls to use this function. **Both flip atomically** — operators cannot end up in a partial-live state.
- Add `_TIER_ACTION_MAP: dict[str, dict]` at module level:
  ```python
  _TIER_ACTION_MAP = {
      "A": {"status": "ignored"},
      "B": {"status": "ignored", "statusDetails": {"ignoreUntilEscalating": true}},
      "E": {"status": "resolved"},
  }
  ```
- New helper `_update_sentry_issue(issue_id, auth_token, payload) -> tuple[bool, str | None]`:
  - `PUT https://yudame.sentry.io/api/0/issues/{issue_id}/` with `Authorization: Bearer {token}`, `Content-Type: application/json`, body = payload.
  - 15s timeout. Catches `requests.RequestException` and returns `(False, str(e))`. On non-2xx, returns `(False, f"HTTP {status}: {body[:200]}")`. On success, returns `(True, None)`.
- In `run_sentry_triage()`, after the existing classification loop, iterate A/B/E:
  - If apply disabled: append `"[DRY RUN] would {action} {short_id}"` to findings (mirrors the existing C-tier dry-run wording).
  - If apply enabled: call `_update_sentry_issue()`. Increment per-tier success/failure counters. Append `"Auto-actioned: {short_id} -> {status}"` or `"FAILED: {short_id}: {error}"`.
- Update the Telegram digest section (currently lines 430-446) to add an "Auto-actioned" block before the per-class counts, showing `A/B/E` success and failure counts. When dry-run, show "would auto-action" counts and the existing `[dry run]` footer.
- The summary string (line 417-426) extends with `f", auto-actioned: A={a_ok}/{a_total} B={b_ok}/{b_total} E={e_ok}/{e_total}"` when apply is on; `"[DRY RUN]"` suffix remains when off.

### Why this shape

- **Env var over CLI flag**: The reflection is invoked by the scheduler with no args (`run_sentry_triage()` takes none). A CLI flag on the callable would require a scheduler signature change. An env var matches the existing `DRY_RUN`/`SENTRY_AUTH_TOKEN` pattern and requires zero yaml schema change.
- **One gate, not two**: Splitting the C dry-run flag from a new A/B/E flag would let an operator enable A/B/E auto-action while C silently stays in dry-run — confusing and undocumented partial-live state. One flag, both behaviors.
- **`requests.put()` not MCP**: The reflection already uses `requests` for the read path with the same token. Going through an MCP server route would add a process boundary, a registration step, and an additional failure mode for no functional gain.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] Existing `except requests.RequestException` in `_fetch_unresolved_issues` (line 150) — already logs at warning level. No change.
- [ ] New `_update_sentry_issue` exception handler — test asserts a failed PUT (mocked to raise `RequestException` and to return HTTP 500) is logged at warning level and added to the digest failure list, NOT swallowed silently.
- [ ] Existing `except Exception` in `_send_telegram_notification` (line 310) — unchanged.

### Empty/Invalid Input Handling

- [ ] If the classifier returns zero A/B/E issues, the auto-action path is a no-op and the digest omits the auto-actioned block — test covers this.
- [ ] If `issue["id"]` is missing or empty, `_update_sentry_issue` short-circuits with `(False, "missing issue id")` rather than calling Sentry with a malformed URL — test covers this.

### Error State Rendering

- [ ] Telegram digest renders the failed-update count when failures occur (e.g., `"Auto-actioned: A=5/5 B=3/4 (1 failed) E=2/2"`). Test asserts the failure detail surfaces in the rendered digest.
- [ ] Per-issue failures do NOT abort the loop — test feeds 3 issues where the middle one PUT fails and asserts the third issue is still attempted.

## Test Impact

- [ ] `tests/unit/test_sentry_cli_update.py` — UPDATE only if it asserts on `DRY_RUN`'s module-level value; otherwise unchanged. (Inspection on read showed it covers `sentry-cli` subprocess paths, not the triage callable, so likely no change needed.)
- [ ] `tests/unit/test_sentry_hibernation_filter.py` — UPDATE only if it imports from `reflections.sentry_triage`; otherwise unchanged.
- [ ] `tests/unit/test_sentry_triage_apply.py` (CREATE) — new file covering: tier→state mapping correctness, dry-run no-op behavior (no PUT calls made), apply-mode PUT call with correct payload per tier (incl. `ignoreUntilEscalating` for B), per-issue failure isolation, digest rendering with auto-actioned counts.

## Rabbit Holes

- **Changing classification heuristics** (`_CLASS_A_PATTERNS`, `_CLASS_B_PATTERNS`, `_STALE_DAYS`). Explicitly out of scope per the issue. A bad classification produces a wrong auto-action, but that's a recoverable Sentry-UI click — not worth a heuristic rewrite this round.
- **MCP `update_issue` plumbing**. The reflection already does direct HTTP for reads. Routing writes through an MCP server adds a process boundary and a new failure mode for no functional gain.
- **Adding a `reflections.yaml` config key** for the apply flag. The yaml schema is shared across many reflections; threading a per-callable flag through the scheduler would expand the contract. Env var matches existing patterns and is per-machine controllable (each operator can flip their own machine without a config commit).
- **Rate limiting / batching the Sentry PUTs.** A typical run is ≤200 A/B/E updates. Sentry's documented rate limit comfortably absorbs this serially. No batching needed; do not invent a queue.

## Risks

### Risk 1: Misclassification on the first live run silently archives a real bug

**Impact:** A Class C bug that gets pattern-matched into A or B gets ignored/archived. Operator may not notice for days.
**Mitigation:** Default-off env var. Operator MUST flip `SENTRY_TRIAGE_APPLY=1` deliberately. First live run on one machine only. Digest explicitly lists which issues were auto-actioned by short ID — operator can spot-check the digest against expectations. All three target states are reversible from the Sentry UI.

### Risk 2: Sentry API rate-limits the burst of PUTs

**Impact:** Some updates fail, digest reports the failures, but the reflection still completes.
**Mitigation:** Per-issue failure isolation — one failed PUT does not abort the rest. Failures surface in the digest. Sentry's documented rate limit comfortably absorbs ≤200 serial PUTs per run, so pre-emptive backoff would be dead code.

### Risk 3: Tier B mapping ignores `statusDetails` and gets `archived_forever` instead of `archived_until_escalating`

**Impact:** Tier B issues that should auto-unarchive when they escalate would stay archived forever, masking a real regression.
**Mitigation:** Explicit test asserting the PUT body for tier B includes `{"statusDetails": {"ignoreUntilEscalating": true}}`. Cited in Research section above.

## Race Conditions

No race conditions identified — the reflection runs serially in a single scheduler tick. Each Sentry PUT is independent; ordering does not matter. No shared mutable state across calls.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1469] Changing the A–E classification heuristics — the issue body explicitly defers this; the recon confirms it.
- Nothing else deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this feature is purely internal to one Python file and one optional env var. The env var lives in `~/Desktop/Valor/.env` per the standard secrets workflow; no new propagation step.

## Agent Integration

No agent integration required — this is a scheduled reflection invoked by the worker, not a tool exposed to the conversational agent. The Telegram digest already reaches the operator via `valor-telegram send` (unchanged path).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sentry-triage.md` (or create if absent) describing the apply gate and tier→state mapping. Include the `SENTRY_TRIAGE_APPLY=1` enablement instruction.
- [ ] Add entry to `docs/features/README.md` index table if not already present for sentry-triage.

### Inline Documentation
- [ ] Module docstring at the top of `reflections/sentry_triage.py` updated to mention the apply flag and the tier→state map.
- [ ] Docstring on the new `_update_sentry_issue` helper explaining the `statusDetails` quirk for tier B.

## Success Criteria

- [ ] Dry-run mode (default, `SENTRY_TRIAGE_APPLY` unset or `"0"`) reports per-tier counts and what *would* be auto-actioned, with zero PUT calls to Sentry.
- [ ] `SENTRY_TRIAGE_APPLY=1` commits state changes: A → `ignored`, B → `ignored` + `ignoreUntilEscalating`, E → `resolved`.
- [ ] Digest distinguishes auto-actioned counts from the human-review pile (C + D).
- [ ] Per-issue PUT failures are caught, logged at warning level, and surfaced in the digest; one bad ID does not abort the run.
- [ ] New `tests/unit/test_sentry_triage_apply.py` covers tier→state mapping (incl. `ignoreUntilEscalating` for B), dry-run no-op behavior, apply-mode per-tier PUT payloads, and per-issue failure isolation.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (sentry-triage-apply)**
  - Name: sentry-builder
  - Role: Implement apply gate, tier→state map, `_update_sentry_issue` helper, digest changes
  - Agent Type: builder
  - Resume: true

- **Test Engineer (sentry-triage-tests)**
  - Name: sentry-test-engineer
  - Role: Create `tests/unit/test_sentry_triage_apply.py` covering all Failure Path Test Strategy and Success Criteria items
  - Agent Type: test-engineer
  - Resume: true

- **Validator (sentry-triage)**
  - Name: sentry-validator
  - Role: Verify implementation matches plan, all tests pass, lint clean
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build apply gate and update helper
- **Task ID**: build-apply-gate
- **Depends On**: none
- **Validates**: tests/unit/test_sentry_triage_apply.py (create)
- **Informed By**: Research finding on `ignoreUntilEscalating` quirk
- **Assigned To**: sentry-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `DRY_RUN = True` constant with `_apply_enabled()` function reading `SENTRY_TRIAGE_APPLY` env var
- Update existing C-tier dry-run check (currently `if DRY_RUN:` near line 385) to use `_apply_enabled()`
- Add `_TIER_ACTION_MAP` module-level dict with A/B/E payloads (B includes `statusDetails.ignoreUntilEscalating: true`)
- Add `_update_sentry_issue(issue_id, auth_token, payload) -> tuple[bool, str | None]` helper with 15s timeout and `requests.RequestException` handling
- In `run_sentry_triage()`, after classification, iterate A/B/E; in apply mode call the helper, in dry-run mode append "[DRY RUN] would action" findings (mirror existing C-tier wording)
- Update digest construction (lines 430-446) to add auto-actioned counts block before the per-class lines; include failure count when failures occurred
- Update module docstring and add docstring to `_update_sentry_issue` explaining the `statusDetails` quirk

### 2. Build tests
- **Task ID**: build-tests
- **Depends On**: none
- **Validates**: tests/unit/test_sentry_triage_apply.py
- **Assigned To**: sentry-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_sentry_triage_apply.py`
- Test: `_apply_enabled()` returns False when env unset, True only when `SENTRY_TRIAGE_APPLY=1`
- Test: tier→state mapping — assert PUT payload for A is `{"status": "ignored"}`, for B is `{"status": "ignored", "statusDetails": {"ignoreUntilEscalating": true}}`, for E is `{"status": "resolved"}` (use `requests_mock` or `unittest.mock.patch` on `requests.put`)
- Test: dry-run mode makes zero PUT calls (assert mock not called)
- Test: apply mode with mixed success/failure — middle issue's PUT raises, third issue is still attempted
- Test: missing/empty `issue["id"]` short-circuits with `(False, "missing issue id")` and zero PUT calls
- Test: digest output string contains the auto-actioned counts and failure detail

### 3. Validate implementation
- **Task ID**: validate-implementation
- **Depends On**: build-apply-gate, build-tests
- **Assigned To**: sentry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sentry_triage_apply.py -v`
- Run `python -m ruff check reflections/sentry_triage.py tests/unit/test_sentry_triage_apply.py`
- Run `python -m ruff format --check reflections/sentry_triage.py tests/unit/test_sentry_triage_apply.py`
- Verify `_apply_enabled()` is used in both the C-tier and A/B/E branches (grep)
- Verify tier B payload literally contains `ignoreUntilEscalating` (grep)
- Report pass/fail

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-implementation
- **Assigned To**: sentry-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update or create `docs/features/sentry-triage.md` describing the apply gate, tier→state map, and the `SENTRY_TRIAGE_APPLY=1` enablement
- Add entry to `docs/features/README.md` index table if not already present
- Verify module docstring on `reflections/sentry_triage.py` mentions the apply flag

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: sentry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -k sentry -v`
- Run `python -m ruff check . && python -m ruff format --check .`
- Confirm all Success Criteria checkboxes can be checked
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Sentry triage tests pass | `pytest tests/unit/test_sentry_triage_apply.py -v` | exit code 0 |
| Lint clean | `python -m ruff check reflections/sentry_triage.py tests/unit/test_sentry_triage_apply.py` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/sentry_triage.py tests/unit/test_sentry_triage_apply.py` | exit code 0 |
| Apply gate present | `grep -q '_apply_enabled' reflections/sentry_triage.py` | exit code 0 |
| Tier B uses ignoreUntilEscalating | `grep -q 'ignoreUntilEscalating' reflections/sentry_triage.py` | exit code 0 |
| DRY_RUN constant removed | `grep -E '^DRY_RUN = ' reflections/sentry_triage.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Telegram digest in apply mode** — current dry-run digest includes the `[dry run — no GitHub issues filed]` footer. Should the live-mode digest get an explicit `[LIVE — Sentry state changes applied]` header so operators can tell at a glance that this was an apply run? (Recommendation: yes, symmetric to the dry-run footer, easy to add.)
2. **First-live-run safety** — should the very first apply-mode run be capped (e.g., cap A+B+E updates at 50 issues for the first run, then unbounded thereafter)? Or trust the reversibility of Sentry states and the digest audit? (Recommendation: no cap; reversibility is the safety net, and a cap adds a one-time flag that becomes stale code.)
