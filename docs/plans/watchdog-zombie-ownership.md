---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-30
tracking: https://github.com/tomcounsell/ai/issues/3592
last_comment_id:
---

# Bridge watchdog stops killing `claude` processes by age

## Problem

The bridge watchdog (`monitoring/bridge_watchdog.py`, launchd, every 60s) lists every process
whose `ps` line contains `"claude "` or `"pyright"` and SIGTERMs (then SIGKILLs) any older than
2 hours. Age is the only evidence. It does not check parent, TTY, CPU, output, or whether an
AgentSession owns the process.

On 2026-09-29 an interactive Claude Code session supervising the `/do-sdlc` pipeline for #3588
was killed three times, two hours apart, and each time its in-flight pipeline subagent died
with it. The kills continued on 2026-09-30 (00:21, 02:22 local). Each one is logged as
"1 zombie process(es) cleaned up" and recovery "level 0 (no action needed)", so a human's
working session dying reads as routine hygiene.

**Current behavior:** any `claude` or `pyright` process alive for 2h is killed, working or not.
Interactive sessions, SDLC supervisors, IDE language servers, and any worker turn past 2h are all
cut off. The kill log records PID, age, and RSS only.

**Desired outcome:** the watchdog never signals a `claude` or `pyright` process. Orphan cleanup
stays with the one component that can prove ownership: the worker's orphan reapers, which
already require PPID==1 (or an orphaned `sh -c` parent) plus no live owning AgentSession. Every
orphan kill log line carries the command, parent PID, and the evidence behind the verdict.

## Freshness Check

(filled below)

## Prior Art

(filled below)

## Research

(filled below)

## Data Flow

(filled below)

## Architectural Impact

(filled below)

## Appetite

**Size:** Small

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

(filled below)

## Failure Path Test Strategy

(filled below)

## Test Impact

- [ ] `tests/unit/test_bridge_watchdog.py` — UPDATE (details below)

## Rabbit Holes

(filled below)

## Risks

(filled below)

## Race Conditions

(filled below)

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this change deletes code from a launchd-run script that
`/update` already restarts; there are no new dependencies, config keys, or migrations.

## Agent Integration

No agent integration required — this is a watchdog/worker-internal change with no new tool or
CLI surface.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` (details below)

## Success Criteria

(filled below)

## Team Orchestration

(filled below)

## Step by Step Tasks

(filled below)

## Verification

(filled below)

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

(filled below)
