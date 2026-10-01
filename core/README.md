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
- One typed settings module. Secrets stay in Keychain and the vault `.env`.
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

- `python -m core migrate` creates the `valor_kernel` role, the `valor_rebuild` database, and the schema in `core/schema.sql`. `python -m core --help` lists the rest: `start`, `run`, `answer`, `feedback`, `status`, `ledger`, `stop`, `pending`, `approve`, `release`, `correct`, `corrections`.
- `python -m core start INSTRUCTION --budget-usd N --ceiling act --workspace DIR --model M --harness-config FILE [--mode bare|clarify]` starts a task that works in a directory. `--mode` is recorded in `task.started`; `clarify`, an experimental arm, adds a section to the Brief asking Valor to spend its first turn inspecting and then write its material questions and intended approach to `.valor/question.md` without editing files. The kernel does not check that it did. `python -m core run TASK` runs its turns, each resuming the same harness session, until Valor asks a question (`question.asked`), delivers (`task.delivered`), the budget is spent, or the task is stopped, and prints one status line. `python -m core answer TASK "TEXT"` records Tom's answer (`question.answered`) and the next `run` opens with it. `python -m core feedback TASK "TEXT"` records Tom's feedback on a delivered task (`feedback.given`), which puts the task back to work: the next `run` resumes the same session with the feedback as its prompt, and a later delivery is a new `task.delivered`. Both carry provenance: `--by` names who wrote it (default `tom`) and `--role-played` marks a stand-in speaking for Tom. An answer or feedback is spent only by a turn that finishes; after a failed or stopped turn the next one opens with it again. A stopped task takes no feedback, and a task with an open question takes `answer` instead. `status` shows the latest delivery and the attention log: every question with its answer, and every piece of feedback, each labelled by kind and carrying its provenance (`by`, `role_played`). A turn reaches Tom and requests effects through files under `.valor/` in its workspace; `core/signals.py` holds that convention and the text every such turn's Brief carries; each signal file is moved to `.valor/handled/<turn_id>/` once read, so a later turn never reads it again.
- `core/__main__.py` is the composition root: `run` and `release` wire `harnesses/claude_code.py` and `tools/push_branch.py` into the kernel. No other module in `core/` imports outside it.
- `python -m core correct "TEXT"` records Tom's next correction in the ledger. Every turn's dispatched Brief renders the corrections in force from the ledger as the turn starts, and `turn.started` records that text.
- `scripts/kernel_smoke.py` runs the kernel end to end: a task with a budget, one `claude -p` turn through the gateway, a `propose` effect, an `act` effect held for Tom's approval, a second task stopped mid-turn, and the ledger.
- `scripts/session_smoke.py` runs a task over several turns from the command line on a toy repository: a question, Tom's answer, a delivery with a `push_branch` held for his tap, the release, and the corrections in every turn's Brief.
