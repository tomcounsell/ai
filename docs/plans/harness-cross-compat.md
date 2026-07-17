---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/1996
last_comment_id:
---

# Harness Cross-Compatibility: Agent-Agnostic Headless Runner (claude -p / codex exec)

## Problem

The headless session runner (`agent/session_runner/`) is hard-wired to `claude -p`
specifics at every seam: argv construction lives in a 183 KB `agent/sdk_client.py`,
turn routing depends on the PM voluntarily emitting `[/user]`/`[/complete]` prefix
tokens plus a compliance-nudge loop, continuity depends on capture-at-init re-reading
of a forked session uuid every turn, and observability is raw claude stream-json.
A July 2026 comparison against OpenAI's `codex exec` (CLI 0.144.1) showed the two
tools have converged on the same headless shape — one subprocess per turn,
resume-by-id, JSONL events, schema-enforced final output — but our runner cannot
express that shape generically. We cannot try `codex exec` as a session substrate
without forking the runner, and we are not even using the codex-parity features
our own CLI has grown (`--json-schema` structured output shipped in claude 2.1.x
and we still route by prefix tokens).

**Current behavior:**
- One harness, one code path: `claude -p` argv is assembled inline in
  `sdk_client.py:2261-2510`; no seam exists to swap the subprocess.
- PM output routing is prompt-discipline-based (`router.py` prefix regex +
  wrap-up guard + compliance nudges) rather than schema-guaranteed.
- Resume machinery assumes every `--resume` forks a new session id ("Race 5"
  capture-at-init in `role_driver.py:210-254`) — behavior that current claude
  CLI help text suggests no longer exists by default (`--fork-session` is now
  the opt-in).
- Session-runner cleanup debt is accumulating in the same seam: 3 open bugs
  live inside the resume/liveness path (#1979, #1983, #1855), and 4 open issues
  are premised on deleted PTY-era architecture.

**Desired outcome:**
- A tidied session-runner surface with stale issues closed/edited and the 3
  in-seam bugs fixed (Phase 1).
- One `HarnessAdapter` seam through which the runner drives any turn-based
  headless CLI: normalized turn events, schema-enforced final message replacing
  prefix-token routing, verified/simplified resume semantics (Phase 2).
- `codex exec` usable as an opt-in **dev-lane executor inside eng sessions**
  behind that seam — every top-level session connected to bridge messaging stays
  claude-only (owner decision 2026-07-10) — with capability degradation handled
  explicitly (Phase 3; manual selection only, policy question carried open in #2001).

**Phase issues:** Phase 1 → #1999, Phase 2 → #2000, Phase 3 → #2001 (each feeds
its own `/do-plan`; this document is their shared design input, and #2001's
dev-lane framing supersedes any whole-session wording below where they conflict).

## Freshness Check

**Baseline commit:** `2f659c7ff9f331f36d2fe92d0df2d5a9b31b089a`
**Issue filed at:** n/a — plan initiated from a live conversation (comparison audit performed 2026-07-10, same day as this plan); a tracking issue is created by this plan.
**Disposition:** Unchanged (all claims verified against working tree at baseline, same day)

**File:line references re-verified (from the comparison audit, hours old):**
- `agent/sdk_client.py:2261-2273` — `_HARNESS_COMMANDS` base argv — holds.
- `agent/session_runner/role_driver.py:210-254` — capture-at-init uuid re-capture — holds.
- `agent/session_runner/router.py:46` — `PREFIX_TOKEN_RE` prefix routing — holds.
- `config/settings.py:403-421` — `SessionRunnerSettings.pm_model`/`dev_model` declared, zero readers — holds (deferred to #1968, see No-Gos).

**Reconciliation update (2026-07-10, later same day):** the resilience-simplification
program landed in parallel (`resilience-simplification-three-tier.md` draft;
`sdlc-run-ownership-merge-enforcement.md` → #2003; `resilience-hygiene-sweep.md` → #2004,
both Ready and in flight) and three PRs merged (#2006 fixing #1979, #2005 for #1834,
#1998 for #2003's T1.7 rung). Phase 1's fold-in code work is dissolved: #1979 shipped,
#1983 is absorbed by #2004, #1855's disposition belongs to #1926's pruning pass. #1818's
disposition changes CLOSE → EDIT (the program cites it as the substrate-durability anchor).
Phase 2 sequences behind #2004 (same `session_runner` files), adopts its `ExitReason`
StrEnum as the `TurnResult.exit_reason` taxonomy, and delivers program item T2.4 via
`TurnResult`. Scope-revision sections on #1999 and #2000 are authoritative where this
document's phase details conflict.

**Active plans in `docs/plans/` overlapping this area:**
- `consolidate_delivery_paths.md` (#1370, Planning) — owns the *outbound outbox send paths* downstream of the runner. This plan changes how the PM's final message is *produced and classified* (schema vs prefix tokens), not how it is delivered. Coordinate: the schema payload must land on the same canonical outbox path #1370 designates. Not a blocker.
- `conversational-dev-fanout.md` (#1541, Planning) — premised on the removed `session_type="dev"`; the Phase 1 issue survey recommends closing it (see Step 1.1).
- `centralize_config_magic_literals.md` (#1968, Ready) — its Part-2 dead-field inventory is the natural home for the `pm_model`/`dev_model` deletion (Step 1.2 edits the issue; this plan does not delete them).

## Prior Art

- **#1925 (open, KEEP)**: Remove `claude_code_sdk`; two-transport split (claude -p harness for sessions, PydanticAI for non-harness LLM calls). The harness-abstraction issue proper. Phase 2's adapter extraction is the natural vehicle for the harness half of #1925 — coordinate, don't duplicate.
- **#1924 / #1928 (closed)**: Single Opus PM session + resumable dev subagent decided and spiked; continuation survives `--resume`. Fixed architecture this plan must preserve under any harness.
- **#1917 (merged, PR #1993)**: Crash auto-resume revival — deterministic floor, progress-fields classifier. Consumes the same resume scalars Phase 2 touches.
- **#1926 (open, KEEP)**: Post-teardown scar-tissue removal (happy-path liveness + Sentry). Umbrella for liveness simplification; Phase 1's fold-in fixes (#1983, #1855) execute inside its direction.
- **Postmortem d451c1bd**: PTY/interactive-TUI thesis retired; headless `claude -p` for all roles. This plan extends that decision to "headless *anything* behind one seam."
- **Issue survey (2026-07-10, this plan's recon)**: 38 open issues; 22 session-runner-relevant; classifications embedded in Step 1.1/1.2 below. No open issue mentions codex or multi-provider substrates.

## Research

External research performed 2026-07-10 (codex exec documentation sweep + local CLI verification).

**Queries used:**
- codex exec non-interactive docs, resume semantics, output schemas, sandboxing, auth (developers.openai.com → learn.chatgpt.com redirect chain; github.com/openai/codex; empirical flag-test gist at v0.114.0)
- `claude --help` local verification (v2.1.204)

**Key findings:**
- `codex exec resume <SESSION_ID>` / `--last` appends to the same rollout JSONL — **stable session id across resumes** (learn.chatgpt.com/docs/non-interactive-mode; deepwiki.com/openai/codex/4.2). Canonical loop: `exec --json` → parse `thread.started.thread_id` → N× `exec resume <id> --json`.
- `--json` emits NDJSON ThreadEvents: `thread.started` → `turn.started` → `item.*` (agent_message, reasoning, command_execution, file_change, mcp_tool_call, error) → `turn.completed{usage}` | `turn.failed{error}` (takopi.dev exec-json cheatsheet). This is the normalization target for our event model.
- `--output-schema <file>` enforces a JSON Schema on the final message (strict mode: `additionalProperties:false`, all properties required). `-o/--output-last-message <file>` writes the final message to a file.
- Sandbox: exec defaults **read-only**; `workspace-write`, `danger-full-access`; approvals silently downgrade to `never` headlessly — approval-needing actions fail. `--full-auto` is deprecated and overrides explicit `--sandbox` (never combine).
- Auth precedence: `CODEX_API_KEY` (exec-only) > `~/.codex/auth.json` (ChatGPT-plan, seedable) > `OPENAI_API_KEY`. ChatGPT-plan login is a one-time human OAuth step per machine.
- Gotchas: `--ephemeral` sessions silently un-resumable (resume creates a NEW session, no error); no headless compaction (`/compact` is TUI-only — context grows monotonically per resume); `-a/--ask-for-approval` is a global flag (before `exec`); no mid-turn input injection (kill-and-resume is the only steer, same as ours).
- Local claude CLI (2.1.204) already exposes `--json-schema`, `--session-id <uuid>`, `--fork-session`, `--from-pr` — see Spike Results.

## Spike Results

### spike-1: claude -p structured output exists today
- **Assumption**: "claude -p has no codex `--output-schema` equivalent"
- **Method**: code-read (`claude --help`, v2.1.204 on this machine)
- **Finding**: **Assumption false.** `--json-schema <schema>` exists: "JSON Schema for structured output validation," inline-JSON argument. Phase 2 can adopt schema routing without waiting on any CLI feature.
- **Confidence**: high that the flag exists; medium on exact runtime semantics under `--output-format stream-json` (where the validated object lands in the event stream, retry-on-invalid behavior). Build task 2.1 verifies empirically before wiring.
- **Impact on plan**: Phase 2's "replace prefix-token routing with output schema" is buildable now; the prefix regex demotes to a fallback.

### spike-2: --resume may now reuse the session id
- **Assumption**: "every claude -p --resume forks a new session id (Race 5 premise)"
- **Method**: code-read (`claude --help`)
- **Finding**: `--fork-session`: "When resuming, create a new session ID **instead of reusing the original**." This wording implies default `--resume` now *reuses* the id. Our capture-at-init machinery (`role_driver.py:235-254`) may be compensating for behavior that no longer exists on 2.1.204.
- **Confidence**: medium — help text only; the runner also persists `claude_version` per session, suggesting version-dependent behavior was already suspected.
- **Impact on plan**: Task 2.2 runtime-verifies with a two-turn probe (compare `system/init` session_id across a resume). If stable: capture-at-init simplifies to an assertion + drift alarm; if forked: keep re-capture inside the claude adapter, hidden behind the seam (codex adapter never needs it).

### spike-3: codex CLI availability
- **Assumption**: "codex is installed where sessions run"
- **Method**: code-read (`which codex`)
- **Finding**: **Not installed** on this machine.
- **Confidence**: high
- **Impact on plan**: Phase 3 gains an install + auth prerequisite and an Update System step; the adapter must fail fast with an actionable error when the binary is absent.

## Data Flow

Target flow after Phase 2 (harness-agnostic; today's flow is identical minus the adapter seam and with prefix-token routing at step 5):

1. **Entry point**: Telegram message → bridge enqueues `AgentSession` → worker picks up → `session_executor` builds `SessionRunner`.
2. **SessionRunner** (`runner.py`): per-turn loop — drain steering, compose message, call `HarnessAdapter.run_turn(TurnRequest)`.
3. **HarnessAdapter** (new seam): builds argv for its CLI (`claude -p --json-schema ... --resume <handle>` | `codex exec resume <handle> --json --output-schema ...`), spawns process group, parses native events into **normalized TurnEvents**, returns `TurnResult{resume_handle, structured_output | final_text, events, usage, exit_reason}`.
4. **Runner telemetry**: normalized events stamp liveness (`liveness.py`), emit `turn_start`/`turn_end` session events, persist `resume_handle` at first sight (crash auto-resume floor unchanged).
5. **Routing**: `TurnResult.structured_output.route` (`user` | `complete` | `continue`) drives routing directly; prefix regex (`router.py`) remains as fallback for schema-validation failure only.
6. **Output**: `adapter.py` (`SessionRunnerAdapter`) delivers via the transport-keyed callback / outbox exactly as today (coordinating with #1370's canonical-path decision).

## Architectural Impact

- **New dependencies**: none in Phases 1-2; Phase 3 adds the `codex` CLI binary (npm/brew, opt-in per machine) — no Python package.
- **Interface changes**: `get_response_via_harness` (sdk_client) is superseded by `HarnessAdapter.run_turn` for the runner path; `AgentSession` gains nullable dev-lane fields (dev-harness opt-in + codex `thread_id`, Phase 3) and `claude_session_uuid` is generalized in meaning to "resume handle" (field name kept — no migration; see Popoto note in Update System).
- **Coupling**: decreases — runner stops importing claude-specific parsing; all CLI knowledge lives in one adapter module per harness.
- **Data ownership**: unchanged — session_runner still owns subprocess lifecycle and the single authoritative liveness signal (per the single-authoritative-liveness rule).
- **Reversibility**: Phase 2 is a refactor with byte-equivalent argv as the acceptance bar (easy revert per commit); Phase 3 is additive and gated behind an opt-in field defaulting to `claude`.

## Appetite

**Size:** Large (phased: P1 Medium, P2 Large, P3 Medium — each phase ships independently and is a legitimate stopping point)

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (open-question answers before P2; selection-policy decision before P3)
- Review rounds: 2+ (one per phase PR)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| claude CLI ≥ 2.1.x with `--json-schema` | `claude --help \| grep -q -- --json-schema` | Phase 2 schema routing |
| codex CLI installed (Phase 3 only) | `which codex` | Phase 3 substrate |
| codex auth present (Phase 3 only) | `test -f ~/.codex/auth.json \|\| test -n "$CODEX_API_KEY"` | Phase 3 headless auth |

Run via `python scripts/check_prerequisites.py docs/plans/harness-cross-compat.md`. Phase 3 rows are expected to fail until its install step runs.

## Solution

### Key Elements

- **Phase 1 — Cleanup & issue hygiene**: execute the 2026-07-10 issue-survey verdicts (close 4, edit 3), fix the 3 fold-in bugs living in the resume/liveness seam, and leave the runner surface green and honest before refactoring it.
- **Phase 2 — HarnessAdapter seam**: extract the runner's claude -p knowledge into a `ClaudeHarnessAdapter` behind a small protocol; normalize turn events to a codex-ThreadEvent-shaped model; move PM routing from prefix tokens to `--json-schema` structured output; verify and simplify resume semantics.
- **Phase 3 — CodexHarnessAdapter (dev-lane only)**: second implementation of the same protocol driving `codex exec --json` / `exec resume`, wired as an opt-in executor for dev work *inside* eng sessions — the PM (claude, top-level) drives an external codex dev session instead of the in-turn `dev` subagent. Explicit capability degradation (no hooks, no compaction, sandbox mapping); manual selection only.

### Flow

Issue survey verdicts → **Phase 1 PR** (fixes + hygiene) → open questions answered → **Phase 2 PR** (adapter + schema routing, claude-only, behavior-preserving) → selection policy decided → **Phase 3 PR** (codex adapter + opt-in wiring) → probe E2E on a real session per harness.

### Technical Approach

**Phase 1 (cleanup):**
- Issue hygiene per the survey table (Step 1.1/1.2): CLOSE #1721, #1541, #1818, #1336 with evidence comments; EDIT #1968 (add `pm_model`/`dev_model` to dead-field inventory), #1802 (rewrite root cause for `agent/session_runner/adapter.py` + prime-pm-role, Option A `<<FILE:>>` shape), #1267 (reframe for eng/teammate taxonomy).
- Fold-in fixes: **dissolved by the 2026-07-10 reconciliation** — #1979 shipped (PR #2006, epoch-scoped delivery guard), #1983 is absorbed by #2004's SessionEvidence unification, #1855's disposition is owned by #1926's pruning pass (Phase 1 only verifies that decision has a home). #1818 changes CLOSE → EDIT: prune its stale PTY-era items but keep it as the substrate-durability anchor the resilience program references.
- Acceptance: issue tracker reflects headless reality; no code changes remain in Phase 1 (appetite: Small). Heartbeat-suite-green becomes Phase 2's entry gate, delivered by #2004.

**Phase 2 (agent-agnostic seam, claude-only, no behavior change except routing):**
- New package `agent/session_runner/harness/`: `base.py` (protocol: `TurnRequest`, `TurnResult`, `TurnEvent`, `HarnessCapabilities{supports_hooks, supports_compaction, supports_schema, id_stability}`), `claude.py` (adapter wrapping today's argv assembly + stream-json parsing, extracted from `sdk_client.py`), `events.py` (normalized event model, field names deliberately aligned with codex ThreadEvents: `turn.started`, `item.*`, `turn.completed{usage}`, `turn.failed{error}`).
- Byte-equivalence gate: the extracted claude adapter must produce the identical argv/env as `sdk_client.py:2261-2510` today (golden test), so the extraction PR is provably behavior-preserving before any semantics change.
- Schema routing: define the PM turn schema (`{route: user|complete|continue, message: str, file_paths?: [str]}` — placeholder shape, finalized in build) written to a temp file/inline arg per turn; pass `--json-schema`; `router.py` becomes: prefer validated object, fall back to prefix regex, then compliance nudge. Prime-pm-role prose about `[/user]`/`[/complete]` updates in the same PR (no parallel-run migration — the schema path becomes the status quo; fallback exists only as a runtime guard, not a second convention).
- Resume verification: two-turn probe test records whether 2.1.204 `--resume` reuses the session id. Stable → capture-at-init becomes assert-and-alarm; forked → logic stays, but privately inside `claude.py`.
- Hook edge (`hook_edge.py`) declared a claude capability: adapters expose `turn_end` reconciliation behind the protocol; codex path later uses `turn.completed` alone.
- Coordination with #1925: this extraction IS the harness half of #1925; comment on the issue linking this plan; do not remove `claude_code_sdk` here.

**Phase 3 (codex adapter, opt-in dev lane — #2001 supersedes on conflict):**
- Scope (owner decision 2026-07-10): codex applies ONLY to dev work inside eng sessions. Top-level bridge-connected sessions (PM, teammate) are claude-only, unconditionally. The PM routes dev work to an external codex session instead of spawning the in-turn `dev` Agent-tool subagent when the session is flagged.
- `harness/codex.py`: first turn `codex exec --json --output-schema <schema> -C <worktree> <prompt>`, capture `thread.started.thread_id` as the resume handle; later turns `codex exec resume <thread_id> --json ...`. Never pass `--ephemeral`. Sandbox default `workspace-write` inside the dev worktree; `danger-full-access` requires an explicit setting. Approvals pinned `never` (headless reality). Version gate at adapter init, recorded like `claude_version`.
- Dev priming: no slash commands in codex — prime via the first-turn prompt body (reuse the dev rails/persona content as prompt text) and/or repo `AGENTS.md`; decision recorded in build.
- Continuity: the codex `thread_id` persists alongside the existing resume scalars so the dev lane survives PM turns and process restarts — the same guarantee `dev_agent_id` provides for the claude dev subagent today. Exact PM-facing mechanism (persona instruction + wrapper tool vs. runner-managed) is a #2001 planning decision.
- Selection — manual only (owner decision 2026-07-10): per-session opt-in at creation (e.g. `valor-session create --dev-harness codex`), immutable once dev work starts (resume handles are not portable across harnesses). Eventual selection policy (per-project `projects.json` config, heuristics, or permanent manual) is deliberately left open — carried in #2001, not resolved by this plan.
- Auth (owner decision 2026-07-10): subscription-first — `~/.codex/auth.json` (ChatGPT-plan OAuth, one-time human login per machine) preferred, `CODEX_API_KEY` from the vault `.env` as backup; adapter fails fast with an actionable message when neither is present.
- Required follow-up filing (owner decision 2026-07-10): as part of Phase 3's acceptance, file a follow-up issue to evaluate removing Phase 2's prefix-regex routing fallback, scheduled one week after schema routing landed, decided on `schema_routing_fallback` telemetry.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Adapter subprocess failures (binary missing, non-zero exit, malformed JSONL line) must surface as typed `TurnResult.exit_reason` values with a session event — test each; no bare `except: pass` in the new `harness/` package.
- [ ] Schema-validation failure path: test that an invalid/missing structured output falls back to prefix-regex routing and emits a `schema_routing_fallback` telemetry event (observable, not silent).

### Empty/Invalid Input Handling
- [ ] Empty final message from either harness: test the wrap-up guard still fires (no silent loop) — this is the existing `_run_wrapup_guard` contract, re-asserted through the adapter seam.
- [ ] Empty/whitespace steering messages: existing `_steer_is_substantive` behavior unchanged — regression test through the seam.
- [ ] codex `turn.failed` and stream-level `error` events: test both map to a failed turn with the error message preserved in telemetry, never to an empty "success."

### Error State Rendering
- [ ] Harness-unavailable (codex not installed / not authed): test the session fails with an operator-actionable message routed through the normal delivery path (never a raw traceback to Telegram — persona rule).
- [ ] Schema fallback exhaustion (invalid output AND no prefix token): test the compliance nudge → needs-attention path still terminates.

## Test Impact

- [ ] `tests/unit/session_runner/*` (role_driver/runner/router suites) — UPDATE: route through the `HarnessAdapter` seam; router tests split into schema-first + regex-fallback cases
- [ ] `tests/unit/test_output_router.py`, `test_output_router_compaction_guard.py` — UPDATE: routing input becomes structured object with regex fallback
- [ ] `tests/unit/test_harness_streaming.py`, `test_harness_retry.py`, `test_harness_stale_uuid_result_preservation.py`, `test_harness_token_capture.py`, `test_harness_context_usage_log.py`, `test_harness_thinking_block_sentinel.py`, `test_harness_oom_backoff.py`, `test_sdk_client_harness_counters.py` — UPDATE: point at the extracted claude adapter; stale-uuid test may become the spike-2 drift alarm test
- [ ] `tests/unit/test_session_heartbeat_progress.py` — no change here: the 3 red tests are fixed by #2004 (which absorbed #1983); green suite is Phase 2's entry gate
- [ ] `tests/integration/test_harness_resume.py`, `test_harness_env_pm_injection.py`, `test_harness_no_op_contract.py` — UPDATE: resume-handle semantics + env assembly move behind the adapter
- [ ] `tests/integration/test_runner_dispatch_e2e.py`, `test_headless_probe_e2e.py`, `test_runner_teardown_reap.py` — UPDATE: assert seam-level contracts; teardown-reap contract unchanged but exercised per adapter
- [ ] NEW: `tests/unit/session_runner/test_harness_argv_golden.py` — byte-equivalence golden test for the Phase 2 extraction
- [ ] NEW (Phase 3): codex adapter unit tests with recorded JSONL fixtures; opt-in E2E behind `which codex`

## Rabbit Holes

- **Emulating the in-turn dev subagent on codex.** The in-turn continuable `dev` Agent-tool subagent is a claude-native capability with no codex equivalent. Phase 3's design is PM-drives-*external*-codex-dev (which codex's stable thread_id handles natively) — do not attempt to recreate sidechain/Agent-tool semantics, nested subagents, or hook-based turn signaling inside the codex lane.
- **Building a universal event superset.** Normalize only what the runner consumes (liveness stamps, turn boundaries, usage, final output, error). Do not model every claude/codex event type "for completeness."
- **PydanticAI-ifying the harness.** #1925's PydanticAI half is for *non-harness* LLM calls; harness sessions stay raw CLI subprocesses (two-transport rule). Don't blend them.
- **Automatic harness-selection heuristics.** Explicitly TBD; anything beyond the manual per-session flag is out of scope (Open Question 1).
- **Headless compaction for codex.** Don't build transcript summarization/compaction infrastructure; the bounded-resume-count guard is the whole answer for now.
- **Chasing codex version churn.** Pin a minimum codex version, gate on `codex --version` at adapter init, and record it like `claude_version`; don't build per-version behavior tables.

## Risks

### Risk 1: `--json-schema` semantics under stream-json differ from expectations
**Impact:** Phase 2's routing rewrite stalls if the validated object is unavailable in the event stream, interacts badly with `--include-partial-messages`, or the model retries degrade turn latency.
**Mitigation:** Task 2.1 is a standalone empirical probe BEFORE the routing rewrite; if unusable, Phase 2 ships the seam + normalized events and keeps prefix routing (still a win), schema routing moves to a follow-up.

### Risk 2: Resume-id behavior is version-dependent across machines
**Impact:** Simplifying capture-at-init against 2.1.204 breaks on a machine running an older claude.
**Mitigation:** Keep re-capture code as an assert-and-alarm (log + Sentry when observed id ≠ expected id) rather than deleting it; we already persist `claude_version` per session for correlation.

### Risk 3: The extraction destabilizes the hottest path in the system
**Impact:** Every production session flows through this code; a subtle env/argv drift breaks all sessions at once.
**Mitigation:** Byte-equivalence golden test gates the extraction PR; phases ship as separate PRs; `/do-deploy` rolls one machine first per existing practice; crash auto-resume (#1917, now active-capable) is the safety net.

### Risk 4: codex sandbox/approval model rejects real work silently
**Impact:** Headless codex downgrades approvals to `never`, so an approval-needing action fails mid-task and the session looks wedged.
**Mitigation:** Map `turn.failed` + item-level errors to explicit needs-attention exits with the error text; default `workspace-write` inside worktrees to minimize approval surface; document the failure signature.

## Race Conditions

### Race 1: Resume-handle persistence vs. crash (existing, re-verified through the seam)
**Location:** `runner.py:623-655` (`_on_harness_init` → `persist_resume_scalars`)
**Trigger:** Process dies between turn start and handle persistence.
**Data prerequisite:** `claude_session_uuid`/resume handle persisted at first sight of the init/`thread.started` event, before turn completion.
**State prerequisite:** Crash auto-resume reads only persisted scalars.
**Mitigation:** Preserve persist-at-init through the adapter: adapters MUST emit the normalized `session.started{handle}` event as their first event, and the runner persists on receipt. Codex: `thread.started` is documented first-event, same guarantee.

### Race 2: Dev-lane harness switch mid-session
**Location:** new dev-harness opt-in consumption in `session_executor` / PM dev-routing.
**Trigger:** Eng session created with dev-lane A (claude subagent), flag mutated (or default changed) before a resume; a claude `dev_agent_id` fed to the codex adapter or a codex `thread_id` fed to Agent-tool continuation.
**Data prerequisite:** A dev resume handle is only meaningful to the executor that minted it.
**State prerequisite:** The dev-lane choice is immutable once dev work has started.
**Mitigation:** Executor resolves the dev harness ONCE at session start and persists it with the resume scalars; on resume, the persisted value wins over field/default; mismatch → discard the dev resume handle, PM starts a fresh dev lane (mirrors the existing invalid-scalar discard rule at `runner.py:418-422`).

### Race 3: Schema temp-file lifetime vs. spawned process
**Location:** new schema-arg assembly in `harness/claude.py` / `codex.py`.
**Trigger:** Per-turn schema written to scratchpad, cleaned up while the subprocess still reads it (or two concurrent sessions share a path).
**Data prerequisite:** Schema file exists for the full subprocess lifetime and is per-session unique.
**Mitigation:** claude takes inline JSON (no file); codex `--output-schema` requires a path — write once per session under the session's own directory, delete on session end, never per-turn temp churn.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1925] Removing `claude_code_sdk` / the PydanticAI split for non-harness calls — Phase 2 coordinates with (and advances) the harness half but the SDK removal ships under its own issue.
- [SEPARATE-SLUG #1968] Deleting dead `SessionRunnerSettings.pm_model`/`dev_model` — belongs to that issue's Part-2 dead-field inventory; Phase 1 edits the issue to include them.
- [SEPARATE-SLUG #1926] Broad liveness/Sentry scar-tissue removal — owns #1855's disposition (per the resilience program's pruning-pass assignment); the umbrella stays independent.
- [SEPARATE-SLUG #2004] Resilience hygiene sweep — owns #1983's fix, the `ExitReason` StrEnum, and the SessionEvidence unification; Phase 2 sequences behind it and consumes its enum.
- [SEPARATE-SLUG #1370] Outbound send-path consolidation — this plan produces the routed payload; #1370 owns delivery-path canonicalization.
- Codex for any top-level bridge-connected session (PM or teammate) — claude-only by owner decision (2026-07-10); codex is reachable exclusively through the eng dev lane.
- Dev-subagent *emulation* on codex (Agent-tool/sidechain semantics) — the external-executor design is the scope (see Rabbit Holes).
- Automatic harness-selection policy (cost/latency/capability heuristics) — deliberately left open by the owner; only the manual per-session flag ships in Phase 3, and the open question is carried in #2001.
- [EXTERNAL] codex ChatGPT-plan OAuth login on each machine — one-time human step per machine; the update system installs the binary and validates, a human completes login.

## Update System

- **Phase 1-2:** No update system changes required — internal refactor; existing launchd plists, env, and deps are untouched.
- **Phase 3:** `/update` (`scripts/update/run.py`) gains an opt-in step: install/upgrade the codex CLI (npm or brew) on machines whose `projects.json`/machine config opts in, then validate `codex --version` ≥ pinned minimum and warn (not block) when `~/.codex/auth.json` is absent. New settings fields (`SessionRunnerSettings.codex_*`: min version, sandbox default, max resumed turns — all env-overridable, marked provisional) propagate via `config/settings.py` defaults; no `.env` secret required unless a machine chooses `CODEX_API_KEY` (then: vault `.env` + `.env.example` placeholder + settings field per the secrets convention).
- **Popoto migration:** the new nullable dev-lane fields (dev-harness opt-in + codex `thread_id` resume scalar) need no migration code — Popoto ≥1.6.1 default-fills absent fields at lazy-load (issues #1099/#1172; see `docs/features/popoto-descriptor-pollution-ledger.md`, #2083). No `MIGRATIONS` entry required; state this in the PR description.

## Agent Integration

- No new MCP servers and no `.mcp.json` changes.
- Phase 3: `valor-session create` (`tools/valor_session.py`, existing `pyproject.toml` script) gains a dev-lane opt-in (e.g. `--dev-harness codex`, eng sessions only); the bridge needs no changes (resolution happens in the worker/executor, and top-level sessions never consult it).
- Integration test: a flagged eng session (on a codex-provisioned machine) completes dev work end-to-end; skipped via `which codex` guard elsewhere.
- Phase 2's routing change touches the PM prime (`.claude/commands/roles/prime-pm-role.md`) — the agent-facing contract moves from "emit `[/user]` prefix" to "your final message is schema-validated"; same PR, no parallel conventions.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/harness-adapter.md` — the seam: protocol, normalized events, capabilities, schema routing, per-harness notes (claude/codex), selection field
- [ ] Update `docs/features/headless-session-runner.md` — routing section (schema replaces prefix tokens), resume semantics findings, adapter extraction
- [ ] Update `docs/features/session-lifecycle.md` — `harness` field, resume-handle generalization
- [ ] Add `docs/features/README.md` index entry for harness-adapter
- [ ] Create `docs/infra/harness-cross-compat.md` — codex CLI dependency, auth posture, sandbox defaults, rollback (Phase 3; INFRA docs are durable, never archived)

### Inline Documentation
- [ ] Docstrings on the `HarnessAdapter` protocol and both adapters (contract: first-event handle emission, exit_reason taxonomy)

## Success Criteria

- [x] Phase 1: 3 issues closed with evidence comments (#1721/#1541/#1336), 4 issues edited (#1968/#1802/#1267/#1818), ceded items cross-linked (#1979 shipped via PR #2006, #1983 → #2004, #1855 → #1926) — completed 2026-07-10, #1999 closed
- [ ] Phase 2: golden argv test proves extraction is behavior-preserving; PM routing driven by `--json-schema` with regex fallback; resume-id behavior empirically recorded and capture-at-init simplified or alarm-fitted accordingly; runner imports no claude-specific parsing outside `harness/claude.py`
- [ ] Phase 3: a dev-lane-flagged eng session completes real dev work on codex end-to-end (incl. process-restart resume via persisted thread_id) on a provisioned machine; absent binary/auth fails fast with an actionable message; top-level sessions cannot reach codex; fallback-removal follow-up issue filed
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] Anti-criteria rows below pass (no prefix-token teaching left in the PM prime; no raw claude argv outside the adapter)

## Team Orchestration

### Team Members

- **Builder (cleanup)** — Name: cleanup-builder — Role: Phase 1 fold-in fixes + issue hygiene — Agent Type: builder — Resume: true
- **Builder (harness-seam)** — Name: seam-builder — Role: Phase 2 extraction, events, schema routing — Agent Type: builder — Resume: true
- **Builder (codex-adapter)** — Name: codex-builder — Role: Phase 3 adapter + selection wiring — Agent Type: builder — Resume: true
- **Validator (per phase)** — Name: phase-validator — Role: verify each phase's Verification rows + success criteria — Agent Type: validator — Resume: true
- **Documentarian** — Name: docs-writer — Role: Documentation section tasks — Agent Type: documentarian — Resume: true

## Step by Step Tasks

### 1.1 Issue hygiene — closures
- **Task ID**: p1-close-issues
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Close #1721, #1541, #1818, #1336 with evidence comments (deleted PTY paths, removed `dev` session type, closed children, removed persona file — per the 2026-07-10 survey)
- #1541 closing comment: if slugless-eng head-of-line blocking recurs, file fresh against the single-eng-session architecture

### 1.2 Issue hygiene — edits
- **Task ID**: p1-edit-issues
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- #1968: add `SessionRunnerSettings.pm_model`/`dev_model` (`config/settings.py:403-421`, zero readers) to the dead-field inventory
- #1802: rewrite root cause against `agent/session_runner/adapter.py` + `prime-pm-role.md`; keep Option A (`<<FILE:>>` on the `[/user]`→outbox path), note the relay already carries `file_paths`
- #1267: reframe outcome-verification for eng/teammate taxonomy (dev = in-turn subagent)
- Comment on #1925 linking this plan as the vehicle for the harness-abstraction half

### 1.3 Reconciliation hygiene (replaces the dissolved fold-in task)
- **Task ID**: p1-reconcile
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- #1818: EDIT (not close) — prune stale PTY-era roadmap items with the survey's evidence, reframe surviving scope around substrate durability (Redis AOF etc.) per the resilience program's sibling-coordination section
- #1855: verify its disposition is tracked under #1926's pruning pass; cross-link, do not decide or fix here
- Verify #1979 (PR #2006) and #1983 (#2004) need nothing from this phase

### 1.4 Phase 1 validation
- **Task ID**: p1-validate
- **Depends On**: p1-close-issues, p1-edit-issues, p1-reconcile
- **Assigned To**: phase-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify issue states match the survey verdicts as revised (closures, edits, ceded items cross-linked)

### 2.1 Empirical probes (schema + resume-id)
- **Task ID**: p2-probes
- **Depends On**: p1-validate
- **Informed By**: spike-1 (flag exists; runtime semantics unverified), spike-2 (id reuse suspected)
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: false
- Probe `--json-schema` under `--output-format stream-json`: where the validated object lands, invalid-output behavior, interaction with `--include-partial-messages`; record findings in the plan's Spike Results
- Two-turn `--resume` probe: record whether session id is stable on 2.1.204; record `--fork-session` behavior
- STOP and update plan tasks if either probe invalidates the approach (per Risk 1/2 mitigations)

### 2.2 Extract HarnessAdapter seam (behavior-preserving)
- **Task ID**: p2-extract-seam
- **Depends On**: p2-probes, #2004 merged (ExitReason StrEnum + SessionEvidence land first; heartbeat suite green is the entry gate)
- `TurnResult.exit_reason` uses #2004's `ExitReason` enum — one taxonomy, no parallel one; keep its "no raw exit-reason literals outside router" verification passing. `TurnResult` delivers program item T2.4 (supersedes the sketched `HarnessResult`)
- **Validates**: tests/unit/session_runner/test_harness_argv_golden.py (create), tests/unit/session_runner/, tests/integration/test_harness_resume.py
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_runner/harness/{base,claude,events}.py`; move argv/env assembly + stream-json parsing from `sdk_client.py`; golden test asserts byte-identical argv/env pre/post
- Runner consumes normalized `TurnEvent`s for liveness/telemetry; persist-at-first-event contract (Race 1)
- Apply spike-2 finding: assert-and-alarm or retained re-capture, inside `claude.py` only
- Delete the superseded `sdk_client.py` harness path in the same PR (no legacy tolerance) — coordinate scope with #1925 so the SDK-removal issue shrinks accordingly

### 2.3 Schema routing
- **Task ID**: p2-schema-routing
- **Depends On**: p2-extract-seam
- **Validates**: tests/unit/test_output_router.py, tests/unit/session_runner/ router suites
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: false
- Define the PM turn schema; pass via `--json-schema`; route on `structured_output.route`
- Demote prefix regex to fallback + `schema_routing_fallback` telemetry; keep compliance nudge as final backstop
- Update `prime-pm-role.md` (and teammate prime if applicable) to the schema contract; remove prefix-token teaching
- Wire `file_paths` through the schema (positions #1802 for a trivial close after this lands — note on the issue)

### 2.4 Phase 2 validation
- **Task ID**: p2-validate
- **Depends On**: p2-schema-routing
- **Assigned To**: phase-validator
- **Agent Type**: validator
- **Parallel**: false
- Run Verification table rows; live probe: one real eng session end-to-end on this machine before deploy

### 3.1 Codex adapter
- **Task ID**: p3-codex-adapter
- **Depends On**: p2-validate (detailed via #2001's own `/do-plan`)
- **Validates**: tests/unit/session_runner/test_codex_adapter.py (create, JSONL fixtures)
- **Assigned To**: codex-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `harness/codex.py` per Technical Approach (exec/resume loop, `--output-schema`, sandbox mapping, `turn.failed` mapping, version gate, fail-fast on missing binary/auth — subscription-first, `CODEX_API_KEY` backup)
- Dev priming via first-turn prompt body; document the decision
- Bounded-resume context guard (provisional env-overridable constant)

### 3.2 Dev-lane wiring + update system
- **Task ID**: p3-dev-lane
- **Depends On**: p3-codex-adapter
- **Validates**: tests/unit for dev-harness resolution + Race 2 mismatch discard
- **Assigned To**: codex-builder
- **Agent Type**: builder
- **Parallel**: false
- Per-session dev-lane opt-in (e.g. `valor-session create --dev-harness codex`, eng only); nullable fields (no migration — healed generically); executor resolves once, persists with resume scalars incl. codex `thread_id`; mismatch → fresh dev lane
- PM dev-routing: flagged sessions route dev work to the codex executor instead of spawning the `dev` subagent; exact mechanism per #2001's plan
- `/update` opt-in codex install + version/auth validation step; settings: `codex_*` fields, env-overridable, marked provisional
- File the follow-up issue: evaluate removing the prefix-regex routing fallback one week after Phase 2's schema routing landed (telemetry-driven) — required by owner decision

### 3.3 Phase 3 validation + E2E probe
- **Task ID**: p3-validate
- **Depends On**: p3-dev-lane
- **Assigned To**: phase-validator
- **Agent Type**: validator
- **Parallel**: false
- On a codex-provisioned machine: a flagged eng session completes real dev work end-to-end (create → multi-turn → PM steer → process-restart resume via persisted thread_id → complete); verify telemetry, liveness stamps, delivery
- Verify claude-path regression: default sessions unchanged; top-level sessions provably cannot reach codex

### N-1. Documentation
- **Task ID**: document-feature
- **Depends On**: p2-validate, p3-validate
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section checklist (per-phase docs may land with each phase PR; this task verifies completeness)

### N. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: phase-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; confirm all Success Criteria; generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Heartbeat suite green (P2 entry gate, delivered by #2004) | `pytest tests/unit/test_session_heartbeat_progress.py -q` | exit code 0 |
| Golden argv test exists (P2) | `pytest tests/unit/session_runner/test_harness_argv_golden.py -q` | exit code 0 |
| No prefix-token teaching in PM prime (P2) | `grep -c "\[/user\]" .claude/commands/roles/prime-pm-role.md` | match count == 0 |
| No claude argv outside adapter (P2) | `grep -rn '"claude", "-p"' agent/ --include="*.py" \| grep -vc harness/` | match count == 0 |
| Dev-harness resolved once (P3) | `pytest tests/unit -k "dev_harness_resolution" -q` | exit code 0 |
| Codex fixtures pass (P3) | `pytest tests/unit/session_runner/test_codex_adapter.py -q` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Decisions (Owner, 2026-07-10)

All open questions resolved; incorporated throughout the plan and into the phase issues (#1999, #2000, #2001):

1. **Harness selection policy:** manual only for now. The eventual policy remains a deliberately open question, carried in #2001 — not resolved by this plan.
2. **Session types:** codex applies ONLY to dev eng subsessions. Every top-level session connected to bridge messaging (PM, teammate) is claude-only. Phase 3 reframed from whole-session harness swap to PM-drives-external-codex-dev; #2001 supersedes any residual whole-session wording here.
3. **Codex auth:** subscription plan (`~/.codex/auth.json`) with `CODEX_API_KEY` as backup.
4. **Prefix-regex fallback:** stays at Phase 2 landing; Phase 3's acceptance criteria include filing a follow-up issue to evaluate its removal one week after schema routing lands (telemetry-driven).
5. **#1855 disposition:** not pre-decided — Phase 1's plan step does further research and decides.
