---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2204
last_comment_id:
---

# Catchup/reconciler re-enqueues already-handled messages

## Problem

The Telegram bridge has a message-recovery layer that re-scans chat history for
messages missed during downtime: a startup catchup scan
(`bridge/catchup.py::scan_for_missed_messages`), a periodic reconciler
(`bridge/reconciler.py::reconcile_once`, every ~3 min), and an LLM-judged sweep
(`valor-catchup`, `bridge/agent_catchup.py`, run as `/update` Step 9). All three
re-enqueue any historical message they judge "not yet handled." That judgment is
failing.

**Current behavior:** After a bridge restart (e.g. `/update`), the recovery scans
re-enqueue Telegram messages that were already fully handled, producing duplicate
sessions and duplicate replies to the human. The blast radius is currently
contained only because the operator kill switch `data/catchup-disabled` (commit
c366bdb84) is SET on the primary machine, which disables the *entire* recovery
layer until this bug is fixed.

**Desired outcome:** Recovery scans never re-enqueue a message that was already
dispatched, regardless of how it was answered (text reply, non-reply message,
emoji reaction, or a deliberate no-reply judgment) or how long ago — while still
recovering genuinely missed messages. The kill switch is then removed and
recovery re-enabled.

## Root Cause (confirmed by code reading)

The "already handled" decision is a chain of guards on the two mechanical
scanners, each with a hole:

1. **Durable dedup TTL (2h) is shorter than the effective scan window.**
   `bridge/dedup.py::is_duplicate_message` checks a per-chat `DedupRecord`
   membership set (`models/dedup.py`, `Meta.ttl = 7200`). But the per-chat cutoff
   (issue #1408, `catchup.py:136-144`) extends the lookback back to the
   `LastProcessedRecord` cursor timestamp minus 60s. **That extension is NOT
   bounded by the 24h cap** — the 24h cap (`catchup.py:70-71`) applies only to
   `lookback_override`, while the per-chat `min(cutoff, cursor_dt - 60s)` can
   reach back as far as the cursor's own TTL (**30 days**, `last_processed_ttl_s`
   default 2592000). So the real scan window is up to ~30 days, and any message
   handled more than 2h before a restart has aged out of dedup and falls through
   to guard 2. (This corrects the issue title's "24h" framing — the window is
   cursor-TTL-bounded, not 24h-bounded.)

2. **`_check_if_handled` only recognizes an explicit threaded reply.**
   `catchup.py:338` fetches the 10 messages after the candidate and returns True
   only if one is `out` AND `reply_to_msg_id == message.id`. This misses:
   emoji-reaction acks (the repo's preferred "I heard you" signal, sent via
   `bridge/response.py::SendReactionRequest` — leaves no reply message),
   non-reply channel answers, replies more than 10 messages later, and deliberate
   no-reply judgments. On any exception it returns False ("better to
   double-process than miss"), compounding the above. **The reconciler
   (`reconcile_once`) has no `_check_if_handled` guard at all** — it relies solely
   on guard 1, so it re-enqueues even more aggressively once dedup ages out.

3. The short-TTL producer claim (`bridge:msgclaim:*`, seconds-scale, #1817) only
   prevents concurrent double-enqueue, not re-enqueue across restarts. Working as
   designed.

**Key structural insight for the fix:** the `LastProcessedRecord` cursor is
monotonic — a single per-chat high-water mark. It **cannot** be used as the skip
signal (`message.id <= cursor`), because that would wrongly skip an out-of-order
*gap* message (id below the high-water mark but never dispatched) — the exact
Telethon-gap scenario the scanners exist to recover (#1408). The authoritative
"was this specific message dispatched" record must be the per-message
`DedupRecord` **set**, not the cursor. The set already exists and is written on
every dispatch (`dispatch.py:187`, `catchup.py:319`, `reconciler.py:290`) — its
only defect is a TTL far shorter than the window it must cover.

## Freshness Check

**Baseline commit:** 3c0fc7ee1
**Issue filed at:** 2026-07-22T06:58:39Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/dedup.py` `is_duplicate_message` / `DedupRecord` 2h TTL — still holds; `models/dedup.py` `Meta.ttl = 7200`.
- `bridge/catchup.py:338` `_check_if_handled` reply-only heuristic — still holds; verbatim as described.
- `bridge/catchup.py:136-144` per-chat cursor lookback extension — still holds; confirmed the 24h cap does NOT bound the cursor extension.
- `models/last_processed.py` cursor TTL 30d (`last_processed_ttl_s` default 2592000) — confirmed.
- `bridge/agent_catchup.py` module docstring: deliberately does NOT read `is_duplicate_message`; idempotency via landed-reply guard; thread is source of truth — confirmed.

**Cited sibling issues/PRs re-checked:**
- #1408 (cursor + reconciler lookback) — CLOSED/merged (PR #1559). Landscape as described.
- #1817 / #1864 (atomic per-message claim) — CLOSED/merged (PR #1870). Claim TTL intentionally short.
- #1709 (agent-judgment catchup) — CLOSED/merged (PR #1715). valor-catchup as described.
- #948 (centralize dedup recording) — CLOSED/merged (PR #952). Forbids a new answered-ness watermark; the fix here reuses the existing dedup path, not a new store.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since` on catchup/reconciler/dedup/agent_catchup returned empty).

**Active plans in `docs/plans/` overlapping this area:**
- `issue-2179-null-msgid-dedup.md` — adjacent (null message-id handling in the dedup path) but a distinct concern; no scope overlap with the TTL/handled-check fix. Coordinate only if both touch `bridge/dedup.py` signatures.

**Notes:** Bug confirmed still present against current main by code reading (reproduction requires a live bridge restart + aged dedup, infeasible to trigger in a unit run — validated by the code path instead).

## Prior Art

- **PR #1559 (#1408)**: close catchup dead zone + extend reconciler lookback — introduced the `LastProcessedRecord` cursor and the per-chat lookback extension. This is the mechanism that widened the scan window past the 2h dedup TTL, exposing the current bug. Its cursor is the right *lookback* signal but the wrong *skip* signal.
- **PR #952 (#948)**: centralize dedup recording in dispatch wrapper — established that all dispatch paths write `record_message_processed` + `record_last_processed` through one wrapper. Confirms the dedup set is already written on every dispatch; forbids adding a new answered-ness store.
- **PR #1870 (#1817/#1864)**: atomic per-message claim — the short-TTL claim key; explicitly decoupled from the durable dedup set. Confirms the claim is not the cross-restart guard.
- **PR #1715 (#1709)**: agent-judgment /catchup — added `valor-catchup`, which reads the thread rather than dedup and is idempotent via a landed-reply guard. Its blind spot is reaction-only acks (a reaction leaves no reply message).
- **PR #590**: original periodic reconciler.

## Research

No relevant external findings — proceeding with codebase context. This is a
purely internal bridge fix (Redis TTL contract + Telethon reaction reads); no
external libraries or ecosystem patterns are involved.

## Data Flow

1. **Dispatch (live / reconciler / catchup)**: on every successful enqueue of an
   inbound message, `dispatch.py`/`reconciler.py`/`catchup.py` call
   `record_message_processed(chat_id, msg_id)` (writes the `DedupRecord` set,
   2h TTL today) and `record_last_processed(chat_id, msg_id, ts)` (advances the
   `LastProcessedRecord` cursor, 30d TTL).
2. **Restart**: `data/last_connected` gives a global cutoff; catchup extends it
   per-chat back to `cursor_ts - 60s` (up to 30d).
3. **Scan candidate loop**: for each fetched message within `per_chat_cutoff`,
   the scanner checks `is_duplicate_message` (guard 1). If the message aged out of
   the 2h dedup set → catchup falls through to `_check_if_handled` (guard 2,
   reply-only); the reconciler falls straight through to `should_respond_fn` and
   **re-enqueues**.
4. **valor-catchup**: reads the recent thread (`read_thread`), asks an LLM judge
   "did Valor reply?" A reaction-only ack has no `out` reply message → judged
   `UNANSWERED_NEEDS_REPLY` → re-enqueue.

The fix moves the authoritative skip decision to guard 1 by making the dedup set
cover the full scan window, and closes the valor-catchup reaction blind spot at
the thread-read layer.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm TTL-unification direction vs. alternatives in Open Questions)
- Review rounds: 1 (dedup TTL contract change + scar-tissue deletion warrant a review pass)

## Prerequisites

No prerequisites — this work has no external dependencies (Redis + Telethon
already in use; no new secrets or services).

## Solution

### Key Elements

- **Unified durable-dedup TTL**: `DedupRecord` remembers every dispatched
  message for at least as long as the cursor can extend the lookback. The dedup
  set becomes the single authoritative "already dispatched" record for exactly the
  messages a scan can reach.
- **Scar-tissue removal**: delete `_check_if_handled` — the reply-only heuristic
  whose holes are the bug. Once guard 1 is authoritative, guard 2 is dead weight
  (and the reconciler already runs without it, so this unifies the two scanners).
- **Reaction-aware valor-catchup**: the LLM sweep treats a Valor emoji reaction on
  an inbound message as "handled," closing the one hole the mechanical dedup fix
  does not reach (valor-catchup deliberately ignores dedup and reads the thread).

### Flow

Bridge restart → catchup/reconciler scan → candidate message within window →
`is_duplicate_message` (now authoritative over the full window) → **skip if
dispatched, recover if genuinely missed**. Separately: valor-catchup thread read →
Valor reaction present on message → treat as answered → no re-enqueue.

### Technical Approach

**Workstream 1 — make the durable dedup set authoritative over the scan window (mechanical scanners):**

- Wire `DedupRecord.Meta.ttl` to a settings-backed value that is coupled to the
  cursor TTL. Add a `dedup_record_ttl_s` field to `config/settings.py` whose
  default equals `last_processed_ttl_s` (30d) — the two must move together because
  the cursor determines the maximum lookback and the dedup set must remember every
  dispatched message for that whole window. Add a GRAIN OF SALT comment marking it
  provisional/tunable (per the magic-numbers rule) and an env override
  (`TIMEOUTS__DEDUP_RECORD_TTL_S`).
- Ensure the per-chat retention cap covers the scan's fetch window. Today
  `DedupRecord._MAX_IDS = 50` == `MAX_MESSAGES_PER_CHAT = 50` (catchup) — edge-exact
  after trim. Bump `_MAX_IDS` to give margin over the largest scanner fetch limit
  (e.g. `2 × MAX_MESSAGES_PER_CHAT`) so a busy chat's most-recent-N inbound IDs are
  all retained. The scanner can only *reach* the most-recent `MAX_MESSAGES_PER_CHAT`
  messages, so the set only needs to cover that many inbound IDs — memory stays
  trivial (a handful of chats × ~100 short strings).
- **Delete `_check_if_handled`** and its call site in `catchup.py`. This is
  scar-tissue removal per the no-legacy-code policy — guard 1 now covers every
  answer type (reply, non-reply, reaction, deliberate no-reply) because the dedup
  set is written at *dispatch* time regardless of how the message was answered.
- Do NOT add a cursor-based `message.id <= cursor` skip. The cursor is monotonic
  and cannot distinguish "dispatched below the high-water mark" from "missed gap
  below the high-water mark" — a range skip would re-break #1408.

**Workstream 2 — close the valor-catchup reaction hole (LLM sweep):**

- Capture per-message reactions in `read_thread`/`ThreadMessage`
  (`bridge/agent_catchup.py`) from the Telethon message object, specifically
  whether *Valor's own* account reacted. A Valor reaction is a thread-native
  "handled" signal, consistent with #948's "thread is source of truth, no new
  watermark store."
- Before enqueue (or in the judge's ANSWERED contract), treat "Valor reacted to
  this inbound message" as ANSWERED. This does not require reading the dedup set,
  preserving the module's landed-reply idempotency design.

**Popoto schema note:** `DedupRecord.Meta.ttl` changes value but not shape (no new
fields). Per the repo's Popoto migration requirement, add an idempotent migration
in `scripts/update/migrations.py` only if the TTL change needs to be applied to
existing keys; since Popoto TTL is set on write, existing short-TTL keys will
simply re-acquire the new TTL on the next `add_message`/`get_or_create` — assess
whether an explicit `EXPIRE`-refresh migration is warranted or whether natural
churn suffices (document the decision in the migration file or plan note).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `is_duplicate_message`, `record_message_processed`, `record_last_processed`,
      `get_last_processed` all swallow exceptions and log — existing tests in
      `tests/unit/test_dedup.py` assert the fail-open behavior; keep them green.
- [ ] `read_thread` reaction extraction must be defensive (a message with no
      reactions / a Telethon shape change must not raise) — add a test asserting
      a missing-reactions message is treated as not-reacted, not an exception.

### Empty/Invalid Input Handling
- [ ] Confirm behavior when a chat has no `DedupRecord` yet (fresh chat) — the
      genuinely-missed message must still be recovered.
- [ ] Confirm a `None`/empty reaction list in valor-catchup maps to "not reacted"
      (conservative → still ANSWERED only via the existing reply guard, never a
      spurious skip).

### Error State Rendering
- [ ] No user-visible rendering path changes; the observable output is
      "no duplicate reply reaches the human." Covered by the integration assertions
      in Test Impact (a handled message produces zero new enqueues).

## Test Impact

- [ ] `tests/unit/test_dedup.py::test_ttl_is_set` — UPDATE: currently asserts
      `DedupRecord._meta.ttl == 7200`; change to assert the new settings-backed TTL
      (equals `dedup_record_ttl_s` / `last_processed_ttl_s`).
- [ ] `tests/unit/test_dedup.py::test_claim_ttl_is_seconds_scoped_and_short` —
      UPDATE (docstring/comment only): the assertion that the claim TTL is decoupled
      from the dedup membership TTL still holds; update the "2h DedupRecord" wording.
- [ ] `tests/unit/test_dedup.py` trimming tests — UPDATE if `_MAX_IDS` changes:
      re-derive expected retained-count against the new cap.
- [ ] `tests/unit/test_catchup_claim.py`, `tests/integration/test_per_chat_catchup_cutoff.py`,
      `tests/integration/test_catchup_revival.py`, `tests/unit/test_duplicate_delivery.py` —
      these reference `_check_if_handled`; REPLACE the reply-only-handled assertions
      with dedup-authoritative assertions (a dispatched message is skipped via
      `is_duplicate_message` across the full window; delete assertions that exercise
      the removed heuristic).
- [ ] `tests/unit/test_reconciler.py`, `tests/integration/test_reconciler.py` —
      UPDATE/extend: add the >2h-old-handled-message-after-restart skip case.
- [ ] `tests/unit/test_agent_catchup.py`, `tests/integration/test_agent_catchup_recovery.py` —
      UPDATE: add the reaction-only-ack case (Valor reacted → judged ANSWERED → no enqueue).

## Rabbit Holes

- **Reworking the cursor into a per-message log.** Tempting to make the cursor
  richer so it can serve as the skip signal. Don't — the `DedupRecord` set already
  is the per-message record; just fix its TTL.
- **Rewriting valor-catchup to consult the dedup set.** Its design (#948)
  deliberately keeps it thread-truth-based for idempotency. Add reaction-awareness
  at the thread-read layer instead of coupling it to Redis dedup.
- **Making `_check_if_handled` thread/reaction-aware instead of deleting it.**
  Patching the heuristic's holes is scar tissue; deletion is the mandated move.
- **Tuning TTL to a clever middle value (e.g. 25h).** A value shorter than the
  cursor TTL leaves the quiet-chat long-lookback hole open. Couple it to the
  cursor TTL and stop.

## Risks

### Risk 1: Longer dedup TTL bloats Redis or accumulates ghost index members
**Impact:** `DedupRecord` keys live ~30d instead of 2h.
**Mitigation:** Per-chat sets are capped at `_MAX_IDS` (~50-100 short strings) and
keyed per monitored chat (a handful). Memory is trivial. The #1817 C3 note
identifies the *short* 2h TTL as the ghost-prone case (hash expires, class-set
membership survives); a longer TTL *reduces* ghost churn, and `reconcile_ghost_members`
still runs on `get_or_create`. Net hygiene improvement.

### Risk 2: Deleting `_check_if_handled` removes a recovery-safety net
**Impact:** If guard 1 had a gap, guard 2 previously (weakly) backstopped it.
**Mitigation:** Guard 1 is now authoritative over the exact set of messages a scan
can fetch (dedup `_MAX_IDS` ≥ scanner fetch limit). Add explicit tests for the
>2h-old-handled and reaction-only cases to prove no regression before removal.

### Risk 3: Reaction detection reads the wrong "who reacted"
**Impact:** Treating *any* reaction (including a human's) as Valor-handled would
suppress genuinely-unanswered messages.
**Mitigation:** Scope the check to Valor's own account's reaction only; default to
"not reacted" on any ambiguity (conservative — a missed reply is recoverable next
sweep, a suppressed genuine question is not).

## Race Conditions

### Race 1: Concurrent dispatch write vs. scan read of the dedup set
**Location:** `bridge/dedup.py` (`add_message` / `has_message`), scanners.
**Trigger:** A live dispatch writes the dedup set while a reconciler scan reads it.
**Data prerequisite:** The dedup set entry must be written before a competing
scanner reads it to decide skip-vs-enqueue.
**State prerequisite:** The atomic per-message claim (`claim_message`, #1817) still
governs concurrent producers; the durable dedup write happens only after a
successful enqueue by the claim winner. This ordering is unchanged by this plan.
**Mitigation:** No new race — the TTL change does not alter write ordering. The
claim key remains the concurrency guard; the dedup set remains the cross-restart
guard. Preserve the existing "record only after successful enqueue" ordering.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2179] Null/absent message-id handling in the dedup path — tracked
  separately in `docs/plans/issue-2179-null-msgid-dedup.md`; not touched here.
- [EXTERNAL] Removing `data/catchup-disabled` on the primary machine — this is an
  operator action on a live machine the build agent cannot safely reach mid-build;
  it happens after the fix lands and is verified (acceptance criterion tracked, but
  the `rm` is a human/deploy step).

## Update System

The `/update` flow runs `valor-catchup` as Step 9 and restarts the bridge. If a
migration is added for the `DedupRecord` TTL refresh, it must be registered in
`scripts/update/migrations.py`'s `MIGRATIONS` dict (idempotent, recorded in
`data/migrations_completed.json`). No new dependencies or config files to
propagate beyond the new `TIMEOUTS__DEDUP_RECORD_TTL_S` env knob (optional, has a
default). Otherwise no update-script changes required.

## Agent Integration

No agent integration required — this is a bridge-internal fix. The three recovery
scanners already run inside the bridge/`/update` flow; `valor-catchup` is an
existing CLI entry point. No new MCP surface or tool wiring. The bridge must be
restarted after the fix lands (per the always-restart-running-services rule), and
`data/catchup-disabled` removed to re-enable recovery.

## Documentation

### Feature Documentation
- [ ] Update the recovery/catchup feature doc(s) under `docs/features/` (e.g. the
      bridge catchup/reconciler doc and `bridge/agent_catchup.py`'s referenced doc)
      to describe: the unified dedup TTL contract (dedup set is authoritative over
      the full cursor-bounded scan window), the removal of `_check_if_handled`, the
      valor-catchup reaction-awareness, and the `data/catchup-disabled` kill switch.
- [ ] Add/verify an entry in `docs/features/README.md` index if a new doc is added.

### Inline Documentation
- [ ] Update `models/dedup.py` docstring (no longer "2h TTL"; now cursor-coupled).
- [ ] Update the `config/settings.py` comment block that currently describes the
      "durable 2h DedupRecord membership set."
- [ ] Docstring on the new `dedup_record_ttl_s` field with the GRAIN OF SALT note.

## Success Criteria

- [ ] A message dispatched at any point within the (cursor-bounded) scan window is
      skipped by startup catchup and the reconciler after a restart — including
      messages answered by emoji reaction only, by non-reply message, or
      deliberately not answered. (Covered by the >2h-old-handled test.)
- [ ] valor-catchup treats a Valor reaction on an inbound message as ANSWERED and
      does not re-enqueue it. (Covered by the reaction-only-ack test.)
- [ ] A genuinely missed message (no dispatch ever happened) is still recovered by
      all three scanners. (Regression test.)
- [ ] `_check_if_handled` and its call site are deleted; no remaining reference in
      `bridge/` or `tests/`.
- [ ] `DedupRecord` TTL is settings-backed, defaults to the cursor TTL, and is
      env-overridable.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `data/catchup-disabled` removed on the primary machine and recovery
      re-enabled (operator step, post-merge).

## Team Orchestration

### Team Members

- **Builder (dedup-authoritative)**
  - Name: dedup-builder
  - Role: WS1 — settings-backed TTL, `_MAX_IDS` margin, delete `_check_if_handled`, update mechanical-scanner tests
  - Agent Type: builder
  - Domain: async/concurrency + Redis/Popoto data
  - Resume: true

- **Builder (reaction-aware valor-catchup)**
  - Name: reaction-builder
  - Role: WS2 — capture Valor reactions in `read_thread`, treat as ANSWERED, tests
  - Agent Type: builder
  - Domain: MCP-tool/API integration (Telethon)
  - Resume: true

- **Validator**
  - Name: catchup-validator
  - Role: Verify both workstreams against success criteria; run targeted tests
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: catchup-docs
  - Role: Feature + inline doc updates
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. WS1: make the durable dedup set authoritative
- **Task ID**: build-dedup-authoritative
- **Depends On**: none
- **Validates**: tests/unit/test_dedup.py, tests/unit/test_reconciler.py, tests/integration/test_reconciler.py, tests/integration/test_per_chat_catchup_cutoff.py, tests/unit/test_catchup_claim.py, tests/unit/test_duplicate_delivery.py
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `dedup_record_ttl_s` to `config/settings.py` (default = `last_processed_ttl_s`, env `TIMEOUTS__DEDUP_RECORD_TTL_S`, GRAIN OF SALT comment).
- Wire `DedupRecord.Meta.ttl` to the setting; bump `_MAX_IDS` to give margin over `MAX_MESSAGES_PER_CHAT`.
- Delete `_check_if_handled` and its call site in `catchup.py`.
- Decide + document whether a `scripts/update/migrations.py` TTL-refresh migration is warranted or natural churn suffices.
- Update the affected mechanical-scanner tests (see Test Impact); add the >2h-old-handled-after-restart skip test.

### 2. WS2: reaction-aware valor-catchup
- **Task ID**: build-reaction-aware
- **Depends On**: none
- **Validates**: tests/unit/test_agent_catchup.py, tests/integration/test_agent_catchup_recovery.py
- **Assigned To**: reaction-builder
- **Agent Type**: builder
- **Parallel**: true
- Capture Valor's own reaction per message in `read_thread`/`ThreadMessage` (defensive against missing reactions).
- Treat "Valor reacted" as ANSWERED before enqueue; default to not-reacted on ambiguity.
- Add the reaction-only-ack test.

### 3. Validation
- **Task ID**: validate-catchup
- **Depends On**: build-dedup-authoritative, build-reaction-aware
- **Assigned To**: catchup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria; confirm no `_check_if_handled` references remain; run the targeted test set.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-catchup
- **Assigned To**: catchup-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update recovery/catchup feature docs + inline docstrings per the Documentation section.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: catchup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification table; confirm docs exist; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Dedup tests pass | `pytest tests/unit/test_dedup.py -q` | exit code 0 |
| Reconciler tests pass | `pytest tests/unit/test_reconciler.py tests/integration/test_reconciler.py -q` | exit code 0 |
| Agent-catchup tests pass | `pytest tests/unit/test_agent_catchup.py -q` | exit code 0 |
| `_check_if_handled` fully removed | `grep -rn "_check_if_handled" bridge/ tests/` | match count == 0 |
| Dedup TTL no longer hardcoded 7200 | `grep -n "7200" models/dedup.py` | match count == 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **TTL coupling vs. independence.** Recommended: `dedup_record_ttl_s` defaults to
   `last_processed_ttl_s` (30d) so the dedup set covers the full cursor-bounded
   lookback. Accept this coupling, or cap the effective scan window instead (e.g.
   bound the per-chat cursor lookback to 24h so a shorter dedup TTL suffices)?
   Capping the window is an alternative single-knob fix but reopens the #1408
   quiet-chat gap risk. Recommendation: couple the TTLs.
2. **Migration for existing dedup keys.** Should the build add an idempotent
   `EXPIRE`-refresh migration for in-flight `DedupRecord` keys, or is natural churn
   (keys re-acquire the new TTL on next write) acceptable given the kill switch is
   currently masking the bug anyway?
3. **valor-catchup scope.** Is thread-native reaction-awareness sufficient for the
   LLM sweep, or should valor-catchup additionally consult the now-authoritative
   dedup set as a belt-and-suspenders guard (a small departure from its #948
   thread-truth design)? Recommendation: reaction-awareness only, keep #948 intact.
