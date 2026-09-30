---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-26
tracking: https://github.com/tomcounsell/ai/issues/1797
last_comment_id:
---

# Deferred Self-Draft on Email Transport — Completed-Path Flush

## Problem

An email-transport session that defers its reply for self-draft (the drafter
holds the text instead of sending it immediately) and then reaches a clean
`completed` state — without ever redrafting — silently loses the held reply. The
human emailed in, the agent did the work, and the answer never arrives.

This is the exact gap PR #1796 closed for **telegram** under #1794, left open for
**email** because synchronously replicating the email outbox payload was out of
that issue's Small appetite.

**Current behavior:**
- Telegram deferred self-draft → flushed on every terminal path by the
  synchronous `flush_deferred_self_draft_sync` chokepoint (`finalize_session`).
- Email deferred self-draft → flushed only on `failed`/`abandoned` by the async
  `_deliver_deferred_self_draft_fallback` helper. On a clean `completed`, neither
  helper fires: the sync flush early-returns for `transport == "email"`, and the
  async helper is never called on the completed path. The text is lost.

**Desired outcome:**
- An email-transport deferred self-draft reaching `completed` is delivered: a
  valid reply-all payload is written to `email:outbox:{session_id}` and drained
  over SMTP by the relay — verbatim text, correct threading.
- No double-send: a single terminal status per session means email-on-completed
  (new sync path) and email-on-failed/abandoned (existing async path) never both
  fire.

## Freshness Check

**Baseline commit:** `a20e7e16` (HEAD at plan time)
**Issue filed at:** 2026-06-25T12:52:56Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_health.py:1585` — `flush_deferred_self_draft_sync` exists; email
  early-return confirmed at `:1630-1631`; telegram outbox write at `:1679-1681`;
  dedup key `self_draft_completed_flush_sent` at `:1637`. Still holds.
- `agent/session_health.py:1704` — `_deliver_deferred_self_draft_fallback` exists;
  telegram/None early-return at `:1781-1782`; dedup key `self_draft_fallback_sent`
  at `:1742`. Still holds.
- `agent/session_health.py:2107, 2130, 2158` — the three `failed`/`abandoned`
  call sites of the async helper, each followed by `finalize_session`. Still holds.
- `models/session_lifecycle.py:406-411` — the chokepoint invocation
  `flush_deferred_self_draft_sync(session)` (status not forwarded). Still holds.
- `agent/output_handler.py:184-299` — `_send_via_email_outbox` payload recipe
  (reply-all `to`, `Re:` subject, `in_reply_to`/`references`, `from_addr`). Still
  holds.
- `bridge/email_relay.py:9-20` — outbox payload contract. Still holds.

**Cited sibling issues/PRs re-checked:**
- #1794 / PR #1796 — closed/merged 2026-06-25; established the telegram
  chokepoint flush and the email-only async helper. This issue is its symmetric
  follow-up.

**Commits on main since issue was filed (touching referenced files):**
- `7fb7e609` fix(delivery): flush deferred self-draft on completed terminal path
  (#1796) — this IS the prior fix; it created the gap this plan closes. Relevant.
- `4b92ed3c` feat(session-health): gate never_started kill on PTY liveness
  (#1798) — unrelated region of `session_health.py`; irrelevant.

**Active plans in `docs/plans/` overlapping this area:**
`deferred_self_draft_completed_path_flush.md` is the #1794 plan (shipped via
#1796). Not active — this plan is the follow-up, not an overlap.

**Notes:** No drift. All line numbers and claims verified against current code.

## Prior Art

- **#1794 / PR #1796** — "flush deferred self-draft on completed terminal path":
  introduced `flush_deferred_self_draft_sync` and made
  `_deliver_deferred_self_draft_fallback` email-only. Succeeded for telegram;
  explicitly deferred email-completed as a tracked follow-up (this issue). This
  plan reuses #1796's exact architecture (chokepoint sync flush, distinct dedup
  keys, exception isolation) and extends it to email.
- **#1730** — original deferred self-draft fallback (failed/abandoned only). The
  ancestor mechanism; superseded for telegram-completed by #1796.

No prior *failed* fixes for the email-completed path — this is the first attempt.
The `## Why Previous Fixes Failed` section is therefore omitted (no prior
failures, only a deliberately-scoped deferral).

## Data Flow

1. **Entry point:** an email arrives → `bridge/email_bridge.py` spawns an
   AgentSession stamping `extra_context` with `transport="email"`,
   `email_subject`, `email_message_id`, `email_to_addrs`, `email_cc_addrs`, and
   `chat_id` = the sender's address.
2. **Defer:** the drafter defers the reply →
   `TelegramRelayOutputHandler.send` writes
   `extra_context["deferred_self_draft_pending"]=True` and
   `["deferred_self_draft_text"]=<held text>` (`agent/output_handler.py:453-454`).
3. **Terminal transition:** the session reaches `completed` →
   `finalize_session(session, "completed", ...)`
   (`models/session_lifecycle.py:221`) calls the chokepoint flush at `:406-411`.
4. **Flush (the fix):** `flush_deferred_self_draft_sync(session, status)`
   re-reads the authoritative session, sees `transport == "email"` and
   `status == "completed"`, builds the email outbox payload synchronously, and
   `rpush`es it to `email:outbox:{session_id}`.
5. **Output:** `bridge/email_relay.py` drains `email:outbox:*` and sends over
   SMTP. The human receives the reply, correctly threaded.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `flush_deferred_self_draft_sync` gains a `status`
  parameter (`status: str | None = None`); its single call site in
  `finalize_session` forwards the target status. One new module-level pure
  function `build_email_outbox_payload(...)` extracted from
  `_send_via_email_outbox` (DRY — the email payload recipe is defined once and
  shared by the async handler and the sync flush).
- **Coupling:** slightly reduced — the email payload recipe moves from a method
  body to a reusable pure function. `session_health.py` already imports from
  `output_handler.py`, so no new cross-module coupling direction.
- **Data ownership:** unchanged. The sync flush owns email delivery on the
  `completed` path; the async helper retains email on `failed`/`abandoned`.
  Disjoint by terminal status.
- **Reversibility:** trivial — revert the three touched files.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue + recon)
- Review rounds: 1 (validator confirms success criteria)

## Prerequisites

No prerequisites — this work has no external dependencies. (`SMTP_USER` is read
at delivery time by the relay, exactly as the existing email path already does;
the sync payload builder reads it from `os.environ` with a safe default, matching
`_send_via_email_outbox`.)

## Solution

### Key Elements

- **Shared email-payload builder** (`agent/output_handler.py`): a module-level
  pure function `build_email_outbox_payload(session, chat_id, text, file_paths=None)
  -> dict` carrying the reply-all `to` construction, `Re:` subject prefixing,
  `in_reply_to`/`references`/`from_addr` derivation. Extracted verbatim from
  `_send_via_email_outbox`, which is refactored to call it. No behavior change to
  the existing async email send.
- **Email branch in the sync flush** (`agent/session_health.py`): after the
  narration gate / empty-text canned notice, `flush_deferred_self_draft_sync`
  branches on transport. For `email` **on the `completed` status only**, it
  builds the payload via `build_email_outbox_payload` and `rpush`es it to
  `email:outbox:{session_id}` (`expire` = `OUTBOX_TTL`). Telegram behavior is
  unchanged.
- **Status forwarding** (`models/session_lifecycle.py`): the chokepoint passes
  the target `status` into `flush_deferred_self_draft_sync(session, status)` so
  the email branch can gate on `completed` and avoid colliding with the async
  helper's `failed`/`abandoned` ownership.

### Flow

Email session defers reply → work succeeds → `finalize_session(session, "completed")`
→ `flush_deferred_self_draft_sync(session, "completed")` → sees `transport=email`,
`status=completed`, acquires `self_draft_completed_flush_sent` lock → builds
reply-all payload → `rpush email:outbox:{sid}` → relay drains → SMTP send → human
receives reply.

### Technical Approach

- **Gate ordering (double-send safety):** resolve `transport` and `status`
  *before* acquiring the dedup lock. Proceed when: `transport != email` (telegram,
  all statuses — unchanged) OR (`transport == email` AND `status == "completed"`).
  Otherwise return early (email on non-completed stays on the async helper). This
  keeps the completed-flush lock from being burned on an email-failed path.
- **One dedup lock for the whole flush:** keep the existing single SETNX on
  `self_draft_completed_flush_sent:{session_id}` (1 h). It is acquired once after
  the gate; the transport branch only decides *which* outbox to write. The async
  helper's distinct `self_draft_fallback_sent` key is untouched.
- **Mutually-exclusive paths:** `finalize_session` is idempotent (first terminal
  wins; re-transitions rejected), so a session reaches exactly one terminal
  status. Email-completed (sync) and email-failed/abandoned (async) cannot both
  fire for the same session — no double-send despite distinct dedup keys.
- **Fully synchronous:** the email branch only `rpush`es a JSON payload to Redis
  (the relay does the SMTP send asynchronously later). No event loop, no `await`,
  matching the telegram branch — correct for the `completed` path which runs with
  no running loop.
- **Exception isolation preserved:** the whole flush stays wrapped in the
  existing try/except; an email-build or Redis failure logs at WARNING and never
  blocks the status write.
- **`status` default:** `status: str | None = None`. When `None` (unknown
  caller), the email branch does NOT fire (conservative — avoids any speculative
  email send). Telegram is unaffected by `status`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `flush_deferred_self_draft_sync` is wrapped in `try/except Exception` that
  logs at WARNING (`agent/session_health.py:1696-1701`). Add a test asserting an
  email-build failure (e.g. Redis `rpush` raising) is swallowed and the terminal
  status write still happens — extend the existing
  `test_flush_exception_does_not_block_terminal_status`.
- [ ] `build_email_outbox_payload` is pure (no try/except needed); the I/O
  `rpush`/`expire` in the email branch is inside the flush's outer try/except.

### Empty/Invalid Input Handling
- [ ] Empty/whitespace `deferred_self_draft_text` → canned notice
  ("I couldn't finish responding to that — please try again.") delivered to the
  email outbox. Add an email variant of `test_empty_deferred_text_delivers_canned_notice`.
- [ ] Missing `email_subject` → `build_email_outbox_payload` yields
  `"Re: (no subject)"` (existing behavior, now unit-tested directly).
- [ ] Missing `email_message_id` → `in_reply_to`/`references` are `None` (valid
  payload, no threading). Assert in a builder unit test.

### Error State Rendering
- [ ] User-visible output is the email reply. Test that the email outbox payload
  `body` equals the held text verbatim (the delivered reply is exactly what was
  drafted), and that the canned notice renders on empty text rather than sending
  nothing.

## Test Impact

- [ ] `tests/unit/test_deferred_self_draft_completed.py::test_email_transport_gate_writes_zero_telegram_outbox`
  — REPLACE: under the old design email-completed produced no delivery; now it
  writes to `email:outbox`. Rewrite to assert (a) zero telegram outbox writes AND
  (b) exactly one `email:outbox:{sid}` entry with verbatim `body` and correct
  `to`/`subject`/`in_reply_to`/`from_addr`.
- [ ] `tests/unit/test_deferred_self_draft_completed.py::test_flush_exception_does_not_block_terminal_status`
  — UPDATE: add an email-transport variant ensuring an email-build/`rpush`
  failure is isolated and the status still commits.
- [ ] `tests/unit/test_session_health_tool_timeout.py` — verify still green
  (the async email helper's `failed`/`abandoned` behavior is unchanged). No code
  edit expected; run to confirm no regression.
- [ ] `tests/unit/test_output_handler*.py` (if present) — verify
  `_send_via_email_outbox` still produces identical payloads after the
  extraction. Add a direct unit test for `build_email_outbox_payload` covering
  reply-all dedup, SMTP_USER/self filtering, subject prefixing, empty subject,
  and missing message-id.

New test cases to add:
- [ ] email-completed delivers verbatim reply-all payload exactly once
- [ ] email-completed exactly-once across a re-finalize (idempotency)
- [ ] email on `failed`/`abandoned` writes **zero** `email:outbox` entries via
  the sync flush (delivery stays on the async helper — proves no double-send)
- [ ] email-completed with `status=None` does NOT write (conservative gate)

## Rabbit Holes

- **Do not** rewrite or remove the async `_deliver_deferred_self_draft_fallback`
  helper. Its `failed`/`abandoned` email ownership is correct and working;
  collapsing both transports/paths into one mechanism is a larger refactor than
  this bug warrants and risks regressing the recovery branches.
- **Do not** add a top-level `transport` field to `AgentSession`. Transport lives
  in `extra_context` by existing convention; respect it.
- **Do not** attempt to send SMTP synchronously from the flush. The relay owns
  SMTP; the flush only enqueues. Trying to send inline reintroduces the event-loop
  problem #1796 solved.
- **Do not** unify the two dedup keys. They are intentionally distinct; mutual
  exclusion by terminal status already prevents double-send.

## Risks

### Risk 1: Double-send if the email branch fires on a non-completed terminal path
**Impact:** A `failed`/`abandoned` email session could get two replies (async
helper + sync flush).
**Mitigation:** The email branch is gated on `status == "completed"`. Because
`finalize_session` is idempotent (one terminal status per session), the sync
email path and the async email path are mutually exclusive. A dedicated test
asserts zero sync-flush email writes on `failed`/`abandoned`.

### Risk 2: Payload divergence between the async handler and the sync flush
**Impact:** Email sent via the completed path could thread/address differently
than the failed-path email, confusing recipients.
**Mitigation:** Both call the single extracted `build_email_outbox_payload`. A
unit test pins the builder's output; `_send_via_email_outbox` is refactored to
call it so the two paths cannot drift.

### Risk 3: `chat_id` not being the sender's email for some email sessions
**Impact:** Reply-all `to` list could be malformed.
**Mitigation:** The async helper already relies on `session.chat_id` as the
primary recipient for email (`agent/session_health.py:1794`); the sync flush uses
the same source, so behavior is identical to the proven path. The builder's
dedup/self-filter tolerates an empty or duplicate primary gracefully.

## Race Conditions

### Race 1: Stale `extra_context` at flush time
**Location:** `agent/session_health.py:1619-1626`
**Trigger:** The defer-time persist (`deferred_self_draft_pending`) may post-date
the caller's in-memory `session` copy.
**Data prerequisite:** the deferral flag and held text must be readable when the
flush runs.
**State prerequisite:** the authoritative session reflects the defer write.
**Mitigation:** The flush already re-reads via `get_authoritative_session` rather
than trusting the caller's `extra_context`. The email branch reads from the same
fresh `source`. No change needed; documented for completeness.

### Race 2: Concurrent terminal transitions racing the dedup lock
**Location:** `agent/session_health.py:1637-1644` (SETNX)
**Trigger:** Two terminal-transition attempts for the same session.
**Data prerequisite:** the held text.
**State prerequisite:** only one flush should write.
**Mitigation:** Single atomic SETNX on `self_draft_completed_flush_sent:{sid}`
(1 h) — first caller wins. Unchanged from #1796.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1794] Telegram completed-path flush — already shipped via PR
  #1796; this plan does not touch it.

Nothing else deferred — the email-completed path is fully in scope and finished
within this plan.

## Update System

No update system changes required — this is a purely internal delivery-path bug
fix. No new dependencies, no config files, no Popoto schema changes (no model
fields added or modified), and therefore no `scripts/update/migrations.py` entry.

## Agent Integration

No agent integration required — this is a worker/bridge-internal delivery path.
No new CLI entry point in `pyproject.toml [project.scripts]`, no MCP server or
`.mcp.json` change, and no new bridge import. The fix operates entirely inside
the existing `finalize_session → flush_deferred_self_draft_sync → email outbox →
relay` chain that the email bridge already drives.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` — "Deferred Self-Draft Fallback
  Delivery" section: state that the sync chokepoint flush now covers **email on
  the completed path** (in addition to telegram on all paths), document the
  `status`-gated email branch and the shared `build_email_outbox_payload`, and
  **remove the "known email-completed-path gap"** note left by #1796.
- [ ] Update `docs/features/agent-message-delivery.md` cross-reference if it
  describes the deferred self-draft delivery transports.

### External Documentation Site
- [ ] N/A — this repo's docs are plain Markdown under `docs/`; no Sphinx/MkDocs
  build to run.

### Inline Documentation
- [ ] Docstring on `build_email_outbox_payload` describing the shared recipe and
  that it is pure/synchronous.
- [ ] Update `flush_deferred_self_draft_sync`'s docstring: it is no longer
  "TELEGRAM ONLY" — it now also handles email on the `completed` status.

## Success Criteria

- [ ] An email-transport deferred self-draft reaching `completed` writes exactly
  one valid payload to `email:outbox:{session_id}` with verbatim `body` and
  correct reply-all `to`/`subject`/`in_reply_to`/`from_addr`.
- [ ] An email-transport deferred self-draft reaching `failed`/`abandoned`
  produces zero sync-flush email writes (delivery stays on the async helper) —
  no double-send.
- [ ] Telegram behavior is byte-for-byte unchanged (existing tests stay green).
- [ ] `_send_via_email_outbox` and the sync flush produce identical payloads
  (shared builder; grep confirms both call `build_email_outbox_payload`).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`); the "known email-completed-path gap"
  note is removed from `docs/features/session-lifecycle.md`.
- [ ] `grep` confirms `flush_deferred_self_draft_sync` references
  `build_email_outbox_payload` and `email:outbox`.

## Team Orchestration

### Team Members

- **Builder (delivery)**
  - Name: email-flush-builder
  - Role: Extract `build_email_outbox_payload`, add the email branch + `status`
    gate to the sync flush, forward `status` from the chokepoint.
  - Agent Type: builder
  - Resume: true

- **Validator (delivery)**
  - Name: email-flush-validator
  - Role: Verify all success criteria, especially no-double-send and payload
    parity.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extract the shared email payload builder
- **Task ID**: build-payload-extract
- **Depends On**: none
- **Validates**: tests/unit/test_output_handler*.py (or new builder test), tests/unit/test_deferred_self_draft_completed.py
- **Assigned To**: email-flush-builder
- **Agent Type**: builder
- **Parallel**: false
- Add module-level `build_email_outbox_payload(session, chat_id, text, file_paths=None) -> dict` in `agent/output_handler.py`, lifting the reply-all/subject/threading/from_addr logic verbatim from `_send_via_email_outbox` (`:226-278`).
- Refactor `_send_via_email_outbox` to call the new builder (no behavior change).
- Add a direct unit test for the builder: reply-all dedup, SMTP_USER + primary filtering, `Re:` prefix, empty subject → `"Re: (no subject)"`, missing message-id → `None` threading.

### 2. Add the email branch + status gate to the sync flush
- **Task ID**: build-sync-email-branch
- **Depends On**: build-payload-extract
- **Validates**: tests/unit/test_deferred_self_draft_completed.py
- **Assigned To**: email-flush-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `flush_deferred_self_draft_sync(session)` → `flush_deferred_self_draft_sync(session, status: str | None = None)`.
- Move the transport/status gate before the dedup SETNX: proceed for telegram (any status) or email+completed; return otherwise.
- After acquiring the lock and computing `message`, branch: telegram → existing telegram outbox write; email → `payload = build_email_outbox_payload(source, chat_id, message)`, `rpush email:outbox:{sid}`, `expire OUTBOX_TTL`.
- Update the docstring (no longer "TELEGRAM ONLY").
- In `models/session_lifecycle.py:409`, forward the target status: `flush_deferred_self_draft_sync(session, status)`.

### 3. Tests for email-completed delivery and no-double-send
- **Task ID**: build-tests
- **Depends On**: build-sync-email-branch
- **Validates**: tests/unit/test_deferred_self_draft_completed.py
- **Assigned To**: email-flush-builder
- **Agent Type**: builder
- **Parallel**: false
- REPLACE `test_email_transport_gate_writes_zero_telegram_outbox` with an email-delivery assertion (verbatim payload, correct envelope, zero telegram writes).
- Add: email-completed exactly-once across re-finalize; email failed/abandoned → zero sync email writes; email empty-text → canned notice in email outbox; `status=None` → no email write.
- Add email variant of `test_flush_exception_does_not_block_terminal_status`.
- Run `tests/unit/test_session_health_tool_timeout.py` to confirm the async helper path is unregressed.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: email-flush-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-lifecycle.md` (email now covered on completed; remove the known-gap note).
- Update cross-reference in `docs/features/agent-message-delivery.md` if needed.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: email-flush-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands.
- Confirm no-double-send and payload-parity criteria.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_deferred_self_draft_completed.py tests/unit/test_session_health_tool_timeout.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_health.py agent/output_handler.py models/session_lifecycle.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py agent/output_handler.py models/session_lifecycle.py` | exit code 0 |
| Sync flush writes email outbox | `grep -n "email:outbox" agent/session_health.py` | output contains email:outbox |
| Shared builder used by both | `grep -rn "build_email_outbox_payload" agent/session_health.py agent/output_handler.py` | output > 1 |
| Status forwarded at chokepoint | `grep -n "flush_deferred_self_draft_sync(session, status)" models/session_lifecycle.py` | exit code 0 |
| Known-gap note removed | `grep -c "known email-completed-path gap\|email-completed-path gap" docs/features/session-lifecycle.md` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The issue, recon, and the #1796 precedent fully specify scope, approach,
and the double-send safety argument. Proceeding to critique.
