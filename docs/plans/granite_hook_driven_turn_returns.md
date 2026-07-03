---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1688
last_comment_id: 4862203800
---

# Hook-Driven Turn Returns for the Granite PTY Shuttle

## Problem

The granite container drives interactive `claude` TUI sessions (PM and Dev) over PTYs. Two of its most load-bearing decisions are still made by **heuristic**, not by a first-class signal:

1. **Turn boundaries** are guessed by PTY idle-polling (`_cycle_idle` → `read_until_idle`, the C5 quiescence heuristic in `agent/granite_container/pty_driver.py`). This ~1s byte-silence heuristic oscillates on mid-turn lulls and creates a **flush-timing race**: after the PTY *looks* idle we read "the last assistant entry" from the JSONL transcript, with no guarantee the entry is the just-completed turn (documented residual at `transcript_tailer.py:444`).
2. **"Needs human input"** (the `[/user]` route) is *inferred*. There is no first-class signal that a PM/Dev session is blocked on a question or a permission prompt.

**Current behavior:** turn-end is a quiescence guess; a Task-bearing PM turn (tool round-trips, subagent fan-out) makes the guess strictly worse, and a stale/partial transcript entry can be forwarded as the turn's answer. Needs-human routing depends on the classifier reading a self-reported token.

**Desired outcome:** Claude Code's own hook event stream supplies the turn-end edge (Stop) and the needs-human edge (Notification / PermissionRequest / `AskUserQuestion`) deterministically, through **one transport-agnostic consumer seam** that works whether the container drives two PTYs, one PTY, or a headless role. The PTY is demoted to what it is good at: injection, a running/idle badge, and crash/liveness detection. The scraper **shrinks** (turn-end no longer scraped); a companion startup pre-authorization task shrinks the startup scrape surface too. This does not delete the scraper by itself, and it does not retire the `[/dev]`/`[/user]`/`[/complete]` token protocol.

## Freshness Check

**Baseline commit:** `7592dd256f61186129b21e7328d75bad4a4f2757`
**Issue filed at:** 2026-06-14T15:54:20Z (binding scope-revision comment: 2026-07-02, incorporated below)
**Disposition:** Minor drift

**File:line references re-verified (against baseline HEAD):**
- `agent/granite_container/container.py` — `_cycle_idle` defined at **line 917** (issue cited `~922-958`); calls `pty.read_until_idle`. Confirmed present, minor line drift.
- `agent/granite_container/pty_driver.py` — `spawn()` builds `args = ["--model", model, "--permission-mode", "bypassPermissions"]` + conditional `--session-id` at **lines 399-400**; **no `--settings` flag** (issue cited `382-384`). `_extra_env` overlay at **line 346**; applied with `env.update()` at **lines 402-404**. Confirmed: env overlay ADDS/overwrites, never removes (see `tests/granite_faults/ollama_env.py` OAuth-pop note).
- `agent/granite_container/pty_driver.py` — `_RESUME_HINT_RE` at **line 82**, `last_resume_uuid()` at **line 636**, `read_until_idle` C5 heuristic at **lines 490-600**, `pexpect.EOF`/`isalive()` handling confirmed. Matches issue claims.
- `agent/granite_container/transcript_tailer.py:444` — carries the exact `# … deterministic fix is the hook-driven Stop signal in followup #1688` comment. Confirmed verbatim.
- `agent/granite_container/granite_classifier.py` — `classify_pm_prefix` pure regex, zero-ollama at runtime. Confirmed (hooks replace the *heuristics*, not the classifier).
- `.claude/hooks/subagent_stop.py` — exists; logs subagent completions. `.claude/hooks/stop.py` + `agent/hooks/stop.py` exist but are unrelated to granite turn-end (SDLC/delivery-gate hooks). Confirmed absent granite wiring.
- `tests/granite_faults/` — MERGED (#1837 / PR #1839). `scenarios.py` defines `turn_detection_wedge` (failure_class 1, seam `pty_driver.read_until_idle`) and `ollama_env.py` builds Substrate B env (`GRANITE_OLLAMA_SMOKE=1`, qwen-pinned, OAuth-pop). Confirmed as the red-first substrate.
- `agent/granite_container/bridge_adapter.py:545-551` — `Container(...)` wired with `on_turn=self._bump_last_turn_at` and `on_pty_read=self._make_pty_read_callback()`; PTY pair acquired via `PairSpawnSpec` carrying `env=self._session_env`, `pm_session_id`, `dev_session_id` (UUIDs → deterministic transcript paths). This is the seam where per-session settings injection is wired.

**Cited sibling issues/PRs re-checked:**
- #1681 — CLOSED (zero-LLM transcript-content shuttle shipped; the content-swap this complements).
- #1732 — CLOSED (Omnigent reference map; practices homed to `docs/features/omnigent-hook-edge-reference.md`).
- #1837 / PR #1839 — CLOSED / MERGED 2026-07-01 (failure-simulation harness; the red-first substrate).
- PR #1840 — MERGED (qwen-pins Substrate B ollama backend).
- #1719 — CLOSED (bridge canned-fallback routing fix + a Stop-hook *completion floor* for the delivery gate — a different Stop-hook layer, not granite turn-end wiring; Practice 9 homes here).
- #1745 — MERGED (`WAITING:` sentinel + `/goal` Stop-hook in PM prime; per issue comment, `WAITING:` is a `/goal`-evaluator transcript affordance, NOT a granite routing signal — it does not compete with #1688's granite-level Stop edge).
- #1721 — OPEN (lossless checkpoint resume; persists resume handles + loop cursor). Adjacent, not blocking — durable hook cursor here (Practice 4) is complementary.
- #1842 — OPEN (per-role transport hedge: PTY vs headless `claude -p`). This is exactly why the hook channel must be built transport-agnostically.

**Commits on main since issue was filed (touching referenced files):** many (`git log` shows the granite area is hot — #1801 mid-run steering, #1815/#1823 liveness-wedge recovery, #1816/#1832 fault containment, #1728 stalled-session recovery). None wire a granite turn-end Stop hook; the idle heuristic remains the completion authority. Line-number drift only, corrected above.

**Active plans in `docs/plans/` overlapping this area:** `granite_lossless_checkpoint_resume.md` (#1721 — resume handles/cursor) is adjacent but non-overlapping (it owns resume state; this owns turn-end/needs-input edges). No conflict; the durable hook cursor here should be designed to coexist with #1721's loop cursor.

**Notes:** Minor line drift only; premises hold. Corrected line numbers are used in Technical Approach below.

## Prior Art

- **#1732 (CLOSED)** — Omnigent `claude_native_*` reference map. Established Stop/StopFailure as the authoritative turn-end edge (vs PTY quiescence) and homed nine practices to `docs/features/omnigent-hook-edge-reference.md`. Practices 1, 2, 7 home to #1688; NEW practices 3, 4, 5, 6, 8 are consumed by this plan.
- **#1681 (CLOSED)** — zero-LLM transcript-content shuttle. Stopped scraping the painted frame for message content; reads `last_assistant_text(transcript_path)` from the JSONL. This plan removes the remaining *heuristics* (turn-boundary + needs-input) on top of that content-swap.
- **#1719 (CLOSED)** — added a Stop-hook *completion floor* to the bridge delivery gate (`agent/hooks/stop.py`). Different layer (delivery review gate), does not wire granite turn-end. Confirms Stop hooks are already used elsewhere in the repo (no new hook infrastructure risk).
- **#1837 / PR #1839 (MERGED)** — failure-simulation harness. `turn_detection_wedge` is the pre-existing red test this plan turns green; Substrate B verifies Stop hooks fire under the real ollama-backed binary.

**No prior attempt wired a granite-level turn-end Stop hook** (spike-2 of #1732 confirmed absent). This is greenfield within the granite container, so there is no "Why Previous Fixes Failed" section.

## Research

**Queries used:**
- "Claude Code hooks Stop SubagentStop payload agent_id transcript_path settings flag reference"
- "Claude Code --settings flag per-session hooks configuration Notification PermissionRequest hook JSON payload"

**Key findings** (source: [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks)):
- **Stop auto-converts to SubagentStop** for Task-tool subagents. Subagent-running events add `agent_id` and `agent_type` to the payload. This is **native disambiguation** — the event *type* distinguishes a parent PM/Dev turn-end from a subagent-end, so Practice 5 (subagent-hook filtering) is satisfied by keying on `hook_event_name == "Stop"` (parent) vs `"SubagentStop"` (child), not by a filtering heuristic. Confirms the 2026-07-02 scope-revision point #4. Caveat: [anthropics/claude-code#7881](https://github.com/anthropics/claude-code/issues/7881) documents that SubagentStop historically shared session IDs; `agent_id`/`agent_type` are the fields that resolve it — Task 0 must verify these fields are populated under the fleet's pinned `claude` version.
- **Common payload fields on every event:** `session_id`, `transcript_path`, `cwd`, `hook_event_name`; plus `permission_mode` on per-turn/tool events, `effort` on tool/subagent events, `agent_id`/`agent_type` inside a subagent. The Stop payload's `transcript_path` names the exact JSONL to read the final assistant message from — eliminating the flush race.
- **`PermissionRequest` hook** fires when the permission dialog would be shown and supports decision control (allow/deny). Combined with `Notification` and `PreToolUse(matcher: AskUserQuestion)`, this is the deterministic "needs human" edge → `[/user]` route.
- **Per-session hooks** are configurable via `claude --settings <path>` (CLI flags are one of the five settings sources). This is the injection seam: generate a per-session `settings.json` registering the hooks and pass `--settings` on spawn.
- Hooks are **client-side** (the Claude Code process runs them regardless of model backend), so they *should* fire under the ollama-backed binary — but that is exactly the class of assumption Task 0's Substrate B fidelity check exists to confirm before the design relies on it.

## Data Flow

**Happy path (turn-end):**
1. **Entry:** bridge enqueues an `AgentSession`; `BridgeAdapter.run()` acquires a PTY pair with per-session UUIDs and `env`, spawns `claude --session-id <uuid> --settings <per-session-settings.json>`.
2. **Injection:** container writes the user message / prime to the PM PTY (verified-submit; Practice 6).
3. **Turn runs:** `claude` streams; PTY read-loop drives only the running/idle **badge** + liveness (Practice 2), no longer the completion decision.
4. **Stop edge:** on clean turn exit, the Stop hook subprocess fires, appending one NDJSON envelope `{event, session_id, transcript_path, cwd, hook_event_name, ts}` to the per-session edge file (Practice 3).
5. **Consumer:** the `HookEdgeConsumer` (one seam, keyed by `session_id`) tails the edge file with a durable cursor (Practice 4), maps `session_id → role`, and on a parent `Stop` (not `SubagentStop`) declares the turn complete.
6. **Content read:** the consumer reads the final assistant message from the payload's `transcript_path` (structural, flush-safe) — no idle guess, no stale-entry race.
7. **Route:** existing classifier applies to the structural content; delivery/routing unchanged downstream.

**Needs-human path:**
- A `Notification` / `PermissionRequest` / `PreToolUse(AskUserQuestion)` hook fires mid-turn → edge envelope → consumer maps directly to the `[/user]` route deterministically (no classifier guess).

**Crash path (hooks do NOT fire):**
- The bounded Stop wait races a watchdog. On `pexpect.EOF` / `!isalive()` / idle-with-no-Stop-timeout, the PTY supervisor detects death, resumes the same claude session via captured `--resume <uuid>` (`last_resume_uuid`), verified-submits `continue`, and re-arms the Stop wait. Repeated crashes on one turn hit a retry cap → operator-terminal escalation (never an infinite loop).

**Compaction path:**
- A `PreCompact` / `SessionStart source=compact` hook edge is forwarded as a compaction-status event and explicitly **not** read as a Stop (Practice 8) — mid-turn compaction no longer looks like completion.

## Architectural Impact

- **New dependencies:** none external. New internal modules: a hook-forwarder script (registered in the generated per-session settings), a `HookEdgeConsumer`, and a per-session settings generator.
- **Interface changes:** `PTYDriver.spawn()` gains a `--settings <path>` arg (settings path derived from a new spawn/env input). `Container` gains a turn-end source that is the hook edge consumer rather than `_cycle_idle`'s `saw_idle`. `read_until_idle` loses *completion authority* but is retained for liveness/badge/crash detection.
- **Coupling:** decreases coupling to TUI paint (turn-end no longer depends on bar/glyph regexes). Adds coupling to Claude Code's hook payload contract (mitigated by Task 0 fidelity check + a pinned-version assertion).
- **Data ownership:** the edge file + durable cursor are new per-session state owned by the container. Must coexist with #1721's loop cursor (separate concern).
- **Reversibility:** high — the hook edge is additive; the idle heuristic code stays in place (demoted). A feature flag / fallback to idle-completion is a cheap safety valve if hooks misbehave in production.

## Appetite

**Size:** Large

**Team:** Solo dev, PM check-ins, code reviewer, test-engineer

**Interactions:**
- PM check-ins: 2-3 (transport-agnostic seam shape, crash-path race semantics, startup pre-auth scope)
- Review rounds: 2 (design review on the edge channel + consumer; code review on the crash-path race and subagent filtering)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| ollama installed + reachable (Substrate B) | `curl -s http://localhost:11434/api/tags >/dev/null && echo ok` | Task 0 real-binary Stop-hook fidelity check |
| qwen model pulled (Substrate B pin) | `curl -s http://localhost:11434/api/tags` | qwen-pinned per PR #1840 (grep for qwen in output) |
| claude binary on PATH | `command -v claude` | PTY spawn target |
| Pinned claude version documented | `claude --version` | Verify Stop/SubagentStop agent_id/agent_type payload fields (docs ~v2.1.172+) |

Run via `python scripts/check_prerequisites.py docs/plans/granite_hook_driven_turn_returns.md`.

## Spike Results

### spike-0 (Task 0, becomes a gating build task): Stop-hook fires under Substrate B
- **Assumption:** "Stop / SubagentStop hooks fire under the ollama-backed `claude` binary, and their payloads carry `transcript_path` and (for subagents) `agent_id`/`agent_type` on the fleet's pinned version."
- **Method:** prototype (Substrate B, `GRANITE_OLLAMA_SMOKE=1`, qwen-pinned) — spawn a real `claude` with a `--settings` file registering a Stop hook that appends to a temp file; drive one Task-bearing turn with a subagent fan-out; assert a parent `Stop` envelope and a distinct `SubagentStop` envelope (with `agent_id`) both land.
- **Finding:** **GATE PASSED** (2026-07-02, `claude --version` = **2.1.198 (Claude Code)**, Substrate B backend `qwen3.6:35b-a3b-coding-nvfp4`). A real `claude` TUI spawned with `--settings <path>` registering `Stop` + `SubagentStop` hooks fired **both** events on a single Task-bearing turn (one general-purpose subagent fan-out; run completed in ~126s). Observed payload shapes: **parent `Stop`** carries `session_id`, `transcript_path` (absolute JSONL path — the flush-safe content source), `cwd`, `prompt_id`, `permission_mode`, `effort`, `hook_event_name="Stop"`, `stop_hook_active`, `last_assistant_message`, `background_tasks`, `session_crons` — and **no** `agent_id`/`agent_type`. **`SubagentStop`** carries all the common fields **plus** `agent_id`, `agent_type` (`"general-purpose"`), and `agent_transcript_path` (the subagent's own JSONL); its `transcript_path` is the *parent's* transcript and its `session_id` equals the parent session — so disambiguation MUST key on `hook_event_name` (native, per Practice 5), never on `session_id`. Bonus finding: `last_assistant_message` is delivered inline in both payloads (the SubagentStop carried the subagent's answer, the Stop carried the parent's final text), which may let the consumer skip the transcript read entirely on the happy path. The hooks are client-side and fire identically under the ollama backend. Durable re-check: `TestStopHookFidelityGate` in `tests/integration/test_granite_ollama_e2e.py` (probe helper: `tests/granite_faults/hook_fidelity.py`).
- **Confidence:** high (verified 2026-07-02)
- **Impact if false:** if Stop does not fire under Substrate B, the whole hook-driven approach is invalid → fall back to a transcript-flush-confirmation design (bounded wait on transcript text-count growth) and re-scope. This is why Task 0 is a hard gate, not an assumption.

### spike-1: edge transport choice
- **Assumption:** "An append-only NDJSON edge file per session + durable cursor (Practice 3+4) is simpler and more restart-safe than a Redis list, and honors the repo's Popoto-only Redis rule."
- **Method:** code-read (Omnigent `claude_native_bridge.py` fsync+os.replace pattern; repo's raw-Redis prohibition).
- **Finding:** append-only file + `(event_cursor, byte_offset, cursor_fingerprint)` cursor avoids new Popoto models and raw-Redis ops entirely; the hook subprocess writes a file path (no Redis client in the hook); the consumer owns the cursor. Confirmed as the design baseline.
- **Confidence:** high
- **Impact on plan:** edge channel = per-session NDJSON file under a known dir; consumer holds the durable cursor.

## Solution

### Key Elements

- **Per-session settings injection** — a generated `settings.json` (passed via `claude --settings <path>` on spawn) registering `Stop`, `SubagentStop`, `Notification`, `PreToolUse(AskUserQuestion)`, `PermissionRequest`, and `PreCompact`/`SessionStart` hooks pointing at the hook-forwarder. The forwarder destination (edge file path) is carried in the per-session env overlay (`_extra_env`), alongside `AGENT_SESSION_ID`.
- **Hook-forwarder** — a tiny script that reads the hook payload on stdin and appends one NDJSON envelope to the per-session edge file (atomic append; never raises; Practice 3). One binary, all events.
- **HookEdgeConsumer (the one transport-agnostic seam)** — tails the per-session edge file by `session_id` with a durable, idempotent cursor `(event_cursor, byte_offset, cursor_fingerprint)` (Practice 4). Maps `session_id → role`. Emits typed edges: `turn_end` (parent `Stop`), `subagent_end` (`SubagentStop`, ignored for turn boundary — Practice 5), `needs_human` (`Notification`/`PermissionRequest`/`AskUserQuestion`), `compaction` (Practice 8). Works identically for two PTYs, one PTY, or a headless role — it never touches the PTY.
- **Turn-boundary authority swap** — `Container` waits on the consumer's `turn_end` edge (racing the crash/timeout watchdog) instead of `_cycle_idle`'s `saw_idle`. On `turn_end`, read the final assistant message from the payload's `transcript_path`.
- **PTY demotion** — `read_until_idle` retained for the running/idle badge, liveness, and crash detection (`pexpect.EOF`, `!isalive()`). It no longer decides completion (Practice 2).
- **Bounded Stop wait + crash-resume** — the wait is a race: `turn_end` edge vs (EOF/`!isalive()`/no-Stop-timeout). On crash → resume via `last_resume_uuid`, verified-submit `continue` (Practice 6), re-arm. Retry cap → operator escalation.
- **Startup pre-authorization (companion task, honest framing)** — trusted-dirs / permission-mode pre-auth so the trust-folder dialog and permission bar during startup are pre-answered, shrinking the `startup_parser` scrape surface. Stop shrinks the steady-state scrape; this shrinks the startup scrape; together → near-zero scrape.

### Flow

Bridge enqueues session → BridgeAdapter spawns `claude --session-id <uuid> --settings <gen.json>` (edge path in env) → PM turn runs → **Stop hook fires** → forwarder appends envelope → HookEdgeConsumer reads `turn_end` → Container reads final message from `transcript_path` → classifier routes → delivery.

Needs-human: mid-turn `PermissionRequest`/`AskUserQuestion`/`Notification` hook → envelope → consumer emits `needs_human` → `[/user]` route.

Crash: no Stop → watchdog trips on EOF/`!isalive()`/timeout → resume `--resume <uuid>` → verified `continue` → re-arm Stop wait (bounded by retry cap → escalate).

### Technical Approach

- **Injection seam:** extend `PairSpawnSpec` / `PTYDriver` so `spawn()` appends `--settings <path>` when a per-session settings path is provided; generate the settings file once per session (per PTY, since each has its own `session_id`). Keep the `--permission-mode bypassPermissions` arg. Carry the edge-file path in `_extra_env`.
- **Subagent disambiguation is native:** key turn-end on `hook_event_name == "Stop"`; treat `"SubagentStop"` as `subagent_end` (never ends the parent turn). Assert `agent_id`/`agent_type` presence on subagent payloads in Task 0. This satisfies Practice 5 without a filtering heuristic.
- **Completion decoupled from injection (Practice 7):** the write→wait block becomes write→(await turn_end edge, racing watchdog). Injection returns promptly; completion arrives asynchronously via the edge.
- **Durable cursor (Practice 4):** persist `(event_cursor, byte_offset, cursor_fingerprint)` so a worker restart replays only unseen edges and never double-delivers; fingerprint detects truncation/replacement before seeking a stale offset. Coexists with #1721's loop cursor (separate key).
- **Compaction (Practice 8):** map `PreCompact` / `SessionStart source=compact` to a `compaction` edge; the consumer never treats it as `turn_end`.
- **Feature-flag safety valve:** gate the hook-driven completion behind a flag defaulting on, with idle-completion as the documented fallback, so production can revert instantly if a `claude` version regresses the hook contract.
- **Red-first:** `tests/granite_faults/` `turn_detection_wedge` (idle bar removed → `saw_idle=False` forever under the heuristic) must go **green** because turn-end now comes from the Stop edge, independent of the bar. Substrate B (Task 0) proves the edge fires under the real binary.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The hook-forwarder must be fail-silent (a hook subprocess that raises would surface a stderr blob into the TUI / block the turn). Test: forwarder given a malformed/oversized payload appends nothing and exits 0 (assert edge file unchanged, exit 0).
- [ ] The `HookEdgeConsumer` must never crash the run on a corrupt/partial NDJSON line (mirror `last_assistant_text` fail-silent). Test: a truncated trailing line is skipped; a garbage line is skipped with a `logger.warning`, cursor still advances past complete lines.
- [ ] Any `except Exception` in the new consumer/adapter wiring must assert observable behavior (logger.warning + state unchanged), mirroring the existing `on_turn`/`on_pty_read` callback guards at `container.py:1351-1355`.

### Empty/Invalid Input Handling
- [ ] Stop edge whose `transcript_path` is missing/unreadable → consumer emits `turn_end` but content read returns "" (existing `last_assistant_text` empty-string contract); assert this does NOT deliver a stale prior-turn entry.
- [ ] Empty edge file / no Stop within watchdog window while PTY alive → **not** treated as completion (the whole point): assert the consumer keeps waiting (alive + quiet + no Stop = still running, per the reference doc's dead-vs-stalled table), and only the watchdog+EOF combination triggers resume.
- [ ] `SubagentStop` arriving while the parent turn runs → consumer must NOT emit `turn_end` (assert against the `turn_detection` harness with a scripted subagent-end envelope).

### Error State Rendering
- [ ] Crash-during-turn (kill the Dev `claude` mid-turn) → assert granite detects it (no Stop fired), resumes via `--resume <uuid>`, verified-submits `continue`, and completes on the subsequent clean Stop (issue acceptance criterion).
- [ ] Retry cap exhausted (repeated crashes on one turn) → operator-terminal escalation message delivered to the user, not an infinite loop (issue acceptance criterion).
- [ ] Needs-human edge → `[/user]` route surfaces to the human deterministically; integration test proves a blocked-on-question Dev session routes without the classifier guessing (issue acceptance criterion).

## Test Impact

- [ ] `tests/granite_faults/` `turn_detection_wedge` scenario — UPDATE: today it asserts `saw_idle=False` + bounded wait when the bar is stripped; add/extend an assertion that with the hook edge present, turn-end is still detected (the wedge no longer wedges). The failure_class-1 red test turns green via the Stop edge.
- [ ] `tests/integration/test_granite_ollama_e2e.py` — UPDATE: add a Substrate B case (Task 0) asserting a Stop envelope lands under the ollama-backed binary, plus a distinct SubagentStop with `agent_id`.
- [ ] Any unit test asserting `_cycle_idle`/`read_until_idle` is the *completion* authority — UPDATE: re-scope to assert it drives badge/liveness only; completion assertions move to the hook-edge consumer tests. (Builder to enumerate exact cases via `grep -rn "read_until_idle\|_cycle_idle\|saw_idle" tests/` at build time.)
- [ ] `bridge_adapter` tests exercising `Container(...)` construction — UPDATE: the container now also receives a turn-end edge source; update construction fixtures/mocks.
- [ ] New tests (REPLACE nothing; additive): hook-forwarder unit tests, `HookEdgeConsumer` cursor/idempotency/subagent-filter/compaction tests, crash-path resume integration test, needs-human routing integration test.

No existing test is DELETEd — the idle heuristic is demoted, not removed, so its liveness/badge tests remain valid.

## Rabbit Holes

- **Full verified-submit injection overhaul (Practice 6 in its maximal form).** Omnigent's `tmux load-buffer`+`paste-buffer -p` bracketed-paste, poll-until-committed, >16KB-safe injection is a large sub-project. Scope here to the load-bearing minimum: verified submit for the **crash-resume `continue` nudge** and the initial prime/message (a dropped `continue` re-wedges the crash path). The general >16KB paste-coalescing rework is out of scope.
- **Retiring the `[/dev]`/`[/user]`/`[/complete]` token protocol.** Tempting once Stop supplies turn-end, but the 2026-07-02 scope revision defers this to the single-session (1-PTY / native-subagents) prototype. `[/complete]` (agent-authored "work done") survives in any architecture. Do not touch the classifier's routing tokens here.
- **Redis-backed edge transport / new Popoto models.** The append-only NDJSON file + durable cursor is simpler, restart-safe, and sidesteps the Popoto-only Redis rule. Do not build a Redis edge queue.
- **Fork-on-resume guard + full lossless resume state.** That is #1721's territory. Reuse `last_resume_uuid` for the crash-resume nudge; do not build the resume-handle persistence layer here.
- **Deleting the scraper.** Honest framing: this shrinks it. Do not attempt to remove `startup_parser` or `read_until_idle` — startup dialogs and liveness still need the PTY.

## Risks

### Risk 1: Stop hooks do not fire (or payload fields absent) under the fleet's pinned `claude`
**Impact:** the entire hook-driven completion authority is invalid; sessions would hang waiting for an edge that never comes.
**Mitigation:** Task 0 is a hard gate — verify under Substrate B before any consumer wiring lands. Feature flag defaults to hook-driven but retains idle-completion fallback. Pin-version assertion in Prerequisites + a doc note in the reference doc.

### Risk 2: Subagent Stop ends the parent turn early (Practice 5 regression)
**Impact:** a Dev turn ends the instant a builder/reviewer subagent finishes → truncated/wrong output (the exact load-bearing failure the issue flags).
**Mitigation:** key turn-end strictly on `hook_event_name == "Stop"`; `SubagentStop` is a distinct edge type that never ends the parent. Task 0 asserts the two events are distinguishable (agent_id present on subagent). Dedicated harness test with a scripted subagent-end envelope.

### Risk 3: Worker restart double-delivers or replays stale edges
**Impact:** a completed turn re-delivered, or a stale offset read after edge-file truncation.
**Mitigation:** durable cursor `(event_cursor, byte_offset, cursor_fingerprint)` (Practice 4); fingerprint detects truncation/replacement before seeking; idempotency test across a simulated restart.

### Risk 4: The Stop wait blocks unbounded when hooks silently stop
**Impact:** a wedged session that never returns (the historical failure mode).
**Mitigation:** the Stop wait is always a race against the crash/timeout watchdog; "alive + quiet + no Stop past watchdog" is disambiguated (still-running vs dead) per the reference doc; retry cap → escalate. Never an unbounded block on the hook.

## Race Conditions

### Race 1: Stop edge lands before the consumer arms its wait
**Location:** `Container` turn loop ↔ `HookEdgeConsumer` (new); analogous to `bridge_adapter.py:545-560` construction ordering.
**Trigger:** a very fast turn (cached/short) fires Stop before the consumer starts tailing.
**Data prerequisite:** the edge file must be created (or its path reserved) before the PTY is written to.
**State prerequisite:** the durable cursor starts at offset 0 for a fresh session.
**Mitigation:** the consumer is level-triggered against the append-only file (reads from cursor, not edge-triggered), so a Stop written before the wait arms is still read when the wait begins — no missed edge. Create/reserve the edge path at spawn, before the first `write()`.

### Race 2: crash detected concurrently with a late Stop edge
**Location:** the bounded Stop-wait race (new watchdog).
**Trigger:** the turn completes (Stop written) at nearly the same moment the PTY hits EOF (clean exit after Stop).
**Data prerequisite:** the edge must be read before EOF is interpreted as a crash.
**State prerequisite:** turn-end takes precedence over EOF when both are observable in the same window.
**Mitigation:** on watchdog wake, drain the edge file to cursor FIRST; if a `turn_end` is present, honor it and treat the following EOF as a normal post-turn exit, not a crash. Only EOF with no unread `turn_end` triggers resume.

### Race 3: subagent edge interleaved with parent Stop in the shared edge file
**Location:** the single per-session edge file (subagents inherit the parent's hook settings → same destination).
**Trigger:** a subagent finishes microseconds before/after the parent turn ends.
**Data prerequisite:** both envelopes carry distinct `hook_event_name`.
**State prerequisite:** ordering within the append-only file is preserved (atomic append).
**Mitigation:** the consumer classifies by `hook_event_name`; a `SubagentStop` never advances turn-boundary state regardless of interleave position. Atomic single-line append guarantees no torn envelopes.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1842] Retiring the `[/dev]`/`[/user]` token protocol and the classifier regex — deferred to the single-session (1-PTY / native-subagents) prototype that consumes this issue's hook channel; `[/complete]` survives in any architecture.
- [SEPARATE-SLUG #1721] Lossless resume-handle + loop-cursor persistence (fork-on-resume guard) — this plan reuses `last_resume_uuid` for the crash-resume nudge only; it does not build the resume-state persistence layer.
- [ORDERED] Enabling the hook-driven path as the *sole* completion authority in production (flag flipped to remove the idle fallback) — waits until Task 0 + Substrate B + a soak period confirm the hook contract holds on the fleet's pinned `claude`; the fallback removal is a follow-up gated on that evidence.
- Full >16KB bracketed-paste verified-submit injection overhaul — the load-bearing minimum (verified `continue` nudge + prime submit) is in scope; the general paste-coalescing rework is not. Tracked as a build-time note, not a promise. Rationale: not a separable deliverable worth its own issue yet; revisit if injection drops recur.

## Update System

- **`scripts/update/run.py`:** no changes required — the hook-forwarder and generated per-session settings are internal to the container and shipped with the repo. No new machine-level install step.
- **`scripts/update/migrations.py`:** no changes — no Popoto model schema change (the durable cursor is per-session file state, not a Popoto model). If the durable cursor is later chosen to live on the `AgentSession` model, add an idempotent migration then; the current design keeps it as file state, so **no migration needed**.
- **New dependencies:** none to propagate (no external packages).
- The hook-forwarder script must be included on PATH for the spawned `claude` (via absolute path in the generated settings), so it works regardless of cwd — verify in Task 0.

## Agent Integration

- **No new MCP surface / `.mcp.json` change.** This is a bridge-internal change to how the granite container detects turn boundaries and needs-human states. The agent already reaches the granite container via the bridge → `BridgeAdapter` → `Container` path; that path is unchanged from the agent's perspective.
- **`bridge/telegram_bridge.py`:** no direct import change — it enqueues `AgentSession`s as today; the hook-edge machinery is entirely inside `agent/granite_container/`.
- **Integration tests** verify the capability end-to-end: (a) a real Substrate B turn produces a Stop edge the consumer reads; (b) a blocked-on-question session routes to `[/user]` via the needs-human edge; (c) a crash mid-turn resumes and completes. These are the agent-observable behaviors.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/granite-hook-driven-turn-returns.md` describing the hook edge channel, the transport-agnostic consumer seam, the two-layer (hook happy-path + PTY crash-path) architecture, and the feature flag.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/omnigent-hook-edge-reference.md` — mark Practices 1, 2, 3, 4, 5, 7, 8 as consumed by #1688 (with the implementing module paths) and re-verify the Omnigent citations' pin note.
- [ ] Update `docs/features/granite-pty-production.md` (if it documents the idle-completion model) to reflect the demotion of `read_until_idle` to liveness/badge.

### Inline Documentation
- [ ] Docstrings on the hook-forwarder, `HookEdgeConsumer`, the durable-cursor struct, and the crash-path race in `Container`.
- [ ] A comment at `transcript_tailer.py:444` updating the "deterministic fix is #1688" residual note to point at the shipped consumer.

## Success Criteria

- [ ] Spawned PM/Dev sessions run with `--settings` registering Stop/SubagentStop/Notification/PermissionRequest/AskUserQuestion/PreCompact hooks routed to the per-session edge file.
- [ ] Container consumes the parent `Stop` edge (not idle-polling) as turn-complete, then reads the final assistant message from the payload's `transcript_path`.
- [ ] `SubagentStop` never ends the parent turn (Practice 5) — proven by a harness test with a scripted subagent-end envelope.
- [ ] `Notification`/`PermissionRequest`/`AskUserQuestion` edges drive the `[/user]` route deterministically — integration test proves a blocked Dev session routes to the human without the classifier guessing.
- [ ] No flush-timing race: the previously-racy `turn_detection_wedge` (idle bar removed) now completes via the Stop edge; the final entry read is the Stop-confirmed entry.
- [ ] Bounded Stop wait: no Stop within watchdog window AND PTY EOF/`!isalive()` → resume via `--resume <uuid>`, verified `continue`, re-arm.
- [ ] Crash-during-turn integration test passes: kill Dev `claude` mid-turn → detect (no Stop) → resume → `continue` → complete on subsequent clean Stop.
- [ ] Retry cap: repeated crashes on one turn escalate (operator-terminal message), no infinite loop.
- [ ] Durable cursor is idempotent across a simulated worker restart (no double-delivery, truncation-safe).
- [ ] Compaction edge is forwarded, not mistaken for completion (Practice 8).
- [ ] Task 0: Stop hook fires under Substrate B (`GRANITE_OLLAMA_SMOKE=1`, qwen-pinned) with `transcript_path`, and SubagentStop carries `agent_id`/`agent_type` on the pinned `claude` version.
- [ ] Startup pre-authorization companion task reduces the `startup_parser` scrape surface (trusted-dirs / permission-mode pre-auth).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] The five NEW reference-doc practices (3, 4, 5, 6, 8) are each accounted for: 3+4 (edge file + cursor) and 5 (subagent filtering) and 8 (compaction) implemented; 6 (verified-submit) implemented at load-bearing minimum with the overhaul explicitly out of scope.

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (hook-channel)**
  - Name: `hook-channel-builder`
  - Role: per-session settings injection, hook-forwarder, HookEdgeConsumer + durable cursor
  - Agent Type: builder
  - Domain: async (edge tailing races the PTY loop)
  - Resume: true

- **Builder (turn-authority-swap)**
  - Name: `turn-authority-builder`
  - Role: swap Container completion authority to the hook edge; demote read_until_idle; bounded Stop-wait race + crash-resume; needs-human routing
  - Agent Type: builder
  - Domain: async
  - Resume: true

- **Builder (startup-preauth)**
  - Name: `startup-preauth-builder`
  - Role: trusted-dirs / permission-mode startup pre-authorization (companion task)
  - Agent Type: builder
  - Resume: true

- **Test-engineer (harness)**
  - Name: `granite-hook-test-engineer`
  - Role: turn_detection_wedge green-swap, Substrate B Task 0, subagent-filter/cursor/compaction unit tests, crash-path + needs-human integration tests
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `granite-hook-validator`
  - Role: verify all success criteria, run harness + Substrate B
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `granite-hook-documentarian`
  - Role: feature doc + reference-doc + README updates
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 0. Substrate B Stop-hook fidelity gate (HARD GATE)
- **Task ID**: gate-substrate-b
- **Depends On**: none
- **Validates**: `tests/integration/test_granite_ollama_e2e.py` (new Stop-hook case)
- **Assigned To**: `granite-hook-test-engineer`
- **Agent Type**: test-engineer
- **Parallel**: false
- Spawn a real `claude` (Substrate B, `GRANITE_OLLAMA_SMOKE=1`, qwen-pinned) with a `--settings` file registering a Stop hook writing to a temp file.
- Drive one Task-bearing turn that fans out a subagent; assert a parent `Stop` envelope (with `transcript_path`) AND a distinct `SubagentStop` (with `agent_id`/`agent_type`) both land.
- Record `claude --version` and the observed payload shape in the plan's Spike Results.
- **If this gate fails, STOP and re-scope** — do not build the consumer on an unfired hook.

### 1. Hook edge channel (settings injection + forwarder + consumer + cursor)
- **Task ID**: build-hook-channel
- **Depends On**: gate-substrate-b
- **Validates**: new unit tests for forwarder (fail-silent), HookEdgeConsumer (subagent filter, compaction, corrupt line), durable cursor (idempotency/truncation)
- **Informed By**: spike-1 (append-only NDJSON file + cursor), gate-substrate-b (payload shape)
- **Assigned To**: `hook-channel-builder`
- **Agent Type**: builder
- **Parallel**: false
- Extend `PairSpawnSpec`/`PTYDriver.spawn` to append `--settings <path>`; carry edge-file path in `_extra_env`.
- Generate the per-session settings file (per PTY session_id) registering all target hooks → forwarder.
- Implement the fail-silent hook-forwarder (stdin payload → atomic NDJSON append).
- Implement `HookEdgeConsumer` keyed by session_id with durable `(event_cursor, byte_offset, cursor_fingerprint)` cursor; classify `Stop`/`SubagentStop`/needs-human/compaction edges.

### 2. Turn-boundary authority swap + crash-path + needs-human routing
- **Task ID**: build-turn-authority
- **Depends On**: build-hook-channel
- **Validates**: harness `turn_detection_wedge` (green), crash-path integration test, needs-human integration test
- **Informed By**: build-hook-channel (consumer edges)
- **Assigned To**: `turn-authority-builder`
- **Agent Type**: builder
- **Parallel**: false
- Swap `Container` completion authority from `_cycle_idle` `saw_idle` to the consumer's `turn_end` edge; read final message from payload `transcript_path`.
- Demote `read_until_idle` to badge/liveness/crash detection only (keep the code).
- Implement the bounded Stop-wait race vs crash/timeout watchdog; on crash → resume `--resume <uuid>` → verified `continue` (Practice 6 minimum) → re-arm; retry cap → escalate.
- Route `needs_human` edges to `[/user]` deterministically.
- Add the feature flag (hook-driven default on; idle fallback documented).

### 3. Startup pre-authorization (companion)
- **Task ID**: build-startup-preauth
- **Depends On**: gate-substrate-b
- **Validates**: startup wedge harness (`startup_login_wedge`) unaffected; new pre-auth test
- **Assigned To**: `startup-preauth-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add trusted-dirs / permission-mode pre-authorization so the trust-folder dialog + permission bar are pre-answered at spawn, shrinking the `startup_parser` scrape surface.

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-turn-authority, build-startup-preauth
- **Assigned To**: `granite-hook-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run the full granite_faults harness + Substrate B; verify every Success Criterion.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: `granite-hook-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/granite-hook-driven-turn-returns.md`; update README index, reference doc practice-consumption, and the `transcript_tailer.py:444` residual note.

### 6. Final validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: `granite-hook-validator`
- **Agent Type**: validator
- **Parallel**: false
- Confirm all criteria (including docs) met; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| turn_detection_wedge green | `pytest tests/ -k turn_detection_wedge -q` | exit code 0 |
| Stop-edge consumer exists | `grep -rl "HookEdgeConsumer" agent/granite_container/` | output contains agent/granite_container |
| Settings injection wired | `grep -rn "\-\-settings" agent/granite_container/pty_driver.py` | output contains --settings |
| Subagent event distinguished | `grep -rn "SubagentStop" agent/granite_container/` | output contains SubagentStop |
| No raw-redis edge queue | `grep -rn "r.lpush\|r.rpush\|r.lpop" agent/granite_container/` | match count == 0 |
| Token protocol NOT retired | `grep -rn "classify_pm_prefix" agent/granite_container/granite_classifier.py` | output contains classify_pm_prefix |
| Feature doc exists | `test -f docs/features/granite-hook-driven-turn-returns.md && echo ok` | output contains ok |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

All four questions below are RESOLVED to match the plan body (critique concern #5: the body asserts these as settled; the annotations below remove the contradiction so builder and reviewer agree on what is approved).

1. **Edge transport** — *(resolved: append-only NDJSON file + durable cursor, per spike-1 and the plan body).* Honors the Popoto-only rule, restart-safe, coexists with #1721's loop cursor as separate per-session file state. Not a Redis list.
2. **Feature-flag default** — *(resolved: default ON, idle fallback retained — matches the body's feature-flag safety valve).* Removing the idle fallback (sole-authority) stays the separate `[ORDERED]` No-Go gated on a soak period. Shipped as `GraniteSettings.hook_driven_turn_end = True`.
3. **Startup pre-auth depth** — *(resolved: permission-mode pre-auth via the generated settings only; `/login` re-auth explicitly excluded).* The `/login` window is owned by the #1750 BYOB driver and is not re-handled here; Task 3 shrinks the permission-bar scrape surface, and the trust-folder/update-notice dismissals remain with `startup_parser` (documented limit in `generate_hook_settings`).
4. **Practice 6 scope line** — *(resolved: load-bearing minimum only, matching the body + Rabbit Holes).* Verified-submit covers the crash-resume `continue` nudge + the initial prime/message submit; the general >16KB bracketed-paste overhaul is out of scope.
