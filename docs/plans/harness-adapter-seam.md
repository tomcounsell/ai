---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2000
last_comment_id:
revision_applied: true
---

# Phase 2: HarnessAdapter Seam — Normalized Turn Events + JSON-Schema Routing for `claude -p`

## Problem

The headless session runner (`agent/session_runner/`) drives every production session as one
`claude -p` subprocess per turn, but all knowledge of that CLI is welded into a 3,999-line
`agent/sdk_client.py`: argv assembly, stream-json parsing, resume-id capture, and the kept
harness entry point `get_response_via_harness` all live inline, with no seam to swap the
subprocess. Alongside the live harness path, the same module still carries a **fully dead
Claude Agent SDK path** — the `ValorAgent` class, `get_agent_response_sdk`, the process-local
`_active_clients` registry, and the `worker/idle_sweeper.py` that sweeps it — kept alive only
by a top-level dependency (`claude-agent-sdk==0.2.116`) and a scatter of test scaffolding. This
dead weight is exactly what blocks #1925 (remove `claude_code_sdk`) from shipping its harness
half.

PM output routing is prompt-discipline-based: the PM must voluntarily emit `[/user]`/`[/complete]`
prefix tokens (`agent/session_runner/router.py`), backed by compliance nudges and a wrap-up guard.
Non-compliant output costs extra turns. Meanwhile our own CLI (claude 2.1.204) grew `--json-schema`
structured-output validation and we still route by regex.

**Current behavior:**
- No seam: `claude -p` argv is assembled inline in `agent/sdk_client.py` (`get_response_via_harness`,
  lines 2339-2803); the runner imports claude-specific event parsing directly. Trying a second
  substrate means forking the runner.
- Dead SDK path co-resident with the live harness path in the same file: `ValorAgent` (1511-2338),
  `get_agent_response_sdk` (3355-end, the only production instantiator of `ValorAgent` at line 3949),
  `_active_clients` registry (59, 656, 666, 1771, 2012), `get_active_client`/`get_all_active_sessions`,
  and `worker/idle_sweeper.py` (wired live at `worker/__main__.py:906-916`, but it sweeps a registry
  that only the dead SDK path ever populates). The `claude_agent_sdk` import (lines 36-42) and the
  `claude-agent-sdk==0.2.116` pin (`pyproject.toml:9`) exist solely for this dead path.
- Routing is prompt-discipline-based: `router.py` prefix regex + compliance nudges + wrap-up guard.
- Resume machinery assumes every `--resume` forks a new session id ("Race 5" capture-at-init,
  `role_driver.py:210-254`); claude 2.1.204's `--fork-session` flag wording implies default
  `--resume` now *reuses* the id — the machinery may be compensating for behavior that no longer exists.

**Desired outcome:**
- One `HarnessAdapter` protocol behind which all claude-specific knowledge lives in a single
  `agent/session_runner/harness/claude.py`; the runner consumes normalized turn events only.
- `TurnResult` (the ex-T2.4 `HarnessResult`) as the normalized return type, carrying
  `resume_handle`, `structured_output | final_text`, `events`, `usage`, and an `exit_reason`
  drawn from #2004's `ExitReason` StrEnum.
- The dead Claude Agent SDK path deleted wholesale (no legacy tolerance), the `claude-agent-sdk`
  dependency dropped, and #1925's harness half thereby completed in this single PR.
- PM routing driven by `--json-schema` structured output (`{route, message, file_paths?}`), with
  the prefix regex demoted to a telemetered fallback; the `file_paths` slot natively delivers
  #1802 (PM file-capable send path), closing it at merge with no separate build.
- Resume-id behavior empirically recorded and capture-at-init simplified to assert-and-alarm or
  kept private inside `claude.py`.

## Definitions

| Term | Definition | Reference |
|------|-----------|-----------|
| HarnessAdapter | New protocol: `run_turn(TurnRequest) -> TurnResult` driving any turn-based headless CLI | this plan, Solution |
| TurnResult | Normalized return type: `{resume_handle, structured_output \| final_text, events, usage, exit_reason}` — delivers program item T2.4 | Solution; #2004 scope revision |
| Normalized TurnEvents | Thin event model aligned with codex ThreadEvent names (`turn.started`, `item.*`, `turn.completed{usage}`, `turn.failed`) — only what the runner consumes | master plan, Data Flow |
| Prefix-token routing | `^\[/(dev\|user\|complete)\]` regex classifying the PM's final text | `agent/session_runner/router.py` |
| Capture-at-init | On each turn's `system/init` event, retargeting `_transcript_path` (mid-turn-preempt safety) *and* reassigning `_claude_session_id` (historically because `--resume` forked ids) — two independent rationales | `agent/session_runner/role_driver.py:238-257` |
| Dead SDK path | `ValorAgent` + `get_agent_response_sdk` + `_active_clients` + `idle_sweeper` — the persistent-`ClaudeSDKClient` substrate, superseded by the `claude -p` harness | `agent/sdk_client.py`, `worker/idle_sweeper.py` |

## Freshness Check

**Baseline commit:** `35301b579a38844764f679e4da647d1bc84d27d3`
**Issue filed at:** 2026-07-10T06:25:46Z
**Disposition:** Minor drift (blast radius larger than the issue's framing; premises otherwise hold)

**File:line references re-verified (against baseline working tree, 2026-07-11):**
- `agent/sdk_client.py` — 3,999 lines total (issue cited `:2261-2510` for argv; the current harness
  entry `get_response_via_harness` is at **2339-2803**, argv/env assembly inside it). Holds; line
  numbers drifted.
- `get_response_via_harness` at **2339** — the KEPT path; extraction target. Confirmed it uses **no**
  `claude_agent_sdk` SDK types (grep of 2339-2803 for `AssistantMessage/ClaudeAgentOptions/ClaudeSDKClient/ResultMessage/TextBlock` is empty) → the SDK import can be dropped once the dead path goes.
- `ValorAgent` class at **1511-2338**; `get_agent_response_sdk` at **3355-end**; the only production
  `ValorAgent(...)` instantiation is at **3949, inside `get_agent_response_sdk`** — confirming
  `ValorAgent` is dead-by-consequence.
- `_active_clients` at **59, 656, 666, 1771, 2012**; populated only within `ValorAgent`'s run loop.
- `worker/idle_sweeper.py` — present (9 KB); wired live at **`worker/__main__.py:906-916`** via
  `supervise("idle-sweeper", run_idle_sweep)`. Its own docstring notes the harness path
  (`get_response_via_harness`) has nothing to go stale — so the sweeper is dead-by-consequence once
  the SDK path is deleted.
- `claude_agent_sdk` import at **36-42**; `claude-agent-sdk==0.2.116` at `pyproject.toml:9`
  (with the `mcp>=1.8.0` comment at `:10` referencing it). Holds.
- `agent/session_runner/router.py:149` — **`ExitReason` StrEnum present** (landed via #2004). Entry
  gate satisfied.

**Cited sibling issues/PRs re-checked:**
- **#1999 (Phase 1)** — CLOSED. Dependency satisfied; heartbeat suite is green baseline.
- **#2004 (resilience hygiene sweep)** — CLOSED. `ExitReason` StrEnum + SessionEvidence landed;
  this plan's entry gate (behind #2004) is satisfied. `TurnResult.exit_reason` reuses this enum.
- **#1925** — OPEN. This PR completes its **harness half** (SDK deletion); coordinate so #1925 shrinks
  to the PydanticAI/non-harness half.
- **#1802** — OPEN. This PR delivers it natively via the `file_paths` schema slot; closed at merge.
- **#1370** — OPEN. Owns outbound delivery-path canonicalization; the schema payload lands on its
  designated path. Coordinate, not a blocker.

**Drift that changes scope (larger blast radius than the issue implied):**
The issue framing ("only two legacy tests reference it") is **understated**. Ground-truth grep found:
- **Live production couplers beyond `sdk_client.py`:** `agent/__init__.py` re-exports
  `ValorAgent`/`get_active_client`/`get_all_active_sessions`; `worker/__main__.py:906-916` supervises
  `idle_sweeper`; `agent/health_check.py::_handle_steering` calls `get_active_client(session_id)` in a
  dead `if client:` arm — the surrounding `else` re-push body is the live CLI-harness steering path and
  is KEPT (only the arm is pruned).
- **Test surface (11 files + conftest), not two:** `tests/conftest.py` (centralized
  `claude_agent_sdk` MagicMock, lines 94-153), `test_agent_session_hierarchy.py`,
  `test_cross_repo_gh_resolution.py`, `test_cross_wire_fixes.py`, `test_persona_substitution.py`,
  `test_sdk_client.py`, `test_sdk_permissions.py`, `test_valor_session_resume_release.py`,
  `test_worker_idle_sweeper.py`, and `tests/integration/test_steering.py`.
- **The two named tests only reference the dead symbol in comments:** `test_pm_channels.py:136,162`
  and `test_error_summary_enforcement.py:40` mention `get_agent_response_sdk` in prose comments, not
  imports/calls — comment cleanup only.

**Build-time scope correction (Task 2.2, 2026-07-12):** the plan's "drop the whole `claude-agent-sdk`
dependency" framing is **too broad**. `claude_agent_sdk` (the installed package) has genuine live
consumers **outside** the dead `ValorAgent`/`get_agent_response_sdk` path: `agent/health_check.py`,
`agent/hooks/{__init__,post_tool_use,pre_tool_use,pre_compact,stop}.py`, and
`agent/agent_definitions.py` all import SDK hook-config types (`HookContext`, `HookMatcher`,
`AgentDefinition`, `PostToolUseHookInput`, `PreToolUseHookInput`, `PreCompactHookInput`,
`StopHookInput`) that are unrelated to the persistent-`ClaudeSDKClient` substrate being deleted here —
these are Claude Code's own hook-registration types, consumed regardless of which harness drives a
turn. **Corrected scope:** delete the `claude_agent_sdk` import **from `agent/sdk_client.py` only**
(confirmed clean — zero references remain there); the `claude-agent-sdk==0.2.116` **dependency stays**
in `pyproject.toml` because the hook-type consumers still need it. Every place below that says "drop
the `claude-agent-sdk` dependency" or "claude-agent-sdk dep dropped" is superseded by this correction.
This drift does not change the premise; it enlarges Test Impact and adds three prod-couple edits.
The plan scopes to the **real** blast radius below.

**Active plans in `docs/plans/` overlapping this area:** `harness-cross-compat.md` (#1996, this
plan's parent design input — Phase 2 section is authoritative); `consolidate_delivery_paths.md`
(#1370, downstream delivery — coordinate on the canonical outbox path). `resilience-simplification-three-tier.md`
is SUPERSEDED (shipped as #2003/#2004) — mined only for the T2.4 `HarnessResult`→`TurnResult` idea.

## Prior Art

- **#1996 (open, parent)** — `harness-cross-compat.md` is the authoritative harness-abstraction
  design; this plan implements its **Phase 2** (Steps 2.1-2.4). Phase 1 (#1999) shipped.
- **#1925 (open, KEEP)** — Remove `claude_code_sdk`; two-transport split. This PR IS its harness
  half — the SDK deletion here means #1925 shrinks to the PydanticAI/non-harness-LLM-call lane
  (a parallel, file-disjoint effort).
- **#2004 (closed)** — Resilience hygiene sweep: `ExitReason` StrEnum (`router.py:149`),
  SessionEvidence unification. `TurnResult.exit_reason` consumes this enum (one taxonomy).
- **#1924 / #1928 (closed)** — Single Opus PM + resumable dev subagent; continuation survives
  `--resume`. Fixed architecture this extraction must preserve under the seam.
- **#1917 (merged, PR #1993)** — Crash auto-resume; consumes the same resume scalars persisted at
  first-event. The persist-at-init contract must survive the seam (Race 1).
- **#1128 (idle_sweeper origin)** — the sweeper exists to tear down persistent SDK clients before a
  ~48h idle death; the harness path has no such client, so the sweeper is obsolete once the SDK path
  is gone.
- **#1802 (open)** — PM file-send gap; the routing schema's `file_paths` slot closes it here.

## Research

No new external research — the parent plan (`harness-cross-compat.md`, Research + Spike Results)
already performed the codex-exec sweep and local `claude --help` verification on 2026-07-10, and this
plan is claude-only. The `--json-schema` and `--resume`/`--fork-session` flag facts are carried
forward from the parent plan's spikes (see Spike Results below). No relevant ecosystem changes in the
one-day window since.

## Spike Results

Carried forward from `harness-cross-compat.md` (spikes performed 2026-07-10, claude CLI 2.1.204 on
this machine). The runtime-semantics probes remain **build-time gates** (Task 2.1) before rewiring.

### spike-1: `claude -p` structured output exists today
- **Assumption**: "claude -p has no codex `--output-schema` equivalent"
- **Method**: code-read (`claude --help`, v2.1.204)
- **Finding**: **False.** `--json-schema <schema>` exists (inline-JSON arg, "JSON Schema for structured
  output validation"). Schema routing is buildable now; prefix regex demotes to fallback.
- **Confidence**: high the flag exists; medium on exact runtime semantics under
  `--output-format stream-json` (where the validated object lands, retry-on-invalid). **Task 2.1 probes
  empirically before wiring.**

### spike-2: `--resume` may now reuse the session id
- **Assumption**: "every `--resume` forks a new session id (Race 5 premise)"
- **Method**: code-read (`claude --help`)
- **Finding**: `--fork-session`: "create a new session ID **instead of reusing the original**" — implies
  default `--resume` now reuses the id. Capture-at-init may be compensating for retired behavior.
- **Confidence**: medium (help text only). **Task 2.1 runtime-verifies with a two-turn probe.**
- **Impact**: stable → capture-at-init becomes assert-and-alarm; forked → logic stays, private to
  `claude.py`.

### spike-3 (deletion ground-truth, this plan, 2026-07-11)
- **Assumption**: "the SDK path is fully dead with no live production caller"
- **Method**: code-read (grep over `agent/ worker/ bridge/ tools/`)
- **Finding**: **Mostly true, with three live prod couplers to prune** (not zero): `agent/__init__.py`
  exports, `worker/__main__.py` idle-sweeper supervision, `agent/health_check.py` `get_active_client`
  branch. The only production instantiator of `ValorAgent` is `get_agent_response_sdk` (itself dead —
  no live caller). The KEPT `get_response_via_harness` uses no SDK types. Deletion is coherent and
  self-contained once those three couplers are pruned.
- **Confidence**: high.

### Task 2.1 empirical results (2026-07-11/12, claude 2.1.207)

**Method**: live shell execution of `claude -p` (not code-reading) on this machine, claude CLI
**2.1.207** (plan's spikes were recorded against 2.1.204 help-text; re-verified live). Both spike-1
and spike-2 hypotheses are **confirmed**, with one important runtime-mechanics correction to spike-1's
open question (where the validated object lands and how invalid output surfaces). Neither probe
invalidates the plan's approach — no STOP triggered.

#### Probe A: `--json-schema` under `--output-format stream-json`

Schema used: `{"type":"object","properties":{"route":{"type":"string","enum":["user","complete","continue"]},"message":{"type":"string"}},"required":["route","message"]}`.

**Mechanism — this is a synthetic tool call, not inline text.** The CLI injects a synthetic
`StructuredOutput` tool into the tool list (visible in the `system/init` event's `"tools"` array) when
`--json-schema` is passed. The model must emit a `tool_use` block naming it; the CLI validates the
tool's `input` against the schema out-of-band, and closes the tool_use with a synthetic `tool_result`.
Observed on a valid run:

```json
{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_01HRmQ4vRzRCcHpTJDPEMv3g","name":"StructuredOutput","input":{"route":"complete","message":"probe ok"}}]}, ...}
{"type":"user","message":{"content":[{"tool_use_id":"toolu_01HRmQ4vRzRCcHpTJDPEMv3g","type":"tool_result","content":"Structured output provided successfully"}]}, ...}
```

**Where the validated object lands**: the terminal `result` event (`{"type":"result","subtype":"success",...}`)
carries a top-level **`structured_output`** key with the parsed, schema-validated object, alongside the
usual `result` key holding the same object JSON-stringified:

```json
{"type":"result","subtype":"success","is_error":false,"result":"{\"route\":\"complete\",\"message\":\"probe ok\"}","stop_reason":"tool_use","structured_output":{"route":"complete","message":"probe ok"},"terminal_reason":"completed", ...}
```

This confirms the adapter's `TurnResult.structured_output` should be sourced from `result.structured_output`
on the terminal event — not scraped from assistant-text content.

**Invalid-output behavior — three sub-cases probed, all against a live CLI, not simulated:**

1. **Model-chosen invalid enum value** (asked the model to deliberately set `route:"banana"`): the model
   *itself* refused (safety/instruction-following behavior, not a CLI mechanism) and self-corrected to a
   valid value before calling the tool. Inconclusive for CLI-level enforcement by itself — see (2).
2. **Genuinely unsatisfiable schema** (`minProperties: 5` + `additionalProperties: false` on a
   2-property schema — no object can ever validate): the CLI performed **real JSON-Schema validation on
   the tool_use input** and rejected it with an `is_error:true` tool_result fed back to the model:
   `"Output does not match required schema: root: must NOT have fewer than 5 properties"`, then
   `"...must NOT have additional properties"` on the model's retry. After the model gave up (explained in
   prose why the schema was impossible instead of calling the tool again), the CLI injected exactly
   **one synthetic compliance nudge** as an `isSynthetic:true` user turn:
   `"[structured-output-enforce] You MUST call the StructuredOutput tool to complete this request. Call this tool now."`
   The model tried once more, failed validation again, then gave up for good. **The process still exited
   with `subtype:"success"`, `is_error:false`, `stop_reason:"end_turn"` — and the terminal `result` event
   had no `structured_output` key at all** (present in every valid run, absent here).
3. **Model refuses to call the tool at all** (instructed to answer in plain prose only): the CLI fired
   the same single `[structured-output-enforce] ... Call this tool now.` synthetic nudge once, the model
   repeated its refusal, and the CLI then terminated normally: `subtype:"success"`, `is_error:false`,
   `stop_reason:"end_turn"`, `result:"I refuse to use the StructuredOutput tool."`, **no `structured_output`
   key**.

**Conclusion — the concrete fallback-detection signal for the runner**: neither process exit code nor
`is_error` distinguishes schema success from schema failure — both are `is_error:false` and the process
exits 0 either way. **The adapter/router must detect schema-validation failure by checking whether the
terminal `result` event's `structured_output` key is present.** If present, route on it. If absent, fall
back to prefix-regex on `result.result` (the final text) exactly as the plan's Risk 1 / Solution
"Schema routing" section describes, and emit `schema_routing_fallback` telemetry. This also means the CLI
itself already implements a one-shot internal compliance nudge (`[structured-output-enforce]`) *before*
giving up — the runner's own compliance-nudge backstop only needs to trigger after the terminal
`structured_output`-absent signal, not duplicate the CLI's internal retry.

**`--include-partial-messages` interaction** (secondary, verified): adding this flag emits a long run of
`{"type":"stream_event","event":{...raw Anthropic streaming deltas...}}` events between `init` and
`result` — `message_start`, `content_block_start`/`delta`/`stop` (including `input_json_delta` chunks
that incrementally build the `StructuredOutput` tool's `input` JSON), `message_delta`, `message_stop`.
The terminal `result` event's `structured_output` field is unaffected — same shape, same location. No
interaction risk: partial-message streaming is additive telemetry only, safe to normalize into
`TurnEvent`s or ignore.

#### Probe B: `--resume` session-id stability (two-turn, then extended to three + a fork control)

Turn 1 (no `--resume`): `claude -p --output-format stream-json "Say the single word: ping"` →
`init.session_id` = `result.session_id` = **`1e7048ef-9ea2-49e4-b225-eee31f298200`**.

Turn 2 (`claude -p --resume 1e7048ef-9ea2-49e4-b225-eee31f298200 ...`, no `--fork-session`): both
`init.session_id` and `result.session_id` on the second turn were **identical**:
`1e7048ef-9ea2-49e4-b225-eee31f298200`. **Confirmed: default `--resume` reuses the id, does not fork.**

Turn 3 (repeated `--resume` on the same original id a second time, a third independent process): still
identical: `1e7048ef-9ea2-49e4-b225-eee31f298200`. Stability holds across repeated resumes, not just a
single one.

Turn 4 (control: `claude -p --resume 1e7048ef-9ea2-49e4-b225-eee31f298200 --fork-session ...`): produced
a **new, different** session id: `18f40925-f4af-4e7f-8089-9e1a204f1565`, distinct from the original.
This confirms `--fork-session` is the only path that forks; plain `--resume` is stable.

`claude --help` exact text on 2.1.207 (unchanged from the 2.1.204 wording the plan's spike-2 cited):

```
--fork-session                        When resuming, create a new session ID
                                      instead of reusing the original (use
                                      with --resume or --continue)
```

**Conclusion**: spike-2's hypothesis is **empirically confirmed, not just help-text-inferred**. The
`_claude_session_id` reassignment in `_handle_init` (`role_driver.py:238-257`) is compensating for
behavior that does not occur under plain `--resume` on this CLI version. Per the plan's Technical
Approach, this clears Task 2.2 to simplify that reassignment to **assert-and-alarm** (log + Sentry keyed
to persisted `claude_version` if an observed id ever differs from the expected one), while leaving
`self._transcript_path` retargeted unconditionally on every init event (the separate, still-valid
mid-turn-preempt rationale — untouched by this finding).

**No STOP triggered.** Both probes support the plan's approach as designed:
- Schema routing is usable as planned, with the refinement that fallback detection keys on
  `structured_output` presence/absence in the terminal event, not on exit code or `is_error`.
- Resume-id is stable under plain `--resume`, confirming capture-at-init's `_claude_session_id` half can
  be simplified to assert-and-alarm.

## Data Flow

Target flow after this PR (today's flow is identical minus the seam and with prefix-token routing at
step 5):

1. **Entry point**: Telegram message → bridge enqueues `AgentSession` → worker → `session_executor`
   builds `SessionRunner`.
2. **SessionRunner** (`runner.py`): per-turn loop — drain steering, compose message, call
   `HarnessAdapter.run_turn(TurnRequest)`.
3. **HarnessAdapter** (new `harness/claude.py`): builds `claude -p --json-schema … --resume <handle>`
   argv (extracted verbatim from `get_response_via_harness`), spawns the process group, parses native
   stream-json into **normalized TurnEvents**, returns `TurnResult{resume_handle, structured_output |
   final_text, events, usage, exit_reason}`.
4. **Runner telemetry**: normalized events stamp liveness (`liveness.py`), emit `turn_start`/`turn_end`
   session events, persist `resume_handle` at first sight (crash auto-resume floor unchanged, Race 1).
5. **Routing**: `TurnResult.structured_output.route` (`user | complete | continue`) drives routing
   directly; prefix regex (`router.py`) remains a telemetered fallback for schema-validation failure
   only; compliance nudge is the final backstop.
6. **Output**: `SessionRunnerAdapter` delivers via the transport-keyed callback / outbox exactly as
   today; `structured_output.file_paths` flows onto the same canonical path (coordinating with #1370),
   delivering #1802.

## Architectural Impact

- **New dependencies**: none. **Removed**: the `claude_agent_sdk` import from `agent/sdk_client.py`
  only. **Dependency kept**: `claude-agent-sdk==0.2.116` (`pyproject.toml:9`) stays — live hook-type
  consumers outside the deleted path (see Freshness Check build-time scope correction).
- **Interface changes**: `get_response_via_harness` (the free function) becomes
  `ClaudeHarnessAdapter.run_turn`; `get_agent_response_sdk`, `ValorAgent`, `get_active_client`,
  `get_all_active_sessions`, and `run_idle_sweep` are **deleted**. `agent/__init__.py` exports shrink.
  `claude_session_uuid` is generalized in meaning to "resume handle" (field name kept — no migration).
- **Coupling**: decreases — the runner stops importing claude-specific parsing; all CLI knowledge lives
  in one adapter module. The dead SDK substrate is removed entirely.
- **Data ownership**: unchanged — `session_runner` still owns subprocess lifecycle and the single
  authoritative liveness signal.
- **Reversibility**: the extraction is gated by a byte-equivalence golden test (provably
  behavior-preserving), so it reverts per-commit. The SDK deletion is a clean removal of unreachable
  code. Schema routing keeps the prefix regex as a runtime fallback, so a schema regression degrades
  rather than breaks.

## Appetite

**Size:** Large — this is the critical-path linchpin: every production session flows through the
extracted path, and it absorbs the seam + `TurnResult` + schema routing + a wholesale SDK deletion +
#1802 in one PR.

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (confirm the Task 2.1 probe findings before the routing/resume rewire; confirm the
  final PM turn schema shape)
- Review rounds: 2+ (extraction correctness on the hottest path; deletion completeness / no-dangling-refs)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| claude CLI ≥ 2.1.x with `--json-schema` | `claude --help \| grep -q -- --json-schema` | schema routing |
| #1999 (Phase 1) merged | `gh issue view 1999 --json state -q .state` → CLOSED | resume/liveness baseline |
| #2004 (`ExitReason` StrEnum) merged | `grep -q "class ExitReason" agent/session_runner/router.py` | one exit-reason taxonomy |
| Heartbeat suite green (entry gate) | `pytest tests/unit/test_session_heartbeat_progress.py -q` | regression baseline |

Run via `python scripts/check_prerequisites.py docs/plans/harness-adapter-seam.md`.

## Solution

### Key Elements

- **`agent/session_runner/harness/` package (new)**: `base.py` (protocol + `TurnRequest`/`TurnResult`/
  `TurnEvent`), `claude.py` (the extracted claude adapter), `events.py` (normalized event model,
  codex-ThreadEvent-aligned names). `HarnessCapabilities` is **deferred** — it is speculative infra
  with no consumer in this PR (its only reader would be the Phase 3 codex adapter / auto-selection
  policy). It ships with #2001, not here.
- **`TurnResult`**: the normalized return type (delivers program item T2.4). `exit_reason` is #2004's
  `ExitReason` StrEnum — no parallel taxonomy.
- **Schema routing**: PM turn schema `{route: user|complete|continue, message: str, file_paths?: [str]}`
  passed via `--json-schema`; `router.py` prefers the validated object, falls back to prefix regex
  (emitting `schema_routing_fallback` telemetry), then compliance nudge. A `schema_routing_fallback`
  **alert threshold** fires when the fallback rate exceeds **5% of turns over a rolling 1h window** (a
  healthy schema path is ~0%); a sustained breach means the schema contract has silently regressed and
  routing has degraded to regex. Wire the threshold into the existing analytics/alerting surface.
- **`file_paths` slot**: delivers #1802 natively — the PM's schema output can name files to attach,
  flowing onto the canonical outbox path. #1802 is confirmed and closed at this PR's merge.
- **Dead SDK path deletion**: remove `ValorAgent`, `get_agent_response_sdk`, `_active_clients` +
  `get_active_client`/`get_all_active_sessions`, `worker/idle_sweeper.py` (and its `worker/__main__.py`
  wiring), and the `claude_agent_sdk` import from `agent/sdk_client.py` (the dependency itself stays —
  see build-time scope correction). Prune the `agent/health_check.py` `get_active_client` branch and
  `agent/__init__.py` exports.

### Flow

Task 2.1 empirical probes (schema + resume-id) → **extract the seam behavior-preserving** (golden argv
test) → **delete the dead SDK path in the same PR** → **rewire routing to schema** (prime-pm-role
updated, `file_paths` wired) → apply the resume-id finding → live probe one real eng session end-to-end.

### Technical Approach

**Extraction (behavior-preserving):**
- Create `agent/session_runner/harness/{base,claude,events}.py`. Move argv/env assembly + stream-json
  parsing from `get_response_via_harness` (2339-2803) into `claude.py`. A **golden argv/env test**
  asserts the extracted adapter produces byte-identical argv and environment to the pre-extraction
  function for a representative `TurnRequest`. The golden test alone does not prove *behavior*
  preservation, so pair it with **behavioral-parity fixtures** asserting: (a) the final assembled argv
  **string** on both the first-turn (no `--resume`) and resume (`--resume <uuid>`) paths; (b)
  `_store_claude_session_uuid` fires with the harness-reported session id on turn completion
  (`sdk_client.py:2706`); and (c) the **#1980 retry-without-`--resume` branch** — a resumed subprocess
  that exits non-zero *without* a `result` event re-runs once with the full-context message and
  `--resume` stripped (`sdk_client.py:2379-2392, 2528-2536`), while a non-zero exit *after* a valid
  `result` does NOT retry. Together these make the extraction provably behavior-preserving before any
  semantics change.
- The runner (`role_driver.py`, `session_completion.py`) call sites switch from
  `get_response_via_harness` to the adapter. Runner consumes normalized `TurnEvent`s for
  liveness/telemetry; the adapter emits `session.started{handle}` as its first event so the runner
  persists the resume handle at first sight (Race 1).
- Apply spike-2's finding to the `_claude_session_id` reassignment **only**. `_handle_init`
  (`role_driver.py:238-257`) carries a second, independent rationale the resume-id question does not
  touch: it retargets `self._transcript_path` on every init so a mid-turn preempt/kill resumes the
  *partial* transcript, never the stale pre-turn uuid. So **keep `self._transcript_path` set on every
  init event unconditionally**; gate only the `self._claude_session_id` reassignment behind the
  resume-drift check (assert-and-alarm on a stable id — log + Sentry keyed to persisted `claude_version`
  on drift — or keep the reassignment if forked). Do not collapse both assignments behind one guard.

**Dead SDK path deletion (same PR, no legacy tolerance):**
- Delete `get_agent_response_sdk` (3355-end), `ValorAgent` (1511-2338), `_active_clients` and its
  accessors `get_active_client`/`get_all_active_sessions` (656, 666), and the module-level
  `_active_clients` machinery (59, 1771, 2012 and the num_turns/stop_reason scratch state that only the
  SDK loop wrote — verify each is SDK-only before removing).
- Delete `worker/idle_sweeper.py` and remove its supervision block in `worker/__main__.py:906-916`.
- Remove the `claude_agent_sdk` import (36-42) once no in-file references remain in `sdk_client.py`
  (verified: the kept harness path uses no SDK types). Per the build-time scope correction, the
  `claude-agent-sdk==0.2.116` dependency in `pyproject.toml:9` **stays** — `agent/health_check.py` and
  `agent/hooks/*.py` still import SDK hook-config types. No `pyproject.toml` edit needed here.
- In `agent/health_check.py::_handle_steering` (the sole steering-injection/delivery path for all
  CLI-harness production sessions, NOT a liveness check), delete **only** the dead `if client:` SDK arm
  (the `get_active_client(session_id)` call + its `from agent.sdk_client import get_active_client`
  import, and the `interrupt()`/`query()` injection body). **KEEP the `else` body** — the
  `_repush_messages` Redis re-push that hands steering messages to the worker's turn-boundary drain. It
  is the live production steering fallback for every CLI-harness session. After the edit the re-push
  becomes the unconditional body (no `client` local remains); the surrounding `try/except`
  re-push-on-failure guard stays untouched. Regression-test that a steering message injected while a
  CLI-harness session runs still lands on the Redis list and is drained next turn.
- Shrink `agent/__init__.py` exports (`ValorAgent`, `get_active_client`, `get_all_active_sessions`).
- Coordinate scope with #1925: comment that this PR completes its harness half; #1925 keeps only the
  PydanticAI/non-harness-LLM lane.

**Schema routing:**
- Task 2.1 first: probe `--json-schema` under `--output-format stream-json` (where the object lands,
  invalid-output behavior, `--include-partial-messages` interaction). If unusable, ship the seam +
  normalized events + SDK deletion and keep prefix routing (still a win); schema routing moves to a
  follow-up (Risk 1).
- Define the PM turn schema; pass via `--json-schema`; route on `structured_output.route`. Demote the
  prefix regex to fallback + `schema_routing_fallback` telemetry; keep the compliance nudge as final
  backstop. Update `.claude/commands/roles/prime-pm-role.md` (and teammate prime if applicable) to the
  schema contract; remove `[/user]`/`[/complete]` teaching. Wire `file_paths` through the schema and
  onto the canonical outbox path (#1370) — closing #1802.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Adapter subprocess failures (binary missing, non-zero exit, malformed stream-json line) surface
  as typed `TurnResult.exit_reason` values with a session event — test each. No bare `except: pass` in
  the new `harness/` package.
- [ ] Schema-validation failure path: an invalid/missing structured output falls back to prefix-regex
  routing and emits a `schema_routing_fallback` telemetry event (observable, not silent) — test.
- [ ] Deletion leaves no dangling `except`/import referencing `claude_agent_sdk` — `grep` gate in
  Verification.

### Empty/Invalid Input Handling
- [ ] Empty final message: the wrap-up guard still fires (no silent loop) — re-assert the existing
  `_run_wrapup_guard` contract through the seam.
- [ ] Empty/whitespace steering messages: existing `_steer_is_substantive` behavior unchanged —
  regression test through the seam.
- [ ] Empty schema output (validated object present but `message` empty): routes to the wrap-up guard,
  never to a silent success.

### Error State Rendering
- [ ] Harness-unavailable / subprocess crash mid-turn: the session fails with an operator-actionable
  message routed through the normal delivery path (never a raw traceback to Telegram — persona rule).
- [ ] Schema fallback exhaustion (invalid output AND no prefix token): the compliance nudge →
  needs-attention path still terminates.

## Test Impact

**Seam / routing (UPDATE to route through the adapter):**
- [ ] `tests/unit/session_runner/*` (role_driver/runner/router suites) — UPDATE: route through the
  `HarnessAdapter` seam; router tests split into schema-first + regex-fallback cases.
- [ ] `tests/unit/test_output_router.py`, `tests/unit/test_output_router_compaction_guard.py` — UPDATE:
  routing input becomes a structured object with regex fallback.
- [ ] `tests/unit/test_harness_streaming.py`, `test_harness_retry.py`,
  `test_harness_stale_uuid_result_preservation.py`, `test_harness_token_capture.py`,
  `test_harness_context_usage_log.py`, `test_harness_thinking_block_sentinel.py`,
  `test_harness_oom_backoff.py`, `test_sdk_client_harness_counters.py` — UPDATE: point at the extracted
  `harness/claude.py`; the stale-uuid test may become the spike-2 drift-alarm test.
- [ ] `tests/integration/test_harness_resume.py`, `test_harness_env_pm_injection.py`,
  `test_harness_no_op_contract.py` — UPDATE: resume-handle semantics + env assembly move behind the
  adapter.
- [ ] `tests/integration/test_runner_dispatch_e2e.py`, `test_headless_probe_e2e.py`,
  `test_runner_teardown_reap.py` — UPDATE: assert seam-level contracts.

**Dead SDK path deletion (DELETE / REPLACE — these test the removed substrate):**
- [ ] `tests/unit/test_sdk_client.py` — REPLACE: drop `ValorAgent` init tests; retain/repoint any
  harness-path assertions (e.g. `load_system_prompt`) onto the extracted adapter.
- [ ] `tests/unit/test_persona_substitution.py` — REPLACE: `ValorAgent._create_options` CUSTOMER_ID
  injection moves to the adapter's option assembly; re-test there.
- [ ] `tests/unit/test_cross_repo_gh_resolution.py` — REPLACE: `ValorAgent._create_options` GH_REPO
  injection re-tested against the adapter.
- [ ] `tests/unit/test_agent_session_hierarchy.py::TestValorAgentSessionIdInjection` — REPLACE:
  `agent_session_id` injection re-tested against the adapter.
- [ ] `tests/unit/test_cross_wire_fixes.py` — UPDATE/DELETE: the `_SDK_AVAILABLE`-gated `ValorAgent`
  cases; keep any cross-wire assertions that survive on the harness path.
- [ ] `tests/unit/test_sdk_permissions.py` — UPDATE: repoint permission-mode assertions onto the
  adapter's option assembly.
- [ ] `tests/unit/test_worker_idle_sweeper.py` — DELETE: `worker/idle_sweeper.py` is removed.
- [ ] `tests/unit/test_valor_session_resume_release.py` — UPDATE: drop any `_active_clients`/idle-sweep
  coupling; keep resume-release logic.
- [ ] `tests/integration/test_steering.py` — UPDATE: steering no longer touches `_active_clients`;
  assert the Redis-steering-list path only.
- [ ] `tests/conftest.py` (lines 94-153) — UPDATE/DELETE: remove the centralized `claude_agent_sdk`
  MagicMock and its per-test restore fixture once nothing imports the SDK.
- [ ] `tests/unit/test_pm_channels.py:136,162`, `tests/unit/test_error_summary_enforcement.py:40` —
  UPDATE (comment-only): scrub stale `get_agent_response_sdk` comments; these tests do not import the
  symbol.

**NEW:**
- [ ] `tests/unit/session_runner/test_harness_argv_golden.py` — CREATE: byte-equivalence golden test
  for the extraction, PLUS behavioral-parity fixtures asserting the final argv string on first-turn and
  resume paths, `_store_claude_session_uuid` firing on completion, and the #1980
  retry-without-`--resume` branch (and its no-retry-after-valid-`result` counterpart).
- [ ] `tests/unit/session_runner/test_schema_routing.py` — CREATE: schema-first routing + fallback +
  `schema_routing_fallback` telemetry + `file_paths` passthrough. The `file_paths` case asserts a real
  **file-delivery behavior** (a named file becomes an attachment on the outbox record), not just that
  the slot is parsed — the plumbing-only assertion is what let #1802 be closed prematurely once.

## Rabbit Holes

- **Building a universal event superset.** Normalize only what the runner consumes (liveness stamps,
  turn boundaries, usage, final output, error). Do not model every claude event type "for completeness."
- **PydanticAI-ifying the harness.** #1925's PydanticAI half is for *non-harness* LLM calls; harness
  sessions stay raw CLI subprocesses (two-transport rule). Don't blend them in this PR.
- **Reviving the idle-sweeper for the harness path.** The sweeper solves a persistent-client staleness
  problem the `claude -p` subprocess model does not have. Delete it; do not "generalize" it.
- **Deleting the `_handle_steering` `else` body along with the SDK arm.** Only the `if client:` arm is
  SDK-only; the `else` Redis re-push is the live CLI-harness steering path and MUST survive. Prune the
  arm, keep the fallback. Confirm each remaining `_active_clients` reader (the `agent/__init__.py`
  exports) is SDK-only before removing; do not leave a half-removed registry with a lone accessor.
- **Rewriting the whole `sdk_client.py`.** Extract the harness path and delete the dead path; do not
  re-architect the surviving helper functions (`build_harness_turn_input`, `verify_harness_health`)
  beyond what the seam requires.
- **Deferring the SDK deletion to #1925.** The deletion IS this PR's scope — folding it in is what lets
  #1925 lose its harness half. Do not split it out.

## Risks

### Risk 1: `--json-schema` semantics under stream-json differ from expectations
**Impact:** the routing rewrite stalls if the validated object is unavailable in the event stream or
degrades latency.
**Mitigation:** Task 2.1 is a standalone empirical probe BEFORE the routing rewrite. If unusable, ship
the seam + normalized events + SDK deletion and keep prefix routing; schema routing moves to a
follow-up. Schema routing is **separable by construction** — Task 2.3 depends only on the seam
(Task 2.2), touches disjoint files (`router.py`, `prime-pm-role.md`, the schema-routing tests), and can
be lifted into its own PR without reworking 2.2. The default remains one PR (the `file_paths` slot that
closes #1802 rides on the schema), but the split is the pre-authorized fallback the moment Task 2.1
finds the flag unusable OR review deems the combined diff too large — no re-plan needed.

### Risk 2: The extraction destabilizes the hottest path in the system
**Impact:** every production session flows through this code; a subtle env/argv drift breaks all
sessions at once.
**Mitigation:** the byte-equivalence golden test gates the extraction; per-commit revertibility;
`/do-deploy` rolls one machine first; crash auto-resume (#1917) is the safety net.

### Risk 3: The SDK deletion removes a symbol something still imports at runtime
**Impact:** an `ImportError` on worker/bridge start from a missed reference to `ValorAgent`,
`get_active_client`, `idle_sweeper`, or `claude_agent_sdk`.
**Mitigation:** grep gates in Verification (`grep -rn` for each deleted symbol and the SDK import across
`agent/ worker/ bridge/ tools/ scripts/`); the three known couplers (`agent/__init__.py`,
`worker/__main__.py`, `agent/health_check.py`) are explicit deletion tasks; full suite + a live worker
start on this machine before merge.

### Risk 4: Resume-id behavior is version-dependent across machines
**Impact:** simplifying capture-at-init against 2.1.204 breaks on a machine running older claude.
**Mitigation:** keep re-capture as assert-and-alarm (log + Sentry on `observed id != expected id`)
rather than deleting it; `claude_version` is already persisted per session for correlation.

## Race Conditions

### Race 1: Resume-handle persistence vs. crash (existing, re-verified through the seam)
**Location:** `runner.py` `_on_harness_init` → `persist_resume_scalars`.
**Trigger:** process dies between turn start and handle persistence.
**Data prerequisite:** `claude_session_uuid`/resume handle persisted at first sight of the init event,
before turn completion.
**State prerequisite:** crash auto-resume reads only persisted scalars.
**Mitigation:** the adapter MUST emit the normalized `session.started{handle}` event as its first event;
the runner persists on receipt — preserving the persist-at-init contract through the seam.

### Race 2: Schema temp-file / inline-arg lifetime vs. spawned process
**Location:** new schema-arg assembly in `harness/claude.py`.
**Trigger:** a per-turn schema written to scratchpad and cleaned up while the subprocess still reads it,
or two concurrent sessions sharing a path.
**Data prerequisite:** the schema must exist for the subprocess's full lifetime and be per-session
unique.
**Mitigation:** claude takes **inline JSON** for `--json-schema` (no file) — the primary path has no
temp-file race at all. Should a file ever be needed, write once per session under the session's own
directory and delete on session end, never per-turn temp churn.

### Race 3: `_active_clients` teardown vs. deletion
**Location:** the deletion itself — `_active_clients` accessors removed while a hypothetical reader
runs.
**Trigger:** none in practice — the registry is only populated by the dead `ValorAgent` loop, which is
removed atomically in the same PR.
**Mitigation:** delete producer and all consumers in one commit; grep gate proves no surviving reader.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1925] The PydanticAI / non-harness-LLM-call half of the `claude_code_sdk` removal —
  this PR completes only the **harness** half (the SDK deletion); the non-harness transport ships under
  #1925.
- [SEPARATE-SLUG #1370] Outbound send-path canonicalization — this plan produces the routed payload
  (including `file_paths`); #1370 owns delivery-path consolidation.
- [SEPARATE-SLUG #2001] The codex dev-lane adapter (`harness/codex.py`) — Phase 3. Hard-blocked on this
  adapter and additionally needs the codex CLI installed plus a one-time human OAuth step per machine;
  ships under its own plan.
- [EXTERNAL] Removing the prefix-regex routing fallback — deferred by owner decision (2026-07-10) to a
  telemetry-driven follow-up filed one week after schema routing lands (owned by Phase 3's acceptance);
  the fallback stays in this PR as a runtime guard.
- Automatic harness-selection policy — deliberately left open (carried in #2001); only claude ships here.

## Update System

- **No update-script changes required for this phase** — the seam extraction, SDK deletion, and schema
  routing are internal; existing launchd plists and env are untouched.
- **Dependency change to propagate:** none. Per the build-time scope correction (Freshness Check), the
  `claude-agent-sdk==0.2.116` dependency in `pyproject.toml` **stays** — `agent/health_check.py` and
  `agent/hooks/*.py` still import SDK hook-config types outside the deleted path. Only the
  `claude_agent_sdk` import inside `agent/sdk_client.py` is removed; no `/update` action needed.
- **Popoto migration:** none. No model fields are added or removed (`claude_session_uuid` keeps its name,
  meaning generalized to "resume handle"). No `MIGRATIONS` entry required; state this in the PR.

## Agent Integration

- **No new MCP servers and no `.mcp.json` changes.**
- **PM prime contract change (agent-facing):** `.claude/commands/roles/prime-pm-role.md` moves from
  "emit `[/user]`/`[/complete]` prefix tokens" to "your final message is schema-validated
  (`{route, message, file_paths?}`)" — same PR, no parallel conventions. This is the agent's routing
  contract, so it is an agent-integration change even though no new tool is registered.
- **#1802 delivered here:** the `file_paths` schema slot gives the PM a file-capable send path natively
  (screenshots/images, not just prose + links). Confirmed and closed at this PR's merge; no separate
  build.
- **Integration test:** `tests/integration/test_headless_probe_e2e.py` (UPDATE) exercises the seam
  end-to-end; a live one-session probe on this machine before deploy verifies the agent still completes
  a real turn through the adapter with schema routing.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/harness-adapter.md` — the seam: `HarnessAdapter` protocol, `TurnRequest`/
  `TurnResult`, normalized `TurnEvent` model, schema routing, the resume-handle contract, and the
  claude adapter's specifics. (`HarnessCapabilities` is deferred to Phase 3 / #2001 — out of scope here.)
- [ ] Update `docs/features/headless-session-runner.md` — routing section (schema replaces prefix
  tokens), resume-id findings, the adapter extraction, and the removal of the SDK path.
- [ ] Update `docs/features/session-lifecycle.md` — resume-handle generalization of
  `claude_session_uuid`.
- [ ] Add `docs/features/README.md` index entry for `harness-adapter`.
- [ ] Note in the PR (and on #1925) that the `claude_code_sdk` harness half is deleted here; remove any
  `docs/features/*` prose that documents the `ValorAgent`/`get_agent_response_sdk`/idle-sweeper SDK path.

### Inline Documentation
- [ ] Docstrings on the `HarnessAdapter` protocol and the claude adapter (contract: first-event handle
  emission, `exit_reason` taxonomy, schema-vs-fallback routing).

## Success Criteria

- [ ] `agent/session_runner/harness/{base,claude,events}.py` exist; the golden argv/env test proves the
  extraction is byte-identical, and behavioral-parity fixtures prove the final argv string (first-turn +
  resume), `_store_claude_session_uuid` firing, and the #1980 retry-without-`--resume` branch are all
  preserved.
- [ ] `TurnResult` is the runner's return type; `exit_reason` uses #2004's `ExitReason` StrEnum (no
  parallel taxonomy); the "no raw exit-reason literals outside router" verification still passes.
- [ ] PM routing driven by `--json-schema` with prefix-regex fallback emitting `schema_routing_fallback`
  telemetry, and an alert threshold fires when the fallback rate exceeds 5% over a rolling 1h window;
  `[/user]` teaching absent from `prime-pm-role.md`.
- [ ] `file_paths` carried through the schema onto the canonical delivery path; #1802 verified and closed
  at merge.
- [ ] The dead SDK path is gone: `ValorAgent`, `get_agent_response_sdk`, `_active_clients` (+ accessors),
  `worker/idle_sweeper.py` and its wiring, and the `claude_agent_sdk` import from `agent/sdk_client.py`
  are all deleted; no dangling references (grep gates green). The `claude-agent-sdk` **dependency
  itself stays** in `pyproject.toml` (build-time scope correction: live hook-type consumers outside
  this path).
- [ ] Resume-id behavior empirically recorded; the `_claude_session_id` reassignment simplified to
  assert-and-alarm (or kept if forked), while `_transcript_path` is still set on every init event
  (mid-turn-preempt retargeting preserved).
- [ ] A live eng session completes a real turn end-to-end through the adapter with schema routing on this
  machine before deploy.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (probes)** — Name: probe-builder — Role: Task 2.1 empirical probes (`--json-schema`
  runtime + resume-id) — Agent Type: builder — Domain: async — Resume: true
- **Builder (harness-seam)** — Name: seam-builder — Role: extract the seam, normalized events, golden
  test, and delete the dead SDK path — Agent Type: builder — Domain: async — Resume: true
- **Builder (schema-routing)** — Name: routing-builder — Role: schema routing, prime-pm-role rewrite,
  `file_paths` wiring — Agent Type: builder — Resume: true
- **Validator** — Name: phase-validator — Role: verify Verification rows + success criteria; live
  one-session probe — Agent Type: validator — Resume: true
- **Documentarian** — Name: docs-writer — Role: Documentation section tasks — Agent Type: documentarian
  — Resume: true

## Step by Step Tasks

### 2.1 Empirical probes (schema + resume-id)
- **Task ID**: p2-probes
- **Depends On**: none (entry gates #1999/#2004 already satisfied)
- **Informed By**: spike-1 (flag exists; runtime semantics unverified), spike-2 (id reuse suspected)
- **Assigned To**: probe-builder
- **Agent Type**: builder
- **Parallel**: false
- Probe `--json-schema` under `--output-format stream-json`: where the validated object lands,
  invalid-output behavior, interaction with `--include-partial-messages`; record findings in this plan's
  Spike Results.
- Two-turn `--resume` probe: record whether the session id is stable on 2.1.204; record `--fork-session`
  behavior.
- STOP and update plan tasks if either probe invalidates the approach (Risk 1/2 mitigations).

### 2.2 Extract HarnessAdapter seam + delete dead SDK path (behavior-preserving)
- **Task ID**: p2-extract-and-delete
- **Depends On**: p2-probes
- **Validates**: tests/unit/session_runner/test_harness_argv_golden.py (create), tests/unit/session_runner/,
  tests/integration/test_harness_resume.py
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Domain**: async
- **Parallel**: false
- Create `agent/session_runner/harness/{base,claude,events}.py`; move argv/env assembly + stream-json
  parsing from `get_response_via_harness` (2339-2803) into `claude.py`; golden test asserts byte-identical
  argv/env pre/post.
- Runner consumes normalized `TurnEvent`s; adapter emits `session.started{handle}` first (Race 1).
  `TurnResult.exit_reason` uses #2004's `ExitReason` enum. Note T2.4 delivered on the program plan.
- Apply spike-2 finding to the `_claude_session_id` reassignment only (assert-and-alarm or retained
  re-capture); keep `_transcript_path` retargeted on every init event (mid-turn-preempt rationale).
- **Delete the dead SDK path in the same PR:** `ValorAgent` (1511-2338), `get_agent_response_sdk`
  (3355-end), `_active_clients` + `get_active_client`/`get_all_active_sessions` (59, 656, 666, 1771,
  2012), `worker/idle_sweeper.py` + its `worker/__main__.py:906-916` wiring; remove the `claude_agent_sdk`
  import (36-42) from `agent/sdk_client.py` only — the `claude-agent-sdk==0.2.116` dependency in
  `pyproject.toml:9` stays (build-time scope correction: hook-type consumers outside this path).
  In `agent/health_check.py::_handle_steering`, delete ONLY the dead `if client:` SDK arm + the
  `get_active_client` import/call — **KEEP the `else` Redis re-push body** (the live CLI-harness
  steering fallback) as the unconditional body. Shrink the `agent/__init__.py` exports. Scrub the stale
  `get_response_via_sdk` comment references (the harness path at `sdk_client.py:2709` and the
  `idle_sweeper` docstring name the deleted function under a wrong name). Run grep gates for zero
  dangling references. Comment on #1925 that its harness half is done here.

### 2.3 Schema routing + `file_paths` (#1802)
- **Task ID**: p2-schema-routing
- **Depends On**: p2-extract-and-delete
- **Validates**: tests/unit/session_runner/test_schema_routing.py (create), tests/unit/test_output_router.py,
  tests/unit/session_runner/ router suites
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Define the PM turn schema `{route, message, file_paths?}`; pass via `--json-schema`; route on
  `structured_output.route`.
- Demote prefix regex to fallback + `schema_routing_fallback` telemetry; keep compliance nudge as final
  backstop.
- Update `.claude/commands/roles/prime-pm-role.md` (and teammate prime if applicable) to the schema
  contract; remove prefix-token teaching.
- Wire `file_paths` onto the canonical outbox path (coordinate with #1370); verify #1802's acceptance and
  post the close note on #1802.

### 2.4 Phase validation + live probe
- **Task ID**: p2-validate
- **Depends On**: p2-schema-routing
- **Assigned To**: phase-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; grep gates for deleted symbols; a live eng session end-to-end on this
  machine before deploy.
- **#1802 real file-delivery assertion (not plumbing):** #1802 was closed once on a plumbing-only
  assertion — do not repeat that. Drive one real turn whose schema output names an actual file in
  `file_paths`, and assert the file is **delivered as an attachment** on the transport (the outbox
  record carries the file / the recipient receives it), not merely that `file_paths` reached the
  router. Capture the delivered-artifact evidence in the validation report before posting the #1802
  close note.

### N-1. Documentation
- **Task ID**: document-feature
- **Depends On**: p2-validate
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section checklist.

### N. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: phase-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; confirm all Success Criteria; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Heartbeat suite green (entry gate) | `pytest tests/unit/test_session_heartbeat_progress.py -q` | exit code 0 |
| Golden argv test exists | `pytest tests/unit/session_runner/test_harness_argv_golden.py -q` | exit code 0 |
| Schema routing test exists | `pytest tests/unit/session_runner/test_schema_routing.py -q` | exit code 0 |
| No prefix-token teaching in PM prime | `grep -c "\[/user\]" .claude/commands/roles/prime-pm-role.md` | match count == 0 |
| No claude argv outside adapter | `grep -rn '"claude", "-p"' agent/ --include="*.py" \| grep -vc harness/` | match count == 0 |
| ValorAgent deleted | `grep -rn "class ValorAgent" agent/ --include="*.py"` | match count == 0 |
| get_agent_response_sdk deleted | `grep -rn "def get_agent_response_sdk" agent/ --include="*.py"` | match count == 0 |
| No SDK-response symbol refs (real or stale name) | `grep -rn "get_agent_response_sdk\|get_response_via_sdk" agent/ worker/ --include="*.py"` | match count == 0 |
| _active_clients registry deleted | `grep -rn "_active_clients" agent/ worker/ --include="*.py"` | match count == 0 |
| idle_sweeper deleted | `test -f worker/idle_sweeper.py && echo present \|\| echo gone` | output contains gone |
| claude_agent_sdk import gone from sdk_client.py | `grep -c "claude_agent_sdk" agent/sdk_client.py` | match count == 0 |
| claude-agent-sdk dependency retained (build-time scope correction — live hook-type consumers) | `grep -c "claude-agent-sdk" pyproject.toml` | match count == 2 (dep line + mcp comment) |
| No dangling get_active_client callers | `grep -rn "get_active_client\|get_all_active_sessions" agent/ worker/ bridge/ tools/ --include="*.py" \| grep -vc "session_runner/harness/"` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | war-room | `health_check.py:538-560` mischaracterized as the SDK-client liveness check; it is the sole steering-injection/delivery path for all CLI-harness (production) sessions | Technical Approach (SDK deletion) + Task 2.2 + Freshness Check + Rabbit Holes | Delete ONLY the dead `if client:` SDK arm + `get_active_client` import; KEEP the `else` Redis re-push body as the unconditional CLI-harness steering fallback; regression-test the re-push path |
| Concern | war-room | Golden argv/env test alone does not prove behavior preservation | Technical Approach (extraction) + Test Impact NEW + Success Criteria | Added behavioral-parity fixtures: final argv string (first-turn + resume), `_store_claude_session_uuid` firing, #1980 retry-without-`--resume` branch (+ no-retry-after-valid-`result`) |
| Concern | war-room | `_handle_init` capture-at-init has a second rationale (mid-turn preempt transcript retargeting) the plan dropped | Technical Approach + Definitions + Task 2.2 + Success Criteria | Keep `_transcript_path` set on every init unconditionally; gate ONLY the `_claude_session_id` reassignment behind the resume-drift check |
| Concern | war-room | `HarnessCapabilities` speculative with no consumer; schema routing separable — consider split | Solution Key Elements + Risk 1 + Documentation | Deferred `HarnessCapabilities` to Phase 3 / #2001; documented schema routing as separable-by-construction, pre-authorized PR split |
| Concern | war-room | #1802 was closed once on a plumbing assertion — need a real file-delivery assertion | Task 2.4 + Test Impact NEW | Task 2.4 drives a real file into `file_paths` and asserts attachment delivery on the transport, not plumbing; test asserts file-delivery behavior |
| Nit | war-room | Missing `schema_routing_fallback` alert threshold | Solution (Schema routing) + Success Criteria | Alert fires at >5% fallback rate over a rolling 1h window |
| Nit | war-room | `get_response_via_sdk` vs `get_agent_response_sdk` grep-gate name mismatch | Verification + Task 2.2 | Grep gate now catches both names; scrub stale `get_response_via_sdk` comments (`sdk_client.py:2709`, idle_sweeper docstring) |

---

## Open Questions

1. **PM turn schema shape.** The placeholder `{route: user|complete|continue, message: str,
   file_paths?: [str]}` — is `continue` the right third route value (vs. reusing the existing nudge
   semantics), and should `file_paths` carry captions/alt-text or bare paths (bearing on the #1370
   delivery contract)? Finalized in build after Task 2.1, but a preferred shape now saves a round.
2. **Fallback removal timing.** Owner decision defers prefix-regex-fallback removal to a Phase 3
   follow-up. Confirm this PR ships the fallback as a permanent-for-now runtime guard (no deprecation
   warning here).
