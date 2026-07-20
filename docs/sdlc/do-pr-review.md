# do-pr-review addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-pr-review/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Substrate, Identity & Tooling (the generic body defers these here)

The leaned body refers to these abstractly. The Multi-Judge Consensus and the
verdict+marker finalize block are documented in their own sections below; this
section adds what they don't cover.

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
stop and report). Read-only calls (`stage-query`, `verdict get`, `next-skill`)
take no run-id. Under a live supervised run (#2026), a bare `session-ensure` instead returns
`{"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id": ...}` — that is
inheritance, not a block: use the returned `run_id` and continue; only a foreign
`ISSUE_LOCKED` (no live supervised signal) means stop and report.

**Verification-table runner (§ 4.5):**

```bash
python -c "from agent.verification_parser import parse_verification_table, run_checks, format_results; ..."
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

**Verdict recording (global skill Step 6.6).** This runs **before** the OUTCOME
block, not after it. In a local pipeline run (`/do-sdlc`) there are no hooks to
write markers/verdicts for you — this `sdlc-tool` call is the ONLY thing that
persists the verdict, and the router (`sdlc-tool next-skill`) re-dispatches REVIEW
in a loop until it sees one. Skipping it is the #1 local-pipeline stall. Always
pass `--issue-number` (quoted) — it is the authoritative session selector:

```bash
# Compute the PR head SHA first — the recorded verdict MUST embed the
# `REVIEW_CONTEXT head_sha=<sha>` trailer (#2003): it is what lets the merge
# predicate's SHA-freshness rung prove the verdict matches the reviewed head
# instead of falling back to timestamp comparison. Survives verdict
# normalization (the predicate regex tolerates the uppercased image).
HEAD_SHA=$(gh pr view "$PR_NUMBER" --json headRefOid -q .headRefOid)
# APPROVED (status=success) — verdict + completion marker are ONE block (#1642):
sdlc-tool verdict record --stage REVIEW --verdict "APPROVED REVIEW_CONTEXT head_sha=$HEAD_SHA" --blockers 0 --tech-debt 0 --issue-number "$ISSUE_NUMBER" --run-id "$RUN_ID"
sdlc-tool stage-marker --stage REVIEW --status completed --issue-number "$ISSUE_NUMBER" --run-id "$RUN_ID"
# Findings:
sdlc-tool verdict record --stage REVIEW --verdict "CHANGES REQUESTED REVIEW_CONTEXT head_sha=$HEAD_SHA" --blockers $BLOCKERS --tech-debt $TECH_DEBT --issue-number "$ISSUE_NUMBER" --run-id "$RUN_ID"
# Preflight short-circuits:
sdlc-tool verdict record --stage REVIEW --verdict "BLOCKED_ON_CONFLICT" --blockers 0 --tech-debt 0 --issue-number "$ISSUE_NUMBER" --run-id "$RUN_ID"
sdlc-tool verdict record --stage REVIEW --verdict "PR_CLOSED" --blockers 0 --tech-debt 0 --issue-number "$ISSUE_NUMBER" --run-id "$RUN_ID"
# Multi-judge: ONE record call with --judges-json/--consensus-json after
# agent.sdlc_review_consensus.compute_consensus (single-writer invariant).
# Read back to confirm persistence before emitting the OUTCOME block:
sdlc-tool verdict get --stage REVIEW --issue-number "$ISSUE_NUMBER"
```

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

## Mandatory Finalize — Verdict + Marker Co-Write (#1642)

On the approval path, the REVIEW verdict record AND the REVIEW completion marker are a **single, self-contained, mandatory block**: `sdlc-tool verdict record --stage REVIEW --verdict "APPROVED" ... --run-id "$RUN_ID"` is immediately followed by `sdlc-tool stage-marker --stage REVIEW --status completed ... --run-id "$RUN_ID"` in the same block. Never record an APPROVED verdict without immediately writing the completion marker. The ordering is enforced in the tool (#2062 WS3c): `stage-marker --stage REVIEW --status completed` refuses with the named `REVIEW_VERDICT_MISSING` (exit 1) when no substrate verdict is readable, so the marker can never precede the verdict; a refused marker leaves the no-verdict state the router's recovery row 8e redirects back to `/do-pr-review`.

This closes the #1642 desync: if the marker write is a separable later step and the skill exits before reaching it, the REVIEW marker stays non-`completed` while the verdict says APPROVED. Router **row 9** (`_rule_review_approved_docs_not_done`) requires `REVIEW == completed` **and** a recorded `APPROVED` verdict (issue #1932 tightened the gate — `REVIEW == completed` alone is no longer sufficient, since a crashed re-review can leave REVIEW `completed` with no verdict at all), so a desynced state stalls `/do-docs` — the skill-layer completion-marker write is what advances REVIEW. On any non-APPROVED verdict, leave the marker at `in_progress`.

## Multi-Machine Compatibility

If the PR adds new environment variables, verify they are in `.env.example` and `config/settings.py`. If the PR adds new migrations, verify they are registered in `MIGRATIONS` in `scripts/update/migrations.py`.

## Bridge/Worker Changes

If the PR modifies `bridge/`, `agent/`, or `worker/`, flag for restart-after-deploy. The reviewer should note whether the change requires a service restart on all machines.

## Multi-Judge Consensus

This repo opts in to multi-judge consensus at the REVIEW stage by default
(`SDLC_REVIEW_JUDGES=code-quality,risk`, `SDLC_REVIEW_K=2`). Reviewers should
expect:

- Two per-judge comments (`## Review (Judge code-quality):`, `## Review (Judge risk):`)
  posted **before** the aggregate `## Review:` comment that `/do-merge` reads.
- The aggregate verdict is derived by `agent.sdlc_review_consensus.compute_consensus`
  with `rule="any-blocker-wins"` — any judge raising a blocker forces
  `CHANGES_REQUESTED`.
- The OUTCOME block includes `judges_run` (int) and `consensus_disagreement` (bool)
  side-fields when multi-judge runs.
- Cost containment: `docs-only` / `lockfile-only` PRs (classified by
  `python -m scripts.pr_shape_classify --pr $PR_NUMBER`, the same module
  `/do-merge` invokes) force the legacy single-judge path. Operators can also
  set `SDLC_REVIEW_JUDGES=none` or `SDLC_REVIEW_K=1` as independent kill
  switches.

Full design: [`docs/features/multi-judge-consensus.md`](../features/multi-judge-consensus.md).
The shape classifier is shared with `/do-merge` — see
[`docs/features/pr-shape-aware-merge-gates.md`](../features/pr-shape-aware-merge-gates.md).

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
