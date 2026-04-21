# Customer Resolver — Infrastructure

Infra considerations for the customer-resolver feature (see `docs/plans/customer-resolver.md` and issue #1093). First deployment target: Cuttlefish at `~/src/cuttlefish`.

## Current State

- **Email bridge** (`bridge/email_bridge.py`) polls IMAP every `IMAP_POLL_INTERVAL` seconds (default 30s). Connects via `IMAP4_SSL` using `IMAP_HOST`/`IMAP_USER`/`IMAP_PASSWORD` from `.env` (symlinked to `~/Desktop/Valor/.env`).
- **Gmail account** is the production mailbox. Gmail's own inbox/spam classification is the implicit floodgate — we only see inbox-resident mail.
- **Redis** (locally bound) already used for: `email:last_poll_ts`, `email:msgid:{Message-ID}` (48h TTL), SMTP dead-letter queue.
- **Bridge watchdog** (`monitoring/bridge_watchdog.py`, launchd `com.valor.bridge-watchdog`) — Telegram-focused; runs every 60s; escalates to Tom via Telegram on sustained failure.
- **No resolver dispatch surface today.** No subprocess dispatch from bridge. No `uv` usage in bridge hot path.

## New Requirements

- **`uv` present on every machine that runs the email bridge** (for subprocess-form resolvers with PEP 723 inline deps). Typically pre-installed; check with `uv --version` during `/update`.
- **Network reachability from bridge machines to the customer CRM** (Cuttlefish DB or its API). If the CRM is not reachable from every bridge machine, the resolver must proxy through a single HTTP endpoint. Decide at deploy time; document which machines are authorized.
- **New Redis keys**:
  - `customer_resolver:{project_key}:{sender_id}` → cached resolver result (string; `""` = cached None; non-empty = customer_id). TTL per-project via `customer_resolver.cache_ttl_seconds` (default 300s).
  - `resolver:failures:{project_key}` → INCR counter; reset to 0 on any successful resolution. Read by watchdog and reflection.
  - Estimated key count: one per active sender per active project. Low volume — bounded by customer-email rate.
- **New Gmail labels** (auto-created on first STORE):
  - `valor-processed` — applied to every message the bridge handles (replaces `\Seen` gating).
  - `customer` — resolver returned a customer_id.
  - `not-customer` — resolver returned None.
  - `resolver-failed` — resolver errored / timed out.
- **New launchd service**: `com.valor.email-bridge-watchdog` — runs `monitoring/email_bridge_watchdog.py` every 60s. Plist lives under `scripts/launchd/`, installer wired into `scripts/valor-service.sh`.
- **New reflection**: `reflections/resolver_health` runs every 15 min. Reads `resolver:failures:*` and `email:last_poll_ts`; attempts self-heal; escalates to Tom via Telegram if unrecoverable.

### Resource estimates

- **Gmail IMAP quotas**: free tier is 15 GB/day bandwidth and no documented op/sec cap. Our v1 volume (10s–100s of customer mails/day) is nowhere near limits. Exponential backoff already present for IMAP errors (`bridge/email_bridge.py:627-641`).
- **Redis memory**: each resolver cache entry is a string under 256B; 1000 active senders × 1 project × 1 cache entry ≈ under 1 MB. Negligible.
- **CPU**: subprocess spawn per email on cache miss. Warm `uv` cache makes this sub-second. Per-poll cost is sender-count-bounded, not total-inbox-bounded.
- **Outbound CRM calls from resolver**: one per unique sender per cache-TTL window. At default 300s TTL, a ping-ponging customer generates ≈1 CRM call per 5 min. Resolver implementer (Cuttlefish) is responsible for its own rate limiting if needed.

## Rules & Constraints

- **Resolver timeout**: hard cap 5s by default (configurable per project via `customer_resolver.timeout_seconds`, future knob). On timeout: fail-closed, label `resolver-failed`, increment failure counter.
- **Sender pre-validation**: any address not matching a conservative email regex is rejected before subprocess dispatch. Defense against shell-injection and malformed payloads.
- **Subprocess form**: `asyncio.create_subprocess_exec` (argv form only). Never `create_subprocess_shell`. Environment inheritance is minimal — inherit `PATH` + `HOME` only, not secrets.
- **Callable form**: for in-repo resolvers only. Dotted path must be importable from valor-ai's venv.
- **Cache-None semantics**: empty string = cached None; distinguishes from "not yet cached" (missing key). Prevents repeated dispatch for known non-customers within the TTL window.
- **Gmail capability check** at bridge connection: if `X-GM-EXT-1` missing from `CAPABILITY`, abort loudly. Prevents silent degradation against non-Gmail servers when label-based gating is active.
- **Watchdog thresholds** (initial values; tune after first week of operation):
  - Poll staleness: > 2 × `IMAP_POLL_INTERVAL` (i.e., > 60s at default).
  - Resolver failures: > 10 sustained in the watchdog window (60s).
  - SMTP dead-letter depth: > 50.
- **Fail-closed posture**: resolver errors never produce a customer session. The operational bet is that dropping a legitimate customer email is detectable and recoverable; misrouting spam into an agent session is not.

## Rollback Plan

- **Config rollback**: remove `customer_resolver` block from `projects.json` for the affected project. Bridge falls back to the static `email.contacts` / `UNSEEN` / `\Seen` path. No code rollback needed.
- **Code rollback**: revert the commit(s). The Gmail labels remain in the inbox (harmless); on re-enable, they're reused.
- **Cache purge**: `redis-cli --scan --pattern 'customer_resolver:*' | xargs -n 100 redis-cli DEL` if we need to clear stale cache (e.g., after a CRM data correction).
- **Failure-counter reset**: `redis-cli --scan --pattern 'resolver:failures:*' | xargs -n 100 redis-cli DEL` — safe, only affects watchdog thresholds.
- **Gmail label cleanup** (optional): `X-GM-RAW "label:valor-processed"` → STORE -X-GM-LABELS per message. Not necessary for rollback; only if we want a clean inbox.
- **Watchdog removal**: `launchctl unload ~/Library/LaunchAgents/com.valor.email-bridge-watchdog.plist` and remove the plist file. Reflection registry entry: remove `resolver_health` from registry; reflection stops running on next scheduler reload.
- **One-time migration rollback**: the startup migration that labels existing `\Seen` mail as `valor-processed` is idempotent and reversible — remove the label via a reverse-STORE script.
