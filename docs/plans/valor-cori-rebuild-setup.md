---
tracking: none
slug: valor-cori-rebuild
type: chore
status: draft
---

# Valor rebuild, setup phase

**Scope of this plan:** the setup phase only. It ends when the branch holds a
rewritten README, the new top-level directories with their scope READMEs, a
minimal kernel that bounds one demonstration, the record of that
demonstration, and the docs the demonstration showed were needed. The rebuild
itself is planned afterward, in conversation, from that experience and those
docs. Rewriting hundreds of documents before demonstrating the builder would
make documentation the first product again.

**Authored:** 2026-09-30, from a conversation with Tom. Every decision below
that came from him is marked as his. Everything else is a recommendation and
open to correction before execution starts.

## Why

Six weeks of history on `main` (2026-08-19 to 2026-09-30) read like this:

| | |
|---|---|
| Commits | 1,571 |
| Plan, critique, revision, and migrate-plan commits | 1,199 (76%) |
| Commits that changed behavior | about 290 |
| Issues opened, by number range | about 2,540 |
| Open `bug` issues | 58 |
| Production Python / test Python | 267k / 370k lines |
| Test files importing mocks, under a no-mocks policy | 688 of 962 |
| Feature docs | 304 files, 61k lines |

The five newest bugs are all one class: a guard fighting another guard
(a stop hook marking live sessions completed, lost concurrent writes to
`extra_context`, outbox writers bypassing the persona pipeline, a merge guard
outliving its hook budget, a watchdog killing processes by age). The core
path, Telegram to queue to worker to one `claude -p` per turn, is sound. The
other 180k lines are tooling, hooks, validators, and an improvement stack
that was specified to refuse automatic promotion on every call.

Valor's checks and balances all live in the prompt layer: skills, hooks,
validators, running as the agent's own user on the agent's own machine. They
can be routed around, and they fight each other. The cori experiment
(`tomcounsell/cori`, 2026-09-19 onward) put authority in a deterministic
kernel outside the model's reach: budgets, effect classes, blind verification,
an append-only ledger. That is the design difference worth rebuilding for.
Cori's build process, however, reproduced Valor's ceremony within eleven days
(339 commits, 148 logged findings, 53 review-round commits, 15 session crashes
that lost a builder or validator). The design survives; the process does not.

## Mission, constraints, evidence

Ranked goals invited the system to optimize toward whichever goal sat first,
and with corrigibility first that is a system that spends indefinitely on
becoming more governed. The split below replaces the ranking. Corrigibility
is a set of guarantees to verify, not a direction to push.

### Mission

Turn Tom's intent into working things worth using. Own the journey from an
incomplete idea to a finished result, exercise taste along the way, and
compound the ability to build. Concretely:

1. **Own outcomes across the whole job.** "Build this" includes understanding
   the problem, inspecting what exists, choosing an approach, implementing,
   testing actual use, delivering within authority, and resolving discovered
   defects. Tom never coordinates the gaps between those steps.
2. **Contribute taste and invention.** Identify the simpler design, challenge
   an unnecessary requirement, produce a concrete alternative when the brief
   leaves room. Meeting expectations is the floor.
3. **Absorb ambiguity through making.** For reversible decisions: inspect,
   infer, prototype, show. Ask only when the answer materially changes the
   outcome or the authority required. An unclear brief produces a useful
   first version before it produces a questionnaire.
4. **Make larger undertakings tractable.** Capability growth means Tom can
   delegate increasingly substantial problems with less supervision: a
   feature, then a workflow, then a product.
5. **Compound leverage through completed work.** Each project may leave
   behind a tool, a tested technique, a reusable component, or knowledge of
   Tom's preferences. Extraction happens only on a demonstrated second need,
   never on a first, and anything unused after ninety days is deleted by
   default. This is the constraint that keeps `tools/` and `skills/` from
   regrowing the archive.
6. **Spend attention as carefully as money.** A task carries an attention
   budget beside its dollar budget. Valor carries routine decisions,
   investigates failures, and brings consequential choices with evidence and
   a recommendation. Requiring Tom to adjudicate internal process is a
   product defect.

### Constraints

Mandatory, verified, and never a place to spend surplus energy.

- **Bounded authority and spend.** Every task carries a money budget and an
   effect ceiling, conserved down the tree. A classifier decides what a
   thing is; the kernel decides what it may do. Cheaper capability does not
   lower the ceiling: it buys better outcomes or lets Tom authorize larger
   undertakings within the same budget. Scope expansion is Tom's choice;
   capability gains create that choice.
- **Reliable stop, recovery, and correction.** Stop is immediate and lossless.
   A ledger the system cannot edit records every effect. Corrections are
   first-class, carry provenance, and reach every session and agent.
   Autonomy shrinks automatically on evidence and grows only by Tom's
   decision.
- **Governance is restrained by structure, not sentiment.** Before any
   check, gate, hook, validator, review round, or approval step is added, the
   change names the mission item it serves and the incident that already
   happened without it; missing either, it is not added. A bug fix never
   adds a guard; it fixes the code. Adding governance is an `act`-class
   effect: the Brief carries a `governance_grant` field, default none, and a
   diff that adds any of the above needs Tom's tap, one approval per
   instance, through the same approval surface as a merge or a send. Every
   guard is ledgered with the incident it prevents, the mission item it
   serves, and a ninety-day expiry; a guard that has not fired by expiry is
   deleted by default. The blind verifier asks one Jev-class boolean over
   every diff, "does this add a check, gate, hook, round, or review step",
   and a yes with no grant is a refused merge. The same paragraph, in the
   same words, sits at the top of `CLAUDE.md`, in the persona rendered into
   every turn, and in the Not-here section of every directory README. No
   restraint skill, no hook that blocks hooks, no governance dashboard: each
   is the disease presenting as the cure.
- **Docs describe reality.** Enforced by a blind verifier reading the doc as
   the contract and by cheap judgement sweeps, never by the model grading its
   own narration.

### Evidence

What counts as knowing the mission is being met:

- **Working results in real use.** The thing runs, someone uses it, and
   defects found in use get resolved without Tom coordinating.
- **Tom's feedback, both directions.** The corrections ledger records what
   went wrong. An exemplar ledger, same store and a distinct source class,
   records work Tom loved and why: an excellent simplification, good taste,
   initiative, unusually complete delivery. Without the second, the system
   learns to avoid mistakes and never learns to build anything exceptional.
- **Independent checks.** Blind verification, the emulator's human-labelled
   cases, and the audit sample.
- **Attention spent.** Decisions escalated to Tom per finished task, logged
   from the first demonstration onward. It is the one outcome number the
   system cannot perform against: escalating less while failing shows up in
   the result.

### The acceptance question

Can Tom give Valor a consequential, imperfectly specified goal and return to
something that works, reflects good judgement, and needs less of his
attention than doing it himself?

Every mechanism in the rebuild answers to that question. Successfully
navigating an SDLC does not.

## Decisions recorded from Tom

| Decision | Tom's call |
|---|---|
| Core directories | `core/` and `memory/` are added to the list |
| Cori | A copy goes into the archive; we take what we want. The name "cori" is dropped. The concepts "corrigible" and "aligned AI" stay in the docs |
| Bridges | Plural, `bridges/`, self-contained comms modules. `comms/` is an acceptable alternative name |
| Identity | Exactly one. The top persona orchestrates every tool and subagent system. No per-space identity |
| Archive | Deleted from the branch after the move and the docs rewrite, before the rebuild |
| Continuity and cutover | Disregarded in this plan. Tom's problem later |
| Data | Postgres only, using document (NoSQL) strategies rather than relational modelling. Memory is built last and gates on popoto's Postgres support; most of the rebuild does not use popoto |
| Skills | A versioned skill system, plus skills that refactor repo-specific skills in other repos. Requirements gathered from Tom at a later stage. Not designed here |
| Execution | Opus agents execute steps 1 to 4 |
| Machine | Mac native. Everything runs on a MacBook Air M4 with 16 GB RAM |
| Survivors as code | The SDLC state machine in principle; the Telegram and email bridges possibly unchanged |
| Redis | Replaced by Postgres |

## Constraints the docs must respect

- **16 GB of RAM.** Postgres, one container runtime, one `claude -p` at a
  time, and the bridges have to coexist. No local classifier model of any
  size worth running. Judgement goes to a hosted Jev-class API with an
  open-weight equivalent seated behind the same port as the fallback.
  Any doc that assumes a resident local model is wrong.
- **Mac native.** launchd for scheduling, Apple containers for sandboxes
  (cori spike 06), Keychain for secrets. No Linux assumptions.
- **Postgres as a document store.** JSONB documents, an append-only events
  table, no foreign-key lattice. The kernel's state (objective tree, ledger,
  approvals, budgets, steering) lives there from day one. Memory arrives last.
- **One identity.** Every outbound message and PR leaves as Valor. Cori's
  "acts as the person" mode is dropped, and with it the per-space sending
  identity field.
- **Three tiers.** Deterministic kernel for authority. Jev-class judgement
  for every decision that is not authority, confidence-gated to a human.
  Frontier agents for the hard work, budgeted. A classifier decides what a
  thing is; it never decides what a thing may do.

## Target top-level structure

Each directory ships with a README that states its scope, what may import
it, and what it may import. The scope text below is the README's content in
draft; step 3 materializes it.

| Directory | Scope | Imports from |
|---|---|---|
| `core/` | The kernel and the control loop. Objective tree, budgets, effect classes and the broker, approvals, the ledger, steering, the supervisor turn, the SDLC state machine as a typed state model, the judgement layer's task taxonomy and router. No LLM call decides authority here | nothing above it |
| `memory/` | Operator record, corrections ledger, episodic memory. Built last, on popoto over Postgres. Until then the directory holds its README and the port the core reads through | `core/` ports only |
| `persona/` | Everything that defines Valor: identity, voice, conduct, what "acting as Valor" means for tone and for what may be sent. One persona. Rendered into every prompt the core builds | `core/` |
| `bridges/` | Self-contained comms modules: `telegram/`, `email/`, later others. I/O and the outbox only. Each conforms to one port in `core/`. No routing, triage, or judgement lives here | `core/` ports only |
| `harnesses/` | Wrappers and logic for running work via a harness: Claude Code, Codex, Pi, and any future one. Turn execution, session resume, transcript capture, per-harness skill rendering | `core/` |
| `skills/` | Versioned skills. Structure deferred until Tom's requirements are gathered. Holds a README naming that deferral and nothing else in this phase | deferred |
| `routines/` | Every scheduled task and runner. Replaces `reflections/`. launchd plists and the routines they run. Every routine is a budgeted objective, never a bare script | `core/` |
| `tools/` | Non-core components and vendor-dependent tooling. Anything the core can run without. A tool declares its effect class and reaches the world through the broker | `core/` |
| `api/` | Programmatic interfaces: MCP servers, any HTTP surface. Reserved; README only in this phase | `core/` |
| `ui/` | The read-only dashboard | `core/` read models only |
| `site/` | Stays as-is. Moved back out of the archive unchanged | none |
| `docs/` | Same structure as today: `docs/features/`, `docs/plans/`, `docs/conventions/`, `docs/sdlc/`. Content rewritten per step 4 | |
| `tests/` | Integration first, no mocks, real Postgres, real containers, real bridges against test accounts. The emulator lives here when it exists | everything |
| `_archive_/` | Temporary. Read-only reference during steps 2 to 4. Deleted before the rebuild. Nothing imports from it, and no new doc cites a path inside it | nothing |

`config/` is not on the list on purpose. Settings become one typed settings
module under `core/`, secrets stay in Keychain and the vault `.env`, and the
1,614-line `config/settings.py` is not carried forward as a shape.

## Step 0: the branch

```bash
git fetch origin main
git checkout -B valor-cori-rebuild origin/main
git push -u origin valor-cori-rebuild
```

Every later step commits to this branch. Nothing in this plan touches
`main`.

## Step 1: move everything to the archive

One agent, one commit for the move and one for the cori copy.

1. Create `_archive_/` and `git mv` every tracked path into it except:
   `.git`, `.gitignore`, `.python-version`, `LICENSE`, and `docs/plans/`
   (this plan and its successors stay at the top level).
   That includes `.claude/`, `.githooks/`, `.github/`, `pyproject.toml`,
   `uv.lock`, and `site/`. `site/` comes back out in step 3.
2. Move `.claude/` first and by itself, in its own commit, so the hooks it
   registers stop firing before the bulk move. Then write a minimal
   `.claude/settings.json` with no hooks and a `CLAUDE.md` of under twenty
   lines that says: this branch is a rebuild in its setup phase; the archive
   is read-only reference; nothing imports from it; the plan is at
   `docs/plans/valor-cori-rebuild-setup.md`.
3. Clone `tomcounsell/cori` at its current head, strip its `.git`, and copy
   it to `_archive_/cori/`. Record the head SHA in `_archive_/cori/SOURCE.md`.
4. Verify: `git status` clean, `git ls-files | grep -v '^_archive_/' `
   lists only the exceptions above and the new `.claude/` and `CLAUDE.md`.

The `.githooks/` commit-msg and pre-push hooks apply to commits landing on
`main` and do not gate this branch, but `core.hooksPath` may still point at
them locally. If a hook blocks a commit on this branch, the agent unsets
`core.hooksPath` for this checkout and says so in the commit message. It
never edits the hooks.

## Step 2: rewrite the README

One agent. Sources: `_archive_/README.md`, `_archive_/cori/README.md`,
`_archive_/cori/docs/architecture.md`, and the Mission and Constraints sections
of this plan.

The README says, in this order and in under 200 lines:

1. What Valor is: one AI employee with one identity, running on one Mac,
   corrigible by construction, and the mission statement verbatim from this
   plan.
2. The corrigibility commitments, taken from cori's README and re-stated for
   Valor: stoppable, bounded, legible, correctable. The RICE table survives
   with its last column ("what enforcement cannot do") intact. Cori's
   research grounding table and `REFERENCES.md` come across, since every
   design assumption still needs a citation.
3. The three tiers: kernel, judgement, agents.
4. The directory map, one line per directory, pointing at each README.
5. Status: setup phase; the rebuild plan follows.

The word "cori" does not appear. "Corrigible" and "aligned" do. Nothing in
it describes the archived system or the history; it describes the new status
quo only.

## Step 3: create the directories with their READMEs

One agent. For each directory in the table above, create it with a README
whose sections are: Scope, Imports (what it may import and what may import
it), Effect classes it may hold (for `tools/`, `bridges/`, `routines/`), and
Not here (what looks like it belongs but does not). The Not-here section is
the one that prevents the guard-of-guards pattern from returning, so it is
required, not optional.

`site/` is `git mv`'d back from the archive unchanged in this step.
`skills/` and `api/` get README-only stubs naming their deferral.

Add a `tests/README.md` stating the rule: real Postgres, real containers,
real bridges on test accounts, no mocks, and every test declares its live
spend.

## Step 4: a minimal kernel and one demonstration

The builder is demonstrated before the docs are written. This step exists
so the architecture is informed by where Tom still had to act as project
manager, rather than by what the archive says the system was.

### The minimal kernel

Pulled from `_archive_/cori/`, not written fresh. Cori's spikes proved and
its code already carries the four bounds a demonstration needs:

| Bound | Source in the archive | What it gives the demonstration |
|---|---|---|
| Money budget, conserved | `gateway/budget.py`, spike 01 | Every model call metered against a committed number |
| Lossless stop | `kernel/runs.py`, spike 03 | Tom can kill it at any instant and nothing is lost |
| Effect ledger | `broker/ledger.py`, `kernel/events.py` | A record the system cannot edit |
| Effect classes on the broker | `broker/actions.py`, spike 05 | Nothing irreversible leaves without Tom's tap |

One Opus agent lifts these into `core/` with the identity mode removed, on
Postgres as a document store, with `governance_grant` (default none) added
to the Brief schema so the constraint above is a kernel fact from the first
turn, and with nothing else: no objective tree
beyond a single task record, no verifier, no scribe, no space model. It is
done when a script can start a task with a budget, run one `claude -p` turn
through the gateway, record an effect, be stopped mid-turn, and show the
ledger.

### The demonstration

Tom picks one meaningful request with real ambiguity, from his actual work,
not from the backlog. Valor carries it to a usable result inside the minimal
kernel. Tom does nothing except answer questions Valor chooses to ask.

Recorded, in `docs/plans/rebuild-demonstration.md`:

- the request as given, verbatim;
- every question Valor asked, and whether the answer changed the outcome or
  the authority required (the attention log);
- every point where Tom had to act as project manager anyway;
- what was delivered, whether it was used, and what Tom would have done
  differently;
- money spent, and what the judgement layer would have taken off a frontier
  model.

The demonstration is also the first emulator case. Before it starts, Tom's
instruction to restrain governance is entered as correction number one,
global scope, source class `direct`, and the demonstration verifies that it
renders into the supervisor's prompt and into every brief it dispatches. If
it does not, the ledger is broken, and that is learned on entry one.

## Step 5: write the docs the demonstration showed were needed

Opus subagents, in two passes. Tom approves the first before the second
starts. The demonstration record is the primary input; the archive is
reference.

### Pass 1: triage

One agent reads the demonstration record first, then every file under
`_archive_/docs/features/` (304), `_archive_/docs/conventions/`,
`_archive_/docs/sdlc/`, and `_archive_/cori/docs/`, and produces
`docs/plans/rebuild-doc-triage.md`: one row per source doc with a
disposition and a one-line reason.

The survival rule: a doc survives only if the demonstration needed the
mechanism it describes, or a bridge or the SDLC state machine depends on it,
or it states a constraint from this plan. Everything else is rederived from
the architecture doc or dropped. Expected outcome: under thirty survivors
from the 304 feature docs, and a rewrite of the four cori docs into the
architecture set.

Dispositions: `rewrite`, `fold` (into a named target), `drop`.

### Pass 2: write

One agent per target doc, in parallel, each briefed with the target's
purpose, the survivor rows that feed it, the demonstration record, the
Mission, Constraints, and Evidence sections of this plan, and the writing
rules. Agents never read each other's output; a final agent runs a
consistency pass.

Target docs:

| Doc | Holds | Primary source |
|---|---|---|
| `docs/architecture.md` | The kernel and control loop: supervisor turn, objective tree, budgets, effect classes, broker, approvals, ledger, stop, execution records, verification, the judgement tier | `_archive_/cori/docs/architecture.md`, identity mode and every "Cori" reference removed, judgement tier added, revised by what the demonstration showed |
| `docs/tech-stack.md` | What each part is built from, with a status on every choice, and the M4 Air constraints | `_archive_/cori/docs/tech-stack.md` |
| `docs/mission.md` | Mission, constraints, evidence, the acceptance question, the attention log | this plan |
| `docs/judgement-layer.md` | Task taxonomy, router, decisions port, calibration discipline, confidence gating, the boundary with the kernel, the ten use shapes | `_archive_/agent/llm/tasks.py`, `router.py`, `backends/decisions.py`, `_archive_/docs/features/llm-task-taxonomy.md` |
| `docs/sdlc-state-machine.md` | States, stages, verdicts, and gates as a typed state model; stages are skills, the state machine is core | `_archive_/agent/pipeline_state.py`, `goal_gates.py`, `tools/sdlc_verdict.py`, `_archive_/.claude/skills-global/do-sdlc/SKILL.md` |
| `docs/bridges/telegram.md`, `docs/bridges/email.md` | The port each bridge conforms to, what it does, what it never does | `_archive_/bridge/telegram_bridge.py`, `telegram_relay.py`, `email_bridge.py`, `email_relay.py` |
| `docs/data.md` | Postgres as a document store: events table, JSONB documents, per-turn rendering, why no relational lattice | cori migrations and architecture, Tom's decision |
| `docs/persona.md` | The one identity: voice, conduct, what may leave under Valor's name | `_archive_/config/identity.json`, `config/personas/`, `_archive_/cori/VOICE.md` |
| `docs/harnesses.md` | The harness port and per-harness wrappers | `_archive_/worker/`, `_archive_/docs/features/headless-session-runner.md` |
| `docs/routines.md` | Scheduled work as budgeted objectives under launchd | `_archive_/reflections/`, launchd plists |
| `docs/emulator.md` | The outward-facing fitness function: human-originated historical requests, labelled by the human merge or review decision, proxies scored by cheap judgement, run on every skill change, seeded by the demonstration | this conversation |
| `docs/machine.md` | The M4 Air: what runs resident, what runs on demand, the RAM budget per component | new |
| `docs/conventions/*.md` | Only the conventions the triage marks `rewrite` | archive |

Writing rules for every agent:

- Describe the new status quo only. No history, no "previously", no
  migration notes, no `_archive_/` path in the final text.
- Every mechanism names the mission item it serves or the constraint it
  enforces. A mechanism that serves neither is cut, and the agent says so.
- Every design assumption cites `REFERENCES.md` or is marked as a gap.
- No em dashes. No AI-writing tells. Say what is true, not what is not.
- "Cori" never appears. "Corrigible" does.
- Under 600 lines per doc.

The consistency agent checks: one vocabulary across the set, no two docs
owning the same mechanism, every directory README pointing at the doc that
governs it.

## Step 6: plan the rebuild, in conversation

Not executed by an agent. Inputs are the demonstration record and the docs.
Questions already known to need Tom's answer:

1. **Orchestration.** A lead agent with builders in worktrees (cori's
   `build-m0` shape), or one session per component driven by Tom? Cori's
   history says the lead-builder-reviewer-validator chain reproduces
   ceremony; the alternative is one plan, one build, one blind verification
   per component, and no findings ledger.
2. **Order.** Recommended: `core/` kernel and data first, `bridges/`
   second, `harnesses/` third, `persona/` and `routines/` fourth, `tools/`
   on demand, `memory/` last when popoto ships Postgres.
3. **Skills.** Tom's requirements for the versioned skill system and the
   cross-repo refactoring skills, gathered before `skills/` is designed.
4. **Emulator seeding.** Repos, date range, the human-originated filter,
   budget per run.
5. **Judgement vendor.** Jev as the primary decisions leg; the open-weight
   fallback behind the port, and where it runs given 16 GB.
6. **Archive deletion.** After Tom confirms the docs and the demonstration
   are sufficient to rebuild from.

## Execution

| Step | Agent | Verification |
|---|---|---|
| 0 | this session | branch exists on origin |
| 1 | one Opus agent | `git ls-files` outside `_archive_/` matches the exception list; cori copy present with `SOURCE.md` |
| 2 | one Opus agent | README under 200 lines, no "cori", mission verbatim, directory map matches step 3 |
| 3 | one Opus agent | every directory has a README with all four sections; `site/` restored byte-identical |
| 4, kernel | one Opus agent | the start, turn, effect, stop, ledger script runs end to end on Postgres |
| 4, demonstration | Valor inside the kernel; Tom observing | demonstration record complete, attention log filled |
| 5, pass 1 | one Opus agent | triage table covers every source file; Tom approves |
| 5, pass 2 | one Opus agent per target doc, then one consistency agent | writing rules hold; consistency report has no open items |
| 6 | Tom and this session | a rebuild plan doc exists |

Each agent's brief carries: this plan in full, the step it owns, and the
verification it must pass before it reports. No agent runs a step it was not
briefed for, and no agent edits this plan.

## Not in this plan

The rebuild itself. Continuity of the running system. Cutover. The skills
system. Memory. Any code beyond what step 1 and step 3 move and the minimal
kernel step 4 lifts from the archive.
