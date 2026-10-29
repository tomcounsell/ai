# Every room connected; team chats quiet and non-engineering

Tom, valor team chat, 2026-10-17 03:07-03:14 UTC: "you're missing some
rooms in that list"; "in general the team chats don't ever do enginering
work. that's why we have separate eng chat groups ... in the team chats,
Valor usually waits to be mentioned and mostly stays quiet"; "go ahead and
enact it all". Source of the rooms: the old system's
`~/Desktop/Valor/projects.json`, read as data.

**Base.** Builds on f3824ece2f03's `rooms`, which is not on `main` yet
(its unmerged commit b34cf0857 has `Spec.rooms` keyed
`"telegram:<id>"`, `intake.room`, a `Room:` line in the Brief). The build
does items 1-4 and their tests first; item 5 and the room tests start only
after that commit has merged and this branch is rebased on it, and match
its actual key format and `room` signature then, whatever they turn out to
be. If it has not merged when items 1-4 are done, the build waits on it
(a question to Tom names the dependency). No second mechanism.

## What is built

1. **Specs.** `projects/northwind.toml` (repo `northwind-co/northwind-consulting`,
   machine "Valor the Bald", kb "Northwind"; kind and suite read from the
   repo) and `projects/litware.toml` (repo `yudame/litware`, kb "LITWARE").
   The old system had litware `machine = "unassigned"`, `paused`: the spec
   keeps `machine = "unassigned"`, so it is listed but no machine hears it
   until Tom names one. Chats added:
   - northwind: Eng -1009420849132; team -1009195413795, -1009361301051
   - litware: Eng -9192132517; team -9415349335
   - fabrikam: team -1009151353130
   - valor: Agent Builders Chat -1009442964902, Perplexity Build Comp -1009784202542
2. **The handle wakes.** `MENTION` is `(?<![\w])@?valor\b`, so a Telegram
   tap-mention `@valorengels` does not match. It becomes
   `(?<![\w])@?valor(?:engels)?\b`, matching "valor", "@valor", "hey
   valor" (the old triggers) and Valor's handle.
3. **Mentions only** for every team chat: Yudame, Fabrikam, both Northwind
   groups, LITWARE, Agent Builders, Perplexity (`mentions`, which exists).
   Eng groups, the operator group and Tom's own chats are not listed.
4. **DMs from others.** `team_spec` accepts a Telegram DM a spec lists, as
   well as a group (Tom's DM stays his via `_toms_own`). A DM is never
   mentions-only. The chat session's opening says "a direct message with
   <name>" for a DM instead of "team chat". As the old `dms` block did
   ("All DMs use teammate persona against the ai repo"), every DM is listed
   in `valor.toml`, so the Cowboy, which runs a kernel, answers all of them
   now; each DM's room names the person and the project they work on. From
   the old whitelist: Riley 982500498, Max Reed 9121238902, Noel Ford
   9101392573, Alex Marsh 9350312702 (valor); Avery 9283348351, Casey
   Lakeshore 9310164543, Taylor Ellis 9122432271, Jordan 9860592743, Jamie
   Moss 9566926312, Harper 9705101085 (northwind); Logan Pratt 9500152038
   (fabrikam); Morgan Byrd 908253545 (orbit-os); Glen R 920543245
   (tailspin, customer service).
5. **Email lanes.** Exact senders already work (`email:<address>`):
   tailspin someone@client.example, customer2@example.com,
   customer3@example.com (tom@yuda.me is Tom's own already); northwind
   teammate@example.com. Domains need code: an `email:@<domain>` entry matches a
   sender whose address ends in exactly `@<domain>` (`owns`,
   `_project_for`; IMAP's `FROM "@domain"` already searches it). Exact
   sender beats domain. Added: fabrikam `@fabrikam.example`,
   `@fabrikam-sports.example`; northwind `@northwind.example`; orbit-os
   `@proseware.example` (its comment about the left-out domain goes).
6. **Room descriptions** (`rooms`), one per chat added, plus:
   - Each team chat: its purpose; "No engineering or SDLC work starts here.
     Web search and filing GitHub issues are fine. Point engineering
     requests to <its Eng group>." Agent Builders adds "a public group".
     Perplexity Build Comp and Agent Builders point to Eng: Valor.
   - yudame and fabrikam's Eng-side texts revised to this rule (yudame
     has no Eng group: requests go to the Eng group of the project asked
     about, else Tom).
   - DMs: "A direct message with <name>, who works on <project>"; same no-
     engineering rule, pointing to that project's Eng group. Glen's:
     customer service for Tailspin.
   - Email: `intake.room` for an email thread takes the session's project
     and returns that spec's `rooms."email"` if set, else today's
     `EMAIL_ROOM`. tailspin's says customer service; the others teammate.
7. Docs: `projects/README.md` (domain entries, DMs), `docs/bridges/telegram.md`
   (DMs bind like team chats), the intake docstring's table.

**No hard lock.** Tom asked for one "if possible". A lock is a restriction;
under the governance rule it needs an incident it prevents, and none has
happened (the group was not even connected). The description governs; if a
message there ever starts engineering, that is the incident, and the lock
goes to Tom then. The delivery says this. This diff adds no governance.

**Named risk, not fixed.** Email is unverified (`core/channels.py`), so a
From can be forged. An exact sender admitted one person; a domain lane
lets anyone forging an address at that domain open a session under that
project. The session carries no Tom's rights (no email does), so the cost
is a reply and the project's context in it. The delivery states this next
to the no-lock note.

## Out of scope

Slack DMs (not in the old list); `trusted_domains` (no email carries
rights); old personas' read-only permissions (the descriptions carry the
rule); rolling out the Pirate, Captain or Bald. Rooms on those machines
stay inert until their rollout, and the delivery lists them: Yudame, the
email lanes and team groups of fabrikam, tailspin, orbit-os (Captain,
Pirate), and all northwind groups and lanes (Bald); litware has no machine.

## Stakes

A wrong entry makes Valor answer every message in a public or client group,
or go deaf in Tom's or an Eng chat; config and one binding rule, reversible
by a commit.

## Tests

- Real specs: every team chat above is in its spec's `mentions`; no Eng
  group, operator group or Tom's DM is; every listed chat has a room;
  northwind and litware load.
- A mentions team chat: unaddressed is `heard`, "Valor ..." and a reply to
  Valor are `steer` (existing test, kept); "@valorengels can you look" is
  `steer`; "valorous" stays `heard`.
- Real specs: every old-whitelist DM is in `valor.toml`'s chats.
- A listed DM from another person: every message `steer`s that DM's
  session, no mention needed; an unlisted DM binds `none`; Tom's DM still
  binds as his own chat.
- Email: `someone@client.example` is owned and binds under northwind; `x@evilnorthwind.example`
  and `someone@client.example.evil.com` are not owned; an exact sender listed in
  another spec wins over a domain.
- `room`: an email thread under tailspin gets its email room; under a
  spec with none, `EMAIL_ROOM`; a DM gets its room.
- Full suite under 5 minutes.

**Live (done).** After the merge and a Cowboy bridge restart, in a team
chat the Cowboy hears (Agent Builders Chat or Perplexity Build Comp;
Yudame is the Pirate's, which has no kernel until its rollout) a message
naming Valor (and one tap-mentioning @valorengels) gets a reply and an
unaddressed one gets none, and Tom has seen it. Yudame itself is checked at the Pirate's rollout.
