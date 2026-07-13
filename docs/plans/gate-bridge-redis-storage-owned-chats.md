---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2020
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-07-13T07:12:28Z
---

# Gate Bridge Redis Storage to Machine-Owned Chats (Cache, Not Archive)

## Problem

The account this bridge runs as belongs to many large, high-traffic Telegram
group chats that no project on this machine serves. In the `NewMessage` handler
(`bridge/telegram_bridge.py`), `store_message()` is called **unconditionally** —
every inbound message from every chat is written to `TelegramMessage` in Redis,
with `project_key=None` for unowned chats. The code comment says this is
intentional.

The only backstop is a global 90-day TTL swept once daily by the
`redis-ttl-cleanup` reflection. `TelegramMessage.cleanup_expired`
(`models/telegram.py:57`) does a full `cls.query.all()` scan and deletes one
record at a time in Python. Its docstring assumes `90 days × 50 msgs/day ≈ 4500
records` — but a single active large group produces hundreds-to-thousands of
messages per day. The volume that inflates storage is the same volume that makes
the daily O(n) sweep slow. There is no per-chat cap and no membership gate on
storage — only on whether the bridge *responds* and whether it writes to the
memory partition.

**Current behavior:**
Inbound messages from any chat the account receives are written to Redis, owned
or not. Unbounded per-chat growth, bounded only by a daily full-table sweep
racing against inflow.

**Desired outcome:**
The bridge stores to Redis **only** for chats this machine owns (plus a
registered-bot carve-out). Unowned chats become read-through: any flow needing
their history fetches it live from the Telegram API via Telethon. Nothing is
lost — Telegram holds the complete, durable history. Redis becomes a **cache of
the working set the bridge actively reasons over**, bounded structurally by
ownership rather than by a daily sweep.

## Freshness Check

**Baseline commit:** `4e297c6d`
**Issue filed at:** 2026-07-11T07:18:46Z
**Disposition:** Minor drift

Line numbers cited in the issue drifted (two commits landed on cited files since
filing) but every semantic claim still holds. Corrected line references below are
used throughout this plan.

**File:line references re-verified:**
- `bridge/telegram_bridge.py:1211` (unconditional `store_message`, "Store ALL incoming" comment) — **drifted to `:1219`–`:1245`**; call site and comment intact.
- `bridge/telegram_bridge.py:491-496` (`ACTIVE_PROJECTS` machine-scoped) — **drifted to `:510`/`:517`**; `_get_active_projects()` + `ACTIVE_PROJECTS = _get_active_projects()` intact.
- `bridge/telegram_bridge.py:1217` (`_early_project_key`) — **drifted to `:1225`**; `_early_project_key = project.get("_key") if project else None` intact.
- `bridge/telegram_bridge.py:1252-1268` (bot loop-guard, awaiter polls recorded history) — **drifted to `:1272`**; `if sender_id and find_project_for_bot(sender_id): ... return` intact.
- `bridge/routing.py:256` (resolution maps built over `ACTIVE_PROJECTS`) — **routing.py was heavily refactored by `443b5642` (net −67 lines)**; maps `GROUP_TO_PROJECT` / `DM_USER_TO_PROJECT` / `BOT_ID_TO_PROJECT` are still built by looping `for project_key in ACTIVE_PROJECTS` (lines 243/267/304); docstring at `:437` reaffirms "Both maps are already filtered to ACTIVE_PROJECTS for this machine." Claim holds.
- `bridge/routing.py:377-378` (name-based substring group matching) — **drifted to `find_project_for_chat` at `:382`–`:391`**; `chat_lower = chat_title.lower(); for group_name, project in GROUP_TO_PROJECT.items(): if group_name in chat_lower` — still substring-by-title. Claim holds.
- `bridge/routing.py:397` (`find_project_for_bot`) — **drifted to `:408`**; resolves via a separate `BOT_ID_TO_PROJECT` map. Claim holds.
- `models/telegram.py:56-70` (`cleanup_expired` full-scan) — **drifted to `:57`–`:70`**; `cutoff = time.time() - (max_age_days * 86400); all_messages = cls.query.all()` intact. **The 90-day literal was NOT moved to `config/settings.py` by the timeout-centralization commit** — it remains a `max_age_days: int = 90` default param at `:57`.
- `config/reflections.yaml:155` (`redis-ttl-cleanup` daily sweep) — **drifted to `:175`–`:182`**; `every: 86400s`, `callable: reflections.maintenance.run_redis_ttl_cleanup`, intact.
- `tools/telegram_history/__init__.py:346` (`store_message` def) — **still `:346`**; returns `{"stored": True, "id": ...}`.

**Cited sibling issues/PRs re-checked:**
- #1574 (bot E2E `--await-reply` + deterministic loop-guard + bot registry) — closed 2026-06-24. Established the registered-bot carve-out this plan must preserve. Its guard code (`telegram_bridge.py:1272`, `find_project_for_bot`) is live and is exactly the seam this plan's predicate reuses.
- #1173 (retired "dm" memory namespace) — its gate (`if _early_project_key:` before the Memory write, `telegram_bridge.py:1282`) is the existing precedent for gating a write on resolved ownership. This plan applies the same pattern to storage.

**Commits on main since issue was filed (touching referenced files):**
- `e1ec8695` "Centralize magic timeout/retry/TTL literals into config/settings.py" — +12/−4 to `telegram_bridge.py` only; did **not** touch the store call site, `cleanup_expired`, or the 90-day literal. Irrelevant to root cause.
- `443b5642` "Standardize non-harness LLM calls on a PydanticAI wrapper" — refactored `bridge/routing.py` (−67 net). All three resolver functions and the `ACTIVE_PROJECTS`-scoped map construction survive intact. Irrelevant to root cause.

**Active plans in `docs/plans/` overlapping this area:** none. (`consolidate_delivery_paths.md` touches delivery/output routing, not inbound storage; no overlap with the store gate.)

**Notes:** The heavy `routing.py` refactor is the only meaningful drift. All resolver seams this plan depends on (`find_project_for_chat`, `find_project_for_dm`, `find_project_for_bot`, `ACTIVE_PROJECTS`-scoped maps) are confirmed present and semantically unchanged.

## Prior Art

- **Issue #1574** (closed 2026-06-24): Bot E2E testing via `valor-telegram send --await-reply` + deterministic bridge loop-guard + bot registry. Introduced `find_project_for_bot` / `BOT_ID_TO_PROJECT` and the rule "a registered bot's message is recorded to history but never spawns a session." The `--await-reply` awaiter polls that recorded history. **This is the carve-out this plan must not break** — the storage predicate must keep storing bot messages even when the bot resolves no project.
- **Issue #1173** (retired "dm" namespace): Established gating the Memory-partition write on a resolved `project_key` (`if _early_project_key:` at `telegram_bridge.py:1282`). This plan extends the same "gate the write on resolved ownership" pattern from the memory surface to the storage surface. Storage was deliberately left ungated in #1173 (the comment at `:1219` documents that choice); #2020 revisits it.
- No prior PR attempted to gate `TelegramMessage` storage on ownership. No failed attempts to learn from.

## Research

No relevant external findings — proceeding with codebase context. This is purely
internal: no new external libraries, APIs, or ecosystem patterns. The
"read-through from Telegram" capability is already established and in production
use (`valor-telegram read` falls back to the Telethon/Telegram API for uncached
chats), so no new dependency is introduced.

## Data Flow

The change touches exactly one entry point: the inbound `NewMessage` handler.

1. **Entry point**: `@client.on(events.NewMessage)` closure at `telegram_bridge.py:1137`. A Telegram update arrives for the logged-in account (any chat it belongs to).
2. **Resolution**: `project = find_project_for_dm(...) or find_project_for_chat(chat_title)` → `_early_project_key = project.get("_key") if project else None` (`:1225`). For unowned chats this is `None`. Bots resolve separately via `find_project_for_bot(sender_id)`.
3. **Storage (the gated point)**: `store_message(...)` at `:1231` writes a `TelegramMessage`; on success, `register_chat(...)` at `:1249` records the chat-name mapping. **Today both run unconditionally.** After this change they run only when `should_store_inbound(...)` is true.
4. **Bot loop-guard**: `if sender_id and find_project_for_bot(sender_id): return` at `:1272` — bot messages are recorded above, then the handler returns without spawning a session. (The `--await-reply` awaiter later polls this recorded history.)
5. **Memory partition**: `if _early_project_key:` gate at `:1282` — already ownership-gated (#1173). Unchanged.
6. **Read-back (unowned chats)**: `valor-telegram read` → Redis cache miss → Telethon/Telegram API fetch. Unchanged; this is why gating storage loses nothing.

The outbound store site (`telegram_bridge.py:2954`, `sender="Valor"`) is the
bridge's own replies, which only go to owned chats it serves. It is **outside**
the inbound handler and is **not** gated by this change.

## Appetite

**Size:** Small

**Team:** Solo dev, plus one validator pair.

**Interactions:**
- PM check-ins: 1-2 (confirm retention decision + register_chat gating tradeoff)
- Review rounds: 1

The coding change is a single predicate and one `if` block wrapping an existing
call pair. The care is entirely in preserving the bot carve-out and choosing a
testable seam — not in volume of code.

## Prerequisites

No prerequisites — this work has no external dependencies. It runs against the
existing bridge, routing resolvers, and test suite.

## Solution

### Key Elements

- **`should_store_inbound(early_project_key, sender_id)` predicate**: A small, module-level, pure function in `bridge/telegram_bridge.py` (importable for unit tests) that returns `True` when the machine owns the chat OR the sender is a registered bot. This is the whole behavioral change, made testable.
- **Gated store block**: The existing `store_message(...)` + `register_chat(...)` pair in the `NewMessage` handler runs only when the predicate is `True`.
- **Retention decision (recorded, no code change)**: Keep the 90-day TTL. See Technical Approach.
- **Docs update**: `docs/features/telegram-history.md` states the cache-not-archive model.

### Flow

Inbound message → resolve `_early_project_key` (and `sender_id`) → **`should_store_inbound(...)`?** → if **yes**: `store_message` + `register_chat` (owned chats and registered bots) → if **no**: skip storage (unowned chat; read-through from Telegram on demand) → bot loop-guard return / memory gate as today.

### Technical Approach

**The predicate.** The machine-owned allowlist already exists; it just isn't
used to gate storage. Add a module-level helper (co-located with the handler, so
it can import `find_project_for_bot`, already imported at `:134`):

```python
def should_store_inbound(early_project_key: str | None, sender_id: int | None) -> bool:
    """Store to Redis only for machine-owned chats, plus registered bots.

    early_project_key is None exactly when this machine owns no project for the
    chat (every resolution map is built over ACTIVE_PROJECTS). Registered bots
    resolve via find_project_for_bot, not chat/DM resolution, so their
    early_project_key is often None — they must still be stored so the
    valor-telegram --await-reply awaiter can poll recorded history (#1574).
    """
    if early_project_key is not None:
        return True
    return bool(sender_id and find_project_for_bot(sender_id) is not None)
```

In the handler, wrap the existing store block (`:1229`–`:1258`):

```python
if should_store_inbound(_early_project_key, sender_id):
    store_result = store_message(...)
    if store_result.get("stored"):
        ...
        if chat_title:
            register_chat(...)
```

`register_chat` is gated **with** `store_message` (they are the paired write, per
the issue). Consequence: unowned chats no longer appear in the CLI chat registry
used by `valor-telegram chats`. That is consistent with cache-not-archive —
keeping `register_chat` ungated would reintroduce unbounded `Chat` record growth,
the same anti-pattern. Discoverability of unowned chats via `valor-telegram chats`
degrades gracefully (see Risks); `valor-telegram read --chat-id <N>` still works
via the API fallback.

**Retention decision — keep the 90-day TTL, no code change.** Once storage is
owned-chats-only, Redis volume collapses by orders of magnitude. Crucially, this
*also* fixes the daily-sweep scaling concern from the issue: `cleanup_expired`'s
O(n) `query.all()` scan was only slow *because of* the unowned-chat volume this
plan removes. With that volume gone, the docstring's `~4500 records` assumption
becomes realistic again, and 90 days of owned-chat scrollback is a sensible warm
working set for the conversation-history prefetch. The Telegram-API backstop
means shortening later is cheap and reversible (a one-line default change). We
therefore keep 90 days and do **not** touch `models/telegram.py` or
`config/reflections.yaml` in this plan.

**Why not gate inside `store_message`?** The gate is a *bridge routing* decision
(does this machine own this chat), not a storage-layer concern.
`store_message` in `tools/telegram_history/__init__.py` is also called from
non-handler paths (e.g. the outbound reply store at `:2954`) that must keep
writing. Gating in the handler keeps the storage helper reusable and leaves its
existing unit tests untouched.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The store block is already wrapped in `try/except Exception as e: logger.error(...)` (`:1257`). Moving it inside the `if should_store_inbound(...)` guard preserves that handler; no `except ...: pass` is introduced. The predicate itself is pure and cannot raise for valid `str|None` / `int|None` inputs.
- [ ] Assert the predicate never raises on `None` inputs (unit test with `(None, None)`).

### Empty/Invalid Input Handling
- [ ] `should_store_inbound(None, None)` → `False` (unowned DM/unknown sender).
- [ ] `should_store_inbound("", sender_id)` → `True` for a truthy empty-string project key would be wrong; the resolver never returns `""` (`project.get("_key")` yields a real key or the whole `project` is falsy → `None`). Test documents `None` is the only "unowned" sentinel; an explicit `""` is treated as owned by the `is not None` check — assert the resolver contract in the test comment.
- [ ] Whitespace/empty message content is orthogonal (handled inside `store_message`, unchanged).

### Error State Rendering
- [ ] No user-visible output path changes. The only observable effect is the *absence* of a Redis write for unowned chats, asserted directly in the new handler-level test.

## Test Impact

- [ ] `tests/tools/test_telegram_history.py` — **No change.** These test `store_message()` directly (the storage helper), which stays unconditional. The gate lives in the bridge handler, not in the helper. (The issue's Test Impact guessed "likely UPDATE" before the handler-vs-helper distinction was pinned down; recon confirms no update is needed.)
- [ ] `tests/integration/test_bot_await_reply.py` — **must still PASS unchanged.** Carve-out guard: a registered bot's message must still be stored so the awaiter settles.
- [ ] `tests/integration/test_bot_loop_guard.py` — **must still PASS unchanged.** Bot recorded-to-history-but-no-session behavior is unaffected.
- [ ] **NEW** `tests/unit/test_should_store_inbound.py` (or added to an existing bridge unit test module) — direct unit tests of the predicate: owned key → True; `None` → False; registered-bot sender with `None` key → True; unregistered sender with `None` key → False. Uses a monkeypatched `find_project_for_bot`.
- [ ] **NEW** handler-level coverage: an unowned-chat inbound message produces **no** new `TelegramMessage` record; an owned-chat inbound message still produces one. If the nested-closure handler proves impractical to drive directly in a unit test, cover this via the predicate unit tests plus an integration assertion that the store block is guard-wrapped (grep-level anti-criterion in Verification).

## Rabbit Holes

- **Moving group matching from title-substring to numeric `chat_id`.** `find_project_for_chat` matches config group *names* as substrings of the chat title (`routing.py:388`). Switching to numeric `event.chat_id` against config-declared ids would be more robust but is a config-schema change and a separate concern. The storage gate rides on the existing name-based resolution unchanged. **Explicitly out of scope — do not touch resolver matching semantics in this plan.**
- **Rewriting `cleanup_expired` to be incremental / indexed.** Tempting because the O(n) scan is ugly, but this plan's volume reduction makes it moot. Leave the sweep as-is.
- **Backfilling / purging already-stored unowned-chat records.** This plan changes go-forward behavior only. A one-time purge of historical unowned records is a separate, `[DESTRUCTIVE]` operation — the daily TTL will age them out within 90 days regardless.
- **Refactoring the whole nested-closure handler for testability.** Extract only the small predicate; do not restructure the 300-line handler.

## Risks

### Risk 1: A subtle carve-out miss silently kills bot E2E (`--await-reply`)
**Impact:** If the predicate omitted the bot check, registered-bot messages would stop being stored, the `--await-reply` awaiter would never settle, and every bot E2E probe would hang/timeout. Silent until someone runs a probe.
**Mitigation:** The predicate's second clause is exactly `find_project_for_bot(sender_id) is not None`. Guarded by `test_bot_await_reply.py` + `test_bot_loop_guard.py` (must pass unchanged) and a dedicated predicate unit test for the bot branch. The build gate runs these before merge.

### Risk 2: Loss of unowned-chat discoverability via `valor-telegram chats`
**Impact:** Gating `register_chat` means unowned chats no longer populate the CLI chat-name registry, so `valor-telegram chats --search <fragment>` won't surface a never-owned chat by name.
**Mitigation:** Acceptable and intended (keeping it ungated reintroduces unbounded growth). `valor-telegram read --chat-id <N>` still resolves via the API fallback for any chat by numeric id. Documented in `telegram-history.md`. Surfaced as an Open Question for the human in case the discoverability loss is unwanted.

### Risk 3: A hidden consumer depends on unowned-chat history being in Redis
**Impact:** Some flow reads unowned-chat history from Redis and silently degrades when it's absent.
**Mitigation:** Recon found only two consumers of recorded history — the bot awaiter (carve-out preserved) and `valor-telegram read` (API fallback preserved). No other silent dependency found. The predicate change is go-forward only and reversible in one commit.

## Race Conditions

No race conditions identified. The change is a synchronous predicate evaluated
inline in the single-threaded `NewMessage` handler before the existing store
call. It adds no shared mutable state, no new async ordering, and no
cross-process coordination. `ACTIVE_PROJECTS` and the resolution maps are
populated at bridge startup (before handlers fire) and read-only thereafter.

## No-Gos (Out of Scope)

- [DESTRUCTIVE] One-time purge of already-stored unowned-chat `TelegramMessage` records. This plan is go-forward only; existing unowned records age out via the 90-day TTL. A bulk delete is irreversible and warrants its own review-before-execute pass.

(Group-matching-by-`chat_id`, `cleanup_expired` rewrite, and handler
refactoring are captured in Rabbit Holes rather than No-Gos — they are
temptations to avoid, not gated deliverables.)

## Update System

No update system changes required. This is a bridge-internal behavioral change
to an existing handler. No new dependencies, config files, config-schema fields,
or Popoto model changes (the `TelegramMessage` schema and its 90-day TTL are
unchanged). Nothing to propagate via `/update` or `scripts/update/`. No Popoto
migration (`scripts/update/migrations.py`) is needed — no field is added,
removed, or re-typed.

## Agent Integration

No agent integration required. This is a bridge-internal change: no new CLI
entry point in `pyproject.toml [project.scripts]`, no new MCP tool or `.mcp.json`
change. The bridge already imports `store_message`, `register_chat`, and
`find_project_for_bot`; the change only adds a local predicate gating an existing
call. The agent-facing surfaces (`valor-telegram read/chats/send`) are unchanged
in signature; `read` continues to fall back to the Telegram API for uncached
chats.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/telegram-history.md` to state the **cache-not-archive** model: Redis holds the owned-chat working set (plus registered-bot history for `--await-reply`); Telegram is the read-through source of truth for everything else. Update the "Full message history — All incoming Telegram messages are stored" line, which is now false, to "All incoming messages **from machine-owned chats** are stored." Note the retention decision (keep 90-day TTL) and its rationale.
- [ ] `docs/features/README.md` index — verify the telegram-history entry description still reads correctly after the edit (update if it claims "all messages").

### Inline Documentation
- [ ] Docstring on `should_store_inbound` explaining the ownership + bot-carve-out rule and citing #1574.
- [ ] Replace the now-stale "Store ALL incoming messages" comment block at `telegram_bridge.py:1219` with one describing the ownership gate.

## Success Criteria

- [ ] The bridge does **not** write a `TelegramMessage` for a chat this machine does not own (verified: an unowned-group inbound produces no new Redis record).
- [ ] Registered-bot messages are still recorded, and `valor-telegram send --await-reply` still settles (`test_bot_await_reply.py` + `test_bot_loop_guard.py` pass unchanged).
- [ ] `valor-telegram read` on an unowned chat still returns messages via the Telethon/API fallback (no regression).
- [ ] Owned-chat storage and the conversation-history prefetch are unchanged.
- [ ] The retention decision (keep 90-day TTL) is recorded in this plan with rationale. ✅ (recorded in Technical Approach)
- [ ] `docs/features/telegram-history.md` states the cache-not-archive model.
- [ ] `should_store_inbound` has direct unit coverage for all four branches (owned / unowned / registered-bot / unregistered-unowned).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (store-gate)**
  - Name: store-gate-builder
  - Role: Add `should_store_inbound` predicate + gate the store block in the `NewMessage` handler; add unit tests.
  - Agent Type: builder
  - Domain: async/bridge, untrusted-input
  - Resume: true

- **Validator (store-gate)**
  - Name: store-gate-validator
  - Role: Verify unowned chats produce no record, owned chats do, and both bot integration tests pass unchanged.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: history-docs
  - Role: Update `telegram-history.md` to the cache-not-archive model.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add the predicate and gate the store block
- **Task ID**: build-store-gate
- **Depends On**: none
- **Validates**: tests/unit/test_should_store_inbound.py (create), tests/integration/test_bot_await_reply.py, tests/integration/test_bot_loop_guard.py
- **Informed By**: recon (find_project_for_bot at routing.py:408; store block at telegram_bridge.py:1229-1258; bot loop-guard at :1272)
- **Assigned To**: store-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Add module-level `should_store_inbound(early_project_key, sender_id) -> bool` to `bridge/telegram_bridge.py` with the ownership + registered-bot-carve-out logic and a docstring citing #1574.
- Wrap the existing `store_message(...)` + `register_chat(...)` block in the `NewMessage` handler with `if should_store_inbound(_early_project_key, sender_id):`. Keep the existing `try/except` and `register_chat` nesting intact.
- Replace the stale "Store ALL incoming messages" comment with an ownership-gate description.
- Add `tests/unit/test_should_store_inbound.py` covering the four branches with a monkeypatched `find_project_for_bot`.

### 2. Validate the gate
- **Task ID**: validate-store-gate
- **Depends On**: build-store-gate
- **Assigned To**: store-gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the new predicate unit tests and both bot integration tests; confirm all pass.
- Confirm (grep) the store block is guard-wrapped and the outbound store at `:2954` is untouched.
- Confirm no changes to `models/telegram.py` or `config/reflections.yaml`.

### 3. Documentation
- **Task ID**: document-cache-not-archive
- **Depends On**: build-store-gate
- **Assigned To**: history-docs
- **Agent Type**: documentarian
- **Parallel**: true
- Update `docs/features/telegram-history.md` to the cache-not-archive model; fix the "all incoming messages are stored" claim; record the retention decision.
- Verify the `docs/features/README.md` index entry.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-store-gate, document-cache-not-archive
- **Assigned To**: store-gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full Verification table; confirm every Success Criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Predicate unit tests pass | `pytest tests/unit/test_should_store_inbound.py -q` | exit code 0 |
| Bot await-reply carve-out intact | `pytest tests/integration/test_bot_await_reply.py -q` | exit code 0 |
| Bot loop-guard intact | `pytest tests/integration/test_bot_loop_guard.py -q` | exit code 0 |
| Predicate exists | `grep -n 'def should_store_inbound' bridge/telegram_bridge.py` | exit code 0 |
| Store block is guarded | `grep -n 'should_store_inbound(_early_project_key' bridge/telegram_bridge.py` | exit code 0 |
| `stored_msg_id` init stays outside guard | `awk '/stored_msg_id = None/{i=NR} /if should_store_inbound/{g=NR} END{exit !(i>0 && g>0 && i<g)}' bridge/telegram_bridge.py` | exit code 0 (init line precedes guard line) |
| Stale "Store ALL incoming" comment removed | `grep -c 'Store ALL incoming messages' bridge/telegram_bridge.py` | match count == 0 |
| TTL/cleanup untouched | `git diff --name-only origin/main -- models/telegram.py config/reflections.yaml` | match count == 0 |
| Docs state cache-not-archive | `grep -in 'cache' docs/features/telegram-history.md` | exit code 0 |
| Lint clean | `python -m ruff check bridge/telegram_bridge.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/telegram_bridge.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Decisions (locked at finalization)

Both former Open Questions were non-blocking judgment calls with recorded defaults; the
critique raised no objection to either, so they are locked in for BUILD:

1. **Retention — keep the 90-day TTL.** Gating storage removes the unowned-chat volume that made the daily sweep slow; 90 days of owned-chat scrollback is a sensible warm working set. Shortening later is a one-line, reversible default change backed by the Telegram-API read-through. No code change in this plan.
2. **`register_chat` gated together with `store_message`.** They are the paired write; gating both keeps `Chat` record growth bounded by ownership (the same anti-pattern otherwise reappears). Consequence — unowned chats drop from the `valor-telegram chats` name registry — is accepted and documented in Risks (`valor-telegram read --chat-id <N>` still resolves any chat via the API fallback).
