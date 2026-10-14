# Reactions: 👀 on arrival, then the shape of the ideal response

Tom's request (task 3624532d04c2): "let's start with reactions". His answer:
"it should start with 👀, then upon classification, apply any premium
reaction based on what the appropriate response might classify as (not the
message, but the ideal response shape)". Everything else follows the
defaults I named in the question: these are status reactions on the message
Valor acts on, in Telegram and Slack, in the chats this machine owns. Tom's
own reactions stay data, not input.

## What gets built

1. **👀 at binding.** When `intake._bind` binds a message to a task (`as`
   is `start`, `answer`, `feedback`, `steer`, or `stop`, never `none`), the
   same transaction writes `reaction.requested` on that task: `{reaction_id,
   received_id, channel, chat_id, message_id, emoji: "👀", stage: "seen"}`.
   This applies only to Telegram and Slack messages. Email and local get no
   reaction, because neither channel has one. A message bound `none` gets
   nothing.

2. **The shape judgement.** A new judgement site, `response.shape`, goes in
   `core/judgement_tasks.py` (`SHAPE`). It asks one question: "What shape
   would the ideal response to this message take?" It judges the response,
   not the message. The inputs are the message text, its thread (oldest
   first, from the `message.received` row), and what it was bound as. Each
   label maps to one emoji:

   | label | emoji | Slack name |
   |---|---|---|
   | `short_answer`: a short direct answer | ⚡ | `zap` |
   | `code_change`: a change to code or a system | 👨‍💻 | `technologist` |
   | `written_report`: a written report or research | ✍ | `writing_hand` |
   | `question_back`: a question back before acting | 🤔 | `thinking_face` |
   | `acknowledge`: an acknowledgement, no reply needed | 👌 | `ok_hand` |
   | `decline`: won't or can't do it | 🤷 | `shrug` |

   The port's gate only knows `proceed` and `caution`, and its argmax
   "decides nothing" (docs/judgement-layer.md, Confidence gating). So this
   adds `Kind.PICK` to `core/judgement.py`:
   - **The rule.** A pick question proceeds with its top label when that
     label's normalized probability is at or above the leg's floor.
     Otherwise it abstains. It is never `caution` from a leg.
   - **Fallback and failure.** The existing fallback rule applies
     unchanged: an abstain asks the open-weight leg. If both legs abstain or
     fail, the abstain and failure routes are `caution`, which here means no
     shape reaction is placed and 👀 stays.
   - **Rendering.** `tools/jev.py` and `tools/open_weight.py` render a pick
     question exactly as they render a `choice`.
   - **Shared settings.** The task uses the declaration fields every task
     uses: `error_cost="low"`, floors 0.55 and 0.60 (provisional, like
     breadth's), `calibrated=None`, and `serves` Mission item 6 (Tom sees
     where his message stands without a reply). `guard` is "none: a label,
     no gate".

   The judgement holds, redirects, and refuses nothing. It chooses an emoji
   and nothing else.

3. **Running it.** In the resident kernel (`core/serve.py`), after
   `intake.bind`, a new `core/reactions.py` finds every `seen` reaction with
   no `shape` reaction and no answered or spent `response.shape` judgement
   for its `received_id`. It runs each judgement as its own background job,
   so a slow provider never holds the tick, and a failure logs and leaves
   the rest. The judgement is metered on the bound task like any other.
   - **On an answer.** A proceed writes a second `reaction.requested` with
     `stage: "shape"`, the label's emoji, and `replaces: "👀"`.
   - **On a stopped task.** `TaskStopped` writes nothing.
   - **On failure.** A judgement that both legs fail is retried up to
     `judgement.UNANSWERED_RUNS` times, as at the other sites, and then
     left.

4. **The outbox and the bridges.**
   - **The outbox.** `core/bridge.py` gains `ReactionDue`. `Outbox.due`
     yields it for this channel's `reaction.requested` rows that have no
     `reaction.placed` or `reaction.failed`. `Outbox.reacted(item, error)`
     writes one of the two, and `wait` also wakes on `reaction.requested`.
     A bridge reacts with its own account, as it sends notices. No broker
     effect, approval, or report is involved, because a reaction is a
     reversible mark in a chat Tom owns and carries no text.
   - **Telegram** (`bridges/telegram/send.py`): `SendReactionRequest(peer,
     msg_id, reaction=[ReactionEmoji(emoji)])`. Setting the list replaces
     the account's earlier reaction, so the shape replaces 👀 in one call.
     All seven emoji are in Telegram's standard reaction list, so the
     account needs no Premium. Premium only widens the choice later; this
     answers "premium" without depending on it. A chat that limits its
     reactions refuses the call, and the refusal is recorded as
     `reaction.failed`. A flood wait is held like a send's.
   - **Slack** (`bridges/slack/send.py`): `reactions.add` with the new name,
     then `reactions.remove` with the replaced one. `already_reacted` and
     `no_reaction` count as done, so a retry is harmless. A DM's
     `<team>/<user>` resolves to its conversation id the way the sender
     already resolves it. `reactions:write` is added to `SCOPES` in
     `bridges/slack/__main__.py` and to docs/bridges/slack.md.

5. **The ledger.** `reaction.requested`, `reaction.placed`, and
   `reaction.failed` join `ledger.QUIET`, so they never move a task's fold.
   `core/schema.sql` gains two additive unique indexes: one reaction per
   `(received_id, stage)` on `reaction.requested`, and one result per
   `reaction_id`. A second kernel or a retry then writes nothing twice, the
   same way notices are deduplicated.

6. **Docs.**
   - **The bridges.** docs/bridges/telegram.md and docs/bridges/slack.md:
     "What the bridge never does" stops saying "no reactions". Each doc
     describes reacting as a reply to a kernel row, like a notice.
   - **The rebuild plan.** docs/plans/valor-rebuild.md: "Not carried" no
     longer lists reactions, and points here.
   - **The judgement layer.** docs/judgement-layer.md: `pick` goes into
     Confidence gating and the task taxonomy, and the use-shape table gets
     row 11, "Response shape", Gate: no.

## Out of scope

- Reading Tom's reactions as input (👍 as approve), which is a separate
  request.
- The lifecycle set I proposed (🤔 waiting, ✅ delivered, ❌ ended), which
  Tom's answer replaced.
- Reactions on email or local.
- Custom-emoji (animated pack) reactions.
- Reactions on Valor's own outbound messages.
- A typing indicator or progress posts.
- Shape 3, "Request kind", and routing on the shape label: the label picks
  an emoji and nothing reads it for anything else.

## Stakes

The change touches the kernel: binding, the resident loop, the judgement
port's gate, and the schema's indexes. A mistake there could stall binding
or charge judgements wrongly. The visible effect is a reversible emoji.

Critique rounds: 2. Review rounds: 1.

## Tests

- **`tests/test_intake.py`**
  - Binding as `start`, `answer`, `feedback`, `steer`, and `stop` each
    writes one `seen` 👀 request on Telegram and on Slack, in the binding's
    transaction.
  - `none`, email, and local write none.
  - A rebind after `AlreadyBound` writes no second 👀.
- **`tests/test_judgement.py`**
  - `decide` on `pick`: the top label at the floor proceeds with that
    label, one just under it abstains, and a pick is never cautious.
  - The fallback is asked only on an abstain.
  - `SHAPE` is in `TASKS`, its labels match the emoji map exactly, and the
    `TASKS` site-list test is updated to include it.
  - Both adapters render a pick like a choice.
- **New `tests/test_reactions.py`**
  - Each of the six labels yields its emoji, with `replaces: "👀"`.
  - An abstain, both legs failing, or a stopped task yields no shape row,
    so 👀 stays.
  - Failures are retried up to `UNANSWERED_RUNS` and then left.
  - A second run makes no second judgement or row.
  - The judgement is metered on the bound task.
  - A message of another task, or one bound `none`, is never judged.
- **`tests/test_bridge.py`**
  - `Outbox.due` yields a reaction until it is placed or failed, and only
    on its own channel.
  - `reacted` writes once; a second write is skipped by the index.
  - `wait` wakes on `reaction.requested`.
- **Telegram (emulator, `tests/test_telegram*`)**
  - 👀 then the shape leaves one reaction, the shape.
  - A refused reaction records `reaction.failed`, and the task's reply
    still sends.
- **Slack (emulator, `tests/slack_emulator.py`)**
  - Add-then-remove works, and `already_reacted` and `no_reaction` are
    treated as done.
  - A `missing_scope` records `reaction.failed` and the reply still posts.
- **The whole suite** runs, with pre-existing failures named apart from new
  ones.

## Open after build, owned by the delivery

- **Slack's live token.** The token lacks `reactions:write` until the app is
  reinstalled with the new manifest. Until then Slack reactions record
  `reaction.failed` (`missing_scope`) and nothing else changes. The delivery
  will say whether I reinstalled it with Valor's own account or what stops
  me.
- **Telegram live.** The reaction is checked on Valor's real account in the
  live test window (`tests/test_live_telegram_window.py`), if the build
  turn can reach it. Otherwise the delivery names that check as open.
