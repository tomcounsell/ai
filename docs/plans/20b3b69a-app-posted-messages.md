# A person's Slack message posted through an app reaches Valor

Task 20b3b69a569c. A bug fix, plus operations on Valor the Captain.

## What happened (observed 2026-11-24, all times UTC)

- Tom posted two replies at 06:29, through his Claude's Slack tools (the
  Yudame Tools app, his own user token): one in a client workspace's
  product channel thread, one in a Yudame dev-channel thread the Captain
  serves. Neither has a `message.received` row on the Captain; no
  reaction was asked for.
- The Captain's Slack bridge was up and receiving (a row at 06:42 from
  the same workspace), the kernel was running turns, and no usage limit
  was in force (no limit rows since 11-23).
- Slack stores a message written through an app with the person's
  `user` and the app's `bot_id`. `bridges/slack/inbound.py` `wanted()`
  drops every message with a `bot_id`, live and in gap fill, so a
  person's message sent from any app (Yudame Tools, Geekbot, Giphy) is
  thrown away as a bot's.
- A real bot's post differs: its `bot_profile.user_id` is the message's
  `user` (sampled in both workspaces: Claude, gato), or it has subtype
  `bot_message`, which `SUBTYPES` already drops.
- Separately, the Captain has taken no update since 2026-11-21 16:37:
  a turn committed `7fb45956e` (product-sheet `order`) straight onto the
  live checkout's `main`, and every rollout since fails "diverged" (2775
  lines in `kernel.log`, heard by no session). A merged fix would not
  reach the Captain until this is cleared.

## What will be built

1. `wanted()`: a message is a bot's when its `bot_profile.user_id` is its
   `user`; the bare `bot_id` test goes. Valor's own posts (`own`),
   Slackbot and non-person subtypes stay dropped as now.
   `docs/bridges/slack.md` (the "Valor's own posts... are dropped"
   paragraph) says the same.
2. Tests beside the existing Slack intake tests (`tests/test_own_posts.py`
   or a new file), made-up ids:
   - a person's message with `bot_id` and a `bot_profile` without
     `user_id` is wanted, live (`handle` records it) and in gap fill
     (`_receive_found` records it);
   - a bot-token post (`bot_profile.user_id == user`) is dropped, live and
     in gap fill;
   - subtype `bot_message` with no `user` is dropped;
   - Valor's own post made through its app (`user == own`, with `bot_id`)
     is dropped;
   - Slackbot and an edit (`message_changed`) stay dropped;
   - a plain person's message (no `bot_id`) is wanted, as before.

## Operations on the Captain (through Valor.app's `valor-run`)

3. Keep `7fb45956e`: a local branch `captain-local-7fb45956` on the
   Captain, and the same commit pushed to origin as that branch (fetched
   into this workspace, `push_branch`). It is unreviewed work; it is named
   in the report for the valor project to land, not merged here.
4. Move the Captain's `main` back onto `origin/main` with
   `git reset --keep` after a fetch (untracked files untouched), then let
   `python -m core update` take main as usual. Confirm `kernel.log` shows
   the next rollout succeeding and the bridges restarted on the new head.
5. Once this fix is merged and on the Captain, confirm gap fill records
   both 06:29 messages (threads with ledger activity in 14 days are read
   each pass) and the kernel steers each to its chat's session, which
   reacts and answers in its thread. If gap fill does not reach them in
   an hour (the Yudame workspace's replies budget is spent every tick),
   hand each on through the kernel's receive path with the bridge's own
   `inbound` fields, run through Valor.app. Nothing is posted by this
   task.
6. Scan both workspaces back to 2026-11-01 for person messages carrying a
   `bot_id` with no ledger row (the scan is in the task's `ops/`, outside
   the repo). On 11-23 onward it found only these two. Anything older
   unanswered is handed on the same way, or listed if stale.

## Out of scope

- Why the Captain's `origin/main` ref sat at 11-17 and why a turn could
  commit to the live checkout: named in the report, not fixed here.
- The Yudame workspace's gap-fill replies budget being spent every tick:
  named, not changed.
- Landing `7fb45956e`.
- No new check, alert or gate. What would have told us sooner (the
  rollout failures, the dropped messages) is said in the report only.

## Stakes

Every Slack message on every Mac passes this filter; a rule too loose
lets bots' posts start turns, too tight drops people, as now.
