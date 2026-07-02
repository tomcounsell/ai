# Delivery-Integrity Hardening

Status: Shipped (workstream A). Tracking issue: #1817.

A red-team sweep for uncatalogued failure modes surfaced a set of correctness and
delivery-integrity hazards: bugs that silently drop, duplicate, or corrupt work with no
crash required. Each fix converts a silent failure into a loud or atomic one. This page
documents the shipped workstream A (lost/dropped work). The remaining workstreams are
tracked as follow-up issues (see the end of this page).

## A1 — Steering inbox consolidation (atomic Redis list)

**Hazard.** External steering wrote to `AgentSession.queued_steering_messages`, a Popoto
`ListField` updated by a non-atomic read-modify-write. The worker popped from a session
instance bound at turn start (not re-fetched), and the resume path saved the whole record.
A human course-correction landing after the bind, or concurrent with a save, was silently
saved over with an empty list. The steer was lost with no error.

**Fix.** The Redis list in `agent/steering.py` is now the sole steering inbox. It was
already the atomic primitive (per-operation `RPUSH` to append, `LPOP` to consume) and the
dominant path for the bridge, pickup, output handler, and health check. A1 removes the
redundant dual-write: the `queued_steering_messages` ListField and its model methods
`push_steering_message()` / `pop_steering_messages()` are deleted from
`models/agent_session.py`.

Steering now flows through one path:

- **Write.** `push_steering_message(session_id, text, sender, ...)` (module function in
  `agent/steering.py`), reached externally via `steer_session()` in
  `agent/session_executor.py`, the `valor-session steer` CLI, and `scripts/steer_child.py`.
- **Drain.** The worker calls `pop_all_steering_messages(session_id)` at each turn
  boundary. The first message becomes that turn's user input; the rest stay on the list
  for later turns. `peek_steering_messages(session_id)` is a non-destructive read for
  status dumps.
- **CLI-harness / watchdog delivery.** When `agent/health_check.py::_handle_steering`
  finds a pending steer but no active SDK client, it re-pushes every non-abort message
  back onto the Redis list so the worker's next turn-boundary drain delivers it. Abort
  signals are delivered immediately via the hook's `additionalContext`.

**Single-consumer safety.** `pop_all_steering_messages` drains via sequential `LPOP`,
which is not a single atomic multi-pop. It is safe because each `LPOP` is atomic, so two
drainers racing on one `session_id` partition the queue disjointly. No message is popped
twice and none is lost. A regression test
(`tests/integration/test_steering.py::test_concurrent_drainers_split_disjointly`) locks
this invariant so a future refactor cannot silently break it.

**Migration.** `scripts/migrate_steering_queue_drain.py` (registered in
`scripts/update/migrations.py` as `steering_queue_drain`) drains any residual ListField
content into the Redis list before the field is dropped, so an in-flight steer that was
sitting in the old inbox at deploy time is preserved.

## A2 — Resolver-unavailable vs not-a-customer

**Hazard.** `resolve_customer` (`bridge/routing.py`) was fail-closed: any resolver error
returned `None`, and `_process_inbound_email` treated `None` as "not a customer", dropped
the message, and set `\Seen`. If the resolver's OAuth token expired, every inbound
customer email was silently and irrecoverably dropped.

**Fix.** `resolve_customer` raises `ResolverUnavailable` on an infrastructure or OAuth
error, distinct from a clean "not a customer" result. On the `ResolverUnavailable` branch,
`_process_inbound_email` leaves the message unseen (it un-marks the `\Seen` flag that the
pre-fetch concurrency guard set) and logs, so the next poll retries. Only a definitively
resolved non-customer is `\Seen`-dropped.

A persistent unavailable resolver (an expired token that fails every inbound email across
the alert threshold) arms a threshold-gated `email:resolver_unavailable` operator alert
plus `logger.critical`, cleared on the first successful resolve. The alert is surfaced on
the dashboard email-health field (`ui/app.py`), matching A3, so a token expiry is loud.

## A3 — Permanent IMAP auth classification + alert

**Hazard.** `email_bridge.py` treated a permanent `IMAP4.error` (a revoked app password)
the same as a transient one, retrying on a 5-minute backoff forever. The only signal was a
stale `email:last_poll_ts`, and no email watchdog existed.

**Fix.** A permanent `IMAP4.error` is classified distinctly: it arms an `email:auth_failed`
operator alert plus `logger.critical` and stops the exponential-backoff doubling, instead
of silently looping. A transient error keeps backing off. The alert clears on the next
successful poll and is surfaced on the dashboard email-health field (`ui/app.py`).

## Deploy notes

These are bridge, worker, and web-UI changes. After merge, a manual restart of the email
bridge, the worker, and the web UI is required for them to take effect. The Popoto
field-drop runs via the `/update` migration (`steering_queue_drain`).

## Follow-up workstreams (tracked separately)

The remaining findings from #1817 ship as their own issues and PRs:

- **B (duplicate execution):** atomic per-message claim before enqueue (B1); atomic
  pending to running claim (B2).
- **C (data integrity):** parent-in-`waiting_for_children` re-finalize sweep (C1); clock-skew
  freshness (C2); ghost index-member reconcile (C3); guarded `projects.json` last-known-good
  read (C4, PR #1861).
- **D (brittleness):** `claude` CLI version pin + startup contract-check (D1); immediate PTY
  pid persistence + broadened reaper (D2); held fire-and-forget tasks (D3); off-path
  notify-listener liveness probe (D4).
