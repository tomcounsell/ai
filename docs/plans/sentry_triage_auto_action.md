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

The `sentry-issue-triage` reflection (`reflections/sentry_triage.py`, scheduled daily via `~/Desktop/Valor/reflections.yaml`) pulls every unresolved Sentry issue for the org, classifies each into one of five tiers (A-E), and sends a Telegram digest. Tiers A (noise), B (transient), and E (stale) are *labeled* "to ignore / archive / resolve" but no Sentry state change is performed — the same issues reappear in every subsequent digest, and the reader has no way to tell the auto-actionable pile apart from the human-review pile without re-reading the issue titles.

**Current behavior:** All 244 issues from the last run were classified and reported. ~90 (tiers C+D) needed human eyes; the other ~154 were re-reported noise that the reflection claimed it was handling but wasn't.

**Desired outcome:** Tiers A, B, and E get their Sentry issue state mutated on the same run that classifies them — A → `ignored`, B → `archived_until_escalating`, E → `resolved`. The Telegram digest reports auto-actioned counts separately from the human-review pile. Dry-run remains the default; live mutation is gated by an env-var signal that also flips tier C's existing dry-run (so the two pipelines stay coherent).

## Freshness Check

**Baseline commit:** `aac0912c2492340768b8386d08090f3f9f27ad85`
**Issue filed at:** 2026-05-26T07:11:36Z (~6 minutes before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**
- `reflections/sentry_triage.py:36` — `DRY_RUN = True` module constant — still holds.
- `reflections/sentry_triage.py:172-205` — `_classify_issue()` returns one of A|B|C|D|E with a reason string — still holds.
- `reflections/sentry_triage.py:108-119` — `_get_auth_token()` reads `SENTRY_AUTH_TOKEN` from env or `.env` — still holds.
- `reflections/sentry_triage.py:314` — `run_sentry_triage()` signature takes no args, returns `{status, findings, summary, duration}` dict — still holds.
- `reflections/sentry_triage.py:385-401` — DRY_RUN gates only the tier-C `_file_github_issue` call — still holds.
- `~/Desktop/Valor/reflections.yaml:134` — `sentry-issue-triage` callable wired daily — still holds.

**Cited sibling issues/PRs re-checked:**
- PR #916 (issue #841) — merged 2026-04-13, added Sentry CLI. Did not touch `_classify_issue` or the triage state-mutation path; orthogonal to this work.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-05-26T07:11:36Z" -- reflections/sentry_triage.py config/reflections.yaml` is empty.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/sentry_hibernation_filter.md` exists but addresses a different concern (filter-out logic earlier in the pipeline) and does not modify auto-action semantics. No overlap that requires coordination.

## Prior Art

- **PR #916 / issue #841**: "feat: sentry-cli integration — install via /update, CLI-only agent, opt-in reflection" — merged 2026-04-13. Added the Sentry CLI install path and opt-in reflection wiring. Did not change classification or state-mutation; this plan extends the same reflection along an orthogonal axis (state mutation, not classification).
- **`docs/plans/sentry_hibernation_filter.md`**: A separate plan on filter-out heuristics for the same reflection. Not yet shipped; doesn't touch the apply pipeline this plan introduces.

No prior attempts at auto-action for tiers A/B/E exist. This is greenfield in the strictest sense — the labels existed but no mutation code does.

## Research

No relevant external findings — Sentry's `PUT /api/0/issues/{issue_id}/` with `{"status": <state>}` is documented in the issue body (link: https://docs.sentry.io/product/issues/states-triage/). Valid states for our map (`ignored`, `archived_until_escalating`, `resolved`) are confirmed by the Recon Summary against the live API. No library or ecosystem question to investigate.

## Data Flow

1. **Entry point**: Worker scheduler invokes `reflections.sentry_triage.run_sentry_triage()` (no args).
2. **Fetch**: `_fetch_unresolved_issues()` paginates `GET /api/0/organizations/{org}/issues/?query=is:unresolved` and returns a flat list of dicts containing `id`, `shortId`, `title`, `lastSeen`, `count`, `project.slug`.
3. **Classify**: For each issue, `_classify_issue()` returns `(tier_letter, reason)` — unchanged by this work.
4. **Branch on apply gate**: Read `SENTRY_TRIAGE_APPLY` env var once at the top of `run_sentry_triage()`. The same flag will also override the existing module-level `DRY_RUN` for tier C, so both pipelines (state mutation for A/B/E and GitHub-issue filing for C) flip together.
5. **State mutation (A/B/E, apply mode only)**: For each tier-A/B/E issue, call a new helper `_update_sentry_status(issue_id, target_status, auth_token, org_slug)` that wraps `PUT /api/0/issues/{issue_id}/` with `{"status": target}`. Per-call try/except logs the failure and continues; one bad ID does not abort the run.
6. **Report aggregation**: Tally per-tier `auto_actioned` and `auto_action_failed` counts. The digest distinguishes "auto-actioned A: 120, B: 22, E: 12" from "needs review: C=68, D=22".
7. **Telegram output**: `_send_telegram_notification()` (unchanged) receives a digest body that now leads with auto-actioned summary and ends with the human-review pile.

## Architectural Impact

- **New dependencies**: None. `requests` is already imported and used for reads; the same module handles writes via `requests.put()`.
- **Interface changes**: `run_sentry_triage()` signature is unchanged (no args, same return dict shape, with the addition of `auto_actioned` and `auto_action_failed` keys in the per-tier breakdown so callers/tests can introspect counts).
- **Coupling**: No new coupling. The reflection already owns the Sentry HTTP client and the Telegram notifier; we are extending the same module along its existing axes.
- **Data ownership**: Sentry remains the source of truth for issue state. We are calling a documented mutation endpoint, not creating a parallel state store.
- **Reversibility**: All three target states (`ignored`, `archived_until_escalating`, `resolved`) are reversible in the Sentry UI. The apply gate defaults to off, so the first live run is opt-in and observable in the digest before the operator decides to keep it on.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Single-file change to `reflections/sentry_triage.py`, plus a new test file `tests/unit/test_sentry_triage_apply.py`. The classification logic and Telegram notifier are out of scope. The apply gate is an env var (no yaml schema change).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `SENTRY_AUTH_TOKEN` configured | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('SENTRY_AUTH_TOKEN')"` | Required for Sentry API auth (already used by the reflection's reads) |

The token already has issue-update permission on the org (confirmed in the Recon Summary). No new credential, no new scope grant.

## Solution

### Key Elements

- **Apply gate**: A module-level env-var read at the top of `run_sentry_triage()` produces a single boolean `apply_mode`. The existing module-level `DRY_RUN` constant remains as the fallback default (False when `apply_mode` is True, True otherwise). Both tier-C filing and the new A/B/E state mutation respect the same gate.
- **Tier-to-status map**: A module-level constant dict `_TIER_TO_SENTRY_STATUS = {"A": "ignored", "B": "archived_until_escalating", "E": "resolved"}`. C and D are deliberately not in the map.
- **`_update_sentry_status()` helper**: Wraps `PUT /api/0/issues/{issue_id}/` with `{"status": target}`. Returns `(ok: bool, error: str | None)`. Catches `requests.RequestException` and non-2xx responses; logs at warning level; never raises.
- **Per-issue isolation**: Each tier-A/B/E issue is processed in its own try/except inside the iteration loop. A failure on one ID logs and continues — the run completes and the digest reports the partial result.
- **Reporting**: The digest gains an "Auto-actioned" section above the "Needs review" pile. Counts are per-tier and distinguish successes from failures. The `findings` list (returned from the callable) likewise distinguishes auto-actioned items from would-be-actioned items.

### Flow

Worker scheduler tick → `run_sentry_triage()` → read `SENTRY_TRIAGE_APPLY` env var → fetch unresolved → classify each → for each A/B/E issue: if apply_mode, `_update_sentry_status()` and tally; else, count as "would action" → for each C issue (existing flow): if apply_mode, file GitHub issue; else, dry-run log → assemble digest (auto-actioned counts ↑, human-review counts ↓) → Telegram notify → return summary dict.

### Technical Approach

- **Gating signal**: `SENTRY_TRIAGE_APPLY` env var. Read once at function entry with `os.environ.get("SENTRY_TRIAGE_APPLY", "").lower() in ("1", "true", "yes")`. The module-level `DRY_RUN = True` constant stays as documentation of the safe default but is no longer the sole switch — `apply_mode = (not DRY_RUN) or env_says_apply` keeps backward compatibility with anyone flipping the constant in a debug shell while making the env var the production lever.
- **Tier-to-status mapping**: Constant dict at module scope, near `_CLASS_A_PATTERNS` so the reader sees the full classification-and-action picture in one place.
- **HTTP path**: `requests.put(f"{SENTRY_API_BASE}/issues/{issue_id}/", headers={"Authorization": f"Bearer {auth_token}"}, json={"status": target}, timeout=15)`. Same auth pattern as the existing GET; no new client setup.
- **Failure semantics**: `_update_sentry_status()` returns `(False, reason)` on any non-2xx or exception. The caller appends to a per-tier `auto_action_failed` list (with short_id and reason) so the digest surfaces failures explicitly rather than silently. The run never aborts on a single failure.
- **Test surface**: Three test classes in `tests/unit/test_sentry_triage_apply.py`:
  - `test_tier_to_status_mapping`: assert the dict literal.
  - `test_dry_run_does_not_mutate`: monkeypatch `requests.put` to raise if called; run with apply_mode=False; assert no `PUT` was attempted and the digest reflects "would action" counts.
  - `test_apply_mode_calls_put_per_issue`: monkeypatch `requests.put` to a fake that records calls; run with apply_mode=True against a synthetic classified-issues fixture; assert one `PUT` per A/B/E issue with the correct payload.
  - `test_per_issue_failure_isolation`: fake `requests.put` raises on the second of three issues; assert the run completes, reports one failure and two successes, and returns `status="ok"` (not "error" — a partial failure is not a run failure).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_update_sentry_status()` is a new function. Its `except requests.RequestException` branch must have a test asserting (a) the function returns `(False, <reason>)`, (b) a `logger.warning` line is emitted, and (c) the caller continues to the next issue.
- [ ] Existing `_send_telegram_notification()` exception handlers are untouched by this work.

### Empty/Invalid Input Handling
- [ ] If `_fetch_unresolved_issues()` returns an empty list, the existing early-return path stays in place and no PUTs are issued. Covered by an additional test `test_apply_mode_empty_fetch_no_calls`.
- [ ] If an issue dict is missing the `id` field, `_update_sentry_status()` short-circuits and counts the issue as a failure (logged, not raised). Covered by `test_apply_mode_missing_id_counted_as_failure`.

### Error State Rendering
- [ ] The Telegram digest renders auto-action failure counts explicitly (`"Auto-action failed: A=1, B=0, E=0"`) rather than hiding them. Covered by inspecting the assembled digest string in `test_digest_includes_failure_counts`.

## Test Impact

- [ ] `tests/unit/test_sentry_triage_apply.py` — CREATE: new file covering the apply-mode behaviors enumerated above. Five test functions.

No existing tests affected — `reflections/sentry_triage.py` has no dedicated test file today (confirmed in the Recon Summary; existing `tests/unit/test_sentry_cli_update.py` and `tests/unit/test_sentry_hibernation_filter.py` cover orthogonal modules). The change is additive to the reflection's behavior with no signature change to `run_sentry_triage()`, so callers/scheduler integration tests are unaffected.

## Rabbit Holes

- **Reworking the classification heuristics**: Explicitly out of scope per the issue. The A/B/E pattern lists and the E stale-threshold are frozen for this PR.
- **Adding a yaml schema field for the apply gate**: Tempting but adds a schema migration and a propagation step across machines. An env var is the right size for a per-machine, opt-in toggle and matches the existing `DRY_RUN` constant pattern.
- **Switching to a Sentry SDK or the MCP `update_issue` route**: The Recon Summary explicitly flags `requests.put()` as the lower-friction path with the same auth. Don't introduce a new dependency surface for a four-line mutation.
- **Batching the state updates**: Sentry's bulk-update endpoint exists but is per-project, requires a different payload, and complicates failure isolation (one bad ID can spoil the batch). Per-issue PUTs are O(154) on the worst-observed digest and complete well within the daily reflection's runtime budget.

## Risks

### Risk 1: Misclassification produces an irreversible-feeling resolve on a live issue
**Impact:** A tier-E "stale" mark on an issue that was actually pre-incident-quiet would auto-resolve it. The next event re-opens it (Sentry re-opens resolved issues on new events by default), but a human might still be confused by the state churn.
**Mitigation:** Default apply mode to off; first live run is operator-supervised. Digest shows per-tier auto-action counts so the first apply run is auditable. Sentry's "resolve" is reversible in one click. The E classifier requires `days_old > 30 AND event_count <= 50` — narrow enough that a misclassification rate above zero is detectable from the digest's auto-actioned list.

### Risk 2: Per-issue PUT call rate exceeds Sentry's rate limit
**Impact:** Mid-run rate-limiting would cause a cascade of `429` responses, and (depending on Sentry's policy) could affect the reflection's read pipeline on subsequent runs.
**Mitigation:** Worst observed digest is 244 issues, of which ~154 are tiers A/B/E. Sentry's documented per-token rate limit is in the hundreds of requests per minute. A 154-PUT burst is within budget. If we hit a 429, `_update_sentry_status()` returns `(False, "rate-limited")` and the digest surfaces the count; the next daily run picks up the remainder. We can add `time.sleep(0.05)` between PUTs if the first live run shows rate-limit failures, but we don't pre-empt with a sleep on first ship.

### Risk 3: Operator flips the apply gate on one machine but not another (multi-machine config drift)
**Impact:** The reflection is scheduled per-machine. If two machines both run it, only the one with `SENTRY_TRIAGE_APPLY=1` actually mutates. The other keeps reporting "would action" forever, which is confusing but not broken.
**Mitigation:** Existing pattern — `reflections.yaml` is iCloud-synced, env vars are per-machine. Documented in the feature doc. The apply gate is intentionally per-machine so the operator can stage the rollout (enable on one machine, verify a week, enable elsewhere).

## Race Conditions

No race conditions identified — the reflection is a single-threaded function invoked once per daily tick by the worker scheduler. The PUT operations are sequential, each one's response is awaited before the next starts. No shared mutable state crosses iterations.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1469] Changing tier C's GitHub-issue-filing behavior beyond unifying its gate with the new apply mode — already noted in the issue as out of scope; the unification is a one-line `apply_mode` substitution and is in scope, but the C dedup/title logic is untouched.
- Nothing else deferred — the apply gate, the tier-to-status map, the helper, the per-issue isolation, the digest format change, and the test coverage are all in scope for this plan.

## Update System

No update system changes required. The change is internal to `reflections/sentry_triage.py`. The env var `SENTRY_TRIAGE_APPLY` is opt-in per machine and does not need to be added to `.env.example` (it has a safe default of off; documenting it in the feature doc is sufficient). No new dependencies, no config-file migrations, no install-script changes.

## Agent Integration

No agent integration required. The reflection is invoked by the worker scheduler, not by the agent. The agent has no need to call this code path directly — operators flip the env var via shell. No MCP server, no `.mcp.json` change, no bridge import. If an operator later wants the agent to be able to flip the gate via Telegram, that is a follow-up — not part of this plan.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/sentry-triage-auto-action.md` describing the apply gate, the tier-to-status map, the env-var signal, and the rollout pattern (enable on one machine, observe digest, expand).
- [ ] Add entry to `docs/features/README.md` index table under reflections.

### External Documentation Site
No external documentation site changes — this repo does not use Sphinx/MkDocs/RTD.

### Inline Documentation
- [ ] Module docstring at the top of `reflections/sentry_triage.py` gains a paragraph describing the apply gate and the tier-to-status map.
- [ ] `_update_sentry_status()` gets a full docstring covering parameters, return shape, and failure semantics.
- [ ] The `_TIER_TO_SENTRY_STATUS` constant gets an inline comment naming the Sentry doc URL for the three target states.

## Success Criteria

- [ ] `SENTRY_TRIAGE_APPLY` env var unset → reflection runs in dry-run mode; no `requests.put()` calls; digest reports per-tier "would action" counts.
- [ ] `SENTRY_TRIAGE_APPLY=1` → reflection mutates each tier-A/B/E issue via `PUT /api/0/issues/{id}/`; digest reports per-tier auto-actioned counts plus any failures.
- [ ] A failure on a single issue (network error, 404, 429) does not abort the run; the digest reports the failure count.
- [ ] On a representative live run with the gate on, the human-review portion of the digest (C+D) is materially smaller than the dry-run "all classified" total (the issue's baseline expectation: 244 → ~90).
- [ ] New test file `tests/unit/test_sentry_triage_apply.py` covers tier mapping, dry-run no-op, apply-mode PUTs, per-issue failure isolation, empty-fetch path, missing-ID path, and digest failure rendering.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `docs/features/sentry-triage-auto-action.md` exists and is linked from `docs/features/README.md`.

## Team Orchestration

### Team Members

- **Builder (triage-apply)**
  - Name: triage-apply-builder
  - Role: Implement the apply gate, the tier-to-status map, the `_update_sentry_status()` helper, and the digest format changes in `reflections/sentry_triage.py`. Add the new test file.
  - Agent Type: builder
  - Resume: true

- **Validator (triage-apply)**
  - Name: triage-apply-validator
  - Role: Verify all success criteria, run the new test file, confirm dry-run remains the default behavior, inspect the assembled digest format.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: triage-apply-docs
  - Role: Create `docs/features/sentry-triage-auto-action.md` and update the features index.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement apply gate and state-mutation pipeline
- **Task ID**: build-triage-apply
- **Depends On**: none
- **Validates**: tests/unit/test_sentry_triage_apply.py (create)
- **Assigned To**: triage-apply-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_TIER_TO_SENTRY_STATUS` module constant near the classification pattern lists.
- Add `_update_sentry_status(issue_id, target_status, auth_token, org_slug)` helper. Returns `(ok, error_reason)`. Uses `requests.put`, 15s timeout, logs warnings on failure, never raises.
- In `run_sentry_triage()`, read `SENTRY_TRIAGE_APPLY` env var once at function entry. Compute `apply_mode = env_says_apply or (not DRY_RUN)`.
- Replace the tier-C `if DRY_RUN:` branch with `if not apply_mode:` so the same gate controls both paths.
- After the existing per-tier reporting loops, add an iteration over `classified["A"] + classified["B"] + classified["E"]`. For each, if `apply_mode`, call `_update_sentry_status(issue["id"], _TIER_TO_SENTRY_STATUS[cls], ...)` and tally success/failure per tier. If not `apply_mode`, the existing "would action" reporting stays in place.
- Update the digest string and the Telegram message body to include per-tier auto-actioned and auto-action-failed counts.
- Create `tests/unit/test_sentry_triage_apply.py` with the seven tests enumerated in Technical Approach and Failure Path Test Strategy.

### 2. Validate implementation
- **Task ID**: validate-triage-apply
- **Depends On**: build-triage-apply
- **Assigned To**: triage-apply-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sentry_triage_apply.py -v` and confirm all tests pass.
- Run `python -m ruff check reflections/sentry_triage.py tests/unit/test_sentry_triage_apply.py` and `python -m ruff format --check` on the same files.
- Manually inspect the assembled digest string in a dry-run scenario (e.g. via a small `python -c` invocation against a synthetic fixture) to confirm format readability.
- Verify `run_sentry_triage()` signature is unchanged (no args, returns dict with `status`, `findings`, `summary`, `duration`).

### 3. Document the feature
- **Task ID**: document-triage-apply
- **Depends On**: validate-triage-apply
- **Assigned To**: triage-apply-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sentry-triage-auto-action.md` covering: what the feature does, the env-var signal, the tier-to-status map, the per-machine rollout pattern, the reversibility argument, and the failure-mode behavior.
- Add an entry to `docs/features/README.md` under the reflections section.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-triage-apply
- **Assigned To**: triage-apply-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the full unit-test suite for the file: `pytest tests/unit/test_sentry_triage_apply.py -v`.
- Run `python -m ruff check . && python -m ruff format --check .` over the repo.
- Confirm every Success Criteria checkbox is satisfied.
- Confirm `docs/features/sentry-triage-auto-action.md` is linked from the index.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New tests pass | `pytest tests/unit/test_sentry_triage_apply.py -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/sentry_triage.py tests/unit/test_sentry_triage_apply.py` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/sentry_triage.py tests/unit/test_sentry_triage_apply.py` | exit code 0 |
| Feature doc exists | `test -f docs/features/sentry-triage-auto-action.md && echo ok` | output contains ok |
| Feature doc indexed | `grep -l "sentry-triage-auto-action" docs/features/README.md` | exit code 0 |
| Apply gate referenced | `grep -n "SENTRY_TRIAGE_APPLY" reflections/sentry_triage.py` | exit code 0 |
| Tier-to-status map present | `grep -n "_TIER_TO_SENTRY_STATUS" reflections/sentry_triage.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None. The Recon Summary in the source issue is unusually thorough and resolves the only ambiguity (CLI flag vs. env var vs. yaml schema change) explicitly in favor of an env var. The risk surface is small, the change is reversible at every step, and the test plan covers the four meaningful failure modes (dry-run no-op, apply-mode happy path, per-issue failure, empty fetch).
