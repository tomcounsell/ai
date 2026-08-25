# do-build addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-build/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## `{slug}` resolution in this repo

The generic body's Plan Resolution step derives `{slug}` from the plan
filename. In this repo that generic default is superseded: the lane's `{slug}`
— the name of its worktree and its branch — is recorded on `PipelineLedger.slug`
by `session-ensure` (the Step 0 substrate probe below already calls it) and
resolved via `tools/lane_identity.py::resolve_lane_slug`. Do not re-derive
`{slug}` from `PLAN_PATH`'s filename; a human-named plan may legitimately
track an issue-derived lane, or the reverse. See
[`docs/features/sdlc-lane-identity.md`](../features/sdlc-lane-identity.md).

## Pipeline Substrate & Scripts (the generic body defers these here)

The leaned body describes these abstractly; here are the concrete invocations.
Always pass `--issue-number {issue_number}` on every `sdlc-tool` write — it is the
authoritative session selector (the `VALOR_SESSION_ID`/`AGENT_SESSION_ID` env-var
session is only a last-resort fallback). A forked build subagent must still pass
`--issue-number` so its writes are not diverted to the parent's session.

**Step 0 substrate probe / BUILD in_progress marker:**

```bash
sdlc-tool stage-marker --stage BUILD --status in_progress --issue-number {issue_number} --run-id {run_id}
```

Run identity: every state-mutating `sdlc-tool` call in this addendum
carries `--run-id {run_id}` — the run_id is supplied by the invoking supervisor
(`/do-sdlc` or `/sdlc` carries it from `session-ensure`). When this skill is
invoked standalone (no supervisor), run
`sdlc-tool session-ensure --issue-number {issue_number}` once at the start and
use the emitted `run_id` (`ISSUE_LOCKED` means another live run owns the issue —
stop and report). Read-only calls `stage-query`, `verdict get`, and `dispatch get` take no
run-id. `next-skill` *accepts* an optional `--run-id` as a read-only identity
assertion for its issue-lock peek -- always pass it so the peek
runs under this run's own stated identity instead of a session lookup that can
legitimately miss and produce a false self-block. Under a live supervised run, a bare `session-ensure` instead returns
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
sdlc-tool meta-set --key plan_hash_at_build_start --value "$PLAN_HASH" --issue-number {issue_number} --run-id {run_id} \
  || { echo "G7 disarmed: meta-set refused the plan-hash write" >&2; exit 1; }
# Before PR: re-read CURRENT_HASH; if STORED_HASH non-empty and differs, abort
# (plan revised mid-build) and `sdlc-tool stage-marker --stage BUILD --status failed --run-id {run_id}`.
STORED_HASH=$(sdlc-tool stage-query --issue-number {issue_number} | python -c "import sys,json; print(json.load(sys.stdin).get('_meta',{}).get('plan_hash_at_build_start') or '')")
```

**PR number recording (single writer).** Immediately after `gh pr create`
succeeds, record the PR number on the session record:

```bash
sdlc-tool meta-set --key pr_number --value {PR} --issue-number {issue_number} --run-id {run_id}
```

`meta-set` exits non-zero on an ownership refusal, so check it on both calls rather than suppressing
with `2>/dev/null || true`. A foreign-owner `ISSUE_LOCKED` is a stop condition —
swallow it and you leave `plan_hash_at_build_start` unset (disarming the G7 guard) or `pr_number`
unrecorded, with the build reporting success either way. The plan-hash call needs the explicit
`|| { ...; exit 1; }` above because it sits mid-block with no `set -e`: without it the block's
status comes from the trailing `STORED_HASH=` pipeline and the refusal is invisible.

This command is the single writer of `AgentSession.pr_number`; the read-only
recovery rungs (validated gh search, `session/{slug}` branch-head fallback)
live in `stage-query` and never write.

**Accepted Residual Concerns note (Step 2).** Router row 4c dispatches `/do-build`
when the with-concerns revision + re-critique loop hit its bound, and the residual
concerns were accepted unreviewed. `row_id` is never plumbed into the dispatched
skill's invocation, so `/do-build` re-derives row 4c's own condition from the same
authoritative source the router used:

```bash
sdlc-tool stage-query --issue-number {issue_number}
```

**Mind the nesting.** `stage-query` emits `{"stages": {...}, "_meta": {...}}`.
`_verdicts` — and therefore the verdict's `recorded_at` — lives under **`stages`**.
`revision_applied_at` and `concern_round_count` live under **`_meta`**.
`_meta["latest_critique_verdict"]` is a bare verdict string with **no** timestamp,
so condition (b) below is *not* derivable from `_meta` alone.

```bash
sdlc-tool stage-query --issue-number {issue_number} | python -c "
import json, sys
from datetime import datetime
from agent.pipeline_graph import MAX_CONCERN_RECRITIQUE_ROUNDS
d = json.load(sys.stdin)
v = d.get('stages', {}).get('_verdicts', {}).get('CRITIQUE', {}) or {}
m = d.get('_meta', {}) or {}
def ts(x):
    try:
        return datetime.fromisoformat(x) if x else None
    except (TypeError, ValueError):
        return None
recorded_at, revised_at = ts(v.get('recorded_at')), ts(m.get('revision_applied_at'))
a = 'WITH CONCERNS' in (v.get('verdict') or '').upper()
b = bool(recorded_at and revised_at and revised_at > recorded_at)
c = int(m.get('concern_round_count') or 0) >= MAX_CONCERN_RECRITIQUE_ROUNDS
print('cap-reached' if (a and b and c) else 'normal')
"
```

Write the note when **all three** hold: (a) the latest CRITIQUE verdict contains
`WITH CONCERNS`; (b) `revision_applied_at` postdates that verdict's `recorded_at`;
(c) `concern_round_count >= MAX_CONCERN_RECRITIQUE_ROUNDS`. An absent
`concern_round_count` reads as `0`, and an absent or unparseable timestamp fails
(b) — both degrade to `normal`, i.e. no note, which is the safe direction: a
clean-verdict build can never grow a spurious note because (a) fails first.

On `cap-reached`, append to the plan's `## Critique Results` section, before
committing any build work:

```markdown
### Accepted Residual Concerns (round {concern_round_count}, bound {MAX_CONCERN_RECRITIQUE_ROUNDS})

The with-concerns revision + re-critique loop reached its bound. The concerns
below were carried into BUILD unresolved and are accepted on the record.

- **{concern title}** — {the concern as the final critique round stated it}.
  Accepted because: {why building with it standing is acceptable — non-blocking
  by definition of CONCERN, plus anything round-specific}.
```

Name the round whose concerns were accepted and the bound it hit, so a reader can
tell an accepted residual concern from one that was answered by a revision. Do not
silently build without the note: if the derivation cannot be evaluated (the
command errors rather than printing `normal`), stop and report rather than
proceeding — an unrecorded accepted concern is the failure this note exists to
prevent.

**Build validators (Step 14) and verification parser (Step 5.1):**

```bash
# scripts/validate_build.py and the inline verification-table runner below share one
# table definition and one expectation evaluator (agent/verification_parser.py) --
# validate_build.py carries no table parser or evaluator of its own.
(cd $TARGET_REPO/.worktrees/{slug} && python scripts/validate_build.py $PLAN_PATH)   # exit 1 → /do-patch, ≤3 iters
(cd $TARGET_REPO/.worktrees/{slug} && python scripts/evaluate_build.py $PLAN_PATH)   # exit 2 → bundle FAILs to /do-patch, ≤2 iters; 3 = no criteria; 1 = non-blocking
# Verification table runner:
python -c "import sys; from agent.verification_parser import parse_verification_table, run_checks, format_results; t = parse_verification_table(open(PLAN_PATH).read()); r = run_checks(t.checks); print(format_results(r, t)); sys.exit(1 if t.malformed or not all(x.passed for x in r) else 0)"
# A row in `t.malformed` is a PLAN-AUTHORING error (an unescaped `|` split it, or a
# pipe-block with rows but no Command column), not a finding about the code. Write
# pipes in the table as `\|`. A row in `t.skipped` is a non-check
# table (a summary, a findings recap) -- named in the report but never counted toward
# the exit code.
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
