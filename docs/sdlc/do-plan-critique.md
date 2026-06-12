# do-plan-critique addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-plan-critique/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

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

## Wait-and-Collect + Mandatory Finalize (#1654)

The six war-room critics are spawned with `run_in_background: true`. The global skill's **Step 3.5 (Wait and Collect)** is a hard barrier: the skill MUST block on all six background critics and retrieve every critic's findings before aggregating. The skill owns finalization end to end — it does not yield to a supervisor for aggregation or verdict recording.

**Step 5.5 is mandatory and reached on every exit path.** Every verdict (READY TO BUILD, NEEDS REVISION, MAJOR REWORK) flows through a single self-contained block that:
1. Records the verdict via `sdlc-tool verdict record --stage CRITIQUE` so the router's G1/G5 guards can consume it.
2. On a READY TO BUILD verdict ONLY, writes the completion stage-marker (`sdlc-tool stage-marker --stage CRITIQUE --status completed`) **co-located in the same block** so the verdict and marker can never desync.

Without the wait barrier, Steps 4/5/5.5 silently never ran and the critique stalled at `in_progress` — the #1654 defect this fix removes.

## Multi-Machine Deployment

This repo runs on multiple machines (see `docs/deployment.md`). The Archaeologist critic should check:
- Does the plan require a new env var? It must be added to `.env.example` and `config/settings.py`
- Does the plan introduce new dependencies? They must be propagated via the update system
- Are there race conditions between machines running `/update` simultaneously?
