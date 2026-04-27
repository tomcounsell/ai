---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-04-21
tracking: https://github.com/tomcounsell/ai/issues/1093
last_comment_id:
---

# Customer Resolver — Dynamic Email → Customer ID Routing

## Problem

Valor's email bridge needs to serve customers in a professional, high-stakes role — starting with the Cuttlefish personalized podcast service, where Valor acts as the customer service agent. When a customer emails in, we need three things that don't exist today:

1. **Identity resolution** — figure out *who* the sender is (a customer? which one?) rather than just "are they in the static allow list?"
2. **Robust routing** — no duplicated work when the same customer sends multiple emails; no agent sessions spun up for strangers or spam; graceful handling of resolver failures without silently losing mail.
3. **Customer-aware agent behavior** — the agent session must know which customer it is serving so the target repo (Cuttlefish) can load the customer's profile, pending tasks, and history from its own CRM.

**Current behavior:**
- `bridge/email_bridge.py` fetches UNSEEN mail from senders matched against a static `email.contacts` list in `projects.json`. The list is baked in at deploy time and gives no signal beyond boolean allow/deny.
- No identity is carried into the session — the agent only sees `from_addr`.
- No per-customer coalescing: two fresh (non–In-Reply-To) emails from the same customer spawn two parallel sessions that can race and send conflicting replies.
- `email.persona: "customer-service"` is wired (`bridge/email_bridge.py:435-437`, `agent/sdk_client.py:2539`) but the persona overlay file doesn't exist, so it falls back to `teammate` silently.

**Desired outcome:**
- Any project can declare a `customer_resolver` hook (subprocess or importlib callable). The hook takes a sender identifier and returns `customer_id | None`.
- If the resolver says "not a customer," the email is dropped cleanly. No session, no cost.
- If the resolver says "this is customer X," the bridge spawns a customer-service session in the target project's working directory with `customer_id` available both in the system prompt and as a `CUSTOMER_ID` environment variable. The target repo's CLAUDE.md and skills do the rest.
- Subject-line and In-Reply-To continuation both coalesce subsequent mail into the same `session_id` — no parallel sessions from the same customer.
- Resolver failures fail-closed but don't silently lose mail: the message is archived via a Gmail `valor-retry` label (applied on failure) so a future retry mechanism can pick it up. The failure counter in Redis is available for any future watchdog.

## Freshness Check

**Baseline commit:** `66814b94` (`main` — this plan's first commit; freshness verified before that against `d3e862ba`)
**Issue filed at:** 2026-04-21T05:40:35Z (~3h before plan)
**Disposition:** Unchanged

**File:line references re-verified:**
- `config/enums.py:31` — `PersonaType.CUSTOMER_SERVICE` — still holds
- `bridge/telegram_bridge.py:588` — DM whitelist comment (variable `DM_WHITELIST` on line 592) — minor drift (1-line shift), claim holds
- `bridge/routing.py:162` — `build_email_to_project_map` — still holds
- `bridge/email_bridge.py:445-459` — In-Reply-To thread continuation — still holds
- `bridge/email_bridge.py:568` — `\Seen` mark-before-fetch — still holds
- `agent/sdk_client.py:2539-2548` — `CUSTOMER_SERVICE` persona with teammate fallback — still holds
- `agent/sdk_client.py:613-668` — `load_persona_prompt()` — still holds
- `agent/sdk_client.py:599-610` — overlay path resolution — still holds
- `agent/sdk_client.py:1034-1035` — `CLAUDE_CODE_TASK_LIST_ID` env injection point — still holds
- `agent/reflection_scheduler.py:193` — `_resolve_callable` importlib dispatch — still holds

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **PR #908 (#847)** — *email bridge — IMAP/SMTP secondary transport*: established the current architecture (IMAP poll → `_process_inbound_email` → `enqueue_agent_session`, SMTP outbound with dead-letter, `\Seen` gating, `email:msgid:*` Redis thread map). This plan extends that skeleton; does not replace it.
- **PR #956 (#946)** — *register EmailOutputHandler for domain-routed projects*: confirmed the routing layer is already domain-aware and can carry project-specific config into session creation.
- **PR #1094 (#1067)** — *valor-email CLI*: outbox relay pattern on the outbound side. Not directly touched, but confirms reply path is well-tested.
- **PR #909** — *SDLC stage model selection + hard-PATCH builder resume*: established the `extra_context` carry-through pattern we use to pipe `customer_id` from bridge → session → subprocess env.
- **Issue #743** / externalized steering (`queued_steering_messages`): considered for coalescing, but Spike 2 confirmed we should use the existing supersede-and-enqueue pattern (same as In-Reply-To), not steering.
- **Issue #998** — *Telegram MessageEdited events — steer or respawn*: parallel prior art for "second inbound from same thread." Telegram chose steer; we choose supersede for email (matches existing email-bridge convention).

No prior attempt to build a dynamic customer resolver. Greenfield in valor-ai.

## Research

**Queries used:**
1. `Python imaplib X-GM-RAW Gmail label search 2026`
2. `uv run inline script metadata PEP 723 subprocess external CLI pattern`
3. `IMAP session coalescing idempotency distributed consumers prevent duplicate processing`

**Key findings:**
- **Python's `imaplib` supports Gmail's IMAP extensions natively** (source: [Gmail IMAP Extensions](https://developers.google.com/workspace/gmail/imap/imap-extensions)). Call shape for applying a custom label: `conn.uid('store', uid, '+X-GM-LABELS', '("valor-retry")')`. Gmail auto-creates labels on first STORE. We use this narrowly for the failure-retry archive; NOT for the primary poll gate.
- **PEP 723 inline script metadata + `uv run`** (source: [PEP 723](https://peps.python.org/pep-0723/), [uv scripts guide](https://docs.astral.sh/uv/guides/scripts/)): lets resolver scripts in external repos (like Cuttlefish) carry their own deps via `#!/usr/bin/env -S uv run` + `# /// script` TOML block, without polluting valor-ai's venv.
- **Idempotent consumer pattern** (source: [Microservices Pattern: Idempotent Consumer](https://microservices.io/patterns/communication-style/idempotent-consumer.html)): store a per-message idempotency key atomically with the outcome. Our equivalents are already in place: `\Seen` gating + `email:msgid:{Message-ID}` Redis map. Sufficient for single-machine-per-project operation.

## Spike Results

### spike-1: Gmail X-GM-LABELS works from our imaplib connection
- **Assumption**: `imaplib.IMAP4_SSL` in Python 3.13 supports `X-GM-LABELS` via `conn.uid(...)` varargs.
- **Method**: code-read (`bridge/email_bridge.py:520-580`) + Python stdlib behavior.
- **Finding**: **Confirmed, high confidence.** Current code already uses `conn.uid('store', uid, '+FLAGS', '\\Seen')` at line 568. The varargs pattern accepts Gmail extensions identically: `conn.uid('store', uid, '+X-GM-LABELS', '("valor-retry")')`. Label names with spaces require quoting. Gmail auto-creates labels on first STORE.
- **Impact on plan**: We only need X-GM-LABELS in the narrow failure-retry path. No capability check required — if the server doesn't speak X-GM-EXT-1 (non-Gmail), the STORE errors and we log+continue; the primary poll path is unaffected.

### spike-2: `queued_steering_messages` vs supersede-and-enqueue for coalescing
- **Assumption**: Bridge-layer coalescing should call `steer_session()`.
- **Method**: code-read (`agent/session_executor.py:422-470`, `models/agent_session.py:1371-1390`, `bridge/email_bridge.py:443-475`).
- **Finding**: **Refuted.** The existing In-Reply-To continuation path does NOT use steering: it looks up `existing_session_id` via `email:msgid:{in_reply_to}` and calls `enqueue_agent_session(session_id=existing_session_id, ...)`. `_push_agent_session` marks old records "superseded" and creates a new pending AgentSession with the same `session_id`.
- **Impact on plan**: Use the same supersede-and-enqueue pattern for subject-line coalescing. Sidesteps the read-modify-write race window in `push_steering_message`.

### spike-3: persona prompt substitution + env var injection
- **Assumption**: `load_persona_prompt()` already supports `{placeholder}` substitution; `CLAUDE_CODE_TASK_LIST_ID` injection point is clear.
- **Method**: code-read (`agent/sdk_client.py:599-668`, `sdk_client.py:957-1035`, `sdk_client.py:2495-2560`).
- **Finding**: **Half-refuted, half-confirmed.** No substitution mechanism today. Env var injection point IS clear: `ValorAgent._create_options()` at `sdk_client.py:1034-1035` sets `env["CLAUDE_CODE_TASK_LIST_ID"]`. Adding `env["CUSTOMER_ID"]` is mechanically identical. Carry-through via `AgentSession.extra_context["customer_id"]`.
- **Impact on plan**: Add substitution via an optional `substitutions` parameter on `load_persona_prompt()`. Add `customer_id` through `ValorAgent.__init__` and `_create_options()`. Read from `extra_context` at `sdk_client.py:2539` area.

### spike-4: subject-line coalescing — normalization, lookup, matching
- **Assumption**: We can match normalized subject + project_key + customer_id to existing non-terminal sessions with acceptable false-positive rate.
- **Method**: code-read.
- **Finding**: **Confirmed with caveats.** No existing `normalize_subject()` utility. Rules: strip leading `^(Re|Fwd|Fw|Aw|AW|Antw|RE|FW|FWD)[\[\]\d\s]*:\s*` repeatedly; strip `[ticket-NNN]`-style leading bracket tags; collapse whitespace; lowercase. **Lookup: DB query** against `AgentSession` filtered by `project_key` and non-terminal statuses, with Python-side filter on `extra_context.get("customer_id")` and normalized subject. Scope `(project_key, customer_id)` bounds false-positive blast radius to a single customer's own recent correspondence — acceptable.
- **Impact on plan**: Build `normalize_subject()` + `find_coalescing_session_id()`. Order of precedence: (1) In-Reply-To (existing), (2) subject-line (new), (3) new session.

## Data Flow

```
Gmail IMAP INBOX (operator keeps empty; Gmail handles spam pre-filter)
      │
      ▼
[1] _email_inbox_loop (email_bridge.py:584)
      │  poll every IMAP_POLL_INTERVAL; update email:last_poll_ts
      ▼
[2] _poll_imap (email_bridge.py:520)
      │  UNSEEN FROM <known_senders>  (UNCHANGED from today)
      │  mark \Seen before fetch (UNCHANGED)
      ▼
[3] parse_email_message (email_bridge.py:150)
      │  extract from_addr, subject, body, message_id, in_reply_to
      ▼
[4] _process_inbound_email (email_bridge.py:401)
      │  find_project_for_email(from_addr) → project_config
      │
      ├─ no customer_resolver in project config ─► existing static flow (unchanged)
      │
      ▼ yes, resolver declared
[5] resolve_customer (bridge/routing.py, NEW)
      │  Redis GET customer_resolver:{project_key}:{from_addr}
      │  cache miss → dispatch subprocess or importlib callable
      │  success empty stdout → cache "" (= None) for TTL
      │  success customer_id → cache id for TTL
      │  error/timeout → increment resolver:failures:{project_key},
      │                  apply X-GM-LABELS +valor-retry (archival),
      │                  return None (fail-closed)
      │
      ├─ None ──► no session (message stays \Seen; we're done)
      │
      ▼ customer_id
[6] find_coalescing_session_id (NEW)
      │  precedence:
      │   (a) In-Reply-To → Redis email:msgid:{id} (existing)
      │   (b) subject-line → DB query + normalize_subject match (new)
      │   (c) neither → new session_id
      │
      ▼
[7] enqueue_agent_session
      │  extra_context_overrides = {
      │      "transport": "email",
      │      "email_message_id": message_id,
      │      "email_from": from_addr,
      │      "email_subject": subject,
      │      "customer_id": customer_id,   (NEW)
      │  }
      │  session_type = TEAMMATE; persona = customer-service
      │
      ▼
[8] Worker picks up AgentSession (status=pending)
      │
      ▼
[9] sdk_client._execute_agent_session
      │  persona resolution (sdk_client.py:2500):
      │    email + persona=customer-service → load_persona_prompt("customer-service", substitutions={"customer_id": ...})
      │  ValorAgent(customer_id=..., ...)
      │  _create_options sets env["CUSTOMER_ID"] = customer_id  (NEW, line 1034-ish)
      │
      ▼
[10] Claude Code subprocess in working_directory (e.g., ~/src/cuttlefish)
      │  Loads its own CLAUDE.md, .claude/skills/, tools
      │  Uses CUSTOMER_ID + prompt context to load customer, check pending tasks, draft reply
      │
      ▼
[11] Reply → EmailOutputHandler.send → SMTP (dead-letter on failure; existing)
      │
      ▼
[12] Outbound Message-ID stored in email:msgid:{id} → session_id (existing, email_bridge.py:348)
```

## Architectural Impact

- **New dependencies**: None in valor-ai. External resolvers carry their own deps via `uv run` / PEP 723.
- **Interface changes**:
  - `ValorAgent.__init__` gains `customer_id: str | None = None`.
  - `load_persona_prompt(persona, substitutions=None)` — backward-compatible.
  - `projects.json` gains optional `customer_resolver` block per project.
  - `bridge/routing.py` gains `resolve_customer()` + `invalidate_customer_cache()` helpers.
  - New utility `normalize_subject()` + `find_coalescing_session_id()` (placement decided at build time).
- **Coupling**: Decreases coupling between valor-ai and external customer CRMs — only the resolver contract is shared. Valor-ai gains zero Cuttlefish-specific knowledge.
- **Data ownership**: Customer identity remains owned by the target repo. Valor-ai caches resolver results but never persists customer data.
- **Reversibility**: High. Projects without `customer_resolver` keep current behavior. Removing the feature is a revert; `valor-retry` labels in Gmail are harmless artifacts.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM + code reviewer

**Interactions:**
- PM check-ins: 1 (critique-stage alignment)
- Review rounds: 1-2 (code review for bridge changes + security review for subprocess dispatch)

Integration across several files is what makes this Medium rather than Small.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Resolver cache, failure counter, existing `email:msgid:*` map |
| IMAP config in `.env` | `python -c "from dotenv import dotenv_values; c = dotenv_values('.env'); assert all(c.get(k) for k in ('IMAP_HOST','IMAP_USER','IMAP_PASSWORD'))"` | Email bridge connection |
| `uv` installed on host | `uv --version` | Subprocess-form resolvers using PEP 723 inline deps |
| `customer-service` PersonaType registered | `python -c "from config.enums import PersonaType; assert PersonaType.CUSTOMER_SERVICE.value == 'customer-service'"` | Persona resolution path exists |

Run all checks: `python scripts/check_prerequisites.py docs/plans/customer-resolver.md`

## Solution

### Key Elements

- **`resolve_customer(sender, project_config, imap_conn=None, imap_uid=None)`** in `bridge/routing.py`: generic per-message dispatcher. Reads Redis cache; on miss, runs subprocess (`command` form) or importlib callable (`callable` form); caches result with per-project TTL. Stores `""` for cached None. Increments `resolver:failures:{project_key}` on error; fail-closed (returns None); applies Gmail `valor-retry` label on error so the message is preserved for future retry (best-effort — skipped if no IMAP context).
- **Subject-line coalescing** in the bridge: `normalize_subject()` + `find_coalescing_session_id()` after In-Reply-To lookup misses.
- **Customer-service persona** at `config/personas/customer-service.md`: MINIMAL fallback overlay. Real tone, escalation rules, and style live in `~/Desktop/Valor/personas/customer-service.md` (iCloud-synced, private) + Cuttlefish repo skills. The in-repo file exists so the loader doesn't silent-fallback to `teammate` when the private overlay is missing.
- **Substitution + env var injection** in `agent/sdk_client.py`: extend `load_persona_prompt()` with optional `substitutions`; plumb `customer_id` through `ValorAgent.__init__` → `_create_options` → `env["CUSTOMER_ID"]`.
- **`valor-retry` Gmail label** (single label, failure-archive only): applied on resolver failure via `X-GM-LABELS` STORE. Not used for poll gating, observability tagging, or primary dedup. Downstream retry mechanism is a future issue.
- **`resolver:failures:{project_key}` Redis counter**: INCR on error, DEL on success. Exposed as a breadcrumb for a future watchdog. v1 has no active consumer.

### Flow

Customer emails Cuttlefish →
  Gmail inbox (spam pre-filtered) →
  Valor email bridge polls UNSEEN FROM known senders →
  resolver hits Cuttlefish API (cached in Redis) →
  `customer_id` resolved →
  bridge checks for existing session (In-Reply-To, then subject-line) →
  enqueues session (new or resumed) with `customer_id` in `extra_context` →
  worker spawns Claude Code in `~/src/cuttlefish` with `CUSTOMER_ID` env var →
  agent loads customer via Cuttlefish's own tools →
  agent drafts reply →
  SMTP sends (dead-letter on failure) →
  outbound Message-ID stored for future thread continuation.

On resolver failure: message marked `\Seen` + labeled `valor-retry` + counter incremented. No session. Future retry mechanism (out of scope) will consume this label.

### Technical Approach

- **Resolver dispatch** in `bridge/routing.py` follows the importlib-callable pattern at `agent/reflection_scheduler.py:193` for the `callable` form. Subprocess form uses `asyncio.create_subprocess_exec` (argv form only; never shell) with a 5s timeout. stdout is trimmed; empty = None; non-empty = customer_id after sanitization regex.
- **Cache key**: `customer_resolver:{project_key}:{sender_id}`. TTL from `customer_resolver.cache_ttl_seconds` (default 300). Cached-None = empty string.
- **Failure counter**: `resolver:failures:{project_key}` INCR on any resolver exception, timeout, or malformed output. DEL on any successful resolution.
- **`valor-retry` label application** on resolver failure: `conn.uid('store', uid, '+X-GM-LABELS', '("valor-retry")')`. If STORE errors (e.g., non-Gmail server), log WARN and continue. Label is advisory archival; absence doesn't break anything.
- **Subject-line coalescing**:
  - `normalize_subject(s)`: strip leading `^(Re|Fwd|Fw|Aw|AW|Antw|RE|FW|FWD)[\[\]\d\s]*:\s*` repeatedly; strip `[ticket-NNN]`-style leading bracket tags; collapse whitespace; `.strip().lower()`.
  - `find_coalescing_session_id(project_key, customer_id, normalized_subject) -> str | None`: query `AgentSession.query.filter(project_key=project_key)` for non-terminal statuses; Python-side filter on `extra_context.get("customer_id")` + `normalize_subject(extra_context.get("email_subject",""))`; return most-recently-created `session_id`.
  - Empty `normalized_subject` never coalesces (return None immediately).
  - Precedence in `_process_inbound_email`: (1) In-Reply-To Redis lookup, (2) subject-line, (3) new `session_id`. Log which path was taken at INFO.
- **Persona substitution**:
  - Extend `load_persona_prompt(persona, substitutions=None)`. After assembling segments + overlay, if `substitutions` is provided: `content = content.format_map(_SafeFormatDict(substitutions))`. `_SafeFormatDict` returns `{key}` for missing keys so unreferenced braces don't crash.
  - At `sdk_client.py:2542`, pass `substitutions={"customer_id": _session_extra_context.get("customer_id", "unknown")}`.
- **Env var injection**:
  - `ValorAgent.__init__` gains `customer_id: str | None = None` (line 957-area).
  - `_create_options` (line 1034-area): `if self.customer_id: env["CUSTOMER_ID"] = self.customer_id`.
  - Caller at `_execute_agent_session` reads `_session_extra_context.get("customer_id")` and passes into `ValorAgent(...)`.

### Single-machine-per-project operation

This plan assumes one bridge machine polls any given Gmail account at a time — enforced by `projects.<key>.machine` in `projects.json` (each project is owned by exactly one machine; whitelist entries inherit ownership via their `project` field). The bridge fails fast at startup if a contact would route to multiple machines. With single-machine operation:
- No concurrent-poll race on `\Seen` marking.
- No concurrent resolver dispatch for the same sender (cache stampede trivially avoided).
- No concurrent `find_coalescing_session_id` races.

If multi-machine polling is ever introduced, the design needs revisiting (locks on poll cursor, etc.). Operational guidance — not code.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `except Exception` blocks in touched files — `bridge/email_bridge.py` (existing: lines 449, 470, 494, 607, 618) and new code. Every new `except` path must increment an observable counter or log at WARN/ERROR.
- [ ] `resolve_customer` logs at WARN on timeout, ERROR on unexpected exception; always increments `resolver:failures:{project_key}`; always attempts `valor-retry` label (best-effort); always returns None on error.

### Empty/Invalid Input Handling
- [ ] Resolver returning empty stdout → treated as None. Test: subprocess that prints nothing.
- [ ] Resolver returning whitespace-only → treated as None. Test: subprocess that prints `"  \n"`.
- [ ] Resolver returning non-ASCII / multi-line / garbage → sanitization regex (conservative `[A-Za-z0-9_\-:.]{1,128}$`) rejects; fail-closed. Test: subprocess that prints `"<html>foo</html>"`.
- [ ] `normalize_subject("")` → empty string → coalescing returns None.
- [ ] In-Reply-To header missing or malformed → Redis lookup returns None → falls through to subject-line path.

### Error State Rendering
- [ ] `valor-retry` label applied on resolver failure; confirmable via Gmail search `label:valor-retry`.
- [ ] Failure counter incremented on every failure path; confirmable via `redis-cli GET resolver:failures:{project_key}`.

## Test Impact

- [ ] `tests/unit/test_email_bridge.py` (or equivalent) — UPDATE tests that construct `extra_context` for an email session to tolerate the new `customer_id` key (prefer `>=` semantics over equality).
- [ ] `tests/unit/test_sdk_client.py` (or equivalent for `load_persona_prompt`) — UPDATE if any test asserts exact output; add a new test for the `substitutions` parameter.
- [ ] Any test asserting `_create_options` env dict — UPDATE to tolerate new `CUSTOMER_ID` key.

No existing test exercises `customer_resolver`, resolver dispatch, subject-line coalescing, persona substitution, or `CUSTOMER_ID` env injection — new test files will be added:
- `tests/unit/test_customer_resolver.py` (new)
- `tests/unit/test_subject_coalescing.py` (new)
- `tests/unit/test_persona_substitution.py` (new)
- `tests/integration/test_email_customer_routing.py` (new)

## Rabbit Holes

- **Building a Redis SET / bloom filter Tier 1 inside valor-ai.** The resolver contract is the seam — if scale demands caching, Cuttlefish builds it behind their resolver. Don't leak that concern into valor-ai.
- **Gmail X-GM-RAW gating for the primary poll.** Considered and rejected — `\Seen` + static `email.contacts` + Gmail's own inbox classification already does the heavy lifting. Switching the poll gate to a custom label adds complexity without solving a real problem at current scale.
- **Multi-purpose Gmail labels (`customer`, `not-customer`, `valor-processed`).** Considered and rejected — new replies on the same Gmail thread inherit thread labels, so per-message classification via labels is misleading. Single-purpose `valor-retry` is the only label, and only for the narrow retry-archival case.
- **Email-bridge watchdog + `resolver_health` reflection.** Out of scope for this plan — followup issue. Keep the `resolver:failures:*` counter as a breadcrumb.
- **One-time inbox migration** to bootstrap existing `\Seen` mail. Not needed — inbox is maintained empty by the operator.
- **Gmail IMAP capability check.** Not needed — the hot path doesn't require X-GM-EXT-1. If the `valor-retry` STORE fails on a non-Gmail server, we log and move on.
- **Generic "message classifier" layer above the resolver.** Only one classification dimension exists (customer vs not). Don't abstract prematurely.
- **Rewriting the email bridge to use `aioimaplib` / `imapclient`.** Current `imaplib` + `asyncio.to_thread` is working and well-understood. Stay the course.

## Risks

### Risk 1: Resolver latency or flakiness stalls the IMAP poll loop
**Impact:** Every inbound message waits on a subprocess or callable. If the resolver hangs, the poll stalls; no mail gets processed.
**Mitigation:** Hard 5s timeout on resolver dispatch (configurable per project). Wrap subprocess in `asyncio.wait_for`. On timeout → fail-closed → `valor-retry` label → increment counter → next message. Sustained failures surface via the counter.

### Risk 2: Subject-line coalescing false-positive merges unrelated threads
**Impact:** Two independent questions from the same customer with accidentally-matching normalized subjects get merged into one session; the agent conflates context.
**Mitigation:** Coalescing is scoped to `(project_key, customer_id)` — false positives are confined to a single customer's own recent correspondence, which is the right semantic. Empty normalized subject never coalesces. In-Reply-To is always preferred. Log every coalescing decision at INFO.

### Risk 3: Cached stale customer_id persists after a customer is removed from CRM
**Impact:** A former customer's email gets routed as a customer for up to `cache_ttl_seconds` after removal.
**Mitigation:** 300s default TTL is a reasonable bound. `invalidate_customer_cache(project_key, sender_id)` helper exported for explicit invalidation when the CRM changes.

### Risk 4: Subprocess resolver shells out to user-influenced input
**Impact:** If `command` template interpolates `{sender_id}` unsafely, a crafted email address could inject shell syntax.
**Mitigation:** Never pass sender through a shell. Use `asyncio.create_subprocess_exec` (argv form only). Pre-validate sender with a conservative email regex before dispatch. Reject anything that doesn't look like a plausible email.

### Risk 5: Silent message loss when the resolver is persistently broken
**Impact:** The bridge keeps marking messages `\Seen`, applying `valor-retry` label, and dropping them. Without an active retry mechanism, the customer is silently ignored until an operator notices.
**Mitigation:** `valor-retry` label preserves the archive — operators can search Gmail for `label:valor-retry` at any time. `resolver:failures:{project_key}` counter is available for a future watchdog. For v1 this is an operational tradeoff: fail-closed with archival, manual supervision until the watchdog lands.

## Race Conditions

### Race 1: Concurrent access to the same Gmail inbox from multiple machines
**Location:** `bridge/email_bridge.py:_poll_imap` (lines 542-574).
**Trigger:** Two machines poll the same Gmail account; both fetch the same UNSEEN messages.
**Mitigation:** **Avoided by operator configuration** — only one machine per project, per Gmail inbox, is expected. If violated, the existing `\Seen`-before-fetch at line 568 plus the `email:msgid:{message_id}` map provides reasonable (not perfect) dedup. Documented as an operating assumption.

### Race 2: Resolver cache stampede on cold start
**Location:** `bridge/routing.py::resolve_customer` (new).
**Trigger:** Cold cache + many messages in a single poll from the same sender → parallel resolver dispatches.
**Mitigation:** In practice bounded by per-poll batch size (`IMAP_MAX_BATCH = 20`) and by `_process_inbound_email` being sequential within a poll cycle (`for raw_bytes in messages` at line 612). No actual concurrency today. Deferred.

## No-Gos (Out of Scope)

- **Tier 1 Redis-set customer-email cache inside valor-ai.** Moved to resolver-side (Cuttlefish decides).
- **Customer-service routing for Telegram.** Email-only in v1.
- **Cuttlefish-side resolver script, `CLAUDE.md`, skills, customer tools.** Owned by Cuttlefish repo.
- **Automatic customer CRM writes from the agent.** Writes are owned by Cuttlefish's tools, not introduced here.
- **`email_bridge_watchdog` launchd service.** Followup issue. This plan ensures `resolver:failures:*` exists so the watchdog has data to consume.
- **`resolver_health` reflection.** Followup issue.
- **Active retry mechanism** for `valor-retry`-labeled messages. Followup. This plan only preserves the archive.
- **Multi-purpose Gmail labels** (`customer`, `not-customer`, `valor-processed`). Rejected — thread-inherited labels make per-message classification misleading.
- **Gmail X-GM-RAW gating of primary poll.** Rejected — current `\Seen` + `UNSEEN FROM <senders>` is sufficient.
- **Gmail capability check at startup.** Rejected — hot path doesn't require X-GM-EXT-1.
- **One-time inbox migration.** Not needed — inbox is maintained empty.
- **Multi-machine polling support for the same inbox.** Deferred — operator handles single-machine assignment.
- **Backward-compat shims for projects using `dms.whitelist` or `email.contacts`.** These continue to work unchanged; `customer_resolver` is purely additive.

## Update System

- **No changes to `scripts/remote-update.sh` or `/update` skill.** This feature is config-level for projects and code-level in valor-ai only. No new launchd services (watchdog deferred).
- **Config propagation**: `projects.json` at `~/Desktop/Valor/projects.json` is iCloud-synced. `customer_resolver` blocks are per-project and propagate naturally.
- **Persona overlay**: `~/Desktop/Valor/personas/customer-service.md` — operator-maintained, iCloud-synced. In-repo `config/personas/customer-service.md` exists as a minimal fallback.
- **No new env var** in `.env` — `CUSTOMER_ID` is set per-subprocess.
- **Migration**: zero migration for existing installations. Projects without `customer_resolver` behave exactly as today.

## Agent Integration

- **No new MCP servers required in valor-ai.** The feature is internal to the bridge + session layer.
- **No `.mcp.json` changes.** Cuttlefish's MCP servers / tools live in its repo and are discovered by Claude Code at `working_directory`.
- **The bridge DOES directly call** `bridge/routing.py::resolve_customer` — no MCP detour.
- **Integration tests** verify: (1) bridge enqueues a customer-service session with `customer_id` in `extra_context` when resolver returns a non-empty ID; (2) spawned Claude Code subprocess receives `CUSTOMER_ID` env var; (3) persona prompt contains the substituted customer_id; (4) resolver failure path results in `valor-retry` label + incremented counter + no session.
- **Cuttlefish-side integration** (its own `CLAUDE.md`, tools, skills consuming `CUSTOMER_ID`) is out of scope and tracked in Cuttlefish.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/customer-resolver.md` covering: config schema, resolver interface contract (subprocess + callable forms), caching semantics, fail-closed behavior with `valor-retry` label, subject-line coalescing rules, customer-service persona behavior, `CUSTOMER_ID` env var contract, single-machine operating assumption, pointers to the followup watchdog/reflection issue.
- [ ] Add entry to `docs/features/README.md` index.
- [ ] Update `docs/features/email-bridge.md` briefly to note the optional customer-resolver hook.

### External Documentation Site
- N/A — no external docs site.

### Inline Documentation
- [ ] Docstrings on `resolve_customer`, `invalidate_customer_cache`, `normalize_subject`, `find_coalescing_session_id`, `_SafeFormatDict`.
- [ ] One-line comment at the substitution site in `load_persona_prompt` noting `_SafeFormatDict` preserves unreferenced braces.

## Success Criteria

- [ ] `bridge/routing.py::resolve_customer(sender, project_config)` dispatches subprocess and callable forms, caches result, fails closed, increments failure counter on error, attempts `valor-retry` label on error.
- [ ] A project with `customer_resolver` declared in `projects.json` sees inbound mail routed to `customer-service` persona with `customer_id` in session `extra_context`.
- [ ] Two fresh emails from the same customer with matching normalized subjects resume the same `session_id` via subject-line coalescing.
- [ ] `load_persona_prompt("customer-service", substitutions={"customer_id": "..."})` returns a prompt with the placeholder substituted.
- [ ] Spawned Claude Code subprocess has `CUSTOMER_ID` env var set when persona is `customer-service`.
- [ ] `config/personas/customer-service.md` exists and is loaded without falling back to `teammate`.
- [ ] Resolver failure path: message marked `\Seen`, `valor-retry` label applied in Gmail, `resolver:failures:*` counter incremented, no session created.
- [ ] Existing Telegram `bridge_watchdog` behavior unchanged — no collateral damage.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -rn "CUSTOMER_ID" agent/sdk_client.py` confirms injection point exists.

## Team Orchestration

### Team Members

- **Builder (resolver-core)**
  - Name: resolver-builder
  - Role: `bridge/routing.py::resolve_customer`, cache layer, failure counter, `valor-retry` label application, config-schema wiring.
  - Agent Type: builder
  - Resume: true

- **Builder (subject-coalescing)**
  - Name: coalescing-builder
  - Role: `normalize_subject`, `find_coalescing_session_id`, wire into `_process_inbound_email` after In-Reply-To lookup.
  - Agent Type: builder
  - Resume: true

- **Builder (persona + env var)**
  - Name: persona-builder
  - Role: `config/personas/customer-service.md` minimal overlay; `load_persona_prompt` substitution support; `ValorAgent.__init__` + `_create_options` env injection; call-site wire-up.
  - Agent Type: builder
  - Resume: true

- **Builder (bridge wire-up)**
  - Name: bridge-wire-builder
  - Role: Wire `resolve_customer` into `_process_inbound_email`; integrate coalescing lookup; ensure persona override on customer-id path; confirm existing static flow untouched for projects without a resolver.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (integration)**
  - Name: integration-tester
  - Role: End-to-end test from injected fake IMAP message through session spawn to `CUSTOMER_ID` env var presence; resolver dispatch + cache test; coalescing test; failure-path test (`valor-retry` label + counter).
  - Agent Type: test-engineer
  - Resume: true

- **Security Reviewer**
  - Name: subproc-security
  - Role: Review subprocess dispatch for command injection, sender-validation regex, timeout enforcement, environment-inheritance scoping, stdout length-cap.
  - Agent Type: security-reviewer
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: `docs/features/customer-resolver.md` + cross-links; update `docs/features/email-bridge.md`, `docs/features/README.md`.
  - Agent Type: documentarian
  - Resume: true

- **Validator (lead)**
  - Name: lead-validator
  - Role: Verifies all success criteria and the Verification table.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build resolver core
- **Task ID**: build-resolver-core
- **Depends On**: none
- **Validates**: `tests/unit/test_customer_resolver.py` (create) — subprocess + callable dispatch; cache hit/miss; cached-None distinction; fail-closed on timeout/error; failure counter increments; `valor-retry` label STORE is attempted on failure.
- **Informed By**: spike-1 (X-GM-LABELS call shape), research finding on PEP 723 + `uv run`.
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `resolve_customer(sender, project_config, imap_conn=None, imap_uid=None) -> str | None` to `bridge/routing.py`. `imap_conn`/`imap_uid` are optional; when both provided, attempt `valor-retry` label STORE on failure.
- Implement subprocess form (`asyncio.create_subprocess_exec`, argv form, 5s default timeout).
- Implement importlib callable form (follow `agent/reflection_scheduler.py:193`).
- Implement Redis cache (`customer_resolver:{project_key}:{sender_id}`, cached-None = `""`).
- Implement `resolver:failures:{project_key}` INCR on error; DEL on success.
- Sender pre-validation regex; reject malformed before dispatch.
- Output sanitization regex on subprocess stdout.
- Export helper `invalidate_customer_cache(project_key, sender_id)`.

### 2. Build subject-line coalescing
- **Task ID**: build-coalescing
- **Depends On**: none
- **Validates**: `tests/unit/test_subject_coalescing.py` (create) — normalization rules; DB query matching; empty-subject behavior; non-terminal status filter.
- **Informed By**: spike-4.
- **Assigned To**: coalescing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `normalize_subject(s)` per spike-4 rules.
- Add `find_coalescing_session_id(project_key, customer_id, normalized_subject) -> str | None` querying `AgentSession` for non-terminal sessions; Python-filter on `extra_context`.
- Decide placement (inline in `email_bridge.py` vs new `bridge/subject_utils.py`) based on LOC — default inline unless LOC > ~60.

### 3. Build persona + env-var plumbing
- **Task ID**: build-persona-env
- **Depends On**: none
- **Validates**: `tests/unit/test_persona_substitution.py` (create) + `tests/unit/test_valor_agent_env.py` (create or extend) — `{customer_id}` substitution; `CUSTOMER_ID` env var in Claude Code subprocess options.
- **Informed By**: spike-3.
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `config/personas/customer-service.md` as a MINIMAL fallback overlay. Defer tone/style/escalation rules to `~/Desktop/Valor/personas/customer-service.md` and target repo skills.
- Extend `load_persona_prompt(persona, substitutions=None)`. Implement `_SafeFormatDict` for safe `.format_map`.
- Add `customer_id: str | None = None` parameter to `ValorAgent.__init__` (line 957-area).
- In `_create_options` (line 1034-area): `if self.customer_id: env["CUSTOMER_ID"] = self.customer_id`.
- At call site near `sdk_client.py:2539`: read `_session_extra_context.get("customer_id")`, pass into `ValorAgent(...)`, pass `substitutions={"customer_id": ...}` to `load_persona_prompt`.

### 4. Wire resolver call into bridge
- **Task ID**: build-bridge-wire
- **Depends On**: build-resolver-core, build-coalescing, build-persona-env
- **Validates**: `tests/integration/test_email_customer_routing.py` (create) — inject fake IMAP message; assert customer_id in extra_context, persona is customer-service, CUSTOMER_ID env var present, coalescing works across two sequential messages, failure path attaches `valor-retry`.
- **Assigned To**: bridge-wire-builder
- **Agent Type**: builder
- **Parallel**: false
- In `_process_inbound_email`, after `find_project_for_email`, check `project.get("customer_resolver")`. If absent, keep existing flow unchanged.
- If present, call `resolve_customer(from_addr, project, imap_conn, imap_uid)`.
- None → log and return (message already `\Seen`).
- Non-None: add `customer_id` to `extra_context_overrides`, set persona to `customer-service` via existing mechanism at `email_bridge.py:435-437`, run coalescing lookup (In-Reply-To → subject → new), enqueue.
- Adjust `_poll_imap`/`_fetch_unseen` to pass the open IMAP connection + UID through to `_process_inbound_email` so the failure-path label STORE can use it. Keep connection scoping tight — no leaks.

### 5. Security review
- **Task ID**: review-subproc-security
- **Depends On**: build-resolver-core, build-bridge-wire
- **Assigned To**: subproc-security
- **Agent Type**: security-reviewer
- **Parallel**: false
- Verify argv-form subprocess (no shell).
- Verify sender pre-validation regex is conservative.
- Verify timeout enforcement path (no leaked child processes).
- Verify environment scoping (minimal inherited env).
- Verify captured stdout is length-capped.
- Pass/fail report. Must be pass before merge.

### 6. Integration tests
- **Task ID**: test-integration
- **Depends On**: build-resolver-core, build-coalescing, build-persona-env, build-bridge-wire
- **Assigned To**: integration-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Scenarios: (1) resolver returns ID → customer-service session spawned with env var; (2) resolver returns None → no session; (3) resolver times out → no session, `valor-retry` label attempt, failure counter incremented; (4) In-Reply-To coalescing (existing behavior still works); (5) subject-line coalescing (new behavior); (6) persona prompt substitution.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-bridge-wire
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/customer-resolver.md`.
- Update `docs/features/email-bridge.md`, `docs/features/README.md`.
- Add inline docstrings on new public callables.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: test-integration, document-feature, review-subproc-security
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every command in the Verification table.
- Walk Success Criteria checkboxes; mark each with evidence.
- Confirm Telegram `bridge_watchdog` behavior untouched.
- Report pass/fail.

### 9. File followup issue for watchdog + reflection + retry
- **Task ID**: file-followup
- **Depends On**: validate-all
- **Assigned To**: docs-writer (or lead)
- **Agent Type**: documentarian
- **Parallel**: false
- Open a GitHub issue (`bridge` label) for: `email_bridge_watchdog` launchd service; `resolver_health` reflection; active retry loop that consumes Gmail `label:valor-retry` + drains `resolver:failures:*`. Reference issue #1093 + this plan in the body.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Resolver module exports | `python -c "from bridge.routing import resolve_customer, invalidate_customer_cache"` | exit code 0 |
| Customer-service persona file exists | `test -f config/personas/customer-service.md` | exit code 0 |
| CUSTOMER_ID env injection wired | `grep -n 'CUSTOMER_ID' agent/sdk_client.py` | output contains `env["CUSTOMER_ID"]` |
| Coalescing helpers importable | `python -c "from bridge.email_bridge import normalize_subject, find_coalescing_session_id"` (adjust if placed in `bridge/subject_utils.py`) | exit code 0 |
| Feature doc exists | `test -f docs/features/customer-resolver.md` | exit code 0 |
| Feature doc index updated | `grep -q 'customer-resolver' docs/features/README.md` | exit code 0 |
| Telegram watchdog unchanged | `git diff HEAD~1 monitoring/bridge_watchdog.py` | empty output |

## Critique Results

<!-- Populated by /do-plan-critique (war room) 2026-04-21. Verdict: READY TO BUILD (with concerns). 0 blockers, 7 concerns, 2 nits. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | `find_coalescing_session_id` does an unbounded Python-side scan of non-terminal `AgentSession` rows per message — grows O(N) with active project history. | Revision pass: embed bounded-query guidance into Task 2 + Technical Approach. | Add `status__in=NON_TERMINAL_STATUSES` and `created_at__gte=time.time() - 48*3600` to the Popoto filter. Optionally maintain a Redis hash `coalesce:{project_key}:{customer_id}` set during enqueue so hot-path lookup is O(1); fall back to DB scan only on miss. |
| CONCERN | Skeptic | Plan assumes `_session_extra_context` is in scope at `sdk_client.py:2542` but spike-3 only verified the env-var injection site (line 1034), not the persona-resolution site. | Revision pass: add "verify variable name in `_execute_agent_session`" note to Task 3. | `sdk_client.py:2510` already uses `_session_extra_context.get("transport")` in the email persona fall-through, confirming the variable is the extra_context dict at that scope. Builder should grep `_session_extra_context` in `_execute_agent_session` and pass the same dict through to `ValorAgent(customer_id=...)`. Do not rename or re-derive. |
| CONCERN | Operator | Fail-closed posture with deferred watchdog AND deferred alerting = silent customer-loss surface for a "high-stakes" role. `resolver:failures:*` has no v1 consumer. | Revision pass: add a minimal alerting rung OR explicit operator-check SOP in feature doc. | Reuse `monitoring/bridge_watchdog.py` Telegram-DM plumbing from `_process_inbound_email`: on failure, if `r.incr("resolver:failures:{project_key}")` crosses a threshold within the window, emit one `logger.critical` line AND one Telegram DM to the operator (gated by dedup key so we alert once per hour max). Alternative: add a `logger.critical(...)` with `label:valor-retry applied for {from_addr}` so it tails in `logs/bridge.log` — and document the `redis-cli GET resolver:failures:*` + `label:valor-retry` daily check in `docs/features/customer-resolver.md`. |
| CONCERN | Operator | `\Seen` is applied at `email_bridge.py:822` BEFORE resolver dispatch. A crash between `\Seen` and the failure-path label-STORE leaves the message `\Seen`-without-`valor-retry` — unrecoverable by the future retry mechanism. | Revision pass: document the failure mode as a known caveat in the plan and feature doc, OR move `\Seen` to after resolver dispatch. | Option A (safer): defer `\Seen` marking in `_fetch_unseen` until after `_process_inbound_email` acks success — single-machine assumption means no concurrent-poll risk. This widens the Task 4 scope. Option B (ship as-is): accept the gap, explicitly list it in "Failure Path Test Strategy" and `docs/features/customer-resolver.md` under "Known failure modes," note that the followup watchdog must detect it via `resolver:failures:*` stall + IMAP cross-check, not via `label:valor-retry` scan alone. |
| CONCERN | Adversary | Sender pre-validation regex is named but not specified. Malformed-but-argv-safe addresses could still poison cache keys, Redis key shape, and `email:msgid:*` lookups. | Revision pass: name the exact regex in Task 1 and Technical Approach. | `bridge/routing.py`: `_SENDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,62}@[A-Za-z0-9][A-Za-z0-9.-]{0,252}\.[A-Za-z]{2,}$")`. On mismatch: return None, log WARN, do NOT increment `resolver:failures:*` (malformed input ≠ resolver failure). Same pattern validates cache-key safety and `email:msgid:*` lookup safety. |
| CONCERN | Adversary | Output sanitization regex `[A-Za-z0-9_\-:.]{1,128}$` lacks `^` anchor and multi-line handling — a resolver that prints `"<html>foo</html>\ncust_42\n"` would match `cust_42` and be silently accepted. | Revision pass: replace with explicit sequence + `re.fullmatch`. | Exact sequence in `bridge/routing.py::resolve_customer`: (1) `cleaned = stdout.decode("utf-8", errors="replace").strip()`; (2) if `"\n" in cleaned or "\r" in cleaned` → fail-closed (malformed); (3) `if not re.fullmatch(r"[A-Za-z0-9_\-:.]{1,128}", cleaned)` → fail-closed; (4) empty → None; else → return cleaned. Add unit test in `tests/unit/test_customer_resolver.py`: resolver prints `"warning: x\ncust_1\n"` → treated as malformed, not silent accept. |
| CONCERN | Adversary | Subject-line coalescing has no time-bound; a `dormant`/`abandoned` session from months ago with a matching normalized subject resurrects unrelatedly. | Revision pass: add `COALESCE_MAX_AGE_SECONDS` bound to Task 2 rules. | Module constant `COALESCE_MAX_AGE_SECONDS = 48 * 3600` next to `normalize_subject`. Include `created_at__gte=time.time() - COALESCE_MAX_AGE_SECONDS` in the Popoto filter. Log bound in the INFO line: `"[email] coalescing matched session={sid} age={hrs}h limit=48h"`. Also addresses the unbounded-scan concern above. |
| CONCERN | User | All 10 success criteria are internal (imports, env vars, grep, tests) — no end-to-end customer-observable acceptance. Reviewer cannot confirm "v1 works" from this plan alone. | Revision pass: add an end-to-end acceptance to Task 8 and to Success Criteria. | In Task 8 (`validate-all`): inject a fake parsed email through `_process_inbound_email` with a stub resolver returning `"test-customer-42"`; assert (a) `AgentSession.extra_context["customer_id"] == "test-customer-42"`, (b) resolved persona is `customer-service`, (c) a reply message is drafted (content-level, not byte-exact). Lift this check into the Success Criteria bullet list. Task 6's integration test already covers most of this — promote it into the explicit acceptance path. |
| NIT | Simplifier | `invalidate_customer_cache` is exported but has no v1 caller inside valor-ai; "external CRM changes" happen outside this repo's reach. | No revision needed — builder may delete and document the Redis key format in the feature doc instead. | — |
| NIT | Simplifier | Four builder roles for ~5 small functions across 4 files; handoff overhead likely exceeds work. | No revision needed — builder may collapse to two (non-bridge code / bridge wire-up) at their discretion. | — |

---

## Open Questions

All initial open questions resolved in planning discussion with Tom (2026-04-21):

1. **Single-machine-per-project resolver execution** — Tom's operational responsibility (not architectural). Noted in the plan's "Single-machine-per-project operation" subsection.
2. **Customer-service persona tone** — Real overlay lives in `~/Desktop/Valor/personas/customer-service.md` + Cuttlefish repo skills. In-repo `config/personas/customer-service.md` is a minimal fallback, not the brand voice.
3. **Gmail labels** — Problem-first approach. Keep `\Seen` + `UNSEEN FROM <senders>` as the primary gate (Gmail's own inbox classification + operator-maintained empty inbox does most heavy lifting). Single `valor-retry` label only on resolver failure (archival, so a future retry mechanism can pick it up). No `customer` / `not-customer` / `valor-processed` labels — thread-inherited labels would make per-message tagging misleading.
4. **Watchdog** — Out of scope for this plan. Keep `resolver:failures:*` counter as a breadcrumb for a followup issue; ensure no collateral damage to the existing Telegram `bridge_watchdog`.
5. **Inbox migration** — Not needed; inbox is maintained empty.

No open questions remaining. Ready for `/do-plan-critique`.
