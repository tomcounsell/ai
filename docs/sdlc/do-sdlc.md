# do-sdlc addendum — this repo only
<!-- Do not duplicate content from the global do-sdlc skill (.claude/skills-global/do-sdlc/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

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

Both the Step 2 initial ensure and the Step 3d.6 between-stage continuity re-ensure run the same
`sdlc-tool` entry point (installed to `~/.local/bin` by `/update`, so no repo-relative path is
needed) and are cwd-independent per `docs/features/sdlc-tool-resolver.md`:

```bash
# Initial (Step 2)
sdlc-tool session-ensure --issue-number {issue_number} --issue-url "https://github.com/$SDLC_REPO/issues/{issue_number}"

# Between-stage continuity re-ensure (Step 3d.6) — always includes --reuse-run-id
sdlc-tool session-ensure --issue-number {issue_number} --reuse-run-id {run_id}
```

`SDLC_TARGET_REPO` (set once in Step 2) must stay exported for the lifetime of the loop — `sdlc-tool`
forces its own cwd to `~/src/ai` and relies on this env var to locate the target repo's plans and
worktree when the target repo is not `ai` itself.

### Step 3d.6 is loud, and its `run_id` is authoritative (issue #2675)

Neither invocation may be wrapped in anything that discards stderr or the exit code. Both used to
carry `2>/dev/null || true`, which routed every `LEASE_ABSENT`/`ISSUE_LOCKED` diagnostic to
`/dev/null` and threw the exit code away — so a run whose lease had lapsed kept its stale `run_id`,
had every subsequent write refused, and reported success. The same suppression is gone from the
stage-marker calls in this repo's `docs/sdlc/do-plan.md`.

Two obligations follow:

- **Adopt the returned identity.** On exit code 0, read `run_id` out of the JSON and use *that*
  value for every subsequent stage and `--run-id` flag. It can legitimately differ from the one you
  passed in: a re-ensure rebinds a lapsed lease, and a fresh contest mints a new id. This is also
  the value Step 5's `session-release` must carry.
- **Branch on the exit code.** A non-zero exit routes through the Step 2 three-way table. A
  *foreign-owner* `ISSUE_LOCKED` is the stop condition — the run has lost the issue. Everything else
  is recoverable: a self/hand-off payload is inherited, an orphaned lock waits out its TTL, and a
  transient broker error is surfaced and retried. Never turn a transient error into a pipeline
  abort; halting a healthy run on a Redis blip is worse than the bug this step catches.

Rebinding after a lapse is now backed by the durable, issue-keyed `_run_identities` anchor on the
`PipelineLedger` rather than by the session record alone — see
[SDLC Issue Ownership Lock](../features/sdlc-issue-ownership-lock.md) for the four reuse proofs.

## `session-release` invocation, this repo's path (Step 5)

Step 5's release runs through the same `sdlc-tool` entry point as `session-ensure` (installed to
`~/.local/bin` by `/update`; cwd-independent per `docs/features/sdlc-tool-resolver.md`):

```bash
sdlc-tool session-release --issue-number {issue_number} --run-id {run_id}
```

`{run_id}` is the value this run is carrying — the one Step 3d.6 passes as `--reuse-run-id`, rebound
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

Never let any of these change the outcome you reported in Step 4. The release is a courtesy that
shortens the *next* run's wait, not part of this run's result.
