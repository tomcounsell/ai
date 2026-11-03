# The Slack bridge serves every workspace in `slack-keys` (issue 3680)

**Ask.** Issue 3680; Tom, valor team chat on Slack, 2026-11-03: a client's
Slack workspace joins Yudame's on the Mac that runs that client's project.
Its first channel is a mentions-only team room, with Valor in the
`teammate` role. "There will be a series of product and engineering
channels to join. This is just the first one." After this, adding a
channel in that workspace is only an entry in the private spec. Answers,
2026-11-03: build and merge now, with the Mac staying on Yudame alone
until the second workspace's tokens are in; Valor creates the client's
app and installs it on its own account, and Tom approves it only if the
workspace asks.

**Premise, checked.** The bridge is built for one workspace:
- `__main__.credentials()` refuses more than one workspace.
- `SlackBridge` and `Sender` each hold one `SlackWire`.
- `Sender._target` fails any team but `wire.team_id`.
- `fill()` skips any chat outside it.

Every chat id is already `<team>/<id>`. `slack-keys` is keyed by team, and
seen state and owned chats are both per chat. So the work is giving each
team its own wire. Spec ownership and mention handling need nothing new.

**One app per workspace.** The Mac's Yudame app can't serve the client.
An internal app installs only in the workspace it was made in. A
distributed one gets one `conversations.history` call a minute, and gap
fill relies on Tier 3. So the client gets its own internal app from
`python -m bridges.slack manifest`, and one token still runs on one bridge.

## What is built

1. **Tokens, `__main__.py`.**
   - `credentials()` returns `{team: (app, user)}` for every whole pair in
     `slack-keys`. It still exits when there are none.
   - `keys` copies every pair this Mac has in the vault: the pair named
     `SLACK_{APP,USER}_TOKEN_<MACHINE>` and any pair named
     `..._<MACHINE>__<LABEL>`. Each pair is filed under the team its
     `auth.test` names, and keys print per team as today. The `__`
     separator stops one Mac's name from matching another Mac's prefix.
2. **Wires by team, `bridge.py`, `send.py`, `wire.py`.**
   - `SlackWire` takes its `team` from `slack-keys`.
   - `SlackBridge` and `Sender` hold `{team: wire}`, and every call picks
     the wire by the chat's team: live handling (a handler bound per
     wire), gap fill, sends, lookup, reactions, the canvas and `read`.
   - A team that isn't in the map is `broker.Failed` ("not a connected
     workspace"), as today.
   - Each wire has its own connect loop and its own `gap.Budget`, since
     Slack's limits are per workspace. `run` doesn't wait for every
     workspace before consuming the outbox, so a revoked client token
     can't stop Yudame.
   - A send or lookup for a workspace that hasn't connected yet is
     `Unknown`, as lookup before connecting is today, so the next wake
     settles it.
3. **Per-workspace connection, `core/bridge.py`, `core/rollout.py`.**
   - `connected()` may return `{part: bool}`. The outbox keeps one
     up-since per part and names its listening connection
     `valor-slack-up@<team>=<epoch>,<team>=0`, where 0 means down. The
     name is `-listen` when every part is down. A bridge whose
     `connected()` returns a bool keeps today's name.
   - `rollout.services` and `states` report each part as
     `slack <team>: connected since …` or `not connected`.
   - Postgres keeps 63 bytes of a connection name, which holds two
     workspaces. `slack.md` names a third workspace as a gap.
4. **Docs.**
   - `docs/bridges/slack.md`: the bridge serves every workspace in
     `slack-keys`, the second-pair vault name, one app per workspace, and
     how a channel in another workspace is added (a spec entry). It drops
     "One workspace" from Gaps.
   - `docs/machine.md` gets the token row.
   - No client names or ids go in this repository: it is public.

## After merge (rollout; the install waits for the session's word that Tom confirmed)
1. Valor creates the client's app from the manifest and installs it on its
   own account there. Tom approves it if the workspace asks.
2. Valor makes the app token (`connections:write`) and puts both tokens in
   the vault as the Mac's `__<LABEL>` pair.
3. The private spec for the client's project gets the channel in `chats`,
   in `mentions`, as `chat_roles` `teammate`, and with a `rooms` line: team
   chat for the client's CRM team; answer whoever mentions you; engineering
   requests go to the project's Eng chat, else Tom.
4. Tom's id in that workspace is added to `VALOR_OPERATOR_SLACK_IDS`.
5. The Mac is updated. The update shows both workspaces connected.
6. Tom mentions Valor in the channel and gets an answer. The date is
   recorded.

## Tests (fake wires, as `tests/test_voice.py` and `tests/test_reactions.py` use)
- `credentials` with two whole pairs returns both. With a half pair it
  returns only the whole one. With none it exits.
- `copy`: the base pair and a `__LABEL` pair are filed under their two
  teams. A pair for `<MACHINE>_2` (another Mac) is not read.
- A live message on team B's wire in a chat a spec owns is recorded as
  `B/<channel>`, with Valor's own mention named from B's user id. The same
  channel id with team A is not owned and is acked.
- Gap fill reads each chat through its own team's wire. Workspace A, down
  or out of budget, does not stop B's chats from filling.
- A send and a reaction to B go out on B's wire. A send to a team that
  isn't configured is `Failed`. A send to one configured but not yet
  connected is `Unknown`.
- One workspace's connect failing in a loop still lets the other receive
  and send.
- The outbox name and `states` for two parts cover up/up, up/down and
  down/down. A bool bridge (Telegram) keeps today's name.
- `permissions.slack` reloads the job when a second team's keys are
  `written`.
- The existing one-workspace Slack tests pass unchanged.

## Out of scope
- A second bridge, poller, or Slack MCP intake.
- More than two workspaces on one Mac (the 63-byte name).
- Making the client's further channels (each is a spec entry, later).

No governance is added: nothing refuses, gates or approves. The
`not a connected workspace` failure already exists.
