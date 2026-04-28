---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-27
tracking: https://github.com/tomcounsell/ai/issues/1175
last_comment_id:
---

# SDLC Tool Resolver — `sdlc-tool` wrapper for cwd-independent invocation

## Problem

When `/sdlc` is invoked from a Claude Code session whose cwd is a **target project** (e.g. `~/src/cuttlefish`, `~/src/popoto`), every `python -m tools.X` call inside the SDLC skills fails with `ModuleNotFoundError: No module named 'tools.sdlc_verdict'`. Python resolves `tools/` against the current working directory, finds the target project's own `tools/` directory (which exists in cuttlefish, popoto, etc. but lacks the SDLC tooling), and aborts.

The failure mode is mostly invisible:

- `python -m tools.sdlc_stage_marker` is intentionally silenced via `2>/dev/null || true` (markers are best-effort).
- `python -m tools.sdlc_stage_query` returns the documented `unavailable` graceful-degradation marker, so the router falls back to dispatch-history inference.
- `python -m tools.sdlc_verdict record` is **not** silenced in the skill markdown, but it is the last shell call before the operator-facing report — its non-zero exit doesn't surface as a hard failure.

**Net effect:** `_verdicts["CRITIQUE"]` is never populated when `/sdlc` is driven from a local Claude Code session in the target repo. Guard G5 ("unchanged-critique cache hit") cannot fire. `/do-plan-critique` re-runs against unchanged plans, and because the war-room critics are LLM-driven and non-deterministic, verdicts diverge across runs. Row 4b (concerns → `/do-plan` revision) and Row 4c (`revision_applied: true` → `/do-build`) cannot be driven by the recorded verdict and require a human to manually inspect the critique output.

This is the same oscillation symptom that #1040 was meant to fix — **the fix simply doesn't reach local-cwd `/sdlc` invocations.**

**Current behavior:**
- Reproduced on cuttlefish issues #299 and #302: three independent critique runs against `docs/plans/env-prod-references-cleanup.md` returned `READY TO BUILD (with concerns)` → `NEEDS REVISION` → `NEEDS REVISION` with diverging finding sets even though the plan only changed once between runs 1→2.
- The same import-shadowing bug applies to the `/do-pr-review` flow: `_verdicts["REVIEW"]` is also unrecorded when the operator drives review from a target-repo cwd.

**Desired outcome:**
- `/sdlc {issue}` from any cwd — local target-repo session or bridge-spawned PM session — successfully writes `_verdicts["CRITIQUE"]` and `_verdicts["REVIEW"]` against the right `AgentSession`.
- `python -m tools.sdlc_stage_query --issue-number {N}` (via the new wrapper) shows the verdict subkey populated with `verdict`, `recorded_at`, and `artifact_hash`.
- Guard G5 fires: re-running `/sdlc {N}` against an unchanged plan reuses the cached verdict.
- Row 4b → Row 4c transition works end-to-end from a local `/sdlc` invocation.
- Verdict-record failures become **loud** (non-zero exit, surfaced to the operator) so this class of bug cannot hide again.

## Freshness Check

**Baseline commit:** `754ea642` (`Plan: Dashboard /memories tab — per-record memory inspector`)
**Issue filed at:** `2026-04-26T13:27:56Z`
**Disposition:** Unchanged — verified at plan time (2026-04-27).

**File:line references re-verified:**
- `tools/sdlc_verdict.py` — module exists, single-writer for `_verdicts`, exits 0 always after `print(json.dumps(result))` (line 278-279). Import chain depends on `popoto` + `models.agent_session.AgentSession` — confirmed not importable from a target-project venv.
- `tools/sdlc_stage_marker.py`, `tools/sdlc_stage_query.py`, `tools/sdlc_dispatch.py`, `tools/sdlc_session_ensure.py` — all present in `tools/`, all use the `tools._sdlc_utils` shared helpers, all share the same cwd-shadowing failure mode.
- `agent/sdlc_router.py` — confirmed reads `_verdicts` from `stage_states` to drive G5; cycle guard is enforced by `tests/unit/test_architectural_constraints.py`.
- `.claude/skills/sdlc/SKILL.md`, `.claude/skills/do-plan-critique/SKILL.md`, `.claude/skills/do-pr-review/SKILL.md`, `.claude/skills/do-plan/SKILL.md`, `.claude/skills/do-issue/SKILL.md`, `.claude/skills/do-docs/SKILL.md`, `.claude/skills/do-pr-review/sub-skills/post-review.md` — all confirmed to invoke `python -m tools.sdlc_*` directly.
- `.claude/hooks/post_compact.py:131` — emits literal `python -m tools.sdlc_stage_query` text in its compaction nudge (not executed; instructs the agent).
- `config/personas/project-manager.md:21,558` — instructs the PM persona to run `python -m tools.sdlc_stage_query` directly.

**Cited sibling issues/PRs re-checked:**
- #1040 — closed 2026-04-18. Original oscillation bug; fixed by introducing `tools.sdlc_verdict` and Guard G5. Resolution **does not** cover cwd-relative tool resolution.
- #1043 — closed 2026-04-18. Sibling reproducer for #1040. Same gap.
- #1044 (PR) — merged. Implemented Guards G1-G5. Verdict reads work, verdict writes do not when cwd shadows `tools/`.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-04-25" -- agent/sdlc_router.py tools/sdlc_*.py` returns empty.
- Skills: only unrelated docs and design-system changes; no SDLC skill markdown drift.

**Active plans in `docs/plans/` overlapping this area:** None. Closest neighbors are `sdlc-router-oscillation-guard.md` (completed, source of #1040 fix) and the now-completed dashboard plans. No active plan touches the SDLC tooling cwd resolution.

**Notes:** The repo uses a hardlink-sync model — `scripts/update/hardlinks.py` hardlinks `.claude/skills/{name}/SKILL.md` to `~/.claude/skills/{name}/SKILL.md` so editing either location updates both (same inode). New scripts deployed under `scripts/` need a parallel hardlink target if they should be available outside the repo.

## Prior Art

- **#1040 (closed)**: "SDLC router oscillates between critique/review stages with non-deterministic verdicts" — root-caused to dual-source verdict drift; fixed by introducing `tools.sdlc_verdict` as the single writer and Guard G5 reading the recorded verdict. **Did not** address cwd-relative module resolution.
- **#1043 (closed)**: Reproducer for #1040 against `/do-pr-review`. Same fix applies; same gap.
- **PR #1044 (merged)**: Implemented Guards G1-G5 and the `tools.sdlc_verdict` single-writer module. The architectural constraints test (`tests/unit/test_architectural_constraints.py`) ensures `agent.sdlc_router` does not import `tools.sdlc_verdict` (cycle guard) — this constraint is preserved in this plan.
- **#887 / #1109 / #1158 (closed)**: Session isolation / working-directory bugs — different problem (about which directory bridge-spawned sessions run in), but reinforces a recurring theme: cwd assumptions in this codebase need explicit handling, not implicit defaults.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1044 (G1-G5) | Single-writer verdict recorder + 5 routing guards | Verdict **read** path works (`agent/sdlc_router.py` reads from in-memory `AgentSession`); verdict **write** path is via `python -m tools.sdlc_verdict` which is cwd-dependent and shadowed by target-project `tools/` packages. The fix designed around the symptom (oscillation) not the failure-to-record root cause. |
| `2>/dev/null \|\| true` on stage markers | Hide best-effort marker noise | Pattern was uncritically copied to verdict recording in some places, hiding load-bearing failures. |

**Root cause pattern:** Skill markdown shells out via `python -m tools.X`, treating `tools/` as if it were universally importable. The codebase has no abstraction layer between "skill markdown" and "ai/-repo Python module," so every cwd assumption leaks straight into the shell command. The right architectural answer is a single resolver — a wrapper command that knows where the ai/ repo lives — instead of expecting every skill to redundantly handle cwd.

## Architectural Impact

- **New artifact:** `scripts/sdlc-tool` — a bash wrapper that resolves the ai/ repo and dispatches into the appropriate `tools.sdlc_*` module via `uv run --directory`. Becomes the single resolver for SDLC tooling.
- **Coupling:** Skill markdown becomes coupled to one wrapper name (`sdlc-tool`) instead of N module names (`tools.sdlc_verdict`, `tools.sdlc_dispatch`, etc.). Lower coupling, higher cohesion.
- **Interface change:** New CLI surface `sdlc-tool {subcommand} ...` mirrors the existing `python -m tools.sdlc_{subcommand}` surface. The underlying Python modules and their CLIs are unchanged — wrapper is additive.
- **New dependency:** None new. `uv` is already a hard dependency of the update system. The wrapper uses `uv run --directory` which is already in use elsewhere.
- **Reversibility:** Trivial. The wrapper is a single bash file; if it proves wrong, revert the skill-markdown changes and delete the wrapper. The underlying `tools.sdlc_*` modules are untouched.
- **Cycle guard (preserved):** `agent/sdlc_router.py` continues to NOT import `tools.sdlc_verdict` or `tools.sdlc_dispatch`. This plan does not change Python imports.

## Appetite

**Size:** Medium

**Team:** Solo dev, lead validator

**Interactions:**
- PM check-ins: 1 (validate option choice and verdict-loudness policy at plan-finalize time)
- Review rounds: 1 (PR review focused on the wrapper + parity test + verdict-loudness policy)

The change touches ~9 files (1 new wrapper, 7 skill-markdown edits, 1 update-script edit) plus tests + docs. The work is mechanical once the option is locked.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `uv` installed | `command -v uv` | The wrapper uses `uv run --directory`. |
| AI repo present | `test -d "${AI_REPO_ROOT:-$HOME/src/ai}/tools"` | The wrapper needs the SDLC modules at the resolved root. |
| `~/.local/bin` on PATH | `echo "$PATH" \| tr ':' '\n' \| grep -qx "$HOME/.local/bin"` | The wrapper is hardlinked into `~/.local/bin/`; PATH must include it for skill markdown to invoke `sdlc-tool` without an absolute path. |

Run all checks: `python scripts/check_prerequisites.py docs/plans/sdlc-1175-tool-resolver.md`

## Solution

### Option Decision

The issue lays out four options; this plan picks **Option (1) — wrapper script** with the following justification:

- **Smallest blast radius.** One new file (`scripts/sdlc-tool`), seven skill-markdown edits, one update-script edit. Compare to Option (2): every skill site grows to a 3-token shell composition (`(cd "$AI_REPO_ROOT" && uv run python -m tools.X)`) and the cwd assumption now has to be re-verified at ~30 sites each time anyone edits a skill.
- **Single source of truth for "where is the ai/ repo."** Option (2) requires `AI_REPO_ROOT` to be set/exported reliably in every shell invocation; the wrapper centralizes the lookup with one defaulting fallback (`$HOME/src/ai`) and one error-out path.
- **Loud-vs-silent semantics in one place.** The wrapper can decide per-subcommand whether to surface non-zero exits (verdict, dispatch — load-bearing) or pass through silently (stage-marker, session-ensure — best-effort). Option (2) would require every skill site to re-derive the right `2>/dev/null || true` policy.
- **Cleaner testability.** A single wrapper can be exercised by a focused unit test that simulates target-repo cwd. Option (2) requires N parity tests, one per invocation site.
- **Doesn't break local /sdlc workflows** the way Option (4) would. Option (4) is hostile to anyone running `/sdlc` from a target repo today — Valor's actual workflow.
- Option (3) (editable install in target venvs) was explicitly called out as too heavy in the issue; pulling `popoto` + `AgentSession` ORM into every target venv is dependency pollution.

### Key Elements

- **`scripts/sdlc-tool`** — bash wrapper. Resolves `${AI_REPO_ROOT:-$HOME/src/ai}`. Translates kebab-case subcommand to `tools.sdlc_<name>`. Execs `uv run --directory "$AI_REPO_ROOT" python -m tools.sdlc_<name> "$@"`. Per-subcommand exit-policy table embedded in the wrapper (verdict + dispatch are loud; marker + session-ensure are silent; stage-query passes through).
- **`scripts/update/hardlinks.py`** — extended to hardlink `scripts/sdlc-tool` into `~/.local/bin/sdlc-tool` and verify executability. Cleaned up by the existing stale-link removal logic.
- **`scripts/update/verify.py`** — new check that `sdlc-tool` is on PATH and resolves to the current source. Gates the update step the same way the bridge config validation gates the bridge restart.
- **Skill markdown rewrites** — every `python -m tools.sdlc_*` invocation in `.claude/skills/**` and `.claude/hooks/post_compact.py` and `config/personas/project-manager.md` becomes `sdlc-tool <subcommand> ...`. The `2>/dev/null || true` suffix stays only on stage-marker and session-ensure calls (best-effort); it is REMOVED from verdict-record and dispatch-record calls (load-bearing; failures must be surfaced).
- **Parity test** — new `tests/unit/test_sdlc_tool_wrapper.py` that:
  1. Confirms the wrapper exits 2 with a clear stderr message when `AI_REPO_ROOT` doesn't contain `tools/`.
  2. Confirms the wrapper exits 2 with a usage message on unknown subcommand.
  3. Spawns a subprocess with cwd set to a tmp dir that contains a fake `tools/__init__.py` (simulating cuttlefish), invokes `sdlc-tool stage-query --issue-number 99999`, and asserts the call succeeds (does NOT raise `ModuleNotFoundError`) and returns the documented unavailable marker.
  4. Cross-checks every `python -m tools.sdlc_*` invocation in `.claude/skills/**` (parity sweep) — fails if any skill site still uses the bare `python -m` form, except in documentation-only contexts.

### Flow

Local /sdlc invocation in target repo cwd → skill markdown calls `sdlc-tool verdict record ...` → wrapper resolves `~/src/ai` → `uv run --directory ~/src/ai python -m tools.sdlc_verdict record ...` → verdict written to `AgentSession.stage_states._verdicts["CRITIQUE"]` → next `/sdlc` invocation reads it via `sdlc-tool stage-query` → Guard G5 fires → /do-plan-critique is **not** re-dispatched.

### Technical Approach

- **Wrapper file format.** Plain bash with `set -euo pipefail`. No Python interpreter startup; the wrapper itself adds <50ms overhead. The slow part is `uv run --directory` which is already incurred today.
- **AI_REPO_ROOT resolution.** Order: explicit env > `$HOME/src/ai` default. No probing of multiple locations (keeps behavior predictable). If neither resolves to a directory containing `tools/`, exit 2 with a clear stderr message naming the resolved path.
- **Subcommand-to-module mapping.** Hard-coded allowlist (`verdict`, `dispatch`, `stage-marker`, `stage-query`, `session-ensure`). Refuses unknown subcommands with exit 2 instead of letting Python report an opaque `ModuleNotFoundError` later.
- **Loud-vs-silent semantics.** The wrapper passes the underlying exit code through. The skill markdown decides whether to silence with `|| true`:
  - `sdlc-tool stage-marker ...` → keep `2>/dev/null || true` in skills (best-effort markers).
  - `sdlc-tool session-ensure ...` → keep `2>/dev/null || true` in skills (idempotent best-effort).
  - `sdlc-tool stage-query ...` → no silencing; the tool already returns `unavailable` on graceful failure.
  - `sdlc-tool verdict record ...` → **no `|| true`**. Failures surface to the operator. Reason: this caused the bug; never silence again.
  - `sdlc-tool dispatch record ...` → **no `|| true`**. Failures surface. Same reason — load-bearing for Guard G4.
- **Hardlink distribution.** `scripts/update/hardlinks.py` already hardlinks `.claude/{skills,commands,agents}` and the SDLC hook scripts. Extended to also hardlink `scripts/sdlc-tool` to `~/.local/bin/sdlc-tool`. The existing `_cleanup_stale_*` logic doesn't apply to a single named file — add an explicit cleanup for the renamed-removal table if the wrapper is ever renamed.
- **Cross-repo invocation under bridge-spawned PM sessions.** The bridge sets `cwd = target project's worktree`. The wrapper does NOT depend on cwd — it cd's into `$AI_REPO_ROOT`. This means the same fix covers the bridge case at zero extra cost.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] No new `except Exception: pass` blocks introduced. The wrapper uses `set -euo pipefail`; any unhandled error becomes a non-zero exit with the underlying message on stderr.
- [ ] `tools/sdlc_verdict.py` already swallows internal exceptions and prints `{}` (line 272-279). Behavior preserved. The wrapper does NOT add a try/catch around the underlying invocation — it passes exit codes through.
- [ ] Skill markdown for verdict-record and dispatch-record sites: confirm by code review that no `|| true` snuck back in.

### Empty/Invalid Input Handling

- [ ] Wrapper invoked with no subcommand: exits 2 with usage message.
- [ ] Wrapper invoked with unknown subcommand: exits 2 listing valid subcommands.
- [ ] Wrapper invoked with `AI_REPO_ROOT` set to a non-existent path: exits 2 naming the resolved path.
- [ ] Wrapper invoked with `AI_REPO_ROOT` set to a path that exists but lacks `tools/`: exits 2 with a clear message ("does not contain a tools/ directory").

### Error State Rendering

- [ ] When `sdlc-tool verdict record` fails (e.g., Redis unreachable), the failure must reach the operator's session log. Confirmed by removing `2>/dev/null || true` from the skill-markdown sites and asserting via the parity test.
- [ ] When `sdlc-tool stage-query` returns `unavailable`, the existing fallback path in `.claude/skills/sdlc/SKILL.md` continues to drive dispatch from history. Confirmed by an integration test that simulates an unreachable Redis.

## Test Impact

- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE: add a sweep that asserts every `python -m tools.sdlc_*` reference in `.claude/skills/**` has been replaced with `sdlc-tool ...`. Existing parity assertions (SDLC.md ↔ Python router) stay.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: existing tests at lines 152, 162, 177 invoke `[sys.executable, "-m", "tools.sdlc_stage_marker"]` directly — those continue to work and remain (we are not removing the underlying Python module, only how skills invoke it). Add one new test that exercises the wrapper end-to-end via subprocess from a tmp cwd containing a fake `tools/`.
- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE: add wrapper-mediated invocation tests parallel to the existing module-direct tests. Verify exit codes are surfaced when the underlying call fails (new requirement: loud verdict failures).
- [ ] `tests/unit/test_pm_session_permissions.py:454` — UPDATE: the literal `"python -m tools.sdlc_stage_query"` matcher in `_make_bash_input(...)` should be augmented (or replaced) with the `sdlc-tool stage-query` form so PM session permission rules accept the new wrapper.
- [ ] `tests/unit/test_architectural_constraints.py` — UPDATE/EXTEND: add an assertion that the wrapper does not introduce new Python imports between `tools/` and `agent/`. The existing cycle guards stay.
- [ ] `tests/unit/test_pipeline_state_machine.py` — NO CHANGE. The classify_outcome → record_verdict in-process path is unchanged (the wrapper only matters for shell-out, not in-process).
- [ ] No DELETE dispositions. No REPLACE dispositions. All updates are additive coverage.

## Rabbit Holes

- **Building a generic CLI dispatcher in Python.** Tempting to make `sdlc-tool` a Python script that does its own argparse and routes to `tools.sdlc_*` directly. Avoid: that just moves the cwd problem from "where is `tools/`" to "where is the `sdlc-tool` Python script's interpreter and its sys.path." Bash + `uv run --directory` is the correct level of abstraction.
- **Editable install of the ai/ repo into target venvs.** Issue's Option (3); already ruled out. Don't revisit; the dep tree is too heavy.
- **Migrating `tools/sdlc_*` to a separate package on PyPI.** Real architectural cleanup but out of scope for this bug fix. If multi-repo usage grows, file as a future plan.
- **Renaming the underlying `tools.sdlc_*` modules to match wrapper subcommand names exactly.** Cosmetic; not worth the in-process import churn (`models/` and tests still import the old names).
- **Adding cwd detection in skill markdown** ("if cwd contains a target tools/ package, run wrapper, else run module directly"). Adds a branching condition to every skill site; wrapper makes the branch trivially uniform.

## Risks

### Risk 1: `~/.local/bin` not on PATH for some shell invocations
**Impact:** Wrapper not found; skills fall back to errors. Equivalent to today's failure mode for those sessions.
**Mitigation:** Update-system verify step asserts `~/.local/bin` is on PATH and that `sdlc-tool` resolves to the project copy. Skill markdown can fall back to `${HOME}/.local/bin/sdlc-tool` (absolute path) on machines where PATH inheritance is suspect — but the default invocation stays bare `sdlc-tool`. Document the PATH requirement in the new feature doc.

### Risk 2: Stale `python -m tools.X` invocations missed during the sweep
**Impact:** Partial fix; some skills continue to oscillate.
**Mitigation:** The new parity test (`tests/unit/test_sdlc_tool_wrapper.py` parity sweep) is a hard assertion: any `python -m tools.sdlc_*` reference outside the wrapper itself, the underlying tool modules, and tests/docs makes the suite fail. CI gates the merge.

### Risk 3: `AI_REPO_ROOT` misconfigured on a remote machine
**Impact:** Wrapper exits 2 with a clear error; SDLC tooling unreachable until fixed.
**Mitigation:** Default `$HOME/src/ai` is correct for every Valor machine today. The `update-system verify` step on each machine catches this at update time; it does not first surface as a `/sdlc` failure mid-pipeline.

### Risk 4: `uv` removed/missing on a remote
**Impact:** Wrapper fails with "uv: command not found." Same impact as today's `python -m tools.X` failure.
**Mitigation:** `uv` is already a hard dependency of the update system; if it's missing, broader breakage is in play. Update-system verify already checks `uv`. No new exposure introduced.

### Risk 5: Loud verdict failures surface noise the operator finds annoying
**Impact:** Operators see verdict failures (e.g., transient Redis blips) reported in their session logs.
**Mitigation:** This is the *intended* design — failures must be visible. The wrapper writes the underlying error to stderr and exits non-zero; skill markdown surfaces the line. If genuine transient noise becomes a problem, add a single-retry policy to `tools.sdlc_verdict.record` itself (not in the wrapper) so the loud failure is reserved for sustained issues.

### Risk 6: Hardlink sync race during update
**Impact:** Update step runs the new hardlink before the bridge restart; if the wrapper is broken, /sdlc breaks until the next update succeeds.
**Mitigation:** Add a verify step that exec's `sdlc-tool stage-query --issue-number 0` and asserts a parseable JSON or `unavailable` response before declaring the update complete. The existing `bridge config validation` gate at update Step 4.6 is the correct precedent.

## Race Conditions

No race conditions identified — the wrapper is a synchronous bash script that exec's a single Python subprocess. There is no shared state, no concurrent invocation pattern (skills shell out one command at a time), and no mutable global state owned by the wrapper. The underlying `tools.sdlc_verdict` already has its own race protection (Redis-backed `AgentSession` writes; same model that pre-existed PR #1044).

## No-Gos (Out of Scope)

- **Refactoring `tools.sdlc_*` modules** to share a common entry point or to be packaged as a separate distribution. Cosmetic; not relevant to the bug.
- **Changing the verdict shape, the artifact-hash semantics, or the Guard G1-G5 logic.** These were settled in PR #1044 and remain correct.
- **Adding new SDLC tools or new pipeline stages.** Wrapper is wired for the five existing modules; adding a sixth is a one-line edit but not part of this fix.
- **Deprecating local `/sdlc` workflows in favor of bridge-only PM sessions.** Issue's Option (4); ruled out — incompatible with current usage.
- **Loud failures for stage-markers or session-ensure.** These remain best-effort and silent. Only verdict-record and dispatch-record become loud.
- **Generic exit-policy framework for arbitrary `python -m tools.X` invocations.** The wrapper is intentionally narrow to SDLC tools; broader CLI tooling unification is a separate scope.

## Update System

The update system requires three changes:

- `scripts/update/hardlinks.py` — extend the hardlink set to include `scripts/sdlc-tool` → `~/.local/bin/sdlc-tool`. Reuse the existing `_ensure_hardlink` helper and stale-cleanup logic. Add `("scripts", "sdlc-tool")` to a new "scripts" sync section, mirroring the existing skills/commands/agents pattern.
- `scripts/update/verify.py` — add a check that `command -v sdlc-tool` resolves and that `sdlc-tool stage-query --issue-number 0` returns a parseable JSON (`{}` is acceptable; `unavailable` is acceptable; no `ModuleNotFoundError` or non-zero exit beyond exit 2 from the wrapper itself).
- `scripts/update/run.py` — add a step (between hardlinks and bridge restart) that invokes the new verify check; on failure, log the error and skip the bridge restart, the same way bridge config validation does today.

Existing installations get the wrapper on the next `/update` run. No manual migration steps required.

## Agent Integration

- The bridge (`bridge/telegram_bridge.py`) does NOT directly invoke `python -m tools.sdlc_*`. It spawns PM sessions via `sdk_client.py`, which sets `GH_REPO` and `SDLC_TARGET_REPO` env vars and lets the skill markdown drive the shell-outs. The fix at the wrapper layer covers bridge-spawned PM sessions automatically.
- `config/personas/project-manager.md` references `python -m tools.sdlc_stage_query` in instruction text (lines 21, 558). UPDATE these to instruct the persona to run `sdlc-tool stage-query` instead. Treat as instruction text only — not executed by the bridge.
- `.claude/hooks/post_compact.py:131` emits the literal `python -m tools.sdlc_stage_query --issue-number {issue_number}` to the agent at compaction time. UPDATE the emitted text to instruct `sdlc-tool stage-query --issue-number {issue_number}` so the post-compact nudge points the agent at the correct command.
- No `.mcp.json` changes. No new MCP server. The wrapper is a shell command surfaced via Claude Code's bash tool; it is not an agent-tool surface.
- Integration verification: a test case in `tests/integration/` that spawns a target-cwd subprocess and asserts the wrapper plus skill markdown round-trip records a verdict the next `sdlc-tool stage-query` reads back. Out of scope for this change if it requires a live Redis fixture; defer to existing `tests/unit/test_sdlc_verdict.py` which mocks the Redis layer.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/sdlc-tool-resolver.md` describing: why the wrapper exists (cwd-shadowing failure mode), how `AI_REPO_ROOT` is resolved, the loud-vs-silent exit policy per subcommand, and how to add a new subcommand.
- [ ] Add an entry to `docs/features/README.md` under the SDLC section, cross-linking to `sdlc-tool-resolver.md` and to the existing `sdlc-router-oscillation-guard.md`.

### External Documentation Site

- N/A. This repo does not publish to a documentation site.

### Inline Documentation

- [ ] `scripts/sdlc-tool` opens with a 5-line header: purpose, why bash, where AI_REPO_ROOT comes from, where to add a new subcommand. Per repo policy on minimal comments — only enough WHY context for the next reader.
- [ ] Update `CLAUDE.md` "Quick Commands" table to list `sdlc-tool` as the canonical entry point for SDLC tooling, with one example each for stage-query and verdict.
- [ ] Update `.claude/skills/sdlc/SKILL.md` "Cross-Repo Resolution" section to document the third resolution mechanism (`AI_REPO_ROOT` for tool dispatch, alongside `SDLC_TARGET_REPO` and `GH_REPO`).

## Success Criteria

- [ ] Running `/sdlc {issue}` from a target-repo cwd (e.g. `~/src/cuttlefish`) successfully records a critique verdict that `sdlc-tool stage-query` reads back. (From issue acceptance.)
- [ ] After `/do-plan-critique` returns, `sdlc-tool stage-query --issue-number {N}` shows `_verdicts["CRITIQUE"]` populated with `verdict`, `recorded_at`, and `artifact_hash`. (From issue acceptance.)
- [ ] Guard G5 fires: re-running `/sdlc {N}` against an unchanged plan reuses the cached verdict instead of re-dispatching `/do-plan-critique`. (From issue acceptance.)
- [ ] Row 4b → Row 4c transition works end-to-end from a local /sdlc invocation: critique with concerns → /do-plan revision (sets `revision_applied: true`) → next /sdlc dispatches /do-build. (From issue acceptance.)
- [ ] Same fix covers `/do-pr-review`'s `_verdicts["REVIEW"]` recording. (From issue acceptance.)
- [ ] `tools.sdlc_verdict record` failures are loud: skill markdown no longer wraps verdict invocations in `2>/dev/null || true`; non-zero exit reaches the operator session log. (From issue: explicitly requested.)
- [ ] Parity sweep test (`tests/unit/test_sdlc_tool_wrapper.py`) passes: zero `python -m tools.sdlc_*` references remain in `.claude/skills/**`, `.claude/hooks/post_compact.py`, or `config/personas/project-manager.md`.
- [ ] `scripts/update/run.py` `--dry-run` reports the new `sdlc-tool` hardlink and verify step.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.

### Team Members

- **Builder (wrapper-and-skills)**
  - Name: `tool-resolver-builder`
  - Role: Author the bash wrapper, edit skill markdown / hook / persona files, extend the update system, write the parity tests.
  - Agent Type: builder
  - Resume: true

- **Validator (parity sweep)**
  - Name: `tool-resolver-validator`
  - Role: Verify zero `python -m tools.sdlc_*` references remain outside the wrapper/tests/docs; verify the `update verify` step passes; smoke-test the wrapper from a simulated target-repo cwd.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `tool-resolver-docs`
  - Role: Create `docs/features/sdlc-tool-resolver.md`, update `docs/features/README.md`, update `CLAUDE.md` Quick Commands, update `.claude/skills/sdlc/SKILL.md` Cross-Repo Resolution section.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Author the wrapper script
- **Task ID**: build-wrapper
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_tool_wrapper.py` (create)
- **Assigned To**: tool-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/sdlc-tool` (bash, `set -euo pipefail`, executable bit set).
- Resolve `AI_REPO_ROOT` with default `$HOME/src/ai`; exit 2 if it doesn't contain `tools/`.
- Map kebab-case subcommand to `tools.sdlc_<name>` against an explicit allowlist (`verdict`, `dispatch`, `stage-marker`, `stage-query`, `session-ensure`).
- Exec `uv run --directory "$AI_REPO_ROOT" python -m "tools.sdlc_$module" "$@"` and pass through exit code.
- Fail with usage message on unknown or missing subcommand.

### 2. Sweep skill markdown
- **Task ID**: build-skill-sweep
- **Depends On**: build-wrapper
- **Validates**: parity check inside `tests/unit/test_sdlc_tool_wrapper.py`
- **Assigned To**: tool-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace every `python -m tools.sdlc_*` invocation in `.claude/skills/**` with `sdlc-tool <subcommand> ...`.
- Preserve `2>/dev/null || true` on stage-marker and session-ensure invocations.
- REMOVE `2>/dev/null || true` (and any other silencing) on verdict-record and dispatch-record invocations.
- Update `.claude/hooks/post_compact.py:131` emitted text to reference `sdlc-tool stage-query`.
- Update `config/personas/project-manager.md:21,558` instruction text to reference `sdlc-tool stage-query`.

### 3. Extend update system
- **Task ID**: build-update-system
- **Depends On**: build-wrapper
- **Validates**: `tests/unit/test_update_hardlinks.py` (UPDATE)
- **Assigned To**: tool-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `scripts/sdlc-tool` to `scripts/update/hardlinks.py` sync set; target is `~/.local/bin/sdlc-tool`.
- Add a verify check in `scripts/update/verify.py` that `command -v sdlc-tool` resolves and that `sdlc-tool stage-query --issue-number 0` returns parseable JSON.
- Wire the new verify step into `scripts/update/run.py` between hardlinks and the bridge restart, gating restart on success.

### 4. Author the parity + wrapper tests
- **Task ID**: build-tests
- **Depends On**: build-wrapper, build-skill-sweep
- **Validates**: itself
- **Assigned To**: tool-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_sdlc_tool_wrapper.py` with: (a) wrapper exits 2 on bad `AI_REPO_ROOT`; (b) wrapper exits 2 on unknown subcommand; (c) subprocess from a tmp cwd containing a fake `tools/__init__.py` invokes `sdlc-tool stage-query --issue-number 99999` and succeeds; (d) parity sweep — fail if any `python -m tools.sdlc_*` remains in `.claude/skills/**`, `.claude/hooks/post_compact.py`, or `config/personas/project-manager.md`.
- UPDATE `tests/unit/test_sdlc_stage_marker.py`, `tests/unit/test_sdlc_verdict.py`, `tests/unit/test_pm_session_permissions.py` per the Test Impact section.

### 5. Validate the build
- **Task ID**: validate-build
- **Depends On**: build-tests, build-update-system
- **Assigned To**: tool-resolver-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdlc_tool_wrapper.py tests/unit/test_sdlc_stage_marker.py tests/unit/test_sdlc_verdict.py tests/unit/test_pm_session_permissions.py -v`.
- Run `pytest -m sdlc` to verify the SDLC test suite stays green.
- Smoke test: `cd /tmp && AI_REPO_ROOT=$HOME/src/ai sdlc-tool stage-query --issue-number 99999` exits 0 with parseable JSON.
- Smoke test: `cd /tmp && AI_REPO_ROOT=/nonexistent sdlc-tool stage-query --issue-number 99999` exits 2 with the expected error.
- Confirm zero `python -m tools.sdlc_*` references remain outside the wrapper / tools dir / tests / docs.

### 6. Document the feature
- **Task ID**: document-feature
- **Depends On**: validate-build
- **Assigned To**: tool-resolver-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-tool-resolver.md`.
- Update `docs/features/README.md` index.
- Update `CLAUDE.md` Quick Commands table.
- Update `.claude/skills/sdlc/SKILL.md` Cross-Repo Resolution section.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: tool-resolver-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` (full suite).
- Run `python -m ruff check .` and `python -m ruff format --check .`.
- Confirm all Success Criteria checkboxes are demonstrably met.
- Generate final pass/fail report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Wrapper installed | `command -v sdlc-tool` | exit code 0 |
| Wrapper resolves | `sdlc-tool stage-query --issue-number 0` | exit code 0 |
| No bare invocations remain | `grep -rn 'python -m tools.sdlc_' .claude/skills/ .claude/hooks/post_compact.py config/personas/project-manager.md` | exit code 1 |
| Update verify clean | `python -m scripts.update.run --dry-run` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Wrapper location.** The plan ships the wrapper to `~/.local/bin/sdlc-tool`. Alternative: `~/.claude/bin/sdlc-tool` (PATH-independent, addressed via absolute reference in skill markdown). `~/.local/bin` is consistent with existing wrappers (`valor-history`, `aider`, `claude`, `officecli`) and Valor's PATH already includes it. Confirm the choice or call out a different preferred location.
2. **AI_REPO_ROOT default.** Defaulting to `$HOME/src/ai` matches every Valor machine today. Confirm there is no machine where the ai/ repo lives elsewhere; if so, document the override path.
3. **Loud-vs-silent boundaries.** The plan makes verdict-record and dispatch-record loud; stage-marker, session-ensure stay silent; stage-query stays graceful (returns `unavailable`). Confirm that's the right boundary, or extend loud-mode to other tools.
4. **Test coverage of the integration end-to-end (live Redis).** The plan defers a live-Redis round-trip to existing test infrastructure (mocked). If a live integration test is required for sign-off, add it as a separate task in Step 5.
