# 99. Integration

| | |
|---|---|
| Slug | `integration` |
| Milestone | M0 |
| Status | reconciled |
| Seams version | 3 (2026-09-20) |
| Owns | `kernel/__main__.py`, `tests/e2e/`, `tests/chaos/` |
| Depends on | every component plan; built last |
| Written | 2026-09-19 by the lead at reconcile, against seams v2; cascaded 2026-09-20 to seams v3 (`docs/reviews/2026-09-20-budget-ruling.md`) |

**Cascade, 2026-09-20 (lead).** Seams v3 made the budget one dollar figure per objective, estimated by the supervisor with no standing budget and no ceiling, and named the effect classes `read`, `propose`, `act`. In this plan: the walkthrough runs at `propose`, the fixture manifest carries no budget, the first objective's log shows the estimate and its basis on the contract and the Executor's allocation as the estimate less the verify reserve, and the two objectives record estimate against actual in the ledger.

## Purpose

M0 proves "one objective end to end at `propose` inside one client space's sandbox, in code and in a drafted message; a stop leaves durable state intact and the sandbox's compute dead" (tech stack §10). The eleven component plans each prove their part in isolation. This plan runs the whole: the process entry point that composes them, the two first objectives as end-to-end tests against the real gateway, sandbox, and seat, the stop mid-run, the gateway kill rerun on the PydanticAI loop (tech stack §14 item 1), the cache measurement on the real loop (§14 item 3), and the first form of the chaos test (spike 02; tech stack §10 says CI at M1, so here it runs by hand and is wired to CI when it is green three times).

## What the documents say

- Tech stack §10, M0 row: what ships and what it proves, "budgets in money per objective with the estimate and the actual recorded". "Cheap now": M0's first objective is a code change in the client's repository at `propose` inside the sandbox, branch, edit, tests, commit, with the Verifier reading the kernel's test record; the second is a drafted reply to a client mail, also inside the sandbox.
- Architecture §4 (REVISED 2026-09-20): the budget is money per objective, estimated by the supervisor at framing with a one-sentence basis; no daily allowance, no ceiling on the estimate; overrun is `FAILED/budget_exhausted` and a card. The two objectives are the first entries in the ledger of estimate against actual.
- Tech stack §14, items 1 and 3: the gateway kill rerun (pass: zero further model calls; budget ledger matches provider usage for completed calls; cut calls charged reserved `max_tokens`) and the cache measurement (set the §1 caps from it).
- Architecture §1: the corrigibility test runs nightly; durable means events, artifacts on a retained disk, confirmed effects. Spike 02: 146 of 146 converged; a loop is only as lossless as its projection.
- Architecture §8 failure table: the rows this plan exercises end to end.
- Seams v3 §3.3 `serve`, §3.5 `run_brief`, §3.6 `verify_objective`, §3.7 the session and conversation functions, §7 ownership of `kernel/__main__.py`; "Version 3" throughout.
- Prereqs decisions table: first space PsyOptimal, roots, routing rule, effect ceiling `propose`, mail account, throwaway repository `yudame/cori-sandbox`.

## Design

`kernel/__main__.py` is the composition root and nothing else: read the Keychain, load the seat file and the manifests, open the database pool and Redis, start the gateway on loopback, build `AppleContainer`, `Door`, `PydanticAIWorker`, `CliSurface` with `approvals.open_terminal_session()`, `approvals.attach(surface)`, run `broker.reconcile_dangling` and issue an `unknown_outcome` card per `unknown` it returns, resume or open the conversation for `--space`, show pending cards, then `supervisor.serve(surface, worker, spaces, seats)`. The inbound dispatch table lives in `serve` (supervisor plan); this file only wires. Decided here: `python -m kernel --space psyoptimal` is the one way to start Cori at M0; a `cori` console script can alias it later.

The end-to-end tests drive the real process through a pty, as the surface plan's round-trip test does, and assert on the store, never on printed text. They run on this Mac only (`requires_container`, the Keychain key, the Gmail token) and skip in CI; CI runs each plan's own tests.

The first objective runs against `yudame/cori-sandbox`, a space manifest fixture `tests/e2e/spaces/sandbox.yaml` with `roots: [github.com/yudame/cori-sandbox]`, `max_effect_class: propose`, and no connector. Decided here: the walkthrough is on the throwaway repository, because the PsyOptimal repository's test suite may need services the sandbox lacks (sandbox plan, Risks) and the first real objective there is the architect's to pick. The second objective runs in the PsyOptimal manifest against a real mail from `psyoptimal.com` already in the inbox, and produces a draft file; nothing is sent.

## Tasks

1. **Entry point.** `kernel/__main__.py` as designed. *Accept:* `uv run python -m kernel --space psyoptimal` starts, prints the conversation line, and `psql -c "select type from events order by id"` ends with `session.opened`, `conversation.opened`; a second start resumes the same conversation id; `--space unassigned` refuses before any row.
2. **First objective: code change at `propose`.** `tests/e2e/test_code_change.py`: with the sandbox-space manifest, type "Add a `hello()` function to `hello.py` that returns the string `hello` and a test for it" into the pty. *Accept:* the event log shows `objective.opened` with a contract whose `budget.usd_micros > 0` and whose `basis` is non-empty, `approval.minted` (kind `self_approved`), `message.sent` carrying the budget as dollars and the basis, `objective.state_changed` to `APPROVED` then `RUNNING`, `brief.issued` for an Executor with capabilities `read@read, write@propose, bash@propose, ask@read` scoped to the space and a budget equal to the contract's less `VERIFY_RESERVE["code"]`, `report.landed`, `snapshot.taken`, `checks.recorded` with `tests` passed, `brief.issued` for a Verifier with `read@read, bash@read`, `verdict.recorded` with `pass`, `objective.state_changed` to `SUCCEEDED/verified`; `tree.project(objective_id).budget_consumed` is positive and at or below `contract.budget`, and no `budget_ledger` row names a turn id; the worktree under `<sandbox_root>/<space>/worktrees/<objective_id>` is on branch `cori/<objective_id>` with one commit; every `tool_log` `artifact_sha256` equals the file on the host; no `gateway_log` row for the objective precedes `checks.recorded`; the Verifier's `gateway_log` rows name the verifier seat; `effect_ledger` has no row for the objective (commit only, no push).
3. **Second objective: drafted reply at `propose`.** `tests/e2e/test_message_draft.py`: `spaces.poll_connectors` once, then type "Draft a reply to the latest mail from PsyOptimal saying I will send the proposal on Monday". *Accept:* `inbound.routed` for at least one item with `space = psyoptimal`; the contract's `artifact_kind` is `message` and `inputs` names the item; the Executor's `node` block carries the mail headers and body (from the `brief.issued` payload's slice, PROJECT only); the artifact `/work/reply.md` begins with `To:` and `Subject:`; `checks.recorded` has `recipient_allowed`, `length`, `no_operator_content` all passed; `verdict.recorded` is `pass`; nothing in `effect_ledger` above `read`; the contract's estimate and the node's `budget_consumed` are both readable, which is the second entry in the ledger of estimate against actual.
4. **Stop mid-run.** `tests/e2e/test_stop.py`: start the first objective with an Executor prompt fixture that runs a background counter loop in the sandbox, then call `tree.stop` from a second connection while it runs. *Accept:* `brief.stopped` then `brief.stop_confirmed` with `confirmed_dead_at >= killed_at`; the counter file on the host mount stops changing within one second of `killed_at`; `gateway_log` has zero `request` rows for the brief after the `token_revoked` row and any in-flight call has a `cut` row with `charged_reserved`; `tool_log` ends with `terminal/aborted` for the old generation; a new `delegate` on the node succeeds with generation 2 and a fresh container name; every file written before the stop is still on the mount.
5. **Gateway kill rerun on the loop** (tech stack §14 item 1). `tests/e2e/test_gateway_kill.py`: the spike 03 scenario on the real adapter: revoke during the second model call. *Accept:* the stream is cut within two upstream chunks; zero provider calls after revoke (the gateway's `request` count for the brief stops); for every completed call the `usage` row equals the SDK's reported usage field for field; the cut call's `output_tokens == max_tokens` and `charged_reserved`; `budget_ledger` `consume` rows for the brief sum to the `usage` rows.
6. **Cache measurement on the real loop** (tech stack §14 item 3). `tests/e2e/test_cache_measurement.py`: twenty consecutive supervisor turns in one conversation (ten utterances, the framing questions and answers they produce), then read `gateway_log`. *Accept:* the script prints total input, `cache_read_input_tokens`, and the read fraction; the fraction is at or above 60% (spike 04's bar); every `turn.rendered` manifest shows the first breakpoint at or beyond Opus 5's 512-token minimum; the numbers are pasted into `docs/plans/README.md` under "Measurements" and the architecture §1 caps are proposed from them as a finding for the lead.
7. **Chaos test, first form.** `tests/chaos/test_kill_loop.py`: the spike 02 harness against the real loop on the first objective with a scripted `FunctionModel` in place of the seat, so convergence is exact: spawn `python -m kernel`, SIGKILL at a random point in the first thirty seconds, restart, let it finish, repeat twenty times. *Accept:* every run reaches `SUCCEEDED` with one `report.landed`, one `verdict.recorded`, no duplicated `brief.issued` for the same generation, and `tree.project` equals the fold of the events (tree property); each trigger event has exactly one `turn.started` (supervisor S2); no `effect_ledger` intent is left dangling after `reconcile_dangling`. Wired into CI at M1 after three green runs by hand.
8. **The unknown-outcome card.** `tests/e2e/test_unknown_outcome.py`: with the fake target from the broker plan, leave one `intent` dangling and restart. *Accept:* `reconcile_dangling` returns one `unknown` and `kernel/__main__.py` issues one `unknown_outcome` card (through `approvals.unknown_outcome_card`) naming the effect id and key before `serve` starts (the silence architecture §7 forbids does not happen).

## Build order dependencies

Task 1 needs every plan built. Tasks 2 to 8 need task 1. Task 6 needs task 2's conversation shape. Task 7 needs tasks 2 and 4.

## Out of scope

- Nightly scheduling of the chaos test and CI wiring (M1).
- The PsyOptimal repository as the first code objective: the architect picks it once the walkthrough is green on `cori-sandbox`.
- Any proposal that leaves the space: `open_pr`, `post_message_draft`, the approval card path (M2). An overrun end to end: the `budget_increase` card is built and unit-tested by the supervisor and surface plans, and the e2e test that provokes one waits for an estimate that is wrong by more than luck.
- The passkey round trip (M3).

## Risks

- The PsyOptimal inbox may hold no recent mail on the day task 3 runs; the test then seeds one by sending from a `psyoptimal.com` address the architect controls, or skips with a reason.
- Task 6's 60% bar is spike 04's on synthetic slices; the real loop's tool definitions and the short M0 roll-up may land below it on the first run. That is the measurement, and the caps are set from what it says.
- The chaos test with the real seat is nondeterministic in prose; the scripted model is what makes convergence exact, and a second form with the real seat asserts on effects and ledger only.

## Findings

None yet; this plan is written before any component is built.
