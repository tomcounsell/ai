# Anyone's Slack DM, by workspace

Tom, valor team chat on Slack, 2026-11-03 09:14 UTC: "approve direct
messages with anyone in Slack for teammate persona use ... for both
workspaces", Yudame's to the yudame project on the Pirate and Pursue
Mastery's to mastery on the Captain. 09:19 UTC he confirmed the defaults
(DMs already listed keep their project and role; everyone else's DM goes
by workspace, teammate role, every message answered). 09:21 UTC: his own
DM in Pursue Mastery is not an exception; it falls into that workspace's
catch-all like anyone's. Only his Yudame DM, listed in `valor.toml`, stays
his own chat.

**Stakes.** Every inbound DM in two workspaces is routed by this rule
across four Macs that all receive every DM (each runs its own app on
Valor's user), so a wrong match makes two Macs answer one person or
silences Tom's own DM; the routing is reversed by editing a spec.

## How a DM is received today

Each Mac's bridge gets every DM Valor's user can see and keeps only those
`intake.owns` names: a `chats` entry `slack:<team>/<user>` in a spec placed
on that Mac. Someone else's listed DM is a team chat (`team_spec`) with its
`rooms`/`chat_roles`; Tom's DMs (`VALOR_OPERATOR_SLACK_IDS`) are his own
chat (`_toms_own`) wherever a spec lists them. An unlisted DM is acked and
dropped on every Mac.

## What is built

1. **A workspace DM entry.** A spec's `chats` may hold `slack:<team>/*`:
   every DM in that workspace that no spec on any Mac lists by id. It takes
   `rooms`, `chat_roles` and `mentions` keyed by the same string. The Slack
   row of `core/channels.py` admits it; other channels do not.
2. **One lookup.** `intake` gets one function giving the spec that takes a
   chat: its exact listing on this Mac, else, for a person's id (`U`/`W`,
   as `inbound.is_dm` reads it) in a team whose `/*` a spec on this Mac
   lists, that spec, unless any spec in `Spec.all()` lists the id exactly
   (so Tom's Yudame DM, on the Cowboy, is never caught by the Pirate).
   `owns`, `team_spec`, `room`, `role`, `_project_for`, `session_for` and
   `_mentions_only` use it in place of their exact match. A channel id
   (`C`/`G`, an mpim) is never caught.
3. **Tom's own Slack DM is one a spec lists.** `_toms_own` for Slack:
   in `operator_slack_ids` and listed by id in some spec. His Pursue
   Mastery DM is then a team chat under mastery; his messages there still
   carry `by: tom`.
4. **The session's opening.** A DM session whose other person is Tom no
   longer says "not Tom".
5. **Gap fill** skips `/*`: `owned` lists exact ids only, so no pass opens
   an IM for `*`. A caught DM is received live; Slack's retries cover a
   bridge restart (decision below).
6. **Docs.** `docs/bridges/slack.md` ("Who is received"), the `Spec`
   docstring, `projects/README.md`, `projects/example.toml`.

**After merge (rollout).** Edit the private specs directly
(docs/plans/private-projects.md): `yudame.toml` (Pirate) and
`mastery.toml` (Captain) each get their workspace's `slack:<team>/*` in
`chats`, `chat_roles` `teammate`, not in `mentions`, and a `rooms` line: a
1:1 direct message with a teammate in that workspace; answer every
message; no engineering or SDLC work starts here; engineering requests go
to the project's Eng chat (#yudame-dev / #mastery-dev), else Tom. Load each
with `Spec.load` and put the diffs in the delivery. Update the Pirate and
Captain over ssh with each kernel job's own launchctl environment, as
earlier rollouts did; confirm each kernel is on the merged commit and its
bridge connected. Confirm live: Tom's Pursue Mastery DM gets a teammate
reply from the Captain (a session under mastery, role teammate) and no
other Mac records it; on each Mac's ledger, a Yudame DM from someone not
listed is recorded only on the Pirate. A Yudame DM from a person not yet
listed needs someone other than Tom to send it; the delivery names who
and whether it arrived.

**Not here.** No new check, gate or allowlist (this widens who is
received). Slack Connect users from another org (their DM's id carries
their home team, so no `/*` entry matches) and gap fill of caught DMs
are left out; named in the delivery.

**Decisions.** Live-only for caught DMs: listing every IM each tick
(`conversations.list`) buys recovery from an outage longer than Slack's
retries, which has not happened. The catch-all is in the spec, not a
setting, so one mechanism (`chats`) keeps placing chats on Macs.

## Tests (made-up ids; fake wires as `tests/test_reactions.py`)

- Spec: `slack:TA/*` loads with its room, role and mention keys;
  `telegram:*` gains no meaning; a malformed `slack:TA/*X` is refused.
- `owns` on the Mac with `TA/*`: an unlisted `TA/U1` and `TA/W1` yes; `TA/C1`,
  `TA/G1` no; `TB/U1` no; `TA/U2` listed by a spec on another Mac no. On
  another Mac, `TA/U1` no. `owned` never contains `*`.
- `team_spec`/`role`/`room` for `TA/U1`: the `/*` spec, `teammate`, its
  line; an exactly listed DM keeps its own entry.
- Binding: every message in a caught DM wakes the session (no mention);
  Tom's id in `TB`, unlisted, binds to a team session under the `/*` spec
  with `by: tom`, and its opening does not say "not Tom"; his listed `TA`
  DM still binds as his own chat.
- `session_for` on a caught DM starts it under the `/*` spec.
- Bridge: a live DM on wire B from an unlisted user is recorded as
  `TB/U..`; with no `TB/*` spec it is acked and not recorded; gap fill
  makes no call for `*`.
