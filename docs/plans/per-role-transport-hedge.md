---
status: Planning
type: feature
appetite: Medium
owner: Tom Counsell
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1842
last_comment_id: none
slug: per-role-transport-hedge
---

# Per-Role Transport Hedge: Config-Selectable PTY vs Headless `claude -p` per PM/Dev Role

## Problem

The worker executes every bridge-originated session through granite: two interactive Claude Code TUIs (PM + Dev personas) driven over PTYs. That cutover (plan #1572) was all-or-nothing by design — `agent/session_executor.py:1641-1645` routes every session type to `BridgeAdapter` with no fallback flag and no feature gate. The preserved headless harness (`agent/sdk_client.py::get_response_via_harness` → `_run_harness_subprocess`) still works but is unreachable from the dispatch path.

The PTY path exists because interactive Claude Code sessions bill flat on the subscription, while every headless path (`claude -p`, Agent SDK) draws metered usage credits. Anthropic announced `claude -p` would be unavailable on subscriptions, then rolled that back on the due date — and as of June 15, 2026, programmatic usage draws from a separate monthly Agent SDK credit pool billed at API rates. The policy has moved once and can move again, in either direction.

**Current behavior:**
- Transport is hardwired: no config, no flag, no per-role choice (`session_executor.py:1641-1645`).
- No cost visibility: if a role ever routes through the metered harness, nothing surfaces what it draws in usage credits per session or per day.
- No documented procedure for flipping transports when policy or economics change.

**Desired outcome:**
- A per-role transport selector (`transport: {pm: pty|headless, dev: pty|headless}` in project config) honored at the dispatch seam, defaulting to today's behavior (both PTY).
- The headless leg, when selected, produces the same `AgentSession` lifecycle (status transitions, telemetry, steering inbox, resume handles) as the PTY leg.
- Metered-leg cost surfaced per session on `dashboard.json` and in the analytics export.
- A short runbook: when and how to flip, what to watch after flipping.

## Freshness Check

**Baseline commit:** `7592dd256f61186129b21e7328d75bad4a4f2757`
**Issue filed at:** 2026-07-02T04:27:58Z (hours before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_executor.py:1641-1645` — all-or-nothing granite routing comment — still holds verbatim (read at plan time).
- `agent/sdk_client.py::get_response_via_harness` — intact at `:2255-2627` (issue said ~2255) — still holds.
- `agent/sdk_client.py::_run_harness_subprocess` — intact at `:2630-2925` (issue said ~2630) — still holds.
- `agent/sdk_client.py::build_harness_turn_input` — intact at `:3005-3100`, still imported by the granite path at `session_executor.py:1648-1652` and called at `:1659-1670` — still holds.
- `models/agent_session.py:198` `claude_session_uuid = IndexedField(null=True)` — still holds.

**Cited sibling issues/PRs re-checked:**
- #1721 — OPEN, plan `docs/plans/granite_lossless_checkpoint_resume.md` is `status: Ready, revision_applied: true` but not yet built. Its 2026-07-02 comments add the transport-agnostic, list-shaped resume-handle constraint (`{role, claude_session_id, transcript_path, transport}`) and flag that the loop-cursor half may be deferred pending the native-subagents prototype. Direct coordination point for this plan (see Technical Approach).
- #1688 — OPEN (hook-driven turn boundaries). Independent; not blocking.
- #1837 — CLOSED, merged as PR #1839 (`tests/granite_faults/` harness + `tests/integration/test_granite_ollama_e2e.py`). Its patterns are the test substrate for AC5.

**Commits on main since issue was filed (touching referenced files):** none — `git log --since=2026-07-02T04:27:58Z` on `agent/session_executor.py`, `agent/sdk_client.py`, `models/agent_session.py`, `agent/granite_container/` is empty.

**Active plans in `docs/plans/` overlapping this area:** `granite_lossless_checkpoint_resume.md` (#1721, Ready, unbuilt) — overlaps only on the resume-handle schema, which the two issues explicitly split (this issue owns transport selection; #1721 owns durable resume). Handled by adopting the agreed list-shaped schema (below), not a blocker.

## Prior Art

- **#1546 (closed 2026-06-05)**: PoC — granite operator drives a real interactive Claude Code session via PTY (no `claude -p`). Origin of the PTY transport; established the billing asymmetry rationale.
- **Plan #1572 (merged)**: granite PTY production cutover. Deliberately removed the harness from the dispatch path with an inline comment reserving a follow-on issue — this is that follow-on. The harness itself was preserved intact (its only remaining production caller is the completion drafter at `agent/session_completion.py:755,817`, which passes `session_id=None`).
- **#1732 (closed 2026-06-18)**: omnigent reference map — demoted granite PTY frame-scraping to a liveness sensor, recommended hook/transcript-edge turn detection. Informs why the headless leg's deterministic `result`-event turn boundary is a feature, not a workaround.
- **#1837 / PR #1839 (merged 2026-07-01)**: granite failure-simulation harness (Substrate A deterministic fault injectors + ollama-backed Substrate B E2E). Supplies the test patterns and fixtures this plan's routing/E2E tests reuse.
- **#1128 (shipped)**: per-session token+cost accounting fields on `AgentSession` (`total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd` at `models/agent_session.py:455-468`) already exposed per-session on `dashboard.json` (`ui/app.py:457-460`). The cost-surfacing half of this plan builds on that, it does not invent a new pipeline.
- **#1245 (shipped)**: dashboard analytics aggregates derive cost/turns directly from AgentSession Popoto fields (`ui/data/analytics.py:41-54`), not the metrics ledger. Sets the precedent for how metered aggregate keys get added.

No prior attempt at per-role transport selection exists — `## Why Previous Fixes Failed` omitted (greenfield selector; the all-or-nothing cutover was deliberate, not a failed attempt).

## Research

**Queries used:**
- `claude -p --output-format json usage cost_usd total_cost tokens fields headless`
- `claude code -p print mode subscription vs API usage credits billing 2026`

**Key findings:**
- Since June 15, 2026, Anthropic splits programmatic usage (`claude -p`, Agent SDK, GitHub Actions) into a separate monthly Agent SDK credit pool billed at standard API rates ($20/mo Pro, $100 Max 5x, $200 Max 20x); interactive TUI use stays on the flat subscription pool. This is exactly the asymmetry the hedge manages, and it confirms the metered leg has a real, bounded monthly budget worth surfacing. (https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan, https://the-decoder.com/claude-subscriptions-get-separate-budgets-for-programmatic-use-billed-at-full-api-prices/)
- `claude -p --output-format stream-json` emits a final `result` event carrying `total_cost_usd`, cumulative `usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`), and `num_turns` — the harness already parses all of these (`agent/sdk_client.py:2798-2818`). Cost surfacing is a persistence/display problem, not a capture problem. (https://code.claude.com/docs/en/costs, https://platform.claude.com/docs/en/agent-sdk/cost-tracking)
- Known upstream behavior: `claude -p` with OAuth (no API key) bills as API/credit usage even on Max (anthropics/claude-code#43333) — consistent with the issue's recon ("`claude -p` runs on the subscription with `ANTHROPIC_API_KEY` blanked but draws metered usage credits"). The harness's unconditional `ANTHROPIC_API_KEY` strip (`sdk_client.py:2369-2377`) routes the metered draw to the subscription's credit pool rather than a raw API key — keep that behavior.

(Memory saves were attempted per Phase 0.7 but skipped on this machine — `resolve_project_key` returned None; findings are captured here instead.)

## Spike Results

Three parallel code-read spikes were run at plan time (Explore agents over the dispatch seam, the harness, and the config/dashboard/analytics surfaces). No prototype spikes were needed — every assumption was resolvable by reading code.

### spike-1: The dispatch seam has a single insertion point and an existing role-conditional precedent
- **Assumption**: "Per-role transport can branch at one seam without restructuring the executor."
- **Method**: code-read
- **Finding**: The seam is `BridgeAdapter(...)` construction at `session_executor.py:1745-1753`. `project_config` is already in scope (`:1557-1574`). An existing role/type-conditional env branch (`:1693-1703`) is the precedent for conditional wiring. Three layers currently collapse PM+Dev into one config: `BridgeAdapter.__init__` (single scalar channel `transport`, `bridge_adapter.py:397`), `PairSpawnSpec` (single `env`, no transport field, `pty_pool.py:116-143`), and `_spawn_session_pair` (both PTYs get identical `spec.env`, `:509-521`). `PTYDriver` already accepts per-driver `role`/`model`/`env`/`session_id` (`pty_driver.py:327-336`), so only the two upper layers need widening.
- **Confidence**: high
- **Impact on plan**: solution threads a `role_transports` mapping through exactly those two layers; naming must avoid colliding with the existing *channel* `transport` (telegram/email) at `session_executor.py:1083-1091`.

### spike-2: The headless harness captures everything the lifecycle needs, but drives none of it
- **Assumption**: "The preserved harness can produce the same AgentSession lifecycle as the PTY leg."
- **Method**: code-read
- **Finding**: `_run_harness_subprocess` returns an 8-tuple including `session_id_from_harness`, `usage`, `cost_usd`, `num_turns`, `tool_call_count` (`sdk_client.py:2651-2683`); turn end is the deterministic stream-json `result` event (`:2778-2821`); per-turn `--resume <uuid>` continuation already works (`:2390-2394`) with stale-UUID retry (`:2487-2530`); liveness callbacks exist (`on_sdk_started` pid, `on_stdout_event`, `on_sdk_finished`, `:2722-2727,2764-2768,2874-2878`). The lifecycle gap: `get_response_via_harness` never touches `session.status`, `last_turn_at`, `exit_reason`, or `user_facing_routed` — those live in the granite orchestration (`bridge_adapter.py:591-662,732-754`; `session_executor.py:1875-1923`). So the parity move is to run headless roles *inside* the existing granite orchestration (Container loop + BridgeAdapter finalization), not to resurrect the old standalone harness turn path.
- **Confidence**: high
- **Impact on plan**: the solution's core is a role-driver abstraction at the actor-turn level inside the container, reusing `_run_harness_subprocess` per turn — orchestration-owned lifecycle stays shared across transports.

### spike-3: Cost surfacing has existing rails end to end
- **Assumption**: "Metered-leg cost can reach dashboard + analytics without a new pipeline."
- **Method**: code-read
- **Finding**: `accumulate_session_tokens` (`sdk_client.py:286,371-382`) already persists the four token/cost fields; `dashboard.json` already serializes them per session (`ui/app.py:455-460`); the dashboard aggregate derives cost from AgentSession fields (`ui/data/analytics.py:41-54`, #1245 precedent); the metrics ledger (`analytics/collector.py:115 record_metric`, dual-write SQLite+Redis) flows into `python -m tools.analytics export/summary` with zero CLI changes for any new dotted metric name. The granite PTY path accumulates tokens via the transcript tailer (`agent/granite_container/transcript_tailer.py`) but never writes `total_cost_usd` — so on granite sessions, `total_cost_usd` is currently always 0 and any nonzero value is by construction metered spend.
- **Confidence**: high
- **Impact on plan**: cost work is: persist per-role transport labels, accumulate headless result-event cost into the existing fields, emit one new ledger metric, add aggregate keys — no new storage.

## Data Flow

1. **Entry point**: Telegram/email message → bridge enqueues `AgentSession` (with `project_config` snapshot) → worker picks it up → `agent/session_executor.py::_execute_agent_session`.
2. **Transport resolution (new)**: executor reads `project_config.get("transport", {})` (project block) with `settings.granite` defaults as fallback, producing `role_transports = {"pm": "pty"|"headless", "dev": "pty"|"headless"}`. Invalid values fail the session loud (`finalize_session(..., "failed")` with a clear reason) — but the primary gate is config validation at update time (`bridge/config_validation.py` via `scripts/update/verify.py:1113-1116`), so mid-session failure is a defensive backstop, not the UX.
3. **Persist the choice (new)**: `role_transports` is written onto the `AgentSession` at dispatch, so the dashboard, analytics, and post-hoc debugging see which transport each role actually ran on (config flips affect only new sessions).
4. **BridgeAdapter → PairSpawnSpec**: `role_transports` rides into `BridgeAdapter(...)` (`session_executor.py:1745-1753`) and onto `PairSpawnSpec` (`pty_pool.py:116-143`). `PTYPool.acquire_pair` still bounds concurrency (one slot per session pair) but `_spawn_session_pair` spawns a PTY only for roles with `pty`; headless roles get no PTY process.
5. **Container loop**: `Container.run()` drives each actor through a role driver. PTY roles: today's `PTYDriver` frame-scrape path, unchanged. Headless roles (new `HeadlessRoleDriver`): each actor turn is one `claude -p` subprocess via the existing `_run_harness_subprocess` — first turn creates the Claude session (UUID captured from the `result` event), later turns pass `--resume <uuid>`; persona priming sends the same `/granite:prime-*` slash command as the first prompt. Turn end is the `result` event (deterministic). Steering drain, watchdog, turn hooks (`on_turn` → `last_turn_at` bump), and exit classification stay orchestration-owned and transport-agnostic.
6. **Cost/telemetry capture**: at each headless turn end, the orchestration accumulates the result-event `usage` + `total_cost_usd` via `accumulate_session_tokens` and emits `record_metric("session.metered_cost_usd", cost_delta, {"role": ..., "project": ...})`. The transcript tailer tails PTY-role transcripts only (no double-count). Resume handles for both transports persist per-role in the transport-agnostic list shape agreed with #1721.
7. **Output**: `dashboard.json` per-session block shows `role_transports` + the existing token/cost fields (`ui/app.py:455-460` area); `ui/data/analytics.py` aggregate gains `metered_cost_today_usd`/`metered_cost_7d_usd`; `python -m tools.analytics export/summary` picks up `session.metered_cost_usd` automatically from the ledger.

## Architectural Impact

- **New dependencies**: none. Reuses the existing harness subprocess machinery, Popoto fields pattern, metrics ledger, and dashboard serializers.
- **Interface changes**: `BridgeAdapter.__init__` gains a `role_transports` parameter (distinct from the existing channel `transport`); `PairSpawnSpec` gains per-role transport fields; `Container` gains a role-driver seam (PTY driver extracted behind the same actor-turn surface a headless driver implements). `AgentSession` gains `role_transports` (JSON) and `resume_handles` (JSON list, schema shared with #1721).
- **Coupling**: decreases transport coupling — the container's orchestration loop (relay, steering, watchdog, exit classification) becomes transport-polymorphic at the actor-turn boundary instead of assuming PTY frames. The native-subagents future (one role) is a config simplification (`role_transports` with one key), not a schema break — this satisfies the issue's shaping constraint.
- **Data ownership**: unchanged — BridgeAdapter still owns AgentSession writes during a run; the executor still owns terminal transitions.
- **Reversibility**: high. Default config reproduces today's behavior exactly; removing the feature is deleting the branch + validator + two nullable fields.

## Appetite

**Size:** Medium

**Team:** Solo dev (Eng session), code reviewer at PR stage

**Interactions:**
- PM check-ins: 1-2 (open-questions resolution; runbook review)
- Review rounds: 1

The issue is deliberately thin: non-goals cut automation, SDK migration, and tmux. The E2E burden is capped by the ACs ("at least a deterministic dispatch-routing test"; mixed transport "at minimum via unit routing tests"). The one genuinely new component is the headless role driver, and it wraps an existing, working subprocess function.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `claude` CLI on PATH | `command -v claude` | Headless leg execs `claude -p` |
| Redis running | `redis-cli ping` | AgentSession persistence, metrics ledger live counters |
| granite fault fixtures present | `test -d tests/granite_faults` | Routing/E2E tests reuse #1837 patterns |

Run all checks via `python scripts/check_prerequisites.py docs/plans/per-role-transport-hedge.md`.

## Solution

### Key Elements

- **Transport config + validator**: an optional per-project `transport: {pm, dev}` block (values `pty`|`headless`), global defaults on `GraniteSettings`, validated fail-loud by a new `validate_transport()` registered in the `validate_projects_config` aggregator — which automatically puts it behind the update-time Step 4.6 gate.
- **Role-driver seam in the container**: the PM↔Dev orchestration loop keeps owning relay, steering, watchdog, turn hooks, and exit classification; each actor's "send message, await settled reply" becomes transport-polymorphic. PTY roles keep today's driver untouched; headless roles get a thin driver that runs one `claude -p --resume` subprocess per turn via the existing `_run_harness_subprocess`.
- **Selective PTY spawning**: the pool still bounds concurrency per session pair, but only spawns PTY processes for PTY-configured roles.
- **Transport-agnostic resume handles**: per-role `{role, claude_session_id, transcript_path, transport}` entries persisted for both transports, in the exact list shape agreed on #1721 (2026-07-02 comment), so #1721's resume execution consumes them without migration.
- **Metered-leg cost surfacing**: headless turn cost accumulates into the existing `total_cost_usd` field, emits a `session.metered_cost_usd` ledger metric, and shows up per-session (`role_transports` label + existing cost fields) on `dashboard.json` plus new aggregate keys in the analytics block.
- **Runbook**: a short flip procedure (edit config → validate → restart worker → watch) with post-flip checks, in the feature doc.

### Flow

Message arrives → executor resolves `role_transports` from config (default both `pty`) → persisted on the AgentSession → BridgeAdapter builds a spawn spec with per-role transports → pool spawns PTYs only where needed → container loop drives each role through its driver → headless turns return deterministic results with usage/cost → cost accumulates + ledger metric emits → session finalizes through the shared orchestration path → dashboard/analytics show transport labels and metered spend.

### Technical Approach

- **Naming: avoid the existing `transport` collision.** `session_executor.py:1083-1091` and `BridgeAdapter(transport=...)` (`bridge_adapter.py:397`) already use `transport` to mean the *channel* (telegram/email). The new concept is named `role_transports` everywhere (config key stays `transport` per the issue's spec, but it lives namespaced under the project block; code-level names use `role_transports`).
- **Config precedence**: project block `projects.<key>.transport.{pm,dev}` > `settings.granite.pm_transport`/`dev_transport` (env `GRANITE__PM_TRANSPORT`/`GRANITE__DEV_TRANSPORT`, mirroring the `pm_model`/`dev_model` pattern at `config/settings.py:403-423`) > literal `"pty"`. Validator: new `validate_transport(config)` in `bridge/config_validation.py`, registered in the aggregator tuple at `:402-409`; rejects any value outside `{"pty","headless"}` and non-dict shapes. It then runs automatically at `scripts/update/verify.py:1113-1116` (update Step 4.6) — that is the "fails loud at validation time" AC. The executor adds a defensive backstop: unknown resolved value → `finalize_session(session, "failed", reason=...)`, never a silent default.
- **Dispatch seam** (`session_executor.py:1745-1753`): resolve `role_transports` right after `project_config` (`:1557-1574`), write it onto the AgentSession (`save(update_fields=[...])`), pass to `BridgeAdapter(role_transports=...)`. Both-PTY (the default) must produce byte-identical behavior to today.
- **PairSpawnSpec + pool** (`pty_pool.py:116-143, 485-521`): add `pm_transport`/`dev_transport` (or a small mapping) to the spec; `_spawn_session_pair` spawns a `PTYDriver` only for `pty` roles and leaves `None` for headless roles; `acquire_pair` continues to hand out one slot per session (the slot is the concurrency unit — a mixed or headless session still occupies one, keeping the bound meaningful and the accounting simple).
- **Role-driver seam in `Container`**: extract the actor-turn surface the loop already uses against `PTYDriver` (send message → await settled reply text; expose liveness/activity signals for the watchdog) into a minimal protocol. `PTYRoleDriver` wraps today's behavior with zero changes to frame-scraping/idle detection. `HeadlessRoleDriver` implements it as: first turn → prime by sending the role's `/granite:prime-*` slash command as the initial `claude -p` prompt (slash commands work in `-p` mode; same prime constants at `container.py:73-88`), capture `session_id_from_harness` from the result tuple; subsequent turns → `prior_uuid=<captured uuid>` so the harness assembles `--resume`. Reuse `get_response_via_harness`'s existing flag assembly, API-key strip, stale-UUID retry, and 16MB stream parsing — do not reimplement subprocess handling. Wire `on_sdk_started`/`on_stdout_event` to the same activity signals the watchdog reads for PTY roles, so hang detection stays meaningful for headless roles (the PTY-liveness gates from #1789/#1798 read PTY state; headless roles substitute stdout-event recency).
- **Lifecycle parity comes free from placement**: because headless roles run inside `Container.run()` under `BridgeAdapter`, the existing machinery — steering drain per turn (`bridge_adapter.py:532-543`), `on_turn` → `last_turn_at` (`:732-754`), exit summary/`exit_reason`/`user_facing_routed` (`:591-662`), executor terminal transitions (`session_executor.py:1875-1923`) — applies unchanged. This closes every gap identified in spike-2 without duplicating lifecycle code.
- **Token/cost accounting, single-writer discipline**: transcript tailer registers PTY-role transcripts only. Headless roles accumulate tokens+cost at turn end from the result tuple via `accumulate_session_tokens` (`sdk_client.py:286`) — called from the orchestration sequentially, so it never races the tailer on the same fields (see Race 1). Also emit `record_metric("session.metered_cost_usd", cost_delta, {"role": role, "project": project_key})` at the same point.
- **Resume handles (coordination with #1721)**: add `resume_handles` JSON field to `AgentSession` holding a list of `{role, claude_session_id, transcript_path, transport}` — the exact schema from #1721's 2026-07-02 comment (correct under both the two-role present and one-role future). Write entries at spawn/first-turn for both transports: PTY roles from the UUIDs already generated at `bridge_adapter.py:505-506` + transcript paths from `_capture_pty_identity`; headless roles from the result-event UUID + the derivable `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` path. This plan persists handles only; resume *execution* (loop cursor, `--resume` re-entry, reply-path) remains #1721's scope. If #1721 builds first and lands the field, this plan writes into it; field definition is idempotent either way (additive nullable JSON — no Popoto migration needed, but see Update System).
- **Dashboard + analytics**: `ui/app.py::_session_to_json` adds `role_transports` next to the existing cost fields (`:455-460`); `ui/data/analytics.py` adds `metered_cost_today_usd`/`metered_cost_7d_usd` by summing `total_cost_usd` over sessions whose `role_transports` include `headless` (per #1245 precedent, derived from Popoto fields); the ledger metric flows into `tools/analytics` export/summary with no CLI change.
- **Corrected line anchors vs the issue**: harness functions live at `sdk_client.py:2255` / `:2630` / `:3005` as the issue estimated; `harness_pid` per-session persistence for granite is actually `pm_pid`/`dev_pid` via `_publish_exit_summary` (`bridge_adapter.py:631-645`), not `session_executor.py:1394` — the plan uses the granite-era fields.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The headless driver's subprocess failure paths (nonzero exit, binary-not-found tuple, stale-UUID retry exhaustion) must each assert an observable outcome: logged error + turn classified as failed + session `exit_reason` set — no swallowed exceptions. `_run_harness_subprocess` already returns error tuples rather than raising; the driver must check `returncode` and stderr snippet, never discard them.
- [ ] Executor defensive backstop: a session whose resolved transport value is invalid finalizes `failed` with a reason string — test asserts `finalize_session` called and the reason names the bad value.
- [ ] `record_metric` and dashboard serialization are best-effort by design (existing pattern); tests assert cost accumulation still happens when the ledger write fails.

### Empty/Invalid Input Handling
- [ ] Empty/None `transport` block → defaults to both-PTY (test each of: absent key, empty dict, one-role-only dict `{pm: headless}` with dev defaulting).
- [ ] Empty headless result text → the existing empty-output guard path must fire (the container's turn classification), not loop silently — reuse #1837's ScriptedRun pattern to assert.
- [ ] Validator rejects: non-dict `transport`, unknown role keys, values outside `{pty, headless}`, non-string values — each with a distinct error message naming the project key.

### Error State Rendering
- [ ] `dashboard.json` renders sessions with absent `role_transports` (pre-feature records) without KeyError — field is nullable, serializer uses `getattr(..., None)`.
- [ ] A failed headless turn's error propagates to the user via the existing exit-summary/nudge path (assert `exit_reason` lands on the session and the reaction gating at `session_executor.py:2068-2087` sees a non-clean exit).

## Test Impact

- [ ] `tests/unit/test_session_executor_granite.py::TestExecutorGraniteWiring::test_executor_does_not_call_get_response_via_harness` — REPLACE: the invariant changes from "harness is never reachable" to "harness is not reached under default (both-PTY) config"; add the counterpart asserting a headless-configured role does reach it.
- [ ] `tests/unit/test_session_executor_granite.py::TestExecutorGraniteWiring::test_executor_calls_bridge_adapter_run` — UPDATE: assert the new `role_transports` kwarg is passed (default both-PTY).
- [ ] `tests/unit/granite_container/test_container.py` — UPDATE: container construction/loop tests adapt to the role-driver seam (PTY behavior itself unchanged; fixtures may need the extracted driver type).
- [ ] `tests/unit/granite_container/conftest.py` — UPDATE: shared fixtures building `PairSpawnSpec` gain the per-role transport fields (defaulted, so most tests are untouched).
- [ ] `tests/unit/test_dm_whitelist_validation.py` — UPDATE: any test asserting the `validate_projects_config` aggregator's validator set/error aggregation must include `validate_transport`.
- [ ] `tests/unit/test_dashboard_pillar_a_fields.py` — UPDATE: per-session serializer test extends to the new `role_transports` key.
- [ ] `tests/integration/test_analytics_dashboard.py` — UPDATE: analytics summary shape gains `metered_cost_*` keys.
- [ ] `tests/unit/granite_container/test_pty_driver.py`, `tests/integration/test_granite_pty_production.py`, `tests/integration/test_granite_ollama_e2e.py` — no changes expected (PTY path byte-identical under default config); re-run to confirm.

## Rabbit Holes

- **Rewriting PTY idle-detection or frame-scraping while extracting the driver seam.** The extraction is mechanical — today's PTY behavior moves behind the protocol unchanged. Turn-boundary improvements are #1688's territory.
- **Migrating the harness to `ClaudeSDKClient` / SDK streaming.** Explicit non-goal; the hand-rolled subprocess works and is reused as-is.
- **Building automated transport switching (policy detection, cost thresholds, auto-flip).** Explicit non-goal; the trigger is rare, loud, and human-observable — the flip is config + runbook.
- **Per-role channel callbacks / per-role env splits beyond transport.** The existing single `session_env` and channel callbacks stay shared; only spawn shape branches per role.
- **Extending the transcript tailer to compute PTY-leg "notional" cost.** Tempting for hedge-savings math, but it contaminates the metered signal (PTY `costUSD` is not billed) and expands tailer scope. Metered cost comes only from headless result events.
- **Reconciling `claude_session_uuid` (legacy scalar) with the new list-shaped handles beyond writing both.** Full unification of resume plumbing is #1721's build.

## Risks

### Risk 1: Container loop assumptions leak PTY specifics through the driver seam
**Impact:** The extraction subtly changes PTY-path behavior (settle timing, watchdog signals), destabilizing the production transport for zero-config users.
**Mitigation:** The seam is defined by what the loop already calls on `PTYDriver`; PTY tests (`test_container.py`, `test_granite_pty_production.py`, ollama E2E) must pass unmodified except for construction fixtures. Default-config routing test asserts `BridgeAdapter.run` wiring is unchanged.

### Risk 2: Headless turn hangs are invisible to PTY-liveness-gated health checks
**Impact:** A hung `claude -p` subprocess never trips the PTY-quiescence kill gates (#1789/#1798), so a headless role could hang past the watchdog's assumptions.
**Mitigation:** Wire `on_stdout_event` recency into the same activity signal the in-loop watchdog reads; the container's existing per-turn timeout applies to the awaited subprocess. Deterministic fault test (fake subprocess that never emits `result`) asserts the turn is killed and classified.

### Risk 3: Metered spend surprises (silent credit burn)
**Impact:** A misconfigured flip routes a role headless and burns the monthly Agent SDK credit pool without anyone noticing.
**Mitigation:** This is the feature: `role_transports` label per session on the dashboard, `metered_cost_*` aggregates, ledger metric in analytics export, and the runbook's post-flip checks (watch `metered_cost_today_usd`). Config validation prevents typo-shaped accidents; the default is both-PTY.

### Risk 4: Schema drift against #1721 (resume handles)
**Impact:** Two plans introduce competing resume-handle fields, forcing a migration.
**Mitigation:** Both plans now reference the same agreed list shape (`{role, claude_session_id, transcript_path, transport}`, #1721 comment 2026-07-02). Whichever builds first introduces the field; the other writes into it. This plan's scope is persistence only.

## Race Conditions

### Race 1: Transcript tailer vs headless turn accumulation on token/cost fields
**Location:** `agent/granite_container/bridge_adapter.py:1161-1248` (tailer tick writes token fields) vs the new headless turn-end `accumulate_session_tokens` call
**Trigger:** Mixed-transport session — tailer task writes PTY-role token totals concurrently with a headless turn completing.
**Data prerequisite:** Both writers use `save(update_fields=[...])` on overlapping fields → lost-update hazard.
**State prerequisite:** Tailer merges PM+Dev transcript counters into absolute totals; a concurrent additive write from the headless leg would be clobbered.
**Mitigation:** Partition sources: tailer registers and folds only PTY-role transcripts; headless usage accumulates only via the turn-end call, which runs sequentially inside the orchestration loop (same task as the turn await). No two writers touch the same counter for the same role. Test: mixed-transport unit test asserts final totals equal PTY-tailed + headless-result sums.

### Race 2: Config flip mid-flight
**Location:** `session_executor.py` transport resolution vs a `projects.json` edit + worker restart
**Trigger:** Operator flips transport while sessions are running.
**Data prerequisite:** A session's transport must be immutable for its lifetime (resume handles are transport-tagged).
**State prerequisite:** In-flight sessions keep their spawn-time transports.
**Mitigation:** `role_transports` is resolved once at dispatch and persisted on the AgentSession; all later reads (dashboard, resume-handle tagging) use the persisted value, never re-read config. Runbook states flips affect new sessions only.

### Race 3: Handle persistence vs early crash
**Location:** headless first-turn UUID capture → `resume_handles` write
**Trigger:** Session crashes after the Claude session is created but before the handle is saved.
**Data prerequisite:** Handle entry must exist before any resume attempt can target it.
**State prerequisite:** none beyond the write.
**Mitigation:** Write the handle in the same code path that today persists `pm_pid`/`dev_pid`/transcript paths, immediately on capture (first result event), not at exit summary. A missing handle degrades to today's behavior (fresh start) — #1721's resume logic already treats absent handles as non-resumable.

## No-Gos (Out of Scope)

- Automated switch-triggering (policy watchers, cost-threshold auto-flips) — rejected as over-engineering in the issue body, not deferred work: the trigger is rare, loudly announced, and human-observable; the switch is a config flip plus the runbook.
- tmux `-CC` transport — rejected in the issue body after evaluation: adds a cleaner byte pipe but no turn-boundary semantics. (Anti-criterion row in Verification.)
- [SEPARATE-SLUG #1721] Resume *execution*: loop cursor persistence, `--resume` re-entry on crash/reply, skip-priming on resume, auto-resume reflection changes. This plan persists transport-tagged resume handles in the agreed schema; #1721 owns consuming them.
- [SEPARATE-SLUG #1688] Turn-boundary improvements for the PTY leg (Stop-hook signals, needs-input routing). The driver seam here must not touch PTY frame-scraping/idle detection.
- [SEPARATE-SLUG #43333 in anthropics/claude-code] Upstream billing-attribution behavior of `claude -p` under OAuth — external product behavior we consume, not change. (Tagged for completeness; validator note: this is an upstream repo's issue, cited for context.)
- `ClaudeSDKClient` migration of the hand-rolled `_run_harness_subprocess` — rejected in the issue body as a separable follow-up; the hedge uses the harness that exists and works today. No issue filed yet by design (the issue's non-goals section is the record).

## Update System

- **Config validation propagates automatically**: the new `validate_transport()` registers in `validate_projects_config`, which `scripts/update/run.py` Step 4.6 already gates on (`scripts/update/verify.py:1113-1116`). A malformed `transport` block blocks the bridge restart on update — no new wiring needed.
- **No Popoto migration required**: `role_transports` and `resume_handles` are additive nullable/defaulted fields; Popoto self-heals absent fields on old records (precedent: #1721 plan note on additive nullable fields, `_heal_descriptor_pollution` #1099/#1172). No entry in `scripts/update/migrations.py`.
- **No new dependencies**: no packages, no new binaries (`claude` CLI is already required everywhere).
- **Config propagation**: `projects.json` is iCloud-synced and per-machine; the `transport` key is optional with a both-PTY default, so machines that never add it see zero behavior change. `.env.example` gains commented `GRANITE__PM_TRANSPORT`/`GRANITE__DEV_TRANSPORT` placeholders (with the required comment line above each) and `config/settings.py` gains the two `GraniteSettings` fields.

## Agent Integration

No new agent integration surface required — transport selection is config-driven at the dispatch seam inside the worker; the agent does not invoke it as a tool. Existing surfaces gain fields rather than new entry points:

- `curl -s localhost:8500/dashboard.json` (already reachable to the agent via Bash) now shows per-session `role_transports` and the metered aggregates.
- `python -m tools.analytics export/summary` (existing CLI in the quick-reference table) picks up `session.metered_cost_usd` automatically from the ledger — no `pyproject.toml [project.scripts]` change.
- No MCP server or `.mcp.json` changes. No bridge import changes beyond the executor/adapter files already in scope.
- Integration test that the surfaced data is agent-reachable: the analytics/dashboard tests in Test Impact assert the new keys appear in the JSON the agent would read.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/per-role-transport.md` — the selector (config schema, precedence, defaults), the headless role driver, cost surfacing, and the **runbook**: when to flip (Anthropic policy/economics change), how to flip (edit `projects.json` or `GRANITE__*_TRANSPORT` → run `/update` or `python -c "from bridge.config_validation import ..."` validate → `./scripts/valor-service.sh worker-restart`), and post-flip checks (dashboard `role_transports` labels on new sessions, `metered_cost_today_usd` trending, `valor-session telemetry` token_usage events, `logs/worker.log` routing lines). Note flips affect new sessions only.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/granite-pty-production.md` — replace the "all-or-nothing cutover, no fallback flag" description with a pointer to the per-role selector (default unchanged).

### External Documentation Site
Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation
- [ ] Rewrite the dispatch-seam comment at `session_executor.py:1641-1645` (it currently documents the absence of this feature).
- [ ] Docstrings on the role-driver protocol, `HeadlessRoleDriver`, and `validate_transport`.

## Success Criteria

- [ ] Per-role transport config exists with PTY default; `validate_projects_config` rejects invalid blocks with actionable errors (AC1).
- [ ] Default (no config) behavior is unchanged: routing test proves `BridgeAdapter.run` is called and the harness is not, exactly as today (AC1).
- [ ] A headless-routed role completes a real session end-to-end with correct AgentSession lifecycle: terminal status via the shared orchestration path, `last_turn_at` bumps, steering drain, `exit_reason`, and a persisted transport-tagged resume handle (AC2) — proven by the deterministic E2E dispatch-routing test using #1837 patterns.
- [ ] Metered-leg cost visible per session (`dashboard.json`: `role_transports` + `total_cost_usd`) and in analytics (`session.metered_cost_usd` in export; `metered_cost_today_usd`/`metered_cost_7d_usd` in the dashboard aggregate) (AC3).
- [ ] Runbook exists in `docs/features/per-role-transport.md` with flip procedure + post-flip checks (AC4).
- [ ] Unit routing matrix covers all four transport combinations plus invalid config; mixed transport (PM=pty, Dev=headless) exercised in unit routing tests; headless leg has a deterministic dispatch-routing test (AC5).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (config + dispatch)**
  - Name: transport-config-builder
  - Role: Config schema, validator, settings fields, executor transport resolution + persistence
  - Agent Type: builder
  - Resume: true

- **Builder (headless driver + container seam)**
  - Name: headless-driver-builder
  - Role: Role-driver protocol extraction, HeadlessRoleDriver, PairSpawnSpec/pool changes, lifecycle wiring
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Builder (cost surfacing)**
  - Name: cost-surfacing-builder
  - Role: AgentSession fields, accumulation + ledger metric, dashboard + analytics keys
  - Agent Type: builder
  - Resume: true

- **Test engineer (routing + faults)**
  - Name: transport-test-engineer
  - Role: Routing matrix, headless fault tests, deterministic E2E per #1837 patterns
  - Agent Type: test-engineer
  - Resume: true

- **Validator (transport)**
  - Name: transport-validator
  - Role: Verify ACs, run Verification table, confirm PTY-path byte-identity under default config
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: transport-documentarian
  - Role: Feature doc + runbook, README index, granite doc update, seam comment rewrite
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Config schema, validator, settings, executor resolution
- **Task ID**: build-transport-config
- **Depends On**: none
- **Validates**: tests/unit/test_transport_config_validation.py (create), tests/unit/test_dm_whitelist_validation.py
- **Informed By**: spike-3 (validator aggregator at `bridge/config_validation.py:402-409`; update gate at `scripts/update/verify.py:1113-1116`)
- **Assigned To**: transport-config-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `validate_transport()` to `bridge/config_validation.py`; register in the aggregator tuple
- Add `pm_transport`/`dev_transport` fields to `GraniteSettings` (`config/settings.py:348-423` pattern), default `"pty"`; add `.env.example` placeholders with comment lines
- In `session_executor.py`, resolve `role_transports` (project block > settings > `"pty"`) after project_config load (`:1557-1574`); persist onto the AgentSession; defensive fail-loud backstop for invalid resolved values
- Add `role_transports` JSON field + `resume_handles` JSON list field to `models/agent_session.py` (nullable, no migration; coordinate field name with #1721's plan if it landed first)

### 2. Role-driver seam + headless driver
- **Task ID**: build-headless-driver
- **Depends On**: build-transport-config
- **Validates**: tests/unit/granite_container/test_headless_role_driver.py (create), tests/unit/granite_container/test_container.py
- **Informed By**: spike-1 (PairSpawnSpec/pool anchors), spike-2 (harness 8-tuple, resume-per-turn, liveness callbacks; lifecycle stays orchestration-owned)
- **Assigned To**: headless-driver-builder
- **Agent Type**: builder
- **Parallel**: false
- Extract the actor-turn protocol from the container loop's use of `PTYDriver`; wrap existing PTY behavior unchanged (`PTYRoleDriver`)
- Implement `HeadlessRoleDriver`: prime-as-first-prompt, per-turn `get_response_via_harness` with `prior_uuid` resume, result-event turn end, `on_stdout_event` wired to watchdog activity
- Widen `PairSpawnSpec` with per-role transports; `_spawn_session_pair` spawns PTYs only for PTY roles; slot semantics unchanged
- Thread `role_transports` through `BridgeAdapter.__init__` → spawn spec → `Container`; persist transport-tagged `resume_handles` entries on capture (both transports)

### 3. Cost surfacing
- **Task ID**: build-cost-surfacing
- **Depends On**: build-headless-driver
- **Validates**: tests/unit/test_dashboard_pillar_a_fields.py, tests/integration/test_analytics_dashboard.py, tests/unit/test_analytics_collector.py
- **Informed By**: spike-3 (accumulate_session_tokens, #1245 aggregate precedent, ledger auto-flow into export)
- **Assigned To**: cost-surfacing-builder
- **Agent Type**: builder
- **Parallel**: false
- Accumulate headless turn usage/cost via `accumulate_session_tokens` at turn end (single-writer partition vs tailer per Race 1)
- Emit `record_metric("session.metered_cost_usd", cost_delta, {"role", "project"})`
- Add `role_transports` to `_session_to_json` (`ui/app.py:455-460`); add `metered_cost_today_usd`/`metered_cost_7d_usd` to `ui/data/analytics.py`

### 4. Routing matrix + fault tests + deterministic E2E
- **Task ID**: build-transport-tests
- **Depends On**: build-cost-surfacing
- **Validates**: tests/unit/test_session_executor_granite.py, tests/unit/test_transport_routing_matrix.py (create), tests/integration/test_transport_dispatch_e2e.py (create)
- **Informed By**: #1837 patterns (`tests/granite_faults/` ScriptedRun, fake subprocess mocks)
- **Assigned To**: transport-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- REPLACE `test_executor_does_not_call_get_response_via_harness` per Test Impact; UPDATE `test_executor_calls_bridge_adapter_run`
- Routing matrix: pty/pty (default + explicit), pty/headless, headless/pty, headless/headless, invalid-config backstop
- Headless fault tests: hung subprocess (no result event) killed + classified; nonzero exit propagates `exit_reason`; empty result hits the empty-output guard
- Deterministic E2E dispatch-routing test for the headless leg; mixed-transport token/cost partition assertion (Race 1)

### 5. Validation
- **Task ID**: validate-transport
- **Depends On**: build-transport-tests
- **Assigned To**: transport-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm all ACs; confirm PTY-only suites pass without behavioral edits

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-transport
- **Assigned To**: transport-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/per-role-transport.md` (incl. runbook); update README index + `granite-pty-production.md`; rewrite the seam comment

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: transport-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands; verify all success criteria including documentation; generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Validator registered | `grep -c "validate_transport" bridge/config_validation.py` | output > 1 |
| Default transport is pty | `python -c "from config.settings import settings; print(settings.granite.pm_transport, settings.granite.dev_transport)"` | output contains pty |
| Routing matrix exists | `pytest tests/unit/test_transport_routing_matrix.py -q` | exit code 0 |
| Dashboard exposes transports | `grep -c "role_transports" ui/app.py` | output > 0 |
| Metered metric emitted | `grep -rc "session.metered_cost_usd" agent/ ui/ \| grep -v ':0'` | output > 0 |
| Runbook exists | `grep -ci "runbook\|flip" docs/features/per-role-transport.md` | output > 0 |
| Anti-criterion: no tmux transport | `grep -rn "tmux" agent/granite_container/ agent/session_executor.py \| wc -l` | match count == 0 |
| Anti-criterion: no auto-switching | `grep -rn "auto_flip\|auto_switch\|policy_watcher" agent/ bridge/ config/ \| wc -l` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Both-headless combination**: it falls out of per-role selection naturally and the routing matrix covers it at the unit level, but should it be *supported* (documented in the runbook) or *validation-rejected* in v1? Proposed default: supported but flagged in the runbook as untested beyond unit routing (no E2E claim).
2. **Slot accounting for headless roles**: this plan keeps one pool slot per session regardless of transport (concurrency bound = sessions, simple and safe). If the intent of a headless flip is to *increase* concurrency beyond PTY pool size, that's a follow-up knob. Confirm the simple semantics are acceptable for v1. Proposed default: yes, keep it simple.
3. **`resume_handles` field introduction order**: if #1721 starts building before this plan, its builder introduces the field and this plan writes into it; otherwise this plan introduces it per the agreed schema. Both plans reference the same shape, so this is sequencing awareness rather than a decision — flagging in case #1721 is deliberately being held for the native-subagents readout. Proposed default: whichever builds first introduces the field; no coordination gate needed.
