# SDLC Fork Artifact-Grounding Guards

## Rationale

A forked SDLC stage can hand a structurally-valid verdict back to the supervising
pipeline **without ever having produced the verifiable artifact that gives the
verdict its meaning**. Each stage gate verifies the *artifact* the completion is
supposed to have produced, rather than trusting the fork's *report of completion*.
Three failure shapes motivate the guards:

- **CRITIQUE**: a `plan-reviewer` fork can return a fabricated verdict — reviewing
  a *different, nonexistent plan* with zero grounded reads of the real plan.
- **REVIEW**: a `/do-pr-review` fork can return while its background judge
  subagents are still in flight, so no `## Review:` comment is ever posted.
- **MERGE**: a worktree HEAD detached at a PR branch head means a later
  docs-cascade `git push` to main carries the PR branch ancestry, and GitHub
  registers the push as the PR merge with no `gh pr merge` ever running.

## Guards

All guards are **fail-closed** (refuse + redirect to a stage re-dispatch, never a
silent pass) and additive (independently revertible).

### WS-A — CRITIQUE grounding leg

`tools/critique_roster_check.py::evaluate()` accepts an optional `plan_path`/`plan_text`.
When supplied, a roster member counts as complete only if it passes BOTH the terminal
two-line fence AND a **grounding check**: after normalization (collapse whitespace,
casefold) and stripping the fence lines, the result file must share with the plan
EITHER a verbatim substring of at least `MIN_GROUNDING_QUOTE_LEN` characters
(provisional default **24**, env-overridable) OR a plan section header. A fabricated
critique of a nonexistent plan carries no substring that collides with the real plan
bytes, so it is reported in an `ungrounded` list and treated exactly like a missing
critic — bounded re-dispatch, then the loud `MAJOR REWORK (CRITIQUE INCOMPLETE)` STOP.

`CRITICS.md` makes a verbatim `GROUNDING:` citation a hard contract for every critic;
the `critique-roster-check --plan-path` gate is the enforcement. Omitting `--plan-path`
runs the fence-only check (generic/foreign-repo safety).

### WS-B — worktree-cwd absolute plan path

`do-plan-critique` Plan Resolution canonicalizes `PLAN_PATH` to an absolute path rooted
at `git rev-parse --show-toplevel` before the existence check and before it is passed to
critics/SOURCE_FILES. A repo-root-relative plan path is unresolvable from a
`.claude/worktrees/agent-*` cwd — the critic then finds nothing and can improvise a
critique of a nonexistent plan instead of failing loudly. The read either succeeds
or the existence check exits 1.

### WS-C — CRITIQUE verdict-readability marker gate

`tools/sdlc_stage_marker.py::_critique_verdict_readable()` mirrors the REVIEW WS3c
probe. The CRITIQUE `completed` marker is refused with a named
`CRITIQUE_VERDICT_MISSING` error (exit 1, fail-closed) when no readable substrate
CRITIQUE verdict exists. The idempotent already-completed path stays exit 0.

### WS-D — REVIEW artifact presence + in-turn-await

`tools/sdlc_stage_marker.py::_review_artifact_posted()` queries the PR for a formal
GitHub review OR a `## Review:` issue comment. The REVIEW `completed` marker
requires **both** a readable verdict (WS3c) AND a verifiable posted artifact; a fork
that exited with judges in flight is refused with `REVIEW_ARTIFACT_MISSING`. The
`do-pr-review` skill body carries a hard rule: judge subagents run in the foreground and
MUST be awaited in-turn — the aggregate `## Review:` comment is posted and the verdict
recorded BEFORE the skill returns, never `run_in_background` with an un-awaited exit.

### WS-E — push-ancestry merge-bypass guard

`tools/push_ancestry_guard.py` (`sdlc-push-guard`) refuses a push to `refs/heads/main`
whose HEAD is descended from (contains) an OPEN PR branch head, unless a break-glass
`data/merge_authorized_{pr}` override (the same file the merge guard honors) authorizes
it. It reads the git pre-push stdin protocol and acts only on the `main` line.

- **Fail-closed** on an open-PR ancestry match (`PUSH_CARRIES_OPEN_PR_ANCESTRY`).
- **Fail-open** on a `gh` outage so an offline machine is not bricked — but a HEAD
  detached exactly at a non-`main` local branch tip is refused
  locally without `gh` (`PUSH_DETACHED_AT_PR_BRANCH_TIP`).
- Scoped strictly to `refs/heads/main`; feature-branch pushes are never impeded.

The guard is wired into both the installed pre-push hook body
(`tools/doctor.py::install_pre_push_hook()`) and the `do-docs` cascade push step, so
protection does not depend on hook installation.

The two call sites declare the pushed SHA differently, and the difference is
load-bearing. The hook pipes git's pre-push stdin protocol, so the guard reads
the pushed SHA from it. The `do-docs` step has no such stdin and must pass
`--assume-head` to have HEAD judged. Absent stdin on its own means "git had
nothing to push" and exits 0 (#2800) — a bare `sdlc-push-guard` in an explicit
call site is a guard that cannot fire.

## Configuration

| Constant | Default | Override | Purpose |
|----------|---------|----------|---------|
| `MIN_GROUNDING_QUOTE_LEN` | 24 | env `MIN_GROUNDING_QUOTE_LEN` | Minimum verbatim-quote length for the WS-A grounding check. Provisional/tunable — bias LOW to accept real critiques. |

## Tests

- `tests/unit/test_do_plan_critique_barrier.py` — grounding-leg cases (grounded quote,
  section header, fenced-but-ungrounded, `--plan-path` omitted (fence-only fallback), unreadable plan
  fails closed).
- `tests/unit/test_sdlc_stage_marker.py` — CRITIQUE verdict gate + `_critique_verdict_readable`
  helper; REVIEW artifact-presence gate + `_review_artifact_posted` helper.
- `tests/unit/test_push_ancestry_guard.py` — ancestry refusal, authorization override,
  gh-outage fail-open, detached-HEAD local refusal.
