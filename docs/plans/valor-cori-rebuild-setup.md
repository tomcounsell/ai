---
tracking: none
slug: valor-cori-rebuild
type: chore
status: draft
---

# Valor rebuild, setup phase

**Scope of this plan:** the setup phase only. It ends when the branch holds a
rewritten README, the new top-level directories with their scope READMEs, the
rewritten docs, and nothing else. The rebuild itself is planned afterward, in
conversation, from those docs.

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

## Goals, as aligned

Ranked. Everything in the rebuild traces to one of these or is not built.

1. **Corrigible.** Stoppable at any instant with nothing lost, bounded in
   spend and effect, legible after the fact, and correctable by a ledger every
   session reads. It stops itself when stopping is needed. Autonomy shrinks
   automatically on evidence and grows only by Tom's decision.
2. **Work arrives and meets expectations.** Judged by Tom, sparsely:
   corrections, a thumbs up, an anecdote. Never a count the system can
   perform against. The corrections ledger is the primary quality signal.
3. **Capability grows without slow degradation.** Guarded structurally, not
   measured, because the tail is hundreds of PRs long. Ceremony stays out of
   the product.
4. **Docs match reality.** Enforced by a blind verifier reading the doc as the
   contract, and by cheap judgement sweeps over every doc, never by the model
   grading its own narration.
5. **Cost bounded, ceiling moving.** Every task carries a money budget.
   Cheaper capability (Jev-class judgement, contributor-tier models) lowers
   the ceiling rather than expanding scope.

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
  Frontier agents for the hard work, scarce and budgeted. A classifier
  decides what a thing is; it never decides what a thing may do.

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
| `cron/` | Every scheduled task and runner. Replaces `reflections/`. launchd plists and the jobs they run. Every job is a budgeted objective, never a bare script | `core/` |
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
`_archive_/cori/docs/architecture.md`, and the Goals and Constraints sections
of this plan.

The README says, in this order and in under 200 lines:

1. What Valor is: one AI employee with one identity, running on one Mac,
   corrigible by construction.
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
it), Effect classes it may hold (for `tools/`, `bridges/`, `cron/`), and
Not here (what looks like it belongs but does not). The Not-here section is
the one that prevents the guard-of-guards pattern from returning, so it is
required, not optional.

`site/` is `git mv`'d back from the archive unchanged in this step.
`skills/` and `api/` get README-only stubs naming their deferral.

Add a `tests/README.md` stating the rule: real Postgres, real containers,
real bridges on test accounts, no mocks, and every test declares its live
spend.

## Step 4: rewrite the docs

Opus subagents, in two passes. Tom approves the output of the first pass
before the second starts.

### Pass 1: triage

One agent reads every file under `_archive_/docs/features/` (304),
`_archive_/docs/conventions/`, `_archive_/docs/sdlc/`, and
`_archive_/cori/docs/`, and produces `docs/plans/rebuild-doc-triage.md`: one
row per source doc with a disposition and a one-line reason.

The survival rule: a doc survives only if a bridge, the SDLC state machine,
or one of the five goals depends on the mechanism it describes. Everything
else is rederived from the architecture doc or dropped. The expected outcome
is under thirty survivors from the 304 feature docs, and a rewrite of the
four cori docs into the architecture set.

Dispositions: `rewrite` (mechanism survives, doc is rewritten against the new
structure), `fold` (content merges into a named target doc), `drop`.

### Pass 2: write

One agent per target doc, in parallel, each briefed with: the target doc's
purpose from the list below, the survivor rows that feed it, the Goals and
Constraints of this plan, and the writing rules. Agents never read each
other's output; a final agent runs a consistency pass across the set.

Target docs:

| Doc | Holds | Primary source |
|---|---|---|
| `docs/architecture.md` | The kernel and control loop: supervisor turn, objective tree, budgets, effect classes, broker, approvals, ledger, stop, the three execution records, verification, the judgement tier | `_archive_/cori/docs/architecture.md`, with the identity mode and every "Cori" reference removed and the judgement tier added |
| `docs/tech-stack.md` | What each part is built from, with a status on every choice, and the M4 Air constraints | `_archive_/cori/docs/tech-stack.md` |
| `docs/goals.md` | The five goals, ranked, and how each is guarded or measured | this plan |
| `docs/judgement-layer.md` | The task taxonomy, router, decisions port, calibration discipline (error-cost tiers and reference-arm agreement), confidence gating to the human, the boundary with the kernel, the ten use shapes mapped to where they land | `_archive_/agent/llm/tasks.py`, `router.py`, `backends/decisions.py`, `_archive_/docs/features/llm-task-taxonomy.md` |
| `docs/sdlc-state-machine.md` | The states, stages, verdicts, and gates as a typed state model, and the rule that stages are skills and the state machine is core | `_archive_/agent/pipeline_state.py`, `goal_gates.py`, `tools/sdlc_verdict.py`, `_archive_/.claude/skills-global/do-sdlc/SKILL.md` |
| `docs/bridges/telegram.md`, `docs/bridges/email.md` | The port each bridge conforms to, what it does, what it never does | `_archive_/bridge/telegram_bridge.py`, `telegram_relay.py`, `email_bridge.py`, `email_relay.py`, and their feature docs |
| `docs/data.md` | Postgres as a document store: the events table, JSONB documents, how state is rendered per turn, why no relational lattice | cori migrations and architecture, plus Tom's decision |
| `docs/persona.md` | The one identity: voice, conduct, what may leave under Valor's name | `_archive_/config/identity.json`, `config/personas/`, `_archive_/cori/VOICE.md` |
| `docs/harnesses.md` | The harness port and the per-harness wrappers | `_archive_/worker/`, `_archive_/docs/features/headless-session-runner.md` |
| `docs/cron.md` | Scheduled work as budgeted objectives under launchd | `_archive_/reflections/`, launchd plists |
| `docs/emulator.md` | The outward-facing fitness function: historical human-originated requests from repos Valor contributes to, labelled by the human merge or review decision, proxies scored by cheap judgement, run on every skill change | this conversation; no archived source |
| `docs/machine.md` | The M4 Air: what runs resident, what runs on demand, the RAM budget per component | new |
| `docs/conventions/*.md` | Only the conventions the triage marks `rewrite` | archive |

Writing rules for every agent:

- Describe the new status quo only. No history, no "previously", no
  migration notes, no references to any `_archive_/` path in the final text.
- Every mechanism names the goal it serves. A mechanism that serves none is
  cut from the doc, and the agent says so in its report.
- Every design assumption cites `REFERENCES.md` or is marked as a gap.
- No em dashes. No AI-writing tells. Say what is true, not what is not.
- "Cori" never appears. "Corrigible" does.
- Under 600 lines per doc. The archive's 700-line feature docs are the
  pattern being left behind.

The consistency agent checks: one vocabulary across the set (objective,
task, turn, effect class, budget, ledger, space or whatever replaces it),
no two docs owning the same mechanism, every directory README pointing at
the doc that governs it.

## Step 5: plan the rebuild, in conversation

Not executed by an agent. Inputs are the docs from step 4. The questions
already known to need Tom's answer:

1. **Orchestration.** Who runs the rebuild: a lead agent on this machine
   with builders in worktrees (cori's `build-m0` shape), or one session per
   component driven by Tom? Cori's history says the lead-builder-reviewer-
   validator chain reproduces ceremony; the alternative is one plan, one
   build, one blind verification per component, and no findings ledger.
2. **Order.** Recommended: `core/` kernel and data first, `bridges/` second
   (re-seated on the port, ideally unchanged), `harnesses/` third,
   `persona/` and `cron/` fourth, `tools/` on demand, `memory/` last when
   popoto ships Postgres.
3. **Skills.** Tom's requirements for the versioned skill system and the
   cross-repo refactoring skills. Gathered before `skills/` is designed.
4. **Emulator seeding.** Which repos, which date range, how the
   human-originated filter is applied, and the budget per run.
5. **Judgement vendor.** Jev as the primary decisions leg; which open-weight
   equivalent sits behind the port as the fallback, and where it runs given
   16 GB.
6. **Archive deletion.** The commit that removes `_archive_/`, after Tom
   confirms the docs are sufficient to rebuild from.

## Execution

| Step | Agent | Verification |
|---|---|---|
| 0 | this session | branch exists on origin |
| 1 | one Opus agent | `git ls-files` outside `_archive_/` matches the exception list; cori copy present with `SOURCE.md` |
| 2 | one Opus agent | README under 200 lines, no "cori", directory map matches step 3 |
| 3 | one Opus agent | every directory has a README with all four sections; `site/` restored byte-identical |
| 4, pass 1 | one Opus agent | triage table covers every source file; Tom approves |
| 4, pass 2 | one Opus agent per target doc, then one consistency agent | writing rules hold; consistency report has no open items |
| 5 | Tom and this session | a rebuild plan doc exists |

Each agent's brief carries: this plan in full, the step it owns, and the
verification it must pass before it reports. No agent runs a step it was not
briefed for, and no agent edits this plan.

## Not in this plan

The rebuild itself. Continuity of the running system. Cutover. The skills
system. Memory. Any code beyond what step 1 and step 3 move.
