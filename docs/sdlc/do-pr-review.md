# do-pr-review addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-pr-review/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Substrate, Identity & Tooling (the generic body defers these here)

**Plan resolution.** The generic body's priority list includes "extract the
slug from the branch name and read `docs/plans/{slug}.md`." In this repo that
guess is unreliable: the branch is the lane's recorded `{slug}`, but the plan
document is not required to share that name (see
[`docs/features/sdlc-lane-identity.md`](../features/sdlc-lane-identity.md)).
Prefer `find_plan_path(issue_number)` (`tools/lane_identity.py`), keyed on the
tracking issue, over a filename guess derived from the branch.

**Review identity (bot account, opt-in per machine).** Pipeline-driven reviews
MAY post under a dedicated service account. Set `SDLC_AGENT_GH_TOKEN` only on the
dedicated bot machine; standard machines leave it blank and post under the
operator credential.

- When `CLAUDE_AGENT_REVIEW=1` (set by `sdk_client.py` at session spawn) AND
  `SDLC_AGENT_GH_TOKEN` is non-empty: inject `GH_TOKEN=$SDLC_AGENT_GH_TOKEN` for
  the single `gh pr review`/`gh pr comment` subprocess that posts the review, and
  emit the marker `<!-- SDLC-AGENT-REVIEW v1 sha=<HEAD_SHA> -->` as the first line
  of the body. All read-only `gh` calls use the operator credential. NEVER pass an
  empty `GH_TOKEN` (it corrupts the stored credential).
- Marker is forensic only — configure branch protection (CODEOWNERS or a Ruleset
  with `bypass_actors`/`actors_can_approve=false` for the bot) separately. Full
  runbook: `docs/features/do-pr-review-bot-identity.md`.

**SDLC env vars (auto-injected by `sdk_client.py`):** `$SDLC_PR_NUMBER`,
`$SDLC_PR_BRANCH`, `$SDLC_SLUG`, `$SDLC_PLAN_PATH`, `$SDLC_ISSUE_NUMBER`
(last-resort hint only — primary is PR-body `Closes #N` extraction, #1731),
`$SDLC_REPO` (`$GH_REPO`). Prefer these over manual resolution when present.

**Cross-repo `gh` targeting:** `GH_REPO` is set automatically by `sdk_client.py`;
`gh` respects it — no `--repo` flags needed.

**Clean-git-state helper (before checkout):**

```bash
python -c "from agent.worktree_manager import ensure_clean_git_state; from pathlib import Path; ensure_clean_git_state(Path('.'))"
```

**Stage marker (REVIEW in_progress)** — write at the start (after § 1 resolves
`ISSUE_NUMBER`), parse degraded mode:

```bash
sdlc-tool stage-marker --stage REVIEW --status in_progress --issue-number "$ISSUE_NUMBER" --run-id "$RUN_ID"
```

Run identity (#2003): every state-mutating `sdlc-tool` call in this addendum
carries `--run-id "$RUN_ID"` — supplied by the invoking supervisor (`/do-sdlc`
or `/sdlc` carries it from `session-ensure`). When this skill is invoked
standalone (no supervisor), run
`sdlc-tool session-ensure --issue-number "$ISSUE_NUMBER"` once at the start and
use the emitted `run_id` (`ISSUE_LOCKED` means another live run owns the issue —
stop and report). Read-only calls `stage-query`, `verdict get`, and `dispatch get` take no
run-id. `next-skill` *accepts* an optional `--run-id` as a read-only identity
assertion for its issue-lock peek (issue #2766) -- always pass it so the peek
runs under this run's own stated identity instead of a session lookup that can
legitimately miss and produce a false self-block. Under a live supervised run (#2026), a bare `session-ensure` instead returns
`{"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id": ...}` — that is
inheritance, not a block: use the returned `run_id` and continue; only a foreign
`ISSUE_LOCKED` (no live supervised signal) means stop and report.

**Verification-table runner (code-review.md section 5):**

```bash
python -c "import sys; from agent.verification_parser import parse_verification_table, run_checks, format_results; t = parse_verification_table(open(PLAN_PATH).read()); r = run_checks(t.checks); print(format_results(r, t)); sys.exit(1 if t.malformed or not all(x.passed for x in r) else 0)"
# A row in `t.malformed` is a PLAN-AUTHORING error (an unescaped `|` split it, or a
# pipe-block with rows but no Command column), not a finding about the code. Write
# pipes in the table as `\|`. See #2570, #2836. A row in `t.skipped` is a non-check
# table (a summary, a findings recap) -- named in the report but never counted toward
# the exit code.
```

**Plan-checkbox updater (post-review.md §2.5).** Sync each rubric-judged criterion with:

```bash
"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.plan_checkbox_writer tick   "$PLAN_PATH" --criterion "$TEXT"   # rubric=pass
"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.plan_checkbox_writer untick "$PLAN_PATH" --criterion "$TEXT"   # rubric=fail or acknowledged
```

Exit 0 with a real mutation → `PLAN_MUTATED=true`. Exit 2 semantics (all preserve existing checkbox state):
- `MATCH_AMBIGUOUS` / `MATCH_AMBIGUOUS_SECTION` → append `> Could not auto-tick "{criterion}" — please review manually.`
- `MATCH_NOT_FOUND` when the rubric judged pass/fail → append `> Rubric judged criterion "{text}" {verdict} but no matching item in plan — investigate.`
- `NO_CRITERIA_SECTION` → one-line warning and skip (some chore plans legitimately omit the section).

**Verdict recording (SKILL.md Step 5, #2193).** One `sdlc-tool verdict
finalize` call, before the OUTCOME block, is the only thing that persists the
verdict; the router (`sdlc-tool next-skill`) re-dispatches REVIEW until it sees
one. Always pass `--issue-number` (quoted); it is the authoritative session
selector. On APPROVED it records the verdict, its `head_sha` field (#2769), and
the REVIEW `completed` marker, and reads all three back; on any other verdict it
leaves the marker `in_progress`.

```bash
# --reviewed-head is the HEAD_SHA captured before reading the diff
# (code-review.md), NOT the live head: Step 2.5's plan-checkbox commit has
# already moved the branch, and the verdict must pin the commit you inspected.
# finalize records it when only documentation changed since, and refuses with
# REVIEW_HEAD_DRIFT otherwise (#3228).
sdlc-tool verdict finalize --pr "$PR_NUMBER" --issue-number "$ISSUE_NUMBER" --verdict "APPROVED" --reviewed-head "$HEAD_SHA" --blocker-count 0 --tech-debt-count 0 --run-id "$RUN_ID"
# Findings:
sdlc-tool verdict finalize --pr "$PR_NUMBER" --issue-number "$ISSUE_NUMBER" --verdict "CHANGES REQUESTED" --blocker-count $BLOCKERS --tech-debt-count $TECH_DEBT --run-id "$RUN_ID"
# Preflight short-circuits:
sdlc-tool verdict finalize --pr "$PR_NUMBER" --issue-number "$ISSUE_NUMBER" --verdict "BLOCKED_ON_CONFLICT" --blocker-count 0 --tech-debt-count 0 --run-id "$RUN_ID"
sdlc-tool verdict finalize --pr "$PR_NUMBER" --issue-number "$ISSUE_NUMBER" --verdict "PR_CLOSED" --blocker-count 0 --tech-debt-count 0 --run-id "$RUN_ID"
# Multi-judge: same single finalize call after agent.sdlc_review_consensus.compute_consensus
# (single-writer invariant preserved).
```

A failed read-back exits non-zero with a named error
(`REVIEW_VERDICT_MISSING`, `REVIEW_TRAILER_MISSING`, `REVIEW_MARKER_INCOMPLETE`,
or `REVIEW_ARTIFACT_MISSING` when no posted review or `## Review:` comment is
verifiable). The error says which writes landed (#2740); re-running the
identical call is idempotent. After this skill returns, `/do-sdlc` runs
`sdlc-tool verdict selfcheck` and advances past REVIEW only on `ok:true`.

### PRs with no plan document

Reviewing a hand-authored fix, a review-derived follow-up, or a dependabot bump
means the issue has no plan, so PLAN and CRITIQUE were never dispatched and no
honest CRITIQUE verdict can ever exist. **Call `finalize` exactly as above** —
its REVIEW marker's predecessor backfill verifies those two stages never ran and
records them `skipped`, rather than refusing with `STATE_MACHINE_REJECTED` as it
did before #2577.

Two things still hold, and both are the point:

- **Post the review artifact first.** `finalize` refuses with
  `REVIEW_ARTIFACT_MISSING` when the PR carries no formal GitHub review and no
  `## Review:` comment. Nothing about the no-plan path relaxes that.
- **Never invent a CRITIQUE verdict to unblock the chain.** That is the forgery
  the invariant exists to prevent, and it is now also unnecessary.

To state the disposition up front instead, `sdlc-tool stage-marker --stage
CRITIQUE --status skipped --issue-number "$ISSUE_NUMBER" --run-id "$RUN_ID"`
(and the same for PLAN) runs the identical verified predicate. See
[`docs/features/off-pipeline-merge-path.md`](../features/off-pipeline-merge-path.md).

**Cross-vendor judge (opt-in, default OFF).** After collecting the Claude judge
dicts and BEFORE `compute_consensus`, if `SDLC_REVIEW_CROSS_VENDOR=1` AND
`shape == feature`, invoke `python -m tools.cross_vendor_judge --pr N` (equiv:
`valor-cross-vendor-judge --pr N`). Append only an `"ok"` judge dict to the
judges list; a `"skipped"`/error result is a non-fatal skip unless
`SDLC_REVIEW_CROSS_VENDOR_REQUIRED=1` (then inject a synthetic CHANGES REQUESTED
so any-blocker-wins triggers). Never crash the review.

**Real-Chrome session requirement (Surface).** The calling session must have `requires_real_chrome=True`;
the bridge auto-infers for pipeline runs, or pass
`valor-session create --needs-real-chrome ...` for manual runs. Two concurrent
real-Chrome sessions race on the active tab.

## Documentation Gate

Every PR must have a corresponding `docs/features/{slug}.md` if the plan's `## Documentation` section specified one. Verify this file exists before approving. Missing docs are a blocker.

## Plan Section Compliance

Verify the plan included all four required sections (validated by hooks):
- `## Documentation` — has checkbox tasks with `docs/features/` paths
- `## Update System` — addresses `migrations.py` for Popoto changes
- `## Agent Integration` — addresses MCP exposure for new Python tools
- `## Test Impact` — lists affected tests with UPDATE/DELETE/REPLACE

If the PR was built from a plan missing any section, flag it as a blocker.

## Ruff and Test Gates

A PR must not merge with:
- `ruff check .` failures (exit non-zero)
- `ruff format --check .` failures
- Failing unit tests

These are hard gates. No exceptions.

## Multi-Machine Compatibility

If the PR adds new environment variables, verify they are in `.env.example` and `config/settings.py`. If the PR adds new migrations, verify they are registered in `MIGRATIONS` in `scripts/update/migrations.py`.

## Bridge/Worker Changes

If the PR modifies `bridge/`, `agent/`, or `worker/`, flag for restart-after-deploy. The reviewer should note whether the change requires a service restart on all machines.

## Multi-Judge Consensus

This repo runs multi-judge consensus at the REVIEW stage by default, with two
judges: **`code-quality`** and **`risk`**. That roster is declared here and
nowhere else — there is no environment variable for it. Reviewers should
expect:

- Two per-judge comments (`## Review (Judge code-quality):`, `## Review (Judge risk):`)
  posted **before** the aggregate `## Review:` comment that `/do-merge` reads.
- The aggregate verdict is derived by `agent.sdlc_review_consensus.compute_consensus`
  with `rule="any-blocker-wins"` — any judge raising a blocker forces
  `CHANGES_REQUESTED`.
- The parent passes `expected_judges=2`, the size of the declared roster it
  just dispatched. Optional judges (the cross-vendor judge) never count toward
  it. When fewer distinct judges report, `compute_consensus` refuses `APPROVED`
  and returns `CHANGES REQUESTED` with `quorum_shortfall: true`; the aggregate
  `## Review:` comment states the shortfall explicitly, and the OUTCOME uses
  the quorum-shortfall variant in `outcome-contract.md`. A sequential-lenses
  run (#3198) is recorded the same way.
- Cost containment: trivial PRs force the legacy single-judge path. A PR is
  trivial when its changed files (`gh pr diff $PR_NUMBER --name-only`) are all
  docs (`docs/**`, `**/*.md`) or all lockfile sync (`uv.lock` /
  `pyproject.toml` only). This is the only cost control on this surface, and
  it needs no operator action. This path never calls `compute_consensus` at
  all — it posts one judge's verdict directly, with no `judges`/`consensus`
  kwargs on `record_verdict` and no `judges_run` in its OUTCOME — so the
  quorum floor above does not apply to it and needs no exemption.

Full design: [`docs/features/multi-judge-consensus.md`](../features/multi-judge-consensus.md).

### Artifact-presence backstop (WS-D, #2124)

`tools/sdlc_stage_marker.py` refuses the REVIEW `completed` marker unless both a
readable substrate verdict and a verifiable posted review artifact (a formal
GitHub review or a `## Review:` issue comment) exist, failing closed with
`REVIEW_VERDICT_MISSING` or `REVIEW_ARTIFACT_MISSING`; the router then
re-dispatches `/do-pr-review`. An un-awaited-judge exit therefore fails closed
rather than advancing the pipeline.

## UI Screenshots

For any PR that touches `ui/`, include before/after screenshots of the running app (not mockups). See [`docs/features/byob-browser-control.md`](../features/byob-browser-control.md).
