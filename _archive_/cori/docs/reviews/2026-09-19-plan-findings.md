# Plan findings, 2026-09-19

Run at the reconcile step of the M0 planning stage. Eleven component plans were written in parallel against seams version 1; each recorded where the design documents contradict each other, the code, a spike, or the seams. This record gathers every finding with a disposition. **edit**: a design document edit the lead makes in one commit after the stage report. **resolved**: settled by seams version 2. **noted**: true, no document change. **code**: fixed by the plan that owns the file. **architect**: waits on an open question.

The plan that raised each finding is in brackets. Line numbers are as of commit 1123f53.

## Persistence and locks

| # | Finding | Disposition |
|---|---|---|
| 1 | Tech stack §3 line 91 claims outbox workers with `FOR UPDATE SKIP LOCKED`; line 83 gives `kernel_rw` INSERT and SELECT only; spike 01 showed `FOR UPDATE` needs UPDATE. The outbox cannot be an event table. At M0 one process polls `events` with a projection check and single-flight. [events 1, supervisor 1] | edit: §3 says the claim table is a mutable kernel-owned table, never an event table, and arrives with the second consumer process |
| 2 | Tech stack §3 summary row (line 32) marks the driver and migrations PROVISIONAL while the §3 assessment marks them LOCKED and the code commits to them. [events 2] | edit: promote the row |
| 3 | `hashtext` is 32-bit; two lock keys can collide. [events amendment 1] | resolved: `hashtextextended(key, 0)` everywhere, seams §0 |
| 4 | `reject_mutation()` names `events` in its message and is reused by every insert-only table. [spaces 3, tree 7] | code: events plan uses `TG_TABLE_NAME` |
| 5 | `tests/test_grants.py` enumerates exact grants and breaks for every plan that adds a table; nobody owns it. [spaces 2, tree 4] | resolved: retired by the spaces plan's catalog conformance test, seams §6, §7 |
| 6 | Tables without `space_id` (`request_bodies`, `gateway_tokens`) cannot be filtered by a policy. [spaces 4, gateway 6] | resolved: `context_ro` is granted only on tables with `space_id`, seams §6 |

## Tree, budgets, stop

| # | Finding | Disposition |
|---|---|---|
| 7 | Architecture §3.1 line 245: "Executors get write capability only when a Planner requests it on an APPROVED node"; tech stack §10 has no Planner at M0 and a class 1 code change. [tree 1, worker 1, supervisor 7] | edit: §3.1 reads "Executors get write capability only on an APPROVED node, issued by the kernel from the node's ceiling; a Planner requests it for the leaves it creates" |
| 8 | README says a call that crosses a budget still answers and the overrun is an incident; seams v1 `consume` refused past zero, leaving the ledger short of billed truth. [tree 2] | resolved: `consume(..., incurred=True)` and `budget.overrun`, seams §3.2 |
| 9 | Tech stack §4 "three things in one kernel transaction": the compute stop is a container operation and cannot sit inside a database transaction. [tree 3] | edit: "fence and revoke in one transaction, then the compute stop, and no replacement Brief until the stop is confirmed" |
| 10 | No durable record of a worktree snapshot between the Executor's terminal and the Verifier's Brief. [tree 5] | resolved: `snapshot.taken` event, seams §4 |
| 11 | `SandboxProfile.env` with secret values would land in `brief.issued` and `briefs`. [tree 6, sandbox 2] | resolved: names only, values read at `create()`, seams §1.9 |
| 12 | Tech stack §4.1 line 148 schematic says the verifier seat is "never newer" while line 157 admits a newer screened model. [tree 8; prereqs 15.8] | edit: the schematic comment |
| 13 | Tech stack §10 places standing budgets at M1; architecture §4 and seams §1.1 need one at M0 for the supervisor's turns and to open any objective. [supervisor 3] | edit: M0 row gains "per-space standing budget as a ledger node"; the morning brief stays at M1 |
| 14 | `StandingBudget.usd_per_day` is a float; seams make every quantity an integer. [supervisor 4] | noted: converted to `usd_micros` at load, seams §0 |

## Gateway and model calls

| # | Finding | Disposition |
|---|---|---|
| 15 | Tech stack §6 line 209 says the host-only network exists "so the sandbox can reach the model"; §5 runs the loop outside the sandbox and §2 gives sandboxes nothing. [gateway 1] | edit: §6 describes the network's shape; nothing inside a sandbox calls the gateway |
| 16 | Seams v1 `issue_token` was synchronous with no connection yet had to write a row. [gateway 2] | resolved: seams §3.9 |
| 17 | Port 8787 is the Cloudflare tunnel's ingress to the placeholder page; a gateway there would be served at `cori.yudame.dev`. [gateway 3] | edit: `infra/tunnel/README.md` names 8787 as reserved; the gateway binds 127.0.0.1:8788 |
| 18 | No document or file carries a price per token, yet §4 meters money. [gateway 4] | edit: §4.1 says the seat file carries `usd_per_mtok` per model; resolved in seams §7 |
| 19 | Tech stack §4 item 6 says "warns or refuses"; seams v1 listed both a refusal reason and a cache state. [gateway 5] | edit: §4 says warn (strip breakpoints, record `unstable`); resolved in seams §1.16 |
| 20 | Tech stack §8 counts tokens with the provider's endpoint; §2 keeps the key out of the kernel. [supervisor 5] | edit: §8 says through the gateway's `count_tokens`; resolved in seams §3.9 |
| 21 | Selection rule: "nothing in the kernel names a vendor"; the supervisor's own call speaks the one wire format the commit pass fixed. [supervisor 6] | noted: confined to one function |

## Worker, sandbox, verifier

| # | Finding | Disposition |
|---|---|---|
| 22 | Tech stack §5 gives the Scribe no way to reach `write_episode` and `propose_belief`. [worker 2] | resolved: two capability-gated tools, seams §5.2 |
| 23 | Seams v1: ids are kernel-minted, yet the adapter wrote `question` rows with an id. [worker 3] | resolved: `raise_question`, seams §2.4 |
| 24 | Seams v1: `run_brief` creates the sandbox but `run(brief)` could not learn the handle. [worker 4, worker 5] | resolved: `run(brief, handle)`, `runs.stop`, seams §2.1, §3.5 |
| 25 | Spike 08's `edit` logged old and new text. [worker 7] | resolved: hashes and lengths only, seams §5.2 |
| 26 | Tech stack §10 gates Executor prompts behind `pydantic-evals`, which prereqs item 5 did not install. [worker 8] | code: worker task 9 adds it to the dev group |
| 27 | apple/container 1.4.1 has no no-network mode; the plan proposed link-down inside the guest with a host ping. [sandbox 1] | architect answered 2026-09-19: the simplest option, host-only for every profile, no guards; edit tech stack §6's table (verify and scratch rows to host-only until the runtime offers a no-network mode) |
| 28 | `SandboxProfileName` in `brief.py` used by `sandbox.py`, which `brief.py` imports: a cycle. [sandbox 3] | resolved: seams §1.9 |
| 29 | Adapter had to mint `SnapshotId`. [sandbox 4] | resolved: `snapshot(h, *, snapshot_id)`, seams §2.2 |
| 30 | Snapshot before stop archives a disk still being written. [sandbox 5] | resolved: stop first, seams §3.5 |
| 31 | `CLAUDE.md` lists network `cori-egress`; no profile uses it. [sandbox 6] | edit: `CLAUDE.md` local setup |
| 32 | The Contract named no root while a space lists several. [sandbox 7] | resolved: `Contract.root`, seams §1.4 |
| 33 | Tech stack §10 M0 and M1 rows overlap on the Verifier: fresh sandbox and recorded verdicts read as M1 while architecture §5 and the index put them at M0. [verifier 1] | edit: M1 row keeps the screen threshold and audit labels; M0 row names the fresh `verify` sandbox and verdict rows |
| 34a | Architect answered 2026-09-19: the audience is a manifest field `Space.audience` (addresses, domains, source), sourced by default from the client's directory README in the work vault; seams §1.1. | edit: architecture §5 message row and §9 manifest |
| 34 | Architecture §5 line 354: "recipient in the space's allowed targets" cannot pass for any client address, since `allowed_targets` holds the person's own accounts. [verifier 2] | architect: open question 2; edit §5 after the answer |
| 35 | Seams v1 gave the Verifier criteria only; README and the message checklist need the contract. [verifier 3] | resolved: the `criteria` block carries the contract, seams §1.5 |
| 36 | Architecture §5 lists lint among code checks; no check name exists. [verifier 4] | noted: added when a client repository's configuration is known |
| 37 | `schemas/verifier_fixture.py` literals differ from the seams' names. [verifier 5] | code: verifier task 8 |
| 38 | The clean-brief fixture cites `ops/ci-failures-2026-q3.md` while the file is `ops-ci-failures-2026-q3.md`. [verifier 6] | code: verifier task 8 |
| 39 | Tech stack §5 says every class has `ask`; architecture §5 blindness argues the Verifier must not. [verifier question 2] | edit: §5 says the Verifier's Brief carries no `ask`, pending the architect's confirmation |

## Broker and spaces

| # | Finding | Disposition |
|---|---|---|
| 40 | Tech stack §2 says the broker never holds DB credentials; §4 and §7 have it write the ledger. The commit pass made it a module in the kernel process and the seams hand it a connection. [broker 1] | edit: §2 says the broker writes through a connection the kernel opens |
| 41 | Tech stack §10 M0 row omits the broker while the "cheap now" list needs the connector for the second objective. [broker 2] | edit: M0 row names the effect ledger, `push_branch`, and the connector |
| 42 | Five tools and a kernel door with no bridge to `request_effect`. [broker 3] | resolved: none at M0; `worker-effects` at M2, seams §1.10 |
| 43 | `broker/push_branch.py` accepts any `cori/*` branch; §7 says the objective's branch. [broker 4] | resolved: `cori/<objective_id>`, seams §1.10 |
| 44 | `broker/README.md` describes an External consent screen; prereqs item 12 recorded Internal. [broker 5] | code: broker task 10 |
| 45 | `PushBranch.source_dir` is a host path on a worker-visible action. [broker 6] | resolved: filled by the bridge, never the model, seams §1.10 |
| 46 | Architecture §9 routes at ingestion with `unassigned` for the rest; `broker/gmail.py` reads only what the rule selects. [spaces 1] | noted: rule-scoped at M0, seams §1.11 |
| 47 | Index says `spaces` depends on `events` only; `root_capabilities` consumes `schemas/capability.py`. [spaces 5] | edit: index dependency line |

## Memory and surface

| # | Finding | Disposition |
|---|---|---|
| 48 | `kernel/memory.py` mints `episode_id` with uuid4 and carries `salience` and `confidence` no document gives an episode. [memory 1, 2] | code: memory task 2 |
| 49 | Architecture §6: "no query spans partitions"; popoto's BM25 statistics are per model class, so rank (never membership) is coupled across spaces. [memory 3] | architect: open question 5 |
| 50 | Seams v1 §6 listed `Episode` and `Summary`; §1.14 made `summary` a kind. [memory 4] | resolved: one model, seams §6 |
| 51 | Architecture §3.5 has the Scribe write raw turns; §6 has episodic memory ingest raw turns with no extraction and gives deterministic records to the kernel. [memory 5] | edit: §3.5 says the kernel ingests turns and reports; the Scribe writes summaries and decisions |
| 52 | Tech stack §8's `embedding_model` column has no M0 writer. [memory 6] | noted: arrives with the vector stage at M1 |
| 53 | `ApprovalRecord.card_id` required while a self-approval shows no card. [surface 1] | resolved: the commit card is written unissued, seams §1.12 |
| 54 | No `CardKind` for a class 2 effect batch. [surface 2] | noted: for the M2 planner |
| 55 | `space.switched` carried one conversation id while a switch starts a new thread. [surface 3] | resolved: `new_conversation_id`, seams §4 |
| 56 | Nothing recorded where a CLI session came from. [surface 5] | resolved: `session.opened`, seams §4 |

## Round two (raised by the revisions against seams v2)

| # | Finding | Disposition |
|---|---|---|
| 60 | `profile_for` had no way to receive `Contract.root`. [tree 10, sandbox 8] | resolved: `root=` keyword, seams Round two |
| 61 | The supervisor's per-turn reservation and `SCRIBE_BUDGET` were both 20,000 tokens, so the Scribe's carve could never fit. [tree 11] | resolved: a Brief with `parent_brief: TurnId` carves from the standing node |
| 62 | `stop` took an objective id and could not reach an objective-less Scribe. [tree 12] | resolved: `stop_brief` |
| 63 | `tests/test_grants.py` would be red between the tree's build and the spaces' build. [tree 13] | resolved: the tree retires it |
| 64 | The `retry` reply on a `verification_failure` card needs an edge out of `FAILED`, which the tree's table made terminal. [supervisor 11] | resolved: `FAILED` to `APPROVED` by `approve` on that reply; architecture §8's "revival is an escalation" |
| 65 | Two claimed issuers of the `unknown_outcome` card. [supervisor 12] | resolved: the entry point, through a surface constructor |
| 66 | A Verdict whose criterion strings mismatch the contract landed after the transition. [verifier 7] | resolved: `validate_verdict_terminal` before `land_report` |
| 67 | Architect answered 2026-09-19: no self-approval cap beyond the standing allowance; a class 0 or 1 contract that does not fit yields a `budget_increase` card. | edit: architecture §2 Commit paragraph gains the sentence; seams §1.12 |

## Critique round, 2026-09-20

Eleven critique agents ran `do-plan-critique`, one per component plan, writing into the plan's Findings section. Ten verdicts were "return to author" and one (events) "ready to build" with two in-task fixes. Every plan was returned once at most, revised by its author with a one-line response under each finding, and merged; all eleven are `reconciled`. The critiques raised cross-plan gaps the lead ruled on in the seams' "Round two" section rather than leaving to the authors: the turn is two transactions so the gateway sees the reservation; `run_brief` takes the Brief from `delegate`'s caller and `brief.issued` is the record, never the trigger; `transition` returns the fenced Briefs; card expiry transitions nothing and cards charge the objective node or the standing node, never a brief; a turn token has no generation; kernel class 0 reads have nullable action ids; every brief-scoped payload carries `objective_id`; the kernel verdict's shape; the `VERIFYING` to `FAILED` reasons; `recover_verdicts` at start; a `grant` ledger kind. Findings from the round that touch design documents are already covered by edits 9, 13, and 39 above.

## Lead's own

| # | Finding | Disposition |
|---|---|---|
| 57 | Tech stack §6 lists six sandbox operations and no stop; §4 requires a compute stop with the disk retained. | edit: `stop` joins the Protocol in §6 |
| 58 | Tech stack §2 names the kernel API as an authenticated call; the import rule forbids workers importing the kernel; the seams make it a Protocol in `ports/`. | edit: §2 names `ports/kernel.py` beside the three Protocols |
| 59 | The index gives `workers/` to worker and `workers/verifier.py` to verifier. | edit: index says "workers/ except verifier.py" |

## Design document edits

Sixteen findings carry an **edit**: 1, 2, 7, 9, 12, 13, 15, 18, 19, 20, 33, 40, 41, 51, 57, 58, plus the three non-design files (17 tunnel README, 31 CLAUDE.md, 47 and 59 the plans index). Three more wait on the architect (27, 34, 39). The lead makes the sixteen in one commit after the stage report.

## Open questions for the architect

Each plan proceeds under the assumption stated in its Questions section.

Answered 2026-09-19 by the architect: 1 (2,000,000 tokens, 20 USD, 10 cards a day), 2 (a manifest `audience` field sourced from the client README), 3 (host-only everywhere, the simplest option, observe and react later), 4 (no cap beyond the standing allowance). Applied in seams §1.1, §1.9, §1.12, §2.2 and in the plans.

Still open, proceeding under its assumption:

5. **BM25 statistics per space in popoto.** Assumed: shared statistics are acceptable at M0; a change is in popoto, not here. [memory]
