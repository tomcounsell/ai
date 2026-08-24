# do-sdlc addendum — this repo only
<!-- Do not duplicate content from the global do-sdlc skill (.claude/skills-global/do-sdlc/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## `/sdlc` is this repo's router-mode entry point

The generic body describes "router mode" abstractly. In this repo it is reached through `/sdlc`, a
thin `context: fork` shim (`.claude/skills/sdlc/SKILL.md`) that reads the global body's Steps 1–4
(resolve → session-ensure → assess → dispatch ONE), executes them once, and returns.

| | `/sdlc` | `/do-sdlc` |
|---|---|---|
| Contract | dispatch ONE stage, return | loop until merge/blocked |
| Progression | the eng session re-invokes | the skill re-invokes the router itself |
| Model assignment | eng session passes `--model` when spawning dev sessions | supervisor passes `model:` on the Agent tool |
| Where it runs | bridge eng sessions + local | local Claude Code sessions |

Both entry points consume the same router (`sdlc-tool next-skill` →
`agent.sdlc_router.decide_next_dispatch()`) and the same stored stage state — there is exactly one
source of dispatch truth. The "supervising session" the global body defers to is the bridge eng
session; when one already owns the issue, supervisor mode is redundant and work drives through
`/sdlc` one stage at a time.

## `{target_repo_path}` is `SDLC_TARGET_REPO`

The global body writes `{target_repo_path}` wherever it needs the target repo's filesystem path.
In this repo that is **`SDLC_TARGET_REPO`**, distinct from `SDLC_REPO` (the GitHub `org/repo`
slug). `sdlc-tool` forces its own cwd to `~/src/ai`, so this env var is how it locates the target
repo's plans and worktree when the target repo is not `ai` itself. Set it once in Step 2 and keep
it exported for the lifetime of the loop:

```bash
SDLC_TARGET_REPO=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
export SDLC_TARGET_REPO
```

## Guard implementation, this repo's sources

The global body carries the G1–G9 table for interpreting a `blocked` decision but defers the
implementation here. Canonical implementation: `agent.sdlc_router.decide_next_dispatch()`. The
parity test `tests/unit/test_sdlc_skill_md_parity.py` keeps the table in sync with the Python
rules in both directions — a guard in `GUARDS` with no table row fails, and vice versa.

- **G8 makes no live calls.** Live verification of claimed stage artifacts happens in the
  next-skill context-assembly path (`tools/sdlc_next_skill.py`), which sets
  `context["stage_artifacts_verified"]` / `context["unverified_stage"]`; G8
  (`agent.sdlc_router.guard_g8_artifact_verification`) only reads those flags. This keeps
  `agent/sdlc_router.py` import-free of `tools/` (see
  `tests/unit/test_architectural_constraints.py`). Absent/unset/`True` is a no-op, and a stage
  whose claimed artifact has no resolvable identifier (e.g. no recorded `pr_number`) is skipped
  rather than reported as a mismatch (#2757). The verifier's `git` checks run with
  `cwd=_target_repo_cwd()` (`SDLC_TARGET_REPO`) so they inspect the SDLC target repo, not the
  process cwd — see the cwd-threading contract (#2078) in
  [SDLC Router Oscillation Guard](../features/sdlc-router-oscillation-guard.md).
- **G9 (#2796).** Its non-conflicting step-aside set (`CLEAN`, `HAS_HOOKS`, `UNSTABLE`, `BLOCKED`,
  `BEHIND`) mirrors `/do-pr-review`'s own preflight decision table exactly, so a PR that is merely
  `BEHIND` or `UNSTABLE` is never wrongly told it has merge conflicts.
  `tools/sdlc_stage_query.py::_fetch_pr_merge_state` retries once on a transient `UNKNOWN` read
  before G9 (or G6) ever sees the value.
- **G7 precedes G5 and G6 in list order (#1871).** G5's cached READY-TO-BUILD fast path does not
  itself read `plan_revising`, so G7 must run first to intercept a stale-hash cache hit while a
  revision is pending. The "an already-mergeable PR is never blocked by a stale `plan_revising`
  flag" guarantee comes from G7's own Gate 1 (`pr_number` set → return `None`), not from list
  position relative to G6.
- **Convergence latch — `revision_applied_at` (#1760).** `/do-plan` Phase 4 Step 2a writes the
  event-scoped `revision_applied_at: <ISO-8601 UTC>` in the SAME step as `revision_applied: true`
  (never a follow-up edit). `agent.sdlc_router._critique_verdict_is_stale()` uses it as a latch;
  absent/unparseable values leave the latch inert. **With-concerns scope (#2787):** on the
  `READY TO BUILD (with concerns)` path a bounded branch runs ahead of the latch — below
  `MAX_CONCERN_RECRITIQUE_ROUNDS` the concern-closing revision re-stales the verdict so row 2b
  re-critiques it; at the bound the latch engages and row 4c builds with the residual concerns
  recorded as accepted. See [With-Concerns Re-Critique Gate](../features/with-concerns-recritique-gate.md)
  and [SDLC Pipeline — Convergence Latch](../features/sdlc-pipeline.md#convergence-latch-revision_applied_at-issue-1760).
- **Skipped stages (#2577).** `"skipped"` is reachable only for PLAN and CRITIQUE, and means the
  pipeline never dispatched that stage because the issue has no plan document. See
  [Off-Pipeline Merge Path](../features/off-pipeline-merge-path.md).
- **`ISSUE_LOCK_TTL_SECONDS`** defaults to 30 min (the happy path releases the lease immediately at
  run end) and `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS` to 20 min; `_lock_owner_is_live` keys on the
  `renewed_at` stamp, not the lease payload's `pid` (#2620). `dispatch record`'s CLI wrapper
  surfaces contention differently from the other tools: on a failed write it peeks the lock and
  merges `reason`/`owner_run_id`/`owner_session_id` into its existing
  `{"ok": false, "history_length": N}` result, never a `blocked` shape — see `_cli_record()` in
  `tools/sdlc_dispatch.py`.
- **`agent.sdlc_router.record_dispatch()` and `tools.stage_states_helpers.update_stage_states()`**
  are what `sdlc-tool dispatch record` wraps. Never call them directly from a shell or skill
  script.

## Stage→Model, and where the table is mirrored

The global body's Stage→Model dispatch table mirrors the engineer persona's table
(`config/personas/engineer.md`) — the local equivalent of `valor-session create --model`. Keep the
two in sync when either changes.

## Ledger-anchor diagnostic tooling (issue #2452)

The generic body names `sdlc-local-{N}` as a non-executable ledger anchor (`is_ledger`, #2042) that
carries the run's `_meta` stage state and must not be killed. This repo's own diagnostic tooling
disagrees on whether that anchor is even visible, and that disagreement is exactly what caused two
anchors to be killed on 2026-07-29 (#2420/#2421/#2422 incident):

- **`python -m tools.valor_session list`** (or the `valor-session list` CLI) **filters ledger anchors
  out of its default view.** A `sdlc-local-{N}` row will NOT appear there even while it is the live,
  correct anchor for an in-progress run.
- **`curl -s localhost:8500/dashboard.json`** (the web UI dashboard state) **does display them.** A
  `sdlc-local-{N}` session showing `status=running` on the dashboard is the **expected**, permanent
  steady state of a ledger anchor — not evidence of a stuck or rogue pipeline, and not grounds to kill
  it. If a `sdlc-local-{N}` row looks "stuck" on the dashboard, cross-check `sdlc-tool stage-query
  --issue-number {N}` for real progress (stage markers, `_meta`) before concluding anything is wrong;
  do not act on the dashboard's `status=running` alone.

**Consequence of killing it anyway:** a `sdlc-local-{N}` anchor is an `AgentSession` record. Killing it
deletes that record, and the `_meta` stage state (dispatch history, recorded verdicts, PR number) it
carries is deleted with it — there is no separate persistence layer for that state. A killed anchor
cannot be un-killed; a fresh `session-ensure` for the same issue mints a new session and starts the
stage-tracking history over.

## `session-ensure` re-ensure invocations, this repo's paths

Both the Step 2 initial ensure and the Step 5d.6 between-stage continuity re-ensure run the same
`sdlc-tool` entry point (installed to `~/.local/bin` by `/update`, so no repo-relative path is
needed) and are cwd-independent per `docs/features/sdlc-tool-resolver.md`:

```bash
# Initial (Step 2)
sdlc-tool session-ensure --issue-number {issue_number} --issue-url "https://github.com/$SDLC_REPO/issues/{issue_number}"

# Between-stage continuity re-ensure (Step 5d.6) — always includes --reuse-run-id
sdlc-tool session-ensure --issue-number {issue_number} --reuse-run-id {run_id}
```

### Step 5d.6 is loud, and its `run_id` is authoritative (issue #2675)

Neither invocation may be wrapped in anything that discards stderr. Both used to carry
`2>/dev/null || true`, which routed every `LEASE_ABSENT`/`ISSUE_LOCKED` diagnostic to `/dev/null` —
so a run whose lease had lapsed kept its stale `run_id`, had every subsequent write refused, and
reported success. The same suppression is gone from the stage-marker calls in this repo's
`docs/sdlc/do-plan.md`.

Two obligations follow:

- **Adopt the returned identity.** On an unblocked payload, read `run_id` out of the JSON and use
  *that* value for every subsequent stage and `--run-id` flag. It can legitimately differ from the
  one you passed in: a re-ensure rebinds a lapsed lease, and a fresh contest mints a new id. This is
  also the value Step 7's `session-release` must carry.
- **Branch on the payload, not the exit code.** `session-ensure` exits 0 on every outcome it can
  report (`tools/sdlc_session_ensure.py::main` prints the result and returns) and signals refusal as
  `{"blocked": true, "reason": ...}`, so an exit-code check would never fire on one. The exception
  is a wrapper or usage error — a bad subcommand, an unset `AI_REPO_ROOT`, an argparse rejection —
  which exits non-zero with **no payload**; that is a broken install, so stop and report rather than
  classifying empty stdout as transient and retrying. A `blocked`
  payload routes through the Step 2 three-way table. A *foreign-owner* `ISSUE_LOCKED` is the stop
  condition — the run has lost the issue. Everything else is recoverable: a self/hand-off payload is
  inherited, an orphaned lock waits out its TTL, and a transient broker error is surfaced and
  retried. Never turn a transient error into a pipeline abort; halting a healthy run on a Redis blip
  is worse than the bug this step catches.

The disposition is per-tool and does not generalize: `stage-marker` genuinely does exit non-zero on
an ownership refusal (`RUN_ID_REQUIRED`, `ISSUE_LOCKED`), which is why the stage-marker guidance in
`docs/sdlc/do-plan.md` and `.claude/skills-global/do-sdlc/SKILL.md` Step 5d *is* written against
the exit code. See [SDLC Tool Resolver](../features/sdlc-tool-resolver.md) for the loud/best-effort
split across the whole `sdlc-tool` surface.

Rebinding after a lapse is now backed by the durable, issue-keyed `_run_identities` anchor on the
`PipelineLedger` rather than by the session record alone — see
[SDLC Issue Ownership Lock](../features/sdlc-issue-ownership-lock.md) for the four reuse proofs.

## `session-release` invocation, this repo's path (Step 7)

Step 7's release runs through the same `sdlc-tool` entry point as `session-ensure` (installed to
`~/.local/bin` by `/update`; cwd-independent per `docs/features/sdlc-tool-resolver.md`):

```bash
sdlc-tool session-release --issue-number {issue_number} --run-id {run_id}
```

`{run_id}` is the value this run is carrying — the one Step 5d.6 passes as `--reuse-run-id`, rebound
if any refusal in the Step 2 table made you re-ensure. Pass the *current* value; a stale one is
refused as a foreign release and leaves the lease held.

Output is typed JSON, exit code always 0:

| `reason` | Meaning |
|---|---|
| `released` | The lease and the supervised-run signal are both gone. |
| `no_lease` | Nothing to release — already released (the merged path's tool-layer leg got there first) or the lease lapsed. Not an error. |
| `not_owner` | A live lease is held by a *different* run; nothing was touched. Report this — it means the `run_id` you carried is not the one that owns this issue. |
| `missing_args` | You passed an empty/absent issue number or `run_id`. |
| `error` | The Redis substrate raised; the lease may still be held and will lapse on its own TTL. |

Never let any of these change the outcome you reported in Step 6. The release is a courtesy that
shortens the *next* run's wait, not part of this run's result.
