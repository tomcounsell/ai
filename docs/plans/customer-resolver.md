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
2. **Robust routing** — no duplicated work when the same customer sends multiple emails; no agent sessions spun up for strangers or spam; resilient to resolver failures and IMAP/SMTP hiccups with the same health-check + recovery quality Telegram sessions already enjoy.
3. **Customer-aware agent behavior** — the agent session must know which customer it is serving so the target repo (Cuttlefish) can load the customer's profile, pending tasks, and history from its own CRM.

**Current behavior:**
- `bridge/email_bridge.py` fetches UNSEEN mail from senders matched against a static `email.contacts` list in `projects.json`. The list is baked in at deploy time, cannot scale past IMAP FROM-query limits, and gives no signal beyond boolean allow/deny.
- No identity is carried into the session — the agent only sees `from_addr`.
- No per-customer coalescing: two fresh (non–In-Reply-To) emails from the same customer spawn two parallel sessions that can race and send conflicting replies.
- `\Seen` flag is the only multi-machine dedup gate; it flips when a human opens the mail on their phone, creating false-negatives.
- No email-bridge-specific watchdog; the existing `monitoring/bridge_watchdog.py` is Telegram-focused.

**Desired outcome:**
- Any project can declare a `customer_resolver` hook (subprocess or importlib callable). The hook takes a sender identifier and returns `customer_id | None`.
- If the resolver says "not a customer," the email is dropped — cleanly, with observability. No session, no cost.
- If the resolver says "this is customer X," the bridge spawns a customer-service session in the target project's working directory with `customer_id` available both in the system prompt and as a `CUSTOMER_ID` environment variable. The target repo's CLAUDE.md and skills do the rest.
- Subject-line and In-Reply-To continuation both coalesce subsequent mail into the same `session_id` — no parallel sessions from the same customer.
- Resolver failures fail-closed (drop the mail) but are detected quickly by a dedicated watchdog and a `resolver_health` reflection that escalates to Tom via Telegram.

## Freshness Check

**Baseline commit:** `d3e862ba` (`main`)
**Issue filed at:** 2026-04-21T05:40:35Z (~2.5h before plan)
**Disposition:** Unchanged

**File:line references re-verified:**
- `config/enums.py:31` — `PersonaType.CUSTOMER_SERVICE` — still holds
- `bridge/telegram_bridge.py:588` — DM whitelist comment block (variable `DM_WHITELIST` on line 592) — minor drift (1-line shift) but claim holds
- `bridge/routing.py:162` — `build_email_to_project_map` — still holds
- `bridge/email_bridge.py:498-517` — IMAP FROM OR-tree filter — still holds
- `bridge/email_bridge.py:445-459` — In-Reply-To thread continuation — still holds
- `bridge/email_bridge.py:568` — `\Seen` mark-before-fetch — still holds
- `agent/sdk_client.py:2539-2548` — `CUSTOMER_SERVICE` persona with teammate fallback — still holds
- `agent/sdk_client.py:613-668` — `load_persona_prompt()` — still holds
- `agent/sdk_client.py:599-610` — overlay path resolution — still holds
- `agent/reflection_scheduler.py:193` — `_resolve_callable` importlib dispatch — still holds

**Commits on main since issue was filed (touching referenced files):** none. `git log --since="2026-04-21T05:40:35Z" --` returned empty for all referenced files.

**Active plans in `docs/plans/` overlapping this area:** none. Adjacent plans (`reliable-pm-final-delivery.md`, `pm-persona-hardening.md`) touch orchestration, not email routing.

## Prior Art

- **PR #908 (#847)** — *email bridge — IMAP/SMTP secondary transport*: established the current architecture (IMAP poll → `_process_inbound_email` → `enqueue_agent_session`, SMTP outbound with dead-letter, `\Seen` gating, `email:msgid:*` Redis thread map). This plan extends that skeleton; does not replace it.
- **PR #956 (#946)** — *register EmailOutputHandler for domain-routed projects*: confirmed the routing layer is already domain-aware and can carry project-specific config into session creation. Supports the "per-project resolver" shape we propose.
- **PR #1094 (#1067)** — *valor-email CLI*: established the outbox relay pattern on the outbound side. Not directly touched by this plan but useful context for "customer-service replies go through the existing pipeline."
- **PR #909** — *SDLC stage model selection + hard-PATCH builder resume*: established the `extra_context` carry-through pattern we use to pipe `customer_id` from bridge → session → subprocess env.
- **Issue #743 / PR on externalized steering** — `queued_steering_messages` inbox. Considered for coalescing, but Spike 2 confirmed the bridge should use the existing supersede-and-enqueue path (same pattern as In-Reply-To continuation) rather than steering, so race window and terminal-status complications are avoided.
- **Issue #998** — *Telegram MessageEdited events — steer or respawn*: parallel prior art for "second inbound from same thread." Followed the steer path for Telegram; here we choose the supersede path for email, which is already the email-bridge convention.

No prior attempt to build a dynamic customer resolver exists. This is greenfield in valor-ai.

## Research

**Queries used:**
1. `Python imaplib X-GM-RAW Gmail label search 2026`
2. `uv run inline script metadata PEP 723 subprocess external CLI pattern`
3. `IMAP session coalescing idempotency distributed consumers prevent duplicate processing`

**Key findings:**
- **Python's `imaplib` supports Gmail's IMAP extensions natively** (source: [Gmail IMAP Extensions](https://developers.google.com/workspace/gmail/imap/imap-extensions), [IMAPClient docs](https://imapclient.readthedocs.io/en/2.1.0/_modules/imapclient/imapclient.html)). Call shapes we'll use: `conn.uid('search', None, 'X-GM-RAW', '"..."')`, `conn.uid('fetch', uid, '(X-GM-LABELS X-GM-MSGID)')`, `conn.uid('store', uid, '+X-GM-LABELS', '("valor-processed")')`. Label names with spaces require quoting; Gmail auto-creates labels on first STORE. Capability inspection via `conn.capability()` will confirm `X-GM-EXT-1` before we rely on it. Memory saved.
- **PEP 723 inline script metadata + `uv run`** (source: [PEP 723](https://peps.python.org/pep-0723/), [uv scripts guide](https://docs.astral.sh/uv/guides/scripts/)): lets resolver scripts in external repos (like Cuttlefish) carry their own deps via `#!/usr/bin/env -S uv run` shebang + `# /// script` TOML block, without polluting valor-ai's venv. Subsequent runs hit uv's cached venv, so per-message startup cost is low. Memory saved.
- **Idempotent consumer pattern** (source: [Microservices Pattern: Idempotent Consumer](https://microservices.io/patterns/communication-style/idempotent-consumer.html), [Architecture Weekly: Deduplication](https://www.architecture-weekly.com/p/deduplication-in-distributed-systems)): canonical approach is storing a per-message idempotency key atomically with the processing outcome. Our equivalents are already in place: `email:msgid:{Message-ID}` Redis map (48h TTL) + the new Gmail `valor-processed` custom label (persistent). Between the two, a multi-machine poll is safe against duplicate processing.

## Spike Results

### spike-1: Gmail X-GM-RAW works from our imaplib connection
- **Assumption**: `imaplib.IMAP4_SSL` in Python 3.13 supports `X-GM-RAW`, `X-GM-LABELS`, `X-GM-MSGID` via `conn.uid(...)` varargs.
- **Method**: code-read (`bridge/email_bridge.py:520-580`) + Python stdlib behavior.
- **Finding**: **Confirmed, high confidence.** Current code already uses `conn.uid('search', ...)` and `conn.uid('store', ..., '+FLAGS', '\\Seen')` (line 568) with the same varargs pattern. Adding X-GM-RAW / X-GM-LABELS is mechanically identical. Gotcha: label names with spaces must be quoted inside parens, e.g., `'("valor processed")'`. Gmail auto-creates labels on first STORE.
- **Impact on plan**: No architectural change. Add a one-time `conn.capability()` check at connection establishment to fail loudly if `X-GM-EXT-1` is missing (e.g., if someone points the bridge at a non-Gmail IMAP server).

### spike-2: `queued_steering_messages` vs supersede-and-enqueue for coalescing
- **Assumption**: Bridge-layer coalescing should call `steer_session()` to append to `queued_steering_messages`.
- **Method**: code-read (`agent/session_executor.py:422-470`, `models/agent_session.py:1371-1390`, `bridge/email_bridge.py:443-475`).
- **Finding**: **Refuted.** `push_steering_message()` is a read-modify-write on a Popoto `ListField` with no compare-and-swap — a race window exists. More importantly, the existing In-Reply-To continuation path does NOT use steering: it looks up `existing_session_id` via `email:msgid:{in_reply_to}` and calls `enqueue_agent_session(session_id=existing_session_id, ...)`. `_push_agent_session()` then marks old records "superseded" and creates a new pending AgentSession with the same `session_id`. Worker picks it up fresh.
- **Impact on plan**: **Use the same supersede-and-enqueue pattern for subject-line coalescing.** Do NOT touch `queued_steering_messages` for this feature. This also means we avoid the race window entirely — the bridge just needs to resolve "is there an existing session_id for this customer + normalized subject?" and pass it to `enqueue_agent_session`.

### spike-3: persona prompt substitution + env var injection
- **Assumption**: `load_persona_prompt()` already supports `{placeholder}` substitution; `CLAUDE_CODE_TASK_LIST_ID` injection point is clear.
- **Method**: code-read (`agent/sdk_client.py:599-668`, `sdk_client.py:957-1035`, `sdk_client.py:2495-2560`).
- **Finding**: **Half-refuted, half-confirmed.** No substitution mechanism today — `load_persona_prompt()` simply concatenates segments + overlay. **We must add substitution.** Env var injection IS clear: `ValorAgent._create_options()` at `sdk_client.py:1034-1035` sets `env["CLAUDE_CODE_TASK_LIST_ID"] = self.task_list_id`. Adding `env["CUSTOMER_ID"] = self.customer_id` is mechanically identical. `ValorAgent.__init__` at line 957 accepts `task_list_id`; we add `customer_id` there. Carry-through via `AgentSession.extra_context["customer_id"]` is the right channel (already used for `email_message_id`, `email_subject`, etc.).
- **Impact on plan**: Add a substitution step inside `load_persona_prompt()` that triggers when persona == "customer-service" (or more generically: apply `.format_map()` with a context dict when one is provided). Add `customer_id` parameter through `ValorAgent.__init__` and `_create_options()`. Read from `extra_context` at persona-resolution time (`sdk_client.py:2539` area).

### spike-4: subject-line coalescing — normalization, lookup, matching
- **Assumption**: We can match normalized subject + project_key + customer_id to existing non-terminal sessions, with acceptable false-positive rate.
- **Method**: code-read (`bridge/email_bridge.py:443-492`, `bridge/telegram_bridge.py` for root-resolution pattern, `models/agent_session.py` status field, existing `email:msgid:*` schema).
- **Finding**: **Confirmed with caveats.** No existing `normalize_subject()` utility — build one. Recommended rules: strip leading `^(Re|Fwd|Fw|Aw|AW|Antw|RE|FW|FWD)[\[\]\d\s]*:\s*` (repeatedly), strip `[ticket-NNN]`-style bracket tags, collapse whitespace, lowercase. **Lookup mechanism: DB query against `AgentSession` (not Redis).** Reason: Redis TTL management is extra complexity and the DB already carries `extra_context["email_subject"]` plus status. Query shape:
  ```python
  AgentSession.query.filter(
      project_key=project_key,
      status__in=NON_TERMINAL_STATUSES,
      ...
  ).order_by('-created_at').first()
  ```
  plus in-Python filter on `extra_context.get("customer_id") == customer_id and normalize_subject(extra_context.get("email_subject","")) == normalized`. **Status window: non-terminal only** (pending, running, active, dormant, waiting_for_children, paused, paused_circuit, superseded — per `docs/features/session-lifecycle.md`). **Edge cases:** empty subject → don't coalesce (return None); very generic subjects ("Hi") → still coalesce but scope is already narrowed by customer_id, so false-positive blast radius is a single customer's own conversations (acceptable); subject changes mid-thread → In-Reply-To path handles that, subject-line path is a fallback for when mailers drop In-Reply-To.
- **Impact on plan**: Build `normalize_subject()` as a small utility in `bridge/email_bridge.py` or a new `bridge/subject_utils.py`. Add `find_coalescing_session(project_key, customer_id, normalized_subject)` that returns `session_id | None`. Order of precedence in `_process_inbound_email`: (1) In-Reply-To lookup (existing), (2) subject-line coalescing (new), (3) new session.

## Data Flow

```
Gmail IMAP INBOX
      │
      ▼
[1] _email_inbox_loop (email_bridge.py:584)
      │  poll every IMAP_POLL_INTERVAL; update email:last_poll_ts
      ▼
[2] _poll_imap (email_bridge.py:520)
      │  X-GM-RAW "-label:valor-processed in:inbox"
      │  (replaces current UNSEEN + FROM filter)
      │  fetch message + X-GM-MSGID + (RFC822)
      ▼
[3] parse_email_message (email_bridge.py:150)
      │  extract from_addr, subject, body, message_id, in_reply_to
      ▼
[4] _process_inbound_email (email_bridge.py:401)
      │  find_project_for_email(from_addr) → project_config
      │
      ├─ has customer_resolver? ─── no ──► existing static contacts path (unchanged)
      │
      ▼ yes
[5] resolve_customer (bridge/routing.py, NEW)
      │  Redis GET customer_resolver:{project_key}:{from_addr}
      │  cache miss → dispatch subprocess or importlib callable
      │  result "" = not customer, non-empty = customer_id
      │  cache SET with project TTL (default 300s)
      │
      ├─ None ──► apply X-GM-LABELS "not-customer" + "valor-processed", return (drop)
      │
      ▼ customer_id
[6] find_coalescing_session (NEW)
      │  precedence:
      │   (a) In-Reply-To → Redis email:msgid:{id} (existing)
      │   (b) subject-line → DB query + normalize_subject match
      │   (c) neither → new session_id
      │
      ▼
[7] enqueue_agent_session (agent/agent_session_queue.py)
      │  extra_context_overrides = {
      │      "transport": "email",
      │      "email_message_id": message_id,
      │      "email_from": from_addr,
      │      "email_subject": subject,
      │      "customer_id": customer_id,  # NEW
      │  }
      │  session_type = TEAMMATE (customer-service maps to TEAMMATE)
      │
      ▼
[8] apply X-GM-LABELS "customer" + "valor-processed" (NEW)
      │
      ▼
[9] Worker picks up AgentSession (status=pending)
      │
      ▼
[10] sdk_client._execute_agent_session
      │  persona resolution (sdk_client.py:2500):
      │   email + persona=customer-service → load_persona_prompt("customer-service")
      │  substitution (NEW): .format_map({"customer_id": customer_id})
      │  ValorAgent(customer_id=customer_id, ...)
      │  _create_options sets env["CUSTOMER_ID"] = customer_id (NEW, line 1034-ish)
      │
      ▼
[11] Claude Code subprocess in working_directory (e.g., ~/src/cuttlefish)
      │  Loads its own CLAUDE.md, .claude/skills/
      │  Uses CUSTOMER_ID env var + prompt context to run repo-local customer lookup
      │
      ▼
[12] Agent produces reply → EmailOutputHandler.send → SMTP
      │  SMTP dead-letter on failure (existing)
      │
      ▼
[13] Outbound Message-ID stored in email:msgid:{id} → session_id (existing, email_bridge.py:348)
```

## Architectural Impact

- **New dependencies**: None in valor-ai. External resolvers (Cuttlefish) carry their own deps via `uv run` / PEP 723.
- **Interface changes**:
  - `ValorAgent.__init__` gains `customer_id: str | None = None` parameter.
  - `load_persona_prompt` gains an optional `substitutions: dict[str, str] | None = None` parameter (backward-compatible).
  - `projects.json` gains a `customer_resolver` block per project (backward-compatible; optional).
  - `bridge/routing.py` gains `resolve_customer()`.
  - New module `bridge/subject_utils.py` (or inline in `email_bridge.py` if we stay small).
- **Coupling**: Decreases coupling between valor-ai and the customer CRM — only the resolver contract is shared. Valor-ai gains zero Cuttlefish-specific knowledge.
- **Data ownership**: Customer identity remains owned by Cuttlefish (or whichever target repo declares the resolver). Valor-ai caches resolver results but never persists customer data.
- **Reversibility**: High. Projects without `customer_resolver` keep current static behavior. The Gmail label gate is additive; if we roll back, processed messages just stay labeled (harmless). Removing the `CUSTOMER_ID` env var on rollback is a no-op for sessions that never referenced it.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM + code reviewer

**Interactions:**
- PM check-ins: 1-2 (scope alignment at critique; post-build review)
- Review rounds: 1-2 (code review for bridge changes + security review for subprocess dispatch)

The cost here is in touching several bridge and session-layer files, adding a new subprocess dispatch surface, and wiring Gmail IMAP extensions. None of the individual changes are large; the integration is what makes this Medium rather than Small.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Resolver cache, `email:last_poll_ts`, failure counter, `email:msgid:*` map |
| `IMAP_HOST` / `IMAP_USER` / `IMAP_PASSWORD` in `.env` | `python -c "from dotenv import dotenv_values; c = dotenv_values('.env'); assert all(c.get(k) for k in ('IMAP_HOST','IMAP_USER','IMAP_PASSWORD'))"` | Email bridge connection |
| Gmail IMAP X-GM-EXT-1 enabled | `python -c "import imaplib, os; c=imaplib.IMAP4_SSL(os.environ['IMAP_HOST'], 993); c.login(os.environ['IMAP_USER'], os.environ['IMAP_PASSWORD']); assert 'X-GM-EXT-1' in c.capabilities"` | Label-based gating requires Gmail extensions |
| `uv` installed on host | `uv --version` | For subprocess-form resolvers using PEP 723 inline deps |
| `customer-service` PersonaType registered | `python -c "from config.enums import PersonaType; assert PersonaType.CUSTOMER_SERVICE.value == 'customer-service'"` | Persona resolution path exists (`config/enums.py:31`) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/customer-resolver.md`

## Solution

### Key Elements

- **`resolve_customer(sender, project_config)`** in `bridge/routing.py`: generic per-message dispatcher. Reads Redis cache; on miss, runs subprocess (`command` form) or importlib callable (`callable` form); caches result with per-project TTL. Stores `""` for cached None to distinguish from not-yet-cached. Increments `resolver:failures:{project_key}` on error; fail-closed (returns None).
- **Gmail label gating** in `bridge/email_bridge.py`: replace `UNSEEN` + `\Seen` with `X-GM-RAW "-label:valor-processed in:inbox"` + `X-GM-LABELS` STORE. Applied labels: `valor-processed` always, plus one of `customer` / `not-customer` / `resolver-failed`.
- **Subject-line coalescing** in `bridge/email_bridge.py` (via a new `normalize_subject` + `find_coalescing_session_id`): after In-Reply-To lookup misses, search active AgentSessions for a match on `(project_key, customer_id, normalized_subject)`. If found, reuse that `session_id`.
- **Customer-service persona** at `config/personas/customer-service.md`: new thin overlay with `{customer_id}` placeholder and a clear escalation-to-Tom instruction.
- **Substitution + env var injection** in `agent/sdk_client.py`: extend `load_persona_prompt()` with optional `substitutions`; plumb `customer_id` through `ValorAgent.__init__` → `_create_options` → `env["CUSTOMER_ID"]`.
- **`email_bridge_watchdog`** at `monitoring/email_bridge_watchdog.py` + `~/Library/LaunchAgents/com.valor.email-bridge-watchdog.plist`: sibling to existing `bridge_watchdog`. Checks `email:last_poll_ts`, `resolver:failures:*`, SMTP dead-letter depth, IMAP connectivity. Shares crash-tracker + Telegram-alert infrastructure.
- **`resolver_health` reflection** at `reflections/resolver_health.py`: periodic check; on sustained failures, attempts recovery (clear stuck cache entries), escalates to Tom if unresolved past threshold.

### Flow

Customer emails Cuttlefish →
  Gmail inbox (spam pre-filtered by Gmail) →
  Valor email bridge polls unprocessed mail →
  resolver hits Cuttlefish API (cached in Redis) →
  `customer_id` resolved →
  bridge checks for existing session (In-Reply-To, then subject-line) →
  enqueues session (new or resumed) with `customer_id` in `extra_context` →
  message labeled `customer` + `valor-processed` →
  worker spawns Claude Code in `~/src/cuttlefish` with `CUSTOMER_ID` env var →
  agent loads customer via Cuttlefish's own tools →
  agent drafts reply →
  SMTP sends (dead-letter on failure) →
  outbound Message-ID stored for future thread continuation

### Technical Approach

- **Resolver dispatch** lives in `bridge/routing.py` and follows the importlib-callable pattern at `agent/reflection_scheduler.py:193` for the `callable` form. Subprocess form uses `asyncio.create_subprocess_exec` with a timeout (default 5s) and parses stdout (trim whitespace; empty = None; non-empty = customer_id).
- **Cache key**: `customer_resolver:{project_key}:{sender_id}`. Cache TTL from `customer_resolver.cache_ttl_seconds` (default 300). Cached-None encoded as empty string; cached-customer as the ID directly.
- **Failure counter**: `resolver:failures:{project_key}` INCR on any resolver exception, timeout, or malformed output. Reset on any successful resolution. Watchdog reads this.
- **Gmail labels**:
  - Poll query: `X-GM-RAW "-label:valor-processed in:inbox"` (no UNSEEN — we rely on the custom label, not `\Seen`).
  - At startup (or first poll), call `conn.capability()` and abort with a loud error if `X-GM-EXT-1` is missing.
  - Labels applied after resolution (even on resolver failure): `valor-processed` always; one of `customer` / `not-customer` / `resolver-failed` depending on outcome.
  - Apply label BEFORE enqueue so a crash between label-apply and enqueue produces observable state (labeled but not processed → operator can see and replay).
- **Subject-line coalescing**:
  - `normalize_subject(s)`: strip leading `Re:`/`Fwd:`/`Fw:`/`Aw:` prefixes with optional whitespace and brackets, case-insensitive, repeatedly; strip `[ticket-NNN]`-style leading bracket tags; collapse consecutive whitespace; `.strip().lower()`.
  - `find_coalescing_session_id(project_key, customer_id, normalized_subject) -> str | None`: query `AgentSession.query.filter(project_key=project_key)` for non-terminal sessions, then Python-side filter by `extra_context.get("customer_id") == customer_id` and `normalize_subject(extra_context.get("email_subject","")) == normalized_subject`; return most recently created. If `normalized_subject` is empty, return None (never coalesce on empty).
  - Precedence: (1) In-Reply-To Redis lookup, (2) subject-line coalescing, (3) new session_id. Log which path was taken at INFO.
- **Persona substitution**:
  - Extend `load_persona_prompt(persona, substitutions=None)`. After assembling segments + overlay, if `substitutions` is provided, do `content = content.format_map(_SafeFormatDict(substitutions))` where `_SafeFormatDict` returns `{key}` for missing keys (so non-placeholder braces in the prompt don't crash).
  - At `sdk_client.py:2542`, pass `substitutions={"customer_id": _session_extra_context.get("customer_id", "unknown")}`.
- **Env var injection**:
  - `ValorAgent.__init__`: add `customer_id: str | None = None` (line 957-area).
  - `_create_options` (line 1034-area): `if self.customer_id: env["CUSTOMER_ID"] = self.customer_id`.
  - Caller in `_execute_agent_session`: read `_session_extra_context.get("customer_id")` and pass into `ValorAgent(...)`.
- **Watchdog**:
  - `monitoring/email_bridge_watchdog.py` structured like `monitoring/bridge_watchdog.py`. Checks: `email:last_poll_ts` age (alert if > 2 × `IMAP_POLL_INTERVAL`), `resolver:failures:*` counters (alert if any counter > threshold, e.g., 10), SMTP dead-letter queue depth (alert if > 50), IMAP connection reachable. Shares `monitoring/crash_tracker.py` for event logging and `monitoring/alerts.py` for Telegram escalation to Tom.
  - Launchd plist: `~/Library/LaunchAgents/com.valor.email-bridge-watchdog.plist`. 60s interval. Installed via an entry in `./scripts/valor-service.sh` (install alongside existing watchdog).
- **Reflection**:
  - `reflections/resolver_health.py`: registered in `reflections/registry` (follow existing pattern). Runs every 15 minutes. Reads `resolver:failures:*`, inspects `email:last_poll_ts`. If failure rate exceeds threshold for a project AND last poll is fresh, tries self-heal: clear stuck cache entries, reset counter, log. If unrecoverable past threshold (e.g., 2 consecutive reflection cycles with elevated failures), escalates to Tom via Telegram with a diagnostic summary.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `except Exception` blocks in `bridge/email_bridge.py` (existing: around lines 449, 470, 494, 607, 618); new code must not add silent-swallow blocks. Every new `except` path must increment an observable counter or log at WARN/ERROR.
- [ ] `resolve_customer` must log at WARN on timeout, ERROR on unexpected exception; always increments `resolver:failures:{project_key}`; returns None (fail-closed).

### Empty/Invalid Input Handling
- [ ] Resolver returning empty stdout → treated as None (documented). Test: subprocess that prints nothing.
- [ ] Resolver returning whitespace-only → treated as None. Test: subprocess that prints `"  \n"`.
- [ ] Resolver returning non-ASCII / multi-line → take first line, strip, use as customer_id only if it matches a conservative regex (e.g., `[A-Za-z0-9_\-:.]{1,128}$`). Reject otherwise, log WARN, fail-closed. Test: subprocess that prints `"<html>foo</html>"`.
- [ ] `normalize_subject("")` → empty string → coalescing returns None (never coalesces on empty).
- [ ] In-Reply-To header missing or malformed → Redis lookup returns None → falls through to subject-line path.

### Error State Rendering
- [ ] `resolver-failed` label gets applied when the resolver errors, giving operators a Gmail-searchable view.
- [ ] Watchdog Telegram alert includes: project_key, failure count, last poll age, which recovery steps were attempted.

## Test Impact

No existing tests assert on resolver behavior (the feature is greenfield), but several existing tests touch the email bridge and need adjustment because we change the IMAP poll contract and add a new enqueue field.

- [ ] `tests/unit/test_email_bridge.py` (if present — check for actual filename; may be under `tests/unit/test_email_*.py`) — UPDATE tests that assume `UNSEEN` + `\Seen` gating. Replace with `X-GM-RAW` + label-based gating assertions. Add a fake-capability test asserting we abort when `X-GM-EXT-1` is missing.
- [ ] Any test that constructs `extra_context` for an email session — UPDATE to tolerate the new `customer_id` key (tests should use `>=` semantics, not equality, where feasible).
- [ ] `tests/unit/test_sdk_client.py` (or whichever file tests `load_persona_prompt`) — UPDATE if any test asserts exact output; add a new test for the `substitutions` parameter.
- [ ] Any test asserting `_create_options` env dict — UPDATE to tolerate new `CUSTOMER_ID` key.

If the audit finds no affected tests (possible — email bridge has thin unit coverage per issue #936), state so explicitly during build. New tests go under `tests/unit/` for pure logic and `tests/integration/` for end-to-end resolver dispatch.

## Rabbit Holes

- **Building a Redis SET / bloom filter Tier 1 inside valor-ai.** The resolver contract is the seam — if Cuttlefish needs fast lookup, they build it behind their resolver. Don't leak that concern into valor-ai.
- **Gmail Apps Script delivery-time labeling.** Appealing but adds a second moving part hosted in Google-land. Skip.
- **Rewriting the email bridge to use `aioimaplib` / `imapclient`.** Tempting for async ergonomics, but the existing `imaplib` + `asyncio.to_thread` approach is working and well-understood. Stay the course.
- **Generic "message classifier" layer above the resolver.** Only one classification dimension exists (customer vs not). Don't abstract prematurely.
- **Building a full reflection framework for bridges.** `resolver_health` is the only bridge reflection we need now. Don't invent a registry/plugin system for one entry.
- **Rewriting the `bridge_watchdog` into a multi-bridge umbrella.** Pattern is "one watchdog per bridge." Keep `bridge_watchdog` (Telegram) and `email_bridge_watchdog` (email) as peers. If a third bridge arrives, add a third peer — don't try to pre-unify.

## Risks

### Risk 1: Resolver latency or flakiness stalls the IMAP poll loop
**Impact:** Every inbound message waits on a subprocess or callable. If Cuttlefish's resolver hangs, the poll stalls; no mail gets processed; customers are silently ignored.
**Mitigation:** Hard timeout on resolver dispatch (default 5s, configurable per project). Wrap subprocess in `asyncio.wait_for`. On timeout → fail-closed → drop message with `resolver-failed` label → increment failure counter. Watchdog catches sustained failures and escalates.

### Risk 2: Gmail custom-label quota or rate-limiting
**Impact:** If we STORE labels on every message at high volume, Gmail may rate-limit IMAP operations.
**Mitigation:** Monitor IMAP error codes; back off exponentially on rate-limit responses. Current `_poll_imap` already has exponential backoff (lines 594-641) — reuse the pattern. Gmail's IMAP limits are generous (15GB/day bandwidth, not per-op); at our scale (10s-100s of mails/day in v1) we are nowhere near limits. Watchdog flags rising error rates.

### Risk 3: Subject-line coalescing false-positive merges unrelated threads
**Impact:** Two independent questions from the same customer with accidentally-matching normalized subjects get merged into one session; the agent conflates context.
**Mitigation:** Coalescing is scoped to `(project_key, customer_id)` — false positives are confined to a single customer's own recent correspondence, which is the right semantic ("this is the same customer's conversation"). Empty normalized subject never coalesces. In-Reply-To is always preferred. Document expected behavior in the feature doc; log every coalescing decision at INFO for observability.

### Risk 4: Cached stale customer_id persists after a customer is removed from CRM
**Impact:** A former customer's email gets routed as a customer for up to `cache_ttl_seconds` after removal.
**Mitigation:** 300s default TTL is a reasonable bound for the customer-service domain. Projects can tune downward if sensitive. Also: expose a `bridge/routing.py::invalidate_customer_cache(project_key, sender_id)` helper for explicit invalidation, callable by hooks when the CRM changes — optional, future concern, not blocking v1.

### Risk 5: Subprocess resolver shells out to user-influenced input
**Impact:** If `command` template interpolates `{sender_id}` unsafely, a crafted email address could inject shell syntax.
**Mitigation:** Never pass the sender through a shell. Use `asyncio.create_subprocess_exec` (argv form, not `create_subprocess_shell`). Pre-validate sender (RFC 5321 reasonably conservative regex) before dispatch. Reject anything that doesn't look like an email.

## Race Conditions

### Race 1: Two bridge machines polling Gmail simultaneously both fetch the same message
**Location:** `bridge/email_bridge.py:_poll_imap` (lines 542-574)
**Trigger:** Both machines run `search` → both get the same UID set → both fetch → both enqueue a session for the same message.
**Data prerequisite:** A message visible to both machines (INBOX, not yet `valor-processed`).
**State prerequisite:** Both machines running the email bridge against the same Gmail account.
**Mitigation:** Apply `valor-processed` label BEFORE enqueue, in the same IMAP session as the fetch. First machine to STORE the label wins; the other's subsequent poll will see `-label:valor-processed` as false and skip. Gmail STORE is atomic per-message. A narrow window exists between fetch and STORE — if a machine crashes between fetch and label, the message will be re-fetched by another machine (desirable). The downstream `email:msgid:{message_id}` map prevents duplicate session enqueue even if both machines race past the label check.

### Race 2: Concurrent subject-line coalescing decisions
**Location:** `bridge/email_bridge.py` `find_coalescing_session_id` (new)
**Trigger:** Two messages with the same normalized subject from the same customer arrive and are processed in parallel by two bridge machines.
**Data prerequisite:** A shared `AgentSession` record or lack thereof.
**State prerequisite:** Redis available; both machines have fresh config.
**Mitigation:** Worst case, both machines decide to create a new session (both miss each other's still-pending record). Downstream effect: two sessions briefly — acceptable, because subject-line coalescing is an optimization, not a correctness requirement. In-Reply-To-based continuation on subsequent emails will re-converge them. If this proves too sloppy in practice, switch to Redis SET NX on `email:subject:{project}:{customer}:{hash}` pattern as a second-pass sync. Deferred until observed.

### Race 3: Resolver cache stampede
**Location:** `bridge/routing.py::resolve_customer` (new)
**Trigger:** Cache miss on a popular sender; many messages arrive in the same poll → every one dispatches the resolver in parallel.
**Data prerequisite:** Cold cache for a high-volume sender.
**State prerequisite:** Multiple concurrent calls to `resolve_customer` for the same sender.
**Mitigation:** Low risk in practice (per-sender rate is bounded by how many messages arrive per poll, typically 1-3). Acceptable as-is for v1. If it bites, add per-(project,sender) `asyncio.Lock` held across the dispatch + cache-write. Deferred.

## No-Gos (Out of Scope)

- **Tier 1 Redis-set customer-email cache inside valor-ai.** Moved to resolver-side (Cuttlefish decides if it needs one).
- **Gmail Apps Script / server-side labeling.** Deferred.
- **Customer-service routing for Telegram.** Email-only in v1. Telegram path is a separate plan.
- **Cuttlefish resolver script, `CLAUDE.md`, skills, customer tools.** Owned by Cuttlefish repo.
- **Automatic customer CRM writes from the agent.** The agent reads customer data via Cuttlefish's tools; writes are owned by those tools, not introduced here.
- **Per-message billing/metering of resolver subprocess calls.** Future concern.
- **Backward compatibility for projects using `dms.whitelist` or `email.contacts`.** These continue to work unchanged. The new `customer_resolver` block is purely additive; projects without it behave as they do today.
- **`\Seen` flag removal as a code path.** Keep `\Seen` flag management for projects that don't declare `customer_resolver` — only switch to `valor-processed` label gating for projects that do (or, if we find this too clever, move to label gating uniformly and cope with the read-on-phone scenario in a later cleanup).

## Update System

- **`scripts/remote-update.sh` / `.claude/skills/update/`**: Add a step to install the new `com.valor.email-bridge-watchdog.plist` launchd service on machines that run the email bridge. Skip on machines without the email bridge (match the existing Telegram-only gating pattern).
- **Config propagation**: `projects.json` lives in `~/Desktop/Valor/projects.json` (iCloud-synced). `customer_resolver` blocks are per-project and iCloud-propagate naturally; no sync step needed.
- **New env var**: None required in `.env` — `CUSTOMER_ID` is set per-subprocess by valor-ai, not a global.
- **Migration for existing installations**: Zero migration. Projects without `customer_resolver` keep current behavior; enabling it on a project is additive and can be staged (first enable the `customer-service` persona with no resolver to test the prompt path; then enable the resolver). Gmail label-gating is the biggest behavioral shift — first poll after deploy will see *all* UNSEEN inbox mail as "unprocessed" because none carry `valor-processed` yet. To avoid a flood: ship with a one-time migration that labels all currently-`\Seen` inbox mail as `valor-processed` before the first poll. The migration is a small Python script run at update time.

## Agent Integration

- **No new MCP servers required in valor-ai.** The feature is internal to the bridge + session layer.
- **No `.mcp.json` changes.** Cuttlefish's own MCP servers / tools live in its repo and are discovered by Claude Code at `working_directory`.
- **The bridge DOES directly call** `bridge/routing.py::resolve_customer` — no MCP detour.
- **Integration tests** verify: (1) bridge enqueues a customer-service session with `customer_id` in `extra_context` when resolver returns a non-empty ID; (2) spawned Claude Code subprocess receives `CUSTOMER_ID` env var; (3) persona prompt contains the substituted customer_id; (4) the agent can invoke tools that read `$CUSTOMER_ID` (verified with a stub tool in tests).
- **Cuttlefish-side agent integration** (its own CLAUDE.md, tools, skills to use `CUSTOMER_ID`) is out of scope here — tracked in the Cuttlefish repo.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/customer-resolver.md` covering: config schema, resolver interface contract (subprocess + callable forms), caching semantics, fail-closed behavior, Gmail label gating, subject-line coalescing rules, customer-service persona behavior, `CUSTOMER_ID` env var contract, watchdog and reflection.
- [ ] Add entry to `docs/features/README.md` index.
- [ ] Update `docs/features/email-bridge.md` to reference the new customer-resolver path and the Gmail-label gating change (replacing `\Seen` behavior).
- [ ] Update `docs/features/bridge-self-healing.md` to document the sibling `email_bridge_watchdog` + `resolver_health` reflection. Establish the "one watchdog per bridge" pattern explicitly.

### External Documentation Site
- N/A — this repo does not publish an external docs site.

### Inline Documentation
- [ ] Docstrings on `resolve_customer`, `normalize_subject`, `find_coalescing_session_id`, `email_bridge_watchdog.main`, `reflections/resolver_health.run`.
- [ ] One-line comment at the substitution site in `load_persona_prompt` calling out the `_SafeFormatDict` choice (so readers understand braces in prompts don't crash).

## Success Criteria

- [ ] `bridge/routing.py::resolve_customer(sender, project_config)` dispatches subprocess and callable forms, caches result, fails closed, and increments failure counter on error.
- [ ] A project with `customer_resolver` declared in `projects.json` sees inbound mail routed to `customer-service` persona with `customer_id` in session `extra_context`.
- [ ] Gmail `X-GM-RAW "-label:valor-processed in:inbox"` replaces `UNSEEN` gating for projects that have a resolver; `valor-processed` + (`customer`|`not-customer`|`resolver-failed`) labels are applied on every processed message.
- [ ] Two fresh emails from the same customer with matching normalized subjects resume the same `session_id` via subject-line coalescing.
- [ ] `load_persona_prompt("customer-service", substitutions={"customer_id": "..."})` returns a prompt with the placeholder substituted.
- [ ] Spawned Claude Code subprocess has `CUSTOMER_ID` env var set when persona is `customer-service`.
- [ ] `config/personas/customer-service.md` exists and is loaded without falling back to `teammate`.
- [ ] `monitoring/email_bridge_watchdog.py` + launchd plist installed and running; raises a Telegram alert to Tom when `email:last_poll_ts` is stale or `resolver:failures:*` exceeds threshold.
- [ ] `reflections/resolver_health` registered and runs on the schedule; attempts recovery + escalates on sustained failure.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -rn "CUSTOMER_ID" agent/sdk_client.py` confirms injection point exists (not just referenced in comments).

## Team Orchestration

### Team Members

- **Builder (resolver-core)**
  - Name: resolver-builder
  - Role: `bridge/routing.py::resolve_customer`, cache layer, failure counter, config-schema wiring.
  - Agent Type: builder
  - Resume: true

- **Builder (bridge-gmail-gate)**
  - Name: gmail-gate-builder
  - Role: Swap `UNSEEN`/`\Seen` for `X-GM-RAW`/custom-label gating in `bridge/email_bridge.py`; capability-check at connection; one-time migration script to bootstrap existing inbox.
  - Agent Type: builder
  - Resume: true

- **Builder (subject-coalescing)**
  - Name: coalescing-builder
  - Role: `normalize_subject`, `find_coalescing_session_id`, wire into `_process_inbound_email` after In-Reply-To lookup.
  - Agent Type: builder
  - Resume: true

- **Builder (persona + env var)**
  - Name: persona-builder
  - Role: `config/personas/customer-service.md` overlay, `load_persona_prompt` substitution support, `ValorAgent.__init__` + `_create_options` env injection.
  - Agent Type: builder
  - Resume: true

- **Builder (watchdog + reflection)**
  - Name: watchdog-builder
  - Role: `monitoring/email_bridge_watchdog.py`, launchd plist, `reflections/resolver_health.py`, registry entry.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (integration)**
  - Name: integration-tester
  - Role: End-to-end test from injected fake IMAP message through session spawn to `CUSTOMER_ID` env var presence; resolver dispatch + cache test; coalescing test; watchdog alert test.
  - Agent Type: test-engineer
  - Resume: true

- **Security Reviewer**
  - Name: subproc-security
  - Role: Review subprocess dispatch path for command injection, sender-validation regex, timeout enforcement, environment inheritance scoping.
  - Agent Type: security-reviewer
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: `docs/features/customer-resolver.md` + cross-links; update `docs/features/email-bridge.md`, `docs/features/bridge-self-healing.md`, `docs/features/README.md`.
  - Agent Type: documentarian
  - Resume: true

- **Validator (lead)**
  - Name: lead-validator
  - Role: Verifies all success criteria and the Verification table at the end.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build resolver core
- **Task ID**: build-resolver-core
- **Depends On**: none
- **Validates**: `tests/unit/test_customer_resolver.py` (create), asserting subprocess + callable dispatch, cache hit/miss, cached-None distinction, fail-closed on timeout/error, failure counter increments.
- **Informed By**: spike-1 (imaplib OK), research finding on PEP 723 + `uv run`.
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `resolve_customer(sender, project_config) -> str | None` to `bridge/routing.py`.
- Implement subprocess form (`asyncio.create_subprocess_exec`, argv form only, 5s default timeout, configurable per project); importlib callable form (follow `agent/reflection_scheduler.py:193` pattern).
- Implement Redis cache (`customer_resolver:{project_key}:{sender_id}`, cached-None = `""`).
- Implement `resolver:failures:{project_key}` INCR on error; DEL on any success.
- Sender pre-validation regex; reject malformed addresses before dispatch.
- Export helper `invalidate_customer_cache(project_key, sender_id)`.
- Do NOT call from bridge yet — next task wires it in.

### 2. Wire Gmail label gating
- **Task ID**: build-gmail-gate
- **Depends On**: none
- **Validates**: `tests/unit/test_email_bridge.py` (update/create), asserting X-GM-RAW query construction, label application order (label BEFORE enqueue), capability check aborts on missing X-GM-EXT-1.
- **Informed By**: spike-1 (X-GM-RAW call shapes + label quoting rules).
- **Assigned To**: gmail-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Gate the X-GM-RAW fetch path on `customer_resolver` presence in project config — projects without it keep the existing `UNSEEN`/`\Seen` flow unchanged.
- Add `conn.capability()` check after login; abort with loud error if `X-GM-EXT-1` missing and the project requires it.
- Replace fetch query with `X-GM-RAW "-label:valor-processed in:inbox"` plus a per-poll OR of known sender terms (keep the existing `_build_imap_sender_query` if needed for non-resolver projects; resolver projects fetch all unprocessed inbox mail since the resolver is the gate).
- After resolution, apply `valor-processed` + (`customer`|`not-customer`|`resolver-failed`) via `STORE +X-GM-LABELS`.
- Write the one-time migration script to label all currently-`\Seen` inbox mail as `valor-processed` for projects about to switch. Park in `scripts/migrations/`.

### 3. Build subject-line coalescing
- **Task ID**: build-coalescing
- **Depends On**: none
- **Validates**: `tests/unit/test_subject_coalescing.py` (create), asserting normalization rules + DB query matching + empty-subject behavior + non-terminal status filter.
- **Informed By**: spike-4 (normalization rules + DB-query lookup strategy + non-terminal status window).
- **Assigned To**: coalescing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `normalize_subject(s)` with rules documented in spike-4.
- Add `find_coalescing_session_id(project_key, customer_id, normalized_subject) -> str | None` that queries `AgentSession` for non-terminal sessions + Python-side filter on `extra_context`.
- Wire into `_process_inbound_email` AFTER the existing In-Reply-To lookup (precedence: In-Reply-To → subject coalescing → new).
- Log coalescing decisions at INFO (which path was taken).

### 4. Build persona + env-var plumbing
- **Task ID**: build-persona-env
- **Depends On**: none
- **Validates**: `tests/unit/test_persona_substitution.py` (create) + `tests/unit/test_valor_agent_env.py` (create or extend), asserting `{customer_id}` substitution and `CUSTOMER_ID` env var presence in the Claude Code subprocess options.
- **Informed By**: spike-3 (no existing substitution, env injection at `sdk_client.py:1034-1035`, carry-through via `extra_context`).
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `config/personas/customer-service.md` per the sketch in the Research/Technical Approach sections. Emphasize: professional tone, no self-reference as AI, escalate-to-Tom-via-Telegram for ambiguity, discover tools via CLAUDE.md.
- Extend `load_persona_prompt(persona, substitutions=None)`; implement `_SafeFormatDict` so unreferenced braces pass through.
- Add `customer_id: str | None = None` parameter to `ValorAgent.__init__`.
- In `_create_options`, inject `env["CUSTOMER_ID"] = self.customer_id` when present (model after `CLAUDE_CODE_TASK_LIST_ID` injection on line 1034-1035).
- At the call site (`_execute_agent_session` area near line 2539), read `_session_extra_context.get("customer_id")`, pass into `ValorAgent(...)`, pass `substitutions={"customer_id": ...}` to `load_persona_prompt`.

### 5. Wire resolver call into bridge
- **Task ID**: build-bridge-wire
- **Depends On**: build-resolver-core, build-gmail-gate, build-coalescing
- **Validates**: `tests/integration/test_email_customer_routing.py` (create) — inject a fake IMAP message, assert customer_id in extra_context, persona is customer-service, CUSTOMER_ID env var present, coalescing works across two sequential messages.
- **Assigned To**: resolver-builder (same human, sequential with other builders)
- **Agent Type**: builder
- **Parallel**: false
- In `_process_inbound_email`, after `find_project_for_email`, check `project.get("customer_resolver")`. If present, call `resolve_customer`.
- If `None`: apply `not-customer` + `valor-processed` labels, return (drop).
- If non-None: add `customer_id` to `extra_context_overrides`, set persona to `customer-service`, run coalescing, apply `customer` + `valor-processed`, enqueue.
- If resolver raises: apply `resolver-failed` + `valor-processed` labels, return (drop, counter already incremented inside `resolve_customer`).

### 6. Build watchdog and reflection
- **Task ID**: build-watchdog-reflection
- **Depends On**: build-resolver-core (needs `resolver:failures:*` keys to exist)
- **Validates**: `tests/unit/test_email_bridge_watchdog.py` (create) — simulated stale poll, elevated failure counter, SMTP dead-letter depth; assert Telegram alert payload; assert launchd plist is syntactically valid.
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true (after resolver-core)
- Create `monitoring/email_bridge_watchdog.py` structured after `monitoring/bridge_watchdog.py`; share crash-tracker and alert modules.
- Thresholds: stale poll > 2×`IMAP_POLL_INTERVAL`; failures > 10 sustained; dead-letter > 50.
- Create `~/Library/LaunchAgents/com.valor.email-bridge-watchdog.plist`, check into the repo under `scripts/launchd/` or wherever existing plists live; wire installer into `scripts/valor-service.sh`.
- Create `reflections/resolver_health.py`; register in the reflections registry. Self-heal steps: clear stuck `customer_resolver:*` entries on persistent failure for a sender; reset `resolver:failures:*` on confirmed success.

### 7. Security review of subprocess dispatch
- **Task ID**: review-subproc-security
- **Depends On**: build-resolver-core, build-bridge-wire
- **Assigned To**: subproc-security
- **Agent Type**: security-reviewer
- **Parallel**: false
- Verify argv-form subprocess (no `shell=True` / `create_subprocess_shell`).
- Verify sender pre-validation regex is conservative (reject whitespace, quotes, null bytes, shell metacharacters).
- Verify timeout enforcement path (no leaked child processes).
- Verify environment scoping (resolver subprocess inherits minimal env — audit which vars leak).
- Verify captured stdout is length-capped (prevent pathological resolver blowing memory).
- Report pass/fail. Must be pass before merge.

### 8. Integration test suite
- **Task ID**: test-integration
- **Depends On**: build-resolver-core, build-gmail-gate, build-coalescing, build-persona-env, build-bridge-wire, build-watchdog-reflection
- **Assigned To**: integration-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Use a stub IMAP in-memory mock OR a dedicated test Gmail inbox.
- Scenarios: (1) resolver returns ID → customer-service session spawned with env var; (2) resolver returns None → no session, `not-customer` label; (3) resolver times out → no session, `resolver-failed` label, failure counter incremented; (4) In-Reply-To coalescing (existing behavior still works); (5) subject-line coalescing (new behavior); (6) watchdog alert path when poll timestamp is stale.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: build-bridge-wire, build-watchdog-reflection
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/customer-resolver.md`.
- Update `docs/features/email-bridge.md`, `docs/features/bridge-self-healing.md`, `docs/features/README.md`.
- Add inline docstrings on new public callables.

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: test-integration, document-feature, review-subproc-security
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every command in the Verification table.
- Walk Success Criteria checkboxes; mark each with evidence.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Resolver module exists | `python -c "from bridge.routing import resolve_customer, invalidate_customer_cache"` | exit code 0 |
| Customer-service persona file exists | `test -f config/personas/customer-service.md` | exit code 0 |
| CUSTOMER_ID env injection in code | `grep -n 'CUSTOMER_ID' agent/sdk_client.py` | output contains `env["CUSTOMER_ID"]` |
| normalize_subject + coalescing importable | `python -c "from bridge.email_bridge import normalize_subject, find_coalescing_session_id"` (adjust path if it lives in `bridge/subject_utils.py`) | exit code 0 |
| Watchdog script exists | `test -f monitoring/email_bridge_watchdog.py` | exit code 0 |
| Reflection module registered | `python -c "from reflections.resolver_health import run"` | exit code 0 |
| Feature doc exists | `test -f docs/features/customer-resolver.md` | exit code 0 |
| Feature doc index updated | `grep -q 'customer-resolver' docs/features/README.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Subprocess resolver execution context — which machine runs it?** If the email bridge runs on multiple machines (per the per-machine deployment pattern), each machine invokes the resolver. For Cuttlefish, that means each machine's `uv run /path/to/resolver.py` needs to reach the Cuttlefish customer DB. Is the DB reachable from every bridge-running machine, or does the resolver proxy through a single-hosted API? The plan assumes the former for v1; if the latter, we need auth/creds propagation and should add that to the Update System section.

2. **Customer-service persona tone — any stylistic preferences I should bake in?** The current sketch says "professional, direct, human; never reference yourself as an AI; escalate to Tom via Telegram for ambiguity." Want to add brand voice (e.g., Cuttlefish-specific warmth), preferred sign-off, escalation-threshold examples?

3. **Gmail label naming final call.** Plan uses `valor-processed` / `customer` / `not-customer` / `resolver-failed`. Any existing label conventions in the Gmail inbox I should match, or is this fresh naming territory? (Also: these labels will auto-create on first STORE — confirm that's OK vs. wanting to pre-create them with specific colors.)

4. **Watchdog alert target.** Plan assumes Tom gets paged via Telegram (matching existing `bridge_watchdog` behavior). Confirm same chat/channel; or does customer-service health warrant a different alert surface (e.g., dedicated ops channel)?

5. **One-time migration for existing inbox.** At deploy time on a machine that's been running the email bridge, the inbox may have thousands of already-`\Seen` messages. Plan proposes a one-shot script that labels them `valor-processed` so the first poll doesn't re-ingest everything. Want that run automatically as part of `/update`, or as a manual one-time step with an explicit operator confirm?
