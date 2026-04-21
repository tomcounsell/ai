# Customer Resolver — Infrastructure

Infra considerations for the customer-resolver feature (see `docs/plans/customer-resolver.md` and issue #1093). First deployment target: Cuttlefish at `~/src/cuttlefish`.

## Current State

- **Email bridge** (`bridge/email_bridge.py`) polls IMAP every `IMAP_POLL_INTERVAL` seconds (default 30s). Connects via `IMAP4_SSL` using `IMAP_HOST`/`IMAP_USER`/`IMAP_PASSWORD` from `.env` (symlinked to `~/Desktop/Valor/.env`).
- **Gmail account** is the production mailbox. Gmail's own inbox/spam classification is the implicit floodgate — only inbox-resident mail is visible to IMAP.
- **Inbox hygiene**: operator maintains an empty inbox; humans process or archive everything. The bridge's `\Seen` gating reflects that convention — once processed (either by the bridge or a human on phone/web), the message is out of the UNSEEN set.
- **Redis** (locally bound) already used for: `email:last_poll_ts`, `email:msgid:{Message-ID}` (48h TTL), SMTP dead-letter queue.
- **Bridge watchdog** (`monitoring/bridge_watchdog.py`, launchd `com.valor.bridge-watchdog`) — Telegram-focused; runs every 60s; escalates to Tom via Telegram on sustained failure. **Not modified by this plan.**
- **No resolver dispatch surface today.** No subprocess dispatch from bridge. No `uv` usage in bridge hot path.

## New Requirements

- **`uv` present on every machine that runs the email bridge** (for subprocess-form resolvers with PEP 723 inline deps). Typically pre-installed; verify with `uv --version` at deploy.
- **Network reachability from the bridge machine to the customer CRM** (Cuttlefish DB or API). If not reachable, the resolver script must proxy via a single HTTPS endpoint.
- **New Redis keys**:
  - `customer_resolver:{project_key}:{sender_id}` → cached resolver result (string; `""` = cached None; non-empty = customer_id). TTL per-project via `customer_resolver.cache_ttl_seconds` (default 300s).
  - `resolver:failures:{project_key}` → INCR counter; DEL on any success. **Consumed by a future watchdog (deferred to followup issue); breadcrumb only in v1.**
- **New Gmail label** (auto-created on first STORE):
  - `valor-retry` — applied ONLY on resolver failure. Preserves the message for a future retry mechanism (deferred). No other custom labels are introduced by this plan.
- **No new launchd services** in v1. Email-bridge watchdog + `resolver_health` reflection are scoped out to a followup issue.

### Resource estimates

- **Gmail IMAP quotas**: 15 GB/day bandwidth; no documented op/sec cap. Current volume (10s–100s of customer mails/day) is nowhere near limits. Exponential backoff already present for IMAP errors (`bridge/email_bridge.py:627-641`).
- **Redis memory**: each resolver cache entry is under 256B; 1000 active senders × 1 project × 1 cache entry ≈ under 1 MB. Negligible.
- **CPU**: subprocess spawn per email on cache miss. Warm `uv` cache makes this sub-second. Per-poll cost is sender-count-bounded, not total-inbox-bounded.
- **Outbound CRM calls from resolver**: one per unique sender per cache-TTL window (≈1 call per 5 min per active customer). Resolver implementer (Cuttlefish) owns its own rate limiting.

## Rules & Constraints

- **Single-machine-per-project operation.** The plan assumes only one bridge machine polls any given Gmail account at a time. Operator's responsibility via `projects.json` + `machine_exclude` gating. Multi-machine polling for the same inbox is out of scope; introducing it later requires a design revisit (poll-cursor locks etc.).
- **Resolver timeout**: hard cap 5s by default (configurable per project via `customer_resolver.timeout_seconds`, future knob). On timeout: fail-closed, label `valor-retry`, increment failure counter.
- **Sender pre-validation**: any address not matching a conservative email regex is rejected before subprocess dispatch. Defense against shell-injection and malformed payloads.
- **Subprocess form**: `asyncio.create_subprocess_exec` (argv form only). Never `create_subprocess_shell`. Environment inheritance is minimal — inherit `PATH` + `HOME` only, not secrets.
- **Callable form**: for in-repo resolvers only. Dotted path must be importable from valor-ai's venv.
- **Cache-None semantics**: empty string = cached None; distinguishes from "not yet cached" (missing key). Prevents repeated dispatch for known non-customers within the TTL window.
- **Label application is best-effort.** If the Gmail STORE for `valor-retry` fails (non-Gmail server, IMAP hiccup), we log WARN and continue. The failure counter increment happens regardless, so the event is still visible.
- **Fail-closed posture**: resolver errors never produce a customer session. The operational bet is that dropping a legitimate customer email is detectable (operator searches `label:valor-retry`) and recoverable (future retry mechanism); misrouting spam into an agent session is not recoverable.

## Rollback Plan

- **Config rollback**: remove `customer_resolver` block from `projects.json` for the affected project. Bridge falls back to the static `email.contacts` / `UNSEEN` / `\Seen` path. No code rollback needed.
- **Code rollback**: revert the feature commit(s). Gmail `valor-retry` labels remain in the inbox (harmless).
- **Cache purge**: `redis-cli --scan --pattern 'customer_resolver:*' | xargs -n 100 redis-cli DEL` to clear stale entries (e.g., after a CRM data correction).
- **Failure-counter reset**: `redis-cli --scan --pattern 'resolver:failures:*' | xargs -n 100 redis-cli DEL` — safe; only affects future watchdog thresholds.
- **Gmail label cleanup** (optional): reverse-STORE via `X-GM-RAW "label:valor-retry"` → `STORE -X-GM-LABELS`. Not necessary for rollback; only if we want a clean label state.
