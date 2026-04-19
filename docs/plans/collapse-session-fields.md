---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1026
last_comment_id:
revision_applied: true
---

# Collapse Overlapping AgentSession Fields: session_type, role, session_mode

## Problem

`AgentSession` has three fields that all answer variants of "what kind of session is this?", creating confusion about which field to read in any given context:

- `session_type` ("pm", "teammate", "dev") — the canonical discriminator used by worker routing and permission injection, but also read inconsistently alongside `session_mode`
- `role` ("pm", "dev", or None) — written only by `create_child()` and decorative in practice; never read for control flow decisions
- `session_mode` (PersonaType.TEAMMATE or None) — a runtime-mutable override read by the summarizer and QA nudge caps to switch output format

**Current behavior:** Three different code paths each read a different field:
- `agent/session_executor.py` (formerly in `agent_session_queue.py`, moved by #1023 refactor) reads `session_mode` first, then falls back to `session_type` (`getattr(agent_session, "session_mode", None) or getattr(agent_session, "session_type", None)`) — a priority flip that can shadow the canonical discriminator
- `bridge/summarizer.py` reads `session_mode == PersonaType.TEAMMATE` to skip structured formatting
- `agent/sdk_client.py` runtime-writes `session_mode = PersonaType.TEAMMATE` mid-session after intent classification
- `tools/valor_session.py:195` does `session_type = role` at CLI boundary, erasing any distinction
- `tools/valor_session.py:569,668` falls back to `.role` when `session_type` is missing

**Desired outcome:** One canonical field answers "what kind of session?". `role` is deleted (vestigial). `session_mode` is collapsed into `session_type` — the teammate runtime reclassification writes `session_type = SessionType.TEAMMATE` directly instead of introducing a shadow field.

## Freshness Check

**Baseline commit:** `4e79e6d25a95b077bfe5dcdd28c7b1ca3cab92a0`
**Issue filed at:** 2026-04-17T08:43:40Z
**Disposition (updated 2026-04-19):** Major drift — `agent_session_queue.py` was split into multiple files by PR #1023 (merged 2026-04-19). Session execution logic including the `session_mode` read sites moved to `agent/session_executor.py`.

**File:line references re-verified (2026-04-19):**
- `models/agent_session.py` (`session_type`, `role`, `session_mode` fields) — still present; exact lines may shift but fields hold
- `tools/valor_session.py:~195` (`session_type = role`) — confirmed, still holds
- `agent/sdk_client.py:~2156, ~2214` (session_mode write sites) — still holds
- **MOVED:** `agent_session_queue.py` session_mode read sites → now in `agent/session_executor.py:662-671` (_session_type derivation and _is_teammate) and `agent/session_executor.py:1337` (reaction-clearing check)

**Cited sibling issues/PRs re-checked:**
- #1022 — still open; parent umbrella for PM orchestration audit open questions
- PR #652 (SessionType.CHAT → PM rename) — merged 2026-04-03; relevant prior art
- PR #596 (replace magic strings with enums) — merged 2026-03-30; relevant prior art
- PR #1023 (split agent_session_queue.py) — merged 2026-04-19; moved session execution logic to `agent/session_executor.py`

**Active plans in `docs/plans/` overlapping this area:**
- `agent_session_field_cleanup.md` (tracking #609, CLOSED) — historical; completed field cleanup but explicitly excluded `session_mode`
- `agent_session_role_generalization.md` (tracking #634, CLOSED) — introduced the `role` field as a supplement to `session_type`; the current issue is the follow-up cleanup

**Notes:** The #1023 refactor moved `session_mode` read sites from `agent_session_queue.py` to `agent/session_executor.py`. All plan task step bullets referencing `agent_session_queue.py` for session_mode reads must use `agent/session_executor.py` instead. The `session_mode` write sites in `agent/sdk_client.py` are unaffected.

## Prior Art

- **PR #652** — "Rename SessionType.CHAT to PM + add TEAMMATE as first-class type" — merged 2026-04-03. Renamed the PM session type and added TEAMMATE as a formal `SessionType` enum value. Relevant: established the enum structure this plan builds on.
- **PR #596** — "Replace residual magic strings with enum constants" — merged 2026-03-30. Cleaned up magic string usage after #652. Relevant: same pattern of enum consolidation.
- **Issue #634 / plan `agent_session_role_generalization.md`** — Added `role` field as a `Field(null=True)` supplement to `session_type`, with spike confirming "role supplements session_type, not replaces it." Now the issue is that the supplement is vestigial — it is never read for control flow.

## Research

No relevant external findings — this is a purely internal data model refactoring with no external library or API dependencies.

## Data Flow

The discriminator fields participate in two key flows:

**Flow 1: Session creation → routing**

1. **Entry:** `tools/valor_session.py create` CLI or bridge enqueue
2. **CLI path:** `role = args.role or "pm"` → `session_type = role` (line 195 conflation) → `AgentSession(session_type=session_type, role=role, ...)`
3. **Worker routing:** `agent/session_executor.py` reads `session_mode or session_type` for nudge cap and reaction decisions (code moved from `agent_session_queue.py` by #1023 refactor)
4. **Permission injection:** `agent/sdk_client.py` reads `session_type` to set read-only vs full-permission SDK mode

**Flow 2: Runtime teammate reclassification**

1. **Entry:** Message arrives; intent classifier or config-driven persona determines Teammate mode
2. **`sdk_client.py`:** Writes `_s.session_mode = PersonaType.TEAMMATE` on the session record
3. **Summarizer:** Reads `session_mode == PersonaType.TEAMMATE` to switch to prose format
4. **Queue nudge loop:** Reads `session_mode == PersonaType.TEAMMATE` for reduced nudge cap
5. **Output router:** Receives `is_teammate=True` flag derived from `session_mode` check

**After this plan:** Flow 2 writes `session_type = SessionType.TEAMMATE` instead of `session_mode = PersonaType.TEAMMATE`. All readers use `session_type == SessionType.TEAMMATE`.

## Architectural Impact

- **Interface changes:** `create_child(role=...)` loses its `role` parameter (replaced by `session_type` parameter for non-dev child sessions, or removed since dev is the only valid child type). `create_dev()` signature unchanged.
- **Coupling:** Reduces coupling by eliminating the shadow field that let `session_mode` override `session_type` for routing.
- **Data ownership:** `session_type` becomes the sole discriminator, owned at session creation. Runtime reclassification (Teammate mode) still exists but writes to `session_type`.
- **Reversibility:** The `session_mode` field can be kept as a deprecated no-op Field during a transition period to avoid Redis deserialization errors on old records. Remove in a follow-up once all running sessions have naturally expired (30-day TTL).

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (verify scope decisions before build)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Delete `role` field:** The `role` field on `AgentSession` is written in `create_child()` and echoed back in `valor_session.py`, but never read for any routing or behavioral decision. Remove the field, remove all write sites, remove `role` parameter from `create_child()`.
- **Collapse `session_mode` into `session_type`:** The two runtime write sites in `sdk_client.py` (`_s.session_mode = PersonaType.TEAMMATE`) should write `session_type = SessionType.TEAMMATE` instead. All readers (`summarizer.py`, `agent_session_queue.py`) switch to reading `session_type`.
- **Fix CLI conflation:** `tools/valor_session.py:195` currently does `session_type = role`. Replace with an explicit mapping: `--role pm` → `session_type=SessionType.PM`, `--role dev` → `session_type=SessionType.DEV`, `--role teammate` → `session_type=SessionType.TEAMMATE`.
- **Deprecate `session_mode` field:** Keep the field declaration in `AgentSession` as `session_mode = Field(null=True)  # deprecated — use session_type` with no readers/writers. This prevents Redis deserialization errors on old in-flight records. Remove in a follow-up after 30-day TTL expires.

### Flow

CLI `valor-session create --role teammate` → explicit mapping → `session_type="teammate"` stored → worker reads `session_type` directly → summarizer reads `session_type == SessionType.TEAMMATE`

SDK runtime reclassification → `_s.session_type = SessionType.TEAMMATE; _s.save()` → same readers work without `session_mode` shadow

### Technical Approach

1. **`models/agent_session.py`**: Remove `role = Field(null=True)` field declaration. Remove the `save()` soft-validation check that warns on `role=None`. Remove `role=role` from `create_child()`. Mark `session_mode` as deprecated with a comment; keep the Field declaration but remove all reads.
2. **`tools/valor_session.py`**: Replace `session_type = role` (line 195) with an explicit `_ROLE_TO_SESSION_TYPE` dict mapping. Update list/status display code that falls back to `.role`.
3. **`agent/sdk_client.py`**: Replace both `_s.session_mode = PersonaType.TEAMMATE` writes with `_s.session_type = SessionType.TEAMMATE`. The guard `if _session_type != SessionType.PM` already prevents PM sessions from being reclassified — keep it.
4. **`agent/session_executor.py`** (formerly `agent/agent_session_queue.py` — moved by #1023 refactor): Replace the `getattr(agent_session, "session_mode", None) or getattr(agent_session, "session_type", None)` expression (~lines 661-666) with just `getattr(agent_session, "session_type", None)`. Update `_is_teammate` derivation (~lines 668-671) from `session_mode == PersonaType.TEAMMATE` to `session_type == SessionType.TEAMMATE`. Update reaction-clearing check (~line 1337) from `session_mode` to `session_type`. The `is_teammate` flag flows into `route_session_output()` for nudge cap selection — all three read sites must be updated together.
5. **`bridge/summarizer.py`**: Replace `getattr(session, "session_mode", None) == PersonaType.TEAMMATE` with `getattr(session, "session_type", None) == SessionType.TEAMMATE`.
6. **`ui/data/sdlc.py`**: Update `_resolve_display_persona()` to read only `session_type` (remove `session_mode` priority override).

## Failure Path Test Strategy

### Exception Handling Coverage
- The `sdk_client.py` write sites are already wrapped in `except Exception: pass` best-effort blocks — behavior unchanged by this plan. These blocks must have an existing test asserting the session type is readable even when the write fails.
- `agent_session_queue.py:4169` uses `getattr(..., None)` defensive reads — replace with `getattr(..., None)` on `session_type` only (same safety profile).

### Empty/Invalid Input Handling
- `valor_session.py` explicit mapping must handle unknown `--role` values: raise `ValueError` with allowed values list rather than silently defaulting.

### Error State Rendering
- No user-visible output in this change. Behavioral changes are in routing/summarization, not rendering.

## Test Impact

- [ ] `tests/unit/test_qa_nudge_cap.py` — UPDATE: replace `session.session_mode = "teammate"` with `session.session_type = SessionType.TEAMMATE` (3 occurrences at lines 50, 66, 80)
- [ ] `tests/unit/test_summarizer.py` — UPDATE: replace `session.session_mode = "teammate"` with `session.session_type = SessionType.TEAMMATE` (lines 1097, 1287); replace `session.session_mode = None` with no-op or remove (lines 39, 1116, 1145, 1163)
- [ ] `tests/unit/test_ui_sdlc_data.py` — UPDATE: replace `"session_mode": None` and `mock_session.session_mode = None` with `session_type`-based setup; verify `_resolve_display_persona()` tests pass
- [ ] `tests/integration/test_bridge_routing.py` — CHECK: verify no `session_mode` references that need updating
- [ ] `tests/unit/test_agent_session_queue.py` — CHECK: verify no `session_mode` references after #1023 refactor split the queue (code moved to `session_executor.py`, `session_health.py`, etc.)

## Rabbit Holes

- **Dev session sub-roles:** The issue body asks "Is there a future use for `role` (e.g., dev specialization by team)?". Do NOT design a generalized role system in this PR. The answer from spike-2 in #634 was clear: `session_type` is canonical, `role` supplements. Since `role` is vestigial, delete it. Future specialization can use `tags` (already a ListField on the model) or a new dedicated field with an actual use case.
- **Migrating existing Redis records:** Do NOT run a migration script on live Redis records to overwrite `session_mode`. Sessions have a 30-day TTL — existing records will naturally expire. New sessions will never set `session_mode`. The deprecated field stays in the model to safely deserialize old records.
- **Rearchitecting the intent classifier:** The runtime reclassification pattern (where `sdk_client` writes to a session field after deciding the persona) is questionable architecturally. Do NOT fix that pattern here — the issue is narrowly about field consolidation.

## Risks

### Risk 1: Old records with session_mode set still in Redis
**Impact:** Summarizer/nudge logic switches to reading `session_type`, but old records have `session_mode="teammate"` and `session_type="pm"` (for PM sessions that were reclassified). After this change, those sessions would lose the Teammate formatting override.
**Mitigation:** The 30-day TTL means all pre-migration sessions expire within 30 days. Analyze: PM sessions are explicitly guarded from Teammate reclassification (`if _session_type != SessionType.PM`), so this combination should not exist for PM sessions. Teammate sessions created before this change will have `session_type="teammate"` already — the new reader will work correctly.

### Risk 2: `create_child()` callers pass `role=`
**Impact:** If any caller passes `role="dev"` to `create_child()`, removing the parameter breaks them.
**Mitigation:** Grep confirms only `create_dev()` calls `create_child(role="dev")`. `create_dev()` is kept as a backward-compat wrapper. No external callers need updating.

## Race Conditions

No race conditions identified. The `session_type` field write in `sdk_client.py` is already in a try/except best-effort block with a Redis save; the same pattern applies whether writing `session_mode` or `session_type`. No new concurrent access patterns introduced.

## No-Gos (Out of Scope)

- Do NOT create a `session_mode` migration script to backfill old Redis records
- Do NOT add new session type variants (e.g., "builder", "reviewer") — that is a separate concern
- Do NOT change the `SessionType` enum values (strings "pm", "dev", "teammate" are stable)
- Do NOT touch any SDLC routing logic, output router, or PM persona prompts
- Do NOT remove `session_mode` field declaration yet — keep as deprecated no-op for 30-day TTL safety

## Update System

No update system changes required — this is a purely internal model and routing change. No new config files, dependencies, or migration steps needed.

## Agent Integration

No agent integration required — this is a model field consolidation with no new MCP tools or bridge changes. The bridge's `sdk_client.py` write sites are updated in place.

## Documentation

- [ ] Update `docs/features/pm-dev-session-architecture.md` to remove `session_mode` references and clarify that `session_type` is the sole discriminator
- [ ] Update docstring at top of `models/agent_session.py` to remove `role` field documentation and mark `session_mode` deprecated
- [ ] Update `CLAUDE.md` "Session Types" section if it references `session_mode`

## Success Criteria

- [ ] `role` field removed from `AgentSession` model and all write sites deleted
- [ ] `session_mode` field kept as deprecated `Field(null=True)` with zero readers and zero writers
- [ ] `tools/valor_session.py` uses explicit `_ROLE_TO_SESSION_TYPE` mapping; no `session_type = role` conflation
- [ ] `agent/sdk_client.py` runtime Teammate reclassification writes `session_type = SessionType.TEAMMATE`
- [ ] `agent/session_executor.py` reads `session_type` only (no `session_mode` fallback) — all three read sites updated
- [ ] `bridge/summarizer.py` reads `session_type == SessionType.TEAMMATE` only
- [ ] Grep confirms zero non-deprecated reads of `session_mode` and zero reads of `.role` on `AgentSession`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-fields)**
  - Name: session-fields-builder
  - Role: Implement field deletions, read/write migrations across all affected files
  - Agent Type: builder
  - Resume: true

- **Validator (session-fields)**
  - Name: session-fields-validator
  - Role: Verify zero `session_mode` readers, zero `.role` readers, grep checks, tests pass
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: session-docs-writer
  - Role: Update feature doc and model docstring
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

builder, validator, documentarian

## Step by Step Tasks

### 1. Delete `role` field and fix CLI conflation
- **Task ID**: build-role-delete
- **Depends On**: none
- **Validates**: `tests/unit/test_qa_nudge_cap.py`, `tests/unit/test_summarizer.py`
- **Assigned To**: session-fields-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `role = Field(null=True)` from `models/agent_session.py`
- Remove `save()` soft-validation `role=None` warning block
- Remove `role=role` kwarg from `create_child()` method; remove `role` parameter from signature
- Update `create_dev()` to call `cls.create_child(session_id=..., ...)` without `role=` kwarg (role is implied by `session_type=SESSION_TYPE_DEV` hardcoded in `create_child()`); rewrite `create_dev()` docstring to remove deprecated notice referencing `create_child(role=...)` (B1)
- Update module-level docstring on `AgentSession` to remove `create_child(role=...)` reference (B1)
- In `tools/valor_session.py`, replace `session_type = role` (line ~195) with an explicit `_ROLE_TO_SESSION_TYPE = {"pm": SessionType.PM, "dev": SessionType.DEV, "teammate": SessionType.TEAMMATE}` dict; map `role` through it; raise `ValueError` for unknown values
- Replace `getattr(s, "session_type", None) or getattr(s, "role", None) or "—"` with `getattr(s, "session_type", None) or "—"` at the two display lines (~843 and ~865) in `valor_session.py` (C2)

### 2. Collapse `session_mode` into `session_type`
- **Task ID**: build-session-mode-collapse
- **Depends On**: none
- **Validates**: `tests/unit/test_summarizer.py`, `tests/unit/test_qa_nudge_cap.py`, `tests/unit/test_ui_sdlc_data.py`
- **Assigned To**: session-fields-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/sdk_client.py` (~lines 2156, 2214): replace `_s.session_mode = PersonaType.TEAMMATE` with `_s.session_type = SessionType.TEAMMATE`
- In `agent/session_executor.py` (~lines 661-666): replace `getattr(agent_session, "session_mode", None) or getattr(agent_session, "session_type", None)` with `getattr(agent_session, "session_type", None)` (NOTE: code moved from agent_session_queue.py by #1023 refactor)
- In `agent/session_executor.py` (~lines 668-671): change `_is_teammate` derivation from `getattr(agent_session, "session_mode", None) == PersonaType.TEAMMATE` to `getattr(agent_session, "session_type", None) == SessionType.TEAMMATE` (C1)
- In `agent/session_executor.py` (~line 1337): update reaction-clearing check from `getattr(agent_session, "session_mode", None) == PersonaType.TEAMMATE` to `getattr(agent_session, "session_type", None) == SessionType.TEAMMATE`
- In `bridge/summarizer.py` (~lines 988, 1396): replace `getattr(session, "session_mode", None) == PersonaType.TEAMMATE` with `getattr(session, "session_type", None) == SessionType.TEAMMATE`
- In `ui/data/sdlc.py` (~lines 478+): update `_resolve_display_persona()` to read `session_type` only, removing `session_mode` priority override
- In `models/agent_session.py`: add deprecation comment to `session_mode` field: `# deprecated — use session_type. Kept as no-op for 30-day Redis TTL safety.`

### 3. Update tests
- **Task ID**: build-test-updates
- **Depends On**: build-role-delete, build-session-mode-collapse
- **Validates**: `tests/unit/test_qa_nudge_cap.py`, `tests/unit/test_summarizer.py`, `tests/unit/test_ui_sdlc_data.py`
- **Assigned To**: session-fields-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `tests/unit/test_qa_nudge_cap.py` `TestTeammateReactionClearing`: rename mock variable `session` → `agent_session`; change `session.session_mode = "teammate"` to `agent_session.session_type = SessionType.TEAMMATE`; change `session.session_mode = None` to `agent_session.session_type = "pm"`; update the inline condition in each test to `if agent_session and getattr(agent_session, "session_type", None) == SessionType.TEAMMATE and not task_error`; add `from config.enums import SessionType` import (B2)
- Update `tests/unit/test_summarizer.py`: replace `session.session_mode = "teammate"` with `session.session_type = SessionType.TEAMMATE` (lines 1097, 1287); remove `session.session_mode = None` no-op assignments (lines 39, 1116, 1145, 1163); add `from config.enums import SessionType` import if missing
- Update `tests/unit/test_ui_sdlc_data.py`: update `"session_mode": None` dict entries and `mock_session.session_mode = None` assignments to use `session_type`-based setup for `_resolve_display_persona()` tests

### 4. Validate
- **Task ID**: validate-session-fields
- **Depends On**: build-test-updates
- **Assigned To**: session-fields-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_qa_nudge_cap.py tests/unit/test_summarizer.py tests/unit/test_ui_sdlc_data.py -v`
- Verify `grep -rn "session_mode" models/ agent/ bridge/ tools/ ui/ --include="*.py"` shows only the deprecated field declaration in `models/agent_session.py` and no other readers/writers
- Verify `grep -rn "PersonaType.TEAMMATE" agent/ bridge/ --include="*.py"` shows no remaining `session_mode` comparisons (check particularly `agent/session_executor.py`, `bridge/summarizer.py`)
- Verify `grep -rn "\.role\b" models/ agent/ bridge/ tools/ ui/ --include="*.py"` shows zero results (excluding `SessionEvent.role`)
- Verify `grep -rn "session_type = role" tools/ --include="*.py"` returns no results
- Run full unit test suite: `pytest tests/unit/ -q`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-session-fields
- **Assigned To**: session-docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pm-dev-session-architecture.md`: remove `session_mode` references, clarify `session_type` is sole discriminator
- Update module docstring in `models/agent_session.py` to remove `role` field documentation and note `session_mode` deprecated
- Check `CLAUDE.md` "Session Types" section for `session_mode` references

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: session-fields-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q`
- Run `python -m ruff check .`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No session_mode readers | `grep -rn "session_mode" models/ agent/ bridge/ tools/ ui/ --include="*.py" \| grep -v "deprecated\|#\|session_mode = Field"` | exit code 1 |
| No role reads | `grep -rn "\.role\b" models/ agent/ bridge/ tools/ ui/ --include="*.py" \| grep -v "e\.role\|SessionEvent"` | exit code 1 |
| No CLI conflation | `grep -n "session_type = role" tools/valor_session.py` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Consistency | `create_dev()` calls `create_child(role="dev", ...)` — after removing `role` from `create_child()` signature, `create_dev()` breaks. Plan says "`create_dev()` is kept as a backward-compat wrapper" but does not update its implementation. | Task 1 (build-role-delete) | In `create_dev()`, change the call to `cls.create_child(session_id=..., project_key=..., ...)` without `role=` kwarg (role is implied by `session_type=SESSION_TYPE_DEV` which is already hardcoded in `create_child()`). Also update `create_dev()` docstring to remove "Deprecated: Use create_child(role='dev', ...) instead" since that pattern is being removed. Update module-level docstring on `AgentSession` that references `create_child(role=...)`. |
| BLOCKER | Consistency | `test_qa_nudge_cap.py` `TestTeammateReactionClearing` tests replicate the queue conditional inline using `session.session_mode`. After migration, those inline copies reference `session.session_mode` but the actual queue code at line 4844 reads `agent_session.session_mode`. The plan updates the tests to use `session.session_type` — but the tests are testing a mock object that stands in for `agent_session`, not `session`. The mock object name must change to `agent_session = MagicMock()` with `agent_session.session_type = SessionType.TEAMMATE` to match the actual queue code path. | Task 3 (build-test-updates) | In `test_qa_nudge_cap.py` `TestTeammateReactionClearing`, rename `session` → `agent_session` in the mock setup, change `session.session_mode = "teammate"` to `agent_session.session_type = SessionType.TEAMMATE`, update inline condition to match updated queue code: `if agent_session and getattr(agent_session, "session_type", None) == SessionType.TEAMMATE and not task_error`. |
| CONCERN | Skeptic | Plan's Technical Approach item 4 says to update `agent_session_queue.py` lines 4169, 4177, 4844 but does not mention the `_is_teammate` derivation at lines 4175-4178. After the migration, `_is_teammate` must be derived from `session_type == SessionType.TEAMMATE` not `session_mode == PersonaType.TEAMMATE`. The `is_teammate` flag flows into `route_session_output()` in `output_router.py` which selects the TEAMMATE nudge cap — missing this update would silently keep using `session_mode` for cap selection. | Task 2 (build-session-mode-collapse) | In `agent_session_queue.py` lines 4175-4178, change `_is_teammate = (agent_session is not None and getattr(agent_session, "session_mode", None) == PersonaType.TEAMMATE)` to `_is_teammate = (agent_session is not None and getattr(agent_session, "session_type", None) == SessionType.TEAMMATE)`. Add this as an explicit bullet in Task 2. |
| CONCERN | Skeptic | `valor_session.py` fallback reads use `getattr(s, "session_type", None) or getattr(s, "role", None) or "—"` (lines 843, 865), not `getattr(s, "role", None)` standalone. Task 1 says "Remove all `getattr(s, 'role', None)` fallback reads" — this phrasing is imprecise. The actual removal is the `or getattr(s, "role", None)` suffix in the two display lines. | Task 1 (build-role-delete) | Replace `getattr(s, "session_type", None) or getattr(s, "role", None) or "—"` with `getattr(s, "session_type", None) or "—"` at lines 843 and 865 of `valor_session.py`. Update task bullet to use exact code form. |
| NIT | Consistency | `create_dev()` docstring says "Deprecated: Use create_child(role='dev', ...) instead." — after this plan ships, `create_child()` no longer accepts `role=` so the deprecation notice becomes misleading. | Task 1 (build-role-delete) | Rewrite `create_dev()` docstring to "Create a Dev session. Preferred factory method for spawning dev child sessions." — remove the Deprecated notice entirely since `create_child(role=...)` is being retired. |

---

## Open Questions

No open questions — the two questions raised in the issue body are answered by existing code:

1. **Should `session_mode` collapse into `session_type`?** Yes. `session_type` already has a `TEAMMATE` value in the `SessionType` enum (added by #652). The runtime write sites in `sdk_client.py` check `if _session_type != SessionType.PM` before writing — this guard works identically whether writing `session_mode` or `session_type`. No enum extension needed.

2. **Is `role` truly dead?** Yes. Grep confirms zero reads of `.role` for control flow. `valor_session.py:405,431,551,641,668` uses it only for display/filtering in the CLI output. The `save()` soft-validation warns when `role=None` but takes no action. Future dev specialization should use `tags` (existing ListField) rather than resurrect `role`.
