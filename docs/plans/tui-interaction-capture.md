---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-23
tracking: https://github.com/tomcounsell/ai/issues/1540
last_comment_id:
revision_applied: true
---

# TUI Interaction Capture — Human-in-the-Loop Patterns as Learnable Reference Data

## Problem

How a human drives Claude Code in a terminal — steering mid-run, slash-command
sequences, approving or rejecting tool calls — is operating knowledge the
autonomous eng/granite sessions could learn to emulate, but today it evaporates
the moment a session ends. The v1 telemetry recorder (`agent/session_telemetry.py`,
shipped in #1699) captures *machine* events (turn boundaries, token usage, tool
durations) but not the *human decisions*: the steering message a human typed at
turn 4, the `/do-test` they ran before `/do-pr-review`, the Edit they rejected.

This is Pillar 3 of epic #1536 — the most exploratory pillar. Its gate ("file
now, plan after pillars 1-2 prove the recording/learning substrate") is now
satisfied: the V1 per-event recorder, the stall classifier (#1536 pillar 1), and
the crash-signature auto-resume (pillar 2) all shipped.

**Current behavior:**
Human TUI interaction signal is invisible. The `Stop` hook runs Haiku extraction
over the transcript and saves a handful of observations, but it is tuned for
*content* learnings (corrections/decisions/patterns/surprises about the codebase),
not *interaction-shape* learnings (when/how a human intervened in a session).
There is no record that says "human steered with a redirect at turn 4 of a
10-turn session" or "human ran `/do-plan` → `/do-build` → `/do-test` in sequence."

**Desired outcome:**
Human-in-the-loop TUI interaction patterns are captured during local Claude Code
sessions and stored as retrievable subconscious-memory observations, so the
eng/granite personas can draw on them via the existing `memory_search` /
`memory_get` MCP recall path. **Capture-and-store only** — no auto-emulation
behavior in this pillar.

## Freshness Check

**Baseline commit:** `9af25e3814a933b22e4ecdae60d9a5866994fb96`
**Issue filed at:** 2026-06-01T08:16:13Z
**Disposition:** Unchanged (substrate confirmed present and matches the cutover context)

**File:line references re-verified:**
- `agent/session_telemetry.py` — V1 per-event JSONL recorder with
  `record_telemetry_event(session_id, event)` + `read_session_timeline()`.
  Confirmed present (351 lines, shipped #1699). Fail-silent, per-session lock,
  10k-event cap, append-only JSONL at `logs/session_telemetry/{id}.jsonl`.
- `.claude/hooks/hook_utils/memory_bridge.py` — `ingest()`, `prefetch()`,
  `recall()`, `extract()` all present; `extract()` already calls
  `extract_observations_async()` over the transcript at `Stop`. Confirmed (1032
  lines). This is the existing CLI-session → memory path.
- `.claude/hooks/user_prompt_submit.py` — fires on every human prompt; already
  calls `memory_bridge.ingest()` + `prefetch()`. This is the natural capture
  point for **steering messages** and **slash-command starts**.
- `.claude/hooks/post_tool_use.py` — fires after every tool; already tracks
  SDLC bash-command state. Natural capture point for **tool sequences**.
- `.claude/hooks/stop.py` — runs post-session extraction. Natural place to
  summarize the captured interaction trace into observations.
- `models/memory.py` — `Memory.safe_save(...)` with `metadata` DictField
  (`category`, `tags`, etc.) and `source` field (`SOURCE_HUMAN`/`SOURCE_AGENT`).
  Confirmed present.

**Cited sibling issues/PRs re-checked:**
- Epic #1536 — open; pillars 1 (stall classifier) and 2 (crash signature)
  shipped per cutover context. This pillar's gate is satisfied.
- PR #1699 — merged; shipped the V1 telemetry recorder this plan reuses.

**Commits on main since issue was filed (touching referenced files):**
- `415e0e10 feat(#1536): session telemetry recorder v1` — *partially the substrate
  this plan builds on*; provides the JSONL recorder we extend with interaction
  event types.

**Active plans in `docs/plans/` overlapping this area:** `delete-observer-telemetry.md`
(unrelated — that removes a legacy observer telemetry path, not session telemetry).
No overlap with this work.

**Notes:** Post-#1633 cutover confirmed in code: `models/agent_session.py` declares
`session_type` ∈ {`eng`, `teammate`, `granite`} — no more `pm`/`dev`. Plan language
uses eng/granite throughout.

## Prior Art

- **PR #1699 / Issue #1536 pillar (V1 telemetry)**: shipped the append-only
  per-session JSONL recorder. Succeeded. This plan reuses it as the capture
  substrate rather than building a new one — we add two new event *types*
  (`slash_command`, `human_steering`) to the same recorder.
- **Subconscious memory (`docs/features/subconscious-memory.md`, multiple PRs)**:
  the established path for turning session signal into retrievable observations
  via `Memory.safe_save` + BM25/RRF recall. Succeeded and in active use. This
  plan emits its captured patterns into that exact path.
- **Claude Code memory bridge (`docs/features/claude-code-memory.md`)**: already
  extends memory ingest/recall/extract to CLI sessions through hooks. This plan
  hangs the interaction capture off two of the same hooks (`UserPromptSubmit`,
  `Stop`) — no new hook wiring, no new daemon.

No prior failed attempts to capture TUI *interaction shape* — this is greenfield
within the established substrate.

## Research

No relevant external findings needed — proceeding with codebase context. This is
entirely internal: Claude Code hook payloads, the in-repo telemetry recorder, and
the in-repo memory substrate. No external libraries, APIs, or ecosystem patterns
are involved.

## Spike Results

### spike-1: Can hooks observe steering / slash-command / tool-decision signal?
- **Assumption**: "The three existing hooks carry enough payload to capture the
  four target signals (steering, slash sequences, approval/rejection, interrupt timing)."
- **Method**: code-read of `user_prompt_submit.py`, `post_tool_use.py`, `stop.py`,
  and `agent/session_telemetry.py`.
- **Finding**:
  - **Steering messages + slash-command starts**: `UserPromptSubmit` receives the
    raw `prompt`. A prompt beginning with `/` is a slash-command invocation; a
    non-first prompt arriving while a session is mid-flight is a steering message.
    Both are observable. ✅
  - **Slash-command *sequences***: each `/x` start is one `slash_command` event;
    the ordered JSONL gives the sequence for free at read time. ✅
  - **Tool approval/rejection**: Claude Code's `PostToolUse` fires only on
    *executed* tools (approvals). Rejections are **not** delivered to `PostToolUse`.
    Partial signal — we capture the approval stream and, where the payload exposes
    a `permission`/`decision` field, the decision; explicit rejection capture is a
    **known gap** (see Risks + No-Gos). ⚠️
  - **Interrupt timing**: the recorder already emits synthetic `idle_gap` events
    (>60s between events). True ESC-interrupts are not a distinct hook event, but
    idle-gap + the next human prompt approximates "human paused the run and
    redirected." Approximate signal. ⚠️
- **Confidence**: high (steering/slash), medium (decision/interrupt — approximate).
- **Impact on plan**: Scope the four signals as: steering ✅ full, slash sequences
  ✅ full, tool decisions ⚠️ approvals + best-effort decision field, interrupt
  timing ⚠️ idle-gap-derived. No new hook event source is invented.

### spike-2: Reuse the V1 recorder or a distinct path?
- **Assumption**: "The V1 `record_telemetry_event` recorder can carry interaction
  events without modification."
- **Method**: code-read of `agent/session_telemetry.py`.
- **Finding**: `record_telemetry_event(session_id, event)` accepts an **arbitrary
  JSON-serialisable dict** keyed by `type`. Unknown types are preserved verbatim
  (the `_normalize_event` path). New event types (`slash_command`, `human_steering`)
  require **zero recorder changes** — they ride the existing
  append-only JSONL trace. `read_session_timeline()` returns them in order.
- **Confidence**: high.
- **Impact on plan**: Reuse the recorder as-is. The only recorder-adjacent change
  is documenting the two new event types in the schema docstring (additive).

## Data Flow

1. **Entry point — human types a prompt in the Claude Code TUI**:
   - A prompt (steering message or `/slash-command`) → `UserPromptSubmit` hook.
   - (Tool approvals are already recorded as `tool_use` events by the V1 recorder
     elsewhere — no new entry point needed.)
2. **Capture (hook → recorder)**: a thin new module
   `agent/tui_interaction_capture.py` classifies the prompt into an interaction
   event dict (deriving the ordinal from the existing timeline) and calls
   `record_telemetry_event(session_id, event)`. Fail-silent; never blocks the hook.
3. **Storage (recorder → JSONL)**: events append to
   `logs/session_telemetry/{session_id}.jsonl` interleaved with V1 machine events.
4. **Summarize (Stop hook → memory)**: at `Stop`, a new function reads the
   session timeline, extracts the interaction *shape* (ordered slash sequence,
   steering count + turn positions, approval/decision tallies, idle-gap interrupts),
   and emits **one compact observation Memory** per session via `Memory.safe_save`
   with `category="pattern"`, `source=SOURCE_HUMAN`, and `metadata.tags=["tui-interaction"]`.
5. **Output (recall)**: the observation is now retrievable through the standard
   BM25/RRF recall path and the `memory_search` / `memory_get` MCP tools the
   eng/granite personas already use.

## Architectural Impact

- **New dependencies**: none. Pure-internal Python; reuses the recorder and the
  Memory model.
- **Interface changes**: additive only — two new telemetry event `type` values
  (documented, not enforced) and one new module
  `agent/tui_interaction_capture.py` with two public functions:
  `capture_prompt_event(session_id, prompt, cwd)` and
  `summarize_and_store(session_id, project_key)`.
- **Coupling**: low. Hooks already import `memory_bridge`; this adds an import of
  the new capture module. The capture module imports the recorder and the Memory
  model — both already imported elsewhere in the same hooks.
- **Data ownership**: the recorder still owns the JSONL trace; the Memory model
  still owns persisted observations. No new store, no new ownership boundary.
- **Reversibility**: high. Remove the two hook call-sites + the module + the
  doc; the recorder and memory substrate are untouched.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (scope alignment — exploratory pillar, signal-vs-noise calls)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Redis (for Memory) and
the local hook environment are already required by the running system.

## Solution

### Key Elements

- **`agent/tui_interaction_capture.py` (new)**: the single home for interaction
  capture logic. Classifies hook payloads into interaction events and, at session
  end, distills the trace into one retrievable observation. Fail-silent throughout.
- **Interaction event types (additive to the recorder)** — two new types:
  - `slash_command` — a prompt starting with `/`; records the command name.
  - `human_steering` — a substantive non-slash prompt arriving after the first
    turn (a mid-run redirect); records the timeline-derived ordinal + a truncated snippet.
  - **Tool approvals** are NOT captured as a new event. The V1 recorder already
    emits `tool_use` events for every executed tool; `summarize_and_store` reads
    those at session end to tally approvals. No `tool_decision` event and no
    `PostToolUse` call-site — the decision/permission field is unconfirmed in the
    current payload and rejections are No-Go'd, so a dedicated capture there adds
    near-zero signal (critique concern C1). This keeps capture to **two**
    fail-silent call-sites: `UserPromptSubmit` and `Stop`.
- **Reuse of `idle_gap`**: interrupt timing is read from the V1 recorder's
  existing synthetic `idle_gap` events at summarize time — no new event.
- **Summarize-and-store at `Stop`**: reads the timeline, composes a compact
  natural-language pattern observation (e.g. *"In a 9-turn eng session, human ran
  /do-plan → /do-build → /do-test, steered once at turn 4 (redirect), approved 12
  tools, 1 idle-gap interrupt of 90s"*), and saves it as a `pattern` Memory tagged
  `tui-interaction`.

### Flow

Human types `/do-test` in TUI → `UserPromptSubmit` hook → `capture_prompt_event`
classifies as `slash_command` → `record_telemetry_event` appends to JSONL →
... session continues, more events accrue ... → human stops → `Stop` hook →
`summarize_and_store` reads timeline → composes one pattern observation →
`Memory.safe_save` → observation now recallable via `memory_search`.

### Technical Approach

- **Hang capture off two existing hooks** — no new hook registration in
  `settings.json`, no daemon. `user_prompt_submit.py` gains a fail-silent call to
  `capture_prompt_event`; `stop.py` gains a fail-silent call to `summarize_and_store`.
  Tool approvals are read from the recorder's existing `tool_use` events at
  summarize time — `post_tool_use.py` is NOT touched (critique C1).
- **Reuse `record_telemetry_event` verbatim** (spike-2): new event types ride the
  existing recorder. The only recorder file change is documenting the two new
  types in the module docstring's event-schema list (additive comment).
- **Signal-vs-noise gating** (the exploratory crux): apply the same triviality
  filter `memory_bridge` already uses (`TRIVIAL_PATTERNS`, `MIN_PROMPT_LENGTH`) so
  "yes"/"ok"/"continue" steering noise is dropped. Slash commands are always
  signal. Approvals are aggregated (counts), not stored per-tool, to keep the
  observation compact and recall-friendly.
- **One observation per session, not per event** — the persisted Memory is a
  single distilled pattern string, keeping recall noise low and respecting the
  WriteFilter importance gate. Use `metadata.category="pattern"` (importance 1.0
  baseline per `CATEGORY_IMPORTANCE`) so these don't crowd out corrections/decisions.
- **Dedup boundary vs. the existing Stop Haiku extraction** (critique C2): the
  existing `_run_memory_extraction` writes *content* observations (what the
  session learned about the code). This writes one *interaction-shape* observation
  tagged `tui-interaction` with `agent_id=f"tui-{session_id}"`. The distinct
  `agent_id` namespace + tag keeps the two streams separable; the
  `strip_private`'d structural string is unlikely to bigram-collide with Haiku's
  content observations. The existing "memory-dedup" consolidation reflection
  handles any residual near-duplicates across sessions.
- **Local Claude Code TUI sessions are the capture surface** (answering the
  issue's open question): the `UserPromptSubmit`/`Stop` hooks fire for local CLI
  sessions via the established `memory_bridge` path. Bridge-driven eng/granite sessions are
  explicitly *out of scope* for this pillar (no human-in-the-TUI there).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every public function in `agent/tui_interaction_capture.py` wraps its body
  in `try/except Exception` and logs at WARNING/DEBUG (matching recorder + bridge
  policy). Add a unit test that forces `record_telemetry_event` to raise and
  asserts `capture_prompt_event` swallows it and the hook proceeds.
- [ ] Add a unit test that forces `Memory.safe_save` to raise inside
  `summarize_and_store` and asserts the `Stop` path is unaffected.

### Empty/Invalid Input Handling
- [ ] Test `capture_prompt_event` with empty/None/whitespace prompt → no event written.
- [ ] Test `summarize_and_store` with an empty/absent timeline → no Memory saved,
  no exception.
- [ ] Test trivial-prompt gating: a `"ok"` steering message produces no
  `human_steering` event.

### Error State Rendering
- [ ] No user-visible output surface (this is silent background capture). Assert
  that capture failures never print to the TUI / never appear in hook stdout
  (hooks already `|| true`-guard). Test that a capture exception does not emit a
  `hookSpecificOutput` payload.

## Test Impact

- [ ] `tests/unit/test_session_telemetry.py` — UPDATE: add cases asserting the
  recorder accepts the two new event types verbatim (round-trips through
  `read_session_timeline`). No existing assertions change (additive).
- [ ] No existing hook tests assert on the *absence* of these new calls, so no
  hook test breaks. New tests are added under
  `tests/unit/test_tui_interaction_capture.py` (create).

No other existing tests affected — the changes are purely additive (new module,
new event types, three fail-silent hook call-sites that no current test asserts against).

## Rabbit Holes

- **Building a new capture daemon / new store.** Avoid — spike-2 proved the V1
  recorder carries arbitrary event types. Reuse it.
- **Capturing every keystroke / full transcript of steering.** Avoid — store
  truncated snippets + structural shape, not verbatim text. The Haiku transcript
  extraction at `Stop` already covers content; this pillar is about *shape*.
- **Per-tool approval/rejection records.** Avoid — aggregate to counts. Per-tool
  rows explode recall noise and the WriteFilter would drop them anyway.
- **Solving true ESC-interrupt capture.** Avoid — Claude Code does not surface
  ESC as a hook event. Approximate with `idle_gap` + next-prompt. Real interrupt
  capture is a separate concern (No-Gos).
- **Auto-emulation / replaying patterns.** Explicitly out of scope for this pillar.

## Risks

### Risk 1: Tool rejections are invisible to PostToolUse
**Impact:** The "rejection decisions" half of "approval/rejection" can't be fully
captured — `PostToolUse` only fires on executed (approved) tools.
**Mitigation:** Capture the approval stream + any `permission`/`decision` field
present in the payload. Document the rejection gap explicitly in the feature doc
and No-Gos. The captured approval pattern is still high-value signal on its own.

### Risk 2: Observation noise crowds recall
**Impact:** One `pattern` Memory per session could accumulate and dilute the recall
pool for the `valor` project.
**Mitigation:** `category="pattern"` carries baseline importance (1.0) so these
sit below corrections/decisions in RRF re-ranking; the existing consolidation
("memory-dedup") reflection will merge near-duplicate interaction patterns. One
observation per session (not per event) caps volume.

### Risk 3: Capturing sensitive steering content
**Impact:** Steering snippets could contain secrets pasted into the TUI.
**Mitigation:** Run snippets through `agent.private_tag.strip_private` (already
used by `memory_bridge.ingest`) before storage, and truncate to a short snippet.

## Race Conditions

### Race 1: Concurrent hook writes to the same session JSONL
**Location:** `agent/session_telemetry.py` `record_telemetry_event` (reused).
**Trigger:** `UserPromptSubmit` and `PostToolUse` for the same session firing near-simultaneously.
**Data prerequisite:** the per-session JSONL handle.
**State prerequisite:** consistent append ordering.
**Mitigation:** The recorder already serialises all writes per session under a
GIL-atomic `threading.Lock` keyed by `session_id`. Reusing it inherits that
guarantee — no new locking needed. Capture is additive; we add no shared mutable
state of our own.

### Race 2: Summarize-at-Stop reads a trace still being appended
**Location:** `agent/tui_interaction_capture.py::summarize_and_store`.
**Trigger:** `Stop` summarize reading the JSONL while a late `PostToolUse` write lands.
**Data prerequisite:** the JSONL file.
**State prerequisite:** the session is terminating, so no new human events should arrive.
**Mitigation:** `read_session_timeline` reads the file independently of the write
handle and silently skips malformed/partial lines (existing behavior). A missed
trailing event is acceptable for a best-effort summary; correctness of the
observation does not depend on the final event.

## No-Gos (Out of Scope)

- `[EXTERNAL]` Auto-emulation / pattern replay by eng/granite sessions — this
  pillar is capture-and-store ONLY by explicit issue scope ("Out of scope: any
  auto-emulation behavior"). Emulation belongs to a future pillar of epic #1536
  and depends on a human product decision about whether/how agents should act on
  captured human patterns. Not buildable now.
- `[EXTERNAL]` True ESC-interrupt capture as a first-class event — Claude Code does
  not deliver ESC/interrupt as a hook event; capturing it would require a harness
  change outside this repo. Approximated here via `idle_gap`.
- `[EXTERNAL]` Explicit tool-*rejection* capture — `PostToolUse` only fires on
  approved tools; rejection events are not delivered to any current hook. Requires
  a Claude Code harness affordance we don't control.
- Bridge-driven eng/granite session capture — no human-in-the-TUI there; nothing
  to capture. Not deferred work, just not applicable.

## Update System

No update system changes required — this feature is purely internal. The new
module ships in the repo, the hook call-sites are already part of the synced
`.claude/hooks/` set, and no new dependency, config file, or secret is introduced.
Existing installations pick up the capture on next pull with no migration.

## Agent Integration

No new agent integration required. The captured patterns become available to the
eng/granite personas through the **existing** subconscious-memory recall path:
the `memory_search` / `memory_get` MCP tools (`mcp_servers/memory_server.py`) and
the hook-side `prefetch`/`recall` injection already surface `pattern` Memories.
No new CLI entry point in `pyproject.toml`, no `.mcp.json` change, no bridge import —
this is a capture path that feeds the substrate the agent already reads from.

- [ ] Integration test: simulate a local session timeline (write interaction
  events via the recorder), run `summarize_and_store`, then assert the resulting
  Memory is retrievable via `tools.memory_search` for the `valor` project.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/tui-interaction-capture.md` describing the capture
  surface (local Claude Code TUI), the three interaction event types, the
  summarize-at-Stop → Memory flow, the recall path, and the documented gaps
  (rejections, true interrupts).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Add a cross-reference subsection to `docs/features/subconscious-memory.md`
  (this pillar feeds that substrate) and `docs/features/session-telemetry.md`
  (the recorder it reuses).

### Inline Documentation
- [ ] Document the two new event types in `agent/session_telemetry.py`'s
  module-docstring event-schema list (additive).
- [ ] Docstrings on the public functions in `agent/tui_interaction_capture.py`.

## Success Criteria

- [ ] `agent/tui_interaction_capture.py` exists with `capture_prompt_event` and
  `summarize_and_store`, both fail-silent.
- [ ] `UserPromptSubmit` and `Stop` hooks call the capture module
  (grep confirms the two call-sites).
- [ ] Slash-command starts and substantive steering messages are recorded as
  interaction events in the session JSONL.
- [ ] At session end, one `pattern` Memory tagged `tui-interaction` is saved and
  is retrievable via `tools.memory_search`.
- [ ] Trivial prompts ("ok", "yes") produce no steering event; secrets are
  stripped via `strip_private`.
- [ ] All capture failures are swallowed and never block a hook or the TUI.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `user_prompt_submit.py` and `stop.py` reference
  `tui_interaction_capture`.

## Team Orchestration

### Team Members

- **Builder (capture-module)**
  - Name: capture-builder
  - Role: Implement `agent/tui_interaction_capture.py` + the two new event
    types' documentation, fail-silent throughout.
  - Agent Type: builder
  - Resume: true

- **Builder (hook-wiring)**
  - Name: hook-builder
  - Role: Wire the three fail-silent call-sites into the existing hooks.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (capture-tests)**
  - Name: capture-tester
  - Role: Unit + integration tests (failure paths, gating, recall round-trip).
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (capture-docs)**
  - Name: capture-docs
  - Role: Feature doc + index + cross-refs + inline docstrings.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: capture-validator
  - Role: Verify all success criteria, run scoped tests.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build the capture module
- **Task ID**: build-capture-module
- **Depends On**: none
- **Validates**: tests/unit/test_tui_interaction_capture.py (create)
- **Informed By**: spike-2 (recorder accepts arbitrary event types verbatim)
- **Assigned To**: capture-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/tui_interaction_capture.py` with `capture_prompt_event(session_id, prompt, cwd)` and `summarize_and_store(session_id, project_key)`.
- `capture_prompt_event`: classify `/`-prefixed prompts as `slash_command`, substantive non-slash prompts as `human_steering` (apply `TRIVIAL_PATTERNS` + `MIN_PROMPT_LENGTH` gating, `strip_private` + truncate snippets), call `record_telemetry_event`. **Derive the ordinal turn index internally** by counting existing prompt events in `read_session_timeline(session_id)` — do NOT accept a `turn_index` arg (the `UserPromptSubmit` payload does not carry one). A prompt classified as `human_steering` only when the derived ordinal > 0 (i.e. not the first prompt).
- `summarize_and_store`: read the timeline, distill slash sequence + steering count/positions + approval tally + idle-gap interrupts into one pattern string, save via `Memory.safe_save`. **`category` and `tags` MUST go inside the `metadata` DictField** (verified against `agent/memory_extraction.py:444` and `models/memory.py:110-113`), exactly: `Memory.safe_save(agent_id=f"tui-{session_id}", project_key=project_key, content=pattern_str[:500], importance=1.0, source=SOURCE_HUMAN, metadata={"category": "pattern", "tags": ["tui-interaction"]})`.
- **Guard `project_key is None`**: `_get_project_key` returns `str | None` (see `memory_bridge.py:198-203`). If it resolves to None, skip the Memory write entirely (log at DEBUG) — mirror the `ingest()`/`extract()` None-skip pattern.
- Wrap every function body in fail-silent try/except.
- Document the two new event types in `agent/session_telemetry.py` module docstring (additive comment only).

### 2. Validate the capture module
- **Task ID**: validate-capture-module
- **Depends On**: build-capture-module
- **Assigned To**: capture-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify fail-silent behavior and event classification against criteria.

### 3. Wire the hook call-sites
- **Task ID**: build-hook-wiring
- **Depends On**: build-capture-module
- **Validates**: tests/unit/test_tui_interaction_capture.py
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a fail-silent `capture_prompt_event` call in `user_prompt_submit.py` (after the existing ingest/prefetch block, reusing `session_id`/`prompt`/`cwd`).
- Add a fail-silent `summarize_and_store` call in `stop.py` (alongside `_run_memory_extraction`, before sidecar cleanup).
- Do NOT touch `post_tool_use.py` — tool approvals are read from the recorder's existing `tool_use` events at summarize time (critique C1).
- Resolve `project_key` via the existing `memory_bridge._get_project_key`.

### 4. Build the tests
- **Task ID**: build-tests
- **Depends On**: build-capture-module, build-hook-wiring
- **Validates**: tests/unit/test_tui_interaction_capture.py, tests/unit/test_session_telemetry.py
- **Assigned To**: capture-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: classification (slash/steering/trivial-gated/empty), fail-silent on recorder/Memory raise, `strip_private` applied.
- Unit: recorder round-trips the two new event types (extend test_session_telemetry.py).
- Integration: write a synthetic timeline → `summarize_and_store` → assert Memory retrievable via `tools.memory_search` for `valor` project. Use a `test-` project_key prefix and clean up via Popoto `instance.delete()`.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: capture-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/tui-interaction-capture.md`; add README index entry; cross-ref subconscious-memory + session-telemetry docs; verify inline docstrings.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: capture-validator
- **Agent Type**: validator
- **Parallel**: false
- Run scoped tests (`pytest tests/unit/test_tui_interaction_capture.py tests/unit/test_session_telemetry.py -q`).
- grep-confirm the two hook call-sites reference `tui_interaction_capture`.
- Verify all success criteria, including the recall round-trip. Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Capture unit tests pass | `pytest tests/unit/test_tui_interaction_capture.py -q` | exit code 0 |
| Telemetry tests still pass | `pytest tests/unit/test_session_telemetry.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/tui_interaction_capture.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/tui_interaction_capture.py` | exit code 0 |
| Hooks wired | `grep -l tui_interaction_capture .claude/hooks/user_prompt_submit.py .claude/hooks/stop.py` | output contains both |
| Module exists | `python -c "import agent.tui_interaction_capture as m; assert hasattr(m,'capture_prompt_event') and hasattr(m,'summarize_and_store')"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room), verdict NEEDS REVISION → revised. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | all 3 | `turn_index` arg assumed but `UserPromptSubmit` payload carries no turn counter | Task 1 + event-types: signature is now `capture_prompt_event(session_id, prompt, cwd)`; ordinal derived internally from `read_session_timeline()` | `human_steering` only when derived ordinal > 0 |
| BLOCKER | structural | `Memory.safe_save(category=..., metadata={tags})` wrong shape; `category`/`tags` must live inside `metadata` DictField; `project_key` None unguarded | Task 1: explicit `metadata={"category":"pattern","tags":["tui-interaction"]}` call verified against `memory_extraction.py:444` + `memory.py:110-113`; None-guard skips write | Mirrors `ingest()`/`extract()` None-skip |
| CONCERN | C1 | `tool_decision`/`PostToolUse` call-site captures near-zero signal (approvals-only, decision field unconfirmed, rejection No-Go'd) | Cut to 2 call-sites; approvals tallied from the recorder's existing `tool_use` events at summarize time | `post_tool_use.py` untouched |
| CONCERN | C2 | Second `category="pattern"` Memory alongside Stop Haiku extraction with no dedup boundary | Technical Approach: distinct `agent_id=f"tui-{session_id}"` + `tui-interaction` tag separates streams; memory-dedup reflection handles residual | Interaction-shape vs. content observation namespaces |
| NIT | History | `.result.md.tmp` rename never landed (process artifact) | N/A — critique-process artifact, not a plan finding | — |

---

## Resolved Decisions

The two design questions raised at plan time are resolved with conservative
defaults (revisit during review if the human prefers otherwise):

1. **Observation granularity** — **one distilled `pattern` Memory per session**
   (not per-pattern records). Keeps recall noise low and respects the WriteFilter
   importance gate. Finer-grained per-slash-sequence records are deferred as a
   tuning follow-up if recall proves too coarse.
2. **Steering snippet length** — **short truncated snippet (≤120 chars), always
   `strip_private`'d** before storage. Carries enough signal to be recallable
   without storing verbatim multi-line steering text. Structural-only (no text)
   is the fallback if the snippets prove noisy in practice.
