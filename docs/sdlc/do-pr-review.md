# do-pr-review addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-pr-review/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Substrate, Identity & Tooling (the generic body defers these here)

The leaned body refers to these abstractly. The Multi-Judge Consensus and the
verdict+marker finalize block are documented in their own sections below; this
section adds what they don't cover.

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

**Verification-table runner (§ 4.5):**

```bash
python -c "import sys; from agent.verification_parser import parse_verification_table, run_checks, format_results; t = parse_verification_table(open(PLAN_PATH).read()); r = run_checks(t.checks); print(format_results(r, t)); sys.exit(1 if t.malformed or not all(x.passed for x in r) else 0)"
# A row in `t.malformed` is a PLAN-AUTHORING error (an unescaped `|` split it, or a
# pipe-block with rows but no Command column), not a finding about the code. Write
# pipes in the table as `\|`. See #2570, #2836. A row in `t.skipped` is a non-check
# table (a summary, a findings recap) -- named in the report but never counted toward
# the exit code.
```

**Plan-checkbox updater (post-review § 2.5).** Sync each rubric-judged criterion with:

```bash
"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.plan_checkbox_writer tick   "$PLAN_PATH" --criterion "$TEXT"   # rubric=pass
"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.plan_checkbox_writer untick "$PLAN_PATH" --criterion "$TEXT"   # rubric=fail or acknowledged
```

Exit 0 with a real mutation → `PLAN_MUTATED=true`. Exit 2 semantics (all preserve existing checkbox state):
- `MATCH_AMBIGUOUS` / `MATCH_AMBIGUOUS_SECTION` → append `> Could not auto-tick "{criterion}" — please review manually.`
- `MATCH_NOT_FOUND` when the rubric judged pass/fail → append `> Rubric judged criterion "{text}" {verdict} but no matching item in plan — investigate.`
- `NO_CRITERIA_SECTION` → one-line warning and skip (some chore plans legitimately omit the section).

**Verdict recording (global skill Step 6.6, #2193).** This runs **before** the
OUTCOME block, not after it. In a local pipeline run (`/do-sdlc`) there are no
hooks to write markers/verdicts for you — this single `sdlc-tool` call is the
ONLY thing that persists the verdict, and the router (`sdlc-tool next-skill`)
re-dispatches REVIEW in a loop until it sees one. Skipping it is the #1
local-pipeline stall. Always pass `--issue-number` (quoted) — it is the
authoritative session selector:

```bash
# ONE call replaces the old 3-call sequence (verdict record + stage-marker
# completed + verdict get readback). `finalize` computes the PR head SHA
# itself, records the bare verdict token plus the SHA in its own `head_sha`
# record field (#2769), writes the REVIEW `completed` marker on the APPROVED
# path, and reads all three back.
#
# --blocker-count / --tech-debt-count take integer COUNTS, not findings text.
# Findings go in the review posted to the PR. Omit for "not assessed"; 0 means
# "assessed, none found".
sdlc-tool verdict finalize --pr "$PR_NUMBER" --issue-number "$ISSUE_NUMBER" --verdict "APPROVED" --blocker-count 0 --tech-debt-count 0 --run-id "$RUN_ID"
# Findings:
sdlc-tool verdict finalize --pr "$PR_NUMBER" --issue-number "$ISSUE_NUMBER" --verdict "CHANGES REQUESTED" --blocker-count $BLOCKERS --tech-debt-count $TECH_DEBT --run-id "$RUN_ID"
# Preflight short-circuits:
sdlc-tool verdict finalize --pr "$PR_NUMBER" --issue-number "$ISSUE_NUMBER" --verdict "BLOCKED_ON_CONFLICT" --blocker-count 0 --tech-debt-count 0 --run-id "$RUN_ID"
sdlc-tool verdict finalize --pr "$PR_NUMBER" --issue-number "$ISSUE_NUMBER" --verdict "PR_CLOSED" --blocker-count 0 --tech-debt-count 0 --run-id "$RUN_ID"
# Multi-judge: same single finalize call after agent.sdlc_review_consensus.compute_consensus
# (single-writer invariant preserved).
```

`finalize` is **self-verifying, and it is honest about partial state rather
than free of it** (#2740). It exits **non-zero with a named error**
(`REVIEW_VERDICT_MISSING`, `REVIEW_TRAILER_MISSING`, `REVIEW_MARKER_INCOMPLETE`)
if any of the three writes fails to read back. It is deliberately **not**
transactional: the verdict is written before the marker is attempted (the
ordering #2415/#2577 hardened the read sites around), so the marker write can be
refused after the verdict has already landed durably. When that happens the
error says so explicitly — it names the verdict as persisted and the marker as
missing, and re-running the identical command is idempotent. **Treat a non-zero
exit as a hard failure: stop, do NOT proceed to emit the OUTCOME block** — but
do not assume nothing was written. No separate `verdict get` readback call is
needed; `finalize` already verifies persistence before returning 0.

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

**Real-Chrome session requirement (Surface).** Screenshot capture runs against
the user's real, logged-in Chrome via BYOB MCP — there is no anonymous-headless
fallback (retired #1256). The calling session must have `requires_real_chrome=True`;
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

## Mandatory Finalize — Verdict + Marker Co-Write (#1642, atomized #2193)

On the approval path, the REVIEW verdict record, its `head_sha` field, and the
REVIEW completion marker are written by **one `sdlc-tool verdict finalize` call** ("Verdict recording" above) instead of a
hand-run, separable sequence. Never emit the OUTCOME block without a
successful (exit 0) `finalize` call first. The verification is enforced in the
tool itself (`tools/sdlc_review_finalize.py`, sharing `check_review_persistence`
with `verdict selfcheck`): `finalize` records the verdict and its head SHA,
writes the marker on the APPROVED path, and reads all three back before
returning 0 — any gap yields a named non-zero error
(`REVIEW_VERDICT_MISSING`, `REVIEW_TRAILER_MISSING`, `REVIEW_MARKER_INCOMPLETE`).
It is self-verifying, not transactional: on the branch where the verdict landed
and the marker write was then refused, the error names exactly that, and
re-running the identical call is idempotent. The underlying WS3c gate in
`tools/sdlc_stage_marker.py` still refuses `stage-marker --stage REVIEW
--status completed` with `REVIEW_VERDICT_MISSING` when no substrate verdict is
readable (and, on the APPROVED path, also requires the trailer — see
"Plan Section Compliance" below), so the marker can never precede or outrun
the verdict even if something calls the lower-level primitives directly.

This closes the #1642 desync: because `finalize` is a single call that either
fully succeeds or fails loudly, the REVIEW marker can no longer stay
non-`completed` while the verdict says APPROVED — there is no longer a
separable "marker write" step the skill can exit before reaching. Router
**row 9** (`_rule_review_approved_docs_not_done`) requires `REVIEW ==
completed` **and** a recorded `APPROVED` verdict (issue #1932 tightened the
gate — `REVIEW == completed` alone is no longer sufficient, since a crashed
re-review can leave REVIEW `completed` with no verdict at all), so a desynced
state stalls `/do-docs`. On any non-APPROVED verdict, `finalize` leaves the
marker at `in_progress`. The `/do-sdlc` supervisor adds a second,
committed backstop: after this skill returns, it calls `sdlc-tool verdict
selfcheck --pr N --issue-number M` and advances past REVIEW only on
`ok:true`, halting and surfacing the machine-readable `reason` on `ok:false`
instead of silently re-looping (#2193).

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
- The OUTCOME block includes `judges_run` (int) and `consensus_disagreement` (bool)
  side-fields when multi-judge runs.
- Cost containment: trivial PRs force the legacy single-judge path. A PR is
  trivial when its changed files (`gh pr diff $PR_NUMBER --name-only`) are all
  docs (`docs/**`, `**/*.md`) or all lockfile sync (`uv.lock` /
  `pyproject.toml` only). This is the only cost control on this surface, and
  it needs no operator action.

Full design: [`docs/features/multi-judge-consensus.md`](../features/multi-judge-consensus.md).

### In-turn-await + artifact-presence gate (WS-D, issue #2124)

The judge subagents run in the **foreground and are awaited in-turn**: the parent
blocks on every judge returning IN THE SAME TURN before it aggregates, posts the
`## Review:` comment, and records the verdict. A fork that exits with judges still in
flight kills those children and posts nothing (the #2112 miss) — so this is a hard
contract, not a latency preference.

The mechanical backstop lives in `tools/sdlc_stage_marker.py`: the REVIEW `completed`
marker now requires **both** (a) a readable substrate verdict (WS3c / #2062,
`_review_verdict_readable`) **and** (b) a verifiable posted review artifact
(`_review_artifact_posted` — a formal GitHub review OR a `## Review:` issue comment on
the PR). If either is missing the completion write is refused with a named
`REVIEW_ARTIFACT_MISSING` (or `REVIEW_VERDICT_MISSING`) error and the WS3b recovery row
re-dispatches `/do-pr-review` — the failure direction is "re-run the stage", never a
silent advance. Both probes fail CLOSED (any error ⇒ refusal).

## UI Screenshots

For any PR that touches `ui/`, include before/after screenshots of the actual running app (not mockups). Capture via BYOB MCP (`mcp__byob__browser_*`) — the only browser surface — so the screenshot reflects the user's real, logged-in Chrome session. See `.claude/skills/do-pr-review/SKILL.md` and `sub-skills/screenshot.md`.

For background, see [`docs/features/byob-browser-control.md`](../features/byob-browser-control.md).
