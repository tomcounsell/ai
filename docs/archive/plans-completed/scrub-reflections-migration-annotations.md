---
status: Planning
type: chore
appetite: Small
owner: Tom Counsell
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1132
last_comment_id:
revision_applied: true
allow_unchecked: true
---

# Scrub monolith-migration annotations from `reflections/` (and the vault `reflections.yaml`)

## Problem

The `reflections/` package was extracted from a 3,086-line `scripts/reflections.py` monolith in PR #967 (issue #748). The extraction shipped, but every module still narrates its origin in a header docstring ("Extracted from scripts/reflections.py"), and most callables carry an inline `Maps to monolith step: step_X` annotation. `reflections/__init__.py` references the deleted `ReflectionRunner` class.

These annotations describe a history that no longer constrains the code. The monolith was deleted weeks ago, the runner class is gone, the numbered-step concept was retired (reflections are registered by name in YAML), and a reader trying to learn the package today is forced to mentally subtract migration metadata from every file before they can see what the code actually does.

Per project principle #1 ("NO LEGACY CODE TOLERANCE"), the migration story belongs in the git history of PR #967 and `docs/plans/completed/reflections-monolith-deletion.md`, not in the live source.

**Current behavior:**
- 7 of 8 `reflections/*.py` modules open with a docstring narrating extraction from the monolith.
- 14 callables across `auditing.py`, `behavioral_learning.py`, `daily_report.py`, `maintenance.py`, `session_intelligence.py`, and `task_management.py` carry inline `Maps to monolith step: step_X` lines.
- `reflections/__init__.py` line 9 references `ReflectionRun` and "the legacy monolith class" as a forbidden dependency.
- `reflections/auditing.py:72` has a "from monolith module level" comment on a regex block.
- `reflections/memory_management.py:4` opens with "New reflections with no monolith equivalent:" — a comparative framing that only makes sense if the reader already knows the monolith existed.

**Desired outcome:**
- Every `reflections/*.py` reads as if it were written in the current architecture. No `scripts/reflections.py` references, no `Maps to monolith step:` markers, no `ReflectionRun(ner)` references, no comparative framing against a system that no longer exists.
- The grep guard in the acceptance criteria returns zero matches in `reflections/`.
- Docstrings are re-flowed so they still convey what each module/callable does — purpose, contract, failure modes — just without the migration narration.

## Freshness Check

**Baseline commit:** `b6eebc15` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-04-22T17:00:42Z
**Disposition:** **Major drift** for one half of the issue's scope (`config/reflections.yaml`); **Unchanged** for the other half (`reflections/*.py`).

**File:line references re-verified:**
- `reflections/{auditing,behavioral_learning,daily_report,maintenance,memory_management,session_intelligence,task_management,utils}.py` — all carry the migration headers and step-mapping annotations the issue describes — **still hold** (re-grepped on baseline `b6eebc15`).
- `config/reflections.yaml:58, 141-142` — issue cites these as the canonical source of monolith narration in YAML — **gone from git**. The file was removed from tracking by commit `c2af0960` ("chore: move reflections.yaml to vault and wire update script", 2026-04-22 03:40 UTC) and is now vault-managed at `~/Desktop/Valor/reflections.yaml` with `config/reflections.yaml` as a per-machine symlink (created by `scripts/update/run.py:396` Step 1.66 / `scripts/install_worker.sh:48`). The issue was filed ~13.5 hours after this commit, before its author had absorbed the relocation.
- `scripts/reflections.py` — issue claims "does not exist" — **confirmed gone**.
- `class ReflectionRunner` — issue claims "does not exist" — **confirmed gone** (zero hits repo-wide outside `docs/plans/completed/`).

**Cited sibling issues/PRs re-checked:**
- #748 — closed 2026-04-14, "Finish reflections unification: extract monolith units" — superseding work for this scrub. Holds.
- PR #967 — merged 2026-04-14, "feat(reflections): delete 3086-line monolith, extract reflections/ package (#748)" — the deletion this scrub trails. Holds.
- #1031 — open, rewrites `docs/features/adding-reflection-tasks.md` against current architecture. Out of scope per issue body.
- #1032 — open, cascades monolith-cleanup across feature/guide docs (`popoto-index-hygiene.md`, `bridge-self-healing.md`, `telegram-history.md:58`, `valor-name-references.md`, `claude-code-feature-swot.md`). Explicitly out of scope per issue body.
- #1028 — open, future refactor to one-file-per-reflection (`reflections/{group}/<name>.py`). Will move and rename every file this scrub touches. Per issue body and recon: ship this scrub now anyway — it is a one-shot text edit, #1028 is still in plan stage, and a clean `reflections/` makes #1028's diff cleaner to review.
- #1033 — closed 2026-04-22, "Investigations from daily audit: adding-reflection-tasks (2026-04-17)" — this issue (#1132) is its sub-item 1. Holds.

**Commits on `main` since issue was filed (touching referenced files):**
- None touching `reflections/`. Two unrelated plan commits on main (`ceedbe68` worker-lifecycle plan revision, `8c9af6b8` worker-lifecycle plan creation).

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/reflections-modular.md` (issue #1028) — overlap is total at the file level (every file this scrub touches will be moved/renamed), zero at the byte level (every annotation this scrub deletes would also be deleted by the move). Coordination is one-way: this scrub must merge before #1028 starts, or rebase against it — there is no merge conflict if this scrub lands first.
- `docs/plans/reflections-quality-pass.md`, `docs/plans/reflections-dashboard.md`, `docs/plans/reflections-dead-import.md` — pre-existing plans, none touch the same lines.

**Notes on the major drift:** The issue's request to "edit `config/reflections.yaml` lines 58, 141-142" cannot be satisfied by an in-repo edit, because the in-repo file is gone. Two options:

1. **Drop YAML scope from this PR.** Leave the YAML scrub for a separate ticket that operates on the vault file (`~/Desktop/Valor/reflections.yaml`), which is the actual source of truth. This PR ships only the `reflections/*.py` half, which is unaffected by the move.
2. **Add an out-of-band note** documenting that the same scrub should be applied to the vault file when it is next edited, and surface this to the user. The vault file is iCloud-synced and not reachable from this worktree (this machine is skills-only per `~/.claude/.../MEMORY.md` — no vault present).

This plan adopts **option 1**. The YAML scrub is recorded as an out-of-scope follow-up in No-Gos.

## Prior Art

- **PR #967** — `feat(reflections): delete 3086-line monolith, extract reflections/ package (#748)` (merged 2026-04-14). The migration whose annotations this scrub removes. The annotations were a deliberate scaffolding choice during extraction so reviewers could trace each callable back to its monolith origin during the migration PR; that role is finished.
- **Issue #926** — `chore: Reflections quality pass — scheduler placement, model split, field conventions` (closed 2026-04-13). Cleaned up a different layer (scheduler placement and field naming). Different scope, same overall direction (post-extraction tidying).
- **Issue #1033** — `Investigations from daily audit: adding-reflection-tasks` (closed 2026-04-22). Triaged the post-extraction debt; this issue (#1132) is its first sub-item to be tackled.
- **Commit `c2af0960`** — `chore: move reflections.yaml to vault and wire update script` (2026-04-22). Relocated `config/reflections.yaml` to vault-managed storage. Material to scope (see Freshness Check Major drift note).

No prior fixes failed; this is the first scrub.

## Research

No relevant external findings — proceeding with codebase context only. The work is purely internal: deleting comments and re-flowing docstrings in 8 files. No libraries, APIs, or ecosystem patterns are involved.

## Data Flow

Skipped — change is purely cosmetic edits to docstrings/comments in 8 files. No runtime data path is touched.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none. Callable names, signatures, return contracts, async-ness — all unchanged.
- **Coupling:** unchanged.
- **Data ownership:** unchanged.
- **Reversibility:** trivial. Every change is in `git revert` range; the deleted text exists in PR #967's diff and `docs/plans/completed/reflections-monolith-deletion.md` if anyone ever needs the migration mapping.

## Appetite

**Size:** Small

**Team:** Solo dev (one builder, one validator, one documentarian for the optional convention note).

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a one-PR cosmetic edit. The risk surface is "did the docstrings still parse and read well after the scrub" — caught by ruff format and a careful diff read.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `pytest` available | `python -m pytest --version` | Run the test suite to confirm zero behavior change |
| `ruff` available | `python -m ruff --version` | Format check after docstring edits |

## Solution

### Key Elements

- **Scope:** `reflections/__init__.py`, `reflections/utils.py`, `reflections/auditing.py`, `reflections/behavioral_learning.py`, `reflections/daily_report.py`, `reflections/maintenance.py`, `reflections/memory_management.py`, `reflections/session_intelligence.py`, `reflections/task_management.py`. Nine files total.
- **Edit shape:** delete migration-narration sentences and step-mapping lines; re-flow surrounding prose so the resulting docstring stands on its own as a present-tense description of what the module/callable does.
- **Optional convention note:** add a one-line "Do not reintroduce numbered-step references (`step_X`); reflections are addressed by name in `config/reflections.yaml`" to `docs/features/reflections.md` (preferred) or, failing that, the CLAUDE.md "Development Principles" block. Issue marks this as optional; include if it lands within the appetite.

### Flow

Reader opens any `reflections/*.py` file → docstring describes module's purpose, contract (no args, returns `{status, findings, summary}`), and failure modes → reader proceeds to code without needing to mentally subtract migration history.

### Technical Approach

- **Per-file edits, not regex-replace.** Each docstring needs to be re-read after the migration text is removed and re-written so the surviving prose is coherent. A blind regex would leave dangling sentence fragments.
- **Preserve all non-migration content.** The "Hotfix (sibling of PR #1056)" block in `reflections/auditing.py:run_log_review` (lines 192-199) explains why the function is sync rather than async — that is current-architecture documentation, not migration narration; keep it. The `behavioral_learning.run` docstring's "Skips gracefully if models.cyclic_episode is not available" guard is current-architecture; keep that, drop the parenthetical "(guard preserved from monolith step_behavioral_learning)".
- **Re-flow `__init__.py` line 9.** "Have no dependency on ReflectionRun or the legacy monolith class" → drop entirely; the previous bullets ("Accept no arguments", "Return a dict: ...", "Handle redis.exceptions.ConnectionError gracefully") fully define the contract.
- **Re-frame `memory_management.py:4`.** "New reflections with no monolith equivalent:" → "Reflection callables:" or similar — the same enumeration, no comparison to a deleted system.
- **Drop the `auditing.py:72` "from monolith module level" half-sentence.** The regex constants are self-explanatory; the comment can become "PR Review audit helper patterns" with no tail.
- **No code-behavior changes.** Function bodies, signatures, decorators, imports — untouched. Any test that was green before remains green after.
- **Run `python -m ruff format` after editing** (per user global instruction: "do not run linting, only black formatting" — ruff format is the project's formatter equivalent). This catches any docstring quote/indent issues introduced during edits.

> **Implementation Note (concern: re-flow quality, not bare deletion):** Bare deletion is not the standard. After removing migration text from a docstring, the surviving prose must still answer three questions: (a) what does this module/callable do, (b) what is its contract (args, return shape, side effects), (c) what failure modes does it handle. If deletion leaves a docstring that only answers (b) — e.g., just a return-shape line with no purpose statement — re-flow it to add a one-sentence purpose line in present tense ("Reads X from Y and returns Z."). The validator (`scrub-validator`) is instructed to read each edited docstring start-to-finish; an answer of "I removed the migration text" without a coherent surviving description is a fail, not a pass.

### Per-file edit list (concrete)

| File | Edits |
|------|-------|
| `reflections/__init__.py` | Drop the `ReflectionRun`/"legacy monolith class" bullet on line 9; verify the docstring still reads as a complete contract. |
| `reflections/utils.py` | Replace the "Extracted from scripts/reflections.py (ReflectionRunner class and module-level helpers)" sentence with a short present-tense description: "Shared helpers for all reflection callables. All helpers are pure functions with no shared mutable state." |
| `reflections/auditing.py` | Drop "Extracted from scripts/reflections.py steps:" and the 6-line step-mapping block in the module docstring. Drop "from monolith module level" tail on line 72. Drop `Maps to monolith step:` lines from `run_log_review` (190), `run_documentation_audit` (300), `run_skills_audit` (351), `run_hooks_audit` (393), `run_feature_docs_audit` (467), `run_pr_review_audit` (574). Keep the "Hotfix (sibling of PR #1056)" prose in `run_log_review`. |
| `reflections/behavioral_learning.py` | Drop "Extracted from scripts/reflections.py pipeline:" + the step-arrow line in module docstring. Drop "(guard preserved from monolith step_behavioral_learning)" parenthetical on line 11. Drop "Maps to monolith: step_behavioral_learning (which calls step_episode_cycle_close and step_pattern_crystallization in sequence)" from `run` docstring on lines 31-32. |
| `reflections/daily_report.py` | Drop "Extracted from scripts/reflections.py pipeline:" + step-arrow line in module docstring. **Re-flow the `_collect_reflection_findings` docstring (line 33)**: replace "Reads from the Reflection model (used by the YAML scheduler) rather than ReflectionRun." with a present-tense description that does not name the deleted class — e.g., "Reads from the Reflection model (the YAML scheduler's per-callable state record). Returns a dict of category → list[finding]." Drop "Maps to monolith: step_daily_report_and_notify (which calls step_produce_report ...)" from line 71. |
| `reflections/maintenance.py` | Drop "Extracted from scripts/reflections.py steps:" + 6-line step-mapping block. Drop `Maps to monolith step:` lines from `run_legacy_code_scan` (35), `run_redis_ttl_cleanup` (79), `run_redis_data_quality` (128), `run_branch_plan_cleanup` (251), `run_disk_space_check` (438), `run_analytics_rollup` (468). |
| `reflections/memory_management.py` | Replace "New reflections with no monolith equivalent:" with "Reflection callables:" (or similar present-tense enumerator). |
| `reflections/session_intelligence.py` | Drop "Extracted from scripts/reflections.py pipeline:" + step-arrow line in module docstring; keep "This is a single callable that runs all three sub-steps internally, preserving ordering without depends_on complexity in the YAML scheduler." Drop "Maps to monolith: step_session_intelligence (which calls step_session_analysis, step_llm_reflection, step_auto_fix_bugs in sequence)" from `run` docstring on lines 143-144. |
| `reflections/task_management.py` | Drop "Extracted from scripts/reflections.py steps:" + 2-line step-mapping block. Drop `Maps to monolith step:` lines from `run_task_management` (26) and `run_principal_staleness` (88). |

Line numbers above are approximate (pre-edit) and provided as anchors. The builder must read each file before editing — line numbers will drift as edits are applied.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] No exception handlers are added or modified by this work — handlers in scope are unchanged. Existing tests that cover the handlers continue to apply.

### Empty/Invalid Input Handling
- [x] No function signatures, parameters, or input validation are touched. No new edge cases are introduced.

### Error State Rendering
- [x] No user-visible output paths are touched. Reflection scheduler tests already cover error propagation (`tests/unit/test_reflection_scheduler.py`).

## Test Impact

- [x] `tests/unit/test_reflections_package.py` — VERIFY: imports each `reflections/*` module by dotted path. After scrub, re-import must still succeed (this is the primary regression guard for "I deleted text and accidentally broke a docstring or import"). No code changes expected; success means the test is green without modification.
- [x] `tests/unit/test_reflection_scheduler.py` — VERIFY: scheduler resolves callables via `importlib`. Unchanged behavior, no edit needed.
- [x] `tests/unit/test_reflections_memory.py`, `test_reflections_scheduling.py`, `test_reflections_multi_repo.py`, `test_reflections_report.py`, `tests/integration/test_reflections_redis.py` — VERIFY: all green without changes. Pure docstring edits should not perturb any test.

No existing tests UPDATE / DELETE / REPLACE — all "VERIFY" because the scrub is text-only inside docstrings/comments. If any test fails after the scrub, that is a bug in the scrub (an accidentally-removed code line), not a planned test churn.

## Rabbit Holes

- **Don't rewrite the docstrings beyond what's needed to remove migration narration.** Per the appetite, this is a scrub, not a docstring rewrite. If a docstring's surviving prose is a bit terse but accurate, leave it. Verbose rewrites trigger style debates and inflate review.
- **Don't fix `docs/features/popoto-index-hygiene.md`, `bridge-self-healing.md`, `telegram-history.md`, etc.** Those are tracked by **#1032** and are explicitly out of scope per the issue body.
- **Don't rewrite `docs/features/adding-reflection-tasks.md`.** Tracked by **#1031**.
- **Don't restructure `reflections/` to one-file-per-reflection.** Tracked by **#1028** and would expand this from a Small to a Large.
- **Don't edit the vault `reflections.yaml`** in this PR — see Freshness Check disposition. The repo file is a symlink (when present); the vault file is iCloud-managed and outside this worktree's reach. Tracking that as a separate concern (No-Gos #2).
- **Don't add a generic "no migration narration" lint rule.** The set of files that need scrubbing is small and finite (the 9 listed); a lint rule has more failure modes than the one-time scrub it would automate.

## Risks

### Risk 1: Accidentally remove a load-bearing line of code while editing a docstring
**Impact:** A reflection silently breaks at runtime; daily-report cycle fails after merge.
**Mitigation:** Edits go through `Edit` with surgical `old_string`/`new_string` matches. Builder runs `pytest tests/unit/test_reflections_package.py tests/unit/test_reflection_scheduler.py tests/unit/test_reflections_memory.py tests/unit/test_reflections_scheduling.py tests/integration/test_reflections_redis.py -x -q` after each file. Validator runs the full reflection suite at the end.

### Risk 2: Re-flowed docstring leaves a dangling sentence or broken Sphinx reference
**Impact:** Cosmetic — docstring reads oddly. Caught by `ruff format --check` (will not flag prose, but will flag malformed quote tokens) and by a manual diff read.
**Mitigation:** After each file, the builder re-reads the resulting docstring start-to-finish to confirm it still narrates what the module does. Ruff format runs as a final gate.

### Risk 3: #1028 (one-file-per-reflection refactor) ships first and breaks our base
**Impact:** This PR rebases onto a structure that no longer has these files. The scrub still applies — same annotations would have moved with the files — but as a different set of file paths.
**Mitigation:** Per issue body, ship this PR promptly to land it before #1028's plan stabilizes. If #1028 lands first, the scrub is even cheaper (fewer call-sites to scrub since some annotations would have been deleted in the move) — re-run the grep guard against the new layout and apply edits to the renamed files.

> **Implementation Note (concern: #1028 rebase strategy):** Builder, before opening the PR, run `gh pr list --search "#1028" --state open --json number,title,headRefName` to confirm #1028 has not landed mid-flight. If it has merged: `git fetch origin main && git rebase origin/main`, expect zero conflicts (the move would have deleted exactly the lines this scrub deletes), then re-run the grep guard against the new `reflections/{group}/<name>.py` layout and re-apply per-file edits to the renamed files. If the rebase produces unexpected conflicts, stop and escalate — the conflict pattern is diagnostic of a third-party edit and warrants human review before continuing.

### Risk 4: Convention note in `docs/features/reflections.md` provokes scope creep
**Impact:** Reviewer asks for a broader documentation rewrite.
**Mitigation:** Keep the convention note to literally one bullet ("Numbered-step references (`step_X`) are historical and should not be reintroduced — reflections are registered by name in `config/reflections.yaml`."). If reviewer pushes for more, defer to #1031/#1032.

## Race Conditions

No race conditions identified — all changes are static text edits in source files. No runtime, no concurrency, no shared state.

## No-Gos (Out of Scope)

1. **Documentation cleanup outside `reflections/`** — `docs/features/popoto-index-hygiene.md`, `bridge-self-healing.md`, `telegram-history.md`, `valor-name-references.md`, `claude-code-feature-swot.md`, `documentation-audit.md`, `session-lifecycle*.md`, `pm-dev-session-architecture.md`, `unified-analytics.md`, `sustainable-self-healing.md`, `reflections.md` (deep edits), `reflections-dashboard.md`. These are tracked by **#1032**. The optional one-line convention note in `docs/features/reflections.md` is the only docs touch this PR allows.

   > **Implementation Note (concern: non-overlap with #1031 and #1032):** Builder, when staging the diff, run `git diff --name-only main` and verify the file list is confined to (a) `reflections/*.py`, (b) at most `docs/features/reflections.md` for the optional convention note, and (c) the plan file itself. Any other path under `docs/features/` indicates accidental scope creep into #1031's or #1032's territory — drop those edits before opening the PR. The PR description should explicitly state "Non-overlapping with #1031 (adding-reflection-tasks rewrite) and #1032 (cascade docs cleanup)."
2. **Vault file `~/Desktop/Valor/reflections.yaml` scrub** — the issue cited `config/reflections.yaml:58, 141-142` but that file moved to vault on commit `c2af0960`. The vault file is iCloud-managed and outside this repo's tracking. **Recommend filing a follow-up issue** to scrub the vault YAML at next opportunity (not gated on this PR; the YAML drift is pure migration narration and breaks nothing).
3. **`docs/features/adding-reflection-tasks.md` rewrite** — tracked by **#1031**.
4. **One-file-per-reflection refactor** — tracked by **#1028**.
5. **Code behavior changes** — explicitly forbidden by the issue; this PR is text-only.
6. **Lint rule to prevent reintroduction** — see Rabbit Holes; the convention note is the chosen guard.
7. **Cleaning up `tests/unit/test_reflections_multi_repo.py` historical comments** — that file's line "Previously tested scripts.reflections.ReflectionRunner and step_* methods — removed" is intentional historical test documentation explaining why a test file changed shape. Leaving it documents the test refactor for future readers; not migration narration in product source.

## Update System

No update system changes required. The scrub is a one-shot text edit of repo source files; nothing is deployed, no new dependencies, no new config files, no migration steps. The previously-relocated `config/reflections.yaml` (now vault-managed via `scripts/update/run.py:396` and `scripts/install_worker.sh:48`) is unaffected — this PR doesn't touch it.

## Agent Integration

No agent integration required. Reflections are dispatched by the scheduler (`agent/reflection_scheduler.py`), not by the agent or MCP servers. The agent never reads `reflections/*.py` source as a runtime dependency. No `.mcp.json` change. No bridge change.

## Documentation

### Feature Documentation
- [x] **Optional, single-line addition**: append a "Convention" bullet to `docs/features/reflections.md` (or, if no natural home there, to the "Development Principles" block in `CLAUDE.md`) stating: "Numbered-step references (`step_X`) are historical and should not be reintroduced — reflections are addressed by name in `config/reflections.yaml`." If a clean insertion point cannot be found within 2 minutes of looking, skip — the convention is implicit in the now-clean source.

No other documentation changes. The deeper docs cascade is **#1032**'s scope.

### External Documentation Site
N/A — this repo doesn't publish a docs site for `reflections/`.

### Inline Documentation
- [x] Confirm each edited docstring still describes what the module/callable does (the whole point of the scrub is preserving useful inline docs while removing migration narration).

## Success Criteria

- [x] `grep -rn 'scripts/reflections.py\|Extracted from\|Maps to monolith\|monolith\|ReflectionRun' reflections/` returns zero matches.

  > **Implementation Note (concern: grep verification scope):** This is the canonical pre-merge guard. Run it exactly as written, against `reflections/` only — do NOT broaden to repo-wide. The patterns intentionally target the five known annotation forms: bare module path (`scripts/reflections.py`), docstring header phrase (`Extracted from`), inline step marker (`Maps to monolith`), bare term (`monolith`), and the deleted class root (`ReflectionRun` — matches both `ReflectionRun` and `ReflectionRunner`). The bare-term `monolith` may collide with prose elsewhere in the repo; that's why scope is `reflections/` only. If the grep returns matches in `tests/unit/test_reflections_multi_repo.py` or `docs/plans/completed/`, those are intentional historical references — out of scope per No-Gos #7.

- [x] `git diff main -- reflections/` shows only docstring/comment changes — zero changes inside function bodies.
- [x] `pytest tests/unit/test_reflections_package.py tests/unit/test_reflection_scheduler.py tests/unit/test_reflections_memory.py tests/unit/test_reflections_scheduling.py tests/unit/test_reflections_multi_repo.py tests/unit/test_reflections_report.py tests/integration/test_reflections_redis.py -x -q` is green.
- [x] `python -m ruff format --check reflections/` passes (i.e., black/ruff finds nothing to reformat after edits).
- [x] Each edited module's docstring reads as a complete, present-tense description of what the module does — no orphaned sentence fragments, no comparative framing against a deleted system.
- [x] (If the convention note ships) `docs/features/reflections.md` contains a one-line guard against reintroducing `step_X` references.
- [x] PR description links #1132 with `Closes #1132`.

## Team Orchestration

This is a Small chore. One builder, one validator, one optional documentarian for the convention note.

### Team Members

- **Builder (annotation-scrubber)**
  - Name: `annotation-scrubber`
  - Role: Edit the 9 `reflections/*.py` files per the per-file edit list, run pytest after each file, run `ruff format` at the end.
  - Agent Type: builder
  - Resume: true

- **Documentarian (convention-noter)** (conditional, optional)
  - Name: `convention-noter`
  - Role: Add the one-line convention bullet to `docs/features/reflections.md` if a clean insertion point exists within 2 minutes of inspection; otherwise skip and document the skip in the PR.
  - Agent Type: documentarian
  - Resume: true

- **Validator (scrub-validator)**
  - Name: `scrub-validator`
  - Role: Run the grep guard, the full reflection test suite, `ruff format --check`, and read each docstring start-to-finish for coherence.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Scrub annotations from all 9 reflection files
- **Task ID**: build-scrub
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_reflections_package.py tests/unit/test_reflection_scheduler.py tests/unit/test_reflections_memory.py tests/unit/test_reflections_scheduling.py tests/integration/test_reflections_redis.py -x -q`
- **Informed By**: per-file edit list in Technical Approach
- **Assigned To**: annotation-scrubber
- **Agent Type**: builder
- **Parallel**: false
- For each file in: `reflections/__init__.py`, `reflections/utils.py`, `reflections/auditing.py`, `reflections/behavioral_learning.py`, `reflections/daily_report.py`, `reflections/maintenance.py`, `reflections/memory_management.py`, `reflections/session_intelligence.py`, `reflections/task_management.py`:
  1. Read the file.
  2. Apply the edits described in the per-file edit list.
  3. Re-read the resulting docstrings start-to-finish; if any reads as fragmented, re-flow.
  4. Run `pytest tests/unit/test_reflections_package.py -x -q` (fast import-and-shape check).
- After all files: run `python -m ruff format reflections/` (per user global instruction: ruff format is permitted; ruff lint is not).
- Run the grep guard: `grep -rn 'scripts/reflections.py\|Extracted from\|Maps to monolith\|monolith\|ReflectionRun' reflections/` — expected zero output.
- Run the full reflection test suite: `pytest tests/unit/test_reflections_package.py tests/unit/test_reflection_scheduler.py tests/unit/test_reflections_memory.py tests/unit/test_reflections_scheduling.py tests/unit/test_reflections_multi_repo.py tests/unit/test_reflections_report.py tests/integration/test_reflections_redis.py -x -q`.

### 2. (Optional) Add convention note
- **Task ID**: build-convention-note
- **Depends On**: build-scrub
- **Assigned To**: convention-noter
- **Agent Type**: documentarian
- **Parallel**: false
- Open `docs/features/reflections.md`, find the section that describes how reflections are registered (search for "config/reflections.yaml" or "callable").
- If a natural insertion point exists, append one bullet: "Numbered-step references (`step_X`) are historical and should not be reintroduced — reflections are addressed by name in `config/reflections.yaml`."
- If no clean insertion point exists within ~2 minutes of looking, skip and note the skip in the build report (the convention is implicit in the now-clean source).

### 3. Validate scrub
- **Task ID**: validate-scrub
- **Depends On**: build-scrub, build-convention-note (if executed)
- **Assigned To**: scrub-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn 'scripts/reflections.py\|Extracted from\|Maps to monolith\|monolith\|ReflectionRun' reflections/` — must return zero output.
- Run `git diff main -- reflections/` and confirm changes are confined to docstrings/comments (no edits inside function bodies, signatures, or imports).
- Run the full reflection test suite (same command as task 1).
- Run `python -m ruff format --check reflections/` — must pass.
- Read each edited file's docstrings start-to-finish; report any fragments or comparative framing that slipped through.
- Confirm `docs/features/reflections.md` either has the new bullet or the build report explains why it was skipped.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Grep guard zero matches | `grep -rn 'scripts/reflections.py\|Extracted from\|Maps to monolith\|monolith\|ReflectionRun' reflections/ \| wc -l` | output: 0 |
| Reflection unit tests pass | `pytest tests/unit/test_reflections_package.py tests/unit/test_reflection_scheduler.py tests/unit/test_reflections_memory.py tests/unit/test_reflections_scheduling.py tests/unit/test_reflections_multi_repo.py tests/unit/test_reflections_report.py -x -q` | exit code 0 |
| Reflection integration test passes | `pytest tests/integration/test_reflections_redis.py -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/` | exit code 0 |
| No code-body changes | `git diff --stat main -- reflections/ \| grep -v 'docstring\|comment'` | manual review — diff confined to docstrings/comments |

## Critique Results

**Verdict:** READY TO BUILD (with concerns)

**Revision applied:** 2026-04-23. The following findings from the war room were addressed in this revision pass:

- **BLOCKER (resolved):** The original per-file edit list for `reflections/daily_report.py` was missing line 33's `ReflectionRun` reference inside the `_collect_reflection_findings` docstring. The grep guard would have failed at validation time and bounced the PR back. **Fix:** the `daily_report.py` row in the per-file edit list now explicitly calls out line 33 with a re-flow recipe.
- **Concern 1 (acknowledged, embedded):** Coordination with #1028 (one-file-per-reflection refactor). See Risk 3's Implementation Note for the rebase recipe.
- **Concern 2 (acknowledged, embedded):** Non-overlap with #1031 (adding-reflection-tasks rewrite) and #1032 (cascade docs cleanup). See No-Gos #1's Implementation Note for the diff-staging guard.
- **Concern 3 (acknowledged, embedded):** Grep verification scope. See Success Criteria's Implementation Note for the canonical pre-merge command and its scoping rationale.
- **Concern 4 (acknowledged, embedded):** Docstring re-flow quality — bare deletion is insufficient. See Technical Approach's Implementation Note for the three-question coherence test the validator applies.

Concerns are acknowledged risks, not defects. The plan proceeds to build.

---

## Open Questions

1. **YAML scope drop confirmation.** The issue named `config/reflections.yaml:58, 141-142` as part of the work, but the file moved to vault (`~/Desktop/Valor/reflections.yaml`) on commit `c2af0960` ~13.5 hours before the issue was filed. This plan drops the YAML scope from this PR (No-Gos #2) and recommends a follow-up issue against the vault file. Confirm this is the right call. Alternative: do nothing about the YAML at all — the migration narration in YAML is technically still legacy text but it's not in the repo, so the project-principle violation is contained.

2. **Convention note placement.** The plan treats this as optional and prefers `docs/features/reflections.md` over `CLAUDE.md`. If you'd rather it live in CLAUDE.md (for higher visibility) or be omitted entirely (since clean source code is the convention), say so.

3. **Coordination with #1028.** The issue body recommends shipping this scrub now even though #1028 will move every file this PR touches. If #1028 is closer to landing than the issue body suggested, it may be cheaper to wait and let #1028 absorb the scrub. Worth a quick check: what's #1028's current status?
