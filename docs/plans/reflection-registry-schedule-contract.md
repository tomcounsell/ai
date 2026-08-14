---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-14
tracking: https://github.com/yudame/ai/issues/2734
last_comment_id: 5286094986
---

# Reflection registry: schedule contract and worktree-safe resolution

## Problem

The reflections registry is a private, gitignored YAML file. Two independent defects sit on top of it, and the second hides the first.

**Defect 1 — the required-fields test encodes a narrower contract than the loader.**
`tests/unit/test_reflection_scheduler.py:667` asserts that *every* registry entry carries an `every` key. The loader does not require that: `ReflectionEntry.validate()` (`agent/reflection_scheduler.py:172`) requires only a `schedule`, and the normalizer at `agent/reflection_scheduler.py:266-283` accepts `every:` / `cron:` (+`cron_tz:`) / `at:` and folds all three into one `schedule` string. The registry's `sdlc-upvote-pickup` entry — added with `f1d86255a` / PR #2721 — is the first to use `cron:`, so it is the first entry the stale assertion rejects. The entry is valid and the reflection runs correctly; the test is wrong.

**Defect 2 — the registry is unresolvable from a worktree under launchd.**
`_resolve_registry_path` (`agent/reflection_scheduler.py:69-95`) deliberately skips the `~/Desktop` vault whenever `VALOR_LAUNCHD` is set (a macOS TCC hang workaround that this repo already paid for once, in the June 2026 worker wedge). It then falls through to `Path(__file__).parent.parent / "config" / "reflections.yaml"` — which its docstring at line 75 calls *"in-repo fallback, always present"*. That is false. `config/reflections.yaml` is gitignored (`.gitignore:8`) and is materialized only in the primary checkout, by `install_reflection_worker.sh` / `install_email_bridge.sh` / `scripts/update/env_sync.py::sync_reflections_yaml`. **No worktree ever has it.** The final `return` is the one branch with no `exists()` check, so the resolver hands back a path that is not there.

The two stack: SDLC lane work and the nightly regression detector both run in `.worktrees/{slug}/` with `VALOR_LAUNCHD=1` inherited from the launchd-managed worker. There, ten registry-reading assertions raise `FileNotFoundError` on a correctly-absent path — which reads like infrastructure noise, and buries Defect 1 underneath it.

**Current behavior:**

- In a worktree under `VALOR_LAUNCHD=1`: 9 call sites in `tests/unit/test_reflection_scheduler.py` and 1 in `tests/unit/test_plan_migration_invariant.py:78` resolve to a nonexistent path and error. The nightly detector reports a path error instead of the contract error it was written to catch.
- `load_registry()` compounds this by failing open — `return []` at both `agent/reflection_scheduler.py:230-241` (the `~/Desktop` realpath guard) and `243-245` (missing path). So `TestRegistryLoading::test_load_registry_returns_only_enabled` iterates an empty list and **passes while asserting nothing**, and any production process inheriting `VALOR_LAUNCHD=1` from a worktree silently schedules **zero reflections** behind a single WARNING.
- In the primary checkout, Defect 1 is plainly red: `TestRegistryIntegrity` → `2 failed, 3 passed`.

**Desired outcome:**

- The registry resolves from *any* checkout — worktree or primary — without weakening the launchd TCC guard.
- The required-fields test accepts every schedule shape the loader accepts, and still fails loudly on an entry with no schedule at all.
- When the resolver genuinely cannot find a registry anywhere, that surfaces as one legible error rather than ten `FileNotFoundError`s.

## Freshness Check

**Baseline commit:** `a9016cbdc`
**Issue filed at:** 2026-08-12T20:19:05Z
**Disposition:** Minor drift — one finding materially revises an acceptance criterion.

**File:line references re-verified:**

| Reference | Issue's claim | Status |
|---|---|---|
| `agent/reflection_scheduler.py:69-95` | `_resolve_registry_path`, skips vault under `VALOR_LAUNCHD` | Still holds, exact lines |
| `agent/reflection_scheduler.py:75` | Docstring claims "always present" | Still holds, exact line |
| `agent/reflection_scheduler.py:172` | `validate()` requires only `schedule` | Still holds, exact line |
| `agent/reflection_scheduler.py:266-280` | Unified-grammar normalization | Drifted slightly — now `266-283` |
| `agent/reflection_scheduler.py:230-241` | `~/Desktop` realpath guard returns `[]` | Still holds, exact lines |
| `agent/reflection_scheduler.py:243-245` | Missing-path fail-open | Still holds, exact lines |
| `tests/unit/test_reflection_scheduler.py:667` | `assert "every" in entry` | Still holds, exact line |
| `tests/unit/test_reflection_scheduler.py:51-58` | `_entry_interval_seconds` is `every:`-only | Still holds; single call site at line 100, scoped to `pm-briefings` |
| `.gitignore:8` | Excludes `config/reflections.yaml` | Still holds |

**Cited sibling issues/PRs re-checked:**

- **#2810** — OPEN. Registry schedules `expectation-reconciler` against `reflections.expectation_reconciler`, a module that does not exist. **This is the one material drift.** At `a9016cbdc`, `TestRegistryIntegrity::test_all_callables_resolve` fails on the *primary* checkout with `ModuleNotFoundError`, independent of anything in this plan. The issue comment claiming that node "was not independently red" is now stale. Consequence: this work cannot turn `TestRegistryIntegrity` fully green on its own, and the acceptance criteria are written around that.
- **#2708** — OPEN, plan `docs/plans/job-expectations-obligation-primitive.md` is `status: Ready` with no PR. That is the work that would create the missing module. Not a blocker for this plan; tracked under #2810.
- **PR #2721** (merged 2026-08-12) — introduced `sdlc-upvote-pickup`, the `cron:` entry that triggers Defect 1. Confirmed present in both registry copies.

**Commits on main since the issue was filed (touching referenced files):** none touched `agent/reflection_scheduler.py` or `tests/unit/test_reflection_scheduler.py`. Both defects reproduce unchanged.

**Registry state re-measured:** 34 entries (was 33 when filed). Still exactly one uses `cron:` — `sdlc-upvote-pickup`. Vault (`~/Desktop/Valor/reflections.yaml`, 19335 bytes) and primary-checkout config copy (10724 bytes) differ in size but agree on the entry set; the config copy is a **real file**, not a symlink, so the lines 230-241 realpath guard does not block reading it.

**Active plans in `docs/plans/` overlapping this area:** none touching the resolver or this test module. `job-expectations-obligation-primitive.md` is adjacent via #2810 but disjoint in code.

## Prior Art

- **PR #1776** — *refactor(reflections): one file per reflection under `reflections/{group}/` (#1028)* — created the re-export shims that `test_all_callables_resolve` exists to guard. Establishes why that test must read the *live* registry rather than a fixture: it is the only thing standing between a hand-edited, gitignored, unreviewed registry and a reflection that errors in production.
- **June 2026 worker wedge** (documented inline at `agent/reflection_scheduler.py:222-241`) — `config/reflections.yaml` was a symlink into `~/Desktop`, silently defeating the `VALOR_LAUNCHD` guard in `_resolve_registry_path` and freezing the worker's event loop. The response was the defense-in-depth realpath check at 230-241 and a switch from symlink to real-file copy (`scripts/update/env_sync.py::sync_reflections_yaml`). **This is the direct reason open-question option 1 is rejected below.**
- **Issue #2810** — the `expectation-reconciler` unresolvable callable. Same test class, different root cause, filed separately. Explicitly out of scope here.
- **Issue #2439** — prior surgery on this same test class (removed `session-liveness-check` assertions). Confirms the class is actively maintained and the right place to fix the contract.

No prior attempt has been made to fix either defect, so there is no "Why Previous Fixes Failed" section.

## Research

No relevant external findings — this is entirely internal: a git worktree layout question and a test contract, both resolved by direct measurement against this repo rather than by documentation lookup. The one externally-defined fact used (git's worktree `.git`-file / `commondir` layout) was verified empirically in spike-1 rather than taken from docs.

## Spike Results

### spike-1: Can a worktree recover its primary checkout root without a subprocess?
- **Assumption**: "A worktree can deterministically locate the primary checkout that owns it."
- **Method**: code-read + direct measurement in `.worktrees/ledger-integrity`
- **Finding**: Two independent routes, both confirmed:
  - `git rev-parse --git-common-dir` → `/Users/valorengels/src/ai/.git`; parent is the primary root. Canonical, handles every layout, but costs a subprocess.
  - The worktree's `.git` is a **file** containing `gitdir: /Users/valorengels/src/ai/.git/worktrees/ledger-integrity`, and that directory holds a `commondir` file containing `../..`. Resolving `gitdir/commondir` yields the primary `.git`; its parent is the primary root. Pure filesystem, no subprocess, no `PATH` dependency.
  - In the primary checkout, `.git` is a directory, so the worktree branch is trivially skipped.
- **Confidence**: high
- **Impact on plan**: Makes option 2 concretely implementable. The plan specifies the filesystem route as primary because `_resolve_registry_path` runs at module import (`REGISTRY_PATH` at line 98) inside a latency-sensitive launchd worker, and a subprocess on that path is exactly the class of hazard this module has already been burned by.

### spike-2: Is Defect 1 confined to one assertion?
- **Assumption**: "Sibling tests carry the same `every:`-only assumption, so a one-line fix is insufficient."
- **Method**: code-read + full-module run against the live registry
- **Finding**: **Assumption is false.** `_entry_interval_seconds` (lines 51-58) is `every:`-only but has exactly one call site (line 100), scoped by name to `entries["pm-briefings"]` (`every: 300s`). It never iterates the registry, so a `cron:` entry cannot reach it. Defect 1 is confined to the assertion at line 667.
- **Confidence**: high
- **Impact on plan**: The issue's "audit other `every:`-only assumptions" acceptance criterion is closed as *audited, none found*. No task is needed for it.

### spike-3: Does the `~/Desktop` realpath guard block reading the primary checkout's copy?
- **Assumption**: "Resolving worktrees to the primary `config/reflections.yaml` will just trip the other launchd guard."
- **Method**: code-read + `os.path.realpath`
- **Finding**: **No.** `/Users/valorengels/src/ai/config/reflections.yaml` is a real file whose `realpath` is itself — it does not resolve under `~/Desktop`. The guard at lines 230-241 passes it. (Had it still been the pre-June-2026 symlink, option 2 would have been dead on arrival; `sync_reflections_yaml` guarantees the real-file form.)
- **Confidence**: high
- **Impact on plan**: Confirms option 2 works end-to-end under `VALOR_LAUNCHD=1`, and adds a Risk entry — the fix is silently coupled to the copy staying a real file.

### spike-4: Is `test_all_callables_resolve` red for reasons outside this plan?
- **Assumption**: "Fixing both defects makes `TestRegistryIntegrity` green."
- **Method**: prototype — ran the class on the primary checkout at `a9016cbdc`
- **Finding**: **Assumption is false.** `2 failed, 3 passed`. The second failure is #2810's `ModuleNotFoundError: No module named 'reflections.expectation_reconciler'`, unrelated to either defect here.
- **Confidence**: high
- **Impact on plan**: Rewrites the success criteria. This plan targets `4 passed, 1 failed` with the sole remaining failure being #2810's, and adds a Verification row that pins the failure to that exact cause so a *different* regression cannot hide behind the carve-out.

## Data Flow

1. **Entry point**: any process importing `agent.reflection_scheduler` — the reflection worker (`python -m reflections`), the dashboard, `scripts/update/reflection_register.py`, or pytest.
2. **`_resolve_registry_path()`** (lines 69-95): `REFLECTIONS_YAML` env override → vault (skipped iff `VALOR_LAUNCHD`) → in-repo `config/reflections.yaml` derived from `__file__`. **This is where a worktree derails**: `__file__` points into the worktree, so the derived path names a file that only ever exists in the primary checkout.
3. **`REGISTRY_PATH`** (line 98): the result is frozen at import time into a module-level constant.
4. **`load_registry()`** (lines 220-245): re-resolves, applies the `~/Desktop` realpath guard, then fails open with `[]` if the path is missing.
5. **Output, production**: `ReflectionScheduler` ticks over an empty entry list. Zero reflections scheduled, one WARNING logged, no crash.
6. **Output, tests**: the 10 test call sites bypass `load_registry()` and `open()` the resolved path directly — so they get a raw `FileNotFoundError` rather than the fail-open `[]`.

The fix lands at step 2, which is the single point every downstream consumer funnels through.

## Architectural Impact

- **New dependencies**: none. Standard-library `pathlib` reads of git's on-disk worktree metadata; no `git` subprocess, no new package.
- **Interface changes**: `_resolve_registry_path()` keeps its signature and return type. Its contract tightens in one way — it logs an error when it exhausts every candidate, instead of returning a nonexistent path silently.
- **Coupling**: adds a narrow, read-only coupling from the scheduler to git's worktree metadata layout. Contained in one helper, and degrades to today's behavior on any parse failure.
- **Data ownership**: unchanged. The vault remains canonical; `config/reflections.yaml` remains the launchd-safe install-time copy. Worktrees gain **read** access to the primary checkout's copy and nothing more.
- **Reversibility**: high — one helper function and one added candidate step, revertible in a single commit.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (the design decision is made in this plan, with evidence)
- Review rounds: 1

Two files change. The cost here is in getting the decision right and in proving the fix under an environment that is awkward to reproduce, not in volume of code.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Reflections registry present on this machine | `test -f ~/Desktop/Valor/reflections.yaml -o -f config/reflections.yaml` | The tests read the live registry; without either copy there is nothing to assert against |
| A registered git worktree to verify against | `python3 -c "import subprocess,sys; out=subprocess.run(['git','worktree','list'],capture_output=True,text=True).stdout; sys.exit(0 if '.worktrees/' in out else 1)"` | Defect 2 only reproduces in a worktree |

## Solution

### Key Elements

- **Primary-checkout fallback in the resolver** — when this checkout's `config/reflections.yaml` is absent, locate the checkout that owns this one and read its copy. Fixes production and all ten test call sites at a single point.
- **Exhausted-candidates diagnostic** — the resolver stops returning a nonexistent path in silence. It logs one legible error naming every candidate it tried.
- **Schedule-shape assertion matching the loader** — the required-fields test requires *exactly one* of `every` / `cron` / `at` / `schedule`, mirroring `agent/reflection_scheduler.py:266-283`.
- **De-vacuuming the enabled-entries test** — `test_load_registry_returns_only_enabled` asserts a non-empty registry before iterating, so it can never again pass by asserting nothing.
- **Truthful docstring** — line 75 stops claiming the in-repo copy is "always present".

### Flow

Process imports `agent.reflection_scheduler` → `_resolve_registry_path()` → `REFLECTIONS_YAML` set and exists? → **use it** → else `VALOR_LAUNCHD` unset and vault exists? → **use vault** → else this checkout's `config/reflections.yaml` exists? → **use it** → else this checkout is a git worktree? → owning checkout's `config/reflections.yaml` exists? → **use it** → else **log one error naming every candidate tried, return the local in-repo path** (unchanged legacy return, so no new exception type escapes into production)

### Technical Approach

**Decision on Defect 2: option 2 — resolve the in-repo path against the primary checkout.** The issue left this open with three candidates. Taking them in turn:

- **Option 1 (fall back to the vault even under `VALOR_LAUNCHD`) — rejected.** It cannot be done in `_resolve_registry_path` alone: `load_registry()` carries an independent realpath guard at lines 230-241 that refuses *any* path resolving under `~/Desktop` while `VALOR_LAUNCHD` is set, so the resolver change would be neutralized one function later and both would have to be unwound together. Unwinding them means betting that the macOS TCC `open()` hang does not reproduce for short-lived non-daemon children — a bet this repo already lost once, at the cost of a wedged worker event loop. The prize for winning it is a fallback we do not need, because option 2 reaches a real file without going near `~/Desktop`.
- **Option 3 (set `REFLECTIONS_YAML` in the tests) — rejected as the primary fix.** It is the established pattern for tests that construct their *own* fixture registry (`tests/unit/test_reflection_arm.py`, `test_reflection_register.py`), but that is a different job from these ten sites, which must read the **live** registry. It also leaves the production hole entirely open: a worktree process under launchd still schedules zero reflections silently. And it scales badly — ten edits now, plus one more for every future site, each of which fails open if forgotten. Comment 5 on the issue puts the decisive argument well: turning `FileNotFoundError` into a skip or a fixture preserves the detection outage, and `test_all_callables_resolve` is the only guard between an unreviewed registry and a reflection that errors every 30 minutes in production.
- **Option 2 — selected.** One change at the single point every consumer funnels through. It fixes the production hole and all ten test sites together, it keeps the tests reading the live registry (preserving the guard's whole purpose), and it never touches `~/Desktop`, so the TCC hazard is sidestepped rather than gambled against.

**Mechanism.** Add a helper that answers "which checkout owns this one?" using git's on-disk worktree metadata, per spike-1:

- If `<repo_root>/.git` is a **directory**, this is the primary checkout — there is nothing to do.
- If it is a **file**, read its single `gitdir: <path>` line. Read `<path>/commondir` and resolve it relative to `<path>` to get the primary `.git`; the primary checkout root is that directory's parent. If `commondir` is missing, fall back to `Path(gitdir).parents[2]`, which is git's fixed `<primary>/.git/worktrees/<name>` layout.
- Wrap the whole helper in a broad `except Exception` that returns `None`. A malformed or unfamiliar git layout must degrade to today's behavior, never raise out of a module-level import.

Deliberately **not** using `git rev-parse --git-common-dir`: `REGISTRY_PATH` is computed at import time (line 98) in a launchd worker, and putting a subprocess on that path reintroduces exactly the shape of hazard — a blocking call during scheduler startup — that the surrounding code exists to prevent.

**Answering the issue's second open question — "should the nightly detector run with `VALOR_LAUNCHD` unset?" No.** That env var is inherited from the launchd worker and is a faithful part of the environment the code must work in. Unsetting it for the detector would make the test environment diverge from production and would paper over the resolver bug rather than fix it. The detector keeps inheriting it; the resolver stops being broken under it.

**Defect 1.** Replace the single `assert "every" in entry` at line 667 with an exactly-one-of check over `every` / `cron` / `at` / `schedule`, matching the normalizer at lines 266-283. Zero keys must fail (missing schedule); two or more must fail (ambiguous — the normalizer would silently pick by precedence). Also assert that `cron_tz` appears only alongside `cron`, since it is meaningless otherwise. Do **not** add an `every:` to the `sdlc-upvote-pickup` registry entry — the entry is valid and the test is what is wrong.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new primary-checkout helper's `except Exception: return None` must have a test asserting observable fallback behavior — feed it a malformed `.git` file (garbage content, no `gitdir:` line) and assert the resolver returns the legacy in-repo path rather than raising.
- [ ] `load_registry()`'s two existing fail-open `return []` paths (lines 230-241, 243-245) are already covered by `test_load_registry_handles_missing_file` (line 148); no change needed, but confirm they still log at their current levels.

### Empty/Invalid Input Handling
- [ ] `.git` file present but empty → helper returns `None`, resolver falls back. Test it.
- [ ] `.git` file with a `gitdir:` pointing at a nonexistent directory → helper returns `None` (no `commondir`, `parents[2]` yields a path whose `config/reflections.yaml` does not exist), resolver falls back. Test it.
- [ ] No registry anywhere (no `REFLECTIONS_YAML`, no vault, no local copy, no primary copy) → resolver logs one error naming every candidate and returns the legacy path. Test the log, not just the return.
- [ ] Registry entry with **no** schedule key at all → required-fields test still fails. This is the anti-regression for Defect 1's fix; without it, "widen the assertion" could degrade to "delete the assertion".

### Error State Rendering
- [ ] The exhausted-candidates error must name each path tried, so an operator reading `logs/` can tell "no vault, no local copy, no primary copy" apart from "wrong path". Assert on the message content via `caplog`, not merely that something was logged.

## Test Impact

- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity::test_all_entries_have_required_fields` — UPDATE: replace `assert "every" in entry` (line 667) with an exactly-one-of `every`/`cron`/`at`/`schedule` check plus the `cron_tz`-implies-`cron` check. This is the node named in the issue.
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryLoading::test_load_registry_returns_only_enabled` — UPDATE: assert `entries` is non-empty before the loop. Today it passes vacuously in a worktree; after the Defect 2 fix it would pass legitimately, and the added assertion is what prevents it from silently regressing to vacuous if resolution breaks again.
- [ ] `tests/unit/test_reflection_scheduler.py` — NEW: unit tests for the primary-checkout helper (worktree hit, primary-checkout no-op, malformed `.git`, empty `.git`, dangling `gitdir:`) and for the exhausted-candidates log.
- [ ] `tests/unit/test_reflection_scheduler.py` — NEW: a required-fields negative test — an entry dict with no schedule key must be rejected — asserted against the extracted predicate, not the live registry (the live registry has no such entry and must not gain one).
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity::{test_registry_yaml_valid,test_no_duplicate_names,test_expected_reflections_present}` — NO CHANGE, but they are the observable proof of the Defect 2 fix: they go from `FileNotFoundError` to passing in a worktree with no edit to their bodies.
- [ ] `tests/unit/test_plan_migration_invariant.py::test_reflections_yaml_registers_merged_branch_cleanup` (line 78) — NO CHANGE: it calls `_resolve_registry_path()` directly and is fixed for free. Must be included in the worktree verification run to confirm that.
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity::test_all_callables_resolve` — NO CHANGE: red for #2810, not for anything here. Explicitly excluded from the green target and pinned by a Verification row.
- [ ] `tests/unit/test_reflection_arm.py`, `tests/unit/test_reflection_register.py`, `tests/unit/test_reflections_main.py` — NO CHANGE: they set `REFLECTIONS_YAML` explicitly, which short-circuits ahead of every branch this plan touches. Listed because they are the largest block of registry-adjacent tests and their non-involvement should be stated rather than assumed.

## Rabbit Holes

- **Re-litigating the launchd TCC guard.** Option 1 makes it tempting to "just test whether the hang still reproduces". Proving a negative about macOS TCC behavior across OS versions is open-ended, and option 2 makes the answer irrelevant. Leave lines 86-90 and 230-241 exactly as they are.
- **Making `config/reflections.yaml` available in worktrees.** Copying or symlinking the registry into each worktree at creation time (`agent/worktree_manager.py`) looks like a tidier fix. It is not: it multiplies copies of a hand-edited private file, they go stale independently, and a symlink into the vault is precisely the June 2026 wedge. Read the one authoritative copy instead.
- **Fixing #2810 while in here.** `test_all_callables_resolve` will be red at the end of this work. It is a registry-content bug awaiting #2708's module and touches nothing in this plan. Resist.
- **Un-failing-open `load_registry()`.** Making a missing registry raise instead of returning `[]` is defensible and is *not* this issue. It would change worker startup behavior on every machine, deserves its own blast-radius analysis, and would obscure whether the resolver fix worked.
- **Generalizing to a repo-wide "find my primary checkout" utility.** One caller needs this. A shared helper in `tools/` invites callers whose failure modes have not been thought through. Keep it private to the scheduler until a second real caller exists.
- **Rewriting `_entry_interval_seconds` to handle `cron:`.** Spike-2 settled this: one call site, scoped to a named `every:` entry. Generalizing it is speculative work with no caller.

## Risks

### Risk 1: Worktree tests now depend on the primary checkout's install-time copy
**Impact:** The worktree fix reads `<primary>/config/reflections.yaml`. On a machine that has never run `/update` or an installer, that file does not exist and worktree tests stay red — with a better error, but still red.
**Mitigation:** The vault branch still covers every non-launchd context, which is the common developer case. The exhausted-candidates error names all three candidates so the remedy (`/update`) is obvious from the log line. The Prerequisites table makes the dependency explicit rather than implicit.

### Risk 2: The primary copy could revert to a symlink and silently re-trip the realpath guard
**Impact:** If `config/reflections.yaml` ever becomes a symlink into `~/Desktop` again, `load_registry()`'s guard at lines 230-241 rejects it under launchd and reflections go silent — the June 2026 wedge, one layer removed.
**Mitigation:** `scripts/update/env_sync.py::sync_reflections_yaml` already enforces the real-file form and reports `was symlink or stale`. Note the coupling in the docstring so the next reader knows the resolver depends on it. Out of scope to re-verify the sync itself.

### Risk 3: `scripts/update/reflection_register.py` reuses this resolver as a **write** target
**Impact:** `_resolve_target()` (line 184-192) delegates to `_resolve_registry_path()` to decide where to *append* a registration. After this change, that call from a worktree under launchd would target the primary checkout's `config/reflections.yaml` — an install-time copy that the next `/update` overwrites from the vault, so the registration would be silently lost.
**Mitigation:** Real-world exposure is nil today: `reflection_register` runs as a step in `scripts/update/run.py`, which runs in the primary checkout, and `_this_machine_owns_valor` already fails closed. The change does not create the hazard — today the same call targets a *nonexistent* worktree path, which is equally lost. Scope here is limited to documenting the read-vs-write asymmetry in `_resolve_target`'s docstring so a future caller does not learn it the hard way. Splitting the resolver into distinct read and write functions is a real improvement and a separate change.

### Risk 4: The #2810 carve-out could mask a genuine new regression
**Impact:** "`test_all_callables_resolve` is expected to fail" is exactly the shape of statement under which a second, unrelated failure hides.
**Mitigation:** The carve-out is pinned, not blanket. A Verification row asserts the failure output contains `expectation_reconciler` — any *other* unresolvable callable fails the check.

## Race Conditions

No race conditions identified. `_resolve_registry_path` is a pure synchronous function over filesystem reads with no shared mutable state; the helper it gains reads two immutable-after-worktree-creation git metadata files. The one ordering property worth naming is pre-existing and unchanged: `REGISTRY_PATH` (line 98) is bound once at import time, so a registry that appears *after* import is not picked up by that constant — every hot path already calls `_resolve_registry_path()` afresh via `load_registry()`, so this affects only the module-level constant, exactly as it does today.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2810] Fixing the `expectation-reconciler` unresolvable callable. It is registry content awaiting #2708's module, not a test contract or a path-resolution defect, and shares no code with this plan. `test_all_callables_resolve` therefore remains red after this work.
- [SEPARATE-SLUG #2810] Removing or disabling the `expectation-reconciler` registry entry to force the class green. Registry content is owned by #2810; editing the shared vault file to make a test pass here would hide a live production error that fires every 30 minutes.
- [ORDERED] Splitting `_resolve_registry_path` into separate read and write resolvers (Risk 3). The write path's only caller runs inside `/update` in the primary checkout, so the change must be sequenced against an `/update` cycle on every machine to be verified; doing it here would put an unverifiable change on the critical path of a test fix. This plan documents the asymmetry instead.

## Update System

No update system changes required. The fix is a pure code change in `agent/reflection_scheduler.py` plus test edits — no new dependency, no new config file, no new env var, nothing to propagate. `/update` continues to materialize `config/reflections.yaml` in the primary checkout exactly as it does today; this change makes worktrees *read* that existing artifact rather than requiring a new one.

Worth noting for whoever runs `/update` after this merges: `scripts/update/reflection_register.py` imports the modified resolver (`_resolve_target`, line 190). Its behavior in the primary checkout — where `/update` runs — is unchanged, because the new worktree branch is only reached when the local `config/reflections.yaml` is absent, and in the primary checkout it is present.

## Agent Integration

No agent integration required. This is an internal fix to a module the reflection worker subprocess and the test suite already import; it adds no capability the agent needs to reach. No new CLI entry point in `pyproject.toml [project.scripts]`, no new bridge import, no MCP surface.

The one agent-visible consequence is indirect and is the point of the work: SDLC lane sessions running in `.worktrees/{slug}/` will stop seeing ten spurious `FileNotFoundError`s when they run this test module, so `/do-test` results from a lane become trustworthy for reflections tests.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` lines 98-110 ("The scheduler resolves `config/reflections.yaml` via a three-level fallback"): document the new fourth level (owning checkout's copy), state explicitly that worktrees have no local copy and why, and correct the stale claim at lines 62 and 102 that `config/reflections.yaml` is a **symlink** — since the June 2026 wedge fix it is a real file copy written by `scripts/update/env_sync.py::sync_reflections_yaml`, which the new fallback depends on (Risk 2).
- [ ] Update `docs/features/worktree-manager.md` with a short note that gitignored install-time artifacts (`config/reflections.yaml` being the worked example) are absent in worktrees, and that the resolution strategy is to read the owning checkout's copy rather than to duplicate the file per worktree.
- [ ] No new entry in `docs/features/README.md` — this modifies existing documented behavior rather than adding a feature.

### Inline Documentation
- [ ] Rewrite the `_resolve_registry_path` docstring (`agent/reflection_scheduler.py:71-77`): remove the false "always present" claim at line 75, enumerate all four resolution levels, and state that a worktree has no local copy.
- [ ] Comment the new primary-checkout helper with the git layout it relies on (`.git` file → `gitdir:` → `commondir`) and why it reads those files instead of shelling out to `git rev-parse --git-common-dir` (import-time subprocess in a launchd worker).
- [ ] Add the read-vs-write asymmetry note to `scripts/update/reflection_register.py::_resolve_target`'s docstring (Risk 3).
- [ ] Comment the widened required-fields assertion with a pointer to `agent/reflection_scheduler.py:266-283` as the contract it mirrors, so the next grammar change updates both.

## Success Criteria

- [ ] From a `.worktrees/{slug}` checkout with `VALOR_LAUNCHD=1` set, `tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity` reports **4 passed, 1 failed**, the sole failure being `test_all_callables_resolve` with `expectation_reconciler` in its message (#2810).
- [ ] The same class, same worktree, with `VALOR_LAUNCHD` unset: identical result — 4 passed, 1 failed on the same node for the same reason.
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryLoading` passes fully from a worktree with `VALOR_LAUNCHD=1`, including `test_load_registry_returns_only_enabled` now asserting a non-empty registry.
- [ ] `tests/unit/test_plan_migration_invariant.py` passes from a worktree with `VALOR_LAUNCHD=1` (fixed for free by the resolver change).
- [ ] The live `cron:` entry `sdlc-upvote-pickup` passes `test_all_entries_have_required_fields`, and the registry is **unmodified** — no `every:` bolted onto it.
- [ ] A synthetic entry with no schedule key is rejected by the required-fields predicate, and a synthetic entry with both `every:` and `cron:` is rejected as ambiguous.
- [ ] `_resolve_registry_path` no longer claims "always present" and logs one error naming every candidate when it exhausts them.
- [ ] Full `tests/unit/test_reflection_scheduler.py` module passes from the primary checkout except the #2810 node.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (resolver)**
  - Name: `resolver-builder`
  - Role: The Defect 2 fix in `agent/reflection_scheduler.py` — primary-checkout helper, fourth resolution level, exhausted-candidates diagnostic, docstring correction.
  - Agent Type: builder
  - Resume: true

- **Builder (test contract)**
  - Name: `contract-builder`
  - Role: The Defect 1 fix and test additions in `tests/unit/test_reflection_scheduler.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (worktree environment)**
  - Name: `worktree-validator`
  - Role: Reproduce the original failures and verify the fix under the real nightly environment — worktree cwd, `VALOR_LAUNCHD=1`.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `reflections-documentarian`
  - Role: The Documentation section tasks.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Capture the red baseline
- **Task ID**: capture-baseline
- **Depends On**: none
- **Assigned To**: `worktree-validator`
- **Agent Type**: validator
- **Parallel**: false
- From a `.worktrees/{slug}` checkout with `VALOR_LAUNCHD=1`, run `tests/unit/test_reflection_scheduler.py` and `tests/unit/test_plan_migration_invariant.py`; record the exact `FileNotFoundError` set.
- From the primary checkout, run `TestRegistryIntegrity`; record the 2-failed/3-passed split and both failure messages verbatim.
- This is the red-state proof the PR description must carry. Demonstrated-red before green: a passing suite afterwards proves nothing without it.

### 2. Fix the resolver (Defect 2)
- **Task ID**: build-resolver
- **Depends On**: capture-baseline
- **Validates**: tests/unit/test_reflection_scheduler.py, tests/unit/test_plan_migration_invariant.py
- **Informed By**: spike-1 (`.git` file → `gitdir:` → `commondir`, no subprocess), spike-3 (primary copy is a real file, realpath guard passes it)
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add the private owning-checkout helper described in Technical Approach, returning `None` on any failure.
- Insert the fourth resolution level after the existing in-repo candidate; do not reorder or weaken the `REFLECTIONS_YAML`, vault, or `VALOR_LAUNCHD` branches.
- Log one error naming every candidate when all are exhausted; keep returning the legacy in-repo path so no new exception escapes at import.
- Rewrite the docstring: four levels, no "always present", worktrees have no local copy.
- Leave `agent/reflection_scheduler.py:230-241` and `86-90` untouched.

### 3. Fix the test contract (Defect 1)
- **Task ID**: build-contract
- **Depends On**: capture-baseline
- **Validates**: tests/unit/test_reflection_scheduler.py
- **Informed By**: spike-2 (blast radius is exactly one assertion; `_entry_interval_seconds` is not implicated)
- **Assigned To**: `contract-builder`
- **Agent Type**: test-engineer
- **Parallel**: true
- Extract the required-fields check into a module-level predicate so it can be unit-tested against synthetic entries without touching the live registry.
- Require exactly one of `every` / `cron` / `at` / `schedule`; reject zero (missing) and reject two or more (ambiguous). Require `cron_tz` only alongside `cron`.
- Add negative tests: no-schedule entry rejected; `every`+`cron` entry rejected; a `cron`-only entry with `cron_tz` accepted.
- Add the non-empty assertion to `test_load_registry_returns_only_enabled`.
- Do not modify `config/reflections.yaml` or the vault registry.

### 4. Failure-path tests for the resolver
- **Task ID**: build-resolver-tests
- **Depends On**: build-resolver
- **Assigned To**: `contract-builder`
- **Agent Type**: test-engineer
- **Parallel**: false
- Cover every bullet in Failure Path Test Strategy: malformed `.git`, empty `.git`, dangling `gitdir:`, primary-checkout no-op, worktree hit, and the exhausted-candidates log asserted via `caplog` on message content.
- Use `tmp_path` to synthesize the git layouts. Do not create real worktrees in tests.

### 5. Verify under the real nightly environment
- **Task ID**: validate-worktree
- **Depends On**: build-resolver, build-contract, build-resolver-tests
- **Assigned To**: `worktree-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run every command from `capture-baseline` and diff against the recorded red state.
- Confirm both `VALOR_LAUNCHD=1` and unset produce identical results (Success Criteria 1 and 2).
- Confirm the sole remaining failure is `test_all_callables_resolve` and that its message names `expectation_reconciler` — any other cause is a regression, not the carve-out.
- Confirm `git status` shows no modification to `config/reflections.yaml` and that `~/Desktop/Valor/reflections.yaml` is byte-identical to its pre-run state.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-worktree
- **Assigned To**: `reflections-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section tasks, including the stale-symlink correction in `docs/features/reflections.md`.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: `worktree-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification table row and confirm each expected result.
- Confirm all Success Criteria boxes.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Registry integrity green except #2810 | `./scripts/pytest-clean.sh "tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity" -p no:randomly -n0 -q 2>&1 \| tail -3` | output contains `1 failed, 4 passed` |
| The one failure is #2810, not something else | `./scripts/pytest-clean.sh "tests/unit/test_reflection_scheduler.py::TestRegistryIntegrity" -p no:randomly -n0 -q 2>&1 \| grep -c "expectation_reconciler"` | output > 0 |
| No FileNotFoundError anywhere in the module | `./scripts/pytest-clean.sh tests/unit/test_reflection_scheduler.py -p no:randomly -n0 -q 2>&1 \| grep -c "FileNotFoundError"` | match count == 0 |
| Registry loading tests fully green | `./scripts/pytest-clean.sh "tests/unit/test_reflection_scheduler.py::TestRegistryLoading" -p no:randomly -n0 -q 2>&1 \| tail -3` | output contains `passed` |
| Plan-migration invariant fixed for free | `./scripts/pytest-clean.sh tests/unit/test_plan_migration_invariant.py -p no:randomly -n0 -q 2>&1 \| tail -3` | output contains `passed` |
| Docstring no longer claims "always present" | `grep -c "always present" agent/reflection_scheduler.py` | match count == 0 |
| Anti-criterion: registry not edited to add `every:` to the cron entry | `python3 -c "import yaml;d=yaml.safe_load(open('config/reflections.yaml'));print(sum(1 for r in d['reflections'] if r.get('name')=='sdlc-upvote-pickup' and 'every' in r))"` | output contains `0` |
| Anti-criterion: `expectation-reconciler` entry untouched (owned by #2810) | `git diff origin/main -- config/reflections.yaml \| wc -l` | output contains `0` |
| Anti-criterion: launchd TCC guards not weakened | `grep -c "VALOR_LAUNCHD" agent/reflection_scheduler.py` | output > 2 |
| Anti-criterion: no import-time git subprocess in the resolver | `grep -c "git rev-parse" agent/reflection_scheduler.py` | match count == 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | The Verification row `grep -c "git rev-parse" agent/reflection_scheduler.py` expecting 0 directly contradicts the Documentation task mandating a helper comment that explains "why it reads those files instead of shelling out to `git rev-parse --git-common-dir`". `grep -c` counts comment lines identically to code lines (count is 0 today, becomes 1 once the mandated comment lands), so task 7 `validate-all` cannot go green: satisfying the documentarian fails the validator. | pending | Fix both sides. (1) Detect the actual hazard, a subprocess, instead of a string: `grep -cE "^\\s*(import subprocess\|from subprocess)" agent/reflection_scheduler.py` expecting 0. (2) Paraphrase the rationale comment so it does not reproduce the banned token verbatim, e.g. "resolved from git's on-disk worktree metadata rather than by invoking the git CLI, because `REGISTRY_PATH` is computed at import time inside a launchd worker". Do not rely on the paraphrase alone — a string-based anti-criterion will keep re-tripping on future prose. |
| CONCERN | Risk & Robustness | Desired outcome 3 ("one legible error rather than ten `FileNotFoundError`s") is not delivered by the chosen design. Data Flow step 6 states the ten call sites bypass `load_registry()` and `open()` the resolved path directly, so each still raises `FileNotFoundError`; and because each calls `_resolve_registry_path()` afresh via `_registry_path()` (verified: 9 sites in `test_reflection_scheduler.py`, 1 at `test_plan_migration_invariant.py:78`), the new diagnostic fires ten times too. The exhausted case becomes strictly noisier than today. | pending | (1) De-duplicate with a module-level `_exhausted_warned: set[str]` keyed on the candidate tuple, not `lru_cache` — `_resolve_registry_path` must stay uncached because `load_registry()` re-resolves per call and tests mutate `REFLECTIONS_YAML`. Keying on a bare bool would let a second bad path silently suppress its own diagnostic. (2) Convert `_registry_path()` (`test_reflection_scheduler.py:40`) into a module-scoped fixture that calls `pytest.fail(...)` once with the candidate list — `fail`, not `skip`, since the Option 3 rejection turns on not preserving the detection outage. |
| CONCERN | Risk & Robustness | The helper's two branches return different *kinds* of path and the plan does not say so. The `commondir` branch yields the primary `.git` ("root is that directory's parent"), but the `Path(gitdir).parents[2]` fallback already yields the checkout **root** for the real layout `/Users/valorengels/src/ai/.git/worktrees/ledger-integrity` (verified verbatim from `.worktrees/ledger-integrity/.git`). A builder applying the stated `.parent` rule uniformly lands on `/Users/valorengels/src`, falls through to the legacy path, and produces a silent wrong answer with no exception. | pending | Converge both branches on one variable before the single `exists()` check: `common = (gitdir / Path(commondir_text.strip())).resolve(); root = common.parent` for the commondir branch, and `root = Path(gitdir).parents[2]` for the fallback — `.parent` applies in the first branch only. Guard the fallback with `len(Path(gitdir).parents) > 2` before indexing; a malformed short `gitdir:` raises `IndexError` that `except Exception: return None` would swallow into the same silent fallback. Add a test asserting the helper returns a directory containing `.git`, not a `.git` directory. |
| CONCERN | Risk & Robustness | Success Criteria 1 and 2 assert "identical result" with `VALOR_LAUNCHD` set vs unset, but the two runs read different files (config copy vs vault). Measured at `a9016cbdc`: both carry the same 34 entry names, but `tech-debt-scan`, `skills-audit`, `hooks-audit` and `docs-auditor` differ in content. They agree on the fields these tests read, so the criterion passes by coincidence of sync state, not construction — and drift in a schedule or `callable` field would fail criterion 2 for a cause unrelated to this work, the same masking hazard named in Risk 4. | pending | Add a Verification row comparing only the projection the tests read, never a byte or full-YAML diff (the copies legitimately differ elsewhere, which would false-alarm): `python3 -c "import yaml;p=lambda f:sorted((r['name'],r.get('every'),r.get('cron'),r.get('at'),r.get('schedule'),r.get('callable')) for r in yaml.safe_load(open(f))['reflections']);print('MATCH' if p('config/reflections.yaml')==p('/Users/valorengels/Desktop/Valor/reflections.yaml') else 'DRIFT')"` expecting `MATCH`. Run from the primary checkout only — `config/reflections.yaml` is absent in a worktree by premise. |
| CONCERN | History & Consistency | The anti-criterion `git diff origin/main -- config/reflections.yaml \| wc -l` cannot fail. The file is gitignored (`.gitignore:8`) and untracked — `git ls-files --error-unmatch config/reflections.yaml` errors — so the command outputs `0` whether or not the file was edited, giving zero assurance that #2810's registry content was left alone. The adjacent row `open()`s `config/reflections.yaml` by literal path, which does not exist in the worktree environment tasks 1 and 5 mandate, so it raises `FileNotFoundError` instead of reporting a verdict. | pending | Capture the fingerprint in task 1 `capture-baseline`, where the red state is already recorded, so there is something to compare against: `shasum -a 256 config/reflections.yaml ~/Desktop/Valor/reflections.yaml > /tmp/2734-registry-baseline.txt`, verified later with `shasum -a 256 -c /tmp/2734-registry-baseline.txt` expecting two `OK` lines. Route the other row through `_resolve_registry_path()` rather than a literal path so it works from either checkout; it must still run from the primary checkout or post-fix, since resolution is the thing under repair. |
| CONCERN | Scope & Value | The Defect 1 fix re-creates the condition it exists to remove, along a different axis. The loader is not ambiguous about multi-key entries: `agent/reflection_scheduler.py:266-283` applies a deterministic precedence (`schedule` > `every` > `cron` > `at`), so an entry with both `every` and `cron` loads and picks `every`. "Reject two or more (ambiguous)" makes the test *stricter* than the loader — the same drift shape as Defect 1 — yet the plan presents it as mirroring the normalizer. | pending | Verified against both live copies at `a9016cbdc`: zero entries carry more than one of `every`/`cron`/`at`/`schedule` and zero carry `cron_tz` without `cron`, so the strict rule is green today and safe to adopt as a lint. Encode the divergence in the failure text, not only a comment: `f"Entry {name} declares {keys!r}; the loader (agent/reflection_scheduler.py:266-283) would silently pick by precedence schedule>every>cron>at. Declare exactly one."` The mandated inline comment should say "stricter than the loader by policy", not "mirrors the loader", so a future loader change formalizing multi-key support reads as intended divergence rather than a test bug. |
| CONCERN | History & Consistency | Risk 3's mitigation ("document the read-vs-write asymmetry in `_resolve_target`'s docstring") appends to a docstring that asserts the opposite invariant as a recorded prior critique decision: `scripts/update/reflection_register.py::_resolve_target` says it delegates to the resolver "so the entry lands where the scheduler will actually look, not in the soon-clobbered config copy (critique C6)". This plan makes that shared resolver able to return precisely that copy, so appending leaves a stale claim beside its own contradiction. | pending | Rewrite the docstring, do not append. Name the exact reachability condition: the new fourth level is reached only when this checkout's `config/reflections.yaml` is absent, which never holds in the primary checkout where `/update` runs, so `_resolve_target` still lands on the vault there and C6 holds for every real caller today. State that a worktree caller under `VALOR_LAUNCHD=1` would write to the primary checkout's install-time copy and lose the registration at the next `/update`, and cross-reference the `[ORDERED]` No-Go. Leave `_this_machine_owns_valor`'s fail-closed behavior untouched — it is the only thing preventing that path from being exercised. |
| NIT | Scope & Value | `appetite: Small` and "Two files change", yet the plan allocates four named agents across seven tasks. Tasks 3 and 4 both write `tests/unit/test_reflection_scheduler.py` and are both owned by `contract-builder`, but task 4 is gated on a different agent's work in a different file, splitting one test file across two scheduling nodes for no isolation benefit. | pending | N/A (NIT). |

---

## Open Questions

Both open questions from the issue are resolved in this plan with evidence rather than deferred:

1. **Defect 2 fix strategy** — decided: **option 2**. Option 1 is blocked by the independent `~/Desktop` realpath guard at `agent/reflection_scheduler.py:230-241` and would require betting against a TCC hang this repo already lost once. Option 3 leaves the production hole open and needs ten edits that each fail open if forgotten. Rationale in full under Technical Approach.
2. **Should the nightly detector run with `VALOR_LAUNCHD` unset?** — decided: **no**. The var is a faithful part of the production environment; unsetting it would make tests diverge from production and hide the resolver bug rather than fix it.

One item for the reviewer's judgment, not a blocker:

3. **The #2810 carve-out.** This work deliberately lands with `TestRegistryIntegrity::test_all_callables_resolve` still red, because its cause is registry content awaiting #2708's module. The alternative — deleting or disabling the `expectation-reconciler` entry to force the class green — would hide a live production error that fires every 30 minutes, so it is listed as a No-Go. If a fully green class is required before merge, #2810 must land first and this becomes an [ORDERED] dependency.
