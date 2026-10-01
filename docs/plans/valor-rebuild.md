---
tracking: none
slug: valor-rebuild
type: plan
status: draft
---

# Valor rebuild

The plan for building Valor from the minimal kernel on this branch to a
system ready for cutover. One milestone per component, in the order Tom set
on 2026-10-01: the kernel and its Postgres data, the Telegram and email
bridges, the harnesses, persona and routines, tools on demand, memory last.

Inputs: [mission.md](../mission.md), [architecture.md](../architecture.md),
[sdlc-state-machine.md](../sdlc-state-machine.md), the rest of `docs/`, the
demonstration ([rebuild-demonstration.md](rebuild-demonstration.md)), the
baseline ([rebuild-baseline.md](rebuild-baseline.md)), and Tom's answers in
[rebuild-open-questions.md](rebuild-open-questions.md). Where this plan and a
doc disagree, the milestone that builds the mechanism fixes the doc.

## How every milestone is built

Every milestone goes through the pipeline in
[sdlc-state-machine.md](../sdlc-state-machine.md): judge, clarify if thin,
plan, critique, build, then test, review, and docs in parallel on each
candidate, patch in the builder's own resumed session, and a merge held for
Tom's tap. Each stage gets its goal and its exit evidence, not steps.

Each milestone below states:

- **Goal**, tied to the mission item or constraint it serves.
- **Done**, as evidence: tests on real services, and a replay or emulator
  result where one applies. Narration is not evidence.
- **Stakes**, which set the critique and review loops (0 to 2 each). The
  guidance in the state machine doc applies: the kernel, stored data, money,
  and anything that sends to a person are a 2.
- **Absorbs**, the known tech debt the plan pulls in ("leave it cleaner").
- **Leaves out**, on purpose.

A milestone may be split into several tasks, each through the pipeline on
its own. The milestone is done when all of its evidence holds on the merged
branch.

Governance binds every milestone. A milestone that needs a new check, gate,
hook, or review step names the incident and mission item and waits for
Tom's tap. The checkpoints already granted are the ones in the state
machine doc (the request judge, the breadth check, critique and review
loops), each ledgered with its ninety-day expiry.

## Execution: who builds what

**Phase A, session-driven (milestone 1).** The new system cannot run its
own pipeline until milestone 1 builds it. So milestone 1 is built by agents
driven from a Claude Code session in `~/src/valor-rebuild`, playing the
same stages by hand:

| Stage | Who runs it in phase A |
|---|---|
| judge, clarify | the driving session reads the task and asks Tom only what changes the plan |
| plan | the builder agent writes the plan file in the repo and commits it |
| critique | a fresh agent that never saw the builder's context |
| build | one builder agent per task |
| test, review, docs | three fresh agents in parallel on the same commit; review is Opus |
| patch | the same builder agent, resumed with every finding at once |
| merge | Tom's tap, then a push to the rebuild branch |

The driving session keeps no findings log beyond the plan file and the
verdicts in each commit message.

**The takeover point.** When milestone 1 is done, Valor builds everything
after it through its own kernel. Tom starts each task and taps each merge
from the command line until the Telegram bridge lands, then from Telegram.
Each task's workspace is a fresh clone of this repository; a merge is a
held `act` that pushes to the rebuild branch on GitHub. The running kernel
is a separate checkout that is pulled and restarted after a kernel merge.

**Phase B, self-built (milestones 2 to 6).** A driving session steps in
only to repair the kernel when Valor cannot run its pipeline at all. Such
a repair is a task in the ledger like any other once the kernel runs
again, so the record shows every fix.

**Where it runs.** The rebuild is built on one of Valor's Macs. The kernel
milestones run beside the old system. From milestone 2 the new bridge uses
Valor's real Telegram account and mailbox: the old bridge on that Mac is
stopped with `worker-disable` only during live bridge test windows and
re-enabled after. The new bridge honors the same single-machine ownership
of chats, so it never reads chats another Mac owns.

## Milestone 1: the kernel and its Postgres data

Session-driven. Stakes: the kernel itself, critique 2 and review 2 for
every task.

**Kept from today's kernel.** The ledger and its schema (`core/ledger.py`,
`core/schema.sql`: append-only by grants and trigger, unique indexes,
advisory locks), the gateway (`core/gateway.py`), per-call reserve and
charge (`core/budget.py`), the broker and approvals (`core/broker.py`),
lossless stop and reaping (`core/runs.py`), the session and turn loop
(`core/session.py`), corrections (`core/corrections.py`), the `.valor/`
signal channel (`core/signals.py`), and the Claude Code wrapper
(`harnesses/claude_code.py`). All 38 tests stay green throughout.

**Replaced.** The hand-picked `--mode bare|clarify` (replaced by the judge
state), `done.md` writing `task.delivered` at once (it becomes a
candidate), prompt text inside `core/session.py` (moves to stage files), and
the hand-driven `run` loop's four-state fold (replaced by the typed state
machine).

### 1.1 Store and settings

**Goal.** A ledger the system cannot edit, with provenance on every row
Tom writes (**Reliable stop, recovery, and correction**; Mission item 6).

**Done.**
- A login as `valor_kernel` without its credential is refused; only the
  kernel process holds it (data.md gap 3).
- A fresh `python -m core migrate` holds correction 1.
- `approval.granted` carries `by`, `via`, `at`, `role_played`; approvals
  appear in the attention fold.
- `python -m core budget raise TASK N` writes `budget.raised` and the fold
  of remaining money includes it; one computation of "remaining", not two.
- A default test run meters a successful streamed call against a local
  upstream replaying a recorded response.
- One typed settings module with a model-seat registry and prices that
  carry the date they were checked.

**Absorbs.** Scattered constants (`BYTES_PER_TOKEN`, `REAP_GRACE_S`,
`IDLE_TURNS`, the price table, the claude path); hard-coded
`/Users/tomcounsell/...` paths; the unenforced spend marker in
`tests/README.md` (enforced in `conftest.py`, or the README sentence goes);
smoke scripts become `VALOR_LIVE` tests.

**Leaves out.** The objective tree (milestone 4), snapshot documents for
long streams (until a stream is measured slow).

### 1.2 The SDLC state machine

**Goal.** Tom never coordinates the gaps between steps, and nothing merges
on a model's say-so (Mission item 1, **Bounded authority and spend**).

**Done.**
- `State`, `Check`, `VERDICTS`, `TRANSITIONS`, `Candidate`, the join, and
  loop counts folded from rows, as specified in the state machine doc.
- Tests cover every row of the join table, a verdict outside its enum
  refused at write, a stale-candidate verdict ignored, and the merge
  refused unless all five predicate terms hold.
- A property test: every ledger prefix folds to exactly one state.
- `guard.granted` rows carry incident, mission item, and expiry; the
  granted checkpoints are seeded as guards.
- The broker's governance flag is set from a review or docs verdict, never
  by the requester.
- Stage instructions live as one file per stage under `skills/sdlc/`, each
  stating the goal and exit evidence; the kernel renders the file for the
  state into the turn.

**Absorbs.** `adds_governance` never set by any producer; `CLARIFY` telling
Valor to write `question.md` even with no question (contradicts
`no_material_question`); `PROTOCOL` hard-coding `push_branch` instead of
listing registered performers.

**Leaves out.** The versioned skill system; `skills/sdlc/` is plain files
until Tom's requirements are gathered.

### 1.3 The judgement port

**Goal.** Classification goes to the cheap tier, authority stays in the
kernel (**Three tiers**; Mission items 3 and 6).

**Done.**
- `JudgementPort` and the router in `core/`; Jev and the open-weight
  fallback as adapters in `tools/`, models pinned by version.
- A gateway route that meters non-Anthropic calls against the same task
  budget; `judgement.answered` and `judgement.failed` rows.
- Three sites wired: `intake.underspecified` (the judge state), the
  breadth call in `checks.test`, and the governance boolean.
- A calibration record over the baseline's six items for the judge site,
  with n and a Brier score.

**Absorbs.** The `--mode` flag and its tests are deleted.

**Leaves out.** The OpenAI Decisions API leg for images (no site needs
images yet); use shapes 2 to 5 and 10.

### 1.4 The checks and the merge

**Goal.** Delivery counts only after an independent check, and a merge
reaches the real branch only on Tom's tap (Mission item 1, Evidence
"Independent checks").

**Done.**
- The kernel provisions each task's workspace (lifted from
  `scripts/replay_workspace.py`), including the app's environment so the
  suite can run.
- Fresh sessions for critique, review, and docs; the docs branch's commits
  outside doc paths are dropped and recorded as a `changes` finding.
- `checks.test` runs the suite at head and base, then the breadth call.
- The blind verifier: Opus in a fresh session, rerunning the tests in an
  Apple container built by the kernel; `review.decided` carries the
  governance boolean. Container RAM measured.
- A GitHub push performer (`act`), so a released merge reaches
  the rebuild branch on GitHub.
- Transcript copies kept in the store with a digest.

**Absorbs.** Redis left running at replay teardown; replay databases
sharing one `test` role; `tools/workspace.py` test-only performers moved to
`tests/`; the broker's synchronous `perform` (made awaitable, which the
bridges need anyway); performers registered in a module-global dict (keyed
per task).

**Leaves out.** The headless browser (milestone 3); routing turns into
containers (open until one replay runs end to end in one).

### 1.5 The emulator, and the takeover gate

**Goal.** The pipeline is measured against human-labelled history before
it builds anything real (Evidence "Independent checks").

**Done.**
- The replay scripts move from `scripts/` to `tests/emulator/`; stand-in
  and judge calls go through the gateway under one budget (no
  `costs.jsonl`). The stand-in moves to an Opus-class model.
- **The takeover gate.** The full pipeline, judge to held merge, carries
  three baseline items: popoto #191 (thin), psyoptimal #872 (precise), and
  popoto #633 (stateful). Each reaches a held merge with fidelity and
  hidden-test results no worse than its bare baseline run, and with the
  attention it took logged. Spend within the $25 per-run cap.

**Absorbs.** `/tmp` shared between runs; answer keys for cuttlefish #646,
popoto #191, and popoto #188 confirmed by Tom (open item).

**Leaves out.** The `routed` arm's full sweep across all items (a routine
in milestone 4).

## Milestone 2: the Telegram and email bridges

Self-built. Stakes: sends to real people, critique 1 and review 2.

**Goal.** Tom gives Valor work and taps approvals where he already is
(Mission items 1 and 6), and nothing leaves under Valor's name without
passing the broker (**Bounded authority and spend**, **One identity**).

### 2.1 The resident kernel and the bridge port

The resident kernel arrives with the bridges (decided by default in the
open-questions file).

**Done.**
- A launchd `KeepAlive` process holds the gateway, the broker, the turn
  runner, and one turn slot held in Postgres. Killing it mid-task loses
  nothing: on restart it resumes from the ledger.
- The supervisor advances a task one state per event (a message, a turn
  ending, a verdict, an approval); same store in, byte-identical context
  out.
- Steering: a message for a task mid-turn is a row delivered at the next
  turn's start.
- The port in `core/`: `intake.receive`, `message.received` with a unique
  index on channel, chat, and message id, `notice.requested` and
  `notice.sent`, a release-requested row the owning bridge performs (the
  bridge's connection belongs to one process), and a sweep that reconciles
  dangling intents on restart.

**Absorbs.** The broker's idempotency key collapsing two identical sends in
one task (the key gains the effect id); the tech-stack doc still marking
the resident kernel "open".

### 2.2 Telegram, adapted from `main`

About 700 lines kept, 600 adapted, the rest of `bridge/` (about 23,500
lines) not carried.

- **Kept:** Telethon client setup, connect and flood backoff, session-lock
  cleanup, media download with retry and `bridge/media.py`'s download
  helpers, `fetch_reply_chain` and the media descriptors, oversized text as
  a file, `scripts/telegram_login.py`, `utils/peer.py`.
- **Adapted:** the head of `handler` builds the inbound record and ends at
  `intake.receive`, replacing four layers of Redis dedup;
  `_send_queued_message` becomes the performer, using raw send requests
  with a `random_id` derived from the broker key, and a `lookup` modelled
  on `_find_already_sent_poll`'s scan of the account's own messages; the
  Redis outbox loop becomes LISTEN on released effects plus unsent notices;
  operator notices go only to Tom's chat from settings, with no approval.
- **Not carried:** routing, the drafter, promise gate, catch-up and
  reconciler state, hibernation, `/update`, reactions, dead letters, polls.
- **Gap fill.** On reconnect, `history_fetch` backfills each chat from the
  highest message id in `message.received`, through the idempotent intake.
  No local cursor.

**Done.** On Valor's real account during a test window: an inbound message
starts a task with the $8 default budget; a question reaches Tom and his
reply binds to it; a delivery card's tap releases a held push; a forced
crash between intent and outcome does not send twice; the bridge's RSS
after a day connected is measured.

**Absorbs.** #3550 and #3095 (polls dropped, per Tom's ruling; the
telegram doc's poll sections removed); #2652 (the inbound record carries
the forum topic id); #3269 (gap fill on reconnect); #3589 (all sends pass
the broker); `flood_sleep_threshold` set to 0 so Telethon never retries on
its own; plain text sends, so what Tom approves is what renders.

### 2.3 Email, adapted from `main`

About 450 lines kept, 350 adapted, 1,700 not carried.

- **Kept:** header decoding, address parsing, body and attachment
  extraction and sanitizing.
- **Adapted:** `_poll_imap` searches `UNSEEN`, fetches `BODY.PEEK[]` with
  UID and UIDVALIDITY, records through `intake.receive`, then marks Seen;
  `parse_email_message` keeps empty-body mail and reads `References`,
  `Date`, and `Authentication-Results`; `_build_reply_mime` takes a
  Message-ID derived from the broker key and the whole references chain;
  `_send_smtp_sync` records refused recipients; `lookup` searches the Sent
  folder by Message-ID.
- **Not carried:** subject coalescing, the vault mirror, Redis history and
  dead letters, the email relay's retries, per-sender project routing.

**Done.** On Valor's real mailbox during a test window: a mail from Tom
passing DMARC starts a task; a reply-all is held and released once; a crash
between SMTP and outcome does not double-send; a 9 MB attachment sends.

**Absorbs.** #3601 (SMTP timeout scaled for large uploads; the other three
items go with the dropped code); #3124 and #2160 (moot: sends are held,
steering is in `core/`).

**Leaves out (milestone 2).** Standing grants for any chat (Tom's default:
tap each one); other channels.

## Milestone 3: the harnesses

Self-built. Stakes: critique 1, review 1. The Claude Code wrapper already
runs every turn; this milestone makes the port real by putting a second
harness and a second vendor behind it.

**Goal.** Orchestration that does not depend on one harness or one model
vendor, and a reviewer seat from another vendor (Mission item 1; Evidence
"Independent checks").

**Done.**
- A contract test suite for the harness port that Claude Code and Pi both
  pass: resume, signals, stop and reap, metering through the gateway.
- Pi running GPT-6.1 on OpenAI, metered by the gateway's OpenAI route.
- Pi carries popoto #633 end to end through the pipeline to a held merge.
- Pi with GPT-6.1 is a seat for the blind verifier; one review on each
  harness of the same candidate is recorded.
- The harness and its version recorded on `turn.started`.
- Compaction in place measured once on a long session.
- A headless browser in the workspace: a turn opens the app it built and
  records what it saw (the demonstration's recommendation; Mission item 1,
  "testing actual use").

**Absorbs.** Subagents receiving corrections, verified or recorded as a
gap; `turn()` without tools used only by smoke scripts.

**Leaves out.** Codex and further harnesses until a second need; per-harness
skill rendering.

## Milestone 4: persona and routines

Self-built.

### 4.1 The objective tree

Stakes: kernel, critique 2 and review 2.

**Goal.** Tom delegates a feature, then a workflow, then a product
(Mission item 4); routines draw from period budgets.

**Done.** A child's budget carved from its parent's remaining and a
child's ceiling never above its parent's, both shown by property tests;
stopping a parent refuses the calls and effects of a grandchild; a child's
report lands where the parent reads it.

### 4.2 The persona

Stakes: critique 1, review 1. Every persona change runs the emulator.

**Goal.** One identity in every turn, with the conduct the evidence asked
for (**One identity**; Mission items 2 and 3).

**Done.** `persona/` holds identity, voice, conduct, and delivery format;
the kernel renders it at the top of every turn, before the Brief and the
corrections, and the governance paragraph appears in it verbatim; the
`turn.started` digest covers it. The conduct says to ask before building
when a request leans on an example or names existing UI (demonstration
recommendation). An emulator run over the baseline items shows no item
worse than before the persona, and psyoptimal #894 reaches Tom's answers
with fewer feedback rounds than the demonstration's two.

### 4.3 Routines and the status page

Stakes: critique 1, review 1.

**Goal.** Scheduled work is a budgeted objective, never a bare script
(Mission item 5, **Bounded authority and spend**).

**Done.** `python -m core routine NAME` runs a routine from its
`routine.toml` under a 30-day period budget; launchd plists call only that;
a routine never makes Tom's task wait for the slot. The first two routines:
the emulator sweep (including the `routed` arm) and the ninety-day expiry
sweep, which opens one deletion branch held for Tom's tap. A read-only page
in `ui/` shows tasks, spend, pending approvals, the attention log, and
routine runs against their period budget, since status messages to Tom are
not sent.

**Leaves out.** Any routine without a demonstrated second need.

## Milestone 5: tools on demand

Self-built. Stakes set per tool.

Nothing is built here ahead of need. A tool is built when a task needs it
twice (Mission item 5), declares one effect class per operation, reaches
the world through the broker, and is deleted after ninety days unused. The
GitHub push performer is already built in 1.4. The OpenAI Decisions API
leg for images is built when the first judgement site needs images.

## Milestone 6: memory

Self-built. Stakes: stored data, critique 2 and review 2. Starts when
popoto ships its Postgres backend (popoto #631).

**Goal.** Valor carries what it learned about Tom's preferences across
tasks (Mission item 5; Evidence "Tom's feedback, both directions").

**Done.** `memory/` reads harness transcripts and the corrections and
exemplar streams through the port in `core/`, under a database role with no
privilege on the kernel's tables; it writes neither stream. An emulator
item with a recorded preference behaves differently with memory on than
off.

**Leaves out.** pgvector until a measured need.

## Before cutover

Cutover is Tom's to plan. These must be true first:

- Milestones 1 to 4 are done and their evidence holds on the merged branch.
- A real task, not a replay, has gone from a Telegram message to a merged
  change Tom used, with its attention logged.
- The emulator's last sweep shows no baseline item worse than its bare
  run.
- Nightly backups run and one restore has been rehearsed.
- RAM on the target Mac measured under a working turn, with the bridges
  connected.
- The old system's data the new one needs (projects, chat ownership,
  operator identity) is listed, with where each comes from.

## Decided by default

Tom can overturn any of these; each is reversible.

- The judgement port, the blind verifier, the emulator, and the GitHub push
  performer are built inside milestone 1, since the pipeline needs them
  before takeover.
- The objective tree is built at the start of milestone 4, where routines
  first need it.
- Stage instructions live as plain files in `skills/sdlc/` until the skill
  system is designed.
- The read-only status page in `ui/` comes with routines.
- Stakes per milestone as stated above.
- Polls are dropped from the Telegram bridge, per Tom's ruling on `main`
  (#3550).
- The inbound Telegram record carries the forum topic id.
- The old system's spend on the replayed PRs is not needed: every
  milestone's evidence compares against the bare baseline and the emulator,
  not against the old system's cost.

## Open items

- Which of Valor's Macs hosts the rebuild (needed before milestone 2).
- The provider hosting the open-weight judgement fallback (needed in 1.3).
- Tom's confirmation of the answer keys for cuttlefish #646, popoto #191,
  and popoto #188 (needed in 1.5).
- Skill system requirements (before `skills/` grows past `skills/sdlc/`).
