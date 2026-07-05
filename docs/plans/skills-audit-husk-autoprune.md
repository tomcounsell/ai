---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1902
last_comment_id:
---

# skills-audit: auto-prune rule-19 "empty" husk directories under --fix

## Problem

When `do-skills-audit` moved from `.claude/skills/` to `.claude/skills-global/`
(commit `19937d75`), git left the old `.claude/skills/do-skills-audit/` directory
behind in every working tree. Git does not track empty directories, so after the
tracked files moved, all that remained on disk was build junk (`scripts/__pycache__`).
Rule 19 (`rule_19_husk_directories`) excludes `__pycache__`/`.DS_Store` from its
contents list, so it saw an otherwise-empty directory and reported:

> Husk directory: no SKILL.md (empty) — delete or restore

The `skills-audit` reflection observed this FAIL on 2 consecutive runs and filed
issue #1902.

**Current behavior:**
- Rule 19 **detects** husks (`audit_skills.py:625`) and runs at the fleet level
  (`audit_skills.py:985`), but nothing ever prunes them.
- `apply_fixes()` (`audit_skills.py:705`) — the only auto-fix path — runs
  **per-skill** for directories that HAVE a `SKILL.md` (called at `:795`). A husk
  has no `SKILL.md`, so `--fix` never touches it.
- The reflection invokes the audit with `--no-sync --json` only
  (`reflections/audits/skills_audit.py:262`) — no `--fix` — so the reflection is
  detection-only by design.
- Net result: an "empty" husk (contents are only ignored build artifacts) recurs
  indefinitely on any machine where the stale directory lingers, with no automated
  remediation. An operator must `rm -rf` it by hand.

**Desired outcome:**
`audit_skills.py --fix` prunes rule-19 "empty" husk directories automatically —
directories with no `SKILL.md` whose only remaining contents are `__pycache__` /
`.DS_Store`. Husks that still contain real orphaned files are left untouched and
still surface as FAIL findings, preserving the human delete-or-restore decision
for anything that might hold real work. A one-command operator remediation
(`audit_skills.py --fix --no-sync`) is documented so a future husk is cleared in
one step instead of a manual `rm -rf`.

## Freshness Check

**Baseline commit:** `63e43118578bdca30ee0d653737549f28e26f981`
**Issue filed at:** 2026-07-05T04:47:25Z
**Disposition:** Minor drift (acute symptom already resolved; prevention work stands)

**File:line references re-verified (against `.worktrees/sdlc-1902`):**
- `.claude/skills-global/do-skills-audit/scripts/audit_skills.py:625` — `rule_19_husk_directories` detects husks, exempts `__pycache__`/`.DS_Store` from contents — still holds.
- `.claude/skills-global/do-skills-audit/scripts/audit_skills.py:705` / `:795` — `apply_fixes()` runs per-skill only, on dirs with a `SKILL.md` — still holds.
- `.claude/skills-global/do-skills-audit/scripts/audit_skills.py:984-988` — fleet-level rules (rule 19, rule 20) run but no prune step exists — still holds.
- `reflections/audits/skills_audit.py:262` — reflection invokes `[python, audit_script, "--no-sync", "--json"]`, no `--fix` — still holds.

**Cited sibling issues/PRs re-checked:**
- #1901 — sibling rule-19 FAIL for the `logs` skill, handled by a parallel agent. OUT OF SCOPE here; do not touch the `logs` skill or its files.
- #1883 (commit `56124515`) — renovated the 20-rule lint that introduced rule 19. Merged; provides the code this plan extends.

**Commits on main since issue was filed (touching referenced files):** none.
`git log --since=2026-07-05T04:47:00Z -- audit_skills.py test_skills_audit.py` returns nothing.

**Active plans in `docs/plans/` overlapping this area:**
- `skills-architecture-audit.md` (#1883, status Planning) — a broad architecture audit
  of the whole skill fleet (dispositions, model tiers). It does NOT touch husk
  auto-pruning. No scope conflict; this narrow tooling fix can land independently.

**Notes:** The acute husk is already gone from the primary checkout —
`audit_skills.py --json --no-sync` currently reports zero rule-19 findings. A pure
cleanup PR would be empty, so this plan is the durable prevention fix per the
project's "prevention over cleanup, guards at creation sites" principle.

## Prior Art

- **Issue #1901**: `skills-audit FAIL: logs (rule 19)` — same rule-19 husk failure
  for a different directory. Handled by a parallel agent. Confirms the husk class is
  systemic, not a one-off — a durable auto-prune is the right lever.
- **PR #1883** (`56124515`): "Renovate do-skills-audit: 20-rule lint..." — introduced
  rule 19 and the `apply_fixes()` auto-fix path. This plan extends both.
- **Commit `19937d75`**: the `skills/` → `skills-global/` split that created the husk.
  Establishes root cause: git's inability to track empty-dir removal on a move.

No prior attempt to auto-prune husks exists — this is the first fix for that gap.

## Research

No relevant external findings — proceeding with codebase context and training data.
This is a purely internal Python tooling change (standard library `shutil`/`pathlib`
only); no external libraries, APIs, or ecosystem patterns involved.

## Data Flow

1. **Entry point**: operator or reflection runs `audit_skills.py` (optionally `--fix`).
2. **Per-skill audit** (`audit_skill`, `:779`): for dirs WITH a `SKILL.md`, `--fix`
   calls `apply_fixes()` which already strips `__pycache__`/`.DS_Store` inside skill dirs.
3. **Fleet-level rules** (`main`, `:978-988`): husk detection (`rule_19_husk_directories`)
   and orphan detection run once across each root. **This is where the new prune step lands.**
4. **Output**: findings serialized to human/JSON; the reflection reads JSON `findings`
   with `rule == 19` to drive its FAIL-streak counter and issue filing.

The fix adds a prune step at stage 3, gated on `--fix`, that runs BEFORE rule-19
detection so a pruned husk no longer appears as a FAIL in the same run.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1883 | Added rule 19 husk detection + per-skill `apply_fixes()` | Detection surfaces the husk but `--fix` only remediates dirs that have a `SKILL.md`; a husk (no `SKILL.md`) is never pruned, so the FAIL recurs every run. |

**Root cause pattern:** the auto-fix path is keyed to skills (things with a
`SKILL.md`), but husks are by definition non-skills. Remediation must live at the
fleet level alongside husk detection, not inside the per-skill loop.

## Appetite

**Size:** Small

**Team:** Solo dev + validator

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Single-file behavior addition plus focused unit tests. The bottleneck is careful
safety review of the delete path, not coding volume.

## Prerequisites

No prerequisites — this work has no external dependencies. Standard library only.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Python venv | `python -c "import shutil, pathlib, yaml"` | Audit script runs |

## Solution

### Key Elements

- **`prune_husk_directories(skills_dir, dir_label)` helper**: mirrors rule 19's
  "empty" test (contents after excluding `__pycache__`/`.DS_Store`). For each
  directory with no `SKILL.md`, not `_`/`.`-prefixed, and no real contents, it
  removes the directory (`shutil.rmtree`) and returns a description string. Husks
  WITH real contents are left in place and returned as skipped (not pruned).
- **Fleet-level `--fix` wiring**: in `main()`, when `args.fix` is set and this is a
  full-fleet run (`not args.skill`), call the prune helper for each root BEFORE
  rule-19 detection, and record each prune as a `Fixed:` PASS finding (consistent
  with how `apply_fixes` reports).
- **Test coverage**: new tests for rule 19 (currently ZERO coverage) and for the
  prune helper — empty husk pruned, real-content husk preserved, `SKILL.md` dir
  ignored, `_`-prefixed dir exempt.

### Flow

Operator runs `audit_skills.py --fix` → fleet-level prune sweep removes empty
husks → rule 19 detection runs on what remains → only real-content husks (if any)
report FAIL → exit 0 when no FAILs remain.

### Technical Approach

- Add `prune_husk_directories(skills_dir: Path, dir_label: str) -> list[str]` next
  to `rule_19_husk_directories`. Reuse the exact contents predicate rule 19 uses
  (`p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"`) so
  "empty" means the same thing in both places — a husk is pruned only if rule 19
  would have labelled it `(empty)`.
- Safety guardrails on the delete path:
  - Only operate on direct children of a known skills root (`skills_dir.iterdir()`).
  - Skip `SKILL.md`-bearing dirs, `_`-prefixed and `.`-prefixed dirs (matches rule 19 exemptions).
  - Skip any dir with real (non-junk) contents — those stay as FAIL for human decision.
  - Wrap `shutil.rmtree` in `try/except OSError` and continue (never crash the audit).
- In `main()` (`:984`), before the `rule_19_husk_directories` loop, add:
  `if args.fix: for label, root in roots: for desc in prune_husk_directories(root, label): report.add(Finding(<dir>, 0, "PASS", f"Fixed: {desc}", dir=label))`.
  Running prune first means a freshly-pruned husk does not also emit a FAIL in the
  same run (self-consistent single-pass remediation).
- No JSON-contract change: pruned dirs appear as existing `Fixed:` PASS findings
  (rule 0), which the reflection already ignores (it only counts `rule == 19` FAILs).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `prune_husk_directories` wraps `shutil.rmtree` in `try/except OSError: continue`. Add a test that a permission/OS error on one husk does not abort the sweep or crash the audit (assert the audit still returns and other husks are still processed).
- [ ] No pre-existing `except Exception: pass` blocks are introduced.

### Empty/Invalid Input Handling
- [ ] Test `prune_husk_directories` against a non-existent / non-dir `skills_dir` — must return `[]` and not raise (mirror rule 19's `if not skills_dir.is_dir(): return`).
- [ ] Test an empty skills root (no children) returns `[]`.
- [ ] This is not agent-output processing — no silent-loop risk.

### Error State Rendering
- [ ] A husk that CANNOT be pruned (real orphaned file present) must still render as a rule-19 FAIL in both human and JSON output — assert the FAIL survives a `--fix` run. This is the key "don't silently swallow real orphans" guarantee.

## Test Impact

- [ ] `tests/unit/test_skills_audit.py` — UPDATE: add `from audit_skills import prune_husk_directories` to the import block and two new test classes (`TestRule19HuskDirectories`, `TestPruneHuskDirectories`). Rule 19 currently has zero coverage, so nothing existing breaks; the change is purely additive.

No other existing tests are affected — the change adds a new fleet-level function
and one `--fix`-gated call site; it does not modify any existing rule, the JSON
contract, or `apply_fixes()` behavior, so `test_skills_audit_reflection.py`,
`test_skills_audit.py`'s existing cases, and `test_reflections_package.py` remain valid.

## Rabbit Holes

- **Do NOT touch the `logs` skill or issue #1901's files** — that sibling rule-19
  husk is owned by a parallel agent; overlap causes merge conflicts.
- **Do NOT make the reflection invoke `--fix`.** The reflection is deliberately
  read-only (it files issues, does not mutate repos). Auto-mutating a target repo
  from a nightly reflection is a much larger blast radius and a separate decision.
  Ship `--fix`-gated pruning + documented one-command remediation instead.
- **Do NOT generalize to auto-deleting husks with real contents.** Real orphaned
  files may hold un-migrated work; the human delete-or-restore decision must remain.
- **Do NOT add a `.gitignore` husk-prevention mechanism** or try to make git track
  empty-dir removal — that is a losing battle and out of scope.

## Risks

### Risk 1: Over-eager deletion removes a directory that held real work
**Impact:** Silent loss of un-migrated orphaned files.
**Mitigation:** Prune only when the exact rule-19 "empty" predicate holds (no files
after excluding `__pycache__`/`.DS_Store`). Real-content husks are never deleted —
they still FAIL. A dedicated test asserts a real-content husk survives `--fix`.

### Risk 2: Pruning a dir outside the intended skills roots
**Impact:** Accidental deletion elsewhere on disk.
**Mitigation:** Iterate only direct children of a known root (`SKILLS_DIR` /
`PROJECT_SKILLS_DIR` via `roots`); honor rule 19's `_`/`.`-prefix exemptions;
never recurse into arbitrary paths for deletion (only `shutil.rmtree` the child dir).

## Race Conditions

No race conditions identified — `audit_skills.py` is a synchronous, single-threaded
CLI. Fleet-level pruning runs once, sequentially, after per-skill audits complete.
No shared mutable state, no async, no cross-process coordination.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1901] Rule-19 husk failure for the `logs` skill — filed as its
  own issue and handled by a parallel agent. Touching it here would collide.
- [DESTRUCTIVE] Auto-pruning husks that contain real (non-junk) orphaned files —
  deliberately excluded; the human delete-or-restore decision is the safety
  mechanism for anything that might hold real work. Anti-criterion asserted in
  `## Verification`.

## Update System

No update system changes required — this is a change to an existing script
(`.claude/skills-global/do-skills-audit/scripts/audit_skills.py`) already synced to
every machine by `scripts/update/hardlinks.py` (`sync_claude_dirs()` hardlinks the
whole `do-skills-audit` dir). No new dependencies, config files, or migration steps.
The next `/update` propagates the fixed script automatically.

## Agent Integration

No agent integration required — this is a change to an existing audit script and its
nightly reflection consumer. No new CLI entry point, no `pyproject.toml [project.scripts]`
change, no `.mcp.json`/MCP surface, and no bridge import. The reflection
(`reflections/audits/skills_audit.py`) already invokes the script and reads its JSON
contract, which is unchanged.

## Documentation

### Feature Documentation
- [ ] Update `.claude/skills-global/do-skills-audit/SKILL.md` (or its `references/`)
      to document the new `--fix` husk-pruning behavior and the one-command operator
      remediation: `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py --fix --no-sync`.
- [ ] Update the module docstring in `audit_skills.py` to note that `--fix` now
      prunes rule-19 "empty" husks (the docstring currently lists detection only).

### Inline Documentation
- [ ] Docstring on `prune_husk_directories` explaining the "empty" predicate and the
      real-content preservation guarantee.

No `docs/features/` entry is warranted — this is a maintenance behavior on an existing
internal lint, not a user-facing feature. The skill's own SKILL.md is the correct
home for the operator remediation note.

## Success Criteria

- [ ] `prune_husk_directories` exists and removes a husk whose only contents are `__pycache__`/`.DS_Store`.
- [ ] A husk with a real orphaned file is NOT pruned and still reports rule-19 FAIL after a `--fix` run.
- [ ] `audit_skills.py --fix` on a fleet containing an empty husk removes it and reports a `Fixed:` PASS.
- [ ] Rule 19 has unit-test coverage (empty husk, real-content husk, `SKILL.md` dir, `_`-prefixed dir).
- [ ] The JSON contract is unchanged (reflection still parses findings; `rule == 19` FAILs only for real-content husks).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `main()` references `prune_husk_directories` under an `args.fix` guard.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (audit-prune)**
  - Name: `audit-prune-builder`
  - Role: Implement `prune_husk_directories`, wire it into `main()` under `--fix`, update docstrings/SKILL.md.
  - Agent Type: builder
  - Domain: none (stdlib file ops)
  - Resume: true

- **Builder (tests)**
  - Name: `audit-test-builder`
  - Role: Add `TestRule19HuskDirectories` + `TestPruneHuskDirectories` to `tests/unit/test_skills_audit.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (audit-prune)**
  - Name: `audit-prune-validator`
  - Role: Verify success criteria, run the audit against a synthetic husk fixture, confirm real-content husk survives.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 core agents as listed in the template. No specialist tier needed.

## Step by Step Tasks

### 1. Implement husk pruning
- **Task ID**: build-prune
- **Depends On**: none
- **Validates**: tests/unit/test_skills_audit.py (create new cases)
- **Assigned To**: audit-prune-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `prune_husk_directories(skills_dir: Path, dir_label: str) -> list[str]` in `audit_skills.py`, adjacent to `rule_19_husk_directories`. Reuse the exact rule-19 contents predicate; delete only when no real contents remain; wrap `shutil.rmtree` in `try/except OSError: continue`; return description strings for pruned dirs.
- In `main()` (before the `rule_19_husk_directories` loop at `:984`, gated on `if args.fix and not args.skill`), call the helper per root and add each result as a `Finding(<dir>, 0, "PASS", f"Fixed: {desc}", dir=label)`.
- Update the `audit_skills.py` module docstring to state `--fix` prunes rule-19 empty husks.
- Add a docstring to `prune_husk_directories`.

### 2. Add test coverage
- **Task ID**: build-tests
- **Depends On**: build-prune
- **Validates**: tests/unit/test_skills_audit.py
- **Assigned To**: audit-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Import `prune_husk_directories` in the test module.
- `TestRule19HuskDirectories`: husk with only `__pycache__` → FAIL `(empty)`; husk with a real file → FAIL `(contains: ...)`; dir with `SKILL.md` → no finding; `_`-prefixed dir → exempt; non-existent dir → `[]`.
- `TestPruneHuskDirectories`: empty husk (only junk) is removed and reported; real-content husk is preserved; `SKILL.md` dir untouched; `_`-prefixed dir untouched; OSError on one husk does not abort the sweep.

### 3. Update skill documentation
- **Task ID**: document-remediation
- **Depends On**: build-prune
- **Assigned To**: audit-prune-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Document the `--fix` pruning behavior and the one-command operator remediation in `do-skills-audit`'s SKILL.md / references.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-prune, build-tests, document-remediation
- **Assigned To**: audit-prune-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_skills_audit.py -q`.
- Build a synthetic fixture: an empty husk (only `__pycache__`) plus a real-content husk under a temp skills root; assert `--fix` prunes the former, keeps the latter, and the latter still FAILs.
- Confirm all success criteria met; generate report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Audit unit tests pass | `pytest tests/unit/test_skills_audit.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/skills-global/do-skills-audit/scripts/audit_skills.py` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/skills-global/do-skills-audit/scripts/audit_skills.py tests/unit/test_skills_audit.py` | exit code 0 |
| Prune helper exists | `grep -c "def prune_husk_directories" .claude/skills-global/do-skills-audit/scripts/audit_skills.py` | output contains 1 |
| Wired under --fix | `grep -n "prune_husk_directories" .claude/skills-global/do-skills-audit/scripts/audit_skills.py` | output contains main |
| No live rule-19 FAILs | `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py --json --no-sync \| python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(1 for f in d['findings'] if f.get('rule')==19 and f.get('severity')=='FAIL'))"` | output contains 0 |
| Anti-criterion: real-content husks not force-deleted | `grep -n "rmtree" .claude/skills-global/do-skills-audit/scripts/audit_skills.py \| grep -c "prune_husk"` | match count == 0 |

<!-- Anti-criterion note: the last row is a proxy guard — it asserts `rmtree` in the
     prune path is not applied unconditionally on the same line as the function name
     (i.e. deletion is guarded by the empty-contents check, not a blanket sweep). The
     authoritative guarantee is the unit test asserting a real-content husk survives
     `--fix`; this grep is the cheap mechanical companion. -->

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None. Scope is well-bounded: the root cause, prevention surface (exact file:line),
and sibling-issue boundary (#1901) are all verified in code. Ready for critique.
