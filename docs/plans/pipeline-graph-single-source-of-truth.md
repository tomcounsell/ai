---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-31
tracking: https://github.com/tomcounsell/ai/issues/2491
last_comment_id:
---

# Pipeline Graph — Single Source of Truth

## Problem

`agent/pipeline_graph.py` opens by declaring itself the canonical definition of the SDLC pipeline. It is not. A repo-wide sweep found **7 hardcoded code duplicates**, **~30 prose restatements**, and only **2 clean importers**. The duplicates have drifted, two carry live defects, and the docs that describe the graph contradict both the code and each other.

The owner's framing: *"make sure there is a single source of truth for that pipeline graph. many other features including docs rely on it."*

**Current behavior:**

Adding a stage today requires edits in at least six places, and nothing detects when they disagree. Concretely:

- `models/agent_session.py:81` defines `SDLC_STAGES` byte-identical to `DISPLAY_STAGES`, and drives `AgentSession.current_stage`.
- `agent/pipeline_state.py:48` defines `ALL_STAGES` while **already importing `PIPELINE_EDGES` two lines above it**.
- `agent/sdlc_router.py:118-129` defines nine `SKILL_DO_*` constants under a `# Keep in sync with agent.pipeline_graph.STAGE_TO_SKILL` comment, while **already importing `STAGE_TO_SKILL` at line 41**.
- `agent/hooks/pre_tool_use.py:60-69` hand-maintains `_SKILL_TO_STAGE`, the inverse of `STAGE_TO_SKILL`, and **is missing `do-issue` → `ISSUE`**. Re-imported by `agent/hooks/post_tool_use.py:85`.
- The stage→model mapping is not in the graph at all. It exists as four markdown tables that disagree on their row sets.

**Desired outcome:**

One definition. Every Python consumer imports or derives; every doc cross-references. Stage→model becomes part of the graph and is *handed* to dispatchers rather than read from prose. A guard test converts recurrence from a discovery into a CI failure.

## Freshness Check

**Baseline commit:** `7d441fed17a211eebb3aad8a138a120eacbc7e1d`
**Issue filed at:** 2026-07-31T04:59:51Z
**Disposition:** Unchanged

**File:line references re-verified:** All references were established by four parallel recon agents against this exact commit, minutes before the issue was filed. No independent re-verification pass was warranted.

**Cited sibling issues/PRs re-checked:**
- #1216 — CLOSED, implemented by PR #1240. Its plan's No-Gos explicitly deferred model-selection plumbing to a follow-up; this plan is that follow-up.
- #1968 — CLOSED. Cannot carry the dead-settings-field deferral, so that cleanup is folded into scope here.
- #2492 (skill merge) and #2493 (phantom `multi` shape) — both OPEN, filed today, both sequenced after or outside this plan.

**Commits on main since issue was filed (touching referenced files):** none. `HEAD` is unchanged from session start.

**Active plans in `docs/plans/` overlapping this area:** none. No active plan touches `agent/pipeline_graph.py`, `agent/sdlc_router.py`, or `tools/sdlc_next_skill.py`.

## Prior Art

- **#1216 / PR #1240**: *"SDLC routing is pattern-matching against a markdown table; pipeline graph is decorative"* → *"refactor: consolidate SDLC pipeline routing to a single source of truth"*. Diagnosed three drifting routing surfaces and replaced the markdown dispatch table with a `sdlc-tool next-skill` call. **Succeeded**, and is the structural template for this work. Its plan (`docs/archive/plans-completed/pipeline-routing-consolidation.md:249`) states the deferral this plan picks up: *"Stage groups (Phase 3)... Touching dev-session boundaries, model selection, and PM steering protocol is its own architectural change."*
- **#900 / PR #909**: *"SDLC stage model selection and hard-PATCH builder session resume"* (merged 2026-04-11). Established the per-stage model table as **prose** and shipped the hard-PATCH resume mechanism. Relevant because spike-4 confirms that mechanism is real and must not be disturbed.
- **#563**: pipeline graph routing not wired into runtime. **Partially addressed** by #1216.
- **#704 / PR #744**: router and dashboard moved to `PipelineStateMachine` instead of artifact inference. **Succeeded** — this is why `ui/data/sdlc.py` is one of the two clean importers today.

## Research

No relevant external findings — proceeding with codebase context. This work is purely internal: no external libraries, APIs, or ecosystem patterns are involved. Phase 0.7 skipped per the skill's stated condition.

## Spike Results

### spike-1: Should `pipeline_graph.py` stay dependency-free?
- **Assumption**: "The module must stay import-pure, so a new stage→model constant should not read env vars."
- **Method**: code-read
- **Finding**: **The premise is false.** `agent/__init__.py` eagerly imports `agent_session_queue`, `branch_manager`, `completion`, `messenger` and more, so *any* `from agent.pipeline_graph import ...` already executes that package `__init__`. Measured: `import agent.pipeline_graph` loads **2360 modules** and `config.settings` is already in `sys.modules`, against a 62-module bare-interpreter baseline. Adding `import os` or even `from config.settings import settings` would be a *declared* dependency, not a *newly loaded* one — no cycle, no runtime cost. `tests/unit/test_architectural_constraints.py` (read in full) says **nothing** about `pipeline_graph.py`; it constrains only `agent/sdlc_router.py` (must not import `tools/`). Module-level `os.environ.get` is **common** in `agent/` (~30 occurrences), and the doctrine is written down twice verbatim: `agent/tool_budget.py:22` and `agent/session_health.py:212` both state that ops dials are read *"via raw `os.environ.get()` at module scope, NOT"* via settings.
- **Confidence**: high on mechanics; **medium** on the recommendation
- **Impact on plan**: Recommends **(b) module-level `os.environ` overlay**. Rules out a full `config.settings` field because `Settings()` at `config/settings.py:1093` runs validators that **raise** — declaring that dependency turns a bad `.env` value into "the router cannot compute the next stage" for a module whose entire value is being always-loadable. Carried to Open Questions, since (a) vs (b) is a judgment call, not a fact.

### spike-2: Where should the model be derived for the router JSON?
- **Assumption**: "Derive it in the CLI wrapper's dict construction, not on the `Dispatch` dataclass."
- **Method**: code-read
- **Finding**: The purity boundary the codebase defends is **I/O into `tools/`**, not data. Verbatim from `tools/sdlc_next_skill.py` (#2395): *"that function is a **pure guard table (G1-G7) and must never import/call `derive_from_durable_signals` itself**, preserving the #1954 purity boundary."* The router **already** imports `from agent.pipeline_graph import MAX_CRITIQUE_CYCLES, STAGE_TO_SKILL`, so a pure lookup in `agent/` violates nothing. `STAGE_TO_SKILL` is **injective** — 9 stages, 9 distinct skills, PATCH→`/do-patch` unique — so the reverse lookup is unambiguous. Only **one** other caller of `decide_next_dispatch` exists outside the CLI wrapper: `agent/session_runner/runner.py:1349` (`_load_ledger`), which reads only `decision.skill` for nudge text and needs no model.
- **Confidence**: high (~85%) on "not on the dataclass"; medium-high (~75%) on "in `pipeline_graph.py`"
- **Impact on plan**: Adds `STAGE_TO_MODEL` to `pipeline_graph.py` beside `STAGE_TO_SKILL`, plus a `model_for_skill()` helper, consumed in the CLI dict. Putting it on the frozen `Dispatch` dataclass would touch 9 construction sites (or 20+ `DispatchRule` rows) to carry a value the router never uses. **Naming**: `STAGE_TO_MODEL`, not the issue's `STAGE_MODELS` — parallel to `STAGE_TO_SKILL`.

### spike-3: Are `pm_model` / `dev_model` live or dead config?
- **Assumption**: "Both are dead; `_resolve_session_model` is the live path."
- **Method**: code-read
- **Finding**: **Both DEAD.** `grep -rn "settings\.session_runner" --include='*.py'` returns **nothing at all** — the entire `SessionRunnerSettings` group is unread except `supervisor_*`. Zero production readers, zero test readers. The live chain is `session.model` > `settings.models.session_default_model` > `None`, at `agent/session_executor.py:99-120`; neither field appears in it. `agent/session_runner/runner.py:500-513` has **no** settings fallback — if `model` is `None` it stays `None`.
- **Confidence**: high (95%)
- **Impact on plan**: No conflict risk — a stage-derived `session.model` wins unopposed. Also surfaced a false claim in the `valor` CLI feature doc that sessions spawn via `SessionRunnerSettings.pm_model`/`dev_model`; that doc and CLI have since been removed, so only the two-field deletion remains, folded into this plan rather than deferred.

### spike-4: Is the PATCH easy/hard model split real?
- **Assumption**: "It is documentation drift and should be deleted."
- **Method**: code-read
- **Finding**: **Assumption wrong — it is real and shipped** (PR #909 / #900). `models/agent_session.py:355-361` has `retain_for_resume` under a `=== BUILD session retention for hard-PATCH resume ===` header; `tools/valor_session.py:784,884,1874` implement the resume path; `tests/unit/test_valor_session_resume_release.py` is ~1100 lines of coverage. The "signals" that choose fresh-vs-resume are **model-judged from prose** (`config/personas/engineer.md:542-561`, `## Hard-PATCH Resume Decision Rules`), which is a legitimate design: mechanism in code, routing in prompt. **Decisively, both `pipeline-graph.md` PATCH rows read `sonnet`** — the second column's "resumed from BUILD" is a dispatch-mechanism annotation, not a different model. The `PATCH -.-> BUILD` mermaid edge is deliberate and documented in `README.md:40,44`; `tests/unit/test_agent_session.py:482` asserts BUILD-resume is *intentionally* outside `SDLC_STAGES`.
- **Confidence**: high (~90%)
- **Impact on plan**: **A flat scalar-per-stage map still suffices.** Do NOT encode a two-value PATCH entry. Do NOT delete the mermaid dotted edge, and do NOT add a `PATCH → BUILD` edge to `PIPELINE_EDGES` — that would corrupt `MAX_PATCH_CYCLES` accounting and the merge gate. The one genuine defect is presentational: collapse `pipeline-graph.md:126-127` to a single `| PATCH | sonnet |` row with the resume note in prose beneath.

## Data Flow

How a stage's model reaches the process that runs it. The two seams are asymmetric, and that asymmetry is the crux of the design.

**Seam A — bridge (Python-reachable, already built end to end):**

1. **Entry point**: eng session decides to spawn a stage child
2. `tools/valor_session.py:1851` — `--model` argparse → `:480` → `:612`
3. `agent/agent_session_queue.py:250,410` — persisted as `model=model or None`
4. `models/agent_session.py:343` — `AgentSession.model` field in Redis
5. `agent/session_executor.py:99-120` — `_resolve_session_model()`: `session.model` > `settings.models.session_default_model` > `None`; applied at `:2003`
6. `agent/session_runner/runner.py:372,450,502` → `role_driver.py:181,199,455` → `harness/claude.py:1700`
7. **Output**: `harness/claude.py:366-371` — `if model: harness_cmd.extend(["--model", model])`

**Seam B — local supervisor (no Python interception point exists):**

1. **Entry point**: `/do-sdlc` supervisor calls `sdlc-tool next-skill`
2. `tools/sdlc_next_skill.py` `decide()` — returns dispatch JSON
3. **The LLM reads that JSON** and writes `model:` into an Agent tool call
4. **Output**: the Claude Code harness applies `model:` to an in-process subagent — no Python in the loop at all

**Consequence:** seam B can only be reached through text the model reads. That is why the model must ride out on the router's own JSON rather than being resolved somewhere in Python. Seam A needs no change; it already carries whatever `--model` it is given.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1240 (#1216) | Replaced the markdown dispatch table with `sdlc-tool next-skill`; made the router the one dispatch surface | Fixed the *dispatch* layer only. Explicitly scoped out model selection as "its own architectural change." Left `agent/sdlc_router.py:118`'s `SKILL_DO_*` duplicate in place under a "keep in sync" comment, and added no test comparing any consumer against `PIPELINE_EDGES`. |
| PR #909 (#900) | Established per-stage model selection and hard-PATCH resume | Encoded the model mapping as **prose in a persona file**, with no code representation and no test. That table has since been copied three more times and all four now disagree. |
| PR #744 (#704) | Routed the dashboard through `PipelineStateMachine` | Succeeded for the dashboard specifically — `ui/data/sdlc.py` is clean today. But it was a single-consumer fix; the other six duplicates were never in its scope. |

**Root cause pattern:** every prior fix corrected *one consumer* and left the *shape of the problem* — a definition that is cheap to copy and has no mechanism detecting copies. None added a guard. This plan's distinguishing move is that the guard test, not the enumerated fix list, is the deliverable.

## Architectural Impact

- **New dependencies**: at most `import os` in `agent/pipeline_graph.py` (stdlib), and only if Open Question 1 resolves to (b). Measured to be a declared-not-loaded dependency.
- **Interface changes**: `sdlc-tool next-skill`'s dispatch JSON gains a `model` key. Additive — `blocked` and error shapes are unchanged and carry no model.
- **Coupling**: net **decrease**. Seven modules stop defining stage data and start importing it.
- **Data ownership**: `agent/pipeline_graph.py` gains ownership of stage→model, which currently has no owner in code.
- **Reversibility**: high. Every change is either an import replacing a literal, an additive JSON key, or a doc edit.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (the module-purity call, and the scope boundary against #2492/#2493)
- Review rounds: 2+ (`agent/pipeline_graph.py` is a force-FULL critique path per `.claude/skills-global/do-plan-critique/CRITICS.md:209`)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo test suite runnable | `test -x scripts/pytest-clean.sh` | Guard test must be verified before landing |
| Ruff available | `python -m ruff --version` | Lint/format gate |

## Solution

### Key Elements

- **`STAGE_TO_MODEL`**: a stage→model-alias map in `agent/pipeline_graph.py`, adjacent to `STAGE_TO_SKILL`, covering all 9 stages. A flat scalar map (spike-4).
- **`model_for_skill()`**: the single derivation path from a skill string to a model, via the injective reverse of `STAGE_TO_SKILL`, with an explicit fallback rather than a `KeyError`.
- **Duplicate collapse**: C1–C7 stop defining stage data; each imports or derives.
- **Router JSON `model` key**: the only wire that reaches seam B.
- **Guard test**: a sweep that fails CI when any file outside the graph module restates stage data, plus positive equality assertions.
- **Doc reconciliation**: one authority, cross-references everywhere else, and five stale claims corrected.

### Flow

**A stage completes** → router asked for next dispatch → **`sdlc-tool next-skill` returns `{skill, model, reason, row_id, dispatched}`** → supervisor reads `model` from the JSON → **stage subagent spawned on the correct model** → no table was consulted anywhere

### Technical Approach

- Add `STAGE_TO_MODEL` beside `STAGE_TO_SKILL`. Values are provisional/tunable and commented as such per the house convention (exemplars: `config/settings.py:772`, `agent/session_runner/liveness.py:186`).
- Add `model_for_skill(skill: str) -> str` in `pipeline_graph.py`, built on the reverse of `STAGE_TO_SKILL`. Make it the **only** path to a model — spike-2's point is that a helper cannot be forgotten by a future multi-dispatch implementation, whereas an inline dict literal can.
- Consume it in `tools/sdlc_next_skill.py`'s dispatch dict (the single construction site). Do **not** touch the frozen `Dispatch` dataclass or `DispatchRule`.
- Collapse the duplicates. C4 and C6 are one-line derivations; C4's missing `do-issue` → `ISSUE` entry is fixed for free by deriving it. C2 and C6 already import from the graph, so their duplicates are pure redundancy.
- For the deliberate stage **subsets** (`agent/goal_gates.py:37`, `models/agent_session.py:619`, `tools/sdlc_verdict.py:112`), derive from the graph where the subset is expressible as a filter; otherwise whitelist with a comment stating *why* the subset differs. `agent/agent_session_queue.py:507,511` holds a third inline copy of the same partition as `_ENG_WORKTREE_STAGES` — that one is a genuine duplicate, not a deliberate subset.
- Delete the four markdown stage→model tables. Collapse `pipeline-graph.md`'s two PATCH rows into one before deleting, so no information is lost in the move.
- Correct the five stale doc claims and resolve the circular authority between `pipeline-graph.md:114` and `pm-sdlc-decision-rules.md:28` to a single direction (the graph module wins).
- Delete dead `pm_model` / `dev_model`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `model_for_skill()` must not raise on an unknown skill string. Test asserts an explicit documented fallback, not a `KeyError` — spike-2 flagged this as the one way the reverse lookup can break if a future skill string is emitted that is not in `STAGE_TO_SKILL`.
- [ ] If Open Question 1 resolves to (b), the env overlay must fall back to the literal on a malformed value rather than raising at import. Test with garbage input (`"NOTASTAGE=x"`, `"BUILD"`, `""`, `"BUILD=,=opus"`).
- [ ] No other exception handlers are in scope — the remaining changes are constant definitions and imports.

### Empty/Invalid Input Handling
- [ ] `model_for_skill("")` and `model_for_skill(None)` return the fallback without raising.
- [ ] The router's `blocked` and error JSON shapes carry **no** `model` key; a test asserts their exact shape is unchanged, so `/do-sdlc` never reads `model` unconditionally.

### Error State Rendering
- [ ] No user-visible output changes. The dashboard renders `DISPLAY_STAGES`, which is unchanged in content. A test asserts `DISPLAY_STAGES` is byte-identical before and after.

## Test Impact

- [ ] `tests/unit/test_sdlc_next_skill.py:460-466` — UPDATE: exact-dict assertion on the dispatch shape must include the new `model` key. This is the **only** exact-dict assertion that breaks.
- [ ] `tests/unit/test_pipeline_graph.py` — UPDATE: add coverage for `STAGE_TO_MODEL` and `model_for_skill()`, including the key-set equality with `STAGE_TO_SKILL` and the unknown-skill fallback.
- [ ] `tests/unit/test_pipeline_integrity.py` — UPDATE: extend to assert the new positive equalities (`SDLC_STAGES == DISPLAY_STAGES`, `set(ALL_STAGES) == set(STAGE_TO_SKILL)`). Both would fail today.
- [ ] `tests/fixtures/Mac-local/eng_system_prompt_baseline.txt` and `dev_system_prompt_baseline.txt` — REPLACE: regenerate, since `config/personas/engineer.md`'s stage→model table is deleted.
- [ ] `tests/fixtures/Mac-local/pm_system_prompt_baseline.txt` — DELETE: a stale 2026-06-15 snapshot of a **deleted** `config/personas/project-manager.md`, containing pipeline text present in no live file. It will trip the guard test. **This is a prerequisite — resolve before the guard lands.**
- [ ] `tests/unit/test_architectural_constraints.py` — UPDATE only if Open Question 1 resolves to (c); otherwise unaffected.
- [ ] New: a guard test file for the sweep (see Verification).

## Rabbit Holes

- **Implementing multi-dispatch.** Filed as #2493. The `{"multi": true}` shape is documented in both skills and implemented in neither. Tempting to "just add it while we're in the JSON," but it drags in parallel-stage semantics, the `pthread` skill, and DOCS+PATCH concurrency safety.
- **Refactoring `SessionRunnerSettings` wholesale.** Spike-3 found the *entire* group unread, not just the two model fields. Deleting two fields that contradict the new SSOT is in scope; auditing the whole settings group is not.
- **Making the mermaid diagram generated.** `docs/assets/sdlc-pipeline.mmd` is the densest single restatement, so generating it from the graph is appealing. But it also encodes the deliberate `PATCH -.-> BUILD` annotation which is *not* in `PIPELINE_EDGES` by design — a generator would have to special-case it. Correct it by hand; leave generation alone.
- **Chasing `docs/archive/plans-completed/**`.** ~25 historical files restate the graph. They are archives of what was true when written.
- **Encoding the hard-PATCH "signals" in code.** They are model-judged prose by design (spike-4). Turning them into branching logic is a different project.

## Risks

### Risk 1: The guard test fires on legitimate deliberate subsets
**Impact:** CI blocked by false positives on `GATE_STAGES`, `_ENG_WORKTREE_STAGES`, `_VERDICT_STAGES`, and the test files that legitimately restate the graph as assertions.
**Mitigation:** The whitelist is part of the deliverable, not an afterthought: `tests/`, `docs/archive/plans-completed/`, `docs/postmortems/`, `site/`, `.worktrees/`, `.claude/worktrees/`. Each remaining in-tree subset gets either a derivation or an inline comment justifying why it differs. Run the guard against current main first and triage every hit before landing it.

### Risk 2: Deleting the persona table changes eng-session behavior
**Impact:** `config/personas/engineer.md:389` is baked into every eng/dev system prompt. Removing it without a replacement instruction could leave sessions with no model guidance, silently defaulting to `opus` for every stage.
**Mitigation:** The replacement instruction ships in the same change: the persona tells the session to use the `model` the router returned. Verify against the regenerated prompt baselines, and confirm `_resolve_session_model`'s fallback chain (`session.model` > `session_default_model` > `opus`) makes the failure mode conservative rather than broken.

### Risk 3: `agent/pipeline_graph.py` is a force-FULL critique path
**Impact:** `.claude/skills-global/do-plan-critique/CRITICS.md:209` forces a full critique on any change to this file, and `docs/archive/plans-completed/triage_first_critique.md:317,348` contains verification gates asserting `git diff` does **not** touch it. Those gates may fire.
**Mitigation:** Expected and correct — this is exactly the change that should get full scrutiny. Budget for 2+ review rounds. Check whether those completed-plan gates are still live anywhere before building.

### Risk 4: The env overlay becomes an unaudited production lever
**Impact:** If Open Question 1 resolves to (b), an operator could silently route REVIEW to a weak model and quietly degrade review quality with no signal.
**Mitigation:** Log the effective mapping at import when an override is present, so the deviation is visible in logs. This risk is the substance of Open Question 1 — if the answer is "we'd page someone," the correct resolution is (a) and this risk disappears.

## Race Conditions

No race conditions identified. Every change in this plan is to module-level constants evaluated once at import, pure function definitions, or documentation. `STAGE_TO_MODEL` and `model_for_skill()` are immutable-after-import and side-effect-free; no concurrent mutation is possible. The router JSON gains a key computed synchronously from a frozen dict within an already-synchronous code path. The env overlay, if adopted, is parsed once at module import before any concurrency exists.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2492] Merging `/sdlc` into `/do-sdlc`. Sequenced after this plan so the skill merge inherits an already-authoritative graph; the only overlap is the stage→model table deletions, which belong here.
- [SEPARATE-SLUG #2493] Implementing or deleting the `{"multi": true, "dispatches": [...]}` router response shape that both SDLC skills branch on and the router cannot emit.
- [DESTRUCTIVE] Adding a `PATCH → BUILD` edge to `PIPELINE_EDGES`. Spike-4 establishes this would corrupt `MAX_PATCH_CYCLES` accounting and the merge gate. The dotted mermaid edge documents a resume mechanism, not a graph transition, and stays as-is.

## Update System

No update system changes required. This work adds no new dependencies, config files, or services. `agent/pipeline_graph.py` and the modules that import it are already propagated by the existing `/update` flow as ordinary repo code. If Open Question 1 resolves to (b), the new env var is **optional with a working default**, so no `.env` propagation is needed; it would be documented in `.env.example` for discoverability only.

## Agent Integration

No new agent surface is required — `sdlc-tool` is an existing CLI entry point in `pyproject.toml [project.scripts]` and its `next-skill` subcommand is already invoked by the SDLC skills.

The integration that *does* matter is the seam-B contract: `/do-sdlc` must be told to read `model` from the router JSON instead of from its own table. That is a skill-body edit paired with the JSON change, and it is the only thing that makes the new constant reach a running subagent.

- [ ] Integration test: assert `sdlc-tool next-skill --issue-number N` emits a `model` key whose value matches `STAGE_TO_MODEL` for the dispatched skill's stage.
- [ ] Grep confirms `.claude/skills-global/do-sdlc/SKILL.md` references the router's `model` field and contains no stage→model table.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pipeline-graph.md`: make it the single declared authority; delete the stage→model table (collapsing the two PATCH rows first); correct line 25's false claim that the graph is *"not consulted for dispatch decisions"* (G8 dispatches via `STAGE_TO_SKILL` at `agent/sdlc_router.py:455`).
- [ ] Update `docs/features/sdlc-pipeline.md:8`: remove PATCH from the linear happy path — no such edge exists.
- [ ] Update `docs/features/pipeline-state-machine.md:217,220`: remove the self-nullifying *"canonical; `agent/pipeline_graph.py` is a shim"* rename debris; `bridge/pipeline_graph.py` does not exist.
- [ ] Update `docs/features/sdlc-pipeline-integrity.md:56-64`: add the missing CRITIQUE stage.
- [ ] Update `docs/features/pm-sdlc-decision-rules.md:28`: resolve the circular authority claim in favor of the graph module.
- [ ] Update `docs/features/agent-session-model.md:296`: rewrite *"stage routing lives in the engineer persona prose, NOT in settings"* — the claim this plan overturns.
- [ ] Update `docs/features/sdlc-local-supervision.md:29,41` and `docs/features/sdlc-stage-handoff.md:35-46`: replace restatements with cross-references.
- [ ] Update `docs/assets/sdlc-pipeline.mmd`: correct by hand; keep the deliberate `PATCH -.-> BUILD` dotted edge and the `README.md:40,44` bullet that documents it.
- [ ] Update `README.md:47`: it currently names `.claude/skills/sdlc/SKILL.md` as *"the ground truth on stage definitions."* Point it at the graph module.
- [ ] Add/update the entry in `docs/features/README.md` index.

### Inline Documentation
- [ ] `STAGE_TO_MODEL` carries a comment marking its values provisional/tunable, per the house convention.
- [ ] `model_for_skill()` docstring states the fallback behavior for unknown skills.
- [ ] Update `agent/session_executor.py:99-119`'s precedence docstring if the resolution chain gains a rung.

## Success Criteria

- [ ] `STAGE_TO_MODEL` exists in `agent/pipeline_graph.py` covering all 9 stages, as a flat scalar map.
- [ ] `STAGE_TO_MODEL.keys() == STAGE_TO_SKILL.keys()` asserted by test.
- [ ] `model_for_skill()` is the only derivation path and returns a documented fallback for unknown skills rather than raising.
- [ ] C1–C7 no longer define stage data.
- [ ] `_SKILL_TO_STAGE` includes `do-issue` → `ISSUE`.
- [ ] `sdlc-tool next-skill` emits `model` on the dispatch shape only; `blocked`/error shapes are unchanged.
- [ ] All four markdown stage→model tables are gone.
- [ ] `assert models.agent_session.SDLC_STAGES == agent.pipeline_graph.DISPLAY_STAGES` passes (fails today).
- [ ] `set(agent.pipeline_state.ALL_STAGES) == set(STAGE_TO_SKILL)` passes (fails today).
- [ ] The guard test fails when a ≥4-stage literal is planted outside the graph module, and passes on a clean tree (red-state proof pasted into the PR).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (graph-core)** — Name: `graph-builder`; Role: `STAGE_TO_MODEL`, `model_for_skill()`, and the C1–C7 duplicate collapse; Agent Type: `builder`; Resume: true
- **Builder (router-json)** — Name: `router-builder`; Role: the `model` key in the dispatch JSON and its test updates; Agent Type: `builder`; Resume: true
- **Builder (guard-test)** — Name: `guard-builder`; Role: the sweep guard test, its whitelist, and the red-state proof; Agent Type: `test-engineer`; Resume: true
- **Documentarian (docs-sweep)** — Name: `docs-writer`; Role: the ~30 prose sites, five stale claims, and circular authority; Agent Type: `documentarian`; Resume: true
- **Validator** — Name: `ssot-validator`; Role: verifies SSOT actually holds, not just that files changed; Agent Type: `validator`; Resume: true

## Step by Step Tasks

### 1. Resolve the stale prompt baseline (prerequisite)
- **Task ID**: prep-baselines
- **Depends On**: none
- **Validates**: tests/unit/ (persona baseline tests)
- **Informed By**: recon (pm baseline is a Jun-15 snapshot of a deleted persona file)
- **Assigned To**: graph-builder
- **Agent Type**: builder
- **Parallel**: false
- Confirm `config/personas/project-manager.md` does not exist and nothing reads `pm_system_prompt_baseline.txt`
- Delete the stale fixture, or regenerate if a live consumer is found
- This must land before the guard test, or the guard will fire on dead content

### 2. Add STAGE_TO_MODEL and model_for_skill
- **Task ID**: build-graph-core
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_graph.py
- **Informed By**: spike-1 (module purity), spike-2 (naming, injectivity, helper-not-literal), spike-4 (flat scalar suffices)
- **Assigned To**: graph-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `STAGE_TO_MODEL` beside `STAGE_TO_SKILL`, all 9 stages, values commented provisional/tunable
- Add `model_for_skill()` using the injective reverse of `STAGE_TO_SKILL`, with an explicit fallback (never `KeyError`)
- Apply the Open Question 1 resolution for env-overridability
- Add tests: key-set equality with `STAGE_TO_SKILL`, unknown-skill fallback, empty/None input

### 3. Collapse the seven code duplicates
- **Task ID**: build-collapse-dupes
- **Depends On**: build-graph-core
- **Validates**: tests/unit/test_pipeline_integrity.py, tests/unit/test_agent_session.py
- **Assigned To**: graph-builder
- **Agent Type**: builder
- **Parallel**: false
- C1 `models/agent_session.py:81`, C2 `agent/pipeline_state.py:48`, C3+C4 `agent/hooks/pre_tool_use.py:52,60`, C5 `tools/sdlc_stage_marker.py:90`, C6+C7 `agent/sdlc_router.py:118,1373`
- Derive C4 from `STAGE_TO_SKILL` so the missing `do-issue` entry is fixed structurally
- For deliberate subsets (`goal_gates.py:37`, `agent_session.py:619`, `sdlc_verdict.py:112`): derive as a filter where expressible, else whitelist with a justifying comment
- Treat `agent_session_queue.py:507,511` as a genuine duplicate of `_ENG_WORKTREE_STAGES`, not a deliberate subset
- Add the two positive equality assertions that fail today

### 4. Add the model key to the router JSON
- **Task ID**: build-router-json
- **Depends On**: build-graph-core
- **Validates**: tests/unit/test_sdlc_next_skill.py
- **Informed By**: spike-2 (single construction site; do not touch the frozen Dispatch dataclass)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `model` to the dispatch dict in `tools/sdlc_next_skill.py` via `model_for_skill()`
- Leave `Dispatch`/`DispatchRule` untouched
- Update the one exact-dict assertion at `test_sdlc_next_skill.py:460-466`
- Assert `blocked`/error shapes still carry no `model`
- Note in a comment that `runner.py:1349` `_load_ledger` bypasses this path and needs no model today

### 5. Delete dead model config
- **Task ID**: build-dead-config
- **Depends On**: none
- **Validates**: tests/unit/
- **Informed By**: spike-3 (both fields DEAD, 95% confidence)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `pm_model` and `dev_model` from `config/settings.py:756,766`
- Remove the commented `.env.example:436,439` lines
- Leave `config/settings.py:1105` alone — that string is a legacy-env-key warning, not a field reference

### 6. Build the guard test
- **Task ID**: build-guard
- **Depends On**: prep-baselines, build-collapse-dupes
- **Validates**: the new guard test file
- **Assigned To**: guard-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Sweep: no file outside the graph module defines a ≥4-stage literal or restates a stage→model row
- Whitelist `tests/`, `docs/archive/plans-completed/`, `docs/postmortems/`, `site/`, `.worktrees/`, `.claude/worktrees/`
- Run against current main FIRST and triage every hit before landing
- Produce a red-state proof: plant a violation, show the guard FAILS, paste output into the PR

### 7. Documentation sweep
- **Task ID**: document-feature
- **Depends On**: build-graph-core, build-router-json, build-collapse-dupes
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Execute every item in the `## Documentation` section
- Collapse `pipeline-graph.md`'s two PATCH rows before deleting the table, preserving the resume note in prose
- Update `.claude/skills-global/do-sdlc/SKILL.md` to read the router's `model` field instead of its own table
- Update `config/personas/engineer.md`: delete the table, add the read-the-router instruction, regenerate baselines

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: all previous
- **Assigned To**: ssot-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify SSOT holds behaviorally: change a value in `STAGE_TO_MODEL` and confirm it propagates to the router JSON with no other edit
- Run every Verification row
- Confirm no success criterion is satisfied only by a doc edit

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| STAGE_TO_MODEL exists and is complete | `python -c "from agent.pipeline_graph import STAGE_TO_MODEL, STAGE_TO_SKILL; assert STAGE_TO_MODEL.keys()==STAGE_TO_SKILL.keys(); print(len(STAGE_TO_MODEL))"` | output contains 9 |
| SDLC_STAGES duplicate collapsed | `python -c "import models.agent_session as m, agent.pipeline_graph as g; assert m.SDLC_STAGES==g.DISPLAY_STAGES; print('ok')"` | output contains ok |
| ALL_STAGES derived | `python -c "import agent.pipeline_state as s, agent.pipeline_graph as g; assert set(s.ALL_STAGES)==set(g.STAGE_TO_SKILL); print('ok')"` | output contains ok |
| do-issue mapping restored | `python -c "from agent.hooks.pre_tool_use import _SKILL_TO_STAGE as m; assert m['do-issue']=='ISSUE'; print('ok')"` | output contains ok |
| Router emits model | `python -c "from agent.pipeline_graph import model_for_skill; print(model_for_skill('/do-pr-review'))"` | output contains opus |
| Unknown skill does not raise | `python -c "from agent.pipeline_graph import model_for_skill; print(model_for_skill('/do-nonexistent'))"` | exit code 0 |
| Router SKILL_DO_* duplicate gone | `grep -c 'SKILL_DO_' agent/sdlc_router.py` | match count == 0 |
| Persona stage-model table gone | `grep -c 'Stage→Model Dispatch Table' config/personas/engineer.md` | match count == 0 |
| Dead settings fields gone | `grep -cE '^\s+(pm_model\|dev_model):' config/settings.py` | match count == 0 |
| No PATCH->BUILD edge added (anti-criterion) | `python -c "from agent.pipeline_graph import PIPELINE_EDGES as e; print(sum(1 for k,v in e.items() if k[0]=='PATCH' and v=='BUILD'))"` | output contains 0 |
| Guard test present and green | `./scripts/pytest-clean.sh tests/unit/test_pipeline_graph_ssot.py -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Is stage→model an ops dial or a correctness invariant?** Spike-1 recommends a module-level `os.environ` overlay (option b), matching the doctrine written down at `agent/tool_budget.py:22` and `agent/session_health.py:212`. But its decision test is a judgment call only you can make: **would you page someone if an operator changed it?** If yes, it is an invariant and should be a plain constant like `MAX_PATCH_CYCLES` (option a, no `import os` at all). If it is a cost/rate-limit dial you would want to turn on a live box when Opus is throttled, option (b) stands. My recommendation is (b), at medium confidence.

2. **Should `config/personas/engineer.md` keep any model guidance at all?** Once the router hands the model out, the persona table is redundant. But the persona is also the only place an eng session learns *why* stages differ. Delete the table entirely, or replace it with one prose line pointing at the router's `model` field?

3. **Should the router also hand out the hard-PATCH create-vs-resume decision?** Spike-4 confirms fresh-vs-resume is model-judged from prose at `engineer.md:542-561`, with the mechanism fully implemented in code. If the router is already telling dispatchers *which model*, telling them *create vs resume* is the natural sibling and would be a small addition to the same JSON. Answer determines whether it enters this plan's scope: **yes** → add it to task 4; **no** → the prose stays authoritative by design and nothing changes.
