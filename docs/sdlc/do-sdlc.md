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
