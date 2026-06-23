---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-04-17
tracking: https://github.com/tomcounsell/ai/issues/1028
last_comment_id:
revision_applied: false
---

# Reflections Modularization: One File per Reflection

## Problem

`config/reflections.yaml` declares 31 recurring reflections, dispatched by `agent/reflection_scheduler.py`. The reflection-owned logic still lives in a handful of **bundle modules** under `reflections/` (`auditing.py` 715L, `maintenance.py` ~470L, `memory_management.py` ~46KB, `task_management.py` ~122L) plus `agent/sustainability.py` (4 self-healing reflections). Each bundle packs several unrelated reflections into one file.

**Current behavior:**
- Bundle modules force a reader to scroll past unrelated reflections to understand one. A failing test in `reflections/auditing.py` doesn't say which of its 6 reflections broke without digging.
- Per-reflection cadence rationale, failure modes, and cross-references aren't consistently documented at the file level.
- `agent/sustainability.py` mixes 4 reflection callables (`circuit_health_gate`, `session_count_throttle`, `failure_loop_detector`, `session_recovery_drip`, `sustainability_digest`) with a non-reflection helper (`send_hibernation_notification`) that `agent/agent_session_queue.py:1476` imports directly.

**Desired outcome:**
- Every bundle-owned reflection becomes a self-contained file at `reflections/{group}/{reflection_name}.py` with a module docstring covering purpose, cadence rationale, failure modes, and related reflections.
- The 4 self-healing reflections move out of `agent/sustainability.py` into `reflections/agents/`; the file's non-reflection helper relocates so the file can be deleted.
- Shared helpers consolidate into `reflections/utilities.py` (replacing `reflections/utils.py`).

## Freshness Check

**Baseline commit:** `8863acc0` (current `origin/main` HEAD, 2026-06-23).
**Issue filed at:** 2026-04-17.
**Disposition: MAJOR DRIFT — issue scope is 2/3 already shipped. This plan is narrowed to the one genuinely-remaining deliverable.**

The issue and the prior plan draft both predate the **Unified Reflection system** (PRs #1341 "Tier 1-2", #1349 "Tier 3", #1364 "remove behavioral_learning", plus #1253/#1276/#1295/#1340 pm-briefings cutover). Those PRs already landed two of the issue's three acceptance pillars. Verified against current code:

| Issue acceptance item | Status on `origin/main` (2026-06-23) | Evidence |
|---|---|---|
| **#2 — Dashboard renders collapsible groups** | **ALREADY SHIPPED** | `ui/data/reflections.py:get_grouped_reflections()` exists; `ui/templates/reflections/_partials/status_grid.html` already renders collapsible group rows (toggle JS, default-collapsed, error dot, `N on`/`N off` badges). |
| **#3 — Remove stale `ReflectionRunner` comment at `config/reflections.yaml:58`** | **ALREADY DONE** | `grep ReflectionRunner ~/Desktop/Valor/reflections.yaml` → no match. Line 58 is now `every: 300s`. |
| YAML gains explicit `group:` field per entry | **ALREADY DONE** | Every entry in the vault YAML already carries `group: agents\|housekeeping\|audits\|memory`. |
| **#1 — One file per reflection under `reflections/{group}/`** | **NOT DONE** — the genuine remaining work | Bundles `reflections/{auditing,maintenance,memory_management,task_management}.py` and `agent/sustainability.py` still pack multiple reflections each. |

**Other corrections to stale references (issue recon + prior draft were wrong):**
- `config/reflections.yaml` is a **gitignored symlink** (`.gitignore:8`) to `~/Desktop/Valor/reflections.yaml`. It is **NOT version-controlled** and does **not exist on `origin/main`** (`git cat-file -t origin/main:config/reflections.yaml` → fatal). The prior draft's claim that it became a committed regular file (`d47d5a81`) is false in this checkout.
- The real reflection module inventory today: `auditing.py`, `crash_recovery.py`, `docs_auditor.py`, `maintenance.py`, `memory_management.py`, `pm_audio_briefing.py`, `sdlc_progress.py`, `sentry_triage.py`, `session_intelligence.py`, `stall_advisory.py`, `task_management.py`, `utils.py`, plus the `pm_briefings/` subpackage. There is **no** `daily_report.py` or `behavioral_learning.py` (deleted in #1362/#1364).
- The 3 "queue callables" the issue lists as living in `agent/agent_session_queue.py` are actually **re-exports**: defined in `agent/session_health.py` (`_agent_session_health_check`, `cleanup_corrupted_agent_sessions`) and `agent/session_revival.py` (`cleanup_stale_branches_all_projects`). These are agent-core functions used by the worker directly — **out of scope to move**; they keep resolving via their existing dotted paths.

**Active plans overlapping this area:** PR #1773 (#1768, branch `worktree-agent-ab50844fb475633c9`, OPEN) edits `reflections/stall_advisory.py` (adds action-mode), `agent/session_stall_classifier.py`, `config/reflections.yaml` (vault), and adds `docs/features/stall-recovery.md`. **Coordination required — see No-Gos and the merge-order note.**

## Research

No relevant external findings — purely internal Python package reorganization. Proceeding with codebase context.

## Prior Art

- **PR #967** — deleted the 3,086-line `scripts/reflections.py` monolith, extracted the `reflections/` package. Precedent for the cut.
- **PRs #1341 / #1349** — Unified Reflection system; already shipped the dashboard grouping + YAML `group:` field this issue asked for. This plan does **not** touch that.
- **PR #991** — `{subject}-{verb}` naming standard; informs file naming (`circuit-health-gate` → `circuit_health_gate.py`).

No prior attempt at per-reflection files found. No failed prior fixes.

## Data Flow

Runtime path is **unchanged** by this refactor:
1. `ReflectionScheduler` loads the registry via `load_registry()` → `_resolve_registry_path()` (env `REFLECTIONS_YAML` → `~/Desktop/Valor/reflections.yaml` → in-repo `config/reflections.yaml`).
2. For each due entry, `_resolve_callable(dotted_path)` does `importlib.import_module` + `getattr`.
3. `execute_function_reflection` calls the resolved callable (sync runs in executor; async awaited).

The refactor only changes **where the callable's code lives**. The dotted path the scheduler resolves must keep working. Two ways to guarantee that — see Solution.

## Architectural Impact

- **New dependencies:** none.
- **Coupling:** decreases — per-file isolation replaces bundle co-location.
- **Reversibility:** high — `git revert` restores bundles; no data/Redis schema change.
- **Registry resolution:** the scheduler/dashboard read dotted paths; correctness hinges on every YAML `callable:` still resolving after the move.

## Appetite

**Size:** Medium. **Team:** Solo dev. Mechanical move+document work across ~20 reflection files; thin risk surface (no behavior change), so review overhead is low.

## Solution

### The registry-resolution decision (drives everything)

`config/reflections.yaml` is the **vault** file (`~/Desktop/Valor/reflections.yaml`), gitignored and **live** — the running worker on this machine reads it directly. Editing it (a) changes production behavior immediately, (b) does not appear in this PR's git diff, and (c) overlaps PR #1773's vault edits.

To keep this refactor **fully git-contained, zero-vault-edit, and conflict-free with #1773**, the chosen approach is:

> **Keep thin re-export shims at the current callable dotted paths.** The reflection logic moves into `reflections/{group}/{name}.py`; the old module (`reflections/maintenance.py`, etc.) is replaced by a short module that imports `run` from the new per-file location and re-exports it under the historical name the YAML already references. `agent/sustainability.py`'s reflection callables move to `reflections/agents/`, and `sustainability.py` re-exports them so `callable: "agent.sustainability.circuit_health_gate"` still resolves.

This means **no vault YAML edit is required** — every existing `callable:` path keeps resolving through the shim. The dashboard `_classify_group()` constant and YAML `group:` field are untouched. The shims are explicit, documented re-exports (not commented-out legacy), so they satisfy the "no half-migration" principle as a deliberate compatibility layer for an un-versioned config file.

**Trade-off vs. the issue's literal acceptance criteria** ("bundles deleted, YAML callable paths updated"): the issue assumed a committed YAML. Because the YAML is vault-only and live, a hard cutover would require editing live shared state outside git and racing PR #1773. The shim approach is the correct engineering call given the un-versioned config; it is surfaced as Open Question 1 for explicit sign-off. If the human prefers a hard cutover, the plan's Phase D switches from "write shim" to "edit vault YAML + delete old module" (additive, post-#1773-merge).

### Target layout

One file per bundle-owned reflection, under the group directory matching its existing YAML `group:`:

```
reflections/
  utilities.py                 # shared: load_local_projects, run_per_project_audit, run_llm_reflection, PROJECT_ROOT, PROJECT_*
  agents/
    __init__.py
    circuit_health_gate.py     # from agent/sustainability.py
    session_count_throttle.py  # from agent/sustainability.py
    failure_loop_detector.py   # from agent/sustainability.py
    session_recovery_drip.py   # from agent/sustainability.py
    system_health_digest.py    # from agent/sustainability.py (sustainability_digest)
  housekeeping/
    __init__.py
    redis_ttl_cleanup.py       # from maintenance.py
    merged_branch_cleanup.py   # from maintenance.py (run_branch_plan_cleanup)
    disk_space_check.py        # from maintenance.py
    analytics_rollup.py        # from maintenance.py
  audits/
    __init__.py
    tech_debt_scan.py          # from maintenance.py (run_legacy_code_scan)
    redis_quality_audit.py     # from maintenance.py (run_redis_data_quality)
    skills_audit.py            # from auditing.py
    hooks_audit.py             # from auditing.py
    pr_review_audit.py         # from auditing.py
    task_backlog_check.py      # from task_management.py (run_task_management)
    principal_staleness.py     # from task_management.py
  memory/
    __init__.py
    memory_decay_prune.py      # from memory_management.py
    memory_quality_audit.py    # from memory_management.py
    embedding_orphan_sweep.py  # from memory_management.py
```

**Out of scope (stays put, already single-purpose or agent-core):** `stall_advisory.py` (touched by #1773 — do NOT move), `sentry_triage.py`, `sdlc_progress.py`, `docs_auditor.py` (+`run_docs_branch_sweeper`), `session_intelligence.py`, `crash_recovery.py`, `pm_audio_briefing.py`, `pm_briefings/`, and the 3 agent-core queue callables in `session_health.py`/`session_revival.py`. These are each one cohesive unit already; relocating them is churn without the maintainability payoff and risks the #1773 conflict (`stall_advisory.py`).

### Shim shape

After moving `run_legacy_code_scan` into `reflections/audits/tech_debt_scan.py` (renamed public entry `run`), the old `reflections/maintenance.py` becomes:

```python
"""Compatibility re-exports for reflections relocated to reflections/{audits,housekeeping}/.

The registry (config/reflections.yaml, vault) references the historical dotted
paths below. Each name re-exports the relocated reflection so the scheduler's
importlib resolution keeps working without a vault edit. New code should import
from the per-reflection module directly.
"""
from reflections.audits.tech_debt_scan import run as run_legacy_code_scan
from reflections.audits.redis_quality_audit import run as run_redis_data_quality
from reflections.housekeeping.redis_ttl_cleanup import run as run_redis_ttl_cleanup
# ... etc
```

`agent/sustainability.py` becomes a re-export shim too: `from reflections.agents.circuit_health_gate import run as circuit_health_gate`, etc., and **keeps** `send_hibernation_notification` defined in-place (its non-reflection consumer at `agent/agent_session_queue.py:1476`) — OR moves `send_hibernation_notification` to a clearly non-reflection home (`agent/notifications.py`) and updates the one importer. Decision deferred to build recon (Open Question 2).

### File shape (standard)

```python
"""reflections/{group}/{name}.py — {one-line purpose}

What it does: {side effects, reads, writes}
Cadence: {interval} ({why})
Failure modes:
    - {failure} → {handling}
Related reflections:
    - {name}: {interaction}
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""
```

Single public `run()` (sync or async — preserve the existing signature; per commit `65fcfcc5` several were deliberately converted to sync, do NOT reintroduce `async`). Private helpers prefixed `_`, carried with their owning reflection. Preserve `from bridge.utc import to_unix_ts` imports verbatim where present (naive-datetime hotfix).

### Utilities consolidation

Rename `reflections/utils.py` → `reflections/utilities.py`. Keep all currently-shared helpers there (`load_local_projects` — 5+ callers, `run_per_project_audit`, `run_llm_reflection`, `PROJECT_ROOT`, ignore/confidence helpers used across modules). Update the ~11 internal importers and ~5 test importers. **Single-use** helpers (e.g. `extract_structured_errors`, `CORRECTION_PATTERNS`) inline into their owning per-reflection file. (Lower-churn alternative if `utilities.py` rename proves noisy: keep the filename `utils.py` and only split bundles — see Open Question 3.)

### Flow

- **Phase A:** Scaffold `reflections/{agents,housekeeping,audits,memory}/__init__.py` (empty) and `reflections/utilities.py`.
- **Phase B (parallel per group):** Move each bundle-owned reflection into its per-file home with standardized docstring; behavior byte-identical.
- **Phase C:** Replace each old bundle module + `agent/sustainability.py` with a re-export shim (or, per OQ1, hard-cutover). Run `ruff`.
- **Phase D:** Update tests' import paths; add `test_all_callables_resolve` (iterate the loaded registry, assert `_resolve_callable` succeeds for every entry).
- **Phase E:** Docs (`docs/features/reflections.md` layout section; grep `docs/` + `CLAUDE.md` for stale bundle paths).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Preserve every `try/except ... logger.warning` and per-project "log and skip" block verbatim when moving (known sites: `_legacy_scan_for_project` grep-returncode handling, `load_local_projects` permissive loading, auditing missing-file swallow).

### Empty/Invalid Input Handling
- [ ] `_resolve_callable` on a bad path currently logs + skips. New `test_all_callables_resolve` asserts the happy path resolves for every registry entry; a typo in a shim re-export must fail this test loudly.

### Error State Rendering
- [ ] No dashboard changes in this plan (grouping already shipped); existing `test_ui_reflections_data.py` must still pass unchanged after import moves.

## Test Impact
- [ ] `tests/unit/test_reflections_package.py` — UPDATE: imports from `reflections.maintenance`/`reflections.auditing`/etc. either keep resolving through shims (no change) or, for tests that import private helpers directly, repoint to the new per-file module. Logic stays.
- [ ] `tests/unit/test_sustainability.py` — UPDATE: 23 tests import from `agent.sustainability`. If shims are kept, imports still resolve; tests asserting module location repoint to `reflections.agents.*`.
- [ ] `tests/unit/test_sustainability_namespace.py` — UPDATE: asserts the `agent.sustainability` namespace shape; update to reflect the shim re-exports.
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE/EXTEND: add `test_all_callables_resolve`; existing tests mostly generic, minimal change.
- [ ] `tests/integration/test_reflections_redis.py` — UPDATE: import paths; Redis persistence assertions unchanged.
- [ ] `tests/unit/test_reflections_multi_repo.py`, `test_run_per_project_audit_helper.py`, `test_per_project_two_repos_aggregation.py` — UPDATE: import `reflections.utils` → `reflections.utilities` (only if the rename is taken per OQ3).
- [ ] `tests/unit/test_ui_reflections_data.py` — NO CHANGE expected (dashboard untouched); run to confirm.

## Rabbit Holes
- **Don't rewrite any reflection's logic.** Move + document only. Buggy reflection → separate issue.
- **Don't touch the dashboard.** Collapsible grouping already shipped; re-implementing it is wasted churn and risks regressing #1341/#1349.
- **Don't move `stall_advisory.py`.** PR #1773 edits it; moving it guarantees a conflict. Leave it in place.
- **Don't move the agent-core queue callables** (`session_health.py`/`session_revival.py`) — they serve the worker directly, not just reflections.
- **Don't edit the vault YAML** unless OQ1 resolves to hard-cutover. The shim approach needs zero vault edits.
- **Don't normalize docstrings across `models/`.** Scope creep.

## Risks

### Risk 1: A shim re-export typo silently breaks a reflection
**Impact:** reflection stops running, no error until silent data accumulates. **Mitigation:** `test_all_callables_resolve` iterates the registry and asserts `_resolve_callable()` succeeds for every entry — catches any broken shim at test time.

### Risk 2: Vault YAML / PR #1773 collision
**Impact:** if a hard cutover edits the vault YAML, it overlaps #1773's vault edits and can't be represented in git. **Mitigation:** shim approach requires zero vault edit (default). If hard cutover is chosen, sequence it strictly after #1773 merges and apply the vault edit as a separate manual step, not part of the PR diff.

### Risk 3: `reflections/utils.py` rename ripple
**Impact:** ~16 importers (incl. `pm_briefings/`) break if the rename misses one. **Mitigation:** grep-driven update + `ruff check` + `test_reflections_package.py`. OQ3 offers keeping `utils.py` to avoid this entirely.

## Race Conditions
None. Scheduler is a single-threaded async loop; resolution is synchronous importlib. No shared mutable state introduced.

## No-Gos (Out of Scope)
- Changing any reflection's cadence or behavior.
- Re-implementing or restyling the (already-shipped) dashboard grouping.
- Moving `stall_advisory.py`, `sentry_triage.py`, `sdlc_progress.py`, `docs_auditor.py`, `session_intelligence.py`, `crash_recovery.py`, `pm_briefings/`, or the agent-core queue callables.
- Editing `config/reflections.yaml` (vault) under the default shim approach.

### Merge-order dependency on PR #1773 (#1768)
This PR **must merge after #1773**. #1773 edits `reflections/stall_advisory.py` (adds action-mode), `agent/session_stall_classifier.py`, and the vault `config/reflections.yaml`, and adds `docs/features/stall-recovery.md`. This plan deliberately does NOT touch `stall_advisory.py` or the vault YAML, so a clean git conflict is unlikely — but the PR body must still state the order explicitly and request a rebase onto post-#1773 main.

## Update System
No update system changes required. `scripts/remote-update.sh` doesn't reference reflection file paths. Note: `install_worker.sh` copies the vault `reflections.yaml` → in-repo `config/reflections.yaml` at install time; the shim approach keeps that copy's callable paths valid with no migration step. (`scripts/update/reflections_yaml.py` exists for vault-sync but needs no change since callable paths are unchanged.)

## Agent Integration
No agent integration required — reflections are scheduled background jobs run by the worker, not tools the agent invokes. No CLI entry point, no `.mcp.json`, no bridge import change.

## Documentation
- [ ] Update `docs/features/reflections.md` to describe the `reflections/{group}/{name}.py` per-file layout and the compatibility-shim rationale for the vault-referenced dotted paths.
- [ ] Grep `docs/` and `CLAUDE.md` for references to `reflections/maintenance.py`, `reflections/auditing.py`, `agent/sustainability.py` and update/remove.
- [ ] Confirm `docs/features/README.md` index entry for reflections is still accurate (no new doc needed).

## Success Criteria
- [ ] Each bundle-owned reflection exists at `reflections/{group}/{name}.py` with a single `run()` entry and a module docstring (purpose, cadence, failure modes, related reflections).
- [ ] `agent/sustainability.py`'s 4+1 reflection callables relocated to `reflections/agents/`; the file is either a documented re-export shim (default) or deleted (hard-cutover), with `send_hibernation_notification` still resolvable for `agent_session_queue.py`.
- [ ] `reflections/{auditing,maintenance,memory_management,task_management}.py` are documented re-export shims (default) or deleted (hard-cutover); no commented-out legacy.
- [ ] Every `callable:` in the registry resolves: new `test_all_callables_resolve` passes.
- [ ] All existing reflection tests pass after import-path updates; no behavior regression.
- [ ] `python -m ruff check .` and `python -m ruff format --check .` clean.
- [ ] `docs/features/reflections.md` updated; no stale bundle-path references remain in `docs/`.
- [ ] PR body states the merge-order dependency on #1773 and requests rebase onto post-#1773 main.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| All callables resolve | `pytest tests/unit/test_reflection_scheduler.py::test_all_callables_resolve -q` | exit 0 |
| Reflection package tests | `pytest tests/unit/test_reflections_package.py tests/unit/test_sustainability.py tests/unit/test_sustainability_namespace.py -q` | exit 0 |
| Dashboard data tests still green | `pytest tests/unit/test_ui_reflections_data.py -q` | exit 0 |
| Per-file reflections exist | `find reflections/agents reflections/housekeeping reflections/audits reflections/memory -name '*.py' ! -name '__init__.py' \| wc -l` | ≥ 19 |
| Lint clean | `python -m ruff check .` | exit 0 |
| Format clean | `python -m ruff format --check .` | exit 0 |

## Open Questions

1. **Shim vs. hard cutover for the vault-referenced callable paths.** `config/reflections.yaml` is the vault file (gitignored, live, also edited by open PR #1773). Default plan = keep documented re-export shims at the old dotted paths so **zero vault edit** is needed and there's **no #1773 conflict**. Alternative = edit the vault YAML to point at the new paths and delete the old modules (a hard cutover that touches live, un-versioned state outside git and must be sequenced after #1773). **Recommend the shim approach.** Confirm?

2. **`send_hibernation_notification`** (the one non-reflection export of `agent/sustainability.py`, imported by `agent_session_queue.py:1476`): leave it defined in the `sustainability.py` shim, or relocate to `agent/notifications.py`? Recommend leaving it in place to minimize blast radius.

3. **`reflections/utils.py` → `utilities.py` rename:** the issue asks for `utilities.py`, but the rename ripples through ~16 importers (incl. `pm_briefings/`). Take the rename, or keep `utils.py` and only split the bundles? Recommend taking the rename for issue-fidelity, guarded by grep + tests.
