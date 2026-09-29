# do-patch addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-patch/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Cross-Repo `gh` Targeting

For cross-project work, the `GH_REPO` environment variable is set automatically
by `sdk_client.py`. The `gh` CLI natively respects it, so all `gh` commands
target the correct repository — no `--repo` flags or manual parsing needed.

## Branch → Issue → Plan-Doc Resolution (Build Context Recovery)

To recover the plan when the caller didn't pass a path: the branch name is the
lane's `{slug}` (`session/{slug}`), but the plan document is **not**
necessarily at `docs/plans/{slug}.md` — a human-named plan may track an
issue-derived lane, or the reverse (see
[`docs/features/sdlc-lane-identity.md`](../features/sdlc-lane-identity.md)).
Resolve the plan through `find_plan_path`, keyed on the issue number, not by
guessing a filename from the branch:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
PLAN_PATH=$(python -c "
from tools.lane_identity import find_plan_path
p = find_plan_path($ISSUE_NUMBER)
print(p or '')
")
```

## Trace & Verify Reference

The full Trace & Verify protocol reference lives at
`docs/features/trace-and-verify.md`.

## Test and Lint Commands

Tests: `scripts/pytest-clean.sh` (never bare `pytest`). Run the affected test
files first (`scripts/pytest-clean.sh tests/unit/test_<x>.py -x -q`) to surface
scope issues fast, then the full `tests/unit/` suite (about 20 minutes).

Lint: `python -m ruff check .` and `python -m ruff format --check .`; never
`black`. The pre-commit hook auto-fixes via `ruff format` + `ruff check --fix`,
and the PostToolUse `format_file.py` hook formats each file after Write/Edit, so
do not fix whitespace or import order by hand. If the hook fails on a
non-fixable lint error, fix that specific error and re-commit.

## Plan-Checkbox Sync (Step 3.5 mechanism)

This repo bundles a criterion tick into the fix commit. Read the builder's
reported `criterion_addressed` from Step 2, then tick it in the SAME `git add -A`
as the code fix:

```bash
# PLAN_PATH comes from find_plan_path above, never from the branch name.
TICK_SUFFIX=""
if [ -n "$CRITERION_ADDRESSED" ] && [ "$CRITERION_ADDRESSED" != "null" ]; then
  if "${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.plan_checkbox_writer tick "$PLAN_PATH" --criterion "$CRITERION_ADDRESSED"; then
    TICK_SUFFIX=" — addresses \"$CRITERION_ADDRESSED\""
  else
    # NON-FATAL: MATCH_AMBIGUOUS / MATCH_NOT_FOUND. The commit still happens
    # (code change only); the next /do-pr-review round reconciles via tick/untick.
    echo "WARN: plan_checkbox_writer failed for criterion: $CRITERION_ADDRESSED" >&2
  fi
fi
git add -A
git commit -m "fix(#${SDLC_ISSUE_NUMBER}): ${SUMMARY}${TICK_SUFFIX}"
git push origin "HEAD:${BRANCH}"
```

The single-commit invariant is what keeps the merge-gate review-comment
freshness check passing on the next attempt. Never `git commit --amend`.

## Worktree Context

Patches apply inside the worktree at `.worktrees/{slug}/`, not the main checkout. The branch is `session/{slug}`. Never run `git checkout session/{slug}` from main — the worktree IS the checkout.

## Shared-.venv Health Probe (Warn-Only, Stage Entry)

Worktrees share the repo-root `.venv`. Before applying a patch,
probe it so a stripped shared environment surfaces as a warning rather than a
confusing test failure mid-patch:

```bash
"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.venv_health || true
```

Warn-only — never blocks the patch. See `docs/features/uv-sync-worktree-guard.md`.

## Bridge/Worker Restart

A patch touching `bridge/`, `agent/`, or `worker/` needs the restart described in CLAUDE.md after committing.
