---
status: Complete
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-16
tracking: https://github.com/tomcounsell/ai/issues/1714
last_comment_id:
revision_applied: true
---

# Triage-First CRITIQUE: Consolidate the War Room, Add Crash-Resume, Kill the Re-Run Amplifiers

## Problem

The `/do-plan-critique` stage routinely takes **2+ hours**, and a minor mid-run failure (one crashed critic sub-agent, an interrupted Claude Code session) **discards all progress and restarts the entire war room from zero**. It is the slowest, most failure-prone stage in the SDLC pipeline, and it applies the same heavyweight scrutiny to a one-line docstring fix as to a doctrine rewrite.

**Current behavior:**
- 7 base critics (`CRITICS.md`), amplified by an Implementation-Note re-run loop (Step 4), bounded re-dispatch (Step 3.5, ≤2/critic), and `MAX_CRITIQUE_CYCLES = 2` — the real agent count is 7 × re-runs × cycles, visibly 20+ in logs.
- No resume: `SKILL.md:151-157` mints a fresh `date +%s%N` run dir every invocation and `mkdir`s it **without** `-p` specifically to forbid reuse. A crash restarts every critic.
- Scrutiny depth is self-selected by the plan author via the `appetite:` frontmatter field — the author of the plan decides how much review their own plan gets.

**Desired outcome:**
- An **independent triage step** assesses each plan and routes it to a **LITE** (one consolidated critic) or **FULL** (consolidated war room) path — the plan author's `appetite` is an input, not the deciding vote.
- The FULL roster is **3 merged critics** dispatched in a single parallel message; the Implementation-Note re-run loop is **deleted** (notes are produced in the critic's first pass).
- A crashed critique **resumes** from the surviving run dir, re-dispatching only the missing critics, guarded against stale-plan reuse.

## Freshness Check

**Baseline commit:** `042fb4730`
**Issue filed at:** 2026-06-16T07:20:41Z (same day, hours earlier)
**Disposition:** Unchanged

**File:line references re-verified (during issue recon + spikes, same session):**
- `do-plan-critique/SKILL.md:151-157` — fresh-run-dir mint, `mkdir` without `-p` — **still holds**.
- `do-plan-critique/SKILL.md` Step 4 sub-bullet 5 (lines ~277-280) — Implementation-Note re-run loop — **still holds**.
- `agent/pipeline_graph.py:35` — `MAX_CRITIQUE_CYCLES = 2` — **still holds**.
- `tools/critique_roster_check.py` — roster-name-agnostic gate — **confirmed by prototype spike** (N=1/3/7 identical code path).
- `tools/sdlc_verdict.py` `compute_plan_hash()` / `_compute_artifact_hash()` — sha256 of plan file, used by G5 — **confirmed by code-read spike**.

**Cited sibling issues/PRs re-checked:**
- #1628 (effort-tiering E1–E5) — open; this plan is the CRITIQUE-only slice, explicitly out of its scope.
- PR #1704 / #1690 (artifact-based roster barrier) — merged; this plan builds directly on it.

**Active plans in `docs/plans/` overlapping this area:** `pm-skips-critique-and-review.md` (status: Done) and `sdlc-plan-critique-revision.md` (status: docs_complete) — both shipped, no active overlap.

**Notes:** No drift. All premises verified within the same session the plan was written.

## Prior Art

- **PR #1704 (issue #1690)**: "do-plan-critique: artifact-based roster barrier for war-room critics" — replaced fire-and-forget `run_in_background` spawn + prose-await with a filesystem membership barrier: each critic atomically writes `{name}.result.md` ending in a two-line terminal fence; synthesis gates on `critique-roster-check` against a frozen `_roster.json`. **This is the foundation this plan extends** — resume reuses the surviving run dir + result files; consolidation just changes which names go in `_roster.json`.
- **`sdlc-plan-critique-revision.md` (issue #779)**: added the Propagation Check and the revision pass — established that critique findings carry Implementation Notes. This plan moves the note requirement *into the critic's first pass* rather than enforcing it via a re-run loop.
- **`pm-skips-critique-and-review.md` (issue #791)**: hardened the pipeline so CRITIQUE can't be silently skipped. The triage LITE path here is **not** a skip — it still runs a critic and records a verdict; it changes *roster size*, not whether the gate runs.

No prior attempt has tried to consolidate the roster or add resume. This is greenfield on top of #1690.

## Research

No relevant external findings — this is a change to a bespoke in-repo SDLC skill (`.claude/skills-global/do-plan-critique/`). No external libraries, APIs, or ecosystem patterns are involved. Proceeding with codebase context.

## Spike Results

### spike-1: Resume insertion points and re-run-loop location (code-read)
- **Assumption**: "Resume and re-run-loop removal can be localized to a few SKILL.md steps without touching the gate tool."
- **Method**: code-read (`SKILL.md`, `CRITICS.md`, `tools/critique_roster_check.py`, `tools/sdlc_verdict.py`)
- **Finding**:
  - Run dir created in Step 3a (lines 151-157, no `-p`); read by critics (Step 3), the gate (Step 3.5), aggregation (Step 4); cleaned with `rm -rf` **only on the `complete: true` path** (Step 5.5/5.6) — **the incomplete path already PRESERVES the dir**. So a crashed run's artifacts already survive on disk; the only missing piece is *finding and reusing* that dir on the next invocation.
  - `tools/sdlc_verdict.py` exposes `compute_plan_hash(plan_path)` → `"sha256:<hex>"` (CRLF-normalized full plan file), already used by G5. Directly reusable as the stale-resume guard.
  - The Implementation-Note re-run lives in Step 4 sub-bullet 5 ("Re-run that critic with the finding and a directive to add a concrete Implementation Note"). Deleting those lines and moving the requirement into each critic's CRITICS.md prompt removes the loop while preserving the note.
- **Confidence**: high
- **Impact on plan**: Resume = a new "resume probe" before Step 3a + a `.plan_hash` written next to `_roster.json` + a per-critic skip-if-complete in dispatch. No gate-tool change.

### spike-2: Gate tool handles 1-name and 3-name rosters (prototype)
- **Assumption**: "`critique-roster-check` works unchanged for LITE (1 critic) and FULL (3 critics)."
- **Method**: prototype (synthetic `_roster.json` + fence'd result files in /tmp, ran the installed CLI)
- **Finding**: Confirmed. LITE `["Triage"]` → `complete:true` exit 0; FULL 3-name with spaces+`&` → `complete:true` exit 0; PARTIAL (2 of 3) → `complete:false`, `missing` names the third, exit 1. The tool maps a roster name to its result file as the **literal name**: `f"{name}.result.md"` (no slugging). Names with spaces and `&` work; the only hard constraint is **no `/`** (path separator) in a critic name.
- **Confidence**: high
- **Impact on plan**: Consolidation is purely a `_roster.json` content change. Keep critic names filename-safe (no `/`, no NUL). Exit codes are branchable: 0 = proceed, 1 = incomplete, 2 = bad manifest.

## Data Flow

1. **Entry point**: SDLC router dispatches `/do-plan-critique {issue-or-plan}` (CRITIQUE stage).
2. **Resume probe (NEW, Step 2b)**: `critique-resume-probe --plan PLAN --issue N` globs `.critique-runs/{issue-or-slug}-*`, returns the newest dir whose `.plan_hash` matches `compute_plan_hash(PLAN)` and whose gate is not yet `complete`. → reuse path, or empty → fresh path.
3. **Structural checks (Step 2)**: unchanged automated checks (sections, task integrity, references). Run regardless of LITE/FULL.
4. **Triage (NEW, Step 2.6)**: deterministic force-FULL guard (doctrine paths / Large appetite) → else a single cheap triage critic (one short-lived `sonnet` agent) → emits `LITE` or `FULL`. Skipped entirely on the resume path (the surviving `_roster.json` already encodes the chosen path).
5. **Roster freeze (Step 3a)**: writes `_roster.json` (1-name LITE or 3-name FULL) **and** `.plan_hash`. On resume, this dir already exists — skip mint.
6. **Dispatch (Step 3)**: dispatch in one parallel message **only** critics whose `{name}.result.md` is absent or fence-less. Each writes its result atomically with the terminal fence.
7. **Gate (Step 3.5)**: `critique-roster-check` membership barrier (unchanged); bounded re-dispatch of only missing names.
8. **Aggregate (Step 4)**: iterate `_roster.json`, read each result; Implementation-Note **validation-only** (malformed CONCERN/BLOCKER excluded + logged, never re-dispatched).
9. **Output**: verdict (`READY TO BUILD` / `NEEDS REVISION` / `MAJOR REWORK`) recorded (Step 5.5) — contract unchanged, router consumes via G1/G5.

## Architectural Impact

- **New dependencies**: none (new CLI reuses stdlib + existing `compute_plan_hash`).
- **Interface changes**: new CLI `critique-resume-probe`; `_roster.json` rosters become triage-selected (1 or 3 names) instead of fixed 6/7. The gate-tool contract is unchanged.
- **Coupling**: unchanged. LITE/FULL stays internal to the critique skill; `agent/sdlc_router.py` and `agent/pipeline_graph.py` have zero `appetite`/`tier` touchpoints and stay untouched. The verdict contract consumed by G1/G5 is preserved verbatim.
- **Data ownership**: run dir + `.plan_hash` owned by the critique skill, same as today's run dir.
- **Reversibility**: high — the change is concentrated in two markdown skill files + one small Python module + tests. Reverting restores the 7-critic flow.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1 (design already aligned in the originating conversation — triage-first, 3 merged critics, standalone-from-#1628)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo venv active | `python -c "import tools.sdlc_verdict"` | Resume probe imports `compute_plan_hash` |
| Gate CLI installed | `critique-roster-check --help` | Confirms `[project.scripts]` wiring present |

Run all checks: `python scripts/check_prerequisites.py docs/plans/triage_first_critique.md`

## Solution

### Key Elements

- **Resume probe** (`critique-resume-probe`): finds a reusable, non-stale, incomplete run dir for the current plan so a crash resumes instead of restarting.
- **Triage step**: an independent LITE/FULL decision (deterministic force-FULL overrides + one cheap classification) that takes scrutiny depth out of the plan author's hands.
- **Consolidated rosters** (`CRITICS.md`): one "Consolidated Critic" for LITE; three merged critics (Risk & Robustness, Scope & Value, History & Consistency) for FULL, each emitting per-lens sections to preserve depth.
- **First-pass Implementation Notes**: the note requirement lives in the critic prompt; Step 4 becomes validation-only. The re-run loop is deleted.

### Flow

CRITIQUE dispatched → **Resume probe** → (reusable dir? reuse it, skip triage) / (none? **Structural checks** → **Triage** → freeze LITE or FULL roster + plan-hash) → **dispatch only missing critics in parallel** → **roster gate** → **aggregate + validate notes** → **record verdict**

### Technical Approach

**1. New module `tools/critique_resume.py` → CLI `critique-resume-probe`.**
- Args: `--plan PATH`, `--issue N` (or `--slug S`), `--base-dir .critique-runs` (default).
- Computes `want = compute_plan_hash(plan)` (imported from `tools.sdlc_verdict`).
- Globs `{base}/{issue-or-slug}-*` newest-first (parse the trailing `%s%N` for ordering). For each:
  - read `{dir}/.plan_hash`; if it equals `want` AND the dir's gate is not complete → print `dir`, exit 0. **`critique_roster_check.evaluate(dir)` returns a `(dict, int)` tuple** (`tools/critique_roster_check.py:125`), so the guard is `decision, _rc = evaluate(dir); if not decision["complete"]:` — never `if not evaluate(dir):` (a non-empty tuple is always truthy).
  - if `.plan_hash` mismatches → it's stale; ignore (and emit its path on stderr so the skill can GC it).
- If none reusable → print nothing, exit 1.
- Register in `pyproject.toml [project.scripts]`: `critique-resume-probe = "tools.critique_resume:main"`.

**2. `SKILL.md` edits.**
- **New Step 2b "Resume Probe"** (before Step 2.6 triage / Step 3a): call `critique-resume-probe`. On a hit, set `CRITIQUE_RUN_DIR` to the returned dir, set `RESUMED=1`, and **skip triage + roster freeze** (the surviving `_roster.json` defines the path). GC any stale-hash sibling dirs reported on stderr.
- **New Step 2.6 "Triage"** (fresh path only):
  - Deterministic force-FULL if the change touches doctrine paths (`config/personas/`, `.claude/skills/`, `.claude/skills-global/`, `agent/sdlc_router.py`, `agent/pipeline_graph.py`, `.claude/hooks/`) or `appetite: Large`. No LLM needed for the override. (No plan-body size threshold — appetite + doctrine paths are the only overrides.)
  - Else a single cheap triage critic — one short-lived `sonnet` Agent — emitting `LITE` or `FULL` + a one-line reason, **biased to FULL on ambiguity**. A LITE vote never overrides a force-FULL. Keep it cheap: a short classification prompt, not a fourth heavyweight critic.
- **Step 3a (roster freeze)** becomes path-aware: LITE → `{"roster":["Consolidated Critic"],"count":1}`; FULL → `{"roster":["Risk & Robustness","Scope & Value","History & Consistency"],"count":3}`. Write `.plan_hash` (= `compute_plan_hash`) next to `_roster.json`. The fresh-mint `mkdir` keeps **no `-p`** (collision still fails loudly); reuse is the explicit Step-2b branch, never a silent `mkdir -p`. Replace the old 6-vs-7 "Small purely-internal skip" with the triage selection.
- **Step 3 (dispatch)**: dispatch **only** roster members whose `{name}.result.md` is absent or fails the terminal-fence check (skip already-complete ones). On a fresh run all are dispatched; on resume only the missing ones.
- **Step 4 sub-bullet 5**: delete the re-run directive; replace with validation-only — a CONCERN/BLOCKER missing its Implementation Note is reported malformed and excluded from the report (logged), never re-dispatched.
- Update the Step 5 report header (`**Critics**:` line) and Version history.

**3. `CRITICS.md` edits.**
- Replace the seven individual critic sections with: one **Consolidated Critic** (LITE) folding the highest-value lenses (failure modes + scope/value + internal consistency), and three **merged critics** (FULL):
  - *Risk & Robustness* = Skeptic + Adversary + Operator
  - *Scope & Value* = Simplifier + User
  - *History & Consistency* = Archaeologist + Consistency Auditor
- Each merged critic's prompt instructs it to emit a labeled sub-section per absorbed lens (preserves focus despite the merge) and to include a concrete **Implementation Note** on every BLOCKER/CONCERN as a condition of emission (downgrade to NIT or drop if it can't).
- Update "Critic Selection" to describe triage-driven LITE/FULL instead of the manual Small-skip.

**4. Constants.** `MAX_CRITIQUE_CYCLES` and the re-dispatch cap stay as-is: a *new plan version* changes the plan hash → fresh run (correct); resume operates within one plan version. No change to `agent/pipeline_graph.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/critique_resume.py` must not raise on a missing/garbage `.plan_hash`, an unreadable dir, or a malformed `_roster.json` — each maps to "not reusable" (exit 1), never a crash that the skill could misread as "no resume." Test asserts exit 1 + empty stdout for each.
- [ ] If `compute_plan_hash` returns `None` (unreadable plan), the probe treats every dir as non-matching (fresh run) rather than crashing — test asserts this.

### Empty/Invalid Input Handling
- [ ] Probe with no `.critique-runs` dir at all → exit 1, empty stdout (test).
- [ ] Probe with a dir present but **complete** gate → not reusable (don't resume a finished run) → exit 1 (test).
- [ ] Triage with an empty/whitespace doctrine-path list still force-FULLs correctly on a doctrine path (prose-invariant test that the force-FULL list is present in SKILL.md).

### Error State Rendering
- [ ] On the resume path, the skill must still record a verdict on every exit path (Step 5.5 unchanged) — prose-invariant test that Steps 5.5/5.6 are reached from both fresh and resume branches.
- [ ] Stale-hash dirs are GC'd or ignored, never silently resumed — test asserts a mismatched `.plan_hash` yields exit 1 (not exit 0).

## Test Impact

- [ ] `tests/unit/test_do_plan_critique_barrier.py::TestProseInvariants::test_run_dir_cleanup_gated_on_complete_and_preserved_on_incomplete` — UPDATE: assert the incomplete/preserve path is now *also* the resume source; cleanup semantics unchanged but prose moves.
- [ ] `tests/unit/test_do_plan_critique_barrier.py::TestProseInvariants::test_step_3a_freezes_roster_manifest_before_dispatch` — UPDATE: roster content is now triage-selected (1 or 3 names), not a fixed 6/7; keep the "freeze before dispatch" assertion.
- [ ] `tests/unit/test_do_plan_critique_barrier.py::TestOrderingAndAggregationInvariants::test_step_4_iterates_every_roster_member` — UPDATE: still valid; confirm it doesn't assert a hardcoded count of 7.
- [ ] `tests/unit/test_do_plan_critique_barrier.py::TestHelperBehavior::*` — KEEP (gate tool is unchanged; `test_under_dispatch_seven_five`'s synthetic 7-name roster still exercises the tool correctly). Add no changes.
- [ ] `tests/unit/test_do_plan_critique_barrier.py` — ADD new prose-invariant tests: (a) Step 2b resume probe present, (b) Step 2.6 triage + force-FULL doctrine list present, (c) Step 4 no longer contains a critic re-run directive, (d) `_roster.json` LITE/FULL shapes documented.
- [ ] `tests/unit/test_critique_resume.py` — CREATE: unit tests for the resume probe (match/mismatch/missing/complete/garbage cases, exit codes, GC-on-stderr).
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — No change: its `"7"` is a dispatch-row id, not a critic count; the router/guards/dispatch rows are untouched by this plan.

## Rabbit Holes

- **Building #1628's general E1–E5 tiering.** Out of scope. Reuse `appetite` + a binary LITE/FULL signal only.
- **An LLM "triage critic" that does deep analysis.** Triage must be *cheap* — a short classification, not a fourth heavyweight critic. If it grows, it reintroduces the cost it was meant to cut.
- **Refactoring `critique_roster_check.py`.** The spike proved it's already roster-agnostic. Do not touch it.
- **Perfecting merged-critic prompts to match 7-lens depth exactly.** Per-lens sub-sections are enough; chasing parity with the old roster is a tar pit.
- **Cross-session run-dir locking.** Two concurrent critiques of the same plan is not a real workflow (the PM session serializes stages). Don't build distributed locking; the plan-hash + newest-dir selection is sufficient.

## Risks

### Risk 1: Merged critics miss findings the 7 separate lenses would have caught
**Impact:** A real blocker slips past CRITIQUE into BUILD.
**Mitigation:** Each merged critic emits an explicit labeled sub-section per absorbed lens (forces it to actually apply each lens, not blur them). FULL remains the default for any non-trivial or doctrine-touching plan via the force-FULL override. The LITE path only ever runs on triage-confirmed small, non-doctrine changes.

### Risk 2: Stale-resume reuses a run dir whose plan changed
**Impact:** Critique findings reference an outdated plan; verdict is wrong.
**Mitigation:** `.plan_hash` (= `compute_plan_hash`, the same hash G5 trusts) gates reuse. Any plan edit changes the hash → fresh run. Mismatched dirs are ignored/GC'd, never resumed.

### Risk 3: Triage misclassifies a risky change as LITE
**Impact:** A change that deserved the war room gets one critic.
**Mitigation:** Deterministic force-FULL override (doctrine paths, Large appetite) runs *before* and *cannot be overridden by* the LLM triage; triage is biased to FULL on ambiguity. The author's `appetite` is an input, never the final say.

## Race Conditions

### Race 1: Resume probe reads a run dir mid-write by a still-live prior session
**Location:** `tools/critique_resume.py` glob + `.plan_hash` read; `SKILL.md` Step 2b.
**Trigger:** A previous critique session is still writing result files when a new invocation probes.
**Data prerequisite:** `.plan_hash` is written in Step 3a (atomic single-line write) before any critic is dispatched, so a probed dir either has a complete hash file or none.
**State prerequisite:** The PM session serializes SDLC stages — two concurrent critiques of the same issue is not a supported flow.
**Mitigation:** The probe only *reads*; it never deletes a matching dir. Result files use the existing atomic `.tmp`→rename, so a half-written result is observed as "missing" (fence absent) and simply re-dispatched, never corrupted. The terminal-fence completion check (unchanged) is the correctness barrier.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1628] General E1–E5 effort-tiering across the whole pipeline. This plan ships the CRITIQUE-only LITE/FULL slice and shapes its signal so #1628 can later generalize it.
- [SEPARATE-SLUG #1628] Tiering of REVIEW depth, cross-vendor review, or anti-criteria depth — all consume #1628's broader tier signal, not this critique-local one.

## Update System

The critique skill lives in `.claude/skills-global/do-plan-critique/`, which `/update` hardlinks to `~/.claude/skills/` on every machine via `scripts/update/hardlinks.py::sync_claude_dirs()` — editing the existing files needs no new sync wiring. The new `critique-resume-probe` CLI is added to `pyproject.toml [project.scripts]`; it becomes available on each machine through the normal `uv`/editable-install step already run by the update process. No `RENAMED_REMOVALS` entry is needed (no skill is renamed or moved between `skills/` and `skills-global/`). No new config files or secrets.

## Agent Integration

- **New CLI entry point:** `critique-resume-probe = "tools.critique_resume:main"` in `pyproject.toml [project.scripts]`. The critique skill invokes it via Bash (Step 2b), exactly as it already invokes `critique-roster-check`.
- **Bridge:** no change — the bridge never calls the critique internals directly; the skill is reached through the SDLC router as today.
- **MCP:** no new MCP server or `.mcp.json` change — this is a skill-internal CLI, not an agent-facing tool.
- **Integration test:** `tests/unit/test_critique_resume.py` invokes the installed CLI end-to-end (subprocess) to confirm the agent's Bash path works, mirroring how `test_do_plan_critique_barrier.py::test_cli_main_prints_json_and_returns_exit_code` exercises the gate CLI.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/` for the critique stage (search for an existing critique/SDLC feature doc; if none, add a short `docs/features/plan-critique-triage.md`) describing the LITE/FULL triage, the 3 merged critics, and crash-resume.
- [ ] Update `docs/sdlc/` per-stage CRITIQUE addendum if one exists (the skill reads `docs/sdlc/` at runtime).
- [ ] Add/refresh the entry in `docs/features/README.md` index if a new feature doc is created.

### Inline Documentation
- [ ] Module docstring for `tools/critique_resume.py` explaining the reuse + stale-guard contract (mirror the thoroughness of `critique_roster_check.py`'s header).
- [ ] Update the `do-plan-critique/SKILL.md` Version history block with the new behavior.

## Success Criteria

- [ ] A triage step routes each plan to LITE (1 consolidated critic) or FULL (3 merged critics); a deterministic force-FULL override fires on doctrine paths / Large appetite regardless of the author's `appetite`.
- [ ] The Implementation-Note re-run loop is removed from `SKILL.md` Step 4; notes are required in the critic's first pass (CRITICS.md).
- [ ] A crashed/interrupted critique resumes from the surviving run dir, re-dispatching only missing critics; a mismatched `.plan_hash` forces a fresh run.
- [ ] `critique-roster-check` passes unchanged for the 1-name LITE and 3-name FULL rosters (regression test).
- [ ] The verdict contract (`READY TO BUILD` / `NEEDS REVISION` / `MAJOR REWORK`) and router consumption (G1/G5) are unchanged; `agent/sdlc_router.py` and `agent/pipeline_graph.py` are untouched.
- [ ] `critique-resume-probe` is wired into `pyproject.toml [project.scripts]` and invoked from SKILL.md Step 2b.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `SKILL.md` Step 2b references `critique-resume-probe`.

## Team Orchestration

### Team Members

- **Builder (resume-tool)**
  - Name: `resume-builder`
  - Role: implement `tools/critique_resume.py` + `pyproject.toml` script entry + unit tests
  - Agent Type: builder
  - Resume: true

- **Builder (skill-prose)**
  - Name: `skill-builder`
  - Role: edit `do-plan-critique/SKILL.md` (Steps 2b, 2.6, 3a, 3, 4, 5, version history) + `CRITICS.md` (consolidated rosters, first-pass Implementation Note) + prose-invariant tests
  - Agent Type: builder
  - Resume: true

- **Validator (critique)**
  - Name: `critique-validator`
  - Role: verify rosters flow through the gate, resume probe behaves on all cases, no re-run directive remains, router untouched
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `critique-doc`
  - Role: feature + inline docs
  - Agent Type: documentarian
  - Resume: true

### 1. Build resume probe CLI
- **Task ID**: build-resume-tool
- **Depends On**: none
- **Validates**: `tests/unit/test_critique_resume.py` (create)
- **Informed By**: spike-1 (compute_plan_hash reusable; incomplete path already preserves dir), spike-2 (gate is roster-agnostic; evaluate() reusable to check completeness)
- **Assigned To**: resume-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/critique_resume.py` with `main()` implementing the probe (glob newest-first, `.plan_hash` match via `tools.sdlc_verdict.compute_plan_hash`, completeness check via `decision, _rc = critique_roster_check.evaluate(dir); not decision["complete"]`, GC-stale on stderr).
- Register `critique-resume-probe` in `pyproject.toml [project.scripts]`.
- Write `tests/unit/test_critique_resume.py` covering match/mismatch/missing/complete/garbage/None-hash cases + CLI subprocess.

### 2. Build skill-prose changes
- **Task ID**: build-skill-prose
- **Depends On**: none
- **Validates**: `tests/unit/test_do_plan_critique_barrier.py`
- **Informed By**: spike-1 (exact step locations + re-run-loop lines), spike-2 (roster naming: no `/`, spaces/`&` OK)
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `SKILL.md`: add Step 2b (resume probe), Step 2.6 (triage + force-FULL list), make Step 3a path-aware + write `.plan_hash`, dispatch only-missing in Step 3, delete Step 4 re-run sub-bullet (validation-only), update Step 5 header + version history.
- Edit `CRITICS.md`: replace 7 critics with 1 Consolidated (LITE) + 3 merged (FULL), per-lens sub-sections, first-pass Implementation Note requirement; rewrite "Critic Selection" for triage.
- Update affected prose-invariant tests + add new ones (Step 2b present, triage present, no re-run directive, LITE/FULL roster shapes).

### 3. Validate
- **Task ID**: validate-critique
- **Depends On**: build-resume-tool, build-skill-prose
- **Assigned To**: critique-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_critique_resume.py tests/unit/test_do_plan_critique_barrier.py -q`.
- Confirm `git diff --name-only` does NOT include `agent/sdlc_router.py` or `agent/pipeline_graph.py`.
- Manually exercise `critique-resume-probe` against a synthetic `.critique-runs` tree (match + stale + complete) and confirm exit codes.
- Report pass/fail.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-critique
- **Assigned To**: critique-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create/update the critique feature doc + `docs/sdlc/` CRITIQUE addendum; refresh the features index.
- Ensure module + SKILL.md version-history docs are present.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-resume-tool, build-skill-prose, validate-critique, document-feature
- **Assigned To**: critique-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table.
- Confirm every Success Criterion, including docs.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Critique tests pass | `pytest tests/unit/test_critique_resume.py tests/unit/test_do_plan_critique_barrier.py -q` | exit code 0 |
| Resume CLI wired | `critique-resume-probe --help` | exit code 0 |
| SKILL invokes probe | `grep -c "critique-resume-probe" .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| Re-run loop gone | `grep -c "Re-run that critic" .claude/skills-global/do-plan-critique/SKILL.md` | output contains 0 |
| Router untouched | `git diff --name-only origin/main -- agent/sdlc_router.py agent/pipeline_graph.py` | exit code 0 |
| Lint clean | `python -m ruff check tools/critique_resume.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/critique_resume.py` | exit code 0 |

## Critique Results

<!-- LIGHT critique (single Consolidated Critic, dogfooding the plan's own LITE path) — 2026-06-16. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Consolidated (Risk&Robustness) | Plan cited `agent.sdlc_verdict` but module is `tools/sdlc_verdict.py`; the Prerequisites import check and `critique_resume.py` import would fail on first install. | FIXED: all 6 refs + prereq check updated to `tools.sdlc_verdict`. Verified `from tools.sdlc_verdict import compute_plan_hash` succeeds. | Signature `compute_plan_hash(plan_path: Path \| str) -> str \| None` at `tools/sdlc_verdict.py:94` — drop-in; only the import path was wrong. |
| CONCERN | Consolidated (Risk&Robustness) | `critique_roster_check.evaluate()` returns `(dict, int)`, not a bool; a literal `if not evaluate(dir):` is always truthy. | FIXED: Technical Approach §1 + Task 1 now specify `decision, _rc = evaluate(dir); if not decision["complete"]:`. | `evaluate(run_dir: str) -> tuple[dict, int]` at `tools/critique_roster_check.py:125`; dict key `"complete": bool`. |

---

## Resolved Design Decisions

All design decisions were resolved with the supervisor before finalizing (recorded here so critique has the rationale):

1. **Roster consolidation** — 7 → 3 merged critics for FULL, 1 consolidated critic for LITE. (The real fan-out is 20+, not 7; the dominant fixes are resume + deleting the re-run loop.)
2. **LITE trigger** — an independent triage step assesses each plan; `appetite` is an input, not the deciding vote ("not up to the planner"). Deterministic force-FULL override on doctrine paths / Large appetite.
3. **Relationship to #1628** — ship standalone now, reusing `appetite`; shape the LITE/FULL signal so #1628 can later generalize it.
4. **Triage mechanism** — a single cheap `sonnet` triage Agent (one short-lived classification call), not inline driver classification.
5. **Force-FULL overrides** — doctrine paths + Large appetite only. No plan-body size threshold.
