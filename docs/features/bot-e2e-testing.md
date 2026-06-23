# Bot End-to-End Testing via `valor-telegram`

Drive deployed bots (e.g. Hermes-agent bots like `@cyndra_staff_bot` / "Bruce")
over the *real* Telegram surface from the `valor-telegram` CLI: send a probe and
synchronously pull the bot's **settled** reply back for assertions — the
bot-testing equivalent of `curl`. Tracking issue: #1574.

Two pieces make this safe and usable:

1. **`valor-telegram send --await-reply`** — a synchronous send-and-wait primitive.
2. **A deterministic registered-bot loop-guard** — a registered bot's inbound
   messages are recorded to history but **never spawn a session**.

## The bot registry (`telegram.bots[]`)

Register a bot under a project in `projects.json`:

```json
"projects": {
  "valor": {
    "machine": "MyMac",
    "telegram": {
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
  }
}
```

A bot's numeric `id` is the prefix of its bot token (e.g. `@cyndra_staff_bot` →
`8837490628`). The registry is the home of the per-bot **settle profile** the
awaiter reads to tune its debounce.

### Why a separate registry (not `dms.whitelist`)

A bot reply carries **no `reply_to`**. The bridge keys session continuity on
`reply_to` (reply → resume; no-reply → **new** session). If a bot were in
`dms.whitelist`, each reply would look like a cold inbound message → spawn a new
session → auto-continue answers it → the bot replies again → **infinite loop**.

So registered bots are kept in a dedicated `BOT_ID_TO_PROJECT` map
(`bridge/routing.py`), deliberately **out** of `DM_USER_TO_PROJECT` and
`GROUP_TO_PROJECT`. `find_project_for_bot(sender_id)` resolves a registered bot;
a hit means "record to history, never spawn".

### Validation (mutual exclusion)

`bridge/config_validation.py::validate_telegram_bots` enforces, at
config-validation time (the same gate that blocks a malformed config from
restarting the bridge):

- **Single-machine ownership** of each bot id.
- **Mutual exclusion**: a bot id must NOT also appear in `dms.whitelist[].id` —
  otherwise it would resolve a project on the spawn path and reintroduce the
  loop. This is the config-time enforcement of the loop-guard invariant.

See [single-machine-ownership.md](single-machine-ownership.md).

## The loop-guard (deterministic)

Two independent layers prevent a registered bot from spawning a session:

1. **Bridge NewMessage handler** (`bridge/telegram_bridge.py`): after recording
   the message to history, `if find_project_for_bot(sender_id): return` — the
   primary short-circuit, before any spawn logic. Covers both DM and group
   paths, regardless of reply-to or question content.
2. **`should_respond_sync`** (`bridge/routing.py`): returns `False` for a
   registered bot — belt-and-suspenders so any future caller inherits the
   invariant.

This is stronger than the prior `classify_conversation_terminus` heuristic
(#1318), which was group-path only, reply-to-Valor only, and silenced a bot only
when its reply contained *no question*. The registry guard is deterministic and
pre-empts that heuristic for registered peers.

## `send --await-reply`

```bash
# Return the bot's settled prose on stdout
valor-telegram send --chat 8837490628 --await-reply "what is the deploy status?"

# Structured transcript for assertions
valor-telegram send --chat 8837490628 --await-reply --json "..." > reply.json

# Override the overall timeout (seconds)
valor-telegram send --chat 8837490628 --await-reply --timeout 900 "..."
```

`--await-reply` is **only** valid against a registered bot; an unregistered id
is refused with a clear stderr error and a non-zero exit (it never polls
forever).

### Settle semantics (these are load-bearing)

A Hermes bot reply is three distinct on-wire streams, not one atomic message:

1. **The answer is STREAMED via in-place edits** on a stable `message_id`. The
   terminal "done" flag is internal — **never emitted on-wire**.
2. **Status/tool chatter** (`⏳ Working...`, `💻 terminal: ...`) arrives as
   separate messages.
3. **A trailing `⚠️` footer is GLUED inside the final answer** message — a test
   signal (e.g. it fires when the bot claims a file edit that didn't land). It
   is **preserved**, never stripped, and surfaced as `footer_present`.

Because there is no on-wire done-marker, the awaiter settles on **silence**:

- **Edit-aware debounce.** The bridge captures `MessageEdited` for a registered
  bot and upserts the latest body onto the same record
  (`update_message_text`, `tools/telegram_history/`). The awaiter resets its
  quiet timer on **any new message OR content change** — so it never settles on
  a mid-stream partial.
- **Two separate timers.** A short **quiet window** (`quiet_window_seconds`, ~5s
  = "stopped streaming") and a generous overall **`--timeout`** (default 600s,
  because Hermes turns run minutes). Conflating them is the #1 bug — a short
  overall timeout kills the await mid-think.
- **Silence decides DONE; patterns only CLEAN the output.** `status_patterns`
  filter status/tool lines out of the *displayed prose*; they never decide when
  to return. A drifted pattern yields at worst a stray interim line, never a
  premature settle.

On timeout, the awaiter prints whatever it captured plus a `TIMED OUT after Ns`
line to stderr and exits non-zero; `--json` still emits valid JSON with
`timed_out: true`.

### `--json` schema

```json
{
  "target": {"id": 8837490628},
  "sent": {"text": "...", "ts": 1733383764.0},
  "reply": {
    "settled_text": "...",
    "footer_present": true,
    "message_ids": [124, 125],
    "edit_count": 9,
    "started_ts": 1733383765.0,
    "settled_ts": 1733383806.0
  },
  "transcript": [{"message_id": 124, "kind": "answer|status", "text": "..."}],
  "settled": true,
  "timed_out": false,
  "elapsed_s": 41.2
}
```

The awaiter is a **pure reader** of the `TelegramMessage` history store. It never
opens a second Telethon client — the bridge holds the SQLite session lock.

## Code map

| Component | Location |
|-----------|----------|
| Bot registry map + `find_project_for_bot` | `bridge/routing.py` |
| Registry startup build (`BOT_ID_TO_PROJECT`) | `bridge/telegram_bridge.py` |
| Loop-guard (NewMessage + `should_respond_sync`) | `bridge/telegram_bridge.py`, `bridge/routing.py` |
| Edit capture (`update_message_text`) | `tools/telegram_history/__init__.py` |
| Edit-handler registered-bot branch | `bridge/telegram_bridge.py` |
| Config validation (`validate_telegram_bots`) | `bridge/config_validation.py` |
| Awaiter (settle algorithm) | `tools/valor_telegram_await.py` |
| CLI flags (`--await-reply`/`--timeout`/`--json`) | `tools/valor_telegram.py` |

## See also

- [telegram-messaging.md](telegram-messaging.md) — the `valor-telegram` CLI surface.
- [single-machine-ownership.md](single-machine-ownership.md) — the ownership rule the bots registry extends.
- [agent-reply-terminus.md](agent-reply-terminus.md) — the prior heuristic bot-loop break (#1318) this supersedes for registered bots.
