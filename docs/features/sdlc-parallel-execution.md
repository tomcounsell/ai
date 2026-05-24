# SDLC Parallel Execution

Multi-dev fan-out within a single issue (Phase 1) and DAG stage dispatch
across parallel-safe stage pairs (Phase 2). Tracked by issue #1393;
supersedes #1307 and #1308.

## What changed

Before this feature, the SDLC pipeline was strictly linear: each stage
blocked until its predecessor completed, and within a single issue the PM
created exactly one Dev session at a time. A plan with independent frontend
and backend work executed them serially, doubling or tripling wall-clock
build time with no technical reason.

Two parallel paths now exist:

| Path | Stage | Mechanism | Cap |
|------|-------|-----------|-----|
| Phase 1 — Multi-dev fan-out | BUILD | `sdlc-decompose` + sub-slug Dev sessions + existing `valor-session wait-for-children` | `MAX_PARALLEL_DEVS` (default 3) |
| Phase 2 — DAG stage dispatch | post-REVIEW | `MultiDispatch` from `decide_next_dispatch` + `pthread` skill | `PARALLEL_SAFE_PAIRS` (currently `{DOCS, PATCH}`) |

Phase 1 is strictly additive — the single-dev path is unchanged. Phase 2
widens the router's return type from `Dispatch | Blocked` to
`Dispatch | MultiDispatch | Blocked` and seeds a single parallel-safe pair
(`DOCS`/`PATCH`).

## Phase 1 — Multi-dev fan-out (BUILD)

### Trigger

When a plan's `## Implementation Plan` section decomposes cleanly into
multiple independent work units, the PM may fan out one Dev sub-session per
unit rather than running a single Dev session through the entire plan.

Two units are *independent* when they touch disjoint sets of files AND can
be implemented without one's output. Tests for a unit live with the unit;
documentation is its own unit only when it does not depend on
implementation details.

### Sub-slug naming

Sub-slugs follow the pattern `{slug}-u{i}` where `i` is the unit index from
`sdlc-decompose`. Examples for issue #1393:

- `sdlc-1393-u1` — Phase 1 decompose CLI
- `sdlc-1393-u2` — Phase 2 router widening
- `sdlc-1393-merge` — integration session that merges sub-slug branches

Each sub-slug produces a distinct `worker_key` in `AgentSession`, so the
worker (per-`worker_key` FIFO queue) executes them concurrently. The
existing `worktree_manager.get_or_create_worktree` allocates one worktree
per sub-slug at `.worktrees/{slug}-u{i}/`.

### `sdlc-decompose` CLI

```bash
sdlc-decompose docs/plans/{slug}.md
sdlc-decompose docs/plans/{slug}.md --max-units 3
```

Reads the plan's `## Implementation Plan` section, calls Claude (haiku) to
identify independent units, validates the output schema, enforces the
`MAX_PARALLEL_DEVS` cap, and prints a JSON array on stdout:

```json
[
  {
    "unit_id": "phase1_decompose_cli",
    "description": "Build sdlc-decompose CLI and register entry point",
    "tasks": ["Task 1.1 — ...", "Task 1.2 — ...", "..."]
  },
  {
    "unit_id": "phase2_router_widening",
    "description": "Add MultiDispatch and PARALLEL_SAFE_PAIRS to router",
    "tasks": ["Task 2.1 — ...", "..."]
  }
]
```

Exit codes:

- `0` — JSON printed; the PM proceeds with fan-out.
- `1` — fatal error (plan not found, malformed JSON from Claude, schema
  violation, over-cap decomposition). The PM falls back to a single-dev
  BUILD on `/do-build`.
- `2` — usage error.

Schema rules (all enforced before printing):

- `unit_id` is a non-empty `[a-z0-9_]+` snake_case string and unique within
  the response.
- `description` is a non-empty string.
- `tasks` is a non-empty list of non-empty strings.
- `len(units) <= max_units` — over-cap decompositions fail closed; this is
  intentional. Multi-wave queueing is explicitly out of scope (see issue
  #1393, "Rabbit Holes").

The cap is read from `agent.sdlc_router.MAX_PARALLEL_DEVS` (default `3`) and
can be overridden per invocation via `--max-units N`.

### Fan-out workflow

After `sdlc-decompose` returns a multi-unit array, the SDLC skill steps the
PM through this sequence (documented in `.claude/skills-global/sdlc/SKILL.md`):

1. **Sequential session creation** — for each unit `u_i`:

   ```bash
   valor-session create --role dev --parent $AGENT_SESSION_ID \
     --slug {slug}-u{i} \
     --message "Implement unit {u_i}: {description}. Tasks: ..."
   ```

   Sequential, never parallel — parallel creation collides on the
   timestamp-based session ID (see `feedback_sequential_session_creation`).

2. **Wait for children** — the PM calls the **existing** CLI:

   ```bash
   valor-session wait-for-children --session-id $AGENT_SESSION_ID
   ```

   This transitions the PM to status `waiting_for_children`. The lifecycle
   hook `_finalize_parent_sync` in `models/session_lifecycle.py` auto-resumes
   the PM when every child reaches a terminal status. Phase 1 reuses this
   transition-and-resume mechanism unchanged — no polling CLI is added.

3. **On resume**:

   - **Failed children** (terminal status != `completed`): steer each failed
     child via `valor-session steer --id <id> --message "fix: ..."` rather
     than spawning a replacement. Re-call `wait-for-children`. Loop until
     every child completes.
   - **All children completed**: dispatch one merge-integration Dev session:

     ```bash
     valor-session create --role dev --parent $AGENT_SESSION_ID \
       --slug {slug}-merge \
       --message "git checkout session/{slug}; git merge session/{slug}-u1 session/{slug}-u2 ... in unit_id order. On conflict, write conflict file list to last_error and exit non-zero."
     ```

4. **After merge** — steer to TEST stage on the parent slug. The single-dev
   path resumes (TEST, REVIEW, DOCS, MERGE remain serial in Phase 1).

### Failure modes

- **`sdlc-decompose` returns over-cap**: exit 1; PM falls back to
  single-dev BUILD. Phase 1 explicitly does not queue overflow waves.
- **Sub-slug merge conflict**: the merge-integration session writes the
  conflict file list to `last_error` and exits non-zero. The PM escalates
  to the human; no automated conflict resolution.
- **Worker not running**: `valor-session create` warns to stderr but
  enqueues; sub-sessions execute when the worker boots.

## Phase 2 — DAG stage dispatch (post-REVIEW)

### `MultiDispatch` return type

`agent.sdlc_router.decide_next_dispatch` now returns
`Dispatch | MultiDispatch | Blocked`:

```python
@dataclass(frozen=True)
class MultiDispatch:
    dispatches: list[Dispatch]
    reason: str
```

A `MultiDispatch` is returned when:

1. The first matching `DISPATCH_RULES` row produces a primary `Dispatch`.
2. The primary dispatch's *stage* (e.g., `PATCH` for `/do-patch`) is in a
   `PARALLEL_SAFE_PAIRS` pair.
3. The other stage in that pair is in a dispatchable state
   (`ready` | `pending` | `failed`).
4. A different `DISPATCH_RULES` row produces a `Dispatch` whose skill
   resolves to that other stage.

Guards (G1–G7) are evaluated BEFORE the parallel-pair scan. A single guard
fire short-circuits the entire `MultiDispatch` — there is no partial
dispatch. This is intentional: e.g., G3 (PR-lock plan-stage dispatch) and
G6 (terminal merge fast-path) are both stronger signals than parallel-safety.

### `PARALLEL_SAFE_PAIRS`

```python
PARALLEL_SAFE_PAIRS: set[frozenset[str]] = {frozenset({"DOCS", "PATCH"})}
```

Pairs are *stages*, not skills. Currently only `{DOCS, PATCH}` is seeded:
after REVIEW completes with `PARTIAL` or `CHANGES REQUESTED` findings, the
PM may dispatch `/do-patch` (row 8) and `/do-docs` (row 9) concurrently
because writing/refreshing user docs has no data dependency on the patch
content.

This set is intentionally separate from `agent/pipeline_graph.PIPELINE_EDGES`
(which is a state-machine transition table keyed by `(stage, outcome)`).
Parallel-safety is a dispatch-time decision, not a graph topology fact.

### `sdlc-tool next-skill` JSON output

The CLI wrapper at `tools/sdlc_next_skill.py` emits a third response shape
alongside the existing single-dispatch and blocked shapes:

```json
{
  "multi": true,
  "dispatched": true,
  "skills": ["/do-docs", "/do-patch"],
  "dispatches": [
    {"skill": "/do-docs", "reason": "...", "row_id": "9"},
    {"skill": "/do-patch", "reason": "...", "row_id": "8"}
  ],
  "reason": "parallel-safe pair: /do-docs (9) + /do-patch (8)"
}
```

### PM orchestration via `pthread`

The SDLC skill, on receiving a multi-dispatch, invokes the existing
`pthread` skill to spawn each listed skill as a parallel sub-agent. The PM
session itself does not split. Both sub-agents must terminate (success or
failure) before the PM re-invokes `/sdlc` to re-dispatch based on the new
pipeline state.

If `pthread` is unavailable on the current machine, the PM falls back to
sequential dispatch of the listed skills in array order — the parallel
path is an optimisation, not a correctness requirement.

## Non-goals

The following are explicitly out of scope; see issue #1393 "Rabbit Holes"
and "No-Gos" for full rationale:

- Multi-PM fan-out across issues (already shipped in #786).
- Changing `worker_key` semantics.
- Parallel BUILD + TEST stages (BUILD must complete before TEST).
- Dynamic pipeline graph rewriting at runtime.
- Multi-wave queueing for over-cap decompositions.
- Polling-based wait CLI (the existing `wait-for-children` transition-and-resume
  pattern is the sole wait mechanism).
- Automated merge conflict resolution between sub-slug branches.

## See also

- `agent/sdlc_router.py` — `MultiDispatch`, `PARALLEL_SAFE_PAIRS`,
  `decide_next_dispatch`, `_find_parallel_dispatch`.
- `tools/sdlc_decompose.py` — `sdlc-decompose` CLI implementation.
- `tools/sdlc_next_skill.py` — JSON wrapper that emits the `multi` shape.
- `.claude/skills-global/sdlc/SKILL.md` — Step 4 fan-out + multi-dispatch
  PM procedures.
- `models/session_lifecycle.py` — `_finalize_parent_sync` (parent auto-resume
  on children-terminal).
- `tools/valor_session.py` — `cmd_wait_for_children`.
- `docs/features/session-isolation.md` — sub-slug worktree allocation.
