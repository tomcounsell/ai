# SDLC Local Supervision (`/do-sdlc`)

## Problem

`/sdlc` is a single-stage router by contract: it assesses state, dispatches ONE sub-skill, and returns ("NEVER loop" — Hard Rule 7). Pipeline *progression* is assigned to the eng session, which re-invokes `/sdlc` after each stage. That eng loop only exists for bridge-initiated sessions — in a local Claude Code session there is no eng session, so the human has to re-prompt "continue" after every stage.

## Solution

`/do-sdlc {issue|PR|description}` (`.claude/skills-global/do-sdlc/SKILL.md`) is the local stand-in for the bridge eng loop. It supervises the full pipeline in one invocation:

1. Resolves the issue (creates one via a `/do-issue` subagent if given a bare description) and runs `sdlc-tool session-ensure`.
2. Loops: `sdlc-tool next-skill` → `sdlc-tool dispatch record` → spawn a stage subagent that invokes the stage's `/do-*` skill → read its structured report → repeat.
3. Exits on merge confirmation (`gh pr view --json state` = `MERGED`), a `blocked` router decision (guard fired — surfaced to the human, never retried), or a 15-dispatch iteration cap.

The supervisor never decides dispatch itself — `sdlc-tool next-skill` (→ `agent.sdlc_router.decide_next_dispatch()`) remains the single source of dispatch truth, so all guards (G1–G9, including G4 oscillation) apply identically to local runs.

The `sdlc-local-{N}` anchor created by `session-ensure` in step 1 is a bookkeeping record, not a job for a worker to run: it is created with `is_ledger=True`, and every worker recovery/pickup guard skips past it rather than requeuing or executing it. This keeps a live standalone `python -m worker` process from mistaking the anchor for orphaned work and driving the same issue a second time in parallel with this local supervisor. See [Eng Session Architecture](eng-session-architecture.md#sdlc-local-session-is_ledger-non-executable-flag-issue-2042) for the full guard-site catalogue.

### Lease Heartbeat (issue #2446/#2451/#2714)

Unlike the worker, which keeps its issue lock alive via the in-process 60s `_tick_issue_lock_renewal` tick, this supervisor is a per-turn `claude -p` subprocess blocked inside a synchronous stage call -- it has no equivalent in-process renewer. `session-ensure` closes that gap on a fresh local mint by launching `tools/sdlc_lease_heartbeat.py` as a **detached** subprocess: a peek-first loop that extends the lease every `ISSUE_LOCK_TTL_SECONDS // 3` through a `renew_only=True` compare-and-set write (`touch_issue_lock`'s two minting branches are structurally skipped, so this renewer can never mint a lease nobody holds) and exits once the lease is no longer owned by its `run_id`.

The heartbeat's lifetime is anchored to evidence about its supervisor, not a fixed clock. `session-ensure` resolves the supervising `claude` process's identity -- `CLAUDE_PID` env var, then a `psutil` ancestry walk, then unresolved -- at mint time (the only moment the detached child is still inside that process tree) and hands `(pid, create_time)` to the heartbeat on its argv. The heartbeat polls that identity every `SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS` (default 60s), decoupled from the renew cadence, and on two consecutive positively-established death observations (`SDLC_SUPERVISOR_DEATH_CONFIRMATIONS`) it releases the lease and the supervised-run signal and exits -- a killed supervisor's lease is gone in roughly two minutes rather than waiting on a multi-hour ceiling. Every decision (start, each exit) is logged at INFO to `logs/sdlc_lease_heartbeat.log`.

When the supervisor resolves, the unchanged 4h `MAX_LIFETIME_SECONDS` backstop still applies -- the supervisor watch is the real death detector, so a live long-running BUILD stage is never cut off by the ceiling. When no supervisor identity can be resolved, a tighter 90-minute `UNSUPERVISED_MAX_LIFETIME_SECONDS` ceiling applies instead, and that exit stops renewing **without releasing** -- an unresolvable supervisor is not proof of death, so the lease is left to lapse on its own 1800s TTL (total exposure ≤2h on that path only).

If the lease still lapses (e.g. a Redis hiccup outlasting the heartbeat), the `AgentSession.owned_run_ids` accumulated set lets a stage fork carrying the prior `run_id` be recognized as self and inherit the re-minted identity instead of aborting. See [SDLC Run Self-Recognition](sdlc-run-self-recognition.md) for the full mechanism.

### Lease release on exit (issue #2714)

Nothing reclaims the lease when the supervisor loop simply stops -- there is no terminal transition on a HALT. Two release paths close that:

- **Tool-layer, path-agnostic.** A successful, non-idempotent `MERGE`/`completed` stage-marker write releases the lease and clears the supervised-run signal in `tools/sdlc_stage_marker.py` itself, so it fires for `/do-sdlc`, the `/sdlc` router, and worker-driven pipelines alike with zero skill cooperation.
- **`/do-sdlc` Step 5.** On the exits the tool layer cannot observe -- the REVIEW self-check HALT, a `blocked` router decision, or the iteration cap -- Step 5 invokes `sdlc-tool session-release --issue-number {n} --run-id {run_id}` after the Final Report. It is ownership-checked and best-effort: a wrong or already-released `run_id` is a safe no-op. See `docs/sdlc/do-sdlc.md` for the concrete invocation and reason-code table.

See [SDLC Issue Ownership Lock](sdlc-issue-ownership-lock.md) for the full renewer/release call-site catalogue.

## Refusal discrimination (issue #2452)

`session-ensure` returns three distinguishable refusals, and the supervisor must not collapse them all to "stop." The skill body (Step 2) encodes the three-way decision: `SUPERVISED_RUN_ACTIVE` is a **hand-off** — inherit the returned `owner_run_id` and continue (this fires on the supervisor's own live signal, the own-ghost case behind the 2026-07-29 `#2420`/`#2421`/`#2422` zero-PR incident); `ISSUE_LOCKED` + `orphaned_lock: true` is wait-then-re-ensure-and-rebind; only `ISSUE_LOCKED` + `orphaned_lock: false` is a genuine foreign owner and a stop. The decisive self-vs-rival term is `owner_run_id` membership in the run's held run_ids, not the issue-keyed `owner_session_id`. The lease can still lapse under a multi-minute stage — inheritance makes the run *recoverable*, not immune; the code-level TTL/heartbeat fix (the detached lease heartbeat above, plus the `owned_run_ids` self-recognition set) is tracked in #2446. The generic body carries this contract; the ai-repo-specific ledger-anchor diagnostics (why a running-looking `sdlc-local-*` on `dashboard.json` is expected, and that killing one destroys its `_meta` stage state) live in `docs/sdlc/do-sdlc.md` behind the skill-context probe.

## Stage→Model Parity

Each stage subagent is spawned with an explicit `model:` parameter mirroring the engineer persona's Stage→Model Dispatch Table (`config/personas/engineer.md`): opus for PLAN/CRITIQUE/REVIEW, sonnet for ISSUE/BUILD/TEST/PATCH/DOCS/MERGE. This is the local equivalent of the bridge eng session's `valor-session create --model` flag — without it, every stage would run on the interactive session's model.

## Stage-Marker Backfill

`/do-test` and `/do-patch` do not write their own stage markers. On the bridge, stage markers are written in-session by the Skill hooks — `agent/hooks/post_tool_use.py` calls `complete_stage()` (paired with `start_stage()` in `pre_tool_use.py`) when a stage's `/do-*` Skill tool finishes — so there is no longer a worker post-completion handler writing TEST/PATCH markers. `/do-sdlc` still backfills those two markers itself (`sdlc-tool stage-marker --stage TEST|PATCH --status completed|failed`) based on the subagent's report, because its subagents run the stage skills without those in-session hooks. All other stage skills self-mark (see [sdlc-stage-tracking.md](sdlc-stage-tracking.md)) and are not double-written.

## Relationship to `/sdlc`

| | `/sdlc` | `/do-sdlc` |
|---|---|---|
| Contract | dispatch ONE stage, return | loop until merge/blocked/cap |
| Progression | eng session re-invokes | skill re-invokes the router |
| Model assignment | eng session passes `--model` on child-session create | `model:` on the Agent tool |
| Execution | child eng `AgentSession`s via worker | subagents in the local session |

`/loop /sdlc {N}` remains a zero-code alternative that closes the progression gap but runs every stage on the session model (no opus/sonnet cost profile).

## Distribution

Lives in `.claude/skills-global/` and is hardlink-synced to `~/.claude/skills/` by `/update` (`scripts/update/hardlinks.py`) — available in any repo, like the other `do-*` stage skills it dispatches.
