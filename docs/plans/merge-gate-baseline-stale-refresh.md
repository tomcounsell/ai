---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-07
tracking: https://github.com/tomcounsell/ai/issues/1933
last_comment_id:
---

# Merge-Gate Baseline: Stale Refresh + Drafter Test Rot

## Problem

The merge gate (`/do-merge` → `scripts/baseline_gate.py`) classifies each test failure on a PR branch as either **pre-existing** (failing on `main` too — non-blocking) or a **regression** (new to the PR — blocking) by diffing against a recorded snapshot of `main`'s failures: `data/main_test_baseline.json`.

**Current behavior:**
- The baseline was last generated **2026-05-08 (~60 days ago)**. At PR #1930's merge gate it produced **40 false regression flags** — tests that fail on `main` for reasons unrelated to the PR but absent from the two-month-old baseline. The baseline-verifier subagent had to re-run each against `main` to unblock the merge, an entire extra classification pass.
- At least one of those `main` failures is genuine stale-test rot, not a product bug: `tests/integration/test_chat_message_log_e2e.py:18` does `from bridge.message_drafter import _build_draft_prompt` at module level (so the whole module fails collection with `ImportError`), and `tests/integration/test_agent_session_lifecycle.py:417` imports the same symbol inside a test. `_build_draft_prompt` was **deleted** in PR #1685 / commit `ef452704` when the drafter was repositioned from an LLM rewriter to a verbatim pass-through + validation filter. Regenerating the baseline without fixing this would bake a permanently-broken test into the "expected failures" set.
- The refresh tool itself has a known defect (#1853): `scripts/_baseline_common.py::parse_junitxml` raises on any `<testcase>` missing a `name` attribute, and the caller discards the **entire run**. On the 2026-07-02 refresh this silently degraded a 3-run refresh to a 1-run baseline (`runs: 1`, `flaky: 0`) — with one run, every transient flake is misclassified `real` and permanently baselined.

**Desired outcome:**
1. Neither test references the deleted `_build_draft_prompt`; the module collects cleanly.
2. `refresh_test_baseline.py` produces a correct multi-run baseline (the nameless-`<testcase>` defect no longer discards whole runs, and a degraded <2-run refresh fails loudly instead of writing a flaky-blind baseline that looks normal).
3. `data/main_test_baseline.json` regenerated from current `main` with a fresh `generated_at` and `runs` reflecting the intended multi-run refresh.
4. The baseline can no longer go silently stale for 60 days.

## Freshness Check

**Baseline commit:** `8353d6ec` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-07T05:38:27Z (same day)
**Disposition:** Unchanged

**File:line references re-verified:**
- `tests/integration/test_chat_message_log_e2e.py:18` — module-level `from bridge.message_drafter import _build_draft_prompt` — still present; also used at lines 97, 120, 135, 143.
- `tests/integration/test_agent_session_lifecycle.py:417` — test-local `from bridge.message_drafter import _build_draft_prompt` in `test_build_prompt_includes_session_context` — still present.
- `bridge/message_drafter.py` — `_build_draft_prompt` confirmed **absent** from all source (`grep -rn _build_draft_prompt bridge/ agent/ worker/ tools/` → no hits). Current public API: `validate_telegram`, `validate_email`, `extract_artifacts`, `format_violations`, `_compose_structured_draft`, `draft_message`. The drafter no longer reads `chat_message_log`; only `bridge/telegram_relay.py` writes it (`_append_outbound_chat_log` / `append_chat_log`).
- `scripts/_baseline_common.py::parse_junitxml` — raises `JunitxmlParseError` on `<testcase>` with no `name`; caller in `refresh_test_baseline.py` discards the run. Confirmed present.
- `scripts/baseline_gate.py` — `STALENESS_THRESHOLD = timedelta(days=14)` and `format_staleness_warning()` present; `main()` emits the warning to **stderr** and into the verdict JSON as `staleness_warning`. So a soft-warn already exists and is wired.
- `scripts/_baseline_post_merge_update.py` — writes the decayed baseline via `apply_decay` but does **not** refresh `generated_at` (correctly — decay does not re-observe `main`), so staleness accumulates even as merges land. This is by design, not a bug.

**Cited sibling issues/PRs re-checked:**
- #1853 — OPEN; junitxml discard defect confirmed live. Folded into this plan (see Scope Decision).
- PR #1685 / commit `ef452704` — merged; removed `_build_draft_prompt`. Confirmed.
- PR #1930 — merged 2026-07-07; site of the 40 false flags.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/completed/merge-gate-baseline-refresh.md` (the original baseline system, #1084/#1154) — completed; this plan maintains it, no conflict.

## Prior Art

- **PR #1154 (#1084)**: "categorise merge-gate baseline + refresh tool" — introduced the schema-v2 categorised baseline (`real`/`flaky`/`hung`/`import_error`) and `refresh_test_baseline.py` / `baseline_gate.py`. This is the system being maintained here. Succeeded; still the current contract (`docs/features/merge-gate-baseline.md`).
- **PR #1685 (commit `ef452704`)**: repositioned the message drafter to verbatim pass-through + validation filter — removed `_build_draft_prompt`. Succeeded; this plan cleans up the two orphaned test imports it left behind.
- No prior attempt has addressed the drafter test rot or #1853.

## Research

No relevant external findings — proceeding with codebase context. This is purely internal (test suite, an internal Python refresh script, and a gitignored machine-local artifact).

## Data Flow

1. **Entry point**: `/do-merge` (or `/do-test`) invokes `scripts/baseline_gate.py`, passing the baseline path and the PR's junitxml.
2. **`load_baseline`**: reads `data/main_test_baseline.json` (gitignored, machine-local), normalises to schema-v2.
3. **`format_staleness_warning`**: compares `generated_at` to `now`; if > 14 days, emits a warning to stderr + verdict JSON (does not block).
4. **`compute_gate_verdict`**: diffs PR failures against baseline categories; `real`/`hung`/`import_error` node IDs new to the PR block the merge.
5. **Baseline production** (`scripts/refresh_test_baseline.py`): runs pytest N× on the current checkout (intended: clean `main`), each run written to `--junitxml`, parsed by `_baseline_common.parse_junitxml`, aggregated across surviving runs into categories, written to `data/main_test_baseline.json` with `generated_at` + `runs`.
6. **Output**: a stale or single-run baseline at step 5 corrupts the classification at step 4 — the failure mode this plan repairs.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `parse_junitxml` gains graceful handling of a nameless `<testcase>` (returns a classified entry / skips the element) instead of raising and discarding the run. `refresh_test_baseline.py` gains a loud-failure path when usable runs < 2.
- **Coupling**: unchanged. The staleness-detector reflection is an additive, read-only callable.
- **Data ownership**: unchanged. `data/main_test_baseline.json` stays gitignored and machine-local.
- **Reversibility**: high — test deletions and a parser hardening; the reflection is opt-in per machine via `reflections.yaml`.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (routing + sign-off), code reviewer (PR review gate)

**Interactions:**
- PM check-ins: 1-2 (baseline regeneration is a heavy full-suite op — confirm machine quiescence / timing)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo test deps installed | `python -c "import pytest, xml.etree.ElementTree"` | Run pytest + parse junitxml |
| Quiescent machine for regen | `curl -s localhost:8500/dashboard.json \| python3 -c "import json,sys; print(len([s for s in json.load(sys.stdin)['sessions'] if s.get('status')=='active']))"` | Full-suite regen must not collide with active sessions on Redis |

## Solution

### Scope Decision — fold in #1853

Yes. The issue's solution sketch is explicit that regenerating through the buggy script yields a lower-quality baseline. Fixing #1853 **before** regeneration is the correct order: it is the difference between a real 3-run flaky-aware baseline and a 1-run flaky-blind one. #1853 is small and directly on the critical path, so it is folded into this change rather than deferred.

### Staleness Answer (required by acceptance criterion #4)

**Decision: keep the existing 14-day soft-warn; do NOT hard-block; do NOT add a scheduled full-suite auto-regeneration; DO add a cheap age-only scheduled *detector*.**

Rationale:
- **Keep soft-warn, no hard-block.** `data/main_test_baseline.json` is machine-local, and regenerating it is a heavy full-suite run. Hard-blocking merges because a local file is old would halt *all* merges on a machine until someone runs a multi-minute 3× suite refresh — strictly worse than a warning. The soft-warn already fires and is actionable (it did fire at PR #1930; the cost there was a manual re-classification pass, not a missed regression). `format_staleness_warning` is already emitted by `baseline_gate.main()` to stderr + the verdict JSON — no change needed to the warn itself.
- **Explicit no-go on scheduled full-suite auto-regeneration.** A weekly full-suite 3× run as a reflection on a live worker machine reintroduces exactly the Redis-collision / memory-thrash hazard the project's own rails forbid ("full-suite runs from parallel worktrees collide on Redis state"), for a per-machine artifact of modest value. Rejected.
- **Add a cheap age-only detector reflection.** The real gap is that nothing *surfaces* staleness on a schedule between merges — it silently drifted to 60 days. A new reflection `test-baseline-refresh-check` reads `data/main_test_baseline.json`, computes `now - generated_at`, and emits a warning-status finding when it exceeds `STALENESS_THRESHOLD` (reusing the constant). **It runs no tests** — it only reads a small JSON file — so it carries none of the collision hazard. It turns today's silent staleness into a visible weekly nudge; the operator then runs the (now #1853-corrected) `refresh_test_baseline.py` when the machine is quiet.

### Key Elements

- **Drafter test cleanup** (`tests/integration/test_chat_message_log_e2e.py`, `tests/integration/test_agent_session_lifecycle.py`): remove all references to the deleted `_build_draft_prompt`; delete the tests that asserted its now-removed LLM-rewrite-prompt behavior; keep tests that exercise still-live behavior (Path-B chat-log recording via `_append_outbound_chat_log`).
- **`parse_junitxml` hardening** (`scripts/_baseline_common.py`): a `<testcase>` with no `name` is classified from its children (an `<error>` child → `collection_error`/`import_error`; otherwise skip that single element) rather than raising and forcing the caller to discard the whole run.
- **Refresh loud-failure** (`scripts/refresh_test_baseline.py`): when surviving/usable runs < 2, emit an explicit `WARNING` in the summary block AND exit non-zero, so a flaky-blind degraded baseline is never written silently.
- **Staleness-detector reflection** (`reflections/housekeeping/test_baseline_refresh_check.py` + a `reflections.yaml` entry): age-only check, no suite run.
- **Baseline regeneration**: run the corrected `refresh_test_baseline.py` on a quiescent `main` checkout; machine-local, not part of the PR diff.

### Flow

Merge gate reads baseline → (if >14d) prints staleness warning → classifies PR failures. Separately: weekly reflection reads baseline age → (if stale) surfaces a maintenance nudge → operator runs corrected refresh on quiet `main` → fresh baseline → warning clears.

### Technical Approach

- **`test_chat_message_log_e2e.py`**: delete the module-level `_build_draft_prompt` import. The four tests that call `_build_draft_prompt` (`test_drafter_prompt_contains_prior_path_b_message`, `test_inbound_entry_also_appears_in_drafter_prompt`, `test_both_in_and_out_entries_appear_in_drafter_prompt`, `test_chat_log_is_empty_for_fresh_session`) assert on a prompt string that no longer exists (the drafter no longer builds an LLM-rewrite prompt from the chat log) — **DELETE** them (NO LEGACY CODE; the exercised behavior is gone). Keep `test_path_b_outbound_entry_is_recorded_in_chat_log` (exercises `_append_outbound_chat_log`, still live) and any tests in the file that do not depend on the deleted symbol. Verify the remaining module still contains at least one meaningful assertion; if the whole file reduces to nothing live, remove the file and note it. (Build agent confirms by reading the full file, including `TestCompletionRunnerSuppressionE2E`.)
- **`test_agent_session_lifecycle.py`**: `test_build_prompt_includes_session_context` (line ~416) asserts `_build_draft_prompt` embeds SDLC session context — behavior gone — **DELETE** the test method. Leave the rest of the file untouched.
- **`_baseline_common.py::parse_junitxml`**: locate the `if not name:` branch (~line 70) that raises `JunitxmlParseError`. Replace with: if the testcase has an `<error>` child, classify as `collection_error`/`import_error` under a best-effort node id (`classname` or a synthetic placeholder); otherwise skip that single `<testcase>` element and continue parsing the rest of the run. Never abort the whole run for one nameless element.
- **`refresh_test_baseline.py`**: after aggregation, if the count of usable runs < 2, append a `WARNING: only N usable run(s) — flaky classification unavailable` line to the summary and return a non-zero exit (respecting existing exit-code conventions; a `--allow-degraded` opt-out flag may be added if the script is ever intentionally run once).
- **Reflection**: model on `reflections/housekeeping/disk_space_check.py`. Import `STALENESS_THRESHOLD` from `scripts.baseline_gate` (single source of truth). Read the baseline via the same loader; if missing or unparseable, return a benign "no baseline" status (do not crash).
- **Regeneration**: after the above land and unit tests pass, run `python scripts/refresh_test_baseline.py` on a quiescent `main` checkout (gate on the dashboard active-session count), reaping xdist workers afterward (`scripts/reap-xdist.sh --apply` / `scripts/pytest-clean.sh` semantics). This writes the gitignored `data/main_test_baseline.json` on this machine only.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `parse_junitxml` nameless-`<testcase>` branch: add a unit test feeding a junitxml with a nameless `<testcase>` (both with and without an `<error>` child) and assert the run is parsed (not discarded) with the expected classification / skip.
- [ ] Reflection: `except`-guard the baseline read; test that a missing/malformed baseline file yields a benign status, not a raised exception.

### Empty/Invalid Input Handling
- [ ] `parse_junitxml`: junitxml containing ONLY a nameless testcase → returns an empty-or-classified dict, does not raise.
- [ ] Reflection with `generated_at` absent/malformed → benign status.
- [ ] `refresh_test_baseline.py` with 0 or 1 usable runs → loud WARNING + non-zero exit (assert via `parse_args`/`main` unit path, not a live suite run).

### Error State Rendering
- [ ] The refresh degraded-run WARNING must reach the operator (stderr/summary block), not be swallowed. Test asserts the WARNING string is emitted when usable runs < 2.

## Test Impact

- [ ] `tests/integration/test_chat_message_log_e2e.py::TestChatMessageLogE2E::test_drafter_prompt_contains_prior_path_b_message` — DELETE: asserts removed `_build_draft_prompt` output.
- [ ] `tests/integration/test_chat_message_log_e2e.py::TestChatMessageLogE2E::test_inbound_entry_also_appears_in_drafter_prompt` — DELETE: same.
- [ ] `tests/integration/test_chat_message_log_e2e.py::TestChatMessageLogE2E::test_both_in_and_out_entries_appear_in_drafter_prompt` — DELETE: same.
- [ ] `tests/integration/test_chat_message_log_e2e.py::TestChatMessageLogE2E::test_chat_log_is_empty_for_fresh_session` — DELETE: same.
- [ ] `tests/integration/test_chat_message_log_e2e.py` module-level import (line 18) — UPDATE: remove the `_build_draft_prompt` import; keep `test_path_b_outbound_entry_is_recorded_in_chat_log` and any non-dependent tests.
- [ ] `tests/integration/test_agent_session_lifecycle.py::test_build_prompt_includes_session_context` (line ~416) — DELETE: asserts removed `_build_draft_prompt` behavior.
- [ ] `tests/unit/test_refresh_test_baseline.py` — UPDATE: add cases for nameless-`<testcase>` parsing and the <2-run loud-failure path (audit existing cases for any that assert the old raise-and-discard behavior and update them).
- [ ] `tests/unit/test_do_merge_baseline.py` — audit; UPDATE only if it asserts on the old parse behavior (expected: no change — it targets `baseline_gate` comparison, not junitxml parsing).

## Rabbit Holes

- **Do NOT** re-architect the baseline schema or the merge-gate comparison logic. This is maintenance, not redesign.
- **Do NOT** convert the staleness warn to a hard block (see Staleness Answer).
- **Do NOT** build a scheduled full-suite auto-regeneration reflection (rails hazard; see Staleness Answer).
- **Do NOT** rewrite the deleted drafter tests to force some equivalent against the new API — the behavior is gone; deleting is correct. Only keep tests whose exercised behavior still exists.
- **Do NOT** root-cause the upstream source of the nameless `<testcase>` (xdist/execnet worker-crash artifact) beyond making the parser resilient — that is a separate, deeper investigation.

## No-Gos

- No changes to `bridge/message_drafter.py` production code — the drafter API is already correct; only its orphaned tests are being cleaned up.
- No committing of `data/main_test_baseline.json` — it is gitignored and machine-local.
- No push of code to `main` — code lands on `session/{slug}` via PR. The plan doc itself commits on `main`.
- No new external dependencies.

## Update System

- **Reflection deployment**: the new reflection callable (`reflections/housekeeping/test_baseline_refresh_check.py`) is committed, but it only runs once registered in the vault `reflections.yaml` (`~/Desktop/Valor/reflections.yaml`, gitignored, iCloud-synced). Add a single entry (`- name: test-baseline-refresh-check`, `module`/`callable` pointing at the new function, weekly `schedule`, worker/bridge role gate) as a per-machine deployment step. Document this in `docs/features/merge-gate-baseline.md`. No `scripts/update/run.py` change required — the YAML scheduler picks up the callable via importlib once the entry exists.
- **No `scripts/update/migrations.py` change** — no Popoto model changes.

## Agent Integration

No agent integration required — no new CLI entry point in `pyproject.toml [project.scripts]`, no `.mcp.json` change, and the bridge does not import any of this. The refresh script is already an operator-run tool; the reflection runs via the existing YAML scheduler; the merge gate is already wired.

## Documentation

- [ ] Update `docs/features/merge-gate-baseline.md`: document the #1853 fix (nameless-`<testcase>` resilience + loud <2-run failure), the staleness decision (soft-warn kept, no hard-block, no auto-regen, age-detector added), and the `reflections.yaml` deployment entry for the detector.
- [ ] Note in the same doc that `_baseline_post_merge_update.py` intentionally does not refresh `generated_at` (decay does not re-observe main), so periodic operator regen remains necessary — surfaced by the new detector.

## Step by Step Tasks

1. Read the full `test_chat_message_log_e2e.py` (incl. `TestCompletionRunnerSuppressionE2E`) and confirm which tests depend on `_build_draft_prompt`. Delete the four drafter-prompt tests + the module-level import; keep the recording test(s). Ensure the module still collects and holds ≥1 live assertion.
2. Delete `test_build_prompt_includes_session_context` from `test_agent_session_lifecycle.py`.
3. Confirm no `_build_draft_prompt` references remain anywhere in `tests/` (`grep -rn _build_draft_prompt tests/` → empty).
4. Harden `parse_junitxml` (`scripts/_baseline_common.py`) for nameless `<testcase>` (classify-or-skip, never discard the run). Add unit tests in `tests/unit/test_refresh_test_baseline.py`.
5. Add the <2-usable-run loud WARNING + non-zero exit to `refresh_test_baseline.py`. Add a unit test.
6. Add `reflections/housekeeping/test_baseline_refresh_check.py` (age-only, imports `STALENESS_THRESHOLD`, benign on missing baseline). Add a unit test for stale/fresh/missing.
7. Update `docs/features/merge-gate-baseline.md`.
8. `ruff format` the touched files. Run the narrow set: `pytest tests/unit/test_refresh_test_baseline.py tests/integration/test_chat_message_log_e2e.py --collect-only` (collection green) plus the new unit tests.
9. Open the PR on `session/{slug}` with `Closes #1933`.
10. (Post-fix, quiescence-gated, local, not in PR) Regenerate `data/main_test_baseline.json` via the corrected `refresh_test_baseline.py` on a quiet `main` checkout; reap xdist workers after.

## Verification / Success Criteria

- `pytest tests/integration/test_chat_message_log_e2e.py --collect-only` succeeds (no `ImportError`).
- `grep -rn _build_draft_prompt tests/` returns nothing.
- New unit tests for nameless-`<testcase>` parsing and the <2-run loud-failure path pass.
- The staleness-detector reflection unit test passes (stale → warning status; fresh → ok; missing → benign).
- `data/main_test_baseline.json` regenerated with a fresh `generated_at` and `runs` ≥ 2 (verified locally on this machine).
- Staleness question answered in this plan (above) with justification.

## Open Questions

1. **Baseline regeneration timing/host.** Regeneration is a heavy full-suite 3× run that must not collide with active sessions on Redis. Should the dev run it now on this machine once the machine is quiescent (dashboard shows no active sessions), or should it be handed to the bridge/worker machine during off-hours? Default assumption: run it on this machine gated on a quiescent dashboard, since the acceptance criterion asks for a regenerated baseline and this is where the pipeline is executing.
2. **Detector reflection inclusion.** Confirm the cheap age-only detector reflection should ship in this PR (vs. answered-in-plan-only). Default assumption: ship it — it is small, runs no tests, and is the concrete anti-recurrence mechanism the issue asks for.
