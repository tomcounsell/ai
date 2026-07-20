# SDLC Fork Artifact-Grounding Guards

**Status:** Shipped · **Issues:** #2124, #2026 · Extends the #2076 (#2026 umbrella) fork/supervisor hardening.

## Problem

A forked SDLC stage can hand a structurally-valid verdict back to the supervising
pipeline **without ever having produced the verifiable artifact that gives the
verdict its meaning**. Three live incidents in one failure family:

- **CRITIQUE (#2124 / PR #2121):** the `plan-reviewer` fork returned a complete-
  looking critique that was entirely fabricated — it reviewed a *different,
  nonexistent plan* and made zero grounded reads of the real plan. A fabricated
  `READY TO BUILD` (or a fabricated blocker) would have steered the pipeline on a
  lie; only a human manually checking tool-call counts caught it.
- **REVIEW (#2112 / PR #2134):** the forked `/do-pr-review` returned while its
  background judge subagents were still in flight; the children died with the fork,
  so no `## Review:` comment was ever posted and the verdict store was empty.
- **MERGE (#2026 / PR #2125):** a worktree HEAD left detached at a PR branch head
  meant a later docs-cascade `git push` to main carried the PR branch ancestry, and
  GitHub registered the push as the PR merge — no `gh pr merge` ever ran.

**Root cause pattern:** each stage gate trusted the fork's *report of completion*
rather than independently verifying the *artifact* the completion is supposed to have
produced.

## Guards

All guards are **fail-closed** (refuse + redirect to a stage re-dispatch, never a
silent pass) and additive (independently revertible).

### WS-A — CRITIQUE grounding leg

`tools/critique_roster_check.py::evaluate()` gains an optional `plan_path`/`plan_text`.
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
is byte-identical to the legacy fence-only gate (generic/foreign-repo safety).

### WS-B — worktree-cwd absolute plan path

`do-plan-critique` Plan Resolution canonicalizes `PLAN_PATH` to an absolute path rooted
at `git rev-parse --show-toplevel` before the existence check and before it is passed to
critics/SOURCE_FILES. A repo-root-relative plan path was unresolvable from a
`.claude/worktrees/agent-*` cwd — the critic then found nothing and could improvise a
critique of a nonexistent plan instead of failing loudly. The read now either succeeds
or the existence check exits 1.

### WS-C — CRITIQUE verdict-readability marker gate

`tools/sdlc_stage_marker.py::_critique_verdict_readable()` mirrors the REVIEW WS3c
(#2062) probe. The CRITIQUE `completed` marker is refused with a named
`CRITIQUE_VERDICT_MISSING` error (exit 1, fail-closed) when no readable substrate
CRITIQUE verdict exists. The idempotent already-completed path stays exit 0.

### WS-D — REVIEW artifact presence + in-turn-await

`tools/sdlc_stage_marker.py::_review_artifact_posted()` queries the PR for a formal
GitHub review OR a `## Review:` issue comment. The REVIEW `completed` marker now
requires **both** a readable verdict (WS3c) AND a verifiable posted artifact; a fork
that exited with judges in flight is refused with `REVIEW_ARTIFACT_MISSING`. The
`do-pr-review` skill body adds a hard rule: judge subagents run in the foreground and
MUST be awaited in-turn — the aggregate `## Review:` comment is posted and the verdict
recorded BEFORE the skill returns, never `run_in_background` with an un-awaited exit.

### WS-E — push-ancestry merge-bypass guard

`tools/push_ancestry_guard.py` (`sdlc-push-guard`) refuses a push to `refs/heads/main`
whose HEAD is descended from (contains) an OPEN PR branch head, unless a break-glass
`data/merge_authorized_{pr}` override (the same file the merge guard honors) authorizes
it. It reads the git pre-push stdin protocol and acts only on the `main` line.

- **Fail-closed** on an open-PR ancestry match (`PUSH_CARRIES_OPEN_PR_ANCESTRY`).
- **Fail-open** on a `gh` outage so an offline machine is not bricked — but a HEAD
  detached exactly at a non-`main` local branch tip (the #2026 shape) is refused
  locally without `gh` (`PUSH_DETACHED_AT_PR_BRANCH_TIP`).
- Scoped strictly to `refs/heads/main`; feature-branch pushes are never impeded.

The guard is wired into both the installed pre-push hook body
(`tools/doctor.py::install_pre_push_hook()`) and the `do-docs` cascade push step, so
protection does not depend on hook installation.

## Configuration

| Constant | Default | Override | Purpose |
|----------|---------|----------|---------|
| `MIN_GROUNDING_QUOTE_LEN` | 24 | env `MIN_GROUNDING_QUOTE_LEN` | Minimum verbatim-quote length for the WS-A grounding check. Provisional/tunable — bias LOW to accept real critiques. |

## Tests

- `tests/unit/test_do_plan_critique_barrier.py` — grounding-leg cases (grounded quote,
  section header, fenced-but-ungrounded, `--plan-path` omitted = legacy, unreadable plan
  fails closed).
- `tests/unit/test_sdlc_stage_marker.py` — CRITIQUE verdict gate + `_critique_verdict_readable`
  helper; REVIEW artifact-presence gate + `_review_artifact_posted` helper.
- `tests/unit/test_push_ancestry_guard.py` — ancestry refusal, authorization override,
  gh-outage fail-open, detached-HEAD local refusal.
