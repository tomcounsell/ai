---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-16
tracking: https://github.com/tomcounsell/ai/issues/1711
last_comment_id:
---

# MCP Hang Graceful Degradation (steering on tool_timeout recovery)

## Problem

When an MCP tool call (e.g. `mcp__claude_ai_Notion__notion-fetch`) hangs past its 120 s
budget, the per-tool timeout sub-loop (`_agent_session_tool_timeout_loop` in
`agent/session_health.py`, from issue #1270) kills the session and re-queues it as
`pending` with `recovery_attempts += 1`. The re-queued session is handed the **same**
turn input it had before, so the model re-issues the same hanging tool call, wedges a
second time, hits `MAX_RECOVERY_ATTEMPTS=2`, and is finalized as `failed`. The user's
original message is never answered.

**Current behavior:**
The `tool_timeout` recovery path (`_apply_recovery_transition` → requeue branch at
`agent/session_health.py:1468-1520`) re-queues with no signal about *why* the session
was killed. The model has no way to know the tool is unavailable, so it repeats the call.

Real example (session `tg_cyndra_8762685703_10818`, 2026-06-16 05:29–05:30 UTC):
- First run: `mcp__claude_ai_Notion__notion-fetch` hung at 120 s → killed → re-queued
- Second run: same tool hung again → `failed` permanently
- Original message: never answered

**Desired outcome:**
On a `tool_timeout` recovery, before re-queuing, inject a steering message naming the
timed-out tool and instructing the model to skip it and degrade gracefully. The session
delivers a degraded-but-real response ("I couldn't reach Notion; here's what I can answer
without it") instead of escalating to `failed`.

## Freshness Check

**Baseline commit:** `fc1f73c88a5120496a5f26972b9085c2870c818e`
**Issue filed at:** 2026-06-16T05:40:08Z
**Disposition:** Unchanged

**File:line references re-verified (against baseline):**
- `agent/session_health.py` `_agent_session_tool_timeout_loop` / `_agent_session_tool_timeout_check` — still present; the `tool_timeout` recovery call site is `_apply_recovery_transition(fresh, …, reason_kind="tool_timeout", …)` at **`agent/session_health.py:2284`** (issue cited "the sub-loop" without a line; corrected here).
- `_apply_recovery_transition` — present at **`agent/session_health.py:1132`**; the `pending` requeue branch is at **lines 1468–1520**.
- `MAX_RECOVERY_ATTEMPTS=2` — present at **`agent/session_health.py:232`** (issue cited `:232` — accurate).
- `_check_tool_timeout()` / `_classify_tool_tier()` / `TOOL_TIMEOUT_MCP_SEC=120` — present at lines 345, 317, 306.
- `push_steering_message()` — present at **`models/agent_session.py:1918`** (issue cited `:1918` — accurate). `pop_steering_messages()` at `:1941`.
- Steering delivery / pop at the turn boundary — present at **`agent/session_executor.py:1514-1537`**; the popped steering message **replaces** the turn input (`_turn_input = steering_msgs[0]`).
- `current_tool_name` field — present at **`models/agent_session.py:444`**; populated by the PreToolUse hook, NOT cleared on requeue.

**Cited sibling issues/PRs re-checked:**
- #1270 (per-tool timeout tiers) — the feature this plan extends; its plan is `docs/plans/per_tool_timeout_tier_counters.md`. Still the live mechanism.
- Session-steering design — `docs/features/session-steering.md`; mechanism unchanged.

**Commits on main since issue was filed (touching referenced files):** none.
`git log --since=2026-06-16T05:40:08Z -- agent/session_health.py models/agent_session.py agent/session_executor.py` returned no commits.

**Active plans in `docs/plans/` overlapping this area:**
- `per_tool_timeout_tier_counters.md` — the #1270 base mechanism (not a conflict; this plan builds on it).
- `parent-child-steering.md`, `summarizer-fallback-steering.md`, `consolidate-steering-docs.md` — touch the steering subsystem but at different call sites; no overlap with the `tool_timeout` recovery path.

**Notes:** Bug confirmed present by code read (reproduction in a live worker is infeasible — requires a real upstream MCP hang). The requeue branch injects no steering today; the defect is real and unaddressed.

## Prior Art

- **Issue/PR #1270**: Per-tool timeout tiers — added `_agent_session_tool_timeout_loop`, `_classify_tool_tier`, `current_tool_name` tracking, and the `reason_kind="tool_timeout"` recovery path. This plan adds the missing "tell the model what timed out" step to that path.
- **Session steering (`docs/features/session-steering.md`)**: `push_steering_message()` / `pop_steering_messages()` already exist and are exercised by `tests/integration/test_steering.py`. The worker pops steering at the turn boundary (`session_executor.py:1522`). No new mechanism is needed — only a new caller.
- No prior closed issue attempted to inject steering on `tool_timeout` recovery. This is the first fix of this specific defect.

## Research

No relevant external findings — this is a purely internal change to the worker's session-health recovery path and the steering subsystem. No external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

1. **Entry point**: PreToolUse hook fires when the model invokes `mcp__claude_ai_Notion__notion-fetch`, writing `current_tool_name` and `last_tool_use_at` on the `AgentSession`.
2. **Wedge**: PostToolUse never returns (upstream MCP hang). After 120 s, `_agent_session_tool_timeout_check` (`session_health.py:~2194`) re-reads the session, `_check_tool_timeout(fresh)` returns `(tier="mcp", reason=...)`.
3. **Counter + recovery**: the check bumps `tool_timeout_count_mcp`, INCRs `…:tool_timeouts:mcp`, then calls `_apply_recovery_transition(fresh, reason_kind="tool_timeout", handle, worker_key)` at `session_health.py:2284`.
4. **Recovery transition**: `_apply_recovery_transition` cancels the task, confirms the subprocess is dead, and — in the `else` requeue branch (`:1468`) — sets `status="pending"`, `started_at=None`. **(NEW injection point)**: before/at this branch, when `reason_kind == "tool_timeout"`, push a steering message naming `current_tool_name`.
5. **Re-pickup**: the worker picks the pending session back up. At the turn boundary (`session_executor.py:1522`), `pop_steering_messages()` returns the injected message; `_turn_input = steering_msgs[0]` **replaces** the original message text.
6. **Output**: `build_harness_turn_input(_turn_input, …)` wraps the steering text with context headers and sends it to the brand-new TUI container run (no `--resume`). The model reads "skip tool X, answer without it", produces a degraded response, and delivers it to the user.

**Load-bearing fact**: the container has no `claude --resume` wiring (`session_executor.py:1653-1659`) — every run is a fresh TUI session. The steering message becomes the *entire* turn input, so it must be **self-contained**: it must reference the original request, not merely say "skip the tool". The original request is preserved on the session as `entry.message_text` and must be woven into the steering text.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none — `push_steering_message(text)` already exists. The fix is a new caller plus a small helper to compose the steering text.
- **Coupling**: `_apply_recovery_transition` gains a read of `entry.current_tool_name` and `entry.message_text` (both already on the model) and a `push_steering_message` call. No new cross-module coupling — `session_health.py` already imports `AgentSession`.
- **Data ownership**: unchanged. The steering queue is owned by `AgentSession`.
- **Reversibility**: trivial — the injection is a single guarded block; remove it to revert.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 1 (confirm the steering-vs-suppression decision in Open Questions)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Runs entirely against the worker's in-process session-health loop and Redis-backed `AgentSession`.

## Solution

### Key Elements

- **Steering composer** (`_compose_tool_timeout_steering`): a small pure helper in `agent/session_health.py` that takes the tool name and the original request text and returns a self-contained steering string. Pure and unit-testable.
- **Injection in the requeue path**: in `_apply_recovery_transition`, when `reason_kind == "tool_timeout"` AND the transition is taking the **requeue (`pending`) branch** (not `failed`/`abandoned`), call `entry.push_steering_message(_compose_tool_timeout_steering(...))` before `transition_status(entry, "pending", …)`.
- **Tool-name capture**: read `current_tool_name` from `entry` at recovery time (it is not cleared on requeue, but capture it into a local before any save to be safe against concurrent clears).

### Flow

MCP tool hangs → tool-timeout sub-loop fires → `_apply_recovery_transition(reason_kind="tool_timeout")` → **compose + push steering naming the tool** → requeue to `pending` → worker re-picks → `pop_steering_messages()` replaces turn input → model skips the tool, answers degraded → response delivered to user (not `failed`).

### Technical Approach

- **Inject only on the requeue branch.** Do NOT push steering when the transition resolves to `failed` (recovery_attempts ≥ MAX or subprocess not confirmed dead) or `abandoned` (local session) — a steering message on a terminal record is dead weight. The cleanest place is inside the `else:` requeue branch at `session_health.py:1468`, immediately before `transition_status(entry, "pending", …)`, guarded by `if reason_kind == "tool_timeout" and tool_name:`.
- **Capture the tool name early.** Read `tool_name = getattr(entry, "current_tool_name", None)` at the top of the function (or just before the branch) into a local so a concurrent PostToolUse-driven clear cannot null it between read and push.
- **Compose a self-contained steering message** because the container has no `--resume` and the steering text replaces the turn input. Include: (a) the timed-out tool name, (b) a "do not retry it" instruction, (c) the original user request (`entry.message_text`, truncated to a safe length) so the model has something to answer, (d) an instruction to note what was unavailable. Example:
  > `The tool {tool_name} timed out twice and is temporarily unavailable — do not call it again this turn. Answer the user's original request as best you can without it, and note which information was unavailable. Original request: {original_request}`
- **First-attempt-only is acceptable but inject on every tool_timeout requeue.** Since `MAX_RECOVERY_ATTEMPTS=2`, the requeue branch only fires on attempt 1 (attempt 2 goes to `failed`). So a single injection per wedge is the natural outcome — no de-dup logic needed. Confirm by reading the branch order: `failed` (attempts ≥ 2) is checked before the `else` requeue.
- **Best-effort, never fatal.** Wrap the `push_steering_message` call so a steering-save failure logs at WARNING and does not block the requeue (matches the existing observability-counter pattern in this file).
- **Telemetry (optional, low-cost):** INCR `{project_key}:session-health:tool_timeout_steering_injected` so dashboards can confirm the path fires. Best-effort.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `push_steering_message` call is wrapped in try/except that logs at WARNING on failure (mirrors `recoveries:{kind}` counter pattern at `session_health.py:1170-1178`). Add a unit test asserting that a `push_steering_message` raising does NOT prevent the `transition_status(entry, "pending")` call.
- [ ] `_compose_tool_timeout_steering` must not raise on `None`/empty `message_text` — assert it returns a valid (if generic) string.

### Empty/Invalid Input Handling
- [ ] `_compose_tool_timeout_steering(tool_name=None, ...)` — guarded by the `and tool_name` injection condition; add a test that no steering is pushed when `current_tool_name` is None.
- [ ] `_compose_tool_timeout_steering(tool_name="x", original_request="")` — returns a string that still instructs the model to skip the tool (no crash, no empty turn input).
- [ ] Truncate `original_request` to a bounded length so a very long original message cannot bloat the steering queue entry.

### Error State Rendering
- [ ] The degraded response IS the user-visible output. Integration/log test (see Test Impact) asserts a simulated mcp-tier wedge produces a `pending` requeue carrying a steering message containing the tool name — the surrogate for "user gets a degraded answer rather than `failed`".
- [ ] Verify the steering message references the tool name and the original request (so the model has enough to answer) — assert both substrings present.

## Test Impact

- [ ] `tests/unit/test_session_health_tool_timeout.py::test_subloop_recovers_wedged_session_default_tier` — UPDATE: this test patches `_apply_recovery_transition` to a fake and asserts `reason_kind == "tool_timeout"`. It does NOT exercise the real transition, so it stays green. Add a sibling test that calls the **real** `_apply_recovery_transition` with `reason_kind="tool_timeout"` and a wedged-but-recoverable session (recovery_attempts=0, subprocess confirmed dead) and asserts a steering message was pushed naming the tool.
- [ ] `tests/unit/test_session_health_tool_timeout.py` — ADD: `test_tool_timeout_requeue_injects_steering_with_tool_name`, `test_tool_timeout_failed_branch_does_not_inject_steering` (attempts ≥ MAX → `failed`, no steering), `test_no_steering_when_current_tool_name_none`, `test_steering_push_failure_does_not_block_requeue`.
- [ ] `tests/unit/` (new or existing health test file) — ADD: pure unit tests for `_compose_tool_timeout_steering` (tool name present, empty original_request, None original_request, truncation).
- [ ] `tests/integration/test_session_health_tool_timeout.py` — ADD: an end-to-end-ish test that drives a real mcp-tier wedge through `_agent_session_tool_timeout_check` and asserts the requeued session carries a steering message (the no-`failed` outcome). REPLACE only if an existing test asserts "no steering on requeue"; none does today.

No existing assertions are invalidated — the change is additive to the requeue branch, and the existing tool-timeout tests patch the transition function rather than asserting its internals.

## Rabbit Holes

- **Active tool suppression via a `disabled_tools` SDK list.** Tempting (more reliable than advice), but requires SDK-client / container changes, a per-session disabled-tools field, and schema work. Out of scope — see Open Questions; the steering approach ships in Small appetite and degrades gracefully if ignored.
- **Distinguishing "transient hang" from "permanently broken MCP server".** Don't build retry/backoff intelligence per MCP server. One wedge → one skip-instruction. Anything smarter is a separate project.
- **Reworking the `MAX_RECOVERY_ATTEMPTS` budget.** The acceptance criteria explicitly forbid raising it. Do not touch.
- **Generalizing steering injection to `no_progress` / `worker_dead` recoveries.** Those reasons have no single offending tool to name; the steering text would be meaningless. Keep the injection `tool_timeout`-only.

## Risks

### Risk 1: Steering message replaces turn input but lacks original context
**Impact:** Because the container has no `--resume`, the steering string becomes the entire turn input. If it only says "skip tool X" without the original request, the model answers nothing useful.
**Mitigation:** `_compose_tool_timeout_steering` embeds `entry.message_text` (truncated) into the steering text. Test asserts the original-request substring is present.

### Risk 2: The model ignores the advisory and re-issues the tool call
**Impact:** Second wedge → `failed`. Same outcome as today (no regression), but the fix didn't help.
**Mitigation:** Accepted residual risk for v1 (steering is advisory). The steering text is imperative ("do not call it again this turn"). Active suppression (Open Question) is the follow-up if advisory proves insufficient in production telemetry (`tool_timeout_steering_injected` vs. subsequent `failed` rate).

### Risk 3: `current_tool_name` cleared between read and push
**Impact:** Steering message names no tool, or the `and tool_name` guard skips injection.
**Mitigation:** Capture `tool_name` into a local before any save. The sub-loop already re-reads a `fresh` entry just before calling `_apply_recovery_transition`, so `current_tool_name` is as fresh as possible. If it is genuinely None, skip injection silently (the requeue still happens).

## Race Conditions

### Race 1: PostToolUse clears tool state between recovery read and steering push
**Location:** `agent/session_health.py` — `_apply_recovery_transition` requeue branch (`:1468`), reading `entry.current_tool_name`.
**Trigger:** The hung MCP tool returns at the exact moment recovery runs; PostToolUse writes `current_tool_name=None`.
**Data prerequisite:** `tool_name` must be captured before the steering composition reads it.
**State prerequisite:** The session is still being requeued (not finalized by a concurrent transition).
**Mitigation:** Read `tool_name` into a local at function entry / just before the branch; guard injection on `if reason_kind == "tool_timeout" and tool_name`. The sub-loop's own re-read + `_check_tool_timeout(fresh)` race guard (`session_health.py:2230-2253`) already aborts recovery if PostToolUse fired before the transition, so by the time we reach the requeue branch the wedge was still live at re-read time.

### Race 2: Concurrent steering writers overflow the queue
**Location:** `models/agent_session.py:1918` `push_steering_message`, `STEERING_QUEUE_MAX=10`.
**Trigger:** Another process steers the same session simultaneously.
**Data prerequisite:** none.
**State prerequisite:** none.
**Mitigation:** `push_steering_message` already trims to the last `STEERING_QUEUE_MAX` and uses a partial save (`update_fields`). One extra message cannot break the invariant. No new locking needed.

## No-Gos (Out of Scope)

Active tool suppression (`disabled_tools` passed to the SDK/container) is the one
design alternative we are deliberately NOT building here, and it is captured as
**Open Question 1** rather than a No-Go promise: it is a genuine open decision for the
supervisor, not committed-but-deferred work. This plan ships the zero-schema advisory-
steering approach; suppression would require SDK/container changes and a new per-session
field, and is gated on production telemetry showing the advisory is insufficient. No
separate issue is filed because the decision itself is still open.

Everything else is in scope — the steering injection, the `_compose_tool_timeout_steering`
helper, the unit + integration tests, and the documentation updates are all part of this
plan. Nothing is deferred to a follow-up.

## Update System

No update system changes required — this feature is purely internal to the worker's session-health loop. No new dependencies, config files, CLI entry points, or cross-machine propagation. The worker restart that `/update` already performs picks up the code change.

## Agent Integration

No agent integration required — this is a worker-internal change. The agent (model) does not call any new tool; it *receives* a steering message at the next turn boundary via the existing `pop_steering_messages()` path in `session_executor.py`. No `.mcp.json` change, no new CLI entry point, no bridge import. The integration surface is the existing steering mechanism, already covered by `tests/integration/test_steering.py`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-steering.md` — add a subsection "Automatic steering on tool_timeout recovery" documenting that `_apply_recovery_transition` injects a skip-the-tool steering message on mcp/default-tier wedges.
- [ ] Update `docs/features/per-tool-timeout-tiers.md` (or `docs/plans/per_tool_timeout_tier_counters.md`'s shipped doc, if present) — cross-link to the graceful-degradation behavior so the #1270 mechanism doc mentions the steering follow-up.

### External Documentation Site
- [ ] No external docs site changes — internal feature.

### Inline Documentation
- [ ] Docstring on `_compose_tool_timeout_steering` explaining the self-contained-message requirement (no `--resume`).
- [ ] Comment at the injection point in `_apply_recovery_transition` explaining why injection is requeue-branch-only and `tool_timeout`-only.

## Success Criteria

- [ ] A session that wedges on `mcp__claude_ai_Notion__notion-fetch` (or any `mcp__`/default-tier tool) receives a steering message on re-queue naming the timed-out tool and the original request.
- [ ] The `tool_timeout` requeue branch injects steering; the `failed` and `abandoned` branches do NOT.
- [ ] `MAX_RECOVERY_ATTEMPTS` is unchanged (still 2).
- [ ] Unit test: `tool_timeout` requeue injects the correct steering message containing the tool name and original-request substring.
- [ ] Unit test: steering-push failure does not block the `pending` requeue.
- [ ] Integration/log test: a simulated mcp-tier wedge produces a `pending` requeue carrying the steering message (surrogate for non-`failed` outcome).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `_apply_recovery_transition` references `push_steering_message` and `_compose_tool_timeout_steering`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (steering-injection)**
  - Name: steering-builder
  - Role: Add `_compose_tool_timeout_steering` and the requeue-branch injection in `_apply_recovery_transition`; write unit + integration tests.
  - Agent Type: builder
  - Resume: true

- **Validator (steering-injection)**
  - Name: steering-validator
  - Role: Verify injection fires only on the `tool_timeout` requeue branch, never on `failed`/`abandoned`; verify failure-path coverage and that `MAX_RECOVERY_ATTEMPTS` is untouched.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: steering-doc
  - Role: Update `docs/features/session-steering.md` and cross-link the per-tool-timeout doc.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard roster — builder, validator, documentarian sufficient for this Small-appetite fix.)

## Step by Step Tasks

### 1. Implement steering composer + injection
- **Task ID**: build-steering-injection
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_tool_timeout.py, tests/integration/test_session_health_tool_timeout.py
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_compose_tool_timeout_steering(tool_name: str, original_request: str | None) -> str` to `agent/session_health.py` (pure, truncates original_request, never raises).
- In `_apply_recovery_transition`, capture `tool_name = getattr(entry, "current_tool_name", None)` before the transition branches.
- In the `else:` requeue branch (`session_health.py:~1468`), before `transition_status(entry, "pending", …)`, guard `if reason_kind == "tool_timeout" and tool_name:` and call `entry.push_steering_message(...)` inside try/except (WARNING on failure).
- Best-effort INCR `{project_key}:session-health:tool_timeout_steering_injected`.

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: build-steering-injection
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: false
- Unit: composer (tool name present, empty/None original_request, truncation).
- Unit: requeue injects steering with tool name + original-request substring; `failed` branch does not inject; None tool name → no injection; push failure → requeue still happens.
- Integration: simulated mcp-tier wedge → `pending` requeue carries steering message.

### 3. Validate
- **Task ID**: validate-steering
- **Depends On**: build-steering-injection, build-tests
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm injection is requeue-branch-only and `tool_timeout`-only.
- Confirm `MAX_RECOVERY_ATTEMPTS` unchanged.
- Run `pytest tests/unit/test_session_health_tool_timeout.py tests/integration/test_session_health_tool_timeout.py -q`.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-steering
- **Assigned To**: steering-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-steering.md` with the automatic-steering subsection.
- Cross-link the per-tool-timeout doc.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks below; confirm all success criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tool-timeout tests pass | `pytest tests/unit/test_session_health_tool_timeout.py tests/integration/test_session_health_tool_timeout.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Injection wired | `grep -n "push_steering_message" agent/session_health.py` | output contains push_steering_message |
| Composer wired | `grep -n "_compose_tool_timeout_steering" agent/session_health.py` | output > 1 |
| MAX unchanged | `grep -n "MAX_RECOVERY_ATTEMPTS = 2" agent/session_health.py` | output contains MAX_RECOVERY_ATTEMPTS = 2 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Steering (advisory) vs. active suppression (`disabled_tools`)?** This plan ships the steering approach: zero-schema, ships in Small appetite, degrades gracefully if ignored. Active suppression (passing a per-session disabled-tools list to the SDK/container) is more reliable but needs SDK/container changes and a new field. The issue itself flags this as the key open question. Recommendation: ship steering now, gate suppression on production telemetry (`tool_timeout_steering_injected` vs. subsequent `failed` rate). Confirm?
2. **Should the steering message embed the full original request or a truncated form?** Plan truncates to a bounded length to avoid bloating the steering queue entry (queue cap is 10, partial-save). Confirm a truncation ceiling (proposed: 1500 chars) is acceptable, or prefer a reference like "your previous message" instead of inlining.
