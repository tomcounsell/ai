# Nightly Alert Triage

Three additions to the nightly regression detector (`scripts/nightly_regression_tests.py`)
that make its alerts more reliable to send and more useful to read: an advisory run
lock, a best-effort LLM summarizer for failure alerts, and a fire-and-forget triage
session dispatch that hands newly-confirmed failures to an Eng session for
investigation.

## Status

Shipped — Scope 1 of issue #2192 ("Nightly Regression Detector & Sentry Triage
Reflection — Dedupe, Readable Alerts, Auto-Triage").

**Scope 2 (Sentry triage reflection, `reflections/sentry_triage.py`) is a separate,
later PR and has not shipped yet.** This document covers Scope 1 only — the nightly
test detector side. Do not treat Sentry-side auto-triage as implemented until that
PR lands.

## What It Does

Builds on the existing nightly detector (see `docs/features/nightly-regression-tests.md`
for the base run: pytest, serial re-confirmation, delta computation). This feature adds
three behaviors layered around that base run:

1. **Run lock** — prevents two overlapping launchd invocations from both running the
   suite and both sending Telegram alerts for the same window.
2. **Failure summarizer** — turns a list of raw pytest node IDs into a short,
   human-readable sentence or two for the Telegram alert.
3. **Triage dispatch** — spins up an Eng session to investigate newly-confirmed
   failures and file a GitHub issue, deduped so the same failing set doesn't
   re-dispatch every night.

## Run Lock (Race 1)

`_acquire_run_lock()` takes an exclusive, non-blocking `fcntl.flock` on
`data/nightly_tests.lock`, acquired as the very first thing `main()` does — before
loading prior state or running any tests.

- **Problem it solves**: if two launchd invocations of the nightly job overlap (e.g. a
  slow prior run still finishing when the next scheduled run fires), both processes
  would otherwise run the suite independently and could both send Telegram alerts for
  what's really the same underlying test run — duplicate, confusing noise.
- **Mechanism**: mirrors the sidecar-lock-file idiom already used in
  `scripts/pr_shape_cache.py` — open/create the lock file, then
  `fcntl.flock(fd, LOCK_EX | LOCK_NB)`. On success, the caller must keep the returned
  file handle alive for the process lifetime (letting it get garbage-collected closes
  the fd and releases the lock early). The OS releases the lock automatically on
  process exit.
- **On collision**: `_acquire_run_lock()` returns `None`. `main()` logs the collision
  and returns `0` immediately — no test run, no Telegram send, no state write. The
  losing invocation is a no-op, not a failure.

## Best-Effort Failure Summarizer

`summarize_failures(confirmed_failing, report)` turns the newly-confirmed failing
node IDs into a 1-3 sentence plain-English summary for the Telegram alert, instead of
a raw list of pytest node IDs.

- **What it does**: groups the failing node IDs by file, pulls the last line of each
  test's traceback (`call.longrepr` or `crash.message`) from the pytest
  `--json-report` payload when available, and asks a cheap LLM
  (`agent/llm/wrapper.run_typed`, `config.models.MODEL_FAST`, the project's
  standard non-harness PydanticAI transport) for a short summary and likely root
  cause area.
- **Fallback discipline**: this is best-effort only. An empty `confirmed_failing`
  list short-circuits before any LLM call. Any other failure — network error, empty
  or malformed LLM response, `LLMCallError`, missing `ANTHROPIC_API_KEY` — is caught
  broadly, logged as a warning, and the function falls back to the raw node-ID
  preview format (`_raw_failure_preview`: first 5 node IDs + "+N more") that
  `main()` used to build inline before this feature existed. The summarizer never
  crashes the nightly run and never blocks an alert from being sent.
- **Subscription-only machines**: machines running on a Claude subscription without
  `ANTHROPIC_API_KEY` set hit the fallback path on every call (the PydanticAI wrapper
  raises when no API key is configured). This degrades gracefully — the alert still
  contains the raw node-ID preview, just without the plain-English gloss.

## Triage Session Dispatch

`maybe_dispatch_triage_session(confirmed_failing, prev)` fires off an Eng-role
`AgentSession` to investigate newly-confirmed failures, whenever the confirmed-failing
set has changed since the last dispatch.

- **Invocation contract**: shells out to
  `python -m tools.valor_session create --role eng --slug nightly-triage-<hash8>
  --json --message <prompt>`.
  - `--slug` is **mandatory** on this call. A slugless `valor_session create` call
    for a non-teammate role tries to auto-derive a slug from an `issue #N` pattern
    in the message and exits 1 silently if none is found — nightly prompts have no
    such pattern, so omitting `--slug` would make every dispatch fail quietly.
  - `--json` is required so the dispatched session ID can be parsed back out of
    stdout (`json.loads(stdout)["session_id"]`, wrapped in try/except — a parse
    failure just means the session ID doesn't make it into the Telegram alert text,
    it doesn't fail the dispatch or the run).
  - The subprocess call has a 30s timeout; any exception (timeout, missing binary,
    non-zero exit) is caught, logged as a warning, and treated as "no dispatch" —
    this is fire-and-forget, not a blocking dependency of the nightly run.
- **Dedup semantics**: the dedup key is the sha256 hash of the sorted, deduped
  confirmed-failing node-ID set. It's persisted as `dispatched_hash` in
  `data/nightly_tests_last_run.json` and compared against the current run's hash. If
  they match, dispatch is skipped (log-only, no subprocess call) — the same failing
  set doesn't re-dispatch a fresh triage session every night it stays unfixed. The
  hash and the resulting session ID (`dispatched_session_id`) are only overwritten on
  a run that actually attempts a dispatch; clean, baseline, and collection-error runs
  carry the previous values forward unchanged so the dedup state survives runs that
  don't reach the dispatch branch.
- **Mandate**: the dispatched session's prompt is explicit that the task is
  investigate-and-file-a-`/do-issue`-quality GitHub issue describing the failure, its
  likely cause, and suggested next steps — **not** an auto-hotfix. Auto-hotfixing
  nightly regressions is out of scope and explicitly called out as a No-Go in the
  originating plan (`docs/plans/nightly-regression-triage.md`).

## Files

| File | Purpose |
|------|---------|
| `scripts/nightly_regression_tests.py` | Adds `_acquire_run_lock`, `summarize_failures`, `maybe_dispatch_triage_session` around the existing detector; see `docs/features/nightly-regression-tests.md` for the base run mechanics |
| `data/nightly_tests.lock` | Advisory lock file for `_acquire_run_lock` (gitignored, empty — existence and the flock state are all that matter) |
| `data/nightly_tests_last_run.json` | Now also carries `dispatched_hash` and `dispatched_session_id` alongside the existing delta-state fields |

## Design Decisions

**Advisory `flock`, not a Redis lock** — the nightly job already writes local JSON
state files rather than depending on Redis (see the base doc's "Local JSON state, not
Redis" decision). A lock file in the same `data/` directory keeps that pattern
consistent and needs no external dependency.

**Summarizer failure is never fatal** — the alert must go out even when the LLM call
fails; a broad `except Exception` with a raw-format fallback guarantees that. This
matches the project's existing "best-effort Telegram" discipline for `send_telegram()`
itself.

**Dispatch is fire-and-forget, not awaited** — the nightly script's job is to detect
and alert, not to babysit a triage investigation. The dispatch subprocess call has a
short timeout and any failure degrades to "no triage session for this alert," never a
blocked or failed nightly run.

**Hash-based dedup over a run-count or time-based dedup** — the confirmed-failing set
is the signal that actually matters: two different failing sets should each get their
own triage session, but the same unresolved set showing up night after night should
not re-dispatch. A content hash captures that directly.

## Manual Testing

```bash
# Preview a full run including the summarizer/dispatch paths (dry-run skips Telegram,
# but the summarizer and dispatch logic still execute if there are newly-confirmed failures)
python scripts/nightly_regression_tests.py --dry-run
```

`summarize_failures()` and `maybe_dispatch_triage_session()` can also be exercised
directly against fake node IDs for a quick sanity check of the fallback path without
running the full suite — see the manual verification note in issue #2192 / the PR
description for a sample run.

## See Also

- `docs/features/nightly-regression-tests.md` — the base detector this feature
  extends (run cadence, serial re-confirmation gate, delta computation, Telegram alert
  conditions)
- `docs/plans/nightly-regression-triage.md` — originating plan, including the No-Gos
  that keep triage dispatch investigate-only
- `docs/features/eng-session-architecture.md` — Eng session semantics for the
  dispatched triage session
