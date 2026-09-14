---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-06-05
tracking: https://github.com/tomcounsell/ai/issues/1574
last_comment_id:
---

# Bot End-to-End Testing via valor-telegram

## Problem

We build, deploy, and update bots (e.g. Hermes-agent bots like `@cyndra_staff_bot` "Bruce", Telegram user id `8837490628`). To verify them we want to drive them over the *real* Telegram surface from the `valor-telegram` CLI: send a probe message and synchronously pull the bot's **settled** reply back into the caller's context to assert on it — the bot-testing equivalent of `curl`.

**Current behavior:**
- `valor-telegram send` fire-and-forgets into the Redis relay; there is no way to send-and-wait-for-the-reply in one invocation. A tester must send, then separately poll `valor-telegram read` and eyeball the result.
- Getting the *right, finished* reply is hard: a Hermes bot streams its answer via repeated in-place edits and interleaves `⏳`/tool-bubble status chatter (see Data Flow). A naive "first reply" capture grabs a half-token partial or a spinner.
- Sending to a bot only works *by accident*: the bot isn't in `projects.json`, so the bridge resolves it to no project and never responds. If a bot were registered the normal way (`dms.whitelist`), its replies (which carry **no `reply_to`**) would each look like a cold inbound message → spawn a new AgentSession → auto-continue answers it → the bot replies again → **infinite session loop**.

**Desired outcome:**
- `valor-telegram send --await-reply [--timeout N]` sends to a registered bot and returns the bot's settled final answer (including the glued `⚠️` footer when present) on stdout, with `--json` for structured assertions.
- Inbound messages from a **registered bot peer** are recorded to history but **deterministically never spawn a session**, enforced at config-validation time — turning today's accidental safety into a designed guarantee.

## Freshness Check

**Baseline commit:** `af8da3bca`
**Issue filed at:** 2026-06-05T08:29:24Z
**Disposition:** Unchanged

Issue filed the same day, hours before planning; no intervening commits touch the bridge dispatch path, `tools/valor_telegram.py`, `tools/telegram_history/`, or `bridge/config_validation.py`. All file:line references were re-verified live during the Phase-1.5 spikes (see Spike Results) and hold at HEAD `af8da3bca`. No overlapping active plans in `docs/plans/` (checked: granite-tui, sdlc_1535, granite_root_session_runner — unrelated).

## Prior Art

- **PR #1200**: `fix(#1191): default valor-telegram send --reply-to to $TELEGRAM_REPLY_TO` — only `--reply-to` defaulting; tangential, confirms the send CLI surface but no await/poll logic.
- **Issue #1318**: introduced `classify_conversation_terminus(sender_is_bot=...)` — the directly-related existing bot loop-break. **Group-path only, reply-to-Valor only, heuristic** (a bot reply containing a question is NOT silenced). Does not cover the DM E2E path. This plan supersedes it *for registered bots* with a deterministic guard; the heuristic remains for unregistered bots in groups.
- No related closed issues for synchronous bot probing / `--await-reply`. Greenfield otherwise.

## Research

No external WebSearch performed — the only external dependency (hermes-agent) was already source-verified at v0.14.0 (delivery mechanics captured in the issue and in session memory `reference_hermes_bot_telegram_delivery`), and the rest is internal bridge/CLI architecture. Telethon `MessageEdited` event semantics were verified by direct code-read of the live handler rather than docs.

## Spike Results

### spike-1: Where the awaiter polls (history store)
- **Assumption**: "`valor-telegram read` and the bridge share a Redis-backed store the awaiter can poll by chat_id, and records carry message_id."
- **Method**: code-read
- **Finding**: Confirmed. Popoto model `TelegramMessage` (`models/telegram.py:12-71`): `chat_id` (KeyField), `message_id` (KeyField), `direction` ("in"/"out"), `sender`, `content` (**non-KeyField → updatable via `.save()`**), `timestamp` (SortedField, `partition_by="chat_id"`), `reply_to_msg_id`. Read path: `cmd_read` → `get_recent_messages()` (`tools/telegram_history/__init__.py:495-541`) → `TelegramMessage.query.filter(chat_id=...)`. Write path: bridge `store_message(... message_id=message.id ...)` at `telegram_bridge.py:1172-1186`. **No `update_message_text`/upsert helper exists** — must be added.
- **Confidence**: high
- **Impact on plan**: Awaiter polls `TelegramMessage.query.filter(chat_id=str(bot_id))` for `direction="in"`, `timestamp > send_ts`. A new `update_message_text(chat_id, message_id, new_text)` helper is required so streamed edits update the same record (not append duplicates).

### spike-2: Can the bridge capture a peer bot's streamed edits?
- **Assumption**: "Telethon `MessageEdited` fires for inbound bot edits and the bridge can record them."
- **Method**: code-read
- **Finding**: `edit_handler` (`telegram_bridge.py:2409-2550`), registered `@client.on(events.MessageEdited)` with **no `chats=`/`from_users=` filter**. Guards: `if event.out or SHUTTING_DOWN: return` (2418) — inbound (`event.out=False`) passes; then `if not project: return` (2447) — **discards edits from peers that don't resolve to a project**; then requires an existing session (2461) else returns (2469). It never persists edited text. For NEW messages, an unowned peer is stored (`project_key=None`, `telegram_bridge.py:1160`) but **no session is spawned** (hard guard `if not project: return` at ~line 1360); bot senders are also excluded from the memory write (`not getattr(sender, "bot", False)`).
- **Confidence**: high
- **Impact on plan**: The edit_handler must gain a registered-bot branch *before* the `if not project` discard — resolve via a new `find_project_for_bot(sender_id)`, upsert the edited body to history, and return (no session). New-message safety is already real but must be made explicit + enforced (see spike-3) so registration doesn't reintroduce spawning.

### spike-3: Registry home + validation
- **Assumption**: "A bot registry can live in `projects.json` and reuse DM-style resolution without tripping single-machine-ownership validation."
- **Method**: code-read
- **Finding**: Config loads via `load_config()` (`bridge/routing.py:75-133`) from `~/Desktop/Valor/projects.json` (fallback `config/projects.json`, override `PROJECTS_CONFIG_PATH`). `find_project_for_dm(sender_id)` → `DM_USER_TO_PROJECT.get(sender_id)`, built at startup (`telegram_bridge.py:602-611`) from `dms.whitelist[]`. `validate_projects_config()` (`config_validation.py:207-222`) enforces single-machine ownership over groups / dm whitelist / email via `validate_dm_whitelist`, `validate_telegram_groups`, `validate_email_routing`. Nested per-entry config has precedent (`telegram.groups`, `email.contacts`). Test fixture: `tests/conftest.py` `sample_config` (~393-457); example at `config/projects.example.json`.
- **Confidence**: high
- **Impact on plan**: Add a **separate** `telegram.bots[]` registry per project (NOT `dms.whitelist` — that would re-enable session-spawn). Build `BOT_ID_TO_PROJECT` at startup; add `find_project_for_bot(bot_id)`. Add `validate_telegram_bots()` enforcing (a) single-machine ownership of each bot id and (b) **mutual exclusion**: a bot id must not also appear in `dms.whitelist[].id` or as a group `chat_id`. (b) is the config-time enforcement of the loop-guard invariant.

## Data Flow

The send→settle→assert path end-to-end:

1. **Entry point**: `valor-telegram send --chat <bot-id> --await-reply --timeout 600 "probe"` → `cmd_send` (`tools/valor_telegram.py:763`). Numeric `--chat` is treated as raw id (existing fallback). Message is queued to `telegram:outbox:{cli-…}` (Redis), `message_id` of the sent message is recorded by the relay path.
2. **Relay → bridge**: `bridge/telegram_relay.py` `int(chat_id)` → `client.send_message(bot_id, text)`. The bot receives the DM.
3. **Bot (Hermes)**: streams its answer via repeated `edit_message_text` on a stable `message_id` (`gateway/stream_consumer.py:420-461`); emits separate `⏳`/tool-bubble status messages; glues a trailing `⚠️` footer **inside** the final answer message. No on-wire done-marker.
4. **Bridge capture**: inbound NewMessage from the bot → stored to `TelegramMessage` (`direction="in"`), **no session** (registered-bot guard). Each subsequent `MessageEdited` from the bot → `update_message_text()` upserts the latest body onto the same `message_id` record.
5. **Awaiter (in the CLI process)**: polls `TelegramMessage.query.filter(chat_id=str(bot_id))` every ~1–2s for `direction="in"` records with `timestamp > send_ts`; tracks per-`message_id` content; **resets a quiet timer on any new message OR content change**; settles when the quiet window (settle-profile, ~4–6s) elapses with no change, bounded by `--timeout`.
6. **Output**: prints the settled prose answer (status/tool lines filtered out of the *display*, never used to decide settle); `--json` emits the structured transcript (see Technical Approach).

## Architectural Impact

- **New dependencies**: none (no new libs; reuses Popoto, Telethon, Redis).
- **Interface changes**: new CLI flags `--await-reply`, `--timeout`, `--json` on `valor-telegram send`; new `telegram.bots[]` config schema; new public functions `find_project_for_bot`, `update_message_text`, an awaiter module.
- **Coupling**: adds a read-only coupling between the CLI awaiter and the `TelegramMessage` store (already the documented IPC surface between bridge and CLI). No new coupling to Telethon from the CLI (the awaiter must NOT open a second client — bridge holds the session lock).
- **Data ownership**: bridge remains sole writer of inbound history; awaiter is a pure reader. New `update_message_text` makes the bridge also the upserter of edited bodies.
- **Reversibility**: high. Registry is additive config; CLI flags are opt-in; the edit-capture branch is guarded behind `find_project_for_bot`. Removing the feature = delete the flags + the bots block; no migration.

## Appetite

**Size:** Large

**Team:** Solo dev + PM (orchestrated builders/validators), 1 review round.

**Interactions:**
- PM check-ins: 1-2 (registry schema sign-off; settle-window defaults)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Bridge running (for live integration test) | `./scripts/valor-service.sh status \| grep -q RUNNING` | The awaiter polls history the bridge writes; live E2E needs it up |
| A registered test bot id reachable | `python -c "import os; assert os.path.exists(os.path.expanduser('~/Desktop/Valor/projects.json'))"` | Config home for the `telegram.bots[]` registry |

Run all checks: `python scripts/check_prerequisites.py docs/plans/bot_e2e_testing_via_valor_telegram.md`

## Solution

### Key Elements

- **Bot registry** (`projects.<key>.telegram.bots[]`): explicit, auditable list of bots under test — `{id, username, name, under_test, settle_profile}`. Validated against the live Telegram `User.bot` flag at registration time; never placed in `dms.whitelist`.
- **`find_project_for_bot(bot_id)`** + `BOT_ID_TO_PROJECT` startup map (mirrors `find_project_for_dm`).
- **Deterministic loop-guard**: registered bot ids never spawn a session (enforced by config-time mutual-exclusion validation + an explicit dispatch guard), in both DM and group paths.
- **Edit-aware history capture**: bridge `edit_handler` upserts a registered bot's streamed edits via a new `update_message_text()` helper.
- **Synchronous awaiter**: `valor-telegram send --await-reply` polls `TelegramMessage`, settles on silence (edit-aware, two timers), returns the settled answer; `--json` for assertions.

### Flow

CLI `send --await-reply` → message queued to relay → bridge delivers to bot → bot streams answer (edits) + status → bridge records/​upserts to `TelegramMessage` (no session) → awaiter polls store, resets quiet timer on each edit → quiet window elapses → settled answer printed (with footer) → exit 0 (or exit non-zero on `--timeout` with partial transcript).

### Technical Approach

- **Registry schema** (additive, per spike-3):
  ```json
  "telegram": {
    "groups": { ... },
    "bots": [
      {
        "id": 8837490628,
        "username": "cyndra_staff_bot",
        "name": "Bruce @ Internal Staff",
        "under_test": true,
        "settle_profile": {
          "cleanup_progress": false,
          "quiet_window_seconds": 5,
          "default_timeout_seconds": 600,
          "status_patterns": ["^⏳", "^(💻|🔎|🔧|📖|⚙️|📝) \\w+:"]
        }
      }
    ]
  }
  ```
- **Loop-guard, deterministic + enforced**:
  - `validate_telegram_bots(config)` in `config_validation.py`: single-machine ownership of each bot id; **mutual exclusion** with `dms.whitelist[].id` and group `chat_id`s. Wired into `validate_projects_config` so a bad config blocks the bridge restart (same gate as today's ownership rules).
  - Dispatch guard: early in the inbound NewMessage flow and in `should_respond_sync`, `if find_project_for_bot(sender_id): record-only, never respond`. Belt-and-suspenders over the existing "unowned → no session" behavior, and it also covers a bot replying inside a monitored group (group path).
- **Edit capture**: in `edit_handler`, *before* the `if not project: return` at 2447, add: `bot_proj = find_project_for_bot(sender_id); if bot_proj: update_message_text(chat_id, event.message.id, edited_text); return`. New `update_message_text(chat_id, message_id, new_text) -> bool` in `tools/telegram_history/__init__.py` finds the record by `(chat_id, message_id)` and `.save()`s the new `content` (content is non-KeyField).
- **Awaiter** (`tools/valor_telegram_await.py` or inline in `cmd_send`):
  - Inputs: `bot_id`, `send_ts`, `settle_profile` (from registry; CLI flags override `--timeout`).
  - Loop: every `poll_interval` (1–2s), fetch `direction="in"` records for `chat_id` with `timestamp >= send_ts`; maintain `{message_id: content}`; on any new id or changed content, reset `last_change_ts`.
  - **Two timers**: settle when `now - last_change_ts >= quiet_window` (we have ≥1 captured message); hard stop at `--timeout` → `timed_out=true`, return whatever was captured.
  - **Silence decides settle; patterns only clean the display.** Filter `status_patterns` out of the printed prose; never use them to decide return. Do NOT drop a leading `⚠️` at message level (footer-only terse answers are valid).
  - Output: settled prose to stdout; `--json` schema:
    ```json
    {
      "target": {"id": 8837490628, "username": "cyndra_staff_bot"},
      "sent": {"text": "...", "message_id": 123, "ts": 1733383764.0},
      "reply": {"settled_text": "...", "footer_present": true,
                "message_ids": [124,125], "edit_count": 9,
                "started_ts": ..., "settled_ts": ...},
      "transcript": [{"message_id":124,"kind":"answer|status|tool","text":"...","ts":...}],
      "settled": true, "timed_out": false, "elapsed_s": 41.2
    }
    ```
- **`User.bot` validation at registration**: a `valor-telegram bots verify` subcommand (or a one-shot in the awaiter's first run) confirms each registry `id`'s `is_bot` via the bridge — but must NOT open a second Telethon client. Resolve via the Bot API `getChat` using the bot's own token *if available*, else a manual `under_test` assertion. (Open Question 3.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `update_message_text` must not raise on a missing record — return `False` and `logger.debug`; test asserts the debug path and that no duplicate row is created.
- [ ] The awaiter's poll loop wraps store reads; a transient Popoto/Redis error must `logger.warning` and retry, not abort the await — test injects a failing query and asserts retry + eventual settle.
- [ ] The edit_handler registered-bot branch must swallow upsert errors (fail-open, never crash the bridge) — assert `logger.warning` on a forced failure.

### Empty/Invalid Input Handling
- [ ] `--await-reply` against an **unregistered** id: refuse with a clear stderr error ("id N is not a registered bot; add it to telegram.bots") and non-zero exit — do not silently poll forever.
- [ ] Bot sends **no reply** within `--timeout`: return `settled=false, timed_out=true` with empty `settled_text`, exit non-zero. Assert the error renders to stderr, not swallowed.
- [ ] Bot sends **only** status bubbles (never a prose answer): settle on quiet window, `settled_text=""`, transcript non-empty — assert the tester can see "only status, no answer."
- [ ] Whitespace-only / empty probe text already rejected by `cmd_send`; keep that guard.

### Error State Rendering
- [ ] On `--timeout`, print the partial transcript + an explicit "TIMED OUT after Ns" line to stderr; `--json` still emits valid JSON with `timed_out:true`.
- [ ] The glued `⚠️` footer is preserved in `settled_text` and surfaced as `footer_present:true` — test asserts it is NOT stripped.

## Test Impact

- [ ] `tests/unit/test_config_driven_routing.py` — UPDATE: add `find_project_for_bot` resolution cases (hit / miss) alongside existing DM routing tests.
- [ ] `tests/unit/test_dm_whitelist_validation.py` — UPDATE: add `validate_telegram_bots` cases — single-machine ownership of bot ids, and mutual-exclusion failures (bot id also in dm whitelist / as group chat_id).
- [ ] `tests/conftest.py::sample_config` — UPDATE: add a `telegram.bots[]` entry so routing/validation fixtures exercise the new schema.
- [ ] `config/projects.example.json` — UPDATE: add a commented `bots` example.
- [ ] No existing `valor-telegram send` test asserts await behavior (greenfield); new tests added under `tests/unit/test_valor_telegram_await.py` and an integration test (below). No DELETE/REPLACE of existing send tests — changes are additive to `cmd_send`.

## Rabbit Holes

- **Forking hermes-agent to emit an on-wire done-marker.** Terminal flags are internal by design; patching a pinned OSS dep couples our tester to a fork. Settle by silence instead. (Dropped in recon.)
- **Generic "wait for any chat to go quiet" framework.** Scope to *registered bots* only; do not build a universal Telegram request/response layer.
- **Parsing/So-classifying every Hermes status emoji.** Patterns only clean the *display*; do not build a status taxonomy or depend on `cleanup_progress` deletions (OFF on staff Mac).
- **A second Telethon client in the CLI** to read live or resolve usernames. The bridge owns the SQLite session lock; the awaiter is a pure history reader. Username→id resolution is unnecessary (bot id is the token prefix / stored in the registry).
- **Multi-turn conversational testing.** First cut is single-shot probe. (See No-Gos.)

## Risks

### Risk 1: Registering a bot re-enables session-spawn (the original loop)
**Impact:** If a bot id leaks into `dms.whitelist` or matches a monitored group, its no-`reply_to` replies spawn runaway sessions.
**Mitigation:** `validate_telegram_bots` mutual-exclusion check fails the config at validation time (blocks bridge restart, same gate as ownership today); plus the explicit `find_project_for_bot` dispatch guard. Two independent layers.

### Risk 2: Premature settle on a mid-stream partial
**Impact:** The awaiter returns a half-token answer because it only watched for new messages, not edits.
**Mitigation:** Edit-aware capture (`update_message_text` on every `MessageEdited`) + quiet timer reset on content change. Integration test streams ≥2 edits and asserts the final body, not a partial.

### Risk 3: Short timeout kills a legitimately slow turn
**Impact:** Hermes turns run minutes (observed 12+ min); a single short timer cuts off mid-think.
**Mitigation:** Two-timer design — short quiet window for "stopped streaming," generous `--timeout` (default 600s, from settle_profile) for total wait. Documented and defaulted in the registry.

### Risk 4: Awaiter and bridge race on the same record
**Impact:** Awaiter reads a record between the bridge's insert and a near-simultaneous edit upsert.
**Mitigation:** Reads are snapshot-consistent per poll; a partial read just resets the quiet timer on the next poll and re-settles. Idempotent by construction (see Race 1).

## Race Conditions

### Race 1: Edit upsert vs. awaiter poll
**Location:** `tools/telegram_history` `update_message_text` (writer, in bridge) vs. awaiter poll loop (reader, in CLI).
**Trigger:** Bridge upserts an edited body while the awaiter is mid-poll.
**Data prerequisite:** The inbound record must exist (bridge `store_message` on first NewMessage) before any edit upsert targets it.
**State prerequisite:** `message_id` is stable across edits (Telegram guarantees; verified in spike-1).
**Mitigation:** Awaiter is poll-based and idempotent — it re-reads the latest content each interval and only settles after a full quiet window with no change. A racing edit simply defers settle by one window; it cannot produce a stale "settled" result because the next poll observes the change and resets the timer.

### Race 2: First edit arrives before the insert is visible
**Location:** bridge NewMessage `store_message` (1172) vs. `edit_handler` upsert.
**Trigger:** An edit event is processed before the insert commits (unlikely; Telegram sends the message before editing it).
**Data prerequisite:** Insert visible before upsert.
**Mitigation:** `update_message_text` returns `False` (record not found) and `logger.debug`s rather than creating a phantom row; the next edit (Hermes edits repeatedly) upserts successfully. Net effect: at most one early edit dropped, immaterial to the settled final body.

## No-Gos (Out of Scope)

- [EXTERNAL] Flipping `cleanup_progress: true` on a real deployed bot's `~/.hermes/config.yaml` to get a cleaner transcript — that's an operator change on the bot's machine, not this repo. The awaiter must work with it OFF (the prod-accurate surface).
- [EXTERNAL] Live `User.bot` verification that requires the bot's own token — token provisioning is a human/world action; the registry stores `under_test` and the verify subcommand is best-effort.

## Update System

- `config/projects.example.json` gains a `telegram.bots[]` example; operators add real entries to their `~/Desktop/Valor/projects.json` (iCloud-synced) — no script change needed for the data itself.
- `bridge/config_validation.py` gains `validate_telegram_bots`, which is already invoked through `validate_projects_config` and gated by `scripts/update/run.py` Step 4.6 — so a malformed `bots` block blocks the bridge restart automatically. No new update-script wiring beyond shipping the validator.
- No new dependencies, services, or secrets. No migration: the `bots` block is optional and absent configs behave exactly as today.

## Agent Integration

- The capability is a **CLI surface** (`valor-telegram send --await-reply`), invoked via the agent's Bash tool — the agent already runs `valor-telegram`. No new MCP server or `.mcp.json` change required.
- The bridge itself must import/call the new `find_project_for_bot` (dispatch guard) and `update_message_text` (edit_handler). These are bridge-internal wirings, covered by unit + integration tests.
- Integration test verifies the agent path: invoke `valor-telegram send --chat <test-bot-id> --await-reply --json` against a live registered bot and assert a settled reply is returned. (Gated on bridge running.)
- No change to `pyproject.toml [project.scripts]` — `valor-telegram` already exists; we add flags, not a new entry point.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/bot-e2e-testing.md`: the registry schema, `--await-reply`/`--timeout`/`--json` usage, settle semantics (two timers, edit-aware, footer-as-signal), and the loop-guard invariant.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `CLAUDE.md` `valor-telegram` quick-reference rows to include `--await-reply`.
- [ ] Cross-link `docs/features/telegram-messaging.md` (existing) and `docs/features/single-machine-ownership.md` (the bots mutual-exclusion rule).

### External Documentation Site
- [ ] N/A — repo has no external docs site for this surface.

### Inline Documentation
- [ ] Docstrings on `find_project_for_bot`, `update_message_text`, and the awaiter module (settle algorithm, why silence decides and patterns only clean).
- [ ] Comment the edit_handler registered-bot branch explaining the loop-guard rationale.

## Success Criteria

- [ ] `valor-telegram send --await-reply` against a registered bot returns the settled final answer (incl. glued `⚠️` footer), not a partial or a `⏳` status line.
- [ ] Separate quiet-window and `--timeout` controls exist; a simulated multi-minute turn is not cut off by the quiet window.
- [ ] A registered bot's inbound messages never spawn an AgentSession (DM and group) — proven by a test that loops under today's behavior and passes under the guard.
- [ ] `validate_telegram_bots` fails a config where a bot id also appears in `dms.whitelist` or a group `chat_id`.
- [ ] Streamed edits are captured and reset the settle timer (no premature settle on the first partial).
- [ ] `--json` emits a structured transcript usable in nightly/E2E assertions.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms bridge `edit_handler` references `find_project_for_bot` and `update_message_text`.

## Team Orchestration

The lead agent orchestrates; builders + validators pair per component.

### Team Members

- **Builder (registry+routing)**
  - Name: `registry-builder`
  - Role: `telegram.bots[]` schema, `find_project_for_bot`/`BOT_ID_TO_PROJECT`, `validate_telegram_bots`, fixtures/example config
  - Agent Type: builder
  - Resume: true
- **Builder (bridge-capture)**
  - Name: `capture-builder`
  - Role: `update_message_text` helper + edit_handler registered-bot branch + dispatch loop-guard
  - Agent Type: builder
  - Resume: true
- **Builder (awaiter-cli)**
  - Name: `awaiter-builder`
  - Role: `--await-reply`/`--timeout`/`--json` + settle algorithm + awaiter module
  - Agent Type: builder
  - Resume: true
- **Validator (loop-guard)**
  - Name: `guard-validator`
  - Role: verify no-session-spawn invariant + validation mutual-exclusion
  - Agent Type: validator
  - Resume: true
- **Validator (settle)**
  - Name: `settle-validator`
  - Role: verify edit-aware two-timer settle + footer preservation
  - Agent Type: validator
  - Resume: true
- **Documentarian**
  - Name: `docs-writer`
  - Role: feature doc + index + CLAUDE.md rows
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types
Using Tier-1 builder/validator/documentarian; optionally `async-specialist` for the poll-loop/settle timing if the settle tests prove flaky.

## Step by Step Tasks

### 1. Registry + routing + validation
- **Task ID**: build-registry
- **Depends On**: none
- **Validates**: tests/unit/test_config_driven_routing.py, tests/unit/test_dm_whitelist_validation.py, tests/conftest.py
- **Informed By**: spike-3 (bots[] under telegram, separate from dms.whitelist; validate ownership + mutual-exclusion)
- **Assigned To**: registry-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `telegram.bots[]` schema handling; build `BOT_ID_TO_PROJECT` at startup; add `find_project_for_bot(bot_id)` in `bridge/routing.py`.
- Add `validate_telegram_bots(config)` to `bridge/config_validation.py`; wire into `validate_projects_config`; enforce single-machine ownership + mutual exclusion with dm whitelist ids and group chat_ids.
- Update `config/projects.example.json` and `tests/conftest.py::sample_config`.

### 2. Bridge capture + loop-guard
- **Task ID**: build-capture
- **Depends On**: build-registry
- **Validates**: tests/unit/test_telegram_history_update.py (create), tests/integration/test_bot_loop_guard.py (create)
- **Informed By**: spike-1 (content is updatable; add update_message_text), spike-2 (edit_handler discards at `if not project`; hook before it)
- **Assigned To**: capture-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `update_message_text(chat_id, message_id, new_text) -> bool` in `tools/telegram_history/__init__.py` (find by chat_id+message_id, `.save()` content, fail-open).
- In `edit_handler` (`telegram_bridge.py:~2447`), add the registered-bot branch (upsert + return) before the `if not project` discard.
- Add the explicit dispatch loop-guard (`find_project_for_bot` → record-only) in the NewMessage path and `should_respond_sync`.

### 3. Awaiter CLI
- **Task ID**: build-awaiter
- **Depends On**: build-registry
- **Validates**: tests/unit/test_valor_telegram_await.py (create)
- **Informed By**: spike-1 (poll TelegramMessage by chat_id/direction/timestamp)
- **Assigned To**: awaiter-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--await-reply`, `--timeout`, `--json` to `cmd_send`; implement the edit-aware two-timer settle loop reading the registry settle_profile.
- Implement display filtering (status_patterns clean output only) and `--json` transcript schema; preserve the `⚠️` footer.
- Refuse `--await-reply` on an unregistered id (clear stderr, non-zero exit).

### 4. Validate loop-guard
- **Task ID**: validate-guard
- **Depends On**: build-capture
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Prove a registered bot's inbound (DM + group) never spawns a session; prove `validate_telegram_bots` rejects mutual-exclusion violations.

### 5. Validate settle
- **Task ID**: validate-settle
- **Depends On**: build-awaiter, build-capture
- **Assigned To**: settle-validator
- **Agent Type**: validator
- **Parallel**: false
- Prove edit-aware settle (no premature return on partial), two-timer behavior, footer preservation, timeout rendering.

### 6. Live integration (bridge up)
- **Task ID**: build-integration
- **Depends On**: build-capture, build-awaiter
- **Validates**: tests/integration/test_bot_await_reply.py (create)
- **Assigned To**: awaiter-builder
- **Agent Type**: builder
- **Parallel**: false
- Register the staff test bot; send `--await-reply --json`; assert a settled reply returns and no session was spawned. Skip-marker if bridge not running.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-registry, build-capture, build-awaiter
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/bot-e2e-testing.md`; add index entry; update `CLAUDE.md` quick-reference rows; cross-link telegram-messaging + single-machine-ownership docs.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-guard, validate-settle, build-integration, document-feature
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands; confirm all success criteria incl. docs and grep checks.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| find_project_for_bot exists | `grep -rn "def find_project_for_bot" bridge/routing.py` | output contains find_project_for_bot |
| update_message_text exists | `grep -rn "def update_message_text" tools/telegram_history/__init__.py` | output contains update_message_text |
| edit_handler wires capture | `grep -n "find_project_for_bot\|update_message_text" bridge/telegram_bridge.py` | output > 0 |
| validation wired | `grep -n "validate_telegram_bots" bridge/config_validation.py` | output > 0 |
| await flag present | `valor-telegram send --help 2>&1 \| grep -- --await-reply` | output contains --await-reply |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Settle defaults**: quiet window `5s` and default `--timeout 600s` — acceptable starting defaults, or do you want the window tighter (e.g. 4s) / timeout longer for the slowest observed turns (12+ min → 900s)?
2. **Group-path scope for v1**: the deterministic guard covers a bot replying in a monitored group via `find_project_for_bot`. Is in-group bot testing in scope now, or DM-only for the first cut (group guard still lands as a safety net, but no in-group awaiter)?
3. **`User.bot` verification mechanism**: validate each registry id against the live `User.bot` flag *how* — via the bot's own token through Bot API `getChat` (requires the token, which we have for the staff bot), or a lighter "asserted `under_test`" with verification only when a token is supplied? The awaiter itself must not open a second Telethon client.
4. **Awaiter location**: inline in `cmd_send` vs. a dedicated `tools/valor_telegram_await.py` module imported by the CLI (cleaner for unit testing the settle algorithm in isolation). Preference?
