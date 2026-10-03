---
tracking: none
slug: rebuild-doc-triage
type: record
status: draft
---

# Rebuild doc triage

Step 5, pass 1 of the setup phase plan in `docs/plans/`. One row per archived source file,
with a disposition, a target, and a reason. Pass 2 does not start until Tom
approves this table.

## Inputs

Read in this order:

1. The setup phase plan, in full.
2. The demonstration record, `docs/plans/rebuild-demonstration.md`. It is the
   primary input: the rows below are judged against what that run needed.
3. The top-level `README.md`, every directory README, and the entry points in
   `core/README.md`, so the triage knows what already exists.
4. Every file under `_archive_/docs/features/`, `_archive_/docs/conventions/`,
   `_archive_/docs/sdlc/`, and `_archive_/cori/docs/`, subdirectories and
   non-markdown assets included.

## The survival rule, verbatim from the plan

> The survival rule: a doc survives only if the demonstration needed the
> mechanism it describes, or a bridge or the SDLC state machine depends on it,
> or it states a constraint from this plan. Everything else is rederived from
> the architecture doc or dropped.

Reasons cite the clause they rely on: **(a)** the demonstration needed it,
**(b)** a bridge or the SDLC state machine depends on it, **(c)** it states a
constraint from the plan.

## Dispositions

- `rewrite`: the source becomes the target doc nearly whole, revised to the
  new status quo.
- `fold`: one mechanism from the source goes into the named target; the rest
  of the source is left behind.
- `drop`: nothing carries forward. Where a dropped mechanism is still needed
  in some form, the reason names the target that rederives it.

A row is a survivor if it is `rewrite` or `fold`.

## Coverage

`find _archive_/docs/features _archive_/docs/conventions _archive_/docs/sdlc _archive_/cori/docs -type f | wc -l`
returns **341**. This file has **341** rows, one per file, and every path in
the `find` output appears exactly once (checked by script before commit).
The feature directory holds 306 files where the plan says 304: 302 markdown
files at the top level (the index `README.md` among them), three under
`archived/`, and one image under `assets/`. Each has its own row.

The plan expects the four design docs of the archived kernel source to be
rewritten into the architecture set. This triage reads those four as the
design documents at the top of `_archive_/cori/docs/`: `architecture.md`
and `tech-stack.md` (rewrite), `prereqs.md` (fold into `docs/machine.md`),
and the seams contract `plans/00-seams.md` (fold into `docs/data.md`). The
component plans and review records under it are triaged
row by row like everything else.

## Counts

Per source directory and disposition:

| Source | Files | rewrite | fold | drop | Survivors |
|---|---|---|---|---|---|
| `_archive_/docs/features/` | 306 | 1 | 26 | 279 | 27 |
| `_archive_/docs/conventions/` | 1 | 0 | 0 | 1 | 0 |
| `_archive_/docs/sdlc/` | 11 | 0 | 2 | 9 | 2 |
| `_archive_/cori/docs/` | 23 | 2 | 13 | 8 | 15 |
| **Total** | **341** | **3** | **41** | **297** | **44** |

Per target, survivors from all sources; the last column counts those from the feature docs:

| Target | rewrite | fold | Total | From features |
|---|---|---|---|---|
| `docs/architecture.md` | 1 | 15 | 16 | 7 |
| `docs/tech-stack.md` | 1 | 1 | 2 | 0 |
| `docs/data.md` | 0 | 2 | 2 | 0 |
| `docs/judgement-layer.md` | 0 | 2 | 2 | 2 |
| `docs/sdlc-state-machine.md` | 1 | 6 | 7 | 5 |
| `docs/bridges/telegram.md` | 0 | 7 | 7 | 7 |
| `docs/bridges/email.md` | 0 | 1 | 1 | 1 |
| `docs/persona.md` | 0 | 1 | 1 | 1 |
| `docs/harnesses.md` | 0 | 5 | 5 | 4 |
| `docs/machine.md` | 0 | 1 | 1 | 0 |
| `docs/mission.md` | 0 | 0 | 0 | 0 |
| `docs/routines.md` | 0 | 0 | 0 | 0 |
| `docs/emulator.md` | 0 | 0 | 0 | 0 |

`docs/mission.md` draws on the plan alone, `docs/routines.md` on the archived code (no doc survived), and `docs/emulator.md` and the uncovered parts of `docs/machine.md` are written fresh (see Gaps). No convention is marked `rewrite`, so pass 2 writes no `docs/conventions/` doc.

## Survivors from the feature docs

27 of 306, grouped by target. Full reasons are in the table below.

**`docs/architecture.md`**

- `docs/features/agent-session-fenced-execution-record.md` (fold): per-turn execution record (pid, create_time fence, cwd, harness); resume is scoped to the cwd
- `docs/features/bridge-worker-architecture.md` (fold): bridge is I/O only; one execution engine; transport-keyed output handlers (telegram/email)
- `docs/features/eng-session-architecture.md` (fold): steering a running session; pause_open_question stops the nudge loop while it waits for Tom's answer
- `docs/features/review-workflow-screenshots.md` (fold): run the app and screenshot it in a browser to verify work
- `docs/features/session-isolation.md` (fold): per-task isolated workspace (worktree) and task list
- `docs/features/session-lifecycle.md` (fold): session state machine, Room/Job/session durability split, terminal states
- `docs/features/session-steering.md` (fold): turn-boundary steering inbox drained between claude -p turns; parent-to-child steer and abort

**`docs/judgement-layer.md`**

- `docs/features/llm-task-taxonomy.md` (fold): per-site LLMTask declaration (kind, backend, error-cost tier) read by a router
- `docs/features/pm-routing-collaboration.md` (fold): four-way intent classification (sdlc/collaboration/other/question)

**`docs/sdlc-state-machine.md`**

- `docs/features/goal-gates.md` (fold): deterministic per-stage gate checks (plan file, PR, review exists)
- `docs/features/pipeline-graph.md` (fold): (stage, outcome) to next-stage edge table with cycles
- `docs/features/pipeline-state-machine.md` (rewrite): programmatic stage state recorded at transition points, not inferred
- `docs/features/sdlc-issue-keyed-stage-ledger.md` (fold): stage/verdict state keyed by (repo, issue), not by the ephemeral session
- `docs/features/sdlc-pipeline.md` (fold): stage sequence and router dispatch over stage state

**`docs/bridges/telegram.md`**

- `docs/features/message-drafter.md` (fold): medium-aware wire-format validation and long-output handling for Telegram and email
- `docs/features/mid-session-steering.md` (fold): Telegram reply-to message injected as steering at the next turn boundary
- `docs/features/reaction-semantics.md` (fold): emoji reaction protocol for message lifecycle (received/working/done/error)
- `docs/features/reply-thread-context-hydration.md` (fold): reply-to carries thread context and resumes or seeds a session
- `docs/features/session-management.md` (fold): reply-chain walk to the root human message resolves one canonical session per thread
- `docs/features/telegram-message-edit-handling.md` (fold): MessageEdited routes to steering a running task or a fresh task for finished work
- `docs/features/telegram-poll-questions.md` (fold): blocked judgment call rendered as native group poll; 8-byte option limit; answer correlation

**`docs/bridges/email.md`**

- `docs/features/email-bridge.md` (fold): IMAP poll, thread continuation, SMTP reply-all with In-Reply-To, transport-keyed output

**`docs/persona.md`**

- `docs/features/personas.md` (fold): identity.json plus composable persona segments and overlays

**`docs/harnesses.md`**

- `docs/features/harness-adapter.md` (fold): TurnRequest/TurnResult/TurnEvent seam owning claude -p argv, env, stream-json parsing
- `docs/features/harness-session-continuity.md` (fold): first-turn full context, persisted session uuid, later turns via --resume
- `docs/features/headless-session-runner.md` (fold): one claude -p per turn, turn-end from stream-json result, steer at turn boundary
- `docs/features/session-transcripts.md` (fold): append-only per-session transcript files of every turn, tool call, and result


## Rows: `_archive_/cori/docs/`

| Path | Disposition | Target | Mechanism | Reason |
|---|---|---|---|---|
| `cori/docs/architecture.md` | rewrite | `docs/architecture.md` | supervisor turn, objective tree, Brief and Report, metered spending and effect classes, verification, approvals, failure points | Plan names it the primary source of the architecture doc; demonstration ran on its kernel (metering, stop, ledger, broker). Remove identity mode, spaces, Scribe, operator record; add the judgement tier |
| `cori/docs/plans/00-seams.md` | fold | `docs/data.md` | schemas, ports, event types, the three execution records (gateway log, tool log, effect ledger) | Record and event shapes the kernel stores; data.md needs the event types and execution records. Space, belief, and memory schemas drop |
| `cori/docs/plans/01-events.md` | fold | `docs/data.md` | append-only event table with type and schema_version, upcasting readers, advisory locks | The ledger the demonstration relied on is this table; states the plan constraint "a ledger the system cannot edit" |
| `cori/docs/plans/02-tree.md` | fold | `docs/architecture.md` | objective nodes and states, effect ceiling conserved down the tree, attenuation, generation fencing | Plan constraint: effect ceiling conserved down the tree (the money half was superseded 2026-10-03: metered spending only; nothing refuses on money). The demonstration ran a single task record; the tree is the next step |
| `cori/docs/plans/03-spaces.md` | drop | - | space manifests, row-level security by read token, inbound routing, unassigned space | One identity and no space model in the plan. Workspace scoping the demonstration needed is listed under Gaps |
| `cori/docs/plans/04-gateway.md` | fold | `docs/architecture.md` | per-Brief tokens, per-call open check, turn caps, revoke, usage rows | Demonstration needed it: every model call metered through the gateway onto the $15 task |
| `cori/docs/plans/05-worker.md` | fold | `docs/harnesses.md` | PydanticAI loop, tool log before and after each call, ask and answer, Report validation | Demonstration needed ask/answer and resume. Keep the ask/answer and tool-log contract; the PydanticAI loop is replaced by the claude -p harness |
| `cori/docs/plans/06-sandbox.md` | fold | `docs/tech-stack.md` | three sandbox profiles on apple/container, host-only network, stop with disk retained, snapshot | Demonstration needed workspace isolation (sandbox-exec there); plan constraint names Apple containers. Sandbox port belongs in the tech-stack sandbox section |
| `cori/docs/plans/07-broker.md` | fold | `docs/architecture.md` | effect protocol of intent, action, outcome; idempotency keys; push_branch; effect ledger | Demonstration delivered through a held push_branch approved by Tom; the broker is the plan's effect-class constraint |
| `cori/docs/plans/08-supervisor.md` | fold | `docs/architecture.md` | turn loop, deterministic context render, framing estimate, corrections rendered per turn | Demonstration rendered correction 1 into every Brief; the supervisor turn is an architecture.md section by name |
| `cori/docs/plans/09-memory.md` | drop | - | episodic memory and Scribe summaries on popoto over Redis | Memory is built last on popoto over Postgres; this design is Redis-based and outside the setup phase |
| `cori/docs/plans/10-surface.md` | fold | `docs/architecture.md` | CLI approval surface, typed cards, approvals bound to argument digest, consumed once, expiry | Demonstration needed Tom's tap on each push (approve then release); plan constraint: act needs Tom per action |
| `cori/docs/plans/11-verifier.md` | fold | `docs/architecture.md` | verify profile, deterministic checks before prose, blind context from tool log and ledger, typed verdicts | Plan constraints: blind verifier reads docs as contract and asks the governance boolean over every diff. architecture.md holds verification |
| `cori/docs/plans/99-integration.md` | drop | - | end-to-end walkthrough, chaos test, gateway kill rerun | A build-stage plan for the earlier codebase; the demonstration record replaces it as the end-to-end evidence |
| `cori/docs/plans/README.md` | drop | - | index of M0 component plans and spike requirements | Build process index; the plan says the design survives and the process does not |
| `cori/docs/prereqs.md` | fold | `docs/machine.md` | apple/container proven with timings, Postgres roles and grants, Keychain secrets, seat file | States plan constraints (Apple containers, Keychain, Postgres roles); its measured boot and exec times are the evidence machine.md needs. The demonstration's Postgres trust hole is the same grants question |
| `cori/docs/reviews/2026-09-19-blind-spots.md` | drop | - | red-team findings on authority and space model | Review round record; findings are already patched into architecture.md, which is rewritten |
| `cori/docs/reviews/2026-09-19-commit-pass.md` | drop | - | commit-over-optionality pass | Review round record; its outcomes live in architecture.md and tech-stack.md |
| `cori/docs/reviews/2026-09-19-plan-findings.md` | drop | - | contradictions found at plan reconcile | Review round record of the process the plan rejects |
| `cori/docs/reviews/2026-09-19-refinement.md` | fold | `docs/architecture.md` | stop defined as generation fence, token revoke, compute stop with disk retained; three execution records | States the plan constraint "stop is immediate and lossless" precisely; the demonstration's failed turn lost nothing under this definition |
| `cori/docs/reviews/2026-09-20-*-ruling.md` (the money ruling) | fold | `docs/architecture.md` | dollars per objective only (superseded 2026-10-03: metered spending only); effect classes named read, propose, act | States plan constraints: a money figure per task (since superseded), named effect classes. The ruling that attention is not a ledger axis conflicts with Mission item 6 and must be resolved in pass 2 |
| `cori/docs/reviews/2026-09-21-build-log.md` | drop | - | M0 build log, phases and checks | Process history; the plan says the process does not survive |
| `cori/docs/tech-stack.md` | rewrite | `docs/tech-stack.md` | selection rule, persistence, gateway and seat registry, worker and sandbox ports, broker, approval surface, status per choice | Plan names it the primary source of the tech-stack doc. Swap Redis and popoto for Postgres only, PydanticAI worker for the harness port, add M4 Air limits |

## Rows: `_archive_/docs/sdlc/`

| Path | Disposition | Target | Mechanism | Reason |
|---|---|---|---|---|
| `docs/sdlc/do-build.md` | drop | - | slug resolution, sdlc-tool substrate, worktree pattern, repo DoD | Old substrate commands; the state machine is rederived as a typed model in core |
| `docs/sdlc/do-docs.md` | drop | - | features README index, tools-reference, plans on main | Doc-process rules for the archived repo; docs/README.md already states the new rules |
| `docs/sdlc/do-merge.md` | fold | `docs/sdlc-state-machine.md` | merge predicate as deterministic facts, gate stack, post-merge steps | The SDLC state machine's terminal transition is merge, an act through the broker; keep only the merge predicate as a state fact |
| `docs/sdlc/do-patch.md` | drop | - | cross-repo gh targeting, plan recovery, lint commands | Substrate plumbing for the archived pipeline |
| `docs/sdlc/do-plan-critique.md` | drop | - | critique roster barrier, triage routing, required sections | Review-round ceremony the plan names as the disease |
| `docs/sdlc/do-plan.md` | drop | - | stage markers, required plan sections, slug conventions | Substrate plumbing; stage markers are rederived from the typed state model |
| `docs/sdlc/do-pr-review.md` | drop | - | review substrate, multi-judge consensus, verdict and marker co-write | Review ceremony; verification in the rebuild is the blind verifier in architecture.md |
| `docs/sdlc/do-sdlc.md` | fold | `docs/sdlc-state-machine.md` | router mode: resolve, assess, dispatch one stage, return; stage to model table | The SDLC state machine depends on the stage list and the assess-dispatch-one contract; the guard sections drop |
| `docs/sdlc/do-test.md` | drop | - | full-suite ownership, baseline comparability, pytest-clean wrapper | Test-runner plumbing for the archived suite; tests/README.md states the new rule |
| `docs/sdlc/merge-troubleshooting.md` | drop | - | recipes for merge gate failures, oscillation guard | Guard-recovery playbook for guards that do not survive |
| `docs/sdlc/plan-revising-lock.md` | drop | - | plan_revising flag and guard G7 | A guard on the critique loop; no incident-backed reason in the new mission |

## Rows: `_archive_/docs/conventions/`

| Path | Disposition | Target | Mechanism | Reason |
|---|---|---|---|---|
| `docs/conventions/knowledge-base-section.md` | drop | - | KB section in each CLAUDE.md naming vault and Redis memory | Memory is built last and is not Redis; nothing in the demonstration read a vault or memory |

## Rows: `_archive_/docs/features/`

| Path | Disposition | Target | Mechanism | Reason |
|---|---|---|---|---|
| `docs/features/README.md` | drop | - | index of old feature docs | index of the archive; rederived |
| `docs/features/adding-reflection-tasks.md` | drop | - | how to add a reflection callable to the reflections scheduler | reflections internals; the demonstration did not use it; no bridge or SDLC dependency |
| `docs/features/agent-definition-fallback.md` | drop | - | falls back gracefully when an .claude/agents/*.md file is missing or malformed | defensive plumbing for the old agent-definition loader; not needed by the demonstration |
| `docs/features/agent-judgment-catchup.md` | drop | - | LLM judge reads chat threads to recover messages that were never answered | tied to Redis dedup and catchup; a recovery watchdog layer; the demonstration did not need it |
| `docs/features/agent-message-delivery.md` | drop | - | delivery review gate that picks send, react, silent or continue | the demonstration delivered through a held push approved by Tom via the broker, not this drafter/stop-hook gate |
| `docs/features/agent-reply-terminus.md` | drop | - | classifies replies as RESPOND, REACT or SILENT to break bot-to-bot loops | Telegram group edge case; the demonstration did not need it and the plan states no such constraint |
| `docs/features/agent-session-fenced-execution-record.md` | fold | `docs/architecture.md` | per-turn execution record (pid, create_time fence, cwd, harness); resume is scoped to the cwd | (a) claude -p turns resuming one harness session in a workspace need an execution record; the target names execution records |
| `docs/features/agent-session-health-monitor.md` | drop | - | periodic scan that finds and recovers stuck Redis sessions | watchdog plus Redis queue hygiene; drop by default |
| `docs/features/agent-session-lifecycle-writes.md` | drop | - | a bare popoto save() counts as a lifecycle write | popoto/Redis hygiene; drop by default |
| `docs/features/agent-session-liveness-authorship.md` | drop | - | rules for who may write each liveness field, plus the reaper ladder | watchdog/reaper internals; drop by default |
| `docs/features/agent-session-model.md` | drop | - | Redis AgentSession model with a 14-state status lifecycle | popoto model replaced by the Postgres document store; rederive from architecture |
| `docs/features/agent-session-queue.md` | drop | - | Redis queue dispatch modules (pickup, revival, health) | Redis queue removed in the rebuild |
| `docs/features/agent-session-scheduling.md` | drop | - | CLI the agent uses to enqueue SDLC runs and message jobs | Redis/popoto enqueue tooling; scheduled work is rederived as objectives with metered spending in routines |
| `docs/features/agent-teams-headless-policy.md` | drop | - | agent teams on for interactive sessions, off for headless claude -p | harness env flag decision; not a plan constraint; rederive in harnesses if needed |
| `docs/features/agentsession-index-drift-detection.md` | drop | - | detects popoto index vs hash desync | Redis/popoto hygiene guard |
| `docs/features/agentsession-pending-index-leak.md` | drop | - | guards popoto pending-index rebuilds against phantom members | Redis/popoto hygiene guard |
| `docs/features/ancestor-safe-process-lookup.md` | drop | - | process-table lookup that works around BSD pgrep excluding ancestors | service-status plumbing for watchdogs; not needed by the demonstration |
| `docs/features/archived/README.md` | drop | - | index of superseded archived docs | historical artifact |
| `docs/features/archived/system-overview.md` | drop | - | single-process architecture from before the bridge/worker split | superseded historical doc |
| `docs/features/archived/telegram.md` | drop | - | Telegram interface layer from before the bridge/worker split | superseded; the current Telegram behavior lives in other docs |
| `docs/features/assets/dashboard-memories.png` | drop | - | dashboard screenshot image | dashboards and memory UI; drop by default |
| `docs/features/audit-skills.md` | drop | - | lint and architecture audit of skills | governance and audit ceremony |
| `docs/features/blue-sky-fog-planning.md` | drop | - | do-issue blue-sky mode that accepts unknowns | SDLC skill ceremony; the plan's ask-when-ambiguous persona text is rederived in persona |
| `docs/features/bot-e2e-testing.md` | drop | - | valor-telegram send --await-reply for testing other bots | test tooling for third-party bots; not needed by the demonstration |
| `docs/features/bridge-message-query.md` | drop | - | file-based IPC for DM history queries | legacy IPC; no dependency |
| `docs/features/bridge-module-architecture.md` | drop | - | Telegram bridge submodule map (media, routing, context, catchup, reconciler) | module layout of the old code; rederive the Telegram bridge from architecture |
| `docs/features/bridge-prompt-injection-inspection.md` | drop | - | screens untrusted bridge input and adds a risk banner, never blocks | a guard layer; the demonstration did not need it |
| `docs/features/bridge-resilience.md` | drop | - | circuit breaker, startup retry, recovery pipeline | resilience/guard plumbing; drop by default |
| `docs/features/bridge-response-improvements.md` | drop | - | reply-to resumes the original session; a new message starts a fresh session | Overlaps two survivors: reply-to session continuity is carried by session-management.md, outbound filtering by message-drafter.md |
| `docs/features/bridge-self-healing.md` | drop | - | multi-layer self-healing, watchdog, crash tracker | watchdog/self-healing; drop by default |
| `docs/features/bridge-worker-architecture.md` | fold | `docs/architecture.md` | bridge is I/O only; one execution engine; transport-keyed output handlers (telegram/email) | (b) both bridges depend on the split between intake and execution and on per-transport delivery; drop the Redis and concurrency sections |
| `docs/features/bridge-workflow-gaps.md` | drop | - | auto-continue nudge loop and session log snapshots | the old output-router ceremony; steering and stop are rederived in architecture |
| `docs/features/build-output-verification.md` | drop | - | do-build checks that builders produced commits | SDLC ceremony gate |
| `docs/features/build-session-reliability.md` | drop | - | logging propagation, builder WIP commit, worktree isolation | SDLC build ceremony; workspace isolation is rederived from the sandbox design |
| `docs/features/byob-browser-control.md` | drop | - | drives the user's logged-in Chrome over MCP | real-browser control, not the recommended headless run-and-view |
| `docs/features/chat-message-log.md` | drop | - | rolling per-session in/out chat log for drafter dedup | serves the redundancy suppression guard; Redis field |
| `docs/features/checkin-primitive.md` | drop | - | one-shot scheduled future check-in session | serves the promise gate; scheduled work is rederived in routines |
| `docs/features/classification.md` | drop | - | Haiku classifies messages as bug, feature or chore | not the ambiguity classifier the demonstration recommended; the judgement tier is rederived |
| `docs/features/claude-child-keychain-tls-diagnostics.md` | drop | - | runbook for Keychain trust failures in claude child processes | operator diagnostics; not needed by the demonstration |
| `docs/features/claude-code-memory.md` | drop | - | hook-based memory ingest and recall for Claude Code | memory is built last; hook plumbing |
| `docs/features/code-impact-finder.md` | drop | - | embedding plus rerank search for a change's blast radius | do-plan SDLC ceremony tooling |
| `docs/features/codex-exec-dev-lane.md` | drop | - | opt-in Codex exec executor for dev turns | the demonstration used only Claude Code; rederive the harness port |
| `docs/features/codex-skills.md` | drop | - | parallel Codex skill tree | skill packaging; not needed |
| `docs/features/compaction-hardening.md` | drop | - | JSONL backup and nudge guard around SDK compaction | hook/guard plumbing |
| `docs/features/completion-tracking.md` | drop | - | branch-per-work plan tracking | superseded by the ledger and objective tree |
| `docs/features/composed-persona-system.md` | drop | - | composes the system prompt from persona, access level and channel | multi-persona machinery; the plan keeps one identity, so rederive persona.md |
| `docs/features/computer-use.md` | drop | - | macOS Accessibility desktop automation via bcu | not needed by the demonstration |
| `docs/features/config-architecture.md` | drop | - | Pydantic settings and path constants | generic config; rederive in tech-stack |
| `docs/features/config-driven-chat-mode.md` | drop | - | per-group persona routing (engineer vs teammate) | multi-persona routing conflicts with one identity |
| `docs/features/config-timeout-catalog.md` | drop | - | catalog of env-overridable timeouts | config tuning; not needed |
| `docs/features/context-fidelity-modes.md` | drop | - | context compression levels for SDLC subagents | SDLC dispatcher ceremony; the Brief is rederived |
| `docs/features/context-recall-advisory.md` | drop | - | advisory telling the PM to read recent history | advisory guard layer |
| `docs/features/correlation-ids.md` | drop | - | correlation id carried from intake to response | tracing plumbing; the ledger covers it |
| `docs/features/cowork-tasks.md` | drop | - | pattern for cloud-scheduled audits | improvement/audit stack |
| `docs/features/crash-signature-auto-resume.md` | drop | - | crash signature library that gates auto-resume | reflections and watchdog internals |
| `docs/features/customer-resolver.md` | drop | - | dynamic resolution from email sender to customer id | customer-service lane; not needed by the demonstration or by the core email bridge |
| `docs/features/dashboard.md` | drop | - | web dashboard of jobs and sessions | dashboards; drop by default |
| `docs/features/deep-plan-analysis.md` | drop | - | extra do-plan template sections | SDLC planning ceremony |
| `docs/features/delivery-integrity-hardening.md` | drop | - | atomic Redis steering inbox; IMAP auth alerts | Redis-specific fixes; lossless steering is rederived in architecture |
| `docs/features/deployment.md` | drop | - | multi-machine project ownership deployment | deployment and multi-machine; drop by default |
| `docs/features/design-system-tooling.md` | drop | - | Pen to CSS token generator | unrelated tooling |
| `docs/features/direct-api-reply-handling.md` | drop | - | helper that extracts text from Messages API replies | small client plumbing; the gateway is rederived |
| `docs/features/do-build-ai-evaluator.md` | drop | - | Haiku judge compares acceptance criteria to the diff | SDLC review-gate ceremony |
| `docs/features/do-design-audit.md` | drop | - | headless-browser screenshot design critique | review ceremony; the run-and-view need is rederived |
| `docs/features/do-patch-skill.md` | drop | - | targeted repair skill for test failures | SDLC ceremony |
| `docs/features/do-pr-review-bot-identity.md` | drop | - | bot gh identity for pipeline PR reviews | review-gate ceremony |
| `docs/features/do-test.md` | drop | - | test orchestration skill | SDLC ceremony |
| `docs/features/docs-auditor.md` | drop | - | unified docs hygiene reflection | reflections and audit stack |
| `docs/features/documentation-lifecycle.md` | drop | - | hook validators that enforce plan doc sections | hooks/validators; drop by default |
| `docs/features/drafter-redundancy-suppression.md` | drop | - | bigram-Jaccard guard against repeated status sends | guard layer |
| `docs/features/durability-model.md` | drop | - | Room/Job/AgentSession durable inbox and obligations | Redis-era durability model; the Postgres ledger and objective tree replace it |
| `docs/features/email-bridge.md` | fold | `docs/bridges/email.md` | IMAP poll, thread continuation, SMTP reply-all with In-Reply-To, transport-keyed output | (b) the email bridge depends on it; drop the customer resolver, DLQ and alert sections |
| `docs/features/email-cs-auto-reply.md` | drop | - | Cuttlefish customer-service email triage lanes | project-specific lane; not needed by the demonstration |
| `docs/features/email-google-workspace-skills.md` | drop | - | skills that pick a tool ladder for email and Workspace | skill tooling; not a bridge dependency |
| `docs/features/emoji-embedding-reactions.md` | drop | - | emoji reactions chosen by embedding similarity | Telegram cosmetics; not needed |
| `docs/features/enforce-review-docs-stages.md` | drop | - | hard gates that force REVIEW and DOCS stages | review-gate ceremony; the state machine is rederived as a typed model |
| `docs/features/eng-session-architecture.md` | fold | `docs/architecture.md` | steering a running session; pause_open_question stops the nudge loop while it waits for Tom's answer | (a) the demonstration needed steering and the question/answer path to Tom; drop the persona/teammate split |
| `docs/features/enhanced-planning.md` | drop | - | spike and RFC phases in do-plan | SDLC planning ceremony |
| `docs/features/env-completeness-validation.md` | drop | - | .env vs .env.example required-key completeness check at update time | Update/deploy hygiene; secrets move to Keychain; no clause applies |
| `docs/features/expectation-reconciler.md` | drop | - | reflection that respawns orphaned lanes from Job outbound expectations | Reflections internals and recovery watchdog; no clause applies |
| `docs/features/feature-map-marker-guard.md` | drop | - | unit test pinning pytest feature-marker resolution | Test-suite guard; governance restraint |
| `docs/features/features-readme-sort-check.md` | drop | - | PostToolUse hook that keeps the features README sorted | Hook/validator plumbing; no clause applies |
| `docs/features/gh-stale-state-verdict-gate.md` | drop | - | cache-immune PR head SHA read for the review-verdict staleness gate | SDLC review-gate ceremony; the state machine does not need gh read hardening |
| `docs/features/git-state-guard.md` | drop | - | aborts in-progress merges/rebases before SDLC branch switches | Guard; the rebuild isolates workspaces instead |
| `docs/features/goal-gates.md` | fold | `docs/sdlc-state-machine.md` | deterministic per-stage gate checks (plan file, PR, review exists) | (b) the plan names gates as part of the typed state model and goal_gates.py as a primary source. Each gate kept must name its incident and mission item under the governance constraint |
| `docs/features/google-calendar-integration.md` | drop | - | valor-calendar work-time event logging via hooks | Peripheral tooling not needed by demonstration or bridges |
| `docs/features/google-workspace-auth.md` | drop | - | Google OAuth token health checks and reauth CLI | Peripheral integration auth; no clause applies |
| `docs/features/gws-cli-auth.md` | drop | - | per-machine gws CLI auth from shared vault OAuth client | Multi-machine setup procedure; no clause applies |
| `docs/features/happy-path-testing-pipeline.md` | drop | - | BYOB trace to Rodney script browser regression pipeline | The recommended headless-browser check is a different mechanism; this is regression ceremony |
| `docs/features/harness-abstraction.md` | drop | - | all sessions routed through claude -p CLI harness | Historical routing note superseded by harness-adapter and session-continuity |
| `docs/features/harness-adapter.md` | fold | `docs/harnesses.md` | TurnRequest/TurnResult/TurnEvent seam owning claude -p argv, env, stream-json parsing | (a) the demonstration ran claude -p turns through a harness port; fold the seam, drop the schema-routing history |
| `docs/features/harness-session-continuity.md` | fold | `docs/harnesses.md` | first-turn full context, persisted session uuid, later turns via --resume | (a) the demonstration needed claude -p turns resuming one harness session |
| `docs/features/harness-startup-retry.md` | drop | - | requeue when the claude binary is missing from PATH | Retry band-aid; bug fixes fix code |
| `docs/features/headless-session-runner.md` | fold | `docs/harnesses.md` | one claude -p per turn, turn-end from stream-json result, steer at turn boundary | (a) the demonstration needed the one-turn-at-a-time runner; drop the PM/dev subagent and Stop-hook reconciliation detail |
| `docs/features/hook-manifest.md` | drop | - | TOML manifest generating hook registration in settings.json | Hook plumbing; governance restraint |
| `docs/features/hook-target-resolution.md` | drop | - | shared module resolving which file a PostToolUse validator judges | Validator plumbing; governance restraint |
| `docs/features/hooks-best-practices.md` | drop | - | hook safety patterns and the /audit-hooks skill | Hook plumbing; governance restraint |
| `docs/features/hotfix-issue-disposition.md` | drop | - | commit-msg/pre-push hooks requiring Closes/Refs/No-issue on main | Git-hook guard; SDLC ceremony |
| `docs/features/hybrid-retrieval-eval.md` | drop | - | popoto BM25+vector vs RRF memory recall evaluation | Memory/popoto, built last; no clause applies |
| `docs/features/image-vision.md` | drop | - | Haiku vision description of Telegram images | Media tooling not needed by the demonstration |
| `docs/features/imagine-build-agent-cma.md` | drop | - | skills that build Claude Managed Agents from a build sheet | Unrelated product skill; no clause applies |
| `docs/features/improvement-cloud-execution.md` | drop | - | cloud sandbox plan and unit-3 infra cost for RSI | Improvement stack |
| `docs/features/improvement-controller.md` | drop | - | recursive self-improvement controller records, journal, dispatch | Improvement stack |
| `docs/features/improvement-evaluation.md` | drop | - | candidate vs incumbent evaluation harness with Redis arenas | Improvement stack; Redis |
| `docs/features/improvement-release.md` | drop | - | release lifecycle, rollback drill, promotion gate for improvements | Improvement stack |
| `docs/features/improvement-research-cycle.md` | drop | - | planner tick, research session, experiment freeze | Improvement stack |
| `docs/features/intake-classifier.md` | drop | - | local granite classifier for interjection vs new_work on Telegram intake | Resident local model; the ambiguity classifier is rederived in judgement-layer.md |
| `docs/features/json-cache-layer.md` | drop | - | JSON-on-disk cache for deterministic LLM calls | Optimization utility; no clause applies |
| `docs/features/knowledge-document-integration.md` | drop | - | work-vault indexing into Redis memory with bloom thoughts | Memory/popoto, built last |
| `docs/features/lane-branch-identity.md` | drop | - | AgentSession.branch_name tracks worktree HEAD, not slug | SDLC lane bookkeeping incident fix; workspace isolation is rederived |
| `docs/features/launchctl-bootstrap-fail-soft.md` | drop | - | errno-5 launchctl bootstrap recovery helper | Deployment/service-install plumbing |
| `docs/features/legacy-artifact-guard.md` | drop | - | test pinning deleted modules as absent | Test guard; governance restraint |
| `docs/features/length-safe-content-store.md` | drop | - | popoto filesystem store with length-safe filenames | Popoto internals |
| `docs/features/link-summarization.md` | drop | - | Perplexity summaries of shared URLs | Enrichment tooling not needed by the demonstration |
| `docs/features/lint-auto-fix.md` | drop | - | PostToolUse and pre-commit ruff auto-fix | Hook plumbing |
| `docs/features/llm-stack-compat-gate.md` | drop | - | anthropic/pydantic-ai coupled-version compat check | Dependency guard |
| `docs/features/llm-task-taxonomy.md` | fold | `docs/judgement-layer.md` | per-site LLMTask declaration (kind, backend, error-cost tier) read by a router | (c) the plan names the task taxonomy and router in the judgement layer; drop the Ollama/encoder legs |
| `docs/features/local-doctor.md` | drop | - | unified environment health-check CLI | Ops tooling; no clause applies |
| `docs/features/local-encoder-classifier.md` | drop | - | in-process ONNX embedding plus linear head classifier backend | Resident local model |
| `docs/features/local-model-policy.md` | drop | - | which Ollama/local models run per machine | Local-model policy; plan says no resident local models |
| `docs/features/log-rotation.md` | drop | - | user-space launchd log rotation | Ops plumbing |
| `docs/features/long-task-checkpointing.md` | drop | - | PROGRESS.md and frequent commits across compaction | Prompt convention not needed by the demonstration; ledger covers state |
| `docs/features/machine-readable-dod.md` | drop | - | plan Verification table of executable checks | SDLC ceremony; architecture verification is rederived |
| `docs/features/markitdown-ingestion.md` | drop | - | binary-to-markdown sidecar ingestion for the knowledge pipeline | Memory/knowledge tooling, built last |
| `docs/features/mcp-library-requirements.md` | drop | - | MCP server catalog and task-based selection | Stale requirements; no clause applies |
| `docs/features/media-enrichment.md` | drop | - | bridge download plus worker media description/transcription | Media tooling; bridge does not depend on enrichment to function |
| `docs/features/memory-hook-performance.md` | drop | - | memory recall hook latency and Stop-hook extraction detach | Memory/hook internals |
| `docs/features/memory-search-tool.md` | drop | - | search/save/inspect/forget API over Memory model | Memory, built last |
| `docs/features/memory-telemetry.md` | drop | - | corpus-level memory quality metrics | Memory, dashboards |
| `docs/features/message-drafter.md` | fold | `docs/bridges/telegram.md` | medium-aware wire-format validation and long-output handling for Telegram and email | (b) both bridges depend on per-medium outbound formatting and the 4096-char limit; also feeds bridges/email.md |
| `docs/features/message-pipeline.md` | drop | - | enqueue-fast, enrich-later bridge pipeline | Redis queue plus enrichment; ingress is rederived from architecture |
| `docs/features/message-reconciler.md` | drop | - | periodic scan recovering missed Telegram updates | Watchdog-style recovery loop |
| `docs/features/mid-session-steering.md` | fold | `docs/bridges/telegram.md` | Telegram reply-to message injected as steering at the next turn boundary | (b) the Telegram bridge depends on reply-to steering; the Redis list becomes Postgres; the mechanism also goes to architecture steering |
| `docs/features/module-scope-env-guard.md` | drop | - | AST census and guard for import-time env reads | Guard; governance restraint |
| `docs/features/multi-judge-consensus.md` | drop | - | K parallel review judges aggregated into a verdict | Review-round ceremony |
| `docs/features/narrated-deck-video.md` | drop | - | Marp deck plus TTS to narrated MP4 | Media/TTS tooling |
| `docs/features/never_started_session_recovery.md` | drop | - | health-loop gate recovering sessions with no harness output | Watchdog; lossless stop is rederived |
| `docs/features/nightly-regression-tests.md` | drop | - | launchd nightly pytest run filing GitHub issues | Test ceremony |
| `docs/features/nightly-triage-dispatch.md` | drop | - | nightly run lock and issue triage session dispatch | Test ceremony |
| `docs/features/nonharness-llm-wrapper.md` | drop | - | PydanticAI run_typed wrapper with backend legs and fallback | Transport is rederived in judgement-layer.md and gateway metering; Ollama legs out |
| `docs/features/off-pipeline-merge-path.md` | drop | - | merging PRs with no plan or CRITIQUE verdict | SDLC critique/merge-gate ceremony |
| `docs/features/officecli-integration.md` | drop | - | OfficeCLI binary install via update | Peripheral tooling, deployment |
| `docs/features/ollama-client.md` | drop | - | Ollama HTTP transport owner | Local model |
| `docs/features/omnigent-hook-edge-reference.md` | drop | - | prior-art map for PTY/hook turn-end detection | Obsolete PTY path and hook plumbing |
| `docs/features/opencode-sync.md` | drop | - | port .claude config into OpenCode layout | Peripheral tooling; no clause applies |
| `docs/features/openrouter-whisper-backend.md` | drop | - | OpenRouter/OpenAI Whisper transcription backend | Media tooling |
| `docs/features/operational-logging.md` | drop | - | bracket-prefixed INFO log tags along the message path | Observability convention; ledger replaces it |
| `docs/features/out-of-domain-recovery.md` | drop | - | bridge-side wedge recovery and per-tool cost backstop | Watchdog/Redis; metered spending is rederived in architecture |
| `docs/features/pattern-kill-guard.md` | drop | - | PreToolUse validator blocking pattern kills | Guard; governance restraint |
| `docs/features/persona-toolbelts.md` | drop | - | per-persona TOML tool manifests compiled to CLI flags (ships dark) | Multi-persona; effect classes and broker replace it |
| `docs/features/personas.md` | fold | `docs/persona.md` | identity.json plus composable persona segments and overlays | (c) the plan's one-identity persona; fold the identity/voice segments, drop the engineer/teammate/CS split |
| `docs/features/pipeline-dead-letters.md` | drop | - | DeadLetter model, dashboard tile, replay reflection | Redis/popoto, reflections, dashboards |
| `docs/features/pipeline-graph.md` | fold | `docs/sdlc-state-machine.md` | (stage, outcome) to next-stage edge table with cycles | (b) the SDLC state machine depends on the transition graph; drop the CRITIQUE-loop ceremony edges |
| `docs/features/pipeline-state-machine.md` | rewrite | `docs/sdlc-state-machine.md` | programmatic stage state recorded at transition points, not inferred | (b) the core of the SDLC state machine; re-home it from hook-written to a typed model |
| `docs/features/plan-checkbox-writers.md` | drop | - | plan checkbox ticking replacing the plan completion gate | SDLC ceremony |
| `docs/features/plan-critique-triage.md` | drop | - | LITE/FULL critique tiering and critic roster | SDLC ceremony (critique rounds); no survival clause applies |
| `docs/features/plan-migration-invariant.md` | drop | - | auto-move completed plans to archive dir | plan-folder housekeeping; demo did not need it, no bridge or state machine dependency |
| `docs/features/plan-prerequisites.md` | drop | - | pre-build check of plan Prerequisites table | build-stage validator ceremony; no survival clause applies |
| `docs/features/pm-briefings.md` | drop | - | slot-driven daily Telegram briefings via reflections | reflections internals; routines can be rederived from the architecture doc |
| `docs/features/pm-channels.md` | drop | - | project mode routing a Telegram group to a work-vault folder | old projects.json mode flag tied to the eng/PM split; not needed by the demo or the bridge contract |
| `docs/features/pm-final-delivery.md` | drop | - | pipeline-complete runner replacing a delivery marker | fixes the old nudge loop; delivery is now a held push through the broker |
| `docs/features/pm-routing-collaboration.md` | fold | `docs/judgement-layer.md` | four-way intent classification (sdlc/collaboration/other/question) | (a) the demo recommends a judgement-tier classifier before the first turn; keep only the taxonomy and drop the thresholds |
| `docs/features/pm-sdlc-decision-rules.md` | drop | - | PM routing table over OUTCOME blocks plus per-stage model choice | PM/Dev split is gone; the verdict model is rederived in sdlc-state-machine |
| `docs/features/pm-session-child-fanout.md` | drop | - | fan out one child session per issue | multi-session orchestration ceremony; the demo was one task |
| `docs/features/pm-session-liveness.md` | drop | - | evidence-only kill detector and liveness dashboard | watchdog/detector; drop by default |
| `docs/features/pm-teammate-mode.md` | drop | - | Haiku fast path for informational queries | persona split; the classifier concept is already covered by the pm-routing-collaboration fold |
| `docs/features/pm-voice-refinement.md` | drop | - | verbatim drafter, question prefix, linkify, sentence truncation | Telegram output formatting can be rederived; the bridge contract does not depend on it |
| `docs/features/popoto-descriptor-pollution-ledger.md` | drop | - | inventory of popoto index-race compensators | popoto hygiene; no popoto until memory |
| `docs/features/popoto-index-hygiene.md` | drop | - | orphaned index cleanup and model TTL | popoto/Redis hygiene |
| `docs/features/popoto-redis-expansion.md` | drop | - | Popoto models for messages, dead letters, events | Redis/popoto storage replaced by the Postgres document store |
| `docs/features/popoto-version-floor-guard.md` | drop | - | refuse index rebuild below popoto floor | guard plus popoto hygiene |
| `docs/features/post-compact-regrounding.md` | drop | - | hook nudge after context compaction | hook plumbing; the Brief re-renders the ledger every turn anyway |
| `docs/features/promise-gate.md` | drop | - | reject forward-deferral promises in deliveries | delivery gate/guard; an honesty rule, if wanted, is persona text |
| `docs/features/pytest-clean-zero-test-guard.md` | drop | - | fail a pytest run that executed zero tests | test-DB guard |
| `docs/features/qa-conversational-humility.md` | drop | - | brief, hedged teammate QA tone | teammate persona tuning; persona is rederived for one identity |
| `docs/features/race-condition-analysis.md` | drop | - | Race Conditions section in plan template | SDLC plan ceremony |
| `docs/features/raw-redis-guard.md` | drop | - | PreToolUse hook blocking raw Redis on popoto keys | guard plus Redis hygiene |
| `docs/features/reaction-semantics.md` | fold | `docs/bridges/telegram.md` | emoji reaction protocol for message lifecycle (received/working/done/error) | (b) the Telegram bridge depends on it for acknowledgement versus reply-delivered signaling |
| `docs/features/read-the-room.md` | drop | - | Haiku send/trim/suppress pass before Telegram send | a classifier deciding what may be sent goes against the judgement-tier rule; extra pre-send gate |
| `docs/features/redis-client-accessor.md` | drop | - | single Redis client accessor for non-ORM keys | Redis replaced by Postgres |
| `docs/features/redis-durability.md` | drop | - | AOF/noeviction durability model for Redis | Redis replaced by Postgres |
| `docs/features/redis-flush-hardening.md` | drop | - | client-side flush guards after prod flush incidents | Redis hygiene guard |
| `docs/features/redis-models.md` | drop | - | popoto model relationship map | Redis/popoto models replaced by Postgres data.md |
| `docs/features/reflection-agent-handoff.md` | drop | - | reflections hand Findings to an agent instead of messaging humans | reflections internals; the one-identity rule already lives in persona/architecture |
| `docs/features/reflection-scheduler-subprocess.md` | drop | - | launchd-supervised reflection scheduler process | reflections internals; routines under launchd are rederived from the architecture doc |
| `docs/features/reflections-dashboard.md` | drop | - | web dashboard for reflection runs | dashboard |
| `docs/features/reflections.md` | drop | - | unified reflection registry, scheduler, run tracking | reflections internals; routines.md is rederived as objectives with metered spending |
| `docs/features/relay-retry-guard.md` | drop | - | bounded outbox retries and dead-letter routing | Redis outbox guard; the bridge is rebuilt on Postgres |
| `docs/features/remote-update.md` | drop | - | Telegram /update command and cron to sync machines | multi-machine deployment |
| `docs/features/reply-thread-context-hydration.md` | fold | `docs/bridges/telegram.md` | reply-to carries thread context and resumes or seeds a session | (b) the Telegram bridge's reply-to routing and context depend on it |
| `docs/features/resume-hydration-context.md` | drop | - | inject recent commits on session resume | the ledger rendered into every Brief replaces it |
| `docs/features/resume-reverification.md` | drop | - | rails rule to re-derive completion claims from live evidence after resume | rails rule/gate; the ledger plus verification cover it |
| `docs/features/review-workflow-screenshots.md` | fold | `docs/architecture.md` | run the app and screenshot it in a browser to verify work | (a) the demonstration found Valor never looked at the result in a browser. Keep only the verify-by-use step for the verification section; the BYOB real-Chrome surface and review ceremony drop |
| `docs/features/scale-agent-session-queue-with-popoto-and-worktrees.md` | drop | - | popoto Job queue with per-session worktrees | Redis/popoto queue; the worktree idea is covered by the session-isolation fold |
| `docs/features/scheduled-disk-reclaim.md` | drop | - | sweep of stale worktrees, transcripts, session logs | ops housekeeping; no survival clause applies |
| `docs/features/sdk-modernization.md` | drop | - | claude-agent-sdk agent registry and hooks | stale SDK/hook plumbing; harnesses are rederived |
| `docs/features/sdlc-agent-session-playlist.md` | drop | - | stub pointing to per-chat sequential queues | empty stub |
| `docs/features/sdlc-critique-stage.md` | drop | - | CRITIQUE stage war-room critics | SDLC ceremony (critique rounds) |
| `docs/features/sdlc-enforcement.md` | drop | - | stop-hook quality gates on code sessions | hook/gate ceremony |
| `docs/features/sdlc-first-routing.md` | drop | - | fast-path plus LLM work-request classification | duplicates the pm-routing-collaboration fold for judgement-layer |
| `docs/features/sdlc-fork-artifact-grounding.md` | drop | - | fail-closed guards that verify fork artifacts | guards |
| `docs/features/sdlc-fork-turn-boundary.md` | drop | - | fork skill must not end with a live background child | Claude Code fork-skill plumbing; no survival clause applies |
| `docs/features/sdlc-issue-keyed-stage-ledger.md` | fold | `docs/sdlc-state-machine.md` | stage/verdict state keyed by (repo, issue), not by the ephemeral session | (b) the SDLC state machine needs durable issue-keyed state; drop the Redis lease details |
| `docs/features/sdlc-issue-ownership-lock.md` | drop | - | Redis per-issue mutex and run_id lease | Redis lock plumbing; Postgres row ownership is rederived |
| `docs/features/sdlc-lane-identity.md` | drop | - | single minted lane slug for worktree, task list, plan | lane bookkeeping; rederived from the architecture doc |
| `docs/features/sdlc-local-supervision.md` | drop | - | /do-sdlc local loop over stages | skill ceremony; the supervisor turn is in the architecture doc |
| `docs/features/sdlc-observer.md` | drop | - | SDLC pipeline web dashboard | dashboard |
| `docs/features/sdlc-parallel-execution.md` | drop | - | removed-feature tombstone | historical artifact |
| `docs/features/sdlc-pipeline-integrity.md` | drop | - | continuation hardening, URL validation, merge guard | guards over old Redis queue; merge as act-with-approval is already in the broker |
| `docs/features/sdlc-pipeline-portability.md` | drop | - | seven robustness fixes for non-ai repos | patches to old router/guards |
| `docs/features/sdlc-pipeline-state.md` | drop | - | PipelineStateMachine stage markers via sdlc-tool | superseded by the issue-keyed ledger fold; CLI details are rederived |
| `docs/features/sdlc-pipeline.md` | fold | `docs/sdlc-state-machine.md` | stage sequence and router dispatch over stage state | (b) the SDLC state machine's stages and transitions; drop G1-G9 guard list |
| `docs/features/sdlc-repo-addenda.md` | drop | - | per-stage repo addenda files for global skills | skill-convention ceremony |
| `docs/features/sdlc-review-drift-classifier.md` | drop | - | classify reviewed-SHA to head drift as docs-only or code | review-gate ceremony |
| `docs/features/sdlc-router-oscillation-guard.md` | drop | - | G1-G9 dispatch guards against router loops | guards |
| `docs/features/sdlc-run-identity-self-heal.md` | drop | - | self-heal missing run_id on resume | patch over Redis lease plumbing |
| `docs/features/sdlc-run-self-recognition.md` | drop | - | track multiple run_ids per logical run | patch over Redis lease plumbing |
| `docs/features/sdlc-skills-audit.md` | drop | - | blocking gates added to SDLC skills | gate ceremony |
| `docs/features/sdlc-stage-handoff.md` | drop | - | stage results posted as GitHub issue comments | SDLC ceremony; the ledger carries stage context |
| `docs/features/sdlc-stage-tracking.md` | drop | - | how stage markers are written day to day | duplicates the issue-keyed ledger fold |
| `docs/features/sdlc-terminal-lane-state.md` | drop | - | terminal guard saying a lane is finished, not blocked | guard; a typed terminal state is rederived in sdlc-state-machine |
| `docs/features/sdlc-tool-resolver.md` | drop | - | cwd-independent bash wrapper for sdlc-tool | tooling plumbing |
| `docs/features/sdlc-verdict-fail-closed-persistence.md` | drop | - | verdict refused without persisted findings | fail-closed gate; typed verdicts are rederived in sdlc-state-machine |
| `docs/features/self-healing-merge-gate.md` | drop | - | eng session self-resolves merge gate conditions | merge gate ceremony |
| `docs/features/semantic-doc-impact-finder.md` | drop | - | embedding plus rerank to find affected docs | docs tooling; no survival clause applies |
| `docs/features/sentry-triage.md` | drop | - | daily Sentry A-E triage reflection | reflections plus ops tooling |
| `docs/features/session-health-check.md` | drop | - | PostToolUse watchdog loop detector | watchdog/hook |
| `docs/features/session-isolation.md` | fold | `docs/architecture.md` | per-task isolated workspace (worktree) and task list | (a) the demo needed workspace isolation per task; keep only the isolation contract, not the task-list tiers |
| `docs/features/session-lifecycle-diagnostics.md` | drop | - | LIFECYCLE log lines and stall detector | diagnostics/watchdog |
| `docs/features/session-lifecycle.md` | fold | `docs/architecture.md` | session state machine, Room/Job/session durability split, terminal states | (a) execution records, lossless stop and resume need a typed session lifecycle; drop the popoto mechanics |
| `docs/features/session-liveness-tick-counter.md` | drop | - | watchdog advances a reaction counter on the originating Telegram message | watchdog liveness UI; demo did not need it and no bridge or SDLC dependency; watchdogs drop by default |
| `docs/features/session-management.md` | fold | `docs/bridges/telegram.md` | reply-chain walk to the root human message resolves one canonical session per thread | (b) Telegram bridge threading depends on mapping every reply in a thread to one task/session |
| `docs/features/session-progress-verdict.md` | drop | - | CLI verdict on whether a session is still working, built from existing signals | liveness diagnostics tooling; not needed by demo, bridge, or SDLC |
| `docs/features/session-recovery-mechanisms.md` | drop | - | catalogue of 10 Redis session revive/re-enqueue paths with terminal guards | Redis worker recovery plumbing; replaced by kernel lossless stop and ledger |
| `docs/features/session-steering.md` | fold | `docs/architecture.md` | turn-boundary steering inbox drained between claude -p turns; parent-to-child steer and abort | (c) steering is a plan kernel constraint; keep the turn-boundary semantics, drop Redis list, watchdog-authored and auto-preempt layers |
| `docs/features/session-tagging.md` | drop | - | auto-tags on AgentSession at finalize for analytics | popoto analytics metadata; no demo, bridge, or SDLC need |
| `docs/features/session-telemetry.md` | drop | - | per-session JSONL event trace on disk | observability layer; ledger and execution records rederived from architecture doc |
| `docs/features/session-transcripts.md` | fold | `docs/harnesses.md` | append-only per-session transcript files of every turn, tool call, and result | (a) demo ran claude -p turns resuming one harness session; transcript capture belongs to the harness port; popoto metadata half drops |
| `docs/features/session-watchdog-reliability.md` | drop | - | type guards and activity-based stall detection fixes for the watchdog | watchdog bug-fix guards; watchdogs drop by default |
| `docs/features/session-watchdog.md` | drop | - | 5-minute health monitor alerting on looping or silent sessions | watchdog; metered spending and lossless stop replace it |
| `docs/features/side-effect-jobs.md` | drop | - | durable SideEffectJob rows drained by a reflection for post-session memory extraction | memory extraction plumbing via reflections; memory built last |
| `docs/features/single-machine-ownership.md` | drop | - | validator that each bridge contact resolves to exactly one machine | multi-machine config; rebuild is one Mac |
| `docs/features/site-vault-content.md` | drop | - | docs site pages built from vault decks and persona bios | public website content; out of scope |
| `docs/features/skill-context-convention.md` | drop | - | per-repo context file probed by global skills | skill distribution plumbing; not needed by demo, bridge, or SDLC |
| `docs/features/skill-context-injection.md` | drop | - | SDLC_* env vars injected into the Claude Code subprocess | SDLC ceremony wiring off Redis AgentSession; the typed state machine rederives what a turn needs |
| `docs/features/skills-dependency-map.md` | drop | - | map of skill and subagent invocation chains | catalogue of old skill sprawl; rederived |
| `docs/features/skills-global.md` | drop | - | catalog of global skills hardlinked to ~/.claude/skills | skill inventory; not a mechanism the rebuild keeps |
| `docs/features/skills-reorganization.md` | drop | - | SKILL.md template split and frontmatter classification | historical reorganization record |
| `docs/features/slot-lease-ownership.md` | drop | - | owner-keyed worker concurrency slot leases and progress-deadline cancel | worker wedge recovery plumbing; drop by default |
| `docs/features/stall-advisory-classifier.md` | drop | - | healthy/suspect/stalled badge from telemetry | dashboard stall detection; drop by default |
| `docs/features/stall-recovery.md` | drop | - | stall-advisory reflection kills and re-enqueues stuck sessions | reflection-driven recovery; drop by default |
| `docs/features/stall-retry.md` | drop | - | watchdog retries stalled sessions with exponential backoff | watchdog; drop by default |
| `docs/features/standardized-enums.md` | drop | - | StrEnum constants for session types, personas, classifications | code hygiene for old session types; rederived |
| `docs/features/steering-implementation-spec.md` | drop | - | historical Redis steering-list design and bridge coalescing | self-declared historical spec; current semantics fold via session-steering.md |
| `docs/features/structured-logging-telemetry.md` | drop | - | structured log lines, Redis counters, alert thresholds for Observer | Redis observability; drop by default |
| `docs/features/subagent-roster.md` | drop | - | catalog of .claude/agents subagents and the skills that dispatch them | old SDLC subagent sprawl; rederived |
| `docs/features/subconscious-memory.md` | drop | - | Redis memory records injected as thought stubs and post-session extraction | popoto memory, which the plan builds last; redesign then rather than carry this over |
| `docs/features/superwhisper-transcription.md` | drop | - | local SuperWhisper voice transcription with Whisper API fallback | media tooling and a local app; drop by default |
| `docs/features/sustainable-self-healing.md` | drop | - | circuit-gated queue pause, drip resume, failure-loop governance reflections | Redis queue governance and reflections; drop by default |
| `docs/features/task-list-isolation.md` | drop | - | CLAUDE_CODE_TASK_LIST_ID scoping of Task tool storage | Claude Code internals note; harness session resume covers what matters |
| `docs/features/teammate-session-permissions.md` | drop | - | pre_tool_use hook blocks teammate writes to source paths | hook-based permission guard; effect classes and broker replace it; one identity |
| `docs/features/telegram-history.md` | drop | - | Redis cache of inbound messages, link collection, chat registry | popoto message cache; Telegram stays source of truth |
| `docs/features/telegram-inbound-attachments.md` | drop | - | enrich steering messages with attachment content and auto-ingest to vault | bridge nicety, not required by demo; vault ingest drops |
| `docs/features/telegram-message-edit-handling.md` | fold | `docs/bridges/telegram.md` | MessageEdited routes to steering a running task or a fresh task for finished work | (b) Telegram bridge correctness depends on edits not being dropped; maps onto the steering kernel |
| `docs/features/telegram-messaging.md` | drop | - | valor-telegram CLI reading from Redis and sending via outbox relay | Redis outbox CLI; delivery now goes through the broker as an act effect |
| `docs/features/telegram-pm-guide.md` | drop | - | fast-path message patterns like "issue N" and "pr N" | keyword routing; the judgement-tier classifier replaces it |
| `docs/features/telegram-poll-questions.md` | fold | `docs/bridges/telegram.md` | blocked judgment call rendered as native group poll; 8-byte option limit; answer correlation | (a) demo needed Valor-to-Tom questions and answers; the protocol constraints are hard-won facts |
| `docs/features/test-baseline-verification.md` | drop | - | baseline-verifier subagent reruns failures on main | SDLC test ceremony; drop by default |
| `docs/features/test-concurrency-coordination.md` | drop | - | sentinel-ID namespacing to avoid Redis contention between test runs | Redis test hygiene; drop by default |
| `docs/features/test-coverage-standards.md` | drop | - | standards for silent-failure classes in the test suite | test governance; drop by default |
| `docs/features/test-db-derivation-guard.md` | drop | - | static check forbidding tests from computing their own Redis db | test-DB guard; drop by default |
| `docs/features/test-db-ownership.md` | drop | - | flock-claimed per-process Redis test db ownership | test-DB guard; drop by default |
| `docs/features/test-isolation-hardening.md` | drop | - | popoto client identity and hooks-less-parent guard fixtures | test isolation guards; drop by default |
| `docs/features/test-reliability-flaky-filter.md` | drop | - | retry failing tests once before baseline verification | SDLC test ceremony; drop by default |
| `docs/features/tools-reference.md` | drop | - | catalog of MCP servers and CLI tools | inventory of the old toolset; rederived |
| `docs/features/tools-standard.md` | drop | - | manifest and README standard plus tool audit | tool governance audit; drop by default |
| `docs/features/trace-and-verify.md` | drop | - | forward data-trace root cause protocol | debugging methodology, not a system mechanism |
| `docs/features/tts.md` | drop | - | text-to-speech module and voice debrief skill | media tooling; drop by default |
| `docs/features/tui-interaction-capture.md` | drop | - | hooks capture TUI human-in-the-loop patterns into memory | hook plumbing feeding memory; drop by default |
| `docs/features/unified-analytics.md` | drop | - | SQLite and Redis dual-write metrics with dashboard | analytics and dashboards; drop by default |
| `docs/features/update-warning-channel.md` | drop | - | /update warning grammar, caps, and suppression | deployment and update tooling; drop by default |
| `docs/features/upvote-autonomous-sdlc-pickup.md` | drop | - | reflection starts SDLC lanes on upvote-labeled issues | reflection-driven SDLC intake; scheduled work rederives as routines with metered spending |
| `docs/features/utc-timestamps.md` | drop | - | tz-aware UTC everywhere, local time only at display | generic convention, not a plan constraint; trivial to restate |
| `docs/features/uv-sync-worktree-guard.md` | drop | - | blocks uv sync from worktrees sharing the root venv | guard; drop by default |
| `docs/features/valorengels-site.md` | drop | - | public static docs site source and deploy | website and deployment; out of scope |
| `docs/features/vault-drift-audit.md` | drop | - | audit of vault vs site and docs drift | doc audit ceremony; drop by default |
| `docs/features/video-watch-visual-grounding.md` | drop | - | CLI that pulls video frames and transcript for visual questions | media tooling; drop by default |
| `docs/features/watchdog-log-isolation.md` | drop | - | watchdog modules have no logging side effects on import | watchdog plumbing; drop by default |
| `docs/features/web-dashboard.md` | drop | - | localhost Jobs and sessions dashboard with stage pills | dashboard; drop by default |
| `docs/features/web-ui.md` | drop | - | FastAPI and HTMX localhost observability server | dashboard infra; drop by default |
| `docs/features/websearch-do-plan.md` | drop | - | WebSearch research phase in /do-plan | SDLC skill ceremony; drop by default |
| `docs/features/wedge-reap-subtree-escalation.md` | drop | - | reap setsid subtrees and durable boot kill-list on wedge | wedge-recovery plumbing; lossless stop is rederived from architecture |
| `docs/features/wire-schemas.md` | drop | - | pydantic schemas for Redis outbox, steering, notify wires | Redis wires; Postgres replaces them |
| `docs/features/with-concerns-recritique-gate.md` | drop | - | re-critique loop before building on a with-concerns verdict | critique round gate; explicitly excluded SDLC ceremony |
| `docs/features/worker-fault-containment.md` | drop | - | respawn logic for long-lived worker asyncio tasks | worker reliability plumbing; drop by default |
| `docs/features/worker-hibernation.md` | drop | - | pause sessions and hibernate the worker on Anthropic circuit open | Redis flag hibernation; drop by default |
| `docs/features/worker-liveness-recovery.md` | drop | - | dead-man's-switch heartbeat recycles a frozen worker loop | watchdog-class plumbing; drop by default |
| `docs/features/worker-service.md` | drop | - | standalone worker consuming AgentSessions from Redis | Redis-queue worker architecture; the supervisor turn is rederived from architecture doc |
| `docs/features/worker-wedge-investigation.md` | drop | - | investigation of worker slot-leak wedge | historical investigation record |
| `docs/features/workspace-safety-invariants.md` | drop | - | pre-launch cwd checks: exists, contained under allowed root, sanitized slug | A pre-launch cwd guard. The demonstration isolated the workspace with a sandbox profile and its own Postgres, which is listed under Gaps; a guard here is not needed |
| `docs/features/worktree-manager.md` | drop | - | git worktree lifecycle and branch verification per slug | SDLC lane plumbing; the demo isolated work with a sandbox workspace, not worktrees |
| `docs/features/worktree-sdk-compatibility.md` | drop | - | experiment showing the SDK runs in git worktrees | historical experiment |
| `docs/features/worktree-venv-isolation.md` | drop | - | per-worktree venv provisioning | Python dev-env plumbing; drop by default |
| `docs/features/xfail-hygiene.md` | drop | - | checks for stale xfail markers | test hygiene; drop by default |
| `docs/features/youtube-search.md` | drop | - | yt-dlp YouTube search CLI | media/youtube tooling; drop by default |
| `docs/features/youtube-transcription.md` | drop | - | auto-transcribe shared YouTube links into context | media/youtube tooling; drop by default |

## Contested

The feature set ends at 27 survivors, under the plan's thirty, so nothing is
over the cap. Two lists follow so Tom can move the line in either direction.

**Weakest survivors, candidates to cut:**

- `docs/features/reaction-semantics.md`: emoji lifecycle signals on Telegram.
  The bridge works without them; the argument for keeping is that Tom reads
  "received, working, done" at a glance without spending attention on a
  message.
- `docs/features/telegram-message-edit-handling.md`: edits routed to steering
  or a fresh task. Keep only if the rebuilt bridge reuses the existing code,
  which the plan allows ("possibly unchanged").
- `docs/features/message-drafter.md`: per-medium wire-format limits (Telegram
  4,096 characters, email). Argument for: hard facts a bridge cannot work
  without. Argument against: they fit in two sentences of each bridge doc.
- `docs/features/eng-session-architecture.md`: the question-pending pause.
  The demonstration's kernel already stops a run on `question.asked`; the
  archived doc mostly describes the nudge loop that pause existed to stop.
- `docs/features/goal-gates.md`: kept because the plan names gates in the
  state machine. Every gate it carries must pass the governance constraint;
  if none does, the fold is empty and the row becomes a drop.
- `docs/features/review-workflow-screenshots.md`: the closest archived doc to
  "view the app in a browser", but its surface is Tom's real Chrome, not a
  headless browser in the workspace. The gap entry below may be enough.

**Near misses, dropped, with the argument for bringing each back:**

- `docs/features/reflections.md` and `reflection-scheduler-subprocess.md`
  into `docs/routines.md`: the only archived description of launchd-driven
  scheduled work. Dropped because no routine ran in the demonstration and the
  plan's primary source for routines is the code, not these docs.
- `docs/features/codex-exec-dev-lane.md` into `docs/harnesses.md`: the one
  worked example of a second harness behind the same port. Dropped because the
  demonstration used Claude Code only.
- `docs/features/intake-classifier.md` and `classification.md` into
  `docs/judgement-layer.md`: prior classifier sites. Dropped because one runs
  a resident local model (ruled out on 16 GB) and the other classifies work
  type, not the ambiguity the demonstration needed.
- `docs/features/agent-session-model.md` and `durability-model.md` into
  `docs/data.md`: the archived session and durable-inbox records. Dropped
  because they are Redis shapes; the task record in `core/` already replaces
  them.
- `docs/features/byob-browser-control.md` and `do-design-audit.md`: browser
  driving and screenshot critique. Dropped in favour of the gap entry for a
  headless run-and-view capability.
- `docs/features/subconscious-memory.md` into a future memory doc: the most
  developed memory design. Dropped because memory is built last and the plan
  defers its design.
- `docs/features/bridge-prompt-injection-inspection.md` into the bridge docs:
  inbound text from a chat is untrusted data. Dropped as a guard; the
  principle (retrieved content carries no authority) is already in the README
  research table [7].

## Gaps

Mechanisms the demonstration or the plan needs that no archived doc covers.
Pass 2 writes these fresh, in the target named.

1. **Asking before building** (`docs/persona.md`, `docs/judgement-layer.md`).
   Valor asked no questions and needed two review rounds for three decisions.
   Persona text for when an example may stand for a whole requirement or a
   request names existing UI it may replace; and the judgement-tier ambiguity
   classifier before the first turn that the record recommends, which counts
   as a gate and so needs Tom's tap and a ledgered guard with an expiry.
   `pm-routing-collaboration.md` covers intent routing, not ambiguity.
2. **Feedback on a delivery** (`docs/architecture.md`). `core feedback`
   exists in code and no archived doc describes it: `feedback.given`, resume of
   the same session, a later delivery as a new `task.delivered`. Includes the
   two open defects: a failed turn must not consume pending feedback, and
   feedback must record its author and the permission it was written under.
3. **The attention log and attention cost** (`docs/mission.md`,
   `docs/architecture.md`). Mission item 6 and the Evidence section. Questions,
   answers, and feedback labelled by kind, per task, with whether each changed
   the outcome or the authority. The archived money ruling says attention is
   not a ledger axis; pass 2 has to settle that against the mission.
4. **Workspace provisioning and isolation** (`docs/architecture.md`,
   `docs/machine.md`). What the demonstration ran on: a clone with no later
   history, a local bare origin as the only remote, a scram-auth Postgres
   cluster of the workspace's own, an allowlisted environment with no tokens,
   an empty gh config, and a sandbox-exec profile with denies before allows.
   The archived sandbox plan covers Apple containers, not sandbox-exec, and
   pass 2 has to say which one the rebuild uses for which work.
5. **Kernel database trust** (`docs/data.md`). The demonstration's first
   incident: a machine cluster that trusted loopback could let a turn write
   ledger rows. Role separation for the kernel's database and its
   unreachability from a turn.
6. **Metering everything a turn calls** (`docs/architecture.md`). Every model
   call, Claude Code's side calls and subagents included, went through the
   gateway. The archived gateway plan meters a worker it owns; routing a
   harness's own calls through the gateway is not written down anywhere.
7. **The turn-to-kernel signal channel** (`docs/harnesses.md`). The `.valor/`
   files a turn writes to ask, deliver, or request an effect, and their move to
   `.valor/handled/<turn_id>/` once read.
8. **Approve, then release** (`docs/architecture.md`). The demonstration's
   push went requested, held, approved, released, performed. The archived
   surface plan has approvals but not the separate release step.
9. **Running and viewing the app** (`docs/harnesses.md` or a `tools/`
   section of `docs/architecture.md`). A headless browser in the workspace so
   Valor tests actual use. A capability, not a gate.
10. **Corrections reaching subagents** (`docs/architecture.md`). Correction 1
    rendered into every Brief the kernel sent; subagents Claude Code starts
    inside a turn were not checked. The plan says corrections reach every
    agent.
11. **Resume cost** (`docs/harnesses.md`). Each resumed turn re-sends the
    whole session (30,000 input tokens on the first call, 109,000 on the
    last). When to resume and when to start fresh with a summary.
12. **The emulator** (`docs/emulator.md`). No archived doc. The demonstration
    is its first case: a replayed historical request, a leak check of the base
    tree, the merged PR held out as the reference, and Tom's recorded answers
    as labels.
13. **The exemplar ledger** (`docs/architecture.md` or `docs/mission.md`).
    Work Tom loved and why, same store as corrections, distinct source class.
14. **Governance grants and guard expiry** (`docs/architecture.md`). The
    `governance_grant` field on the Brief, the guard ledger with incident,
    mission item, and ninety-day expiry, and the verifier's one boolean over
    every diff. Stated in the plan; described in no archived doc.
15. **The judgement port and its fallback** (`docs/judgement-layer.md`). A
    hosted Jev-class leg, an open-weight fallback behind the same port, and
    confidence gating to a human. `llm-task-taxonomy.md` covers task
    declaration; calibration and the fallback's placement on 16 GB are new.
16. **The machine's RAM plan** (`docs/machine.md`). What runs resident and what
    runs on demand, with RAM per component. The plan marks this doc new.
