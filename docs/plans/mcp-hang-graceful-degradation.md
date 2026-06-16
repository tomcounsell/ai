---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-16
tracking: https://github.com/tomcounsell/ai/issues/1711
last_comment_id:
revision_applied: true
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

**Desired outcome (two layers):**

1. **Advisory steering (attempt-1 requeue).** On the first `tool_timeout` recovery, before
   re-queuing, **prepend** a steering message (to the FRONT of the steering queue) naming the
   timed-out tool and instructing the model to skip it and degrade gracefully. The session
   delivers a degraded-but-real response ("I couldn't reach Notion; here's what I can answer
   without it") on the re-pickup turn.
2. **Deterministic floor (terminal/second wedge).** Advisory steering may be ignored — the
   model can re-issue the same tool call and wedge again. On the **terminal** `tool_timeout`
   recovery (recovery_attempts ≥ MAX → would otherwise finalize `failed`), the worker delivers
   a **canned, user-facing degraded message** to the originating chat ("I couldn't complete
   that because the {tool} service didn't respond — try again shortly") via the Telegram outbox
   **before** finalizing `failed`. The user is never left with silent failure.

This two-layer design is the lesson of prior art PR #892 (see Prior Art): advisory-only
steering was shipped once before and required a deterministic last-resort gate to actually
guarantee a user-visible outcome.

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
- **Session steering (`docs/features/session-steering.md`)**: `push_steering_message()` / `pop_steering_messages()` already exist and are exercised by `tests/integration/test_steering.py`. The worker pops steering at the turn boundary (`session_executor.py:1522`). The steering queue is **FIFO** — `push_steering_message` *appends* (`models/agent_session.py:1927`) and the executor consumes `steering_msgs[0]` (`agent/session_executor.py:1524`), re-queuing the remainder at the back. A plain append is therefore **not** sufficient for tool-skip injection (see Risk 4 / Solution): if any steering message is already queued, an append would run that older message first and let the model re-call the hung tool. This plan adds a **prepend** path.
- **PR #892 — "Summarizer fallback: agent self-summary via session steering"** (`docs/plans/summarizer-fallback-steering.md`, merged 2026-04-10, closed #891). This is the **direct precedent and load-bearing prior art**. PR #892 ran the *same* experiment this plan proposes — inject advisory steering asking the agent to degrade gracefully when an upstream path fails. Its own implementation concluded advisory steering alone was insufficient and added a **deterministic last-resort gate** (`is_narration_only()` wired as the final guard "when steering is unavailable", per the PR body) so a user-visible outcome is guaranteed even when steering is ignored or unavailable. **Lesson adopted here:** advisory steering ships PLUS a deterministic floor (the canned outbox delivery on the terminal wedge — see Solution / B2). We do NOT repeat the advisory-only mistake.
- No prior closed issue attempted to inject steering on `tool_timeout` recovery specifically. This is the first fix of this specific defect, but #892 already proved out the advisory-plus-deterministic-floor shape.

## Research

No relevant external findings — this is a purely internal change to the worker's session-health recovery path and the steering subsystem. No external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

1. **Entry point**: PreToolUse hook fires when the model invokes `mcp__claude_ai_Notion__notion-fetch`, writing `current_tool_name` and `last_tool_use_at` on the `AgentSession`.
2. **Wedge**: PostToolUse never returns (upstream MCP hang). After 120 s, `_agent_session_tool_timeout_check` (`session_health.py:~2194`) re-reads the session, `_check_tool_timeout(fresh)` returns `(tier="mcp", reason=...)`.
3. **Counter + recovery**: the check bumps `tool_timeout_count_mcp`, INCRs `…:tool_timeouts:mcp`, then calls `_apply_recovery_transition(fresh, reason_kind="tool_timeout", handle, worker_key)` at `session_health.py:2284`.
4. **Recovery transition (attempt 1 → requeue)**: `_apply_recovery_transition` cancels the task, confirms the subprocess is dead, and — in the `else` requeue branch (`:1468`) — sets `status="pending"`, `started_at=None`. **(NEW injection point A)**: before/at this branch, when `reason_kind == "tool_timeout"`, **prepend** a steering message naming `current_tool_name` to the FRONT of the queue (NOT a plain append — see B1 / Risk 4). This guarantees the tool-skip instruction is the `steering_msgs[0]` the executor consumes on the very next turn.
5. **Re-pickup**: the worker picks the pending session back up. At the turn boundary (`session_executor.py:1522`), `pop_steering_messages()` returns the queue with the tool-skip message first; `_turn_input = steering_msgs[0]` **replaces** the original message text.
6. **Output**: `build_harness_turn_input(_turn_input, …)` wraps the steering text with context headers and sends it to the brand-new TUI container run (no `--resume`). The model reads "skip tool X, answer without it", produces a degraded response, and delivers it to the user.
7. **Terminal wedge (attempt 2 → would be `failed`)**: if the model ignored the advisory and the tool wedged again, recovery_attempts reaches MAX and the transition resolves to the `failed` branch (`:1424`). **(NEW injection point B — deterministic floor)**: before `finalize_session(entry, "failed", …)`, write a canned user-facing degraded message to `telegram:outbox:{entry.session_id}` via `TelegramRelayOutputHandler` (or its underlying outbox-write helper). The bridge relay (`bridge/telegram_relay.py`) delivers it to the originating chat. Only then does the session finalize `failed`. The user gets a real reply, not silence.

**Load-bearing fact**: the container has no `claude --resume` wiring (`session_executor.py:1653-1659`) — every run is a fresh TUI session. The steering message becomes the *entire* turn input, so it must be **self-contained**: it must reference the original request, not merely say "skip the tool". The original request is preserved on the session as `entry.message_text` and must be woven into the steering text.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: one small additive method on `AgentSession` — `push_steering_message(text, front: bool = False)` gains a `front` keyword (default `False` preserves all existing append callers). When `front=True` the message is inserted at index 0 so the executor's `steering_msgs[0]` consumption picks it up first. The overflow trim still applies (`current[-STEERING_QUEUE_MAX:]`), but trimming keeps the front entry because it drops the OLDEST (tail-relative) — note: with a front insert the trim must keep index 0 and drop from the *append* end; the implementation trims to `current[:STEERING_QUEUE_MAX]` when `front=True` so the just-prepended message is never the one dropped.
- **Coupling**: `_apply_recovery_transition` gains a read of `entry.current_tool_name` and `entry.message_text` (both already on the model), a `push_steering_message(..., front=True)` call, and (on the terminal branch) an outbox write via `TelegramRelayOutputHandler`. `session_health.py` already imports `AgentSession`; the output handler import is local to the new block.
- **Data ownership**: unchanged. The steering queue is owned by `AgentSession`; the outbox key is owned by the relay contract (`telegram:outbox:{session_id}`).
- **Reversibility**: trivial — both injections are single guarded blocks; remove them to revert. The `front=` keyword defaults to the prior append behavior.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0 (design committed — advisory steering + deterministic floor; suppression is out of scope)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Runs entirely against the worker's in-process session-health loop and Redis-backed `AgentSession`.

## Solution

### Key Elements

- **Prepend-capable steering** (`push_steering_message(text, front=False)`): add a `front` keyword to the existing `AgentSession.push_steering_message`. `front=True` inserts at index 0 and trims `current[:STEERING_QUEUE_MAX]` (keeps the prepended message, drops the oldest *appended* tail). Default `front=False` is the existing append behavior — no existing caller changes. **This is the B1 fix**: the executor consumes `steering_msgs[0]`, so the tool-skip instruction MUST be at the front to be the next turn's input; a plain append would let any already-queued message run first and re-call the hung tool.
- **Steering composer** (`_compose_tool_timeout_steering`): a small pure helper in `agent/session_health.py` that takes the tool name and the original request text and returns a self-contained steering string. Pure and unit-testable.
- **Advisory injection in the requeue path**: in `_apply_recovery_transition`, when `reason_kind == "tool_timeout"` AND the transition is taking the **requeue (`pending`) branch** (not `failed`/`abandoned`), call `entry.push_steering_message(_compose_tool_timeout_steering(...), front=True)` before `transition_status(entry, "pending", …)`.
- **Deterministic floor on the terminal branch (B2)**: a helper `_deliver_tool_timeout_degraded_notice(entry, tool_name)` that, on the `failed` branch (`recovery_attempts >= MAX_RECOVERY_ATTEMPTS` with `reason_kind == "tool_timeout"`), writes a canned user-facing degraded message to `telegram:outbox:{entry.session_id}` via `TelegramRelayOutputHandler` **before** `finalize_session(entry, "failed", …)`. Guarded so a delivery failure logs WARNING and still lets the finalize proceed. Skipped if `response_delivered_at` is already set (no double-delivery, matches the #918 guard).
- **Tool-name capture**: read `current_tool_name` from `entry` at recovery time (it is not cleared on requeue, but capture it into a local before any save to be safe against concurrent clears). Used by both the advisory composer and the deterministic notice.

### Flow

**Attempt 1 (advisory):** MCP tool hangs → tool-timeout sub-loop fires → `_apply_recovery_transition(reason_kind="tool_timeout")` → **compose + PREPEND steering naming the tool (`front=True`)** → requeue to `pending` → worker re-picks → `pop_steering_messages()[0]` is the tool-skip message → model skips the tool, answers degraded → response delivered to user.

**Attempt 2 (deterministic floor):** model ignored the advisory, tool wedges again → recovery_attempts hits MAX → `failed` branch → **write canned degraded notice to the outbox (user sees it)** → `finalize_session("failed")`. The user always gets a reply; `failed` is now a clean terminal state, not a silent one.

### Technical Approach

- **PREPEND, do not append (B1).** The executor consumes `steering_msgs[0]` (`agent/session_executor.py:1524`) and re-queues the rest at the back. `push_steering_message` appends today (`models/agent_session.py:1927`). If any steering message is already queued when the tool wedges, a plain append would let that older message run first while the tool-skip waits — and on a fresh-TUI re-pickup the model would just re-call the hung tool. The fix adds `front: bool = False` to `push_steering_message`; the advisory injection calls it with `front=True` so the tool-skip instruction is index 0. Overflow trim for `front=True` keeps the prepended entry (`current[:STEERING_QUEUE_MAX]`).
- **Advisory injection only on the requeue branch.** Do NOT push steering when the transition resolves to `failed` or `abandoned` — a steering message on a terminal record is dead weight (the deterministic floor handles `failed`). The cleanest place is inside the `else:` requeue branch at `session_health.py:1468`, immediately before `transition_status(entry, "pending", …)`, guarded by `if reason_kind == "tool_timeout" and tool_name:`.
- **Deterministic floor on the `failed` branch (B2).** In the `elif entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:` branch (`session_health.py:1424`), when `reason_kind == "tool_timeout"`, call `_deliver_tool_timeout_degraded_notice(entry, tool_name)` BEFORE `finalize_session(entry, "failed", …)`. This writes a canned user-facing message ("I couldn't finish that — the {tool} service didn't respond after two tries. Please try again shortly; everything else is working.") to `telegram:outbox:{entry.session_id}` via `TelegramRelayOutputHandler`, which the relay delivers. Guard with the existing `response_delivered_at is None` check so we never double-send. This is the prior-art lesson from PR #892: advisory steering needs a deterministic backstop.
- **Capture the tool name early.** Read `tool_name = getattr(entry, "current_tool_name", None)` just before the transition branches into a local so a concurrent PostToolUse-driven clear cannot null it between read and use. If `tool_name` is None, the floor still delivers a generic notice (no `{tool}` substitution); the advisory injection is skipped (the `and tool_name` guard).
- **Compose a self-contained advisory steering message** because the container has no `--resume` and the steering text replaces the turn input. Include: (a) the timed-out tool name, (b) a "do not retry it" instruction, (c) the original user request (`entry.message_text`, **inlined truncated to 1500 chars** — committed decision, see Risk 1) so the model has something to answer, (d) an instruction to note what was unavailable. Example:
  > `The tool {tool_name} timed out and is temporarily unavailable — do not call it again this turn. Answer the user's original request as best you can without it, and note which information was unavailable. Original request: {original_request_truncated_1500}`
- **One advisory injection per wedge, naturally.** Since `MAX_RECOVERY_ATTEMPTS=2`, the requeue branch only fires on attempt 1 (attempt 2 goes to `failed` → deterministic floor). A single advisory injection per wedge is the natural outcome — no de-dup logic needed. Confirm by reading the branch order: `failed` (attempts ≥ 2) is checked before the `else` requeue.
- **Best-effort, never fatal.** Wrap both the `push_steering_message(..., front=True)` call and the outbox write so a failure logs at WARNING and does not block the requeue or the finalize (matches the existing observability-counter pattern in this file).
- **Telemetry (optional, low-cost):** INCR `{project_key}:session-health:tool_timeout_steering_injected` on advisory injection and `{project_key}:session-health:tool_timeout_degraded_delivered` on deterministic floor delivery so dashboards can confirm both paths fire. Best-effort.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The advisory `push_steering_message(..., front=True)` call is wrapped in try/except that logs at WARNING on failure (mirrors `recoveries:{kind}` counter pattern at `session_health.py:1170-1178`). Add a unit test asserting that a `push_steering_message` raising does NOT prevent the `transition_status(entry, "pending")` call.
- [ ] The deterministic-floor outbox write is wrapped in try/except that logs WARNING on failure and still calls `finalize_session(entry, "failed")`. Add a unit test asserting an outbox-write failure does NOT prevent finalize.
- [ ] `_compose_tool_timeout_steering` must not raise on `None`/empty `message_text` — assert it returns a valid (if generic) string.

### Empty/Invalid Input Handling
- [ ] `_compose_tool_timeout_steering(tool_name=None, ...)` — guarded by the `and tool_name` injection condition; add a test that no advisory steering is pushed when `current_tool_name` is None.
- [ ] `_compose_tool_timeout_steering(tool_name="x", original_request="")` — returns a string that still instructs the model to skip the tool (no crash, no empty turn input).
- [ ] Inline `original_request` truncated to **1500 chars** (committed) so a very long original message cannot bloat the steering queue entry; assert a 5000-char input is truncated to ≤1500 in the composed string.
- [ ] Deterministic floor with `tool_name=None` — `_deliver_tool_timeout_degraded_notice` still produces a generic (no `{tool}`) notice and does not raise.

### Prepend Ordering (B1)
- [ ] With a pre-existing steering message already queued (e.g. `["older instruction"]`), the advisory injection prepends so `queued_steering_messages[0]` is the tool-skip message and the older one is at index 1. Assert ordering explicitly — this is the regression guard for the FIFO bug.
- [ ] `push_steering_message(text, front=True)` overflow: queue already at `STEERING_QUEUE_MAX`, prepend one more → the prepended message survives the trim (it is `[0]`) and the oldest *appended* tail is dropped.

### Error State Rendering / Delivered Outcome (B3)
- [ ] **Advisory path:** Integration/log test asserts a simulated mcp-tier wedge produces a `pending` requeue carrying a steering message at index 0 containing the tool name AND the original-request substring — the surrogate for "model has what it needs to answer degraded".
- [ ] **Deterministic floor (delivered, not just queued):** A test drives a session to the terminal `tool_timeout` `failed` branch and asserts a payload was actually written to `telegram:outbox:{session_id}` containing a coherent user-facing degraded notice (tool name when known, an apology, and a "try again shortly" instruction) BEFORE `finalize_session("failed")` ran. This validates that a real degraded answer is **delivered to the user** — not merely that a steering message was queued.

## Test Impact

- [ ] `tests/unit/test_session_health_tool_timeout.py::test_subloop_recovers_wedged_session_default_tier` — UPDATE: this test patches `_apply_recovery_transition` to a fake and asserts `reason_kind == "tool_timeout"`. It does NOT exercise the real transition, so it stays green. Add a sibling test that calls the **real** `_apply_recovery_transition` with `reason_kind="tool_timeout"` and a wedged-but-recoverable session (recovery_attempts=0, subprocess confirmed dead) and asserts a steering message was prepended naming the tool.
- [ ] `tests/unit/test_session_health_tool_timeout.py` — ADD (advisory path): `test_tool_timeout_requeue_prepends_steering_with_tool_name`, `test_tool_timeout_prepend_when_queue_already_has_message` (B1 ordering: tool-skip is index 0, pre-existing message at index 1), `test_no_steering_when_current_tool_name_none`, `test_steering_push_failure_does_not_block_requeue`.
- [ ] `tests/unit/test_session_health_tool_timeout.py` — ADD (deterministic floor / B2): `test_tool_timeout_failed_branch_delivers_degraded_notice` (attempts ≥ MAX → outbox payload written before `finalize_session("failed")`), `test_failed_branch_does_not_inject_advisory_steering` (no steering on the terminal branch), `test_degraded_notice_skipped_when_response_already_delivered`, `test_outbox_write_failure_does_not_block_finalize`, `test_degraded_notice_generic_when_tool_name_none`.
- [ ] `tests/unit/test_agent_session.py` (or the model's steering test file) — ADD: `test_push_steering_message_front_inserts_at_head`, `test_push_steering_message_front_overflow_keeps_prepended` (B1 — the model-level prepend method).
- [ ] `tests/unit/` (new or existing health test file) — ADD: pure unit tests for `_compose_tool_timeout_steering` (tool name present, empty original_request, None original_request, truncation to 1500 chars).
- [ ] `tests/integration/test_session_health_tool_timeout.py` — ADD: (a) an end-to-end-ish test that drives a real mcp-tier wedge through `_agent_session_tool_timeout_check` and asserts the requeued session carries a prepended steering message; (b) a test driving the session to the terminal `failed` branch and asserting a degraded notice was written to `telegram:outbox:{session_id}` (delivered-outcome surrogate, B3). REPLACE only if an existing test asserts "no steering on requeue"; none does today.

No existing assertions are invalidated — the advisory change is additive to the requeue branch, the `front=` keyword defaults to the prior append behavior (existing callers unaffected), and the existing tool-timeout tests patch the transition function rather than asserting its internals.

## Rabbit Holes

- **Active tool suppression via a `disabled_tools` SDK list.** Tempting (more reliable than advice), but requires SDK-client / container changes, a per-session disabled-tools field, and schema work. Explicitly out of scope for this plan (see Scope / No-Gos); the advisory-steering-plus-deterministic-floor approach ships in Small appetite and guarantees a user-visible outcome regardless of whether the advice is honored.
- **Distinguishing "transient hang" from "permanently broken MCP server".** Don't build retry/backoff intelligence per MCP server. One wedge → one skip-instruction. Anything smarter is a separate project.
- **Reworking the `MAX_RECOVERY_ATTEMPTS` budget.** The acceptance criteria explicitly forbid raising it. Do not touch.
- **Generalizing steering injection to `no_progress` / `worker_dead` recoveries.** Those reasons have no single offending tool to name; the steering text would be meaningless. Keep the injection `tool_timeout`-only.

## Risks

### Risk 1: Steering message replaces turn input but lacks original context
**Impact:** Because the container has no `--resume`, the steering string becomes the entire turn input. If it only says "skip tool X" without the original request, the model answers nothing useful.
**Mitigation:** `_compose_tool_timeout_steering` **inlines** `entry.message_text` truncated to **1500 chars** (committed decision — Q2) into the steering text. A reference like "your previous message" is NOT used, because the fresh-TUI run has no `--resume` and would have nothing to dereference. Test asserts the original-request substring is present and that a 5000-char input is truncated to ≤1500.

### Risk 2: The model ignores the advisory and re-issues the tool call
**Impact:** Second wedge → terminal recovery. Without a backstop the user would be left with silent failure (the exact #1711 defect).
**Mitigation:** **The deterministic floor (B2) eliminates the silent-failure outcome.** On the terminal `failed` branch the worker delivers a canned user-facing degraded notice to the outbox before finalizing — so even when the model ignores the advisory, the user gets a real reply. The advisory text is also imperative ("do not call it again this turn") to maximize the chance the model honors it on attempt 1. Telemetry (`tool_timeout_steering_injected` vs. `tool_timeout_degraded_delivered`) measures how often the advisory is honored vs. the floor fires; active SDK `disabled_tools` suppression remains explicitly out of scope (see Scope / No-Gos) and would only be revisited if telemetry shows the floor firing frequently.

### Risk 3: `current_tool_name` cleared between read and push
**Impact:** Steering message names no tool, or the `and tool_name` guard skips injection.
**Mitigation:** Capture `tool_name` into a local before any save. The sub-loop already re-reads a `fresh` entry just before calling `_apply_recovery_transition`, so `current_tool_name` is as fresh as possible. If it is genuinely None, skip advisory injection silently (the requeue still happens); the deterministic floor still delivers a generic notice.

### Risk 4: FIFO steering queue runs an older message before the tool-skip (B1)
**Impact:** The steering queue is FIFO — `push_steering_message` appends (`models/agent_session.py:1927`) and the executor consumes `steering_msgs[0]` (`agent/session_executor.py:1524`), re-queuing the rest at the back. If a steering message was already queued when the tool wedged, a plain append would run that older message first on the re-pickup turn; the tool-skip instruction would wait, and on a fresh-TUI run the model would simply re-call the hung tool — defeating the fix.
**Mitigation:** Inject via the new `push_steering_message(..., front=True)` PREPEND path so the tool-skip instruction is `steering_msgs[0]` and is consumed first. Overflow trim for `front=True` keeps the prepended entry. A dedicated unit test (`test_tool_timeout_prepend_when_queue_already_has_message`) asserts the ordering as a regression guard.

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

**Scope statement.** This plan's scope is the two-layer graceful-degradation fix for the
`tool_timeout` recovery path: (1) advisory prepend-steering on the attempt-1 requeue, and
(2) the deterministic outbox-delivered degraded notice on the terminal `failed` branch.
Both layers are fully built, tested, and documented within this plan — nothing in scope is
left undone.

**Active SDK tool suppression (`disabled_tools`) is architecturally out of scope.** Passing
a per-session disabled-tools list to the SDK/container so the hung tool is *suppressed at the
harness level* (rather than advised against) is a genuinely separate piece of work: it
requires SDK-client and granite-container changes plus a new per-session schema field, which
is a different architectural surface than the in-process session-health recovery path this
plan touches. It is **not** a smaller-but-skipped part of this fix — it is a different
mechanism. The advisory-plus-deterministic-floor design here already guarantees a user-visible
outcome (the floor delivers regardless of whether the advisory is honored), so suppression is
not required to close #1711. We are not filing a tracking promise for it; if production
telemetry (`tool_timeout_degraded_delivered`) later shows the floor firing often enough to
justify harness-level suppression, that becomes its own independently-scoped issue at that
time, decided on its own merits.

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

- [ ] **(B1)** A session that wedges on `mcp__claude_ai_Notion__notion-fetch` (or any `mcp__`/default-tier tool) receives a steering message **prepended** (at index 0) on re-queue naming the timed-out tool and the inlined original request — even when another steering message is already queued.
- [ ] **(B1)** `push_steering_message(text, front=True)` inserts at the head and the prepended message survives overflow trim; default `front=False` callers are unchanged.
- [ ] The advisory `tool_timeout` requeue branch injects steering; the `failed` and `abandoned` branches do NOT inject advisory steering.
- [ ] **(B2)** The terminal `tool_timeout` `failed` branch delivers a canned degraded notice to `telegram:outbox:{session_id}` BEFORE `finalize_session("failed")`, guarded by `response_delivered_at is None`. An outbox-write failure does not block finalize.
- [ ] **(B3 — delivered, not just queued)** A test confirms that on the terminal wedge a coherent user-facing degraded answer (apology + tool name when known + "try again shortly") is actually written to the user's outbox — validating the user receives a real reply, not merely that a steering message was queued.
- [ ] `MAX_RECOVERY_ATTEMPTS` is unchanged (still 2).
- [ ] **(Q2)** Unit test: the advisory steering message inlines the original request truncated to 1500 chars and contains the tool name.
- [ ] Unit test: steering-push failure does not block the `pending` requeue.
- [ ] Integration test: a simulated mcp-tier wedge produces a `pending` requeue carrying the prepended steering message (advisory path), and a terminal wedge writes the degraded notice to the outbox (deterministic-floor path).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `_apply_recovery_transition` references `push_steering_message`, `_compose_tool_timeout_steering`, and `_deliver_tool_timeout_degraded_notice`.

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
- Add `front: bool = False` keyword to `AgentSession.push_steering_message` (`models/agent_session.py`): when `front`, insert at index 0 and trim `current[:STEERING_QUEUE_MAX]`; default path unchanged.
- Add `_compose_tool_timeout_steering(tool_name: str, original_request: str | None) -> str` to `agent/session_health.py` (pure, inlines original_request truncated to 1500 chars, never raises).
- Add `_deliver_tool_timeout_degraded_notice(entry, tool_name: str | None) -> None` to `agent/session_health.py`: writes a canned degraded message to `telegram:outbox:{entry.session_id}` via `TelegramRelayOutputHandler`, best-effort (WARNING on failure), skipped if `response_delivered_at` is set.
- In `_apply_recovery_transition`, capture `tool_name = getattr(entry, "current_tool_name", None)` before the transition branches.
- In the `else:` requeue branch (`session_health.py:~1468`), before `transition_status(entry, "pending", …)`, guard `if reason_kind == "tool_timeout" and tool_name:` and call `entry.push_steering_message(..., front=True)` inside try/except (WARNING on failure).
- In the `elif entry.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:` branch (`session_health.py:~1424`), when `reason_kind == "tool_timeout"`, call `_deliver_tool_timeout_degraded_notice(entry, tool_name)` BEFORE `finalize_session(entry, "failed", …)`.
- Best-effort INCR `{project_key}:session-health:tool_timeout_steering_injected` (advisory) and `…:tool_timeout_degraded_delivered` (floor).

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: build-steering-injection
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: false
- Unit: composer (tool name present, empty/None original_request, truncation to 1500 chars).
- Unit: `push_steering_message(front=True)` prepends at head + survives overflow trim.
- Unit (advisory): requeue PREPENDS steering with tool name + original-request substring; ordering correct when a message is already queued (B1); `failed` branch does not inject advisory; None tool name → no advisory injection; push failure → requeue still happens.
- Unit (deterministic floor / B2): terminal `failed` branch writes degraded notice to outbox before finalize; skipped when `response_delivered_at` set; generic notice when tool name None; outbox-write failure does not block finalize.
- Integration: simulated mcp-tier wedge → `pending` requeue carries prepended steering message; terminal wedge → degraded notice written to `telegram:outbox:{session_id}` (B3 delivered-outcome).

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
| Advisory injection wired | `grep -n "push_steering_message" agent/session_health.py` | output contains push_steering_message with front=True |
| Composer wired | `grep -n "_compose_tool_timeout_steering" agent/session_health.py` | output > 1 |
| Deterministic floor wired | `grep -n "_deliver_tool_timeout_degraded_notice" agent/session_health.py` | output > 1 |
| Prepend method wired | `grep -n "front" models/agent_session.py` | push_steering_message has front kwarg |
| MAX unchanged | `grep -n "MAX_RECOVERY_ATTEMPTS = 2" agent/session_health.py` | output contains MAX_RECOVERY_ATTEMPTS = 2 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | Critique (B1) | FIFO queue runs an older steering message before tool-skip | Prepend path (`push_steering_message(front=True)`) | Solution / Risk 4 / B1 tests |
| Blocker | Critique (B2) | Advisory-only repeats PR #892's mistake; no deterministic floor | Deterministic outbox notice on terminal `failed` branch | Prior Art (#892), Solution, B2 tests |
| Blocker | Critique (B3) | Success criteria only assert plumbing, not a delivered answer | Delivered-outcome criterion + outbox-write test | Success Criteria / Failure Path B3 |
| Blocker | Critique (B4) | Open Question 2 contradicted committed inlining decision | Committed to inlining at 1500 chars; OQ removed | Solution / Risk 1 / Q2 |

---

## Open Questions

None — all prior open questions resolved during the revision pass:
- **Advisory vs. active suppression:** committed to advisory steering PLUS a deterministic floor (the canned outbox delivery). Active SDK `disabled_tools` suppression is explicitly out of scope (see No-Gos) — a separate architectural surface, not required to close #1711.
- **Inline vs. reference the original request:** committed to **inlining the original request truncated to 1500 chars** (see Solution / Risk 1).
