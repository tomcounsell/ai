---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-05-01
tracking: https://github.com/tomcounsell/ai/issues/1187
issue: "#1187"
last_comment_id:
revision_applied: true
---

# Per-Project Iteration for 5 Single-Repo Audit Reflections + Dashboard Breakdown

## Problem

Five recurring audit reflections are hardcoded to scan only the AI repo (`~/src/ai`), even though the same audit logic applies to any project with the relevant artifacts on disk. On machines hosting multiple projects, they silently ignore everything except the AI repo.

**Current behavior:**
- `tech-debt-scan`, `documentation-audit`, `skills-audit`, `hooks-audit`, and `feature-docs-audit` each hardcode `PROJECT_ROOT` (the AI repo root) as the only scan target.
- On `Valor the Cowboy` — which also has `~/src/popoto` checked out — these five audits never scan `popoto`.
- The reflection modal at `localhost:8500` shows a flat history table with no way to tell which projects were scanned (today there is only one, so this distinction doesn't matter — but after this fix it will).

**Desired outcome:**
- Each of the five audits iterates `load_local_projects()` and runs once per project with the required artifact on disk.
- Projects lacking the required artifact are silently skipped — not counted as failures.
- The reflection modal renders a per-project breakdown for the most recent run when per-project data is available.

## Freshness Check

**Baseline commit:** `6ed64d53dc6dc8a6fbe0929c7a295fb6860fe78f`
**Issue filed at:** 2026-04-28T05:05:07Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `reflections/maintenance.py:30,49` — `PROJECT_ROOT` passed to subprocess grep calls — still holds at lines 30 and 49 in `run_legacy_code_scan`
- `reflections/auditing.py:298` — `DocsAuditor(repo_root=PROJECT_ROOT)` — drifted: now at line 465, but claim still holds
- `reflections/auditing.py:340,352` — `PROJECT_ROOT` in `run_skills_audit` — drifted to lines 507, 519; still holds
- `reflections/auditing.py:394,414,435` — `PROJECT_ROOT` in `run_hooks_audit` — drifted to lines 561, 581, 602; still holds
- `reflections/auditing.py:460,523` — `PROJECT_ROOT` in `run_feature_docs_audit` — drifted to lines 627, 690; still holds
- `reflections/utils.py:37` — `load_local_projects()` — still at line 37; unchanged
- `models/reflection.py:85-120` — `mark_completed()` — confirmed; signature takes `(duration, error=None)` only, no `projects` kwarg yet
- `agent/reflection_scheduler.py` — `execute_function_reflection` discards the callable return value; mark_completed called at lines 319, 328, 338

**Cited sibling issues/PRs re-checked:**

- #561 — Merged 2026-03-30; added `run_pr_review_audit` as canonical per-project pattern; still the reference implementation
- #978 — Closed 2026-04-22; reflections tidy-up / naming convention — landed, not blocking
- #1028 — Still OPEN: reflections modularization into one-file-per-reflection. This plan touches the same bundle files (`reflections/auditing.py`, `reflections/maintenance.py`). Coordination signal: if #1028 ships first and moves these functions, the builder should target the new locations. For now, proceed against the current layout.
- #1132 — Closed 2026-04-22; scrubbed monolith-migration annotations — no impact

**Commits on main since issue was filed (touching referenced files):**

- `96449ac5` feat(reflections): daily-log-review sends summary to Telegram (#1188) (#1230) — irrelevant, touched `run_log_review` (already per-project), not the five audits

**Active plans in `docs/plans/` overlapping this area:** `reflections-modular.md` (tracking #1028) — touches same bundle files. Not blocking; coordinate if #1028 lands first.

**Notes:** All line number citations in the issue have drifted by ~60 lines since the issue was filed (result of #1188/#1230 landing), but every claim about hardcoded `PROJECT_ROOT` still holds. No root-cause changes.

## Prior Art

- **PR #561** — "Add PR review audit reflection step (step 20)" — Introduced `run_pr_review_audit` with canonical per-project `for project in load_local_projects()` iteration and `[slug]` prefixed findings. This is the reference implementation for the pattern this issue generalizes.
- **Issue #978** (closed) — Reflections tidy-up; established naming conventions and scheduler placement. No code overlap.
- No prior attempts to generalize the 5 single-repo audits were found.

## Research

No relevant external findings — proceeding with codebase context and training data. This work is purely internal: extending Python callables in `reflections/`, a model method in `models/reflection.py`, the scheduler in `agent/reflection_scheduler.py`, and a Jinja2 template in `ui/templates/`. No external libraries are involved.

## Spike Results

No spikes needed. All assumptions were validated via direct code-read:

1. `execute_function_reflection` in `agent/reflection_scheduler.py` (lines 248-263) calls the callable but **discards its return value** — confirmed. The scheduler must be updated to capture the result dict and forward `projects` to `mark_completed`.
2. `DocsAuditor` in `scripts/docs_auditor.py` already accepts `repo_root=` as a constructor parameter — confirmed. `run_documentation_audit` just needs to loop over projects and pass each project's working directory as `repo_root`.
3. `run_skills_audit` hardcodes `cwd=str(PROJECT_ROOT)` and builds the audit script path from `PROJECT_ROOT / ".claude/skills/..."` — confirmed. For per-project iteration, the target repo's copy of the script must be invoked (or skipped if absent).
4. `load_local_projects()` already filters to only projects whose `working_directory` exists on disk — confirmed. No additional machine-scoping logic is needed.

## Data Flow

For one per-project audit call (e.g., `run_hooks_audit`):

1. **Entry point**: `reflection_scheduler.py::execute_function_reflection` calls the registered callable (e.g., `reflections.auditing.run_hooks_audit`)
2. **`run_hooks_audit()`** calls `load_local_projects()` → gets list of projects on this machine → iterates; for each project, evaluates skip predicate, runs the audit body with the project's `repo_root`, prefixes findings with `[slug]`
3. **Aggregate result dict** returned: `{status, findings: ["[ai] ...", "[popoto] ..."], summary: "...", projects: [{slug, status, duration, findings_count, error}, ...]}`
4. **`execute_function_reflection`** captures the return value and returns it to `run_reflection`
5. **`run_reflection`** extracts `result.get("projects", [])` and calls `state.mark_completed(duration, projects=projects_list)`
6. **`Reflection.mark_completed()`** appends `{timestamp, status, duration, error, projects: [...]}` to `run_history`
7. **Dashboard modal** (`/reflection/{name}/modal-content`): `get_run_history()` returns run dicts including the `projects` list; the template renders a per-project sub-table when `run.projects` is non-empty

## Architectural Impact

- **Interface changes**: `mark_completed(duration, error=None)` → `mark_completed(duration, error=None, projects=None)`. Backward-compatible — all existing callers omitting `projects` see no behavior change.
- **New coupling**: `execute_function_reflection` now captures and surfaces the callable's return value. Previously it was fire-and-forget. This is a one-directional tightening — the scheduler gets richer data from audit functions that opt in, while non-audit functions return `None` which is safely ignored.
- **Data ownership**: Per-project sub-results live inside each `run_history` record; no new top-level fields or separate lists. The 200-record cap stays clean.
- **Reversibility**: Fully additive. Remove the `projects` kwarg from `mark_completed` and the `projects` key from run records to revert. Old run records without a `projects` key render as before (template guards with `{% if run.projects %}`).
- **Timeout amplification (multi-project)**: Per-project iteration multiplies wall-clock budget by the number of qualifying projects. The default `DEFAULT_FUNCTION_TIMEOUT=1800s` (30 min) in `agent/reflection_scheduler.py` was sized for single-repo audits. For 5-20 local projects the worst case (`tech-debt-scan`: 4 grep × N projects × ~30s; `documentation-audit`: Anthropic API per-doc-file × N projects) blows past 30 min. Mitigation: explicit `timeout:` overrides in `config/reflections.yaml` per the budget table below — see Solution → Timeout Budgets.
- **Anthropic spend amplification**: `documentation-audit` calls Anthropic per documentation file (`max_api_calls=50` per project default). Multiplying by N projects multiplies steady-state spend 5-10×. Mitigation: per-project hard cap on `max_api_calls` and an aggregate global cap honored by `run_per_project_audit` — see Solution → Cost Controls.
- **DocsAuditor schedule gate is global, not repo-scoped**: `scripts/docs_auditor.py::_load_state` (line 1012) reads `docs_auditor:last_audit_date` as a plain Redis key with no project key suffix. Under per-project iteration, the FIRST project to record its date will suppress every subsequent project for 7 days. Mitigation: scope the state key per repo — see Solution → DocsAuditor Repo-Scoped State.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment before build)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `load_local_projects()` returns projects | `python -c "from reflections.utils import load_local_projects; ps = load_local_projects(); print(len(ps), 'projects')"` | Verifies local `projects.json` is reachable |

Run all checks: `python scripts/check_prerequisites.py docs/plans/per-project-audit-reflections.md`

## Solution

### Key Elements

- **`run_per_project_audit()` helper in `reflections/utils.py`**: Accepts a per-project audit callable and skip predicate; iterates `load_local_projects()`; aggregates findings with `[slug]` prefix; returns the standardized `{status, findings, summary, projects}` shape.
- **Refactored 5 audit functions**: Each wraps its existing body inside `run_per_project_audit()`, with per-project skip predicates (see table below).
- **`mark_completed(projects=)` extension on `Reflection`**: Accepts an optional `projects: list[dict] | None` kwarg; stores it on the run record. All existing callers omit it and see no change.
- **Scheduler result forwarding**: `execute_function_reflection` captures the callable's return value; `run_reflection` extracts `projects` and passes it to `mark_completed`.
- **Dashboard modal per-project table**: When a run record has non-empty `projects`, the history table renders a per-project sub-table (slug, status, duration, error).

### Skip Predicates

| Audit | Skip when missing |
|---|---|
| `tech-debt-scan` | Never — always runs (grep for TODO/deprecated typing across any Python repo) |
| `documentation-audit` | `docs/` directory absent in the project's working dir |
| `skills-audit` | `.claude/skills/do-skills-audit/scripts/audit_skills.py` absent in the target repo |
| `hooks-audit` | Both `logs/hooks.log` and `.claude/settings.json` absent |
| `feature-docs-audit` | `docs/features/` directory absent |

### Timeout Budgets

`DEFAULT_FUNCTION_TIMEOUT=1800s` is too tight when N=20 projects. The plan adds explicit per-reflection `timeout:` overrides to `config/reflections.yaml` sized for a realistic worst-case machine (N=20 local projects). Per-project budgets are computed from observed single-repo runtimes scaled linearly (no concurrency — `run_per_project_audit` is sync/sequential).

| Reflection | Per-project budget | N=20 cap | YAML `timeout:` | Rationale |
|---|---|---|---|---|
| `tech-debt-scan` | 4 grep × 30s = 120s | 2400s | **2700s (45 min)** | Sequential grep over each repo's `*.py`; 25% headroom |
| `documentation-audit` | 50 API calls × ~6s ≈ 300s | 6000s | **6300s (105 min)** | Bound by per-project `max_api_calls` cap (see Cost Controls); 5% headroom for Anthropic backoff |
| `skills-audit` | ~20s subprocess | 400s | **600s (10 min)** | Tight bound; `audit_skills.py` is fast |
| `hooks-audit` | ~15s log scan | 300s | **600s (10 min)** | Disk read + regex; small projects skipped via `skip_if` |
| `feature-docs-audit` | ~30s file walk | 600s | **900s (15 min)** | grep + index parsing; modest scaling |

These budgets accept that `documentation-audit` becomes a long-running reflection on multi-project hosts. Hard ceiling: the `timeout:` value enforces the cap; a project that hits the timeout gets `status="error"` for the whole reflection (existing scheduler behavior — no per-project timeout granularity in this plan).

If wall-clock pressure becomes painful in practice, the next iteration is to introduce `run_per_project_audit(parallel=True)` using `asyncio.gather` or thread pools — out of scope here. See Rabbit Holes.

### Cost Controls (`documentation-audit`)

`DocsAuditor.__init__(max_api_calls=50)` caps per-instance Anthropic calls. With per-project iteration, total per-run spend grows as `N × 50` calls in the worst case.

- **Per-project cap**: keep `max_api_calls=50` per project (default). Under a 20-project worst case = 1000 calls per reflection run = ~$1-3 (sonnet) or ~$0.10-0.30 (haiku) depending on the auditor's model choice.
- **Aggregate global cap**: `run_documentation_audit` (the outer wrapper) enforces a hard ceiling of `max_total_api_calls=500` across all projects per scheduled run. The wrapper tracks a counter; once exhausted, remaining projects are recorded as `{slug, status: "skipped", skip_reason: "global API cap reached", findings_count: 0}` and the loop exits.
- **Daily steady-state**: with default daily cadence and `max_total_api_calls=500`, expected spend is ~500 calls/day = single-digit dollars/month at sonnet pricing, sub-dollar at haiku. Document this in `docs/features/reflections.md` as part of Step 8.

### DocsAuditor Repo-Scoped State

`scripts/docs_auditor.py` currently writes to global Redis key `docs_auditor:last_audit_date`. Under per-project iteration the first project's write would suppress every subsequent project for 7 days. The fix is bounded to two methods inside `DocsAuditor`:

- `_load_state(self)` → read key `f"docs_auditor:last_audit_date:{self.repo_root.name}"` instead of the global key.
- `_record_audit_date(self)` → write the same per-repo key.

Migration: the global key becomes orphaned; let it expire naturally. Add a one-line comment in the class noting the historical key. No data migration script — the 7-day frequency gate is robust to a single missed cycle.

This change ships in this plan (added to Step 2 task list) because per-project iteration is incoherent without it.

### Flow

`ReflectionScheduler` ticks → calls `run_hooks_audit()` → `run_per_project_audit(audit_one=_audit_hooks_for_project, skip_if=..., description="Hooks audit")` → iterates `load_local_projects()` → skips projects without the artifact → runs `audit_one(project)` per qualifying project → aggregates findings → returns `{status, findings, summary, projects: [...]}` → `execute_function_reflection` captures result → `run_reflection` passes `projects` to `mark_completed` → stored in `run_history` → dashboard modal renders per-project sub-table

### Technical Approach

1. **`run_per_project_audit()` helper** (new, in `reflections/utils.py`):
   - Signature: `def run_per_project_audit(audit_one: Callable[[dict], dict], *, skip_if: Callable[[Path], bool] | None = None, name: str) -> dict`
   - `audit_one(project)` receives the full project dict (including `working_directory` as string); returns `{status: str, findings: list[str], summary: str, duration: float}`
   - `skip_if(repo_root: Path)` returns `True` when the project should be silently skipped
   - The `name` kwarg is the audit's stable identifier (matches the `name:` in `config/reflections.yaml`). Used in log lines and the aggregate `summary` string (e.g. `"hooks-audit: 3 projects scanned, 1 skipped, 0 errors"`). It is NOT decorative — it is the only piece of routing context the helper has, so don't drop it.
   - **Failure isolation**: BOTH `skip_if(repo_root)` AND `audit_one(project)` are wrapped in the same `try/except Exception` per project. A network-mount race where `Path.exists()` raises inside `skip_if` must NOT crash the whole audit — it produces `{slug, status: "error", error: "skip_if raised: ...", duration: 0.0, findings_count: 0}` and the loop continues. (Tested explicitly — see Test Impact `test_skip_if_exception_isolated_per_project`.)
   - **Async dispatch**: `run_per_project_audit` is sync. `audit_one` must be sync. The two callers that today are `async def` (`run_documentation_audit`) extract a sync inner helper; the outer async wrapper offloads the `run_per_project_audit` call via `asyncio.to_thread` (see decision in Risk 1 — locked in this plan).
   - Aggregator: collects findings from all qualifying projects, prefixes each with `[slug] `, merges into flat list, derives aggregate status per the table below.
   - Returns `{status, findings, summary, projects: [{slug, status, duration, findings_count, error}]}`

   **Aggregate `status` derivation** (mixed per-project results):

   | Per-project mix | Aggregate `status` | Rationale |
   |---|---|---|
   | All projects `ok` | `"ok"` | Trivial happy path |
   | Any project `error` | `"error"` | Surfaces failures to dashboard sparkline |
   | All projects skipped (no qualifying repos) | `"ok"` | Skips are not failures; per `summary` "no qualifying projects" |
   | Mix of `ok` + skipped | `"ok"` | Skipped repos are intentional no-ops |
   | Mix of `ok` + `disabled` (cost cap, etc.) | `"ok"` | `disabled` is intentional; non-fatal |
   | Mix of `error` + anything | `"error"` | Any error wins |
   | All `disabled` (e.g. global cap exhausted before any work) | `"disabled"` | Surfaces the configuration problem distinctly |

   The aggregator records per-project `status ∈ {"ok", "error", "skipped", "disabled"}`. Skipped projects are recorded with `findings_count=0, error=None` and are NOT added to `findings`.

2. **Refactor each of the 5 audits** to extract a `_audit_X_for_project(project: dict) -> dict` inner function / module-level helper, then call `run_per_project_audit(audit_one=_audit_X_for_project, skip_if=..., name="<audit-name>")`.
   - For `run_documentation_audit` (currently `async def`): the per-project body becomes sync `_docs_audit_for_project(project)`. The outer wrapper stays `async def` and calls `await asyncio.to_thread(run_per_project_audit, _docs_audit_for_project, skip_if=..., name="documentation-audit")`. This is locked in — see Risk 1 / Rabbit Holes.
   - For `run_skills_audit`: invoke `project_wd / ".claude/skills/do-skills-audit/scripts/audit_skills.py"` as the target (not the AI repo's copy). Skip if absent.

3. **`Reflection.mark_completed()` extension** (`models/reflection.py`):
   - Add `projects: list[dict] | None = None` kwarg
   - Include `"projects": projects or []` in the run record dict
   - All 3 existing call sites in `agent/reflection_scheduler.py` omit the kwarg → pass `None` → stored as `[]` (no behavior change for non-audit reflections)

4. **Scheduler result forwarding** (`agent/reflection_scheduler.py`):
   - `execute_function_reflection` currently returns `None`; change to return the callable's return value
   - `run_reflection` captures the result from `execute_function_reflection`; extracts `result.get("projects") if isinstance(result, dict) else None`; passes to `mark_completed(duration, projects=projects_list)`
   - Agent-type reflections return nothing meaningful; guard: `if result is None: projects_list = None`

5. **Dashboard modal** (`ui/templates/reflections/_partials/modal_content.html`):
   - In the History table body, after the existing `<tr>` row for each run, add a conditional `{% if run.projects %}` block rendering a per-project sub-table (indented rows): slug, status indicator, duration, error cell
   - CSS: indent project rows with `padding-left: 16px` or a nested table; use the existing `badge-*` classes for status
   - Sparkline remains aggregate (color driven by top-level `run.status`)

## Failure Path Test Strategy

### Exception Handling Coverage

- `run_per_project_audit()` must not let a single project failure abort the whole run. BOTH the `skip_if(repo_root)` evaluation AND the `audit_one(project)` call are wrapped in the same `try/except Exception` per project; on exception, the project record is `{slug, status: "error", error: str(e), duration: ..., findings_count: 0}` and the loop continues to the next project.
- Tests must assert: if one project's `audit_one` raises, the aggregate result still has `status: "error"` (or `"ok"` if other projects ran cleanly — see aggregate-status table in Technical Approach) and includes an error entry in `projects` for the failing project.
- Tests must explicitly cover the `skip_if` exception path: a `skip_if` predicate that calls `Path(...).exists()` on a network mount can raise `OSError` mid-call. The wrapper must record this as a per-project error, not crash the audit. New test: `test_skip_if_exception_isolated_per_project` in `tests/unit/test_run_per_project_audit_helper.py`.

### `grep` Return Code Disambiguation (`tech-debt-scan`)

`grep -r ...`'s exit codes are not just "found / not found":
- `0` = matches found
- `1` = no matches found (NOT an error)
- `2` = error (missing path, permission denied, broken symlink, etc.) — but ALSO returned for a missing target directory

The current `run_legacy_code_scan` treats `returncode != 0` as "no matches" implicitly via `result.stdout.splitlines()`, which is incidentally correct because `stdout` is empty in both cases. Under per-project iteration the target dir is always the project's `working_directory`, which `load_local_projects()` filters to existing paths. However, **race**: a working dir that disappears between the `load_local_projects()` filter and the `grep` invocation produces `returncode=2` with empty stdout — indistinguishable from "no matches".

Plan decision: treat `returncode in (0, 1)` as success and `returncode == 2` as a per-project error with `error=f"grep returned 2 (target may have been removed): stderr={proc.stderr[:200]}"`. Test: `test_grep_returncode_2_recorded_as_error` in `tests/unit/test_reflections_package.py::TestMaintenanceCallables`.

### Empty/Invalid Input Handling

- `load_local_projects()` returns `[]` on missing or malformed `projects.json`: `run_per_project_audit()` must return `{status: "ok", findings: [], summary: "No local projects found", projects: []}` — not an error.
- Projects with a `working_directory` that disappears between the `load_local_projects()` call and the audit body (race condition on network mounts): the skip predicate or `audit_one` will raise; handled by the per-project `try/except` (see Exception Handling Coverage above).
- `documentation-audit` global API cap exhausted mid-run: remaining projects are recorded as `{status: "disabled", error: "global API cap reached"}`; aggregate result `summary` notes the cap; aggregate `status` follows the table in Technical Approach.

### Error State Rendering

- Dashboard modal: test that when `run.projects` is non-empty and one project has `error` set, the error text is rendered in the per-project sub-table row (not silently dropped).
- Test with `run.projects = []` (non-audit reflections): the per-project sub-table block must not render at all.
- Test that a project with `status="disabled"` renders with a distinct visual badge (not green like `ok`, not red like `error`) so cost-cap exhaustion is legible.

### AC#8 Coverage (Manual smoke replacement)

The previous plan included a manual smoke test for AC#8 ("two-project run on `Valor the Cowboy`"). It was dropped in revision but Cowboy is the only machine where N>1 today, so the build needs equivalent automated coverage. Add `tests/unit/test_per_project_two_repos_aggregation.py` (NEW): mock `load_local_projects()` to return two fake projects (`{slug: "ai", ...}`, `{slug: "popoto", ...}`), patch each audit's per-project inner helper to return a deterministic finding, assert the aggregate `findings` list contains both `[ai]` and `[popoto]` prefixes, and assert `projects` list has two entries with the expected slugs. Covers the same intent as the manual smoke without requiring Cowboy.

## Test Impact

- [ ] `tests/unit/test_reflections_package.py::TestMaintenanceCallables::test_run_legacy_code_scan_returns_valid` — UPDATE: mock `load_local_projects()` to return one project; assert result contains `projects` key with one entry; assert findings contain `[slug]` prefix
- [ ] `tests/unit/test_reflections_package.py::TestAuditingCallables::test_run_hooks_audit_no_log` — UPDATE: was patching `PROJECT_ROOT` global; now must mock `load_local_projects()` to return a project pointing at `tmp_path`; verify no log → no error findings
- [ ] `tests/unit/test_reflections_package.py::TestAuditingCallables::test_run_feature_docs_audit_no_dir` — UPDATE: same migration from `PROJECT_ROOT` patch to `load_local_projects()` mock
- [ ] `tests/unit/test_reflections_package.py::TestAuditingCallables::test_run_documentation_audit_returns_valid` — UPDATE: must mock `load_local_projects()` and patch `DocsAuditor` per project
- [ ] `tests/unit/test_reflections_package.py::TestAuditingCallables::test_run_skills_audit_no_script` — UPDATE: mock `load_local_projects()` returning a project with no `.claude/skills/do-skills-audit/scripts/audit_skills.py`; assert result is `ok`
- [ ] `tests/unit/test_reflections_package.py::TestMaintenanceCallables::test_grep_returncode_2_recorded_as_error` — NEW: simulate `grep` returncode=2 (e.g. via `subprocess.run` mock); assert per-project `status="error"` with the expected `error` string
- [ ] `tests/unit/test_ui_reflections_data.py::TestReflectionModelExtension::test_mark_completed_appends_history` — UPDATE: verify `projects` key present in appended run record (default empty list)
- [ ] `tests/unit/test_ui_reflections_data.py::TestReflectionModelExtension::test_mark_completed_signature_unchanged` — UPDATE: add assertion that positional call `mark_completed(1.0)` still works, verify no `projects` kwarg required
- [ ] `tests/unit/test_docs_auditor.py` (or new file `tests/unit/test_docs_auditor_state_scoping.py`) — NEW or UPDATE: assert `_load_state` and `_record_audit_date` use a per-repo Redis key (`docs_auditor:last_audit_date:{repo_name}`); two fake DocsAuditor instances pointing at different repo names must not see each other's state
- [ ] New: `tests/unit/test_run_per_project_audit_helper.py` — NEW: cover per-project iteration, skip semantics, one-project-error-continues-others, empty-projects case, `[slug]` prefix, aggregate status logic (full table from Technical Approach), `test_skip_if_exception_isolated_per_project`, `disabled`-status aggregation
- [ ] New: `tests/unit/test_mark_completed_projects.py` — NEW: cover `mark_completed(duration, projects=[...])` stores `projects` on run record; `mark_completed(duration)` stores `projects: []`; existing callers pass without kwarg
- [ ] New: `tests/unit/test_per_project_modal.py` — NEW: cover dashboard modal rendering: non-empty `projects` renders sub-table rows; empty `projects` omits sub-table; error in project renders error cell; `disabled`-status renders distinct badge
- [ ] New: `tests/unit/test_per_project_two_repos_aggregation.py` — NEW: replaces dropped manual AC#8 smoke test; asserts each of the 5 audits aggregates findings from two fake projects with `[slug]` prefixes and produces a `projects` list of length 2
- [ ] New: `tests/unit/test_documentation_audit_global_cap.py` — NEW: assert `run_documentation_audit` honors `max_total_api_calls=500` aggregate cap; remaining projects get `status="disabled"` after exhaustion

## Rabbit Holes

- **Per-project `run_history` splitting**: Storing separate `run_history` lists per project would multiply the 200-entry cap across all projects. The issue explicitly excludes this — per-project data lives inside each run record, not as separate lists.
- **Retroactively backfilling the 4 existing per-project audits** (`run_log_review`, `run_pr_review_audit`, `run_task_management`, `sentry-issue-triage`) to use the new helper — these already work correctly. The helper is designed to accommodate them opportunistically but migrating them is out of scope.
- **Adding `--repo-root` flag to `audit_skills.py`**: The script self-derives REPO_ROOT from its own file location. That is correct — when invoked from `~/src/popoto/.claude/skills/.../audit_skills.py`, it targets popoto. Do not add a flag.
- **Async `run_per_project_audit`**: Making the entire helper async because `run_documentation_audit` is currently `async def`. The simplest path: extract the sync body from `run_documentation_audit` into `_audit_docs_for_project(project)` — that inner function can call `asyncio.run()` internally, or `run_per_project_audit` can detect async `audit_one` and dispatch via `asyncio.to_thread`. Keep the outer `run_documentation_audit` as `async def` to preserve the scheduler's existing `run_in_executor` path. Spike conclusion: extract sync inner helper; the outer wrapper remains async and calls `run_per_project_audit` with a sync `audit_one`.
- **Dashboard history pagination**: The existing 5-run-per-page limit means per-project sub-rows only appear on paginated history views. Expanding the pagination is out of scope.

## Risks

### Risk 1: Async/sync inconsistency between reflections (LOCKED)
**Impact:** Of the 5 audits, only `run_documentation_audit` is `async def` today. `run_per_project_audit` is sync (single-threaded loop). Mixing async/sync at the audit boundary creates two valid implementations and risks both being attempted in the build.
**Decision (locked, not pending):**
- `run_per_project_audit` is **sync**. `audit_one` callables are **sync**. No async detection inside the helper.
- 4 of 5 audits stay sync end-to-end: `run_legacy_code_scan`, `run_skills_audit`, `run_hooks_audit`, `run_feature_docs_audit`.
- `run_documentation_audit` keeps its `async def` outer wrapper (so the scheduler's `asyncio.run` path is unchanged), extracts a sync `_docs_audit_for_project(project: dict) -> dict`, and calls `await asyncio.to_thread(run_per_project_audit, _docs_audit_for_project, skip_if=..., name="documentation-audit")`. `DocsAuditor.run()` is itself synchronous.
- Rationale: the only thing the original `async def` bought was non-blocking the event loop during `auditor.run()`; that is preserved by `asyncio.to_thread` on the outer wrapper.
**Why this matters for the builder:** do not "fix" `run_per_project_audit` to detect coroutines and `await` them. The helper signature accepts `Callable[[dict], dict]`, not `Callable[[dict], Awaitable[dict]]`.

### Risk 2: `execute_function_reflection` return value propagation breaks non-audit reflections
**Impact:** Most reflection functions don't return a `projects` list. If the scheduler blindly passes `result.get("projects")` and the function returns `None` (no explicit return), the guard `isinstance(result, dict)` prevents a crash, but it's a new code path.
**Mitigation:** Guard in `run_reflection`: `projects_list = result.get("projects") if isinstance(result, dict) else None`. This is always safe; non-dict results (None or non-dict returns) pass `None` to `mark_completed`, which stores `[]`. Add a unit test for this guard.

### Risk 3: `run_history` record size growth
**Impact:** Adding `projects: [{slug, status, duration, findings_count, error}]` to each run record increases record size. On a machine with many projects, this could inflate Redis memory usage.
**Mitigation:** The `projects` sub-list contains only 5 fields per project, and most machines have ≤5 local projects. The 200-record cap controls total history size. The error field is capped at 500 chars (consistent with the existing `error` cap on the run record). No action needed beyond the existing cap.

## Race Conditions

No race conditions identified. `run_per_project_audit` is synchronous and single-threaded; `load_local_projects()` reads from disk once at the start of each run; all mutations go through `mark_completed` which uses Popoto ORM (no concurrent write contention since each reflection runs at most once at a time, gated by the scheduler's running-check).

## No-Gos (Out of Scope)

- Splitting `Reflection.run_history` per-project (multiplies the 200-entry cap)
- Backfilling the 4 audits that already iterate per-project (`run_log_review`, `run_pr_review_audit`, `run_task_management`, `sentry-issue-triage`) to use the new helper — they already work
- Adding new audits
- Adding `--repo-root` flag to `audit_skills.py`
- Dashboard history pagination changes
- Verifying on `Valor the Cowboy` as part of the build (acceptance criterion for two-project run is a manual smoke test; automated tests use mocked `load_local_projects()`)

## Update System

No update system changes required — this feature is purely internal. It modifies existing Python modules and a Jinja2 template in-repo. No new dependencies, no new config files, no deployment topology changes.

## Agent Integration

No agent integration required — this is a reflections/dashboard-internal change. The five audit functions are registered in `config/reflections.yaml` as function-type reflections and invoked by the scheduler directly. No new CLI entry points or bridge imports are needed.

## Documentation

- [ ] Update `docs/features/reflections.md` to describe the per-project iteration pattern, the `run_per_project_audit()` helper, and the dashboard per-project breakdown.
- [ ] Add a note to the `## Dashboard` section of `docs/features/reflections.md` documenting the `projects` field in run records and the per-project sub-table in the modal.
- [ ] If `docs/features/reflections.md` does not exist, create it with the above content.

## Success Criteria

- [ ] All 5 audit functions iterate `load_local_projects()` and run once per project that passes the audit's skip predicate.
- [ ] A shared `run_per_project_audit(...)` helper exists in `reflections/utils.py` and is used by all 5 refactored audits.
- [ ] `run_per_project_audit` wraps BOTH `skip_if(repo_root)` AND `audit_one(project)` in a single `try/except Exception` per project — a `skip_if` exception cannot abort the whole audit.
- [ ] Each audit's findings are prefixed with `[slug]` (matching the existing `run_log_review` pattern).
- [ ] Projects lacking the required artifact are skipped silently (not reported as errors, not counted as failures).
- [ ] Aggregate `status` follows the table in Solution → Technical Approach (covers `ok`, `error`, `disabled`, mixed-skipped cases).
- [ ] `Reflection.mark_completed()` accepts `projects: list[dict] | None = None`, stores it on the run record, and existing callers omit the kwarg without behavior change.
- [ ] `agent/reflection_scheduler.py` captures the audit return value and forwards `projects` through to `mark_completed(projects=...)`.
- [ ] The reflection modal renders a per-project sub-table when `run.projects` is non-empty AND distinguishes `disabled` status visually from `ok`/`error`.
- [ ] `config/reflections.yaml` has explicit `timeout:` overrides for all 5 reflections per the budget table; no reflection silently inherits `DEFAULT_FUNCTION_TIMEOUT=1800`.
- [ ] `DocsAuditor` reads/writes `docs_auditor:last_audit_date:{repo_name}` (per-repo); no project's frequency gate suppresses another project's run.
- [ ] `run_documentation_audit` honors `max_total_api_calls=500` aggregate cap; remaining projects after exhaustion get `status="disabled"`.
- [ ] `tech-debt-scan` distinguishes `grep returncode==2` (per-project error) from `returncode in (0, 1)` (success).
- [ ] All updated and new tests pass: `pytest tests/unit/test_reflections_package.py tests/unit/test_ui_reflections_data.py tests/unit/test_run_per_project_audit_helper.py tests/unit/test_mark_completed_projects.py tests/unit/test_per_project_modal.py tests/unit/test_per_project_two_repos_aggregation.py tests/unit/test_documentation_audit_global_cap.py tests/unit/test_docs_auditor_state_scoping.py -x -q`
- [ ] No raw Redis writes — all `Reflection` reads/writes go through Popoto. (`docs_auditor:last_audit_date:*` plain Redis keys remain plain — they predate this plan and stay outside Popoto by design.)

## Team Orchestration

### Team Members

- **Builder (reflections-core)**
  - Name: reflections-builder
  - Role: Implement `run_per_project_audit()` helper and refactor all 5 audit functions
  - Agent Type: builder
  - Resume: true

- **Builder (model-and-scheduler)**
  - Name: model-scheduler-builder
  - Role: Extend `Reflection.mark_completed()` with `projects` kwarg and update `execute_function_reflection` result forwarding in `agent/reflection_scheduler.py`
  - Agent Type: builder
  - Resume: true

- **Builder (dashboard-modal)**
  - Name: modal-builder
  - Role: Update reflection modal template to render per-project sub-table
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: test-engineer
  - Role: Write all new test files and update existing tests per the Test Impact section
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Run all tests, verify success criteria, check no raw Redis writes
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See template above.

## Step by Step Tasks

### 1. Add `run_per_project_audit()` helper to `reflections/utils.py`

- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: `tests/unit/test_run_per_project_audit_helper.py` (create)
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `run_per_project_audit(audit_one, *, skip_if=None, name)` to `reflections/utils.py`
- Signature: `audit_one: Callable[[dict], dict]`, `skip_if: Callable[[Path], bool] | None`, `name: str` (matches `name:` in `config/reflections.yaml`), returns `{status, findings, summary, projects}`
- Per-project loop: call `load_local_projects()`, then for each project evaluate `skip_if(Path(project["working_directory"]))` AND call `audit_one(project)` inside the SAME `try/except Exception` per project. A `skip_if` exception (e.g. `OSError` on a network mount) MUST be recorded as `{slug, status: "error", error: "skip_if raised: ...", duration: 0.0, findings_count: 0}` and the loop continues — NOT abort.
- Prefix each finding with `[{slug}] `; collect `{slug, status, duration, findings_count, error}` per project (status ∈ `{"ok", "error", "skipped", "disabled"}`).
- Aggregate status follows the table in Solution → Technical Approach: any `error` → `"error"`; all `disabled` → `"disabled"`; otherwise `"ok"`. Skipped projects are recorded but excluded from `findings`.
- On `load_local_projects()` returning `[]`: return `{status: "ok", findings: [], summary: "No local projects found", projects: []}`
- The `name` kwarg appears in `summary` (e.g. `"hooks-audit: 3 projects scanned, 1 skipped, 0 errors"`) and in log lines — it is NOT decorative.

### 2. Refactor 5 audit functions to use `run_per_project_audit()`

- **Task ID**: build-audit-refactor
- **Depends On**: build-helper
- **Validates**: `tests/unit/test_reflections_package.py` (update per Test Impact)
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: false
- `run_legacy_code_scan()` in `reflections/maintenance.py`: extract `_legacy_scan_for_project(project: dict) -> dict`; wrap in `run_per_project_audit(audit_one=_legacy_scan_for_project, skip_if=None, name="tech-debt-scan")`. Treat `grep returncode in (0, 1)` as success; `returncode == 2` as a per-project error (capture stderr first 200 chars in the `error` field).
- `run_documentation_audit()` in `reflections/auditing.py`: extract sync `_docs_audit_for_project(project: dict) -> dict`; keep outer `async def run_documentation_audit()` calling `await asyncio.to_thread(run_per_project_audit, _docs_audit_for_project, skip_if=..., name="documentation-audit")`; skip_if: `not (Path(project["working_directory"]) / "docs").exists()`. Enforce aggregate global cap `max_total_api_calls=500` (see step 2b).
- `run_skills_audit()` in `reflections/auditing.py`: extract `_skills_audit_for_project(project: dict) -> dict` that builds the script path from `project["working_directory"]`; skip_if: `not (Path(wd) / ".claude/skills/do-skills-audit/scripts/audit_skills.py").exists()`
- `run_hooks_audit()` in `reflections/auditing.py`: extract `_hooks_audit_for_project(project: dict) -> dict` receiving the project dict; skip_if: `not ((Path(wd) / "logs/hooks.log").exists() or (Path(wd) / ".claude/settings.json").exists())`
- `run_feature_docs_audit()` in `reflections/auditing.py`: extract `_feature_docs_audit_for_project(project: dict) -> dict`; skip_if: `not (Path(wd) / "docs/features").exists()`
- All per-project inner functions return `{status, findings: list[str], summary: str, duration: float}`
- Do NOT add `--repo-root` flag to `audit_skills.py`; invoke the target repo's copy of the script

### 2a. Repo-scope `DocsAuditor` Redis state key

- **Task ID**: build-docsauditor-scoping
- **Depends On**: none
- **Validates**: `tests/unit/test_docs_auditor_state_scoping.py` (NEW or extend existing)
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: true
- In `scripts/docs_auditor.py`, change `_load_state` (line ~1012) to read `f"docs_auditor:last_audit_date:{self.repo_root.name}"` instead of the global `docs_auditor:last_audit_date`.
- Apply the same key change to `_record_audit_date` (line ~1031) so reads/writes stay symmetric.
- Add a class-level constant `_STATE_KEY_PREFIX = "docs_auditor:last_audit_date"` and build the key as `f"{self._STATE_KEY_PREFIX}:{self.repo_root.name}"` in both methods.
- Leave the global key orphaned (TTL not set; let it expire naturally). Comment the change with a one-line note: `# 2026-05: keyed per-repo for per-project iteration; old global key is dead.`
- Required because per-project iteration would otherwise cause the first project's write to suppress every subsequent project for 7 days.

### 2b. `documentation-audit` global API cap

- **Task ID**: build-doc-audit-cap
- **Depends On**: build-audit-refactor
- **Validates**: `tests/unit/test_documentation_audit_global_cap.py` (NEW)
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: false
- In `run_documentation_audit`'s outer wrapper, track an aggregate `total_api_calls` counter shared across the per-project iteration.
- Hard ceiling: `max_total_api_calls = 500` per scheduled run (constant in `reflections/auditing.py`; document in the docstring and `docs/features/reflections.md`).
- Mechanism: pass a closure / mutable counter object into `_docs_audit_for_project` that increments after each `DocsAuditor.run()` returns (use the `api_calls_made` field from `AuditSummary` if exposed; otherwise estimate via `auditor._api_call_count` if accessible — verify during build).
- Once exhausted: remaining projects (still in the iteration) record `{slug, status: "disabled", error: "global API cap reached (500)", findings_count: 0}` and the loop exits cleanly.
- Aggregate status: if all projects are `disabled`, the reflection's `status` becomes `"disabled"` (not `"error"`) per the aggregate-status table.

### 2c. Per-reflection `timeout:` overrides in `config/reflections.yaml`

- **Task ID**: build-yaml-timeouts
- **Depends On**: none
- **Validates**: manual verification that `agent/reflection_scheduler.py::_get_timeout` returns the YAML override (not `DEFAULT_FUNCTION_TIMEOUT=1800`)
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `timeout:` keys to each of the 5 reflections in `config/reflections.yaml` per the budget table in Solution → Timeout Budgets:
  - `tech-debt-scan`: `timeout: 2700`
  - `documentation-audit`: `timeout: 6300`
  - `skills-audit`: `timeout: 600`
  - `hooks-audit`: `timeout: 600`
  - `feature-docs-audit`: `timeout: 900`
- Confirm `agent/reflection_scheduler.py::_get_timeout` honors the YAML key (it should — `pm-audio-briefing` uses the same mechanism at line 327 of the YAML).
- No code changes to the scheduler itself.

### 3. Extend `Reflection.mark_completed()` with `projects` kwarg

- **Task ID**: build-model
- **Depends On**: none
- **Validates**: `tests/unit/test_mark_completed_projects.py` (create), `tests/unit/test_ui_reflections_data.py` (update)
- **Assigned To**: model-scheduler-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `projects: list[dict] | None = None` to `mark_completed()` signature in `models/reflection.py`
- Include `"projects": projects or []` in the `run_record` dict
- Existing call sites (lines 319, 328, 338 of `agent/reflection_scheduler.py`) omit `projects` → pass `None` → stored as `[]`

### 4. Update `execute_function_reflection` to capture and return result

- **Task ID**: build-scheduler
- **Depends On**: build-model
- **Validates**: tests passing with no regressions
- **Assigned To**: model-scheduler-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `execute_function_reflection` return type from `None` to `dict | None`
- Capture callable return value: for sync functions `result = func()`, for async `result = await func()`
- Return `result` from `execute_function_reflection`
- In `run_reflection()`: capture result from `await execute_function_reflection(entry)`; extract `projects_list = result.get("projects") if isinstance(result, dict) else None`; update all 3 `mark_completed` call sites to pass `projects=projects_list`

### 5. Update dashboard modal template for per-project breakdown

- **Task ID**: build-modal
- **Depends On**: build-model
- **Validates**: `tests/unit/test_per_project_modal.py` (create)
- **Assigned To**: modal-builder
- **Agent Type**: builder
- **Parallel**: true
- In `ui/templates/reflections/_partials/modal_content.html`, inside the History table `{% for run in recent_runs %}` loop, add after the main `<tr>` row: `{% if run.projects %}` block rendering indented per-project rows with columns: slug, status badge, duration, error
- Status badge classes: `badge-ok` (green) for `ok`, `badge-error` (red) for `error`, `badge-skipped` (gray) for `skipped`, `badge-disabled` (amber/yellow) for `disabled` (cost-cap-exhausted). The four states must be visually distinct.
- CSS: add `.project-sub-row` with `padding-left: 20px; font-size: 11px; color: var(--text-secondary)` to the existing `<style>` block; reuse existing `badge-*` if present, else add `badge-disabled` and `badge-skipped`.
- Test rendering with `projects = []` (sub-table absent), `projects = [{...}]` (sub-table present), project with `error` field set, and project with `status="disabled"` (distinct badge).

### 6. Write new tests and update existing tests

- **Task ID**: build-tests
- **Depends On**: build-helper, build-audit-refactor, build-model, build-scheduler, build-modal
- **Validates**: `pytest tests/unit/ -x -q`
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_run_per_project_audit_helper.py`: covers empty projects list, skip predicate, one-error-continues-others, `[slug]` prefix, aggregate status
- Create `tests/unit/test_mark_completed_projects.py`: covers `projects` kwarg stored; default `[]` when omitted; backward-compatible signature
- Create `tests/unit/test_per_project_modal.py`: covers HTML rendering with and without `projects`; error text in sub-row
- Update `tests/unit/test_reflections_package.py` per Test Impact section above
- Update `tests/unit/test_ui_reflections_data.py` per Test Impact section above

### 7. Final validation

- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_reflections_package.py tests/unit/test_ui_reflections_data.py tests/unit/test_run_per_project_audit_helper.py tests/unit/test_mark_completed_projects.py tests/unit/test_per_project_modal.py -x -q`
- Verify no `r.save()` or `r.hgetall()` raw Redis calls introduced in new/modified code
- Verify all 5 audit functions no longer reference `PROJECT_ROOT` directly in their main body (only in their extracted per-project inner helpers if needed, and only as fallback for the AI repo)
- Confirm `mark_completed` callers in `agent/reflection_scheduler.py` all pass `projects=...`

### 8. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: documentarian (builder with documentation focus)
- **Agent Type**: documentarian
- **Parallel**: false
- Update or create `docs/features/reflections.md` with per-project iteration design, `run_per_project_audit()` API, and dashboard per-project breakdown description

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted unit tests pass | `pytest tests/unit/test_reflections_package.py tests/unit/test_ui_reflections_data.py tests/unit/test_run_per_project_audit_helper.py tests/unit/test_mark_completed_projects.py tests/unit/test_per_project_modal.py tests/unit/test_per_project_two_repos_aggregation.py tests/unit/test_documentation_audit_global_cap.py tests/unit/test_docs_auditor_state_scoping.py -x -q` | exit code 0 |
| All unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/ models/reflection.py agent/reflection_scheduler.py scripts/docs_auditor.py ui/` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/ models/reflection.py agent/reflection_scheduler.py scripts/docs_auditor.py ui/` | exit code 0 |
| No PROJECT_ROOT in audit main bodies | `grep -n "PROJECT_ROOT" reflections/auditing.py reflections/maintenance.py` | output contains only per-project inner function references (zero references in top-level audit function bodies) |
| `run_per_project_audit` exists with `name` kwarg | `python -c "import inspect; from reflections.utils import run_per_project_audit; assert 'name' in inspect.signature(run_per_project_audit).parameters; print('ok')"` | output contains ok |
| `mark_completed` accepts projects kwarg | `python -c "import inspect; from models.reflection import Reflection; sig = inspect.signature(Reflection.mark_completed); assert 'projects' in sig.parameters; print('ok')"` | output contains ok |
| YAML timeouts present for all 5 audits | `python -c "import yaml; cfg = yaml.safe_load(open('config/reflections.yaml')); names = {'tech-debt-scan','documentation-audit','skills-audit','hooks-audit','feature-docs-audit'}; entries = [r for r in cfg['reflections'] if r['name'] in names]; assert all('timeout' in r for r in entries), [r['name'] for r in entries if 'timeout' not in r]; print('ok')"` | output contains ok |
| `DocsAuditor` state key is per-repo | `grep -n "docs_auditor:last_audit_date" scripts/docs_auditor.py` | every match includes `:{` (f-string interpolation) — no bare global key remains |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Operator | Multi-project timeout amplification: `tech-debt-scan` (4 grep × N × 30s) and `documentation-audit` (Anthropic per-doc-file × N) exceed `DEFAULT_FUNCTION_TIMEOUT=1800` at N=20. | Architectural Impact, Solution → Timeout Budgets, Step 2c | New per-reflection `timeout:` overrides in `config/reflections.yaml`. Budget table sized for N=20 worst case with 5-25% headroom. `documentation-audit` capped at 6300s (~105 min) with global API cap as the secondary brake. |
| BLOCKER | Adversary | `skip_if` not wrapped in per-project `try/except`. A network-mount race where `Path.exists()` raises crashes the whole audit. | Solution → Technical Approach (1), Failure Path Test Strategy → Exception Handling, Step 1, Success Criteria | Helper now wraps BOTH `skip_if(repo_root)` AND `audit_one(project)` in the same `try/except Exception` per project. New test `test_skip_if_exception_isolated_per_project`. Explicit success-criterion bullet. |
| Concern | Skeptic | `status="disabled"` aggregate behavior undefined — how does the helper summarize mixed `ok`/`error`/`disabled` per-project results? | Solution → Technical Approach (aggregate-status table) | Added explicit table covering all mix permutations: any error → error; all disabled → disabled; mix of ok+disabled → ok; mix of ok+skipped → ok; etc. Tests cover each row. |
| Concern | Operator | Per-project iteration multiplies Anthropic spend 5-10×; no hard cap, no documented steady-state spend. | Solution → Cost Controls, Step 2b, Documentation | Per-project `max_api_calls=50` retained; new aggregate `max_total_api_calls=500`. Documented steady-state ~500 calls/day = single-digit $/month at sonnet, sub-dollar at haiku. Enforced in `run_documentation_audit` outer wrapper. |
| Concern | Archaeologist | `DocsAuditor` schedule gate (`docs_auditor:last_audit_date`) is global — first project's write suppresses all others for 7 days. | Solution → DocsAuditor Repo-Scoped State, Step 2a | New step 2a: scope key as `docs_auditor:last_audit_date:{repo_name}` in `_load_state` and `_record_audit_date` (lines ~1012, 1031). New test file. Old global key let to expire. |
| Concern | Simplifier | Async/sync inconsistency — pin which reflections are sync vs async and why. | Risk 1 (LOCKED), Solution → Technical Approach (1), Step 2 | Locked decision: helper is sync, all 5 `audit_one` callables are sync. Only `run_documentation_audit` keeps `async def` outer wrapper, calling `await asyncio.to_thread(run_per_project_audit, ...)`. Builders explicitly told NOT to add coroutine detection. |
| Concern | User | Dropped manual smoke test for AC#8 (two-project run on Cowboy) — restore or replace. | Failure Path Test Strategy → AC#8 Coverage, Test Impact | New `tests/unit/test_per_project_two_repos_aggregation.py`: mocks two fake projects, asserts `[ai]` + `[popoto]` prefix aggregation across all 5 audits. Automated equivalent of the dropped smoke. |
| Nit | Skeptic | Wrong test class names (`TestMaintenanceReflections`/`TestAuditingReflections` don't exist). | Test Impact | Verified actual classes are `TestMaintenanceCallables` and `TestAuditingCallables`. Updated all references. |
| Nit | Simplifier | `description` param unused. | Solution → Technical Approach (1), Step 1 | Renamed `description` → `name` (matches `name:` in `config/reflections.yaml`); explicitly used in aggregate `summary` string and log lines. |
| Nit | Adversary | `grep returncode=2` ambiguity (errors AND missing dirs). | Failure Path Test Strategy → grep Return Code Disambiguation, Step 2, Test Impact, Success Criteria | `(0, 1)` = success; `2` = per-project error with stderr captured. New test `test_grep_returncode_2_recorded_as_error`. |

---

## Open Questions

None. The revision pass closed every concern raised by the critique war room. Remaining design notes (informational, not pending decisions):

1. **Async `run_documentation_audit`**: LOCKED — see Risk 1. Sync inner helper, async outer wrapper via `asyncio.to_thread`.
2. **`execute_function_reflection` return value**: Safe change — `run_reflection` is the only caller and updates in the same PR.
3. **Per-project sub-results format**: Matches `run_log_review` / `run_pr_review_audit` shape; standardized in helper return value.
4. **`DocsAuditor` global Redis key migration**: No migration script; the orphaned global key is harmless and naturally falls out of use after the first per-repo write per machine.
5. **Parallel per-project execution**: Out of scope. If wall-clock pressure ever bites, the next iteration is `run_per_project_audit(parallel=True)` using `asyncio.gather` or thread pools — see Rabbit Holes.
