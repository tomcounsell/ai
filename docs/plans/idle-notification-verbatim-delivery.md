---
status: Cancelled
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-06
tracking: https://github.com/tomcounsell/ai/issues/1919
last_comment_id:
superseded_by: docs/plans/granite-pty-teardown.md
---

> **Cancelled 2026-07-06 — absorbed, not discarded.** This plan's Open Question 1 (build vs. cutover) was answered the same day: the granite+PTY teardown is approved and planned (`docs/plans/granite-pty-teardown.md`, #1924). The delivery-path halves of this fix (`container.py` `_await_turn_end` / `_needs_human_message`) are deleted with the substrate, but the root cause lives in `hook_edge.py`, which **graduates** into the new headless runner — so the content-aware Notification classification, the central boilerplate constant, and the turn_end-over-needs_human ordering are carried as an explicit bullet of the teardown's task 1 (build-graduate), and the implementation PR closes #1919. The analysis below remains the canonical description of the defect.

# Idle notification "Claude is waiting for your input" swallows the PM's real answer

## Problem

The CEO asked a substantive question (evaluate MiniCPM5 as a local-model replacement). The granite PM session answered it: the PM transcript ends with a complete, multi-paragraph `[/user]` reply. What arrived on Telegram was a single line — **"Claude is waiting for your input"** — Claude Code's own generic idle-notification text. The real answer was never delivered on any channel; the work product was silently discarded, and the human saw a confusing system burp.

**Current behavior:** two visible defects, one shared root cause.

1. **Verbatim boilerplate delivery.** `_needs_human_message` (`agent/granite_container/container.py:1647-1671`) returns `payload["message"]` verbatim for any non-empty string. Claude Code's idle Notification always carries the fixed text "Claude is waiting for your input", so it short-circuits the intended fallback chain (`last_assistant_text` → persona-safe generic).
2. **The real answer was swallowed.** The idle Notification is classified as a `needs_human` edge (`hook_edge.py:71,356-357`). Inside `_await_turn_end` (`container.py:1332-1354`), a single `consumer.poll()` drains BOTH the `Stop`/`turn_end` edge (which carries the `[/user]` transcript) and the later idle `Notification`, but `needs_human` is checked **before** `turn_end` and returns immediately (`container.py:1335-1344`). The `turn_end` branch (`1345-1354`) is never reached, so `classify_pm_prefix` never runs on the PM's `[/user]` text. `_deliver_needs_human` ships the boilerplate and sets `user_facing_routed=True` + `exit_reason=pm_user`, which also suppresses the wrap-up guard (`container.py:2430-2431`). The messenger heartbeat's `communicated` never flips because the normal reply path never ran. This is why the incident showed `communicated=False` for the full 6-minute run.

**Root cause (single):** the idle Notification — which Claude Code fires after *every* response, not only when input is genuinely needed — is treated as a `needs_human` edge. That spurious edge both (a) preempts the completed turn's delivery and (b) supplies the boilerplate string that leaks. Fixing the classification fixes both defects.

**Desired outcome:** the human receives the PM's actual `[/user]` answer in persona voice. Claude Code boilerplate strings never appear in an outbound chat message under any fallback path.

## Freshness Check

**Baseline commit:** worktree HEAD `0a3a8162`; actual `main` is ahead at `d451c1bd` (checked at plan time)
**Issue filed at:** 2026-07-06T07:03:17Z
**Disposition:** Major drift / Overlap — **a strategic decision to delete the entire granite+PTY subsystem is on the table** (see below). The bug and its code path are unchanged and the fix is technically correct, but whether to build it depends on a human call. This plan is a **draft pending that decision** (Open Question 1), not READY TO BUILD.

**⚠️ Overlap with an in-flight strategic decision (primary finding):**
`main` HEAD `d451c1bd` (committed 2026-07-06 14:09, ~7h *after* this issue was filed) adds `docs/postmortems/2026-07-06-granite-pty-fragility.md`, a postmortem that **argues for removing `agent/granite_container/` entirely** (9,392 LOC, incl. the 725-LOC BYOB OAuth robot and ~30 PTY timing knobs) and running PM/Dev/teammate/eng via `claude -p` headless. Trigger: SDLC #1915/#1916 failed twice with zero work because the interactive TUI never reached idle during prime. The postmortem is a **proposal/case, not an executed removal** — `agent/granite_container/container.py` and `hook_edge.py` are unchanged and still run bridge sessions in production. But if the cutover is accepted, this entire fix is thrown away with the directory. Neighboring commit `c7a3085d` walks back the "headless is metered" claim (billing parity today), removing one obstacle to the cutover.

**Decision impact:**
- If granite+PTY **stays** (no cutover, or cutover is weeks out): this fix is correct and valuable — the bug silently discards CEO answers today. Build it.
- If granite+PTY is **deleted soon**: close #1919 as obsolete; the `claude -p` headless path has its own delivery routing and this needs_human/idle-Notification code ceases to exist. Do not build.
- This is a genuine human decision (strategy + timing), surfaced as Open Question 1 below.

**File:line references re-verified:**
- `agent/granite_container/container.py:1647-1671` (`_needs_human_message`) — still returns `payload["message"]` verbatim ahead of the `last_assistant_text` fallback. Holds.
- `agent/granite_container/hook_edge.py:71` (`_NEEDS_HUMAN_EVENTS = {"Notification", "PermissionRequest"}`) and `:356-357` (`_classify` maps every `Notification` to `needs_human` with no content inspection). Holds.
- `agent/granite_container/container.py:1332-1354` (`_await_turn_end`) — `needs_human` checked before `turn_end` within one poll batch. Confirmed by trace; this is the swallowed-answer mechanism.
- Steady-state site `container.py:2340-2348` and prime-turn site `container.py:2195-2197` — both take the `needs_human` branch before the transcript-classify path. Hold.

**Cited sibling issues/PRs re-checked:**
- #1688 / PR #1847 (merged 2026-07-02) — built the hook-driven turn returns and the `Notification`/`PermissionRequest`→needs-human mapping this bug lives in. Still the current implementation.
- #1877, #1744, #1546, #1612 — related lifecycle/architecture work; none reverts or supersedes the classification under change.

**Commits on main since issue was filed (touching referenced files):**
- `0a3a8162` "Fix granite PTY idle detection blindness on claude CLI 2.1.201 (#1918)" — touched `pty_driver.py`, `config/models.py`, docs, and PTY tests only. Did **not** touch `container.py` or `hook_edge.py`. Irrelevant to this fix.

**Active plans in `docs/plans/` overlapping this area:** none in `docs/plans/`, but see the `docs/postmortems/2026-07-06-granite-pty-fragility.md` overlap above — it is the dominant overlap signal.

**Notes:** Bug still reproduces by inspection: the classification + poll-ordering that swallowed the answer is unchanged on both `0a3a8162` and `main` `d451c1bd`. No line-number drift. The only material change since filing is the strategic postmortem, which does not touch code but may obsolete this entire work item.

## Prior Art

- **PR #1847 (#1688)**: "hook-driven turn returns — deterministic turn-end + needs-human edges." Introduced `_NEEDS_HUMAN_EVENTS`, `_classify`, and `_deliver_needs_human`. It correctly made `PermissionRequest` and `PreToolUse(AskUserQuestion)` needs-human edges, but bundled `Notification` in the same set without distinguishing the idle-prompt notification (fires after every response) from a genuine input request. That bundling is the defect. This plan does not revert #1688; it refines the `Notification` branch.
- **#1877**: added persona-aware interrupt copy ("I was interrupted and will resume automatically") for a neighboring path — precedent for persona-safe fallback strings, reused here.
- **#1744**: granite messageless-session silent success — same family of "session ends without delivering its work"; different mechanism (no transcript) so no code overlap.

No closed issue previously attempted this specific fix.

## Research

**Queries used:**
- Claude Code Notification hook "waiting for your input" message field permission

**Key findings:**
- Claude Code's `Notification` hook `idle_prompt` fires **after every response**, not only when input is genuinely required — matching the incident (an idle Notification arrived right after the PM's turn settled). Source: [github.com/anthropics/claude-code/issues/12048](https://github.com/anthropics/claude-code/issues/12048), [github.com/anthropics/claude-code/issues/13024](https://github.com/anthropics/claude-code/issues/13024).
- The `Notification` `message` field is generic/often empty for the idle case ("Claude is waiting for your input") and distinct phrasing for permission ("Claude needs your permission to use ..."). There is a `permission_prompt` vs `idle_prompt` matcher on the settings side, but the *payload* itself is keyed on the `message` text. Source: [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks).
- Implication for the fix: `_classify` **can** inspect the payload — the forwarder (`hook_forwarder.py:56-66`) writes the verbatim payload including `message` — and the correct discriminator is the message text, not a separate matcher field. This directly shapes the classification design below (idle-boilerplate/empty message → liveness-only; substantive message → needs_human).

## Data Flow

1. **Entry point:** Claude Code TUI (granite PM PTY) finishes a turn → fires the `Stop` hook → `hook_forwarder.py` appends a `turn_end` envelope (with `transcript_path`) to the per-session edge file. ~Immediately after, the TUI's `idle_prompt` fires the `Notification` hook → forwarder appends a `Notification` envelope carrying `message="Claude is waiting for your input"`.
2. **`HookEdgeConsumer.poll()`** (`hook_edge.py:410-504`) drains **both** appended lines in one batch and runs `_classify` on each: `Stop`→`turn_end`, `Notification`→`needs_human` (current, buggy).
3. **`_await_turn_end`** (`container.py:1332-1354`) checks `_first_new_needs_human(edges)` **first**; finds the Notification's needs_human edge → returns `saw_turn=False, needs_human=<edge>` and never inspects the `turn_end` edge.
4. **Caller** (`container.py:2340-2348` steady-state; `2195-2197` prime) takes the `needs_human` branch → `_deliver_needs_human` → `_needs_human_message` returns `payload["message"]` verbatim → the boilerplate string is sent via `_on_user_payload`; `user_facing_routed=True`, `exit_reason=pm_user`. The wrap-up guard (`2430-2431`) is suppressed.
5. **Output:** Telegram receives "Claude is waiting for your input". The PM's `[/user]` transcript answer is discarded; `classify_pm_prefix` never runs on it.

**Post-fix flow:** step 2 classifies the idle Notification as **liveness-only** (no edge). Step 3 finds no needs_human, reaches the `turn_end` branch, returns `saw_turn=True` with `transcript_path`. Step 4 reads `last_assistant_text` → `classify_pm_prefix` → `[/user]` → `_route_pm_classification` delivers the real answer; `communicated` flips True.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #1847 (#1688) | Added `Notification` to `_NEEDS_HUMAN_EVENTS` alongside `PermissionRequest` | Did not distinguish Claude Code's `idle_prompt` Notification (fires after every response) from a genuine input request. The idle Notification became a spurious needs-human edge that races and preempts the real turn-end delivery. Root cause was correctly located at the edge layer, but the `Notification` event was mapped too coarsely. |

**Root cause pattern:** a coarse event→edge mapping treated a high-frequency liveness ping as a rare human-input signal. The fix narrows the mapping by inspecting message content, and adds a boilerplate filter at the extraction point as defense-in-depth.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `_classify(event, payload)` already receives `payload`; no signature change. A new shared constant (known Claude Code boilerplate) is added and imported by both `hook_edge.py` (classification) and `container.py` (`_needs_human_message`). One definition, two consumers — satisfies the issue's "one definition of known boilerplate, conservative and centralized" constraint.
- **Coupling:** unchanged. The boilerplate constant is a small, self-contained module-level definition in `hook_edge.py` (the edge layer already owns event semantics).
- **Data ownership:** unchanged.
- **Reversibility:** trivial — revert the classification branch and the extraction filter.

## Appetite

**Size:** Small

**Team:** Solo dev, PM check-in on the classification decision

**Interactions:**
- PM check-ins: 1 (confirm the classification decision: idle Notification is liveness-only)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work has no external dependencies (no new services, keys, or infra). It is a pure logic fix in two existing modules plus tests and docs.

## Solution

### Key Elements

- **Central boilerplate definition** (`hook_edge.py`): one place that names Claude Code's known non-informative Notification strings — the exact idle string `"Claude is waiting for your input"` and the permission-phrasing prefix `"Claude needs your permission to use "`. Both `_classify` and `_needs_human_message` consume it. Match conservatively (exact string / stable prefix), never a broad substring.
- **Content-aware `_classify`** (`hook_edge.py`): `Notification` no longer maps unconditionally to `needs_human`. Remove `"Notification"` from `_NEEDS_HUMAN_EVENTS`; add a dedicated branch — a Notification whose `message` is the idle boilerplate (or empty/whitespace-only) classifies as **liveness-only** (`_classify` returns `None`, no edge emitted). A Notification with a substantive, non-boilerplate message still classifies as `needs_human` (defensive: preserves any genuine input-request Notification). `PermissionRequest` and `PreToolUse(AskUserQuestion)` remain `needs_human`, unchanged.
- **Boilerplate filter at extraction** (`container.py::_needs_human_message`): defense-in-depth. Even for a genuine `needs_human` edge (e.g. a permission Notification/`PermissionRequest`), if `payload["message"]` matches known boilerplate, skip it and fall through to `last_assistant_text`, then to the persona-safe generic prompt. No raw Claude Code string can reach the human under any path.
- **Persona-safe fallback fix** (`container.py`): the current generic fallback `"This needs your input to continue — please reply."` contains an em-dash (a vanilla-LLM tell forbidden for published text). Replace with `"This needs your input to continue. Please reply."`

### Flow

PM finishes turn → `Stop` edge (turn_end, carries `[/user]` transcript) + idle `Notification` both drained in one poll → idle Notification classifies as liveness-only (no edge) → `_await_turn_end` returns `saw_turn=True` → `last_assistant_text` → `classify_pm_prefix` → `[/user]` → delivered to Telegram in persona voice.

### Technical Approach

- **Classification decision (documented):** an idle Notification is a **liveness signal, not a needs-human edge**. Rationale: in this architecture the PM signals "I need the human" by emitting a `[/user]` turn (delivered via the turn-end path) or, mid-turn, via `AskUserQuestion`/`PermissionRequest` — all of which have dedicated edges. Claude Code's `idle_prompt` Notification fires after every response and is redundant/harmful as a needs-human trigger. Keep the door open for a genuinely substantive Notification message (rare) by still routing non-boilerplate Notifications to needs_human.
- **Keep `Notification` registered** in `generate_hook_settings` (`hook_edge.py:164`). The forwarder keeps writing Notification envelopes; `_classify` decides per-message. This keeps blast radius minimal and preserves the ability to act on a future substantive Notification. (The existing `test_registers_all_target_hooks_to_forwarder` stays green.)
- **Single-site coverage of both callers:** both the steady-state (`container.py:2340`) and prime-turn (`2195`) needs_human branches flow through `_await_turn_end`; once the idle Notification stops producing a needs_human edge, neither branch fires spuriously — no change needed at the call sites themselves.
- **Do NOT sanitize downstream** in the message drafter — that masks the symptom while still discarding the answer (issue's Dropped item).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_needs_human_message` already wraps the transcript read in `try/except Exception: pass` (`container.py:1664-1670`). Preserve it; add a test that when `last_assistant_text` raises, the function still returns the persona-safe generic (not boilerplate, not a crash).
- [ ] No new `except Exception: pass` blocks are introduced.

### Empty/Invalid Input Handling
- [ ] `_classify` with a `Notification` payload that has no `message` key, an empty string, or whitespace-only → liveness-only (returns `None`). Add explicit tests for each.
- [ ] `_needs_human_message` with an empty/whitespace payload message and no transcript → returns the persona-safe generic prompt. Add a test.
- [ ] Verify the boilerplate match is exact/prefix, not substring: a legitimate message that merely *contains* the word "input" (e.g. a real question) must still route as needs_human. Add a test asserting a non-boilerplate message survives.

### Error State Rendering
- [ ] The user-visible failure path (needs_human delivery) is tested to render the PM's answer or the persona-safe generic — never the raw Claude Code boilerplate. Covered by the integration test below.
- [ ] Assert the persona-safe fallback contains no em-dash (`—`).

## Test Impact

- [ ] `tests/unit/granite_container/test_hook_edge.py::test_notification_is_needs_human` (line 184) — REPLACE: rename to `test_idle_notification_is_liveness_only`; a `Notification` with no message (or the idle boilerplate message) must yield **no** needs_human edge (`poll()` returns `[]`). Add a sibling `test_substantive_notification_is_needs_human` asserting a Notification with a non-boilerplate `message` still classifies as `needs_human`.
- [ ] `tests/unit/granite_container/test_hook_edge.py::test_registers_all_target_hooks_to_forwarder` (line 47) — UNCHANGED: `Notification` stays registered; assertion still holds (verify, do not edit).
- [ ] `tests/unit/granite_container/test_hook_edge.py::test_permission_request_is_needs_human` (line 188) and `test_ask_user_question_pretooluse_is_needs_human` (line 192) — UNCHANGED: these edges remain needs_human.
- [ ] `tests/unit/granite_container/test_container_hook_turn.py::test_needs_human_routes_to_user` (line 105) — UNCHANGED: it constructs a `NEEDS_HUMAN` edge directly (bypassing `_classify`) and still validates genuine needs_human routing. Verify it stays green.
- [ ] The `_envelope_line` helper (`test_hook_edge.py:42`) already forwards `**payload`, so `_envelope_line("Notification", message="...")` works with no helper change.

New tests to add (greenfield, no impact on existing):
- `test_hook_edge.py`: the classification cases enumerated above.
- `test_container_hook_turn.py` (or a new `test_container_needs_human_message.py`): unit tests over `_needs_human_message` — (a) idle-Notification payload with a transcript → returns `last_assistant_text`, not boilerplate (acceptance #1); (b) idle payload without transcript → persona-safe generic; (c) permission-boilerplate payload → falls through; (d) fallback string has no em-dash.
- Integration-style test over the container edge flow: feed a real `HookEdgeConsumer` a batch of `[Stop(turn_end, transcript with [/user] answer), Notification(idle)]`; drive delivery; assert the routed message is the PM's `[/user]` answer, not the boilerplate (acceptance #2).

## Rabbit Holes

- **Rewriting `_await_turn_end` to timestamp-order `turn_end` vs `needs_human`.** Tempting after seeing the poll-ordering, but once the idle Notification stops producing a needs_human edge, the observed race cannot recur. A general ts-ordering rewrite risks *dropping* a genuine mid-turn `needs_human` (permission/AskUserQuestion) that shares a poll batch with a Stop, because `poll()` advances the cursor past unconsumed edges. Deferred; see No-Gos.
- **Disambiguating `exit_reason=pm_user`** (it currently means either a real `[/user]` delivery or a needs_human boilerplate delivery). A nice observability cleanup, but not required to fix the bug and not in the acceptance criteria. Deferred; see No-Gos.
- **Parsing the settings-side `permission_prompt`/`idle_prompt` matcher.** The matcher lives in the settings config, not the payload; the payload discriminator is the message text. Chasing a matcher field in the payload is a dead end — key on `message`.
- **Scrubbing outbound text in the message drafter.** Explicitly dropped in the issue: masks the symptom and still loses the answer.

## Risks

### Risk 1: A genuine, substantive Notification is misread as boilerplate and dropped from needs_human
**Impact:** a real input request delivered via Notification (rather than PermissionRequest/AskUserQuestion) is silently downgraded to liveness-only.
**Mitigation:** match **only** the exact idle string and the stable permission prefix; any other non-empty message still routes as needs_human. A test asserts a non-boilerplate message survives. In granite's bypass-permissions posture, permission Notifications are largely suppressed anyway, so the practical exposure is near-zero.

### Risk 2: Boilerplate string drifts in a future Claude Code version
**Impact:** the exact-match filter stops matching a reworded idle string, and boilerplate could leak again.
**Mitigation:** the constant is centralized and named, with a `# provisional — Claude Code idle-notification text, verify on CLI bump` comment marking it tunable. The `record_hook_fallback` counter and the persona rule mean any leak is a visible regression caught by the integration test on the next run. Documented in the hook-edge reference so a CLI bump review checks it.

## Race Conditions

### Race 1: `turn_end` and idle `Notification` drained in one poll batch
**Location:** `agent/granite_container/container.py:1332-1354` (`_await_turn_end`), `hook_edge.py:410-504` (`poll()`).
**Trigger:** the PM turn settles; `Stop` (turn_end) and the `idle_prompt` `Notification` are both appended to the edge file and drained in the same `poll()`.
**Data prerequisite:** the `turn_end` edge's `transcript_path` must point at the transcript containing the PM's `[/user]` answer (it does — written by the Stop hook before the Notification fires).
**State prerequisite:** the idle Notification must NOT be surfaced as a `needs_human` edge, so `_first_new_needs_human` returns `None` and control reaches the `turn_end` branch.
**Mitigation:** the classification fix removes the idle Notification from the needs_human set entirely, so within any poll batch only the `turn_end` edge is actionable. No ordering/locking change to `_await_turn_end` is required (and a broad ts-ordering rewrite is deliberately out of scope — see Rabbit Holes).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1922] Timestamp-ordering `turn_end` vs `needs_human` inside `_await_turn_end`, and disambiguating the overloaded `exit_reason=pm_user` (real `[/user]` vs needs_human boilerplate). Both are robustness/observability improvements the classification fix makes non-urgent; they carry their own edge cases (cursor-advance edge loss) and belong in a dedicated change. Filed as #1922.

## Update System

No update system changes required — this is a pure internal logic fix in `agent/granite_container/`. No new dependencies, config files, migrations, or Popoto model changes. `scripts/update/run.py` and `migrations.py` are untouched. The change ships with the normal git pull + worker restart that `/update` already performs; the running worker picks up the new classification on its next session spawn.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. The fix lives entirely inside the granite PTY container's edge-classification and delivery path (`agent/granite_container/`), which the worker already drives for bridge-originated sessions. No new CLI entry point in `pyproject.toml [project.scripts]`, no MCP server or `.mcp.json` change, and `bridge/telegram_bridge.py` needs no new imports. The existing bridge → worker → `Container` → `_on_user_payload` → `send_cb` delivery path is unchanged; only *what text* it delivers changes. The integration-style test over the container edge flow is the agent-reachability check.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/omnigent-hook-edge-reference.md`: document the classification decision — idle `Notification` (`idle_prompt`) is a liveness signal, not a `needs_human` edge; `PermissionRequest`, `PreToolUse(AskUserQuestion)`, and substantive-message Notifications remain needs-human. Describe only the resulting behavior (no historical narration).
- [ ] Update `docs/features/granite-pty-production.md`: note that idle notifications no longer route as `[/user]` deliveries and that `_needs_human_message` filters known Claude Code boilerplate, falling through to `last_assistant_text` then a persona-safe generic.

### External Documentation Site
- [ ] Not applicable — this repo has no Sphinx/MkDocs site for these internals.

### Inline Documentation
- [ ] Docstring on the new boilerplate constant: what it matches, why it is provisional, and that a Claude Code CLI bump should re-verify it.
- [ ] Update the `_classify` and `_needs_human_message` docstrings to describe the idle-Notification handling and the boilerplate fallback.

## Success Criteria

- [ ] A `needs_human` edge whose payload message is Claude Code boilerplate (idle notification / permission phrasing) never produces that string in an outbound message; unit test over `_needs_human_message` with a real idle-Notification payload asserts fallback to `last_assistant_text`. (Acceptance #1)
- [ ] For the evidence scenario (PM produced a `[/user]` answer, idle Notification fired), the delivered message is the PM's answer text; integration test over the container edge flow. (Acceptance #2)
- [ ] The swallowed-answer mechanism is root-caused (idle Notification classified as needs_human preempts the turn-end delivery) and **fixed** in this plan — not split; the follow-up #1922 covers only the optional ordering/observability hardening. (Acceptance #3)
- [ ] The classification decision (idle Notification = liveness-only) is documented in `docs/features/omnigent-hook-edge-reference.md` and the granite PTY doc; no historical narration. (Acceptance #4)
- [ ] The persona-safe fallback string contains no em-dash.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (edge-classification)**
  - Name: `edge-builder`
  - Role: Implement the central boilerplate constant, content-aware `_classify`, `_needs_human_message` filter, and the persona-safe fallback fix.
  - Agent Type: builder
  - Domain: async/concurrency (granite PTY edge flow)
  - Resume: true

- **Test engineer (edge-tests)**
  - Name: `edge-tester`
  - Role: Author the unit + integration-style tests enumerated in Test Impact.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (hook-edge-docs)**
  - Name: `edge-doc`
  - Role: Update the two feature docs and inline docstrings.
  - Agent Type: documentarian
  - Resume: true

- **Validator (edge-validate)**
  - Name: `edge-validator`
  - Role: Verify all success criteria and run the Verification checks.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Central boilerplate + classification fix
- **Task ID**: build-classification
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_hook_edge.py
- **Assigned To**: edge-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a centralized, named constant in `agent/granite_container/hook_edge.py` for known Claude Code boilerplate: exact idle string `"Claude is waiting for your input"` and permission prefix `"Claude needs your permission to use "`. Mark it provisional (grain-of-salt comment; re-verify on CLI bump).
- Remove `"Notification"` from `_NEEDS_HUMAN_EVENTS`. Add a `Notification` branch in `_classify`: idle boilerplate OR empty/whitespace `message` → return `None` (liveness-only); substantive non-boilerplate `message` → return `NEEDS_HUMAN`.
- Keep `Notification` registered in `generate_hook_settings`. Update the `_classify` docstring.

### 2. Extraction filter + persona-safe fallback
- **Task ID**: build-extraction
- **Depends On**: build-classification
- **Validates**: tests/unit/granite_container/test_container_hook_turn.py (or new test file)
- **Assigned To**: edge-builder
- **Agent Type**: builder
- **Parallel**: false
- In `container.py::_needs_human_message`, import the shared boilerplate constant; when `payload["message"]` matches known boilerplate, skip it and fall through to `last_assistant_text`, then the generic prompt.
- Replace the em-dash fallback `"This needs your input to continue — please reply."` with `"This needs your input to continue. Please reply."`
- Update the `_needs_human_message` docstring.

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-extraction
- **Validates**: tests/unit/granite_container/test_hook_edge.py, tests/unit/granite_container/test_container_hook_turn.py
- **Assigned To**: edge-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- REPLACE `test_notification_is_needs_human` per Test Impact; add liveness-only + substantive-Notification cases; add empty/whitespace-message cases.
- Add `_needs_human_message` unit tests (idle-with-transcript → last_assistant_text; idle-without-transcript → generic; permission-boilerplate → fallthrough; no-em-dash assertion; transcript-read-raises → generic).
- Add the integration-style edge-flow test (Stop+idle Notification batch → delivered message is the `[/user]` answer).

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: edge-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/omnigent-hook-edge-reference.md` and `docs/features/granite-pty-production.md` per the Documentation section (behavior-only, no history).

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: edge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table commands; confirm all success criteria; report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Hook-edge tests pass | `pytest tests/unit/granite_container/test_hook_edge.py -q` | exit code 0 |
| Container hook-turn tests pass | `pytest tests/unit/granite_container/test_container_hook_turn.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/granite_container/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/granite_container/` | exit code 0 |
| Notification no longer unconditional needs_human | `grep -n 'frozenset({"Notification"' agent/granite_container/hook_edge.py` | exit code 1 |
| No em-dash in persona fallback | `grep -n 'to continue —' agent/granite_container/container.py` | exit code 1 |
| Boilerplate constant is centralized | `grep -c 'Claude is waiting for your input' agent/granite_container/hook_edge.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **⚠️ BUILD-OR-DROP: is granite+PTY being deleted?** (blocking — see Freshness Check.) `main` HEAD adds a postmortem arguing to delete `agent/granite_container/` entirely in favor of `claude -p` headless. This fix lives inside that directory. If the cutover is accepted and near-term, close #1919 as obsolete and do not build. If granite+PTY stays (or the cutover is weeks out), build this fix — the bug silently discards CEO answers today. This is a strategy + timing call only a human can make; the rest of the plan is ready to execute the moment the answer is "build."
2. **Classification decision confirmation.** The plan decides an idle Notification is **liveness-only** (not needs_human), keeping only substantive-message Notifications, `PermissionRequest`, and `AskUserQuestion` as needs-human edges. Confirm this is the desired behavior versus keeping the idle Notification as a pure liveness ping fed to the badge (the plan drops it as an edge entirely; the PTY badge already handles liveness independently).
3. **Follow-up scope (#1922).** The ts-ordering + `exit_reason=pm_user` disambiguation is filed as #1922. Confirm that is acceptable, or say if either should be pulled into this Small-appetite fix.
