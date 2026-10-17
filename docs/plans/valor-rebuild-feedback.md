---
tracking: none
slug: valor-rebuild-feedback
type: plan
status: draft
---

# Valor rebuild: Tom's feedback

Tom's rulings on [the rebuild plan](valor-rebuild.md) (2026-10-03), on the governance classifier (2026-10-07), and on autonomy (2026-10-09), and what each changes.

## Tom's feedback (2026-10-03)

On the "Stop means stop" amendment, in Tom's words: "I think this meant for
the orchestration between Valor and his subagents. Valor decides who stops
and who gets more budget. Valor's communication with the user is as a
project manager speaks to a CEO, so this kind of think would never come
up."

And: "Yes, AND the human also doesn't even get merge taps. Valor also
decides that based on his high level objective. The guiding principle is a
CEO x project manager relationship. If a CEO wants to get technical he can,
but it'd be on the CEO's lead. by defualt, Valor should never raise
technical decisions to a CEO, only ask about vision, business priorities,
and cost benefits of tradeoffs that need to be made in the context of the
how the company works"

What this changes:

- **Stop means stop** governs Valor's subagents. When a task's rounds are
  spent, Valor (and, until takeover, the /build lead) decides whether it
  gets another patch round or stops. It does not go to Tom.
- **Merges are Valor's call**, against the task's objective. "A merge held
  for Tom's tap" in the rebuild plan, in each task plan, and in
  `.claude/skills/build/SKILL.md` no longer holds; the skill text gets the
  matching edit at the next /build.
- **Tom's queue** holds questions about vision, business priorities, and
  the cost and benefit of tradeoffs in how the company works, plus things
  only Tom holds (a password, an account). Technical decisions are never
  raised unless Tom asks to get technical.
- Unchanged: the governance paragraph in `CLAUDE.md`. Adding a check,
  gate, hook, review step, or guard still needs an incident, a mission
  item, and Tom's tap, one per instance.
- **The Macs' admin password** (the same on all of Valor's machines) is in
  1Password, vault `m-valor`, item `bgqjftkvj4witkswgdoxegdjsy`. A step
  that needs `sudo` reads it from there and never prints it.

Decided the same day, recorded in each task's plan: the five passed
deliveries merge (3a, 3c, 4.2, 1.4d, 1.4c part one); one more patch round
for 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, and 1.5, in that order; no
timers on email commands, a stop ends a hung one; open question 17's DMARC
check parked; `idle_turns` removed.

## Tom's ruling on governance.adds (2026-10-07)

Count as a new checkpoint only a step that looks at work and can stop it or
send it back. An ordinary fix that makes code correct, like a lock that
makes two runs take turns, is no checkpoint, the way tests already are.

The incident: review row 1436 on task 09975a1c2e52, the expiry-lock fix for
review N4. `governance.adds` answered true on two hunks, the advisory lock
in `core/routines.py` and the `docs/routines.md` sentence describing it. The
Opus reviewer wrote "This is not governance", and since a reviewer cannot
remove an instance, a confirmed fix waited on a tap.

What this changes (the plan, in git history):

- The question's gloss names a step that judges work, a request, or an
  action and holds, redirects, or refuses it on that judgement, or a step
  someone must pass; code that makes the work itself correct and prose that
  only describes what code does are none of these, beside tests.
- A settled governance row is reused, and a failed one counts as a spent
  rerun, only under the question that answered it, so a reworded question
  is asked fresh.
- A reviewer's note that contests an instance is an incident against
  `governance.adds`, answered by a classifier change, never by a grant
  (`docs/judgement-layer.md`, note 6).
- Unchanged: the governance paragraph in `CLAUDE.md`.

## Tom's ruling on autonomy (2026-10-09)

Asked what ceiling a task started from Telegram should get, Tom said:

> Not only should the agent act, but it should employ agents to act and
> only come back to the user for a report on what was done, or a question
> that must be asked to resolve a major blocker. Valor, this agent, is
> using its own accounts so it is expected to act independently to send,
> build, merge without asking any human for approval. In cases where the
> agent does seek a 2nd opinion, it should spawn an advisor agent to help
> it decide then act.

What this changes, beyond the 2026-10-03 ruling that merges are Valor's:

- No `act` effect waits for a human. Sends, pushes, and merges inside a
  task's ceiling leave when the kernel decides, not on a tap.
- A task Tom starts from any channel runs at `act`.
- Tom receives reports of what was done and questions that resolve a major
  blocker. Nothing else reaches him.
- A second opinion comes from an advisor agent Valor spawns, never from
  Tom. The advisor informs; Valor decides and acts. An advisor is never a
  step work must pass, so it adds no governance.
- Unchanged: the governance paragraph in `CLAUDE.md`. A new check, gate,
  hook, round, review step, or guard still needs its incident, mission
  item, and Tom's grant (`python -m core grant`). Removing an approval step
  is not adding one and needs no grant.

## Tom's ruling on the yudame plugins (2026-10-15)

> yudame/skills is live Claude plugin marketplace ... we have that for
> everyone on the yudame team, including Valor, so that should be
> automatically loaded in and kept recent

What this changes:

- Valor's turns carry `yudame` and `yudame-dev`, refreshed from
  `yudame/skills` at each task's start
  (c18, absorbed by strip.md).
- Part of the open item "Skill system requirements" is answered: shared
  skills live in `yudame/skills`, not in this repository.
- Unchanged: safe mode, and authority in `core/`. A plugin brings skill
  text only.

## Tom's ruling on Sentry (2026-10-15)

> for Sentry, we should have the Sentry CLI auth connected for projects
> that use it

What this changes:

- A turn on a project that reports to Sentry can run `sentry-cli` against
  it, read-only, through the gateway with the kernel's token
  ([c19-sentry-route.md](c19-sentry-route.md)).
- Unchanged: a turn holds no credential.

## Tom's rulings (2026-10-16)

On what Valor is for, Tom said: "full operator, all of the above." On whose
accounts: "on his accounts. he has a full human level google workspace seat
as well as human-level memberships in all platforms." And: "he already has
a logged in browser on each machine." On authority, Tom chose "Fully free"
over "free inside a task" and "reads free, acts tapped", and on money: "no
ceiling. Valor uses claude -p on his claude subscription".

On skills: "Like the previous system, Valor should have the combination of
his own internal skills, yudame skills, and the skills in the project repo
he is working in."

On orchestration: "there was an intended distinction for the agent that
acts with Valor's persona for communication is simply an orchestrator of
subagents and it's those subagents that are the ones that use all the
skills." Asked whether workers are subagents or kernel tasks: "Both, by
size", "but other kernel tasks report to the parent Valor agent and are not
allowed to interact with comms channels."

On the web: "for web fetch, I recommend a dedicated subagent that must be
used."

On posting: "there is no gate or block or approval from Tom because Valor
posts as himself on his own accounts."

On email (2026-10-17): "email threads are chat threads too."

On the SDLC (2026-10-17), asked whether the kernel's stages should run the
yudame-dev do-issue, do-plan and do-pr-review skills with GitHub issues and
PRs: "no, it's better the kernel has it's own SDLC. my yudame ones are made
for humans and their tendency to scope creep."

What this changes:

- **Valor is a full operator.** He reads the world (the web, media, the
  links, images and videos Tom sends, memory on demand), uses accounts
  (Gmail, Calendar, Docs, Sheets, Drive, Linear, Sentry, Stripe, Notion,
  Telegram history, SMS codes for 2FA), produces deliverables (decks,
  Office documents, diagrams, images), posts on LinkedIn and X, and builds
  managed agents for clients (imagine-agent, build-agent, cowork). Issues
  #3636 to #3642 are all wanted, in no order.
- **Valor acts as himself, on his own accounts**: his own Google Workspace
  seat, his own memberships on every platform, his own logged-in browser on
  each machine. He never acts as Tom.
- **Fully free.** No post, send, portal submission or client agent's launch
  waits on a tap from Tom. There is no spend ceiling: Valor's turns run
  `claude -p` on his own Claude subscription, and spending stays metered.
- **Skills come from three places**: Valor's own internal skills, the
  yudame plugins from `yudame/skills`, and the project repository's own
  skills. A workspace turn already loads the last two; Valor's own skill
  set does not exist yet (`skills/` holds the SDLC stage files) and is an
  open item.
- **The persona orchestrates; workers do the work.** The persona's turn
  reads the chat, memory, task status and the repository, and starts,
  steers, answers and stops work. It never edits, runs a skill or uses the
  web. The persona judges each request's size: a small thing is a Claude
  Code subagent inside its turn; anything with a deliverable or a merge is
  a kernel task. Workers send their questions, progress and deliveries to
  the persona, the parent Valor agent, never to Telegram, email or Slack;
  only the persona speaks. Tom granted the comms rule directly.
- **One conversation turn, everywhere.** Every inbound message that does
  not bind to running work, Tom's DM included, gets the persona's
  conversation turn, which answers, chats, stays quiet, steers, or starts
  work; a reply's steer, answer and stop bind as they do now. It replaces
  starting an SDLC task on each message of Tom's that is not a reply, and
  generalises step 2 of [team-chat.md](team-chat.md) to every channel. The
  build is [orchestration.md](orchestration.md).
- **The web goes through one web agent.** Only the web agent holds
  WebFetch and WebSearch; the persona and every other agent ask it, and it
  returns findings as data, so raw pages never enter the caller's context.
  Replays get no web. Tom granted this as a capability removal; its
  incident is inbound and retrieved-content injection (issue #3648), with
  the ninety-day expiry every guard carries.
- **Posting craft is skills, not a gate.** de-slop and authenticity-pass
  are used when posting; nothing waits on Tom.
- Unchanged: the governance paragraph in `CLAUDE.md`.
