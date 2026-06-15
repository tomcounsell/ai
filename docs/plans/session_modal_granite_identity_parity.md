---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-06-15
tracking: https://github.com/tomcounsell/ai/issues/1663
last_comment_id:
---

# Session Modal Granite Identity Parity (pm_pid/dev_pid/transcripts + pty_slot)

## Problem

Granite PTY sessions run a PM/Dev PTY pair inside a PTYPool slot. PR #1658 (issue #1648)
threaded the pair's identity — `pm_pid`, `dev_pid`, `pm_transcript_path`,
`dev_transcript_path` — from `ContainerResult` onto `AgentSession`, and surfaced it in
`dashboard.json`. But two gaps remain:

1. The session **modal** (`ui/templates/_partials/session_modal_content.html`) renders the
   newer `exit_reason` chip and the "no user msg" chip, but does **not** render the granite
   identity fields. An operator who opens a live granite session in the modal cannot jump
   from the card to the PM/Dev PIDs or transcript files — they are only in `dashboard.json`.
2. **No `pty_slot` field exists anywhere.** The PTYPool slot index a session pair occupies is
   computed inside `PTYPool.acquire_pair` (`slot.idx`) but never surfaced. Operators can't
   tell which pool slot a session is bound to — invisible in the model, in `dashboard.json`,
   and in the modal.

**Current behavior:**
- Modal shows: status, exit_reason, "no user msg", harness PID/liveness, timing — but not
  `pm_pid` / `dev_pid` / transcript paths.
- `pty_slot` does not exist on `AgentSession`, `ContainerResult`, `PipelineView`, or any UI.

**Desired outcome:**
- The modal surfaces a "Granite PTY" block showing `pm_pid`, `dev_pid`, the two transcript
  paths (copyable), and `pty_slot`, rendered only when the session ran on the granite path.
- `pty_slot` is captured at acquire time, threaded `PTYPool → ContainerResult → AgentSession`
  exactly the way `pm_pid`/`dev_pid` were in PR #1658, and exposed in `dashboard.json` +
  `PipelineView` + modal.

## Freshness Check

**Baseline commit:** `0d000e59cf39304b0861e93240d5623aad6f43f3`
**Issue filed at:** 2026-06-12T19:23:03Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `models/agent_session.py:310-317` — `pm_pid`, `dev_pid`, `pm_transcript_path`,
  `dev_transcript_path` IntField/Field(null=True) — **still present, unchanged.**
- `ui/app.py:459-463` — granite identity fields serialized into the per-session dashboard
  dict — **still holds.**
- `ui/data/sdlc.py:336-339` — `PipelineView` carries the four granite identity fields;
  `_build_pipeline_view` populates them via `_safe_nullable_int` / `_safe_str`
  (lines 944-947) — **still holds.**
- `ui/templates/_partials/session_modal_content.html:15-20` — modal renders `exit_reason`
  (15-17) and "no user msg" / `user_facing_routed` (18-20). **Confirmed: granite identity
  fields are NOT rendered in the modal.** (The issue calls the "no user msg" chip the
  `communicated` chip; the actual field is `user_facing_routed` — both refer to the same
  chip. Real exit/communicated chips already exist; only the PID/transcript fields are
  missing, matching the issue.)
- `agent/granite_container/container.py:268-271, 297-313` — `ContainerResult` carries
  `pm_pid`/`dev_pid`/`pm_transcript_path`/`dev_transcript_path`; `_capture_pty_identity`
  fills them from the PTY drivers — **still holds. No `pty_slot` field exists.**
- `agent/granite_container/pty_pool.py:264-322` — `acquire_pair` yields `(pm, dev)`;
  `slot.idx` is the pool slot index (`_Slot.idx`, line 85). The slot index is **not**
  currently surfaced through the yield — confirmed gap for `pty_slot`.
- `agent/granite_container/bridge_adapter.py:281, 373-384` — `acquire_pair` call site;
  pm_pid/dev_pid/transcripts persisted to AgentSession under the `update_fields` pattern.
  **This is the exact pattern `pty_slot` must follow.**

**Cited sibling issues/PRs re-checked:**
- PR #1658 "Dashboard telemetry parity for granite PTY sessions" — merged 2026-06-12T19:17:56Z
  (the source of the existing identity fields). **Still the canonical reference pattern.**
- #1648 — parent telemetry issue; the identity fields landed under it. Still relevant.

**Commits on main since issue was filed (touching referenced files):**
- `971b77d6` Granite PTY persona-as-priming refactor — touched granite container; did **not**
  remove pm/dev PTYs or the identity fields. Irrelevant to this plan's premise.
- `dd926192` (#1691) "Merge PM/Dev bridge roles into single Eng role; collapse SessionType to
  {eng, teammate, granite}" — collapsed *bridge session roles*, NOT the granite container's
  internal PM/Dev PTY pair. The container still spawns a PM PTY and a Dev PTY
  (`container.py:472-475`, `pty_pool.py:506-509`), still records `pm_pid`/`dev_pid`. **The
  issue's premise (a PM/Dev PTY pair with distinct PIDs/transcripts) still holds.** This is
  the only drift worth noting and it does not change scope.
- `277f346d` popoto bump to >=1.7.1 — dependency only, irrelevant.

**Active plans in `docs/plans/` overlapping this area:** None active. `granite_pty_production_cutover.md`
and `dashboard-session-detail-liveness.md` are shipped/complete and only provide reference
context (the modal liveness block this plan extends).

**Notes:** Minor drift only — the role-merge in #1691 renames bridge concepts but leaves the
container's PM/Dev PTY model (and therefore the issue's premise) intact. No line numbers in
the issue body itself drifted. Proceed.

## Prior Art

- **PR #1658 / issue #1648**: "Dashboard telemetry parity for granite PTY sessions" — added
  `pm_pid`/`dev_pid`/`pm_transcript_path`/`dev_transcript_path` to `ContainerResult`,
  `AgentSession`, `PipelineView`, and `dashboard.json`. **Succeeded.** This plan completes the
  follow-up the PR review flagged: render those fields in the modal and add the missing
  `pty_slot`. The threading pattern (`ContainerResult → BridgeAdapter update_fields →
  AgentSession`) is copied verbatim for `pty_slot`.
- **`docs/plans/dashboard-session-detail-liveness.md`**: shipped the modal's "Timing &
  Liveness" table and harness-PID/liveness chips this plan extends. Reference for modal table
  conventions.

No prior attempt at rendering granite identity in the modal exists — this is the first.

## Data Flow

`pty_slot` (new) — mirrors the existing pm_pid path:

1. **Entry point**: `PTYPool.acquire_pair` (`pty_pool.py:264`) selects an idle `_Slot` and
   sets `slot.state = "locked"` (line 317). `slot.idx` is the pool slot index.
2. **Surface the index**: change the yield from `yield (pm, dev)` to also expose `slot.idx`
   (the chosen mechanism is a spike decision — see Technical Approach).
3. **BridgeAdapter** (`bridge_adapter.py:281`): capture `slot.idx` from the acquire context
   and stamp it onto the `ContainerResult` after `container.run` returns (or onto the
   AgentSession directly), alongside the existing pm_pid/dev_pid persistence (373-384).
4. **AgentSession**: new `pty_slot = IntField(null=True)` persisted in the same
   `update_fields` block that already writes pm_pid/dev_pid.
5. **PipelineView** (`sdlc.py`): new `pty_slot: int | None = None`, populated via
   `_safe_nullable_int(getattr(session, "pty_slot", None))`.
6. **dashboard.json** (`app.py:459-463`): add `"pty_slot": s.pty_slot` to the per-session dict.
7. **Output**: modal renders the Granite PTY block, including `pty_slot`.

The four existing identity fields already flow 1→7 except for the final modal render — that
last hop is the only change needed for them.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `PTYPool.acquire_pair`'s yielded value gains the slot index (the
  only behavioral interface change; the spike resolves whether to widen the tuple or attach
  the index to the PTY objects). `AgentSession`, `ContainerResult`, `PipelineView` each gain
  one nullable int field — purely additive.
- **Coupling**: unchanged. The modal already reads `PipelineView`; this adds fields to a
  surface it already consumes.
- **Data ownership**: unchanged — the pool owns slot assignment; the field is a read-only
  snapshot stamped at acquire time.
- **Reversibility**: trivial — nullable fields and additive template blocks; revert is a
  clean delete.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (modal render correctness + the `acquire_pair` yield-shape change)

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are internal Python +
Jinja template edits.

## Solution

### Key Elements

- **Modal Granite PTY block**: a new section in `session_modal_content.html`, rendered only
  when granite identity fields are present, showing `pm_pid`, `dev_pid`, copyable transcript
  paths, and `pty_slot`. Mirrors the existing "Timing & Liveness" table styling.
- **`pty_slot` capture**: surface `slot.idx` from `acquire_pair`, thread it through
  `ContainerResult` → `AgentSession` using the PR #1658 `update_fields` pattern.
- **`pty_slot` exposure**: new field on `AgentSession`, `ContainerResult`, `PipelineView`,
  and the `dashboard.json` per-session dict.

### Flow

Dashboard sessions list → Click a granite session → Modal opens → **Granite PTY** block shows
PM PID, Dev PID, PM transcript (copyable), Dev transcript (copyable), pool slot.

### Technical Approach

- **Modal render (gap 1)**: Add a `{% if pipeline.pm_pid or pipeline.dev_pid or
  pipeline.pm_transcript_path or pipeline.dev_transcript_path or pipeline.pty_slot is not none %}`
  guarded block. Render PIDs in a compact `data-table`; render the two transcript paths as
  copyable mono chips (reuse the existing `copySessionDetails`/clipboard JS pattern, or a small
  inline copy handler). Keep it inside `modal-body`, after the Timing & Liveness grid. Render
  only when at least one granite field is present so SDK-path sessions show nothing.
- **`pty_slot` capture (gap 2)**: `acquire_pair` (`pty_pool.py:264`) currently does
  `yield (pm, dev)`. The cleanest surface (resolved by spike-1) is to widen the yield to
  `yield (pm, dev, slot.idx)` and update the single caller (`bridge_adapter.py:281`). Stamp
  the captured slot onto `ContainerResult.pty_slot` after `container.run`, then persist it in
  the existing `update_fields` block (bridge_adapter.py:371-384) with a
  `if result.pty_slot is not None:` guard, matching pm_pid/dev_pid.
- **Field plumbing**: add `pty_slot: int | None = None` to `ContainerResult`
  (`container.py`), `pty_slot = IntField(null=True)` to `AgentSession`
  (`models/agent_session.py`, in the granite identity block), `pty_slot: int | None = None`
  to `PipelineView` (`sdlc.py`) populated via `_safe_nullable_int`, and
  `"pty_slot": s.pty_slot` to the `app.py` dashboard dict.
- **Backcompat**: per the `_heal_descriptor_pollution` note, adding a nullable AgentSession
  field needs no extra backcompat code — the descriptor walk handles it generically.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks introduced. The `acquire_pair` yield-shape change
      touches a context manager whose `finally` already handles release; verify the slot is
      still released correctly after the yield-shape change (existing `test_pty_pool.py`
      release/respawn tests must still pass).
- [ ] If the single caller is updated but a tuple-unpack count mismatches, that surfaces as a
      hard `ValueError` at acquire — covered by `test_bridge_adapter.py` integration through
      the container run, not silently swallowed.

### Empty/Invalid Input Handling
- [ ] Document SDK-path / pre-deploy granite sessions: `pty_slot` is `None`. The modal block
      must not render for `None`-only sessions (guard on presence of any granite field).
- [ ] `_safe_nullable_int(None)` returns `None` (existing helper, `sdlc.py:732`) — assert in
      the PipelineView test that a session with no `pty_slot` yields `pty_slot is None`.

### Error State Rendering
- [ ] The modal block is user-visible: add/extend a template render test (or assert via the
      `PipelineView` → rendered HTML path) that a granite session shows the PIDs/transcripts
      and a non-granite session shows none.
- [ ] Transcript path values come straight from the model; verify they render escaped (Jinja
      autoescape) and do not break the modal when `None`.

## Test Impact

- [ ] `tests/unit/test_ui_sdlc_data.py::test_*` (the granite-identity PipelineView tests at
      lines 408-427) — UPDATE: extend the existing assertions to also assert `pty_slot is None`
      in the empty case and `pty_slot == <N>` when set on the source session.
- [ ] `tests/unit/granite_container/test_pty_pool.py` — UPDATE: the `acquire_pair` tests must
      account for the widened yield `(pm, dev, slot.idx)`; assert the yielded slot index equals
      the locked slot's `idx`.
- [ ] `tests/unit/granite_container/test_bridge_adapter.py` /
      `test_bridge_adapter_delivery.py` — UPDATE: wherever `acquire_pair` is mocked or its
      yield consumed, update the unpack to the 3-tuple and assert `pty_slot` is persisted to
      AgentSession in the `update_fields` set.
- [ ] `tests/unit/granite_container/test_container.py` — UPDATE: assert `ContainerResult`
      default `pty_slot is None` and that it round-trips when set.

No modal-specific render test exists yet; a new assertion is additive (covered under Failure
Path Test Strategy), not a modification of existing tests.

## Rabbit Holes

- **Live PID liveness probing for pm_pid/dev_pid in the modal.** Tempting to show
  alive/ghost chips for the PM/Dev PIDs like the harness PID does. Out of scope — the existing
  fields are post-exit snapshots; the harness liveness probe is a separate mechanism. Just
  render the values.
- **Making transcript paths clickable file:// links or fetching transcript content.** The
  modal should display + copy the path. Rendering transcript contents is a much larger feature.
- **Refactoring `acquire_pair` to a richer return object/dataclass.** Widening the yielded
  tuple to include `slot.idx` is the minimal change. A new "AcquiredPair" dataclass is
  over-engineering for one integer.
- **Surfacing pool-wide slot occupancy (which slots are busy) in the dashboard.** That's a
  separate pool-observability feature, not per-session identity.

## Risks

### Risk 1: `acquire_pair` yield-shape change breaks an un-grepped caller
**Impact:** A tuple-unpack mismatch raises at runtime in a granite session.
**Mitigation:** `acquire_pair` has exactly one production caller (`bridge_adapter.py:281`)
plus test usages. Grep `acquire_pair` across the repo before changing, update every call site,
and rely on `test_pty_pool.py` + `test_bridge_adapter.py` to catch unpack errors.

### Risk 2: Modal renders for SDK-path sessions or shows empty/None values
**Impact:** Visual noise / a block of empty rows on non-granite sessions.
**Mitigation:** Guard the entire block on presence of any granite field; render each row only
when its value is non-None (same pattern as the existing Timing & Liveness conditional rows).

## Race Conditions

No new race conditions identified. `slot.idx` is read while the slot is `locked` (after
`acquire_pair` sets `slot.state = "locked"`, line 317), so the index is stable for the
session's lifetime — the pool will not reassign that slot until release. `pty_slot` is stamped
once at acquire and persisted once in the same post-run `update_fields` save that already
writes pm_pid/dev_pid; no concurrent writer touches it. The existing release/respawn machinery
is unchanged.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item is in scope for this plan. The modal render and the
  `pty_slot` capture are both completable here; there is no external action, ordered deploy,
  destructive operation, or separately-tracked follow-up.

## Update System

No update system changes required — this feature is purely internal (Python field additions +
a Jinja template block). No new dependencies, config files, or migration steps. Nullable model
fields are handled generically by `_heal_descriptor_pollution`; no per-machine migration.

## Agent Integration

No agent integration required — this is a dashboard/UI change plus internal granite-container
plumbing. No new CLI entry point in `pyproject.toml [project.scripts]`, no new MCP tool, and
the bridge does not need to import anything new. The data is consumed by the existing
`ui/app.py` dashboard route and modal template only.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` (or the dashboard telemetry doc that
      covers the #1648 identity fields) to note the modal now surfaces pm_pid/dev_pid/
      transcripts and the new `pty_slot` field. If a dedicated dashboard-telemetry doc exists,
      update it; otherwise add a short subsection to the granite PTY feature doc.
- [ ] Verify `docs/features/README.md` index still points correctly (no new file expected; if a
      new doc is added, add the index row).

### Inline Documentation
- [ ] Docstring/comment on the new `AgentSession.pty_slot` field mirroring the existing
      granite-identity field comments (block at `models/agent_session.py:302-317`).
- [ ] Comment on the widened `acquire_pair` yield explaining why `slot.idx` is surfaced.

## Success Criteria

- [ ] Opening a granite session in the modal shows a Granite PTY block with `pm_pid`,
      `dev_pid`, both transcript paths (copyable), and `pty_slot`.
- [ ] A non-granite (SDK-path) session shows no Granite PTY block in the modal.
- [ ] `AgentSession.pty_slot`, `ContainerResult.pty_slot`, and `PipelineView.pty_slot` exist;
      `dashboard.json` includes `pty_slot` per session.
- [ ] `pty_slot` is captured from `PTYPool.acquire_pair`'s `slot.idx` and persisted via the
      same `update_fields` path as pm_pid/dev_pid.
- [ ] `grep -n '"pty_slot"' ui/app.py` confirms the dashboard dict includes it.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead orchestrates; it does not build directly.

### Team Members

- **Builder (granite-plumbing)**
  - Name: `pty-slot-builder`
  - Role: Add `pty_slot` to ContainerResult/AgentSession/PipelineView/dashboard.json and
    thread `slot.idx` from `acquire_pair` through BridgeAdapter.
  - Agent Type: builder
  - Resume: true

- **Builder (modal-render)**
  - Name: `modal-builder`
  - Role: Add the Granite PTY block to `session_modal_content.html` rendering the five fields.
  - Agent Type: builder
  - Resume: true

- **Validator (parity)**
  - Name: `parity-validator`
  - Role: Verify modal renders granite fields, hides them for SDK-path sessions, and
    `pty_slot` flows end-to-end into `dashboard.json`.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Thread pty_slot through the data layer
- **Task ID**: build-pty-slot
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_pty_pool.py, tests/unit/granite_container/test_bridge_adapter.py, tests/unit/granite_container/test_container.py, tests/unit/test_ui_sdlc_data.py
- **Informed By**: spike-1 (yield-shape decision)
- **Assigned To**: pty-slot-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `pty_slot: int | None = None` to `ContainerResult` (`container.py`).
- Add `pty_slot = IntField(null=True)` to `AgentSession` granite-identity block with a comment.
- Surface `slot.idx` from `acquire_pair` (widen yield per spike-1); update the
  `bridge_adapter.py:281` call site and stamp `result.pty_slot`.
- Persist `pty_slot` in the `update_fields` block (bridge_adapter.py:371-384) guarded by
  `if result.pty_slot is not None:`.
- Add `pty_slot: int | None = None` to `PipelineView` and populate via `_safe_nullable_int`.
- Add `"pty_slot": s.pty_slot` to the `app.py` dashboard dict.

### 2. Render the Granite PTY block in the modal
- **Task ID**: build-modal
- **Depends On**: build-pty-slot
- **Validates**: ui/templates/_partials/session_modal_content.html render (new assertion in test_ui_sdlc_data.py or a modal render test)
- **Assigned To**: modal-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a guarded Granite PTY block after the Timing & Liveness grid: render `pm_pid`,
  `dev_pid`, copyable `pm_transcript_path` / `dev_transcript_path`, and `pty_slot`.
- Guard on presence of any granite field; render each row only when non-None.
- Reuse the existing modal copy/clipboard JS pattern for transcript paths.

### 3. Validation
- **Task ID**: validate-parity
- **Depends On**: build-pty-slot, build-modal
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify a granite session's modal shows all five fields; SDK-path session shows none.
- Verify `dashboard.json` includes `pty_slot`.
- Run the affected unit tests and lint/format.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-pty-slot, build-modal
- **Assigned To**: parity-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Update the granite PTY / dashboard telemetry doc to describe the modal block and `pty_slot`.
- Add inline field/comment documentation.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-parity, document-feature
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm success criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted unit tests pass | `pytest tests/unit/test_ui_sdlc_data.py tests/unit/granite_container/test_pty_pool.py tests/unit/granite_container/test_bridge_adapter.py tests/unit/granite_container/test_container.py -q` | exit code 0 |
| dashboard.json exposes pty_slot | `grep -n '"pty_slot"' ui/app.py` | output contains pty_slot |
| AgentSession has pty_slot field | `grep -n 'pty_slot = IntField' models/agent_session.py` | output contains pty_slot |
| Modal renders granite identity | `grep -n 'pm_transcript_path\|pty_slot' ui/templates/_partials/session_modal_content.html` | output contains pty_slot |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **`pty_slot` surfacing mechanism** — widen `acquire_pair`'s yield to `(pm, dev, slot.idx)`
   (this plan's default, one caller to update) vs. attach `.pty_slot` onto the PTY driver
   objects (no signature change but mutates the driver). Spike-1 should confirm the tuple-widen
   is clean; flag if any hidden caller makes that risky.
2. **Modal placement** — render the Granite PTY block as its own `<h3>` section after Timing &
   Liveness (default), or fold the PIDs into the existing Timing & Liveness table next to the
   harness PID row? Default is a separate block for clarity.
3. **Transcript path display** — show the full absolute path (copyable) or a truncated tail
   (`…/{uuid}.jsonl`) with full path on copy? Default: truncated display, full path on copy,
   matching the existing `modal-id-chip` truncation convention.
