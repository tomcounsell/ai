---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-11
tracking: https://github.com/tomcounsell/ai/issues/1619
last_comment_id: IC_kwDOEYGa088AAAABFs5DyQ
revision_applied: true
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
- **Interface changes**: `_check_worker_health()` keeps its `(healthy: bool, age_s: int | None)` shape, now judged against a new `WORKER_DOWN_THRESHOLD_S = 600` constant; `HEARTBEAT_STALENESS_THRESHOLD_S = 360` stays in place untouched for the dashboard (`ui/app.py:330,348`). New patchable helper `_resolve_heartbeat_path()` is the single path-resolution seam, shared with `tools/agent_session_scheduler.py`. `--json` output keeps `worker_healthy` and adds `worker_state` (`"ok"`/`"down"`) + `worker_heartbeat_age_s` in BOTH `cmd_create` and `cmd_status`
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

No prerequisites — this work has no external dependencies. (Per Decision 1, PR #1612 merges together with this work, #1612 first; the wrapper-doc and wrapper-test tasks execute once it lands.)

## Solution

### Key Elements

- **Alias removal (this machine)**: delete the stale `valor` alias from `~/.zshrc` so the venv binary resolves
- **Verify-step alias-shadow check**: `/update` warns when `~/.zshrc` carries an `alias valor=` line (anchored regex `^\s*alias\s+valor\s*=` on non-comment lines, no subprocess), with a copy-paste fix in the warning
- **Worktree-proof heartbeat path**: new `_resolve_heartbeat_path()` helper resolves the heartbeat file against the main checkout (`git rev-parse --path-format=absolute --git-common-dir` with a relative-output guard), not `__file__`; the same helper replaces the scheduler's duplicate inline path expression
- **Honest warning, new 600s CLI threshold**: `ok` (< 600s, silent) / `down` (≥ 600s or missing) via new `WORKER_DOWN_THRESHOLD_S = 600`; the dashboard's `HEARTBEAT_STALENESS_THRESHOLD_S = 360` is untouched. Down text says "no recent worker heartbeat on this machine" and names the start command — it never fires inside the worker's normal 300s write cadence. Applied at both `cmd_create` and `cmd_status` warning sites
- **Wrapper-test verification**: confirm the branch's 23 tests are green on main after #1612 merges (no new wrapper test file)

### Flow

`valor "prompt"` in fresh zsh → venv binary resolves (no alias error) → session created → heartbeat read from main-checkout `data/` → (ok < 600s: silent | down ≥ 600s or missing: actionable warning) → operator/agent trusts the output.

### Technical Approach

**Part 1 — alias (machine config + update system)**
- One-time on this machine: remove `~/.zshrc:16`. The vault-synced `~/Desktop/Valor/zshenv.sh` (managed by `scripts/update/zshenv_sync.py`) is deliberately NOT used to repoint the alias — the venv binary already lands on PATH per machine; an alias adds a second source of truth.
- Add `check_valor_alias_shadow()` to `scripts/update/verify.py` following the existing `ToolCheck` pattern (cf. `check_python_alias()` at verify.py:134, which already solves the same problem-shape for `python`). Implementation (Decision 3, pattern pinned per critique C3): per line, skip lines whose first non-whitespace char is `#`, then match `re.search(r'^\s*alias\s+valor\s*=', line)`. This excludes comments, tolerates indentation and `alias valor =` spacing, and cannot match `valor-session`/`valor-*` aliases (the `\s*=` must immediately follow `valor`). Pass when no such alias line exists; warn otherwise with the exact rc-edit instruction (line number + the offending line) in the message. No subprocess, no interactive shell — deterministic in launchd/update contexts. The venv-binary existence check already in `check_valor_tools` (since `6e8de6d8`) covers the not-on-PATH case. Non-fatal (warn, don't block) — machines are heterogeneous.
- Wire the check into `check_valor_tools()` or `verify_environment()` so `/update` surfaces it (skip cleanly when `~/.zshrc` is absent or unreadable).

**Part 2 — pre-flight trustworthiness (`tools/valor_session.py` + `tools/agent_session_scheduler.py`)**
- Path fix (critique B1 + C1): extract a patchable helper `_resolve_heartbeat_path(repo_root: Path | None = None) -> Path` in `tools/valor_session.py`, default anchor `Path(__file__).parent.parent`. Inside it: run `git -C <repo_root> rev-parse --path-format=absolute --git-common-dir` (git ≥ 2.31; **flag order matters** — `--path-format` only applies to options after it, so it must precede `--git-common-dir`; verified empirically on git 2.50.1) and **guard against relative output** — `common = Path(output.strip()); abs_common = common if common.is_absolute() else (repo_root / common).resolve()` — then `abs_common.parent / "data" / "last_worker_connected"`. Never resolve against process cwd: bare `--git-common-dir` prints the relative string `.git` from the main checkout, which would re-introduce the cwd-dependence bug this plan exists to cure. Clamp negative ages from future-dated mtimes (clock skew / iCloud sync): `age_s = max(0, age_s)` — affects only the reported age, never the health decision. Any subprocess failure (non-zero exit, missing git binary, exception) falls back to the `__file__`-relative path (preserves the #980 never-raise contract). The git subprocess lives entirely inside the helper, keeping `_check_worker_health` thin; the helper is the test patch seam (tests `monkeypatch.setattr(tools.valor_session, "_resolve_heartbeat_path", ...)` — they no longer patch a module-level constant). Lazy/cheap: one subprocess call, only at check time — not import time.
- Threshold fix (Decision 2, revised per critique B2): **do NOT retire `HEARTBEAT_STALENESS_THRESHOLD_S = 360`** — it is consumed by the dashboard (`ui/app.py:330` bridge tiering, `ui/app.py:348` worker tiering) as the ok/running tier split and must stay untouched. Instead add a new constant `WORKER_DOWN_THRESHOLD_S = 600` to `agent/constants.py` (2× the 300s write cadence; spike-2(b) showed 360s left only 60s margin) and use it for the CLI pre-flight: `ok` (< 600s) / `down` (≥ 600s or file missing).
- Scheduler alignment (critique B2): `tools/agent_session_scheduler.py:566-568` carries its own inline heartbeat check with the identical `__file__`-relative path defect and the 360s threshold. Replace its path expression with an import of `_resolve_heartbeat_path` and switch it to `WORKER_DOWN_THRESHOLD_S`, so both CLIs give the same `worker_healthy` answer from the same machine. The `worker_healthy` field name stays — backward-compatible.
- Dashboard constant naming (round-2 critique): `ui/app.py` hardcodes the literal `600` as its running/error tier bound (two sites, `_get_bridge_health` and `_get_worker_health`). Import `WORKER_DOWN_THRESHOLD_S` there and substitute both literals — zero behavior change, kills the #1098-style two-representations drift. The dashboard's `__file__`-relative heartbeat path is deliberately NOT rewired (it always runs from the main checkout by service definition); leave a one-line `# TODO: migrate to _resolve_heartbeat_path if the UI ever runs from a worktree` marker.
- Message fix (both warning sites: `cmd_create` at `tools/valor_session.py:491`, `cmd_status` at `:734` — the latter emits a DISTINCT harmful string, `"No active worker — session may wait indefinitely."`, critique C2):
  - `down`: `no recent worker heartbeat on this machine ({age or 'no file'}) — session will stay pending until a worker is started (run: ./scripts/valor-service.sh worker-start)` — same template at both sites
  - `ok`: silent (no output)
  - `--json` mode: keep `worker_healthy` (true for `ok`) and add `worker_heartbeat_age_s` + `worker_state` (`"ok"`/`"down"`) in **both** `cmd_create` and `cmd_status` JSON branches (C2 parity — agent callers branch on structured data at either call site). **The fields must be unconditionally present in `cmd_status` JSON** (round-2 critique): the existing pending-only compute gate stays (avoids the subprocess on every status call), but non-pending sessions emit `"worker_state": null, "worker_heartbeat_age_s": null` so agent callers always see a predictable shape — never a silently absent key.
  - Reword the code comment at `tools/valor_session.py:465` (`# Check worker health after enqueue — warn if no active worker`) to text not containing "active worker" (e.g. `# Check worker health after enqueue — warn if no recent heartbeat`); the verification grep matches comments too, and this comment is the third hit that would otherwise fail the gate (round-2 critique, near-unanimous).
- `_check_worker_health()` stays exception-silent end to end (the #980 contract).

**Part 3 — wrapper tests (verify-only)**
- Decision 1 makes this unconditional: #1612 merges together with this work (#1612 first). After #1612 lands, run `pytest tests/unit/test_valor_cli.py -n0` on the build branch and confirm 23 green; update the feature doc's shortcoming items (see Documentation). Spike-1's branch-side evidence (`6e8de6d8`, 23/23) is the fallback record if merge ordering slips mid-build.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_check_worker_health()` swallows all exceptions by design (#980). Existing `test_does_not_raise_on_permission_error` covers it; extend with a case where the git-common-dir subprocess fails (returns non-zero / binary missing) asserting fallback to the `__file__`-relative path and a `down`/`None` result rather than a raise
- [ ] `_resolve_heartbeat_path()` with git emitting a RELATIVE common dir (e.g. `.git`): assert the result resolves under the `repo_root` anchor, NOT the process cwd — run the assertion with cwd set elsewhere (`monkeypatch.chdir(tmp_path)`) (critique B1)
- [ ] New verify-step check: `~/.zshrc` missing and `~/.zshrc` unreadable (PermissionError) must each produce a clean pass/skip-level `ToolCheck` (never crash `/update`) — one test per failure mode with the path/read mocked

### Empty/Invalid Input Handling
- [ ] Heartbeat file exists but is empty or has future mtime: assert `age_s` is clamped to 0 (`max(0, age_s)`) — state reads `ok`, and no negative number reaches the message text or `worker_heartbeat_age_s` JSON field (round-2 critique pinned the behavior: future mtime = age 0 = healthy)
- [ ] `~/.zshrc` containing only a commented-out `# alias valor=` line: verify the alias-shadow check passes (no false warning on dead config)
- [ ] `~/.zshrc` containing `alias valor-session=...` (or other `valor-*` aliases): verify the anchored pattern does NOT false-positive (critique C3 — fixture must include this line in the pass case)

### Error State Rendering
- [ ] Assert no warning is emitted for any heartbeat age under 600s, and that BOTH harmful strings are gone — `"no active worker detected"` (cmd_create) and `"No active worker — session may wait indefinitely."` (cmd_status) — via the broadened case-insensitive grep in Verification (critique C2)
- [ ] Assert the `down` message names the start command; assert `--json` carries `worker_state` in both `cmd_create` and `cmd_status` so agent callers branch on structured data, not prose

## Test Impact

- [ ] `tests/unit/test_worker_health_check.py::TestCheckWorkerHealth` (5 tests) — UPDATE: `test_healthy_worker`, `test_stale_worker`, `test_missing_heartbeat_file`, `test_exact_threshold_boundary` rewritten against the 600s threshold (a 360–599s age is now healthy). **Patch-seam migration (critique C1):** existing tests patch the module-level `tools.valor_session._WORKER_HEARTBEAT_FILE`; with resolution moving inside the helper, every test must be migrated to `monkeypatch.setattr(tools.valor_session, "_resolve_heartbeat_path", lambda **_: tmp_path / "last_worker_connected")` — otherwise they silently exercise the live filesystem. Add `_resolve_heartbeat_path` cases: real `tmp_path` worktree layout, relative-git-output guard (cwd elsewhere), git-failure fallback
- [ ] `tests/unit/test_valor_cli.py` (branch-only) — no change; verify-only per spike-1
- [ ] Tests covering `tools/agent_session_scheduler.py` `worker_healthy` output (if any reference the heartbeat path or 360s threshold) — UPDATE to the shared resolver + 600s threshold; grep at build time
- [ ] No other existing tests reference `_check_worker_health` or `check_valor_tools` (grep-verified at plan time)

## Rabbit Holes

- **Dashboard HTTP fallback** (issue candidate (b)): falling back to `GET localhost:8500/dashboard.json` couples the CLI to the UI server being up, adds a network call to a hot path, and the dashboard reads the same file anyway — fixing the path + tiers makes the fallback redundant. Skip.
- **Auto-editing `~/.zshrc` from `/update`**: tempting "proactive maintenance", but programmatically rewriting user rc files from an update script is invasive and hard to make idempotent across heterogeneous machines. Warn with a copy-paste fix instead. (The one-time removal on THIS machine is done by hand in this plan, not by the script.)
- **Deriving `KNOWN_SUBCOMMANDS` from the parser at runtime**: explicitly #1620's scope, not this plan's.
- **Touching the worker's write cadence** (`AGENT_SESSION_HEALTH_CHECK_INTERVAL`): shortening it to widen the margin affects the whole health loop and the watchdog's 600s threshold math. The read-side threshold fix achieves the same outcome with zero blast radius.
- **Granite PTY substrate interactions**: PR #1612's larger content (PTY pool, bridge adapter) is entirely out of scope; only the three named files matter here.

## Risks

### Risk 1: #1612 merge slips despite Decision 1
**Impact:** Wrapper-specific tasks (feature-doc shortcoming updates, on-main test verification) have no target files on main until #1612 lands; merging this PR first would orphan those edits.
**Mitigation:** Decision 1 fixes the merge order (#1612 first, this PR immediately after); build order still puts main-safe parts (1, 2) first, and spike-1's branch-side evidence (`6e8de6d8`, 23/23) is recorded in the PR body as fallback if ordering slips mid-build.

### Risk 2: static `~/.zshrc` grep misses aliases defined elsewhere
**Impact:** An alias in `~/.zprofile`, `~/.zshenv`, or a sourced file shadows the binary without triggering the warning.
**Mitigation:** Accepted (Decision 3) — the check targets the one known artifact (`~/.zshrc` alias) and is warn-only; the venv-binary existence check in `check_valor_tools` independently catches a missing binary. A broader interactive-resolution probe was rejected for hang risk in launchd contexts.

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
**State prerequisite:** the 600s threshold must fully cover the worst-case write gap (300s cadence + health-check duration) — it does, with 300s of slack
**Mitigation:** atomic replace already in place; the 2× cadence threshold absorbs jitter. No locking needed; the check is advisory only.

No other concurrency concerns — the CLI path is synchronous and read-only.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1620] Wrapper shortcomings 4, 5, 6 (help after positional shortcut, PTY-boundary reframe, #1288 operator path) plus deriving the allowlist from subparsers — already filed as issue #1620
- [ORDERED] Merging PR #1612 itself — human-gated merge of the granite-pty production cutover; this plan reads its state but never advances it. Decision 1 fixes the ordering at the MERGE stage (#1612 first, this PR immediately after)
- [EXTERNAL] Removing the stale alias on OTHER machines' `~/.zshrc` — requires running on each machine; the `/update` verify warning (shipped here) is the mechanism that reaches them on their next update cycle

## Update System

This plan CHANGES the update system: `scripts/update/verify.py` gains the alias-shadow check, surfaced through the existing `/update` verify output. No new dependencies, no new config files, no migration steps — the check is self-contained and warn-only. Machines pick it up on their next `/update` run; the warning text carries the per-machine fix instruction.

## Agent Integration

No new agent integration required — `valor` and `valor-session` are already CLI entry points in `pyproject.toml [project.scripts]` reachable via the agent's Bash tool. This plan improves the trustworthiness of their output for agent callers: the `--json` create/status payloads gain `worker_state` and `worker_heartbeat_age_s` so agents branch on structured fields instead of stderr prose. No `.mcp.json` or bridge changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/valor-cli-wrapper.md` — shortcoming item 1 (alias): fixed status + the verify-step guard; item 3 (pre-flight): correct the misdiagnosis ("stale Redis cache" → file-mtime path divergence + threshold semantics) and document the 600s single-threshold behavior; item 7/9 (tests): already-accurate, confirm wording. This file lands on main with PR #1612, which merges together with this work (Decision 1) — execute after #1612 merges
- [ ] Update `docs/features/session-steering.md` (or the doc section covering `valor-session create`) with the new warning semantics and `--json` fields
- [ ] `docs/features/README.md` index — no new entry needed (existing pages updated in place)

### Inline Documentation
- [ ] Docstring on the reworked `_check_worker_health()` documenting the 600s threshold rationale (2× write cadence), the git-common-dir resolution, and the #980 never-raise contract
- [ ] Comment on the verify-step check pointing at this issue for the heterogeneous-machines rationale

## Success Criteria

- [ ] `valor "test prompt" --json` runs from a fresh interactive zsh on this machine with no alias error
- [ ] `/update` verify warns when `~/.zshrc` carries an `alias valor=` line (covered by unit tests for pass/alias-present/commented/missing-rc/unreadable-rc cases)
- [ ] Creating a session within 600s of the last heartbeat write — and from a worktree cwd — produces no warning at all
- [ ] `--json` output carries `worker_state` and `worker_heartbeat_age_s` in both `cmd_create` and `cmd_status` — unconditionally present in `cmd_status` (null when the pending-only compute is skipped), never a silently absent key
- [ ] Fresh-shell end-to-end: `zsh -ic 'valor "smoke test" --json'` exits 0 with `worker_state` in output (session cleaned up after)
- [ ] `tests/unit/test_worker_health_check.py` rewritten for the 600s threshold, patching the `_resolve_heartbeat_path` seam, including worktree-path, relative-git-output, and git-fallback cases — green
- [ ] Dashboard tiering unchanged: `HEARTBEAT_STALENESS_THRESHOLD_S = 360` still in place and consumed by `ui/app.py`
- [ ] `tools/agent_session_scheduler.py` heartbeat check uses the shared resolver + `WORKER_DOWN_THRESHOLD_S` (no contradictory `worker_healthy` answers between CLIs)
- [ ] Wrapper tests: 23/23 green on the build branch after #1612 merges; no new wrapper test file written
- [ ] Case-insensitive repo grep for "active worker" returns nothing in tools/, agent/, scripts/ (both harmful strings gone)
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
- Extract `_resolve_heartbeat_path(repo_root=None)` helper: `git rev-parse --path-format=absolute --git-common-dir` (flag order matters — `--path-format` first) with relative-output guard (resolve against the `__file__` anchor, never cwd — critique B1) and `__file__`-relative fallback; never raise; clamp negative ages (`max(0, age_s)`)
- Add `WORKER_DOWN_THRESHOLD_S = 600` to `agent/constants.py`; use it in the CLI pre-flight. **Leave `HEARTBEAT_STALENESS_THRESHOLD_S = 360` untouched** for the dashboard (critique B2)
- Switch `tools/agent_session_scheduler.py:566-568` to the shared resolver + `WORKER_DOWN_THRESHOLD_S` (critique B2)
- In `ui/app.py`, import `WORKER_DOWN_THRESHOLD_S` and replace the two bare `600` literals in `_get_bridge_health`/`_get_worker_health` (zero behavior change); add the heartbeat-path TODO marker (round-2 critique)
- Update both warning sites (`cmd_create` :491, `cmd_status` :734) with the honest `down` message; extend `--json` with `worker_state`, `worker_heartbeat_age_s` at BOTH sites — unconditionally present in `cmd_status` JSON, `null` when the pending-only compute is skipped; keep `worker_healthy` (critique C2 + round 2)
- Reword the code comment at `tools/valor_session.py:465` so it no longer contains "active worker" (round-2 critique)
- Case-insensitive grep repo for "active worker"; clear EVERY match — warning strings AND comments — until the grep exits 1 (this is the verification gate's exact command)
- Rewrite `tests/unit/test_worker_health_check.py` per Test Impact — migrate every test to patch the `_resolve_heartbeat_path` seam (critique C1)

### 2. Verify-step alias-shadow check (Part 1)
- **Task ID**: build-alias-check
- **Depends On**: none
- **Validates**: tests/unit/test_update_valor_alias.py (create)
- **Informed By**: recon (verify.py:134 `check_python_alias` is the existing pattern; `check_valor_tools` gained the venv-binary check in 6e8de6d8)
- **Assigned To**: preflight-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `check_valor_alias_shadow()` to `scripts/update/verify.py` (anchored regex `^\s*alias\s+valor\s*=` on non-comment lines of `~/.zshrc` — critique C3; warn-only ToolCheck, copy-paste fix in the message — Decision 3)
- Wire into the verify flow next to `check_valor_tools`
- Unit tests: pass (fixture includes an `alias valor-session=...` line — must NOT match) / alias-present / commented-out-alias / rc-file-missing / rc-file-unreadable (path mocked via tmp_path)
- Remove the stale alias from `~/.zshrc` on this machine and confirm `zsh -ic 'whence -p valor'` resolves to the venv binary

### 3. Validate Parts 1+2
- **Task ID**: validate-preflight
- **Depends On**: build-preflight, build-alias-check
- **Assigned To**: preflight-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the two test files serially (`-n0`); confirm green
- From a worktree cwd, run `valor-session create --role teammate --message "smoke" --json` against a fresh heartbeat and assert `worker_state: ok` and no warning; clean up the test session per Manual Testing Hygiene (recognizable project key, Popoto-only deletion)
- Fresh-shell end-to-end (round-2 critique — `valor --help` alone never reaches `cmd_create`): run `zsh -ic 'valor "smoke test" --json'` and assert no alias error, exit 0, and `worker_state` present in the JSON; this exercises the wrapper → enqueue → health-check → JSON path that acceptance criterion 1 actually names. Clean up the created session per Manual Testing Hygiene immediately after

### 4. Wrapper verification + docs (Part 3)
- **Task ID**: verify-wrapper
- **Depends On**: validate-preflight
- **Informed By**: spike-1 (23/23 green on branch @ 6e8de6d8)
- **Assigned To**: preflight-validator
- **Agent Type**: validator
- **Parallel**: false
- Decision 1: #1612 merges together with this work (#1612 first). Check `gh pr view 1612 --json state` — once MERGED: run `pytest tests/unit/test_valor_cli.py -n0`, confirm 23 green on the build branch
- If #1612 is still OPEN at this step, record spike-1's branch-side verification (commit `6e8de6d8`, 23 passed) in the implementation PR body and proceed; the wrapper-doc edits land after #1612 does, before this PR merges

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
| Lint clean | `python -m ruff check tools/valor_session.py tools/agent_session_scheduler.py scripts/update/verify.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/valor_session.py tools/agent_session_scheduler.py scripts/update/verify.py` | exit code 0 |
| Harmful claims gone (both strings, case-insensitive) | `grep -rni "active worker" tools/ agent/ scripts/` | exit code 1 |
| JSON contract (create + status parity) | `grep -n "worker_state" tools/valor_session.py` | ≥ 2 hits (cmd_create and cmd_status JSON branches) |
| Dashboard constant untouched | `grep -n "HEARTBEAT_STALENESS_THRESHOLD_S: int = 360" agent/constants.py` | exit code 0 |
| Scheduler uses shared resolver | `grep -n "_resolve_heartbeat_path\|WORKER_DOWN_THRESHOLD_S" tools/agent_session_scheduler.py` | both present |
| Dashboard 600-literals named | `grep -n "WORKER_DOWN_THRESHOLD_S" ui/app.py` | exit code 0 |

## Critique Results

Critique 2026-06-11 (comment 4677448506, plan @ `6b988f3d`): NEEDS REVISION — 2 blockers, 3 concerns. All five incorporated in this revision.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | All (unanimous) | B1: bare `--git-common-dir` prints relative `.git` from the main checkout — planned path arithmetic was cwd-relative, re-introducing the bug class inverted | Technical Approach Part 2 (path fix); Failure Path Test Strategy; Task 1 | `--path-format=absolute` + guard: non-absolute output resolves against the `__file__` anchor (`(repo_root / common).resolve()`), never cwd; unit test asserts resolution with cwd set elsewhere |
| BLOCKER | Operator (+4 cross-validated) | B2: `HEARTBEAT_STALENESS_THRESHOLD_S` is shared with `ui/app.py:330,348` and `tools/agent_session_scheduler.py:568` — retiring it breaks dashboard tiering and leaves a split-brain scheduler | Technical Approach Part 2 (threshold + scheduler alignment); Architectural Impact; Verification table; Task 1 | New `WORKER_DOWN_THRESHOLD_S = 600` in `agent/constants.py`; 360s constant untouched for the dashboard; scheduler's inline block switches to the shared resolver + 600s; `worker_healthy` field name unchanged |
| CONCERN | Skeptic | C1: moving path resolution inside `_check_worker_health()` destroys the `_WORKER_HEARTBEAT_FILE` patch seam — rewritten tests would hit the live filesystem | Technical Approach Part 2; Test Impact; Task 1 | `_resolve_heartbeat_path(repo_root=None)` is the patchable seam; tests `monkeypatch.setattr(tools.valor_session, "_resolve_heartbeat_path", lambda **_: tmp_path / ...)` |
| CONCERN | Skeptic +5 | C2: verification grep missed `cmd_status`'s distinct harmful string (`"No active worker — session may wait indefinitely."`); `cmd_status --json` lacked `worker_state` parity | Technical Approach Part 2 (message fix); Verification table; Success Criteria; Task 1 | Grep broadened to case-insensitive `grep -rni "active worker"` (expected exit 1); `cmd_status` :734 gets the same `down` template and the structured JSON fields |
| CONCERN | Adversary | C3: unspecified alias grep pattern could false-positive on `valor-*` aliases or match commented lines | Technical Approach Part 1; Failure Path Test Strategy; Task 2 | `re.search(r'^\s*alias\s+valor\s*=', line)` on lines whose first non-whitespace char is not `#`; pass-case fixture includes `alias valor-session=...` |

Re-critique 2026-06-11 round 2 (plan @ `0271712f`): READY TO BUILD (with concerns) — 0 architectural defects (B1 guard logic independently re-verified correct in both worktree and main-checkout layouts by the Adversary), 6 findings, all embedded in this revision pass:

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN (top) | 7 of 7 | Verification grep `grep -rni "active worker"` also matches the code comment at `tools/valor_session.py:465` — gate would fail with only the two warning strings fixed | Technical Approach Part 2; Task 1 (explicit reword bullet + clear-every-match instruction) | Reword the comment to drop "active worker"; Task 1's grep step now states the gate's exact command and that comments count |
| CONCERN (top) | User | Task 3 validated AC1 with `valor --help`, which never reaches `cmd_create`/health-check | Task 3; Success Criteria | Fresh-shell end-to-end `zsh -ic 'valor "smoke test" --json'`, assert exit 0 + `worker_state` present, Popoto cleanup after |
| CONCERN | Skeptic, Adversary, Consistency | `cmd_status` JSON fields gated on `pending` — non-pending sessions silently lack `worker_state` | Technical Approach Part 2; Task 1; Success Criteria | Keep pending-only compute; emit `worker_state: null, worker_heartbeat_age_s: null` otherwise — fields always present |
| CONCERN | Archaeologist | `ui/app.py` hardcodes `600` twice — #1098-style drift vs the new named constant | Technical Approach Part 2; Task 1; Verification table | Import `WORKER_DOWN_THRESHOLD_S`, substitute both literals; zero behavior change |
| CONCERN | Operator | `ui/app.py` heartbeat path still `__file__`-relative (third copy of the pattern) | Technical Approach Part 2 (explicit non-rewire decision + TODO marker) | Dashboard always runs from the main checkout; TODO marker makes the gap visible; full rewire deferred |
| NIT | Skeptic, Consistency | Plan prose had `--path-format=absolute` AFTER `--git-common-dir`, which still prints relative `.git` (flag only applies to options after it) | Technical Approach Part 2; Key Elements; Task 1 | Order corrected to `--path-format=absolute --git-common-dir`; relative-output guard retained as true fallback |
| NIT | Adversary | Future-mtime heartbeat → negative `age_s` leaks into message/JSON | Technical Approach Part 2; Failure Path Test Strategy | `age_s = max(0, age_s)` after the subtraction; affects reported age only |
| DISMISSED | User | Re-add a `stale` tier for the 360–599s band | — | Overridden by Decision 2 (operator chose single 600s threshold); agents needing finer granularity have `worker_heartbeat_age_s` |

---

## Decisions (Open Questions resolved 2026-06-11)

1. **Sequencing vs PR #1612**: RESOLVED — #1612 merges together with this work. Merge order at the MERGE stage: #1612 first, then this PR immediately after. The wrapper-conditional tasks (feature-doc updates, on-main wrapper-test verification) are therefore unconditional and execute as part of this plan.
2. **Warning tier thresholds**: RESOLVED — single 600s threshold (2× the 300s write cadence). Two states: `ok` (< 600s) and `down` (≥ 600s or file missing). No `stale` tier. *(Revised per critique B2: the 600s value lives in a NEW constant `WORKER_DOWN_THRESHOLD_S`; the shared `HEARTBEAT_STALENESS_THRESHOLD_S = 360` stays for the dashboard.)*
3. **`zsh -ic` in verify**: RESOLVED — static grep of `~/.zshrc` for `alias valor=`. Deterministic, zero hang risk in launchd/update contexts, and it targets exactly the known stale-alias artifact. The venv-binary existence check (already in `check_valor_tools` since `6e8de6d8`) covers the not-on-PATH case.
