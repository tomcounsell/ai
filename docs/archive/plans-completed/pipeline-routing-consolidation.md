---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-30
tracking: https://github.com/tomcounsell/ai/issues/1216
last_comment_id:
---

# Consolidate SDLC Pipeline Routing to a Single Source of Truth

## Problem

The SDLC pipeline has three parallel routing surfaces. Only one is the actual runtime; the other two are decorative and silently drift.

| # | Surface | Claimed purpose | Actual production role |
|---|---------|-----------------|------------------------|
| 1 | `agent/pipeline_graph.py` — `PIPELINE_EDGES`, `get_next_stage()` | "Single source of truth for pipeline transitions" | State-bookkeeping helper. `PipelineStateMachine.complete_stage()`/`fail_stage()` call it to mark the next stage `"ready"`. **Never consulted to decide which sub-skill to dispatch.** |
| 2 | `agent/sdlc_router.py` — `DISPATCH_RULES` (14 rule predicates) + guards G1–G6 | "Python reference implementation of the SKILL.md algorithm" | Zero production callers. `decide_next_dispatch()` runs only in tests. Imports exactly one symbol from the graph (`MAX_CRITIQUE_CYCLES`). Has its own duplicate `STAGE_TO_SKILL` as nine `SKILL_DO_*` constants. |
| 3 | `.claude/skills/sdlc/SKILL.md` Step 4 dispatch table | "For human readability only" (line 223) | The actual runtime routing surface. The LLM reads the markdown table and pattern-matches against current `stage_states`. |

The skill markdown lies in two places. SKILL.md:222 says: *"The canonical pipeline graph is defined in `bridge/pipeline_graph.py`. All routing derives from that module."* `bridge/pipeline_graph.py` is a 15-line back-compat shim (canonical at `agent/pipeline_graph.py` since PR #601 Phase 3), and routing does not derive from the graph — it derives from a hand-rolled table the LLM reads.

**Current behavior:**
- Adding a stage requires synchronized edits to three files. Drift between the three is detectable only by `tests/unit/test_sdlc_skill_md_parity.py`, which compares two of the three (SKILL.md ↔ `DISPATCH_RULES`) and never compares against `PIPELINE_EDGES`.
- The Python "reference implementation" gives a false sense of testability — passing `decide_next_dispatch()` tests prove nothing about runtime behavior, because runtime doesn't call it.
- Six stale references to the `bridge/pipeline_*` shim paths remain in skills, commands, persona docs, and test docstrings.

**Desired outcome:**
- Exactly one source of truth for pipeline routing.
- `bridge/pipeline_graph.py` and `bridge/pipeline_state.py` shims deleted.
- All stale references to `bridge/pipeline_*` updated to `agent/pipeline_*`.
- A parity check that guarantees the routing source-of-truth and the SKILL.md (or generated equivalent) cannot drift silently.

## Freshness Check

**Baseline commit:** `f284165307f0e903f3f4f9a91603b7b288dd4a50`
**Issue filed at:** 2026-04-30T06:25:16Z
**Disposition:** Unchanged.

**File:line references re-verified during the integration audit minutes before plan creation:**
- `agent/sdlc_router.py:44` — imports only `MAX_CRITIQUE_CYCLES` — still holds
- `agent/sdlc_router.py:74-82` — duplicated `SKILL_DO_*` constants — still holds
- `agent/sdlc_router.py:627-715` — `DISPATCH_RULES` 14-row list — still holds
- `agent/sdlc_router.py:723` — `decide_next_dispatch()` definition — still holds
- `agent/pipeline_state.py:425, 536, 577, 627, 638, 643` — graph usage limited to next-stage `"ready"` marking — still holds
- `agent/session_completion.py:1099-1109` — runtime entry point for `classify_outcome` + `complete_stage`/`fail_stage` — still holds
- `bridge/pipeline_graph.py` — 15-line back-compat shim — still holds
- `bridge/pipeline_state.py` — 16-line back-compat shim — still holds
- `.claude/skills/sdlc/SKILL.md:222` — stale reference to `bridge/pipeline_graph.py` — still holds
- `.claude/commands/do-merge.md:18, 23, 102` — stale `bridge.pipeline_*` imports — still holds

**Cited sibling issues/PRs re-checked:**
- PR #601 (wire SDLC graph routing into runtime) — merged. Phase 3 moved canonical files to `agent/`. Did not touch dispatch path.
- Issue #563 (`docs/plans/wire-pipeline-graph-563.md`) — Merged. Addressed coach + subagent_stop, not dispatch.
- Issue #399 (upgrade to directed graph) — closed. Predates this issue.
- Issues #463, #704, #729, #1007 — all closed and unrelated to current scope.

**Commits on main since issue was filed (touching referenced files):** none (issue filed minutes before plan).

**Active plans in `docs/plans/` overlapping this area:**
- `agentsession-harness-abstraction.md` (status: `docs_complete Progress`) — large refactor about CLI-vs-SDK execution. Test Impact section lists `bridge.pipeline_*` import migrations for `test_pipeline_state_machine.py`, `test_pipeline_graph.py`, `test_pipeline_integrity.py`, `test_ui_sdlc_data.py`, `test_parent_child_round_trip.py`, `test_artifact_inference.py`, `test_stage_aware_auto_continue.py`, `test_routing.py`. **Coordination:** if this plan ships first, it absorbs those test migrations; if the harness plan ships first, this plan's stale-reference cleanup gets simpler.
- `sdlc-router-oscillation-guard.md` (status: `Ready`) — the plan that introduced `DISPATCH_RULES`. This plan would partially un-do it (delete the rule list, keep the guards). Acknowledge as adjacent, not blocking.

## Prior Art

- **PR #601** ([wire-pipeline-graph-563.md](wire-pipeline-graph-563.md)) — wired `classify_outcome` and `fail_stage` into the runtime via `_handle_dev_session_completion`. Moved canonical graph to `agent/`. **Did not** address the dispatch path. The "Three separate routing implementations exist, only two are used at runtime, and neither uses the graph" framing in that plan is the same problem this plan resolves.
- **PR #412** (closed predecessor of #399) — original directed-graph upgrade. Established `PIPELINE_EDGES`.
- **`docs/plans/sdlc-router-oscillation-guard.md`** (status: `Ready`) — added `DISPATCH_RULES` and guards G1–G6 as "the canonical Python implementation." Justification was test surface + parity guard for SKILL.md. The unintended consequence is the third source of truth this plan eliminates.
- **Closed issues #463, #704, #729, #1007** — adjacent SDLC tracking work. Not blockers, but signal that this area churns.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|------------------------|
| PR #601 | Wired `classify_outcome` + `fail_stage` into worker; moved graph from `bridge/` to `agent/` | Stopped at state-machine bookkeeping. The actual *dispatch* — picking which `/do-*` to invoke — remained in SKILL.md and was never wired to the graph. |
| `sdlc-router-oscillation-guard.md` (Ready) | Added `agent/sdlc_router.py` with `DISPATCH_RULES` + G1–G6 to make the routing testable | Created a new "reference implementation" but never wired it into runtime. The reference now competes with the markdown table for source-of-truth status. |
| Phase 3 of PR #601 | Moved `bridge/pipeline_graph.py` to `agent/pipeline_graph.py` and added a back-compat shim | Six callers were never migrated. Shims still exist. SKILL.md still cites the shim path as canonical. |

**Root cause pattern:** Incremental changes added new sources of truth without deleting the old ones. Every fix doubled the surfaces that need to stay in sync. The shim and the parallel rule list are both deferred-cleanup that never happened.

## Spike Results

### spike-1: Are the 14 dispatch rules expressible as graph edges?
- **Assumption:** "Most or all of the 14 `DISPATCH_RULES` rows can be re-expressed as `(stage, outcome) → next_stage` graph edges (possibly with outcome enrichment), so deleting the rule list is viable. The 6 guards (G1–G6) handle meta-state and remain as Python predicates."
- **Method:** code-read (Explore agent, ~5 min)
- **Finding:** **Assumption falsified.** Of the 14 dispatch rows, only 2 are GREEN (rows 2, 6 — pure stage-state edges). 6 are YELLOW (rows 1, 3, 4a, 7, 8, 9, 10 — expressible as edges if outcomes are enriched and verdicts are pre-computed). 6 are RED:
  - **Rows 4b/4c** read `meta["revision_applied"]` — a **plan frontmatter flag**, external to `stage_states`.
  - **Row 5** reads `context["branch_exists"]` — **runtime context**, not stored state.
  - **Row 8b** reads `meta["last_dispatched_skill"]` — **dispatch history**, prevents re-critique loops.
  - **Row 10b** is a **fallback for missing `stage_states`** — guard-level recovery, not derivable from edges.
  - Guards G1–G6 are also irreducibly meta-state (cycle counts, PR existence, hash caching, CI status, review verdicts, oscillation history).
- **Confidence:** high
- **Impact on plan:** "Delete `DISPATCH_RULES` and fold into graph + guards" is **rejected**. The graph and the rule list serve genuinely different purposes — the graph is for state-machine bookkeeping (next-stage-ready tracking), the rule list is for dispatch decisions over enriched runtime state. Consolidation strategy revised: **keep both, but make `DISPATCH_RULES` the canonical dispatch source-of-truth and make SKILL.md mechanically derived from it.** Option B (delete rules) is dropped; the plan now centers on Option B1 (LLM calls a tool that wraps `decide_next_dispatch`) or B2 (SKILL.md Step 4 table is generated from `DISPATCH_RULES` + CI parity check).

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:**
  - SKILL.md Step 4 dispatch table replaced by either (a) a single Python entry point `sdlc-tool next-skill` that returns the dispatch decision, or (b) a generated markdown rendering of `PIPELINE_EDGES` + guard list. Either way, the LLM stops pattern-matching against a hand-edited table.
  - `agent/sdlc_router.py` either keeps only G1–G6 guards (deletes `DISPATCH_RULES`) or remains as the canonical Python rules with SKILL.md auto-generated from it.
  - `bridge/pipeline_graph.py` and `bridge/pipeline_state.py` deleted. Six callers updated.
- **Coupling:** decreases — three sources collapse to one. Drift between them becomes structurally impossible (mechanical generation) or detectable in CI (single canonical source).
- **Data ownership:** unchanged — `PipelineStateMachine` still owns `stage_states`. `pipeline_graph.PIPELINE_EDGES` becomes the canonical edge set; sdlc_router becomes either gone or the guard-only module.
- **Reversibility:** medium — once shims are deleted, callers must use the new paths. The source-of-truth choice (Option A/B/C) is harder to reverse if it ships, but Phase 1 (mechanical migration) is fully reversible.

## Appetite

**Size:** Medium

**Team:** Solo dev, plus a critique pass.

**Interactions:**
- PM check-ins: 1 (confirm Option B vs. an alternative before /do-build, after spike-1 lands).
- Review rounds: 1.

**Phasing:** the work splits into three logical phases. Phase 1 ships even if the design question stalls.

| Phase | Scope | Independent? |
|-------|-------|--------------|
| Phase 1: Stale-reference cleanup | Migrate the six `bridge/pipeline_*` callers to `agent/pipeline_*`. Delete the two shims. Update SKILL.md:222. | Yes — pure mechanical refactor. |
| Phase 2: Single source of truth for dispatch | Either delete `DISPATCH_RULES` and fold into graph + guards (Option B), or keep `DISPATCH_RULES` and generate SKILL.md from it (Option C). Wire the LLM router to consult the canonical source via tool. | Depends on Phase 1 being done first to avoid double-edit. |
| Phase 3: Stage groups | Add edge-level metadata to permit one Dev session to span multiple unconditional-success stages. | **Spin out as a separate plan** if scope grows. Mention as a No-Go for this plan if Phase 2 expands. |

If Phase 2 reveals significant complexity, Phase 3 splits into its own plan. The Medium appetite covers Phases 1 + 2.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` CLI authenticated | `gh auth status` | Read/edit issue #1216, open PR |
| Repo clean state | `git diff --quiet && git diff --cached --quiet` | Avoid contaminating other in-flight work |

No external API keys, services, or environment variables required.

## Solution

### Key Elements

- **Phase 1: Migration cleanup**
  - Rewrite the six stale callers to import from `agent.pipeline_graph` / `agent.pipeline_state` directly.
  - Delete `bridge/pipeline_graph.py` and `bridge/pipeline_state.py` shims.
  - Update SKILL.md:222 to point at `agent/pipeline_graph.py` and remove the false claim that "all routing derives from that module" (or make the claim true via Phase 2).

- **Phase 2: Source-of-truth consolidation (post-spike-1 direction)**
  - **Spike-1 conclusion:** the graph and the rule list serve different purposes and cannot collapse into one. Keep both:
    - `agent/pipeline_graph.py` (`PIPELINE_EDGES`, `STAGE_TO_SKILL`, `get_next_stage()`) — canonical for *state-machine bookkeeping* (which stage is next-ready when one completes).
    - `agent/sdlc_router.py` `DISPATCH_RULES` + guards G1–G6 — canonical for *dispatch decisions* (which `/do-*` to invoke given current state + meta + context).
  - The third surface — SKILL.md Step 4 hand-edited table — is the one to eliminate. It has no unique role; it's a manual rendering of `DISPATCH_RULES` that drifts.
  - Replace SKILL.md Step 4 hand-edited table with one of:
    - **B1 (proposed):** A single CLI call (`sdlc-tool next-skill --issue-number N`) that runs `agent.sdlc_router.decide_next_dispatch()` over the live `stage_states` + meta + context, and returns a JSON dispatch decision. SKILL.md Step 4 becomes "call the tool, dispatch the skill it returns, log the reason." The LLM stops authoring routing decisions; it executes the tool's output. `decide_next_dispatch()` becomes a real production path (currently test-only).
    - **B2:** Auto-generate the SKILL.md Step 4 table from `DISPATCH_RULES` row docstrings + a small Jinja-style template. Add a CI check that fails if the generated section diverges from the rules. LLM continues reading markdown, but the markdown cannot drift.
  - The final pick (B1 vs. B2) is the user's PM check-in. **Default proposal: B1**, because it eliminates the LLM's role as a router-author entirely — the LLM still chooses *whether* to dispatch (e.g., paused for human input) but not *which* skill.
  - Update SKILL.md:222 to remove the false claim that "all routing derives from `bridge/pipeline_graph.py`". Replace with an accurate statement of the graph/router/guards separation of concerns.

- **Phase 3: Stage groups (split if needed)**
  - Add `group: str | None` metadata to graph edges. Edges within the same group permit one Dev session to span multiple stages without returning to PM.
  - Update SKILL.md Hard Rule #7 to allow same-group continuation.
  - Define dev-session protocol for marking intermediate stage completion via `sdlc-tool stage-marker` at boundaries.
  - Constrain groups to "edges with same Dev model" (otherwise per-stage model selection breaks).

### Flow

**Current dispatch flow (broken):**
PM session → `/sdlc` → LLM reads SKILL.md Step 4 table → pattern-matches row against `stage_states` → invokes `/do-X` → Dev session runs → completes → `_handle_dev_session_completion` updates `PipelineStateMachine` → PM steered → repeat

**Phase 2 Option B1 flow (proposed):**
PM session → `/sdlc` → LLM calls `sdlc-tool next-skill` → tool runs `decide_next_dispatch(stage_states, meta, context)` (from `agent/sdlc_router.py`, which evaluates guards G1–G6 then walks DISPATCH_RULES) → returns `(skill, reason, row_id)` JSON → LLM invokes returned skill → Dev session → completes → `_handle_dev_session_completion` updates `PipelineStateMachine` (still uses `PIPELINE_EDGES` for next-ready bookkeeping) → PM steered → repeat

`DISPATCH_RULES` becomes the dispatch source of truth. `PIPELINE_EDGES` remains the state-machine bookkeeping source of truth. SKILL.md stops being a routing surface — it's a runbook that says "call the tool" and documents the guards/rules in prose for human readers, with the prose either generated or covered by a parity check.

### Technical Approach

- **Phase 1 — pure migration.** A `git grep -l "bridge\.pipeline_graph\|bridge\.pipeline_state"` produces the exact file list. Sed-like replacement plus targeted SKILL.md edit. No semantic changes. Delete shims when grep returns empty.
- **Phase 2 — Option B1 (proposed, post-spike-1).**
  - Add `tools/sdlc_next_skill.py` exposing `sdlc-tool next-skill --issue-number N` that:
    - Resolves the active session via the existing `sdlc-tool` resolver (`AI_REPO_ROOT` + session lookup).
    - Reads `stage_states`, builds `_meta` (the same way `tools/sdlc_stage_query.py` does), builds `context` (current plan hash for G5, branch_exists check for row 5).
    - Calls `agent.sdlc_router.decide_next_dispatch(stage_states, meta, context)`.
    - Outputs JSON: `{"skill": "/do-X", "reason": "...", "row_id": "...", "dispatched": true}` or `{"blocked": true, "reason": "...", "guard_id": "G4"}`.
  - Rewrite `.claude/skills/sdlc/SKILL.md` Step 4: replace the table with a single tool invocation. Step 3.5 (Legal Dispatch Guards) is preserved for human-readable docs but no longer the source of routing logic.
  - Wire the tool into the existing dispatch-record flow: `sdlc-tool next-skill` invokes `decide_next_dispatch`, then `sdlc-tool dispatch record` is called BEFORE the sub-skill runs (existing pattern at SKILL.md:160).
  - Update SKILL.md:222 to: "Pipeline state transitions live in `agent/pipeline_graph.py` (state-machine bookkeeping). Dispatch logic lives in `agent/sdlc_router.py` (`decide_next_dispatch`). Both are accessed at runtime via `sdlc-tool`."
  - Behavioral parity: drive the existing 12-step regression sequence in `tests/unit/test_sdlc_router_decision.py` through the new tool path and assert identical decisions.
  - **Risk-2 mitigation rollout:** Ship behind `SDLC_ROUTER_SOURCE` env var. `SDLC_ROUTER_SOURCE=table` (default for one cycle) keeps the LLM table-match behavior. `SDLC_ROUTER_SOURCE=tool` flips to `sdlc-tool next-skill`. Default flips after one cycle of soak; flag is removed in a follow-up.
- **Phase 3 — deferred.** Sketched only. If Phase 1+2 ship cleanly within the medium appetite budget, open a follow-up issue for stage groups; do not bundle.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks introduced. Existing predicate try/except in `decide_next_dispatch` (line 760) is removed if `DISPATCH_RULES` is deleted.
- [ ] If Option B1 ships, the new `sdlc-tool next-skill` CLI must surface guard `Blocked` outcomes as a non-zero exit + structured stderr — not silently fall through to default.

### Empty/Invalid Input Handling
- [ ] Test: `sdlc-tool next-skill` with empty `stage_states` returns Row 1 (`/do-plan`) consistent with current behavior.
- [ ] Test: `sdlc-tool next-skill` with `stage_states={"MERGE": "completed"}` returns terminal/no-dispatch.
- [ ] Test: missing `_meta` keys default sensibly (matches current `decide_next_dispatch` behavior in `agent/sdlc_router.py:752-753`).

### Error State Rendering
- [ ] If Phase 2 ships Option B1, the LLM-facing tool output must be machine-parseable JSON (not free text). The skill markdown must specify the parsing contract.
- [ ] Document the new dispatch tool's output format in `docs/features/pipeline-graph.md` and `.claude/skills/sdlc/SKILL.md`.

## Test Impact

- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — REPLACE: the current test parses the SKILL.md Step 4 table and compares row predicates against `DISPATCH_RULES.__doc__`. After Phase 2, the table is gone (B1) — rewrite to assert the new SKILL.md only references `sdlc-tool next-skill` for dispatch and never re-invents the rule logic. Keep the `DISPATCH_RULES` predicate-docstring quality check as a separate unit test.
- [ ] `tests/unit/test_sdlc_router_decision.py` — UPDATE: Phase 2 makes `decide_next_dispatch()` a runtime entry point. Add tests that drive the new `sdlc-tool next-skill` CLI end-to-end (subprocess-style) to confirm the JSON output schema. Existing 14+ pure-function tests against `decide_next_dispatch()` keep their assertions; the function is now production code, not test-only.
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — UPDATE: imports unchanged (G4 logic stays in `agent/sdlc_router.py`). Add a new test asserting that Phase 2's CLI surfaces a `Blocked` decision as a non-zero exit + structured stderr (so PM can detect oscillation downstream).
- [ ] `tests/unit/test_pipeline_graph.py` — no change. Phase 2 keeps `PIPELINE_EDGES` semantics unchanged.
- [ ] `tests/unit/test_pipeline_integrity.py` — UPDATE: change `bridge.pipeline_*` imports to `agent.*` (Phase 1, line 207, 212, 223, 229).
- [ ] `tests/unit/test_pipeline_state_machine.py` — no behavioral change.
- [ ] `tests/integration/test_artifact_inference.py` — UPDATE: change `bridge.pipeline_*` imports to `agent.*` (Phase 1).
- [ ] `tests/e2e/test_routing.py` — UPDATE: fix docstring claim about `bridge/pipeline_graph.py` (line 5); imports already correct (Phase 1).
- [ ] `tests/unit/test_architectural_constraints.py` — no change. The test asserts `decide_next_dispatch` exists in `agent.sdlc_router`; that survives Phase 2.
- [ ] **New:** `tests/integration/test_sdlc_next_skill_cli.py` — CREATE: drive the new CLI against fixture sessions and assert JSON output matches `decide_next_dispatch` output for the same inputs. This is the regression net for the migration.

## Rabbit Holes

- **Don't redesign `PipelineStateMachine`.** It already does state bookkeeping correctly via the graph. Touching it expands scope into PR #601's territory. Constrain Phase 2 to the dispatch-decision path only.
- **Don't re-litigate the graph's edge structure.** `PIPELINE_EDGES` was settled in PR #601. Phase 2 may *enrich outcomes* (split `"success"` into discriminated variants where rules require it) but does NOT remove edges or change happy-path topology.
- **Don't bundle stage-groups (Phase 3) into Phases 1+2.** Stage groups touch dev-session boundaries, model-selection plumbing, and PM steering protocol. That's a feature, not a refactor. Split it out the moment scope feels strained.
- **Don't try to make the LLM stop reading markdown entirely.** Even with Option B1, the surrounding skill prose (Steps 1–3, guards documentation, hard rules) stays in SKILL.md. Only the Step 4 dispatch table is replaced by a tool call.
- **Don't write a new oscillation/cycle counter.** G4 `same_stage_dispatch_count` is already plumbed via `_sdlc_dispatches`. Reuse it.

## Risks

### Risk 1: Hidden behavioral coupling between `DISPATCH_RULES` and SKILL.md
**Impact:** If the LLM has implicitly learned to disambiguate between rules in ways the Python rules also encode but the graph doesn't, deleting `DISPATCH_RULES` could break edge cases that no test catches.
**Mitigation:** spike-1 must catalog all RED rows (irreducible to graph edges). For each RED row, decide explicitly: keep as guard, encode as enriched outcome, or accept as a behavioral simplification with an explicit test. Never silently drop a rule.

### Risk 2: SKILL.md changes break ongoing PM sessions mid-conversation
**Impact:** Active SDLC sessions reading the old markdown could see different routing than the new code.
**Mitigation:** Roll out by phases. Phase 1 changes only documentation references (no behavior change). Phase 2 ships behind an env-var flag (`SDLC_ROUTER_SOURCE=table|tool`) for one cycle, then defaults to the new path and removes the flag in a follow-up. Document the flip in the deploy notes.

### Risk 3: Outcome enrichment leaks into PipelineStateMachine semantics
**Impact:** If new outcome strings (e.g., `"ready_with_concerns_pre_revision"`) flow through `complete_stage(stage, outcome)`, they may collide with existing `"success"`/`"fail"`/`"partial"` semantics that other code paths rely on.
**Mitigation:** Enriched outcomes live only at the graph-lookup layer. `PipelineStateMachine` continues to use the canonical 3-value outcome set. The enrichment is a router input, not a state-machine input.

### Risk 4: Coordination collision with `agentsession-harness-abstraction.md`
**Impact:** Both plans want to migrate `bridge/pipeline_*` test imports. If both ship in flight, merge conflicts on the same files.
**Mitigation:** Phase 1 of this plan acquires the migrations explicitly. The harness plan removes them from its Test Impact section once Phase 1 lands. Coordinate with whoever picks up the harness plan before /do-build.

## Race Conditions

No race conditions identified. All routing decisions and graph traversal are pure functions over current `stage_states`. The only concurrency surface is `_sdlc_dispatches` history writes via `tools.stage_states_helpers.update_stage_states`, which is already protected by optimistic-retry as documented in `agent/sdlc_router.py:777-836`. This plan does not introduce new shared mutable state.

## No-Gos (Out of Scope)

- **Stage groups (Phase 3).** Sketched in the issue but split out as a follow-up plan if Phase 1+2 absorb the medium appetite. Touching dev-session boundaries, model selection, and PM steering protocol is its own architectural change.
- **Re-litigating `PIPELINE_EDGES` topology.** The happy path and failure cycles are correct as of PR #601. Outcome enrichment only.
- **`PipelineStateMachine` redesign.** State machine semantics stay as-is.
- **New cycle/oscillation logic.** G4 counter already works.
- **Cross-repo SDLC tooling changes.** `sdlc-tool` already cross-repo via `AI_REPO_ROOT` resolver. New `sdlc-tool next-skill` (if Option B1) follows the same pattern.
- **Auto-classification of issue/plan types.** Out of scope; orthogonal.

## Update System

No update system changes required. This refactor is purely internal to the `ai/` repo. The `/update` skill (`scripts/remote-update.sh`) does not interact with any of the files this plan touches; deploys propagate via normal git pull + service restart.

## Agent Integration

Two integration considerations:

1. **`/sdlc` skill consumption** — The agent (PM session) reaches the new dispatch logic via the existing `sdlc-tool` CLI namespace. Phase 2 Option B1 introduces `sdlc-tool next-skill` as a new sub-command. This requires a new entry point declaration in `pyproject.toml [project.scripts]` only if `sdlc-tool` doesn't already auto-discover sub-commands. Currently `sdlc-tool` is wired via `tools/sdlc_*` modules; `sdlc-tool next-skill` would add `tools/sdlc_next_skill.py` and register the dispatch sub-command in `tools/sdlc_tool_resolver.py` (or whichever dispatcher the resolver uses).
2. **No bridge-level changes.** The bridge (`bridge/telegram_bridge.py`) does not invoke routing logic directly. Bridge code only reads `bridge/pipeline_graph.py` via the `do-merge.md` command (which runs in a Dev session subprocess), and that path is fixed in Phase 1.

Integration test: `tests/integration/test_sdlc_dispatch.py` (new) — drive a fixture `stage_states` through the new tool and assert the same outputs the current `decide_next_dispatch()` produces. This is the regression net for the migration.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pipeline-graph.md` to describe the new single source of truth (graph + guards). Remove or rewrite the "Three previously duplicated definitions" section since two are now gone.
- [ ] Update `docs/features/sdlc-pipeline-integrity.md` if it exists, or create it, to describe the parity guarantee between graph and dispatch.
- [ ] Add a new note to `docs/features/README.md` if not already linked.

### Inline Documentation
- [ ] Update `agent/sdlc_router.py` module docstring to reflect post-cleanup state (guard-only module, or fully deleted).
- [ ] Update `agent/pipeline_graph.py` module docstring to describe enriched outcomes if Phase 2 introduces them.
- [ ] Remove the "all routing derives from that module" claim in `.claude/skills/sdlc/SKILL.md:222` and replace with the actual current relationship.

### Stale Reference Updates (Phase 1)
- [ ] `.claude/skills/sdlc/SKILL.md:222` — point at `agent/pipeline_graph.py`
- [ ] `.claude/commands/do-merge.md:18, 23, 102` — switch to `agent.pipeline_graph` / `agent.pipeline_state`
- [ ] `.claude/skills/do-build/SKILL.md:500` — switch to `agent/pipeline_state.py`
- [ ] `.claude/skills/do-test/SKILL.md:611` — switch to `agent/pipeline_state.py`
- [ ] `config/personas/project-manager.md:191` — switch to `agent/pipeline_graph.py`
- [ ] `tests/e2e/test_routing.py:5` — fix docstring (imports are already correct)

## Success Criteria

- [ ] **Phase 1 — shim deletion proof:** `find bridge/ -name 'pipeline_*.py'` returns empty after migration.
- [ ] **Phase 1 — stale-reference proof:** `grep -rn "bridge\.pipeline_graph\|bridge\.pipeline_state" --include="*.py" --include="*.md"` returns zero matches outside `docs/plans/completed/`.
- [ ] **Phase 2 — single dispatch source:** `decide_next_dispatch()` either deleted or wired into runtime such that test-only callers and prod callers share the same code path.
- [ ] **Phase 2 — drift impossible:** the parity test rewrite asserts the SKILL.md routing surface (table or tool contract) is mechanically derived from the canonical source — not hand-edited in two places.
- [ ] **Phase 2 — guard preservation:** all 6 guards (G1–G6) still fire correctly. Existing `tests/unit/test_sdlc_router_oscillation.py` and the relevant subset of `test_sdlc_router_decision.py` continue to pass after rewrite.
- [ ] **Behavioral parity:** for the 12-step regression sequence in `test_sdlc_router_decision.py`, the new dispatch path produces identical decisions to the current LLM table-match. Verified by replaying the sequence against the new entry point in test.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms no remaining `bridge.pipeline_*` imports anywhere outside `docs/plans/completed/`

## Team Orchestration

### Team Members

- **Builder (phase-1-migration)**
  - Name: `migration-builder`
  - Role: Phase 1 stale-reference cleanup + shim deletion. Pure mechanical refactor.
  - Agent Type: builder
  - Resume: true

- **Validator (phase-1)**
  - Name: `migration-validator`
  - Role: Verify zero remaining `bridge.pipeline_*` references; confirm shims deleted; tests still pass.
  - Agent Type: validator
  - Resume: true

- **Builder (phase-2-router)**
  - Name: `router-builder`
  - Role: Phase 2 source-of-truth implementation. Wire `sdlc-tool next-skill` (Option B1) or generate SKILL.md table (Option C/B2). Decision deferred to PM check-in after spike-1.
  - Agent Type: builder
  - Resume: true

- **Validator (phase-2)**
  - Name: `router-validator`
  - Role: Verify behavioral parity between old LLM table-match and new dispatch path. Run the 12-step regression sequence.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `routing-documentarian`
  - Role: Update `docs/features/pipeline-graph.md`, `.claude/skills/sdlc/SKILL.md`, and module docstrings. Verify all six stale references are fixed.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Phase 1: Migrate stale references
- **Task ID:** build-phase-1-migration
- **Depends On:** none
- **Validates:** `tests/integration/test_artifact_inference.py`, `tests/e2e/test_routing.py`, all skill+command markdown loads
- **Informed By:** none
- **Assigned To:** `migration-builder`
- **Agent Type:** builder
- **Parallel:** false (single-file edits across the whole list)
- Migrate each of the six stale references to the canonical `agent/pipeline_*` paths
- After zero matches remain, delete `bridge/pipeline_graph.py` and `bridge/pipeline_state.py`
- Verify: `grep -rn "bridge\.pipeline_graph\|bridge\.pipeline_state" --include="*.py" --include="*.md" | grep -v "^docs/plans/completed/"` returns empty

### 2. Phase 1 validation
- **Task ID:** validate-phase-1
- **Depends On:** build-phase-1-migration
- **Assigned To:** `migration-validator`
- **Agent Type:** validator
- **Parallel:** false
- Confirm both shim files are gone
- Run `pytest tests/unit/test_pipeline_graph.py tests/unit/test_pipeline_state_machine.py tests/unit/test_pipeline_integrity.py` — must pass
- Run full unit suite — no regressions

### 3. PM check-in: confirm B1 vs B2
- **Task ID:** decide-skill-interface
- **Depends On:** validate-phase-1
- **Assigned To:** Valor
- **Agent Type:** human
- **Parallel:** false
- Spike-1 settled that `DISPATCH_RULES` stays as canonical dispatch source. Remaining question: how SKILL.md interfaces with it.
- Pick B1 (LLM calls `sdlc-tool next-skill`) or B2 (generated SKILL.md table with CI parity check).
- Default proposal: B1. User confirms or overrides.

### 4. Phase 2: Implement chosen interface
- **Task ID:** build-phase-2-router
- **Depends On:** decide-skill-interface
- **Validates:** `tests/unit/test_sdlc_router_decision.py` (extended), `tests/unit/test_sdlc_router_oscillation.py`, `tests/unit/test_sdlc_skill_md_parity.py` (rewritten), `tests/integration/test_sdlc_next_skill_cli.py` (new)
- **Informed By:** spike-1 (DISPATCH_RULES stays; only SKILL.md surface changes)
- **Assigned To:** `router-builder`
- **Agent Type:** builder
- **Parallel:** false
- Implement the chosen interface:
  - **B1:** Add `tools/sdlc_next_skill.py` exposing `sdlc-tool next-skill`. Wire it into existing `sdlc-tool` resolver. Rewrite SKILL.md Step 4 to call the tool. Update SKILL.md:222 to accurate description. Ship behind `SDLC_ROUTER_SOURCE` env var.
  - **B2:** Add `scripts/generate_skill_dispatch_table.py` that emits the Step 4 table from `DISPATCH_RULES`. Wire into pre-commit. Add CI parity check.
- Behavioral parity: replay 12-step regression sequence in `test_sdlc_router_decision.py` against the new path; assert identical decisions.

### 5. Phase 2 validation
- **Task ID:** validate-phase-2
- **Depends On:** build-phase-2-router
- **Assigned To:** `router-validator`
- **Agent Type:** validator
- **Parallel:** false
- Run rewritten parity test — must pass
- Run full unit + integration suite — no regressions
- Confirm guards G1–G6 still fire on synthetic state

### 6. Documentation
- **Task ID:** document-routing
- **Depends On:** validate-phase-2
- **Assigned To:** `routing-documentarian`
- **Agent Type:** documentarian
- **Parallel:** false
- Update `docs/features/pipeline-graph.md`
- Update `.claude/skills/sdlc/SKILL.md` (line 222 and surrounding section)
- Update module docstrings in `agent/pipeline_graph.py` and `agent/sdlc_router.py`
- Confirm `docs/features/README.md` index entry is current

### 7. Final validation
- **Task ID:** validate-all
- **Depends On:** document-routing
- **Assigned To:** `router-validator`
- **Agent Type:** validator
- **Parallel:** false
- Run all Verification commands below
- Confirm all Success Criteria met
- Generate final report for PR

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Shims deleted | `find bridge/ -name 'pipeline_*.py'` | empty output |
| No stale `bridge.pipeline_*` references | `grep -rn "bridge\.pipeline_graph\|bridge\.pipeline_state" --include="*.py" --include="*.md" \| grep -v "^docs/plans/completed/" \| grep -v "^docs/plans/pipeline-routing-consolidation.md"` | exit code 1 |
| Parity test passes | `pytest tests/unit/test_sdlc_skill_md_parity.py -v` | exit code 0 |
| Router decision tests pass | `pytest tests/unit/test_sdlc_router_decision.py tests/unit/test_sdlc_router_oscillation.py -v` | exit code 0 |
| New integration test exists & passes | `pytest tests/integration/test_sdlc_dispatch.py -v` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique runs. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **B1 (LLM calls tool) vs. B2 (SKILL.md generated from rules)?** Spike-1 settled the bigger question — `DISPATCH_RULES` cannot be deleted; it stays as the canonical dispatch source. The remaining choice is how SKILL.md interfaces with it:
   - **B1:** SKILL.md Step 4 says "call `sdlc-tool next-skill` and dispatch what it returns." LLM stops authoring routing decisions. Maximum simplicity, smallest blast radius for future changes.
   - **B2:** SKILL.md Step 4 table is auto-generated from `DISPATCH_RULES` row docstrings. LLM keeps reading markdown but the markdown is provably synchronous with code via CI parity check.
   **Default proposed: B1.** Confirm or override.

2. **Stage groups (Phase 3) — bundle or split?** The original issue calls out stage groups as part of the problem. This plan defers them as a No-Go and recommends a follow-up plan. Confirm or override: should we attempt to bundle stage groups into this plan (which would push appetite to Large), or split as planned?

3. **Coordination with `agentsession-harness-abstraction.md`.** That plan is in flight and lists `bridge.pipeline_*` test-import migrations in its Test Impact section. Should this plan absorb those migrations explicitly (Phase 1 acquires them; harness plan drops them) or coordinate at merge time? Default: this plan absorbs.

4. **Env-var flag for the rollout (Risk 2 mitigation).** Proposal is `SDLC_ROUTER_SOURCE=table|tool` with a one-cycle deprecation. Confirm the flag name and removal timeline, or override.

5. **SKILL.md prose retention.** Even with B1, SKILL.md still documents Steps 1–3 (issue resolution, session-ensure, state assessment) and the guards G1–G6 in prose for human readers. Should the guard documentation in Step 3.5 be auto-generated from guard docstrings (matching B2 spirit even within B1), or hand-maintained as a runbook? Default: auto-generate guard prose to make drift impossible.
