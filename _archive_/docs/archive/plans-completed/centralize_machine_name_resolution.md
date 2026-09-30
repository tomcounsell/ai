---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/1997
last_comment_id:
---

# Centralize Machine-Name Resolution

## Problem

Machine-name / ownership resolution is copy-pasted across the codebase, each
copy with its own `scutil --get ComputerName` call and/or `projects.json`
ownership match. The copies have **drifted** (they are not byte-identical), so a
bug fixed in one copy (e.g. the #1834 empty-machine fail-to-development guard)
does not propagate to the others.

**Revision note (2026-07-10):** The original plan and issue both asserted "there
is no lower shared layer today, so `config/` is the right home" and that this is
the "first consolidation attempt (greenfield)". **Both claims are false.** A
shared, self-described canonical hub already exists — `tools/machine_identity.py`
(module docstring: *"Single source of truth for this machine's identity"*) —
with **five consumers** and **~20 test patch sites** already depending on it. The
premise has changed materially; see **Freshness Check → Major drift** below. This
revision corrects the inventory and the target, and records that the already-open
implementation PR (#2008) is a **half-migration** that must be reworked.

**Current behavior — the true inventory (9 resolution copies, not 5):**

*Name resolution (`scutil --get ComputerName`):*

| Module | Symbol | timeout | fallback | slug | on failure |
|--------|--------|---------|----------|------|-----------|
| `tools/machine_identity.py` | `computer_name()` **(existing hub)** | 5 | no | no | `""` |
| `tools/machine_identity.py` | `display_machine_name()` **(existing hub)** | — | `socket.gethostname()` → `"unknown"` | no | never empty |
| `ui/data/machine.py` | `get_machine_name()` | none | no | no | `""` |
| `bridge/update.py` | `_get_machine_name()` (L88) | 5 | `platform.node().split(".")[0]` | no | fallback |
| `tools/google_workspace/auth.py` | `_get_machine_name()` (L98) | 5 | `platform.node()...lower()` | **yes** | fallback |
| `scripts/update/readme_check.py` | `_get_machine_name()` (L56) | none | no | no | **raises** |
| `monitoring/sentry_config.py` | `_get_machine_name()` (L99) | none | no | no | `""` |
| `reflections/pm_briefings/__init__.py` | `_resolve_machine()` (L61) | 5 | no | no | `""` |
| `bridge/telegram_bridge.py` | inline in `_get_active_projects()` (L489) | none | no | no | `""` |
| `scripts/update/verify.py` | `check_machine_identity()` (L1230) | none | no | no | error-dict |

Plus `reflections/docs_auditor.py::_filing_machine_name()` — **already** delegates
to `tools.machine_identity.display_machine_name` (i.e. one call site is *already
centralized* onto the existing hub, proving the hub is the intended home).

*Ownership matching (`projects.<key>.machine` compare):* `ui/data/machine.py`
(`get_machine_project_keys`, `get_machine_projects`), `monitoring/sentry_config.py`
(`_owned_project_key`, #1834 empty-guard), plus three that read the **repo-local**
`config/projects.json` with a `working_directory`-shaped fallback and do their own
iteration: `scripts/update/readme_check.py`, `scripts/update/verify.py::check_machine_identity`,
`bridge/telegram_bridge.py::_get_active_projects`.

**Existing consumers of the hub (must not break):** `tools/reflection_machine_filter.py`,
`reflections/docs_auditor.py`, `reflections/crash_recovery.py`,
`scripts/update/reflection_arm.py`, `scripts/update/reflection_register.py` — plus
~20 test patch sites targeting `tools.machine_identity.computer_name`
(`test_reflection_arm.py`, `test_reflection_register.py`, `test_crash_recovery_gates.py`).

**Desired outcome:**

**One** module owns every `scutil` call and every vault-`projects.json` ownership
match. No second parallel hub. Every raw copy imports from it. Fail-soft behavior
preserved (`""` / `[]` on read failure); the #1834 empty-machine guard preserved;
display consumers keep a non-empty fallback (via the existing `display_machine_name`
chain) rather than losing it. The three repo-local-`projects.json` readers keep
their own iteration (different file source + shape) but borrow the centralized
**name**.

## Freshness Check

**Baseline commit:** `9a873ec6` (current `origin/main` at revision time).
**Issue filed at:** 2026-07-10T06:10:13Z
**Disposition:** **Major drift** — the issue's core premise ("no shared layer
exists") is false, and an implementation PR (#2008) already merged the flawed
premise into code as a half-migration.

**What moved / was wrong at filing (evidence, all against `9a873ec6`):**

1. **A canonical hub already exists.** `tools/machine_identity.py` (title:
   *"Single source of truth for this machine's identity"*) exposes `computer_name()`
   (== the proposed `get_machine_name()`, `""`-on-failure) and `display_machine_name()`
   (ComputerName → `socket.gethostname()` → `"unknown"`). It has 5 internal consumers
   and ~20 test patch sites. The issue/plan/recon missed it entirely.
2. **The inventory was undercounted.** The plan named 5 copies; there are 9 `scutil`
   copies plus the hub. Uncounted: `reflections/pm_briefings/_resolve_machine`,
   `bridge/telegram_bridge._get_active_projects` (L489), `scripts/update/verify.check_machine_identity`
   (L1230), and the hub itself. **Consequence:** the original plan's Success-Criteria
   grep ("`scutil --get ComputerName` appears only in `config/machine.py`") **fails on
   day one** — it matches all of these.
3. **PR #2008 is already open, green, and is a half-migration.** It created a
   *greenfield* `config/machine.py` (a **second** module docstringed "Single source of
   truth for machine identity") and cut over only the 5 originally-named modules. On
   the PR's own branch (`session/dev-7bd4cf82`), `scutil` still appears in
   `tools/machine_identity.py`, `pm_briefings`, `telegram_bridge`, and `verify.py`.
   The new module's docstring claim — *"Every scutil call … resolves through here"* —
   is objectively false on its own branch. This is a NO-LEGACY-CODE / half-migration
   violation and must be reworked, not merged.
4. **#1834 confirmed landed** (PR #2005, `53569a43`) — the `monitoring/sentry_config.py`
   copy + empty-guard are present as the issue describes. The build must branch from
   **current `main`** (which contains both this plan and #1834's sentry copy).

**Active-plan overlap:** `centralize_config_magic_literals.md` (#1968) migrates inline
`timeout=` literals into `config/settings.py`; it does not touch machine-name
resolution. Coordination note only, unchanged from prior revision.

**Required action:** this is precisely the "Stop — do not silently build a plan for a
stale/mis-premised problem" case from the do-plan Freshness Check. The consolidation
**target module** and the **half-migration rework of PR #2008** are scope decisions
that belong to the supervisor/human (see Open Questions). Status stays `Planning`; the
plan is **not** finalized and the plan-revising lock is **not** cleared by this pass.

## Prior Art

- **`tools/machine_identity.py` (already merged, canonical):** the existing shared hub.
  Its `computer_name()`/`display_machine_name()` already encode the exact
  ComputerName-vs-hostname distinction this issue reinvents. It is stdlib-only
  (`subprocess`, `socket`) with no internal imports, so **any** layer (including
  `monitoring`) can import it with zero cycle risk — which dissolves the issue's stated
  reason for preferring `config/` ("avoid a `monitoring → ui` inversion"): the
  inversion was only ever against the *ui* copy, never against a dependency-free hub.
- **PR #2008 (open, `session/dev-7bd4cf82`):** the flawed greenfield implementation.
  Files: `config/machine.py` (new), `bridge/update.py`, `monitoring/sentry_config.py`,
  `scripts/update/readme_check.py`, `tools/google_workspace/auth.py`, `ui/app.py`,
  `ui/data/machine.py`, `ui/data/memories.py`, + 3 test files. Leaves the hub and 3
  other copies untouched. Its `get_machine_slug()` still carries the false
  "guaranteed-non-empty" wording with a `platform.node()`-only fallback (which can be
  empty) — critique concern #1 is live in the shipped code.
- **Issue #1834 / PR #2005:** introduced the sentry copy + empty-machine
  fail-to-development guard deliberately as a self-contained copy. Its guard is the
  canonical semantics the centralized ownership function must preserve.

## Research

No relevant external findings — purely internal refactor (stdlib `subprocess`/`socket`/
`platform`/`json`/`pathlib` only). Proceeding with codebase context.

## Data Flow

Two resolution chains, both terminating in the single hub after this change:

1. **Name resolution** — `scutil --get ComputerName` → stripped string (`""` on
   failure) → consumed for **display** (bridge `/update` lines, ui dashboard,
   pm_briefings stamps, docs_auditor issue stamps, sentry env log) and for **ownership
   name** (readme_check, verify, telegram_bridge, ui, sentry).
2. **Ownership resolution** — name → lowercase compare against each
   `projects.<key>.machine` in `~/Desktop/Valor/projects.json` (vault) → owned
   `project_key`s (`[]` on failure) → consumed by ui dashboard scoping and sentry
   environment. **Asymmetry (unchanged):** `readme_check`, `verify.check_machine_identity`,
   and `telegram_bridge._get_active_projects` read the **repo-local**
   `config/projects.json` (different shape/fallback) and keep their own iteration —
   they consume only the centralized **name**, never the vault ownership function.

## Architectural Impact

- **New dependencies:** none (stdlib only; hub may read `config.paths.VALOR_DIR` for the
  vault `projects.json` path — no cycle, both lowest layer).
- **Interface:** one hub exposes `get_machine_name()` (`""`), a display variant
  (`display_machine_name()`/`get_machine_display_name()`, never empty),
  `get_machine_slug()` (never empty — built on the display variant + slugify), and
  `get_machine_project_keys(machine=None)` (vault, empty-guard). All private
  `_get_machine_name` / inline copies deleted; the ui public functions moved.
- **Coupling:** decreases — one hub, dependency-free, importable from every layer.
- **Reversibility:** high — pure refactor, no data/schema/state change.

## Appetite

**Size:** Small–Medium (larger than the original "Small" — 9 copies + hub + ~20
existing test patches, not 5). **Team:** solo dev + reviewer. **Review rounds:** 1,
but **re-critique required** because the premise changed (Major drift).

## Prerequisites

No external dependencies (stdlib-only; tests run offline with monkeypatched
`subprocess`/`projects.json`). **Blocked on a scope decision** (Open Questions) before
build/rework.

## Solution

### RESOLVED (2026-07-10): Option B variant — adopt PR #2008's `config/machine.py`, retire `tools/machine_identity.py`

Decision (human-confirmed): **`config/machine.py` (from PR #2008) is the canonical
hub.** Rationale: the issue explicitly asked for `config/machine.py`; the module is a
strict superset of `tools/machine_identity.py` (adds `get_machine_project_keys` +
`get_machine_slug`); and the no-legacy-code rule forbids two coexisting scutil hubs.
Execution (all on PR #2008's branch `session/dev-7bd4cf82`):

1. `tools/machine_identity.py` deleted; `get_display_machine_name()` (its
   ComputerName→hostname→"unknown" chain) absorbed into `config/machine.py`.
2. The 5 former hub consumers repointed (`tools/reflection_machine_filter.py`,
   `reflections/docs_auditor.py`, `reflections/crash_recovery.py`,
   `scripts/update/reflection_arm.py`, `scripts/update/reflection_register.py`).
3. The remaining raw copies cut over (`reflections/pm_briefings/__init__.py`,
   `bridge/telegram_bridge.py`, `scripts/update/verify.py::check_machine_identity`).
4. Contract reconciliation: the **stricter** `returncode == 0` check from
   `get_machine_name()` is kept (old `computer_name()` didn't check exit status);
   documented in the module docstring.
5. The ~21 test patches on `tools.machine_identity.computer_name` retargeted to
   `config.machine.get_machine_name`.

The Option A/B analysis below is retained as the decision record.

### Decision record (superseded by the resolution above)

The issue names `config/machine.py`, but `tools/machine_identity.py` already IS the
canonical hub. Two viable targets:

- **Option A (recommended — lower risk):** make `tools/machine_identity.py` the single
  hub. Extend it with `get_machine_project_keys(machine=None)` and `get_machine_slug()`;
  rename `computer_name()` → keep as-is (it is already the fail-soft name function) and
  add thin aliases if the issue's `get_machine_name` name is desired. Cut the 9 raw
  copies over to it. **No `config/machine.py` is created.** ~20 existing test patches on
  `tools.machine_identity.computer_name` keep working unchanged; the 5 existing consumers
  are untouched. PR #2008's new `config/machine.py` is **closed/reverted**.
- **Option B (honors the literal issue title — higher churn):** move
  `tools/machine_identity.py` → `config/machine.py` (rename), repoint all 5 existing
  consumers **and** the ~20 test patch targets, then cut the 9 raw copies over. PR #2008
  is reworked to also delete `tools/machine_identity.py` and cover the 4 missed copies.

Either way, the invariant is: **exactly one module wraps `scutil`; every copy imports
it; no second hub survives.**

### Key elements (target-agnostic)

- `get_machine_name() -> str` — `scutil`, `timeout=5`, `""` on any failure. No
  `platform.node()` fallback (ownership guard). (== existing `computer_name()`.)
- `get_machine_display_name() -> str` — never empty: ComputerName → `socket.gethostname()`
  → `"unknown"`. (== existing `display_machine_name()`.) **Bridge `/update` display and
  every other human-facing stamp route through this** — resolving critique concern #3
  *without* dropping any fallback.
- `get_machine_slug() -> str` — filesystem-safe, **genuinely** non-empty:
  `_slugify(get_machine_display_name())` (display name is never empty, so the slug is
  never empty). Drops the false "guaranteed-non-empty via `platform.node()`" wording —
  resolving critique concern #1 by making the invariant actually true rather than
  asserted. Reproduces `google_workspace/auth.py`'s current token-filename behavior.
- `get_machine_project_keys(machine: str | None = None) -> list[str]` — reads
  `VALOR_DIR / "projects.json"`; case-insensitive `machine` match; `[]` on
  missing/unreadable/malformed; **empty-machine guard** `if not machine: return []`
  (preserves #1834). Optional pre-resolved `machine` avoids a double `scutil` in sentry.

### Cutover (all copies)

- **ui/data/machine.py:** delete `get_machine_name` + `get_machine_project_keys`; keep
  `get_machine_projects` (ui-specific), import the name from the hub. Update `ui/app.py`
  (L146/356/397/769) + `ui/data/memories.py` (L48).
- **bridge/update.py:** delete `_get_machine_name`; display sites L169/345/459 use
  `get_machine_display_name()` (confirmed **display-only**: `machine` is only interpolated
  into f-strings at L134/185/197/312/316/322/433/515 — no control flow). **Concern #3
  resolved:** no fallback is dropped.
- **tools/google_workspace/auth.py:** delete `_get_machine_name`; use `get_machine_slug()`
  in `_get_token_path`; remove now-unused `subprocess`/`platform` imports.
- **scripts/update/readme_check.py:** delete `_get_machine_name`; import the hub name
  (strictly improves — currently *raises* on failure, will inherit fail-soft `""`; the
  existing `if not machine_name:` guard already handles it).
- **monitoring/sentry_config.py:** delete `_get_machine_name`; import the hub name +
  `get_machine_project_keys`; reduce `_owned_project_key(machine)` to a one-line adapter
  (`keys[0] if keys else None`). `_is_designated_bridge_machine` calls the hub name (its
  internal `_get_machine_name()` call is deleted). **Concern #4:** update the stale
  docstrings — the module-level "Design notes" bullet (the *"self-contained copy … does
  NOT import the ui-layer helper"* justification) and the `_owned_project_key` /
  `_is_designated_bridge_machine` docstrings must be rewritten to state they now delegate
  to the hub. Leaving them is a no-legacy-code violation.
- **reflections/pm_briefings/__init__.py:** delete `_resolve_machine`; import the hub name.
- **bridge/telegram_bridge.py:** replace the inline `scutil` at L489 in
  `_get_active_projects` with the hub name (keep its repo-local iteration).
- **scripts/update/verify.py::check_machine_identity:** replace the inline `scutil`
  (L1240) with the hub name; keep `hostname` semantics (`if not hostname: return
  {"error": ...}` preserves the error-dict contract) and its repo-local
  `config/projects.json` fallback iteration.
- **reflections/docs_auditor.py:** already delegates — repoint import if the module is
  renamed (Option B) else no change (Option A).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Hub wraps `scutil` and the vault `projects.json` read in `try/except`; each swallow
  path asserted: `scutil` raising → name `""`; unreadable/malformed `projects.json` →
  `[]`.
- [ ] Deleted `except Exception: pass` blocks in the raw copies are re-homed and re-tested
  in the hub's test.

### Empty/Invalid Input Handling
- [ ] Name on `scutil` non-zero / empty stdout → `""`.
- [ ] `get_machine_project_keys("")` and `get_machine_project_keys()` when name unresolved
  → `[]` (empty-machine guard; the #1834 case).
- [ ] `get_machine_slug()` when ComputerName empty → non-empty (`gethostname()`/`"unknown"`
  → slugified). **Concern #1: the invariant is now real, not asserted.**

### Error State Rendering
- [ ] bridge `/update` renders (no crash, non-empty display via `get_machine_display_name`)
  when ComputerName fails.
- [ ] readme_check `if not machine_name:` warning path fires (no raise) when name `""`.

## Test Impact

- [ ] `tests/unit/test_worker_sentry_init.py` — **UPDATE (concern #2, corrected).** The
  original plan claimed `_owned_project_key` tests at L156/L168/L179 and `_get_machine_name`
  patches at L190/L194; **none exist** — the file has 5 tests and no ownership tests. The
  real exposure: `test_configure_sentry_inits_when_dsn_set_and_no_guard` asserts
  `environment == "production"` **without patching machine resolution**, so it silently
  reads the *real host* (`scutil` + the real vault `projects.json`) and only passes on a
  machine that owns a project. After centralization the read moves into the hub, so any
  attempt to control it must patch **the hub's lookup site** (`get_machine_name` /
  `get_machine_project_keys` as imported into `monitoring.sentry_config`, or the hub's
  `VALOR_DIR`) — **not** `monitoring.sentry_config.Path`/`json`, which no longer perform
  the read and would be a silent no-op reading the real host. Make this test deterministic
  by patching the hub consumer sites.
- [ ] `tests/unit/test_bridge_update.py` — **UPDATE.** Existing patches of
  `scripts.update.verify.check_machine_identity` (L247/263/281/302) stay (that function
  survives). Any `_get_machine_name` monkeypatch retargets to the imported hub name in the
  `bridge.update` namespace. Assert display path renders non-empty via
  `get_machine_display_name`.
- [ ] `tests/unit/test_reflection_arm.py`, `tests/unit/test_reflection_register.py`,
  `tests/unit/test_crash_recovery_gates.py` — **~20 patch sites on
  `tools.machine_identity.computer_name`.** Option A: **no change** (hub keeps that
  symbol). Option B: **UPDATE all ~20** to the renamed `config.machine` target. This is the
  single biggest test-impact delta and the strongest argument for Option A.
- [ ] `tests/unit/test_update_release_verify.py` — patches `check_machine_identity` (whole
  function). No change (function survives; only its inner `scutil` line is swapped).
- [ ] `tests/unit/test_ui_data_memories.py`, `tests/integration/test_dashboard_memories.py`
  — no change (patch `get_machine_projects`, which stays in `ui/data/machine.py`).
- [ ] `tests/unit/test_config_machine.py` (PR #2008) — **REPLACE or relocate** depending on
  target module; assert name success/failure, slug real-non-empty fallback, ownership
  match/empty-guard/read-failure.

## Rabbit Holes

- **Do NOT** create or keep a *second* hub. Exactly one module wraps `scutil`. (This is
  the specific failure of PR #2008.)
- **Do NOT** unify the two `projects.json` *file sources* — `readme_check` / `verify` /
  `telegram_bridge` read the repo-local `config/projects.json` (with `working_directory`
  fallback); ui/sentry read the vault copy. Only the **name** is shared; the repo-local
  readers keep their own iteration.
- **Do NOT** fold `get_machine_projects` (exploded per-Telegram-group rows, depends on
  `config.enums.PersonaType`) into the hub — it stays in `ui/data/machine.py`.
- **Do NOT** add a `platform.node()` fallback to `get_machine_name()` — it would break the
  #1834 empty-machine guard. Non-emptiness belongs only to the display variant / slug.
- **Do NOT** rewrite `timeout=5` into `config.settings` here — leave to #1968.

## Risks

### Risk 1: A second hub survives (the PR #2008 failure)
**Impact:** two modules both claim "single source of truth"; the exact duplication #1997
targets, now worse (naming collision of intent).
**Mitigation:** anti-criterion grep asserts `scutil --get ComputerName` appears in exactly
one `.py` file, tree-wide (this grep must pass for the *whole* repo, not just the 5
originally-named files).

### Risk 2: Test patch targets silently no-op after cutover
**Impact:** patching `monitoring.sentry_config.Path`/`json` (which no longer read
projects.json) or a deleted `_get_machine_name` leaves the real machine live; tests pass
against the real host.
**Mitigation:** patch at the consumer lookup site (hub symbol as imported into each
module) or the hub's `VALOR_DIR`; Test Impact names the exact targets.

### Risk 3: ~20 existing `computer_name` patches break under a rename (Option B)
**Impact:** a rename to `config/machine.py` breaks every `@patch("tools.machine_identity.computer_name")`.
**Mitigation:** Option A avoids the rename entirely; if Option B is chosen, all ~20 sites +
5 consumers are enumerated for update.

## Race Conditions

None — all synchronous single-process reads. `auth.py` computes `TOKEN_PATH` once at import
(intentional, stable) and continues via `get_machine_slug()`.

## No-Gos (Out of Scope)

Repo-local `projects.json` reader unification and the `timeout=5` → `settings` migration
are deliberate non-goals (Rabbit Holes; #1968 territory). Everything else is in scope.

## Update System

No update system changes required. Pure internal refactor: no new deps, no `.env` keys, no
config files to propagate, no launchd/plist, no Popoto model change (no
`scripts/update/migrations.py` entry). The hub reads the same `~/Desktop/Valor/projects.json`
the code already reads on every machine.

## Agent Integration

No agent integration required. No new `pyproject.toml [project.scripts]` entry, no MCP /
`.mcp.json` change, no new bridge call surface — the bridge swaps private helpers for a hub
import. These are internal resolution helpers the agent never invokes directly.

## Documentation

### Feature Documentation
- [ ] Add a "Machine identity resolution" note to `docs/features/single-machine-ownership.md`
  naming the single hub as the source of truth and stating the `""`/`[]` fail-soft +
  empty-machine-guard contract and the display-vs-ownership distinction. Do **not** leave
  the note pointing at two modules.

### Inline Documentation
- [ ] Hub module + function docstrings: fail-soft contracts, why `get_machine_name()` omits
  `platform.node()` (ownership guard) while the display variant / slug are never empty.
- [ ] Rewrite the stale `monitoring/sentry_config.py` docstrings (module "Design notes"
  bullet + `_owned_project_key` + `_is_designated_bridge_machine`) to state delegation to the
  hub (concern #4).

## Success Criteria

- [ ] Exactly one module wraps `scutil --get ComputerName`; **tree-wide** grep confirms it.
- [ ] No `def _get_machine_name` and no inline `scutil` copy survives anywhere (ui, bridge/update,
  auth, readme_check, sentry, pm_briefings, telegram_bridge, verify, and no second hub).
- [ ] All display consumers render non-empty (via the display variant) — no dropped fallback.
- [ ] #1834 empty-machine fail-to-development guard preserved (covered by a hub test and the
  retained sentry `_owned_project_key` adapter).
- [ ] `get_machine_slug()` is genuinely non-empty (real fallback, not asserted).
- [ ] `google_workspace/auth.py` token filename unchanged; unused `subprocess`/`platform`
  imports removed.
- [ ] Stale sentry docstrings rewritten.
- [ ] The ~20 existing `tools.machine_identity.computer_name` test patches still pass (Option A)
  or are all updated (Option B); the 5 existing hub consumers still work.
- [ ] Tests pass (`/do-test`); docs updated (`/do-docs`).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| One scutil hub, tree-wide | `grep -rln 'scutil' --include=*.py . \| xargs grep -l ComputerName \| grep -v tests/` | exactly one file |
| No private name helper | `grep -rn 'def _get_machine_name' --include=*.py .` | exit 1 |
| No second hub | two modules both titled "Single source of truth …" for machine identity | only one exists |
| config importable (target-dependent) | `python -c "from <hub> import get_machine_name, get_machine_slug, get_machine_project_keys"` | exit 0 |
| Lint/format | `python -m ruff check . && python -m ruff format --check .` | exit 0 |

## Team Orchestration

### Step by Step Tasks (contingent on Open Questions #1)

### 1. Resolve target module + PR #2008 disposition
- **Task ID**: decide-target
- **Depends On**: none
- Human/supervisor picks Option A (extend `tools/machine_identity.py`, close #2008's
  `config/machine.py`) or Option B (rename to `config/machine.py`, rework #2008). No code
  until resolved.

### 2. Establish the single hub
- **Task ID**: build-hub
- **Depends On**: decide-target
- Add `get_machine_project_keys(machine=None)` + `get_machine_slug()` (real non-empty) to
  the chosen hub; keep name (`""`) + display (never empty) variants. Docstrings per
  Documentation.

### 3. Cut over all 9 copies + fix sentry docstrings
- **Task ID**: build-cutover
- **Depends On**: build-hub
- Per Solution → Cutover. Route display through the display variant; delete every raw copy;
  rewrite stale sentry docstrings.

### 4. Tests
- **Task ID**: build-tests
- **Depends On**: build-cutover
- Hub tests; deterministic sentry test (patch hub consumer sites); bridge display test;
  Option-B: update ~20 `computer_name` patches.

### 5. Docs + final validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- `docs/features/single-machine-ownership.md` note; run the tree-wide anti-criterion grep.

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Concern#1 | critique | `get_machine_slug` false "guaranteed non-empty" | Slug built on display variant (never empty) — invariant real | Solution → Key elements |
| Concern#2 | critique | Test Impact omits projects.json read relocation | Corrected: real sentry test reads real host; patch hub consumer sites / `VALOR_DIR`, not `sentry_config.Path/json` | Test Impact |
| Concern#3 | critique | Bridge display fallback loss | Display routes through never-empty display variant; L169/345/459 confirmed display-only | Solution → Cutover |
| Concern#4 | critique | Stale sentry docstrings | Rewrite module "Design notes" + `_owned_project_key`/`_is_designated_bridge_machine` | Documentation / build-cutover |
| Concern#5 | critique | Baseline SHA stale | Refreshed to `9a873ec6` | Freshness Check |
| **Major** | do-plan revision | Premise false: `tools/machine_identity.py` is an existing canonical hub; PR #2008 is a half-migration (2nd hub, 4 copies missed) | Reframed onto one hub; posed target decision | Freshness Check / Open Questions |

---

## Open Questions

1. **Target module + PR #2008 disposition (BLOCKING).** `tools/machine_identity.py` already
   is the canonical hub (5 consumers, ~20 test patches). **Option A (recommended):** make it
   the single hub, extend it, cut the 9 raw copies over, and **close/revert PR #2008's new
   `config/machine.py`** (no second hub; ~20 patches untouched). **Option B:** honor the
   issue's literal `config/machine.py` name by *renaming* the hub there, updating all 5
   consumers + ~20 patches, and reworking #2008 to also delete `tools/machine_identity.py`
   and cover the 4 missed copies. Which?
2. **Full-coverage confirmation.** Confirm the 4 previously-missed copies
   (`pm_briefings._resolve_machine`, `telegram_bridge._get_active_projects`,
   `verify.check_machine_identity`, and the hub consolidation) are all in scope so the
   tree-wide "one scutil" criterion can actually hold. (Recommended: yes — otherwise the
   issue's own goal is unmet.)
