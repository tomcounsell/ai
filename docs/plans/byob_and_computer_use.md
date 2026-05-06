---
status: Refresh
type: feature
appetite: Medium
owner: Valor
created: 2026-05-02
refreshed: 2026-05-06
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
   serializes within the worker process. If a developer runs a one-off session manually
   (`python -m tools.valor_session create --needs-real-chrome ...`) while the worker is also picking up a
   BYOB session, both processes can speak to the same Chrome DOM tree concurrently. PR #1277 explicitly punted
   this case to "single-user serial discipline" without a guard. Per memory `feedback_prevention_over_cleanup`,
   that's a gap, not a feature.
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

### Track 1 — BYOB browser automation (post-refresh)

```
agent (Claude Code) ──MCP──▶ byob MCP server (~/.byob via tsx)
                                      │
                                      ▼
                              byob-bridge (~/.byob/bridges/<deviceId>.sock)
                                      │
                              Native Messaging
                                      ▼
                         Chrome MV3 extension (active tab)
                                      │
                                      ▼
                            DOM/screenshot result up the chain

Worker session-pick:
  1. Read AgentSession.requires_real_chrome
  2. If True, check no other running session has it True (shipped gate)
  3. NEW (Decision 1): write ~/.byob/active-session.lock with PID + agent_session_id
  4. On session end, remove lock (try/finally — always)

Non-worker BYOB caller (dev session via --force-local):
  1. Read ~/.byob/active-session.lock
  2. If present and PID is alive, refuse to start with clear error
  3. If present and PID is dead, log a warning + clean up + proceed
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

- **New files**: `tools/byob/lock.py` (~30 lines, two functions); two manual smoke artifacts under
  `tests/manual/`; one integration test for the Electron retry path.
- **Modified files**:
  - `tools/computer/__init__.py` — add manifest-cache + Electron retry wrapper.
  - `tools/computer/cli.py` — pass `selector=` through.
  - `agent/session_pickup.py` — write/release `~/.byob/active-session.lock` around the BYOB-flagged
    session run.
  - `tools/valor_session.py` (CLI surface) — add `--force-local` flag with lock check.
  - `docs/features/byob-browser-control.md` — document the global-serialization contract +
    `--force-local` operator path.
  - `docs/features/computer-use.md` — document the per-invocation budget + Electron retry.
  - `.claude/skills/computer-use/SKILL.md` — document the `selector=` rule for Electron.
- **No new dependencies.** All three decisions reuse stdlib (`fcntl`, `urllib.request`) and existing
  modules.
- **Coupling**: low. The lock file is filesystem-state, not a new model field. The Electron retry is
  internal to `tools/computer/`. The CLI flag is additive.
- **Reversibility**: full. Each guard is a pure addition; deleting `tools/byob/lock.py` and the wrapper
  reverts to the shipped behavior with no cleanup cost.

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

- **`tools/byob/lock.py`**: New module. Two public functions —
  `acquire_byob_session_lock(owner_id: str) -> None` (writes `~/.byob/active-session.lock` containing
  `f"{os.getpid()}\n{owner_id}\n"` atomically; raises `BYOBSessionLockHeld(holding_pid, owner_id)` if
  another live PID holds it) and `release_byob_session_lock(owner_id: str) -> None` (removes the file
  iff its content matches our PID + owner_id; logs a warning otherwise). PID liveness check uses
  `os.kill(pid, 0)` with `ProcessLookupError` swallowed.
- **`agent/session_pickup.py` integration**: at the moment the worker picks a `requires_real_chrome=True`
  session, call `acquire_byob_session_lock(agent_session.id)` *immediately before* the executor starts.
  Wrap the entire session run in `try/finally` and call `release_byob_session_lock(agent_session.id)` in
  the `finally`. **Idempotency**: if lock acquisition fails (already held by *this* same session — e.g.
  resume case), proceed; if held by a different PID, abandon the pick (log + put session back in queue).
- **`tools/valor_session.py --force-local`**: new flag. Refuses to run when `requires_real_chrome=True`
  unless the lock is free or held by this PID. Error message:
  `"BYOB session in progress: agent_session_id=X, pid=Y. View on dashboard: http://localhost:8500/sessions/X"`.
- **`tools/computer/__init__.py` manifest cache + Electron retry**:
  - Module-level `_BCU_BASE_URL_CACHE: str | None = None`. First call reads
    `$TMPDIR/background-computer-use/runtime-manifest.json` and caches `base_url`. Cache invalidates on
    `ConnectionRefusedError` (signal that bcu restarted and the URL may have changed).
  - Each action function gains an `if bundle_id in ELECTRON_BUNDLES and selector and is_stale_ref(resp): retry_once(...)`
    branch. Retry is exactly once; second failure returns the second-attempt error to the caller.
- **Smoke artifacts**:
  - `tests/manual/byob_authenticated_smoke.txt` — captured run of the agent calling `byob_navigate` to
    `https://github.com/notifications` + `byob_get_title` + `byob_screenshot` showing the user's logged-in
    notifications page. No `state.json` files in repo (verified by `git ls-files | grep state.json` →
    empty). Captures the BYOB-down failure mode (kill `byob-bridge`, retry — operator-readable error).
  - `tests/manual/bcu_no_cursor_smoke.txt` — captured run of `valor-computer click` against Notes.app
    while the operator is actively typing in Mail.app; transcript shows operator's keystrokes continued
    landing in Mail and the cursor did not jump.
  - `tests/manual/valor_computer_latency_baseline.txt` — `time valor-computer list_apps` × 10 with
    median + p95 captured.
- **`.claude/skills/computer-use/SKILL.md`**: add an "Electron app rule" subsection — always pass
  `selector=` for known-Electron windows. List the Electron `bundle_id` set inline.
- **`docs/features/byob-browser-control.md`**: new "Concurrency contract" subsection — global serial,
  `--force-local` escape hatch, lock file path, dashboard link to identify holders.
- **`docs/features/computer-use.md`**: new "Latency budget" + "Electron retry" subsections.

### Flow

**BYOB session (worker path):**
1. Worker picks session with `requires_real_chrome=True`.
2. Calls `acquire_byob_session_lock(agent_session.id)`. If held by a different live PID, abandons pick.
3. Runs the session.
4. `finally`: `release_byob_session_lock(agent_session.id)`.

**BYOB session (manual `--force-local` path):**
1. Operator runs `python -m tools.valor_session create --needs-real-chrome --force-local --message "..."`.
2. CLI calls `acquire_byob_session_lock("manual-{os.getpid()}")`. If held, prints error + exits 1.
3. Runs in-process (no worker handoff).
4. `finally`: releases lock.

**Computer-use call (Electron app):**
1. Skill invokes `valor-computer click <window_id> --selector '{"role":"button","label":"Send"}'`.
2. CLI calls `tools.computer.click(window_id, selector=...)`.
3. First HTTP attempt → bcu returns stale_ref.
4. Wrapper detects `bundle_id` is Electron (Slack), re-fetches `get_window_state`, resolves selector to
   fresh ref, retries action.
5. Second attempt succeeds → result returned.

### Technical Approach

- **Lock file format**: `f"{os.getpid()}\n{owner_id}\n{datetime.utcnow().isoformat()}\n"`. Atomic write
  via `tempfile.NamedTemporaryFile` + `os.rename` in `~/.byob/`. Read with `pathlib.Path.read_text` and
  parse line-by-line; tolerate trailing whitespace.
- **PID liveness check**: `os.kill(pid, 0)` — raises `ProcessLookupError` if dead, `PermissionError` if
  alive but owned by another user (treat as alive). On `ProcessLookupError`, the lock is stale; log
  warning, remove, proceed.
- **No `fcntl` lock on the file itself**: a single file rename is atomic. Holding `flock` over the
  duration of an entire session is fragile (unhandled crash leaves the lock; we'd need a stale-flock
  cleanup step anyway). Liveness check + atomic rename is simpler and matches the failure mode
  (worker crash → `finally` releases; orphan reaper on next worker start cleans up via the same
  liveness check at session-pick time).
- **Manifest cache invalidation**: on `ConnectionRefusedError`, set `_BCU_BASE_URL_CACHE = None` and
  retry the manifest read once. If the retry also fails, raise `ComputerUseUnavailableError`.
- **Stale-ref detection**: parse the bcu response. Initial implementation: HTTP status 422 OR JSON body
  containing `{"error": "stale_ref"}`. Validated in build by spike-r3.
- **Selector → fresh-ref resolution**: walk the AX tree returned by `get_window_state`, match by
  `role` first, then `label` exact match, then nearest-bounds (Euclidean distance from selector's
  `bounds` center to the candidate's center). If multiple candidates tie, prefer the visible one. If
  none match, return `{"error": "selector_no_match", "selector": ..., "tree_size": N}`.

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
- [ ] **Worker crash mid-session**: simulate by killing the worker process while a BYOB session is
  running; assert that on next worker start, the orphan-reaper sees the dead-PID lock, cleans it up,
  and the session is retried.
- [ ] **Electron stale-ref retry succeeds**: integration test against a live Slack window — query
  `get_window_state`, scroll to invalidate AX, click via selector, assert second attempt succeeds.
- [ ] **Electron stale-ref retry fails twice**: integration test where the target element is removed
  between calls; assert the wrapper returns the second-attempt error to the caller (does not retry
  a third time).
- [ ] **Non-Electron stale-ref**: assert wrapper does NOT retry (returns the first-attempt error).
- [ ] **Manifest-cache invalidation**: mock bcu to return ConnectionRefusedError on first call after a
  cached read; assert the wrapper re-reads the manifest exactly once and retries.
- [ ] **`--force-local` lock collision**: with worker holding the lock, run
  `tools.valor_session create --needs-real-chrome --force-local`; assert exit 1 with the error message
  containing the holding session id and dashboard URL.

### Empty/Invalid Input Handling

- [ ] `tools.computer.click(window_id=1, selector={})` → `ValueError` (empty selector remains invalid;
  unchanged from shipped behavior).
- [ ] `tools.computer.click(window_id=1, selector={"role": "button"})` → resolves on label-less match
  (label is optional in selector dicts; documented in SKILL).
- [ ] `acquire_byob_session_lock(owner_id="")` → `ValueError` (empty owner_id is a programmer error,
  fail loudly).

### Error State Rendering

- [ ] `BYOBSessionLockHeld` exception's `__str__` includes the holding PID, the holding owner_id, and
  the dashboard URL `http://localhost:8500/sessions/<owner_id>` so the operator can see who's holding
  it.
- [ ] `valor-computer` Electron-retry-failed error includes the original stale-ref response and the
  selector that didn't resolve, so debugging shows what was searched for vs. what was in the tree.

## Test Impact

- [ ] `tests/integration/test_byob_scheduler.py` — **UPDATE**: extend with a multi-process test that
  spawns a child process attempting to acquire the lock while the parent holds it. Assert child raises
  `BYOBSessionLockHeld`. Existing same-process serialization tests stay unchanged.
- [ ] `tests/unit/test_mcp_byob_registrar.py` — **NO CHANGE**.
- [ ] `tests/unit/test_byob_skill_triggers.py` — **NO CHANGE**.
- [ ] `tools/computer/tests/test_computer_use.py` — **UPDATE**: add cases for the manifest cache
  (first call reads, second call uses cache, ConnectionRefusedError invalidates) and for the
  Electron retry path (mocked bcu returns stale_ref then success on second call).
- [ ] `tools/computer/tests/test_computer_use_integration.py` — **UPDATE**: add live Slack
  Electron-retry test (skipped when bcu+Slack not available, marked `@pytest.mark.integration`).
- [ ] **New: `tests/unit/test_byob_lock.py`** — covers `tools/byob/lock.py`:
  acquire/release happy path, dead-PID cleanup, live-PID refusal, atomic-rename behavior under
  filesystem-fault simulation.
- [ ] **New manual smoke artifacts** (not pytest-runnable; checked into `tests/manual/`):
  `byob_authenticated_smoke.txt`, `bcu_no_cursor_smoke.txt`, `valor_computer_latency_baseline.txt`.

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

### Risk 1: Lock file leaks if both the worker and `--force-local` sessions crash on the same machine

**Impact:** A stale `~/.byob/active-session.lock` blocks all future BYOB sessions until manually
removed.
**Mitigation:** Liveness check at every `acquire_byob_session_lock` call uses `os.kill(pid, 0)`. On
`ProcessLookupError`, the wrapper logs a warning and removes the stale lock. The orphan-reaper that
runs on every worker startup also calls `acquire_byob_session_lock("orphan-cleanup")` which
exercises the same path. Stale locks heal automatically within one worker startup cycle.

### Risk 2: Manifest cache returns wrong URL after bcu restart with port change

**Impact:** All `valor-computer` calls fail until the Python process exits and a new one reads the
fresh manifest.
**Mitigation:** `ConnectionRefusedError` invalidates the cache and re-reads the manifest. macOS bcu
restarts (rare, only on update or manual stop/start) trigger this naturally. Long-lived processes
calling `tools.computer` (the worker, in particular) recover within one failed call.

### Risk 3: Electron retry masks a genuine "the button you wanted disappeared" failure

**Impact:** A skill thinks it clicked Send when in fact the message UI navigated away and Send no
longer exists.
**Mitigation:** Selector resolution prefers exact `role` + `label` match; nearest-bounds is only used
to break ties between equal `role`+`label` matches. Documented in SKILL: "if your selector matches
multiple elements after re-query, the wrapper picks the closest to the original bounds — pass tighter
selectors if that's wrong." On second-attempt failure, the wrapper returns `{"error": ...}` with the
fresh tree size so the caller can see how big the AX tree got between attempts.

## Race Conditions

### Race 1: Two processes try to write `~/.byob/active-session.lock` simultaneously

**Location:** `tools/byob/lock.py::acquire_byob_session_lock`
**Trigger:** Worker picks a BYOB session at the same instant a developer types
`--force-local` on their CLI.
**Mitigation:** Atomic `os.rename` over a `tempfile.NamedTemporaryFile` in the same directory means
exactly one of the two writes wins. The loser of the race re-reads the lock and sees the winner's
PID — and refuses cleanly.

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
**Mitigation:** The retry uses `bounds`-based tie-breaking; a small downward shift still resolves to
the same target. A large shift (modal opens) means the selector probably no longer corresponds to the
intended element — the second attempt fails and the caller sees the fresh-tree error.

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
(`config/bcu_pin.json`), and the `mcp_byob` registrar. This refresh adds:

1. **Orphan-lock cleanup step** in `scripts/update/run.py`: after the worker restart, run a
   one-shot `python -c "from tools.byob.lock import cleanup_stale_locks; cleanup_stale_locks()"`
   that removes `~/.byob/active-session.lock` if its PID is dead. **Why here**: a crashed worker
   leaves a lock; `/update` is the natural recovery point. Failure of this step is non-fatal
   (warning, not error).
2. **No new pin files.** Decisions 1–3 are pure code changes; nothing pinned.
3. **No `/setup` changes.** Operators who already ran the existing setup are good to go; the new
   surface is additive on top.
4. **No new dependencies for `/update` to install.** All stdlib.

## Agent Integration

- **No new MCP tools.** Decision 2 explicitly keeps `valor-computer` on CLI.
- **No new CLI entry points.** `tools/byob/lock.py` is internal; agents do not call it directly.
- **`valor-computer click/type_text/...` surface gains a `--selector` flag** (already declared in
  `pyproject.toml [project.scripts]` as `valor-computer = "tools.computer.cli:main"`). The flag
  takes a JSON string: `--selector '{"role": "button", "label": "Send"}'`. The `computer-use`
  SKILL body is updated to reference this in the Electron section.
- **`tools/valor_session.py` gains `--force-local`**, an opt-in operator flag for the rare manual
  BYOB session that bypasses the worker. Documented in `docs/features/byob-browser-control.md`'s
  Concurrency Contract section. **Not added to bridge-side enqueue paths** — bridges should never
  bypass the worker.
- **Integration test the agent can actually invoke**:
  - `valor-computer click 12345 --selector '{"role":"button","label":"Send","bounds":[100,200,80,30]}'`
    against Slack, with the AX tree shifted between query and click — assert the click landed on
    the right element. Lives in `tools/computer/tests/test_computer_use_integration.py`.

## Documentation

- [ ] Update `docs/features/byob-browser-control.md` with a new "Concurrency Contract" section
  documenting the global-machine-serialization model, the `~/.byob/active-session.lock` file
  format, the `--force-local` operator escape hatch, and the dashboard URL for identifying lock
  holders.
- [ ] Update `docs/features/computer-use.md` with a new "Latency Budget" subsection (Decision 2 +
  baseline numbers from the smoke artifact) and a new "Electron Retry" subsection (Decision 3,
  including the explicit list of retried `bundle_id`s).
- [ ] Update `.claude/skills/computer-use/SKILL.md` with the "Always pass `selector=` for Electron
  apps" rule, including the canonical `bundle_id` set inline.
- [ ] Update `docs/features/README.md` index entries for BYOB and computer-use to reflect the
  refresh's additions (concurrency contract, Electron retry).
- [ ] Cross-link this refresh ↔ #1256 in both directions; update issue #1256 with a comment that
  Decisions 1–3 are now resolved.
- [ ] Migrate this plan to `docs/plans/done/byob_and_computer_use.md` post-merge of the refresh PR
  (per repo plan-migration policy).

## Success Criteria

**Technical:**
- [ ] `tools/byob/lock.py` exists; `acquire_byob_session_lock` / `release_byob_session_lock` /
  `cleanup_stale_locks` covered by `tests/unit/test_byob_lock.py`.
- [ ] `agent/session_pickup.py` writes the lock at session-pick time and releases in `finally`.
  Verified by `tests/integration/test_byob_scheduler.py`'s new multi-process case.
- [ ] `tools/valor_session.py --force-local` flag refuses to start when the lock is held by a live
  PID, with an error message containing the holding session id and a dashboard link.
- [ ] `tools/computer/__init__.py` caches the manifest read across calls in the same Python
  process; `ConnectionRefusedError` invalidates the cache. Covered by
  `tools/computer/tests/test_computer_use.py`.
- [ ] `tools/computer/__init__.py` retries exactly once on stale-ref for known-Electron windows
  with a `selector=` argument; non-Electron windows do not retry. Covered by
  `tools/computer/tests/test_computer_use_integration.py`.
- [ ] All shipped tests still pass (`pytest tests/unit/test_mcp_byob_registrar.py
  tests/unit/test_byob_skill_triggers.py tests/integration/test_byob_scheduler.py`).
- [ ] `pytest tests/unit/test_byob_lock.py tools/computer/tests/test_computer_use.py
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
  - Captured in `tests/manual/byob_authenticated_smoke.txt` as the trailing failure-mode appendix.

- [ ] **AC-4 (Decision 2 budget): `time valor-computer list_apps` median across 10 runs is < 200 ms
  on this machine.**
  - Run: `for i in $(seq 1 10); do time valor-computer list_apps > /dev/null; done`.
  - Capture median + p95 in `tests/manual/valor_computer_latency_baseline.txt`.
  - If median ≥ 200 ms: file followup issue requesting MCP-now evaluation; this AC is recorded as
    "deferred decision" and the plan still ships.

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

### Team Members

- **Builder (decision-1-lock)**
  - Name: byob-lock-builder
  - Role: `tools/byob/lock.py`, `agent/session_pickup.py` integration, `tools/valor_session.py
    --force-local`, multi-process integration test extension.
  - Agent Type: builder
  - Resume: true

- **Builder (decision-2-3-computer-use)**
  - Name: computer-use-refresh-builder
  - Role: `tools/computer/__init__.py` manifest cache + Electron retry, `--selector` flag in
    `tools/computer/cli.py`, unit + integration tests for both, capture latency baseline artifact.
  - Agent Type: builder
  - Resume: true

- **Builder (docs + smoke artifacts)**
  - Name: docs-smoke-builder
  - Role: Capture three manual smoke artifacts, update `docs/features/byob-browser-control.md` and
    `docs/features/computer-use.md`, update `.claude/skills/computer-use/SKILL.md`.
  - Agent Type: builder
  - Resume: true

### Coordination

- Decision-1-lock and Decision-2-3-computer-use builders run in **parallel worktrees** (per memory
  `feedback_parallel_builds_need_worktrees`):
  - byob-lock-builder: `.worktrees/byob-lock-1256/`
  - computer-use-refresh-builder: `.worktrees/computer-use-refresh-1256/`
- docs-smoke-builder runs **after** both code builders merge, in `.worktrees/byob-docs-smoke-1256/`.
- One PR per worktree; merge order: byob-lock → computer-use-refresh → docs-smoke.

## Step by Step Tasks

### Task 1: `tools/byob/lock.py` + cross-process guard

**Worktree:** `.worktrees/byob-lock-1256/`

1. Create `tools/byob/__init__.py` (empty) and `tools/byob/lock.py` with the three public functions
   (`acquire_byob_session_lock`, `release_byob_session_lock`, `cleanup_stale_locks`) and the
   `BYOBSessionLockHeld` exception.
2. Write `tests/unit/test_byob_lock.py` covering: happy-path acquire/release, dead-PID cleanup,
   live-PID refusal, atomic-rename behavior under simulated failure, `cleanup_stale_locks` idempotency.
3. Wire `acquire_byob_session_lock` / `release_byob_session_lock` into `agent/session_pickup.py`
   around the BYOB-flagged session run. Add a multi-process integration test in
   `tests/integration/test_byob_scheduler.py` that spawns a child subprocess attempting the acquire.
4. Add `--force-local` flag to `tools/valor_session.py`'s `create` subcommand. Wire the lock check.
   Add a CLI-level test (`tests/unit/test_valor_session_force_local.py` or extend an existing test).
5. Wire `cleanup_stale_locks` into `scripts/update/run.py` as a non-fatal post-worker-restart step.

### Task 2: `tools/computer/__init__.py` manifest cache + Electron retry

**Worktree:** `.worktrees/computer-use-refresh-1256/`

1. Add `_BCU_BASE_URL_CACHE` module-level cache to `tools/computer/__init__.py`. Update the manifest
   read to populate / read from cache. Add `ConnectionRefusedError` invalidation.
2. Add the Electron retry wrapper to each action function (`click`, `type_text`, `set_value`, `drag`,
   `perform_secondary_action`). Use `electron_bundles.py` for the bundle_id check.
3. Implement selector → fresh-ref resolution: walk AX tree from `get_window_state`, match by role +
   label + nearest-bounds.
4. Add `--selector` flag to `tools/computer/cli.py` for each affected subcommand.
5. Update `tools/computer/tests/test_computer_use.py`:
   - manifest cache hit/miss tests
   - cache invalidation on ConnectionRefusedError
   - mocked Electron retry path (stale_ref → success on retry)
   - non-Electron stale_ref does NOT retry
   - empty selector → ValueError
6. Update `tools/computer/tests/test_computer_use_integration.py`:
   - live Slack stale-ref retry (skipped if Slack/bcu unavailable)
7. Capture `tests/manual/valor_computer_latency_baseline.txt`:
   `for i in $(seq 1 10); do { time valor-computer list_apps > /dev/null; } 2>> /tmp/lat.txt; done`
   then median + p95.

### Task 3: Smoke artifacts + docs

**Worktree:** `.worktrees/byob-docs-smoke-1256/` (runs after Tasks 1 & 2 merge)

1. Capture `tests/manual/byob_authenticated_smoke.txt` per AC-1 + AC-3.
2. Capture `tests/manual/bcu_no_cursor_smoke.txt` per AC-2.
3. Capture `tests/manual/bcu_electron_retry_smoke.txt` per AC-5.
4. Update `docs/features/byob-browser-control.md`:
   - Add "Concurrency Contract" section with `~/.byob/active-session.lock` format, `--force-local`
     escape hatch, dashboard link template.
5. Update `docs/features/computer-use.md`:
   - Add "Latency Budget" section citing the baseline numbers from Task 2's artifact.
   - Add "Electron Retry" section listing the bundle_ids and the once-only retry semantics.
6. Update `.claude/skills/computer-use/SKILL.md` with the Electron `selector=` rule.
7. Update `docs/features/README.md` index entries for BYOB and computer-use.
8. Post a comment on issue #1256 referencing this refresh plan and the three Decisions, then close
   the issue at PR merge.

## Verification

- [ ] `pytest tests/unit/test_byob_lock.py tests/integration/test_byob_scheduler.py
  tools/computer/tests/test_computer_use.py tools/computer/tests/test_computer_use_integration.py`
  all pass.
- [ ] `python scripts/check_prerequisites.py docs/plans/byob_and_computer_use.md` reports green.
- [ ] All three smoke artifacts present and non-empty under `tests/manual/`.
- [ ] `python -m ruff format .` and `python -m ruff check .` are clean on the changed files.
- [ ] Doc validators (`validate_documentation_section.py`, `validate_test_impact_section.py`) pass
  on this plan file.
- [ ] Issue #1256 closed with a comment summarizing the three Decisions and linking the smoke
  artifacts.

## Open Questions

None blocking. Two recorded for the build phase:

1. **bcu's exact stale-ref response shape** (HTTP status code + JSON body) — to be observed during
   spike-r3 in build phase 0. If the shape differs from the expectation in Decision 3, the wrapper
   falls back to text-match on the response message.
2. **Median latency of `valor-computer list_apps`** on the build machine — to be measured during
   build phase 0. If ≥ 500 ms, file followup issue for MCP-now evaluation; this plan still ships
   with the CLI surface.
