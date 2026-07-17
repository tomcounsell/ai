# do-build addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-build/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Pipeline Substrate & Scripts (the generic body defers these here)

The leaned body describes these abstractly; here are the concrete invocations.
Always pass `--issue-number {issue_number}` on every `sdlc-tool` write — it is the
authoritative session selector (the `VALOR_SESSION_ID`/`AGENT_SESSION_ID` env-var
session is only a last-resort fallback). A forked build subagent must still pass
`--issue-number` so its writes are not diverted to the parent's session
(#1671/#1672).

**Step 0 substrate probe / BUILD in_progress marker:**

```bash
sdlc-tool stage-marker --stage BUILD --status in_progress --issue-number {issue_number} --run-id {run_id}
```

Run identity (#2003): every state-mutating `sdlc-tool` call in this addendum
carries `--run-id {run_id}` — the run_id is supplied by the invoking supervisor
(`/do-sdlc` or `/sdlc` carries it from `session-ensure`). When this skill is
invoked standalone (no supervisor), run
`sdlc-tool session-ensure --issue-number {issue_number}` once at the start and
use the emitted `run_id` (`ISSUE_LOCKED` means another live run owns the issue —
stop and report). Read-only calls (`stage-query`, `verdict get`, `next-skill`)
take no run-id. Under a live supervised run (#2026), a bare `session-ensure` instead returns
`{"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id": ...}` — that is
inheritance, not a block: use the returned `run_id` and continue; only a foreign
`ISSUE_LOCKED` (no live supervised signal) means stop and report.

Parse the JSON: `in_progress` → substrate present; `degraded` → announce "running
in degraded mode (state not persisted)" and continue (the build never depends on
the substrate); non-zero exit → report the stderr diagnostic and proceed.

**Pipeline state machine** (`agent.build_pipeline`): resume check, init, and
stage advancement:

```bash
python -c "from agent.build_pipeline import load; import json; s = load('{slug}'); print(json.dumps(s) if s else 'null')"
python -c "from agent.build_pipeline import initialize; initialize('{slug}', 'session/{slug}', '$TARGET_REPO/.worktrees/{slug}', target_repo='$TARGET_REPO')"
python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', '<branch|implement|test|review|document|pr>')"
```

**Cross-repo resolution** (`resolve_repo_root`) and **worktree manager**
(idempotent get-or-create, clean-git-state guard, removal/prune):

```bash
python -c "from agent.worktree_manager import resolve_repo_root; print(resolve_repo_root('$PLAN_PATH'))"
python -c "from agent.worktree_manager import ensure_clean_git_state; from pathlib import Path; print(ensure_clean_git_state(Path('$TARGET_REPO')))"
python -c "from agent.worktree_manager import get_or_create_worktree; from pathlib import Path; print(get_or_create_worktree(Path('$TARGET_REPO'), '{slug}'))"
```

Worktree removal/post-merge cleanup: `cd "${AI_REPO_ROOT:-$HOME/src/ai}"` first
(prevents CWD death), then `agent.worktree_manager.remove_worktree` /
`prune_worktrees`, or `python scripts/post_merge_cleanup.py {slug}` (busy-guard
exit codes: 0 clean, 1 error, 2 busy — see `docs/sdlc/merge-troubleshooting.md`).

**Freshness & prerequisite gates:**

```bash
python scripts/check_plan_freshness.py {PLAN_PATH}     # exit 1 = stale → run /do-plan first
python scripts/check_prerequisites.py {PLAN_PATH}      # any fail → stop
```

`check_plan_freshness.py` does NOT use `gh api` — `gh api` is excluded from PM
session Bash by `agent/hooks/pre_tool_use.py::PM_BASH_ALLOWED_PREFIXES`.

**Plan-hash mid-build guard (G7).** Record at build start, verify before PR:

```bash
PLAN_REPO=$(git -C "$(dirname "$PLAN_PATH")" rev-parse --show-toplevel)
git -C "$PLAN_REPO" fetch origin main 2>/dev/null || true
PLAN_REL=$(python -c "import os; print(os.path.relpath('$PLAN_PATH', '$PLAN_REPO'))")
PLAN_HASH=$(git -C "$PLAN_REPO" log -1 --format=%H origin/main -- "$PLAN_REL")
sdlc-tool meta-set --key plan_hash_at_build_start --value "$PLAN_HASH" --issue-number {issue_number} --run-id {run_id} 2>/dev/null || true
# Before PR: re-read CURRENT_HASH; if STORED_HASH non-empty and differs, abort
# (plan revised mid-build) and `sdlc-tool stage-marker --stage BUILD --status failed --run-id {run_id}`.
STORED_HASH=$(sdlc-tool stage-query --issue-number {issue_number} | python -c "import sys,json; print(json.load(sys.stdin).get('_meta',{}).get('plan_hash_at_build_start') or '')")
```

**PR number recording (single writer).** Immediately after `gh pr create`
succeeds, record the PR number on the session record:

```bash
sdlc-tool meta-set --key pr_number --value {PR} --issue-number {issue_number} --run-id {run_id} 2>/dev/null || true
```

This command is the single writer of `AgentSession.pr_number`; the read-only
recovery rungs (validated gh search, `session/{slug}` branch-head fallback)
live in `stage-query` and never write.

**Build validators (Step 14) and verification parser (Step 5.1):**

```bash
(cd $TARGET_REPO/.worktrees/{slug} && python scripts/validate_build.py $PLAN_PATH)   # exit 1 → /do-patch, ≤3 iters
(cd $TARGET_REPO/.worktrees/{slug} && python scripts/evaluate_build.py $PLAN_PATH)   # exit 2 → bundle FAILs to /do-patch, ≤2 iters; 3 = no criteria; 1 = non-blocking
# Verification table runner:
python -c "from agent.verification_parser import parse_verification_table, run_checks, format_results; ..."
```

**Documentation gate scripts (Step 6):**

```bash
(cd $TARGET_REPO/.worktrees/{slug} && python scripts/validate_docs_changed.py {PLAN_PATH})   # exit 1 (missing docs) or exit 3 (file/command error) BLOCKS PR; exit 2 (stale markers, diff-scoped) = non-blocking warning, proceed
(cd $TARGET_REPO/.worktrees/{slug} && CHANGED_FILES=$(git diff --name-only main...HEAD | tr '\n' ' ') && python scripts/scan_related_docs.py --json $CHANGED_FILES > /tmp/related_docs.json)
cat /tmp/related_docs.json | python scripts/create_doc_review_issue.py
```

**OUTCOME parser.** The OUTCOME contract is parsed by `classify_outcome()` in
`agent/pipeline_state.py` (Tier 0).

## Lint and Format

This repo uses `ruff` for both formatting and linting. The pre-commit hook auto-fixes all fixable issues via `ruff format` + `ruff check --fix`. Do not run manual lint checks during build — the hook handles it on final commits.

Use `--no-verify` on intermediate WIP commits only. Final commits must go through the hook.

## Test Isolation

Unit tests in `tests/unit/` must never touch production Redis. Use `REDIS_TEST_DB` or a separate prefix. Bulk Redis operations must always be project-scoped. See `tests/README.md` for test markers.

## Worktree Pattern

- Builder agents work in `.worktrees/{slug}/`, not main checkout
- Never `git checkout session/{slug}` — the worktree IS the checkout
- Commits happen at logical checkpoints throughout Implement, not batched at end

## Definition of Done (this repo)

In addition to global DoD, this repo requires:
- `python -m ruff check .` passes (exit 0)
- `python -m ruff format --check .` passes (exit 0)
- `pytest tests/unit/ -x -q` passes
- New `docs/features/` doc created if plan has one in the ## Documentation section
