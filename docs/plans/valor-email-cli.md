---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-04-21
tracking: https://github.com/tomcounsell/ai/issues/1067
last_comment_id:
---

# valor-email CLI

## Problem

When working in a terminal (Claude Code or ad-hoc dev session), reading recent emails or sending a quick reply requires either `gws gmail` (verbose, OAuth-context-dependent) or raw `python -m bridge.email_bridge` commands. The Telegram equivalent — `valor-telegram read / send / chats` — is ergonomic, Redis-cached, and bridge-agnostic. Email has no such surface.

**Current behavior:**
- Reading email requires `gws gmail users messages list --params '{"userId":"me","maxResults":5}'` — verbose JSON args, no Redis cache, no `--since` or `--search` shorthand.
- Sending email requires either (a) starting the full email bridge, (b) hand-crafting `gws gmail` POSTs, or (c) writing ad-hoc SMTP Python. There is no one-line CLI.
- No unified CLI mirrors `valor-telegram read / send / chats`. The existing `email:outbox:{session_id}` Redis queue is written to by `tools/send_message.py` (agent-facing), but nothing **drains** it — sends are only dispatched directly from `EmailOutputHandler.send()` inside the worker. The queue is effectively dormant.

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
4. **Entry point (send):** `valor-email send --to X --subject Y Z` builds a payload dict `{to, subject, body, attachments, in_reply_to, references, session_id, timestamp}` and pushes to `email:outbox:{cli-<ts>}`. TTL 1h.
5. **Relay drain:** `bridge/email_relay.py` (new) polls `email:outbox:*` every 100ms, pops payloads, invokes the shared `_build_reply_mime()` helper (extracted from `EmailOutputHandler`), and dispatches via `smtplib.SMTP`. Retries with exponential backoff (up to 3 attempts), then DLQ via `write_dead_letter()`.
6. **Direct-SMTP fallback:** if the CLI detects the relay is not running (Redis queue key has no consumer, or `email:last_poll_ts` is stale by >5 minutes), it prints a warning and optionally dispatches via direct SMTP (same `_send_smtp()` helper). Controlled by `--direct` flag (default: queue via Redis only).
7. **Threads listing:** `valor-email threads` reads a new `email:threads` hash maintained by the poll loop — each `Message-ID` → `In-Reply-To` chain collapses into a thread root, stored as `{thread_root_msgid: [child_msgid_1, child_msgid_2, ...]}`.
8. **Output:** JSON (via `--json`) or human-readable table (default), mirroring `valor-telegram`'s conventions.

## Architectural Impact

- **New dependencies:** None — everything uses stdlib (`email`, `imaplib`, `smtplib`, `mimetypes`) and existing Redis (`redis-py`).
- **Interface changes:**
  - `bridge/email_bridge.py::EmailOutputHandler._build_reply()` is refactored to accept `attachments: list[Path] | None = None` and return `MIMEMultipart` when attachments are present, else `MIMEText` (preserves backward compatibility — no call-site signature changes for the worker path).
  - `bridge/email_bridge.py::_email_inbox_loop` gains a new write-through call to `_record_history()`.
  - **New:** `bridge/email_relay.py` module with `run_email_relay()` async coroutine, mirroring `bridge/telegram_relay.py` structure.
  - **New:** `tools/valor_email.py` CLI entry point.
  - **New:** `tools/email_history/` package with `get_recent_emails()`, `search_history()`, `list_threads()` mirroring the `tools/telegram_history/` shape.
- **Coupling:** No new cross-module coupling. The CLI depends on the bridge config helpers (`_get_imap_config`, `_get_smtp_config`) — these are already module-level pure functions.
- **Data ownership:** The IMAP poll loop owns `email:history:INBOX`. The CLI and MCP tools are **readers only** for the history cache. The outbox queue is write-by-anyone, drain-by-relay — identical to Telegram's `telegram:outbox:*` model.
- **Reversibility:** Fully reversible. Removing `tools/valor_email.py`, `bridge/email_relay.py`, and the two write-through calls in `_email_inbox_loop` reverts the system to pre-plan state.

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
- **`tools/email_history/`** — small package with `get_recent_emails()`, `search_history()`, `list_threads()`, `list_mailboxes()`. Pure Redis reads from `email:history:*` keys.
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

CLI send (relay path, default):
$ valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"
  → tools/valor_email.py::cmd_send
    → build payload {to, subject, body, in_reply_to=None, attachments=[], ...}
    → r.rpush("email:outbox:cli-{ts}", json.dumps(payload))
    → "Message queued. Delivery requires the email relay to be running."
  → bridge/email_relay.py picks up within 100ms
    → _build_reply_mime(...) → smtp.sendmail(...) → success OR DLQ

CLI send (direct fallback):
$ valor-email send --to alice@example.com --direct "Quick note"
  → _send_direct_smtp(...) — same _send_smtp helper, synchronous

CLI threads:
$ valor-email threads
  → tools/email_history/__init__.py::list_threads()
    → r.hgetall("email:threads")
  → human-readable table: thread root subject, message count, last activity
```

### Technical Approach

- **Mirror valor_telegram.py structure**: argparse with three subparsers (`read`, `send`, `threads`), `--json` flag on all, shared helpers (`parse_since`, `format_timestamp`, `_get_redis_connection`) — copy-paste-adapt rather than extract, to keep each CLI tool self-contained. Cross-CLI extraction is a rabbit hole (see Rabbit Holes).
- **Cache key schema:**
  - `email:history:INBOX` — sorted set, score = UNIX timestamp, member = `Message-ID` string. Allows `ZREVRANGE` for recent-first, `ZRANGEBYSCORE` for `--since` filters.
  - `email:history:msg:{message_id}` — string key containing JSON blob `{from_addr, subject, body, timestamp, message_id, in_reply_to}`. TTL 7 days.
  - `email:threads` — hash, field = thread root `Message-ID`, value = JSON `{subject, message_count, last_ts, participants}`.
  - Cap enforcement: after each write, `ZREMRANGEBYRANK("email:history:INBOX", 0, -501)` trims to 500 newest.
- **`--since` parsing:** reuse `tools/valor_telegram.parse_since` verbatim by importing it. (Same parser semantics: `"1 hour ago"`, `"2 days ago"`, `"30 minutes ago"`.) This is the one piece of shared code — everything else is copy-paste-adapt.
- **`--reply-to` semantics:** accepts a Message-ID string (angle-bracketed or not; normalize to `<...>` form). CLI sets both `In-Reply-To` and `References` from the same value. This differs from Telegram's integer `reply_to`; the argparse `type=` differs accordingly.
- **Attachment payload shape:** payload carries `"attachments": [absolute_path, ...]` — the CLI validates file existence **before** enqueueing (fail fast, mirror valor-telegram). The relay re-validates at drain time and DLQs if the file was deleted.
- **Direct SMTP fallback:** invoked only when user passes `--direct`. Avoids race conditions with the relay (if both tried to send from the same session_id key, we'd get duplicates). `--direct` bypasses the queue entirely.
- **Drafter integration:** the CLI does NOT route through `bridge.message_drafter.draft_message(medium="email")`. The drafter is for agent-originated text; CLI text is user-authored and should pass through verbatim. This matches `valor-telegram send` behavior (which only applies `_linkify_text`, not the full drafter).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/email_bridge.py::_record_history` — wrap in try/except; failures log `logger.warning` but MUST NOT break the poll loop. Test asserts `caplog` contains warning on Redis down.
- [ ] `tools/valor_email.py::cmd_send` — Redis push failure prints to stderr with exit code 1; direct-SMTP fallback failure prints error with exit code 1. Tests simulate `redis.ConnectionError` and `smtplib.SMTPException`.
- [ ] `bridge/email_relay.py::_drain_outbox` — SMTP failure triggers retry with backoff; after 3 attempts, routes to DLQ. Tests assert DLQ write on exhausted retries (mirror `test_email_bridge.py` DLQ pattern).
- [ ] `tools/email_history/__init__.py::get_recent_emails` — Redis read failure returns `{"error": str}` dict; CLI prints the error with exit code 1.

### Empty/Invalid Input Handling
- [ ] `valor-email send` with no text AND no `--file` → error "Must provide a message or file" (mirror `valor-telegram`).
- [ ] `valor-email read` with empty cache AND IMAP fallback disabled → prints "No messages found." exit 0.
- [ ] `valor-email send --reply-to ""` → argparse rejects (min length 1 via custom type validator).
- [ ] `parse_since("yesterday")` returns None → caller treats as no filter (same as `valor-telegram`).
- [ ] `cmd_send` with empty body but a file attachment → valid (matches Telegram `--file` without caption pattern).
- [ ] MIME assembly with empty body AND no attachments → reject at CLI layer before enqueue.

### Error State Rendering
- [ ] CLI error output goes to stderr; successful output goes to stdout. `--json` always goes to stdout.
- [ ] Dead-letter rendering: when the relay DLQs a CLI-originated send, the user has no direct feedback path (the CLI exited after enqueue). Surface this via `./scripts/valor-service.sh email-dead-letter list` — existing surface, no new UX needed. Document in CLI help epilog.

## Test Impact

- [ ] `tests/unit/test_email_bridge.py::test_build_reply_*` — UPDATE: refactored `_build_reply` signature. Existing assertions still valid for no-attachment path; add new cases for attachment path.
- [ ] `tests/unit/test_email_bridge.py::TestEmailOutputHandler::test_send_*` — UPDATE: `_build_reply_mime` is now module-level; adjust imports if tests patched the method.
- [ ] `tests/integration/test_email_bridge.py::test_email_inbox_loop_*` — UPDATE: add assertion that `email:history:INBOX` receives writes after a processed inbound.

No other existing tests affected. New test files:
- `tests/unit/test_valor_email.py` — mirror `test_valor_telegram.py` structure; mock Redis/IMAP/SMTP.
- `tests/unit/test_email_relay.py` — mirror `test_telegram_relay.py` patterns (if it exists; otherwise mirror `test_email_bridge.py` DLQ patterns).
- `tests/unit/test_email_history.py` — cache read helpers; Redis fake or patched.
- `tests/integration/test_valor_email.py` — end-to-end: CLI → Redis → relay → mocked SMTP → received.

## Rabbit Holes

- **Don't extract a shared CLI helper module.** `valor_telegram.py` and `valor_email.py` will share `parse_since`, `format_timestamp`, and `_get_redis_connection`. Extracting these to `tools/cli_common.py` is tempting but premature — only 3 functions, stable APIs, no third caller. Copy-paste with an import for `parse_since` is the right tradeoff.
- **Don't implement HTML email.** The issue explicitly drops HTML composition. `MIMEText("plain", "utf-8")` only. If someone needs HTML later, it's a new issue.
- **Don't add multi-mailbox support.** The bridge polls only `INBOX`. `--mailbox` is accepted as an argument but only `INBOX` is valid in v1; other values error out. Multi-mailbox is a separate design.
- **Don't write a Gmail-API path.** Issue drops this. IMAP only.
- **Don't implement streaming/tail mode.** `valor-telegram` has no `--follow` flag; `valor-email` doesn't need one either. If needed, IMAP IDLE is a separate issue.
- **Don't restructure `EmailOutputHandler`.** Promoting `_build_reply` to a module-level helper is the minimal refactor. A full handler class split into separate builder/sender responsibilities is a separate concern — do it only if the MIME refactor forces it, which it won't.
- **Don't try to unify `email:outbox` payload shape with `telegram:outbox`.** They serve different transports with different header semantics (no `reply_to_msg_id` for email, no `In-Reply-To` for Telegram). A unified shape would be all-optional-fields and lose clarity.
- **Don't add `--watch` or daemon mode to the CLI.** The relay in `bridge/email_relay.py` is the daemon; the CLI is one-shot. Keep them separate.

## Risks

### Risk 1: Duplicate sends if both the relay drains AND `--direct` fires
**Impact:** Recipient gets two identical emails; thread breaks.
**Mitigation:** `--direct` bypasses Redis entirely. The relay only drains keys it sees. No collision possible. Additionally, the relay uses `LPOP` (atomic pop-and-delete) so a single queue entry can only be processed once. Document `--direct` in CLI help with a warning.

### Risk 2: History cache fills Redis if IMAP gets a flood of inbound from many senders
**Impact:** Redis memory pressure; eviction of other keys.
**Mitigation:** Hard cap of 500 entries per mailbox (ZREMRANGEBYRANK after each write). TTL of 7 days on individual message JSON keys. Monitor with existing Redis memory alerting. Document in `docs/features/email-bridge.md`.

### Risk 3: `_build_reply_mime` refactor subtly changes existing worker-path behavior
**Impact:** Agent replies stop threading correctly or get spam-flagged.
**Mitigation:** Keep `attachments=None` as the default; for no-attachment calls, produce a `MIMEText` object with identical headers to today's output. Add a byte-for-byte regression test that asserts the MIME output of `_build_reply_mime(to, subj, body, inreply, refs, from_addr, attachments=None)` equals the current `_build_reply(...)` output for a known fixture. (Exception: `Message-ID` and `Date` headers vary per call — exclude those from the byte comparison.)

### Risk 4: Relay races with `EmailOutputHandler.send()` on the same session_id
**Impact:** Two SMTP sends for one agent output.
**Mitigation:** The worker's `EmailOutputHandler.send()` does NOT push to `email:outbox:*` — it sends directly. The outbox is ONLY used by CLI (and `tools/send_message.py`). The relay has no overlap with `EmailOutputHandler.send()`. Verify with a grep for `email:outbox` in `agent/` and `worker/`: all writes come from `tools/` or `bridge/email_relay.py`. Document this invariant in `bridge/email_relay.py` module docstring.

### Risk 5: Bridge restart is required to pick up `bridge/email_relay.py`
**Impact:** After merging, if nobody restarts the bridge, CLI `send` will hang messages in Redis.
**Mitigation:** CLI prints "Note: delivery requires the email relay to be running (./scripts/valor-service.sh email-status)." after enqueue — same pattern as `valor-telegram`. Update `scripts/valor-service.sh` if necessary so `email-start` also starts the relay. Document in plan's Update System section.

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
**Mitigation:** Use `f"cli-{int(time.time())}-{os.getpid()}"` or a 4-byte random suffix (`secrets.token_hex(2)`) to disambiguate. Matches `valor-telegram`'s pattern — which also has this bug today; fix both CLIs in this plan, or accept the bug as out-of-scope for Telegram (pick the latter — adding PID+random to the email CLI only is scoped; changing `valor-telegram` is out of scope).
**Note:** The pid+token_hex fix applies only to `valor_email.py`. Modifying `valor_telegram.py` is out of scope.

### Race 3: Relay drains an outbox entry during bridge restart
**Location:** `bridge/email_relay.py::_drain_outbox`.
**Trigger:** Bridge restart kills the relay mid-SMTP-send.
**Data prerequisite:** Payload must not be lost if the SMTP call hasn't completed.
**State prerequisite:** The outbox key must still exist after `LPOP`.
**Mitigation:** Use `LPOP` after SMTP success, not before — i.e., peek with `LRANGE 0 0`, attempt SMTP, then `LPOP` only on success. On failure, retry in-place without removing. Mirror `telegram_relay.py`'s `_relay_attempts` counter pattern: re-push with incremented counter on failure, DLQ after 3. The worker retry already does this for the handler path.

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

## Update System

The update script (`scripts/remote-update.sh`) must trigger an email-relay start on machines that run the bridge. Specific changes:

- **`scripts/valor-service.sh`**: extend `email-start` to also start the new relay coroutine (or, if the relay is integrated into `run_email_bridge()` via `asyncio.gather`, no script change is needed — the bridge process already carries it).
- **New env vars:** none — the CLI reuses existing `IMAP_*` / `SMTP_*` / `REDIS_URL` secrets.
- **Migration:** no data migration. Existing `email:msgid:*` keys coexist with new `email:history:*` keys. Existing DLQ entries are unchanged.
- **Rollback:** remove the relay task from the event loop, delete `email:history:*` and `email:threads` keys (optional — they'll expire after 7 days anyway). CLI entry in `pyproject.toml` is removed; `pip install -e .` will no longer expose `valor-email`.

Update skill (`scripts/remote-update.sh` + `.claude/skills/update/SKILL.md`) needs one line verifying `valor-email --help` resolves after the dependency sync step, mirroring how `valor-telegram` is verified today.

## Agent Integration

- **No new MCP server.** The agent already sends email via `tools/send_message.py` which writes to `email:outbox:{session_id}` — that path is unchanged.
- **`.mcp.json`:** no changes.
- **`bridge/telegram_bridge.py`:** no direct imports — the relay is started alongside the email bridge in `worker/__main__.py` or `bridge/email_bridge.py::run_email_bridge` via `asyncio.gather`.
- **Integration test:** `tests/integration/test_valor_email.py::test_cli_send_drains_via_relay_to_smtp` spins up the relay in a background task, pushes a payload, asserts a mocked SMTP call receives the message.
- **Memory system:** no integration. The CLI is a dev tool, not part of the agent loop.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/email-bridge.md` — append a "### CLI (`valor-email`)" section covering read/send/threads usage and the relay architecture. Add the `bridge/email_relay.py` role to the Key Modules table.
- [ ] Add entry for `valor-email` in `docs/features/README.md` index table under the email-bridge feature row (or create a new row if cleaner).

### Inline Documentation
- [ ] Docstrings on `tools/valor_email.py` module, `cmd_read`, `cmd_send`, `cmd_threads`, and `main`.
- [ ] Docstring on `bridge/email_relay.py` module explaining the drain-retry-DLQ contract and the invariant that `EmailOutputHandler.send()` does NOT write to the outbox.
- [ ] Docstrings on `tools/email_history/` public helpers.
- [ ] Inline comment in `_email_inbox_loop` explaining the write-through to the history cache.

### Quick-Reference Tables
- [ ] Update `CLAUDE.md` "Reading Telegram Messages" section or add a new "Reading Email" section with `valor-email` examples.
- [ ] Update `CLAUDE.md` Quick Commands table if email-start already lists relay start semantics; otherwise note it runs within the email bridge process.

## Success Criteria

- [ ] `valor-email read --limit 5` outputs the 5 most recent emails from the history cache (or falls back to IMAP on cache miss).
- [ ] `valor-email read --search "test"` filters correctly.
- [ ] `valor-email read --since "2 hours ago"` filters correctly.
- [ ] `valor-email send --to addr@x --subject "Sub" "Body"` enqueues a message and the relay drains it via mocked SMTP.
- [ ] `valor-email send --to addr --file ./path.txt "Caption"` composes a `MIMEMultipart` with the file attached; mocked SMTP receives the payload with correct `Content-Disposition`.
- [ ] `valor-email send --reply-to "<abc@host>" "Body"` produces `In-Reply-To: <abc@host>` and `References: <abc@host>` in the mocked SMTP message.
- [ ] `valor-email send --direct "Body" --to addr` bypasses Redis and sends directly via SMTP (mocked).
- [ ] `valor-email threads` lists threads from the `email:threads` hash.
- [ ] `--json` flag works on all three subcommands.
- [ ] `pyproject.toml` registers `valor-email`; `pip install -e .` makes the CLI available on `$PATH`.
- [ ] All new and updated tests pass: `pytest tests/unit/test_valor_email.py tests/unit/test_email_relay.py tests/unit/test_email_history.py tests/integration/test_valor_email.py -v`.
- [ ] Existing email-bridge tests still pass: `pytest tests/unit/test_email_bridge.py tests/integration/test_email_bridge.py -v`.
- [ ] `docs/features/email-bridge.md` has a CLI section; `docs/features/README.md` updated; `CLAUDE.md` has `valor-email` examples.
- [ ] `ruff check` and `ruff format --check` pass on all new files.
- [ ] Byte-regression test asserts `_build_reply_mime(attachments=None, ...)` produces the same MIME output as the old `_build_reply(...)` (excluding Date/Message-ID headers).

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
- **Validates**: `tests/unit/test_email_bridge.py` (all existing tests still pass); new byte-regression test added
- **Informed By**: Risk 3 mitigation (byte-regression test)
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: true
- Extract `EmailOutputHandler._build_reply` to a module-level `_build_reply_mime(to, subject, body, in_reply_to, references, from_addr, attachments=None)`.
- Accept `attachments: list[Path] | None = None`. When None or empty, return `MIMEText` (current behavior, byte-identical except for Date/Message-ID).
- When attachments provided, return `MIMEMultipart("mixed")` with `MIMEText` body first, then one `MIMEBase` part per attachment with `Content-Disposition: attachment; filename="..."` (use `mimetypes.guess_type` for the MIME type; fallback `application/octet-stream`).
- Update `EmailOutputHandler.send` to call the module-level helper with `attachments=None`.
- Add byte-regression test (Risk 3 mitigation) asserting identical output for the no-attachment path.

### 2. Create `bridge/email_relay.py`
- **Task ID**: build-relay
- **Depends On**: build-mime-refactor
- **Validates**: `tests/unit/test_email_relay.py` (create)
- **Informed By**: `bridge/telegram_relay.py` pattern (100ms poll, `_relay_attempts` counter, DLQ on exhaustion)
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `bridge/email_relay.py` with `run_email_relay()` async coroutine.
- Poll `email:outbox:*` via `r.scan_iter(match="email:outbox:*")` every 100ms.
- For each key, `LRANGE 0 0` to peek, call `_build_reply_mime(...)`, attempt `smtplib.SMTP` send in `asyncio.to_thread`.
- On success: `LPOP` the key. On failure: increment `_relay_attempts` field in the payload, re-push. After 3 attempts, route to `bridge.email_dead_letter.write_dead_letter()`.
- Wire into `bridge/email_bridge.py::run_email_bridge` via `asyncio.gather(run_email_relay(), _email_inbox_loop(...))`.

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
  - `list_mailboxes() -> dict` — currently hardcoded to `["INBOX"]`; scaffold for future
- All return `{"error": str}` on Redis failure; never raise.

### 5. Create `tools/valor_email.py` CLI
- **Task ID**: build-cli
- **Depends On**: build-history-package, build-relay
- **Validates**: `tests/unit/test_valor_email.py` (create)
- **Informed By**: `tools/valor_telegram.py` (structural mirror), Race 2 mitigation (session_id with pid+token_hex)
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: false
- `cmd_read`: uses `tools.email_history.get_recent_emails` / `search_history`. On empty cache, falls back to direct `imaplib.IMAP4_SSL` (new helper `_fetch_from_imap`).
- `cmd_send`: builds payload, pushes to `email:outbox:cli-{ts}-{pid}-{random}` with TTL 1h. `--direct` bypasses queue and calls `_send_direct_smtp()`.
- `cmd_threads`: uses `tools.email_history.list_threads`.
- `--json` flag on all three subcommands.
- `--reply-to`: `type=` validator normalizes to angle-bracketed form.
- Attachment validation at CLI layer (file exists, readable) before enqueue.
- Import `parse_since` from `tools.valor_telegram` (the one piece of shared code).
- Register in `pyproject.toml` `[project.scripts]`: `valor-email = "tools.valor_email:main"`.

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
- End-to-end: CLI `--direct` bypasses queue (assert no Redis write).
- End-to-end: poll loop writes to cache → CLI `read` returns cached entries.
- Failure path: SMTP fails 3 times → DLQ entry written; CLI surfaces "Note: delivery requires the relay..." in stderr.

### 7. Validation
- **Task ID**: validate-all
- **Depends On**: build-mime-refactor, build-relay, build-cache-write, build-history-package, build-cli, build-integration-tests
- **Assigned To**: email-cli-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_email_bridge.py tests/unit/test_email_relay.py tests/unit/test_email_history.py tests/unit/test_valor_email.py tests/integration/test_email_bridge.py tests/integration/test_valor_email.py -v`.
- Run `ruff check .` and `ruff format --check .`.
- Confirm byte-regression test passes for `_build_reply_mime(attachments=None)`.
- Verify `pip install -e .` then `which valor-email` resolves.
- Confirm `valor-email --help` prints all three subcommands.
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
| Email-bridge tests unbroken | `pytest tests/unit/test_email_bridge.py tests/integration/test_email_bridge.py -x -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_valor_email.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| CLI installed | `which valor-email` | output contains `valor-email` |
| CLI help | `valor-email --help 2>&1` | output contains `read send threads` |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_valor_email.py tests/unit/test_email_relay.py tests/unit/test_email_history.py` | exit code 1 |
| Byte regression | `pytest tests/unit/test_email_bridge.py::test_build_reply_mime_byte_regression -x -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Relay lifecycle:** Should `bridge/email_relay.py` be started via `asyncio.gather()` inside `bridge/email_bridge.py::run_email_bridge` (one process owns both), or as a separate `./scripts/valor-service.sh email-relay-start` target? The plan assumes the former for simplicity; confirm.
2. **Direct-SMTP flag default:** The plan defaults to queue-via-Redis (relay path) with `--direct` as the opt-in. Alternative: default to direct SMTP (since the user invoked the CLI synchronously), and `--queue` as the opt-in. Which do you prefer? The rationale for queue-default is consistency with `valor-telegram send`, but email lacks Telegram's session-lock concern, so direct-default is defensible.
3. **Session ID race in `valor-telegram`:** Race 2 notes that `valor-telegram` has the same same-second collision bug today. Should the `secrets.token_hex` fix be applied to both CLIs in this plan, or kept scoped to `valor-email` only (and filed as a separate Telegram issue)? Plan currently scopes to email only.
4. **Multi-account future-proofing:** The schema uses `email:history:INBOX` hardcoded. If we later add multi-account support (e.g., `valor@yuda.me` + `tom@yuda.me` on the same bridge), the key becomes `email:history:{account}:INBOX`. Worth designing for that now, or accept the migration pain later? Plan currently accepts the pain.
