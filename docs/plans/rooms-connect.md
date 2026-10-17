# Every room connected; team chats quiet and non-engineering

Tom, valor team chat, 2026-10-17 03:07-03:14 UTC: "you're missing some
rooms in that list"; "in general the team chats don't ever do enginering
work. that's why we have separate eng chat groups ... in the team chats,
Valor usually waits to be mentioned and mostly stays quiet"; "go ahead and
enact it all". Source of the rooms: the old system's
`~/Desktop/Valor/projects.json`, read as data.

**Base.** Builds on f3824ece2f03's `rooms` (`Spec.rooms`, `intake.room`,
the `Room:` line in the Brief). The build turn rebases on `main` once that
commit is there; no second mechanism.

## What is built

1. **Specs.** `projects/cyndra.toml` (repo `Cyndra-AI/cyndra-consulting`,
   machine "Valor the Bald", kb "Cyndra"; kind and suite read from the
   repo) and `projects/satsol.toml` (repo `yudame/satsol`, kb "SATSOL").
   The old system had satsol `machine = "unassigned"`, `paused`: the spec
   keeps `machine = "unassigned"`, so it is listed but no machine hears it
   until Tom names one. Chats added:
   - cyndra: Eng -1003900483201; team -1003794218389, -1004385743413
   - satsol: Eng -5136818964; team -5095889193
   - psyoptimal: team -1002600253717
   - valor: Agent Builders Chat -1003879986445, Perplexity Build Comp -1003936769817
2. **Mentions only** for every team chat: Yudame, PsyOPTIMAL, both Cyndra
   groups, SATSOL, Agent Builders, Perplexity (`mentions`, which exists).
   Eng groups, the operator group and Tom's own chats are not listed.
3. **DMs from others.** `team_spec` accepts a Telegram DM a spec lists, as
   well as a group (Tom's DM stays his via `_toms_own`). A DM is never
   mentions-only. The chat session's opening says "a direct message with
   <name>" for a DM instead of "team chat". DMs from the old whitelist:
   valor: Kevin 577036901, Matt Rideout 2106858278, Nick Frith 7954357506,
   Andy Malkin 2019570687; cyndra: Charlie 7312372670, Colin Behring
   8209004019, Thabiso Epema 8762685703, Johann 6084902164, Jess Mason
   7388810030, Hazem 7178632018; psyoptimal: Lewis Parrott 7820562338;
   gato-os: Mike Berson 469027377; cuttlefish: Graham D 437868058.
4. **Email lanes.** Exact senders already work (`email:<address>`):
   cuttlefish nav@krystallabs.io, gcason2@gmail.com,
   grahamhderry@gmail.com (tom@yuda.me is Tom's own already); cyndra
   charlie@yuda.me. Domains need code: an `email:@<domain>` entry matches a
   sender whose address ends in exactly `@<domain>` (`owns`,
   `_project_for`; IMAP's `FROM "@domain"` already searches it). Exact
   sender beats domain. Added: psyoptimal `@psyoptimal.com`,
   `@psyoptimalsports.com`; cyndra `@cyndra.ai`; gato-os
   `@chainstarters.com` (its comment about the left-out domain goes).
5. **Room descriptions** (`rooms`), one per chat added, plus:
   - Each team chat: its purpose; "No engineering or SDLC work starts here.
     Web search and filing GitHub issues are fine. Point engineering
     requests to <its Eng group>." Agent Builders adds "a public group".
     Perplexity Build Comp and Agent Builders point to Eng: Valor.
   - yudame and psyoptimal's Eng-side texts revised to this rule (yudame
     has no Eng group: requests go to the Eng group of the project asked
     about, else Tom).
   - DMs: "A direct message with <name>, a teammate on <project>"; same no-
     engineering rule. Graham's (cuttlefish): customer service.
   - Email: `intake.room` for an email thread takes the session's project
     and returns that spec's `rooms."email"` if set, else today's
     `EMAIL_ROOM`. cuttlefish's says customer service; the others teammate.
6. Docs: `projects/README.md` (domain entries, DMs), `docs/bridges/telegram.md`
   (DMs bind like team chats), the intake docstring's table.

**No hard lock.** Tom asked for one "if possible". A lock is a restriction;
under the governance rule it needs an incident it prevents, and none has
happened (the group was not even connected). The description governs; if a
message there ever starts engineering, that is the incident, and the lock
goes to Tom then. The delivery says this. This diff adds no governance.

## Out of scope

Slack DMs (not in the old list); `trusted_domains` (no email carries
rights); old personas' read-only permissions (the descriptions carry the
rule); rolling out the Pirate, Captain or Bald, so rooms on those machines
stay inert until their rollout.

## Stakes

A wrong entry makes Valor answer every message in a public or client group,
or go deaf in Tom's or an Eng chat; config and one binding rule, reversible
by a commit.

## Tests

- Real specs: every team chat above is in its spec's `mentions`; no Eng
  group, operator group or Tom's DM is; every listed chat has a room;
  cyndra and satsol load.
- A mentions team chat: unaddressed is `heard`, "Valor ..." and a reply to
  Valor are `steer` (existing test, kept).
- A listed DM from another person: every message `steer`s that DM's
  session, no mention needed; an unlisted DM binds `none`; Tom's DM still
  binds as his own chat.
- Email: `x@cyndra.ai` is owned and binds under cyndra; `x@evilcyndra.ai`
  and `x@cyndra.ai.evil.com` are not owned; an exact sender listed in
  another spec wins over a domain.
- `room`: an email thread under cuttlefish gets its email room; under a
  spec with none, `EMAIL_ROOM`; a DM gets its room.
- Full suite under 5 minutes.

**Live (done).** After the merge and a Cowboy bridge restart, in a team
chat the Cowboy hears (Agent Builders Chat or Perplexity Build Comp;
Yudame is the Pirate's, which has no kernel until its rollout) a message
naming Valor gets a reply and an unaddressed one gets none, and Tom has
seen it. Yudame itself is checked at the Pirate's rollout.
