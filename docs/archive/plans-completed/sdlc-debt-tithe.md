---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-08-24
tracking: https://github.com/tomcounsell/ai/issues/2959
---

# SDLC Debt-Tithe: Net-Reduce Codebase Surface per Pipeline Run

## Problem

The SDLC pipeline is **net-additive by construction**. Every stage's job is to add something — a plan doc, a feature doc, an index row, a validator hook, an env var, a test — and no stage's job is to remove anything. The only "cleanup" anywhere in the pipeline is worktree deletion and archiving a completed plan to `docs/archive/plans-completed/`. Over time this has produced the current state, quantified in the repo audit that motivated this work:

- **278 feature docs**, append-only (`do-docs` explicitly refuses to delete a feature doc — it flags it for human review instead).
- **21 validator hooks**, one invariant per historical incident.
- **17 one-shot migration scripts** still in-tree (`scripts/migrate_*.py`).
- **86 env vars**, and `config/settings.py` at ~1,200 lines.
- God-modules: `agent/session_health.py` at 6,419 LOC (61 top-level functions), `agent/sdlc_router.py` at 2,344 LOC, `bridge/telegram_bridge.py` at 3,458 LOC.
- **111 fix : 5 refactor** commit ratio over the last 90 days — the system patches itself but never restructures itself.
- **~288K test LOC vs ~207K production LOC.**

**Current behavior:** adding a feature means editing one of the shared god-modules, appending a sentence to a feature doc and its index row, possibly adding a validator hook, an env var, and a migration script — and none of that is ever removed. The cost of the next feature is the sum of all prior features.

**Desired outcome:** each merged PR is **net-reducing** — it ships its feature *and* removes a small, pre-identified piece of accumulated debt — so the codebase surface trends down with every pipeline run instead of up. This must be enforced mechanically, not by discipline.

## Freshness Check

**Baseline commit:** `358c00f15f33c0ad0c134251eb3119e07fcafe0f`
**Issue filed at:** 2026-08-24 (same session; no drift window)
**Disposition:** Unchanged

**File:line references re-verified (the skill/substrate files this plan touches):**
- `.claude/skills-global/do-docs/SKILL.md` — Step 2c "What's Missing" and Edge Cases ("do NOT delete the feature doc") — still present.
- `docs/sdlc/do-plan.md` — "Required Plan Sections" (## Documentation / ## Update System / ## Agent Integration / ## Test Impact) — still the enforced set.
- `.claude/skills-global/do-patch/SKILL.md` — "annotate rather than skip" `# NOTE:` convention — still present.
- `docs/sdlc/do-merge.md` — Plan Migration archives to `docs/archive/plans-completed/`, never deletes — still present.
- `.claude/hooks/validators/validate_documentation_section.py` — the hook pattern to mirror — still present.
- `tools/merge_predicate.py` — the shared merge predicate (group (b) DOCS gate) — still the single enforcement point.

**Commits on main since issue filed:** none (issue filed this session, at this baseline).

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **No prior issues/plans** target net-debt-reduction via the pipeline (`gh issue list --search "debt tithe surface budget bloat"` → empty).
- Adjacent, out-of-band tooling exists but is never wired into the pipeline: the `cruft-auditor` agent, `do-integration-audit`, `de-slop`, and the `merged-branch-cleanup` reflection (which only migrates plans). This plan's contribution is to make reduction a *default per-PR property*, not a manually-invoked sweep.
- The deterministic-gate substrate already exists and is reusable: `tools/merge_predicate.py` (group (b) DOCS gate), the plan-section validator hooks, and the `sdlc-tool` stage/verdict substrate. This plan extends those, rather than inventing a parallel mechanism.

## Research

No relevant external findings — this is purely internal process/tooling work. Proceeding with codebase context and training data.

## Data Flow

The lifecycle of one debt-register item ("tithe") through the pipeline:

1. **Seed**: `data/debt-ledger.json` holds open items, each with a `verify` spec (`deleted` path / `shrink` path+floor) and a size (`S`/`M`/`L`).
2. **Claim (PLAN)**: `/do-plan` fills the new `## Debt Paydown` section by claiming the top open item sized to the PR's appetite. The claim marks the item `claimed` + `claimed_by: <issue-url>`.
3. **Validate (PLAN write)**: the new `validate_debt_paydown_section.py` hook reads the plan; advisory now, blocking later.
4. **Adversarial check (CRITIQUE, later phase)**: a fake/undersized claim → `NEEDS REVISION`.
5. **Execute (BUILD)**: the builder performs the removal (deletes the migration script / superseded doc / dead config) as a normal task.
6. **Verify (MERGE, later phase)**: `tools/debt_ledger.py --verify --pr N --item D-x` confirms the diff actually removed/shrunk the artifact; the merge predicate leg passes/fails on it.
7. **Close**: the checker flips the item to `done`; the surface delta is reported in the merge report.

## Architectural Impact

- **New dependencies:** none. Pure-Python, stdlib-only checker; no new libraries.
- **Interface changes:** new required plan section (`## Debt Paydown`) and a new hook — additive to the plan contract, enforced by a validator like the existing four sections.
- **Coupling:** **decreases** coupling over time (register items target god-module extraction); the mechanism itself adds one small tool + one data file, both independent of existing modules.
- **Data ownership:** the debt register is a single tracked file on `main` (like `docs/plans/`), owned by no process; reads/writes go through `tools/debt_ledger.py`.
- **Reversibility:** high — every PR is individually reviewed and `git`-reversible; the mechanism can be turned off by removing one hook entry.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer (the standard pipeline cast)

**Interactions:**
- PM check-ins: 1-2 (scope: which register items to seed; whether to go blocking)
- Review rounds: 1-2 (the mechanism must itself pass review before it enforces anything)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | Issue/PR verification for the checker's `--verify` leg |
| venv present | `test -x .venv/bin/python` | Run `python -m tools.debt_ledger` and the hook |

No external dependencies. Run via `python scripts/check_prerequisites.py docs/plans/sdlc-debt-tithe.md`.

## Solution

### Key Elements

- **Debt register** (`data/debt-ledger.json`): a machine-readable, tracked ledger of individually-shippable bloat items, seeded from the audit. Schema: `{id, size, what, verify{kind, path, min_lines?}, status(open|claimed|done), claimed_by}`.
- **Checker** (`tools/debt_ledger.py`): three verbs — `list` (open items by size), `verify --pr N --item D-x` (confirm the diff did the removal/shrink), `close --item D-x` (mark done). Verify-only; never mutates the repo.
- **`## Debt Paydown` required plan section**: the fifth mandatory plan section, mirroring `validate_documentation_section.py`. A plan must claim ≥1 register item sized to its appetite, or justify an exemption.
- **Enforcement at the three existing choke points** (phased): critique (`NEEDS REVISION` on a fake claim), review (diff actually retires the item), merge predicate (deterministic leg).

### Flow

Issue → **PLAN** (claim a register item in `## Debt Paydown`) → **CRITIQUE** (verify claim is real/sized) → **BUILD** (perform the removal) → **REVIEW** (confirm diff removed it) → **DOCS** → **MERGE** (predicate verifies via checker) → register item → `done`.

### Technical Approach

- **Enforce in code, not prose.** The register + checker + hook + predicate leg, plus *one short line per skill*. The existing skills already demonstrate the failure mode (749-line `do-sdlc` re-explaining guards that live in `agent/sdlc_router.py`); this mechanism must not repeat it.
- **Deterministic verification.** `deleted` means the path is absent from the diff; `shrink` means the module's LOC decreased by the stated floor. No LLM judgment in the gate — the checker is the single source, with a parity test (mirroring `tests/unit/test_do_merge_docs_gate.py`).
- **Tithe defaults to removal, not refactor.** The low-risk default is deleting a dead migration script / superseded doc / dead env var; god-module extraction is an optional *larger* tithe, never mandatory on a feature PR.
- **Phased rollout, one PR per phase** (the repo already supports a multi-PR issue — the plan stays in `docs/plans/` root until the issue closes):
  - **Phase A (this build):** seed register, add `tools/debt_ledger.py`, add `## Debt Paydown` + `validate_debt_paydown_section.py` in **advisory** mode, register the hook, tests + feature doc.
  - **Phase B:** critique enforces (fake/undersized claim → `NEEDS REVISION`).
  - **Phase C:** merge-predicate blocking leg + parity test.
  - **Phase D:** `do-docs` doc-net-zero + `do-patch` NOTE-cap + surface-delta metric.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/debt_ledger.py` handles malformed `data/debt-ledger.json` (missing file, bad JSON, unknown item id) with a named error, never a silent pass.
- [ ] `validate_debt_paydown_section.py` handles an absent `## Debt Paydown` section (advisory warning now, blocking later) and an empty/boilerplate section.

### Empty/Invalid Input Handling
- [ ] `debt_ledger.py list` on an empty register prints "no open items" and exits 0 (not an error).
- [ ] `verify` on an unclaimed/unknown item id exits non-zero with a specific message (never "passes").
- [ ] `verify` on a PR that did not touch the item's path fails closed.

### Error State Rendering
- [ ] The merge-report surface-delta line renders "unavailable" (not a false zero) when the delta cannot be computed.
- [ ] The advisory hook prints a human-actionable warning naming the missing section, not a bare exit.

## Test Impact

- [ ] `tests/unit/test_debt_ledger.py` — NEW: cover `list`/`verify`/`close` on a fixture register (deleted-path pass/fail, shrink-floor pass/fail, unknown-id, malformed JSON, empty register).
- [ ] `tests/unit/test_validate_debt_paydown_section.py` — NEW: cover missing/empty/valid sections, advisory exit-0 vs blocking exit-2 behavior.
- [ ] `tests/unit/test_do_merge_docs_gate.py` — UPDATE (Phase C only): add the debt-tithe leg to the shared-predicate parity cases.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE (Phase B only): if the tithe becomes a G-guard, add it to the guard parity table.

No existing tests break in Phase A (the hook is advisory and the checker is new); the two UPDATE entries above are gated to later phases.

## Rabbit Holes

- **Actually refactoring the god-modules in this plan.** The register only *lists* them; extraction happens organically in future PRs that claim those items. Do not build an automated refactoring engine.
- **Building a dashboard/UI for the surface-delta metric.** A counter + a merge-report line is enough; a UI is a separate project.
- **A retroactive big-bang cleanup sweep** of the 17 migration scripts / 278 docs. Deliberately avoided — reduction must be organic and reviewed per PR.
- **Auto-wiring every future bloat source** (hooks, env vars) into the register in one go. Phase D's feed loop covers this incrementally.

## Risks

### Risk 1: Agents game the tithe (claim a "removal" that is a rename or fake shrink)
**Impact:** The gate becomes theater; surface keeps growing.
**Mitigation:** The checker verifies `deleted` = path absent from the diff, `shrink` = LOC decreased by the floor. Critique/review remain the adversarial backstop. The parity test locks the predicate to the checker.

### Risk 2: The tithe becomes rubber-stamp ("retire one trivial item")
**Impact:** Reduction is real but negligible.
**Mitigation:** Size-to-appetite rule; items are pre-sized and a "small" item is still a real artifact (dead script, superseded doc, dead env var).

### Risk 3: The register itself rots (stale items, nobody feeds new debt)
**Impact:** The tithe runs dry and the mechanism stalls.
**Mitigation:** `do-patch`/`do-pr-review` append newly-noticed debt; a scheduled reflection audits staleness (the repo already runs reflection workers).

### Risk 4: The gate becomes expensive (like the removed 20-minute merge test)
**Impact:** Merge slows or wedges.
**Mitigation:** The surface-delta leg is O(1) on the diff — an LOC count and a diff grep, seconds, never a test run.

## Race Conditions

### Race 1: Two lanes claim the same register item
**Location:** `data/debt-ledger.json` (shared on `main`)
**Trigger:** Two concurrent plans read the same top open item.
**Data prerequisite:** item `status == open` at claim time.
**State prerequisite:** claim writes `claimed` + `claimed_by` atomically.
**Mitigation:** Claim happens at plan time and marks the item; a second claimer sees `claimed` and picks the next item. Register edits are committed on `main` like plans (small, single-purpose commits).

### Race 2: Item removed on the branch, register closed before merge
**Location:** `tools/debt_ledger.py close` vs the PR's merge
**Trigger:** The builder flips `status=done` in the branch while the PR is still open.
**Data prerequisite:** the removal is in the PR diff.
**State prerequisite:** the PR must be open with the removal present.
**Mitigation:** `close` is driven by the merge gate after `verify` succeeds, not by the builder pre-emptively; `verify` re-checks the diff at merge time.

## No-Gos (Out of Scope)

- [DESTRUCTIVE] No one-shot bulk deletion of the 17 migration scripts, 278 feature docs, or god-module code as a single sweep. Reduction happens only as individually-reviewed, checker-verified register items inside ordinary PRs — review-before-execute is the safety mechanism for irreversible bulk change.
- [DESTRUCTIVE] `tools/debt_ledger.py` is verify-only: it never deletes files or mutates the repo, only flips a register item's `status` after a PR has already removed the code. Automated, unreviewed deletion is out of scope.

## Update System

- The new hook (`validate_debt_paydown_section.py`) must be registered in `.claude/hooks/manifest.toml` (the source of truth for hook registration; `~/.claude/settings.json` is generated from it) so `/update` propagates it to every machine.
- `data/debt-ledger.json` and `tools/debt_ledger.py` are tracked files — they propagate via ordinary git + `/update`, no special step.
- No new dependencies or migration steps for existing installations.

## Agent Integration

No new CLI entry point required — the agent invokes `python -m tools.debt_ledger` via Bash, consistent with the other `tools/*.py` checkers (e.g. `python -m tools.merge_predicate`). The plan-section hook runs automatically on plan writes; no bridge wiring needed. Integration is exercised by the `## Verification` rows that run the checker directly.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/sdlc-debt-tithe.md` describing the register schema, the checker verbs, the `## Debt Paydown` plan-section contract, and the phased enforcement rollout.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/sdlc/do-plan.md` "Required Plan Sections" to name `## Debt Paydown` as the fifth required section.

### Inline Documentation
- [ ] Docstring on each `debt_ledger.py` verb documenting its contract and exit codes.

## Success Criteria

- [ ] `data/debt-ledger.json` exists and lists ≥10 seeded items (migration scripts, superseded docs, god-module extractions, dead config).
- [ ] `python -m tools.debt_ledger list` exits 0 and prints open items grouped by size.
- [ ] `python -m tools.debt_ledger verify` passes on a PR that deletes the claimed path and fails on one that does not.
- [ ] `docs/sdlc/do-plan.md` names `## Debt Paydown` among the required sections.
- [ ] `validate_debt_paydown_section.py` is registered in `.claude/hooks/manifest.toml` and runs in advisory mode.
- [ ] `docs/features/sdlc-debt-tithe.md` and its `docs/features/README.md` row exist.
- [ ] Tests pass (`/do-test`).
- [ ] Lint/format clean (`python -m ruff check .` and `python -m ruff format --check .`).

## Team Orchestration

### Team Members

- **Builder (debt-ledger-tool)**
  - Name: debt-ledger-builder
  - Role: Implement `tools/debt_ledger.py` + seed `data/debt-ledger.json` + the `validate_debt_paydown_section.py` hook + manifest entry.
  - Agent Type: builder
  - Resume: true

- **Validator (debt-ledger-tool)**
  - Name: debt-ledger-validator
  - Role: Verify the checker/hook against the Success Criteria and run the new unit tests.
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Seed the debt register
- **Task ID**: build-debt-register
- **Depends On**: none
- **Assigned To**: debt-ledger-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `data/debt-ledger.json` with ≥10 open items seeded from the audit (the 17 `scripts/migrate_*.py` as `deleted` items; god-module extraction as `shrink` items; superseded feature docs as `deleted` items; dead env vars/config as `shrink` items).
- Each item carries `size`, `what`, `verify{kind, path, min_lines?}`, `status`, `claimed_by`.

### 2. Implement the checker
- **Task ID**: build-debt-ledger-tool
- **Depends On**: build-debt-register
- **Assigned To**: debt-ledger-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `tools/debt_ledger.py` with `list`, `verify --pr N --item D-x`, `close --item D-x`.
- `verify` reads the PR diff (`gh pr diff`/git) and checks `deleted` (path absent) or `shrink` (LOC delta ≤ floor); fail closed on unknown id / malformed register / no-op diff.

### 3. Implement the plan-section hook
- **Task ID**: build-debt-paydown-hook
- **Depends On**: build-debt-ledger-tool
- **Assigned To**: debt-ledger-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `.claude/hooks/validators/validate_debt_paydown_section.py` mirroring `validate_documentation_section.py`, in **advisory** mode (warn + exit 0).
- Add the `## Debt Paydown` section to `docs/sdlc/do-plan.md` "Required Plan Sections" and to `PLAN_TEMPLATE.md`.

### 4. Register the hook and write tests
- **Task ID**: build-debt-register-hook
- **Depends On**: build-debt-paydown-hook
- **Assigned To**: debt-ledger-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a `validate_debt_paydown_section` entry to `.claude/hooks/manifest.toml`.
- Write `tests/unit/test_debt_ledger.py` and `tests/unit/test_validate_debt_paydown_section.py`.

### 5. Validate
- **Task ID**: validate-debt-ledger
- **Depends On**: build-debt-register-hook
- **Assigned To**: debt-ledger-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the new unit tests, `python -m ruff check .`, `python -m ruff format --check .`.
- Verify each Success Criteria checkbox.

### 6. Documentation
- **Task ID**: document-debt-tithe
- **Depends On**: validate-debt-ledger
- **Assigned To**: debt-ledger-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-debt-tithe.md` and its `docs/features/README.md` row.

> **Phases B/C/D are separate PRs on this same issue** (multi-PR issue; the plan stays in `docs/plans/` root until the issue closes). Phase B: critique enforcement. Phase C: merge-predicate leg + parity test. Phase D: `do-docs` doc-net-zero + `do-patch` NOTE-cap + surface-delta metric. Each phase is dispatched as a fresh `/do-build` resuming this plan.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Register lists open items | `python -m tools.debt_ledger list` | exit code 0 |
| Checker verifies a removal | `python -m tools.debt_ledger verify --pr <PR> --item D-001` | exit code 0 |
| Hook is advisory on a missing section | `python .claude/hooks/validators/validate_debt_paydown_section.py <empty-plan-fixture.md>` | exit code 0 |
| New unit tests pass | `pytest tests/unit/test_debt_ledger.py tests/unit/test_validate_debt_paydown_section.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Plan names the new section | `grep -c 'Debt Paydown' docs/sdlc/do-plan.md` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Should the tithe be **size-matched strictly** (a Large PR must retire a Large item) or **floor-matched** (any PR retires at least one Small item)? Recommend floor-matched for Phase A to avoid blocking small fixes.
2. Should Phase C's merge-predicate leg **fail closed** (block merge) or **fail open with a warning** until the register has been live for a few weeks? Recommend fail-closed from Phase C, since the register is seeded before it.
3. Is the register a single `data/debt-ledger.json`, or should it partition by area (docs vs code vs config)? Recommend single-file for simplicity, split later if it grows past ~100 items.
