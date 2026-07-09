# Delivery Guard: Resume-Epoch Scoping (#1979)

## Problem

The session-health "Delivery guard" force-finalizes a `running` `AgentSession` as
`completed` whenever `response_delivered_at` is set *at all* — it never checks whether that
timestamp belongs to the *current* run. Two read sites carry the identical bug:

- `agent/session_health.py:3203` inside `_agent_session_health_check()` — the periodic
  running-session scan.
- `agent/session_health.py:2197` inside `_apply_recovery_transition()` — the per-entry
  recovery path.

Both do `if getattr(entry, "response_delivered_at", None) is not None:` → `finalize_session(entry, "completed", ...)`.

`tools/valor_session.py::resume_session()` transitions a terminal session back to `pending`
for a fresh attempt but never clears `response_delivered_at`. So a session that delivered a
response during any *prior* attempt carries that stale timestamp into every resume. The
very next health-check tick after the resume force-finalizes the session as `completed`
using the prior attempt's delivery timestamp — while the session is still legitimately
working (confirmed live subprocess in the field report on session
`43b42260899a4f1f81f254fa2b1fc2bb`, 2026-07-09). The orphaned live process later hits a
terminal→terminal status conflict when it tries to finalize itself normally.

This is the same *class* of bug as #1614 (a sticky field not scoped to the current run),
here for `response_delivered_at`.

### Key enabling fact

`started_at` is refreshed to `datetime.now(tz=UTC)` on **every** pickup, immediately before
the `pending → running` transition (`agent/session_pickup.py:463` and `:611`). Therefore, at
the moment either delivery guard evaluates a `running` session, `started_at` reflects the
**current** run's start. A genuine current-run delivery always satisfies
`response_delivered_at >= started_at` (started_at is stamped at pickup, delivery happens
later in the run). A prior-attempt delivery has `response_delivered_at < started_at`.

## Solution

Scope both delivery guards to the current run via a single shared predicate — do **not**
weaken the guard's original purpose (catching genuinely stuck-after-delivery sessions).

Add a helper in `agent/session_health.py`:

```python
def _delivered_this_run(entry) -> bool:
    """True only if response_delivered_at belongs to the CURRENT run (issue #1979).

    started_at is refreshed to now on every pending->running pickup
    (session_pickup.py), so a delivery timestamp predating started_at belongs to
    a PRIOR attempt (resume) and must not trip the delivery guard. When started_at
    is absent (legacy/anomalous running row with no epoch anchor) fall back to the
    original behavior — a set delivery timestamp counts as this-run delivery — so
    the guard's stuck-after-delivery purpose is preserved for those rows.
    """
    delivered = _ts(getattr(entry, "response_delivered_at", None))
    if delivered is None:
        return False
    started = _ts(getattr(entry, "started_at", None))
    if started is None:
        return True
    return delivered >= started
```

Replace both guard conditions:

- `agent/session_health.py:3203`: `if getattr(entry, "response_delivered_at", None) is not None:` → `if _delivered_this_run(entry):`
- `agent/session_health.py:2197`: same replacement.

Chosen approach: **epoch-scoping at the read site** (Solution Sketch option 1), not clearing
`response_delivered_at` in `resume_session()` (option 2). Rationale:

- Single surgical fix at the guard; covers **all** resume paths (CLI resume, auto-resume
  reflection) because they all route through the pickup that refreshes `started_at`.
- No data mutation — `response_delivered_at` remains an honest historical record of the
  prior delivery; nothing else that reads it (e.g. duplicate-delivery prevention) is
  disturbed.
- Defense-in-depth at the exact decision point that misfired.

### Sticky-field audit (per #1614 precedent)

Requested by the issue: audit whether other sticky fields read by `agent/session_health.py`
share the resume-epoch-scoping gap.

- `turn_count`, `log_path`, `claude_session_uuid` — flagged as sticky by the existing
  comment at `session_health.py:3197-3202`. These were already gated on
  `NO_OUTPUT_BUDGET_SECONDS` by #1614, so they no longer permanently block recovery. No
  further change needed.
- `last_heartbeat_at` — refreshed continuously by the live run; not sticky across resume.
- `started_at` — actively refreshed on pickup (the enabling fact above); it is the anchor,
  not a victim.
- `response_delivered_at` — the remaining ungated sticky field. This plan closes it.

Conclusion: `response_delivered_at` is the only remaining gap; the fix is complete with the
two-site change above.

## Files Changed

- `agent/session_health.py` — add `_delivered_this_run()` helper; replace the two delivery-guard
  conditions (lines ~2197 and ~3203) to call it.
- `tests/integration/test_agent_session_health_monitor.py` — add regression tests (below).
- `docs/features/session-recovery-mechanisms.md` — document the resume-epoch scoping of the
  Delivery guard under the Health Check section.

## Tests

Written test-first (verify-fail before implementing):

1. `test_delivery_guard_ignores_prior_run_delivery` (integration) — a `running` session with
   `response_delivered_at` set to a time **before** a fresh `started_at`, an alive worker, and a
   fresh heartbeat is NOT finalized by `_agent_session_health_check()`; it stays `running`.
   (AC1 + AC2.)
2. `test_delivery_guard_finalizes_current_run_delivery` (integration) — a `running` session
   with `response_delivered_at >= started_at` (delivery genuinely in the current run) IS still
   finalized to `completed` by `_agent_session_health_check()`. (AC3.)
3. `test_delivered_this_run_predicate` (unit) — direct table test of `_delivered_this_run`:
   prior-run (delivered < started) → False; current-run (delivered >= started) → True; no
   delivery → False; started_at=None fallback → True.

## Failure Path Test Strategy

The failure this fixes is a false-positive finalization. Test 1 reproduces the exact field
report (prior delivery timestamp + fresh resume) and asserts the session survives — this
fails against the current code (guard fires) and passes after the fix. Test 2 guards against
over-correction (the guard must still fire for genuine current-run stuck-after-delivery).
Test 3 pins the predicate's boundary behavior including the `started_at=None` legacy
fallback so a future refactor can't silently regress either direction.

## Test Impact
- [ ] `tests/integration/test_agent_session_health_monitor.py` — ADD three new tests; no
      existing test asserts the delivery-guard-on-prior-delivery behavior, so no existing
      case changes disposition. Verified via grep: no current test sets `response_delivered_at`
      on a running session and asserts finalization.

## Rabbit Holes

- Do NOT try to clear `response_delivered_at` on resume — rejected above; would mutate an
  honest historical field and only cover the resume paths, not the read decision.
- Do NOT touch the `NO_OUTPUT_BUDGET_SECONDS` gating for turn_count/uuid (#1614 already
  handles those); out of scope.
- Do NOT change `finalize_session()` / `transition_status()` terminal-guard semantics — the
  downstream status-conflict log line is a symptom, not the bug.

## Risks

- `started_at` semantics: relies on pickup always refreshing `started_at` before
  `running`. Confirmed at two pickup sites. If a future path sets `running` without
  refreshing `started_at`, a genuine current-run delivery could be missed — mitigated by the
  `started_at=None` fallback and by test 2 pinning current-run finalization.

## Race Conditions

None introduced. The guard reads two already-persisted timestamps on one entry; no new
cross-process ordering. The equality edge (`delivered == started`, astronomically unlikely
across distinct code paths) resolves toward "this run delivered" (`>=`), which is the safe
direction for the guard's purpose.

## No-Gos (Out of Scope)

- Not filing/fixing the downstream terminal→terminal status-conflict log line (a symptom of
  the root cause fixed here).
- Not re-designing the health check's recovery taxonomy.
- Not altering `resume_session()`.

## Update System

No update system changes required — this is a purely internal change to
`agent/session_health.py`. No new dependencies, config files, or migration steps; existing
installations pick it up on the next code sync + worker restart.

## Agent Integration

No agent integration required — this is a worker-internal health-check change. No new CLI
entry point in `pyproject.toml [project.scripts]` and no new bridge import. The behavior is
exercised entirely by the worker's periodic `_agent_session_health_check()` loop and by the
new integration tests that invoke it directly.

## Documentation
- [ ] Update `docs/features/session-recovery-mechanisms.md` under the Health Check
      (`_agent_session_health_check`) section: document that the Delivery guard is scoped to
      the current run via `response_delivered_at >= started_at` (the `_delivered_this_run`
      predicate) and explain why — a resumed session carries a prior attempt's delivery
      timestamp, and `started_at` is refreshed on each pickup (#1979).
- [ ] In the same doc, record the sticky-field audit conclusion: `response_delivered_at` was
      the last ungated sticky field; `turn_count`/`log_path`/`claude_session_uuid` were
      already gated by #1614. Cross-reference #1614 and #1979.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New regression tests pass | `pytest tests/integration/test_agent_session_health_monitor.py -k "delivery_guard or delivered_this_run" -q` | exit code 0 |
| Health test module green | `pytest tests/integration/test_agent_session_health_monitor.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_health.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py` | exit code 0 |

## Success Criteria

- AC1: A session resumed after a prior delivery is NOT force-finalized as `completed` by the
  Delivery guard based on that prior delivery's timestamp. (Test 1.)
- AC2: A regression test simulates deliver → terminal → resume → health check while still
  `running` with a fresh heartbeat, and asserts it is NOT finalized. (Test 1.)
- AC3: The existing "delivered but stuck running" case still finalizes correctly when the
  delivery genuinely belongs to the current run. (Test 2.)
- Both delivery-guard read sites (`:2197`, `:3203`) are scoped via the shared predicate.
- Sticky-field audit documented (no additional gaps beyond `response_delivered_at`).
