---
status: docs_complete
type: bug
appetite: Medium
owner: Valor
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2204
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-23T03:18:59Z
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

**Desired outcome:** The mechanical scanners (startup catchup + reconciler) never
re-enqueue a message that was already dispatched, regardless of how it was answered
(text reply, non-reply message, emoji reaction, or a deliberate no-reply judgment),
for any message within the cursor-TTL (~30-day) startup-catchup lookback window —
while still recovering genuinely missed messages. Separately,
`valor-catchup` (the LLM sweep) additionally recognizes a Valor emoji reaction as
"answered." (WS2 only adds *reaction* awareness to valor-catchup; the full
answer-type-agnostic guarantee is a property of the WS1 dedup path, not of the LLM
sweep — see Root Cause and the scope note in Workstream 2.) The kill switch is then
removed and recovery re-enabled.

## Root Cause (confirmed by code reading)

The "already handled" decision is a chain of guards on the two mechanical
scanners, each with a hole:

1. **Durable dedup TTL (2h) is shorter than the effective *startup-catchup* scan
   window.** `bridge/dedup.py::is_duplicate_message` checks a per-chat `DedupRecord`
   membership set (`models/dedup.py`, `Meta.ttl = 7200`). But the per-chat cutoff
   (issue #1408, `catchup.py:136-144`) extends the *startup catchup* lookback back
   to the `LastProcessedRecord` cursor timestamp minus 60s. **That extension is NOT
   bounded by the 24h cap** — the 24h cap (`catchup.py:70-71`) applies only to
   `lookback_override`, while the per-chat `min(cutoff, cursor_dt - 60s)` can
   reach back as far as the cursor's own TTL (**30 days**, `last_processed_ttl_s`
   default 2592000). So the real startup-catchup scan window is up to ~30 days, and
   any message handled more than 2h before a restart has aged out of dedup and falls
   through to guard 2. (This corrects the issue title's "24h" framing — the window is
   cursor-TTL-bounded, not 24h-bounded.)

   **Scope note (the reconciler is NOT part of this bug).** The periodic reconciler
   (`reconciler.py`) uses a *fixed* 30-minute lookback (`RECONCILE_LOOKBACK_MINUTES
   = 30`) with **no cursor extension** — it only ever reaches messages ≤30 min old,
   which is comfortably inside the 2h dedup TTL. So for the reconciler, guard 1
   never ages out and it cannot re-enqueue an aged-out handled message. The
   re-handling bug is **startup-catchup-only** (the cursor-extended lookback is the
   sole path that reaches past the dedup TTL). The reconciler is included in WS1
   only as a beneficiary (a longer dedup TTL is strictly harmless to it) and a
   regression guard, not as a bug site.

2. **`_check_if_handled` only recognizes an explicit threaded reply.**
   `catchup.py:338` fetches the 10 messages after the candidate and returns True
   only if one is `out` AND `reply_to_msg_id == message.id`. This misses:
   emoji-reaction acks (the repo's preferred "I heard you" signal, sent via
   `bridge/response.py::SendReactionRequest` — leaves no reply message),
   non-reply channel answers, replies more than 10 messages later, and deliberate
   no-reply judgments. On any exception it returns False ("better to
   double-process than miss"), compounding the above. This guard exists only on
   startup catchup (`catchup.py:214`); the reconciler has no such guard and does not
   need one (it stays inside the 2h dedup window, per the scope note above).

3. The short-TTL producer claim (`bridge:msgclaim:*`, seconds-scale, #1817) only
   prevents concurrent double-enqueue, not re-enqueue across restarts. Working as
   designed.

**Key structural insight for the fix:** the `LastProcessedRecord` cursor is
monotonic — a single per-chat high-water mark. It **cannot** be used as the
*live-scan* skip signal (`message.id <= cursor`), because on every recurring scan
that would wrongly skip an out-of-order *gap* message (id below the high-water mark
but never dispatched) — the exact Telethon-gap scenario the scanners exist to
recover (#1408). The authoritative "was this specific message dispatched" record
must be the per-message `DedupRecord` **set**, not the cursor. The set already
exists and is written on every dispatch (`dispatch.py:187`, `catchup.py:319`,
`reconciler.py:290`) — its only defect is a TTL far shorter than the window it must
cover.

**Why the one-time rollout seed may still use `id <= cursor` (not a contradiction).**
The prohibition above is about the *recurring* skip decision, where a permanent
`id <= cursor` filter would suppress gap recovery on every future scan. The rollout
seed is a different, one-shot use: it runs exactly once to repopulate the
already-aged-out dedup keys, and `id <= cursor` is the best available *proxy* for
"already dispatched" at seed time (the true per-message evidence was deleted with
the expired keys). Its only downside — seeding a genuine gap message that happens to
sit below the high-water mark — is the *same* pre-existing #1408 gap-below-cursor
exception the scanners already can't perfectly recover (Risk 4), it is bounded to
one rollout, and it is strictly better than the alternative (re-enqueuing the entire
handled backlog as duplicate replies). The cursor never becomes a live skip signal;
it is consulted once, at seed time, then the durable `DedupRecord` set resumes sole
authority.

## Freshness Check

**Baseline commit:** 3c0fc7ee1
**Issue filed at:** 2026-07-22T06:58:39Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/dedup.py` `is_duplicate_message` / `DedupRecord` 2h TTL — still holds; `models/dedup.py` `Meta.ttl = 7200`.
- `bridge/catchup.py:338` `_check_if_handled` reply-only heuristic — still holds; verbatim as described.
- `bridge/catchup.py:136-144` per-chat cursor lookback extension — still holds; confirmed the 24h cap does NOT bound the cursor extension.
- `bridge/reconciler.py:33` `RECONCILE_LOOKBACK_MINUTES = 30` fixed lookback, no cursor extension — confirmed (grounds the reconciler scope note).
- `models/last_processed.py` cursor TTL 30d (`last_processed_ttl_s` default 2592000) — confirmed.
- `bridge/agent_catchup.py` module docstring: deliberately does NOT read `is_duplicate_message`; idempotency via landed-reply guard; thread is source of truth — confirmed. Note: this file also names `_check_if_handled` in two docstrings (~line 38, ~line 385) — scrubbed in WS2.

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
   the scanner checks `is_duplicate_message` (guard 1). If a *startup catchup*
   message aged out of the 2h dedup set → catchup falls through to
   `_check_if_handled` (guard 2, reply-only) and **re-enqueues** on the holes above.
   The reconciler's fixed 30-min window never ages out of the 2h dedup set, so its
   guard-1 check is authoritative today — it is not a re-handling source.
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
- **One-time rollout dedup-seeding (per-chat idempotent)**: because the old 2h TTL
  already deleted the historical handled-message keys, re-seed the dedup set (from a
  live Telethon read) before the first post-fix scan so the fix's very first run does
  not re-enqueue the already-handled backlog. Required build step, not optional.
  Guarded by **per-chat** markers (`data/dedup-seeded.{chat_id}`), never a single
  global flag, so a partial per-chat failure self-heals on the next restart.
- **Observability + rollback lever**: structured seed/scan logging to detect a
  recurrence, and `data/catchup-disabled` re-touch as the documented one-command
  rollback if duplicate replies reappear (see Observability & Rollback).
- **Scar-tissue removal**: delete `_check_if_handled` — the reply-only heuristic
  whose holes are the bug. Once guard 1 is authoritative, guard 2 is dead weight
  (and the reconciler already runs without it, so this unifies the two scanners).
- **Reaction-aware valor-catchup**: the LLM sweep treats a Valor emoji reaction on
  an inbound message as "handled," closing the one hole the mechanical dedup fix
  does not reach (valor-catchup deliberately ignores dedup and reads the thread).

### Flow

Bridge restart → **one-time dedup-seed (if not yet seeded)** → catchup/reconciler
scan → candidate message within window → `is_duplicate_message` (now authoritative
over the full window) → **skip if dispatched, recover if genuinely missed**.
Separately: valor-catchup thread read → Valor reaction present on message → treat as
answered → no re-enqueue.

### Technical Approach

**Workstream 1 — make the durable dedup set authoritative over the scan window (mechanical scanners):**

- Wire `DedupRecord.Meta.ttl` to a settings-backed value that is coupled to the
  cursor TTL. Add a `dedup_record_ttl_s` field to `config/settings.py` whose
  default equals `last_processed_ttl_s` (30d) — the two must move together because
  the cursor determines the maximum lookback and the dedup set must remember every
  dispatched message for that whole window. Add a GRAIN OF SALT comment marking it
  provisional/tunable (per the magic-numbers rule) and an env override
  (`TIMEOUTS__DEDUP_RECORD_TTL_S`).
- **Do NOT bump `_MAX_IDS`** (was critique NIT). The retention cap only needs to
  cover the largest scanner fetch limit, and it already does: `DedupRecord._MAX_IDS
  = 50` == `max(catchup MAX_MESSAGES_PER_CHAT=50, reconciler RECONCILE_MESSAGE_LIMIT
  =30)`, and the trim logic keeps the most-recent `_MAX_IDS` only after the set
  exceeds `_MAX_IDS × 2` (=100), so the most-recent 50 inbound IDs are always
  retained — a scanner can never reach past those. The prior "2×" bump was
  speculative; drop it. Instead, **add an asserting unit test** that pins the
  invariant so a future fetch-limit increase can't silently break it:
  `assert DedupRecord._MAX_IDS >= max(catchup.MAX_MESSAGES_PER_CHAT, reconciler.RECONCILE_MESSAGE_LIMIT)`.
- **Delete `_check_if_handled`** and its call site in `catchup.py`. This is
  scar-tissue removal per the no-legacy-code policy — guard 1 now covers every
  answer type (reply, non-reply, reaction, deliberate no-reply) because the dedup
  set is written at *dispatch* time regardless of how the message was answered.
- Do NOT add a cursor-based `message.id <= cursor` skip. The cursor is monotonic
  and cannot distinguish "dispatched below the high-water mark" from "missed gap
  below the high-water mark" — a range skip would re-break #1408.

- **REQUIRED one-time dedup-seeding at rollout (closes the rollout regression —
  was the critique BLOCKER).** The old 2h `DedupRecord` TTL means every message
  handled more than 2h before the fix ships has *already aged out of Redis and been
  deleted*. With `_check_if_handled` removed, the very first post-fix startup catchup
  (30d cursor lookback) would find those handled-but-forgotten messages absent from
  the dedup set and re-enqueue them → the exact duplicate-reply storm this bug is
  about, fired once at rollout. An `EXPIRE`-refresh migration cannot fix this (the
  keys are already gone; nothing to refresh). The fix must **re-seed** the dedup set
  before the first post-fix scan:
  - Add a startup seeding pass that runs **once, before `scan_for_missed_messages`**,
    inside the bridge startup path (it needs a live Telethon client to read message
    IDs, so it cannot live in `scripts/update/migrations.py`, which runs clientless).
  - For each monitored/owned chat, fetch the most-recent `MAX_MESSAGES_PER_CHAT`
    messages and write a `DedupRecord` entry (via `record_message_processed`) for
    each *inbound* message whose `id <=` the chat's `LastProcessedRecord` cursor id
    — i.e. messages the cursor already advanced past (hence already dispatched). This
    scopes the seed to messages that were demonstrably handled, rather than blanket-
    seeding the whole window, so it does not permanently suppress a genuine gap
    message sitting *above* the cursor. (Gap messages *below* the high-water mark are
    the same rare #1408 exception the scanners already can't perfectly recover; the
    one-time seed does not make that worse.)
  - Make it **idempotent and one-shot *per chat*, never globally** (was the
    re-critique BLOCKER). Guard each chat's seed with its **own** durable marker —
    `data/dedup-seeded.{chat_id}` — written only *after that chat's seed fully
    succeeds*. Do **NOT** use a single global `data/dedup-seeded` flag: a partial
    Telethon failure (rate-limit / transient) for one chat while others succeed would
    still let the pass finish and stamp the global marker, permanently skipping the
    failed chat's seed on every future restart — that chat then re-enqueues its
    aged-out handled backlog on the first post-fix scan, silently reproducing the
    duplicate-reply storm with no recovery path. Per-chat markers make the seed
    self-healing: a chat that failed (or was newly added) simply has no marker, so it
    re-seeds on the next restart while already-seeded chats are skipped. Concretely:
    for each chat, `if not marker_exists(chat_id): seed(chat_id); on success →
    write_marker(chat_id)`; on a caught per-chat exception, log and continue **without**
    writing that chat's marker. Seeding is additive (`record_message_processed` only
    adds IDs), so a re-seed of an already-seeded chat (e.g. after a crash mid-pass)
    is harmless; the per-chat marker exists only to avoid redundant Telethon reads for
    chats that already completed.
  - Ordering at rollout: fix lands → bridge restarts → **per-chat seed runs
    (before the live NewMessage handler begins dispatching) → then** startup
    catchup / reconciler / valor-catchup run → `data/catchup-disabled` removed.
    Because the seed populates the dedup set with the already-handled window, the
    first real scan finds those messages present in guard 1 and skips them. Running
    the seed before live dispatch begins also closes the seed-vs-live-dispatch
    lost-update window (see Race 3).

**Workstream 2 — close the valor-catchup reaction hole (LLM sweep):**

- **FIRST, before building: verify the self-reaction is actually readable via
  Telethon** (was critique Concern 3). Telethon exposes recent reactions on a
  message via a *bounded* `recent_reactions` list (part of `MessageReactions`); in a
  busy group that reactor list is capped and *Valor's own* reaction may not appear
  in it. `message.reactions` may also be `None`, and the "my reaction" signal
  (`ReactionCount.chosen_order` / a self `PeerUser` in `recent_reactions`) is not
  contractually guaranteed to be present for the self account. **Do a live Telethon
  read against a real chat** where Valor has reacted, confirm which field reliably
  reports the self-reaction, and only then build against it. If the self-reaction
  cannot be read reliably, fall back to the existing landed-reply guard for those
  cases (conservative: a missed reaction-only ack is recoverable next sweep; a
  spurious skip is not) and record the finding in the build notes / Spike Results.
- Capture per-message reactions in `read_thread`/`ThreadMessage`
  (`bridge/agent_catchup.py`) from the Telethon message object, specifically
  whether *Valor's own* account reacted. A Valor reaction is a thread-native
  "handled" signal, consistent with #948's "thread is source of truth, no new
  watermark store."
- Before enqueue (or in the judge's ANSWERED contract), treat "Valor reacted to
  this inbound message" as ANSWERED. This does not require reading the dedup set,
  preserving the module's landed-reply idempotency design.
- **Scrub the stale `_check_if_handled` docstring references** in
  `bridge/agent_catchup.py` (the symbol is named at ~line 38 and ~line 385) as part
  of this workstream — once WS1 deletes the symbol, these dangling references must go
  so the removal-verification grep stays clean (was critique Concern 2).

**Popoto schema note:** `DedupRecord.Meta.ttl` changes value but not shape (no new
fields). Popoto TTL is set on write, so no `scripts/update/migrations.py` migration
is needed for the *TTL contract itself* — new and re-touched keys acquire the new
TTL naturally. An `EXPIRE`-refresh migration is explicitly **not** the answer to the
rollout regression, because the already-handled historical keys have *aged out and
been deleted* under the old 2h TTL — there is nothing left to `EXPIRE`. That gap is
closed by the **required one-time Telethon-backed dedup-seeding pass** described in
Workstream 1 (which repopulates the already-handled window), not by a clientless
key-refresh migration.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `is_duplicate_message`, `record_message_processed`, `record_last_processed`,
      `get_last_processed` all swallow exceptions and log — existing tests in
      `tests/unit/test_dedup.py` assert the fail-open behavior; keep them green.
- [ ] `read_thread` reaction extraction must be defensive (a message with no
      reactions / a Telethon shape change must not raise) — add a test asserting
      a missing-reactions message is treated as not-reacted, not an exception.
- [ ] The one-time dedup-seeding pass must be defensive: a Telethon read failure for
      one chat must not abort seeding for the others, and any failure must not crash
      bridge startup (log + continue; the scanners still run behind the per-chat marker).
- [ ] A per-chat seed failure must NOT write that chat's `data/dedup-seeded.{chat_id}`
      marker, so the chat re-seeds on the next restart (assert marker absent after a
      simulated per-chat Telethon failure; assert a sibling chat that succeeded DID get
      its marker). This is the BLOCKER-fix regression test — a global marker would fail
      it.

### Empty/Invalid Input Handling
- [ ] Confirm behavior when a chat has no `DedupRecord` yet (fresh chat) — the
      genuinely-missed message must still be recovered.
- [ ] Confirm a `None`/empty reaction list in valor-catchup maps to "not reacted"
      (conservative → still ANSWERED only via the existing reply guard, never a
      spurious skip).
- [ ] Confirm the seeding pass on a chat with no `LastProcessedRecord` cursor seeds
      nothing (no cursor → no "already dispatched" evidence → leave the window open
      to normal recovery).

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
- [ ] `tests/unit/test_dedup.py` — ADD `test_max_ids_covers_scanner_fetch_limits`:
      assert `DedupRecord._MAX_IDS >= max(catchup.MAX_MESSAGES_PER_CHAT,
      reconciler.RECONCILE_MESSAGE_LIMIT)` (replaces the dropped `_MAX_IDS` bump).
- [ ] `tests/unit/test_dedup.py` trimming tests — keep green (no `_MAX_IDS` change);
      re-verify the retained-count expectation still matches `_MAX_IDS = 50`.
- [ ] `tests/unit/test_catchup_claim.py`, `tests/integration/test_per_chat_catchup_cutoff.py`,
      `tests/integration/test_catchup_revival.py`, `tests/unit/test_duplicate_delivery.py` —
      these reference `_check_if_handled`; REPLACE the reply-only-handled assertions
      with dedup-authoritative assertions (a dispatched message is skipped via
      `is_duplicate_message` across the full window; delete assertions that exercise
      the removed heuristic).
- [ ] `tests/unit/test_reconciler.py`, `tests/integration/test_reconciler.py` —
      UPDATE only as regression guards (a longer dedup TTL must not change reconciler
      behavior). **Do NOT add a ">2h-old-handled reconciler" case** — it is
      unrealizable: the reconciler's fixed 30-min lookback never reaches a message
      older than the 2h dedup TTL. The >2h skip case belongs to startup catchup.
- [ ] `tests/unit/test_catchup_claim.py` / `tests/integration/test_per_chat_catchup_cutoff.py`
      (or the appropriate catchup test) — ADD the >2h-old-handled-after-restart
      **startup-catchup** skip case (dispatched >2h ago → seeded/durable dedup →
      skipped on the next startup scan).
- [ ] NEW `tests/unit/test_catchup_seed.py` (or extend an existing catchup test) —
      the one-time seeding pass writes dedup entries only for inbound ids ≤ cursor,
      is idempotent under the **per-chat** `data/dedup-seeded.{chat_id}` marker, does
      not seed when no cursor exists, and — on a simulated per-chat Telethon failure —
      leaves the failed chat's marker absent while a sibling chat's marker is written
      (per-chat-marker BLOCKER-fix regression).
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
- **Making the one-time seed clever (backfilling all history, or persisting it as a
  recurring pass).** It is a single rollout bridge over the aged-out-keys gap. Seed
  the most-recent `MAX_MESSAGES_PER_CHAT` per chat once, mark done, move on. Do not
  turn it into an ongoing reconciler or a full-history backfill.

## Risks

### Risk 1: Longer dedup TTL bloats Redis or accumulates ghost index members
**Impact:** `DedupRecord` keys live ~30d instead of 2h.
**Mitigation:** Per-chat sets are capped at `_MAX_IDS` (~50 short strings) and
keyed per monitored chat (a handful). Memory is trivial. The #1817 C3 note
identifies the *short* 2h TTL as the ghost-prone case (hash expires, class-set
membership survives); a longer TTL *reduces* ghost churn, and `reconcile_ghost_members`
still runs on `get_or_create`. Net hygiene improvement.

### Risk 2: Deleting `_check_if_handled` removes a recovery-safety net
**Impact:** If guard 1 had a gap, guard 2 previously (weakly) backstopped it.
**Mitigation:** Guard 1 is now authoritative over the exact set of messages a scan
can fetch (dedup `_MAX_IDS` ≥ scanner fetch limit, pinned by the new invariant test),
and the one-time rollout seed closes the aged-out-keys hole. Add explicit tests for
the >2h-old-handled (startup catchup) and reaction-only cases to prove no regression
before removal.

### Risk 3: Reaction detection reads the wrong "who reacted"
**Impact:** Treating *any* reaction (including a human's) as Valor-handled would
suppress genuinely-unanswered messages.
**Mitigation:** Scope the check to Valor's own account's reaction only; default to
"not reacted" on any ambiguity (conservative — a missed reply is recoverable next
sweep, a suppressed genuine question is not). Gated on the WS2 live-Telethon
verification: if the self-reaction can't be read reliably, do not treat reactions as
answered at all (fall back to the landed-reply guard).

### Risk 4: The one-time seed suppresses a genuine gap message at rollout
**Impact:** Seeding writes dedup entries for handled ids; if a genuinely-missed gap
message sits *below* the cursor high-water mark, seeding it would suppress its
recovery.
**Mitigation:** This is the pre-existing #1408 gap-below-high-water case, not a new
hole — the seed only writes ids `≤ cursor`, which the cursor already treats as
"passed." The alternative (no seed) re-enqueues the *entire* handled backlog as
duplicate replies, which is strictly worse. One-time, bounded to `MAX_MESSAGES_PER_CHAT`.

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

### Race 2: Seed pass vs. the first scan reading the dedup set
**Location:** bridge startup path (seed pass) → `scan_for_missed_messages`.
**Trigger:** If the scan started before the seed finished, the scan would read a
partially-seeded set and re-enqueue.
**Data prerequisite:** The seed must fully complete (per chat) before the first
post-fix scan reads that chat's dedup set.
**Mitigation:** Sequence the seed *before* `scan_for_missed_messages` in the startup
path (await to completion), and gate each chat behind its own `data/dedup-seeded.{chat_id}`
marker written only on that chat's full success — so a mid-run crash, a per-chat
Telethon failure, or a newly-added chat re-runs the seed for exactly the chats that
did not complete (additive, safe) rather than skipping them. A single global marker
is explicitly rejected here (see the WS1 BLOCKER note).

### Race 3: Seed pass vs. live dispatch lost-update on the DedupRecord set
**Location:** `models/dedup.py::add_message` (read-modify-write: `get_or_create`
reads the record → mutate the in-memory `message_ids` set → `save()` the whole set),
concurrently exercised by the seed pass and a live inbound dispatch.
**Trigger:** `add_message` is not an atomic Redis `SADD` — it rewrites the entire
set. If the seed reads chat C's record, a live dispatch then adds a *new* (id > cursor)
message and saves, and the seed then saves its historical (id ≤ cursor) additions,
the seed's save clobbers the live dispatch's just-added id (lost update), leaving the
freshly-dispatched message absent from dedup.
**Data prerequisite:** The seed's read→save window for a chat must not overlap a live
dispatch's read→save for the same chat.
**Mitigation:** Run the per-chat seed **before the live NewMessage handler starts
dispatching** (the rollout ordering above), so no live dispatch competes with the
seed's read→save window. This is the primary fix — sequencing, not locking. Secondary
containment even if the window were hit: a clobbered id is a *live* id (> cursor, just
claimed via the #1817 short-TTL claim key, so no concurrent double-enqueue), and it is
re-added to the dedup set on the very next dispatch-path write or recovery scan; the
seed itself only ever adds historical ids ≤ cursor, so it can never clobber another
historical id. Do **not** introduce a broad lock around `add_message` for this — the
ordering already removes the hazard, and per-chat sets are tiny.

## Observability & Rollback

Because the failure mode is *silent* (a duplicate reply reaches the human with no
error raised), the rollout needs an explicit signal and a fast revert path (was the
re-critique Concern 3 — no post-rollout observability/rollback trigger).

**Signal (detect a recurrence).**
- The seed pass logs a structured, one-line-per-chat summary at INFO on completion:
  chat id, count of ids seeded, and marker-written yes/no (so a partial-failure chat
  is visible in `logs/bridge.log` rather than silently skipped).
- Each mechanical scanner logs, at INFO, a per-scan counter of `re_enqueued` vs
  `skipped_duplicate` decisions (structured fields), so a post-rollout spike in
  `re_enqueued` for historical (pre-restart) message ids is greppable. A single
  `catchup.re_enqueue reason=... msg_id=... chat=... age_s=...` line per re-enqueue is
  enough; no new metrics backend is required (grep `logs/bridge.log`).
- Reuse the existing analytics/log surface — do NOT stand up a new dashboard for this
  one-time rollout. If a lightweight count is wanted, expose it via the existing
  bridge logging, not a new endpoint.

**Rollback trigger and path.** If duplicate replies reappear after re-enabling
recovery (observed via the human reporting a doubled reply, or the `re_enqueue` log
lines above firing for aged historical ids):
1. Immediately re-touch the kill switch: `touch data/catchup-disabled` on the affected
   machine — this disables the *entire* recovery layer again (the same containment that
   holds today), stopping further duplicate replies within one scan cycle.
2. Restart the bridge (`./scripts/valor-service.sh restart`) so the disabled flag takes
   effect on the running process.
3. The per-chat seed markers (`data/dedup-seeded.{chat_id}`) are left in place; the
   longer dedup TTL and deleted `_check_if_handled` are safe to keep. Investigate the
   offending chat's seed log line before re-removing `data/catchup-disabled`.

This makes `data/catchup-disabled` a documented, tested rollback lever, not just the
pre-fix stopgap.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2179] Null/absent message-id handling in the dedup path — tracked
  separately in `docs/plans/issue-2179-null-msgid-dedup.md`; not touched here.
- [EXTERNAL] Removing `data/catchup-disabled` on the primary machine — this is an
  operator action on a live machine the build agent cannot safely reach mid-build;
  it happens after the fix lands and is verified (acceptance criterion tracked, but
  the `rm` is a human/deploy step).
- Ongoing/recurring dedup reconciliation or full-history backfill — the seed is a
  one-time rollout bridge only.

## Update System

The `/update` flow runs `valor-catchup` as Step 9 and restarts the bridge. The
one-time dedup-seeding pass runs inside bridge startup (behind per-chat
`data/dedup-seeded.{chat_id}` markers), so `/update` picks it up automatically on the
next bridge restart — no new `/update` step is required. No migration is added for the
`DedupRecord` TTL (Popoto sets TTL on write; the seed handles the aged-out-keys
backfill). No new dependencies or config files to propagate beyond the new
`TIMEOUTS__DEDUP_RECORD_TTL_S` env knob (optional, has a default). Rollback if
duplicate replies reappear post-rollout: re-touch `data/catchup-disabled` and restart
the bridge (see Observability & Rollback). Otherwise no update-script changes required.

## Agent Integration

No agent integration required — this is a bridge-internal fix. The three recovery
scanners and the one-time seed run inside the bridge/`/update` flow; `valor-catchup`
is an existing CLI entry point. No new MCP surface or tool wiring. The bridge must be
restarted after the fix lands (per the always-restart-running-services rule), and
`data/catchup-disabled` removed to re-enable recovery.

## Documentation

### Feature Documentation
- [ ] Update the recovery/catchup feature doc(s) under `docs/features/` (e.g. the
      bridge catchup/reconciler doc and `bridge/agent_catchup.py`'s referenced doc)
      to describe: the unified dedup TTL contract (dedup set is authoritative over
      the full cursor-bounded scan window), the one-time rollout dedup-seed, the
      removal of `_check_if_handled`, the valor-catchup reaction-awareness, and the
      `data/catchup-disabled` kill switch. Make explicit that the re-handling bug and
      its fix are startup-catchup-scoped (the reconciler was never a bug site).
      Document the **per-chat** `data/dedup-seeded.{chat_id}` seed markers (why
      per-chat, not global) and the post-rollout Observability & Rollback procedure
      (seed/scan log signals + `data/catchup-disabled` re-touch as the rollback lever).
- [ ] Add/verify an entry in `docs/features/README.md` index if a new doc is added.

### Inline Documentation
- [ ] Update `models/dedup.py` docstring (no longer "2h TTL"; now cursor-coupled).
- [ ] Update the `config/settings.py` comment block that currently describes the
      "durable 2h DedupRecord membership set."
- [ ] Docstring on the new `dedup_record_ttl_s` field with the GRAIN OF SALT note.
- [ ] Scrub the two `_check_if_handled` mentions in `bridge/agent_catchup.py`
      docstrings (~line 38, ~line 385) when the symbol is deleted.

## Success Criteria

- [ ] A message dispatched at any point within the (cursor-bounded) startup-catchup
      scan window is skipped by startup catchup after a restart — including messages
      answered by emoji reaction only, by non-reply message, or deliberately not
      answered. (Covered by the >2h-old-handled startup-catchup test.)
- [ ] The reconciler continues to skip already-dispatched messages within its
      fixed 30-min window (regression guard; no new >2h reconciler behavior, which is
      unrealizable by design).
- [ ] The one-time rollout dedup-seed repopulates the already-handled window before
      the first post-fix scan, so re-enabling recovery does NOT produce a duplicate-
      reply storm. (Covered by the seeding test.)
- [ ] The seed is guarded by **per-chat** markers (`data/dedup-seeded.{chat_id}`)
      written only on that chat's full success; a partial per-chat failure leaves that
      chat unmarked and it re-seeds on the next restart. No single global marker exists.
      (Covered by the per-chat-failure regression test.)
- [ ] A structured log signal exists to detect a post-rollout recurrence (per-chat
      seed summary + per-scan `re_enqueued`/`skipped_duplicate` counts), and
      `data/catchup-disabled` re-touch is the documented rollback path. (Verified by
      the Observability & Rollback section and the seed/scan log assertions.)
- [ ] valor-catchup treats a Valor reaction on an inbound message as ANSWERED and
      does not re-enqueue it — contingent on the WS2 live-Telethon verification
      confirming the self-reaction is readable; otherwise it falls back to the
      landed-reply guard. (Covered by the reaction-only-ack test.)
- [ ] A genuinely missed message (no dispatch ever happened, id above the cursor) is
      still recovered by all three scanners. (Regression test.)
- [ ] `_check_if_handled`, its call site, and its docstring references are deleted;
      no remaining reference in `bridge/catchup.py`, `bridge/agent_catchup.py`, or
      `tests/`.
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
  - Role: WS1 — settings-backed TTL, `_MAX_IDS` invariant test, one-time dedup-seed pass, delete `_check_if_handled`, update mechanical-scanner tests
  - Agent Type: builder
  - Domain: async/concurrency + Redis/Popoto data
  - Resume: true

- **Builder (reaction-aware valor-catchup)**
  - Name: reaction-builder
  - Role: WS2 — verify self-reaction read, capture Valor reactions in `read_thread`, treat as ANSWERED, scrub `_check_if_handled` docstrings, tests
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

### 1. WS1: make the durable dedup set authoritative + one-time rollout seed
- **Task ID**: build-dedup-authoritative
- **Depends On**: none
- **Validates**: tests/unit/test_dedup.py, tests/unit/test_reconciler.py, tests/integration/test_reconciler.py, tests/integration/test_per_chat_catchup_cutoff.py, tests/unit/test_catchup_claim.py, tests/unit/test_duplicate_delivery.py, tests/unit/test_catchup_seed.py
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `dedup_record_ttl_s` to `config/settings.py` (default = `last_processed_ttl_s`, env `TIMEOUTS__DEDUP_RECORD_TTL_S`, GRAIN OF SALT comment).
- Wire `DedupRecord.Meta.ttl` to the setting. Do NOT bump `_MAX_IDS`; add the `_MAX_IDS >= max(scanner fetch limits)` asserting unit test instead.
- Add the REQUIRED one-time dedup-seeding pass (runs once before `scan_for_missed_messages` and before the live NewMessage handler dispatches, Telethon-backed, seeds inbound ids ≤ cursor per chat, idempotent under **per-chat** `data/dedup-seeded.{chat_id}` markers written only on that chat's full success — never a single global marker, defensive per-chat).
- Add structured seed/scan logging (per-chat seed summary; per-scan `re_enqueued`/`skipped_duplicate` counters) for post-rollout recurrence detection.
- Delete `_check_if_handled` and its call site in `catchup.py`.
- Update the affected mechanical-scanner tests (see Test Impact); add the >2h-old-handled-after-restart **startup-catchup** skip test, the seeding test, and the per-chat-failure marker regression test. Do NOT add a >2h reconciler test (unrealizable).

### 2. WS2: reaction-aware valor-catchup
- **Task ID**: build-reaction-aware
- **Depends On**: none
- **Validates**: tests/unit/test_agent_catchup.py, tests/integration/test_agent_catchup_recovery.py
- **Assigned To**: reaction-builder
- **Agent Type**: builder
- **Parallel**: true
- FIRST: do a live Telethon read to verify the self-reaction is reliably readable; record the finding. If not reliable, fall back to the landed-reply guard (no reaction-as-answered) and note it.
- Capture Valor's own reaction per message in `read_thread`/`ThreadMessage` (defensive against missing reactions).
- Treat "Valor reacted" as ANSWERED before enqueue; default to not-reacted on ambiguity.
- Scrub the two stale `_check_if_handled` docstring references in `bridge/agent_catchup.py`.
- Add the reaction-only-ack test.

### 3. Validation
- **Task ID**: validate-catchup
- **Depends On**: build-dedup-authoritative, build-reaction-aware
- **Assigned To**: catchup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria; confirm no `_check_if_handled` references remain in `bridge/` or `tests/`; run the targeted test set.

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
| Catchup + seed tests pass | `pytest tests/unit/test_catchup_claim.py tests/unit/test_catchup_seed.py tests/integration/test_per_chat_catchup_cutoff.py -q` | exit code 0 |
| Agent-catchup tests pass | `pytest tests/unit/test_agent_catchup.py -q` | exit code 0 |
| `_check_if_handled` fully removed (symbol + docstrings) | `grep -rn "_check_if_handled" bridge/catchup.py bridge/agent_catchup.py tests/` | match count == 0 |
| Dedup TTL no longer hardcoded 7200 | `grep -n "7200" models/dedup.py` | match count == 0 |
| Seed uses per-chat markers, not a global flag | `grep -rn "dedup-seeded" bridge/ | grep -v "dedup-seeded\."` | match count == 0 (every reference is per-chat `dedup-seeded.{chat_id}`) |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | do-plan-critique | Re-enable sequence re-triggers the duplicate-reply bug once at rollout: handled-historical messages aged out of the 2h dedup TTL are gone from Redis, so the first post-fix catchup (30d lookback) with `_check_if_handled` deleted finds them absent and re-enqueues. An EXPIRE-refresh migration cannot help (keys deleted). | Solution WS1 "REQUIRED one-time dedup-seeding at rollout"; Popoto schema note; Success Criteria; Step 1; Race 2 | Required build step: Telethon-backed one-time seed of inbound ids ≤ cursor per chat, runs before the first scan, idempotent under a `data/dedup-seeded` marker. |
| CONCERN 1 | do-plan-critique | Reconciler uses fixed 30-min lookback, no cursor extension; dedup never ages out for messages it reaches. The ">2h-old handled reconciler" test is unrealizable. | Root Cause scope note; Data Flow #3; Test Impact (reconciler = regression guard only, >2h case moved to startup catchup); Success Criteria | Root cause reframed as startup-catchup-only; reconciler dropped from the >2h criterion. |
| CONCERN 2 | do-plan-critique | Verification grep `_check_if_handled == 0` fails even after correct deletion — symbol survives in two `bridge/agent_catchup.py` docstrings (~38, ~385). | WS2 docstring-scrub bullet; Verification grep narrowed to `bridge/catchup.py bridge/agent_catchup.py tests/`; Documentation; Success Criteria; Step 2 | Both: scrub the docstrings AND scope the grep to the files that legitimately reference the symbol. |
| CONCERN 3 | do-plan-critique | WS2 self-reaction read isn't contractually guaranteed by Telethon (bounded `recent_reactions`; own reaction may not appear in busy groups). | WS2 "FIRST, verify" bullet; Risk 3; Success Criteria (contingent); Step 2 | Required live-Telethon verification before building; documented fallback to landed-reply guard if unreliable. |
| CONCERN 4 | do-plan-critique | Desired-outcome overpromises the "deliberate no-reply" guarantee for valor-catchup (WS2 only adds reaction awareness). | Desired Outcome rewrite scoping the full guarantee to WS1 mechanical scanners; valor-catchup scoped to reaction-awareness | Guarantee split: answer-type-agnostic skip is a WS1 dedup property; valor-catchup adds reaction awareness only. |
| NIT | do-plan-critique | `_MAX_IDS` bump is speculative — drop it or derive from max(scanner fetch limits) with an asserting test. | WS1 "Do NOT bump `_MAX_IDS`" bullet; Test Impact assert test; Risk 2 | Dropped the bump (`_MAX_IDS = 50` already == max fetch limit); added `_MAX_IDS >= max(...)` asserting unit test. |
| BLOCKER (rev 2) | do-plan-critique | Seed guarded by a single global `data/dedup-seeded` marker: a partial per-chat Telethon failure still finishes the pass and stamps the global marker, permanently skipping that chat's seed on every future restart → that chat re-enqueues its aged-out backlog with no recovery path. | Solution WS1 seeding bullet (per-chat markers); Race 2; Key Elements; Step 1; Failure Path; Test Impact; Success Criteria; Update System | Switched to **per-chat** `data/dedup-seeded.{chat_id}` markers written only on that chat's full success; global marker explicitly rejected; added the per-chat-failure marker regression test. |
| CONCERN 1 (rev 2) | do-plan-critique | Seed-vs-live-dispatch lost-update on `DedupRecord` (`add_message` is read-modify-write, not atomic SADD) — a live dispatch's new id could be clobbered by the seed's save. | New Race 3; rollout ordering (seed before live handler) | Primary fix: run the per-chat seed before the live NewMessage handler dispatches, removing the overlap window; documented secondary containment (clobbered live id is claim-guarded and re-added next write). |
| CONCERN 2 (rev 2) | do-plan-critique | "Key structural insight" text ("cursor cannot be `id <= cursor` skip signal") contradicts the seed's own `id <= cursor` logic. | Root Cause: scoped the prohibition to the *recurring live-scan* skip; added a paragraph reconciling the one-shot seed's `id <= cursor` proxy use | Clarified: the ban is on a *permanent* live skip filter; the one-time seed legitimately uses `id <= cursor` as a seed-time proxy, downside = the same bounded #1408 gap-below-cursor exception (Risk 4). |
| CONCERN 3 (rev 2) | do-plan-critique | No post-rollout observability or rollback trigger for a silent recurrence. | New Observability & Rollback section; Key Elements; Success Criteria; Update System | Added structured seed/scan log signals (per-chat seed summary + `re_enqueued`/`skipped_duplicate` counts) and a documented rollback lever (re-touch `data/catchup-disabled` + restart). |
| NIT (rev 2) | do-plan-critique | "regardless of how long ago" overstates the TTL-bounded ~30-day guarantee. | Desired Outcome rewrite | Scoped the guarantee to "any message within the cursor-TTL (~30-day) startup-catchup lookback window." |

## Decisions (resolved from prior Open Questions)

1. **TTL coupling — DECIDED: couple.** `dedup_record_ttl_s` defaults to
   `last_processed_ttl_s` (30d) so the dedup set covers the full cursor-bounded
   startup-catchup lookback. Capping the window (e.g. 24h) was rejected — it reopens
   the #1408 quiet-chat gap.
2. **Migration for existing dedup keys — DECIDED: no EXPIRE migration; one-time
   Telethon-backed seed instead.** The old-TTL keys are already deleted, so there is
   nothing to `EXPIRE`. The required rollout seed (WS1) repopulates the already-
   handled window before the first post-fix scan.
3. **valor-catchup scope — DECIDED: reaction-awareness only** (keep #948 thread-truth
   design intact; do not consult the dedup set), contingent on the WS2 live-Telethon
   self-reaction verification.
