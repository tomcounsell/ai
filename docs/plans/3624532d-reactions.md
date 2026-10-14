# Reactions: 👀 on arrival, then the shape of the ideal response

Tom's request (task 3624532d04c2): "let's start with reactions". His answer:
"it should start with 👀, then upon classification, apply any premium
reaction based on what the appropriate response might classify as (not the
message, but the ideal response shape)". Everything else follows the
defaults I named in the question: these are status reactions on the message
Valor acts on, in Telegram and Slack, in the chats this machine owns. Tom's
own reactions stay data, not input.

Revision 1 answers the first critique and revision 2 the second. Their changes are marked (r1) and (r2).

## The reading of "premium"

I read "any premium reaction" as "a reaction picked from the full set to
fit the shape, rather than a fixed acknowledgement". All seven emoji below
are in Telegram's standard reaction list, so they work without Premium. The
other reading is that Tom wants Premium-only or animated custom emoji.
Under that reading the shape emoji would be custom-emoji documents
(`ReactionCustomEmoji(document_id)`), which work only if Valor's account
has Premium.

(r1) The build records whether the account has Premium. `TelethonWire.connect`
already signs in, so it reads `premium` from `get_me()` and logs it once.
The delivery lists this reading first under "Decisions Tom may want to
change", with what the other reading would build and the account's Premium
state as evidence.

## What gets built

1. **👀 at binding.** When `intake._bind` binds a Telegram or Slack message
   to a task as `start`, `answer`, `feedback`, or `steer`, the same
   transaction writes `reaction.requested` on that task:
   `{reaction_id, received_id, channel, chat_id, message_id, emoji: "👀", stage: "seen"}`.
   A message bound `none` gets nothing. Email and local get nothing,
   because neither channel has reactions. `channels.Channel` gains
   `reacts: bool`, true only for Telegram and Slack, and intake reads that
   instead of naming the channels.

   (r1) **Stop, and a stopped task.**
   - **A message bound `stop`.** It is not judged. The binding writes the
     `shape` reaction straight away, 👌 (`acknowledge`, `judged: false`),
     and writes no 👀. A stop's ideal response is the acknowledgement, and
     a judgement on a stopped task could only be refused.
   - **A message whose task is stopped before its judgement runs.** The
     port raises `TaskStopped` for an `answer`, `feedback`, or `steer` in
     this case. The shape job then writes the same 👌 with `judged: false`
     and the reason `task stopped`. No message is left showing 👀 for good.

2. **The shape judgement.** A new site, `response.shape` (`SHAPE` in
   `core/judgement_tasks.py`), asks one question: "What shape would the
   ideal response to this message take?" It judges the response, not the
   message. Its inputs are the message text, its thread (oldest first,
   from the `message.received` row), and what the message was bound as.

   (r1) The emoji are pinned as exact code points. Each is checked against
   Telegram's standard list, and none carries U+FE0F:

   | label | emoji | code points | Slack name |
   |---|---|---|---|
   | (seen) | 👀 | U+1F440 | `eyes` |
   | `short_answer` | ⚡ | U+26A1 | `zap` |
   | `code_change` | 👨‍💻 | U+1F468 U+200D U+1F4BB | `technologist` |
   | `written_report` | ✍ | U+270D | `writing_hand` |
   | `question_back` | 🤔 | U+1F914 | `thinking_face` |
   | `acknowledge` | 👌 | U+1F44C | `ok_hand` |
   | `decline` | 🤷 | U+1F937 | `shrug` |

   `Kind.PICK` is new in `core/judgement.py`:
   - **The rule.** A pick question proceeds when its top label's normalized
     probability is at or above the leg's floor. Otherwise it abstains. A
     leg never answers it with caution. This narrows the documented "the
     argmax decides nothing" rule, and only for pick questions:
     docs/judgement-layer.md says so in Confidence gating.
   - **(r1) Carrying the label.** `action` keeps only proceed and caution.
     `_merge` and `_single` add `picked: {question: label}` to the
     `judgement.answered` payload. The label comes from
     `answers[q]["label"]` of the leg that proceeded. For a question the
     primary abstained on, that is the fallback's label, never the
     primary's argmax. A question both legs abstain on has no `picked`
     entry.
   - **(r1) Digest and calibration.** `task_sha256` already hashes
     `kind.value`, so a pick task gets its own digest with no change. The
     calibration scorer in `core/judgement_sites.py` (`calibrate`) scores a
     pick question by `picked` against the case's label, where it scores
     every other question by action. `tools/jev.py` and
     `tools/open_weight.py` render a pick exactly as a `choice`.
   - **Declaration.** `error_cost="low"`; floors 0.55 for the primary and
     0.60 for the fallback, provisional, like breadth's;
     `on_abstain="caution"` and `on_failure="caution"`, which here mean no
     shape reaction, so 👀 stays; `calibrated=None`; `serves` Mission item
     6; `guard` "none: a label, no gate".

   The judgement chooses an emoji and nothing else. It holds, redirects,
   and refuses nothing.

3. **(r1) Keeping the judgement out of the task's scheduling.** The shape
   judgement is metered on the bound task, so its `gateway.*` rows (already
   quiet) and its `judgement.answered` / `judgement.failed` row land beside
   the task's own work.
   - **One rule.** `ledger.QUIET` stays a tuple of types. A new
     `ledger.quiet(row)` returns true for a type in `QUIET`, any
     `reaction.*` row, and a `judgement.*` row whose `site` is in
     `ledger.QUIET_SITES = ("response.shape",)`.
   - **Its readers.** The three readers the critique names use it instead
     of `r["type"] in QUIET`: `router.retry_due` (core/router.py:277),
     `tasks.failed_step` (core/tasks.py:741), and `serve._ready`'s `latest`
     and `_step`'s `seen` loop (core/serve.py:686, 827).
   - **What was left alone.** Every other reader that looks for the latest
     row was read and keyed by type (`router.failures`,
     `session._failure_report`, `serve._provision_failures`). They are
     unaffected and not changed.

4. **(r1) Running it, apart from the task's own jobs.** `core/reactions.py`
   holds `pending(conn)`, which lists every `seen` reaction with no `shape`
   reaction and no answered or spent `response.shape` judgement for its
   `received_id`, and `shape(port, dsn, row)`, which runs one judgement.
   - **Its own jobs.** `serve.Kernel` keeps those jobs in a dict of their
     own, `self.shaping`, keyed by `received_id`. It never uses `_start` or
     `self.jobs`, so a task's step is scheduled while its shape judgement
     runs. A shape job's done-callback touches only `self.shaping` and
     logs a failure. `close()` cancels and awaits these jobs, as it does
     the task jobs.
   - **When.** `tick` starts a shape job after `intake.bind` for each
     pending row not already in `self.shaping`.
   - **Outcomes.**
     - **An answer.** A proceed with a `picked` label writes
       `reaction.requested` with `stage: "shape"` and that label's emoji.
     - **`TaskStopped`.** It writes 👌 `judged: false`, as in item 1.
     - **Failure.** A judgement both legs fail is asked again on later
       ticks, up to `judgement.UNANSWERED_RUNS` times, as at the other
       sites, then left. 👀 stays.

5. **The outbox and the bridges.**
   - **The outbox.** `core/bridge.py` gains `ReactionDue(reaction_id,
     task_id, chat_id, message_id, emoji, stage, at)`. `Outbox.due` yields
     one for each of this channel's `reaction.requested` rows that has no
     `reaction.placed` or `reaction.failed`, in row order.
     `Outbox.reacted(item, error=None)` writes one of the two. `wait` also
     wakes on `reaction.requested`.
   - **(r2) A shape supersedes its 👀.** Order within one pass does not
     survive a retry: a 👀 that hits a flood wait is yielded again on a
     later wake, after its shape may have been placed, and would put 👀
     back (Telegram replaces the list; Slack adds `eyes` beside the shape).
     So `Outbox.due` never yields a `seen` reaction once a `shape`
     `reaction.requested` exists for the same `received_id`. Instead it
     closes that `seen` row itself with `reaction.placed` carrying
     `superseded: true` (nothing sent), in a savepoint, so the result
     index still holds one result per `reaction_id` and a second outbox
     racing it writes nothing. The shape's own placement does the
     replacing: Telegram's list set to the shape alone, and Slack's
     `reactions.remove` of `eyes` answered `no_reaction` when 👀 never
     landed, which counts as done. A bridge reacts with
     its own account, as it sends notices. There is no broker effect,
     approval, or report: the reaction is a reversible mark in a chat Tom
     owns and carries no text.
   - **(r1) Dispatch.** The four outbox loops dispatch on each type by
     name:
     - **Telegram and Slack** (`bridges/telegram/bridge.py`,
       `bridges/slack/bridge.py`): `_consume` gains a `ReactionDue` branch
       that calls `self.sender.react(item, outbox)`. The `else` that today
       sends a notice becomes `elif isinstance(item, NoticeDue)`.
     - **Local** (`bridges/local/__init__.py`): its `else` becomes an
       explicit `NoticeDue` branch too.
     - **Email** (`bridges/email/__init__.py`): it already handles only
       `Release`.

     Local and email are never handed a reaction: intake writes them only
     for channels that react, and `Outbox.due` filters by channel.
   - **(r1) Telegram, through the wire.**
     - **The wire.** `Wire` gains `react(chat_id, msg_id, emoji) -> None`.
       `TelethonWire.react` sends `SendReactionRequest(peer, msg_id,
       reaction=[ReactionEmoji(emoji)])` through `_call`, so its errors map
       as a send's do: `FloodWait` and `Refused` (for
       `REACTION_INVALID`, `REACTION_EMPTY`, and a chat that limits its
       reactions). Telethon stays inside `wire.py`.
     - **(r2) The sender.** `Sender.react` in `bridges/telegram/send.py`
       follows `Sender.notice` (send.py:246-250), not a send's hold: it
       awaits `self.flood.wait()` before `wire.react`; on `FloodWait` it
       calls `self.flood.hit(e.seconds)`, logs once, writes nothing, and
       returns, so the outbox yields it again on a later wake. Any other
       transient `WireError` (`NotConnected`, `InDoubt`) is handled the
       same way: logged, unmarked, retried. Only `Refused` writes
       `reaction.failed`, with Telegram's error; success writes
       `reaction.placed`. A flood wait never writes `reaction.failed`,
       which the result index would make final.
     - **One reaction at a time.** Setting the list replaces the account's
       earlier reaction, so the shape replaces 👀 in one call.
   - **Slack** (`bridges/slack/send.py`): `Sender.react` calls
     `reactions.add` with the new name. For a `shape` reaction it then
     calls `reactions.remove` with `eyes`.
     - **Answers.** `already_reacted` and `no_reaction` count as done, so a
       retry is harmless. (r2) A 429 or a lost connection is left unmarked
       and retried on a later wake, as the Slack sender's notices are; any
       other error is recorded as `reaction.failed`.
     - **Addressing.** A DM's `<team>/<user>` resolves to its conversation
       id the way the sender already resolves it.
     - **The scope.** `reactions:write` joins `SCOPES` in
       `bridges/slack/__main__.py` and docs/bridges/slack.md.

6. **(r1) Emulators.**
   - **Telegram.** `tests/telegram_emulator.py` and its wire implement
     `react`. They keep the account's current reactions per message, so a
     test can read them back. They refuse `REACTION_INVALID` for an emoji
     outside the standard list, which is pinned in the emulator, so a
     wrong code point fails in tests. The build checks that pinned list
     against `messages.getAvailableReactions` on the live account if the
     turn can reach it, and names it as open if not.
   - **Slack.** `tests/slack_emulator.py` gains `reactions.add` and
     `reactions.remove`, which keep reactions per message and answer
     `already_reacted`, `no_reaction`, and, when a test withholds the
     scope, `missing_scope`.

7. **The ledger.** `reaction.requested`, `reaction.placed`, and
   `reaction.failed` are new row types, all quiet by `ledger.quiet`.
   `core/schema.sql` gains two additive unique indexes: one reaction per
   `(received_id, stage)` on `reaction.requested`, and one result per
   `reaction_id` across `reaction.placed` and `reaction.failed`. A second
   kernel or a retry then writes nothing twice, the same way notices are
   deduplicated.

8. **Docs.**
   - **The bridges.** docs/bridges/telegram.md and docs/bridges/slack.md:
     "What the bridge never does" stops saying "no reactions". Each doc
     describes reacting as a reply to a kernel row, like a notice, and
     telegram.md names `Wire.react`.
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
- Custom-emoji (animated pack) reactions: the other reading of "premium",
  named in the delivery.
- Reactions on Valor's own outbound messages.
- A typing indicator or progress posts.
- Shape 3, "Request kind", and routing on the shape label: the label picks
  an emoji and nothing reads it for anything else.

## Stakes

The change touches the kernel: binding, the resident loop's scheduling, the
judgement port's gate, and the schema's indexes. A mistake there could stall
a task's retry, start spurious steps, or charge judgements wrongly. The
visible effect is a reversible emoji.

Critique rounds: 2. Review rounds: 1.

## Tests

- **`tests/test_intake.py`**
  - Binding as `start`, `answer`, `feedback`, or `steer` writes one `seen`
    👀 on Telegram and on Slack, in the binding's transaction.
  - `stop` writes 👌 `shape` with `judged: false` and no 👀.
  - `none`, email, and local write nothing.
  - A rebind after `AlreadyBound` writes no second row.
- **`tests/test_judgement.py`**
  - `decide` on `pick`: the top label at the floor proceeds, just under it
    abstains, and a pick is never cautious.
  - A fallback-only proceed carries the fallback's label in `picked`, not
    the primary's abstained argmax.
  - Both legs abstaining leaves no `picked`.
  - `SHAPE` is in `TASKS`, and the `TASKS` site-list test is updated.
  - `task_sha256` differs between a pick and the same question as a
    choice.
  - Both adapters render a pick like a choice.
  - The calibration scorer scores a pick by label.
- **New `tests/test_reactions.py`**
  - Each label yields its exact code points, pinned as a literal per label,
    and none contains U+FE0F.
  - An abstain or both legs failing yields no shape row, so 👀 stays.
  - Failures are asked again up to `UNANSWERED_RUNS` and then left.
  - A task stopped before judging yields 👌 `judged: false`.
  - A second pass makes no second judgement or row.
  - The judgement is metered on the bound task.
  - A message bound `none` is never judged.
- **`tests/test_kernel.py`** (the resident loop)
  - A task whose latest real row is `step.failed` still retries after a
    shape judgement and its reaction rows land.
  - A shape judgement on a task in a harness state starts no extra step.
  - A task's step is scheduled while its shape job is in flight in
    `self.shaping`.
  - `close()` cancels a running shape job.
  - Each of these drives `retry_due`, `failed_step`, `_ready`, and the
    `seen` loop through `ledger.quiet`.
- **`tests/test_bridge.py`**
  - `Outbox.due` yields a reaction until it is placed or failed, in row
    order, and only on its own channel.
  - (r2) Given a message's `seen` and `shape` both unplaced, `Outbox.due`
    yields only the shape and closes the `seen` row with `reaction.placed`
    `superseded: true`; two outboxes doing so at once write one result.
  - `reacted` writes once; the index skips a second write.
  - `wait` wakes on `reaction.requested`.
  - The local bridge's loop, handed a reaction row on another channel,
    neither sees it nor stops.
- **Telegram (emulator)**
  - 👀 then the shape leaves exactly one reaction, the shape.
  - An emoji outside the standard list (✍ with U+FE0F) is refused and
    recorded as `reaction.failed`, and the task's reply still sends.
  - A flood wait on a reaction delays it without dropping it: no
    `reaction.failed` is written, and after the wait it is placed.
  - (r2) With 👀 and the shape both due, 👀 hits a flood wait, the shape is
    placed, and after the wait the message shows only the shape.
  - (r2) `NotConnected` on a reaction leaves it unmarked and it is placed on
    the next wake.
- **Slack (emulator)**
  - Add-then-remove leaves only the shape.
  - `already_reacted` and `no_reaction` count as done.
  - `missing_scope` records `reaction.failed`, and the reply still posts.
  - (r2) With 👀 and the shape both due, 👀 hits a 429, the shape is placed,
    and after the wait the message carries only the shape's name.
- **The whole suite** runs, with pre-existing failures named apart from new
  ones.

## Open after build, owned by the delivery

- **Slack's live token.** The token lacks `reactions:write` until the app is
  reinstalled with the new manifest. Until then Slack reactions record
  `reaction.failed` (`missing_scope`) and nothing else changes. The delivery
  will say whether I reinstalled it with Valor's own account or what stops
  me.
- **Telegram live.** The reaction, the account's Premium state, and the
  standard list are checked on Valor's real account in the live test
  window (`tests/test_live_telegram_window.py`), if the build turn can
  reach it. Otherwise the delivery names each check as open.
