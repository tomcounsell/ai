---
status: docs_complete
type: feature
appetite: Small
owner: Valor
created: 2026-06-15
tracking: https://github.com/tomcounsell/ai/issues/1663
last_comment_id:
revision_applied: true
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

`pty_slot` (new) — mirrors the existing pm_pid path, but with one critical scoping detail the
capture point must respect:

1. **Entry point**: `PTYPool.acquire_pair` (`pty_pool.py:264`) runs a liveness-recycle loop
   (lines 289-318). It may `_release_pair` several dead slots before it finally locks a live
   one (`slot.state = "locked"; break`, lines 317-318). `slot.idx` is the pool slot index
   (`_Slot.idx`, line 85) — a **stable physical slot index**, reused across sessions and
   invariant across a `spawn_spec` respawn (the respawn reoccupies the *same* slot).
2. **Surface the index**: widen the yield at `pty_pool.py:322` from `yield (pm, dev)` to
   `yield (pm, dev, slot.idx)`. Because `slot.idx` is read from the variable bound by the
   FINAL locked slot (after the recycle loop's `break`), it always reflects the slot the
   session actually runs on — not an earlier recycled-away slot.
3. **BridgeAdapter** (`bridge_adapter.py:281`): unpack the widened yield as
   `async with ... as (pm, dev, pty_slot):`. After `result = await
   asyncio.to_thread(container.run)` (line 317) returns, stamp `result.pty_slot = pty_slot`
   — **before** `self._publish_exit_summary(result)` (line 327). Both `container.run` and
   `_publish_exit_summary` execute inside the `async with` block, so `pty_slot` is in scope at
   the stamp site. `_publish_exit_summary` then persists `result.pty_slot` in the same
   `update_fields` block that already writes pm_pid/dev_pid (lines 373-384).
4. **AgentSession**: new `pty_slot = IntField(null=True)` persisted in that same
   `update_fields` block, guarded by `if result.pty_slot is not None:`.
5. **PipelineView** (`sdlc.py`): new `pty_slot: int | None = None`, populated via
   `_safe_nullable_int(getattr(session, "pty_slot", None))`.
6. **dashboard.json** (`app.py:459-463`): add `"pty_slot": s.pty_slot` to the per-session dict.
7. **Output**: modal renders the Granite PTY block, including `pty_slot`.

**Why the stamp must live in `bridge_adapter.py`, not inside `Container`:** `Container.run`
(`container.py:260-271`) never learns the slot index — it only receives the two PTY drivers.
`_capture_pty_identity` fills pm_pid/dev_pid from the *driver* processes, which have no slot
awareness. The slot index exists only in `acquire_pair`'s scope, so the adapter is the single
place where both `slot.idx` and the `ContainerResult` are simultaneously in scope. This is the
exact scope that the previous plan revision missed: `_publish_exit_summary(result)` receives
only `result`, so the slot index must be stamped onto `result` *before* that call.

The four existing identity fields already flow 1→7 except for the final modal render — that
last hop is the only change needed for them.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `PTYPool.acquire_pair`'s yielded value widens from `(pm, dev)` to
  `(pm, dev, slot.idx)` — the only behavioral interface change, with exactly one production
  caller to update. `AgentSession`, `ContainerResult`, `PipelineView` each gain one nullable
  int field — purely additive.
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

- **Modal render (gap 1) — intent, not pixel-spec**: Render the four existing granite
  identity fields (`pm_pid`, `dev_pid`, `pm_transcript_path`, `dev_transcript_path`) plus the
  new `pty_slot` row in the session modal, placed after the Timing & Liveness grid. Reuse the
  existing modal copy/clipboard affordance for the two transcript paths. Guard the whole block
  on presence of any granite field so SDK-path sessions render nothing, and render each row
  only when its value is non-None. Exact Jinja structure (separate `<h3>` block vs. folded into
  the existing table) and the JS copy wiring are the builder's call — the existing
  `copySessionDetails`/clipboard pattern and `modal-id-chip` truncation convention are the
  reference; do not prescribe the markup here.
- **`pty_slot` capture (gap 2) — DECISION: widen the yield**: `acquire_pair`
  (`pty_pool.py:264`) currently does `yield (pm, dev)` at line 322. **Decided approach** (no
  spike needed — this is a one-line, single-caller change verified against source): widen the
  yield to `yield (pm, dev, slot.idx)`, capturing `slot.idx` immediately after the recycle
  loop's `slot.state = "locked"; break` (so it is the FINAL locked slot, never an earlier
  recycled-away one). Update the single production caller (`bridge_adapter.py:281`) to unpack
  `as (pm, dev, pty_slot)`. After `result = await asyncio.to_thread(container.run)` (line 317)
  returns, stamp `result.pty_slot = pty_slot` **before** `_publish_exit_summary(result)` (line
  327). `_publish_exit_summary` persists it in the existing `update_fields` block (lines
  373-384) with an `if result.pty_slot is not None:` guard, matching pm_pid/dev_pid.
- **REJECTED alternative — attach `.pty_slot` onto the PTY driver objects**: explicitly not
  done. The pair (`pm`/`dev` drivers) is swapped out and respawned on `spawn_spec` mismatch
  and on every release; a `.pty_slot` attribute on a driver would be tied to a transient pair
  object, not the stable physical slot. The slot index belongs on the *slot*, surfaced through
  the yield, and read once at lock time.
- **Slot-vs-pair semantics (record in the field comment)**: `pty_slot` is the **stable
  physical PTYPool slot index** the session ran on. It is reused across sessions and invariant
  across a `spawn_spec` respawn (the respawn reoccupies the same slot). It is correlated to a
  *specific* PM/Dev pair only via the co-persisted `pm_pid`/`dev_pid` captured in the same
  `update_fields` save — the slot alone does not identify a pair, only the physical lane.
- **Partial-data warning (not a crash)**: a granite run that records `pm_pid` but leaves
  `pty_slot` as `None` is partial data, not a failure — but it signals the capture wiring
  regressed. In `_publish_exit_summary`, when `result.pm_pid is not None` and
  `result.pty_slot is None`, emit a `logger.warning("[bridge-adapter] pm_pid set but pty_slot
  is None — slot capture may have regressed")`. This stays inside the existing fail-silent
  `try` block; it never raises.
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
- [ ] `tests/unit/granite_container/test_bridge_adapter.py` — ADD a **real-acquire** test that
      drives the genuine `PTYPool.acquire_pair` context across the `async with` boundary (not a
      tuple-mock of the yield), runs a stubbed container, and asserts the persisted
      `AgentSession.pty_slot` equals the slot index actually acquired (`slot.idx` of the locked
      slot). A tuple-mock would hard-code the slot value and mask the original scope/timing
      blocker (slot index never reaching the persistence call). This test is the regression
      guard for the BLOCKER and must exercise the real capture-and-stamp path.
- [ ] `tests/unit/granite_container/test_bridge_adapter.py` — ADD an assertion that when
      `result.pm_pid` is set but `result.pty_slot is None`, the partial-data warning is emitted
      (caplog), confirming partial data logs rather than crashes.

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

This is a Small, linear, additive change — one builder does the whole thing end to end
(data-layer plumbing then the modal render), then a validator confirms parity. No split across
named agents: the modal render depends on the field plumbing, so there is no parallelism to
exploit, and a single builder keeps the `acquire_pair → ContainerResult → AgentSession →
PipelineView → dashboard.json → modal` thread coherent in one head.

### Team Members

- **Builder (full change)**
  - Name: `parity-builder`
  - Role: Thread `pty_slot` through `acquire_pair`/ContainerResult/AgentSession/PipelineView/
    dashboard.json (widen the yield, stamp `result.pty_slot` before `_publish_exit_summary`,
    add the partial-data warning), then render the granite identity + `pty_slot` block in the
    session modal. Add the real-acquire regression test. Update docs and inline comments.
  - Agent Type: builder
  - Resume: true

- **Validator (parity)**
  - Name: `parity-validator`
  - Role: Verify modal renders granite fields, hides them for SDK-path sessions, that
    `pty_slot` flows end-to-end into `dashboard.json`, and that the real-acquire test asserts
    the persisted slot equals the acquired slot.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Thread pty_slot through the data layer
- **Task ID**: build-pty-slot
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_pty_pool.py, tests/unit/granite_container/test_bridge_adapter.py, tests/unit/granite_container/test_container.py, tests/unit/test_ui_sdlc_data.py
- **Assigned To**: parity-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `pty_slot: int | None = None` to `ContainerResult` (`container.py`).
- Add `pty_slot = IntField(null=True)` to `AgentSession` granite-identity block with a comment
  recording the slot-vs-pair semantics (stable physical slot, correlated to a pair only via
  co-persisted pm_pid/dev_pid).
- Widen the `acquire_pair` yield at `pty_pool.py:322` to `yield (pm, dev, slot.idx)` (slot.idx
  read from the FINAL locked slot after the recycle loop's `break`). Update the single caller
  at `bridge_adapter.py:281` to unpack `as (pm, dev, pty_slot)`.
- Stamp `result.pty_slot = pty_slot` after `container.run` returns (line 317) and **before**
  `_publish_exit_summary(result)` (line 327), inside the `async with`.
- Persist `pty_slot` in the `update_fields` block (bridge_adapter.py:373-384) guarded by
  `if result.pty_slot is not None:`. Add the partial-data warning when `pm_pid` is set but
  `pty_slot is None`.
- Add `pty_slot: int | None = None` to `PipelineView` and populate via `_safe_nullable_int`.
- Add `"pty_slot": s.pty_slot` to the `app.py` dashboard dict.

### 2. Render the Granite PTY block in the modal
- **Task ID**: build-modal
- **Depends On**: build-pty-slot
- **Validates**: ui/templates/_partials/session_modal_content.html render (new assertion in test_ui_sdlc_data.py or a modal render test)
- **Assigned To**: parity-builder
- **Agent Type**: builder
- **Parallel**: false
- Render the four granite identity fields + `pty_slot` in the modal after the Timing &
  Liveness grid (intent — exact markup is the builder's call; reuse the existing copy
  affordance).
- Guard on presence of any granite field; render each row only when non-None.

### 3. Tests, docs, and inline comments
- **Task ID**: build-tests-docs
- **Depends On**: build-pty-slot, build-modal
- **Assigned To**: parity-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the real-acquire regression test (drives the genuine `acquire_pair` context, asserts
  persisted `AgentSession.pty_slot` equals the acquired slot) and the partial-data-warning
  test.
- Update the granite PTY / dashboard telemetry doc to describe the modal block and `pty_slot`.
- Add inline field/comment documentation (the `pty_slot` field comment and the widened-yield
  comment).

### 4. Validation
- **Task ID**: validate-parity
- **Depends On**: build-tests-docs
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify a granite session's modal shows all five fields; SDK-path session shows none.
- Verify `dashboard.json` includes `pty_slot` and the real-acquire test asserts the persisted
  slot equals the acquired slot.
- Run the affected unit tests and lint/format; confirm success criteria including docs.

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

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Adversary | `pty_slot` capture unreachable at persistence time — `_publish_exit_summary(result)` has no access to `slot.idx`; would persist None | Data Flow §3 + Technical Approach | Widen yield to `(pm, dev, slot.idx)`; unpack at bridge_adapter:281; stamp `result.pty_slot` after `container.run` (317) and BEFORE `_publish_exit_summary` (327), all inside the `async with`. Source-verified both calls are in-block. |
| CONCERN | Archaeologist | Slot-vs-pair semantics undocumented | Technical Approach + field comment | Documented `pty_slot` as the stable physical slot index, correlated to a pair only via co-persisted pm_pid/dev_pid. |
| CONCERN | Operator | Recycle loop releases earlier slots before locking a different one | Data Flow §1-2 + Technical Approach | Capture `slot.idx` from the FINAL locked slot after `break`; explicitly REJECTED the driver-attach alternative (pair swaps on respawn). |
| CONCERN | Skeptic | None-pty_slot should warn, not crash | Technical Approach + Test Impact | Added partial-data `logger.warning` when pm_pid set but pty_slot None, inside the fail-silent try; added caplog test. |
| CONCERN | Adversary | Missing real-acquire test (tuple-mock would mask the blocker) | Test Impact | Added real-acquire regression test driving the genuine `acquire_pair` context across the async-with boundary. |
| CONCERN | Simplifier | Contradiction: "resolved by spike-1" vs "still open" | Technical Approach + Decisions | Reworded to a DECISION (widen the yield); dropped spike-1 entirely. |
| CONCERN | Simplifier | Team over-scoped (3 agents + documentarian) for a Small change | Team Orchestration + Tasks | Collapsed to ONE builder + validator. |
| CONCERN | User | Redundant Open Questions (all had defaults) | Decisions section | Promoted all three to decisions; removed Open Questions. |
| NIT | User | Modal over-specification | Technical Approach + Tasks | Stated INTENT (five fields + reuse copy affordance); left Jinja/JS to the builder. |

---

## Decisions (formerly Open Questions)

All three prior open questions had clear defaults and are now decisions — no human input
required before build:

1. **`pty_slot` surfacing mechanism** — DECIDED: widen `acquire_pair`'s yield to
   `(pm, dev, slot.idx)` and update the single caller. The driver-attach alternative is
   rejected (see Technical Approach — the pair swaps on respawn; the slot index belongs on the
   slot). Source-verified one-caller change; no spike needed.
2. **Modal placement** — DECIDED: the builder chooses separate `<h3>` block vs. folding into
   the Timing & Liveness table. Intent is what matters (render the five fields, reuse the copy
   affordance); markup is delegated.
3. **Transcript path display** — DECIDED: truncated display, full path on copy, matching the
   existing `modal-id-chip` truncation convention.
