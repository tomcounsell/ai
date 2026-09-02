---
status: Planning
type: feature
appetite: Large
owner: Tom Counsell
created: 2026-09-02
tracking: https://github.com/tomcounsell/ai/issues/3081
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-09-02T11:45:35Z
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

**Baseline commit:** `5021a40aa924f0e2a3b44fa75713690e7a730911` (plan time) — **re-baselined at `f60a00fd4`** (2026-09-02, critique round 2): ~70 commits landed on main during the plan/critique cycle, including `config/settings.py` churn; no core harness/hook file changed, and all file:line claims below re-verify at the new baseline (critique round 2 additionally confirmed the `--model`/`--settings` extend calls at `harness/claude.py:370/:380` precede the `--resume`/message append).
**Issue filed at:** 2026-09-02T10:48:50Z
**Disposition:** Unchanged (minor drift absorbed at re-baseline)

Issue #3081 was filed minutes before this plan and no commits had landed on main at plan time (HEAD predated the issue). All file:line claims in the issue were verified during its own recon at this same baseline: `_HARNESS_COMMANDS` with `bypassPermissions` at `agent/session_runner/harness/claude.py:155-166`, persona→skill mapping at `agent/session_runner/role_driver.py:65-72`, teammate write gate at `agent/hooks/pre_tool_use.py:539`.

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

### spike-3: omitting a tool from `--tools` measurably removes definition tokens at runtime
- **Assumption**: "The −40% context target rests on `--tools` restriction actually reducing input tokens, not just gating calls." (Raised by critique round 1.)
- **Method**: prototype — identical one-line prompt run twice via `claude -p --output-format json --max-turns 1` from a directory with no project `.mcp.json`, diffing input-side usage from the JSON result.
- **Finding**: full surface: 40,062 `cache_creation_input_tokens`; `--tools Bash`: 36,243. A 3,819-token (~9.5%) drop from built-in tool definitions alone, before any MCP trimming — production sessions load MCP servers whose definitions are far larger, and `--strict-mcp-config` removes those the same way.
- **Confidence**: high
- **Impact on plan**: the token-drop premise is verified before Lane A starts; the Lane A baseline will quantify the MCP share.

## Data Flow

1. **Entry point**: worker picks up an AgentSession turn; `role_driver.py` selects the persona (`pm` / `dev` / `teammate`).
2. **Belt resolver (new)**: if `TOOLBELTS_ENFORCE` is on, loads `config/toolbelts/{persona}.toml`, validates the pin, and compiles it to CLI flags (`--tools`, `--allowedTools`, `--mcp-config` + `--strict-mcp-config`). Unresolvable belt → refuse to start the turn with a structured error. Flag off → current ambient behavior, byte-identical invocation.
3. **Harness**: `harness/claude.py` appends the compiled flags to `_HARNESS_COMMANDS` and launches `claude -p`.
4. **Telemetry (Phase 0)**: the stream-json parser attributes tokens per tool call and stamps counts/cost onto the session telemetry stream and stage transitions.
5. **Escalation (Phase 1)**: when the agent reports a missing capability, one tagged line is appended to the session's open-question channel output; non-blocking.
6. **Output**: session proceeds normally; belt version AND the resolved `TOOLBELTS_ENFORCE` state are stamped on the AgentSession (optional fields, ORM writes only). At turn start the resolver compares the prior-turn stamp against the current host's resolved state and emits a WARNING-level telemetry event on mismatch (fail-quiet, via `record_telemetry_event`), making fleet skew during the activation window observable.

## Architectural Impact

- **New dependencies**: none (TOML via stdlib `tomllib`; everything else is existing CLI flags).
- **Interface changes**: `_HARNESS_COMMANDS` becomes a base template + per-persona flag composition; a new `agent/session_runner/belt_resolver.py` module; a new `config/toolbelts/` directory.
- **Coupling**: decreases — role capability moves out of hooks and prompt text into one declared file per persona.
- **Data ownership**: belt version and enforce-state recorded on the AgentSession (optional audit fields owned by task 2, default None).
- **Reversibility**: high — removing the resolver call restores the current ambient behavior; Lane B hook removals each keep their guard test.

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

- **Belt manifest** (`config/toolbelts/{persona}.toml`): version-pinned declaration of built-in tools, MCP servers, and CLI entry points a persona may use. Committed, reviewed, client-shareable. Every entry carries a one-line human-readable `# why` comment so the file, read cold, states what the persona can and cannot do without codebase context.
- **Belt resolver** (`agent/session_runner/belt_resolver.py`): pure function persona → compiled CLI flags; fails closed on unknown persona, missing manifest, or version mismatch. Ships **dark** behind an activation flag so the baseline window closes before enforcement changes anything.
- **Context-cost instrumentation**: per-tool call counts and token attribution stamped onto session telemetry and stage transitions, plus a baseline report per merged PR.
- **Missing-capability escalation**: one tagged, non-blocking line riding the existing open-question channel.
- **AXI wrappers with interleaved retirement**: thin CLIs over `gh`/`git`/`pytest` for the top tools by measured cost; each wrapper PR retires the PreToolUse hook it makes redundant in the same PR, guard test rewritten to assert belt-absence and mutation-checked. The AXI conformance linter is deferred until the wrapper count reaches 3 (the AXI checklist in Technical Approach covers earlier wrappers manually). `valor-docs-write` is the first wrapper regardless of ranking — it is justified by the role-boundary goal, not context savings — and Teammate's general Write/Edit leaves the belt when it ships. Its compiled allowlist reproduces the FULL current `_teammate_is_allowed_write` scope, all four mechanisms (anchored root dirs `docs/`, `.claude/`, `.github/`, `wiki/`, `skills/`; the exact top-level meta-filename set; the top-level `*.md` rule; the `~/work-vault/` absolute prefix) — a `docs/`-only tool would silently strip skill/wiki/vault edits with no denial to explain why.

### Flow

Worker picks turn → resolver loads persona belt → belt compiles to CLI flags → `claude -p` starts with only the declared surface → agent works; a missing tool produces an escalation line, not a workaround → telemetry stamps per-tool cost → belt version recorded on the session.

### Technical Approach

The PRD's four phases compact into **two build lanes plus one activation event**. The PRD phase numbering maps as: Lane A = PRD Phases 0+1, Activation = the Phase 0→1 boundary, Lane B = PRD Phases 2+3. The compaction is safe because Phase 1 never truly depended on Phase 0's data (belts are seeded by *enumerating* the current surface, not by measuring usage), and retirement was always coupled to wrapping (a hook can only go once its replacement exists).

- **Lane A — Instrument + declare (dark).** Two disjoint workstreams in one lane. (1) Instrumentation: extend the stream-json parse path in the runner to attribute tokens per tool call; stamp aggregates onto `session_telemetry` events and stage transitions; add a `tools.belt_baseline` report (tool-definition + tool-output tokens per merged PR, tool-call turns per merged PR, PreToolUse denial counts). (2) Belts: manifest format TOML, one file per persona, `belt_version` pin, seeded by enumeration of today's surface (faithful snapshot, zero behavior change). Resolver composes `--tools` for built-ins, `--strict-mcp-config` + generated per-persona MCP config for MCP, `--allowedTools`/`--disallowedTools` where a tool stays visible but scoped — all **behind an activation flag** (`TOOLBELTS_ENFORCE` via `config/settings.py`), default off. Dev keeps open Bash by decision — belts are ergonomics and reproducibility, not a trust boundary, and every doc says so plainly. Reproducibility test: resolve each belt in two synthetic environments (different env vars, different fake host inventory) and assert byte-identical flag output. Escalation: the role priming skills gain one line of instruction; the runner tags and forwards it on the open-question channel.
- **Activation — its own observable event.** After the baseline window (2-4 weeks of merged PRs) closes and the baseline report is published, flip `TOOLBELTS_ENFORCE` on in a dedicated commit. Baseline measurement MUST precede activation or the −40% target becomes unfalsifiable — the flag exists precisely to let both halves of Lane A merge early without contaminating the measurement. Keeping the flip isolated also makes it the obvious suspect if a live session stalls on a too-tight belt.
- **Lane B — Wrap + retire, interleaved.** Wrapper order ranked strictly by the baseline numbers (expected but provisional: `sdlc-tool` family reads such as `stage-query` returning PR number + merge state + CI + verdict in one call, PR/issue read-write, test execution with baseline-delta classification, worktree/branch state). Exception: `valor-docs-write` goes first regardless of ranking, since it is justified by the role-boundary goal. Each wrapper is a thin `[project.scripts]` entry over `gh`/`git`/`pytest`, and **each wrapper PR retires the PreToolUse hook it makes redundant in the same PR**: hook branch removed, guard test rewritten to assert belt-absence, mutation-checked in that PR (prove the test fails when the belt re-adds the tool). The wrapper and its guard land atomically, so there is no window where both old hook and new wrapper half-apply, and retirement — the phase the PRD flagged as most likely to be skipped — structurally cannot be skipped. Belt-composed flags are inserted at the same point as the existing `--model`/`--settings` `harness_cmd.extend(...)` calls in `harness/claude.py` — before the positional message and `--resume` — matching the hand-written ordering guard every existing flag injection carries; the reproducibility test asserts argv *order*, not just flag-set equality. The **AXI checklist** is the single authoritative bar for both the early manual reviews and the later linter — eight items, enumerated once here: (1) compact list output, 3-4 fields per item; (2) a `--full` escape hatch; (3) pre-computed aggregates so one call returns assembled state; (4) definitive empty states, never blank output; (5) structured, parseable errors; (6) documented, stable exit codes; (7) idempotent mutations; (8) a next-step suggestion after each output. Serialization format is deliberately excluded per the PRD's TOON non-goal. `tools/axi_lint.py` is built only once the wrapper count reaches 3 — manual review applies this same checklist to the first wrappers — and automates every item, so the manual-to-automated handoff does not change the bar.

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

- [ ] `tests/unit/test_teammate_write_restriction.py` — UPDATE (Lane B, first wrapper PR): assertions move from "hook denies Write outside docs/" to "Teammate belt omits Write/Edit; `valor-docs-write` refuses paths off the allowlist", parametrized across every current allow category (anchored root dirs, top-level filenames, top-level `*.md`, `~/work-vault/` prefix). Guard intent is preserved at full scope.
- [ ] `tests/unit/test_tool_budget.py` — UPDATE (Lane A): extended for context-cost attribution fields alongside call counts.
- [ ] `tests/unit/test_session_telemetry.py` — UPDATE (Lane A): new event fields for per-tool cost.
- [ ] `tests/unit/test_harness_stale_uuid_result_preservation.py` — NO CHANGE: its tests patch `_run_harness_subprocess` wholesale (the sole `cmd` occurrence is an unused fake-signature parameter) and assert on reply/fired/call counts, never argv, so belt-flag composition is invisible to it. The flag-off/flag-on argv-shape assertions live in the new `tests/unit/test_belt_resolver.py` reproducibility test (task 2); a harness-layer argv regression test would need a new captured-`cmd` fixture via the file's existing `AsyncMock(side_effect=fake)` pattern, which does not exist today.
- [ ] `tests/unit/test_sdk_permissions.py` — UPDATE (Lane A, only if permission mode ever branches): this file pins `--permission-mode bypassPermissions` as a deliberate plan #2000 constant (`test_default_permission_mode_is_bypass` asserts `cmd[idx + 1] == "bypassPermissions"` unconditionally) — it is not hook-denial role scoping. Open Question 2's resolution keeps the constant, so the expected disposition is NO CHANGE; any future per-persona permission-mode branch must parametrize that exact assertion and cite plan #2000 Task 2.2.

## Rabbit Holes

- **Scoping Bash.** Decided out. Any "while we're here, restrict Bash" impulse is a separate project; belts ship with the open-Bash caveat stated plainly.
- **Serialization format debates (TOON etc.).** Adopt the eight-item AXI checklist (Technical Approach); let baseline measurement pick output formats. No format migration work.
- **Wrapping everything.** Only tools the Phase 0 numbers rank. A wrapper for a tool called twice a week is negative-value maintenance.
- **Belt-driven subagent surfaces.** Subagent tool rosters (`.claude/agents/*.md`) are a separate mechanism; do not extend belts there in this pass.
- **Perfect token attribution.** Stream-json usage deltas are approximate around caching; a consistent approximation is sufficient for ranking. Do not build exact accounting.

## Risks

### Risk 1: Belt too tight breaks live sessions
**Impact:** A persona hits a missing tool mid-pipeline and stalls real work.
**Mitigation:** Belts are seeded by enumerating today's full surface (faithful snapshot, zero behavior change at activation), shrinking later; belts ship dark and activate in a dedicated commit so a stall points straight at the flip; the escalation line makes gaps visible within a turn. "Cut too tight" is a codified gate, not prose: escalation-tagged lines per persona per week above `ESCALATION_CEILING_MULTIPLIER` × the pre-activation denial baseline for two consecutive weeks flips `TOOLBELTS_ENFORCE` back off (see task 4 and Verification).

### Risk 2: CLI flag semantics drift across Claude CLI versions
**Impact:** An upgrade changes `--tools`/`--strict-mcp-config` behavior and belts silently widen.
**Mitigation:** The reproducibility test pins expected flag output; a doctor check asserts the installed CLI version supports the flags (Prerequisites row); belts record the CLI version they were validated against.

### Risk 3: Baseline numbers contradict the expected Lane B set
**Impact:** Effort planned for `sdlc-tool` wrapping turns out to be misdirected.
**Mitigation:** By design — Lane B ordering is data-bound (only `valor-docs-write` is exempt, being role-boundary-justified), and the plan's task list marks the expected set as provisional.

### Risk 4: Baseline contamination from early activation
**Impact:** Belts activate before the measurement window closes; the −40%/−25% targets lose their pre-change baseline and become unfalsifiable.
**Mitigation:** The resolver ships dark behind `TOOLBELTS_ENFORCE`; task 4's gate requires the published baseline report before the flip, and the flip is a dedicated commit that cannot ride in on a feature PR.

### Risk 5: Retirement removes a guard the belt does not actually cover
**Impact:** A capability the hook blocked becomes reachable.
**Mitigation:** One allowlist at a time; each removal requires its guard test rewritten to assert absence-in-belt and passing before the hook branch is deleted (mutation-check each guard: prove the test fails when the belt re-adds the tool).

## Race Conditions

### Race 1: Belt manifest edited while turns are in flight
**Location:** `agent/session_runner/belt_resolver.py` (new), worker turn loop
**Trigger:** `/update` pulls a new belt version (or the `TOOLBELTS_ENFORCE` flip) while a session is mid-turn.
**Data prerequisite:** The belt is resolved once at turn start and the resolved flags are immutable for the turn.
**State prerequisite:** Turn boundaries are the only belt-switch points (matches the steering-drain pattern).
**Mitigation:** Resolver reads the manifest into memory per turn; no mid-turn re-reads. The session records the belt version and enforce-state it ran under.

### Race 2: Belt narrowing across a `--resume` boundary
**Location:** `agent/session_runner/harness/claude.py` resume path (`prior_uuid` set), `belt_resolver.py`
**Trigger:** A belt narrows between turns of one live session; the next turn resumes with `--tools` omitting a tool whose `tool_use` blocks are already baked into the replayed history.
**Data prerequisite:** The resumed transcript may reference tools the current belt no longer offers — distinct from the "current turn requests a missing tool" case the escalation line targets.
**State prerequisite:** The CLI's behavior on this mismatch (graceful degradation vs hard failure) is unverified.
**Mitigation:** Pin the observed outcome in `tests/unit/test_belt_resolver.py`: a resume case with `prior_uuid` set and a `--tools` list that is a strict subset of the tools referenced in a fake resumed history's `tool_use` blocks. The test documents actual behavior rather than assuming grace; if it hard-fails, the resolver widens the belt to the union for resumed turns and logs it.

### Race 3: Fleet-skewed activation window
**Location:** `belt_resolver.py` turn-start stamp comparison
**Trigger:** The `TOOLBELTS_ENFORCE` flip propagates per machine via git sync + `/update`, not atomically; mid-window the same session can resolve different surfaces depending on which host takes its next turn.
**Data prerequisite:** Prior-turn enforce-state stamp on the AgentSession.
**State prerequisite:** None — skew is expected and transient during the window.
**Mitigation:** WARNING telemetry event on stamp-vs-host mismatch (fail-quiet); `tools.belt_skew_report` aggregates the per-session JSONL files into the single cross-session view an operator actually watches; the flip commit plus prompt fleet `/update` keeps the window short, and the per-machine env override remains break-glass rollback only.

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

- Lane A: no new CLI entry points — the resolver is runner-internal (`agent/session_runner/`), wired into the existing turn-start path.
- Lane B: each AXI wrapper is a new `[project.scripts]` entry in `pyproject.toml` (e.g. `valor-docs-write`, extended `sdlc-tool` reads), reachable through the persona's belt.
- Integration tests verify a headless turn launched with each persona's belt can invoke a belt tool and cannot see an off-belt tool (the drift/absence tests double as agent-integration proof).

## Documentation

- [ ] Create `docs/features/persona-toolbelts.md` — the belt model, manifest format, resolver behavior, escalation path, and the open-Bash caveat stated plainly.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/teammate-session-permissions.md` when Lane B's first wrapper PR moves Teammate writes to `valor-docs-write`.
- [ ] Update `docs/features/headless-session-runner.md` with the turn-start belt resolution step.
- [ ] Update `CLAUDE.md` session-types table to reference belts once Lane A activates.
- [ ] `docs/features/persona-toolbelts.md` must state plainly that belt absence is the *sole* enforcement layer headless — `bypassPermissions` stays (plan #2000), so no permission-mode safety net exists behind the belt.

## Success Criteria

- [ ] Baseline report exists: tool-surface tokens per merged PR, tool-call turns per merged PR, PreToolUse denial counts — published before activation.
- [ ] One manifest per persona in `config/toolbelts/`, version-pinned; resolver refuses unresolvable belts; flag off leaves the harness invocation byte-identical.
- [ ] Reproducibility test fails when the same persona+version resolves different surfaces in two environments.
- [ ] Activation is a dedicated commit flipping `TOOLBELTS_ENFORCE`, nothing else in the diff.
- [ ] Escalation line lands on the open-question channel, tagged, non-blocking.
- [ ] A belt file, read cold, states what the persona can and cannot do without codebase context (every entry has a `# why` comment).
- [ ] AXI linter (built at the wrapper-count-3 floor) fails a tool violating any item of the eight-item AXI checklist in Technical Approach.
- [ ] Escalation rollback gate is codified (`ESCALATION_CEILING_MULTIPLIER` in `config/settings.py`) and armed at activation, with `tools.belt_skew_report` providing the cross-session watch path.
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
- Add `tools.belt_skew_report`: globs the session-telemetry JSONL files, filters the Race 3 mismatch event type, prints session_id + host + counts — the cross-session query path `read_session_timeline` (one session at a time) cannot provide

### 2. Lane A: manifests + dark resolver + drift test + escalation
- **Task ID**: build-belts-dark
- **Depends On**: none
- **Validates**: tests/unit/test_belt_resolver.py (create), tests/unit/test_harness_stale_uuid_result_preservation.py
- **Informed By**: spike-1 (CLI flags confirmed on 2.1.236; `--tools` is true absence)
- **Assigned To**: belt-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `config/toolbelts/{pm,dev,teammate}.toml` seeded by enumerating today's surface (faithful snapshot); every entry carries a one-line `# why` comment
- Implement `belt_resolver.py` (pure, fail-closed) behind `TOOLBELTS_ENFORCE` (default off) and wire into `harness/claude.py` flag composition; flag off → byte-identical invocation
- Insert belt flags at the same point as the existing `--model`/`--settings` `harness_cmd.extend` calls, before the positional message and `--resume` (the argv-ordering guard every existing injection carries)
- Reproducibility test: identical resolution across two synthetic environments, asserting argv order, not just flag-set equality
- Resume case in `tests/unit/test_belt_resolver.py`: `prior_uuid` set, `--tools` a strict subset of the fake resumed history's `tool_use` tools; pin the observed outcome (Race 2)
- Add optional AgentSession fields for belt version + enforce-state, default None (pre-belt legacy read, no migration), ORM writes only; turn-start stamp comparison emits the Race 3 WARNING telemetry event on mismatch
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
- During the propagation window, run `python -m tools.belt_skew_report` (built in task 1) to watch Race 3 skew across sessions; prompt fleet `/update` keeps the window short
- Escalation rollback gate armed at flip time: if escalation-tagged lines per persona per week exceed `ESCALATION_CEILING_MULTIPLIER` (a `config/settings.py` constant, default 2×) times the pre-activation PreToolUse-denial baseline from `tools.belt_baseline` for two consecutive weeks, flip `TOOLBELTS_ENFORCE` back off and widen the offending belt before re-activating

### 5. Lane B: wrappers with interleaved retirement
- **Task ID**: build-wrappers-retire
- **Depends On**: activate-belts
- **Validates**: tests/unit/test_axi_lint.py (create), per-wrapper tests (create), tests/unit/test_teammate_write_restriction.py, tests/unit/test_sdk_permissions.py
- **Informed By**: baseline ranking (expected set is provisional: sdlc-tool reads, PR/issue, test execution, worktree state)
- **Assigned To**: axi-builder
- **Agent Type**: builder
- **Parallel**: false
- First wrapper regardless of ranking: `valor-docs-write` compiling the FULL `_teammate_is_allowed_write` scope (anchored root dirs, top-level meta filenames, top-level `*.md` rule, `~/work-vault/` prefix); same PR removes Teammate Write/Edit from the belt and rewrites its guard test parametrized across EVERY current category — `TEAMMATE_ALLOWED_DIR_NAMES_AT_ROOT`, the filename set, the extension rule, and the vault prefix, not a `docs/` sample — so a scope mismatch fails the mutation-checked guard before merge instead of surfacing as a live Teammate stall
- Early wrappers are reviewed against the eight-item AXI checklist (Technical Approach); build `tools/axi_lint.py` only once the wrapper count reaches 3 (linter floor — do not build static analysis for one or two tools)
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
| Escalation rollback gate codified | `grep -c "ESCALATION_CEILING_MULTIPLIER" config/settings.py` | output > 0 |
| Skew report exists | `python -m tools.belt_skew_report --help >/dev/null 2>&1; echo $?` | output contains 0 |
| No new bypass of belt in harness | `grep -c "bypassPermissions" agent/session_runner/harness/claude.py` | output > 0 |

(The last row is now a real anti-criterion: Open Question 2 resolved to keep `bypassPermissions` per plan #2000, so its continued presence in the harness is asserted, guarding against an accidental permission-mode change riding in with belt work. The drift test itself runs inside the test suite row.)

## Critique Results

Round 2 (2026-09-02, FULL war room at plan revision 9d58b8dfb). All ten round-1 findings verified as genuinely addressed by all three critics — spot-checked against source, none re-raised (round-1 table preserved in git history at 1cfcd64fb).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | The first Lane B wrapper is framed everywhere as replacing "Teammate docs writes", but the hook it retires (`_teammate_is_allowed_write`, `agent/hooks/pre_tool_use.py:118-164`) allows five root directory categories (`docs/`, `.claude/`, `.github/`, `wiki/`, `skills/`) plus a top-level meta-files allowlist and the `~/work-vault/` prefix. Nothing states `valor-docs-write`'s compiled allowlist reproduces that full set; if it covers only `docs/`, removing general Write/Edit from the Teammate belt in the same PR silently strips skill/wiki/vault edits with no denial message to explain why. | Key Elements + task 5: full four-mechanism scope stated; guard test parametrized across every category |  In the first-wrapper PR's guard-test rewrite (`tests/unit/test_teammate_write_restriction.py`), parametrize every path category currently exercised against `TEAMMATE_ALLOWED_DIR_NAMES_AT_ROOT` (docs/, .claude/, .github/, wiki/, skills/), the top-level filename set, and the `~/work-vault/` prefix — not just a `docs/` sample — so a scope mismatch fails the mutation-checked guard before merge rather than surfacing as a live Teammate stall. State in Key Elements/task 5 that the compiled allowlist is the full current set (or scope the tool name to what it actually covers and keep Write/Edit on the belt until the rest is wrapped). |
| CONCERN | Risk & Robustness | Race 3's mitigation and task 4 tell an operator to "watch for Race 3 skew WARNINGs in telemetry", but `record_telemetry_event` (`agent/session_telemetry.py:173`) writes per-session JSONL read back only by `read_session_timeline(session_id)` one session at a time — there is no cross-session aggregation or query path, so during a fleet-wide window there is no single place to watch. | `tools.belt_skew_report` added (task 1); task 4 names it as the literal command; Race 3 + Verification row updated |  Add a `tools.belt_skew_report` (or extend `tools.belt_baseline`) that globs `logs/session_telemetry/*.jsonl`, filters on the Race 3 mismatch event type, and prints session_id + host + counts; name it in task 4 as the literal command run during the propagation window, since `read_session_timeline` alone only answers "did this one session skew". |
| CONCERN | Scope & Value | Risk 1 names "sustained escalation volume" as the cut-too-tight signal, but no Success Criteria or Verification row turns it into a measured threshold or rollback trigger — task 4's "watch the first live turns" is an operational note, not a codified gate, so activation could proceed indefinitely against a regression the plan itself anticipates. | `ESCALATION_CEILING_MULTIPLIER` (2× pre-activation denial baseline, two consecutive weeks → flip off) in Risk 1, task 4, Verification, Success Criteria |  Ground the threshold in data task 1 already collects: the pre-activation PreToolUse-denial baseline from `tools.belt_baseline`. Add a Verification-table row of the form "escalation-tagged lines per persona per week must not exceed N× the pre-activation denial baseline for two consecutive weeks, else `TOOLBELTS_ENFORCE` flips back off" so the signal is checked like every other command/expected pair instead of living as prose in a Risk mitigation. |
| CONCERN | History & Consistency | The Test Impact row for `tests/unit/test_harness_stale_uuid_result_preservation.py` claims it has "fixtures that assert the exact `_HARNESS_COMMANDS` shape"; the file's only `cmd` occurrence is an unused parameter in the `_make_fake_run` fake's signature — every test patches `_run_harness_subprocess` wholesale and asserts on reply/fired/call counts, never argv. Same mischaracterization class round 1 caught in `test_sdk_permissions.py`, in a row round 1 did not check. | Row corrected to NO CHANGE (verified: sole `cmd` is an unused fake param); argv assertions placed in `test_belt_resolver.py` |  Correct the row to expected NO CHANGE (the fake replaces `_run_harness_subprocess` below the point where belt flags are composed) and put the flag-off/flag-on argv-shape assertions where the plan already places them: the new `tests/unit/test_belt_resolver.py` reproducibility test (task 2). If a harness-layer regression test is wanted too, it needs a new fixture capturing the `cmd` argument via the file's existing `AsyncMock(side_effect=fake)` pattern and asserting on the captured list — that fixture does not exist today. |
| CONCERN | History & Consistency | "The nine AXI principles" is invoked four times (Key Elements, Rabbit Holes, task 5) but never enumerated in the plan; the only concrete list anywhere totals five checks, and the AXI-linter Success Criteria row encodes only three of those five (idempotent mutations and next-step suggestion unchecked). Task 5's manual checklist for early wrappers has no authoritative source for the other four principles. | Eight-item AXI checklist enumerated once in Technical Approach; all "nine" mentions replaced; linter criterion reconciled to the full list |  Enumerate the full principle list once in Key Elements or Technical Approach (pulling from the PRD) so task 5's manual checklist and `tools/axi_lint.py` check the same bar — or correct every "nine" to the real count. Reconcile the linter's Success Criteria row against whatever list gets written down; otherwise the manual-to-automated handoff at wrapper-count-3 silently changes the bar. |
| NIT | Structural | Freshness Check still claims "no commits have landed on main since" baseline 5021a40aa; ~70 commits have since landed (including `config/settings.py` churn from f6ba598ce). All plan file:line claims re-verify at HEAD 9d58b8dfb and no core harness/hook file changed, so this is re-baseline bookkeeping only. | Freshness Check re-baselined at f60a00fd4 | — |

---

## Open Questions

None outstanding. Resolutions on record:

1. **Belt versioning** (resolved 2026-09-02, critique round 1 unanimous): explicit `belt_version` integer pin, bumped on every edit. The resolver's fail-closed version-mismatch check needs a discrete comparable value; implicit git versioning drifts transiently between hosts mid-`/update` and makes every unrelated commit look like a belt change.
2. **Permission mode** (resolved 2026-09-02, critique round 1 unanimous): keep `--permission-mode bypassPermissions` — headless turns cannot answer prompts, and belt absence is the scoping mechanism. This reaffirms plan #2000 Task 2.2's pinned decision (`test_default_permission_mode_is_bypass`) rather than being a routine default. Rider honored in ## Documentation: the feature doc states plainly that belt absence is the sole enforcement layer headless.
3. **Belt seeding** (resolved 2026-09-02 with the two-lane compaction): by enumeration of the current surface, not observed usage; see Technical Approach.
