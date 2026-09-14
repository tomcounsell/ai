---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-18
tracking: https://github.com/tomcounsell/ai/issues/2145
last_comment_id:
---

# Per-Tool Wedge Timeout Must Respect the Bash Call's Declared Timeout

## Problem

The worker's per-tool timeout sub-loop (`agent/session_health.py`,
`_check_tool_timeout`) declares a session "tool-wedged" when
`current_tool_name` is set and `last_tool_use_at` is older than the tier
budget (internal=30s, mcp=120s, default=300s). The tier budget is the ONLY
input — the tool call's own declared `timeout` parameter is ignored.

Observed live 2026-07-17 (issue #2145 recon): session `0_1784286820434`
(issue #2133 pipeline, MERGE stage) ran
`scripts/pytest-clean.sh tests/ ...` as a Bash call with a declared
`timeout: 600000` (10 min). At 300s the default tier fired
(`tool-wedge: Bash (default tier) older than 300s`), recovery was attempted
twice, and the session was finalized **failed** — one stage from merge, with
its PR approved and CI green. A call operating legitimately inside its own
declared budget was killed at half that budget.

**Current behavior:** a Bash call with a declared 600s timeout is
wedge-killed at 300s by the default tier.
**Desired outcome:** the wedge detector treats a declared-timeout call as
non-wedged until its own budget (plus grace) expires, while a cap prevents an
absurd declared value from disabling wedge detection entirely.

## Freshness Check

**Baseline commit:** 8633cfb6 (main)
**Issue filed at:** 2026-07-17T14:16:48Z
**Disposition:** Unchanged

File:line references re-verified against main @ 8633cfb6:
- `agent/session_health.py:515-561` — `_check_tool_timeout` computes
  `budget = _tool_tier_budget(tier)` with no declared-timeout input — holds.
- `agent/session_health.py:473-477` — tier constants, env-overridable — holds.
- `agent/hooks/liveness_writers.py:69-135` — `record_tool_boundary` /
  `_save_tool_boundary` write only `current_tool_name` + `last_tool_use_at` —
  holds.
- `agent/hooks/pre_tool_use.py:470-476` — PreToolUse hook calls
  `record_tool_boundary(tool_name=..., clear=False)` and has `tool_input` in
  scope (Bash `timeout` lives there, in **milliseconds**) — holds.
- `models/agent_session.py:508-519` — `current_tool_name` /
  `last_tool_use_at` field definitions — holds.
- `agent/session_health.py:2728-2757` — requeue paths clear
  `current_tool_name` / `last_tool_use_at` on the tool_timeout recovery
  branch — holds.

## Prior Art

- Issue #1270 introduced the per-tool tier sub-loop; #2002 added the epoch
  gate so stale wedge pairs from a prior run don't fire post-resume. The
  declared-timeout field must ride the same write as the wedge pair so the
  epoch gate covers all three fields consistently.
- Issue #2149 (just merged, 6932ad37) fixed the sibling age-based false
  positive (fast-oneshot-reap). Same afternoon, same class: health rules
  assuming durations that real work violates.

## Solution

### Key Elements

1. **Model field** (`models/agent_session.py`): `current_tool_timeout_s`
   (`Field(null=True, default=None)`, float seconds). Set on PreToolUse when
   the tool call declares a timeout; cleared to None on PostToolUse alongside
   `current_tool_name`.

2. **Capture** (`agent/hooks/liveness_writers.py` + `agent/hooks/pre_tool_use.py`):
   - `record_tool_boundary` gains keyword `declared_timeout_s: float | None = None`;
     `_save_tool_boundary` persists it in the same `save(update_fields=[...])`
     write as the wedge pair (consistency + epoch-gate coverage).
   - `pre_tool_use_hook` extracts the declared timeout: for Bash,
     `tool_input.get("timeout")` is **milliseconds** → divide by 1000.
     Non-numeric / non-positive values → None (defensive). Other tools: None
     (no supported declared-timeout parameter today; the parameter name is
     tool-specific, so extraction is a small per-tool mapping with Bash as
     the only entry).
   - `clear=True` (PostToolUse) always writes `current_tool_timeout_s=None`.

3. **Enforcement** (`agent/session_health.py::_check_tool_timeout`):
   ```python
   budget = _tool_tier_budget(tier)
   declared = getattr(entry, "current_tool_timeout_s", None)
   if isinstance(declared, (int, float)) and declared > 0:
       capped = min(float(declared), TOOL_TIMEOUT_DECLARED_MAX_SEC)
       budget = max(budget, capped + TOOL_TIMEOUT_DECLARED_GRACE_SEC)
   ```
   New env-tunable constants (same convention as sibling tiers):
   - `TOOL_TIMEOUT_DECLARED_MAX_SEC` (default **600**) — cap on the raise;
     the harness's own Bash maximum is 600s, and a missing/absurd declared
     value must never disable wedge detection (issue Downstream constraint).
   - `TOOL_TIMEOUT_DECLARED_GRACE_SEC` (default **60**) — covers PostToolUse
     hook latency after the tool itself finishes.
   The wedge reason string includes the effective budget and notes when the
   declared timeout raised it, e.g.
   `tool-wedge: Bash (default tier) older than 660s (declared 600s + 60s grace)`.

4. **Requeue-path hygiene** (`agent/session_health.py:2728-2757`): add
   `current_tool_timeout_s` to the field lists wherever `current_tool_name` /
   `last_tool_use_at` are cleared on the tool_timeout recovery branch, so a
   recovered session doesn't carry a stale declared budget into its next run.

### Non-goals

Excluding suite-lock wait time from wedge age (issue Solution Sketch option
2) is NOT pursued — the `max(tier, declared+grace)` rule already covers the
observed failure, and lock-aware accounting would couple session_health to
the pytest wrapper.

## No-Gos

- No new tier. The declared timeout modulates the existing tier budget; it
  does not introduce a fourth classification.
- No uncapped trust of the declared value: the raise is bounded by
  `TOOL_TIMEOUT_DECLARED_MAX_SEC`.
- No change to the epoch gate (#2002) semantics — the new field rides the
  same write as the wedge pair and is naturally epoch-scoped.
- No changes to MCP/internal tier behavior for tools without a declared
  timeout — their effective budget is unchanged.

## Update System

No update system changes required — pure worker-code + hook change deployed
by the ordinary `/update` git pull + worker restart. The new env knobs have
safe defaults and require no `.env` entry (they follow the existing
`TOOL_TIMEOUT_*_SEC` os.environ convention, documented in the timeout
catalog doc).

## Agent Integration

No agent integration required — this is a worker-internal health-loop change.
No new CLI entry point in `pyproject.toml [project.scripts]`; no bridge
import changes. The PreToolUse hook already runs inside every session
subprocess; it gains one optional extracted parameter.

## Failure Path Test Strategy

- **Capture failure**: `_save_tool_boundary` raising → `record_tool_boundary`
  returns False, never raises (existing contract, re-asserted with the new
  parameter present).
- **Bad declared values**: `timeout` missing, `None`, `0`, negative,
  non-numeric string → field written as None → tier budget applies unchanged.
- **Absurd declared values**: `timeout=86400000` (24h) → capped at
  `TOOL_TIMEOUT_DECLARED_MAX_SEC + grace` → wedge detection still fires.
- **Stale declared value from a prior run**: epoch gate returns None before
  the budget math (declared rides the wedge pair; #2002 test pattern reused).
- **Legacy rows**: `current_tool_timeout_s` attribute absent →
  `getattr(..., None)` → tier budget applies (no AttributeError).

## Test Impact

- [ ] `tests/unit/test_session_health_tool_timeout.py` — UPDATE: add
  declared-timeout resolution cases (declared < tier → tier; tier < declared ≤
  cap → declared+grace; declared > cap → cap+grace; None/0/negative/absent →
  tier; reason-string content). Existing tier cases remain valid unchanged.
- [ ] `tests/unit/test_pre_tool_use_liveness_writes.py` — UPDATE: assert the
  hook passes `declared_timeout_s` for Bash (ms→s), None for other tools and
  for malformed values.
- [ ] `tests/unit/test_agent_session_liveness_fields.py` — UPDATE: cover the
  new nullable field's default and round-trip.

## Critique Notes (folded)

Critique verified four hazards against source before Ready:
1. **ms-vs-s units**: Bash `timeout` is milliseconds — confirmed by the tool
   schema ("timeout in milliseconds, max 600000") AND the incident's
   `tool_use.jsonl` showing `timeout: 600000` for a 10-minute budget.
2. **Cooldown split-brain**: impossible by construction — `_save_tool_boundary`
   writes `current_tool_name`, `last_tool_use_at`, and
   `current_tool_timeout_s` in ONE `save(update_fields=[...])`; a
   cooldown-dropped PreToolUse write drops all three together (leaving
   name=None from the prior clear → wedge detector inert, the pre-existing
   safe envelope).
3. **Callers**: only two `record_tool_boundary` call sites
   (`pre_tool_use.py:474` clear=False, `post_tool_use.py:91` clear=True);
   keyword-with-default param breaks neither.
4. **Additional field-hygiene site**: the new field must also join
   `AgentSession._UPDATED_AT_OMISSION_OK_FIELDS` (models/agent_session.py
   ~894-908) so high-frequency liveness saves stay DEBUG-quiet.

## Rabbit Holes

- Don't try to enumerate declared-timeout parameters across all tools/MCP
  servers — Bash is the only tool with a first-class timeout parameter and
  the only observed failure mode. The extraction map is one entry.
- Don't build suite-lock-aware wedge accounting (see Non-goals).
- Don't migrate the tier constants into `config/settings.py TimeoutSettings`
  in this change — they predate the catalog and follow the raw-env
  convention; promoting them is a separate cleanup per the catalog's
  promote-vs-name-locally criterion.

## Documentation

- [ ] Update `docs/features/config-timeout-catalog.md`: document the
  tier-vs-declared-timeout interaction and the two new knobs
  (`TOOL_TIMEOUT_DECLARED_MAX_SEC`, `TOOL_TIMEOUT_DECLARED_GRACE_SEC`)
  (acceptance criterion on the issue).
- [ ] Update the per-tool timeout section of
  `docs/features/session-lifecycle.md` (or the doc that describes the
  tool-wedge recovery path) if it states the flat 300s default without
  qualification.

## Success Criteria

- [ ] A Bash call with declared timeout T (tier < T ≤ cap) is not wedge-killed
  before T + grace elapses (issue acceptance criterion 1).
- [ ] A declared timeout above `TOOL_TIMEOUT_DECLARED_MAX_SEC` raises the
  budget only to cap + grace — wedge detection is never disabled.
- [ ] Tools without a declared timeout keep today's exact tier behavior.
- [ ] Unit tests cover tier-vs-declared resolution (issue acceptance
  criterion 2) and all Failure Path cases above.
- [ ] `docs/features/config-timeout-catalog.md` documents the interaction
  (issue acceptance criterion 3).
- [ ] The 2026-07-17 incident scenario (600s-declared full-suite Bash call at
  MERGE) replayed against the new logic yields no wedge before 660s.

## Verification

1. `pytest tests/unit/test_session_health_tool_timeout.py -n0` — all pass,
   including new declared-timeout cases.
2. `pytest tests/unit/test_pre_tool_use_liveness_writes.py tests/unit/test_agent_session_liveness_fields.py -n0` — all pass.
3. Grep check: `_check_tool_timeout` reason string includes effective budget.
4. CI green on the PR (full suite runs in CI, not locally — worker-only
   machine constraint).
