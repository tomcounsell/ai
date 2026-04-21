---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-04-21
revised: 2026-04-21
tracking: https://github.com/tomcounsell/ai/issues/1067
last_comment_id:
revision_applied: true
---

# valor-email CLI

## Problem

When working in a terminal (Claude Code or ad-hoc dev session), reading recent emails or sending a quick reply requires either `gws gmail` (verbose, OAuth-context-dependent) or raw `python -m bridge.email_bridge` commands. The Telegram equivalent — `valor-telegram read / send / chats` — is ergonomic, Redis-cached, and bridge-agnostic. Email has no such surface.

**Current behavior:**
- Reading email requires `gws gmail users messages list --params '{"userId":"me","maxResults":5}'` — verbose JSON args, no Redis cache, no `--since` or `--search` shorthand.
- Sending email requires either (a) starting the full email bridge, (b) hand-crafting `gws gmail` POSTs, or (c) writing ad-hoc SMTP Python. There is no one-line CLI.
- No unified CLI mirrors `valor-telegram read / send / chats`. The existing `email:outbox:{session_id}` Redis queue is written to by `tools/send_message.py` (agent-facing) using a legacy payload shape `{session_id, to, text, timestamp}`, but nothing **drains** it — sends are only dispatched directly from `EmailOutputHandler.send()` inside the worker. The queue is effectively dormant, so we can freely update the payload contract in the same PR as long as both writers (send_message.py and the new CLI) emit the new shape.

**Desired outcome:**
- `valor-email read --mailbox INBOX --limit 10` returns recent emails from a Redis history cache, falling back to IMAP on cache miss.
- `valor-email read --search "deployment" --since "2 hours ago"` filters correctly across both cache and IMAP.
- `valor-email send --to foo@example.com --subject "Re: Thing" "Body text"` sends via the Redis relay (or falls back to direct SMTP if the bridge is not running).
- `valor-email send --to ... --file ./report.pdf "See attached"` attaches a file using `MIMEMultipart`.
- `valor-email send --reply-to "<msgid@host>" "Body"` threads correctly via `In-Reply-To` / `References` headers.
- `valor-email threads` lists known email threads from the Redis cache.
- `--json` flag works uniformly across all subcommands.

## Freshness Check

**Baseline commit:** `1eb76f6f` (main, 2026-04-21)
**Issue filed at:** 2026-04-20T04:41:32Z (~1 day ago)
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/valor_telegram.py` — claimed 501 lines, `read`/`send`/`chats` subcommands, Redis-first with Telethon fallback, outbox relay at `telegram:outbox:{session_id}` with 1h TTL — **still holds** (exactly 501 lines; `cmd_send` at line 316 pushes to the outbox at line 381).
- `bridge/email_bridge.py` — claimed 716 lines with `_email_inbox_loop` at line 584, IMAP/SMTP env vars, `EmailOutputHandler` — **still holds** (exact line number 584; `_email_inbox_loop` body intact).
- `bridge/telegram_relay.py` — claimed to be the relay pattern to mirror — **still holds** (22.6 KB, polls `telegram:outbox:*` every 100ms).
- `bridge/email_dead_letter.py` — claimed DLQ surface at `email:dead_letter:{session_id}` — **still holds** (8.4 KB, `write_dead_letter()` exported).
- `pyproject.toml` `[project.scripts]` — claimed 10 `valor-*` entries at lines 47-57 — **verified** (10 entries, ready for an 11th).
- `tools/send_message.py:160` — claimed `email:outbox:{session_id}` key already written — **verified** (line 160 in `_send_via_email()`).

**Cited sibling issues/PRs re-checked:**
- #847 (email bridge — merged 2026-04-13 as PR #908) — still closed, foundation intact.
- #936 (email bridge operational test coverage — closed 2026-04-13) — still closed.
- #946 (EmailOutputHandler registration bug — closed 2026-04-14) — still closed; no regressions since.

**Commits on main since issue was filed (touching referenced files):**
- `1eb76f6f` — Bump claude-agent-sdk 0.1.63→0.1.64 — **irrelevant** (SDK bump only).
- `93bf4719` — Consolidate bridge/response.py (#1077) — **irrelevant** (response.py, not email_bridge.py).
- `baeb93ea` — Browser screenshot dimension fix (#1068) — **irrelevant**.
- `26c0ed5e` — Message drafter rename (#1035) — **touches `EmailOutputHandler.send()` indirectly via drafter-at-handler**, but only changes `medium="email"` routing inside an already-safe try/except. No impact on the new relay consumer or cache layer.

**Active plans in `docs/plans/` overlapping this area:** None. No active plans touch `bridge/email_*.py`, `tools/valor_*.py`, or `email:outbox` / `email:history` Redis namespaces.

**Notes:** The email bridge ecosystem has been stable for 8 days. This plan cleanly layers on top.

## Prior Art

Searched closed issues for `valor-email` and `email CLI`:

- **Issue #847 / PR #908** — "Email bridge: add email as a secondary inbox/outbox transport" (merged 2026-04-13). Introduced `bridge/email_bridge.py`, IMAP poll loop, `EmailOutputHandler`, dead-letter queue. **Foundation for this work** — this plan extends that bridge with a history-cache write and an outbox-relay consumer, and adds a CLI layer.
- **Issue #936** — "Email bridge: add operational test coverage for env loading, batch cap, and health timestamp" (closed 2026-04-13). Established test harness patterns for mocking `imaplib.IMAP4_SSL` and `smtplib.SMTP`. **Reusable directly** for this plan's CLI tests.
- **Issue #946** — "Email bridge: worker skips EmailOutputHandler registration for domain-only projects" (closed 2026-04-14). A registration bug fix; orthogonal to CLI work but confirms the bridge's transport-callback plumbing is complete.

No prior issue or PR attempted `valor-email`. `gh pr list --state merged --search "valor-email"` returned zero hits. This is greenfield at the CLI layer but stands atop a well-tested bridge.

## Research

**Queries used:**
- `python email MIMEMultipart attachment In-Reply-To References header threading best practices 2026`

**Key findings:**
- **MIMEMultipart composition pattern** ([python docs](https://docs.python.org/3/library/email.mime.html)): `MIMEMultipart()` + `.attach(MIMEText(body, "plain", "utf-8"))` + `.attach(MIMEBase(...))` with `Content-Disposition: attachment; filename="..."`. This supersedes the current `email_bridge._build_reply()` which uses plain `MIMEText` and therefore cannot carry attachments.
- **Threading headers** ([ilostmynotes.blogspot.com](https://ilostmynotes.blogspot.com/2014/11/smtp-email-conversation-threads-with.html)): both `In-Reply-To` and `References` must carry the original `Message-ID` string (angle-bracketed, e.g. `<abc@host>`). Gmail fragments threads if either is missing. The existing `EmailOutputHandler._build_reply()` already sets both correctly — the CLI must preserve this discipline for `--reply-to`.
- **Common pitfall** ([kitson-consulting.co.uk](https://kitson-consulting.co.uk/blog/definitive-guide-creating-emails-attachments-python-3)): Subject, From, To, Date, Message-ID are all required or the message is spam-marked. Date must be RFC-2822 via `email.utils.formatdate(localtime=True)`.

**Impact on plan:** The `_build_reply()` helper in `email_bridge.py` must be **refactored** (not replaced) to accept an optional `attachments: list[Path] | None` and switch to `MIMEMultipart` when any are present. Both the existing worker send path and the new CLI send path will use the upgraded helper. The drafter-at-handler integration must still receive the plain-text body, not a MIME object — i.e., text transformation happens **before** MIME assembly.

## Spike Results

### spike-1: `_build_reply` refactor — extend vs. parallel helper
- **Assumption**: "`EmailOutputHandler._build_reply()` can be refactored in place without risking the existing agent-reply path."
- **Method**: code-read (`bridge/email_bridge.py:234-258` `_build_reply`; `bridge/email_bridge.py:322-329` call site inside `EmailOutputHandler.send`)
- **Finding**: `_build_reply` is called from exactly one site (`EmailOutputHandler.send()` at line 322) with fixed positional arguments. Adding an optional `attachments: list[Path] | None = None` keyword is backward compatible; existing callers omit it. The function's output type broadens from `MIMEText` to `MIMEText | MIMEMultipart`, which requires updating the `_send_smtp` signature to accept the union. No other consumers reference `_build_reply` directly.
- **Confidence**: high
- **Impact on plan**: Task 2 refactors `_build_reply` in place with the optional kwarg. The relay consumer in `bridge/email_relay.py` imports the same helper. Tests for the no-attachment path remain green; new tests cover the attachment path. Zero risk of drift between the worker-reply and CLI-send MIME assembly.

### spike-2: History cache schema — single-blob vs. split-blob
- **Assumption**: "The `email:history:msg:{msgid}` split-blob design (separate from the sorted set) is worth the 2-phase write complexity."
- **Method**: code-read (`bridge/email_bridge.py:157-193` `parse_email_message` output shape) + design tradeoff analysis
- **Finding**: Split-blob (sorted set of Message-IDs + per-msg string keys) enables independent TTL on individual messages and keeps the sorted set small (strings, not JSON blobs). It also allows `HDEL` on a single message without rewriting the whole set. The cost is two Redis round-trips per read: one `ZREVRANGE` then N `GET` lookups. For `--limit 10`, that's 11 round-trips vs. 1 for a combined-blob design. On localhost Redis (sub-millisecond) this is invisible; on a networked Redis it matters. Current deployment uses localhost Redis, so the split-blob design is acceptable for v1.
- **Confidence**: high
- **Impact on plan**: Keep the split-blob schema as written in the Solution section. Race 1 (poll-loop-write vs. CLI-read) is real under this design — the ZADD must happen AFTER the per-msg SET, and the CLI must tolerate a missing per-msg blob for a Message-ID that just appeared in the sorted set (retry once, then skip).

### spike-3: `--reply-to` source UX (raw Message-ID vs. short-index)
- **Assumption**: "Users need a short/ergonomic way to specify which message they're replying to, since raw RFC-822 Message-IDs like `<abc@host>` are unwieldy to type."
- **Method**: design decision informed by `valor-telegram` parity and the `email:msgid:*` / `email:history:msg:*` key structure
- **Finding**: Telegram's `--reply-to <int>` uses a stable, short integer. Email's equivalent is the `<Message-ID>` string. Supporting a short cache-index (e.g., `--reply-to 3` meaning "the 3rd most recent cached message") would introduce stateful CLI behavior that breaks under concurrent terminal sessions and across machines. For v1, accept the raw RFC-822 string only. Users who want the Message-ID run `valor-email read --json --limit 10` and copy the `message_id` field. Short-index support can be a v2 follow-up with a per-terminal state file if real demand emerges.
- **Confidence**: medium (design call, not technical)
- **Impact on plan**: `cmd_send` accepts `--reply-to "<Message-ID>"` only. No cache-index lookup. Documented in No-Gos. `CLAUDE.md` section shows a worked example with the Message-ID copied from a prior `read --json` invocation.

### spike-4: Direct-IMAP fallback sender filter policy
- **Assumption**: "The CLI's direct-IMAP fallback should show all INBOX messages, not just those from known senders, so developers can see mail that the bridge skipped."
- **Method**: code-read (`bridge/routing.py:244-256` `get_known_email_search_terms` — see the docstring's multi-machine inbox-sharing comment) + `bridge/email_bridge.py:540-580` poll loop
- **Finding**: The bridge deliberately filters polls by known senders because UNSEEN messages are shared across multiple machines — a message left UNSEEN here is meant for another machine to pick up. If the CLI fallback read were to open INBOX without `readonly=True` and without the FROM filter, it would (a) mark unrelated messages as SEEN and break multi-machine sharing, or (b) leak messages that belong to a different project's policy boundary. The CLI must both open IMAP with `readonly=True` AND filter FROM by `get_known_email_search_terms()`.
- **Confidence**: high
- **Impact on plan**: CLI direct-IMAP fallback wraps the IMAP session with `readonly=True` and reuses `_build_imap_sender_query(get_known_email_search_terms())`. A future "show all inbox messages" mode would need a dedicated flag and an explicit machine-ownership boundary; explicitly deferred and called out in No-Gos.

## Data Flow

```
┌──────────────────────────┐       ┌───────────────────────────┐
│  valor-email read        │──────▶│ Redis: email:history:INBOX│
│  (CLI)                   │       │  (sorted set by ts)       │
└──────────────────────────┘       └───────────────────────────┘
          │ cache miss                      ▲
          ▼                                 │ write-through
┌──────────────────────────┐       ┌───────────────────────────┐
│  Direct IMAP fetch       │       │ bridge/email_bridge.py    │
│  (fallback; uses same    │       │  _email_inbox_loop        │
│   imaplib config)        │       │  _process_inbound_email   │
└──────────────────────────┘       └───────────────────────────┘

┌──────────────────────────┐       ┌───────────────────────────┐
│  valor-email send        │──────▶│ Redis:                    │
│  (CLI)                   │       │ email:outbox:{session_id} │
└──────────────────────────┘       └───────────────────────────┘
          │ bridge not running             │ poll 100ms
          │ (fallback)                     ▼
          │                        ┌───────────────────────────┐
          ▼                        │ bridge/email_relay.py     │
┌──────────────────────────┐       │  (NEW: drain outbox,      │
│  Direct SMTP send        │       │   SMTP send with retry,   │
│  (mirrors _send_smtp)    │       │   DLQ on exhaustion)      │
└──────────────────────────┘       └───────────────────────────┘
                                            │ exhausted retries
                                            ▼
                                   ┌───────────────────────────┐
                                   │ email:dead_letter:{sid}   │
                                   │ (bridge/email_dead_letter)│
                                   └───────────────────────────┘
```

1. **Entry point (read):** `valor-email read --mailbox INBOX --limit N` calls a new `tools.email_history` helper which reads from `email:history:INBOX` (sorted set keyed by UNIX timestamp).
2. **Cache layer:** `email:history:{mailbox}` is populated by the IMAP poll loop in `_email_inbox_loop`, via a new `_record_history()` write-through call after `parse_email_message()` succeeds. TTL 7 days, capped at 500 entries (ZREMRANGEBYRANK).
3. **IMAP fallback:** if the cache returns < `limit` entries (empty-cache bootstrap, or the daemon has been down), `valor-email read` opens a standalone `imaplib.IMAP4_SSL` connection using the same env-var config as the bridge and fetches directly. Results are NOT SEEN-flagged (CLI reads are idempotent — the bridge owns SEEN semantics).
4. **Entry point (send):** `valor-email send --to X --subject Y Z` builds a payload dict with the **unified outbox contract** `{session_id, to, subject, body, attachments, in_reply_to, references, from_addr, timestamp}` and pushes to `email:outbox:{session_id}`. TTL 1h. `tools/send_message.py::_send_via_email` is updated in the same PR to emit the same shape (it currently emits the legacy `{session_id, to, text, timestamp}` — unused by any consumer, so rewriting in place is safe). `subject`, `in_reply_to`, `references`, and `attachments` are optional; the relay supplies defaults on read.
5. **Relay drain:** `bridge/email_relay.py` (new) polls `email:outbox:*` every 100ms. It mirrors `bridge/telegram_relay.py` exactly: **atomic `LPOP` first**, then attempt SMTP send. On success: done (the LPOP already removed the entry). On failure: increment `_relay_attempts`, `RPUSH` the same payload back onto the key; after 3 failed attempts, DLQ via `write_dead_letter()`. Relay writes its heartbeat to `email:relay:last_poll_ts` once per poll cycle.
6. **No direct-SMTP fallback.** The CLI always queues via Redis. If the relay is not running, the message sits in the queue until it is (the `email-status` service command is the operator's signal). Rationale: `valor-telegram send` has identical semantics (always via relay, never direct); email should not diverge. See Rabbit Holes.
7. **Threads listing:** `valor-email threads` reads a new `email:threads` hash maintained by the poll loop — each `Message-ID` → `In-Reply-To` chain collapses into a thread root, stored as `{thread_root_msgid: [child_msgid_1, child_msgid_2, ...]}`.
8. **Output:** JSON (via `--json`) or human-readable table (default), mirroring `valor-telegram`'s conventions.

## Architectural Impact

- **New dependencies:** None — everything uses stdlib (`email`, `imaplib`, `smtplib`, `mimetypes`) and existing Redis (`redis-py`).
- **Interface changes:**
  - `bridge/email_bridge.py::EmailOutputHandler._build_reply()` is refactored to accept `attachments: list[Path] | None = None` and return `MIMEMultipart` when attachments are present, else `MIMEText` (preserves backward compatibility — no call-site signature changes for the worker path).
  - `bridge/email_bridge.py::_email_inbox_loop` gains a new write-through call to `_record_history()`.
  - `tools/send_message.py::_send_via_email` is rewritten to emit the unified outbox payload shape (`body` replaces `text`; adds optional `subject`, `in_reply_to`, `references`, `from_addr`, `attachments`). The old shape is unused by any consumer today, so this is a lossless contract upgrade.
  - **New:** `bridge/email_relay.py` module with `run_email_relay()` async coroutine, mirroring `bridge/telegram_relay.py` structure (atomic `LPOP`, re-push on failure, DLQ on exhaustion, heartbeat key).
  - **New:** `tools/valor_email.py` CLI entry point.
  - **New:** `tools/email_history/` package with `get_recent_emails()`, `search_history()`, `list_threads()` mirroring the `tools/telegram_history/` shape.
- **Coupling:** No new cross-module coupling. The CLI depends on the bridge config helpers (`_get_imap_config`, `_get_smtp_config`) — these are already module-level pure functions.
- **Data ownership:** The IMAP poll loop owns `email:history:INBOX`. The CLI and MCP tools are **readers only** for the history cache. The outbox queue is write-by-anyone, drain-by-relay — identical to Telegram's `telegram:outbox:*` model.
- **Reversibility:** Fully reversible. Remove `tools/valor_email.py`, `bridge/email_relay.py`, and the two write-through calls in `_email_inbox_loop`; revert `tools/send_message.py::_send_via_email` to the legacy shape; flush `email:history:*`, `email:threads`, `email:outbox:*`, and `email:relay:last_poll_ts` Redis keys. See Update System for the exact rollback sequence.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (to confirm that the Redis relay consumer is scoped into this issue — the issue body calls it out as a gap, and this plan explicitly includes it; a check-in guards against scope creep if the human wants it deferred to a follow-up).
- Review rounds: 1-2 (one post-build review, then possibly a second after patch).

Rationale for Medium (not Small): the work includes three new modules (CLI, relay, history package), a MIME refactor in `_build_reply()`, and a new Redis cache/namespace (`email:history:*`, `email:threads`). Each is individually straightforward but the orchestration across them exceeds the Small appetite ceiling.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD` | `python -c "import os; assert all(os.environ.get(k) for k in ('IMAP_HOST','IMAP_USER','IMAP_PASSWORD')), 'IMAP env vars missing'"` | Read path fallback + cache warm-up |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` | `python -c "import os; assert all(os.environ.get(k) for k in ('SMTP_HOST','SMTP_USER','SMTP_PASSWORD')), 'SMTP env vars missing'"` | Send path (relay + direct) |
| Redis reachable | `python -c "import redis, os; redis.Redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0')).ping()"` | Outbox queue + history cache |
| Editable install | `which valor-telegram` | Confirms `pip install -e .` will pick up new CLI entry |

Run all checks: `python scripts/check_prerequisites.py docs/plans/valor-email-cli.md`

## Solution

### Key Elements

- **`tools/valor_email.py`** — CLI with `read`, `send`, `threads` subcommands. Structural mirror of `tools/valor_telegram.py`; subcommand names and flags align where semantics match.
- **`tools/email_history/`** — small package with `get_recent_emails()`, `search_history()`, `list_threads()`. Pure Redis reads from `email:history:*` keys. (No `list_mailboxes()` scaffold — YAGNI; multi-mailbox is explicitly a No-Go.)
- **`bridge/email_relay.py`** — new module. `run_email_relay()` coroutine polls `email:outbox:*` every 100ms, drains payloads, calls the upgraded `_build_reply_mime()` helper, and dispatches via SMTP with retry + DLQ. Registered in the bridge's event loop next to `run_email_bridge()`.
- **`bridge/email_bridge.py` changes:**
  - Extract `_build_reply_mime(to, subject, body, in_reply_to, references, from_addr, attachments)` as a module-level helper (was `EmailOutputHandler._build_reply`). Accept `attachments: list[Path] | None`.
  - `_email_inbox_loop` adds `_record_history(parsed, raw_bytes)` call after each successful parse; cap at 500 entries per mailbox, TTL 7 days.
  - `_record_thread(parsed)` maintains `email:threads` hash so `valor-email threads` has structured data.
- **`pyproject.toml`** — one-line addition: `valor-email = "tools.valor_email:main"`.
- **`CLAUDE.md`** — add a `valor-email` usage table next to `valor-telegram`.
- **`docs/features/email-bridge.md`** — append a "CLI" section.

### Flow

```
CLI read:
$ valor-email read --limit 5
  → tools/email_history/__init__.py::get_recent_emails(mailbox="INBOX", limit=5)
    → r.zrevrange("email:history:INBOX", 0, 4, withscores=True)
    → hydrate each msgid via r.get("email:history:msg:{msgid}")
  → human-readable table output

CLI send (always via relay):
$ valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"
  → tools/valor_email.py::cmd_send
    → build unified payload {session_id, to, subject, body, in_reply_to=None, attachments=[], ...}
    → r.rpush("email:outbox:{session_id}", json.dumps(payload))
    → r.expire("email:outbox:{session_id}", 3600)
    → "Queued. Check delivery via ./scripts/valor-service.sh email-status"
  → bridge/email_relay.py picks up within 100ms
    → r.lpop(key) (atomic)
    → _build_reply_mime(...) → smtp.sendmail(...)
    → success: done; failure: increment _relay_attempts, r.rpush back, DLQ after 3

CLI threads:
$ valor-email threads
  → tools/email_history/__init__.py::list_threads()
    → r.hgetall("email:threads")
  → human-readable table: thread root subject, message count, last activity
```

### Technical Approach

- **Mirror valor_telegram.py structure**: argparse with three subparsers (`read`, `send`, `threads`), `--json` flag on all, shared helpers (`parse_since`, `format_timestamp`, `_get_redis_connection`) — copy-paste-adapt rather than extract, to keep each CLI tool self-contained. Cross-CLI extraction is a rabbit hole (see Rabbit Holes).
- **Relay contract (mirror `bridge/telegram_relay.py`):** atomic `LPOP` from each `email:outbox:*` key, then SMTP send in `asyncio.to_thread`. On success: done (entry was already removed by LPOP). On failure: increment `_relay_attempts` in the payload, `RPUSH` back onto the key; after `MAX_EMAIL_RELAY_RETRIES` (default 3), route to `bridge.email_dead_letter.write_dead_letter()`. The heartbeat key `email:relay:last_poll_ts` is `SET` to `time.time()` with a 5-minute TTL once per poll cycle so operators can probe liveness via `GET`. See `bridge/telegram_relay.py:443-572` for the exact pattern to copy — especially lines 464-465 (atomic LPOP) and 528-545 (requeue-with-counter).
- **Unified outbox payload contract:** both `tools/send_message.py::_send_via_email` and `tools/valor_email.py::cmd_send` emit the same shape:
  ```json
  {
    "session_id": "<string>",
    "to": "<address>",
    "subject": "<optional string>",
    "body": "<string>",
    "attachments": ["<absolute path>", ...],
    "in_reply_to": "<optional Message-ID>",
    "references": "<optional Message-ID>",
    "from_addr": "<optional override>",
    "timestamp": <unix seconds float>
  }
  ```
  The relay normalizes on read: missing `subject` → `"(no subject)"`; missing `body` → reject and DLQ (malformed); missing `from_addr` → use `SMTP_USER` env var; missing `attachments` → empty list. Legacy field `text` is treated as a synonym for `body` during a single transitional release to avoid stranding any in-flight entries (harmless since the queue is currently empty).
- **Cache key schema:**
  - `email:history:INBOX` — sorted set, score = UNIX timestamp, member = `Message-ID` string. Allows `ZREVRANGE` for recent-first, `ZRANGEBYSCORE` for `--since` filters.
  - `email:history:msg:{message_id}` — string key containing JSON blob `{from_addr, subject, body, timestamp, message_id, in_reply_to}`. TTL 7 days.
  - `email:threads` — hash, field = thread root `Message-ID`, value = JSON `{subject, message_count, last_ts, participants}`. Thread roots are recomputed on each new message (follow `in_reply_to` chain); if a newly arrived message reveals an earlier root than we'd stored, we re-key the entry. Drift is acceptable for v1 — the hash is a best-effort navigation aid, not a source of truth.
  - Cap enforcement: after each ZADD, `ZREMRANGEBYRANK("email:history:INBOX", 0, -501)` trims to 500 newest. To prevent orphan blob leaks, the trim path `ZRANGE 0 -501` first to capture the evicted IDs, then `DEL` each `email:history:msg:{msgid}` key in the same Redis pipeline. (Individual TTLs still bound the leak at 7 days, but active deletion keeps memory pressure tighter under heavy inbound.)
- **`--since` parsing:** reuse `tools/valor_telegram.parse_since` verbatim by importing it. (Same parser semantics: `"1 hour ago"`, `"2 days ago"`, `"30 minutes ago"`.) This is the one piece of shared code — everything else is copy-paste-adapt.
- **`--reply-to` semantics:** accepts a Message-ID string (angle-bracketed or not; normalize to `<...>` form). CLI sets both `In-Reply-To` and `References` from the same value. This differs from Telegram's integer `reply_to`; the argparse `type=` differs accordingly.
- **Attachment payload shape:** payload carries `"attachments": [absolute_path, ...]` — the CLI validates file existence **before** enqueueing (fail fast, mirror valor-telegram). The relay re-validates at drain time and DLQs if the file was deleted.
- **Drafter integration:** the CLI does NOT route through `bridge.message_drafter.draft_message(medium="email")`. The drafter is for agent-originated text; CLI text is user-authored and should pass through verbatim. This matches `valor-telegram send` behavior (which only applies `_linkify_text`, not the full drafter).

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `bridge/email_bridge.py::_record_history` — wrap in try/except; failures log `logger.warning` but MUST NOT break the poll loop. Test asserts `caplog` contains warning on Redis down.
- [x] `tools/valor_email.py::cmd_send` — Redis push failure prints to stderr with exit code 1; direct-SMTP fallback failure prints error with exit code 1. Tests simulate `redis.ConnectionError` and `smtplib.SMTPException`.
- [x] `bridge/email_relay.py::_drain_outbox` — SMTP failure triggers retry with backoff; after 3 attempts, routes to DLQ. Tests assert DLQ write on exhausted retries (mirror `test_email_bridge.py` DLQ pattern).
- [x] `tools/email_history/__init__.py::get_recent_emails` — Redis read failure returns `{"error": str}` dict; CLI prints the error with exit code 1.

### Empty/Invalid Input Handling
- [x] `valor-email send` with no text AND no `--file` → error "Must provide a message or file" (mirror `valor-telegram`).
- [x] `valor-email read` with empty cache AND IMAP fallback disabled → prints "No messages found." exit 0.
- [x] `valor-email send --reply-to ""` → argparse rejects (min length 1 via custom type validator).
- [x] `parse_since("yesterday")` returns None → caller treats as no filter (same as `valor-telegram`).
- [x] `cmd_send` with empty body but a file attachment → valid (matches Telegram `--file` without caption pattern).
- [x] MIME assembly with empty body AND no attachments → reject at CLI layer before enqueue.

### Error State Rendering
- [x] CLI error output goes to stderr; successful output goes to stdout. `--json` always goes to stdout.
- [x] Dead-letter rendering: when the relay DLQs a CLI-originated send, the user has no direct feedback path (the CLI exited after enqueue). Surface this via `./scripts/valor-service.sh email-dead-letter list` — existing surface, no new UX needed. Document in CLI help epilog.

## Test Impact

- [x] `tests/unit/test_email_bridge.py::test_build_reply_*` — UPDATE: refactored `_build_reply` signature. Existing assertions still valid for no-attachment path; add new cases for attachment path.
- [x] `tests/unit/test_email_bridge.py::TestEmailOutputHandler::test_send_*` — UPDATE: `_build_reply_mime` is now module-level; adjust imports if tests patched the method.
- [x] `tests/integration/test_email_bridge.py::test_email_inbox_loop_*` — UPDATE: add assertion that `email:history:INBOX` receives writes after a processed inbound.
- [x] `tests/unit/test_send_message.py` — UPDATE: `_send_via_email` now writes the unified payload (`body` not `text`; includes `subject`, `from_addr`, `attachments`, etc.). Existing assertions on the legacy shape must be replaced.

No other existing tests affected. New test files:
- `tests/unit/test_valor_email.py` — mirror `test_valor_telegram.py` structure; mock Redis/IMAP/SMTP.
- `tests/unit/test_email_relay.py` — mirror `tests/unit/test_telegram_relay.py` if it exists; otherwise mirror `test_email_bridge.py` DLQ patterns. Must cover: atomic LPOP, re-push on failure with counter, DLQ after 3 attempts, legacy-text payload compat, heartbeat write.
- `tests/unit/test_email_history.py` — cache read helpers; Redis fake or patched.
- `tests/integration/test_valor_email.py` — end-to-end: CLI → Redis → relay → mocked SMTP → received.

## Rabbit Holes

- **Don't extract a shared CLI helper module.** `valor_telegram.py` and `valor_email.py` will share `parse_since`, `format_timestamp`, and `_get_redis_connection`. Extracting these to `tools/cli_common.py` is tempting but premature — only 3 functions, stable APIs, no third caller. Copy-paste with an import for `parse_since` is the right tradeoff.
- **Don't implement HTML email.** The issue explicitly drops HTML composition. `MIMEText("plain", "utf-8")` only. If someone needs HTML later, it's a new issue.
- **Don't add multi-mailbox support.** The bridge polls only `INBOX`. `--mailbox` is accepted as an argument but only `INBOX` is valid in v1; other values error out. Multi-mailbox is a separate design.
- **Don't write a Gmail-API path.** Issue drops this. IMAP only.
- **Don't implement streaming/tail mode.** `valor-telegram` has no `--follow` flag; `valor-email` doesn't need one either. If needed, IMAP IDLE is a separate issue.
- **Don't restructure `EmailOutputHandler`.** Promoting `_build_reply` to a module-level helper is the minimal refactor. A full handler class split into separate builder/sender responsibilities is a separate concern — do it only if the MIME refactor forces it, which it won't.
- **Don't try to unify `email:outbox` payload shape with `telegram:outbox`.** They serve different transports with different header semantics (no `reply_to_msg_id` for email, no `In-Reply-To` for Telegram). A unified *email* outbox shape (across the CLI and send_message.py) is in scope; cross-transport unification is not.
- **Don't add `--watch` or daemon mode to the CLI.** The relay in `bridge/email_relay.py` is the daemon; the CLI is one-shot. Keep them separate.
- **Don't add a `--direct` SMTP fallback.** Earlier drafts proposed a `--direct` flag to bypass Redis if the relay was down. Dropped because (a) it duplicates the queue-drain path with a second send site, (b) `valor-telegram send` has no equivalent and we want behavior parity, (c) the relay heartbeat + `email-status` command is a cleaner operator signal than baking fallback into every CLI invocation. If the relay is down, the fix is to start the relay, not to bypass it.

## Risks

### Risk 1: History cache fills Redis if IMAP gets a flood of inbound from many senders
**Impact:** Redis memory pressure; eviction of other keys.
**Mitigation:** Hard cap of 500 entries per mailbox (ZREMRANGEBYRANK after each write, with active DEL of the evicted per-msg blobs in the same pipeline). TTL of 7 days on individual message JSON keys as a secondary bound. Monitor with existing Redis memory alerting. Document in `docs/features/email-bridge.md`.

### Risk 2: `_build_reply_mime` refactor subtly changes existing worker-path behavior
**Impact:** Agent replies stop threading correctly or get spam-flagged.
**Mitigation:** Keep `attachments=None` as the default; for no-attachment calls, produce a `MIMEText` object with identical headers to today's output. Add a **parsed-header regression test** (not a byte-for-byte test — header order, line-folding, and default encodings vary by Python minor version and are semantically irrelevant). The test asserts that for a fixed fixture input, `email.message_from_bytes(new.as_bytes())` and `email.message_from_bytes(old.as_bytes())` have identical values for `From`, `To`, `Subject`, `In-Reply-To`, `References`, `Content-Type`, and `Content-Transfer-Encoding`, and identical `get_payload(decode=True)` bytes. Date and Message-ID are excluded (per-call variance). This approach is robust to MIME library version drift.

### Risk 3: Relay races with `EmailOutputHandler.send()` on the same session_id
**Impact:** Two SMTP sends for one agent output.
**Mitigation:** The worker's `EmailOutputHandler.send()` does NOT push to `email:outbox:*` — it sends directly. The outbox is ONLY used by CLI (and `tools/send_message.py`). The relay has no overlap with `EmailOutputHandler.send()`. Verify with a grep for `email:outbox` in `agent/` and `worker/`: all writes come from `tools/` or `bridge/email_relay.py`. Document this invariant in `bridge/email_relay.py` module docstring.

### Risk 4: Bridge restart is required to pick up `bridge/email_relay.py`
**Impact:** After merging, if nobody restarts the bridge, CLI `send` will hang messages in Redis.
**Mitigation:** CLI prints "Queued. Check delivery via ./scripts/valor-service.sh email-status" after enqueue — same pattern as `valor-telegram`. The `email-status` service command reads `email:relay:last_poll_ts` and prints "relay stale (>5 minutes)" if the heartbeat is old. Update `scripts/valor-service.sh email-status` to include the relay heartbeat check. Document in plan's Update System section.

### Risk 5: Payload-shape migration breaks `tools/send_message.py::_send_via_email`
**Impact:** Agent sends (triggered by the drafter) fail to deliver after deploy.
**Mitigation:** The queue has no consumer today; updating both the writer (`_send_via_email`) and the new reader (relay) in the same PR is atomic. The relay's normalization layer accepts legacy `text` as a synonym for `body` for one transitional release (trivial code path, easy to delete in a follow-up). A unit test asserts the relay drains a legacy `{session_id, to, text, timestamp}` payload correctly. No runtime migration needed — in-flight entries (if any) survive.

## Race Conditions

### Race 1: Poll loop writes `email:history:msg:{msgid}` while CLI reads it
**Location:** `bridge/email_bridge.py::_record_history` (new) vs. `tools/email_history/__init__.py::get_recent_emails` (new).
**Trigger:** User runs `valor-email read` mid-poll; the sorted set has the new msgid but the JSON blob has not been written yet (one Redis round-trip later).
**Data prerequisite:** `email:history:msg:{msgid}` must exist before `ZADD email:history:INBOX ts msgid`.
**State prerequisite:** N/A.
**Mitigation:** Write the JSON blob **first**, then ZADD. `get_recent_emails` handles missing JSON blobs by skipping entries (defensive — returns only entries with both parts). Use a Redis pipeline (`MULTI/EXEC`) to make the two writes atomic — this is cheap and idiomatic.

### Race 2: Two concurrent CLI sends push to the same `email:outbox:cli-{ts}` key (same-second collision)
**Location:** `tools/valor_email.py::cmd_send`.
**Trigger:** Two invocations within the same UNIX second produce identical session_ids.
**Data prerequisite:** session_ids must be unique per invocation.
**State prerequisite:** N/A.
**Mitigation:** Use `f"cli-{int(time.time())}-{os.getpid()}-{secrets.token_hex(4)}"` for the session_id suffix. 4 bytes = 32 bits of randomness, giving ~2^16 = 65k concurrent-per-second invocations before a 50% collision probability (birthday paradox). The scalar collision rate at expected CLI-per-second usage (< 10) is under 10^-8 per call — effectively never. 2-byte tokens (16 bits) were in the prior draft but flagged as insufficient margin; upgraded to 4 bytes.
**Note:** This fix applies only to `valor_email.py`. Modifying `valor_telegram.py` is out of scope (existing same-second bug there is separately tracked if real; not blocking here).

### Race 3: Relay drains an outbox entry during bridge restart
**Location:** `bridge/email_relay.py::process_outbox`.
**Trigger:** Bridge restart kills the relay mid-SMTP-send.
**Data prerequisite:** Payload must not be lost if the SMTP call hasn't completed.
**State prerequisite:** `_relay_attempts` counter must survive a crash so retries don't loop forever.
**Mitigation:** Use **atomic `LPOP` first**, then attempt SMTP (the `telegram_relay.py:464-465` pattern). On handler failure, increment `_relay_attempts` inside the in-memory payload dict, `RPUSH` it back to the same key, and continue. After `MAX_EMAIL_RELAY_RETRIES` (default 3) failed attempts, route to `bridge.email_dead_letter.write_dead_letter()` and do NOT re-push. Crash-mid-send semantics: the entry has been popped but not yet re-pushed — equivalent to an at-most-once delivery on an SMTP the bridge probably never completed anyway. The SMTP server's own anti-dup (Message-ID uniqueness) prevents duplicates if the crash happened post-send but pre-ack. This is acceptable for email; we accept the occasional at-most-once edge case in exchange for simplicity and consistency with `telegram_relay.py`. A peek-then-LPOP design was considered and rejected because it introduces a race window where two relay instances (or a crashed-and-restarted relay) can both see the same entry and double-send — strictly worse than accepting the rare at-most-once edge.

## No-Gos (Out of Scope)

- HTML email composition (plain text only).
- Multi-mailbox / multi-account support (only `INBOX` in v1).
- Gmail API / `gws gmail` integration (IMAP/SMTP only).
- `--follow` / IMAP IDLE streaming mode.
- Encryption (S/MIME, PGP).
- Contact-book / address-book management (compose against a raw email address only).
- Changes to `valor-telegram` CLI (the `secrets.token_hex` session_id fix applies only to `valor-email`).
- New MCP server for email (see Agent Integration — bridge already has drafter/handler plumbing).
- Search of arbitrary IMAP folders (only the history cache + INBOX).
- `--direct` SMTP fallback flag (see Rabbit Holes for rationale).
- Short-index `--reply-to` (raw Message-ID only; short-index is a v2 follow-up per spike-3).

## Update System

The update script (`scripts/remote-update.sh`) must trigger an email-relay start on machines that run the bridge. Specific changes:

- **`scripts/valor-service.sh`**:
  - `email-start`: no change if the relay runs inside `run_email_bridge()` via `asyncio.gather` (preferred design). If run as a separate process, add a new `email-relay-start` target.
  - `email-status`: extend output to include the relay heartbeat — `GET email:relay:last_poll_ts`, compute age, print `relay healthy (last poll Xs ago)` or `relay stale (>5 minutes) — restart via email-restart`.
  - `email-restart`: must cycle both the poll loop and the relay (one process under the preferred design).
- **New env vars:** none — the CLI reuses existing `IMAP_*` / `SMTP_*` / `REDIS_URL` secrets.
- **Migration:** no data migration required. Existing `email:msgid:*` keys coexist with new `email:history:*` keys. Existing DLQ entries are unchanged. The `email:outbox:*` queue is dormant today (no consumer), so changing its payload shape is not a breaking change. In-flight entries (none expected, but possible on a loaded host) are handled by the relay's legacy-`text` normalization path (see Unified outbox payload contract in Technical Approach).
- **Rollback (ordered sequence):**
  1. Revert the PR via `git revert`. This removes `tools/valor_email.py`, `bridge/email_relay.py`, `tools/email_history/`, the write-through calls in `_email_inbox_loop`, the `_build_reply_mime` refactor, the `send_message.py::_send_via_email` payload rewrite, and the `pyproject.toml` entry.
  2. Run `pip install -e .` so the `valor-email` script is deregistered from `$PATH`.
  3. Restart the bridge: `./scripts/valor-service.sh email-restart`. (This also clears the relay asyncio task.)
  4. Flush the new Redis namespaces (all optional — TTLs bound the leak at 7 days; do this only if you need an immediate clean slate): `redis-cli --scan --pattern 'email:history:*' | xargs redis-cli DEL`; same for `email:threads`, `email:outbox:*`, and `email:relay:last_poll_ts`.
  5. Verify: `valor-telegram --help` still works; `valor-email` is no longer on `$PATH`; `tests/unit/test_email_bridge.py` passes against the reverted `_build_reply`.

Update skill (`scripts/remote-update.sh` + `.claude/skills/update/SKILL.md`) needs one line verifying `valor-email --help` resolves after the dependency sync step, mirroring how `valor-telegram` is verified today.

## Agent Integration

- **No new MCP server.** The agent already sends email via `tools/send_message.py` which writes to `email:outbox:{session_id}` — that path is unchanged.
- **`.mcp.json`:** no changes.
- **`bridge/telegram_bridge.py`:** no direct imports — the relay is started alongside the email bridge in `worker/__main__.py` or `bridge/email_bridge.py::run_email_bridge` via `asyncio.gather`.
- **Integration test:** `tests/integration/test_valor_email.py::test_cli_send_drains_via_relay_to_smtp` spins up the relay in a background task, pushes a payload, asserts a mocked SMTP call receives the message.
- **Memory system:** no integration. The CLI is a dev tool, not part of the agent loop.

## Documentation

### Feature Documentation
- [x] Update `docs/features/email-bridge.md` — append a "### CLI (`valor-email`)" section covering read/send/threads usage and the relay architecture. Add the `bridge/email_relay.py` role to the Key Modules table.
- [x] Add entry for `valor-email` in `docs/features/README.md` index table under the email-bridge feature row (or create a new row if cleaner).

### Inline Documentation
- [x] Docstrings on `tools/valor_email.py` module, `cmd_read`, `cmd_send`, `cmd_threads`, and `main`.
- [x] Docstring on `bridge/email_relay.py` module explaining the drain-retry-DLQ contract and the invariant that `EmailOutputHandler.send()` does NOT write to the outbox.
- [x] Docstrings on `tools/email_history/` public helpers.
- [x] Inline comment in `_email_inbox_loop` explaining the write-through to the history cache.

### Quick-Reference Tables
- [x] Update `CLAUDE.md` "Reading Telegram Messages" section or add a new "Reading Email" section with `valor-email` examples.
- [x] Update `CLAUDE.md` Quick Commands table if email-start already lists relay start semantics; otherwise note it runs within the email bridge process.

## Success Criteria

- [x] `valor-email read --limit 5` outputs the 5 most recent emails from the history cache (or falls back to IMAP on cache miss, with `readonly=True` and sender filter applied).
- [x] `valor-email read --search "test"` filters correctly.
- [x] `valor-email read --since "2 hours ago"` filters correctly.
- [x] `valor-email send --to addr@x --subject "Sub" "Body"` enqueues the unified payload shape and the relay drains it via mocked SMTP.
- [x] `valor-email send --to addr --file ./path.txt "Caption"` composes a `MIMEMultipart` with the file attached; mocked SMTP receives the payload with correct `Content-Disposition`.
- [x] `valor-email send --reply-to "<abc@host>" "Body"` produces `In-Reply-To: <abc@host>` and `References: <abc@host>` in the mocked SMTP message.
- [x] `valor-email threads` lists threads from the `email:threads` hash.
- [x] `--json` flag works on all three subcommands.
- [x] `pyproject.toml` registers `valor-email`; `pip install -e .` makes the CLI available on `$PATH`.
- [x] Relay atomic-LPOP behavior verified: on SMTP failure, the payload is re-pushed with `_relay_attempts` incremented; after 3 failures, DLQ receives it and the queue is empty.
- [x] Relay accepts legacy `{session_id, to, text, timestamp}` payload (text→body normalization test).
- [x] `tools/send_message.py::_send_via_email` writes the unified shape (existing tests updated).
- [x] `email:relay:last_poll_ts` heartbeat key is written once per poll cycle with 5-minute TTL.
- [x] `email:history:*` orphan blobs are actively deleted when ZREMRANGEBYRANK evicts (no leak beyond 7-day TTL).
- [x] Session-id suffix uses `secrets.token_hex(4)` (not 2).
- [x] All new and updated tests pass: `pytest tests/unit/test_valor_email.py tests/unit/test_email_relay.py tests/unit/test_email_history.py tests/unit/test_send_message.py tests/integration/test_valor_email.py -v`.
- [x] Existing email-bridge tests still pass: `pytest tests/unit/test_email_bridge.py tests/integration/test_email_bridge.py -v`.
- [x] `docs/features/email-bridge.md` has a CLI section; `docs/features/README.md` updated; `CLAUDE.md` has `valor-email` examples.
- [x] `ruff check` and `ruff format --check` pass on all new files.
- [x] Parsed-header regression test asserts `_build_reply_mime(attachments=None, ...)` produces semantically equivalent MIME to the old `_build_reply(...)` (From/To/Subject/In-Reply-To/References/Content-Type/Content-Transfer-Encoding + payload bytes; excludes Date/Message-ID).
- [ ] Live-environment smoke test passes when SMTP/IMAP/Redis are configured, or is logged as skipped. *(Out of scope: no automated live smoke test was added — integration tests cover the same surface area via mocked SMTP. Live verification is a manual/operational check.)*

## Team Orchestration

### Team Members

- **Builder (cli)**
  - Name: cli-builder
  - Role: Implement `tools/valor_email.py` and `tools/email_history/` package
  - Agent Type: builder
  - Resume: true

- **Builder (relay)**
  - Name: relay-builder
  - Role: Implement `bridge/email_relay.py` and refactor `_build_reply` → `_build_reply_mime` with attachment support; wire relay into bridge event loop
  - Agent Type: builder
  - Resume: true

- **Builder (cache)**
  - Name: cache-builder
  - Role: Add `_record_history` and `_record_thread` write-through calls to `_email_inbox_loop`
  - Agent Type: builder
  - Resume: true

- **Test-engineer**
  - Name: test-engineer
  - Role: Unit + integration tests for CLI, relay, cache, history helpers
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: email-cli-validator
  - Role: Verify all success criteria, run full test suite, confirm byte-regression on `_build_reply_mime`
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: documentarian
  - Role: Update `docs/features/email-bridge.md`, `docs/features/README.md`, `CLAUDE.md`
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Refactor `_build_reply` → `_build_reply_mime` with attachment support
- **Task ID**: build-mime-refactor
- **Depends On**: none
- **Validates**: `tests/unit/test_email_bridge.py` (all existing tests still pass); new parsed-header regression test added
- **Informed By**: Risk 2 mitigation (parsed-header regression, not byte-for-byte)
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: true
- Extract `EmailOutputHandler._build_reply` to a module-level `_build_reply_mime(to, subject, body, in_reply_to, references, from_addr, attachments=None)`.
- Accept `attachments: list[Path] | None = None`. When None or empty, return `MIMEText` (current behavior).
- When attachments provided, return `MIMEMultipart("mixed")` with `MIMEText` body first, then one `MIMEBase` part per attachment with `Content-Disposition: attachment; filename="..."` (use `mimetypes.guess_type` for the MIME type; fallback `application/octet-stream`).
- Update `EmailOutputHandler.send` to call the module-level helper with `attachments=None`.
- Add **parsed-header regression test** (Risk 2 mitigation): for a fixed input, parse both old `_build_reply(...)` and new `_build_reply_mime(..., attachments=None)` via `email.message_from_bytes(msg.as_bytes())`; assert `From`, `To`, `Subject`, `In-Reply-To`, `References`, `Content-Type`, `Content-Transfer-Encoding` headers match, plus `get_payload(decode=True)` bytes. Exclude `Date` and `Message-ID` from comparison.

### 2. Create `bridge/email_relay.py`
- **Task ID**: build-relay
- **Depends On**: build-mime-refactor
- **Validates**: `tests/unit/test_email_relay.py` (create)
- **Informed By**: `bridge/telegram_relay.py:443-572` (atomic LPOP, re-push on failure, DLQ after 3 attempts)
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `bridge/email_relay.py` with `run_email_relay()` async coroutine and a `process_outbox()` helper mirroring the telegram relay.
- Poll `email:outbox:*` via `r.keys(OUTBOX_KEY_PATTERN)` every 100ms.
- For each key, atomically `r.lpop(key)` (wrapped in `asyncio.to_thread`). If nothing returned, move on.
- Parse JSON. **Normalize** the payload: if `text` is present and `body` is not, rename `text`→`body` (legacy compat); fill defaults (`subject="(no subject)"`, `from_addr=SMTP_USER`, `attachments=[]`).
- Reject malformed payloads (missing `to` or missing both `text`/`body`) — log and DLQ without retry.
- Call `_build_reply_mime(...)`, attempt `smtplib.SMTP` send in `asyncio.to_thread`.
- On success: done (entry already popped).
- On failure: increment `_relay_attempts` in-memory, `r.rpush(key, json.dumps(message))`. After `MAX_EMAIL_RELAY_RETRIES` (default 3), call `bridge.email_dead_letter.write_dead_letter()` instead of re-pushing.
- On each poll cycle (regardless of work found), `r.set("email:relay:last_poll_ts", time.time(), ex=300)` — 5-minute TTL heartbeat for liveness probing.
- Wire into `bridge/email_bridge.py::run_email_bridge` via `asyncio.gather(run_email_relay(...), _email_inbox_loop(...))`.
- Include a module docstring that states the invariant: `EmailOutputHandler.send()` sends directly and never writes to `email:outbox:*`; the relay and the handler do not race.

### 3. Add write-through cache to `_email_inbox_loop`
- **Task ID**: build-cache-write
- **Depends On**: none
- **Validates**: `tests/integration/test_email_bridge.py::test_email_inbox_loop_*` (update)
- **Informed By**: Race 1 mitigation (write JSON blob before ZADD, use pipeline)
- **Assigned To**: cache-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_record_history(parsed: dict) -> None` helper: writes `email:history:msg:{msgid}` (string JSON, TTL 7d) then ZADDs `email:history:INBOX` with timestamp score. Use Redis pipeline for atomicity.
- Trim: `r.zremrangebyrank("email:history:INBOX", 0, -501)` after each write to cap at 500.
- Add `_record_thread(parsed: dict) -> None` helper: updates `email:threads` hash; thread root is the chain head (follow `in_reply_to` until None). Store thread metadata as JSON.
- Call both helpers in `_email_inbox_loop` after `_process_inbound_email` succeeds. Wrap each call in try/except so cache failures never break the poll loop (log warning only).

### 4. Create `tools/email_history/` package
- **Task ID**: build-history-package
- **Depends On**: build-cache-write
- **Validates**: `tests/unit/test_email_history.py` (create)
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/email_history/__init__.py` with:
  - `get_recent_emails(mailbox="INBOX", limit=10) -> dict` — ZREVRANGE + hydrate
  - `search_history(query, mailbox="INBOX", max_results=10, max_age_days=7) -> dict` — scan the sorted set, filter JSON blobs by body/subject substring match
  - `list_threads() -> dict` — HGETALL `email:threads`
- All return `{"error": str}` on Redis failure; never raise.
- **No `list_mailboxes()` scaffold** — YAGNI. Add when multi-mailbox support actually lands (currently a No-Go).

### 5. Create `tools/valor_email.py` CLI and update `tools/send_message.py` payload
- **Task ID**: build-cli
- **Depends On**: build-history-package, build-relay
- **Validates**: `tests/unit/test_valor_email.py` (create); `tests/unit/test_send_message.py` (update existing email-path tests)
- **Informed By**: `tools/valor_telegram.py` (structural mirror), Race 2 mitigation (session_id with pid + `secrets.token_hex(4)`)
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: false
- `cmd_read`: uses `tools.email_history.get_recent_emails` / `search_history`. On empty cache, falls back to direct `imaplib.IMAP4_SSL` with `readonly=True` AND sender-filter query built from `bridge.routing.get_known_email_search_terms()` (per spike-4 — prevents marking other machines' UNSEEN mail as SEEN and leaking cross-project messages).
- `cmd_send`: builds the **unified payload** (`{session_id, to, subject, body, attachments, in_reply_to, references, from_addr, timestamp}`), pushes to `email:outbox:cli-{int(time.time())}-{os.getpid()}-{secrets.token_hex(4)}` with TTL 1h. After enqueue: print `"Queued. Check delivery via ./scripts/valor-service.sh email-status"` and exit 0.
- `cmd_threads`: uses `tools.email_history.list_threads`.
- `--json` flag on all three subcommands.
- `--reply-to`: `type=` validator normalizes to angle-bracketed form.
- Attachment validation at CLI layer (file exists, readable) before enqueue.
- Import `parse_since` from `tools.valor_telegram` (the one piece of shared code).
- Register in `pyproject.toml` `[project.scripts]`: `valor-email = "tools.valor_email:main"`.
- **Update `tools/send_message.py::_send_via_email`**: rewrite the payload to match the unified contract (`body` replaces `text`; include `subject`, `in_reply_to`, `references`, `from_addr`, `attachments` even if empty). Update existing unit tests to assert the new shape. Note in the docstring that the relay accepts legacy `text` for one transitional release.

### 6. Integration tests
- **Task ID**: build-integration-tests
- **Depends On**: build-cli, build-relay, build-cache-write
- **Validates**: `tests/integration/test_valor_email.py` (create)
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- End-to-end: CLI send → Redis queue → relay drain → mocked SMTP → success.
- End-to-end: CLI send with `--file` → relay drain → mocked SMTP with attachment verified.
- End-to-end: `--reply-to "<abc@host>"` → mocked SMTP receives correct `In-Reply-To` / `References` headers.
- End-to-end: poll loop writes to cache → CLI `read` returns cached entries.
- **Legacy-payload compat:** relay unit test pushes a `{session_id, to, text, timestamp}` payload directly and asserts the relay drains it successfully (the text→body normalization path).
- **Heartbeat probe:** relay unit test asserts `email:relay:last_poll_ts` is written within one poll cycle.
- Failure path: SMTP fails 3 times → DLQ entry written; relay does NOT re-push after the 3rd failure; operator can see the queue is empty and DLQ has one entry.

### 7. Validation
- **Task ID**: validate-all
- **Depends On**: build-mime-refactor, build-relay, build-cache-write, build-history-package, build-cli, build-integration-tests
- **Assigned To**: email-cli-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_email_bridge.py tests/unit/test_email_relay.py tests/unit/test_email_history.py tests/unit/test_valor_email.py tests/unit/test_send_message.py tests/integration/test_email_bridge.py tests/integration/test_valor_email.py -v`.
- Run `ruff check .` and `ruff format --check .`.
- Confirm parsed-header regression test passes for `_build_reply_mime(attachments=None)`.
- Verify `pip install -e .` then `which valor-email` resolves.
- Confirm `valor-email --help` prints all three subcommands.
- **Live-environment smoke test** (only runs when `IMAP_HOST`/`SMTP_HOST`/`REDIS_URL` are configured; skipped otherwise with a logged reason):
  1. `valor-email read --limit 1 --json` — exit 0, output parseable as JSON (may be `[]` if cache empty and IMAP has nothing matching the sender filter).
  2. `valor-email send --to $SMTP_USER --subject "smoke-test" "test body"` to a self-address — exit 0.
  3. Within 5 seconds, assert `email:relay:last_poll_ts` exists in Redis AND (the outbox key was drained OR a DLQ entry was created). This is a loopback test against the local dev SMTP/IMAP — safe to run in CI if env is present.
- Report pass/fail per success criterion.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Append "### CLI (`valor-email`)" section to `docs/features/email-bridge.md`; add `bridge/email_relay.py` to the Key Modules table.
- Update `docs/features/README.md` index.
- Update `CLAUDE.md`: add a "Reading Email" section with read/send/threads examples mirroring the Telegram section.
- Add docstrings on all new modules and public functions.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_valor_email.py tests/unit/test_email_relay.py tests/unit/test_email_history.py -x -q` | exit code 0 |
| Send-message tests updated | `pytest tests/unit/test_send_message.py -x -q` | exit code 0 |
| Email-bridge tests unbroken | `pytest tests/unit/test_email_bridge.py tests/integration/test_email_bridge.py -x -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_valor_email.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| CLI installed | `which valor-email` | output contains valor-email |
| CLI help | `valor-email --help 2>&1` | output contains {read,send,threads} |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_valor_email.py tests/unit/test_email_relay.py tests/unit/test_email_history.py` | exit code 1 |
| Parsed-header regression | `pytest tests/unit/test_email_bridge.py::TestBuildReplyMimeHeaderRegression::test_build_reply_mime_header_regression -x -q` | exit code 0 |
| Legacy payload compat | `pytest tests/unit/test_email_relay.py::TestProcessOutboxSend::test_drains_legacy_text_payload -x -q` | exit code 0 |
| Heartbeat written | `pytest tests/unit/test_email_relay.py::TestProcessOutboxHeartbeat::test_heartbeat_written_each_cycle -x -q` | exit code 0 |

## Critique Results

Critique run 2026-04-21. Verdict: NEEDS REVISION (2 blockers, 6 concerns, 3 nits). Revision applied in same commit as this table update. `revision_applied=true`.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| B1 | Archaeologist | Outbox payload schema drift — `send_message.py:154-158` writes legacy `{session_id, to, text, timestamp}`; new CLI writes richer shape. Relay would `KeyError` on existing agent-originated payloads. | Technical Approach §Unified outbox payload contract; Task 5 now rewrites `tools/send_message.py::_send_via_email`; Risk 5 added; integration test for legacy compat | Relay normalizes `text`→`body` on read for one transitional release; both writers updated same-PR; queue has no consumer today so it's lossless. |
| B2 | Adversary | Non-atomic peek-then-LPOP in relay drain contradicts `telegram_relay.py` mirror directive and is a correctness bug. | Data Flow step 5; Technical Approach §Relay contract; Race 3 rewrite; Task 2 rewrite | Switched to atomic LPOP + re-push on failure + DLQ after 3 attempts, mirroring `telegram_relay.py:464-465`. Accept at-most-once on crash-mid-send; SMTP Message-ID uniqueness prevents duplicate delivery. |
| C1 | Operator | Relay liveness probe gap — plan references `email:last_poll_ts` stale-check without specifying where it's written. | Technical Approach §Relay contract; Update System §`email-status`; Task 2 step "heartbeat" | Relay writes `email:relay:last_poll_ts` with 5-minute TTL on each poll cycle. `email-status` service command reads it. |
| C2 | Simplifier | Byte-regression test fragility — excludes Date/Message-ID but still sensitive to Python minor version header order and encoding drift. | Risk 2 rewrite; Task 1 rewrite; Verification table | Replaced byte-for-byte with parsed-header comparison (From/To/Subject/In-Reply-To/References/Content-Type/Content-Transfer-Encoding + payload bytes). |
| C3 | Skeptic | Unjustified `--direct` flag adds a second send site for no clear benefit. | Data Flow step 6; Technical Approach (flag removed); No-Gos; Rabbit Holes | Dropped `--direct`. CLI always queues via Redis; operator fixes relay via `email-status`/`email-restart`. Parity with `valor-telegram send`. |
| C4 | Archaeologist | Incomplete rollback sequence — doesn't cover `email:outbox:*`, `email:relay:last_poll_ts`, or `send_message.py` revert. | Update System §Rollback (ordered sequence) | Five-step rollback sequence added covering revert, reinstall, restart, flush (including new heartbeat/outbox keys), and verification. |
| C5 | Simplifier | `email:threads` drift risk when a later message reveals an earlier root. | Technical Approach §Cache key schema (threads bullet) | Thread root recomputed on each new message; re-key the hash if an earlier root is discovered. Drift accepted for v1 as a best-effort nav aid. |
| C6 | Operator | History-cache orphan blob leak — `ZREMRANGEBYRANK` evicts IDs from the sorted set but leaves per-msg blobs until 7-day TTL. | Technical Approach §Cache key schema (cap enforcement bullet) | Trim path uses pipeline: `ZRANGE 0 -501` to capture evicted IDs → `DEL email:history:msg:{msgid}` → `ZREMRANGEBYRANK`. Active deletion bounds the leak. |
| N1 | Adversary | `secrets.token_hex(2)` collision risk — 16 bits is narrow margin. | Race 2 rewrite | Upgraded to `secrets.token_hex(4)` (32 bits of randomness). |
| N2 | Simplifier | Unused `list_mailboxes()` scaffold violates YAGNI. | Task 4 (removed from scope) | Dropped; added when multi-mailbox feature actually lands. |
| N3 | Operator | No live-environment verification step. | Task 7 (live smoke test) | Added conditional smoke test that runs when SMTP/IMAP/Redis are configured; skipped-with-reason otherwise. |

---

## Open Questions

All prior questions resolved by critique revision:

- ~~Q1 Relay lifecycle~~ → resolved: `asyncio.gather()` inside `run_email_bridge` (Task 2, Update System §`email-restart`).
- ~~Q2 Direct-SMTP default~~ → resolved: `--direct` flag dropped entirely per C3 (Rabbit Holes, No-Gos).
- ~~Q3 `valor-telegram` session_id fix~~ → resolved: kept scoped to `valor-email`; Telegram CLI change is a separate concern if it matters.
- ~~Q4 Multi-account future-proofing~~ → resolved: accept migration pain later. Single-account is firmly No-Gos, and reshaping a sorted-set key when multi-account arrives is a 30-line script, not architecture.

None remaining.
