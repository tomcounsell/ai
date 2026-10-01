# core

The kernel and the control loop. Authority lives here and nowhere else.

## Scope

- Objective tree and the task record, with a money budget conserved down the tree.
- Effect classes and the broker every effect passes through.
- Approvals: the record of Tom's tap on an `act`, with his literal message as provenance.
- The ledger: append-only, written by the kernel, never editable by an agent.
- Steering and lossless stop.
- The supervisor turn and the prompt it builds.
- The SDLC state machine as a typed state model. Stages are skills; the states, verdicts, and transitions are here.
- The judgement layer's task taxonomy and router, and `JudgementPort`.
- Ports that `bridges/`, `harnesses/`, and `memory/` conform to.
- One typed settings module. Secrets stay in Keychain and the vault `.env`, except the kernel databases' passwords, which live in a libpq password file outside both ([docs/machine.md](../docs/machine.md), Keychain).
- State lives in Postgres as JSONB documents plus an append-only events table.
- The corrections and exemplar streams in the ledger. `memory/` reads them; `core/` owns them.

Governed by [docs/architecture.md](../docs/architecture.md) (the kernel and control loop), [docs/sdlc-state-machine.md](../docs/sdlc-state-machine.md) (states and verdicts), [docs/judgement-layer.md](../docs/judgement-layer.md) (the judgement tier), and [docs/data.md](../docs/data.md) (storage), under [docs/mission.md](../docs/mission.md).

No LLM call decides authority here. A classifier decides what a thing is; the kernel decides what it may do.

## Imports

- May import: the standard library and third-party libraries. Nothing else in this repository.
- Imported by: every other code directory. `bridges/` and `memory/` import only the ports.

## Effect classes

Effect classes are defined in `core/`: `read` (no effect), `propose` (reversible: sandbox writes, branches, drafts, anything that can be withdrawn), and `act` (irreversible or money: merge, send, pay, deploy). The kernel enforces them: a child's ceiling never exceeds its parent's, and every `act` needs Tom, per action. Core holds the definitions and the enforcement, and performs no effect of its own outside the broker.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Vendor clients, harness wrappers, and comms I/O. Those are `tools/`, `harnesses/`, and `bridges/`.
- Persona text. That is `persona/`; core renders it.
- Stage logic for the SDLC (how to plan, build, review). Stages are skills.
- A model call that grants, widens, or refuses authority. Judgement classifies; the kernel decides.
- Relational schemas with a foreign-key lattice.

## Entry points

- `python -m core migrate` creates the `valor_kernel` role, the `valor_rebuild` database, the schema in `core/schema.sql`, the verdict enum's constraint, correction 1 (the governance paragraph, read from `CLAUDE.md`) when the ledger has none, and the four guards granted on 2026-10-01 when the ledger lacks them, then runs `secure-login`. `python -m core secure-login` (`core/credentials.py`) is the only code that touches a role's password, the kernel's password file (`settings.pg_passfile`), or the cluster's `pg_hba.conf`: it puts `scram-sha-256` rules for every role on the kernel databases ahead of every other rule, and is idempotent. `python -m core --help` lists the rest: `start`, `run`, `verdict`, `grant`, `answer`, `feedback`, `status`, `ledger`, `stop`, `pending`, `approve`, `release`, `correct`, `corrections`, `budget raise`, `backup`, `restore`, `settings`.
- `core/settings.py` is the one typed settings module: paths, Postgres, the backup disk, tunables, the model seats (`frontier`, `reviewer`, `light`, each a pinned id), and the price table, each price carrying the day it was checked. `python -m core settings` prints every value except the seats and prices as a shell assignment.
- `python -m core budget raise TASK N` adds N US dollars to a task's committed budget (`budget.raised`, with provenance); a stopped task takes none. Remaining money is one fold, `tasks.money`, which `status` and the gateway's reservations both use.
- `python -m core backup` dumps the kernel database to the backup disk with a manifest and keeps the newest 30 dumps; `python -m core restore DUMP` restores one into a scratch cluster and checks it against its manifest; `python -m core backup --plist` prints the launchd job (`core/backup.py`).
- `python -m core start INSTRUCTION --budget-usd N --ceiling act --workspace DIR --model M --harness-config FILE [--mode bare|clarify] [--target-branch B]` starts a task that works in a directory, recording where its merge goes before any turn can touch the workspace's config: origin's push URL as an absolute path, the target branch (the flag, or the branch origin's `HEAD` names; start refuses when it names none, and refuses a detached `HEAD`), and the head. `--mode` records the judge's verdict by hand until the judgement port runs it (`bare` is `precise`, `clarify` is `thin`, `leg: manual`, with the starter's provenance); without it the task waits in `judge`.
- The SDLC state machine (`docs/sdlc-state-machine.md`): `core/machine.py` holds `State`, `Check`, `VERDICTS`, `TRANSITIONS`, the total fold over a task's rows, the join, and the merge predicate, as pure code; `db.migrate` applies the verdict enum as a `CHECK` constraint generated from `VERDICTS`. `python -m core run TASK` is the router (`core/router.py`): it folds the ledger and runs the runner the composition root registered for the state, one run per task at a time (a session advisory lock; a second run says `already running`), until the task needs Tom or reaches a stage with no runner, and prints one status line. The working session (`core/session.py`) runs `clarify`, `plan`, `build`, and `patch`, each turn resuming the one before, its prompt the data that moved the task into its state and its Brief carrying that state's stage file from `skills/sdlc/`. `python -m core verdict TASK STAGE VERDICT` records by hand the verdict of a stage with no runner yet (`judge`, `critique`, `test`, `review`, `docs`), `leg: manual`, with provenance, and refuses a stage that has a runner (`core/verdicts.py`). The verdict that completes a join to `merge` writes `task.delivered`, and the kernel requests the merge (`merge`, `act`), held for Tom; the broker releases it only when the five terms of the merge predicate hold, checked with its intent in one transaction. `python -m core grant TASK INSTANCE --note T` is Tom's tap on one governance instance a review or docs verdict named (`guard.granted`; always his, never role-played); `migrate` seeds the four guards granted with the pipeline (`core/guards.py`). `python -m core answer TASK "TEXT"` answers the open question (`question.answered`), and the next run resumes in the state that asked. `python -m core feedback TASK "TEXT"` records Tom's feedback on a delivery in `merge` or `merged` (`feedback.given`), which sends the work to `patch` in the same session. Both carry provenance: `--by` names who wrote it (default `tom`) and `--role-played` marks a stand-in speaking for Tom. An answer, findings, or feedback is spent only by a turn that finishes. A stopped task takes no answer, feedback, verdict, or grant. A task started before the state machine (no `sdlc` on its `task.started`) folds read-only by the old kernel's precedence. `status` shows the state, plan, loops, candidate, checks, governance instances, the latest delivery, and the attention log: every question with its answer, every piece of feedback, every approval, budget raise, manual verdict, and grant, each labelled by kind and carrying its provenance (`by`, `via`, `at`, `role_played`; a field an older row never recorded reads as null), and `attention_counts`, which counts each kind apart. A turn reaches Tom and requests effects through files under `.valor/` in its workspace (`core/signals.py`; the text a turn reads is `skills/sdlc/channel.md`); each signal file is moved to `.valor/handled/<turn_id>/` once read.
- `core/__main__.py` is the composition root: `run`, `verdict`, and `release` wire `harnesses/claude_code.py`, the runners, and `tools/push_branch.py` (`push_branch` and `merge`) into the kernel. No other module in `core/` imports outside it.
- `python -m core correct "TEXT"` records Tom's next correction in the ledger. Every turn's dispatched Brief renders the corrections in force from the ledger as the turn starts, and `turn.started` records that text.
- With `VALOR_LIVE=1`, `tests/test_live_turn.py` runs real `claude -p` turns through the gateway (a metered turn, a stop mid-stream, a `propose` effect, and an `act` effect held until Tom approves from the command line), and `tests/test_live_session.py` runs a task through the state machine from the command line on a toy repository: a question, Tom's answer, a committed plan, a manual critique, a candidate with a `push_branch`, the three checks by hand, and the push and the merge each released after Tom's tap, with the corrections and the stage in every turn's Brief.
