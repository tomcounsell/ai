# Plan Critique: BYOB Real-Chrome Control + macOS Computer Use — Refresh Plan

**Plan**: `docs/plans/byob_and_computer_use.md`
**Issue**: #1256 (OPEN)
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor
**Findings**: 8 total (2 blockers, 4 concerns, 2 nits)

---

## Blockers

### B1. Lock guard does not catch the actual cross-process attack surface

- **Severity**: BLOCKER
- **Critics**: Adversary, Skeptic, Operator (3 critics agree → elevated)
- **Location**: Decision 1 (lines 193-225); Solution > Key Elements (lines 439-452); spike-r1 (lines 291-303)
- **Finding**: BYOB MCP is registered in user-scope `~/.claude.json` (`scripts/update/mcp_byob.py:39 → CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"`). Every `claude` / `claude -p` invocation on this machine — interactive Claude Code session, hook-spawned `claude -p`, manual debug shell, dashboard maintenance script — auto-spawns the BYOB MCP child via stdio. The plan's mitigation acquires the lock at exactly two points: `agent/session_pickup.py` (worker session-pick) and `tools/valor_session.py --force-local`. Neither covers the most likely real failure mode: a developer (or hook, or sub-agent) running `claude -p "navigate to github.com/notifications"` directly without going through `valor-session create` at all. That process bypasses both lock-acquisition sites and races the worker-held Chrome tab silently. The plan's spike-r1 even names this gap ("a Python process that uses BYOB MCP for ad-hoc work without going through the session machinery") but Decision 1 only addresses the `valor-session create --force-local` slice of it.
- **Suggestion**: Move lock acquisition into the BYOB MCP server startup itself — either (a) have `scripts/update/mcp_byob.py` register a wrapper command that probes the lock before invoking `tsx byob-mcp.ts` and exits non-zero with a clear message if held by another live PID, or (b) wrap the BYOB MCP server entrypoint with a small Python shim under `tools/byob/` that does the same lock check before relaying stdio to the real BYOB server. Either approach catches every consumer of BYOB MCP regardless of how the parent process was spawned. The `valor-session --force-local` flag becomes redundant (or kept as defense-in-depth, which is fine).
- **Implementation Note**: The lock check belongs at the MCP-server invocation layer, not the session-creation layer. Concretely: change `_expected_entry()` in `scripts/update/mcp_byob.py:82-88` so `command` is a Python wrapper (e.g. `python -m tools.byob.mcp_gate`) that takes `tsx`/`byob-mcp.ts` as args. The wrapper calls `acquire_byob_session_lock(owner_id=f"mcp-gate-{os.getpid()}")` first; on `BYOBSessionLockHeld`, write a JSON-RPC error response that Claude Code surfaces to the agent ("BYOB busy: session X holding lock") and exit 1; on success, `os.execvp` into the real `tsx` invocation so stdio passes through unchanged. The lock is then released by `os.kill(pid, 0)` failing on the wrapper PID when the agent's claude session ends. Add a reproducer test: spawn the worker holding a BYOB session, then `subprocess.run(["claude", "-p", "byob_get_title"])` from a separate shell, assert the second invocation fails fast with the lock-held error message rather than touching Chrome.

### B2. spike-r2 measurement is gated AFTER plan lock-in, not before

- **Severity**: BLOCKER
- **Critics**: Skeptic, Archaeologist (memory `feedback_no_speculation`)
- **Location**: Decision 2 (lines 226-258); spike-r2 (lines 305-313); Open Questions item 2 (line 919-920); AC-4 (lines 780-786)
- **Finding**: Decision 2 ("keep CLI, don't promote to MCP") is recorded as the working decision in this plan, but it is justified by an *unmeasured* assumption ("dominant cost is Python startup + manifest read (~80–120 ms in profiling on this machine)"). spike-r2 says "Will run `time valor-computer list_apps` × 10 in build phase 0". That is post-plan-lock. AC-4 then says: "If median ≥ 200 ms: file followup issue requesting MCP-now evaluation; this AC is recorded as 'deferred decision' and the plan still ships." This means the plan ships even if the very assumption it rests on is falsified. Per memory `feedback_no_speculation` ("don't offer 'guess and patch mid-flight' as an option"), the measurement that decides between two architecturally-different surfaces (CLI vs MCP) must run before the plan is committed, not after. The 500 ms threshold mentioned in plan-maker's flagged concerns is even worse — at 500 ms median, the plan ships with a known-bad latency contract.
- **Suggestion**: Either (a) measure now, before this plan is committed to build, by running spike-r2 as a pre-commit step and recording the actual numbers in the Decisions section; OR (b) explicitly downgrade Decision 2 to "Provisional pending spike-r2; if median ≥ 200 ms, do NOT proceed to build until MCP-vs-CLI is re-decided." Option (a) is preferable. The plan owner (Valor) has bcu opt-in available; spike-r2 is a 2-minute terminal exercise.
- **Implementation Note**: Run `for i in $(seq 1 10); do { time valor-computer list_apps > /dev/null; } 2>> /tmp/lat.txt; done` on the build machine before plan freeze. Compute median + p95 from `/tmp/lat.txt`. Edit Decision 2 to cite the actual numbers (e.g. "Median 95 ms / p95 140 ms — well under the 200 ms threshold; CLI surface confirmed."). If median ≥ 200 ms, explicitly re-open the MCP-vs-CLI decision in this plan rather than punting to a followup issue. The measurement does not require changing any code — it only requires running an existing CLI ten times and writing the result into `tests/manual/valor_computer_latency_baseline.txt`. Block plan freeze until the artifact exists with non-empty median.

---

## Concerns

### C1. Selector tie-breaker can silently pick the wrong element with no detectable error

- **Severity**: CONCERN
- **Critics**: Adversary, User
- **Location**: Decision 3 step 2 (lines 270-275); Risks > Risk 3 (lines 622-629); Race Conditions > Race 3 (lines 651-657); Solution > Technical Approach (lines 514-517)
- **Finding**: Decision 3's selector resolver matches on `role` + `label` first, then breaks ties on Euclidean distance to the original `bounds` center. If the AX tree shifts (Slack receives a message, a modal renders, scroll position changes), a *different* element matching the same `role` + `label` may now be closer to the original coordinates than the intended target. The wrapper happily clicks it and returns success. Risk 3 describes this as "Selector resolution prefers exact role + label match; nearest-bounds is only used to break ties between equal role+label matches" — but in modern UI frameworks (especially Slack/Discord), `role=AXButton, label=Send` is exactly the kind of selector that has many duplicates (every channel's send button, every thread's send button). The plan tells callers to "pass tighter selectors" but provides no enforcement and no detection. The SKILL.md Electron section even encourages the loose form: `--selector '{"role":"AXButton","label":"Send","bundle_id":"com.tinyspeck.slackmacgap"}'`.
- **Suggestion**: Make tie-breaking *fail-loud by default*: if the selector resolver finds 2+ candidates with equal `role` + `label` matches, raise `MultipleSelectorMatches(candidates=[...])` instead of silently picking the closest. Callers who genuinely want nearest-bounds tie-breaking pass an explicit `--allow-tie-break` flag. Add an explicit "Gotcha" section to `.claude/skills/computer-use/SKILL.md` explaining this. Add an integration test where two equal-`role`+`label` elements exist and the selector matches both, asserting the wrapper raises `MultipleSelectorMatches` rather than picking one.
- **Implementation Note**: In `tools/computer/__init__.py::_resolve_selector` (line 193), after `target_role` / `target_label` filter, if `len(candidates) > 1` and the `selector` does not contain an explicit `tie_break: "nearest"` key, raise `MultipleSelectorMatches(role, label, len(candidates), candidates_summary)` instead of silently sorting by Euclidean distance. Update the SKILL.md Electron section: add "Gotcha — if multiple buttons match (e.g. two `Send` buttons), the wrapper raises `MultipleSelectorMatches` rather than guessing. Add a discriminator (`parent_role`, `index`, or tighter `bounds`) or pass `tie_break='nearest'` explicitly." Add `tools/computer/tests/test_computer_use.py::test_multiple_selector_matches_raises` and `tools/computer/tests/test_computer_use_integration.py::test_send_button_disambiguation_in_slack`.

### C2. Lock-leak failure mode silently breaks BYOB on every machine until manual cleanup

- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Risks > Risk 1 (lines 604-611); Update System (lines 686-689); Solution > Technical Approach (lines 502-509)
- **Finding**: The plan claims liveness-check (`os.kill(pid, 0)`) self-heals stale locks on every `acquire_byob_session_lock` call. This is true for the same machine. But three failure modes are unaddressed: (1) **PID reuse** — on a long-running machine, a stale lock containing PID 12345 may now be held by an unrelated process (`Spotlight`, `bash`, anything). `os.kill(12345, 0)` returns `0` because the PID is alive, so the lock is treated as held forever. (2) **The stale-lock cleanup runs in `scripts/update/run.py`** as a "non-fatal post-worker-restart step" (line 689). On a developer machine that doesn't run `/update` regularly (most of them), the lock leaks indefinitely. (3) **No observability** — there's no log line / dashboard widget / health check that surfaces "BYOB lock has been held for >24 hours." The operator has no way to discover the leak short of trying to use BYOB and getting refused.
- **Suggestion**: Add a timestamp + heartbeat to the lock file (the plan already mentions `datetime.utcnow().isoformat()` in the format at line 499). Treat any lock older than N minutes (e.g. 30) as stale and reap regardless of `os.kill` result, since real BYOB sessions don't legitimately last that long without producing output. Add a dashboard widget to `localhost:8500/dashboard.json` that surfaces the current BYOB lock holder + age. Add an entry to `python -m tools.doctor` output that warns when the lock is held but no `requires_real_chrome=True` AgentSession is running.
- **Implementation Note**: In `tools/byob/lock.py::acquire_byob_session_lock`, parse the timestamp line from the existing lock file. If `(now - lock_ts) > timedelta(minutes=30)` AND `os.kill(pid, 0)` succeeds but no AgentSession with `id == owner_id and status == "running"` exists, treat as orphaned (log warning with both PIDs, remove, proceed). Update `tools/doctor.py` with a check function `check_byob_lock_freshness()` that returns WARN if the lock is held without a backing session. Add `byob_lock` keys to the dashboard JSON in `ui/app.py` (path: `/dashboard.json`) — `holder_pid`, `holder_session_id`, `held_since_ts`, `is_stale`. Update Risk 1 to acknowledge PID reuse explicitly and document the 30-min staleness backstop.

### C3. spike-r3 stale-ref response shape is unmeasured but Decision 3 ships a parser for it

- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: spike-r3 (lines 315-322); Decision 3 step 2 (lines 270-275); Open Questions item 1 (line 916-918)
- **Finding**: Decision 3 commits to detecting bcu's stale-ref via "HTTP 422 OR JSON body containing `{"error": "stale_ref"}`" and the plan says spike-r3's response shape is "to be observed during build phase 0." If bcu's actual stale-ref shape is different (e.g. HTTP 500 with a Swift exception, or a textual error body without a structured `error` key), the Electron retry wrapper *never fires* — every stale-ref bubbles back to the caller as a generic error. The retry feature is silently inert. The plan's fallback ("falls back to text-match on the response message") is hand-waved; no test verifies the fallback either.
- **Suggestion**: Run spike-r3 before plan lock-in, same argument as B2. The verification is one terminal exercise: open Slack, run `valor-computer get_window_state`, scroll Slack to invalidate AX, run `valor-computer click ... --ref <stale ref>`, capture the bcu response shape, paste into Decision 3. Removes the "we'll figure it out at build time" failure mode for the second of two architecturally-load-bearing assumptions in this plan.
- **Implementation Note**: Capture bcu's stale-ref response by running on the build machine: (a) `valor-computer list_windows --bundle-id com.tinyspeck.slackmacgap` to get a Slack window_id, (b) `valor-computer get_window_state <id>` and copy a returned ref, (c) scroll Slack manually so the ref goes stale, (d) `valor-computer click <id> --ref <stale ref>` and capture stderr+stdout. Paste the actual response into Decision 3 step 2 (replacing "exact shape verified in build via test_computer_use_integration.py"). If the shape is not a clean HTTP code or `{"error": "stale_ref"}`, write the parser inline (regex on response body) and add a unit test with the captured fixture text in `tools/computer/tests/test_computer_use.py::test_stale_ref_detection_real_response`. Block plan freeze until the captured fixture exists.

### C4. `--force-local` design is misnamed and creates a UX trap

- **Severity**: CONCERN
- **Critics**: User, Simplifier
- **Location**: Decision 1 step 4 (lines 211-215); Solution > Key Elements (lines 449-452); Solution > Flow (lines 483-487); Agent Integration (lines 703-706)
- **Finding**: `tools/valor_session.py --force-local` reads as "force the session to run on this local machine." It actually means "skip the worker queue, run in-process." That's a different concept entirely. Worse, it's only meaningful when paired with `--needs-real-chrome`; otherwise the flag is silently ignored. And critically (per B1) the flag *doesn't* actually prevent the original race — a developer who reads the docs and types `claude -p "byob_navigate ..."` directly bypasses the flag entirely. So the operator gets a flag whose name is misleading, whose scope is narrow, and whose protection is illusory.
- **Suggestion**: Rename to `--bypass-worker` (accurate to what it does) and document that it is the *only* sanctioned way to invoke BYOB outside the worker (with the lock check actually living in the BYOB MCP server entrypoint per B1's fix, not in this CLI flag). If B1's MCP-server-side guard is implemented, this flag becomes pure ergonomics for the manual case and its existence is justified.
- **Implementation Note**: In `tools/valor_session.py` create_parser, rename `--force-local` to `--bypass-worker`. Update the help string: "Run the session in-process instead of enqueueing to the worker. Required for manual BYOB sessions when the worker is busy. Implies --needs-real-chrome." Wire the implication: if `--bypass-worker` is set, set `args.needs_real_chrome = True` automatically. Update `docs/features/byob-browser-control.md` Concurrency Contract section accordingly. Update the error message at lines 451-452 to point at the MCP-gate's lock holder, not just at this CLI's check.

---

## Nits

### N1. Plan claims "AC-3 captured in `byob_authenticated_smoke.txt` as the trailing failure-mode appendix"

- **Severity**: NIT
- **Critics**: Consistency Auditor
- **Location**: AC-3 (lines 772-778); Test Impact (lines 587-588) lists three smoke artifacts but does not call out that one of them carries two ACs.
- **Finding**: Bundling AC-1 (authenticated read) and AC-3 (BYOB-down clarity) into one file works but the Test Impact section doesn't flag it. A reviewer skimming the artifact list won't realize the same file proves both criteria.
- **Suggestion**: Split into two files (`byob_authenticated_smoke.txt`, `byob_down_clarity_smoke.txt`) OR add a one-line note to Test Impact explaining the dual coverage.

### N2. Task 3 dependency on Tasks 1+2 merge is implicit, not declared

- **Severity**: NIT
- **Critics**: Consistency Auditor
- **Location**: Team Orchestration > Coordination (lines 832-837); Step by Step Tasks > Task 3 header (line 882)
- **Finding**: Coordination says "docs-smoke-builder runs after both code builders merge" but the Task 3 body does not declare `Depends On: Task 1, Task 2`. A reader looking at tasks in isolation won't know.
- **Suggestion**: Add `**Depends On**: Task 1, Task 2 (both PRs merged)` as the first line under "### Task 3: Smoke artifacts + docs".

---

## Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1, 2, 3 sequential, no gaps |
| Dependencies valid | PASS (with N2 nit) | Task 3 depends on Task 1+2 implicitly via Coordination section; not declared in task body |
| File paths exist | PASS | 20 of 20 referenced existing files verified on disk; 4 paths (tools/byob/lock.py, tools/byob/__init__.py, tests/unit/test_byob_lock.py, tests/unit/test_valor_session_force_local.py) are new — intentional per plan |
| Prerequisites met | PARTIAL | `~/.byob/` installed; bcu opt-in is FALSE on this machine (operator-required, documented as "out of build scope") |
| Cross-references | PASS | No-Gos consistent with Solution; Success Criteria each map to ≥1 task; Rabbit Holes do not appear in tasks |
| Shipped/In-Progress/Remaining audit | PASS | Verified PR #1277 (commit ce44e1e4) merged 2026-05-05; PR #1286 (commit 1c50ded6) merged 2026-05-05; #1274 closed by #1286; #1256 still OPEN. All claimed-shipped items checked on disk via grep. |
| Plan-maker's flagged concerns | CONFIRMED | All 3 confirmed against source: #1 → B2; #2 → B1; #3 → C1 |

---

## Verdict

**NEEDS REVISION** — 2 blockers must be resolved before build.

**Required actions before this plan can move to build:**

1. **B1 (lock-at-MCP-server-entry)** — move the lock acquisition out of `agent/session_pickup.py` / `tools/valor_session.py --force-local` and into the BYOB MCP server invocation path so every consumer of BYOB MCP is gated, regardless of how the parent process was spawned. The current design only protects two of N entry points.
2. **B2 (measure spike-r2 before plan-lock)** — run the latency baseline now, paste the actual numbers into Decision 2, and either confirm CLI surface based on real measurement or re-open the MCP-vs-CLI decision. Same argument applies to spike-r3 (C3) for stale-ref response shape — both spikes are load-bearing and currently deferred to "build phase 0", which is too late.

**Concerns to resolve via revision pass before build:**

- C1 (selector tie-breaker silent failure) — make tie-break fail-loud by default, add SKILL gotcha section, add integration test for the "two Send buttons in Slack" case.
- C2 (lock-leak observability) — add 30-min staleness backstop, dashboard widget, doctor check.
- C3 (spike-r3 unmeasured before plan lock) — fold into B2 (measure both spikes before freeze).
- C4 (`--force-local` UX) — rename and align with B1's MCP-side guard.

**Positive findings (no rework needed):**

- Plan has a thorough Shipped/In-Progress/Remaining audit (rare and high-quality — all 14 shipped items independently verified).
- Three Decisions are well-argued with rejected alternatives explicitly listed.
- Test Impact section is concrete with explicit dispositions (UPDATE / NO CHANGE / NEW).
- Acceptance criteria are framed as executable proof artifacts per memory `feedback_acceptance_criteria_must_be_executable`.
- Race Conditions, Risks, and No-Gos sections are present and non-trivial.
- Worktree allocation per parallel build is explicitly named per memory `feedback_parallel_builds_need_worktrees`.

The plan is structurally sound; the BLOCKERs are about tightening two architectural decisions before lock-in, not about reworking the approach.
