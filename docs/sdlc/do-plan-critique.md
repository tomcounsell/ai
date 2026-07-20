# do-plan-critique addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-plan-critique/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Substrate Invocations (concrete commands the generic body defers here)

These are the exact `sdlc-tool`/helper invocations the leaned body refers to
abstractly. The roster-barrier mechanics (`critique-roster-check`, `_roster.json`,
atomic result files, the `MAJOR REWORK (CRITIQUE INCOMPLETE)` STOP) and the
Step 5.5 verdict-record + completion-marker block are documented in their own
sections below — this section adds the invocations not covered there.

**Plan resolution** also accepts the repo convention: plans live at
`docs/plans/{slug}.md`; the slug derives from the plan filename or the issue.

**Start-of-skill stage marker (in_progress).** Write at the very start, before triage:

```bash
sdlc-tool stage-marker --stage CRITIQUE --status in_progress --issue-number "$ISSUE_NUMBER" --run-id "$RUN_ID"
```

`ISSUE_NUMBER` MUST be assigned unconditionally (never `${ISSUE_NUMBER:-…}`) and
asserted to be a positive integer before any recorder call — a stale inherited
value would divert recorder writes to the wrong session (#1731).

Run identity (#2003): every state-mutating `sdlc-tool` call in this addendum
carries `--run-id "$RUN_ID"` — supplied by the invoking supervisor (`/do-sdlc`
or `/sdlc` carries it from `session-ensure`). When this skill is invoked
standalone (no supervisor), run
`sdlc-tool session-ensure --issue-number "$ISSUE_NUMBER"` once at the start and
use the emitted `run_id` (`ISSUE_LOCKED` means another live run owns the issue —
stop and report). Read-only calls (`stage-query`, `verdict get`, `next-skill`)
take no run-id.

**Step 2b crash-resume probe.** Before triage/roster freeze, check for a reusable
incomplete run dir:

```bash
RESUME_DIR=$(critique-resume-probe --plan "$PLAN_PATH" --issue "$ISSUE_NUMBER" 2>/tmp/critique-resume-stale.txt)
PROBE_EXIT=$?
```

If `PROBE_EXIT == 0`, set `CRITIQUE_RUN_DIR="$RESUME_DIR"`, `RESUMED=1`, GC the
stale-hash siblings (`cat /tmp/critique-resume-stale.txt | xargs -r rm -rf`),
skip triage + roster freeze, and dispatch only the missing critics.

**Step 3a plan-hash + run-dir creation.** Compute the stale-resume guard hash and
create the per-run directory (mkdir WITHOUT `-p` so a collision fails loudly):

```bash
PLAN_HASH=$(uv run --directory "${AI_REPO_ROOT:-$HOME/src/ai}" python -c "from tools.sdlc_verdict import compute_plan_hash; print(compute_plan_hash('$PLAN_PATH') or '')")
ISSUE_OR_SLUG="${ISSUE_NUMBER:-$(basename "$PLAN_PATH" .md)}"
CRITIQUE_RUN_DIR=".critique-runs/${ISSUE_OR_SLUG}-$(date +%s%N)"
mkdir "$CRITIQUE_RUN_DIR"
echo "$PLAN_HASH" > "$CRITIQUE_RUN_DIR/.plan_hash"
```

Then write the frozen roster manifest (`_roster.json`): LITE →
`{"roster": ["Consolidated Critic"], "count": 1}`; FULL →
`{"roster": ["Risk & Robustness", "Scope & Value", "History & Consistency"], "count": 3}`.

**Step 5.6 plan-revising lock.** When the verdict needs a revision pass and
`revision_applied` is not already `true`:

```bash
sdlc-tool meta-set --key plan_revising --value true --issue-number "$ISSUE_NUMBER" --run-id "$RUN_ID"
```

This activates the SDLC router guard G7 (blocks `/do-build` until `/do-plan`
clears the lock). Do NOT set it for `READY TO BUILD (no concerns)` or when
`revision_applied: true` already.

## Triage Routing (LITE / FULL)

Step 2.6 classifies each plan as LITE (1 Consolidated Critic) or FULL (3 merged critics). Force-FULL applies to doctrine paths (`config/personas/`, `.claude/skills/`, `.claude/skills-global/`, `agent/sdlc_router.py`, `agent/pipeline_graph.py`, `.claude/hooks/`) and `appetite: Large` plans. For all other plans an LLM classifier biased toward FULL makes the call. See [`docs/features/plan-critique-triage.md`](../features/plan-critique-triage.md) for the full decision table and crash-resume flow.

## Required Section Enforcement

The critique must verify all four required plan sections are present and substantive:

- **## Documentation** — must include a checkbox task with a `docs/features/` path
- **## Update System** — must address `scripts/update/migrations.py` for any Popoto model changes
- **## Agent Integration** — must address MCP server exposure for new Python tools
- **## Test Impact** — must list affected tests with UPDATE/DELETE/REPLACE dispositions

If any section is missing or contains only a placeholder, raise it as a HIGH-severity blocker.

## Popoto Migration Check

If the plan touches any Popoto model, the critique must verify:
- A migration function is planned in `scripts/update/migrations.py`
- The migration is registered in `MIGRATIONS`
- The plan avoids raw Redis operations

## Artifact-Based Roster Barrier (#1690, supersedes the #1654 wait-and-collect)

The war-room critics (1 for LITE, 3 for FULL — per the Step 2.6 triage) write their findings to **per-critic result files** rather than relying on a prose await-all instruction. This barrier is observable on the filesystem and verifiable by a test — it does not depend on the LLM driver choosing to block.

### How the barrier works

**Step 3a — Frozen `_roster.json` manifest.** Before any critic is dispatched, the skill fixes the roster from the triage depth (LITE → `["Consolidated Critic"]`; FULL → `["Risk & Robustness", "Scope & Value", "History & Consistency"]`) and writes `${CRITIQUE_RUN_DIR}/_roster.json` — a JSON object listing the expected critic names and count. This manifest is the **membership set** the Step 3.5 gate checks against. Because the manifest is frozen before dispatch, the gate cannot be satisfied by dispatching fewer critics than expected (the under-dispatch loophole from the prior prose-await design).

**Step 3 — Atomic per-critic result files.** Each critic writes its findings body first, then appends a **two-line terminal completion fence** as its final action: the unique delimiter `<<<CRITIQUE-RESULT-COMPLETE>>>` as the penultimate non-empty line, immediately followed by `STATUS: COMPLETED` as the last non-empty line. The write is **atomic**: the critic writes to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md.tmp`, then renames it to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md`. Because both files live inside `${CRITIQUE_RUN_DIR}` (same filesystem), the rename is atomic — the canonical path is never observed in a truncated or partial state.

**Step 3.5 — The `critique-roster-check` membership gate (runs BEFORE Step 4).** The skill calls:

```bash
critique-roster-check --run-dir "$CRITIQUE_RUN_DIR" --plan-path "$PLAN_PATH"
```

This helper reads `_roster.json` and, for each named roster member, checks that `{name}.result.md` exists and carries the terminal two-line fence. **With `--plan-path` (WS-A / issue #2124)** it ALSO verifies each result file verifiably cites the real plan — a verbatim normalized substring of at least `MIN_GROUNDING_QUOTE_LEN` characters (provisional default 24, env-overridable via `MIN_GROUNDING_QUOTE_LEN`) that appears in the plan, OR a plan section header. A fenced-but-ungrounded result (the fabricated-critique signal from #2124) is reported in an `ungrounded` list and counted as an incomplete member — bounded re-dispatch, then the `MAJOR REWORK (CRITIQUE INCOMPLETE)` STOP. `$PLAN_PATH` is the ABSOLUTE path resolved in Plan Resolution (WS-B), so the grounding read never fails against a `.claude/worktrees/agent-*` cwd. Omitting `--plan-path` yields the legacy fence-only gate (generic/foreign-repo safety). It prints a JSON gate decision and exits 0 when the full roster is complete:

```json
{"complete": true, "missing": [], "present": ["Risk & Robustness","Scope & Value","History & Consistency"], "roster_count": 3, "completed_count": 3}
```

Step 4 (aggregation) runs **only after this gate reports `complete: true`**. Step 4 then iterates the full `_roster.json` manifest — every named roster member — rather than "the result files that are present," so a missing file surfaces as a visible gap rather than being silently skipped.

**Why the two-line terminal fence is structurally unforgeable.** Two guarantees compose:

1. *Truncation guard.* Placing the fence at the end of the file (terminal position) means "fence present" is equivalent to "body fully written, then the critic stamped the fence as its deliberate final act." A sentinel written on line 1 would let a critic that crashes after writing the first line pass the gate with an empty or garbage body.

2. *Token-collision guard.* The bare line `STATUS: COMPLETED` is forgeable: critics in this skill routinely quote that exact string in findings prose. Requiring `<<<CRITIQUE-RESULT-COMPLETE>>>` as the penultimate line — a token no critic emits in ordinary prose — makes the fence impossible to forge by quoted output. A file whose body merely ends on the bare `STATUS: COMPLETED` line (without the preceding delimiter) does NOT pass the gate.

A truncated or token-colliding write can only produce a STOP or re-dispatch — the failure direction is always loud, never a silent green.

**Bounded `MAX_CRITIC_REDISPATCH` cap.** If the gate reports `complete: false`, the skill re-dispatches **only the missing critics** (foreground, no `run_in_background`). The cap is named and fixed: **1 initial dispatch + up to 2 re-dispatches = 3 attempts maximum per critic**. There is no unbounded retry or polling loop.

**`MAJOR REWORK (CRITIQUE INCOMPLETE)` STOP verdict.** If the roster is still incomplete after the re-dispatch cap, the skill records the verdict string:

```
MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: {names})
```

through the normal Step 5.5 path, then sets the `plan_revising` lock (Step 5.6). The substring `MAJOR REWORK` matches the SDLC router's guard **G1** verbatim, routing back to `/do-plan`. The stage always produces a verdict — it never returns empty and never lingers at `in_progress`.

**Cleanup gated on `complete: true`.** After Step 5.5/5.6, `${CRITIQUE_RUN_DIR}` is deleted **only on the `complete: true` path**. On the incomplete / `CRITIQUE INCOMPLETE` path the run dir is **preserved** as forensic evidence of which critics never reported.

### Step 5.5 — mandatory finalize (unchanged from #1654)

**Step 5.5 is mandatory and reached on every exit path.** Every verdict (READY TO BUILD, NEEDS REVISION, MAJOR REWORK, or CRITIQUE INCOMPLETE) flows through a single self-contained block that:
1. Records the verdict via `sdlc-tool verdict record --stage CRITIQUE ... --run-id "$RUN_ID"` so the router's G1/G5 guards can consume it.
2. On a READY TO BUILD verdict ONLY, writes the completion stage-marker (`sdlc-tool stage-marker --stage CRITIQUE --status completed ... --run-id "$RUN_ID"`) **co-located in the same block** so the verdict and marker can never desync.

### Context: prior fixes

- **#1654** (v1.3.0) added Step 3.5 "Wait and Collect" as a prose barrier: the driver was instructed to block on all background critics before aggregating. The barrier lived only in prose aimed at an LLM and failed when the driving subagent returned early, dropping a BLOCKER finding from a late-arriving Adversary critic in the #1681 run.
- **#1690** (v1.4.0, this fix) replaces the prose-await with the artifact-based roster barrier described above. The `critique-roster-check` helper is independently verifiable: a test can create and omit result files and assert the gate behaves — the barrier is checkable, not merely asserted. The `docs/sdlc/` addendum and the `tests/unit/test_do_plan_critique_barrier.py` regression test both reference #1690.

## Multi-Machine Deployment

This repo runs on multiple machines (see `docs/deployment.md`). The History & Consistency critic (Archaeologist lens) should check:
- Does the plan require a new env var? It must be added to `.env.example` and `config/settings.py`
- Does the plan introduce new dependencies? They must be propagated via the update system
- Are there race conditions between machines running `/update` simultaneously?
