---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-06-20
tracking: https://github.com/tomcounsell/ai/issues/1749
last_comment_id: IC_kwDOEYGa088AAAABHAzF7A
revision_applied: true
---

# Telegram Relay: File-Send Idempotency + Oversized-Text Guard for File Messages + FloodWaitError Handling

## Problem

The Telegram relay (`bridge/telegram_relay.py`) — the outbox-draining worker loop that ships PM/agent messages to Telegram via Telethon — has defects that together produced a burst of repeated file sends followed by a hard dead-letter fail, observed in production for supergroup chat `-1003900483201`.

**The real incident trigger** (per the logs at `/Users/valorengels/Downloads/issue1749_logs`) was a **`MessageTooLongError`** raised by the **follow-up text step** of a file+text message — a *permanent* `BadRequestError`, NOT a `FloodWaitError`. The file shipped, the oversized text step then failed identically on every retry, the whole payload (file included) was re-queued each time, and after `MAX_RELAY_RETRIES` the dead-letter itself silently dropped the payload because the chat_id was negative (a supergroup). So the incident is the composition of three distinct defects, only the first two of which the original plan addressed.

**Current behavior:**

1. **Duplicate file sends.** A file message is delivered in two non-atomic steps: `send_file(...)` (no caption, `telegram_relay.py:345`) then `send_message(...)` (the text, `telegram_relay.py:364`). If the **text step fails**, `_send_queued_message` returns `None`, and `process_outbox` re-queues the *entire* payload — file paths included (`telegram_relay.py:778-788`). On the next attempt the file is sent **again**. Persistent files (outputs that survive on disk between retries) get re-sent once per retry; `cleanup_file` files are unlinked after send so they can't recur. The text send is already de-duped via `recent_sent_drafts` / `redundancy_filter` (`telegram_relay.py:748-759`, the #1205 fix); the **file send has no equivalent idempotency guard**.

2. **Oversized text on a file+text message is never converted to a `.txt` attachment, so it dead-letters permanently.** The relay has a belt-and-suspenders oversized-text guard that converts any text >4096 chars into a `.txt` attachment instead of letting Telegram raise `MessageTooLongError` (`telegram_relay.py:383-438`). But that guard sits in the **text-only path**, *after* the file branch has already executed its follow-up `send_message` at `telegram_relay.py:363-370` and `return`ed. For a **file+text** message, control never reaches the guard — the oversized follow-up text hits `send_message` raw at line 364, raises `MessageTooLongError`, and (because that error is permanent, not transient) fails identically on every retry until dead-letter. Idempotency (defect 1) correctly stops the duplicate *file* send on retry, but the *text* still fails every time. **This was the actual incident trigger.**

3. **The dead-letter path silently drops group/supergroup payloads — in TWO places.** `_dead_letter_message` returns early for any `chat_id <= 0` (`telegram_relay.py:650`), discarding the payload without persisting it for replay. Group and supergroup chat IDs are *legitimately negative* (the incident chat `-1003900483201` is a supergroup), so the very messages that exhaust retries in a group context are silently lost. The entry-side validation already gets this right — it drops only `chat_id_int == 0` (`telegram_relay.py:266`) — but the dead-letter side over-broadly uses `<= 0`. **Critically, even if only the relay-side guard is narrowed, the fix is a no-op**: `bridge/dead_letters.py::replay_dead_letters` has a SECOND, identical `chat_id_int <= 0` guard (`dead_letters.py:57`) that `async_delete()`s any negative-chat_id record on the next bridge startup — silently undoing the persist. Both guards must be narrowed to `== 0` **in lockstep**, or the persisted payload is deleted on the very next replay pass. This double-`<= 0` asymmetry (against the line-266 `== 0` precedent) is the bug.

4. **No FloodWaitError handling.** The relay has no `FloodWaitError` handling at all (`grep FloodWait bridge/telegram_relay.py` → nothing). Telethon raises `telethon.errors.FloodWaitError` when Telegram demands an N-second backoff. The relay's generic catch treats it as a plain failure, immediately increments `_relay_attempts`, and re-queues — **hammering Telegram while it asks the client to back off**, escalating toward a flood ban, then dead-lettering after `MAX_RELAY_RETRIES` (3). The closest existing precedent is the bridge's **connection/auth loop** at `bridge/telegram_bridge.py:2659`, which catches `FloodWaitError` around `client.connect()` and sleeps `e.seconds + 5` (with a `_write_flood_backoff` side-effect). **That is a connect-path handler, NOT a send-path one** — the relay has no send-path flood handling at all, which is precisely the gap. We borrow only the *blocking-sleep shape* (`sleep(e.seconds + buffer)`) from that precedent, not its connect-loop control flow or its `_write_flood_backoff` side-effect. **Note:** this defect was *not* exercised by the incident; it is a defensive hardening kept in scope by an explicit decision below (see "Scope decision: FloodWait handling").

**Desired outcome:**

- A file is sent **at most once** per logical message, regardless of how many times the text step is retried.
- An oversized follow-up text on a **file+text** message is converted to a `.txt` attachment (same as the text-only path) **before** the raw `send_message`, so it never dead-letters permanently on `MessageTooLongError`.
- A dead-lettered message for a **group/supergroup** (legitimately negative chat_id) is persisted for replay rather than silently dropped; only `chat_id == 0` is discarded.
- A flood wait is **honored** (sleep the required interval, as the bridge does) instead of counted as a failed attempt and immediately retried — no retry-budget burn, no ban escalation.

## Freshness Check

**Baseline commit:** `457e1f78`
**Issue filed at:** 2026-06-20T08:13:34Z
**Disposition:** Unchanged

**File:line references re-verified (all still hold; line numbers re-confirmed against current `bridge/telegram_relay.py` during this revision pass):**
- `bridge/telegram_relay.py:46` — `MAX_RELAY_RETRIES = 3` — confirmed.
- `bridge/telegram_relay.py:344-349` — two-step file send: `send_file` without caption — confirmed.
- `bridge/telegram_relay.py:363-370` — separate `send_message` text step for the **file branch**, followed by `return msg_id` at line 370 — confirmed. This `return` is why control never falls through to the oversized-text guard for file+text messages (defect 2).
- `bridge/telegram_relay.py:360-362` — `cleanup_file` unlink after send — confirmed.
- `bridge/telegram_relay.py:388-438` — oversized-text DETECTION + `.txt`-conversion block (`if text and len(text) > 4096:` … `tempfile` → `send_file` with the `[auto-attached: response exceeded 4096 chars]` caption → `return msg_id`), sitting in the **text-only path** after the file branch returns — confirmed (defect 2 root cause: unreachable for file+text).
- `bridge/telegram_relay.py:364` — the **file branch's** terminal follow-up send is a RAW `await telegram_client.send_message(...)` — confirmed.
- `bridge/telegram_relay.py:443` — the **text-only branch's** terminal send is `await send_markdown(...)` (from `bridge.markdown`), NOT `send_message` — confirmed. The two call sites use DIFFERENT terminal send functions; this is why a shared helper must NOT perform the terminal send (see Blocker-2 resolution in Technical Approach defect 2).
- `bridge/telegram_relay.py:266` — entry-side validation drops only `chat_id_int == 0` — confirmed (the correct precedent for the defect-3 narrowing).
- `bridge/telegram_relay.py:650` — `_dead_letter_message` returns early for `chat_id_int <= 0`, silently dropping negative (group/supergroup) chat IDs — confirmed (defect 3 root cause, **guard #1 of 2**).
- `bridge/dead_letters.py:57` — `replay_dead_letters` has a SECOND, identical `if chat_id_int <= 0:` guard that `async_delete()`s the record (line 61) on the next bridge startup — confirmed (defect 3 root cause, **guard #2 of 2**; narrowing only the relay-side guard is a no-op without narrowing this one in lockstep).
- `bridge/dead_letters.py:65-66` — `replay_dead_letters` truncates `len(text) > 4096` to `text[:4093] + "..."` before re-sending — confirmed (replay-path truncation; see Risk 5).
- `bridge/telegram_relay.py:778-794` — bounded-retry / re-queue / dead-letter block — confirmed.
- `grep FloodWait bridge/telegram_relay.py` → no matches — confirmed (defect 4 still present).
- `bridge/telegram_bridge.py:2659` — `except FloodWaitError as e:` sleeps `e.seconds + 5` — confirmed, BUT this wraps `client.connect()` in the **connection/auth loop** (with a `_write_flood_backoff(e.seconds)` side-effect at line 2664), NOT a send path. We borrow only its blocking-sleep shape; it is not a send-path precedent.

**Cited sibling issues/PRs re-checked:** #1205 (text-dedup precedent) — referenced by the in-code comment at `telegram_relay.py:748-759`, still present. #698 (relay-retry-guard, merged) — added the bounded-retry block this plan extends.

**Commits on main since issue was filed (touching `bridge/telegram_relay.py` or `bridge/dead_letters.py`):** none.

**Active plans in `docs/plans/` overlapping this area:** `relay-retry-guard.md` (issue #698, **Merged**) added bounded retries + dead-lettering; it does **not** address idempotency or FloodWait. This plan builds directly on top of its `_relay_attempts` / re-queue machinery. No active (unmerged) overlap.

## Prior Art

- **PR #698 / `relay-retry-guard.md` (Merged):** Added `MAX_RELAY_RETRIES`, the `_relay_attempts` counter, and dead-letter routing to `process_outbox`. This plan reuses that re-queue path and adds idempotency + flood-wait handling on top. No conflict.
- **Issue #1205 (text-dedup):** Established `recent_sent_drafts` / `redundancy_filter` for duplicate **text** suppression. The file-send idempotency guard here is the file-side analogue of that precedent, but scoped to the in-flight message rather than the cross-session draft cache.
- No prior failed attempts at file-send idempotency or relay FloodWait handling were found (`gh issue list --state closed`, `gh pr list --state merged`). Greenfield within the relay for both defects.

## Data Flow

1. **Entry point:** A PM/agent message with `file_paths` (persistent file) + `text` is enqueued to `telegram:outbox:<session>` in Redis.
2. **`process_outbox` (`telegram_relay.py:675`)** LPOPs the raw JSON, parses to `message` (a dict), validates `type`, and dispatches to `_send_queued_message(telegram_client, message)` (line 727). **`message` is passed by reference** and is the same object re-serialized on re-queue (`json.dumps(message)`, line 787).
3. **`_send_queued_message` (`telegram_relay.py:223`)** file branch: `send_file` (step 1) → `send_message` (step 2) → return `msg_id`. The whole body is wrapped in `try/except Exception` (line 277/455) which **swallows any failure and returns `None`** — including a text-step failure that occurs *after* the file already shipped.
4. **Back in `process_outbox`:** `success = msg_id is not None`. On `None`, the else branch (line 778) increments `_relay_attempts` and re-queues the **same `message` dict** (file paths intact) → next loop re-sends the file. **This is the duplicate-send loop.**
5. **Output:** repeated file deliveries to the chat, ending in a dead-letter after 3 attempts.

**Key leverage point:** because `message` is mutated by reference and re-serialized verbatim on re-queue, a flag set on `message` inside `_send_queued_message` (e.g. `message["_file_sent"] = True`) **persists across the re-queue boundary** — exactly how `_relay_attempts` already works. This is the idempotency anchor.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue)
- Review rounds: 1 (PR review gate)

Two-file change — the primary edits land in `bridge/telegram_relay.py`, plus a one-line lockstep narrowing in `bridge/dead_letters.py` (`replay_dead_letters`'s `<= 0` → `== 0`, defect 3 guard #2) — alongside unit tests in the existing `tests/unit/test_bridge_relay.py` and a replay-survival test (in `tests/unit/test_bridge_relay.py` or the dead-letter test module, whichever already exercises `replay_dead_letters`). The bottleneck is correctness review, not code volume.

## Prerequisites

No prerequisites — `telethon` (which exports `FloodWaitError`) is already a project dependency and already imported in `bridge/telegram_bridge.py`. No external services, keys, or config.

## Solution

### Key Elements

- **File-send idempotency flag** — once a file successfully ships, mark it on the in-flight `message` dict so a re-queue (caused by a later failing step) resends only the **text**, never the file.
- **Oversized-text DETECTION shared, terminal send NOT** — extract the oversized-text detection + `.txt`-conversion (`telegram_relay.py:388-438`) into a shared helper that returns a converted-attachment `msg_id` (or `None` when no conversion happened / conversion failed). Call it from the file branch *before* the follow-up `send_message` at line 364, and from the text-only path. Each call site keeps its OWN terminal send: the file branch falls back to raw `send_message` (line 364), the text-only branch to `send_markdown` (line 443). The helper shares the *decision and conversion*, never the terminal send. **This is the fix for the actual incident trigger.**
- **Dead-letter guard narrowed to `chat_id == 0` in BOTH places** — change the `_dead_letter_message` early-return from `chat_id_int <= 0` to `chat_id_int == 0` (`telegram_relay.py:650`) AND the identical guard in `replay_dead_letters` (`dead_letters.py:57`) in lockstep, matching the entry-side validation precedent at line 266. Narrowing only the relay-side guard is a no-op — the replay-side guard would `async_delete()` the persisted negative-chat_id record on the next bridge startup. Both must move together so group/supergroup payloads (legitimately negative IDs) are persisted AND actually re-sent on replay instead of silently dropped.
- **Real FloodWait handling** — catch `telethon.errors.FloodWaitError` in the relay send path, honor the required wait (sleep, as the bridge does), and re-queue **without** burning the `_relay_attempts` budget.
- **Bounded flood backstop** — a separate, high-cap flood-wait counter and a sleep ceiling so a pathological/endless flood condition can't block the relay loop forever or loop infinitely.

### Flow

Outbox message with persistent file + text → `send_file` succeeds, `message["_file_sent"]=True` set → text step raises (flood/network) → re-queue carries `_file_sent=True` → next attempt **skips file**, sends text only → success → message drains. No duplicate file.

Flood wait on any send → catch `FloodWaitError` → sleep `min(e.seconds + buffer, ceiling)` → re-queue **without** incrementing `_relay_attempts` (increment `_flood_waits` backstop instead) → retry after the wait. No ban escalation, no premature dead-letter.

### Technical Approach

**Defect 1 — file-send idempotency (in `_send_queued_message`, default file branch ~`telegram_relay.py:341-370`):**
- Before the `send_file` call, guard on the in-flight flag: `if message.get("_file_sent"):` → skip the file send, reuse the previously recorded `msg_id` (`message.get("_file_msg_id")`), and fall straight through to the text send. Log at info (`"skipping already-sent file(s) … (idempotency guard)"`).
- Immediately **after** a successful `send_file` (before the text step), set `message["_file_sent"] = True` and `message["_file_msg_id"] = msg_id`. Because `message` is mutated by reference and re-serialized on re-queue, the flag survives the re-queue exactly like `_relay_attempts`.
- This sits inside the `if available:` block, so it only governs the persistent-file case (the actual bug). `cleanup_file` files are unlinked after send → `available` is empty on retry → existing "all files missing → text-only" path already prevents recurrence; the guard is harmless there.

**Defect 2 — oversized follow-up text on a file+text message (in `_send_queued_message`, file branch at `telegram_relay.py:363-370`):**
- The text-only path already has a working oversized-text DETECTION + `.txt`-conversion block at `telegram_relay.py:388-438`. The file branch's follow-up send at line 364 never reaches it because the file branch `return msg_id`s at line 370 first.
- **CRITICAL — the two call sites use different terminal send functions.** The file branch's terminal send is a RAW `await telegram_client.send_message(...)` (line 364). The text-only branch's terminal send is `await send_markdown(...)` (line 443, from `bridge.markdown`). A shared helper that performed the terminal send would route one of the two sites through the wrong function. So the helper must NOT do the terminal send.
- **Scope the shared helper to the `.txt`-conversion DECISION ONLY.** Signature: `async def _maybe_send_oversized_text_as_file(telegram_client, chat_id, text, reply_to, session_id) -> int | None` where the return value is the converted-attachment `msg_id` when the text was oversized AND conversion succeeded, and `None` in every other case (text not oversized, or conversion raised). Internally it owns: the `len(text) > 4096` check, the error log, the tempfile creation/write, the `send_file` of the `.txt` overflow with the `[auto-attached: response exceeded 4096 chars]` caption, and the fall-through-on-failure logging. It does NOT own the normal (non-oversized) terminal send.
- **Each call site keeps its own terminal send.** File branch, before line 364: `attach_id = await _maybe_send_oversized_text_as_file(...)`; if `attach_id is not None`, the oversized text already shipped as an attachment — skip the raw `send_message`; otherwise fall through to the existing raw `send_message` at line 364 unchanged. Text-only path: replace the inline 388-438 block with the same call; if it returns a `msg_id`, `return` it; otherwise fall through to the existing `send_markdown` at line 443 unchanged. This shares the *detection + conversion* (NO LEGACY CODE TOLERANCE — the ~50-line block lives in exactly one place) while leaving each terminal send (`send_message` vs `send_markdown`) per-call-site.
- **Composition with idempotency:** the file has already shipped and `_file_sent=True` is set before the follow-up text is attempted. If the oversized follow-up text is now sent as a `.txt` attachment, the message drains successfully — the dead-letter loop never starts. If conversion fails and the raw `send_message` raises, idempotency (defect 1) still prevents the *file* from re-sending on retry; the text continues to fail, but defect 3 now ensures the eventual dead-letter is at least persisted for replay rather than silently dropped.

**Defect 3 — dead-letter silently drops group/supergroup payloads, in TWO guards that must move in lockstep:**

*Guard #1 — persist side (`_dead_letter_message`, `telegram_relay.py:650`):*
- Change the early-return condition from `if chat_id_int <= 0:` to `if chat_id_int == 0:`, and update the accompanying comment/log to say "chat_id=0 is not a valid Telegram peer" (matching the entry-side precedent at line 266). Group and supergroup IDs are legitimately negative and MUST be dead-lettered for replay.

*Guard #2 — replay side (`replay_dead_letters`, `bridge/dead_letters.py:57`):*
- This is the no-op trap. Even after guard #1 persists a negative-chat_id record, `replay_dead_letters` runs its OWN identical `if chat_id_int <= 0:` (`dead_letters.py:57`) on the next bridge startup and `async_delete()`s the record (`dead_letters.py:61`) before ever attempting the re-send — silently undoing the fix. Narrow this guard to `if chat_id_int == 0:` in lockstep, updating its comment/log accordingly. `persist_failed_delivery` (`dead_letters.py:18-31`) already stores `chat_id` as an opaque string with no `<= 0` validation, so the only two `<= 0` drops are these two guards — both are now narrowed.
- **Replay-path truncation (concern from critique):** `replay_dead_letters` truncates `len(text) > 4096` to `text[:4093] + "..."` before `client.send_message` (`dead_letters.py:65-66`). This is a *pre-existing* lossy fallback on the replay path only and is **scoped OUT of this plan** — justification: (a) it cannot undo defect 2, because defect 2's fix converts oversized text to a `.txt` attachment *at relay time* so an oversized file+text message never reaches dead-letter in the first place; (b) the only text that still reaches replay-truncation is a text-only message that exhausted retries for a *non-length* reason (network/flood), where lossy-but-delivered beats silently-dropped; (c) fixing replay to also `.txt`-convert is a larger change to a different function with its own send path, out of Small appetite. Noted here so the builder does not "fix" it and so a reviewer sees it was a deliberate scope call, not an oversight. (See Risk 5.)

**Defect 4 — FloodWait handling (in `_send_queued_message` + `process_outbox`):**

> **Scope decision: FloodWait handling stays bundled.** This defect was *not* exercised by the incident (the trigger was the permanent `MessageTooLongError`, not a transient flood wait). It is kept in this plan rather than split into a follow-up issue because: (a) it is a small, self-contained addition to the same `_send_queued_message` / `process_outbox` send path being edited for defects 1–3, so the marginal review/test cost is low and the merge conflict surface is zero; (b) the relay genuinely lacks *any* flood-wait handling today, which is a latent ban-escalation risk every time Telegram rate-limits this client; (c) shipping it now avoids a second round-trip through the same file. The alternative — a separate follow-up issue — was considered and rejected as needless ceremony for a ~15-line defensive addition that shares all its test scaffolding with the incident fixes. If a reviewer judges the bundle too large for Small appetite, the FloodWait constants + branch are the cleanest seam to defer (they touch no code the other three defects touch).
- Add `from telethon.errors import FloodWaitError` at module top (mirroring the import in `telegram_bridge.py`). Note the `telegram_bridge.py:2659` handler we borrow the sleep shape from is a **connect-loop** handler, not a send handler — do NOT copy its `_write_flood_backoff` side-effect or its connect-retry control flow; we want only `await asyncio.sleep(e.seconds + buffer)` adapted to the relay's per-message re-queue loop.
- In `_send_queued_message`, add `except FloodWaitError: raise` **before** the generic `except Exception` (line 455) so a flood wait propagates instead of being swallowed to `None`. Apply the same `except FloodWaitError: raise` to `_send_custom_emoji_message` and `_send_queued_reaction` if (and only if) their bodies have a generic `except` that would otherwise swallow it — verified at build time.
- In `process_outbox`, the dispatch is already wrapped in `try/except Exception as handler_err` (line 720-734). Add a dedicated `except FloodWaitError as flood_err:` clause **before** the generic one that:
  1. Honors the wait: `await asyncio.sleep(min(flood_err.seconds + RELAY_FLOOD_WAIT_BUFFER_SECS, RELAY_FLOOD_WAIT_MAX_SLEEP_SECS))`.
  2. Increments a separate `message["_flood_waits"]` counter (NOT `_relay_attempts`).
  3. If `_flood_waits >= RELAY_FLOOD_WAIT_MAX` (backstop) → dead-letter; else re-queue the same `message` (carrying `_file_sent` if set) and `continue` to the next message.
- New tunable constants near `MAX_RELAY_RETRIES` (`telegram_relay.py:46`), each named and env-overridable with a "provisional — tune from production flood-wait telemetry" comment (per the magic-numbers convention):
  - `RELAY_FLOOD_WAIT_BUFFER_SECS` (default 5 — matches the bridge's `e.seconds + 5`)
  - `RELAY_FLOOD_WAIT_MAX_SLEEP_SECS` (default 300 — ceiling so a huge flood value can't wedge the loop)
  - `RELAY_FLOOD_WAIT_MAX` (default 10 — backstop against an endless flood loop)

**Interaction (all defects together):** the incident sequence is now closed end-to-end. A file+text message with oversized text: file ships → `_file_sent=True` set (defect 1) → oversized follow-up text is converted to a `.txt` attachment instead of raising `MessageTooLongError` (defect 2) → message drains, no dead-letter loop. If the text were instead to hit a transient flood wait, the flood re-queue carries `_file_sent`, so the retry skips the file and only re-sends text (defect 4 + defect 1 compose). And in the residual case where a message genuinely exhausts retries in a group/supergroup, the dead-letter is now persisted for replay rather than silently dropped (defect 3). The four fixes compose correctly.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_send_queued_message`'s generic `except Exception` (line 455) already logs `logger.error(... exc_info=True)` and returns `None` — its observable behavior (return `None` → re-queue) is covered by the existing `test_returns_none_on_send_failure`. The new `except FloodWaitError: raise` is covered by a test asserting the exception propagates (not swallowed to `None`).
- [ ] `process_outbox`'s new `except FloodWaitError` branch — test asserts (a) `asyncio.sleep` is called with the honored interval, (b) `_relay_attempts` is **not** incremented, (c) the message is re-queued (not dead-lettered) until the flood backstop is hit.

### Empty/Invalid Input Handling
- [ ] Idempotency flag is only read/written when `file_paths` is non-empty and files are `available`; empty/None file lists fall through unchanged. Covered by re-running existing `test_missing_file_*` cases (they must stay green).
- [ ] Oversized-text guard in the file branch only fires when `text and len(text) > 4096`; normal-length captions take the unchanged raw `send_message` path. Covered by the existing happy-path file+text test staying green.
- [ ] No agent-output processing in scope — no silent-loop risk.

### Error State Rendering
- [ ] Relay output is the Telegram delivery itself; the failure path is observable via logs and the dead-letter queue.
- [ ] **Oversized file+text (defect 2):** test asserts an oversized follow-up text on a file message routes through the `.txt`-conversion helper (a `send_file` for the overflow attachment) and does NOT raise `MessageTooLongError` to the retry loop.
- [ ] **Negative-chat_id dead-letter persist (defect 3, guard #1):** test asserts a message with a negative (group/supergroup) chat_id that exhausts retries IS persisted via `persist_failed_delivery` (not silently dropped), while a `chat_id == 0` message is still discarded.
- [ ] **Negative-chat_id replay survival (defect 3, guard #2):** test asserts a persisted `DeadLetter` with a negative chat_id SURVIVES a `replay_dead_letters` pass — it is NOT `async_delete()`d by the replay-side guard, and `client.send_message` IS called with that negative chat_id (the record actually re-sends). This is the test that catches the no-op trap: without narrowing `dead_letters.py:57`, the record is deleted and `send_message` is never called.
- [ ] **Flood backstop (defect 4):** test asserts a flood wait beyond the backstop routes to `_dead_letter_message` with a flood-specific reason string.

## Test Impact

Most tests live in `tests/unit/test_bridge_relay.py` (mock-client pattern: `send_file` / `send_message` / `send_markdown` as `AsyncMock`s). The replay-survival test (defect 3, guard #2) lives wherever `replay_dead_letters` is already exercised (the dead-letter test module); if no such test exists yet, add that module to scope. Changes are **additive** — existing assertions remain valid because the happy path is unchanged.

- [ ] `tests/unit/test_bridge_relay.py::test_sends_file_via_send_file` — UPDATE (defensive): confirm still green; the success path sets `_file_sent` but still calls `send_file` exactly once and returns the same `msg_id`.
- [ ] `tests/unit/test_bridge_relay.py::test_file_only_send_no_caption` — verify unchanged (no text → no idempotency interaction).
- [ ] `tests/unit/test_bridge_relay.py::test_missing_file_falls_back_to_text` / `test_missing_file_no_text_returns_none` / `test_backward_compat_file_path_string` — verify unchanged (additive guard doesn't alter these paths).
- [ ] `tests/unit/test_bridge_relay.py::test_max_relay_retries` — verify unchanged (`MAX_RELAY_RETRIES == 3` constant untouched).
- [ ] **NEW** `test_file_not_resent_on_text_step_retry` — file send succeeds, text step raises generic error → `_send_queued_message` returns `None`, `message["_file_sent"]` is `True`; a second call with the same dict does **not** call `send_file` again but does call `send_message`.
- [ ] **NEW** `test_oversized_text_on_file_message_converts_to_txt` (defect 2) — file+text message with `len(text) > 4096`: `send_file` is called for the file, then the oversized follow-up text is sent as a `.txt` attachment (second `send_file`, never a raw `send_message` of the oversized text); no `MessageTooLongError` propagates. Assert the shared helper is what converts, and that the file branch's terminal `send_message` is NOT called with the oversized text.
- [ ] **NEW** `test_oversized_text_only_still_converts_via_send_markdown_path` (defect 2 regression guard) — a text-only oversized message still converts to a `.txt` attachment via the shared helper, and the normal (non-oversized) text-only fallback still routes through `send_markdown` (line 443), NOT `send_message`. Guards against the helper accidentally swallowing the text-only terminal send.
- [ ] **NEW** `test_dead_letter_persists_negative_group_chat_id` (defect 3, guard #1) — `_dead_letter_message` with a negative chat_id (e.g. `-1003900483201`) and non-empty text calls `persist_failed_delivery` with that chat_id; the same call with `chat_id == 0` returns early and does NOT persist.
- [ ] **NEW** `test_replay_dead_letter_survives_negative_chat_id` (defect 3, guard #2) — a persisted `DeadLetter` with a negative chat_id survives a `replay_dead_letters` pass: it is NOT `async_delete()`d, and the mock client's `send_message` IS called with the negative chat_id (the record re-sends rather than being dropped). A `chat_id == 0` record is still deleted. This test lives wherever `replay_dead_letters` is already exercised (dead-letter test module); add the module to scope if no such test exists yet.
- [ ] **NEW** `test_floodwait_propagates_from_send_queued_message` — `send_message` raises `FloodWaitError(seconds=N)` → it propagates out of `_send_queued_message` (not swallowed to `None`).
- [ ] **NEW** `test_floodwait_honored_without_burning_retries` — in `process_outbox`, a `FloodWaitError` triggers an `asyncio.sleep` of the honored interval and re-queues **without** incrementing `_relay_attempts`.
- [ ] **NEW** `test_floodwait_backstop_dead_letters` — after `RELAY_FLOOD_WAIT_MAX` flood waits, the message is dead-lettered.
- [ ] **NEW** `test_floodwait_after_file_send_skips_file_on_retry` — composition: file ships, text step floods → retry skips file (idempotency) and re-sends text only.

## Rabbit Holes

- **Don't redesign the two-step file+text send into a single captioned send.** The split is deliberate (caption-column layout, comment at `telegram_relay.py:342-343`). Keep it; just make it idempotent.
- **Don't build a cross-process / Redis-backed file-dedup cache** (the issue's optional "belt-and-suspenders" part 3). The in-flight `message`-dict flag fully fixes the observed bug; a content/path-level global dedup is a separate, larger effort — defer.
- **Don't reschedule flood waits onto a separate timer/queue.** The bridge's proven pattern is a blocking sleep; the relay processes one message at a time, and the sleep ceiling bounds the worst case. A scheduler is over-engineering for Small appetite.
- **Don't touch the voice-note branch** beyond letting `FloodWaitError` propagate — it has its own send path and is out of the observed incident.
- **Do extract the oversized-text DETECTION + conversion, but NOT the terminal send.** Defect 2 *requires* reaching the oversized-text conversion from the file branch. Implement it by extracting the existing text-only block (`telegram_relay.py:388-438`) into a shared helper scoped to the conversion decision only — NOT by copy-pasting the block (NO LEGACY CODE TOLERANCE), and NOT by pulling the terminal send into the helper. The two call sites send via different functions (file branch: raw `send_message` at line 364; text-only: `send_markdown` at line 443), so a helper that performed the terminal send would route one site through the wrong function. The helper returns the converted-attachment `msg_id` or `None`; each call site keeps its own terminal send. Don't otherwise redesign the conversion (tempfile naming, caption text, fall-through-on-failure behavior stay identical).

## Risks

### Risk 1: Blocking sleep stalls the whole relay loop during a long flood wait
**Impact:** All queued messages wait while the relay sleeps for the flood interval.
**Mitigation:** `RELAY_FLOOD_WAIT_MAX_SLEEP_SECS` ceiling (default 300s) caps any single sleep; the flood backstop (`RELAY_FLOOD_WAIT_MAX`) dead-letters rather than looping forever. A flood wait means Telegram is rate-limiting this client anyway — pausing sends is the correct behavior, matching the inbound bridge.

### Risk 2: `_file_sent` flag persists onto a re-queue but the persistent file is later removed
**Impact:** On a retry the guard skips the file but the file is gone — acceptable, since the goal is "send the file at most once"; the text still ships. If the file never sent at all, `_file_sent` was never set, so the normal path runs.
**Mitigation:** Flag is set strictly **after** a confirmed successful `send_file`. No false positives.

### Risk 3: Extra `message` keys (`_file_sent`, `_file_msg_id`, `_flood_waits`) leak into persisted/logged payloads
**Impact:** Cosmetic; underscore-prefixed internal keys mirror the existing `_relay_attempts` convention.
**Mitigation:** Follow the exact `_relay_attempts` precedent (already serialized on re-queue, ignored elsewhere). No new persistence surface.

### Risk 4: Narrowing only ONE of the two `<= 0` guards leaves the fix a silent no-op
**Impact:** If only the persist-side guard (`_dead_letter_message`, `telegram_relay.py:650`) is narrowed but the replay-side guard (`replay_dead_letters`, `dead_letters.py:57`) is left at `<= 0`, the negative-chat_id record is persisted but then `async_delete()`d on the next bridge startup before it can re-send — the payload is still lost, just one process-restart later. This is the trap the original plan walked into by calling defect 3 a "single-file change."
**Mitigation:** Both guards are narrowed to `== 0` in lockstep (Technical Approach defect 3, guards #1 and #2). The replay-survival test (`test_replay_dead_letter_survives_negative_chat_id`) asserts the record survives a replay pass and `send_message` is actually called — it fails if either guard is left at `<= 0`. `replay_dead_letters` passes `int(chat_id)` straight to Telethon `send_message` (`dead_letters.py:67`), and negative chat_ids are valid Telegram peers (the entry path at line 266 accepts them), so the re-send itself is correct once the guard is narrowed.

### Risk 5: Replay path truncates oversized text (`dead_letters.py:65-66`)
**Impact:** `replay_dead_letters` truncates `len(text) > 4096` to `text[:4093] + "..."` before re-sending. A text that reaches replay-truncation is sent lossy rather than as a `.txt` attachment.
**Mitigation / scope call:** Scoped OUT (see Technical Approach defect 3). Defect 2's relay-time `.txt`-conversion means an oversized file+text message never reaches dead-letter, so the only text hitting replay-truncation is a text-only message that exhausted retries for a non-length reason — where lossy-but-delivered beats silently-dropped. Fixing replay to `.txt`-convert is a larger change to a different send path, deferred out of Small appetite. Documented, not solved.

## Race Conditions

### Race 1: File ships, then the process/relay dies before the re-queue persists the `_file_sent` flag
**Location:** `bridge/telegram_relay.py:345-370` (file send) → `:778-788` (re-queue).
**Trigger:** `send_file` succeeds; crash before `process_outbox` re-pushes the mutated `message`.
**Data prerequisite:** `_file_sent=True` must be persisted (via the re-queue's `json.dumps(message)`) before the next attempt reads it.
**State prerequisite:** Redis re-queue write must complete.
**Mitigation:** This is the pre-existing at-least-once delivery semantics of the outbox (the message was already LPOP'd; a crash mid-handling can drop or duplicate regardless). The idempotency flag does not make this worse than today; it strictly reduces duplicates for the common (no-crash) retry path. Out of scope to make the outbox exactly-once — noted, not solved.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1749] Optional content/path-level global file-dedup cache (the issue's "belt-and-suspenders" part 3) — the in-flight flag fixes the observed bug; a cross-message dedup cache is a larger, separate effort tracked under the same issue's follow-on note. *(If pursued, file a dedicated issue; not built here.)*
- [EXTERNAL] Attaching the live incident bridge-log slice from the "Valor the Bald" machine to issue #1749 — that machine's operator must do this; the code-level root cause stands on its own and the fix does not depend on the log slice.

## Update System

No update system changes required — this is a bridge-internal bug fix in `bridge/telegram_relay.py` with no new dependencies, config files, or migration steps. `telethon` is already installed everywhere the relay runs. The running relay picks up the fix on the next `./scripts/valor-service.sh restart` after merge/deploy.

## Agent Integration

No agent integration required — this is a bridge-internal change to the outbox relay loop. No new CLI entry point, no MCP tool, no `.mcp.json` change. The bridge already imports and runs `process_outbox` via the relay loop; the fix is transparent to the agent. Integration coverage is the existing relay unit suite.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md` (or the relay's feature doc if one exists) with a short note on the relay's file-send idempotency guard, the file-branch oversized-text `.txt` conversion (shared detection helper, per-call-site terminal send), the group/supergroup-safe dead-letter (BOTH the persist guard in `telegram_relay.py` and the replay guard in `dead_letters.py`, narrowed in lockstep), and send-path FloodWait honoring (distinct from the bridge's connect-loop handler). If no relay-specific feature doc exists, add a subsection there.

### Inline Documentation
- [ ] Comment the `_file_sent` guard referencing #1749 and the #1205 text-dedup analogue.
- [ ] Comment the shared oversized-text helper noting it serves both the file+text and text-only paths AND that it deliberately does NOT perform the terminal send (callers send via `send_message` vs `send_markdown` respectively) (#1749 defect 2 was the unreachable-guard bug).
- [ ] Comment BOTH narrowed dead-letter guards (`telegram_relay.py:650` persist side and `dead_letters.py:57` replay side) referencing the line-266 entry-side `== 0` precedent, noting negative IDs are valid group/supergroup peers and that the two guards MUST stay in lockstep or the fix is a no-op (#1749 defect 3).
- [ ] Comment the FloodWait branch noting it borrows only the blocking-sleep shape from the `telegram_bridge.py:2659` connect-loop handler — it is a send-path handler, not a connect-path one, and intentionally omits the `_write_flood_backoff` side-effect (#1749 defect 4).
- [ ] Docstring note on `_send_queued_message` that it may mutate `message` with `_file_sent` / `_file_msg_id` idempotency keys.

[No external docs site in this repo.]

## Success Criteria

- [ ] A **persistent (non-cleanup) file** whose text step fails on the first attempt is sent **exactly once** across all retries (new test `test_file_not_resent_on_text_step_retry` passes). (Cleanup files have different send semantics — they are unlinked after send, so `available` is empty on retry and the existing "all files missing → text-only" path governs them; the idempotency guard targets the persistent-file case that the incident exhibited.)
- [ ] An oversized follow-up text on a **file+text** message is converted to a `.txt` attachment instead of raising `MessageTooLongError`, so the message drains rather than dead-lettering permanently (new test `test_oversized_text_on_file_message_converts_to_txt` passes). *(Defect 2 — the actual incident trigger.)*
- [ ] A dead-lettered message for a **group/supergroup** (negative chat_id) is persisted for replay; only `chat_id == 0` is discarded (new test `test_dead_letter_persists_negative_group_chat_id` passes). *(Defect 3, guard #1.)*
- [ ] A persisted negative-chat_id dead-letter **survives a replay pass and actually re-sends** — it is not deleted by the replay-side guard (new test `test_replay_dead_letter_survives_negative_chat_id` passes; `grep -c 'chat_id_int <= 0' bridge/dead_letters.py` == 0). *(Defect 3, guard #2 — the lockstep fix that makes guard #1 non-no-op.)*
- [ ] `FloodWaitError` from any relay send is honored via `asyncio.sleep` and does **not** increment `_relay_attempts` (new flood tests pass).
- [ ] A flood condition exceeding the backstop dead-letters cleanly rather than looping forever.
- [ ] All existing `tests/unit/test_bridge_relay.py` cases stay green (happy path unchanged).
- [ ] `grep FloodWait bridge/telegram_relay.py` now returns matches (defect 4 closed).
- [ ] Tests pass (`/do-test` on the relay suite).
- [ ] Documentation updated (`/do-docs`).
- [ ] `python -m ruff format` clean.

## Team Orchestration

Small appetite — solo dev drives the change directly with a validator pass. No parallel fan-out needed (single file).

### Team Members

- **Builder (relay-fix)**
  - Name: relay-builder
  - Role: Implement idempotency guard + FloodWait handling + constants in `bridge/telegram_relay.py` and the new unit tests.
  - Agent Type: builder
  - Resume: true

- **Validator (relay-fix)**
  - Name: relay-validator
  - Role: Verify the new tests pass, existing relay tests stay green, and the diff matches the Technical Approach.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard set; this plan uses `builder` + `validator` + `documentarian`.

## Step by Step Tasks

### 1. Add idempotency guard + oversized-text file-branch guard + dead-letter narrowing (both guards) + FloodWait handling
- **Task ID**: build-relay-fix
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_relay.py
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: false
- **Scope:** `bridge/telegram_relay.py` AND `bridge/dead_letters.py` (defect 3 guard #2).
- **Defect 1 (idempotency):** In `_send_queued_message` file branch: guard `send_file` on `message.get("_file_sent")`; set `_file_sent` / `_file_msg_id` after a successful send.
- **Defect 2 (oversized file+text):** Extract the existing text-only oversized-text DETECTION + `.txt`-conversion block (`telegram_relay.py:388-438`) into a shared helper scoped to the conversion DECISION only (returns the converted-attachment `msg_id` or `None`; does NOT perform the terminal send). Call it from the file branch *before* the follow-up `send_message` at line 364, and from the text-only path. Each call site keeps its OWN terminal send — file branch: raw `send_message` (line 364); text-only: `send_markdown` (line 443). Preserve fall-through-on-failure to each per-site terminal send.
- **Defect 3 (dead-letter narrowing — BOTH guards, lockstep):** In `_dead_letter_message`, change `if chat_id_int <= 0:` to `if chat_id_int == 0:` (`telegram_relay.py:650`). In `replay_dead_letters`, change `if chat_id_int <= 0:` to `if chat_id_int == 0:` (`dead_letters.py:57`). Update both comments/logs to match the line-266 precedent. `persist_failed_delivery` already stores chat_id as an opaque string — no change needed there. Do NOT touch the replay-path truncation at `dead_letters.py:65-66` (scoped out, Risk 5).
- **Defect 4 (FloodWait):** Add the three tunable constants near `MAX_RELAY_RETRIES` with provisional-tuning comments. Add `from telethon.errors import FloodWaitError` at module top. Add `except FloodWaitError: raise` before the generic handler in `_send_queued_message` (and the other two handlers iff they swallow it). In `process_outbox`: add the `except FloodWaitError` dispatch branch (honor sleep, increment `_flood_waits`, re-queue without burning `_relay_attempts`, backstop dead-letter).

### 2. Write unit tests
- **Task ID**: build-relay-tests
- **Depends On**: build-relay-fix
- **Validates**: tests/unit/test_bridge_relay.py
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the NEW tests from Test Impact (idempotency, oversized file+text via shared helper, oversized text-only regression guard, negative-chat_id dead-letter persist (guard #1), negative-chat_id replay survival (guard #2), FloodWait propagate/honor/backstop, flood-after-file composition), reusing the existing mock-client pattern. The replay-survival test goes in the dead-letter test module that exercises `replay_dead_letters`.
- Confirm existing relay tests stay green.

### 3. Validate
- **Task ID**: validate-relay-fix
- **Depends On**: build-relay-tests
- **Assigned To**: relay-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_bridge_relay.py -q` and confirm all pass.
- Confirm `grep FloodWait bridge/telegram_relay.py` returns matches.
- Confirm the diff matches the Technical Approach (no scope creep into voice/oversized branches).

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-relay-fix
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update the relay/bridge feature doc with the idempotency + FloodWait note.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: relay-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the relay unit suite, confirm all success criteria met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Relay tests pass | `pytest tests/unit/test_bridge_relay.py -q` | exit code 0 |
| FloodWait handled | `grep -c FloodWait bridge/telegram_relay.py` | output > 0 |
| Idempotency flag present | `grep -c _file_sent bridge/telegram_relay.py` | output > 0 |
| Relay dead-letter guard narrowed | `grep -c 'chat_id_int <= 0' bridge/telegram_relay.py` | output == 0 |
| Replay dead-letter guard narrowed (lockstep) | `grep -c 'chat_id_int <= 0' bridge/dead_letters.py` | output == 0 |
| Replay-survival test present | `grep -rc 'test_replay_dead_letter_survives_negative_chat_id' tests/unit/` | output > 0 |
| Oversized conversion shared | `grep -c 'exceeded 4096 chars' bridge/telegram_relay.py` | output > 0 (exactly one definition — helper, not duplicated) |
| Format clean | `python -m ruff format --check bridge/telegram_relay.py bridge/dead_letters.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | Un-guarded follow-up `send_message` at `telegram_relay.py:364`: real incident trigger was `MessageTooLongError` (permanent), which idempotency doesn't fix — oversized text dead-letters every retry. | Defect 2 (Solution + Technical Approach) | Port the text-only oversized-text `.txt` guard into the file+text branch *before* line 364 via a shared extracted helper; oversized caption → attachment, not permanent fail. |
| BLOCKER | critique | New flood-backstop dead-letter inherits the `_dead_letter_message` `chat_id <= 0` silent drop (line 650), losing group/supergroup payloads like chat `-1003900483201`. | Defect 3 (Solution + Technical Approach) | Narrow the guard to `chat_id_int == 0`, matching the entry-side precedent at line 266; negative group/supergroup IDs are now persisted for replay. |
| CONCERN | critique | Part 2 FloodWait handling is speculative scope never exercised in the incident — keep bundled or split? | "Scope decision" callout in Technical Approach (Defect 4) | Decision: KEEP bundled. Same send path, zero conflict surface, closes a latent ban-escalation gap; FloodWait constants are the clean deferral seam if a reviewer disagrees. |
| CONCERN | critique | "exactly once" success criterion doesn't match the code path — cleanup files have different send semantics. | Success Criteria reword | Criterion now reads "persistent (non-cleanup) file" with a parenthetical explaining cleanup-file semantics. |
| BLOCKER (2nd pass) | re-critique | Defect 3 is a no-op: narrowing only the relay-side guard leaves `dead_letters.py:57` `replay_dead_letters` guard (`<= 0`) which `async_delete()`s the negative-chat_id record on next bridge startup. Plan claimed "single-file change" and never named `replay_dead_letters`. | Defect 3 (Technical Approach guards #1+#2), Step 1 scope, Verification table, Risk 4, new replay-survival test | Both `<= 0` guards (`telegram_relay.py:650`, `dead_letters.py:57`) narrowed to `== 0` in lockstep; `dead_letters.py` added to scope; "single-file" framing removed; `test_replay_dead_letter_survives_negative_chat_id` asserts the record survives replay and re-sends. |
| BLOCKER (2nd pass) | re-critique | Shared oversized-text helper conflated two terminal send functions: file branch uses raw `send_message` (line 364), text-only uses `send_markdown` (line 443). A helper doing the terminal send would route one site through the wrong function. | Defect 2 (Technical Approach), Key Elements, Rabbit Holes, Test Impact | Helper scoped to the `.txt`-conversion DECISION ONLY (returns attachment `msg_id` or `None`); each call site keeps its own terminal send (`send_message` vs `send_markdown`); regression test added for the text-only `send_markdown` path. |
| CONCERN (2nd pass) | re-critique | Replay path truncates oversized text (`dead_letters.py:65-66`) — could undo defect-2 intent. | Defect 3 (Technical Approach), Risk 5 | Scoped OUT with justification: defect-2 relay-time conversion means oversized file+text never reaches dead-letter; only non-length-failed text-only messages hit replay-truncation, where lossy-but-delivered beats dropped. Builder told NOT to touch it. |
| CONCERN (2nd pass) | re-critique | `telegram_bridge.py:2659` cited as a send-path FloodWait precedent, but it's a connect/auth-loop handler with a `_write_flood_backoff` side-effect. | Problem statement defect 4, Freshness Check, Technical Approach defect 4, Inline Documentation | Citation corrected throughout: we borrow only the blocking-sleep shape, NOT the connect-loop control flow or the `_write_flood_backoff` side-effect. |

---

## Open Questions

None — the incident logs (`/Users/valorengels/Downloads/issue1749_logs`) and code-level verification fully specify all four defects, their root causes, and the desired outcomes. Two design decisions are resolved in-plan: (1) blocking sleep vs. reschedule for FloodWait → bounded blocking sleep, borrowing only the sleep shape from the inbound-bridge connect-loop precedent; (2) keep FloodWait handling bundled vs. split to a follow-up issue → KEEP bundled (rationale in the Defect 4 scope-decision callout). The first-pass critique's two blockers (oversized file+text guard; group/supergroup dead-letter drop) and two concerns (FloodWait scope; "exactly once" wording) are resolved. The second-pass re-critique's two new blockers — (a) defect 3 was a no-op without narrowing the `replay_dead_letters` guard at `dead_letters.py:57` in lockstep, and (b) the shared oversized-text helper conflated the file branch's `send_message` (line 364) with the text-only branch's `send_markdown` (line 443) — are resolved: both `<= 0` guards are now narrowed together with a replay-survival test, and the helper is scoped to the conversion decision only while each call site keeps its own terminal send. The two second-pass concerns (replay-path truncation scoped out with justification; `telegram_bridge.py:2659` connect-loop citation corrected) are addressed above.
