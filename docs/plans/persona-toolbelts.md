---
status: Planning
type: feature
appetite: Large
owner: Tom Counsell
created: 2026-09-02
tracking: https://github.com/tomcounsell/ai/issues/3081
last_comment_id: none
---

# Persona Toolbelts

## Problem

A Valor session's capabilities are a property of the host machine, not the role. Every headless persona turn launches `claude -p --permission-mode bypassPermissions` (`agent/session_runner/harness/claude.py`, `_HARNESS_COMMANDS`) and inherits whatever tools, MCP servers, and CLIs exist on that host. The same PM turn has different powers on different machines, which contradicts the system's claim that a session is a durable record that moves between machines.

**Current behavior:**
- Tool definitions and verbose human-oriented tool output consume context every turn whether used or not.
- Role limits are enforced by denial after the fact: the Teammate write restriction is a PreToolUse allowlist (`agent/hooks/pre_tool_use.py::_teammate_is_allowed_write`) fighting a surface that offered the capability in the first place. A denial invites a Bash workaround.
- "What can Valor do" has no versioned, testable, client-facing answer.

**Desired outcome:**
- One version-pinned manifest per persona; identical belt resolution on any host (drift test failures: zero).
- Context spent on tool surface per merged PR −40% against a measured baseline; tool-call turns per merged PR −25%.
- PreToolUse denials trend toward zero because absent tools cannot be called.
- The belt file answers "Valor ships with these tools, scoped this way" in one read.

## Freshness Check

**Baseline commit:** `5021a40aa924f0e2a3b44fa75713690e7a730911`
**Issue filed at:** 2026-09-02T10:48:50Z
**Disposition:** Unchanged

Issue #3081 was filed minutes before this plan and no commits have landed on main since (HEAD predates the issue). All file:line claims in the issue were verified during its own recon at this same baseline: `_HARNESS_COMMANDS` with `bypassPermissions` at `agent/session_runner/harness/claude.py:155-166`, persona→skill mapping at `agent/session_runner/role_driver.py:65-72`, teammate write gate at `agent/hooks/pre_tool_use.py:539`.

**Active plans in `docs/plans/` overlapping this area:** none — the recent active plans (dependency-bump gate, docs-auditor sender, overclaim guard) touch different systems.

## Prior Art

- **#2410**: tool-budget deny-vs-halt default — the existing per-tool call counter this plan's Phase 0 extends; open, complementary, not overlapping.
- **#1886 / #1821**: the tool-budget backstop lineage (`agent/tool_budget.py`) — established the pattern of inline PreToolUse evaluation this plan eventually retires for role-scoping (budget enforcement itself stays).
- No prior issues or PRs mention toolbelts, per-persona tool surfaces, or AXI-style CLIs (`gh` searches for "toolbelt persona" and "AXI" return empty).

## Research

Verified locally instead of web search — the load-bearing external dependency is the installed Claude CLI itself.

**Queries used:** `claude --help` against the installed CLI (v2.1.236).

**Key findings:**
- `--tools <tools...>` exists and specifies the list of available built-in tools ("default" = all). This is true absence: an unlisted built-in tool has no definition in context. Stronger than `--allowedTools`, which only gates permission on an offered tool.
- `--allowedTools` / `--disallowedTools` exist for permission-level gating where a tool must remain visible but restricted.
- `--mcp-config <configs...>` + `--strict-mcp-config` exist: only MCP servers from the given config load, so a per-persona MCP config removes unused MCP tool definitions from context entirely.
- `--setting-sources` exists to control which settings files load, useful for keeping machine-local settings out of belt resolution.

## Spike Results

### spike-1: Phase 1 is deliverable with existing CLI flags
- **Assumption**: "The Claude CLI can express a per-persona belt without wrapper CLIs."
- **Method**: code-read + local CLI verification
- **Finding**: Confirmed on claude 2.1.236 — `--tools`, `--allowedTools`, `--disallowedTools`, `--mcp-config`, `--strict-mcp-config`, `--setting-sources` all present. `_HARNESS_COMMANDS` is a static template today; the resolver extends it per persona.
- **Confidence**: high
- **Impact on plan**: Phase 1 needs no AXI wrappers; belt manifests compile to CLI flags at turn start.

### spike-2: Phase 0 instrumentation partially exists
- **Assumption**: "Per-tool call counting and per-session telemetry already have homes."
- **Method**: code-read
- **Finding**: `agent/tool_budget.py` counts tool calls inline in both PreToolUse surfaces; `agent/session_telemetry.py` streams per-session events to disk with `record_telemetry_event`/`read_session_timeline`. Missing piece: per-tool context-cost attribution from the stream-json token usage the runner already parses.
- **Confidence**: high
- **Impact on plan**: Phase 0 is an extension of two existing modules plus a report script, not a new subsystem.

## Data Flow

1. **Entry point**: worker picks up an AgentSession turn; `role_driver.py` selects the persona (`pm` / `dev` / `teammate`).
2. **Belt resolver (new)**: if `TOOLBELTS_ENFORCE` is on, loads `config/toolbelts/{persona}.toml`, validates the pin, and compiles it to CLI flags (`--tools`, `--allowedTools`, `--mcp-config` + `--strict-mcp-config`). Unresolvable belt → refuse to start the turn with a structured error. Flag off → current ambient behavior, byte-identical invocation.
3. **Harness**: `harness/claude.py` appends the compiled flags to `_HARNESS_COMMANDS` and launches `claude -p`.
4. **Telemetry (Phase 0)**: the stream-json parser attributes tokens per tool call and stamps counts/cost onto the session telemetry stream and stage transitions.
5. **Escalation (Phase 1)**: when the agent reports a missing capability, one tagged line is appended to the session's open-question channel output; non-blocking.
6. **Output**: session proceeds normally; belt version is recorded on the session for audit.

## Architectural Impact

- **New dependencies**: none (TOML via stdlib `tomllib`; everything else is existing CLI flags).
- **Interface changes**: `_HARNESS_COMMANDS` becomes a base template + per-persona flag composition; a new `agent/session_runner/belt_resolver.py` module; a new `config/toolbelts/` directory.
- **Coupling**: decreases — role capability moves out of hooks and prompt text into one declared file per persona.
- **Data ownership**: belt version recorded on the AgentSession (audit field, P2 of the PRD).
- **Reversibility**: high — removing the resolver call restores the current ambient behavior; Phase 3 removals each keep their guard test.

## Appetite

**Size:** Large

**Team:** Solo dev, PM check-ins at phase boundaries

**Interactions:**
- PM check-ins: 2-3 (baseline review ranks Lane B's wrapper order; the activation flip is a deliberate checkpoint)
- Review rounds: 2+ (two lanes instead of four phases roughly halves trips through the review pipeline; Lane B still lands as one PR per wrapper)

Lanes land independently. Lane A + activation is the stopping point that still leaves the project net-positive.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Claude CLI ≥ 2.1 with belt flags | `claude --help 2>&1 \| grep -q -- --strict-mcp-config` | Phase 1 flag composition |
| Telemetry dir writable | `python -c "from agent.session_telemetry import _get_telemetry_dir; _get_telemetry_dir()"` | Phase 0 instrumentation |

## Solution

### Key Elements

- **Belt manifest** (`config/toolbelts/{persona}.toml`): version-pinned declaration of built-in tools, MCP servers, and CLI entry points a persona may use. Committed, reviewed, client-shareable.
- **Belt resolver** (`agent/session_runner/belt_resolver.py`): pure function persona → compiled CLI flags; fails closed on unknown persona, missing manifest, or version mismatch. Ships **dark** behind an activation flag so the baseline window closes before enforcement changes anything.
- **Context-cost instrumentation**: per-tool call counts and token attribution stamped onto session telemetry and stage transitions, plus a baseline report per merged PR.
- **Missing-capability escalation**: one tagged, non-blocking line riding the existing open-question channel.
- **AXI wrappers with interleaved retirement**: thin CLIs over `gh`/`git`/`pytest` for the top tools by measured cost; each wrapper PR retires the PreToolUse hook it makes redundant in the same PR, guard test rewritten to assert belt-absence and mutation-checked. The AXI conformance linter lands with the first wrapper. `valor-docs-write` (path allowlist compiled in) is the first wrapper regardless of ranking — it is justified by the role-boundary goal, not context savings — and Teammate's general Write/Edit leaves the belt when it ships.

### Flow

Worker picks turn → resolver loads persona belt → belt compiles to CLI flags → `claude -p` starts with only the declared surface → agent works; a missing tool produces an escalation line, not a workaround → telemetry stamps per-tool cost → belt version recorded on the session.

### Technical Approach

The PRD's four phases compact into **two build lanes plus one activation event**. The PRD phase numbering maps as: Lane A = PRD Phases 0+1, Activation = the Phase 0→1 boundary, Lane B = PRD Phases 2+3. The compaction is safe because Phase 1 never truly depended on Phase 0's data (belts are seeded by *enumerating* the current surface, not by measuring usage), and retirement was always coupled to wrapping (a hook can only go once its replacement exists).

- **Lane A — Instrument + declare (dark).** Two disjoint workstreams in one lane. (1) Instrumentation: extend the stream-json parse path in the runner to attribute tokens per tool call; stamp aggregates onto `session_telemetry` events and stage transitions; add a `tools.belt_baseline` report (tool-definition + tool-output tokens per merged PR, tool-call turns per merged PR, PreToolUse denial counts). (2) Belts: manifest format TOML, one file per persona, `belt_version` pin, seeded by enumeration of today's surface (faithful snapshot, zero behavior change). Resolver composes `--tools` for built-ins, `--strict-mcp-config` + generated per-persona MCP config for MCP, `--allowedTools`/`--disallowedTools` where a tool stays visible but scoped — all **behind an activation flag** (`TOOLBELTS_ENFORCE` via `config/settings.py`), default off. Dev keeps open Bash by decision — belts are ergonomics and reproducibility, not a trust boundary, and every doc says so plainly. Reproducibility test: resolve each belt in two synthetic environments (different env vars, different fake host inventory) and assert byte-identical flag output. Escalation: the role priming skills gain one line of instruction; the runner tags and forwards it on the open-question channel.
- **Activation — its own observable event.** After the baseline window (2-4 weeks of merged PRs) closes and the baseline report is published, flip `TOOLBELTS_ENFORCE` on in a dedicated commit. Baseline measurement MUST precede activation or the −40% target becomes unfalsifiable — the flag exists precisely to let both halves of Lane A merge early without contaminating the measurement. Keeping the flip isolated also makes it the obvious suspect if a live session stalls on a too-tight belt.
- **Lane B — Wrap + retire, interleaved.** Wrapper order ranked strictly by the baseline numbers (expected but provisional: `sdlc-tool` family reads such as `stage-query` returning PR number + merge state + CI + verdict in one call, PR/issue read-write, test execution with baseline-delta classification, worktree/branch state). Exception: `valor-docs-write` goes first regardless of ranking, since it is justified by the role-boundary goal. Each wrapper is a thin `[project.scripts]` entry over `gh`/`git`/`pytest`, and **each wrapper PR retires the PreToolUse hook it makes redundant in the same PR**: hook branch removed, guard test rewritten to assert belt-absence, mutation-checked in that PR (prove the test fails when the belt re-adds the tool). The wrapper and its guard land atomically, so there is no window where both old hook and new wrapper half-apply, and retirement — the phase the PRD flagged as most likely to be skipped — structurally cannot be skipped. AXI linter (`tools/axi_lint.py`) lands with the first wrapper and checks: compact list output with `--full` hatch, definitive empty states, structured exit codes, idempotent mutations, next-step suggestion.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Belt resolver failure paths are loud: missing manifest, malformed TOML, unknown persona, and version mismatch each raise a structured error that refuses the turn — test each asserts the refusal message and that no `claude -p` process spawns.
- [ ] Telemetry attribution failures must never break a turn: token-attribution errors log a warning and drop the sample — test asserts the warning and turn continuation (matches the existing fail-quiet telemetry pattern).

### Empty/Invalid Input Handling
- [ ] Empty belt manifest (zero tools) is valid and resolvable — test asserts the turn starts with the minimal surface, since "conversation-only" is a legitimate belt.
- [ ] Empty or missing telemetry stream produces a baseline report that says so explicitly rather than reporting zeros as measurements.

### Error State Rendering
- [ ] A refused turn (unresolvable belt) surfaces the reason to the session output path, not just logs.
- [ ] The escalation line renders in the session report even when the turn otherwise succeeds.

## Test Impact

- [ ] `tests/unit/test_teammate_write_restriction.py` — UPDATE (Lane B, first wrapper PR): assertions move from "hook denies Write outside docs/" to "Teammate belt omits Write/Edit; `valor-docs-write` refuses paths off the allowlist". Guard intent is preserved.
- [ ] `tests/unit/test_tool_budget.py` — UPDATE (Lane A): extended for context-cost attribution fields alongside call counts.
- [ ] `tests/unit/test_session_telemetry.py` — UPDATE (Lane A): new event fields for per-tool cost.
- [ ] `tests/unit/test_harness_stale_uuid_result_preservation.py` — UPDATE (Lane A): harness invocation construction gains flag-gated belt composition; fixtures that assert the exact `_HARNESS_COMMANDS` shape need the flag-off (identical) and flag-on (composed) forms.
- [ ] `tests/unit/test_sdk_permissions.py` — UPDATE (Lane B): permission expectations shift from hook-denial to tool-absence for role scoping, per retired hook.

## Rabbit Holes

- **Scoping Bash.** Decided out. Any "while we're here, restrict Bash" impulse is a separate project; belts ship with the open-Bash caveat stated plainly.
- **Serialization format debates (TOON etc.).** Adopt the nine AXI principles that matter; let Phase 0 measurement pick output formats. No format migration work.
- **Wrapping everything.** Only tools the Phase 0 numbers rank. A wrapper for a tool called twice a week is negative-value maintenance.
- **Belt-driven subagent surfaces.** Subagent tool rosters (`.claude/agents/*.md`) are a separate mechanism; do not extend belts there in this pass.
- **Perfect token attribution.** Stream-json usage deltas are approximate around caching; a consistent approximation is sufficient for ranking. Do not build exact accounting.

## Risks

### Risk 1: Belt too tight breaks live sessions
**Impact:** A persona hits a missing tool mid-pipeline and stalls real work.
**Mitigation:** Belts are seeded by enumerating today's full surface (faithful snapshot, zero behavior change at activation), shrinking later; belts ship dark and activate in a dedicated commit so a stall points straight at the flip; the escalation line makes gaps visible within a turn; sustained escalation volume is the "cut too tight" signal per the PRD.

### Risk 2: CLI flag semantics drift across Claude CLI versions
**Impact:** An upgrade changes `--tools`/`--strict-mcp-config` behavior and belts silently widen.
**Mitigation:** The reproducibility test pins expected flag output; a doctor check asserts the installed CLI version supports the flags (Prerequisites row); belts record the CLI version they were validated against.

### Risk 3: Baseline numbers contradict the expected Lane B set
**Impact:** Effort planned for `sdlc-tool` wrapping turns out to be misdirected.
**Mitigation:** By design — Lane B ordering is data-bound (only `valor-docs-write` is exempt, being role-boundary-justified), and the plan's task list marks the expected set as provisional.

### Risk 5: Baseline contamination from early activation
**Impact:** Belts activate before the measurement window closes; the −40%/−25% targets lose their pre-change baseline and become unfalsifiable.
**Mitigation:** The resolver ships dark behind `TOOLBELTS_ENFORCE`; task 4's gate requires the published baseline report before the flip, and the flip is a dedicated commit that cannot ride in on a feature PR.

### Risk 4: Retirement removes a guard the belt does not actually cover
**Impact:** A capability the hook blocked becomes reachable.
**Mitigation:** One allowlist at a time; each removal requires its guard test rewritten to assert absence-in-belt and passing before the hook branch is deleted (mutation-check each guard: prove the test fails when the belt re-adds the tool).

## Race Conditions

### Race 1: Belt manifest edited while turns are in flight
**Location:** `agent/session_runner/belt_resolver.py` (new), worker turn loop
**Trigger:** `/update` pulls a new belt version (or the `TOOLBELTS_ENFORCE` flip) while a session is mid-turn.
**Data prerequisite:** The belt is resolved once at turn start and the resolved flags are immutable for the turn.
**State prerequisite:** Turn boundaries are the only belt-switch points (matches the steering-drain pattern).
**Mitigation:** Resolver reads the manifest into memory per turn; no mid-turn re-reads. The session records the belt version it ran under.

No other concurrency hazards identified: resolution is a pure per-turn read, and telemetry writes already serialize per session file.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3081] Bash scoping for the Dev belt — tracked as a resolved decision on the issue (Dev keeps open Bash); any future Bash-scoping effort gets its own issue and spec before belts are described as a boundary.
- [EXTERNAL] Marketing the belt file to clients — writing the outward-facing capability statement is a human/business action once the belt format stabilizes.

Nothing else deferred. MCP wholesale replacement, TOON adoption, and a third-party AXI ecosystem are PRD non-goals (never in scope), stated here for reader clarity rather than as deferrals.

## Update System

- `config/toolbelts/*.toml` ships with the repo; `/update` propagates it via normal git sync — no new update step.
- The `/update` doctor gains one check: installed Claude CLI supports the belt flags (`claude --help` grep). Add to `scripts/update/verify.py`.
- No migrations: belts are new files; existing sessions without a recorded belt version are treated as pre-belt legacy reads.
- `TOOLBELTS_ENFORCE` lives in `config/settings.py` with a committed default (env-overridable per the existing settings pattern), so the activation flip propagates to every machine through normal git sync + `/update` — the fleet activates together, and a per-machine env override exists only as a break-glass rollback.

## Agent Integration

- Phase 1: no new CLI entry points — the resolver is runner-internal (`agent/session_runner/`), wired into the existing turn-start path.
- Phase 2: each AXI wrapper is a new `[project.scripts]` entry in `pyproject.toml` (e.g. `valor-docs-write`, extended `sdlc-tool` reads), reachable through the persona's belt.
- Integration tests verify a headless turn launched with each persona's belt can invoke a belt tool and cannot see an off-belt tool (the drift/absence tests double as agent-integration proof).

## Documentation

- [ ] Create `docs/features/persona-toolbelts.md` — the belt model, manifest format, resolver behavior, escalation path, and the open-Bash caveat stated plainly.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/teammate-session-permissions.md` when Phase 3 moves Teammate writes to `valor-docs-write`.
- [ ] Update `docs/features/headless-session-runner.md` with the turn-start belt resolution step.
- [ ] Update `CLAUDE.md` session-types table to reference belts once Phase 1 lands.

## Success Criteria

- [ ] Baseline report exists: tool-surface tokens per merged PR, tool-call turns per merged PR, PreToolUse denial counts — published before activation.
- [ ] One manifest per persona in `config/toolbelts/`, version-pinned; resolver refuses unresolvable belts; flag off leaves the harness invocation byte-identical.
- [ ] Reproducibility test fails when the same persona+version resolves different surfaces in two environments.
- [ ] Activation is a dedicated commit flipping `TOOLBELTS_ENFORCE`, nothing else in the diff.
- [ ] Escalation line lands on the open-question channel, tagged, non-blocking.
- [ ] AXI linter fails a tool missing empty-state handling, structured exit codes, or `--full`.
- [ ] Top baseline-ranked tools wrapped, each its own PR, each retiring its redundant hook in the same PR with a mutation-checked guard test.
- [ ] Teammate docs writes survive via `valor-docs-write` while general Write/Edit leaves its belt.
- [ ] Post-rollout measurement against baseline reported (targets −40% context / −25% turns; first-pass review rate not regressed).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (instrumentation)** — Name: `telemetry-builder` — Role: Lane A token attribution + baseline report — Agent Type: builder — Resume: true
- **Builder (belts)** — Name: `belt-builder` — Role: Lane A manifests, dark resolver, harness wiring; owns the activation flip — Agent Type: builder — Resume: true
- **Builder (wrappers + retirement)** — Name: `axi-builder` — Role: Lane B wrappers + linter, retiring each redundant hook in the same PR — Agent Type: builder — Resume: true
- **Validator (all lanes)** — Name: `belt-validator` — Role: verify each lane's criteria, run drift/absence tests, baseline stability — Agent Type: validator — Resume: true
- **Documentarian** — Name: `belt-documentarian` — Role: feature docs + index + cross-doc updates — Agent Type: documentarian — Resume: true

## Step by Step Tasks

### 1. Lane A: per-tool context-cost attribution
- **Task ID**: build-instrumentation
- **Depends On**: none
- **Validates**: tests/unit/test_session_telemetry.py, tests/unit/test_tool_budget.py
- **Informed By**: spike-2 (telemetry + budget modules already exist)
- **Assigned To**: telemetry-builder
- **Agent Type**: builder
- **Parallel**: true
- Attribute stream-json token usage per tool call in the runner's parse path
- Stamp per-tool counts and cost onto session telemetry events and stage transitions
- Add `tools.belt_baseline` report (tokens per merged PR, turns per merged PR, denial counts)

### 2. Lane A: manifests + dark resolver + drift test + escalation
- **Task ID**: build-belts-dark
- **Depends On**: none
- **Validates**: tests/unit/test_belt_resolver.py (create), tests/unit/test_harness_stale_uuid_result_preservation.py
- **Informed By**: spike-1 (CLI flags confirmed on 2.1.236; `--tools` is true absence)
- **Assigned To**: belt-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `config/toolbelts/{pm,dev,teammate}.toml` seeded by enumerating today's surface (faithful snapshot)
- Implement `belt_resolver.py` (pure, fail-closed) behind `TOOLBELTS_ENFORCE` (default off) and wire into `harness/claude.py` flag composition; flag off → byte-identical invocation
- Reproducibility test: identical resolution across two synthetic environments
- Escalation line: role-skill instruction + runner tagging onto the open-question channel

### 3. Validate Lane A + publish baseline
- **Task ID**: validate-lane-a
- **Depends On**: build-instrumentation, build-belts-dark
- **Assigned To**: belt-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the baseline report over recent merged PRs; confirm numbers are non-empty and stable across two runs
- With flag off: assert the harness invocation is byte-identical to pre-belt behavior
- With flag on (test harness only): launch a headless turn per persona; assert belt tools callable, off-belt tools absent, unresolvable belt refuses loudly

### 4. Activation flip (dedicated event)
- **Task ID**: activate-belts
- **Depends On**: validate-lane-a
- **Assigned To**: belt-builder
- **Agent Type**: builder
- **Parallel**: false
- Gate: baseline report published over the agreed measurement window (2-4 weeks of merged PRs) — do NOT flip early; the −40% target is unfalsifiable without a pre-activation baseline
- Flip `TOOLBELTS_ENFORCE` on in a dedicated commit, nothing else in the diff
- Watch the first live turns per persona; a stall here indicts the flip, not a feature PR

### 5. Lane B: wrappers with interleaved retirement
- **Task ID**: build-wrappers-retire
- **Depends On**: activate-belts
- **Validates**: tests/unit/test_axi_lint.py (create), per-wrapper tests (create), tests/unit/test_teammate_write_restriction.py, tests/unit/test_sdk_permissions.py
- **Informed By**: baseline ranking (expected set is provisional: sdlc-tool reads, PR/issue, test execution, worktree state)
- **Assigned To**: axi-builder
- **Agent Type**: builder
- **Parallel**: false
- First wrapper regardless of ranking: `valor-docs-write` with the compiled path allowlist; same PR removes Teammate Write/Edit from the belt and rewrites its guard test to assert belt-absence
- Land `tools/axi_lint.py` with that first wrapper
- Then one PR per baseline-ranked wrapper; each PR retires the hook it makes redundant, guard test rewritten and mutation-checked in the same PR

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-wrappers-retire
- **Assigned To**: belt-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- All items in ## Documentation

### 7. Final validation + re-measurement
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: belt-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the baseline report; publish the before/after against the −40%/−25% targets and first-pass review rate

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Belt manifests exist | `ls config/toolbelts/pm.toml config/toolbelts/dev.toml config/toolbelts/teammate.toml \| wc -l` | output > 2 |
| Resolver fails closed | `python -c "from agent.session_runner.belt_resolver import resolve_belt; resolve_belt('nonexistent')" 2>&1 \| grep -ci "unresolvable\|unknown persona"` | output > 0 |
| CLI supports belt flags | `claude --help 2>&1 \| grep -c -- "--strict-mcp-config"` | output > 0 |
| No new bypass of belt in harness | `grep -c "bypassPermissions" agent/session_runner/harness/claude.py` | output > 0 |

(The last row is a placeholder until Open Question 2 decides the permission-mode interaction. The drift test itself runs inside the test suite row.)

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | spike-1 confirms the belt flags exist (`claude --help`) but never verifies that omitting a tool from `--tools` actually removes definition tokens at runtime; the −40% context target rests on this unverified premise. | pending | Pre-Lane-A micro-spike: run an identical prompt with full vs restricted `--tools` and diff `usage.input_tokens` from the stream-json `result` event (parse path already exists at `agent/session_runner/harness/claude.py` ~1140-1210). A near-zero drop means the value proposition gets revisited before Lane A starts, not at Task 7. |
| CONCERN | Risk & Robustness | Race 1 misses the `--resume` path: a belt narrowing between turns of one live session leaves `--tools` omitting a tool whose `tool_use` blocks are already baked into the replayed history — distinct from the "current turn requests a missing tool" case the escalation line targets. | pending | Add a resume case to `tests/unit/test_belt_resolver.py`: `prior_uuid` set and a `--tools` list that is a strict subset of the tools referenced in a fake resumed history's `tool_use` blocks; pin the actual observed outcome (escalation vs hard failure) rather than assuming graceful degradation. |
| CONCERN | Risk & Robustness | The activation flip propagates per machine via git sync + `/update`, not atomically; during the window the same session can resolve different tool surfaces depending on which host takes its next turn — briefly reproducing the exact machine-dependence problem the plan exists to close. | pending | Stamp the `TOOLBELTS_ENFORCE` state (not just belt_version) onto the AgentSession; on turn start `belt_resolver.py` compares the prior-turn stamp against the current host's resolved state and emits a WARNING-level telemetry event (fail-quiet, per `record_telemetry_event`) on mismatch, so fleet skew during the flip is observable. |
| CONCERN | Scope & Value | `tools/axi_lint.py` is a general static-analysis subsystem enforcing five style rules over what Rabbit Holes concedes may be 3-5 wrappers; disproportionate to the core deliverable and untracked by either headline metric. | pending | Add a floor to task 5: build the linter only once wrapper count reaches 3; until then, a short manual review checklist against the nine AXI principles covers the first wrapper(s). |
| CONCERN | Scope & Value | The client-facing outcome ("the belt file answers what Valor ships with in one read") has no success criterion; all ten criteria are internal/technical, and No-Gos defers the outward-facing statement as [EXTERNAL]. | pending | Require each `config/toolbelts/{persona}.toml` entry to carry a one-line human-readable `# why` comment, and add a Success Criteria row: "A belt file, read cold, states what the persona can and cannot do without codebase context." |
| CONCERN | History & Consistency | Belt-flag composition never states the positional argv ordering constraint every existing flag injection carries — `--model` and `--settings` each have hand-written guards ensuring they precede the positional message and `--resume` in `harness_cmd`. | pending | Insert belt-composed flags at the same point as the existing `--model`/`--settings` `harness_cmd.extend(...)` calls in `agent/session_runner/harness/claude.py` (before the positional message is appended), and add an argv-order assertion — not just flag-set equality — to the reproducibility test. |
| CONCERN | History & Consistency | The Test Impact row for `tests/unit/test_sdk_permissions.py` mischaracterizes the file: it pins `--permission-mode bypassPermissions` as a deliberate plan #2000 constant (no per-session customization), not hook-denial role scoping; that settled decision is not surfaced at Open Question 2, which proposes revisiting exactly that constant. | pending | `test_default_permission_mode_is_bypass` asserts `cmd[idx + 1] == "bypassPermissions"` unconditionally — any per-persona permission-mode branch must parametrize that exact assertion or it fails regardless of lane. Correct the row and cross-reference plan #2000 Task 2.2 from Open Question 2. |
| CONCERN | Structural | "Belt version recorded on the AgentSession" appears in Data Flow step 6, Architectural Impact, and Race 1's mitigation, but no task in Step by Step Tasks owns the AgentSession field work. | pending | Add the field to task 2 (build-belts-dark): an optional AgentSession field defaulting to None (pre-belt legacy read, consistent with the stated no-migration disposition), written via the ORM only — never raw Redis. |
| NIT | History & Consistency, Structural | Stale four-phase language survives the two-lane revision: Agent Integration says "Phase 1/Phase 2", Documentation says "Phase 3", Architectural Impact says "P2 of the PRD" — readers must re-derive the mapping via the Technical Approach table each time. | pending | — |
| NIT | Structural | Risks are numbered 1, 2, 3, 5, 4 in that order. | pending | — |

---

## Open Questions

1. **Belt versioning scheme**: do belts version with the repo (implicit via git, simplest) or carry an independent `belt_version` integer bumped on every edit (better for the client-facing artifact and session audit stamps)? Plan currently assumes an explicit pin; confirm or simplify.
2. **Permission-mode interaction**: with belts composed via `--tools`/`--strict-mcp-config`, does the headless runner keep `--permission-mode bypassPermissions` (absence does the scoping; prompts stay impossible) or move to a stricter mode for Teammate? Plan assumes keep, since headless turns cannot answer prompts.

(Resolved 2026-09-02: belt seeding is by enumeration of the current surface, not observed usage — decided with the two-lane compaction; see Technical Approach.)
