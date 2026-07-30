---
name: do-sdlc-refusal-discrimination
title: "/do-sdlc supervisor: discriminate session-ensure refusals instead of aborting"
status: Ready
type: bug
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/2452
last_comment_id:
revision_applied: false
revision_applied_at:
---

# /do-sdlc supervisor: discriminate session-ensure refusals instead of aborting

## Problem

The `/do-sdlc` skill body (`.claude/skills-global/do-sdlc/SKILL.md`) predates #2026's
fork-inheritance hardening in the `sdlc-tool session-ensure` **tool**. The tool now returns
three distinguishable refusals, but the skill body names only `ISSUE_LOCKED` and instructs
"a foreign `run_id` … treat it like a router block: stop and report." A local supervisor (or a
stage fork) that receives the *designed hand-off signal* `SUPERVISED_RUN_ACTIVE` pattern-matches
on `blocked: true` and aborts, standing down from a lease it should inherit — including from its
**own** ledger anchor.

Confirmed live: on 2026-07-29, #2420/#2421/#2422 each reached CRITIQUE and produced zero PRs; on
#2421 the fork received `SUPERVISED_RUN_ACTIVE` with `owner_session_id: "sdlc-local-2421"` (its own
anchor) and abandoned the run. Two `sdlc-local-{N}` ledger anchors were killed on the mistaken
premise that a running-looking anchor was a rogue pipeline, and killing one destroyed the `_meta`
stage state stored on it.

## Freshness Check

Baseline: `main` at `6d78fdea6`. Issue filed ~2026-07-29 (~1 day ago). Disposition: **Unchanged**.

- `tools/sdlc_session_ensure.py` docstring (issue cite L19–L30) re-read: the three refusal payloads
  are exactly as the issue documents. `SUPERVISED_RUN_ACTIVE` = `{"blocked": true, "reason":
  "SUPERVISED_RUN_ACTIVE", "run_id", "owner_run_id", "owner_session_id"}` (mints nothing, hand back
  via `--run-id`/`--reuse-run-id`); `ISSUE_LOCKED` carries `orphaned_lock` (`true` = owner died,
  frees within TTL; `false` = genuine foreign holder).
- `is_ledger` flag confirmed at `tools/sdlc_session_ensure.py:617`.
- `SKILL.md` last touched by #2214 (repo-agnosticism guards); term frequency still
  `SUPERVISED_RUN_ACTIVE 0 / orphaned_lock 0 / is_ledger 0 / ISSUE_LOCKED 2`, matching the issue.
- #2446 is **OPEN** and owns the lease-TTL/heartbeat code fix — this plan cross-references it, never
  duplicates it.
- Coordination: Lane 1 (#2446 + #2451) is adding lease *renewal* code but is **not** changing the
  three refusal payload shapes. Verified the payload shapes directly against source; they are the
  stable contract this plan documents.

## Research

No external research needed — this is an internal prose change against an existing, verified tool
contract. All source of truth is `tools/sdlc_session_ensure.py` and `agent/supervised_run.py` in
this repo.

## Solution

Rewrite the ownership section of the `/do-sdlc` skill body so the supervisor discriminates the three
refusals instead of collapsing them to "stop." The tool is correct; only the skill's interpretation
is wrong. **This is a prose-only change against the existing tool contract** — no changes to
`agent/sdlc_router.py` dispatch rows, no changes to the `session-ensure` contract, no lease-TTL code.

The `/do-sdlc` skill is a **global** skill (hardlinked to `~/.claude/skills/` on every machine). The
generic body already assumes the `sdlc-tool` substrate (synced to every machine via `~/.local/bin`)
behind the canonical context probe at line 17. The split:

### Generic body (`.claude/skills-global/do-sdlc/SKILL.md`)

The refusal semantics, run identity, and ledger-anchor concept are all part of the `sdlc-tool
session-ensure` contract that ships to every machine — they belong in the body, the same way the
existing `ISSUE_LOCKED` / `run_id` / `--reuse-run-id` text already lives there.

1. **Three-way refusal decision table** — replace the single "foreign `run_id` → stop" rule in Step 2
   with an explicit table:
   - `SUPERVISED_RUN_ACTIVE` → **inherit** the returned `owner_run_id` (pass back via
     `--run-id`/`--reuse-run-id`) and continue. Never stop.
   - `ISSUE_LOCKED` + `orphaned_lock: true` → wait out the ≤300s TTL, re-ensure, continue.
   - `ISSUE_LOCKED` + `orphaned_lock: false` → the **only** stop condition of the three; report.
2. **Self-identity check before standing down** — before treating any refusal as a stop, compare
   `owner_session_id` against `sdlc-local-{issue_number}` and `owner_run_id` against the `run_id`s
   this run has held. A match is the supervisor's own ghost, never a rival.
3. **Between-stage lease renewal** — in the Step 3 loop, after each stage subagent returns and before
   asking the router, re-ensure with `session-ensure --reuse-run-id {run_id}` (the tool verifies the
   claim against the live lock/record). This keeps the lease warm across stage boundaries.
4. **Ledger-anchor rule** — state that `sdlc-local-{N}` is a non-executable ledger anchor
   (`is_ledger`, #2042), permanently showing `status=running`, that carries the run's `_meta` stage
   state, must **not** be killed, and is not a rogue pipeline.
5. **Commit-early rule** — instruct stage subagents to commit to `session/{slug}` as work lands, so a
   preempt/lease-lapse mid-stage never loses work.
6. **TTL cross-reference** — note the known limit (a ≤300s lease still lapses under a multi-minute
   stage; inheritance makes the run *recoverable*, not immune) and cross-reference **#2446** for the
   code-level fix. Do not duplicate the TTL discussion.

### Repo-specific addendum (`docs/sdlc/do-sdlc.md`, behind the existing probe)

The one genuinely ai-repo-specific surface is *how the ledger anchor appears in this repo's
diagnostic tooling*. This does not exist in a foreign repo and belongs behind the probe:

- `valor_session list` filters ledger anchors (hidden) while `curl localhost:8500/dashboard.json`
  displays them — so a running-looking `sdlc-local-*` on the dashboard is **expected**, not a rogue
  pipeline. This is the exact confusion that got two anchors killed on 2026-07-29.
- The AgentSession-level consequence: killing a `sdlc-local-{N}` anchor destroys the `_meta` stage
  state stored on the record.
- Any concrete `sdlc-tool session-ensure` re-ensure invocation specifics for this repo's paths.

The body carries the canonical probe sentence pattern (already present at line 17); the addendum is
read only when it exists.

## Data Flow

Not applicable — single-file prose change with a companion addendum. The runtime data flow it
*describes* (supervisor → `session-ensure` → refusal payload → inherit/wait/stop decision) is
unchanged; only the skill's written interpretation of the existing payloads changes.

## Step by Step Tasks

- [ ] Edit `.claude/skills-global/do-sdlc/SKILL.md`: replace the Step 2 "foreign run_id → stop" rule
      and the redundant-context check with the three-way refusal decision table (task 1 above).
- [ ] Add the self-identity check prose (task 2) to the same ownership section.
- [ ] Add the between-stage `--reuse-run-id` renewal step (task 3) to the Step 3 supervision loop.
- [ ] Add the ledger-anchor rule (task 4), naming `is_ledger` and the no-kill / carries-`_meta` facts.
- [ ] Add the commit-early rule for stage subagents (task 5).
- [ ] Add the TTL known-limit note cross-referencing #2446 (task 6); do not duplicate #2446's content.
- [ ] Create `docs/sdlc/do-sdlc.md` with the ai-repo-specific ledger-anchor diagnostic addendum
      (valor_session vs dashboard.json, `_meta` destruction consequence).
- [ ] Verify `grep -c` over `SKILL.md` returns ≥1 for each of `SUPERVISED_RUN_ACTIVE`,
      `orphaned_lock`, `is_ledger`.
- [ ] Run the skills audit (`audit-skills`) to confirm the body still passes rule_13/rule_21 probe
      coverage after the edits.

## Success Criteria

- `SKILL.md` documents `SUPERVISED_RUN_ACTIVE` as a hand-off (inherit + continue, never stop).
- `SKILL.md` documents `ISSUE_LOCKED` + `orphaned_lock: true` as wait-and-retry within the TTL.
- `SKILL.md` documents `ISSUE_LOCKED` + `orphaned_lock: false` as the only stop condition of the three.
- The Step 3 loop includes a between-stage `session-ensure --reuse-run-id {run_id}` renewal step.
- `SKILL.md` includes a self-identity check comparing `owner_session_id` to `sdlc-local-{issue_number}`
  and `owner_run_id` to run_ids held by the current run.
- `SKILL.md` states `sdlc-local-{N}` is a non-executable ledger anchor permanently showing
  `status=running`, must not be killed, and carries `_meta` stage state.
- `SKILL.md` instructs stage subagents to commit to `session/{slug}` as work lands.
- `grep -c` over `SKILL.md` returns ≥1 for each of `SUPERVISED_RUN_ACTIVE`, `orphaned_lock`,
  `is_ledger`.
- The TTL limit is cross-referenced to #2446, not duplicated.
- `audit-skills` passes for `do-sdlc` (probe coverage intact).

## Failure Path Test Strategy

There is no automated test surface — `SKILL.md` is prose consumed by a model at runtime, and
`docs/sdlc/do-sdlc.md` is a probed addendum. The failure paths are validated by:

1. **Grep assertions** (deterministic, in Success Criteria) — the term-frequency acceptance criteria
   are a mechanical check that each refusal is named.
2. **Skills audit** — `audit-skills` confirms the body keeps its probe coverage (rule_13/rule_21) so
   the coupling additions do not leak repo specifics into a foreign-repo body.
3. **Prose review** at CRITIQUE/REVIEW — a reviewer confirms the three-way table matches the tool
   docstring and that the "inherit, never stop" action is unambiguous.

## Test Impact

No existing tests affected — this is a prose change to a skill body and a new probed addendum doc,
neither of which has an executable test surface. The `audit-skills` repo-agnosticism guards
(rule_13/rule_21) exercise the body and are the standing regression protection.

## Rabbit Holes

- **Do not fix the lease TTL here.** A ≤300s lease against a multi-minute stage still lapses;
  inheritance makes the run recoverable, not immune. The code-level TTL/heartbeat fix is #2446 —
  cross-reference it, do not reimplement it.
- **Do not touch `agent/sdlc_router.py` dispatch rows** or the `session-ensure` tool contract. The
  tool is correct; only the skill's interpretation is wrong.
- **Do not over-explain the AgentSession model in the body.** The `is_ledger`/`_meta` concept is
  named in the body (AC requirement) but the repo-specific diagnostic tooling (valor_session,
  dashboard.json) stays in the addendum behind the probe.

## No-Gos

- No changes to `sdlc_router.py` dispatch rows.
- No changes to the `session-ensure` tool contract or `tools/sdlc_session_ensure.py`.
- No lease-TTL / heartbeat code (owned by #2446).
- No repo-specific executables (`valor_session`, `dashboard.json`, `localhost:8500`) in the generic
  body — those go in the addendum only.

## Update System

No update system changes required — the `/update` sync already hardlinks
`.claude/skills-global/do-sdlc/SKILL.md` to `~/.claude/skills/` on every machine, and reads
`docs/sdlc/do-sdlc.md` behind the existing probe. No new deps, config, or migration steps.

## Agent Integration

No agent integration required — this is a skill-body + docs change. The `/do-sdlc` skill and the
`sdlc-tool` substrate it drives are already wired; no new CLI entry point or bridge import is added.

## Documentation

- [ ] Create `docs/sdlc/do-sdlc.md` — the ai-repo-specific ledger-anchor diagnostic addendum behind
      the skill-context probe (this is both a Step task and the documentation deliverable).
- [ ] No `docs/features/` entry needed — the change is internal to the SDLC pipeline skill and its
      addendum; the skill body itself is the user-facing documentation of the behavior.
