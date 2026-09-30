# `/ask-me` — repo context

This repo can render an `/ask-me` question as a **native Telegram poll**, so the human
answers with one tap instead of composing prose on a phone. The generic skill body stays
surface-agnostic; everything Telegram-specific lives here.

Full design: [`docs/features/telegram-poll-questions.md`](../../docs/features/telegram-poll-questions.md).

## Which branch you are on

**Interactive local session** (a terminal Claude Code session) → nothing changes. Use
`AskUserQuestion` exactly as the generic body describes. It prompts, you get an answer.

**Headless bridge session** (`TELEGRAM_CHAT_ID` and `VALOR_SESSION_ID` are both set) →
`AskUserQuestion` never prompts here. Do this instead, **in this order**:

1. Call `valor-ask-poll` via Bash.
2. Then call `AskUserQuestion` as the turn's **final act**.

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

(The second half of that mechanism is `agent/output_router.py`'s `pause_open_question`
branch, which stops the nudge loop re-enqueuing the session while a poll of its own is
outstanding. You do not have to do anything for it — it reads the poll registry itself.)

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
