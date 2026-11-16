# Chat sessions on Fable, and a Brief that keeps the cache

Tom, 2026-11-16 (valor team chat): "move to Fable, make the context compiler
faster (with a cache if that helps). only drop the list if it will not impact
quality of the agent being aware of all it's work. ... cost is no issue".
Groundwork: tasks 8d98f8bafffd (model review) and f6c9d22cd9e9 (cache).

## Problem

A chat session's Brief is its system prompt (`harnesses/claude_code.py:204`),
ahead of the whole conversation, so any byte that changes in it rewrites
140-180K cached tokens. It is re-rendered every turn and changes whenever a
child moves: the Children section (`core/tasks.py:866`) carries each child's
state and spending to the micro-dollar, its subtree's included. Other sections
are also rendered now and move between turns: Remembered (`memory.recall`), the
other sessions' memory index (`record.memories`), the nightly summary
(`record.summary`), the corrections in force, and the room, repos and CC read
from the spec. Those are the likely 13 unattributed turns.

## What gets built

1. **Fable seats.** `core/settings.py`: a `chat` seat
   `("claude_code", "claude-fable-5-2")`; `reviewer` moves to
   `claude-fable-5-2` (code review; the advisor's Claude fallback seat follows
   it). `PRICES["claude-fable-5-2"] = Price(8.00, 40.00, 10.00, 16.00, 0.20,
   <day checked>)`, checked against the pricing page by the builder;
   `WINDOWS["claude-fable-5-2"] = 1_000_000`, so the reminders and forced
   compaction apply. `frontier` stays Opus 5.5: tasks, critique, docs, the call
   worker and routines are unchanged.
2. **Every chat session on Fable, the existing ones too.** A Brief is written
   once, so a session's stored `model` stays Opus. A *chat session's turn* is
   one run by `session.run` for a task with `b.chat`, never a `fresh` turn:
   the Meet call worker (`core/__main__.py:557`, `fresh="call"`) is not one,
   though its Brief has `chat`. `tasks.turn_model(b)` gives the `chat` seat's
   model for a chat session, else `b.model`; `session.run` passes
   `dataclasses.replace(b, model=turn_model(b))` to `turn_for`, and
   `context_report` (`core/session.py:1483`) uses it. `_turn_for` and the call
   path are untouched, so the call worker keeps Opus. `core/intake.py` starts
   new sessions on the `chat` seat. The first Fable turn of a resumed session
   rewrites its cache once (caches are per model).
3. **A frozen system prompt; what changed goes in the turn prompt.** For a
   chat session's turn (`b.chat` and `fresh is None`), `tasks.dispatch`
   renders the Brief fresh as named sections plus the structured child
   reports (id, state, spending, last activity, instruction), then:
   - **Refresh** (the fresh Brief becomes the system prompt) on the
     session's first turn; when the last turn *ended* (`turn.ended` at) more
     than an hour ago (the cache's TTL; the builder confirms from metered
     usage that Fable turns write `ephemeral_1h` tokens through the gateway);
     after a `/compact` turn; when the persona or stage file changed; or when
     the news below exceeds a quarter of the Brief's size, so repeated news
     never outgrows what a rewrite would cost. A refresh's `turn.started`
     records `brief_sections` and `brief_children` beside `brief`.
   - **Otherwise** the system prompt is the latest snapshot's `brief`, byte
     for byte (the newest `turn.started` with `brief_sections`), and dispatch
     returns `news`: under "Your Brief has changed since it was written",
     each section whose text differs from the snapshot's, in full, and one
     line per child new or changed against `brief_children` (id, state,
     spending, last activity), with the instruction for a new child. Against
     the snapshot, not the last turn, so every turn's system prompt plus its
     own prompt hold every child's current state and instruction, and a
     compaction loses nothing. No rendered text is parsed back.
   - Children lines gain the child's last activity (its latest row's time).
     Spending stays exact: a change costs only that child's line.
   - `runs._run_turn` passes `news` to the harness's `build` (one keyword
     argument, all three harnesses), which appends it to the turn's prompt;
     Claude Code's `subagent_start` gets the Brief plus news, so subagents
     see current states. `turn.started` records it as `brief_news`.
   Task turns and fresh turns (critique, review, docs, advice, call) are
   unchanged: fresh each turn, no news.
4. **Build time, measured.** Time `dispatch` on the Cowboy for the #valor-dev
   session (a read). `child_report` reads each child's whole ledger and
   subtree spending, one child at a time; if dispatch takes over a second,
   fetch the children's facts in one query. Report the before and after.

## Refusals

Fable can refuse outright, and Claude Code does not fall back to Opus. The
builder runs one refused `claude -p` output through the harness and the
kernel, and the delivery says how it surfaces (expected: the turn ends as an
error, `_failure_report` tells the next turn, and the chat gets no reply until
then). No fallback is added: no refusal has happened yet.

## Out of scope

Effort levels; tasks and other stages on Fable; dropping the Children list;
moving the persona out of the system prompt; the Meet call worker's model
(it stays on Opus).
No new check, gate or guard: the Brief comparison is how dispatch renders,
not a check on anything.

## Tests

- `tests/test_tasks.py` (or the Brief's own tests): two chat turns with a
  `turn.started` between; a child changes state and spending, a new child
  starts, another session writes a memory, a correction is added. The second
  dispatch's `text` is byte-identical to the first's; its `news` names every
  changed child's new state, the new child's instruction, and the changed
  sections; text plus news contain every child's id, state and instruction.
- Refresh: on a first turn, an hour after the last turn ended (a long turn
  that ended minutes ago does not refresh), after a compaction turn, after a
  persona change, and when news passes a quarter of the Brief: `text` is the
  fresh render, `news` empty, and `turn.started` carries the snapshot.
- Nothing changed: `news` empty, `text` identical.
- A task (non-chat) Brief, and the call worker's (`fresh="call"`), are
  fresh each turn with no news; the call worker's turn keeps the frontier
  model while a chat session's turn gets the chat seat's.
- Harness: `build(..., news=...)` puts news after the prompt in argv; empty
  news leaves the argv as today; `VALOR_SUBAGENT_START` holds Brief and news.
- Settings: `compaction("claude-fable-5-2")` gives the 1M marks; the Fable
  price is used by metering; `turn_model` gives Fable for a chat session
  whose stored model is Opus, and the stored model for a task.
- Existing tests whose assumptions move (reviewer model, seat lists) updated.

## After merge

On the Cowboy, from the ledger: a day of chat turns before and after, cache
written per turn, the turns that still rewrote and why (the stored `brief`
texts now show which section moved), and the weekly cost at Fable prices.
Short chat text. Done when Tom's chats run on Fable and a turn after a child's
report no longer rewrites the whole context.
