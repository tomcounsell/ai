# `/ask-me` — repo context

This repo can render an `/ask-me` question as a **native Telegram poll**, so the human
answers with one tap instead of composing prose on a phone. The generic skill body stays
surface-agnostic; everything Telegram-specific lives here.

Full design: [`docs/features/telegram-poll-questions.md`](../../docs/features/telegram-poll-questions.md).

## Which branch you are on

These map onto the three contexts in the global body's Channel Probe.

**Context 1, interactive local session** (a terminal Claude Code session) → nothing changes. Use
`AskUserQuestion` exactly as the generic body describes. It prompts, you get an answer.

**Context 2, headless bridge session** (`TELEGRAM_CHAT_ID` and `VALOR_SESSION_ID` are both set) →
this repo's asynchronous human channel is a **native Telegram poll**. `AskUserQuestion` never
prompts here. Do this instead, **in this order**:

1. Call `valor-ask-poll` via Bash.
2. Then call `AskUserQuestion` as the turn's **final act**.

**Context 3, subagent** (you were spawned via the Agent tool by another agent) → there is no
channel. `valor-ask-poll` would post a poll into a chat nobody is watching on your behalf, and
`AskUserQuestion` would hang the tree. Do neither. Carry the question up in your final report and
let the parent, which does hold a channel, decide whether to run this skill for real.

## Step 2 is not a redundant double-ask — do not remove it

It looks like you are asking twice. You are not. The two calls do different jobs:

- `valor-ask-poll` **renders** the question where the human will see it.
- `AskUserQuestion` **ends the turn** so the session stops and waits for the answer.

The `needs_human` turn edge fires only on a `PreToolUse` tool-name match against
`AskUserQuestion` (`agent/session_runner/hook_edge.py`, `_ASK_USER_MATCHER`). A Bash call
to `valor-ask-poll` has tool name `Bash`, which never matches. Without step 2 the turn does
not end on `PM_NEEDS_HUMAN`, the session keeps running while the human is still deciding,
and it very likely answers its own question by guessing — which defeats the entire feature.

Under `claude -p` step 2 does not prompt anyone. It fires the edge and ends the turn. That
is the whole reason it is there.

## The other half: the nudge loop stops re-enqueuing you

You do nothing to activate this. It is written down here so you do not go hunting for a way
to hold the turn open, and so nobody deletes the machinery as dead weight.

`valor-ask-poll` writes an unanswered row into the poll registry (`bridge/poll_registry.py`)
naming your `session_id`. At the end of your turn the executor reads that registry via
`session_has_open_poll(session_id)` (thread-offloaded, fail-quiet) and passes the result into
`determine_delivery_action()` in `agent/output_router.py` as `has_open_question`. That
function stays pure and performs no I/O of its own.

When the flag is true, `determine_delivery_action()` returns `"pause_open_question"`: your
output is delivered and the session is **not** re-enqueued with `NUDGE_MESSAGE`. The branch
sits after the terminal-status, `completion_sent`, post-compaction, watchdog, rate-limit,
empty-output and nudge-cap guards, and immediately ahead of the unconditional `eng` + `sdlc`
`"nudge_continue"` line, which is the only thing it overrides. Without it, an sdlc eng session
that asks anything at all, poll or plain prose, is nudged straight past its own question and
answers it by guessing.

Both the open index and the pending index count as open, so the window between
`valor-ask-poll` enqueuing the payload and the relay actually sending it is covered.

The pause is released when the registry row is closed, which happens on a poll tap **or** on a
typed reply into the session. Nothing releases it on a timer.

Details: [`docs/features/telegram-poll-questions.md`](../../docs/features/telegram-poll-questions.md)
and the `pause_open_question` section of
[`docs/features/eng-session-architecture.md`](../../docs/features/eng-session-architecture.md).

## Invocation

```bash
valor-ask-poll \
  --question "Which approach should the retry path take?" \
  --option "Exponential backoff with a cap (Recommended)" \
  --option "Fixed 5s interval, fail after 3"
```

- **Put your recommended option FIRST**, labeled `(Recommended)` as the generic body says.
- The literal final option `Other: wait for followup message` is **appended
  automatically**. Supply it yourself only if you want to; the CLI de-duplicates it and
  moves it last either way.
- `--question` is capped at 300 characters; each option at 100; 2–10 options total.
- `TELEGRAM_CHAT_ID`, `VALOR_SESSION_ID` and `TELEGRAM_REPLY_TO` are read from the
  environment. Do not pass a chat id or a message id as a flag.

## When you get a poll, and when you get prose

A poll ships only when the chat is a **group** AND the session is an **eng** session.

| Surface | Result |
|---|---|
| Telegram group + eng session | **Native poll** |
| Telegram 1:1 DM | Numbered prose |
| `teammate` session (even in an eligible group) | Numbered prose |
| Email, local, system | Numbered prose |

Both constraints are settled owner decisions, not defaults to work around:

- A **user account cannot send a poll into a 1:1 DM.** MTProto rejects it outright with
  `MediaInvalidError`. A *bot* could, but a bot cannot post into a user-to-user chat, so
  its question would land in a separate conversation and break the thread scoping sessions
  depend on. Rejected on identity, not capability.
- **Polls are an engineering affordance.** A `teammate` session gets prose.

You do not check any of this yourself. Always call `valor-ask-poll`; it degrades to the
ordinary text path on every ineligible surface and logs the reason. Never try to detect
the surface and branch by hand, and never send a test poll to confirm what a DM or a bot
can do — that matrix is settled.

## The escape hatch

If the human taps `Other: wait for followup message`, that is not an answer — it is a
request for a better question. Send a **narrowed plain-text followup** naming what you
still need. They answer it with an ordinary reply-to message, which resumes the same
session through the existing reply path.

## Batching

The generic body relaxes one-question-at-a-time from a rule to a preference. In this repo
that means separate **polls** are permitted for genuinely independent questions. It is
still the exception: a group chat with several polls in it is hard to answer and easy to
lose track of. If answering one could reshape another, ask the first and wait.
