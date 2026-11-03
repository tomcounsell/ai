# Anyone's Slack DM, by workspace

Tom, valor team chat on Slack, 2026-11-03 09:14 UTC: "approve direct
messages with anyone in Slack for teammate persona use ... for both
workspaces", Yudame's to the yudame project on the Pirate and the other
workspace's to the Captain's project. No question was asked from this task;
two answers reached it as steering from the chat session it reports to,
in this task's first prompt, each quoting Tom in thread #see-issue-3680:
09:19 UTC (`1793697574.590689`), "Good call on those exceptions ...
That's perfect", confirming the Brief's defaults (DMs already listed keep
their project and role; everyone else's DM goes by workspace, teammate
role, every message answered); and 09:21 UTC (`1793697665.793929`), "A DM
from me in that workspace should just fall altogether with the catch-all
of all DMs within [that workspace]". So the Brief's "Tom's own DM keeps its
project" covers his Yudame DM only (listed in `valor.toml`); his DM in the other
workspace is a teammate DM under the Captain's project.

**Tom's ids, read from each Mac's launchd jobs (2026-11-03).** His
id in the other workspace (kept out of this repo) is a member of that workspace
(not Slack Connect: its team is the workspace's own, so `inbound.person`
gives it, not his Yudame id). On the Captain it is in
`VALOR_OPERATOR_SLACK_IDS` of `com.valor.kernel.slack` but not of
`com.valor.kernel`, where binding runs; the Pirate's jobs and this Mac
carry only his Yudame id `T0B6E8JPE3C/U0B78H17XQ8`. Issue 3680's rollout
step 4 meant every job to carry it; the rollout here adds it to the
Captain's kernel job, so his messages there carry `by: tom`.

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
   Every Mac reads the same synced folder, so each decides alike. "Lists"
   means a spec that loads: a paused file (`*.toml.paused`, which `Spec.all`
   does not read) or one refused at load lists nothing, and its DMs are
   caught by the workspace entry, so that person still reaches Valor as a
   teammate rather than no one. Today every spec loads and the one paused
   file lists no DM (read 2026-11-03).
   `owns`, `team_spec`, `room`, `role`, `_project_for`, `session_for` and
   `_mentions_only` use it in place of their exact match. A channel id
   (`C`/`G`, an mpim) is never caught.
3. **Tom's own Slack DM is one a spec lists.** `_toms_own` for Slack:
   in `operator_slack_ids` and listed by id in some spec. Today, with the
   Captain's kernel lacking his id there, his DM there would already be
   no own chat; once the kernel carries it (rollout) it would be, and with
   no spec listing it `_project_for` would start an own session under
   `valor` on the Captain. This keeps Tom's 09:21 answer true whatever a
   job's settings hold: his DM there is a team chat under the Captain's project, his
   messages `by: tom`.
4. **The session's opening.** A DM session whose other person is Tom no
   longer says "not Tom".
5. **Slackbot is a bot.** `inbound.wanted` already means "not a bot's",
   but Slackbot's DM posts (`user` `USLACKBOT`, no `bot_id`) pass it, and
   the catch-all would open a teammate session for each reminder. A bug
   fix in `wanted`: `USLACKBOT` is not a person.
6. **Gap fill** skips `/*`: `owned` lists exact ids only, so no pass opens
   an IM for `*`. A caught DM is received live; Slack's retries cover a
   bridge restart (decision below).
7. **Docs.** `docs/bridges/slack.md` ("Who is received"), the `Spec`
   docstring, `projects/README.md`, `projects/example.toml`.

**After merge (rollout), in this order.** A Mac on pre-merge code refuses
a spec holding `slack:<team>/*` (the Slack pattern admits no `*`) and
`Spec.all` drops the whole spec, so the specs change last.
1. Update every Mac that reads `~/Desktop/Valor/projects` (Cowboy,
   Pirate, Captain, Bald) to the merged commit: this Mac with the update
   skill, the others over ssh with each kernel job's own launchctl
   environment, as earlier rollouts did. Confirm each kernel is on it and
   its bridges connected.
2. Append his id in the other workspace to `VALOR_OPERATOR_SLACK_IDS` in the
   Captain's `com.valor.kernel` (and its Telegram and email jobs, as issue
   3680's step 4 said) and reload them.
3. Edit the private specs directly (docs/plans/private-projects.md):
   `yudame.toml` (Pirate) and the Captain's project spec each get their
   workspace's `slack:<team>/*` in `chats`, `chat_roles` `teammate`, not in
   `mentions`, and a `rooms` line: a 1:1 direct message with a teammate in
   that workspace; answer every message; no engineering or SDLC work
   starts here; engineering requests go to the project's Eng chat
   (#yudame-dev / the Captain's project's dev channel), else Tom.
4. On each Mac, through a turn or the kernel's own environment (sshd cannot
   read `~/Desktop`), read `Spec.all()`: both specs load, and on the Pirate
   `valor.toml` loads too (it is what keeps Tom's Yudame DM off the
   Pirate). Diffs and results go in the delivery.
5. Confirm live: Tom's DM in the other workspace ("hi", already sent, then his
   next) gets a teammate reply from the Captain (a session under the Captain's project,
   role teammate) and no other Mac records it. For Yudame, a DM from
   someone not listed is recorded only on the Pirate and answered there.
   This task posts nothing, so it cannot ask anyone to send one: if none
   arrives, the delivery reports the Yudame half of the done criterion as
   not yet met, naming what is needed, not done on tests alone (rule 6).

**The delivery** quotes Tom's 09:19 and 09:21 answers with their `ts` as
the basis for item 3, and names one consequence of "a spec that does not
load lists nothing": a Mac that momentarily cannot read `valor.toml` (an
iCloud eviction, a bad edit) would catch Tom's Yudame DM as a yudame
teammate DM on the Pirate while the Cowboy answers it as his own, so two
Macs answer him until it reads again.

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
- `wanted`: a `USLACKBOT` DM is neither caught nor recorded.
- A DM listed only in a spec that is refused at load, or in a
  `*.toml.paused` file, is caught by the `/*` spec.
- Tom's reaction on Valor's post in his caught other-workspace DM binds as a
  team-chat reaction does (to the DM's session), raises nothing and starts
  no task.
- `session_for` on a caught DM starts it under the `/*` spec.
- Bridge: a live DM on wire B from an unlisted user is recorded as
  `TB/U..`; with no `TB/*` spec it is acked and not recorded; gap fill
  makes no call for `*`.
