---
status: Refresh (cycle N+1 revision)
type: feature
appetite: Medium
owner: Valor
created: 2026-05-02
refreshed: 2026-05-06
revision_cycle: N+1
revised_for_critique: docs/plans/critique/byob_and_computer_use.md
tracking: https://github.com/tomcounsell/ai/issues/1256
last_comment_id: IC_kwDOEYGa088AAAABA-geYQ
followups:
  - https://github.com/tomcounsell/ai/issues/1274
shipped_via:
  - https://github.com/tomcounsell/ai/pull/1277  # Track 1+2 foundation: BYOB MCP + computer-use skill
  - https://github.com/tomcounsell/ai/pull/1286  # Followup #1274: per-skill agent-browser → BYOB migration
---

# BYOB Real-Chrome Control + macOS Computer Use — Refresh Plan

> **Why this exists as a "refresh" and not a fresh plan.** The original plan (`docs/plans/byob_and_computer_use.md`,
> 1164 lines, rev4) was migrated post-merge in commit `57582da0` after PR #1277 landed. That deletion was premature
> against the issue's full Acceptance Criteria, and m13v's review comment on #1256 surfaced **three architectural
> questions that were not resolved in PR #1277 and remain open for the remaining surface**. Issue #1256 is still
> OPEN. This plan re-opens the remaining slice and decides those three questions before any new build.

---

## Shipped / In-Progress / Remaining (Audit)

### Shipped (closed, in main, verified on disk)

The foundation work from issue #1256 has shipped. Verified in current `main` (commit `5576e4dc`):

| Acceptance criterion (from issue body) | Where it shipped | Disposition |
|---|---|---|
| BYOB MCP server registered in `~/.claude.json` | PR #1277 (commit `ce44e1e4`) — `scripts/update/mcp_byob.py` | **DONE.** `mcp_byob.verify_byob_mcp` runs from `scripts/update/run.py:898` on every `/update`. |
| `BYOB_ALLOW_EVAL=0` is the registered default | PR #1277 — `scripts/update/mcp_byob.py:86` | **DONE.** Drift-heal back to `"0"` confirmed at line 115. |
| `~/.byob/` install + Chrome MV3 extension | PR #1277 — `/setup` skill body, `config/byob_pin.json` | **DONE.** Pin commit `7e346c5ab1cd9d837eae8b756eab98ed02c78854`. |
| `tools/computer/__init__.py` HTTP wrapper for bcu loopback API | PR #1277 — `tools/computer/{__init__.py, cli.py, electron_bundles.py}` | **DONE.** |
| `valor-computer` CLI entry point | PR #1277 — `pyproject.toml:86` declares `valor-computer = "tools.computer.cli:main"` | **DONE.** |
| OS gate exits 78 (`EX_CONFIG`) on non-macOS | PR #1277 — `tools/computer/cli.py:_enforce_os_gate` | **DONE.** |
| `computer-use` skill body | PR #1277 — `.claude/skills/computer-use/SKILL.md` | **DONE.** |
| `AgentSession.requires_real_chrome` Popoto field | PR #1277 — `models/agent_session.py:285` | **DONE.** |
| Worker scheduler defers concurrent real-Chrome sessions | PR #1277 — `agent/session_pickup.py:57-69, 425` | **DONE.** Same-process serialization. |
| `bcu_pin.json` and `byob_pin.json` exist | PR #1277 — `config/{byob_pin.json, bcu_pin.json}` | **DONE.** |
| Bridge-side inference auto-flags Telegram/email sessions for `requires_real_chrome` | PR #1286 (commit `1c50ded6`, hotfix `87de9664`) — `agent/byob_skill_triggers.py` | **DONE.** Adding a new BYOB-migrated skill = add a row to `BYOB_SKILL_TRIGGERS`. |
| Per-skill migration of `linkedin` to BYOB MCP | PR #1286 (followup #1274) | **DONE** with behavioral smoke artifact at `tests/manual/linkedin_byob_smoke.txt`. |
| Per-skill migration of `do-design-audit`, `do-pr-review` (incl. `screenshot.md`) to dual-surface allowlist | PR #1286 | **DONE.** |
| Doc-only updates for `do-design-system`, `prepare-app`, `do-test`, `README.md` | PR #1286 | **DONE.** |
| `do-discover-paths` and `mermaid-render` documented "stays on agent-browser" with reason | PR #1286 (commit `1dcd08bf`) | **DONE.** |
| `~/.byob/` MCP gating (skip registration when binaries absent) | Hotfix `ac8501f4` | **DONE.** |
| `tsx` path resolves to BYOB workspace root `node_modules` | Hotfixes `f2dd99fc`, `23994db5` | **DONE.** |
| Tests: `tests/integration/test_byob_scheduler.py`, `tests/unit/test_mcp_byob_registrar.py`, `tests/unit/test_byob_skill_triggers.py` | PR #1277, #1286 | **DONE.** |
| Feature docs: `docs/features/byob-browser-control.md`, `docs/features/computer-use.md` | PR #1277 (commit `f51a30ce`) | **DONE.** Indexed in `docs/features/README.md:29, 36`. |

### In Progress (none)

There are no in-flight branches/PRs/sessions touching the BYOB or computer-use surface as of `2026-05-06 01:07 UTC`.

### Remaining (this plan's actual scope)

The work remaining under issue #1256 is **the three architectural questions m13v raised that PR #1277 did not
resolve, plus the executable-acceptance gap on the issue's user-facing criteria**. Specifically:

1. **Multi-process real-Chrome serialization gap.** The shipped scheduler (`agent/session_pickup.py`) only
   serializes within the worker process. Any consumer of BYOB MCP that bypasses the session machinery —
   `claude -p "use byob_..."` directly, hook-spawned agents, dashboard maintenance scripts, sub-agent
   invocations — collides silently with a worker-running session on the same Chrome tab. Per memory
   `feedback_prevention_over_cleanup`, that's a gap, not a feature. **Cycle N+1 revision**: the lock
   moves from session-creation (which only catches `valor-session create` callers) to the BYOB MCP
   server entrypoint itself (which catches every consumer regardless of how the parent process was
   spawned).
2. **Chrome concurrency model decision is not documented in the SKILL/feature docs as a contract.** Operators
   reading `byob-browser-control.md` today see a description of *what serialization does* but not *what they
   may safely run concurrently*. The decision below makes that contract explicit.
3. **Electron AX staleness retry strategy is described but not enforced.** `tools/computer/__init__.py` accepts
   a `selector={...}` dict for Electron apps, but on a stale-ref failure it returns `{"error": ...}` and
   relies on the caller to retry. Unmemoized retry inside `tools/computer` for the known-Electron path closes
   the gap.
4. **Issue acceptance criteria not yet promoted to executable proof artifacts.** The issue lists three
   user-facing checks ("agent reads authenticated page with zero state.json files", "bcu doesn't move the
   user's cursor", "BYOB-down clarity"). One of three has a captured artifact (`linkedin_byob_smoke.txt`).
   The remaining two need runnable commands and stored outputs.
5. **m13v open question: MCP-vs-CLI lifecycle for `valor-computer`.** Track 1 (BYOB) is settled — MCP only.
   Track 2 (`valor-computer`) is CLI-only today; each invocation re-reads the runtime manifest and respawns
   an HTTP client. m13v's connection-lifecycle critique applies: long sessions doing many `valor-computer`
   calls re-handshake the bcu HTTP server every time. Decision required: leave as-is, or add a persistent
   client / tools-as-MCP for bcu.

Everything below resolves these five items.

---

## Problem

Three structural concerns from PR #1277 / #1286 remain unresolved and block calling issue #1256 closed:

**Current behavior (post-shipped state):**

- **Multi-process Chrome race**: `agent/session_pickup.py` serializes BYOB sessions within the worker process
  only. A second Python process (manual dev session, repl, dashboard maintenance script) that touches BYOB MCP
  can collide with a worker-running session on the same Chrome tab. Symptoms: half-rendered DOM snapshots,
  navigation fighting, the user's active tab silently switched.
- **`valor-computer` re-handshakes bcu on every call**: each shell invocation reads
  `$TMPDIR/background-computer-use/runtime-manifest.json`, opens a fresh HTTP connection, runs one action,
  exits. For a sequence (`list_apps` → `list_windows` → `click` → `type_text` → `screenshot_window`) that's
  five separate process spawns + manifest reads + TCP connects on loopback. Over a real session this adds
  measurable latency and falls over if bcu is mid-restart between calls.
- **Electron AX staleness**: `tools/computer/__init__.py` advertises a selector-aware Electron API but does
  not internally retry on stale-ref. Skills must implement their own retry loops, which they don't.
- **Two of three user-facing acceptance criteria from the issue body are unverified**: the BYOB authenticated
  GitHub end-to-end and the bcu-cursor-doesn't-jump check have no captured artifact.

**Desired outcome:**

- A single contract — written into `docs/features/byob-browser-control.md` and enforced by code — for which
  processes may concurrently touch real Chrome, with a guard that fails loudly when violated.
- `valor-computer` either retains its current shape with a documented latency budget, or moves to a persistent
  surface; the decision is recorded in this plan and reflected in the SKILL body.
- `tools/computer/__init__.py` retries internally for known-Electron windows when a stale-ref error fires,
  re-querying `get_window_state` and resolving the selector to a fresh ref before failing the call.
- Two new behavioral smoke artifacts under `tests/manual/` cover the BYOB-authenticated and bcu-no-cursor
  criteria from the issue body.

## Freshness Check

**Baseline commit:** `5576e4dc907247076b3154e4f73fef96ff0e92f0`
**Issue filed at:** 2026-05-01T15:32:11Z
**Refresh disposition:** Major progress — most of the plan shipped via PR #1277 (merged 2026-05-05) and
PR #1286 (merged 2026-05-05). Issue #1256 remains OPEN (`gh issue view 1256 --json state` → `OPEN`). The
remaining scope is the three open architectural questions from m13v's review comment plus two
acceptance-criterion proof artifacts.

**File:line references re-verified on `5576e4dc`:**
- `models/agent_session.py:279-285` — `requires_real_chrome` Popoto field present, default `False`. Confirmed.
- `agent/session_pickup.py:57-69, 425` — scheduler-pick gate checks `requires_real_chrome` against running
  sessions in the worker process. Confirmed. **Multi-process gap acknowledged here**: the check uses
  `AgentSession.query` which sees DB state, but a non-worker Python process holding an MCP client open is
  invisible to the gate.
- `tools/computer/__init__.py` — `ComputerUseUnavailableError` raised on missing manifest. Selector dict
  accepted. **Internal retry on stale-ref not yet present** (the decision below adds it).
- `tools/computer/cli.py:_enforce_os_gate` — exits 78 on non-darwin. Confirmed.
- `scripts/update/mcp_byob.py:74-115` — `BYOB_ALLOW_EVAL=0` written and drift-healed. Confirmed.
- `agent/byob_skill_triggers.py` — `BYOB_SKILL_TRIGGERS` registry + `infer_requires_real_chrome` exist.
  Bridge enqueue paths in `bridge/telegram_bridge.py` and `bridge/email_bridge.py` call this before
  `enqueue_agent_session`. Confirmed in commit `87de9664`.
- `docs/features/byob-browser-control.md`, `docs/features/computer-use.md` — both present, indexed in
  `docs/features/README.md:29, 36`. Confirmed.

**Cited sibling issues/PRs re-checked:**
- #1274 — CLOSED 2026-05-05 by PR #1286 merge.
- #1277 — MERGED 2026-05-05 (commit `ce44e1e4`).
- #1286 — MERGED 2026-05-05 (commit `1c50ded6`).
- #66 (Telegram desktop control) — closed pre-1256 in favor of this umbrella; computer-use skill is the
  primitive, higher-level Telegram workflows build on top.

**Commits on main since PR #1286 merged (touching `tools/computer/`, `tools/browser/`, `agent/byob_*`,
`scripts/update/mcp_byob.py`, BYOB-using skills):** None. The shipped surface is stable; nothing has
landed against it in the 24 hours between #1286 and this refresh.

**Active plans in `docs/plans/` overlapping this area:** None. `docs/plans/agent_browser_to_byob_skill_migration.md`
is the followup #1274 plan, now docs-complete; this refresh does not modify it.

**Notes:** No drift in the shipped surface. The freshness baseline is `5576e4dc`.

## Prior Art

- **PR #1277** (merged 2026-05-05) — shipped Track 1 (BYOB MCP) + Track 2 (computer-use skill +
  `tools/computer/`). Includes scheduler gate via `requires_real_chrome` Popoto field.
- **PR #1286** (merged 2026-05-05) — shipped per-skill migration: `linkedin` to BYOB,
  `do-design-audit` and `do-pr-review` to dual-surface allowlist, doc-only updates for the rest. Bridge
  inference layer added so Telegram/email sessions pick up the scheduler gate automatically.
- **PR #1277 review tech-debt commit** `0aaad7bb` ("address PR #1277 review tech debt + nit") cleaned
  small post-merge issues; m13v's three architectural questions were not in scope.

The refresh below explicitly addresses m13v's three open questions as Decisions 1–3.

## Research

The five hotfixes that landed between PR #1277 merge and PR #1286 merge document the on-disk reality of
BYOB v0.3+:

- **`23994db5` / `f2dd99fc`** — `tsx` is at `~/.byob/node_modules/.bin/tsx` (workspace root), not per-package.
- **`ac8501f4`** — MCP registration must gate on `~/.byob/` binaries existing; otherwise `/update` on a
  machine without BYOB installed fails noisily.
- **`87de9664`** — bridge-side inference is the right layer for Telegram/email entry points; the worker's
  `requires_real_chrome` check is too late if the field isn't set at enqueue.
- **`1dcd08bf`** — three skills (`do-discover-paths`, `mermaid-render`, `bowser`) are documented as
  staying on `agent-browser` because they need anonymous/headless surfaces or `browser_eval` (which BYOB
  blocks by `BYOB_ALLOW_EVAL=0`).

These all hardened the shipped surface; none address m13v's three open questions.

## Decisions (m13v's Three Open Questions)

### Decision 1 — Chrome concurrency model: lock at the MCP-server entrypoint (cycle N+1 revision)

**The question:** real Chrome has one DOM tree. The shipped scheduler serializes within the worker process.
What about other Python processes (dev sessions started manually, dashboard scripts, hooks, direct
`claude -p "use byob_..."`, sub-agent invocations) that touch BYOB MCP?

**Decision (revised cycle N+1):** **The lock lives in the BYOB MCP server invocation path, not at session
creation.** Every consumer of BYOB MCP — interactive `claude` session, `claude -p "..."`, hook-spawned
agent, manual debug script, worker session — auto-spawns the BYOB MCP child via the user-scoped
`~/.claude.json` registration (verified at `scripts/update/mcp_byob.py:39`). The only architectural place
that catches all of them is the MCP child itself.

**Mechanism (revised):**

1. **Wrapper at the MCP-server entrypoint (the only required guard).** A new Python module
   `tools/byob/mcp_gate.py` is registered as the `command` in `_expected_entry()` (`scripts/update/mcp_byob.py:82-88`)
   in place of the raw `tsx` invocation. The wrapper:
   - Acquires `acquire_byob_session_lock(owner_id=f"mcp-gate-{os.getpid()}")` first.
   - On `BYOBSessionLockHeld`: writes a JSON-RPC error response that Claude Code surfaces to the agent
     (`{"jsonrpc":"2.0","error":{"code":-32000,"message":"BYOB busy: session X holding lock (PID Y); see http://localhost:8500/sessions/X"}}`)
     and exits 1.
   - On success: `os.execvp` into the real `tsx <byob-mcp.ts>` invocation so stdio passes through unchanged.
   - The lock is released by `os.kill(pid, 0)` failing on the wrapper's PID when the parent claude session
     exits — handled by the same liveness-check + 30-min staleness backstop that lives inside
     `acquire_byob_session_lock` (see Decision 1's lock-leak observability addendum).
2. **Worker session-pickup keeps its existing role** but is now defense-in-depth, not the primary guard.
   `agent/session_pickup.py:57-69, 425` already refuses to pick a `requires_real_chrome` session when a
   different one is running — that prevents two worker sessions from racing each other inside the same
   process; the MCP-gate handles cross-process races.
3. **`tools/byob/lock.py` exposes `acquire_byob_session_lock(owner_id)` / `release_byob_session_lock(owner_id)`
   / `cleanup_stale_locks()`** as before. The MCP gate, the worker, and any test harness all use the same
   three-function API.
4. **No `--force-local` / `--bypass-worker` flag.** Per cycle N+1 critique C4: such a flag is misleading
   (it actually means "skip-worker-queue", not "force-local"), narrow in scope (only meaningful with
   `--needs-real-chrome`), and now redundant — the MCP-gate covers every entry point regardless of how
   the parent process was spawned. A developer who needs a manual BYOB session simply runs
   `claude -p "use byob_navigate ..."`; the MCP-gate handles serialization.

**Trade-off accepted, written into `docs/features/byob-browser-control.md`:** "BYOB serializes globally on
this machine via a lock acquired by the MCP server itself. If you want anonymous parallel browsing, use
`bowser`. If you want two BYOB-driven sessions in two windows at once, you can't — that's by design
(Chrome has one DOM tree)."

**Rejected alternatives (unchanged):**
- Per-agent Chrome profiles. Defeats the "use the user's logged-in browser" goal at the heart of #1256.
- Per-agent dedicated Chrome instances. Same. Plus operator UX cost (multiple Chrome icons in the dock).
- `flock(2)` on the BYOB Unix socket. The socket is per-device-UUID under `~/.byob/bridges/`; locking
  the socket file races against BYOB itself rotating the bridge process.

**Rejected alternatives added in cycle N+1:**
- Lock at session-creation (`tools/valor_session.py`). Misses every direct `claude -p` and hook-spawned
  consumer that bypasses the session machinery (cycle N+1 critique B1).
- A `--bypass-worker` operator flag. Misnamed, narrow, and made redundant by the MCP-gate (cycle N+1
  critique C4).

### Decision 2 — MCP-vs-CLI lifecycle for `valor-computer`: keep CLI (measured)

**The question:** does `valor-computer` re-handshake the bcu HTTP server on every invocation?

**Answer:** yes. Each `valor-computer X` invocation: spawns Python → reads the runtime manifest → opens a
fresh `urllib.request` connection to `127.0.0.1:<port>` → runs one HTTP call → exits. Over a sequence of N
actions, that's N process spawns and N TCP handshakes.

**Decision (revised cycle N+1, with measured numbers):** **Keep `valor-computer` as a CLI.** The measurement
ran during this revision pass — not deferred to "build phase 0" — per cycle N+1 blocker B2.

**Measured baseline (cycle N+1, machine: Tom's MacBook Pro, Darwin 25.4.0, 2026-05-06):**

| Mode | N | Median | p95 | p99 |
|---|---|---|---|---|
| Direct subprocess | 50 | 78.6 ms | 82.3 ms | 88.2 ms |
| Via /bin/bash -c (skill path) | 50 | 81.6 ms | 86.9 ms | n/a |

Captured at `tests/manual/valor_computer_latency_baseline.txt`. The 200 ms threshold is met with >2x
headroom; the 500 ms "flip-to-MCP-now" threshold is met with >6x headroom.

**Caveat:** bcu opt-in is not set on this measurement machine. The HTTP loopback round-trip when bcu
runs adds ~1–10 ms (in-kernel zero-copy on macOS). Even at the upper end, the budget is intact.
Re-measurement on a build machine with bcu enabled is queued as a non-blocking confirmation step
(if real-bcu numbers blow past 200 ms, escalate to MCP-now in a followup issue — but the 6x headroom
makes that highly unlikely).

**Reasoning still standing:**

- bcu's HTTP server is loopback-only. TCP-on-loopback handshake on macOS is sub-millisecond.
- Promoting `tools/computer/` to an MCP server means ~13 new tools loaded into agent context on every
  session — meaningful token cost for a feature only used in macOS desktop workflows.
- bcu itself is a long-running process. It does not get GC'd between calls (m13v's MCP-vs-CLI critique
  applies to BYOB's *native messaging host*, which Chrome may GC; bcu has no such issue).

**Operational guard added by this plan**: `tools/computer/__init__.py` gains a `_BCU_BASE_URL_CACHE` that
memoizes the manifest read for the lifetime of the Python process, so when one CLI invocation makes multiple
calls (rare today but possible if a future skill drives a sequence in a single CLI process), only the first
call touches the manifest. This is forward-compat for either keeping CLI or moving to MCP later.

**Trade-off accepted, written into `docs/features/computer-use.md`:** "`valor-computer` invocations have
~80 ms startup cost each (measured 2026-05-06: median 81.6 ms / p95 86.9 ms via shell). For sequences of
more than ~10 actions, expect ~1 s of overhead. If this becomes a bottleneck for a real workflow, file an
issue requesting a persistent surface."

**Rejected alternative:** **moving to MCP now is rejected** because:
- No measured workflow today is in the regime where the CLI cost matters; spike-r2 (median 81.6 ms, p95
  86.9 ms) is well within the 200 ms threshold.
- The issue acceptance criteria do not specify a latency budget.
- Adding ~13 MCP tools to context costs every session token, including non-macOS sessions.
- The CLI surface's OS gate (exit 78 on non-darwin) is cleaner than a "tools fail at runtime on Linux"
  shape.

### Decision 3 — Electron AX staleness: internal retry, with fail-loud tie-break (cycle N+1 revision)

**The question:** Slack, VS Code, Telegram Desktop, Discord build their AX tree lazily; refs go stale
between `get_window_state` and the next action. What's the retry strategy?

**Decision:** **`tools/computer/__init__.py` retries internally exactly once on a stale-ref failure when
the target window's `bundle_id` is in `tools/computer/electron_bundles.py`. When the resolver finds 2+
candidates with equal `role` + `label` matches, it raises `MultipleSelectorMatches` rather than silently
sorting by Euclidean distance** (cycle N+1 critique C1: silent miss-clicks were the failure mode the
original wording allowed). Concretely:

1. Each action function (`click`, `type_text`, `set_value`, `drag`, `perform_secondary_action`) takes an
   optional `selector={'role': ..., 'label': ..., 'bounds': (x, y, w, h), 'tie_break': '...'}` argument.
2. When the call returns bcu's stale-ref error code (HTTP 422 or 4xx with `{"error": "stale_ref"}` payload —
   exact shape verified in build via `tests/integration/test_computer_use_integration.py`) AND the target
   window's `bundle_id` is in the Electron list, the wrapper:
   - Re-calls `get_window_state(window_id)` to refresh the AX tree.
   - Resolves the selector to a fresh ref by matching `role` + `label`. If exactly one match: use it.
   - **If 2+ matches and the selector does not contain `tie_break: 'nearest'`: raise
     `MultipleSelectorMatches(role, label, count, candidates_summary)` (fail-loud — the original
     Euclidean-tie-break is opt-in only).**
   - If 2+ matches and `tie_break='nearest'`: break ties by Euclidean distance to the original `bounds`
     center, prefer visible candidates.
   - Retries the action exactly once.
3. If the retry fails (stale-ref or anything else), the caller sees the second-attempt error.
4. Non-Electron windows: no retry (their AX trees are stable; a stale-ref there is genuinely an error).
5. Caller without `selector=`: no retry on either kind of window (we have no way to resolve the ref).

**The `is_electron` heuristic** is already shipped at `tools/computer/electron_bundles.py`; this plan adds
the retry wrapper, the fail-loud tie-break, and the integration tests for both paths.

**Resolver perf — measured in cycle N+1:** the resolver (depth-first walk + role/label/bounds filter) is
not a hot path. Measured at `tests/manual/selector_resolver_perf_baseline.txt`:

| AX tree size | Median | p95 |
|---|---|---|
| 100 nodes (typical Slack pane) | 0.013 ms | 0.019 ms |
| 1000 nodes (stress) | 0.15 ms | 0.16 ms |

Five orders of magnitude inside the 1-second budget the cycle N+1 critique flagged. **No pre-resolved
selector cache is needed** — the bcu HTTP `get_window_state` round-trip dominates the retry path.

**Trade-off accepted, written into `.claude/skills/computer-use/SKILL.md`:**

- "For Electron apps, always pass `selector=` so the wrapper can heal a stale ref. For native AppKit apps,
  pass either ref or selector — both work."
- "**Gotcha — multiple matching elements.** If your selector matches 2+ elements (e.g. two `Send` buttons
  in different Slack threads, two `OK` buttons in stacked modals), the wrapper raises
  `MultipleSelectorMatches` rather than guessing. Add a discriminator (`parent_role`, `index`, or tighter
  `bounds`), or pass `tie_break='nearest'` explicitly to opt into Euclidean-distance tie-breaking."

---

## Spike Results

### spike-r1: Confirm `agent/session_pickup.py` does NOT see non-worker BYOB clients

- **Assumption**: "The shipped scheduler gate covers all BYOB MCP traffic on this machine."
- **Method**: code-read (`agent/session_pickup.py:57-69`) + manual reasoning about process boundaries
- **Finding**: The check is `AgentSession.query.filter(...)` — it inspects the Popoto store. A Python
  process that opens an MCP client without creating an `AgentSession` row is invisible to the gate.
  Dev sessions created via `python -m tools.valor_session create` *do* create rows, but they are picked
  up by the worker, so they go through the same gate. The gap is: **a Python process that uses BYOB MCP
  for ad-hoc work without going through the session machinery** — including a dashboard script, a one-off
  repl invocation, a hook-spawned `claude -p`, or a developer running `claude -p "use byob_..."`
  directly. Crucially, BYOB MCP is registered in user-scoped `~/.claude.json`
  (`scripts/update/mcp_byob.py:39`), so **every `claude` / `claude -p` invocation auto-spawns the BYOB
  MCP child via stdio** — that spawn is the surface area that needs gating.
- **Confidence**: high (code-verified)
- **Impact on plan (revised cycle N+1)**: Decision 1 moves the lock to the **MCP-server entrypoint
  itself** via `tools/byob/mcp_gate.py`, which `_expected_entry()` registers as the `command` in
  `~/.claude.json`. This catches every consumer regardless of how the parent process was spawned. The
  worker session-pickup gate stays as defense-in-depth.

### spike-r2: Measure `valor-computer` per-invocation cost — RUN, NOT DEFERRED

- **Assumption**: "Per-invocation overhead is small enough that CLI shape is fine."
- **Method (cycle N+1)**: 50 invocations of `valor-computer list_apps` measured two ways: direct
  `subprocess.run` and via `/bin/bash -c` (mirrors how skills invoke the CLI through the agent's Bash
  tool). Recorded with `time.perf_counter` for ms precision.
- **Result (measured 2026-05-06 on Tom's MacBook Pro, Darwin 25.4.0):**

  | Mode | N | Median | p95 | p99 |
  |---|---|---|---|---|
  | Direct subprocess | 50 | 78.6 ms | 82.3 ms | 88.2 ms |
  | Via /bin/bash -c | 50 | 81.6 ms | 86.9 ms | n/a |

- **Confidence**: high (measured)
- **Impact on plan**: **Decision 2 (CLI surface) is confirmed.** No flip to MCP. The 200 ms threshold is
  met with >2x headroom; the 500 ms flip-to-MCP-now threshold is met with >6x headroom.
- **Caveat**: bcu opt-in is not set on this machine. The HTTP loopback round-trip when bcu runs adds
  ~1–10 ms. Even at the upper end the budget is intact.
- **Artifact**: `tests/manual/valor_computer_latency_baseline.txt`.

### spike-r3: Selector-resolver perf — RUN, NOT DEFERRED

- **Assumption**: "The selector-resolver is fast enough that no pre-resolved cache is needed."
- **Method (cycle N+1)**: Reconstructed the resolver hot-path from `tools/computer/__init__.py`
  (`_walk_ax_tree` + role/label/bounds filter), fed it a synthetic Slack/VSCode-shaped AX tree (mixed
  AXButton/AXStaticText/AXGroup/AXList/AXTextField, depth ~10, realistic label pool). Ran the resolver
  50 times each at 100-node and 1000-node tree sizes.
- **Result (measured 2026-05-06):**

  | Tree size | N | Median | p95 |
  |---|---|---|---|
  | 100 nodes (typical) | 50 | 0.013 ms | 0.019 ms |
  | 1000 nodes (stress) | 50 | 0.15 ms | 0.16 ms |

- **Confidence**: high (measured against the actual shipped resolver code)
- **Impact on plan**: **Decision 3's tree re-fetch + resolver pass is confirmed.** No pre-resolved
  selector cache is needed. The bcu HTTP `get_window_state` round-trip (~1–10 ms loopback) dominates
  the retry path; the resolver is invisible.
- **bcu stale-ref response shape**: still observed during build phase 0 (`tests/integration/
  test_computer_use_integration.py` captures bcu's actual error envelope on the live machine). If the
  shape is not HTTP 422 or `{"error": "stale_ref"}`, the wrapper falls back to text-match on the
  response — fixture committed alongside the integration test.
- **Artifact**: `tests/manual/selector_resolver_perf_baseline.txt`.

---

## Data Flow

The end-to-end data flow is unchanged from the shipped surface; this refresh adds three guards.

### Track 1 — BYOB browser automation (post-refresh, cycle N+1 revision)

```
agent (Claude Code) ──MCP stdio──▶ python -m tools.byob.mcp_gate
                                      │  (NEW: lock-acquire wrapper)
                                      │
                                      ▼ (lock free) os.execvp ──▶ tsx <byob-mcp.ts>
                                                                          │
                                                                          ▼
                                                                  byob-bridge socket
                                                                          │
                                                                  Native Messaging
                                                                          ▼
                                                           Chrome MV3 extension (active tab)
                                                                          │
                                                                          ▼
                                                                DOM/screenshot result up the chain

mcp_gate flow on EVERY claude / claude -p invocation:
  1. acquire_byob_session_lock(owner_id=f"mcp-gate-{os.getpid()}")
  2. on BYOBSessionLockHeld → write JSON-RPC error → exit 1
     (claude surfaces the message to the agent: "BYOB busy: session X
      holding lock; see http://localhost:8500/sessions/X")
  3. on success → os.execvp into real tsx invocation; stdio passes through
  4. on parent claude exit → lock holder PID dies → liveness check + 30-min
     staleness backstop reaps the lock on the next acquire attempt

Worker session-pick (defense-in-depth, unchanged behavior):
  1. Read AgentSession.requires_real_chrome
  2. If True, check no other running session has it True (shipped gate at
     agent/session_pickup.py:57-69, 425)
  3. The mcp_gate handles the cross-process race; this gate handles the
     within-process race
```

### Track 2 — Computer-use (post-refresh)

```
skill ─────▶ valor-computer <cmd> args... (CLI)
                  │
                  ▼ (Python startup, ~100ms one-time)
            tools.computer.cli:main
                  │
                  ▼ (NEW: cached manifest read)
            tools.computer.{click, type_text, ...}
                  │
                  ▼ HTTP urllib.request → 127.0.0.1:<bcu_port>
            bcu Swift app
                  │
                  ▼ macOS Accessibility API
            target window (no cursor movement)

Electron-app retry path (Decision 3):
  - bcu returns stale_ref
  - tools.computer detects bundle_id in electron_bundles.py
  - re-calls get_window_state(window_id)
  - resolves selector to fresh ref
  - retries action once
  - returns result (or second-attempt error)
```

## Architectural Impact

- **New files (cycle N+1 revision)**:
  - `tools/byob/__init__.py` (empty marker).
  - `tools/byob/lock.py` (~50 lines: `acquire_byob_session_lock`, `release_byob_session_lock`,
    `cleanup_stale_locks`, `BYOBSessionLockHeld` exception, plus a 30-min staleness backstop).
  - `tools/byob/mcp_gate.py` (~40 lines: lock-acquire wrapper that `os.execvp`s into the real `tsx`
    invocation; this is the file `_expected_entry()` registers as the BYOB MCP `command`).
  - Three manual smoke artifacts under `tests/manual/`.
  - `tests/unit/test_byob_lock.py`, `tests/unit/test_byob_mcp_gate.py`,
    `tools/computer/tests/test_computer_use_integration.py::test_send_button_disambiguation_in_slack`.
- **Modified files (cycle N+1 revision)**:
  - `scripts/update/mcp_byob.py` — `_expected_entry()` registers `python -m tools.byob.mcp_gate`
    as the `command`, with `tsx <byob-mcp.ts>` as its args (the gate `os.execvp`s after lock-acquire).
  - `tools/computer/__init__.py` — manifest-cache + Electron retry wrapper + fail-loud
    `MultipleSelectorMatches` exception in `_resolve_selector`.
  - `tools/computer/cli.py` — pass `selector=` through.
  - `agent/session_pickup.py` — kept as-is for defense-in-depth (no lock-write needed; the
    mcp_gate covers it). Document the changed responsibility in the docstring.
  - `tools/doctor.py` — add `check_byob_lock_freshness()` returning WARN if the lock is held but
    stale or no backing AgentSession exists.
  - `ui/app.py` (`/dashboard.json`) — add `byob_lock` keys: `holder_pid`, `holder_owner_id`,
    `held_since_ts`, `is_stale`.
  - `docs/features/byob-browser-control.md` — document the MCP-gate concurrency contract +
    lock-file format + 30-min staleness backstop + dashboard URL for identifying holders.
  - `docs/features/computer-use.md` — document the per-invocation budget (with measured numbers)
    + Electron retry + fail-loud tie-break.
  - `.claude/skills/computer-use/SKILL.md` — document the `selector=` rule for Electron and the
    "Multiple matching elements" gotcha.
- **Removed (cycle N+1 revision)**:
  - The previously-planned `--force-local` / `--bypass-worker` flag on `tools/valor_session.py` is
    NOT added. The MCP-gate covers every entry point regardless of how the parent process was
    spawned, making the flag redundant (cycle N+1 critique C4).
- **No new dependencies.** All changes reuse stdlib (`fcntl`, `urllib.request`, `os.kill`,
  `os.execvp`) and existing modules.
- **Coupling**: low. The lock file is filesystem-state, not a new model field. The Electron retry
  is internal to `tools/computer/`. The MCP-gate is a transparent stdio wrapper.
- **Reversibility**: full. Restoring the previous shipped behavior is `_expected_entry()` writing
  `tsx` directly (one revert), plus deleting `tools/byob/`.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM check-ins.

**Interactions:**
- PM check-ins: 1 (sign-off on Decision 2 — "CLI for now, defer MCP" — before tests freeze).
- Review rounds: 1.

## Prerequisites

The Prerequisites table only gates on operator-required state. Build-installable items are handled
inline.

| Requirement | Check Command | Install Command (if missing) | Purpose |
|-------------|---------------|------------------------------|---------|
| Chrome (not Chromium) installed and BYOB extension already loaded (operator click-through from PR #1277 setup) | `cd ~/.byob && bun run doctor 2>&1 | grep -q "all green"` | Re-run `/setup` BYOB section | Decision 1 lock test must run against a real bridge |
| bcu opted-in and running (this machine) | `test -f ~/.config/valor/computer-use-enabled && curl -fs --max-time 1 "$(jq -r .base_url $TMPDIR/background-computer-use/runtime-manifest.json)/v1/list_apps"` | Run `/setup` and answer "yes" to computer-use opt-in | Decision 3 Electron retry test must hit a real bcu |

Run all checks: `python scripts/check_prerequisites.py docs/plans/byob_and_computer_use.md`

### Operator Setup (out of build scope)

| Operator Step | When | Provides |
|---------------|------|----------|
| Have the BYOB extension loaded in the operator's Chrome (per PR #1277 setup) | Before build phase 1 | Real-Chrome target for the Decision 1 cross-process lock test |
| Slack and VS Code installed and signed in | Before build phase 3 | Two known-Electron windows for the Decision 3 retry test |

---

## Solution

### Key Elements

- **`tools/byob/lock.py`**: New module. Three public functions —
  - `acquire_byob_session_lock(owner_id: str) -> None`: writes `~/.byob/active-session.lock` containing
    `f"{os.getpid()}\n{owner_id}\n{datetime.utcnow().isoformat()}\n"` atomically; raises
    `BYOBSessionLockHeld(holding_pid, owner_id)` if another live PID holds it.
  - `release_byob_session_lock(owner_id: str) -> None`: removes the file iff its content matches our
    PID + owner_id; logs a warning otherwise.
  - `cleanup_stale_locks() -> None`: idempotent reaper used at worker startup and `/update`.
  - **PID liveness check** uses `os.kill(pid, 0)` with `ProcessLookupError` swallowed.
  - **30-min staleness backstop (cycle N+1 critique C2)**: if the lock file's `mtime` is older than
    30 minutes AND `os.kill(pid, 0)` succeeds (PID is alive — could be PID reuse) AND no
    `AgentSession.query.filter(id == owner_id, status == "running").first()`, treat as orphaned
    (log warning with both PIDs, remove, proceed). This addresses PID-reuse (a stale lock holding
    PID 12345, where 12345 is now Spotlight or any unrelated process).
- **`tools/byob/mcp_gate.py`**: New module. The lock-acquire wrapper registered as the BYOB MCP
  `command` in `~/.claude.json`. Concretely:
  ```python
  # tools/byob/mcp_gate.py
  import os, sys, json
  from tools.byob.lock import acquire_byob_session_lock, BYOBSessionLockHeld

  def main():
      try:
          acquire_byob_session_lock(owner_id=f"mcp-gate-{os.getpid()}")
      except BYOBSessionLockHeld as e:
          err = {
              "jsonrpc": "2.0",
              "error": {
                  "code": -32000,
                  "message": (
                      f"BYOB busy: session {e.owner_id} holding lock "
                      f"(PID {e.holding_pid}); see http://localhost:8500/sessions/{e.owner_id}"
                  ),
              },
          }
          sys.stdout.write(json.dumps(err) + "\n")
          sys.exit(1)
      # Hand off to real BYOB MCP; stdio passes through unchanged.
      os.execvp(sys.argv[1], sys.argv[1:])
  ```
- **`scripts/update/mcp_byob.py::_expected_entry()` change**:
  ```python
  return {
      "type": "stdio",
      "command": sys.executable,  # the venv python
      "args": ["-m", "tools.byob.mcp_gate", str(BYOB_TSX_BIN), str(BYOB_MCP_SERVER_TS)],
      "env": {"BYOB_ALLOW_EVAL": "0"},
  }
  ```
  The drift-heal already in place keeps `BYOB_ALLOW_EVAL=0` enforced. Existing tests covering this
  function are updated (one assertion change).
- **`agent/session_pickup.py` (defense-in-depth, unchanged behavior)**: keeps the existing within-process
  `requires_real_chrome` gate. The MCP-gate handles cross-process. **Removed from cycle N+1 plan**: the
  previously-planned `acquire_byob_session_lock(agent_session.id)` inside session_pickup is no longer
  needed — every BYOB consumer goes through the MCP-gate which acquires the lock at MCP-spawn time.
- **`tools/computer/__init__.py` manifest cache + Electron retry + fail-loud tie-break**:
  - Module-level `_BCU_BASE_URL_CACHE: str | None = None`. First call reads
    `$TMPDIR/background-computer-use/runtime-manifest.json` and caches `base_url`. Cache invalidates on
    `ConnectionRefusedError` (signal that bcu restarted and the URL may have changed).
  - Each action function gains an `if bundle_id in ELECTRON_BUNDLES and selector and is_stale_ref(resp): retry_once(...)`
    branch. Retry is exactly once; second failure returns the second-attempt error to the caller.
  - **`_resolve_selector` change (cycle N+1 critique C1)**: when the role+label filter yields 2+
    candidates and the selector does NOT contain `tie_break: "nearest"`, raise
    `MultipleSelectorMatches(role, label, count, candidates_summary)` — fail-loud. Callers who want
    Euclidean tie-break opt in via `selector["tie_break"] = "nearest"`.
- **`tools/doctor.py` (cycle N+1 critique C2)**: new `check_byob_lock_freshness()` that returns WARN
  if `~/.byob/active-session.lock` exists but (a) `mtime > 30 min ago`, OR (b) the lock's `owner_id`
  has no running `AgentSession` row. Includes the holder PID and a copy-pasteable cleanup command in
  the warning message.
- **`ui/app.py` `/dashboard.json` (cycle N+1 critique C2)**: new top-level `byob_lock` block:
  `{"holder_pid": int, "holder_owner_id": str, "held_since_ts": str, "is_stale": bool}` (or
  `{"is_held": false}` when free). Surfaces the holder so the operator can see who's blocking BYOB
  without trying to use it and getting refused.
- **Smoke artifacts**:
  - `tests/manual/byob_authenticated_smoke.txt` — captured run of the agent calling `byob_navigate` to
    `https://github.com/notifications` + `byob_get_title` + `byob_screenshot` showing the user's logged-in
    notifications page. No `state.json` files in repo (verified by `git ls-files | grep state.json` →
    empty).
  - `tests/manual/byob_down_clarity_smoke.txt` — captures the BYOB-down failure mode (kill
    `byob-bridge`, retry — operator-readable error). Split out from the authenticated smoke per cycle
    N+1 nit N1.
  - `tests/manual/byob_concurrent_mcp_smoke.txt` — captures the cycle N+1 B1 reproducer: spawn
    `claude -p "use byob_get_title"` while a worker session holds the lock; assert the second
    invocation surfaces the JSON-RPC error from the MCP-gate rather than silently colliding.
  - `tests/manual/bcu_no_cursor_smoke.txt` — captured run of `valor-computer click` against Notes.app
    while the operator is actively typing in Mail.app.
  - `tests/manual/valor_computer_latency_baseline.txt` — already captured this cycle; see Spike Results.
  - `tests/manual/selector_resolver_perf_baseline.txt` — already captured this cycle; see Spike
    Results.
  - `tests/manual/bcu_electron_retry_smoke.txt` — captures the Decision 3 retry path on a real Slack
    window.
- **`.claude/skills/computer-use/SKILL.md`**: add an "Electron app rule" subsection — always pass
  `selector=` for known-Electron windows; list the Electron `bundle_id` set inline. **Add a "Gotcha —
  multiple matching elements" subsection (cycle N+1 critique C1)** describing the
  `MultipleSelectorMatches` failure mode and how to disambiguate.
- **`docs/features/byob-browser-control.md`**: new "Concurrency contract" subsection — global serial via
  the MCP-gate, lock file path + format, 30-min staleness backstop, dashboard URL for identifying
  holders.
- **`docs/features/computer-use.md`**: new "Latency budget" subsection (citing measured spike-r2
  numbers) + "Electron retry" subsection (citing the fail-loud tie-break and spike-r3 numbers).

### Flow

**BYOB session (worker path):**
1. Worker picks session with `requires_real_chrome=True`. The within-process gate at
   `agent/session_pickup.py:57-69` ensures no other worker session is running with the flag.
2. Worker spawns `claude -p "..."` to execute the session.
3. `claude` reads `~/.claude.json` and spawns the BYOB MCP child via the registered command:
   `python -m tools.byob.mcp_gate <tsx> <byob-mcp.ts>`.
4. The mcp_gate calls `acquire_byob_session_lock("mcp-gate-<pid>")`. **First spawn**: lock is free, gate
   `os.execvp`s into `tsx <byob-mcp.ts>`; stdio passes through.
5. When `claude` exits, the MCP child is reaped; the lock-holder PID dies; the next acquire reaps via
   liveness check.

**BYOB collision (any cross-process attacker):**
1. Operator (or hook, or sub-agent) runs `claude -p "use byob_get_title ..."` while the worker is
   holding the lock.
2. `claude` spawns the BYOB MCP child via the registered command: `python -m tools.byob.mcp_gate ...`.
3. The mcp_gate calls `acquire_byob_session_lock` and gets `BYOBSessionLockHeld` (PID is alive, lock
   is fresh).
4. The gate writes a JSON-RPC error to stdout: `"BYOB busy: session X holding lock (PID Y); see
   http://localhost:8500/sessions/X"` and exits 1.
5. The agent in the second `claude` invocation sees the error message and can surface it to the user.
   No silent collision.

**Computer-use call (Electron app):**
1. Skill invokes `valor-computer click <window_id> --selector '{"role":"button","label":"Send"}'`.
2. CLI calls `tools.computer.click(window_id, selector=...)`.
3. First HTTP attempt → bcu returns stale_ref.
4. Wrapper detects `bundle_id` is Electron (Slack), re-fetches `get_window_state`, runs
   `_resolve_selector`. **If exactly one match**: retries action, returns result.
5. **If 2+ matches and no `tie_break`**: raises `MultipleSelectorMatches` — caller sees an actionable
   error listing the candidates rather than a silent miss-click on the wrong element.
6. **If 2+ matches and `tie_break="nearest"`**: picks closest by Euclidean distance to original bounds,
   retries action.

### Technical Approach

- **Lock file format**: `f"{os.getpid()}\n{owner_id}\n{datetime.utcnow().isoformat()}\n"`. Atomic write
  via `tempfile.NamedTemporaryFile` + `os.rename` in `~/.byob/`. Read with `pathlib.Path.read_text` and
  parse line-by-line; tolerate trailing whitespace.
- **PID liveness check**: `os.kill(pid, 0)` — raises `ProcessLookupError` if dead, `PermissionError` if
  alive but owned by another user (treat as alive). On `ProcessLookupError`, the lock is stale; log
  warning, remove, proceed.
- **30-min staleness backstop (cycle N+1 C2)**: parse the lock file's ISO timestamp; if `(now - ts) >
  30 min` AND `os.kill(pid, 0)` succeeds (PID is alive but ancient — could be PID reuse) AND
  `AgentSession.query.filter(id == owner_id, status == "running").first() is None`, treat as
  orphaned: log warning with both PIDs and the staleness duration, remove, proceed. Real BYOB sessions
  do not legitimately hold the lock for 30+ minutes without releasing it.
- **No `fcntl` lock on the file itself**: a single file rename is atomic. Holding `flock` over the
  duration of an entire session is fragile (unhandled crash leaves the lock; we'd need a stale-flock
  cleanup step anyway). Liveness check + atomic rename + 30-min staleness backstop is simpler and
  matches the failure mode (claude exit → child reap → liveness check on next acquire reaps the lock;
  worker startup runs `cleanup_stale_locks`; `/update` runs `cleanup_stale_locks` post-worker-restart).
- **MCP-gate stdio passthrough**: `os.execvp` is the simplest correct primitive. The gate is the same
  process as `tsx <byob-mcp.ts>` after exec — Claude Code's MCP client sees one PID; stdio file
  descriptors persist across the exec; environment is inherited from the gate's process (which
  inherits from `claude`, which inherits the `BYOB_ALLOW_EVAL=0` env from `~/.claude.json`'s
  `mcpServers.byob.env`).
- **Manifest cache invalidation**: on `ConnectionRefusedError`, set `_BCU_BASE_URL_CACHE = None` and
  retry the manifest read once. If the retry also fails, raise `ComputerUseUnavailableError`.
- **Stale-ref detection**: parse the bcu response. Initial implementation: HTTP status 422 OR JSON body
  containing `{"error": "stale_ref"}`. Validated in build by capturing bcu's actual response shape
  against a real Slack window (build-phase observation; if the shape differs, fall back to
  text-match on the response message and check in the captured fixture).
- **Selector → fresh-ref resolution (cycle N+1 revision)**: walk the AX tree returned by
  `get_window_state`, match by `role` first, then `label` exact match. **Then**:
  - If exactly one candidate matches: return it.
  - If 2+ match and `selector["tie_break"] == "nearest"`: sort by Euclidean distance from selector's
    `bounds` center to candidate's center, prefer the visible one, return the first.
  - If 2+ match and no `tie_break` key: raise `MultipleSelectorMatches(role, label, count,
    candidates_summary)` — fail-loud (cycle N+1 C1).
  - If none match: return `{"error": "selector_no_match", "selector": ..., "tree_size": N}`.

### What's NOT in scope

- **Promoting `tools/computer/` to MCP server.** Decision 2 above explicitly defers this. If a real
  workflow ever measures > 500 ms median per `valor-computer` invocation, file a followup.
- **Per-process Chrome profiles or per-agent Chrome instances.** Decision 1 rejects these alternatives.
- **Editing the BYOB upstream extension.** Same as the original plan: BYOB upstream is treated as an
  immutable dep.
- **Migrating any additional skills off `agent-browser`.** That's #1274's scope, which closed with #1286.
  Future migrations are new issues.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] **Lock held by live PID**: `tools/byob/lock.py::acquire_byob_session_lock` raises
  `BYOBSessionLockHeld(holding_pid, owner_id)` when the lock file exists and the PID is alive. Test:
  pytest fixture writes a lock containing `os.getpid()`, call acquire from a child process, assert
  it raises with the right PID.
- [ ] **Lock held by dead PID**: same module silently cleans up the stale lock and proceeds. Test:
  write a lock containing PID `999999` (very unlikely to be live), call acquire, assert success and
  the lock file now contains the test's PID.
- [ ] **Lock held by stale-but-alive PID (cycle N+1 C2 PID-reuse)**: write a lock dated 31 minutes ago
  with `os.getpid()` (alive PID, mtime > 30 min, no backing AgentSession). Call acquire, assert
  success and a `WARN`-level log line containing both the holder PID and the staleness duration.
- [ ] **Concurrent `claude -p` racing the worker (cycle N+1 B1 reproducer)**: spawn the worker holding
  a BYOB session (which means the MCP-gate spawned by that worker holds the lock), then run
  `subprocess.run(["claude", "-p", "use byob_get_title"])` from a separate shell. Assert the second
  invocation's MCP child surfaces the JSON-RPC error from the gate (`"BYOB busy: session ..."`) rather
  than touching Chrome. Pytest test in `tests/integration/test_byob_mcp_gate_concurrency.py`.
- [ ] **MCP-gate exec passthrough**: with the lock free, run the gate with a fake `tsx` (an
  `argv[1]` pointing at a small Python script that prints a known marker to stdout). Assert the
  marker appears on the gate's stdout — proves `os.execvp` correctly hands off stdio.
- [ ] **Electron stale-ref retry succeeds (single match)**: integration test against a live Slack
  window — query `get_window_state`, scroll to invalidate AX, click via selector, assert second
  attempt succeeds.
- [ ] **Electron stale-ref retry fails twice**: integration test where the target element is removed
  between calls; assert the wrapper returns the second-attempt error to the caller (does not retry
  a third time).
- [ ] **Selector matches multiple elements, no `tie_break` (cycle N+1 C1)**: integration test where
  two matching elements exist (e.g. two `Send` buttons in the AX tree). Assert
  `_resolve_selector` raises `MultipleSelectorMatches` and the action wrapper surfaces it to the
  caller as `{"error": "multiple_selector_matches", "candidates": [...]}` rather than picking one.
- [ ] **Selector matches multiple elements, `tie_break='nearest'` (opt-in)**: same fixture as above
  but with `selector["tie_break"] = "nearest"`. Assert the resolver picks the closest by Euclidean
  distance and the action proceeds.
- [ ] **Non-Electron stale-ref**: assert wrapper does NOT retry (returns the first-attempt error).
- [ ] **Manifest-cache invalidation**: mock bcu to return ConnectionRefusedError on first call after a
  cached read; assert the wrapper re-reads the manifest exactly once and retries.

### Empty/Invalid Input Handling

- [ ] `tools.computer.click(window_id=1, selector={})` → `ValueError` (empty selector remains invalid;
  unchanged from shipped behavior).
- [ ] `tools.computer.click(window_id=1, selector={"role": "button"})` → resolves on label-less match
  (label is optional in selector dicts; documented in SKILL).
- [ ] `acquire_byob_session_lock(owner_id="")` → `ValueError` (empty owner_id is a programmer error,
  fail loudly).
- [ ] `acquire_byob_session_lock(owner_id="x" * 1024)` → `ValueError` (oversized owner_id, fail
  loudly — prevents lock-file abuse).

### Error State Rendering

- [ ] `BYOBSessionLockHeld` exception's `__str__` includes the holding PID, the holding owner_id, and
  the dashboard URL `http://localhost:8500/sessions/<owner_id>` so the operator can see who's holding
  it.
- [ ] `MultipleSelectorMatches` exception's `__str__` includes the role, label, count, and a summary
  of each candidate's bounds + visibility. Helps the developer see what their selector actually
  matched.
- [ ] `valor-computer` Electron-retry-failed error includes the original stale-ref response and the
  selector that didn't resolve, so debugging shows what was searched for vs. what was in the tree.
- [ ] `tools/doctor.py` BYOB-lock-stale warning includes the holder PID, the staleness duration in
  minutes, the owner_id, and a copy-pasteable cleanup command (`rm ~/.byob/active-session.lock`).

## Test Impact

- [ ] `tests/integration/test_byob_scheduler.py` — **NO CHANGE in cycle N+1 revision**: the within-process
  scheduler gate at `agent/session_pickup.py` keeps its existing behavior. The cross-process race is
  now covered by the new `test_byob_mcp_gate_concurrency.py` (below) — keeping these tests separated
  by responsibility.
- [ ] `tests/unit/test_mcp_byob_registrar.py` — **UPDATE**: `_expected_entry()` now returns
  `{"command": sys.executable, "args": ["-m", "tools.byob.mcp_gate", str(BYOB_TSX_BIN),
  str(BYOB_MCP_SERVER_TS)], ...}`. Existing assertion on `command == str(BYOB_TSX_BIN)` flips to
  the new shape; existing drift-heal tests still pass once the assertion is updated.
- [ ] `tests/unit/test_byob_skill_triggers.py` — **NO CHANGE**.
- [ ] `tools/computer/tests/test_computer_use.py` — **UPDATE**: add cases for the manifest cache
  (first call reads, second call uses cache, ConnectionRefusedError invalidates), the Electron retry
  path (mocked bcu returns stale_ref then success on second call), and **the fail-loud tie-break
  cycle N+1 critique C1**: `test_resolve_selector_raises_multiple_selector_matches` and
  `test_resolve_selector_tie_break_nearest_opts_in`.
- [ ] `tools/computer/tests/test_computer_use_integration.py` — **UPDATE**: add live Slack
  Electron-retry test, plus `test_send_button_disambiguation_in_slack` (cycle N+1 C1) — exercises
  the failure mode against a real Slack window with two thread-Send buttons in the AX tree. Both
  marked `@pytest.mark.integration`; skipped when bcu+Slack not available.
- [ ] **New: `tests/unit/test_byob_lock.py`** — covers `tools/byob/lock.py`:
  acquire/release happy path, dead-PID cleanup, live-PID refusal, **30-min staleness backstop with
  PID-reuse simulation (cycle N+1 C2)**, atomic-rename behavior under filesystem-fault simulation,
  `cleanup_stale_locks` idempotency.
- [ ] **New: `tests/unit/test_byob_mcp_gate.py`** — covers `tools/byob/mcp_gate.py`:
  - Lock-free path: gate `os.execvp`s into a fake `tsx` (a Python script printing a known marker);
    assert marker appears on stdout.
  - Lock-held path: pre-write a lock with this PID; run the gate; assert it writes the JSON-RPC error
    envelope to stdout and exits 1.
- [ ] **New: `tests/integration/test_byob_mcp_gate_concurrency.py` (cycle N+1 B1 reproducer)** —
  the test the cycle N+1 critique explicitly asked for:
  spawn process A acquiring the lock; from process B, run a `subprocess.run` that executes the
  gate with a fake tsx; assert process B's stdout contains the JSON-RPC error and exit code is 1.
  Marked `@pytest.mark.slow` because it spawns subprocesses.
- [ ] **New: `tests/unit/test_doctor_byob_lock.py`** — covers `tools/doctor.py::check_byob_lock_freshness`:
  no-lock returns OK; fresh-lock returns OK; stale-lock returns WARN with the holder PID + cleanup
  command in the message; lock with no backing AgentSession returns WARN.
- [ ] **New: dashboard `byob_lock` block tests** — extend `tests/unit/test_dashboard.py` (or the
  closest existing dashboard test) with: free state returns `{"is_held": false}`; held state returns
  `{"holder_pid": ..., "holder_owner_id": ..., "held_since_ts": ..., "is_stale": ...}`.
- [ ] **New manual smoke artifacts** (not pytest-runnable; checked into `tests/manual/`):
  - `byob_authenticated_smoke.txt` (AC-1).
  - `byob_down_clarity_smoke.txt` (AC-3, split out per cycle N+1 nit N1).
  - `byob_concurrent_mcp_smoke.txt` (cycle N+1 B1 manual reproducer).
  - `bcu_no_cursor_smoke.txt` (AC-2).
  - `bcu_electron_retry_smoke.txt` (AC-5).
  - `valor_computer_latency_baseline.txt` (AC-4) — already captured this cycle.
  - `selector_resolver_perf_baseline.txt` (cycle N+1 C3) — already captured this cycle.
- [ ] **DELETED from prior plan revision**: `tests/unit/test_valor_session_force_local.py`.
  Per cycle N+1 critique C4, the `--force-local` flag is no longer added; this test is not created.

## Rabbit Holes

- **Building a generic process-coordination service** for cross-process BYOB serialization. A single
  lock file on the user's machine is enough — we are not building a multi-machine BYOB cluster.
- **Adding `flock(2)` over the BYOB Unix socket.** The socket is rotated by BYOB itself; locking it
  races with bridge process restarts. Decision 1 explicitly rejected this.
- **Promoting `tools/computer/` to MCP now.** Deferred per Decision 2 until measured.
- **Auto-retrying on non-Electron windows.** Their AX trees don't go stale lazily; a stale ref there
  means the window state genuinely changed (modal, navigation). Auto-retry would mask real bugs.
- **Building a Chrome-extension-side multiplexer** so multiple agents could share Chrome. Out of scope —
  same DOM tree, by design.

## Risks

### Risk 1: Lock file leaks (cycle N+1 C2 — addressed with PID-reuse + 30-min backstop + observability)

**Impact:** A stale `~/.byob/active-session.lock` blocks all future BYOB MCP spawns until manually
removed. Three failure modes:
1. Process holding the lock crashed without `finally`-cleanup (the MCP-gate's lock release happens
   when the gate's PID dies; SIGKILL kills the process before any cleanup can run).
2. **PID reuse**: a stale lock containing PID 12345 — that PID is now held by an unrelated process
   (Spotlight, bash, anything). Naive `os.kill(pid, 0)` returns 0 because the PID is alive, so the
   lock looks held forever.
3. Long-running dev machine never invokes `/update`, so the `cleanup_stale_locks` step in
   `scripts/update/run.py` never fires.

**Mitigation (cycle N+1 C2):**

- **Liveness check** (`os.kill(pid, 0)`) catches case 1 — dead PIDs reap immediately on next acquire.
- **30-min staleness backstop** catches case 2: if the lock's mtime is older than 30 minutes AND the
  PID is alive AND no `AgentSession.query.filter(id == owner_id, status == "running").first()`, the
  lock is treated as orphaned regardless of `os.kill` result. Real BYOB sessions never legitimately
  hold the lock for 30+ minutes without a release. Reap with `WARN`-level log including both PIDs.
- **`tools/doctor.py::check_byob_lock_freshness`** surfaces the leak proactively: any operator
  running `python -m tools.doctor` sees a WARN line with the holder PID, staleness duration, and a
  copy-pasteable cleanup command.
- **Dashboard `byob_lock` widget at `/dashboard.json`** shows the holder + age live; the operator can
  spot a leaked lock without trying to use BYOB and getting refused.
- **`cleanup_stale_locks` runs at worker startup** (already planned) AND **at every
  `acquire_byob_session_lock` call** (the staleness check is part of the acquire path itself).
  No reliance on `/update` — case 3 above is fixed by making cleanup intrinsic to the acquire path.

### Risk 2: Manifest cache returns wrong URL after bcu restart with port change

**Impact:** All `valor-computer` calls fail until the Python process exits and a new one reads the
fresh manifest.
**Mitigation:** `ConnectionRefusedError` invalidates the cache and re-reads the manifest. macOS bcu
restarts (rare, only on update or manual stop/start) trigger this naturally. Long-lived processes
calling `tools.computer` (the worker, in particular) recover within one failed call.

### Risk 3: Electron retry masks a genuine "the button you wanted disappeared" failure (cycle N+1 C1)

**Impact:** A skill thinks it clicked Send when in fact the message UI navigated away, the AX tree
re-rendered, and a different `Send` button (e.g. in a different thread or modal) is now closest to the
original bounds. The shipped behavior at `tools/computer/__init__.py:193` silently picks that one.

**Mitigation (cycle N+1 C1):** Selector tie-break is **fail-loud by default**. When 2+ candidates
match equal `role` + `label`, `_resolve_selector` raises `MultipleSelectorMatches(role, label,
count, candidates_summary)` rather than guessing. Callers who knowingly want Euclidean tie-breaking
opt in via `selector["tie_break"] = "nearest"`. The SKILL.md "Multiple matching elements" gotcha
section documents the failure mode with a concrete example (two `Send` buttons in stacked Slack
threads). Integration test
`tools/computer/tests/test_computer_use_integration.py::test_send_button_disambiguation_in_slack`
exercises the failure mode against a real Slack window.

## Race Conditions

### Race 1: Two processes try to acquire the lock simultaneously

**Location:** `tools/byob/lock.py::acquire_byob_session_lock`
**Trigger:** Two `claude -p` invocations spawn their MCP-gate children at the same instant. Both
gates call `acquire_byob_session_lock` in parallel.
**Mitigation:** Atomic `os.rename` over a `tempfile.NamedTemporaryFile` in the same directory means
exactly one of the two writes wins. The loser re-reads the lock and sees the winner's PID — and
refuses cleanly with `BYOBSessionLockHeld`. Each gate then writes its JSON-RPC error (winner doesn't,
loser does) and exits.

### Race 2: bcu restarts between the manifest read and the HTTP call

**Location:** `tools/computer/__init__.py` — first call after a bcu restart
**Trigger:** Operator runs `pkill background-computer-use && open ~/Applications/BackgroundComputerUse.app`
between two `valor-computer` calls; the second call uses the cached URL pointing at the old port.
**Mitigation:** `ConnectionRefusedError` invalidates the cache and re-reads. One call drops; the next
recovers.

### Race 3: Electron AX tree changes between `get_window_state` re-fetch and retry click

**Location:** `tools/computer/__init__.py` — Electron retry path
**Trigger:** Slack receives a new message between the re-fetch and the retry click; the AX tree
shifts down.
**Mitigation (cycle N+1 revised):** The resolver is fail-loud on 2+ matches by default — a small
downward shift that still produces a unique role+label match resolves cleanly; a large shift that
duplicates the match (modal opens with another `Send` button) raises `MultipleSelectorMatches`
rather than guessing. Callers see an actionable error listing the candidates. If the caller wants
the historical Euclidean-distance tie-break, they pass `selector["tie_break"] = "nearest"` (opt-in
exactly like the original implementation).

### Race 4 (cycle N+1 new): Lock holder dies between `os.kill` check and atomic rename

**Location:** `tools/byob/lock.py::acquire_byob_session_lock` — the staleness-reclaim path
**Trigger:** Lock holder is alive at the moment of the `os.kill(pid, 0)` check, dies between that
check and the atomic rename, and a different process (PID reuse) takes its PID before our rename
lands.
**Mitigation:** This race is benign because of the staleness backstop. If the original holder dies
and the lock's mtime is < 30 min, our acquire returns `BYOBSessionLockHeld` (we believe the new
PID is the holder; it isn't, but the next acquire after the new PID dies — or after 30 min, if it
turns out to be a long-lived unrelated process — will reap). If the lock's mtime is > 30 min and
the lock has no backing AgentSession, the staleness backstop reaps regardless. The race window is
narrow and the failure mode degrades to "next BYOB invocation gets a fast error and tries again",
not silent corruption.

## No-Gos (Out of Scope)

- **Multi-machine BYOB coordination.** This is a single-user-machine feature; we are not building
  shared state across hosts.
- **Per-agent Chrome profiles or per-agent Chrome instances.** Defeats the "use the user's logged-in
  browser" design goal. Decision 1 rejected.
- **Promoting `valor-computer` to MCP in this cycle.** Decision 2 deferred. Will revisit if a measured
  workflow shows CLI overhead is the bottleneck.
- **Headless replacements for BYOB or bcu.** BYOB is real-Chrome by design. `agent-browser`/`bowser`
  cover the headless lane. We are not building a headless-BYOB hybrid.
- **Pushing the user's pointer or stealing focus.** bcu drives via the macOS Accessibility API
  precisely so it doesn't move the cursor. This plan reinforces that contract; it does not move
  the contract.
- **Committing cookie/state files.** As in the original plan: BYOB uses the user's real Chrome
  profile, no `state.json` artifacts touch the repo. Verified by smoke test.
- **Backwards-compatibility shims.** Per CLAUDE.md "no legacy code tolerance". Replace cleanly.
- **`BYOB_ALLOW_EVAL=1` by default.** `browser_eval` stays disabled; the registrar drift-heals it
  back to `"0"`.
- **Reopening Decisions 1–3 mid-build.** If a builder believes a decision is wrong, they raise a
  PM check-in; they do not silently change the surface.

## Update System

The shipped `/update` flow already covers BYOB pin bumps (`config/byob_pin.json`), bcu pin bumps
(`config/bcu_pin.json`), and the `mcp_byob` registrar. This refresh adds (cycle N+1 revisions in
**bold**):

1. **`mcp_byob.verify_byob_mcp` re-runs on the new `_expected_entry()` shape**. The drift-heal
   already in place will detect that any older `command: "<tsx>"` registration is wrong and
   rewrite it to `command: <python>`, `args: ["-m", "tools.byob.mcp_gate", <tsx>, <byob-mcp.ts>]`.
   No new code needed in `scripts/update/run.py` — the registrar's existing repair path covers it.
2. **Orphan-lock cleanup step** in `scripts/update/run.py`: after the worker restart, run a
   one-shot `python -c "from tools.byob.lock import cleanup_stale_locks; cleanup_stale_locks()"`
   that removes `~/.byob/active-session.lock` if its PID is dead OR if it's older than 30 minutes
   without a backing running AgentSession. **Why here**: belt-and-suspenders alongside the
   intrinsic staleness check at every acquire — operators who pull updates regularly get prompt
   cleanup; operators who don't are still covered by the acquire-time check. Failure of this step
   is non-fatal (warning, not error).
3. **No new pin files.** Decisions 1–3 are pure code changes; nothing pinned.
4. **No `/setup` changes.** Operators who already ran the existing setup are good to go; the new
   surface is additive on top.
5. **No new dependencies for `/update` to install.** All stdlib.

## Agent Integration

- **No new MCP tools surfaced to agents.** Decision 2 explicitly keeps `valor-computer` on CLI.
  `tools/byob/mcp_gate.py` is registered as the BYOB MCP `command` but it `os.execvp`s into the
  real BYOB MCP server immediately; from the agent's perspective the BYOB tool surface
  (`byob_navigate`, `byob_get_title`, `byob_screenshot`, etc.) is unchanged.
- **No new CLI entry points.** `tools/byob/lock.py` and `tools/byob/mcp_gate.py` are internal —
  the gate is invoked by Claude Code's MCP loader via `~/.claude.json`, never by an agent
  directly. `python -m tools.byob.mcp_gate` is intentionally not declared in `pyproject.toml
  [project.scripts]` because it is not a user-facing surface.
- **`valor-computer click/type_text/...` surface gains a `--selector` flag** (already declared in
  `pyproject.toml [project.scripts]` as `valor-computer = "tools.computer.cli:main"`). The flag
  takes a JSON string: `--selector '{"role": "button", "label": "Send"}'`. **`tie_break`** is
  optional inside the JSON (cycle N+1 critique C1): omit it for fail-loud behavior on 2+ matches,
  set it to `"nearest"` to opt into Euclidean tie-breaking. The `computer-use` SKILL body is
  updated to reference this in the Electron section.
- **`tools/valor_session.py` is NOT modified in cycle N+1 revision.** The previously-planned
  `--force-local` / `--bypass-worker` flag is removed (cycle N+1 critique C4): with the MCP-gate
  in place, every BYOB consumer is gated regardless of how the parent process was spawned, so a
  CLI flag for "manual BYOB sessions" is redundant. Operators who need a manual BYOB session run
  `claude -p "use byob_navigate ..."` directly — the MCP-gate handles serialization.
- **Integration tests the agent can actually invoke**:
  - `valor-computer click 12345 --selector '{"role":"button","label":"Send","bounds":[100,200,80,30]}'`
    against Slack, with the AX tree shifted between query and click — assert the click landed on
    the right element. Lives in `tools/computer/tests/test_computer_use_integration.py`.
  - `valor-computer click 12345 --selector '{"role":"button","label":"Send"}'` against a Slack
    window with two `Send` buttons in stacked threads — assert the wrapper raises
    `MultipleSelectorMatches` rather than picking one. Same test file (cycle N+1 C1 reproducer).
  - Spawn `claude -p "use byob_get_title"` while a worker BYOB session holds the lock — assert
    the second invocation surfaces the MCP-gate JSON-RPC error rather than touching Chrome
    (cycle N+1 B1 reproducer in
    `tests/integration/test_byob_mcp_gate_concurrency.py`).

## Documentation

- [ ] Update `docs/features/byob-browser-control.md` with a new "Concurrency Contract" section
  documenting:
  - The MCP-gate model (every `claude` / `claude -p` invocation passes through
    `tools/byob/mcp_gate.py` which acquires `~/.byob/active-session.lock` before exec'ing into
    `tsx <byob-mcp.ts>`).
  - The `~/.byob/active-session.lock` file format (`pid\nowner_id\niso_timestamp`).
  - The 30-min staleness backstop and PID-reuse-resilience semantics (cycle N+1 C2).
  - The `python -m tools.doctor` health check that surfaces stale locks.
  - The dashboard `byob_lock` widget at `/dashboard.json`.
  - **Removed from this section in cycle N+1**: the `--force-local` operator escape hatch is no
    longer in scope (critique C4).
- [ ] Update `docs/features/computer-use.md` with a new "Latency Budget" subsection (Decision 2 +
  measured spike-r2 numbers: median 81.6 ms / p95 86.9 ms via /bin/bash on the build machine) and
  a new "Electron Retry" subsection (Decision 3, including the explicit list of retried
  `bundle_id`s and the fail-loud tie-break semantics).
- [ ] Update `.claude/skills/computer-use/SKILL.md` with:
  - The "Always pass `selector=` for Electron apps" rule, including the canonical `bundle_id` set
    inline.
  - **A "Gotcha — multiple matching elements" subsection (cycle N+1 C1)** showing the two-Send-buttons
    failure mode, the `MultipleSelectorMatches` exception, and the `tie_break='nearest'` opt-in.
- [ ] Update `docs/features/README.md` index entries for BYOB and computer-use to reflect the
  refresh's additions (concurrency contract via MCP-gate, lock observability, Electron retry,
  fail-loud selector matching).
- [ ] Cross-link this refresh ↔ #1256 in both directions; update issue #1256 with a comment that
  Decisions 1–3 are now resolved (with the cycle N+1 revisions to Decision 1 + Decision 3
  highlighted).
- [ ] Migrate this plan to `docs/plans/done/byob_and_computer_use.md` post-merge of the refresh PR
  (per repo plan-migration policy).

## Success Criteria

**Technical (cycle N+1 revisions in **bold**):**
- [ ] `tools/byob/lock.py` exists; `acquire_byob_session_lock` / `release_byob_session_lock` /
  `cleanup_stale_locks` covered by `tests/unit/test_byob_lock.py`. **Includes the 30-min staleness
  backstop and PID-reuse handling (cycle N+1 C2).**
- [ ] **`tools/byob/mcp_gate.py` exists; lock-held + lock-free paths covered by
  `tests/unit/test_byob_mcp_gate.py`.** The gate `os.execvp`s into the real `tsx <byob-mcp.ts>` on
  the lock-free path; emits a JSON-RPC error envelope and exits 1 on the lock-held path.
- [ ] **`scripts/update/mcp_byob.py::_expected_entry()` returns the new shape with
  `command=sys.executable` and `args=["-m", "tools.byob.mcp_gate", <tsx>, <byob-mcp.ts>]`.**
  Drift-heal in place rewrites any older registration. Covered by an updated assertion in
  `tests/unit/test_mcp_byob_registrar.py`.
- [ ] **Cross-process collision reproducer**: spawn worker session holding the BYOB lock; spawn a
  separate `claude -p` invocation; assert the second's MCP child surfaces the JSON-RPC error from
  the gate. Covered by `tests/integration/test_byob_mcp_gate_concurrency.py`.
- [ ] `agent/session_pickup.py` keeps its existing within-process `requires_real_chrome` gate. **No
  new lock-write inside session_pickup** (cycle N+1 revision: the MCP-gate handles the lock; the
  scheduler stays defense-in-depth).
- [ ] **The `--force-local` flag is NOT added to `tools/valor_session.py`** (cycle N+1 critique
  C4). Verified by absence of the flag in `python -m tools.valor_session create --help`.
- [ ] `tools/computer/__init__.py` caches the manifest read across calls in the same Python
  process; `ConnectionRefusedError` invalidates the cache. Covered by
  `tools/computer/tests/test_computer_use.py`.
- [ ] `tools/computer/__init__.py` retries exactly once on stale-ref for known-Electron windows
  with a `selector=` argument; non-Electron windows do not retry. Covered by
  `tools/computer/tests/test_computer_use_integration.py`.
- [ ] **`tools/computer/__init__.py::_resolve_selector` raises `MultipleSelectorMatches` when 2+
  candidates match equal role+label and `tie_break` is not set; opts in to Euclidean tie-break
  when `tie_break="nearest"`** (cycle N+1 C1). Covered by both unit tests
  (`tools/computer/tests/test_computer_use.py`) and the live-Slack integration test
  (`test_send_button_disambiguation_in_slack`).
- [ ] **`tools/doctor.py::check_byob_lock_freshness` returns WARN when the lock is stale or its
  owner_id has no running AgentSession** (cycle N+1 C2). Covered by
  `tests/unit/test_doctor_byob_lock.py`.
- [ ] **`/dashboard.json` exposes a top-level `byob_lock` block** (cycle N+1 C2). Covered by an
  extended dashboard test.
- [ ] All shipped tests still pass (`pytest tests/unit/test_mcp_byob_registrar.py
  tests/unit/test_byob_skill_triggers.py tests/integration/test_byob_scheduler.py`).
- [ ] `pytest tests/unit/test_byob_lock.py tests/unit/test_byob_mcp_gate.py
  tests/integration/test_byob_mcp_gate_concurrency.py tests/unit/test_doctor_byob_lock.py
  tools/computer/tests/test_computer_use.py
  tools/computer/tests/test_computer_use_integration.py` passes.

**Executable user-facing acceptance criteria (proof artifacts required):**

Per memory `feedback_acceptance_criteria_must_be_executable`, every user-facing AC names a runnable
command and a captured proof artifact path.

- [ ] **AC-1 (issue body): "agent reads an authenticated page end-to-end with zero state.json files in
  the repo"**
  - Run: `valor-session create --role dev --project-key valor --needs-real-chrome --message "navigate
    to github.com/notifications, screenshot it, return the page title"`
  - Assert: `git ls-files | grep -E '(^|/)state\.json$'` returns empty.
  - Assert: the captured screenshot shows `/notifications` (logged-in view), not `/login`.
  - Proof artifact: `tests/manual/byob_authenticated_smoke.txt`.

- [ ] **AC-2 (issue body): "click in Notes.app without moving the user's mouse cursor"**
  - Setup: open Notes.app, find its window via `valor-computer list_windows --bundle-id com.apple.Notes`.
  - Operator action: open Mail.app, place cursor in compose body, start typing slowly.
  - Run (parallel): `valor-computer click <notes_window_id> --x 100 --y 100`.
  - Assert: operator's keystrokes continued landing in Mail (visual confirmation).
  - Assert: cursor position before and after the click is unchanged (visual confirmation captured by
    operator-recorded screen video, transcribed in the artifact).
  - Proof artifact: `tests/manual/bcu_no_cursor_smoke.txt`.

- [ ] **AC-3 (issue body, "BYOB-down clarity"): "BYOB MCP failure surfaces an actionable message"**
  - Setup: BYOB extension installed and bridge running.
  - Run: `pkill -f byob-bridge.ts && valor-session create --role dev --project-key valor
    --needs-real-chrome --message "navigate to about:blank"`.
  - Assert: the agent's response includes "BYOB bridge not running — start Chrome and run
    `~/.byob/start.sh`" or equivalent.
  - **Proof artifact (cycle N+1 nit N1, split out): `tests/manual/byob_down_clarity_smoke.txt`**
    (separate from the AC-1 authenticated smoke).

- [ ] **AC-4 (Decision 2 budget): `valor-computer list_apps` median across 50 runs is < 200 ms.**
  - **Captured in cycle N+1 revision pass**: median 81.6 ms / p95 86.9 ms via /bin/bash on Tom's
    MacBook Pro (Darwin 25.4.0). Well under the 200 ms threshold (>2x headroom) and the 500 ms
    flip-to-MCP-now threshold (>6x headroom).
  - **Caveat**: bcu opt-in not set on the measurement machine. Re-measurement on a build machine
    with bcu enabled is queued as a non-blocking confirmation step. If real-bcu numbers blow past
    200 ms, escalate to MCP-now in a followup issue.
  - Proof artifact: `tests/manual/valor_computer_latency_baseline.txt` (already committed).

- [ ] **AC-6 (cycle N+1 B1 reproducer): "concurrent `claude -p` BYOB call is refused with an
  actionable JSON-RPC error"**
  - Setup: a worker session is running with `requires_real_chrome=True` (lock is held by the
    MCP-gate child of that session).
  - Run: `claude -p "use byob_get_title"` from a separate shell.
  - Assert: stdout contains `"BYOB busy: session ..."` and exit code is 1; Chrome's active tab is
    untouched.
  - Proof artifact: `tests/manual/byob_concurrent_mcp_smoke.txt`.

- [ ] **AC-7 (cycle N+1 C1 reproducer): "selector matching 2+ elements raises `MultipleSelectorMatches`"**
  - Setup: a Slack window with two stacked threads, both showing a `Send` button in their
    respective compose areas.
  - Run: `valor-computer click <slack_window_id> --selector '{"role":"button","label":"Send"}'`.
  - Assert: the command exits non-zero with an error message naming both candidates and their
    bounds; Chrome/Slack state is unchanged.
  - Proof artifact: captured inline in `tools/computer/tests/test_computer_use_integration.py`'s
    test name (the test exists; this AC verifies the manual reproducer with a real Slack window).

- [ ] **AC-5 (Decision 3 Electron retry): "click into Slack via selector after AX tree shifts and
  succeed on the first wrapper call (which internally retries once)"**
  - Setup: Slack open with a channel selected.
  - Run: `valor-computer click <slack_window_id> --selector '{"role":"button","label":"Send"}'` after
    forcing an AX shift (scroll up in Slack between the call's internal `get_window_state` and the
    first action attempt — the test mocks this in CI; the manual artifact captures it on a real Slack).
  - Assert: the call returns success on first invocation (the wrapper retries internally; caller does
    not see two attempts).
  - Captured in `tools/computer/tests/test_computer_use_integration.py::test_electron_stale_ref_retry`
    + `tests/manual/bcu_electron_retry_smoke.txt`.

**Verification:**
- [ ] `python scripts/check_prerequisites.py docs/plans/byob_and_computer_use.md` passes on the
  build machine.
- [ ] All four required plan sections present and substantive: Documentation, Update System,
  Agent Integration, Test Impact (this section's hooks: `validate_documentation_section.py` +
  `validate_test_impact_section.py`).

## Team Orchestration

### Team Members (cycle N+1 revision)

- **Builder (lock + MCP-gate)**
  - Name: byob-lock-builder
  - Role: `tools/byob/__init__.py`, `tools/byob/lock.py`, `tools/byob/mcp_gate.py`,
    `scripts/update/mcp_byob.py::_expected_entry()` change (registers the gate as `command`),
    unit tests for both new modules, integration test for cross-process reproducer, registrar
    test update. **Does NOT touch `tools/valor_session.py`** (cycle N+1 critique C4).
  - Agent Type: builder
  - Resume: true

- **Builder (computer-use changes)**
  - Name: computer-use-refresh-builder
  - Role: `tools/computer/__init__.py` manifest cache + Electron retry + **fail-loud
    `MultipleSelectorMatches` (cycle N+1 C1)**, `--selector` flag in `tools/computer/cli.py`,
    unit + integration tests for all three changes (mocked unit + live-Slack integration).
  - Agent Type: builder
  - Resume: true

- **Builder (observability — cycle N+1 C2)**
  - Name: byob-observability-builder
  - Role: `tools/doctor.py::check_byob_lock_freshness`, dashboard `byob_lock` block in
    `ui/app.py`, unit tests for both. Depends on byob-lock-builder for `tools/byob/lock.py`'s API.
  - Agent Type: builder
  - Resume: true

- **Builder (docs + smoke artifacts)**
  - Name: docs-smoke-builder
  - Role: Capture remaining manual smoke artifacts (latency + resolver baselines already done in
    revision pass), update `docs/features/byob-browser-control.md`,
    `docs/features/computer-use.md`, `.claude/skills/computer-use/SKILL.md`,
    `docs/features/README.md`.
  - Agent Type: builder
  - Resume: true

### Coordination

- byob-lock-builder and computer-use-refresh-builder run in **parallel worktrees** (per memory
  `feedback_parallel_builds_need_worktrees`):
  - byob-lock-builder: `.worktrees/byob-lock-1256/`
  - computer-use-refresh-builder: `.worktrees/computer-use-refresh-1256/`
- byob-observability-builder runs **after byob-lock-builder merges** (depends on
  `tools.byob.lock`'s API), in `.worktrees/byob-observability-1256/`.
- docs-smoke-builder runs **after** all three code builders merge, in
  `.worktrees/byob-docs-smoke-1256/`.
- One PR per worktree; merge order: byob-lock → (computer-use-refresh in parallel) →
  byob-observability → docs-smoke.

## Step by Step Tasks

### Task 1: `tools/byob/{lock,mcp_gate}.py` + MCP-server entrypoint registration

**Worktree:** `.worktrees/byob-lock-1256/`

1. Create `tools/byob/__init__.py` (empty) and `tools/byob/lock.py` with the three public functions
   (`acquire_byob_session_lock`, `release_byob_session_lock`, `cleanup_stale_locks`) and the
   `BYOBSessionLockHeld` exception. **Includes 30-min staleness backstop and PID-reuse handling**
   per cycle N+1 critique C2.
2. Write `tests/unit/test_byob_lock.py` covering: happy-path acquire/release, dead-PID cleanup,
   live-PID refusal, **30-min staleness reclaim with PID-reuse simulation**, atomic-rename
   behavior under simulated failure, `cleanup_stale_locks` idempotency.
3. **Create `tools/byob/mcp_gate.py`**: lock-acquire wrapper that emits a JSON-RPC error envelope
   on `BYOBSessionLockHeld` and otherwise `os.execvp`s into `argv[1:]` (passing stdio through
   unchanged).
4. **Write `tests/unit/test_byob_mcp_gate.py`**: lock-free path execs into a fake `tsx` script
   that prints a known marker; lock-held path writes the JSON-RPC error envelope to stdout and
   exits 1.
5. **Update `scripts/update/mcp_byob.py::_expected_entry()`**: change `command` from
   `str(BYOB_TSX_BIN)` to `sys.executable`, change `args` to
   `["-m", "tools.byob.mcp_gate", str(BYOB_TSX_BIN), str(BYOB_MCP_SERVER_TS)]`. Update the
   matching assertion in `tests/unit/test_mcp_byob_registrar.py`. The drift-heal already in
   place will rewrite older registrations on the next `/update`.
6. **Write `tests/integration/test_byob_mcp_gate_concurrency.py`** (cycle N+1 B1 reproducer):
   pre-write a lock containing this PID; spawn a `subprocess.run(["python", "-m",
   "tools.byob.mcp_gate", ...])`; assert stdout contains the JSON-RPC error and exit code is 1.
7. Wire `cleanup_stale_locks` into `scripts/update/run.py` as a non-fatal post-worker-restart step
   (belt-and-suspenders alongside the intrinsic acquire-time staleness check).
8. **NOT in scope (cycle N+1 critique C4)**: `tools/valor_session.py --force-local` /
   `--bypass-worker` flag is not added. The MCP-gate covers every entry point.

### Task 2: `tools/computer/__init__.py` manifest cache + Electron retry + fail-loud tie-break

**Worktree:** `.worktrees/computer-use-refresh-1256/`

1. Add `_BCU_BASE_URL_CACHE` module-level cache to `tools/computer/__init__.py`. Update the
   manifest read to populate / read from cache. Add `ConnectionRefusedError` invalidation.
2. Add the Electron retry wrapper to each action function (`click`, `type_text`, `set_value`,
   `drag`, `perform_secondary_action`). Use `electron_bundles.py` for the bundle_id check.
3. **Update `_resolve_selector` (cycle N+1 critique C1)**: when 2+ candidates match equal
   role+label and `selector["tie_break"]` is not `"nearest"`, raise `MultipleSelectorMatches(role,
   label, count, candidates_summary)` rather than silently sorting by Euclidean distance.
   Define the exception class at module top alongside `ComputerUseUnavailableError`.
4. Add `--selector` flag to `tools/computer/cli.py` for each affected subcommand.
5. Update `tools/computer/tests/test_computer_use.py`:
   - manifest cache hit/miss tests
   - cache invalidation on ConnectionRefusedError
   - mocked Electron retry path (stale_ref → success on retry)
   - non-Electron stale_ref does NOT retry
   - empty selector → ValueError
   - **`test_resolve_selector_raises_multiple_selector_matches` (cycle N+1 C1)**: build an AX tree
     with two equal-role+label nodes; assert `_resolve_selector` raises.
   - **`test_resolve_selector_tie_break_nearest_opts_in`**: same fixture; pass
     `selector["tie_break"]="nearest"`; assert it returns the closest match.
6. Update `tools/computer/tests/test_computer_use_integration.py`:
   - live Slack stale-ref retry (skipped if Slack/bcu unavailable)
   - **`test_send_button_disambiguation_in_slack` (cycle N+1 C1)**: open two Slack threads with
     visible compose areas; assert `valor-computer click --selector '{"role":"button","label":"Send"}'`
     surfaces `MultipleSelectorMatches` rather than picking one.

### Task 3: Observability — doctor check + dashboard widget (cycle N+1 C2)

**Worktree:** `.worktrees/byob-observability-1256/` (runs after Task 1 merges)

1. Add `tools/doctor.py::check_byob_lock_freshness()`: returns OK when no lock; OK when fresh and
   backed by a running AgentSession; WARN when stale (mtime > 30 min) or missing-AgentSession.
   Warning message includes the holder PID, owner_id, staleness duration, and copy-pasteable
   `rm` cleanup command.
2. Write `tests/unit/test_doctor_byob_lock.py`: covers no-lock, fresh-lock, stale-lock,
   missing-AgentSession.
3. Add `byob_lock` block to `/dashboard.json` in `ui/app.py`. Surface fields: `is_held`,
   `holder_pid`, `holder_owner_id`, `held_since_ts`, `is_stale`. Reads
   `~/.byob/active-session.lock` lazily on each dashboard request (cheap; lock file is small).
4. Extend `tests/unit/test_dashboard.py` with the byob_lock block tests.

### Task 4: Smoke artifacts + docs

**Worktree:** `.worktrees/byob-docs-smoke-1256/` (runs after Tasks 1, 2, 3 merge).
**Depends On**: Task 1 (lock + mcp_gate), Task 2 (computer-use changes), Task 3 (observability).

1. Capture `tests/manual/byob_authenticated_smoke.txt` per AC-1.
2. **Capture `tests/manual/byob_down_clarity_smoke.txt` per AC-3** (cycle N+1 nit N1: split out
   from the authenticated smoke into its own artifact for reviewer clarity).
3. **Capture `tests/manual/byob_concurrent_mcp_smoke.txt` per AC-6** (cycle N+1 B1 manual
   reproducer): pre-spawn a worker BYOB session; from a separate shell run
   `claude -p "use byob_get_title"`; capture the JSON-RPC error and exit code.
4. Capture `tests/manual/bcu_no_cursor_smoke.txt` per AC-2.
5. Capture `tests/manual/bcu_electron_retry_smoke.txt` per AC-5.
6. **`tests/manual/valor_computer_latency_baseline.txt`** — already captured this revision pass.
   Verify it's checked in and referenced from `docs/features/computer-use.md`.
7. **`tests/manual/selector_resolver_perf_baseline.txt`** — already captured this revision pass.
   Verify it's checked in and referenced from `docs/features/computer-use.md`.
8. Update `docs/features/byob-browser-control.md`:
   - Add "Concurrency Contract" section with the MCP-gate model, the
     `~/.byob/active-session.lock` format, the 30-min staleness backstop, the doctor check, and
     the dashboard widget.
   - **Removed (cycle N+1 C4)**: the `--force-local` operator escape hatch is not documented
     because it is not added.
9. Update `docs/features/computer-use.md`:
   - Add "Latency Budget" section citing measured numbers from
     `valor_computer_latency_baseline.txt`.
   - Add "Electron Retry" section listing the bundle_ids and the once-only retry semantics.
   - **Add a "Selector matching — fail-loud" subsection (cycle N+1 C1)** with the
     `MultipleSelectorMatches` example and the `tie_break='nearest'` opt-in.
10. Update `.claude/skills/computer-use/SKILL.md`:
    - Electron `selector=` rule.
    - **"Gotcha — multiple matching elements" subsection (cycle N+1 C1)**.
11. Update `docs/features/README.md` index entries for BYOB and computer-use.
12. Post a comment on issue #1256 referencing this refresh plan and the three Decisions plus the
    cycle N+1 revisions, then close the issue at PR merge.

## Verification

- [ ] `pytest tests/unit/test_byob_lock.py tests/unit/test_byob_mcp_gate.py
  tests/integration/test_byob_mcp_gate_concurrency.py tests/unit/test_doctor_byob_lock.py
  tests/unit/test_mcp_byob_registrar.py tools/computer/tests/test_computer_use.py
  tools/computer/tests/test_computer_use_integration.py` all pass.
- [ ] `python scripts/check_prerequisites.py docs/plans/byob_and_computer_use.md` reports green.
- [ ] All five smoke artifacts present and non-empty under `tests/manual/`:
  `byob_authenticated_smoke.txt`, `byob_down_clarity_smoke.txt`,
  `byob_concurrent_mcp_smoke.txt`, `bcu_no_cursor_smoke.txt`, `bcu_electron_retry_smoke.txt`.
  Plus the two already-committed baselines: `valor_computer_latency_baseline.txt`,
  `selector_resolver_perf_baseline.txt`.
- [ ] `python -m ruff format .` and `python -m ruff check .` are clean on the changed files.
- [ ] Doc validators (`validate_documentation_section.py`, `validate_test_impact_section.py`) pass
  on this plan file.
- [ ] Issue #1256 closed with a comment summarizing the three Decisions, the cycle N+1 revisions
  (B1 lock-at-MCP-entry, B2 measured spike, C1 fail-loud tie-break, C2 lock observability, C4
  flag removal), and the smoke artifacts.

## Open Questions

None blocking. Two recorded for the build phase (downgraded from the prior cycle's "deferred to
build" framing per cycle N+1 critique B2: spike-r2 + spike-r3 ran during this revision pass):

1. **bcu's exact stale-ref response shape** (HTTP status code + JSON body) — to be observed during
   build phase 0 against a real Slack window. If the shape differs from the expectation in
   Decision 3 (HTTP 422 OR `{"error": "stale_ref"}`), the wrapper falls back to text-match on the
   response message and the captured fixture is committed in
   `tools/computer/tests/test_computer_use.py::test_stale_ref_detection_real_response`.
2. **Real-bcu confirmation of the `valor-computer list_apps` latency** — the cycle N+1 spike-r2
   measurement ran with bcu disabled (median 81.6 ms / p95 86.9 ms via /bin/bash, well under the
   200 ms threshold). The bcu HTTP loopback adds ~1–10 ms on top. Re-run the same benchmark on a
   build machine with bcu opted in to confirm the budget. If real-bcu numbers blow past 200 ms,
   file a followup issue for MCP-now evaluation; this plan still ships with the CLI surface.

---

## Revision Pass — Cycle N+1

This revision pass addressed the cycle-N critique (`docs/plans/critique/byob_and_computer_use.md`):
verdict NEEDS REVISION with 2 blockers, 4 concerns. All 6 findings are addressed below; one nit
(N1) was also addressed because it was cheap. Nit N2 (Task 3 dependency declaration) is addressed
by the new Task 4 explicit `Depends On` line.

### Blockers resolved

- **B1 — Lock-guard layer**: Decision 1 was rewritten. The lock is now acquired by
  `tools/byob/mcp_gate.py`, registered as the BYOB MCP `command` in `~/.claude.json` via
  `_expected_entry()`. Every consumer of BYOB MCP — interactive `claude`, `claude -p`, hook-spawned
  agents, dashboard scripts, sub-agents — passes through the gate. The previously-planned lock
  acquisition at `agent/session_pickup.py` and `tools/valor_session.py --force-local` is replaced by
  this single architecturally-correct site. `agent/session_pickup.py` keeps its existing within-process
  scheduler gate as defense-in-depth. New integration test
  `tests/integration/test_byob_mcp_gate_concurrency.py` is the cycle N+1 reproducer the critique
  asked for.

- **B2 — Spikes ran NOW, not deferred**: Both spike-r2 (CLI latency) and spike-r3 (selector-resolver
  perf) were measured during this revision pass and committed as artifacts at
  `tests/manual/valor_computer_latency_baseline.txt` and
  `tests/manual/selector_resolver_perf_baseline.txt`. The measured numbers are pasted into the
  Decisions section.
  - Spike-r2 result: median 81.6 ms / p95 86.9 ms via /bin/bash (the realistic skill path). >2x
    headroom on the 200 ms threshold; >6x headroom on the 500 ms flip-to-MCP-now threshold.
  - Spike-r3 result: 100-node tree resolves in 0.013 ms median; 1000-node stress in 0.15 ms. The
    bcu HTTP `get_window_state` round-trip dominates the retry path; pre-resolved selector
    caching is NOT needed.
  - Decision 2 (CLI surface) and Decision 3 (one-time retry with tree re-fetch + resolver pass)
    are confirmed with real numbers.

### Concerns resolved

- **C1 — Selector tie-break silent miss-click**: `_resolve_selector` is now fail-loud by default.
  When 2+ candidates match equal role+label and `selector["tie_break"]` is not `"nearest"`, the
  resolver raises `MultipleSelectorMatches(role, label, count, candidates_summary)`. Callers opt in
  to Euclidean tie-break by passing `selector["tie_break"]="nearest"` explicitly. The SKILL.md
  gains a "Gotcha — multiple matching elements" subsection. New integration test
  `test_send_button_disambiguation_in_slack` exercises the failure mode against a real Slack window.

- **C2 — Lock-leak observability**: Three new mitigations:
  - 30-minute staleness backstop inside `acquire_byob_session_lock` (resilient to PID reuse).
  - `tools/doctor.py::check_byob_lock_freshness` surfaces stale locks with copy-pasteable cleanup.
  - Dashboard `byob_lock` widget at `/dashboard.json` shows holder PID, owner, age, and is_stale.
  No reliance on `/update`-gated cleanup; the staleness check is intrinsic to every acquire.

- **C3 — Selector-resolver perf**: Resolved by running spike-r3 (above). Resolver is invisible
  against the bcu HTTP cost; no pre-resolved cache needed.

- **C4 — `--force-local` misnamed and redundant**: The flag is removed entirely from the plan.
  With the MCP-gate covering every entry point (B1's fix), there is no need for a CLI flag whose
  purpose is to wire a lock check around manual BYOB invocations — the lock check now lives at
  the MCP-server entrypoint regardless of how the parent process was spawned.

### Nits resolved

- **N1 — AC-3 artifact split**: `byob_down_clarity_smoke.txt` is now its own file (separate from
  `byob_authenticated_smoke.txt`) so the Test Impact section's artifact list aligns with what
  reviewers actually see.

- **N2 — Task dependency declaration**: Task 4 (formerly Task 3) now starts with an explicit
  `**Depends On**: Task 1 (lock + mcp_gate), Task 2 (computer-use changes), Task 3
  (observability)` line.

### Build readiness

The plan is now build-ready. All 2 blockers and 4 concerns from the cycle-N critique are
addressed; the architectural direction (lock-at-MCP-entry, fail-loud selector matching, intrinsic
staleness backstop, measured spike numbers) is concrete and testable. Builders can pick up Tasks
1, 2, 3, 4 in the orchestration order specified above.
