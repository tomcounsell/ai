# Self-Healing Merge Gate

**Issue:** [#1155](https://github.com/tomcounsell/ai/issues/1155)
**Status:** Shipped
**Related features:** [Plan Checkbox Writers](plan-checkbox-writers.md) ·
[PM SDLC Decision Rules](pm-sdlc-decision-rules.md) ·
[Pipeline State Machine](pipeline-state-machine.md)

> **Partially removed by #2376 (2026-07-25):** hardenings 4 (baseline decay)
> and the safe-shape review exemption were deleted along with the merge-time
> full-suite gate and its shape/baseline ecosystem. The merge gate now runs
> only deterministic checks; sections below are annotated where affected.

## Why

An Eng session that reaches the MERGE stage on an approved, mergeable PR should
be able to finish the pipeline without pinging a human. Prior to this work,
seven distinct gate conditions halted the pipeline with a "report and stop"
output — each one was a well-understood failure mode with a well-understood
fix, but the Eng session had no documented pattern for self-resolution. Routine gate
mechanics were escalating to humans instead of self-healing.

This feature either eliminates each friction point at the source or
documents it as a senior-dev playbook the Eng session can follow. Human intervention
is reserved for genuinely unique architect-level judgement — not gate
mechanics.

## What the seven hardenings do

### 1. Durable-signal fallback

`PipelineStateMachine.derive_from_durable_signals(session)` in
`agent/pipeline_state.py` derives PLAN/BUILD/TEST/REVIEW/DOCS completion
from durable artifacts when Redis `stage_states` is cold:

- **PLAN** — plan file on `origin/session/{slug}` (falls back to
  `origin/main`) with a `tracking:` URL.
- **BUILD** — open PR on `session/{slug}` from `gh pr list`.
- **TEST** — `gh pr view --json statusCheckRollup` all green.
- **REVIEW** — latest `## Review:` comment starts with
  `## Review: Approved`, filtered by commit-SHA (see item 2 below).
- **DOCS** — **tri-OR**: `docs/` files in PR diff, OR every
  `## Documentation` checkbox ticked, OR review comment body mentions
  `docs (complete|updated|verified|reviewed)` case-insensitively.

The fallback activates **only** when `get_display_progress()` returns
empty or all-`pending`. Populated Redis state always wins. Any subprocess
error (`gh`, `git`) is caught at the top level; the function never
raises and returns a dict (possibly empty) rather than propagating.

### 2. Commit-SHA-aware review filter

`.claude/commands/do-merge.md` now reads the PR's latest commit
`committer.date` via `gh api repos/$REPO/pulls/$PR/commits --jq
'.[-1].commit.committer.date'` and filters `## Review:` issue comments to
those created at-or-after that date. A stale `Approved` before a
force-push no longer passes; a stale `Changes Requested` after a
re-approval no longer blocks.

On API failure (empty `LATEST_COMMIT_DATE`), the gate fails with a
specific diagnostic (`GATES_FAILED: could not fetch latest commit date
for review-filter`) rather than silently regressing to unfiltered
behavior — silent fallback defeats the class of bug the filter prevents.

**Safe-shape exemption removed (#1283, removed by #2376).** A narrow
exemption once re-admitted a prior `## Review: Approved` for docs-only /
lockfile-only follow-up diffs; it was deleted with the shape classifier.
The filter now applies uniformly: any post-approval commit needs a fresh
review or a matching `<!-- REVIEW_CONTEXT head_sha=... -->` trailer.

### 3. `uv lock --locked` pre-commit phase

`.githooks/pre-commit` gains a phase 1.5 between ruff and secret scan:

- **Short-circuit:** skip entirely when neither `pyproject.toml` nor
  `uv.lock` is staged (fast path for unrelated commits).
- **No-uv skip:** if `uv` is not on `PATH`, emit a one-line warning and
  continue — machines without `uv` still get a working hook.
- **Block on drift:** `uv lock --locked` is read-only; non-zero exit
  means regeneration would produce changes. Block with the fix command
  `uv lock && git add uv.lock && git commit --amend --no-edit`.

Drift now surfaces at commit time, not merge time.

### 4. Baseline decay + quarantine hint (removed by #2376)

Removed with the merge-time full-suite gate: `scripts/baseline_gate.py`,
its decay/flake-tracker machinery, and the post-merge baseline update no
longer exist. Test-failure classification lives in the TEST stage
(`baseline-verifier`) and the nightly regression run.

### 5. Engineer gate-recovery rule

`config/personas/engineer.md` gains a `## Gate-Recovery Behavior`
section after Rule 5 (Rule 5 itself is unchanged). The section:

- Enumerates blocker categories (`PIPELINE_STATE`,
  `PARTIAL_PIPELINE_STATE`, `REVIEW_COMMENT`,
  `LOCKFILE`, `MERGE_CONFLICT`; `FULL_SUITE` was retired with the
  merge-time test gate, #2376).
- Maps each category to a remediation (e.g.
  `REVIEW_COMMENT → /do-pr-review`, `LOCKFILE → uv lock && commit`).
- States the re-dispatch rule: after any remediation, re-dispatch
  `/do-merge {pr}`.
- Invokes G4 convergence (3-dispatch cap) as the escalation boundary.

### 6. Merge-troubleshooting playbook

`docs/sdlc/merge-troubleshooting.md` is the command-first reference the
PM consults for each blocker category. Six sections: Merge Conflict,
G4 Oscillation, Stale Review, Lockfile Drift, Flake False Regression,
**Partial Pipeline State**. Each section: Symptom → Diagnose →
Remediate → Verify → cross-link to the relevant
`.claude/commands/do-merge.md` section.

### 7. Merge-guard tokeniser

`.claude/hooks/validators/validate_merge_guard.py` gains an
`_extract_executed_commands(command)` tokenizer that identifies actual
command positions vs. quoted strings, heredoc bodies, and backtick
substitutions. The `_MERGE_CMD_RE` check runs only against actual
command spans — diagnostic text like
`git commit -m "references gh pr merge"` no longer self-blocks.

**Fail-closed contract** (mandatory): on any tokenizer exception OR an
ambiguous parse (empty span list on non-empty input), the guard falls
back to applying `_MERGE_CMD_RE` against the full command string (the
old bare-match behavior). This preserves the block on direct merges
even if the tokenizer is broken. Covered by
`test_tokenizer_failure_fails_closed` in
`tests/unit/test_validate_merge_guard.py`.

## How the pieces compose

**Typical autonomous recovery.** An Eng session dispatches `/do-merge` on
an approved PR. The gate reports `LOCKFILE: FAIL`. The Eng session reads the
Gate-Recovery Behavior section (item 5), looks up the LOCKFILE row, and
consults `docs/sdlc/merge-troubleshooting.md` (item 6) for the exact
`uv lock && git add uv.lock && commit && push` recipe. The Eng session
dispatches a child session to run it, then re-dispatches `/do-merge`.
The gate now passes the lockfile check; any remaining gates use the
durable-signal fallback (item 1) or the commit-SHA filter (item 2)
as needed. Item 3 would have caught this drift at commit time; item 4
advises on flake quarantine if the full suite surfaces repeat flakes.
Item 7 stops the guard from self-blocking on quoted text in any
intermediate commits.

**G4 convergence.** If the same category recurs 3 times, the Eng session
escalates to the human per the existing G4 oscillation guard. Item 5
references G4 explicitly; items 1–4 and 7 do NOT loop (they are
one-shot fixes).

## Verifying it works

- `pytest tests/unit/test_pipeline_state_machine.py -k derive` — tests
  for the durable-signal fallback (item 1).
- `pytest tests/unit/test_do_merge_review_filter.py` — stale-review
  filter tests (item 2).
- `pytest tests/unit/test_pre_commit_hook.py` — uv-lock phase tests
  (item 3).
- `pytest tests/unit/test_engineer_persona_guards.py -k
  TestGateRecoveryBehavior` — persona section tests (item 5).
- `pytest tests/unit/test_engineer_persona_guards.py -k
  TestMergeTroubleshootingDoc` — playbook structure tests (item 6).
- `pytest tests/unit/test_validate_merge_guard.py` — tokeniser tests
  (item 7).

## Out of scope (from the plan's "No-Gos" section)

- Rewriting `PipelineStateMachine` to use durable signals as the primary
  path — Redis remains the fast path; this is a fallback only.
- Adding `pytest-retry`/`pytest-rerunfailures` at the merge gate —
  `/do-test`'s retry layer is sufficient.
- Post-merge deploy orchestration — separate skill (`/do-deploy`).
- Reintroducing "human approval" framing to the merge-guard block
  message — hotfix `1d67d81e` deliberately removed it.
