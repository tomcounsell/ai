---
status: Ready
type: feature
appetite: Medium
owner: Tom Counsell
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1842
last_comment_id: none
slug: per-role-transport-hedge
revision_applied: true
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
- #1688 — OPEN (hook-driven turn boundaries), plan `docs/plans/granite_hook_driven_turn_returns.md`, currently building (its settings-field work is live in the tree). **Now a build prerequisite, not independent.** #1688 introduces the transport-agnostic turn-end seam this plan's headless leg must consume: a `HookEdgeConsumer` (keyed by `session_id`) emitting typed edges (`turn_end` from a parent `Stop`, `subagent_end`, `needs_human`, `compaction`), fed by a per-session append-only NDJSON edge file via `--settings`-injected hooks + a hook-forwarder + a durable cursor. Per the pipeline orchestrator, #1688 merges to main BEFORE this plan builds; BUILD rebases onto the landed hook-channel code and wires the headless leg's turn-end to `HookEdgeConsumer.poll()`'s `EdgeType.TURN_END` envelope (the surface #1688's plan doc declares; the build rebases onto whatever actually lands) — see Technical Approach → "Turn-end authority" and Prerequisites.
- #1837 — CLOSED, merged as PR #1839 (`tests/granite_faults/` harness + `tests/integration/test_granite_ollama_e2e.py`). Its patterns are the test substrate for AC5.

**Commits on main since issue was filed (touching referenced files):** none — `git log --since=2026-07-02T04:27:58Z` on `agent/session_executor.py`, `agent/sdk_client.py`, `models/agent_session.py`, `agent/granite_container/` is empty.

**Active plans in `docs/plans/` overlapping this area:** `granite_lossless_checkpoint_resume.md` (#1721, Ready, unbuilt) — overlaps only on the resume-handle schema, which the two issues explicitly split (this issue owns transport selection; #1721 owns durable resume). Handled by adopting the agreed list-shaped schema (below), not a blocker.

## Prior Art

- **#1546 (closed 2026-06-05)**: PoC — granite operator drives a real interactive Claude Code session via PTY (no `claude -p`). Origin of the PTY transport; established the billing asymmetry rationale.
- **Plan #1572 (merged)**: granite PTY production cutover. Deliberately removed the harness from the dispatch path with an inline comment reserving a follow-on issue — this is that follow-on. The harness itself was preserved intact (its only remaining production caller is the completion drafter at `agent/session_completion.py:755,817`, which passes `session_id=None`).
- **#1732 (closed 2026-06-18)**: omnigent reference map — demoted granite PTY frame-scraping to a liveness sensor, recommended hook/transcript-edge turn detection. This is exactly why #1688 builds the transport-agnostic hook channel this plan's headless leg consumes for turn-end (rather than the `result` event), and why the `result` event is demoted here to a content/liveness carrier.
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
5. **Container loop**: `Container.run()` drives each actor through a role driver. PTY roles: today's `PTYDriver` injection/liveness path, unchanged. Headless roles (new `HeadlessRoleDriver`): each actor turn is one `claude -p` subprocess via the existing `_run_harness_subprocess` — first turn creates the Claude session (UUID captured from the `result` event), later turns pass `--resume <uuid>`; persona priming sends the role's `/granite:prime-*` slash command as the first prompt (with an inlined-body fallback if slash resolution under `-p` is unconfirmed — see Technical Approach + gate task). **Turn-end authority is #1688's hook channel for BOTH transports:** the headless subprocess is spawned with the same `--settings`-injected hook set #1688 generates (Stop/SubagentStop/Notification/PermissionRequest/AskUserQuestion), writing to the same per-session NDJSON edge file; the container drains `HookEdgeConsumer.poll()` and treats a parent-`Stop` envelope classified `EdgeType.TURN_END` as the boundary, rather than the `result` event or subprocess exit. The `result` event is retained only as the turn's content/usage carrier and as a crash-liveness signal (mirroring #1688's demotion of `read_until_idle`). Steering drain, watchdog, turn hooks (`on_turn` → `last_turn_at` bump), and exit classification stay orchestration-owned and transport-agnostic.
6. **Cost/telemetry capture (transport-partitioned fields — no shared scalar)**: PTY-leg token totals continue to flow through the transcript tailer, which writes the **absolute** merged totals to the existing `total_input_tokens`/`total_output_tokens`/`total_cache_read_tokens` scalars (`bridge_adapter.py:1225-1229`) — tailing PTY-role transcripts only. The headless leg accumulates into a **disjoint** set of new fields — `metered_input_tokens`/`metered_output_tokens`/`metered_cache_read_tokens`/`metered_cost_usd` — through a single accumulation call (the one already inside `get_response_via_harness` at `sdk_client.py:2542`, routed to the metered fields via a new `metered=True` flag; NO second turn-end call is added). Because the tailer's absolute writes and the headless additive writes never touch the same field, the mixed-transport lost-update race is eliminated by construction (see Race 1). The metered ledger metric `record_metric("session.metered_cost_usd", cost_delta, {"role", "project"})` is emitted from that same single metered-accumulation point. Displayed grand totals = `total_* (PTY) + metered_* (headless)`, computed at read time by the serializer/analytics. Resume handles for both transports persist per-role in the transport-agnostic list shape agreed with #1721 (persistence only; consumption is #1721 — see Technical Approach).
7. **Output**: `dashboard.json` per-session block shows `role_transports` + the existing token/cost fields (`ui/app.py:455-460` area); `ui/data/analytics.py` aggregate gains `metered_cost_today_usd`/`metered_cost_7d_usd`; `python -m tools.analytics export/summary` picks up `session.metered_cost_usd` automatically from the ledger.

## Architectural Impact

- **New dependencies**: none. Reuses the existing harness subprocess machinery, Popoto fields pattern, metrics ledger, and dashboard serializers.
- **Interface changes**: `BridgeAdapter.__init__` gains a `role_transports` parameter (distinct from the existing channel `transport`); `PairSpawnSpec` gains per-role transport fields; `Container` gains a role-driver seam (PTY driver extracted behind the same actor-turn surface a headless driver implements) whose turn-end authority is #1688's `HookEdgeConsumer.poll()` `EdgeType.TURN_END` envelope for both transports. `accumulate_session_tokens` and `get_response_via_harness` each gain a `metered: bool = False` keyword (default preserves every existing caller). `AgentSession` gains: `role_transports` (JSON), `resume_handles` (JSON list, schema shared with #1721), and four disjoint metered-accounting fields `metered_input_tokens`/`metered_output_tokens`/`metered_cache_read_tokens`/`metered_cost_usd` (nullable, default 0) that the transcript tailer never touches.
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
| **#1688 hook-channel merged to main** | `git fetch origin main --quiet && test -n "$(gh pr list --search '1688 in:body' --state merged --json number --jq '.[0].number')" && git cat-file -e origin/main:agent/granite_container/hook_edge.py` | **Build gate (verifies MERGE state, not local WIP):** a bare working-tree `grep` false-passes today because #1688's build exists as untracked working-tree files (`agent/granite_container/hook_edge.py`, modified `transcript_tailer.py`). This gate instead fetches `origin/main`, confirms a merged PR references #1688, AND confirms `hook_edge.py` is tracked in `origin/main` — none of which a local WIP tree can satisfy. The build MUST rebase onto the landed #1688 code before wiring the driver seam (mandate B). |

Run all checks via `python scripts/check_prerequisites.py docs/plans/per-role-transport-hedge.md`.

**Build sequencing (mandate B):** BUILD does not start until #1688's PR merges to main. The gate above must run against a **fresh-pulled `origin/main`** (`git fetch origin main` first) — a working-tree check cannot distinguish "merged to main" from "local WIP", which is why the gate verifies the merged PR AND the tracked-in-`origin/main` blob rather than a bare `grep` over the working tree. The first build action is a rebase of `session/per-role-transport-hedge` onto the landed hook-channel code, so the `HookEdgeConsumer` seam, the `--settings`-injected hook set, and the per-session edge-file plumbing exist before this plan wires the headless role driver into them. #1688 is still unbuilt/unmerged at plan time; this plan names its turn-end surface as declared by #1688's own plan doc (`docs/plans/granite_hook_driven_turn_returns.md`) — `HookEdgeConsumer.poll()` returning `HookEnvelope`s classified by `EdgeType.TURN_END` — and the build rebases onto whatever #1688 actually lands. If #1688 has NOT merged when this plan reaches BUILD, the pipeline holds — do not invent a bespoke headless turn-end signal to unblock.

## Solution

### Key Elements

- **Transport config + validator**: an optional per-project `transport: {pm, dev}` block (values `pty`|`headless`), global defaults on `GraniteSettings`, validated fail-loud by a new `validate_transport()` registered in the `validate_projects_config` aggregator — which automatically puts it behind the update-time Step 4.6 gate.
- **Role-driver seam in the container**: the PM↔Dev orchestration loop keeps owning relay, steering, watchdog, turn hooks, and exit classification; each actor's "send message, await settled reply" becomes transport-polymorphic. PTY roles keep today's driver untouched; headless roles get a thin driver that runs one `claude -p --resume` subprocess per turn via the existing `_run_harness_subprocess`. **Turn-end for both transports is a parent-`Stop` `EdgeType.TURN_END` envelope from #1688's `HookEdgeConsumer.poll()`** (mandate A) — the headless leg registers the same `--settings`-injected hooks #1688 generates; no bespoke headless turn-end signal is invented.
- **Selective PTY spawning**: the pool still bounds concurrency per session pair, but only spawns PTY processes for PTY-configured roles.
- **Transport-agnostic resume handles (minimal #1721 coordination)**: per-role `{role, claude_session_id, transcript_path, transport}` entries persisted for both transports, in the exact list shape agreed on #1721 (2026-07-02 comment). Written only from data this plan already captures at spawn/first-turn (zero extra capture cost); **consumption — loop cursor, `--resume` re-entry, reply-path — is explicitly deferred to #1721** (concern 3). If #1721 lands the field first, this plan writes into it (idempotent, additive nullable).
- **Metered-leg cost surfacing (disjoint fields, single writer)**: headless turn usage/cost accumulates into the NEW `metered_*` fields (never the tailer-owned `total_*` scalars), via a single `metered=True` accumulation call inside `get_response_via_harness` (no second turn-end call — the duplicate-count blocker). It emits a `session.metered_cost_usd` ledger metric at that same point, and shows up per-session (`role_transports` label + `metered_cost_usd` + combined tokens) on `dashboard.json` plus new aggregate keys in the analytics block.
- **Runbook**: a short flip procedure (edit config → validate → restart worker → watch) with post-flip checks, in the feature doc.

### Flow

Message arrives → executor resolves `role_transports` from config (default both `pty`) → persisted on the AgentSession → BridgeAdapter builds a spawn spec with per-role transports → pool spawns PTYs only where needed → container loop drives each role through its driver → headless turns return deterministic results with usage/cost → cost accumulates + ledger metric emits → session finalizes through the shared orchestration path → dashboard/analytics show transport labels and metered spend.

### Technical Approach

- **Naming: avoid the existing `transport` collision.** `session_executor.py:1083-1091` and `BridgeAdapter(transport=...)` (`bridge_adapter.py:397`) already use `transport` to mean the *channel* (telegram/email). The new concept is named `role_transports` everywhere (config key stays `transport` per the issue's spec, but it lives namespaced under the project block; code-level names use `role_transports`).
- **Config precedence**: project block `projects.<key>.transport.{pm,dev}` > `settings.granite.pm_transport`/`dev_transport` (env `GRANITE__PM_TRANSPORT`/`GRANITE__DEV_TRANSPORT`, mirroring the `pm_model`/`dev_model` pattern at `config/settings.py:403-423`) > literal `"pty"`. Validator: new `validate_transport(config)` in `bridge/config_validation.py`, registered in the aggregator tuple at `:402-409`; rejects any value outside `{"pty","headless"}` and non-dict shapes. It then runs automatically at `scripts/update/verify.py:1113-1116` (update Step 4.6) — that is the "fails loud at validation time" AC. The executor adds a defensive backstop: unknown resolved value → `finalize_session(session, "failed", reason=...)`, never a silent default.
- **Dispatch seam** (`session_executor.py:1745-1753`): resolve `role_transports` right after `project_config` (`:1557-1574`), write it onto the AgentSession (`save(update_fields=[...])`), pass to `BridgeAdapter(role_transports=...)`. Both-PTY (the default) must produce byte-identical behavior to today.
- **PairSpawnSpec + pool** (`pty_pool.py:116-143, 485-521`): add `pm_transport`/`dev_transport` (or a small mapping) to the spec; `_spawn_session_pair` spawns a `PTYDriver` only for `pty` roles and leaves `None` for headless roles; `acquire_pair` continues to hand out one slot per session (the slot is the concurrency unit — a mixed or headless session still occupies one, keeping the bound meaningful and the accounting simple).
- **Role-driver seam in `Container`, turn-end via #1688 (mandate A)**: extract the actor-turn surface the loop already uses against `PTYDriver` (send message → await settled reply text; expose liveness/activity signals for the watchdog) into a minimal protocol. `PTYRoleDriver` wraps today's injection/liveness behavior. `HeadlessRoleDriver` implements it as: first turn → prime with the role's `/granite:prime-*` (see prime-fallback bullet), capture `session_id_from_harness` from the result tuple; subsequent turns → `prior_uuid=<captured uuid>` so the harness assembles `--resume`. Reuse `get_response_via_harness`'s existing flag assembly, API-key strip, stale-UUID retry, and 16MB stream parsing — do not reimplement subprocess handling. **Turn-end authority is #1688's hook channel for BOTH transports** — the headless subprocess is spawned with the same per-session `--settings` hook set #1688 generates (writing envelopes to the same per-session NDJSON edge file); the container drains `HookEdgeConsumer.poll()` (returning `list[HookEnvelope]`) and treats a parent-`Stop` envelope classified `EdgeType.TURN_END` as the boundary, exactly as the PTY leg does post-#1688. (#1688 is still unbuilt/unmerged; this surface — `poll()` + `EdgeType.TURN_END` — is as declared by `docs/plans/granite_hook_driven_turn_returns.md`, and the build rebases onto whatever actually lands.) Do NOT invent a bespoke headless turn-end signal: the `result` event and subprocess exit are consumed only as the turn's content/usage carrier and as a crash-liveness signal (the headless analogue of #1688's `pexpect.EOF`/`!isalive()`). Wire `on_sdk_started`/`on_stdout_event` to the same watchdog activity signals PTY roles use (the PTY-liveness gates from #1789/#1798 read PTY state; headless roles substitute stdout-event recency), so a hung `claude -p` that never emits a `Stop` edge is caught by the bounded-wait watchdog #1688 already races the `EdgeType.TURN_END` envelope against.
- **Prime-under-`-p` verification + fallback (concern 5)**: it is unconfirmed that a `/granite:prime-pm-role` / `/granite:prime-dev-role` slash command *resolves and primes* correctly when passed as the first prompt to `claude -p` (project skills resolve from `.claude/skills/`; slash resolution semantics may differ in print mode). Task 0 (gate) verifies this under Substrate B (`GRANITE_OLLAMA_SMOKE=1`, qwen-pinned, reusing #1837 fixtures): assert the primed persona surfaces in the first `result`. **Fallback if slash resolution fails in `-p`:** read the prime skill's SKILL.md body and inject it via `--append-system-prompt` (or as the literal first-message preamble), so priming never depends on unverified slash behavior. The driver selects the verified path at build time; the fallback is the documented contingency, not shipped speculatively.
- **Lifecycle parity comes free from placement**: because headless roles run inside `Container.run()` under `BridgeAdapter`, the existing machinery — steering drain per turn (`bridge_adapter.py:532-543`), `on_turn` → `last_turn_at` (`:732-754`), exit summary/`exit_reason`/`user_facing_routed` (`:591-662`), executor terminal transitions (`session_executor.py:1875-1923`) — applies unchanged. This closes every gap identified in spike-2 without duplicating lifecycle code.
- **Token/cost accounting — disjoint fields, provably no clobber (blockers 1 + 2)**: the original "single-writer partition" was insufficient. The transcript tailer writes **absolute** merged totals to the shared `total_input_tokens`/`total_output_tokens`/`total_cache_read_tokens` scalars every tick (`bridge_adapter.py:1225-1229`); an additive headless write to those same scalars is deterministically clobbered on the next tick regardless of which transcripts the tailer folds. **Fix (blocker 1):** the headless leg writes a DISJOINT field set — new `metered_input_tokens`/`metered_output_tokens`/`metered_cache_read_tokens`/`metered_cost_usd` on `AgentSession` — that the tailer never reads or writes. `accumulate_session_tokens` (`sdk_client.py:286`) gains a `metered: bool = False` keyword: `metered=False` keeps today's `total_*` additive write (all existing callers — completion drafter, SDK path — unchanged); `metered=True` writes the `metered_*` fields additively and emits the `session.metered_cost_usd` ledger metric. On a mixed session the tailer's absolute `total_*` write and the headless additive `metered_*` write touch disjoint fields — no lost update, and the design is robust whether accounting is per-role or global (concern 4: the partition is by *transport/writer*, not by role, so per-role granularity is no longer the source of the race). **Fix (blocker 2):** `get_response_via_harness` already calls `accumulate_session_tokens(session_id, ...)` exactly once internally (`sdk_client.py:2542-2549`); this plan adds a `metered: bool` param that `get_response_via_harness` forwards to that single existing call, and adds **no** second turn-end accumulation. The `HeadlessRoleDriver` calls `get_response_via_harness(session_id=<sid>, metered=True, ...)`; that lone internal call is the sole metered accumulation, so headless cost is counted exactly once. Displayed grand totals = `total_* + metered_*` (summed by the serializer/analytics at read time); the metered-cost deliverable reads `metered_cost_usd` directly — on granite sessions `total_cost_usd` is always 0, so metered spend has its own dedicated field rather than an inferred slice of a shared scalar.
- **Resume handles (coordination with #1721)**: add `resume_handles` JSON field to `AgentSession` holding a list of `{role, claude_session_id, transcript_path, transport}` — the exact schema from #1721's 2026-07-02 comment (correct under both the two-role present and one-role future). Write entries at spawn/first-turn for both transports: PTY roles from the UUIDs already generated at `bridge_adapter.py:505-506` + transcript paths from `_capture_pty_identity`; headless roles from the result-event UUID + the derivable `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` path. This plan persists handles only; resume *execution* (loop cursor, `--resume` re-entry, reply-path) remains #1721's scope. If #1721 builds first and lands the field, this plan writes into it; field definition is idempotent either way (additive nullable JSON — no Popoto migration needed, but see Update System).
- **Dashboard + analytics**: `ui/app.py::_session_to_json` adds `role_transports`, the four `metered_*` fields, and a combined `total_cost_usd + metered_cost_usd` next to the existing cost fields (`:455-460`); `ui/data/analytics.py` adds `metered_cost_today_usd`/`metered_cost_7d_usd` by summing the dedicated `metered_cost_usd` field over sessions (per #1245 precedent, derived from Popoto fields — cleaner than inferring a slice of a shared scalar); the ledger metric flows into `tools/analytics` export/summary with no CLI change.
- **Territory / composing with #1688 (mandate D)**: the build's primary edits are the dispatch seam (`session_executor.py` transport resolution + the `BridgeAdapter(...)` call site at `:1745-1753`) and the additive `metered=True` keyword on two `sdk_client.py` functions. Changes inside `container.py` / `pty_driver.py` / `bridge_adapter.py` are held to the minimum the dispatch and driver-seam require, and are designed to **compose with, not conflict with, #1688's landed changes**: the role-driver seam consumes #1688's `HookEdgeConsumer.poll()` `EdgeType.TURN_END` envelope rather than adding a parallel turn-end path; the `--settings`/hook-forwarder plumbing is #1688's and is reused as-is for headless spawns; the tailer's `total_*` writes are left untouched (the headless leg only adds the disjoint `metered_*` writes). Since #1688 merges first, the build rebases onto it and extends its seam; it does not re-architect the container loop.
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
- [ ] `tests/unit/test_dashboard_pillar_a_fields.py` — UPDATE: per-session serializer test extends to the new `role_transports` key, the four `metered_*` fields, and the combined `total_cost_usd + metered_cost_usd` view.
- [ ] `tests/integration/test_analytics_dashboard.py` — UPDATE: analytics summary shape gains `metered_cost_today_usd`/`metered_cost_7d_usd` derived from the `metered_cost_usd` field.
- [ ] `tests/unit/granite_container/test_pty_driver.py`, `tests/integration/test_granite_pty_production.py`, `tests/integration/test_granite_ollama_e2e.py` — no changes expected (PTY path byte-identical under default config); re-run to confirm. NOTE: these will also gain #1688's `--settings`/hook-edge assertions once #1688 merges — this plan rebases onto that state and does not conflict with it.
- [ ] Mixed-transport accounting test (create, e.g. `tests/unit/granite_container/test_metered_accounting_partition.py`) — assert the tailer's absolute `total_*` write and the headless additive `metered_*` write never clobber each other (Race 1 / blocker 1), and that headless cost is accumulated exactly once (blocker 2 — a single `metered=True` call, no double count).

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

### Race 1: Transcript tailer vs headless accumulation on token/cost fields (driver-CONFIRMED blocker — redesigned)
**Location:** `agent/granite_container/bridge_adapter.py:1224-1232` (tailer tick writes token fields, **absolute** SET) vs the headless leg's `accumulate_session_tokens` call (`sdk_client.py:371-374`, **additive** ADD).
**Trigger:** Mixed-transport session (e.g. PM=pty, Dev=headless) — tailer task writes PTY-role token totals concurrently with a headless turn completing.
**Why the original mitigation failed:** the tailer's write is *absolute* (`session.total_input_tokens = merged_input`), not additive. Even if the tailer folds only PTY-role transcripts, its next tick overwrites the shared `total_*` scalar and discards any additive contribution the headless leg wrote to the same field — a deterministic clobber, not a timing-window race. "Partition the sources" is insufficient while both legs write the same scalar.
**Mitigation (redesigned — partition the FIELDS, not just the sources):** the headless leg writes a disjoint field set (`metered_input_tokens`/`metered_output_tokens`/`metered_cache_read_tokens`/`metered_cost_usd`) via `accumulate_session_tokens(..., metered=True)`; the tailer keeps writing only the `total_*` scalars and never touches the `metered_*` fields. The two writers now target non-overlapping fields, so neither the absolute SET nor the additive ADD can clobber the other — the lost update is impossible by construction, independent of interleave or role granularity. Displayed grand total = `total_* + metered_*`, summed at read time. Test: mixed-transport unit test asserts `total_*` equals the PTY-tailed sum AND `metered_*` equals the headless-result sum AND the combined view equals their sum (AC5, now passable).

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
- [DEPENDS-ON #1688] Building the hook channel itself (per-session `--settings` injection, hook-forwarder, `HookEdgeConsumer`, durable cursor, needs-input routing) is #1688's scope. This plan **consumes** that landed seam for headless turn-end (mandate A) — it does not build or fork it, and it does not modify PTY frame-scraping/idle detection. BUILD rebases onto #1688 (mandate B).
- [SEPARATE-SLUG #43333 in anthropics/claude-code] Upstream billing-attribution behavior of `claude -p` under OAuth — external product behavior we consume, not change. (Tagged for completeness; validator note: this is an upstream repo's issue, cited for context.)
- `ClaudeSDKClient` migration of the hand-rolled `_run_harness_subprocess` — rejected in the issue body as a separable follow-up; the hedge uses the harness that exists and works today. No issue filed yet by design (the issue's non-goals section is the record).

## Update System

- **Config validation propagates automatically**: the new `validate_transport()` registers in `validate_projects_config`, which `scripts/update/run.py` Step 4.6 already gates on (`scripts/update/verify.py:1113-1116`). A malformed `transport` block blocks the bridge restart on update — no new wiring needed.
- **No Popoto migration required**: `role_transports`, `resume_handles`, and the four `metered_*` accounting fields (`metered_input_tokens`/`metered_output_tokens`/`metered_cache_read_tokens`/`metered_cost_usd`, default 0) are additive nullable/defaulted fields; Popoto self-heals absent fields on old records (precedent: #1721 plan note on additive nullable fields, `_heal_descriptor_pollution` #1099/#1172). No entry in `scripts/update/migrations.py`.
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
- [ ] A headless-routed role completes a real session end-to-end with correct AgentSession lifecycle: terminal status via the shared orchestration path, `last_turn_at` bumps, steering drain, `exit_reason`, turn-end from a #1688 `HookEdgeConsumer.poll()` `EdgeType.TURN_END` envelope, and a persisted transport-tagged resume handle (AC2) — proven by the deterministic E2E dispatch-routing test using #1837 patterns.
- [ ] Metered-leg cost visible per session (`dashboard.json`: `role_transports` + dedicated `metered_cost_usd`) and in analytics (`session.metered_cost_usd` in export; `metered_cost_today_usd`/`metered_cost_7d_usd` in the dashboard aggregate) — cost counted exactly once, no double-count (blocker 2) (AC3).
- [ ] Runbook exists in `docs/features/per-role-transport.md` with flip procedure + post-flip checks (AC4).
- [ ] Unit routing matrix covers all four transport combinations plus invalid config; mixed transport (PM=pty, Dev=headless) exercised in unit routing tests AND passes token/cost accounting with the tailer (`total_*`) and headless (`metered_*`) writers proven non-clobbering (blocker 1 / Race 1); headless leg has a deterministic dispatch-routing test (AC5).
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

### 0. Build gate: rebase onto #1688 + verify prime-under-`-p` (HARD GATE)
- **Task ID**: gate-rebase-and-prime
- **Depends On**: #1688 PR merged to main (mandate B)
- **Validates**: manual gate + `tests/integration/test_granite_ollama_e2e.py` (new prime-under-`-p` case)
- **Informed By**: #1688 `HookEdgeConsumer` seam; concern 5 (prime slash-resolution unconfirmed)
- **Assigned To**: headless-driver-builder
- **Agent Type**: builder
- **Parallel**: false
- **Run the gate against fresh-pulled `origin/main`** (`git fetch origin main` FIRST — a local working-tree check false-passes on #1688's untracked WIP). Confirm #1688 has actually merged: a merged PR references #1688 (`gh pr list --search "1688 in:body" --state merged --json number,mergedAt`, or `gh pr view <pr> --json state` expecting `MERGED`) AND `hook_edge.py` is tracked in `origin/main` (`git cat-file -e origin/main:agent/granite_container/hook_edge.py`, or `git ls-files --error-unmatch agent/granite_container/hook_edge.py` after the pull). Only then rebase `session/per-role-transport-hedge` onto landed main.
- Verify `/granite:prime-pm-role` / `/granite:prime-dev-role` resolve and prime under `claude -p` (Substrate B, `GRANITE_OLLAMA_SMOKE=1`, qwen-pinned): spawn one headless turn with the prime slash command as the first prompt; assert the primed persona surfaces in the first `result`.
- **If slash resolution fails in `-p`:** switch the driver to the fallback — inject the prime SKILL.md body via `--append-system-prompt` / first-message preamble. Record which path was verified in this task's notes.

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
- Add `role_transports` JSON field + `resume_handles` JSON list field + four `metered_*` accounting fields (`metered_input_tokens`/`metered_output_tokens`/`metered_cache_read_tokens`/`metered_cost_usd`, default 0) to `models/agent_session.py` (nullable/defaulted, no migration; coordinate `resume_handles` shape with #1721's plan if it landed first)

### 2. Role-driver seam + headless driver
- **Task ID**: build-headless-driver
- **Depends On**: gate-rebase-and-prime, build-transport-config
- **Validates**: tests/unit/granite_container/test_headless_role_driver.py (create), tests/unit/granite_container/test_container.py
- **Informed By**: spike-1 (PairSpawnSpec/pool anchors), spike-2 (harness 8-tuple, resume-per-turn, liveness callbacks; lifecycle stays orchestration-owned), #1688 (`HookEdgeConsumer.poll()` / `EdgeType.TURN_END` seam)
- **Assigned To**: headless-driver-builder
- **Agent Type**: builder
- **Parallel**: false
- Extract the actor-turn protocol from the container loop's use of `PTYDriver`; wrap existing PTY behavior unchanged (`PTYRoleDriver`)
- Implement `HeadlessRoleDriver`: prime (verified path from Task 0), per-turn `get_response_via_harness` with `prior_uuid` resume, **turn-end via a #1688 `HookEdgeConsumer.poll()` `EdgeType.TURN_END` envelope for both transports (mandate A — no bespoke headless turn-end)**, `result` event consumed as content/usage + crash-liveness, `on_stdout_event` wired to watchdog activity
- Spawn headless subprocesses with #1688's `--settings`-injected hook set writing to the same per-session edge file (reuse #1688 plumbing; do not fork it — mandate D)
- Widen `PairSpawnSpec` with per-role transports; `_spawn_session_pair` spawns PTYs only for PTY roles; slot semantics unchanged
- Thread `role_transports` through `BridgeAdapter.__init__` → spawn spec → `Container`; persist transport-tagged `resume_handles` entries on capture (both transports); consumption deferred to #1721 (concern 3)

### 3. Cost surfacing
- **Task ID**: build-cost-surfacing
- **Depends On**: build-headless-driver
- **Validates**: tests/unit/test_dashboard_pillar_a_fields.py, tests/integration/test_analytics_dashboard.py, tests/unit/test_analytics_collector.py
- **Informed By**: spike-3 (accumulate_session_tokens, #1245 aggregate precedent, ledger auto-flow into export)
- **Assigned To**: cost-surfacing-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `metered: bool = False` to `accumulate_session_tokens` (`metered=True` → writes disjoint `metered_*` fields + emits the ledger metric) and forward it from `get_response_via_harness`; the `HeadlessRoleDriver` calls `get_response_via_harness(session_id=<sid>, metered=True)` so cost is accumulated exactly ONCE via the existing internal call (blockers 1 + 2 — disjoint fields, no second turn-end call)
- Ledger metric `record_metric("session.metered_cost_usd", cost_delta, {"role", "project"})` emitted from the metered-accumulation branch
- Add `role_transports` + four `metered_*` fields + combined `total_cost_usd + metered_cost_usd` to `_session_to_json` (`ui/app.py:455-460`); add `metered_cost_today_usd`/`metered_cost_7d_usd` (summed from the `metered_cost_usd` field) to `ui/data/analytics.py`

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
- Headless fault tests: hung subprocess (no `EdgeType.TURN_END` envelope from #1688's `HookEdgeConsumer.poll()` + no `result`) killed + classified by the bounded-wait watchdog; nonzero exit propagates `exit_reason`; empty result hits the empty-output guard
- Deterministic E2E dispatch-routing test for the headless leg (turn-end via a `HookEdgeConsumer.poll()` `EdgeType.TURN_END` envelope); mixed-transport accounting assertion: `total_*` (tailer) and `metered_*` (headless) do not clobber (blocker 1 / Race 1) AND headless cost counted exactly once (blocker 2)

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
| Disjoint metered fields exist | `grep -c "metered_input_tokens\|metered_cost_usd" models/agent_session.py` | output > 1 |
| Headless turn-end via #1688 seam | `grep -rn "HookEdgeConsumer\|EdgeType.TURN_END" agent/granite_container/ \| wc -l` | match count > 0 |
| Runbook exists | `grep -ci "runbook\|flip" docs/features/per-role-transport.md` | output > 0 |
| Anti-criterion: no tmux transport | `grep -rn "tmux" agent/granite_container/ agent/session_executor.py \| wc -l` | match count == 0 |
| Anti-criterion: no auto-switching | `grep -rn "auto_flip\|auto_switch\|policy_watcher" agent/ bridge/ config/ \| wc -l` | match count == 0 |

## Critique Results

Critique verdict **NEEDS REVISION** (2026-07-02). First revision pass addressed all findings + supervisor-mandated additions below.

**Second revision pass (2026-07-02, re-critique NEEDS REVISION — prior blockers CONFIRMED resolved):** 1 blocker + 2 concerns, all narrow. Addressed below the divider.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | 3 critics (confirmed) | Race-1 token-field lost update: tailer ABSOLUTE write vs headless ADDITIVE write on the same session-level scalars → deterministic clobber; AC5 unpassable | Disjoint `metered_*` fields | Headless leg writes `metered_input/output/cache_read_tokens` + `metered_cost_usd`; tailer keeps `total_*`; fields never overlap. Data Flow §6, Technical Approach, Race 1, AC5. |
| BLOCKER | driver-confirmed | Double-counted headless cost: `get_response_via_harness` already calls `accumulate_session_tokens` internally (`sdk_client.py:2542`); adding a turn-end call 2x-inflates | Single accumulation via `metered=True` flag | `metered` param forwarded to the ONE existing internal call; no second turn-end accumulation. Technical Approach (blocker 2), task 3. |
| CONCERN 3 | — | `resume_handles` persisted but never consumed (scope creep) | Trim + explicit defer | Write only from already-captured data (zero extra cost); consumption (loop cursor, `--resume`) explicitly deferred to #1721. Solution Key Elements, task 2. |
| CONCERN 4 | — | Per-role granularity is the sole source of Race 1 | Field partition by transport | Accounting partitioned by *transport/writer*, not role → robust either way; per-role granularity no longer the race source. Race 1, Technical Approach. |
| CONCERN 5 | — | Unverified `/granite:prime-*` behavior under headless `claude -p` | Task 0 gate + fallback | Substrate B verification; fallback injects SKILL.md body via `--append-system-prompt`. Technical Approach, task 0. |
| MANDATE A | orchestrator | Turn-end must come from #1688's hook channel for BOTH transports | `HookEdgeConsumer.poll()` / `EdgeType.TURN_END` | Headless leg drains `poll()` and honors a parent-`Stop` `EdgeType.TURN_END` envelope; `result` event demoted to content/liveness. Data Flow §5, Technical Approach, task 2. |
| MANDATE B | orchestrator | BUILD runs only after #1688 merges; rebase onto landed hook-channel | Build gate + prerequisite row | Prerequisites row + Build sequencing note + task 0. |
| MANDATE C | orchestrator | Resume-handle schema `{role, claude_session_id, transcript_path, transport}` verbatim, transport-agnostic | Schema adopted verbatim | Solution Key Elements + Risk 4; no PTY-specific field names. |
| MANDATE D | orchestrator | Keep container/pty_driver/bridge_adapter changes minimal; compose with #1688 | Territory note | Technical Approach → "Territory / composing with #1688"; primary edits at dispatch seam + additive `metered` kwarg. |
| **BLOCKER (rev 2)** | re-critique | Build gate `grep -rl "HookEdgeConsumer" agent/granite_container/` false-passes today: #1688's build exists as UNTRACKED working-tree WIP (`hook_edge.py`, modified `transcript_tailer.py`), so a working-tree grep cannot tell "merged to main" from "local WIP" | Gate verifies actual MERGE state | Prerequisites gate now: `git fetch origin main` + merged-PR check (`gh pr list --search "1688 in:body" --state merged`) + tracked-in-`origin/main` blob (`git cat-file -e origin/main:agent/granite_container/hook_edge.py`). No bare grep. Prerequisites row, Build sequencing note, task 0. |
| **CONCERN (rev 2)** | re-critique | Imprecise #1688 API: plan cited `HookEdgeConsumer.turn_end` as a member; actual surface is `poll()` → `list[HookEnvelope]` classified by `EdgeType.TURN_END` enum (per #1688's WIP `hook_edge.py` + its plan doc) | Rewrote to `poll()` / `EdgeType.TURN_END` | All integration references now name `HookEdgeConsumer.poll()` draining a parent-`Stop` `EdgeType.TURN_END` envelope; wording notes #1688 is unbuilt and the build rebases onto whatever lands. Data Flow §5, Technical Approach, tasks 2/4, AC2, Verification. |
| **CONCERN (rev 2)** | re-critique | Stale-gate hazard generally: local WIP could satisfy the gate | Gate must run against fresh-pulled `origin/main` | Task 0 and Build sequencing note now mandate `git fetch origin main` FIRST, and the gate checks `origin/main` (not the working tree). Task 0, Build sequencing note. |

---

## Open Questions

1. **Both-headless combination**: it falls out of per-role selection naturally and the routing matrix covers it at the unit level, but should it be *supported* (documented in the runbook) or *validation-rejected* in v1? Proposed default: supported but flagged in the runbook as untested beyond unit routing (no E2E claim).
2. **Slot accounting for headless roles**: this plan keeps one pool slot per session regardless of transport (concurrency bound = sessions, simple and safe). If the intent of a headless flip is to *increase* concurrency beyond PTY pool size, that's a follow-up knob. Confirm the simple semantics are acceptable for v1. Proposed default: yes, keep it simple.
3. **`resume_handles` field introduction order**: if #1721 starts building before this plan, its builder introduces the field and this plan writes into it; otherwise this plan introduces it per the agreed schema. Both plans reference the same shape, so this is sequencing awareness rather than a decision — flagging in case #1721 is deliberately being held for the native-subagents readout. Proposed default: whichever builds first introduces the field; no coordination gate needed.
