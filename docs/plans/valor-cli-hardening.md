---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-11
tracking: https://github.com/tomcounsell/ai/issues/1619
last_comment_id: none
---

# valor CLI Hardening

## Problem

The `valor` wrapper CLI (positional-prompt wrapper over `valor-session`, shipped on `session/granite-pty-production-cutover` in PR #1612) works, but two defects undercut it in daily use — and a third claimed defect turned out to be already fixed (see Freshness Check):

1. **A stale shell alias shadows the venv binary.** `~/.zshrc:16` contains `alias valor="cd /Users/valorengels/src/ai && ./scripts/telegram_run.sh"` — a script deleted long ago. Typing `valor` in an interactive shell errors instead of running `.venv/bin/valor`. The headline feature is broken at the front door on any machine carrying the stale alias.
2. **The worker pre-flight check fires false negatives.** `valor-session create` calls `_check_worker_health()` which reads the mtime of `data/last_worker_connected`. Observed live on 2026-06-10: CLI printed `WARNING: no active worker detected — session will stay pending` while the dashboard simultaneously reported `worker: ok, last_seen: 112s`. The session ran fine. A false "no worker" warning is worse for agent callers than humans — an agent may take corrective action (restart worker, re-enqueue duplicate) that causes real churn.

**Current behavior:** bare `valor` fails in interactive shells; the pre-flight warning is untrustworthy (claims the session won't run when it will).

**Desired outcome:** bare `valor` works in a fresh interactive zsh on every machine (or `/update` warns loudly when it can't); the pre-flight warning appears only when the worker is genuinely down, and never claims a session won't run when it will.

## Freshness Check

**Baseline commit:** main `d04a7c98`; target-branch baseline `session/granite-pty-production-cutover` @ `6e8de6d8`
**Issue filed at:** 2026-06-11T02:23:39Z
**Disposition:** Minor drift (one of three parts already done; remaining two unchanged)

**File:line references re-verified:**
- `tools/valor_session.py:109` (`_check_worker_health`) — still holds on main and branch; reads `_WORKER_HEARTBEAT_FILE.stat().st_mtime` against threshold
- `agent/constants.py:38` (`HEARTBEAT_STALENESS_THRESHOLD_S = 360`) — still holds
- `~/.zshrc:16` stale alias — confirmed present on this machine; `scripts/telegram_run.sh` confirmed absent from the repo

**Cited sibling issues/PRs re-checked:**
- PR #1612 — still OPEN (`session/granite-pty-production-cutover` not merged to main)
- #1331 (pgrep case-sensitivity false negative) — closed; prior art only
- #1620 — open; owns wrapper shortcomings 4/5/6 (tagged in No-Gos below)

**Commits on the target branch since issue context was gathered:**
- `6e8de6d8` (2026-06-10 15:57 +0700, ~10h BEFORE the issue was filed) — **already fixes Part 3**: added `tests/unit/test_valor_cli.py` (302 lines, 23 tests) covering shortcut rewrite, allowlist/parser parity, per-subcommand namespace translation, and an AST-based attr-contract guard. Spike-1 below confirms all 23 pass. The same commit added a `.venv/bin/valor --help` check to `scripts/update/verify.py::check_valor_tools` — but NOT the alias-shadow check this issue requires.

**Active plans in `docs/plans/` overlapping this area:** none found (`grep -r valor_cli docs/plans/` empty apart from this plan)

**Notes:** Issue Part 3 ("zero wrapper tests") was stale at filing time. This plan reduces Part 3 to verification + residual-gap closure. Parts 1 and 2 are unchanged and confirmed real.

## Prior Art

- **#980**: "valor_session create silently enqueues on machines with no worker running" — the issue that ADDED the pre-flight warning. The warning exists for a real reason (sessions silently rotting in `pending`); the fix must preserve genuine-down detection, not delete the check.
- **#1098**: "worker health-check poll window too short — false 'system degraded' on every /update" — prior art for exactly this failure class: a health check whose window didn't account for write cadence. Fixed by widening the poll window.
- **#1331**: "Worker watchdog kills healthy worker: pgrep case-sensitivity" — prior art for a worker-health false negative whose blast radius was an automated corrective action (watchdog kill). Same lesson: false negatives in health checks trigger harmful automation.
- **PR #1612** (open): shipped the wrapper, feature doc, tests, and the venv-binary verify check this plan builds on.

## Research

No relevant external findings — the work is purely internal (shell config, file-mtime heartbeat semantics, argparse wrapper tests). Proceeding with codebase context.

## Spike Results

### spike-1: Do the branch's wrapper tests actually pass?
- **Assumption**: "`tests/unit/test_valor_cli.py` on the cutover branch satisfies the issue's test acceptance criteria"
- **Method**: prototype (temporary worktree at the branch tip, single-file serial pytest run, worktree removed after)
- **Finding**: 23 passed in 61s. Coverage maps 1:1 onto the acceptance criteria: `test_known_subcommands_matches_parser` asserts `KNOWN_SUBCOMMANDS ==` the parser-derived set (allowlist drift fails CI); `TestPositionalShortcut` covers the rewrite incl. flags-first and subcommand-name-never-prompt; `TestNamespaceTranslation` has one case per subcommand; `TestUnderlyingAttrContract` AST-walks `tools/valor_session.py` to catch new `args.<attr>` reads the wrapper doesn't provide.
- **Confidence**: high
- **Impact on plan**: Part 3 becomes verify-only. No new test file is written for the wrapper.

### spike-2: Why did the CLI warn while the dashboard said ok? (code-read)
- **Assumption**: "the heartbeat file is written only on worker connect, making mtime the wrong signal"
- **Method**: code-read
- **Finding**: Assumption FALSE. The worker writes the file every health-loop iteration: `_write_worker_heartbeat()` (`agent/session_health.py:2037`) called from `_agent_session_health_loop` every `AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300`s (`agent/session_health.py:190`). Two real defects found instead:
  - **(a) Worktree path divergence** — `_WORKER_HEARTBEAT_FILE = _repo_root / "data" / "last_worker_connected"` where `_repo_root = Path(__file__).parent.parent` (`tools/valor_session.py:104`). Invoked from a worktree checkout (`.worktrees/{slug}/`, `.claude/worktrees/*`), the CLI reads the worktree's own `data/` dir, which the worker never touches → file missing → `(False, None)` → warning, while the dashboard process (main checkout) reads the real file. This exactly reproduces the 2026-06-10 observation (warning + dashboard 112s simultaneously; 112 < 360 rules out a threshold miss on the same file).
  - **(b) Thin cadence margin** — write cadence 300s vs threshold 360s leaves 60s of margin that health-check duration can eat through. The dashboard (`ui/app.py:341`) grades 360–600s as "running" rather than error; the CLI's binary healthy/unhealthy threshold does not.
- **Confidence**: high (mechanism (a) proven by path arithmetic; (b) by constants)
- **Impact on plan**: fix is (1) worktree-proof path resolution, (2) tiered warning semantics matching the dashboard, (3) warning text that never claims the session won't run.

## Data Flow

1. **Entry point**: operator or agent runs `valor "prompt"` / `valor-session create` from an arbitrary cwd (main checkout, worktree, or elsewhere)
2. **`tools/valor_cli.py`**: argv rewrite → namespace translation → delegates to `tools.valor_session.cmd_create`
3. **`tools/valor_session.py::cmd_create`**: enqueues the AgentSession (Popoto/Redis), then calls `_check_worker_health()` → reads heartbeat-file mtime → prints warning to stderr if unhealthy
4. **Worker** (`agent/session_health.py`): independently writes the heartbeat file every 300s into ITS checkout's `data/` dir
5. **Output**: created-session confirmation on stdout (+ `worker_healthy` in `--json` mode); warning on stderr

The defect is at step 3: the reader resolves a different `data/` path than the writer when invoked from a worktree, and applies stricter staleness semantics than the dashboard.

## Architectural Impact

- **New dependencies**: none
- **Interface changes**: `_check_worker_health()` return contract extended from `(healthy: bool, age_s: int | None)` to a three-state result (ok / stale / down) or equivalent; `--json` output keeps `worker_healthy` key for backward compatibility
- **Coupling**: none added — the CLI keeps reading a file, no new dependency on the dashboard HTTP endpoint (rejected, see Rabbit Holes)
- **Data ownership**: unchanged — worker owns the heartbeat file; CLI is read-only
- **Reversibility**: trivial (small, local diffs)

## Appetite

**Size:** Small

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (sequencing decision on PR #1612, see Open Questions)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. (The wrapper-specific verification tasks are conditioned on PR #1612 merge state at build time; see Step by Step Tasks.)

## Solution

### Key Elements

- **Alias removal (this machine)**: delete the stale `valor` alias from `~/.zshrc` so the venv binary resolves
- **Verify-step alias-shadow check**: `/update` warns when interactive-shell `valor` resolves to anything other than `{project_dir}/.venv/bin/valor`, with a copy-paste fix in the warning
- **Worktree-proof heartbeat path**: `_check_worker_health()` resolves the heartbeat file against the main checkout (git common dir), not `__file__`
- **Tiered, honest warning**: CLI mirrors the dashboard's ok / stale / down tiers; stale text says "session will start when the worker picks it up", down text says "no worker heartbeat on this machine" — neither claims a created session won't run
- **Wrapper-test verification**: confirm the branch's 23 tests are green where the wrapper lives (no new wrapper test file)

### Flow

`valor "prompt"` in fresh zsh → venv binary resolves (no alias error) → session created → heartbeat read from main-checkout `data/` → (ok: silent | stale 360–600s: informational note | down >600s or missing: actionable warning) → operator/agent trusts the output.

### Technical Approach

**Part 1 — alias (machine config + update system)**
- One-time on this machine: remove `~/.zshrc:16`. The vault-synced `~/Desktop/Valor/zshenv.sh` (managed by `scripts/update/zshenv_sync.py`) is deliberately NOT used to repoint the alias — the venv binary already lands on PATH per machine; an alias adds a second source of truth.
- Add `check_valor_alias_shadow()` to `scripts/update/verify.py` following the existing `ToolCheck` pattern (cf. `check_python_alias()` at verify.py:134, which already solves the same problem-shape for `python`). Implementation: run `zsh -ic 'whence -p valor; alias valor'` with a hard timeout (interactive shells can hang on slow rc files); pass when resolution is `{project_dir}/.venv/bin/valor` and no alias exists; warn otherwise with the exact `unalias`/rc-edit instruction. Non-fatal (warn, don't block) — machines are heterogeneous.
- Wire the check into `check_valor_tools()` or `verify_environment()` so `/update` surfaces it (gated to interactive-capable environments; skip cleanly when `zsh -ic` itself fails for environmental reasons).

**Part 2 — pre-flight trustworthiness (`tools/valor_session.py`)**
- Path fix: resolve the heartbeat file via the git common directory so worktrees converge on the main checkout: `git -C <repo_root> rev-parse --git-common-dir` → `common_dir.parent / "data" / "last_worker_connected"`. Wrap in the same never-raise discipline; on any git failure fall back to the current `__file__`-relative path. Keep it lazy/cheap (one subprocess call, only at check time — not import time).
- Tier fix: replace the boolean with three states mirroring `ui/app.py::_get_worker_health`: `ok` (< 360s), `stale` (360–600s), `down` (> 600s or missing). Spike-2(b) showed the 300s write cadence leaves only 60s margin at the current threshold — the `stale` tier absorbs that.
- Message fix (both `cmd_create` warning sites, `tools/valor_session.py:465` and `:703`):
  - `stale`: `worker heartbeat is {age}s old — session created; it will start when the worker picks it up`
  - `down`: `no recent worker heartbeat on this machine ({age or 'no file'}) — session will stay pending until a worker is started (run: ./scripts/valor-service.sh worker-start)`
  - `--json` mode: keep `worker_healthy` (true only for `ok`) and add `worker_heartbeat_age_s` + `worker_state` for agent callers.
- `_check_worker_health()` stays exception-silent end to end (the #980 contract).

**Part 3 — wrapper tests (verify-only)**
- If PR #1612 has merged by build time: run `pytest tests/unit/test_valor_cli.py -n0` on the build branch and confirm 23 green; update the feature doc's shortcoming items (see Documentation).
- If not merged: nothing to build — the file exists and passes on the branch (spike-1). Record the verification in the PR body and leave doc updates conditioned as described in Step by Step Tasks.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_check_worker_health()` swallows all exceptions by design (#980). Existing `test_does_not_raise_on_permission_error` covers it; extend with a case where the git-common-dir subprocess fails (returns non-zero / binary missing) asserting fallback to the `__file__`-relative path and a `down`/`None` result rather than a raise
- [ ] New verify-step check: `zsh` missing, `zsh -ic` timeout, and non-zero exit must each produce a warn-level `ToolCheck` (never crash `/update`) — one test per failure mode with the subprocess mocked

### Empty/Invalid Input Handling
- [ ] Heartbeat file exists but is empty or has future mtime: assert the check still returns a well-formed state (no negative-age weirdness leaking into messages)
- [ ] `whence -p valor` returning empty output (binary not found anywhere): verify the alias-shadow check reports "not on PATH" rather than passing vacuously

### Error State Rendering
- [ ] Assert the `stale` message contains "will start" and does NOT contain "no active worker" (the harmful claim this issue exists to kill)
- [ ] Assert the `down` message names the start command; assert `--json` carries `worker_state` so agent callers branch on structured data, not prose

## Test Impact

- [ ] `tests/unit/test_worker_health_check.py::TestCheckWorkerHealth` (5 tests) — UPDATE: `test_healthy_worker`, `test_stale_worker`, `test_missing_heartbeat_file`, `test_exact_threshold_boundary` assert against the boolean contract; rewrite for the three-state contract (and add a 360–600s `stale`-tier boundary case + git-common-dir resolution cases using a real `tmp_path` worktree layout)
- [ ] `tests/unit/test_valor_cli.py` (branch-only) — no change; verify-only per spike-1
- [ ] No other existing tests reference `_check_worker_health` or `check_valor_tools` (grep-verified at plan time)

## Rabbit Holes

- **Dashboard HTTP fallback** (issue candidate (b)): falling back to `GET localhost:8500/dashboard.json` couples the CLI to the UI server being up, adds a network call to a hot path, and the dashboard reads the same file anyway — fixing the path + tiers makes the fallback redundant. Skip.
- **Auto-editing `~/.zshrc` from `/update`**: tempting "proactive maintenance", but programmatically rewriting user rc files from an update script is invasive and hard to make idempotent across heterogeneous machines. Warn with a copy-paste fix instead. (The one-time removal on THIS machine is done by hand in this plan, not by the script.)
- **Deriving `KNOWN_SUBCOMMANDS` from the parser at runtime**: explicitly #1620's scope, not this plan's.
- **Touching the worker's write cadence** (`AGENT_SESSION_HEALTH_CHECK_INTERVAL`): shortening it to widen the margin affects the whole health loop and the watchdog's 600s threshold math. The read-side tier fix achieves the same outcome with zero blast radius.
- **Granite PTY substrate interactions**: PR #1612's larger content (PTY pool, bridge adapter) is entirely out of scope; only the three named files matter here.

## Risks

### Risk 1: PR #1612 doesn't merge before this work builds
**Impact:** Wrapper-specific tasks (feature-doc shortcoming updates, on-main test verification) have no target files on main; acceptance criteria 1, 4, 5 of the issue can only be verified against the branch.
**Mitigation:** Build order puts main-safe parts (1, 2) first; wrapper-conditional tasks check merge state at build time and either execute (merged) or record branch-side verification evidence in the PR body (not merged). The issue itself anticipated this ("targets the cutover branch if still open, otherwise main").

### Risk 2: `zsh -ic` is slow or hangs in the update environment
**Impact:** `/update` verify step stalls on machines with heavy rc files or no zsh.
**Mitigation:** hard subprocess timeout (10s, matching existing `run_cmd` usage), warn-and-continue on timeout, skip cleanly when zsh is absent.

### Risk 3: git-common-dir resolution surprises in unusual layouts
**Impact:** Wrong path on bare repos, submodules, or non-git installs → false `down`.
**Mitigation:** strict fallback chain — git resolution failure of ANY kind falls back to the current `__file__`-relative behavior, which is never worse than today. Unit tests cover the fallback.

### Risk 4: agents parse the old warning string
**Impact:** Any automation grepping for "no active worker detected" silently stops matching.
**Mitigation:** repo-wide grep for the literal string at build time; `--json` gains structured `worker_state` as the supported contract for agents going forward.

## Race Conditions

### Race 1: heartbeat write vs. CLI read
**Location:** `agent/session_health.py:2037` (writer) vs `tools/valor_session.py:109` (reader)
**Trigger:** CLI reads between the worker's 300s-cadence writes, or mid-replace
**Data prerequisite:** none — writer uses `tmp` + `os.replace` (atomic), so the reader never sees a partial file
**State prerequisite:** the `stale` tier (360–600s) must fully cover the worst-case write gap (300s cadence + health-check duration) — it does, with 300s of slack
**Mitigation:** atomic replace already in place; tier widths absorb cadence jitter. No locking needed; the check is advisory only.

No other concurrency concerns — the CLI path is synchronous and read-only.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1620] Wrapper shortcomings 4, 5, 6 (help after positional shortcut, PTY-boundary reframe, #1288 operator path) plus deriving the allowlist from subparsers — already filed as issue #1620
- [ORDERED] Merging PR #1612 itself — human-gated merge of the granite-pty production cutover; this plan reads its state but never advances it
- [EXTERNAL] Removing the stale alias on OTHER machines' `~/.zshrc` — requires running on each machine; the `/update` verify warning (shipped here) is the mechanism that reaches them on their next update cycle

## Update System

This plan CHANGES the update system: `scripts/update/verify.py` gains the alias-shadow check, surfaced through the existing `/update` verify output. No new dependencies, no new config files, no migration steps — the check is self-contained and warn-only. Machines pick it up on their next `/update` run; the warning text carries the per-machine fix instruction.

## Agent Integration

No new agent integration required — `valor` and `valor-session` are already CLI entry points in `pyproject.toml [project.scripts]` reachable via the agent's Bash tool. This plan improves the trustworthiness of their output for agent callers: the `--json` create/status payloads gain `worker_state` and `worker_heartbeat_age_s` so agents branch on structured fields instead of stderr prose. No `.mcp.json` or bridge changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/valor-cli-wrapper.md` — shortcoming item 1 (alias): fixed status + the verify-step guard; item 3 (pre-flight): correct the misdiagnosis ("stale Redis cache" → file-mtime path divergence + tier semantics) and document the new three-state behavior; item 7/9 (tests): already-accurate, confirm wording. **Conditional:** this file exists only on the PR #1612 branch — execute when merged; otherwise record the needed edits in the implementation PR body and link from #1620
- [ ] Update `docs/features/session-steering.md` (or the doc section covering `valor-session create`) with the new warning tiers and `--json` fields
- [ ] `docs/features/README.md` index — no new entry needed (existing pages updated in place)

### Inline Documentation
- [ ] Docstring on the reworked `_check_worker_health()` documenting the three states, the git-common-dir resolution, and the #980 never-raise contract
- [ ] Comment on the verify-step check pointing at this issue for the heterogeneous-machines rationale

## Success Criteria

- [ ] `valor "test prompt" --json` runs from a fresh interactive zsh on this machine with no alias error
- [ ] `/update` verify warns when interactive `valor` resolves to anything other than the venv binary (covered by unit tests for pass/shadowed/missing/timeout cases)
- [ ] Creating a session within 360s of a worker restart — and from a worktree cwd — produces no "no active worker" claim; stale-tier text says the session will start
- [ ] `--json` create output carries `worker_state` and `worker_heartbeat_age_s`
- [ ] `tests/unit/test_worker_health_check.py` rewritten for the three-state contract, including worktree-path and git-fallback cases — green
- [ ] Wrapper tests: 23/23 green where the wrapper lives (branch or main per merge state); no new wrapper test file written
- [ ] Repo grep shows no remaining emitter of the literal "no active worker detected" string
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

- **Builder (preflight-and-verify)**
  - Name: preflight-builder
  - Role: Parts 1+2 — verify.py check, `_check_worker_health` rework, warning tiers, alias removal on this machine
  - Agent Type: builder
  - Resume: true

- **Validator (preflight-and-verify)**
  - Name: preflight-validator
  - Role: Verify success criteria, run targeted tests, check the fresh-shell behavior
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: cli-documentarian
  - Role: Documentation section tasks (conditional wrapper-doc updates + session-steering doc)
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Rework `_check_worker_health` (Part 2)
- **Task ID**: build-preflight
- **Depends On**: none
- **Validates**: tests/unit/test_worker_health_check.py (rewrite)
- **Informed By**: spike-2 (worktree path divergence is the prime mechanism; 300s cadence vs 360s threshold)
- **Assigned To**: preflight-builder
- **Agent Type**: builder
- **Parallel**: true
- Resolve heartbeat path via `git rev-parse --git-common-dir` with `__file__`-relative fallback; never raise
- Replace boolean with three-state (ok / stale / down) mirroring `ui/app.py` tiers (360s / 600s)
- Update both warning sites (`cmd_create`, `cmd_status`) with tiered messages; extend `--json` with `worker_state`, `worker_heartbeat_age_s`; keep `worker_healthy`
- Grep repo for "no active worker detected" consumers; update any found
- Rewrite `tests/unit/test_worker_health_check.py` per Test Impact

### 2. Verify-step alias-shadow check (Part 1)
- **Task ID**: build-alias-check
- **Depends On**: none
- **Validates**: tests/unit/test_update_valor_alias.py (create)
- **Informed By**: recon (verify.py:134 `check_python_alias` is the existing pattern; `check_valor_tools` gained the venv-binary check in 6e8de6d8)
- **Assigned To**: preflight-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `check_valor_alias_shadow(project_dir)` to `scripts/update/verify.py` (zsh -ic, 10s timeout, warn-only ToolCheck, copy-paste fix in the message)
- Wire into the verify flow next to `check_valor_tools`
- Unit tests: pass / alias-shadowed / not-on-PATH / zsh-missing / timeout (subprocess mocked)
- Remove the stale alias from `~/.zshrc` on this machine and confirm `zsh -ic 'whence -p valor'` resolves to the venv binary

### 3. Validate Parts 1+2
- **Task ID**: validate-preflight
- **Depends On**: build-preflight, build-alias-check
- **Assigned To**: preflight-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the two test files serially (`-n0`); confirm green
- From a worktree cwd, run `valor-session create --role teammate --message "smoke" --json` against a fresh heartbeat and assert `worker_state: ok` and no warning; clean up the test session per Manual Testing Hygiene (recognizable project key, Popoto-only deletion)
- Confirm fresh-shell `valor --help` works (acceptance criterion 1)

### 4. Wrapper verification + conditional docs (Part 3)
- **Task ID**: verify-wrapper
- **Depends On**: validate-preflight
- **Informed By**: spike-1 (23/23 green on branch @ 6e8de6d8)
- **Assigned To**: preflight-validator
- **Agent Type**: validator
- **Parallel**: false
- Check `gh pr view 1612 --json state` — if MERGED: run `pytest tests/unit/test_valor_cli.py -n0`, confirm 23 green on the build branch
- If still OPEN: record spike-1's branch-side verification (commit `6e8de6d8`, 23 passed) in the implementation PR body; flag the conditional doc tasks for #1620 linkage

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: verify-wrapper
- **Assigned To**: cli-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section (wrapper doc conditional on #1612 merge state; session-steering doc unconditional)
- Inline docs per the Documentation section

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: preflight-validator
- **Agent Type**: validator
- **Parallel**: false
- Run Verification table commands
- Verify all Success Criteria including documentation

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Health-check tests | `pytest tests/unit/test_worker_health_check.py -n0 -q` | exit code 0 |
| Alias-check tests | `pytest tests/unit/test_update_valor_alias.py -n0 -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/valor_session.py scripts/update/verify.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/valor_session.py scripts/update/verify.py` | exit code 0 |
| Harmful claim gone | `grep -rn "no active worker detected" tools/ agent/ scripts/` | exit code 1 |
| JSON contract | `grep -n "worker_state" tools/valor_session.py` | output contains worker_state |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Sequencing vs PR #1612**: this plan's build targets a `session/valor-cli-hardening` branch off main (Parts 1+2 are main-safe; `tools/valor_session.py` is identical on both branches so merge conflicts are unlikely). Wrapper-doc updates execute only if #1612 merges first. Acceptable, or should this plan block until #1612 merges so everything lands in one pass?
2. **Warning tier thresholds**: the plan mirrors the dashboard's 360s/600s tiers for consistency. Alternative: single 600s threshold (2× write cadence) with one softened message. Preference?
3. **`zsh -ic` in verify**: the alias-shadow check spawns an interactive zsh, which sources the operator's full rc. Comfortable with that (10s timeout, warn-only), or prefer a weaker but safer static grep of `~/.zshrc` for `alias valor=`?
