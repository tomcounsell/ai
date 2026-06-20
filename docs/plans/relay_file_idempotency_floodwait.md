---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-06-20
tracking: https://github.com/tomcounsell/ai/issues/1749
last_comment_id:
---

# Telegram Relay: File-Send Idempotency + FloodWaitError Handling

## Problem

The Telegram relay (`bridge/telegram_relay.py`) — the outbox-draining worker loop that ships PM/agent messages to Telegram via Telethon — has two compounding defects that together produced a burst of repeated file sends followed by a hard dead-letter fail, first observed in the **Eng: Cyndra** group.

**Current behavior:**

1. **Duplicate file sends.** A file message is delivered in two non-atomic steps: `send_file(...)` (no caption, `telegram_relay.py:345`) then `send_message(...)` (the text, `telegram_relay.py:364`). If the **text step fails**, `_send_queued_message` returns `None`, and `process_outbox` re-queues the *entire* payload — file paths included (`telegram_relay.py:778-788`). On the next attempt the file is sent **again**. Persistent files (Cyndra studio-probe outputs that survive on disk between retries) get re-sent once per retry; `cleanup_file` files are unlinked after send so they can't recur. The text send is already de-duped via `recent_sent_drafts` / `redundancy_filter` (`telegram_relay.py:748-759`, the #1205 fix); the **file send has no equivalent idempotency guard**.

2. **No FloodWaitError handling.** The relay has no `FloodWaitError` handling at all (`grep FloodWait bridge/telegram_relay.py` → nothing). Telethon raises `telethon.errors.FloodWaitError` when Telegram demands an N-second backoff. The relay's generic catch treats it as a plain failure, immediately increments `_relay_attempts`, and re-queues — **hammering Telegram while it asks the client to back off**, escalating toward a flood ban, then dead-lettering after `MAX_RELAY_RETRIES` (3). The inbound bridge already handles this correctly by sleeping for the required interval (`bridge/telegram_bridge.py:2659`).

**Desired outcome:**

- A file is sent **at most once** per logical message, regardless of how many times the text step is retried.
- A flood wait is **honored** (sleep the required interval, as the bridge does) instead of counted as a failed attempt and immediately retried — no retry-budget burn, no ban escalation.

## Freshness Check

**Baseline commit:** `457e1f78`
**Issue filed at:** 2026-06-20T08:13:34Z
**Disposition:** Unchanged

**File:line references re-verified (all still hold at `457e1f78`):**
- `bridge/telegram_relay.py:46` — `MAX_RELAY_RETRIES = 3` — confirmed.
- `bridge/telegram_relay.py:344-349` — two-step file send: `send_file` without caption — confirmed.
- `bridge/telegram_relay.py:363-369` — separate `send_message` text step — confirmed.
- `bridge/telegram_relay.py:360-362` — `cleanup_file` unlink after send — confirmed.
- `bridge/telegram_relay.py:778-794` — bounded-retry / re-queue / dead-letter block — confirmed.
- `grep FloodWait bridge/telegram_relay.py` → no matches — confirmed (defect 2 still present).
- `bridge/telegram_bridge.py:2659` — `except FloodWaitError as e:` sleeps `e.seconds + 5` — confirmed (the reference pattern).

**Cited sibling issues/PRs re-checked:** #1205 (text-dedup precedent) — referenced by the in-code comment at `telegram_relay.py:748-759`, still present. #698 (relay-retry-guard, merged) — added the bounded-retry block this plan extends.

**Commits on main since issue was filed (touching `bridge/telegram_relay.py`):** none.

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

Single-file change (`bridge/telegram_relay.py`) plus unit tests in the existing `tests/unit/test_bridge_relay.py`. The bottleneck is correctness review, not code volume.

## Prerequisites

No prerequisites — `telethon` (which exports `FloodWaitError`) is already a project dependency and already imported in `bridge/telegram_bridge.py`. No external services, keys, or config.

## Solution

### Key Elements

- **File-send idempotency flag** — once a file successfully ships, mark it on the in-flight `message` dict so a re-queue (caused by a later failing step) resends only the **text**, never the file.
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

**Defect 2 — FloodWait handling (in `_send_queued_message` + `process_outbox`):**
- Add `from telethon.errors import FloodWaitError` at module top (mirroring `telegram_bridge.py:90`).
- In `_send_queued_message`, add `except FloodWaitError: raise` **before** the generic `except Exception` (line 455) so a flood wait propagates instead of being swallowed to `None`. Apply the same `except FloodWaitError: raise` to `_send_custom_emoji_message` and `_send_queued_reaction` if (and only if) their bodies have a generic `except` that would otherwise swallow it — verified at build time.
- In `process_outbox`, the dispatch is already wrapped in `try/except Exception as handler_err` (line 720-734). Add a dedicated `except FloodWaitError as flood_err:` clause **before** the generic one that:
  1. Honors the wait: `await asyncio.sleep(min(flood_err.seconds + RELAY_FLOOD_WAIT_BUFFER_SECS, RELAY_FLOOD_WAIT_MAX_SLEEP_SECS))`.
  2. Increments a separate `message["_flood_waits"]` counter (NOT `_relay_attempts`).
  3. If `_flood_waits >= RELAY_FLOOD_WAIT_MAX` (backstop) → dead-letter; else re-queue the same `message` (carrying `_file_sent` if set) and `continue` to the next message.
- New tunable constants near `MAX_RELAY_RETRIES` (`telegram_relay.py:46`), each named and env-overridable with a "provisional — tune from production flood-wait telemetry" comment (per the magic-numbers convention):
  - `RELAY_FLOOD_WAIT_BUFFER_SECS` (default 5 — matches the bridge's `e.seconds + 5`)
  - `RELAY_FLOOD_WAIT_MAX_SLEEP_SECS` (default 300 — ceiling so a huge flood value can't wedge the loop)
  - `RELAY_FLOOD_WAIT_MAX` (default 10 — backstop against an endless flood loop)

**Interaction (both defects together):** when a flood wait is raised by the **text** step, the file has already shipped and `_file_sent=True` is set on `message`; the flood re-queue carries the flag, so the retry skips the file and only re-sends text. The two fixes compose correctly.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_send_queued_message`'s generic `except Exception` (line 455) already logs `logger.error(... exc_info=True)` and returns `None` — its observable behavior (return `None` → re-queue) is covered by the existing `test_returns_none_on_send_failure`. The new `except FloodWaitError: raise` is covered by a test asserting the exception propagates (not swallowed to `None`).
- [ ] `process_outbox`'s new `except FloodWaitError` branch — test asserts (a) `asyncio.sleep` is called with the honored interval, (b) `_relay_attempts` is **not** incremented, (c) the message is re-queued (not dead-lettered) until the flood backstop is hit.

### Empty/Invalid Input Handling
- [ ] Idempotency flag is only read/written when `file_paths` is non-empty and files are `available`; empty/None file lists fall through unchanged. Covered by re-running existing `test_missing_file_*` cases (they must stay green).
- [ ] No agent-output processing in scope — no silent-loop risk.

### Error State Rendering
- [ ] Relay output is the Telegram delivery itself; the failure path is observable via logs and the dead-letter queue. Test asserts a flood wait beyond the backstop routes to `_dead_letter_message` with a flood-specific reason string.

## Test Impact

All tests live in `tests/unit/test_bridge_relay.py` (mock-client pattern: `send_file` / `send_message` as `AsyncMock`s). Changes are **additive** — existing assertions remain valid because the happy path is unchanged.

- [ ] `tests/unit/test_bridge_relay.py::test_sends_file_via_send_file` — UPDATE (defensive): confirm still green; the success path sets `_file_sent` but still calls `send_file` exactly once and returns the same `msg_id`.
- [ ] `tests/unit/test_bridge_relay.py::test_file_only_send_no_caption` — verify unchanged (no text → no idempotency interaction).
- [ ] `tests/unit/test_bridge_relay.py::test_missing_file_falls_back_to_text` / `test_missing_file_no_text_returns_none` / `test_backward_compat_file_path_string` — verify unchanged (additive guard doesn't alter these paths).
- [ ] `tests/unit/test_bridge_relay.py::test_max_relay_retries` — verify unchanged (`MAX_RELAY_RETRIES == 3` constant untouched).
- [ ] **NEW** `test_file_not_resent_on_text_step_retry` — file send succeeds, text step raises generic error → `_send_queued_message` returns `None`, `message["_file_sent"]` is `True`; a second call with the same dict does **not** call `send_file` again but does call `send_message`.
- [ ] **NEW** `test_floodwait_propagates_from_send_queued_message` — `send_message` raises `FloodWaitError(seconds=N)` → it propagates out of `_send_queued_message` (not swallowed to `None`).
- [ ] **NEW** `test_floodwait_honored_without_burning_retries` — in `process_outbox`, a `FloodWaitError` triggers an `asyncio.sleep` of the honored interval and re-queues **without** incrementing `_relay_attempts`.
- [ ] **NEW** `test_floodwait_backstop_dead_letters` — after `RELAY_FLOOD_WAIT_MAX` flood waits, the message is dead-lettered.
- [ ] **NEW** `test_floodwait_after_file_send_skips_file_on_retry` — composition: file ships, text step floods → retry skips file (idempotency) and re-sends text only.

## Rabbit Holes

- **Don't redesign the two-step file+text send into a single captioned send.** The split is deliberate (caption-column layout, comment at `telegram_relay.py:342-343`). Keep it; just make it idempotent.
- **Don't build a cross-process / Redis-backed file-dedup cache** (the issue's optional "belt-and-suspenders" part 3). The in-flight `message`-dict flag fully fixes the observed bug; a content/path-level global dedup is a separate, larger effort — defer.
- **Don't reschedule flood waits onto a separate timer/queue.** The bridge's proven pattern is a blocking sleep; the relay processes one message at a time, and the sleep ceiling bounds the worst case. A scheduler is over-engineering for Small appetite.
- **Don't touch the voice-note or oversized-text `.txt` branches** beyond letting `FloodWaitError` propagate — they have their own send paths and are out of the observed incident.

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
- [ ] Update `docs/features/bridge-worker-architecture.md` (or the relay's feature doc if one exists) with a short note on the relay's file-send idempotency guard and FloodWait honoring, mirroring how the inbound bridge's FloodWait handling is described. If no relay-specific feature doc exists, add a subsection there.

### Inline Documentation
- [ ] Comment the `_file_sent` guard referencing #1749 and the #1205 text-dedup analogue.
- [ ] Comment the FloodWait branch referencing the `telegram_bridge.py:2659` precedent.
- [ ] Docstring note on `_send_queued_message` that it may mutate `message` with `_file_sent` / `_file_msg_id` idempotency keys.

[No external docs site in this repo.]

## Success Criteria

- [ ] A persistent file whose text step fails on the first attempt is sent **exactly once** across all retries (new test `test_file_not_resent_on_text_step_retry` passes).
- [ ] `FloodWaitError` from any relay send is honored via `asyncio.sleep` and does **not** increment `_relay_attempts` (new flood tests pass).
- [ ] A flood condition exceeding the backstop dead-letters cleanly rather than looping forever.
- [ ] All existing `tests/unit/test_bridge_relay.py` cases stay green (happy path unchanged).
- [ ] `grep FloodWait bridge/telegram_relay.py` now returns matches (defect 2 closed).
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

### 1. Add idempotency guard + FloodWait handling
- **Task ID**: build-relay-fix
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_relay.py
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the three tunable constants near `MAX_RELAY_RETRIES` with provisional-tuning comments.
- Add `from telethon.errors import FloodWaitError` at module top.
- In `_send_queued_message` file branch: guard `send_file` on `message.get("_file_sent")`; set `_file_sent` / `_file_msg_id` after a successful send.
- Add `except FloodWaitError: raise` before the generic handler in `_send_queued_message` (and the other two handlers iff they swallow it).
- In `process_outbox`: add the `except FloodWaitError` dispatch branch (honor sleep, increment `_flood_waits`, re-queue without burning `_relay_attempts`, backstop dead-letter).

### 2. Write unit tests
- **Task ID**: build-relay-tests
- **Depends On**: build-relay-fix
- **Validates**: tests/unit/test_bridge_relay.py
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the five NEW tests from Test Impact, reusing the existing mock-client pattern.
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
| Format clean | `python -m ruff format --check bridge/telegram_relay.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the issue fully specifies both defects, their root causes (confirmed at the code level), and the desired outcomes. The single design decision the issue flagged (blocking sleep vs. reschedule for FloodWait) is resolved here in favor of a bounded blocking sleep, matching the proven inbound-bridge precedent.
