---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-05
tracking: https://github.com/tomcounsell/ai/issues/1572
last_comment_id:
---

# Granite PTY Container: Production Cutover + Bounded Slot Pool

## Problem

Today, every bridge-originated `AgentSession` runs through `claude -p stream-json` via `agent/sdk_client.py::get_response_via_harness` — a per-turn headless subprocess that exits after each response. This works, but it cannot drive Claude Code's interactive TUI (slash commands, persona priming, trust-folder dismissal) and it requires the `ANTHROPIC_API_KEY` path rather than the Max subscription OAuth path.

PR #1570 (issue #1546) delivered a kernel-validated alternative: `agent/granite_container/container.py` drives two persistent interactive `claude` TUI sessions via PTY, with a local `granite4.1:3b` model routing between them. The PoC confirmed the architecture works end-to-end on a real `claude` TUI. This plan cuts it over to production and adds a bounded slot pool to cap process count.

**Current behavior:**

- `agent/session_executor.py:1708` routes all sessions through `get_response_via_harness` — a `claude -p` subprocess.
- `valor-granite-loop` runs standalone; produces a results JSON only; no Telegram delivery, no `AgentSession` record, no heartbeat.
- A first live test run (2026-06-05) hit `pm_hang` after 2 turns because `classify_pm_prefix` was matching raw ANSI escape sequences as the `[/dev]` prefix token (resolved in code by `granite_classifier.py:185`, but uncovered by tests).
- N concurrent granite sessions = 2N persistent `claude --permission-mode bypassPermissions` processes of ~200 MB each, with no concurrency cap. The first live run left 6 orphaned ~1.2 GB of PTY children after container exit.

**Desired outcome:**

- All bridge-originated sessions execute via `Container`, not `get_response_via_harness`.
- A `PTYPool` enforces a hard maximum of N concurrent PM+Dev PTY pairs (configurable, default 3). No slot available → session waits in the Redis queue.
- Granite sessions appear in `dashboard.json`, `valor-session list`, and the watchdog like any other session.
- Output classified as `[/user]` or `[/complete]` reaches Telegram/email via the existing `TelegramRelayOutputHandler` path, with progress signals written to `agent_session.session_events` (not chat).
- ANSI escape sequences are stripped reliably before classification, and the stripping is unit-tested.
- The PoC code (`agent/granite_agent_loop.py`, `agent/granite_router.py`, `agent/claude_session.py`, `scripts/granite_poc.py`, `scripts/granite_questions_game.py`) is deleted.
- `sdk_client.py::get_response_via_harness` and the stream-json parser remain for now (explicitly out of scope per the issue's Recon Summary); a follow-on issue will handle their removal.

## Freshness Check

**Baseline commit:** `89899116002c847abc6dab6fedcc6824c9a219f9` (current `main` at plan time)
**Issue filed at:** 2026-06-05T06:36:57Z
**Disposition:** Minor drift — the issue body describes a bug ("ANSI escape sequences break classify_pm_prefix") whose code fix was already landed in PR #1570 at `granite_classifier.py:185`. The **functional premise** still holds (the fix needs test coverage and the production cutover has not happened), but Phase 1 of the issue's solution sketch has been pre-resolved by PR #1570.

**File:line references re-verified:**

| Reference | Verified | Notes |
|---|---|---|
| `agent/granite_container/granite_classifier.py:185` | ✅ present | `re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", pm_tail)` — landed in PR #1570. Phase 1 in the issue body is now "harden this code path with tests and broaden the strip" |
| `agent/session_executor.py:1708` (`get_response_via_harness` call) | ✅ unchanged | Production harness call site, still routes through the headless subprocess |
| `agent/session_executor.py:1751` (`BackgroundTask(...)`) | ✅ unchanged | BackgroundTask is reusable as-is for the container, with `send_result=False` |
| `agent/granite_container/container.py:338` (`Container.run`) | ✅ unchanged | Returns `ContainerResult` with `turns[]`, `exit_reason`, `exit_message` — clean integration surface |
| `agent/granite_container/pty_driver.py:253` (`["--model", model, "--permission-mode", "bypassPermissions"]`) | ✅ unchanged | The PTY child command that leaks orphans on container crash |
| `agent/granite_container/pty_driver.py:84-85` (`_ANSI_CSI_RE` / `_ANSI_OSC_RE`) | ✅ unchanged | PTY-driver strip is upstream; the classifier's strip is defense-in-depth |
| `agent/granite_container/pty_driver.py:129-142` (`_strip_ansi`) | ✅ unchanged | Strips CSI + OSC + keypad mode. Classifier should reuse this helper (or its union), per spike-1 |
| `agent/messenger.py:187-256` (`BackgroundTask.run`, `send_result` flag) | ✅ unchanged | `send_result=False` is the right call — Container publishes its own per-turn `[/user]` payloads |
| `agent/output_handler.py:34-52` (`OutputHandler.send` protocol) | ✅ unchanged | Stateless, supports N calls per session. No protocol changes needed |
| `agent/session_executor.py:1116` (`send_cb(session.chat_id, msg, session.telegram_message_id, agent_session)`) | ✅ unchanged | The 4-arg call shape the Container will reuse |
| `agent/granite_agent_loop.py`, `agent/granite_router.py`, `agent/claude_session.py` | ✅ present (5 files, 19KB) | All present, only used by `scripts/granite_poc.py` + `scripts/granite_questions_game.py` + their unit tests. Safe to delete |
| `agent/sdk_client.py:2261` (`get_response_via_harness`) | ✅ unchanged | 3759 lines; many non-harness callers. **Out of scope** for this plan per issue Recon Summary |
| `docs/plans/granite-tui-pty-spike.md` | ✅ present (Complete) | The kernel-validating spike that the production cutover follows |
| `docs/plans/granite_root_session_runner.md` | ✅ present (Cancelled) | Predecessor plan, cancelled for missing the PTY kernel. No overlap |
| `docs/plans/granite_root_session_runner.md` (Cancelled) | ✅ no overlap | Spike plan was a different cutover attempt; superseded by this plan |

**Cited sibling issues/PRs re-checked:**

| Ref | Status | Relevance |
|---|---|---|
| #1546 (PoC) | CLOSED 2026-06-05 via PR #1570 | The kernel-validating PoC. The `granite_container/` module is its deliverable. **Production cutover is this plan.** |
| #1542 (granite_root_session_runner) | CANCELLED | The cancelled predecessor. Cancelled because it drove `claude -p`, not a real TUI. This plan supersedes it by addressing that root cause |
| PR #1570 | MERGED 2026-06-05 | Landed the granite PoC. Includes the ANSI strip at `granite_classifier.py:185` (Phase 1 of the issue's solution) |

**Commits on main since issue was filed (touching referenced files):**

- `00282b5e` PoC #1546: granite operator drives interactive Claude Code session via PTY (#1570) — this is the merge that landed the `granite_container/` module and the ANSI strip. Already accounted for above.

**Active plans in `docs/plans/` overlapping this area:**

- `docs/plans/granite-tui-pty-spike.md` — **status: Complete**. The kernel-validating spike. No overlap.
- `docs/plans/granite_root_session_runner.md` — **status: Cancelled**. Cancelled predecessor cutover. No overlap.
- `docs/plans/sdlc_tool_sessionless_state_noop.md` — overlapping in time but a different feature (SDLC tool sessionless-state behavior). No overlap.

**Notes:**

- The plan's Phase 1 ("strip ANSI escape sequences in `classify_pm_prefix`") is **already done in code**. The remaining work is: (a) add unit tests for the existing strip, (b) broaden the strip to OSC + keypad mode per spike-1's recommendation, and (c) reuse the upstream `_strip_ansi` helper to keep the two layers in sync.
- File:line references in the issue body still match — the issue was filed on the same day as PR #1570, and the merge landed first. The plan documents the resolved state and the residual hardening work.

## Prior Art

- **PR #1570** (issue #1546): PoC kernel — landed `agent/granite_container/` (1,675 lines across 4 files: `container.py`, `pty_driver.py`, `granite_classifier.py`, `startup_parser.py`) plus `tests/unit/granite_container/` (4 test modules). Confirmed the interactive TUI can be driven by pexpect, persona priming works, trust-folder dismissal works, and granite routing works. **This plan builds on the PoC.**
- **`docs/plans/granite-tui-pty-spike.md`** (Complete): The kernel-validating spike that produced the `granite_container/` module. Spike scenarios C1-C5 (submit key, interjection, resume UUID, `/help` overlay, idle signal) define the substrate contract.
- **`docs/plans/granite_root_session_runner.md`** (Cancelled): Earlier cutover attempt. Cancelled for driving `claude -p` (headless) instead of a real TUI. The cancellation root cause is fixed by the PoC.
- **`docs/features/granite-agent-loop.md`**: Prior PoC docs (from #1486, closed). Describes TUI affordances the PoC validated. Superseded by the PoC's actual results.
- **No prior issues or PRs attempted the production wiring** of a PTY container into `_execute_agent_session`. This is greenfield wiring work.

**Why previous fixes failed (or didn't address this):** The previous attempts all assumed a headless `claude -p` substrate. The cancelled `granite_root_session_runner` plan attempted the cutover on top of that wrong assumption. The PoC and the spike that fed it corrected the substrate assumption; this plan is the first attempt at the cutover on the corrected substrate.

## Research

**Skip justification:** The substrate is the specific `claude` CLI binary on the operator's machine plus a local Ollama model — no public library, framework, or ecosystem pattern is more relevant than the codebase context. WebSearch would surface generic pexpect/asyncio material that doesn't constrain the design. Proceeding with codebase context, three parallel spike investigations, and the spike plan's findings.

## Spike Results

Three parallel spikes were dispatched (P-Thread pattern). All returned with high confidence.

### spike-1: ANSI regex coverage

- **Assumption:** The single-line ANSI strip on `granite_classifier.py:185` (`r"\x1b\[[0-9;?]*[a-zA-Z]"`) is sufficient for the Claude Code TUI's actual escape output.
- **Method:** code-read (compared against `_strip_ansi` in `pty_driver.py:129-142` and the test surface in `tests/unit/granite_container/test_granite_classifier.py`).
- **Finding:**
  - The strip catches **CSI** (SGR, cursor-move, screen-erase) but not **OSC** (`ESC]...BEL`) or **single-char ESC controls** (`ESC=`, `ESC>` for keypad mode).
  - The upstream `_strip_ansi` in `pty_driver.py` strips all three categories. The classifier's strip is defense-in-depth — primary defense is upstream.
  - The test suite has **zero coverage** of `\x1b` input to `classify_pm_prefix`. A future refactor that deletes the strip would pass the tests.
- **Confidence:** high
- **Impact on plan:** Replace `granite_classifier.py:185`'s inline regex with a call to the upstream `_strip_ansi` helper (or replicate the union of CSI+OSC+keypad) and add 3 unit tests covering each escape family. This is Phase 1 of the issue's solution, with the bar raised: not just "fix the regression" but "harden the regression path against future Ink/React TUI upgrades."

### spike-2: async wrapping of sync Container

- **Assumption:** `Container.run()` (synchronous, pexpect-driven) can be wired into the worker's existing asyncio event loop without stalling other concurrent sessions.
- **Method:** code-read of `Container.run()` (lines 338-615), `BackgroundTask` (messenger.py:187-295), and the worker's concurrent-session semaphore (`worker/__main__.py:181`, `MAX_CONCURRENT_SESSIONS=8`).
- **Finding:**
  - `asyncio.to_thread(container.run)` is the right primitive. The codebase already uses it at `session_executor.py:271, 403` for sync I/O offload.
  - `BackgroundTask.run(coro, send_result=False)` works as-is — the watchdog and cancel semantics survive (cancel propagates to the awaiting coroutine, the thread's `Container._close_pair`/`_run_pkill_fallback` runs in its `finally:`).
  - Default `ThreadPoolExecutor` (`min(32, os.cpu_count()+4)`) comfortably handles `MAX_CONCURRENT_SESSIONS=8` simultaneous long-running containers.
  - Cancellation has up to `CYCLE_IDLE_TIMEOUT_S=120s` of latency — same order as the existing watchdog tick.
  - One UX concern: `result_to_json(container_result)` produces multi-KB indented JSON. Needs a short-formatter for the final `BackgroundTask` result (or `send_result=False` and a dedicated bridge adapter publishes only the `exit_message`).
- **Confidence:** high
- **Impact on plan:** The adapter shape is `await asyncio.to_thread(container.run)` wrapped in a coroutine that returns either a short summary or empty (so `BackgroundTask.send_result=False` doesn't double-deliver). All `[/user]` payloads are dispatched in-thread by the container, not by the harness layer.

### spike-3: OutputHandler multi-turn shape

- **Assumption:** `OutputHandler.send` and the downstream `TelegramRelayOutputHandler` support being called multiple times per session, so each `[/user]` turn can deliver to Telegram mid-run.
- **Method:** code-read of `OutputHandler` protocol (`output_handler.py:26-67`), `TelegramRelayOutputHandler` (`output_handler.py:131-703`), `FileOutputHandler` (`output_handler.py:70-109`), and `send_to_chat` chat_state (`session_executor.py:1018-1264`).
- **Finding:**
  - `OutputHandler.send` is **stateless** — each call is an independent `rpush` to `telegram:outbox:{session_id}`. Multiple sends per session are supported.
  - The drafter, redundancy filter, and RTR pipeline all run per-call, so each `[/user]` is treated as an independent message. This is the correct semantic.
  - `chat_state.completion_sent` only flips on the first `do_work()` return — if the Container publishes `[/user]` mid-loop, the flag is still `False` and won't suppress the per-turn delivery.
  - **No production code path currently calls `OutputHandler.send` mid-execution.** The granite Container is a greenfield caller.
  - `chat_id`/`reply_to_msg_id`/`agent_session` are sourced from `session.chat_id`/`session.telegram_message_id` and the live `AgentSession` ORM record.
- **Confidence:** high
- **Impact on plan:** The bridge adapter for the Container should:
  1. Resolve the registered `send_cb` once at construction (via `agent_session_queue._resolve_callbacks(project_key, transport)`).
  2. For each `[/user]` turn, call `await send_cb(chat_id, text, reply_to_msg_id, agent_session)`.
  3. For `[/complete]`, do the same with the trailing summary.
  4. For progress signals (granite extract latency, classification misses), write to `agent_session.session_events` rather than spamming the chat.
  5. Pass `send_result=False` to `BackgroundTask.run(...)` so the harness layer doesn't double-deliver.

## Data Flow

```
Telegram inbound (or email inbound) → bridge enqueue → AgentSession in Redis
                                                                  │
                                                                  ▼
                              worker picks session (semaphore-bounded, 8 concurrent)
                                                                  │
                                                                  ▼
            agent/session_executor.py::_execute_agent_session(session)
              ├─ 1. write AgentSession row, set status=running
              ├─ 2. resolve registered send_cb (TelegramRelayOutputHandler.send)
              ├─ 3. read session.chat_id, session.telegram_message_id
              ├─ 4. build do_work() coroutine
              │
              ▼
              do_work() = async def:
                  async with pty_pool.acquire_pair() as (pm, dev):
                      container = Container(user_message=..., cwd=working_dir, max_turns=...)
                      result = await asyncio.to_thread(container.run)
                      #     ├── container calls send_cb(chat_id, text, reply_to, session)
                      #     │     for each [/user] turn (mid-loop delivery)
                      #     ├── container calls send_cb(chat_id, summary, reply_to, session)
                      #     │     on [/complete]
                      #     ├── container writes session_events entries
                      #     │     for granite latency / classification misses
                      #     └── container returns ContainerResult
                      return result.exit_message or ""  # short summary, not multi-KB JSON
              │
              ├─ 5. BackgroundTask.run(do_work(), send_result=False)
              │     ├── watchdog: 60s liveness tick → messenger.notify_heartbeat_tick
              │     ├── cancel: propagates to coroutine → to_thread future → container teardown
              │     └── on completion: _run_work skips messenger.send (send_result=False)
              │
              ├─ 6. _heartbeat_loop (existing): tier-1 60s heartbeat + tier-2 25min calendar
              ├─ 7. _handle_dev_session_completion (existing) — unchanged, no PM/Dev handoff in granite mode
              ├─ 8. memory extraction (existing _schedule_post_session_extraction)
              └─ 9. complete_transcript, AgentSession.status = completed/failed
```

**Key design choice: where the boundary is.** The Container is the boundary between "driver" (sync, pexpect, owns 2 PTYs) and "session" (async, owns AgentSession, owns output delivery). The adapter at the boundary is ~30 lines — a coroutine that calls `asyncio.to_thread(container.run)`, plus a thin progress callback hook so the Container can publish `[/user]` payloads without knowing about the messenger.

**Pre-existing data flow (harness path, being replaced):** `claude -p stream-json` subprocess → JSONL stdout → `get_response_via_harness` parses → returns final string → `BackgroundTask` delivers via `messenger.send`. One-shot, no per-turn delivery.

## Architectural Impact

- **New module:** `agent/granite_container/pty_pool.py` — `PTYPool` class with `acquire_pair()`/`release_pair()` blocking context manager. Singleton owned by the worker process, not re-created per session.
- **New module:** `agent/granite_container/bridge_adapter.py` — `BridgeAdapter` class wrapping `Container` with: send_cb resolution, mid-loop `[/user]` delivery, progress-event publication, short-result formatting. Sized at ~150 lines including docstrings.
- **New module:** `agent/granite_container/stream_short.py` (or inline in adapter) — `format_short_result(container_result) -> str` that produces a 1-2 line Telegram-friendly summary (not multi-KB JSON). Returns `""` for `pm_complete`/`pm_user` (the adapter already delivered the full payload mid-loop).
- **Modified:** `agent/session_executor.py:1700-1756` — replace the `do_work` body with the new container-driven coroutine; add `send_result=False`. Approximately 30 lines changed, 0 lines removed from the harness path.
- **New config field:** `config/settings.py::GRANITE_PTY_POOL_SIZE` (default 3, env-overridable as `GRANITE_PTY_POOL_SIZE`).
- **New config field:** `config/settings.py::GRANITE_MAX_TURNS` (default 10, env-overridable; currently a module constant in `container.py:68`).
- **No new dependencies.** pexpect is already in use by the PoC (`agent/granite_container/pty_driver.py:50-51`).
- **No new env vars required for the operator** beyond the two settings above.
- **No protocol changes.** `OutputHandler.send` is unchanged; `BackgroundTask.run` is unchanged; `AgentSession` model is unchanged.
- **Reversibility:** Each change is independently revertible. The harness call at `session_executor.py:1708` is preserved as a fallback for the first release (gated by a feature flag `GRANITE_PTY_ENABLED`, default `True`); on a regression, setting the flag to `False` restores the headless path within a single restart. The feature flag and the harness fallback are removed in the follow-on issue that deletes `get_response_via_harness`.
- **Data ownership:** The Container owns the two PTYs and the sandbox tempdir. The BridgeAdapter owns the `send_cb` reference and the `agent_session` ORM record. The PTYPool owns the slot lifecycle.

## Appetite

**Size:** Medium.

**Team:** Solo dev + PM (the dev-role session for /do-build). The PM role is already engaged via issue #1572's tracking. The reviewer role runs `/do-pr-review` after build.

**Interactions:**
- PM check-ins: 1 (scope alignment on the feature flag fallback and the short-result formatter)
- Review rounds: 1 (PR review covers design + integration; the second-round /do-patch handles any review blockers)

Solo dev work is fast — the bottleneck is the two integration points (PTYPool, BridgeAdapter) and the harness-fallback gate. Appetite measures the integration complexity, not the line count.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `granite4.1:3b` reachable via local Ollama | `curl -sf http://localhost:11434/api/tags \| jq -e '.models[] \| select(.name == "granite4.1:3b")'` | Local granite model for the routing classifier |
| `pexpect >= 4.9.0` | `python -c "import pexpect; assert pexpect.__version__ >= '4.9.0'"` | PTY driver dependency (already a PoC dep) |
| `ANTHROPIC_API_KEY=""` in worker env | `python -c "import os; assert os.environ.get('ANTHROPIC_API_KEY','')==''"` | Force OAuth/Max subscription path; no API-key fallback |
| Interactive `claude` binary on PATH | `which claude && claude --version` | The TUI substrate |
| Ollama running and bound to localhost:11434 | `curl -sf http://localhost:11434/api/tags > /dev/null` | Granite classifier backend |
| `cwd` worktree path writable | `test -w "$(pwd)"` | Container's `cwd=` parameter; the worker passes `session.working_dir` |
| `redis-cli` reachable for outbox smoke-test | `redis-cli ping` | Verify `telegram:outbox:{session_id}` writes during integration test |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_pty_production_cutover.md`

## Solution

### Key Elements

- **`PTYPool`**: A singleton, asyncio-aware, bounded pool of pre-warmed PM+Dev PTY slot pairs. `acquire_pair()` returns `(pm_pty, dev_pty)` as an async context manager; `release_pair()` schedules a background respawn of the released slots so the next acquirer gets fresh PTYs. Pool size is the hard max-concurrency cap.
- **`BridgeAdapter`**: A thin wrapper around `Container` that: (a) resolves the registered `send_cb` once at construction, (b) installs mid-loop progress callbacks on the container's per-turn path so `[/user]` payloads are delivered to Telegram as they happen, (c) writes per-turn observability data to `agent_session.session_events`, (d) returns a short final string for `BackgroundTask` (or empty when mid-loop delivery handled it).
- **Harness fallback gate**: A `GRANITE_PTY_ENABLED` feature flag (env var, default `True`) gates the new path. When `False`, the existing `get_response_via_harness` call at `session_executor.py:1708` is the active path. The flag is removed when the follow-on issue deletes the harness path entirely.
- **Short-result formatter**: A 1-2 line Telegram-friendly summary produced from `ContainerResult` (`f"Granite session ended: {result.exit_reason} (turns={len(result.turns)}, compliance_misses={result.classification_compliance_misses})"`). Empty string when `exit_reason` is `pm_complete` or `pm_user` (the `[/complete]`/`[/user]` payload was already delivered mid-loop).
- **ANSI hardening**: Replace the inline regex on `granite_classifier.py:185` with a call to `pty_driver._strip_ansi` (or replicate its union of CSI+OSC+keypad). Add 3 unit tests: leading OSC, leading keypad-mode escape, plain CSI parity.

### Flow

**Starting point** → Telegram inbound message → `AgentSession` enqueued in Redis →

**Worker picks session** (semaphore-bounded, 8 concurrent) → `_execute_agent_session` runs →

**BridgeAdapter** → acquires PTY pair from `PTYPool` (blocks if all 3 slots locked; session waits) →

**Container** runs the PM→granite→Dev→granite→PM loop → on each `[/user]` turn, calls `send_cb(chat_id, text, reply_to, session)` → on `[/complete]`, same →

**Container exits** → PTY pair returned to pool (background respawn) →

**End state** → `AgentSession.status = completed/failed` → dashboard reflects session → Telegram has received each `[/user]` turn and the `[/complete]` summary.

### Technical Approach

- **PTYPool design:** Singleton initialized in `worker/__main__.py` (or `agent/session_executor.py` module-level lazy init) at worker startup. Uses an `asyncio.Semaphore(GRANITE_PTY_POOL_SIZE)` to gate `acquire_pair()`. Each slot is `(PTYDriver, PTYDriver)` for the PM and Dev pair. On `release_pair()`, the old PTYs are closed (with the existing `pty_driver.close(force=True)` teardown), and a `asyncio.create_task(self._respawn_slot(idx))` schedules a fresh spawn in the background — the next acquirer of slot N gets the respawned pair or waits for it.

- **Background respawn correctness:** Slot lifecycle states: `idle` (available), `locked` (held by a session), `respawning` (background-restarting after release). `acquire_pair()` waits on the semaphore; if the assigned slot is `respawning`, it awaits a per-slot `asyncio.Event` that the respawn task sets. This is the bounded-concurrency invariant: at most `GRANITE_PTY_POOL_SIZE` PTY pairs are alive at any moment, and the pool's "in flight" count is exactly the semaphore count.

- **BridgeAdapter call-injection:** The Container currently calls `classify_pm_prefix(pm_buf)` to get a routing decision (`dev`/`user`/`complete`/`unknown`). The adapter wraps this with a callback: after classification but before `extract_dev_prompt`, if `destination == "user"`, fire `send_cb(chat_id, payload, reply_to, agent_session)`. This is one extra conditional in the container's per-turn block, not a structural change. Similarly for `destination == "complete"`, fire the same callback with the trailing summary.

- **Where to do this in the Container:** Add an optional `on_user_payload: Callable[[str], None]` and `on_complete_payload: Callable[[str], None]` parameter to `Container.__init__`. The bridge adapter passes async callables that schedule `send_cb` via `asyncio.run_coroutine_threadsafe(..., loop)` (since the Container runs on a thread). This keeps the Container substrate-agnostic — the PoC passes `None` (default) and behaves exactly as today.

- **Session-events progress signals:** Use `agent_session.session_events` (Popoto field, list of dicts) for non-user-visible progress: `{turn: int, classification: str, compliance_miss: bool, pm_idle_ms: int, dev_idle_ms: int, granite_extract_ms: int, granite_summarize_ms: int}`. The dashboard and reflection sweeps can pick these up; Telegram is not spammed.

- **Harness fallback gating:** Add `GRANITE_PTY_ENABLED = os.environ.get("GRANITE_PTY_ENABLED", "true").lower() in ("1", "true", "yes")` to `agent/session_executor.py` module-level. The replacement at `session_executor.py:1700` branches on this flag: `True` → new container path, `False` → existing harness call. The flag is a temporary safety net; removing it is a follow-on issue.

- **Short-result formatter:** New function `format_short_result(result: ContainerResult) -> str` lives in `agent/granite_container/bridge_adapter.py`. Returns the 1-2 line summary as described above. The bridge adapter's coroutine returns this from `to_thread` so `BackgroundTask` has a string to log.

- **ANSI hardening:** Change `granite_classifier.py:185` from inline regex to `from agent.granite_container.pty_driver import _strip_ansi` then `pm_tail = _strip_ansi(pm_tail)`. Add 3 tests to `tests/unit/granite_container/test_granite_classifier.py` covering each escape family.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `Container.run`'s `try/except Exception: e` (line 602) sets `result.exit_reason = "exception"`, `result.exit_message = _truncate_exit_message(...)` — covered by existing `tests/unit/granite_container/test_container.py`. **Add test:** exception path produces a `ContainerResult` with `exit_reason == "exception"` and `exit_message` truncated to `EXIT_MESSAGE_MAX_CHARS = 500`.
- [ ] `Container._close_pair`'s `try/except Exception: pass` per PTY (lines 232-234) — silently swallows close errors. **Add test:** mock a PTY whose `close(force=True)` raises; assert `_close_pair` returns cleanly and the other PTY is still closed.
- [ ] `Container._run_pkill_fallback`'s `try/except Exception: pass` (line 256) — best-effort, never raises. **Add test:** mock `subprocess.run` to raise; assert container exits cleanly.
- [ ] `BridgeAdapter.on_user_payload` callback is wrapped in `try/except` (new code) — **Add test:** mock `send_cb` to raise; assert container still progresses to the next turn.
- [ ] `PTYPool._respawn_slot` is wrapped in `try/except` (new code) — **Add test:** mock `PTYDriver.spawn` to raise; assert the slot is marked failed, the semaphore is released, and subsequent acquires do not block forever.

### Empty/Invalid Input Handling

- [ ] `Container.__init__` raises `ValueError` for empty `user_message` (line 202-203) — already covered.
- [ ] `classify_pm_prefix("")` returns `unknown` with empty payload (line 79-82 in tests) — already covered.
- [ ] `classify_pm_prefix("   \n   \n")` (whitespace-only) returns `unknown` — already covered.
- [ ] `BridgeAdapter` with `user_message=""` propagates the `ValueError` (the existing constructor check covers it).
- [ ] `PTYPool` with `pool_size <= 0` raises `ValueError` at construction (new code) — **Add test.**
- [ ] Empty `ContainerResult.exit_message` (when `exit_reason == "pm_max_turns"` and the last turn's `payload` is empty) — `format_short_result` returns a string that mentions the turn count, not a traceback. **Add test.**

### Error State Rendering

- [ ] `exit_reason == "exception"` is rendered as a single line: `"Granite session crashed: <exit_message>"`. The `exit_message` is already truncated to 500 chars. **Add test.**
- [ ] `exit_reason == "pm_hang"` / `"dev_hang"` / `"startup_unresolved"` are rendered with the specific reason. **Add test.**
- [ ] Mid-loop `send_cb` failure does not crash the container — it logs the error and continues to the next turn. **Add test.**
- [ ] PTY process orphans after `BridgeAdapter` cancellation: the container's `_run_pkill_fallback` runs in `Container.run`'s `finally:` block, so cancellation propagates correctly. **Add test:** mock cancellation mid-loop, assert `pkill -f "claude --permission-mode bypassPermissions"` is called.

## Test Impact

- [ ] `tests/unit/granite_container/test_granite_classifier.py` — UPDATE: add 3 tests for ANSI stripping (CSI parity, OSC leading, keypad mode `ESC=`/`ESC>` leading). The existing test suite has zero `\x1b` coverage for `classify_pm_prefix`; this closes the gap.
- [ ] `tests/unit/granite_container/test_pty_driver.py` — no change (the helper is already tested at lines 198-206).
- [ ] `tests/unit/granite_container/test_container.py` (if it doesn't exist, create it) — UPDATE: add exception path, empty `exit_message`, and per-turn callback tests.
- [ ] `tests/unit/granite_container/test_pty_pool.py` — CREATE: 6-8 tests covering `acquire_pair` blocking, `release_pair` respawn, semaphore cap invariant, cancellation cleanup, failed-spawn recovery, `pool_size <= 0` ValueError.
- [ ] `tests/unit/granite_container/test_bridge_adapter.py` — CREATE: 6-8 tests covering `send_cb` resolution, mid-loop `[/user]` delivery, `[/complete]` delivery, session-events progress writes, short-result formatting, fallback flag disabled.
- [ ] `tests/integration/test_granite_pty_production.py` — CREATE: end-to-end test that a single bridge-originated session reaches `Container.run` (mocked at the pexpect layer to avoid spawning real `claude` processes in CI), produces an `AgentSession` record, calls `send_cb` for each `[/user]` turn, and reaches `completed` status. This is the parity-with-harness integration test.
- [ ] `tests/unit/test_granite_agent_loop.py` — DELETE: PoC's `granite_agent_loop` is being deleted; the test goes with it.
- [ ] `tests/unit/test_granite_router.py` — DELETE: PoC's `granite_router` is being deleted; the test goes with it.
- [ ] `tests/unit/test_claude_session.py` — UPDATE or DELETE: `claude_session.py` is being deleted (it provided `_RESUME_HINT_RE` which is re-imported by `pty_driver.py:56`; verify that the import is moved to `pty_driver.py`'s local definition or to a new shared module before deletion).
- [ ] `tests/unit/test_granite_questions_game.py` — DELETE: PoC's `scripts/granite_questions_game.py` is being deleted; the test goes with it.
- [ ] `tests/unit/test_granite_poc.py` (if exists) — DELETE: PoC's `scripts/granite_poc.py` is being deleted; the test goes with it.
- [ ] `tests/unit/test_session_executor.py` — UPDATE: any tests that mock `get_response_via_harness` need to mock the new `BridgeAdapter.run` path. Most existing tests should be untouched if the harness fallback gate is `True` by default and the test fixtures set `GRANITE_PTY_ENABLED=False`.

## Rabbit Holes

- **Tempting: replace the harness path entirely in the same PR.** Issue's Recon Summary explicitly says `sdk_client.py` is out of scope (3,759 lines, many non-harness callers). Adding the harness removal here triples the diff and conflates the cutover with the cleanup. **Stay focused on the cutover + the PoC deletion; the harness path stays behind a flag for one release.**
- **Tempting: per-machine `claude` model selection / fallback.** The PoC hardcodes `claude --model` via the `pm_model`/`dev_model` parameters on `Container.__init__`. Today, the production harness does the same. Adding a model-selection matrix is a feature, not a cutover. **Defer.**
- **Tempting: optimize the granite classifier to use a smaller model or a regex-only path for `[/user]`.** The PoC is already O(N×granite_call); the classifier is the slow part. The PoC's 2-call per turn (extract + summarize) is the bottleneck, and replacing either call is a model-quality research project. **Defer.**
- **Tempting: write a custom restart loop for the PTY driver on transient pexpect errors.** The `pty_driver.py` close+respawn path in the pool already handles this. Adding more logic inside the driver conflates substrate with pool. **Defer to a separate investigation if pexpect EOFs become a production issue.**
- **Tempting: add per-chat session limits (e.g. only N granite sessions per Telegram chat).** Not in the issue. The global pool size is the only cap we need. **Defer.**
- **Tempting: wire the progress signals to Telegram as ephemeral "..." messages.** The user sees the final `[/user]` payload already; spamming progress violates the agent's system-prompt rule against intermediate status chatter. **Already decided: use `agent_session.session_events`, not chat.**

## Risks

### Risk 1: PTY process orphans if the worker is SIGKILL'd mid-run
**Impact:** Each orphaned `claude --permission-mode bypassPermissions` process consumes ~200 MB. The first live run left 6 orphans (~1.2 GB). A worker SIGKILL during a high-traffic moment could leave 24 orphans (~4.8 GB with the default pool size of 3 pairs × 8 concurrent sessions).
**Mitigation:** The pool's `_respawn_slot` runs on a per-slot `asyncio.Event`. If the worker restarts, the next `acquire_pair` will block on slots whose respawn is in flight — but the orphaned PTYs are still alive from the previous worker process. **Add a startup hook in `worker/__main__.py` that runs `pkill -f "claude --permission-mode bypassPermissions"` before the pool is constructed** to clear any orphans from a previous SIGKILL. This is the same `_run_pkill_fallback` pattern but at worker startup. Document this in `agent/granite_container/pty_pool.py` module docstring.

### Risk 2: 40-minute worst-case Container.run blocks the worker event loop
**Impact:** Without `asyncio.to_thread`, a single container run could stall heartbeats, steering injection, and watchdog ticks for up to `max_turns × CYCLE_IDLE_TIMEOUT_S × 2 = 40 minutes`. This would break the worker's two-tier no-progress detector and the `valor-session status` liveness probes.
**Mitigation:** Use `asyncio.to_thread(container.run)` per spike-2's recommendation. The thread pool default is `min(32, os.cpu_count()+4)`, comfortably handling `MAX_CONCURRENT_SESSIONS=8` × long containers. Cancellation has `CYCLE_IDLE_TIMEOUT_S=120s` latency — same as the watchdog tick. The `BackgroundTask` watchdog still ticks every 60s against the wrapping asyncio.Task, not the thread.

### Risk 3: First live test of the wired path produces `pm_hang` for the same reason as the PoC's first run
**Impact:** A regression in the wiring (e.g. `_strip_ansi` not called on the buffer before classification) would silently re-introduce the issue. The new test coverage from Phase 1 catches the regression in CI, but only if the CI run actually exercises the full path.
**Mitigation:** Three layers of defense: (1) the new unit tests in `test_granite_classifier.py` catch the regression at CI; (2) the integration test in `tests/integration/test_granite_pty_production.py` exercises the full path with a mocked pexpect; (3) the manual acceptance test in the issue's Acceptance Criteria ("a live `valor-granite-loop --user-message "handle PR 1568"` run produces at least one classified turn") is gated on the PR merge to main, not on CI green. **The PR template must include a "live smoke test" checkbox that the reviewer confirms before merge.**

### Risk 4: Mid-loop `send_cb` failure spams the user or silently swallows output
**Impact:** If `send_cb` raises mid-loop and the adapter swallows it, the user doesn't see that turn's `[/user]` payload. If the adapter doesn't swallow it, the container crashes.
**Mitigation:** The adapter wraps `send_cb` in `try/except`, logs the error at WARNING with the session_id and turn index, and continues. The error is also written to `agent_session.session_events` so the dashboard surfaces it. A user-visible "I tried to send you a message but it failed" delivery is NOT emitted (would violate the no-spam rule and could itself fail). Documented in `agent/granite_container/bridge_adapter.py` module docstring.

### Risk 5: 2N persistent `claude` processes overload the machine at MAX_CONCURRENT_SESSIONS=8 × pool size 3 = 24
**Impact:** With the default `MAX_CONCURRENT_SESSIONS=8` and a pool size of 3, the worker can hold 8 × 3 = 24 `claude` PTY pairs simultaneously — ~5 GB of resident memory. This is the orphan issue from Risk 1, but in the happy path: legitimate concurrent sessions.
**Mitigation:** Two knobs. (1) The pool size defaults to 3 but is env-overridable (`GRANITE_PTY_POOL_SIZE`); operators on memory-constrained machines can set it to 1 or 2. (2) The pool size is intentionally **smaller** than `MAX_CONCURRENT_SESSIONS` so the Redis queue absorbs over-cap sessions. **Document the relationship between the two settings in `config/settings.py` docstring.**

### Risk 6: `claude --permission-mode bypassPermissions` flag is gated on a specific TUI version
**Impact:** The PTY driver hardcodes `--permission-mode bypassPermissions` at `pty_driver.py:253`. A TUI upgrade that renames or removes this flag would break every container run silently (the PTY would spawn but the trust-folder prompt would never appear, the startup-phase parser would loop on `startup_unresolved`, and the session would exit after `STARTUP_WINDOW_CYCLES=10`).
**Mitigation:** Already covered by the PoC's startup-phase parser (`agent/granite_container/startup_parser.py`): if the trust-folder prompt doesn't appear, the parser returns `UNKNOWN` and the container exits `startup_unresolved`. The integration test in `tests/integration/test_granite_pty_production.py` should include a smoke assertion that `startup_unresolved` is the exit reason when the flag is missing (mocked at the pexpect level). **No additional mitigation needed; existing parser handles this case.**

## Race Conditions

### Race 1: PTY slot reused before background respawn completes
**Location:** `agent/granite_container/pty_pool.py` (new code).
**Trigger:** A session calls `release_pair()` → the respawn task is scheduled but not yet awaited → a new session calls `acquire_pair()` and gets the same slot index → tries to use stale PTYs.
**Data prerequisite:** The slot's `(pm, dev)` tuple must be the freshly spawned pair, not the released-and-not-yet-respawned pair.
**State prerequisite:** The slot's state must be `idle` and its `pty_pair` attribute must be the new pair, set by the respawn task.
**Mitigation:** The slot's `pty_pair` is only set by the respawn task under a per-slot `asyncio.Lock`. `acquire_pair` reads the slot under the same lock. The semaphore is acquired before the per-slot lock, so the "lock-or-wait" pattern is well-defined. If a session calls `acquire_pair` and the assigned slot is `respawning`, the acquire waits on the per-slot event.

### Race 2: Mid-loop `send_cb` races with worker shutdown
**Location:** `agent/granite_container/bridge_adapter.py` (new code), `agent/granite_container/container.py:410-484`.
**Trigger:** The worker is shutting down. A container turn is in flight. The container classifies a `[/user]` turn and schedules `send_cb` via `asyncio.run_coroutine_threadsafe`. The worker's event loop is closed before the coroutine runs.
**Data prerequisite:** The `send_cb` coroutine must complete (or be cancelled cleanly) before the worker exits.
**State prerequisite:** The Redis connection inside `TelegramRelayOutputHandler._get_redis` must be closed cleanly.
**Mitigation:** `asyncio.run_coroutine_threadsafe` raises `RuntimeError` if the loop is closed; the adapter catches it, logs, and continues. Redis is closed at worker shutdown (`worker/__main__.py` shutdown hook). The drafter inside `send_cb` is already exception-safe (it falls back to raw text on internal failure).

### Race 3: `Container.run` cancellation vs. background pkill fallback
**Location:** `agent/granite_container/container.py:602-613` (existing `try/except/finally`).
**Trigger:** The container is in the middle of `_run_pkill_fallback` when a SIGKILL arrives. The fallback subprocess.run is interrupted.
**Data prerequisite:** None — this is a cleanup race, not a correctness race.
**State prerequisite:** Worker is shutting down; the PTYs may or may not have been killed.
**Mitigation:** The `subprocess.run` call has a `timeout=5`. If the timeout fires, the subprocess is killed by the `with` semantics. The orphan PTY children survive until the next worker startup, where the startup hook from Risk 1 cleans them up. **Documented as the load-bearing reason for the startup pkill hook.**

### Race 4: `AgentSession.last_heartbeat_at` write during container execution
**Location:** `agent/session_executor.py:1774-1791` (existing `_heartbeat_loop`).
**Trigger:** The `BackgroundTask` is running `do_work()` on a thread. The heartbeat loop ticks every 60s. The container is mid-turn. The heartbeat write races with the container's status transition.
**Data prerequisite:** `last_heartbeat_at` is monotonically increasing (or at least non-decreasing).
**State prerequisite:** None — the heartbeat write is idempotent and uses `update_fields=["last_heartbeat_at"]`.
**Mitigation:** The existing code already handles this race: the heartbeat write is in a separate `asyncio.create_task` and uses `session.save(update_fields=...)` which is a single-field update. The container's status transition (set `completed` at the end) is gated on a different field (`status`) and uses a different `update_fields` set. No race. **Already correct.**

## No-Gos (Out of Scope)

- [EXTERNAL] **`sdk_client.py` deletion** — `get_response_via_harness` and `_run_harness_subprocess` stay for one release cycle, behind the `GRANITE_PTY_ENABLED=False` flag. The 3,759-line module has many non-harness callers (persona loading, prompt composition, etc.) that the agent cannot safely rewrite. **Sequenced for a follow-on issue** filed at plan-execution time.
- [ORDERED] **Harness path removal follow-on** — must wait for at least one release cycle of the granite path in production to confirm stability. Filed as a separate issue at plan-execution time.
- [DESTRUCTIVE] **Worker startup pkill hook** is added in the same PR but ONLY runs `pkill -f "claude --permission-mode bypassPermissions"`. It does not kill any other `claude` process (the regex is specific). Documented in `worker/__main__.py` startup code.
- [SEPARATE-SLUG #1572] All 12 acceptance-criteria items in the issue are in scope; this No-Go list is the inverse — explicit deferrals. The single ordered deferral is the `sdk_client.py` deletion.

## Update System

- **No update system changes required for the `/update` skill or `scripts/remote-update.sh`.** The change is purely code-level: new modules in `agent/granite_container/`, modified `agent/session_executor.py:1700-1756`, deleted `agent/granite_agent_loop.py` + `agent/granite_router.py` + `agent/claude_session.py` + `scripts/granite_poc.py` + `scripts/granite_questions_game.py`, two new config fields.
- **Two new env vars are operator-facing** (in `~/Desktop/Valor/.env`): `GRANITE_PTY_POOL_SIZE` (default 3) and `GRANITE_PTY_ENABLED` (default `true`). **No new env vars are deployment-required** — the defaults are correct.
- **No new config files are introduced.** `config/settings.py` gains two new fields; no new YAML/TOML/JSON.
- **The update skill's Step 4.6 (`validate_projects_config`)** does not touch this change. The `projects.json` file is unchanged.
- **The PoC code deletion is part of the same `git push`** — operators do not need to coordinate a separate cleanup PR. The PoC files are referenced by `scripts/granite_poc.py` and `scripts/granite_questions_game.py` only; deleting both scripts + the granite_agent_loop + granite_router + claude_session modules in the same PR is a clean cutover.
- **The harness fallback gate (`GRANITE_PTY_ENABLED=False`)** is a temporary safety net. Operators on machines where the granite path is misbehaving can flip this to `False` to restore the headless path. Documented in the new `config/settings.py` docstring.

## Agent Integration

- **No new MCP servers are required.** The bridge already calls `valor_session create` / `valor_session steer` / etc., and the existing `TelegramRelayOutputHandler` is the bridge integration point. The agent does not need a new MCP tool to use granite sessions.
- **No changes to `.mcp.json`.**
- **No changes to `bridge/telegram_bridge.py` directly.** The bridge is unchanged; it still enqueues `AgentSession` records to Redis. The change is in how the worker (the consumer) executes them.
- **The worker is the integration point.** `worker/__main__.py` gains the PTYPool startup hook (the `pkill` from Risk 1) and the pool's singleton initialization. This is the bridge between "Telegram message arrives" and "interactive `claude` TUI processes spin up."
- **The agent persona is the SAME persona as the headless harness path.** The system prompt loaded by `session_executor.py:1682` (`_load_persona_overlay_with_log`) is the same code path; the difference is the substrate. The agent's behavior in Telegram is identical to today's; only the driver's runtime characteristics change.
- **Integration tests verify the agent can invoke the new path:** `tests/integration/test_granite_pty_production.py` covers the full path from a simulated Telegram inbound through the wire-output to `TelegramRelayOutputHandler`. The PoC's existing `tests/integration/test_granite_tui_pty.py` (if it exists) covers the substrate layer; this plan adds the production-layer integration.

## Documentation

- [ ] Create `docs/features/granite-pty-production.md` describing the production path, the PTYPool, the BridgeAdapter, and the harness fallback flag. Reference the PoC plan and the spike plan.
- [ ] Update `docs/features/README.md` index table to add the new feature doc.
- [ ] Update `docs/features/subconscious-memory.md` to note that granite sessions participate in memory extraction (no behavioral change, but the doc should call it out so operators know the integration is end-to-end).
- [ ] Update `docs/infra/email-cs-auto-reply.md` (existing) and the new `docs/features/granite-pty-production.md` cross-link so operators can see how the granite path interacts with the email-cs path (both produce `AgentSession` records; the granite path also produces `ContainerResult` events).
- [ ] Add a `## Granite PTY Pool` section to `docs/deployment.md` documenting the two new env vars (`GRANITE_PTY_POOL_SIZE`, `GRANITE_PTY_ENABLED`) and the relationship to `MAX_CONCURRENT_SESSIONS`.
- [ ] Update the developer-facing `CLAUDE.md` "System Architecture" diagram (if it currently shows the headless `claude -p` path) to reflect the PTY container path as the primary production path with the headless path as a fallback.

## Success Criteria

- [ ] `classify_pm_prefix` reuses `pty_driver._strip_ansi` (or its union of CSI+OSC+keypad), and 3 new unit tests cover each escape family — all green
- [ ] `agent/granite_container/pty_pool.py` exists with `acquire_pair`/`release_pair` blocking context manager, semaphore-bounded at `GRANITE_PTY_POOL_SIZE` (default 3)
- [ ] `agent/granite_container/bridge_adapter.py` exists with mid-loop `send_cb` delivery, `agent_session.session_events` progress writes, and short-result formatting
- [ ] `_execute_agent_session` calls `BridgeAdapter.run` via `await asyncio.to_thread(...)`; the harness path is gated by `GRANITE_PTY_ENABLED` (default `True`)
- [ ] Bridge-originated sessions route through `Container`; `claude -p stream-json` is NOT spawned when the flag is on
- [ ] A live `valor-granite-loop --user-message "handle PR 1568"` run produces at least one classified turn (not `pm_hang` after 2 unknowns)
- [ ] A simulated bridge session reaches `Container.run`, produces an `AgentSession` record, calls `send_cb` for each `[/user]` turn, and reaches `completed` status in the integration test
- [ ] `curl localhost:8500/dashboard.json` shows a running granite session mid-execution with `last_heartbeat_at` < 120s old
- [ ] `valor-session list` shows an active granite session; `valor-session status --id <id>` shows heartbeat age < 120s during execution
- [ ] Output classified as `[/user]` or `[/complete]` reaches Telegram via `TelegramRelayOutputHandler` (verified by inspecting `telegram:outbox:{session_id}` in Redis)
- [ ] Steering messages queued to `AgentSession.queued_steering_messages` are injected between PM turns (existing test surface; no regression)
- [ ] Calendar heartbeat fires at session start and on the 25-minute timer (existing test surface; no regression)
- [ ] Post-session memory extraction fires on exit (existing test surface; no regression)
- [ ] After a 3-run test session, `ps aux | grep 'claude --permission-mode'` shows ≤ pool-size × MAX_CONCURRENT_SESSIONS processes (no orphan leak); the worker startup `pkill` hook clears any orphans from a prior SIGKILL
- [ ] `agent/granite_agent_loop.py`, `agent/granite_router.py`, `agent/claude_session.py`, `scripts/granite_poc.py`, `scripts/granite_questions_game.py`, and related unit tests are deleted
- [ ] `GRANITE_PTY_ENABLED=False` restores the headless path; the regression-tested harness path still works behind the flag
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] Live smoke test (the reviewer confirms a real `claude` TUI session reaches `Container.run` and produces a `[/user]` delivery to Telegram) is checked off in the PR template

## Team Orchestration

### Team Members

- **Builder (ansi-hardening)**
  - Name: ansi-builder
  - Role: Replace `granite_classifier.py:185` inline regex with `_strip_ansi` call; add 3 unit tests
  - Agent Type: builder
  - Resume: true

- **Builder (pty-pool)**
  - Name: pty-pool-builder
  - Role: Create `agent/granite_container/pty_pool.py` with acquire/release/lifecycle; 6-8 unit tests
  - Agent Type: builder
  - Resume: true

- **Builder (bridge-adapter)**
  - Name: adapter-builder
  - Role: Create `agent/granite_container/bridge_adapter.py`; mid-loop `send_cb` delivery; session_events writes; short-result formatter; 6-8 unit tests
  - Agent Type: builder
  - Resume: true

- **Builder (executor-wiring)**
  - Name: wiring-builder
  - Role: Modify `agent/session_executor.py:1700-1756` to call the new path; add `GRANITE_PTY_ENABLED` flag; `config/settings.py` new fields; `worker/__main__.py` startup pkill hook
  - Agent Type: builder
  - Resume: true

- **Builder (poc-deletion)**
  - Name: poc-deleter
  - Role: Delete `agent/granite_agent_loop.py`, `agent/granite_router.py`, `agent/claude_session.py`, `scripts/granite_poc.py`, `scripts/granite_questions_game.py`, and their unit tests; verify no remaining callers
  - Agent Type: builder
  - Resume: true

- **Builder (integration)**
  - Name: integration-builder
  - Role: Create `tests/integration/test_granite_pty_production.py` end-to-end test; mock pexpect layer to avoid spawning real `claude` in CI
  - Agent Type: builder
  - Resume: true

- **Validator (cross-cutting)**
  - Name: cross-validator
  - Role: Run all tests, verify the harness fallback flag works, verify the live smoke test, check that no PoC files remain
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Create/update the 6 documentation items above
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Phase 1 — ANSI hardening (small, parallel-safe)
- **Task ID**: build-ansi
- **Depends On**: none
- **Validates**: `tests/unit/granite_container/test_granite_classifier.py` (UPDATE: 3 new tests)
- **Informed By**: spike-1 (confirmed: regex covers CSI but not OSC/keypad; tests are absent)
- **Assigned To**: ansi-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `granite_classifier.py:185`'s inline `re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", pm_tail)` with `from agent.granite_container.pty_driver import _strip_ansi; pm_tail = _strip_ansi(pm_tail)`
- Add 3 unit tests to `tests/unit/granite_container/test_granite_classifier.py`: CSI parity (matches pty_driver behavior), leading OSC `ESC]0;titleBEL` does not corrupt `[/dev]`, leading keypad-mode `ESC=` does not corrupt `[/dev]`
- Verify all existing classifier tests still pass

### 2. Phase 2a — PTYPool (bounded slot pool)
- **Task ID**: build-pty-pool
- **Depends On**: none
- **Validates**: `tests/unit/granite_container/test_pty_pool.py` (CREATE: 6-8 tests)
- **Informed By**: spike-2 (confirmed: `asyncio.to_thread` + `asyncio.Semaphore` is the pattern)
- **Assigned To**: pty-pool-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/granite_container/pty_pool.py` with `PTYPool` class: `__init__(pool_size: int)`, `acquire_pair()` async context manager, `release_pair(pm, dev)` async method that schedules background respawn
- Implement slot lifecycle: `idle` → `locked` → `respawning` → `idle`; per-slot `asyncio.Event` for respawn signaling
- `acquire_pair` waits on `asyncio.Semaphore`; if assigned slot is `respawning`, awaits the per-slot event
- `release_pair` closes old PTYs (`pty_driver.close(force=True)`) and schedules `asyncio.create_task(self._respawn_slot(idx))`
- Singleton: lazy module-level init in `agent/granite_container/pty_pool.py`; `worker/__main__.py` startup hook calls the singleton's `initialize()` to pre-warm slots
- Add 6-8 unit tests: pool_size=0 raises ValueError; acquire blocks when all slots locked; release respawns in background; failed spawn releases semaphore; cancellation cleans up; per-slot lock prevents stale read

### 3. Phase 2b — BridgeAdapter (mid-loop output delivery)
- **Task ID**: build-bridge-adapter
- **Depends On**: build-pty-pool
- **Validates**: `tests/unit/granite_container/test_bridge_adapter.py` (CREATE: 6-8 tests)
- **Informed By**: spike-3 (confirmed: `OutputHandler.send` is stateless, supports N calls; mid-loop delivery via `asyncio.run_coroutine_threadsafe` is the pattern)
- **Assigned To**: adapter-builder
- **Agent Type**: builder
- **Parallel**: false (depends on PTYPool interface for `acquire_pair` usage)
- Create `agent/granite_container/bridge_adapter.py` with `BridgeAdapter` class: `__init__(agent_session, project_key, transport)`, `async run(user_message, working_dir)` returns short string
- Resolve `send_cb` once at construction via `agent_session_queue._resolve_callbacks(project_key, transport)`; store `chat_id`, `reply_to_msg_id` from the agent_session
- Add `on_user_payload: Callable[[str], None]` and `on_complete_payload: Callable[[str], None]` to `Container.__init__` (in `agent/granite_container/container.py`); default `None` preserves PoC behavior
- In `BridgeAdapter.run`, pass callables that wrap `send_cb` in `try/except`, log warnings, write to `agent_session.session_events` on failure
- Implement `format_short_result(result: ContainerResult) -> str`: returns 1-2 line summary, empty for `pm_complete`/`pm_user` (mid-loop delivery handled it)
- Acquire PTY pair from the pool, run container in `asyncio.to_thread`, return short result
- Add 6-8 unit tests: send_cb called for each `[/user]`; send_cb called once for `[/complete]`; failed send_cb logs and continues; session_events entries written; short result format for each exit_reason; fallback flag disabled → existing harness path used

### 4. Phase 3 — Executor wiring
- **Task ID**: build-wiring
- **Depends On**: build-bridge-adapter
- **Validates**: `tests/unit/test_session_executor.py` (UPDATE: mock the new path; fallback flag tests)
- **Informed By**: spike-2 (confirmed: `asyncio.to_thread` + BackgroundTask unchanged)
- **Assigned To**: wiring-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `GRANITE_PTY_ENABLED` and `GRANITE_PTY_POOL_SIZE` to `config/settings.py` (env-overridable)
- In `agent/session_executor.py:1700`, branch on `GRANITE_PTY_ENABLED`: `True` → new `BridgeAdapter.run` path; `False` → existing `get_response_via_harness` call (preserved)
- The new path: `task = BackgroundTask(messenger=messenger, working_dir=str(working_dir), project_key=...)`; `await task.run(adapter.run(...), send_result=False)`
- Modify the existing `_handle_dev_session_completion` call site to skip the dev-completion nudge when in granite mode (granite has its own PM/Dev handoff)
- In `worker/__main__.py` startup, add `pkill -f "claude --permission-mode bypassPermissions"` (best-effort) before the PTYPool singleton initializes
- Add 2-3 unit tests: `GRANITE_PTY_ENABLED=True` calls BridgeAdapter.run; `GRANITE_PTY_ENABLED=False` calls `get_response_via_harness`; send_result=False is the right call

### 5. Phase 4 — PoC deletion
- **Task ID**: build-poc-delete
- **Depends On**: build-wiring
- **Validates**: `grep -r 'granite_agent_loop\|granite_router\|claude_session' --include='*.py' /Users/valorengels/src/ai/` returns zero references
- **Informed By**: PoC's `granite_container/` module supersedes all
- **Assigned To**: poc-deleter
- **Agent Type**: builder
- **Parallel**: false (must verify no remaining callers)
- Delete `agent/granite_agent_loop.py`
- Delete `agent/granite_router.py`
- Move `_RESUME_HINT_RE` from `agent/claude_session.py:49-51` to a new `agent/granite_container/_constants.py` (or inline into `pty_driver.py`), then delete `agent/claude_session.py`
- Delete `scripts/granite_poc.py`
- Delete `scripts/granite_questions_game.py`
- Delete `tests/unit/test_granite_agent_loop.py`
- Delete `tests/unit/test_granite_router.py`
- Delete `tests/unit/test_claude_session.py` (after the regex import is moved)
- Delete `tests/unit/test_granite_questions_game.py` and `tests/unit/test_granite_poc.py` if they exist
- Run `grep` to verify no remaining references; run `pytest tests/` to confirm

### 6. Phase 5 — Integration test
- **Task ID**: build-integration
- **Depends On**: build-wiring, build-poc-delete
- **Validates**: `tests/integration/test_granite_pty_production.py` (CREATE: end-to-end)
- **Informed By**: spike-2 + spike-3
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/integration/test_granite_pty_production.py` with one test: simulates a bridge-originated session, mocks the pexpect layer to drive a deterministic PM→Dev cycle, asserts `send_cb` is called for each `[/user]` turn, `agent_session.status == "completed"`, and `telegram:outbox:{session_id}` contains the expected payloads
- Mark the test `@pytest.mark.granite_integration` so it can be skipped in fast CI
- The test does NOT spawn a real `claude` process — it mocks `pty_driver.PTYDriver.spawn` and drives a script of pre-canned byte responses

### 7. Validation — cross-cutting
- **Task ID**: validate-cross
- **Depends On**: build-ansi, build-pty-pool, build-bridge-adapter, build-wiring, build-poc-delete, build-integration
- **Assigned To**: cross-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -n auto --dist=loadfile`; expect zero failures
- Run `python -m ruff check . && python -m ruff format .`; expect zero issues
- Run `grep -r 'claude -p' agent/ --include='*.py'`; expect only references in `agent/sdk_client.py` (the harness path, behind the flag)
- Verify `GRANITE_PTY_ENABLED=False` restores the headless path: set the env var in a test, run `_execute_agent_session`, assert `get_response_via_harness` was called (mocked) and `BridgeAdapter.run` was NOT called
- Run `ps aux | grep 'claude --permission-mode'` before and after a 3-run test session; expect ≤ pool-size × MAX_CONCURRENT_SESSIONS processes
- Verify no PoC files remain: `ls agent/granite_agent_loop.py agent/granite_router.py agent/claude_session.py scripts/granite_poc.py scripts/granite_questions_game.py 2>&1` returns all "No such file" errors

### 8. Documentation cascade
- **Task ID**: build-docs
- **Depends On**: validate-cross
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/granite-pty-production.md` (production path, PTYPool, BridgeAdapter, harness fallback flag)
- Update `docs/features/README.md` index table
- Update `docs/features/subconscious-memory.md` to note granite sessions participate in memory extraction
- Cross-link `docs/infra/email-cs-auto-reply.md` and the new `docs/features/granite-pty-production.md`
- Add `## Granite PTY Pool` section to `docs/deployment.md` (env vars, relationship to MAX_CONCURRENT_SESSIONS)
- Update `CLAUDE.md` "System Architecture" diagram to show PTY container as primary path

### 9. Live smoke test (PR template checkbox)
- **Task ID**: validate-live
- **Depends On**: validate-cross, build-docs
- **Assigned To**: reviewer (via /do-pr-review)
- **Agent Type**: reviewer
- **Parallel**: false
- The PR template includes a "Live smoke test" checkbox
- The reviewer confirms: a real `claude` TUI session reaches `Container.run`, produces a `[/user]` delivery to Telegram, and the `AgentSession` shows `completed` status
- If the live smoke test fails, the reviewer requests changes; the harness fallback gate (`GRANITE_PTY_ENABLED=False`) is the immediate mitigation
