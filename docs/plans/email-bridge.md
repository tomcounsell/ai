---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/847
last_comment_id:
---

# Email Bridge: Secondary Inbox/Outbox Transport

## Problem

The system currently speaks only Telegram. Contacts who prefer email, or contexts where Telegram is not appropriate (client relationships, async communication), have no path to reach the agent. The architecture is already transport-agnostic at the worker level — `OutputHandler` protocol, `register_callbacks()`, and `enqueue_agent_session()` are all transport-neutral in design. What is missing is a second bridge that implements the inbox/outbox pattern for email and a config extension that maps email addresses to projects.

**Current behavior:** Every inbound message must arrive via Telegram. The `projects.json` config maps Telegram group names and chat IDs to projects. DMs are classified via `event.is_private` (a Telegram API property). Session IDs are prefixed `tg_`. The `initial_telegram_message` DictField and `telegram_message_key` Field on `AgentSession` have Telegram-specific names but store generic data.

**Desired outcome:** An email bridge listens for inbound messages (IMAP poll), enqueues an `AgentSession`, and registers an SMTP `OutputHandler`. The same contact can be reachable via Telegram or email. The `projects.json` config gains email address mappings per contact. Session IDs for email use an `email_` prefix.

## Freshness Check

**Baseline commit:** `29c5507a`
**Issue filed at:** 2026-04-09T06:46:52Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/output_handler.py` — `OutputHandler` protocol at lines 26-64, `TelegramRelayOutputHandler` at lines 156-272 — still holds, structure matches issue description
- `agent/agent_session_queue.py` — `register_callbacks()` at line 1806, keyed by `project_key` only — still holds
- `agent/agent_session_queue.py` — `enqueue_agent_session()` at line 1893 — still holds, signature matches issue
- `models/agent_session.py` — `initial_telegram_message` DictField at line 145, `telegram_message_key` at line 156 — still holds
- `bridge/routing.py` — `find_project_for_chat()` at line 159, `load_config()` at line 66 — still holds
- `bridge/telegram_bridge.py` — session_id construction `f"tg_{project_key}_{event.chat_id}_{message.id}"` at line 1004 — still holds
- `worker/__main__.py` — registers `TelegramRelayOutputHandler` per project at line 152-196 — still holds

**Cited sibling issues/PRs re-checked:**
- #731 (Extract standalone worker service) — closed 2026-04-06, merged. Relevant: established the bridge/worker separation that this plan extends.

**Commits on main since issue was filed (touching referenced files):**
- `570a0763` "Add PM session resume hydration context" — touches `agent_session_queue.py`, irrelevant to email bridge
- `4c03a851` "Add RECOVERY_OWNERSHIP registry" — touches `agent_session_queue.py`, irrelevant
- `136d51e3` "Add TelegramRelayOutputHandler for worker Telegram delivery" — adds the relay handler pattern we will mirror for email. Confirms the OutputHandler pattern is the canonical approach.
- `0e3d8cdf` "Add COLLABORATION and OTHER classifier buckets" — touches `bridge/routing.py`, irrelevant (adds classification types, doesn't change project resolution)

**Active plans in `docs/plans/` overlapping this area:** None. No plans touch the bridge transport layer or OutputHandler registration.

**Notes:** The `TelegramRelayOutputHandler` (commit `136d51e3`) landed after the issue was filed and validates the architectural approach — the email bridge follows the exact same pattern (implement `OutputHandler`, register via `register_callbacks`). No drift that changes the plan's premise.

## Prior Art

- **Issue #731**: "Extract standalone worker service from bridge monolith" — Closed. Established the bridge/worker separation that makes this multi-transport approach viable. The worker is fully transport-agnostic; only the bridge layer is Telegram-specific.
- **Issue #395**: "Multi-persona system: PM as communication layer" — Closed. Established the PM/Dev/Teammate session type system. Email sessions will use the same session types and persona resolution.

No prior issues found related to email bridge specifically. This is greenfield work building on the established transport abstraction.

## Data Flow

1. **Entry point**: Inbound email arrives at configured IMAP mailbox
2. **Email bridge inbox** (`bridge/email_bridge.py`): IMAP poll loop fetches unread messages, parses sender/subject/body, extracts `In-Reply-To` header for thread continuation
3. **Contact resolution** (`bridge/routing.py:find_project_for_email()`): Looks up sender email in `projects.json` contacts config, resolves to project + persona
4. **Session creation** (`agent/agent_session_queue.py:enqueue_agent_session()`): Creates `AgentSession` with `session_id=email_{project_key}_{sender}_{timestamp}`, `transport="email"` in extra_context
5. **Worker execution** (`worker/__main__.py`): Pops session, runs agent via SDK — fully transport-agnostic, no changes needed
6. **Output routing** (`agent/agent_session_queue.py`): Resolves `EmailOutputHandler` via transport-keyed callback lookup `(project_key, "email")`
7. **Email bridge outbox** (`EmailOutputHandler.send()`): Composes SMTP reply with `In-Reply-To` header referencing original email, sends via configured SMTP server
8. **Delivery**: Email arrives in sender's inbox as a reply to their original message

## Architectural Impact

- **New dependencies**: `imaplib` (stdlib), `smtplib` (stdlib), `email` (stdlib) — no third-party packages required for basic IMAP/SMTP
- **Interface changes**: `register_callbacks()` gains optional `transport` parameter; `_send_callbacks` dict key changes from `str` to `tuple[str, str]` with backward-compatible fallback. `find_project_for_email()` added to `bridge/routing.py`.
- **Coupling**: Decreases coupling — email bridge is a peer to telegram bridge, both depend on the same `OutputHandler` protocol and `enqueue_agent_session()` entry point. No cross-bridge dependencies.
- **Data ownership**: `AgentSession` gains `transport` metadata (stored in `extra_context`). Email origin data stored in `initial_telegram_message` (field rename to `initial_message` is out of scope — backward-compat alias already planned in issue #847's "concurrent" section but deferred).
- **Reversibility**: High. Email bridge is a new standalone module. Removing it means deleting `bridge/email_bridge.py`, removing email config from `projects.json`, and reverting the transport-keyed callback change (which is backward-compatible).

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (IMAP/SMTP config decisions, contact mapping format, thread continuation strategy)
- Review rounds: 2+ (new bridge module, config schema change, callback registration change)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| SMTP credentials | `python -c "from dotenv import dotenv_values; e=dotenv_values('.env'); assert e.get('SMTP_HOST') and e.get('SMTP_USER')"` | Outbound email delivery |
| IMAP credentials | `python -c "from dotenv import dotenv_values; e=dotenv_values('.env'); assert e.get('IMAP_HOST') and e.get('IMAP_USER')"` | Inbound email polling |
| Redis | `python -c "import redis; redis.Redis().ping()"` | Session queue |

Run all checks: `python scripts/check_prerequisites.py docs/plans/email-bridge.md`

## Solution

### Key Elements

- **Email inbox poller** (`bridge/email_bridge.py`): IMAP polling loop that fetches unread messages, resolves sender to project, and enqueues sessions
- **Email output handler** (`bridge/email_bridge.py:EmailOutputHandler`): Implements `OutputHandler` protocol, sends SMTP replies threaded to the original email
- **Contact-to-project resolution** (`bridge/routing.py`): New `find_project_for_email()` function, reads email mappings from `projects.json`
- **Transport-keyed callback registration** (`agent/agent_session_queue.py`): Extends `register_callbacks()` and `_send_callbacks` to support `(project_key, transport)` composite keys with fallback to `project_key`-only for backward compatibility
- **Config schema extension** (`projects.json`): Adds `email` section per project with address-to-contact mappings and IMAP/SMTP server config

### Flow

**Inbound email** → IMAP poll → Parse sender/body/thread → Resolve project → `enqueue_agent_session()` → Worker executes agent → `EmailOutputHandler.send()` → SMTP reply → **Email in sender's inbox**

### Technical Approach

- IMAP polling with `imaplib` (stdlib) — no third-party dependencies. Poll interval configurable, default 30 seconds.
- SMTP sending with `smtplib` (stdlib) — supports STARTTLS. Connection pooling via persistent SMTP connection with keepalive.
- Thread continuation via `In-Reply-To` and `References` headers. Email `Message-ID` stored as origin metadata on `AgentSession` (in `extra_context`).
- Transport field stored in `AgentSession.extra_context["transport"]` (values: `"telegram"` or `"email"`). Existing sessions default to `"telegram"`.
- Callback registration extended: `register_callbacks(project_key, transport="telegram", handler=...)`. Internal lookup: try `(project_key, transport)` first, fall back to `project_key` alone for backward compat.
- Email bridge runs as a separate process alongside the worker (same pattern as telegram bridge). Can be started via `python -m bridge.email_bridge` or integrated into `valor-service.sh`.
- Contact resolution: exact-match on email address in `projects.json` contacts section. No fuzzy matching, no domain-only matching.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] IMAP connection failures (network, auth) — must log warning and retry after backoff, not crash the poll loop
- [ ] SMTP send failures — must log error, retry once, then route to dead letter queue (mirror `bridge/telegram_relay.py` pattern)
- [ ] Malformed emails (no sender, no body, encoding errors) — must skip with warning log, not crash
- [ ] Unknown sender (no project match) — must log and discard, not enqueue a session with no project

### Empty/Invalid Input Handling
- [ ] Empty email body — skip, do not enqueue session
- [ ] Email with only attachments (no text body) — log warning, skip (attachment handling is out of scope)
- [ ] Whitespace-only email body — skip
- [ ] `find_project_for_email()` with None/empty string — return None

### Error State Rendering
- [ ] SMTP connection failure during reply — error logged, message persisted in dead letter queue for retry
- [ ] Email output handler `react()` is a no-op (email has no reactions) — verify it does not raise

## Test Impact

No existing tests affected — this is a greenfield feature with no prior test coverage. The email bridge is a new module (`bridge/email_bridge.py`) and the only existing code modified is `register_callbacks()` in `agent/agent_session_queue.py`, which gains backward-compatible transport keying. Existing tests for `register_callbacks()` will continue to work because the default transport fallback preserves current behavior.

- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: add test cases for transport-keyed callback registration (existing tests should still pass unchanged due to backward compat)

## Rabbit Holes

- **Full email parsing library** (e.g., `flanker`, `mailparser`): stdlib `email` module is sufficient for plain text and basic HTML-to-text. Do not add third-party email parsing.
- **Attachment handling**: Tempting to support file attachments in v1. Defer entirely — text-only emails for the first iteration.
- **HTML email rendering**: Agent output is plain text. Do not build an HTML email template system. Send plain text replies.
- **Email forwarding/routing rules**: Do not build a rules engine for email routing. Single inbox `valor@yuda.me`, sender-based exact-match to project.
- **Field rename (`initial_telegram_message` → `initial_message`)**: The issue mentions this as concurrent work. It is a separate migration with its own risk profile. Do not bundle it into this feature.
- **Cross-transport routing** (email in, Telegram out): Explicitly out of scope per the issue.
- **OAuth/Gmail API**: IMAP with app passwords is simpler and sufficient. Do not build OAuth flows for Gmail.

## Risks

### Risk 1: IMAP connection stability
**Impact:** Email inbox stops polling, inbound emails are missed until connection is restored
**Mitigation:** Implement reconnection with exponential backoff in the poll loop (same resilience pattern as `bridge/telegram_bridge.py`). Add health check endpoint that monitors IMAP connection state. Log connection failures to bridge log.

### Risk 2: SMTP delivery failures
**Impact:** Agent output never reaches the email sender
**Mitigation:** Mirror the `bridge/telegram_relay.py` retry+dead-letter pattern. Failed SMTP sends get up to 3 retries, then persist to dead letter queue for manual replay.

### Risk 3: Callback registration backward compatibility
**Impact:** Existing Telegram bridge breaks if `register_callbacks()` or `_send_callbacks` lookup changes are not backward compatible
**Mitigation:** Transport-keyed lookup falls back to `project_key`-only lookup. All existing callers pass no `transport` argument and continue to work. Regression test covers the fallback path.

## Race Conditions

### Race 1: Concurrent IMAP poll and session creation
**Location:** `bridge/email_bridge.py` — poll loop
**Trigger:** Two poll cycles run concurrently (e.g., slow IMAP fetch overlaps next poll)
**Data prerequisite:** IMAP SEEN flag must be set before next poll starts
**State prerequisite:** Message must be marked as SEEN atomically on fetch
**Mitigation:** Use IMAP `STORE +FLAGS (\Seen)` immediately after `FETCH`. Poll loop is single-threaded (async single task), so no true concurrency within one bridge instance. Guard against duplicate processing by checking session_id existence before enqueue.

### Race 2: Transport-keyed callback registration during session execution
**Location:** `agent/agent_session_queue.py` — `_send_callbacks` lookup at line 2783
**Trigger:** Worker resolves callback while bridge is registering new transport
**Data prerequisite:** Callback must be registered before any session for that transport is popped
**State prerequisite:** Bridge startup must complete registration before worker processes sessions
**Mitigation:** Registration happens at bridge startup, before any sessions are enqueued. The dict mutation is atomic in CPython (GIL). No lock needed.

## No-Gos (Out of Scope)

- SMS, Slack, Discord, or other transports
- Cross-transport routing (email in, Telegram out)
- Email threading beyond basic `In-Reply-To` header
- Attachment handling (file uploads/downloads in email)
- HTML email composition (plain text replies only)
- Field rename (`initial_telegram_message` → `initial_message`)
- OAuth-based email auth (use IMAP/SMTP with app passwords)
- Email forwarding rules or routing engine
- Multiple inbound email addresses (single address `valor@yuda.me` in v1)

## Update System

The update script (`scripts/remote-update.sh`) and update skill need changes:

- New `.env` variables must be propagated: `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_PORT`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_PORT`
- `valor-service.sh` needs new commands: `email-start`, `email-stop`, `email-restart`, `email-status`
- launchd plist for email bridge process (similar to `com.valor.bridge` and `com.valor.worker`)
- `projects.json` gains `email` section — existing installations without email config will not be affected (email bridge simply does not start if no email config exists)
- Migration: no data migration needed. New installations add email credentials to `.env` and email contacts to `projects.json`.

## Agent Integration

No agent integration required — the email bridge is a transport-layer change. The agent interacts with the same `AgentSession` model and `OutputHandler` protocol regardless of whether the session originated from Telegram or email. No new MCP servers, no `.mcp.json` changes, no bridge import changes for the agent.

The bridge itself (`bridge/email_bridge.py`) is a new standalone module that imports from `agent/agent_session_queue.py` and `bridge/routing.py` — both existing modules. The worker (`worker/__main__.py`) gains `EmailOutputHandler` registration alongside the existing `TelegramRelayOutputHandler`.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/email-bridge.md` describing the email bridge architecture, config, and operation
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/deployment.md` with email bridge setup instructions

### Inline Documentation
- [ ] Docstrings on all public functions in `bridge/email_bridge.py`
- [ ] Docstrings on `find_project_for_email()` in `bridge/routing.py`
- [ ] Update `register_callbacks()` docstring for transport parameter

## Success Criteria

- [ ] `bridge/email_bridge.py` receives inbound email via IMAP and calls `enqueue_agent_session()`
- [ ] `EmailOutputHandler` sends session output via SMTP, replying to the originating thread
- [ ] Email sender → project resolution works via `projects.json` contact config
- [ ] `AgentSession` carries `transport` in `extra_context`; worker resolves the correct outbox per session
- [ ] `register_callbacks()` supports transport-keyed registration without breaking existing Telegram callers
- [ ] Telegram bridge behavior is unchanged — no regressions
- [ ] `session_id` for email sessions uses `email_` prefix
- [ ] Unit tests: email → project resolution, `EmailOutputHandler.send()` shapes correct SMTP message, transport-keyed callback dispatch
- [ ] Integration test: end-to-end inbound email → session enqueued → output delivered via SMTP
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (email-bridge)**
  - Name: email-bridge-builder
  - Role: Implement email bridge module, output handler, and IMAP polling loop
  - Agent Type: builder
  - Resume: true

- **Builder (transport-keying)**
  - Name: transport-keying-builder
  - Role: Extend register_callbacks and callback lookup with transport dimension
  - Agent Type: builder
  - Resume: true

- **Builder (config-routing)**
  - Name: config-routing-builder
  - Role: Extend projects.json schema and routing.py with email contact resolution
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: email-test-engineer
  - Role: Write unit and integration tests for all email bridge components
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: email-validator
  - Role: Verify all success criteria, run full test suite, check backward compatibility
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using: builder, test-engineer, validator, documentarian

## Step by Step Tasks

### 1. Transport-Keyed Callback Registration
- **Task ID**: build-transport-keying
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_queue.py` (update), `tests/unit/test_transport_keyed_callbacks.py` (create)
- **Assigned To**: transport-keying-builder
- **Agent Type**: builder
- **Parallel**: true
- Extend `_send_callbacks` and `_reaction_callbacks` dict keys from `str` to support `(project_key, transport)` tuples
- Add `transport` parameter to `register_callbacks()` (default `None` for backward compat)
- Update callback lookup in `send_to_chat` (line ~2783) to try `(project_key, transport)` first, fall back to `project_key`
- Add `transport` property to `AgentSession` reading from `extra_context.get("transport", "telegram")`
- Ensure all existing callers continue to work with no changes (backward compat)

### 2. Email Contact Resolution in Config and Routing
- **Task ID**: build-config-routing
- **Depends On**: none
- **Validates**: `tests/unit/test_email_routing.py` (create)
- **Assigned To**: config-routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `email` section to `projects.example.json` with contact mapping format
- Implement `find_project_for_email(sender_email: str) -> dict | None` in `bridge/routing.py`
- Implement `load_email_contacts(config: dict) -> dict[str, dict]` to build email-to-project mapping
- Initialize email contact map at bridge startup (parallel to `build_group_to_project_map`)

### 3. Email Bridge Module (Inbox + Outbox)
- **Task ID**: build-email-bridge
- **Depends On**: build-transport-keying, build-config-routing
- **Validates**: `tests/unit/test_email_bridge.py` (create), `tests/integration/test_email_bridge.py` (create)
- **Assigned To**: email-bridge-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `bridge/email_bridge.py` with:
  - `EmailOutputHandler` class implementing `OutputHandler` protocol (SMTP `send()`, no-op `react()`)
  - `_poll_imap()` coroutine: connect to IMAP, fetch unseen messages, parse sender/subject/body/Message-ID
  - `_process_inbound_email()`: resolve sender to project, construct session_id, call `enqueue_agent_session()` with `transport="email"` in extra_context
  - `_email_inbox_loop()`: poll loop with configurable interval, reconnection with backoff
  - `main()` entry point for `python -m bridge.email_bridge`
- Thread continuation: store `Message-ID` in `extra_context["email_message_id"]`, use `In-Reply-To` header in outbound replies
- Dead letter handling: mirror `bridge/telegram_relay.py` retry pattern for failed SMTP sends

### 4. Worker Registration
- **Task ID**: build-worker-registration
- **Depends On**: build-email-bridge, build-transport-keying
- **Validates**: `tests/unit/test_worker_email_registration.py` (create)
- **Assigned To**: email-bridge-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `worker/__main__.py:_run_worker()` to register `EmailOutputHandler` alongside `TelegramRelayOutputHandler` when email config exists
- Register with `transport="email"` parameter
- Skip registration if no email config in `projects.json` (graceful degradation)

### 5. Service Scripts
- **Task ID**: build-service-scripts
- **Depends On**: build-email-bridge
- **Assigned To**: email-bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `email-start`, `email-stop`, `email-restart`, `email-status` commands to `scripts/valor-service.sh`
- Create launchd plist `com.valor.email-bridge.plist` (mirror `com.valor.bridge` pattern)
- Add install script `scripts/install_email_bridge.sh`

### 6. Test Suite
- **Task ID**: build-tests
- **Depends On**: build-email-bridge, build-transport-keying, build-config-routing
- **Validates**: full test suite
- **Assigned To**: email-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit tests: `EmailOutputHandler.send()` constructs correct SMTP message with headers, `react()` is no-op
- Unit tests: `find_project_for_email()` exact match, unknown sender returns None, empty input returns None
- Unit tests: transport-keyed callback registration and lookup with fallback
- Unit tests: IMAP message parsing (sender extraction, body extraction, Message-ID extraction)
- Integration test: end-to-end email → session enqueue → output via SMTP (mock IMAP/SMTP servers)

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/email-bridge.md`
- Add entry to `docs/features/README.md` index table
- Update `docs/deployment.md` with email bridge setup
- Update `CLAUDE.md` quick commands table with email bridge commands

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: email-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify backward compatibility: existing Telegram tests pass unchanged
- Verify all success criteria met
- Verify documentation created and indexed
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Email bridge importable | `python -c "from bridge.email_bridge import EmailOutputHandler"` | exit code 0 |
| Email routing importable | `python -c "from bridge.routing import find_project_for_email"` | exit code 0 |
| Transport-keyed callbacks | `pytest tests/unit/test_transport_keyed_callbacks.py -x -q` | exit code 0 |
| Email bridge tests | `pytest tests/unit/test_email_bridge.py -x -q` | exit code 0 |
| Email routing tests | `pytest tests/unit/test_email_routing.py -x -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). 2026-04-10 -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Adversary | `telegram_message_id: int` is required in `enqueue_agent_session()` and `_push_agent_session()` — email has no Telegram message ID | Task 1 or Task 3 | Change param to `origin_message_id: int | str | None = None` with backward-compat alias, or pass `0` and document the convention. The value flows to `send_cb(chat_id, msg, session.telegram_message_id, agent_session)` at line 2864 — `EmailOutputHandler.send()` must tolerate a non-Telegram value as `reply_to_msg_id`. |
| BLOCKER | Skeptic, Operator | `register_callbacks()` is called once per project (line 194 of `worker/__main__.py`) — plan says email and Telegram handlers coexist per project, but current callback lookup at line 2783 uses `session.project_key` only, not transport | Task 1 | The callback lookup at line 2783 (`_send_callbacks.get(session.project_key)`) and all `send_cb(...)` call sites (lines 2864, 2934, 2965) must be updated to try `(project_key, transport)` first. The plan's Task 1 mentions this but the plan's Data Flow step 6 incorrectly says `agent/agent_session_queue.py` — the lookup is in the same file but inside `send_to_chat()` closure, not a separate function. |
| CONCERN | Adversary | `enqueue_agent_session()` has `telegram_message_key: str | None` parameter — email bridge needs equivalent for thread continuation but plan stores Message-ID in `extra_context["email_message_id"]` only | Task 3 | For email thread continuation, the `In-Reply-To` lookup needs a reverse mapping from email Message-ID to session_id. Store in Redis: `email:msgid:{message_id} -> session_id`. Without this, replies to Valor's emails cannot resume the correct session. |
| CONCERN | Operator | No health check or monitoring for the email bridge — plan mentions "health check endpoint" in Risk 1 mitigation but no task implements it | Task 5 | Add `email-health` command to `valor-service.sh` that checks IMAP connection liveness. Mirror the bridge watchdog pattern: check last successful poll timestamp in Redis (`email:last_poll_ts`), alert if stale > 5 minutes. |
| CONCERN | Operator | Dead letter queue mentioned in Failure Path Test Strategy and Risk 2 but not specified — no Redis key, no replay mechanism, no monitoring | Task 3 | Define Redis key `email:dead_letter:{session_id}` storing the failed SMTP payload. Add `email-dead-letter list` and `email-dead-letter replay` commands to `valor-service.sh` or as a Python script. |
| CONCERN | Skeptic | Prerequisites check for SMTP/IMAP credentials will fail on all current machines (verified: `.env` has none of these) — no task creates the credentials setup documentation or `.env.example` update | Task 5 or Task 7 | Add IMAP/SMTP credential vars to `.env.example`. Document Gmail App Password setup in `docs/features/email-bridge.md`. |
| CONCERN | Simplifier | Task 5 (Service Scripts) creates launchd plist and install script — this is premature for v1 when email bridge can run via `python -m bridge.email_bridge` manually | Task 5 | Consider deferring launchd plist to a follow-up. The `valor-service.sh` commands are sufficient for v1. |
| NIT | Simplifier | Task 4 (Worker Registration) is trivially small — 3 lines of code adding email handler alongside Telegram handler. Could be absorbed into Task 1 or Task 3. | — | — |
| NIT | User | Success criteria list "Tests pass (`/do-test`)" and "Documentation updated (`/do-docs`)" — these are process gates, not feature acceptance criteria | — | — |

---

## Open Questions

*All resolved — see decisions below.*

### Resolved

1. **IMAP vs webhook for inbound email**: **IMAP polling.** Stdlib only, no third-party deps, works with any Gmail account. App Password auth (Gmail → Security → 2FA → App Passwords). Webhook-based ingest can be a future optimization if latency matters.

2. **Email address allocation**: **Single address `valor@yuda.me`** with sender-based routing. The `From:` address on outbound replies determines which project/context Valor is operating in. Different products (cuttlefish, etc.) will have different email requirements but all route through the same inbox.

3. **SMTP/IMAP credentials**: **Single global account** via `.env` variables: `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_PORT`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_PORT`. Gmail with App Password.

4. **Gmail API vs IMAP/SMTP**: **IMAP/SMTP.** The bridge is a long-running daemon — static App Password credentials are simpler than OAuth token refresh. Gmail MCP tools remain available to the agent during sessions for email search/context, but the bridge transport layer uses IMAP/SMTP.
