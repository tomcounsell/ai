---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/1997
last_comment_id:
---

# Centralize Machine-Name Resolution into config/machine.py

## Problem

Machine-name / ownership resolution is copy-pasted across five modules, each with
its own `scutil --get ComputerName` call and/or `projects.json` ownership match.
The copies have **drifted** — they are not byte-identical — so the "duplication"
is worse than it looks: a bug fixed in one copy (e.g. the #1834 empty-machine
fail-to-development guard) does not propagate to the others.

**Current behavior:**

Five private implementations, diverging on four axes:

| Module | scutil timeout | `platform.node()` fallback | slug transform | on failure |
|--------|---------------|----------------------------|----------------|------------|
| `ui/data/machine.py::get_machine_name` | none | no | no | `""` |
| `bridge/update.py::_get_machine_name` (L88) | `timeout=5` | yes (`.split(".")[0]`) | no | fallback |
| `tools/google_workspace/auth.py::_get_machine_name` (L98) | `timeout=5` | yes (`.split(".")[0].lower()`) | **yes** (`.lower().replace(" ","-")`) | fallback |
| `scripts/update/readme_check.py::_get_machine_name` (L56) | none | no | no | **raises** (no try/except) |
| `monitoring/sentry_config.py::_get_machine_name` (L99) | none | no | no | `""` |

Plus two ownership-resolution copies: `ui/data/machine.py::get_machine_project_keys`
(list, `[]` on failure) and `monitoring/sentry_config.py::_owned_project_key`
(first-or-`None`, with the #1834 empty-machine guard).

`monitoring/sentry_config.py` deliberately kept its own copy (#1834) to avoid a
`monitoring -> ui` layering inversion. That is exactly the smell this issue fixes:
there is no lower shared layer today, so `config/` is the right home.

**Desired outcome:**

A single `config/machine.py` (the lowest shared layer, stdlib-only) owns every
`scutil` call and every `projects.json` ownership match. All call sites import
from it. No re-export shims left in `ui/data/machine.py` — full cutover per the
no-legacy-code rule. Fail-soft behavior preserved (`""` / `[]` on read failure).

## Freshness Check

**Baseline commit:** `e40e1cab03a419fed5b1a9124aa78dfe35325b1d`
**Issue filed at:** 2026-07-10T06:10:13Z
**Disposition:** Unchanged

**File:line references re-verified (all against baseline):**
- `ui/data/machine.py:8` `get_machine_name`, `:60` `get_machine_project_keys`, `:16` `get_machine_projects` — all present as issue describes.
- `bridge/update.py:88` `_get_machine_name`, used at `:169/:345/:459` (display only) — confirmed.
- `tools/google_workspace/auth.py:98` `_get_machine_name`, feeds `_get_token_path` at `:118` — confirmed; the filesystem-safe transform is real and load-bearing for the token filename.
- `scripts/update/readme_check.py:56` `_get_machine_name` (no try/except, raises on failure), used at `:107` with an `if not machine_name` guard — confirmed.
- `monitoring/sentry_config.py:99` `_get_machine_name`, `:113` `_owned_project_key` (with empty-machine guard), used at `:152/:202/:203` — confirmed.

**Cited sibling issues/PRs re-checked:**
- #1834 — CLOSED, merged via PR #2005 (`53569a43`), already in main. It added the fifth copy in `monitoring/sentry_config.py`; the code I read is post-#1834. No further drift.

**Commits on main since issue was filed (touching referenced files):**
- `53569a43` Gate Sentry environment ... (#1834 / #2005) — this is the merge that *created* the fifth copy; already accounted for in the issue and recon. No new root-cause change.

**Active plans in `docs/plans/` overlapping this area:** `centralize_config_magic_literals.md` (#1968, status Ready) migrates inline `timeout=` literals into `config/settings.py`. It does **not** touch machine-name resolution and does not create `config/machine.py`. The only micro-overlap: the `timeout=5` in the two scutil calls could someday be a #1968 target. Coordination note only — not a blocker; whichever lands first, the other adapts trivially.

## Prior Art

- **Issue #1834 / PR #2005**: Gated Sentry `environment` to dev-vs-prod by machine ownership. Introduced `monitoring/sentry_config.py::_get_machine_name` + `_owned_project_key` with an explicit empty-machine fail-to-development guard, *deliberately as a self-contained copy* to avoid a `monitoring -> ui` import. This issue is the scoped-out follow-up #1834 named. The guard it introduced is the canonical semantics the centralized ownership function must preserve.
- No closed issues found for "machine name config" — this is the first consolidation attempt (greenfield for `config/machine.py`).

## Research

No relevant external findings — purely internal refactor (stdlib `subprocess`/`platform`/`json`/`pathlib` only). Proceeding with codebase context.

## Data Flow

Two independent resolution chains, both terminating in `config/machine.py` after this change:

1. **Name resolution** — `scutil --get ComputerName` → stripped string (or `""`) → consumed by: bridge `/update` Telegram/log display; readme_check ownership match; google_workspace token filename (via slug transform); ui dashboard display; sentry environment tag.
2. **Ownership resolution** — `get_machine_name()` → lowercase compare against each `projects.<key>.machine` in `~/Desktop/Valor/projects.json` → list of owned `project_key`s (or `[]`) → consumed by: ui dashboard memory scoping (`ui/app.py`); sentry `environment` = production-iff-owns-a-project.

**Important asymmetry:** `readme_check.py` reads the **repo-local** `config/projects.json` (not the vault copy) and needs `working_directory` per project, so it does its own project iteration — it consumes only `get_machine_name()`, never the centralized ownership function.

## Architectural Impact

- **New dependencies:** none (stdlib only). `config/machine.py` may import `config/paths.py` (`VALOR_DIR`) — both are the lowest layer, no cycle.
- **Interface changes:** new module `config.machine` with `get_machine_name()`, `get_machine_slug()`, `get_machine_project_keys(machine=None)`. Five private `_get_machine_name` defs and two ui-layer public functions are deleted/moved.
- **Coupling:** *decreases* — resolves the #1834 `monitoring -> ui` inversion; `monitoring` now depends on `config` (correct direction). Everything already depends on `config`.
- **Data ownership:** `config/machine.py` becomes the single owner of "what machine am I / what do I own."
- **Reversibility:** high — pure refactor, no data/schema/state change; revert is a single-commit revert.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully specified here)
- Review rounds: 1 (mechanical cutover; reviewer confirms behavior-preservation + no leftover copies)

## Prerequisites

No prerequisites — this work has no external dependencies (stdlib-only refactor; tests run offline with monkeypatched `subprocess`/`projects.json`).

## Solution

### Key Elements

- **`config/machine.py`** (new, lowest layer): the single home for machine identity.
  - `get_machine_name() -> str` — `scutil --get ComputerName`, `timeout=5`, returns stripped stdout on success; `""` on non-zero exit, empty output, or any exception. No `platform.node()` fallback (the `""`-on-failure contract is what the ownership guard and the issue's "empty string on read failure" require).
  - `get_machine_slug() -> str` — filesystem-safe variant for per-machine token filenames: `get_machine_name().lower().replace(" ", "-")`, falling back to `platform.node().split(".")[0].lower()` when empty. Guaranteed non-empty. Byte-for-byte reproduces `google_workspace/auth.py`'s current behavior.
  - `get_machine_project_keys(machine: str | None = None) -> list[str]` — reads `VALOR_DIR / "projects.json"`; case-insensitive match of each `projects.<key>.machine`; `[]` on missing/unreadable/malformed file. When `machine` is `None`, resolves via `get_machine_name()`. **Empty-machine guard:** `if not machine: return []` (preserves the #1834 fail-to-development semantics — an unresolved ComputerName must never match a `"machine": ""` entry). The optional `machine` param lets sentry pass a pre-resolved name to avoid a double `scutil` call.

- **Five call-site cutovers** (full, no shims) — see Step by Step Tasks.

### Flow

`config/machine.py` (single scutil + single projects.json reader) → imported by ui, bridge, google_workspace, readme_check, monitoring → each call site keeps its own thin adaptation (display string, token filename, first-owned-key) but shares the resolution.

### Technical Approach

- **Canonical `get_machine_name()` returns `""` on failure** (no `platform.node()` fallback). Rationale: the two ownership consumers (ui, sentry) and `readme_check` all need `""` to signal "unknown → don't match / skip". The two display consumers (bridge `/update`, ui dashboard) merely render the string; `""` on a broken-`scutil` host is cosmetic and, on a real bridge machine, unreachable (a bridge always resolves ComputerName or it cannot route). This is a deliberate, documented micro-change to `bridge/update.py` (loses its `platform.node()` display fallback) — the cleaner contract wins over preserving a fallback that never fires in production.
- **`readme_check.py` strictly improves**: it currently *raises* on `scutil` failure (no try/except); after cutover it inherits the fail-soft `""`, and its existing `if not machine_name:` guard already handles that path.
- **`google_workspace` keeps its exact behavior** via `get_machine_slug()` (transform + `platform.node()` fallback moved intact into `config/machine.py`). Its now-unused `subprocess` and `platform` imports are removed (verified: each is used only inside the deleted `_get_machine_name`).
- **`sentry_config.py`**: delete `_get_machine_name`; `_owned_project_key(machine)` becomes a one-line adapter over `get_machine_project_keys(machine)` (`keys[0] if keys else None`), preserving single-resolution and the empty-machine guard (now enforced inside `get_machine_project_keys`). `_is_designated_bridge_machine` and `_resolve_environment` are unchanged except for the `get_machine_name` import.
- **`ui/data/machine.py`**: delete its `get_machine_name` and `get_machine_project_keys`; keep `get_machine_projects` (ui-specific exploded per-Telegram-group rows) but have it import `get_machine_name` from `config.machine`. Update `ui/app.py` (4 sites) and `ui/data/memories.py` (1 site) to import `get_machine_name` / `get_machine_project_keys` from `config.machine` (they keep importing `get_machine_projects` from `ui.data.machine`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `config/machine.py` will contain `try/except Exception` around the `scutil` call and the `projects.json` read. Each swallow path is asserted in `test_config_machine.py`: `scutil` raising → `get_machine_name()` returns `""`; unreadable/malformed `projects.json` → `get_machine_project_keys()` returns `[]`.
- [ ] Existing `except Exception: pass` blocks in the five edited modules are being *deleted* (they lived inside the removed `_get_machine_name` copies); their behavior is re-homed and re-tested in `test_config_machine.py`.

### Empty/Invalid Input Handling
- [ ] `get_machine_name()` on `scutil` non-zero exit / empty stdout → `""` (tested).
- [ ] `get_machine_project_keys("")` and `get_machine_project_keys()` when name is unresolved → `[]` (empty-machine guard; tested — this is the #1834 fail-to-development case).
- [ ] `get_machine_slug()` when `get_machine_name()` is `""` → non-empty `platform.node()` fallback (tested; guards the token-filename-must-not-be-empty invariant).

### Error State Rendering
- [ ] `bridge/update.py` display: assert the `/update` flow still renders when `get_machine_name()` returns `""` (no crash; empty prefix is acceptable). Covered by updated `test_bridge_update.py`.
- [ ] `readme_check.py`: assert the `if not machine_name:` warning path fires (README check skipped, no raise) when name is `""`.

## Test Impact

- [ ] `tests/unit/test_bridge_update.py` — UPDATE: two `monkeypatch.setattr(bridge_update, "_get_machine_name", ...)` sites (L78, L299) must retarget to the imported name `bridge.update.get_machine_name` (patch where it's *looked up*, i.e. the `bridge.update` module namespace).
- [ ] `tests/unit/test_worker_sentry_init.py` — UPDATE: `patch("monitoring.sentry_config._get_machine_name", ...)` sites (≈L190/L194) retarget to `monitoring.sentry_config.get_machine_name`. The `_owned_project_key` tests (empty-machine guard L156, case-insensitive L168, read-failure L179) stay green because `_owned_project_key` remains as a thin adapter — verify they still pass; the guard itself is now also covered at the `config` layer.
- [ ] `tests/unit/test_ui_data_memories.py` — no change: patches `ui.data.machine.get_machine_projects`, which stays in `ui/data/machine.py`.
- [ ] `tests/integration/test_dashboard_memories.py` — no change: same reason (patches `get_machine_projects`).
- [ ] `tests/unit/test_config_machine.py` — CREATE: new coverage for the three `config.machine` functions (success/failure/slug-fallback/ownership-match/empty-machine guard).

## Rabbit Holes

- **Do NOT** migrate the two `projects.json` *readers* into one shared loader — `readme_check.py` reads the repo-local `config/projects.json` with a different shape (`working_directory`), while ui/sentry read the vault copy. Unifying the file source is a separate concern (arguably #1968's territory) and would change readme_check's data source. Only the *name* resolution is shared here.
- **Do NOT** fold `get_machine_projects` (the exploded per-group rows) into `config/machine.py` — it depends on `config.enums.PersonaType` and is ui-presentation logic. It stays in `ui/data/machine.py` and merely borrows `get_machine_name`.
- **Do NOT** add a `platform.node()` fallback to the canonical `get_machine_name()` "to be safe" — it would silently break the ownership empty-machine guard (#1834). The fallback belongs only in `get_machine_slug()`.
- **Do NOT** rewrite the `timeout=5` into a `config.settings` field here — leave that to #1968 to avoid scope collision.

## Risks

### Risk 1: Test patch targets silently no-op after the cutover
**Impact:** `monkeypatch.setattr(module, "_get_machine_name", ...)` against a now-deleted attribute would raise `AttributeError` (caught early) — but the subtler failure is patching `config.machine.get_machine_name` while the consumer imported it into its own namespace, leaving the real function live and tests passing against the real machine.
**Mitigation:** Patch at the *consumer* lookup site (`bridge.update.get_machine_name`, `monitoring.sentry_config.get_machine_name`), which is where each module resolves the name. Test Impact section names the exact targets. The new `test_config_machine.py` tests the source function directly.

### Risk 2: `bridge/update.py` display loses its `platform.node()` fallback
**Impact:** On a host where `scutil` fails, `/update` Telegram/log lines show an empty machine prefix instead of a hostname.
**Mitigation:** Accepted and documented. Real bridge machines always resolve ComputerName (they cannot route messages otherwise). If a reviewer objects, the one-line mitigation is `get_machine_name() or platform.node().split(".")[0]` at bridge's call sites — but default is the clean contract.

### Risk 3: Leftover copy escapes the cutover
**Impact:** A missed `scutil` call re-introduces the exact drift this issue removes.
**Mitigation:** Verification anti-criterion greps the whole tree for `scutil --get ComputerName` and asserts it appears **only** in `config/machine.py`; a second grep asserts no `def _get_machine_name` survives anywhere.

## Race Conditions

No race conditions identified — all operations are synchronous single-process reads (`subprocess.run` for `scutil`, one file read for `projects.json`). No shared mutable state, no async, no cross-process coordination. `google_workspace/auth.py` computes `TOKEN_PATH` once at import (already documented as intentional and stable) and continues to do so via `get_machine_slug()`.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The `readme_check`/vault `projects.json` reader unification and the `timeout=5` → `settings` migration are named in Rabbit Holes as deliberate non-goals (they belong to #1968 / a separate concern), not as deferred work owed a follow-up.

## Update System

No update system changes required. This is a pure internal refactor: no new dependencies, no new `.env` keys, no config files to propagate, no launchd/plist changes, no Popoto model changes (so no `scripts/update/migrations.py` entry). `config/machine.py` reads the same `~/Desktop/Valor/projects.json` the code already reads on every machine.

## Agent Integration

No agent integration required. No new CLI entry point in `pyproject.toml [project.scripts]`, no MCP server or `.mcp.json` change, and the bridge does not gain a new call surface — `bridge/update.py` swaps a private helper for a `config.machine` import with identical (minus the cosmetic display fallback) behavior. The agent reaches none of these functions directly; they are internal resolution helpers.

## Documentation

### Feature Documentation
- [ ] No new `docs/features/*.md` page — this is a refactor of existing behavior, not a new capability. Instead, add a short "Machine identity resolution" note to `docs/features/single-machine-ownership.md` (the existing home for machine-ownership semantics) pointing to `config/machine.py` as the single source, and stating the `""`/`[]` fail-soft + empty-machine-guard contract.

### Inline Documentation
- [ ] Module docstring in `config/machine.py` stating: lowest shared layer, the three functions' fail-soft contracts, and why `get_machine_name()` deliberately omits the `platform.node()` fallback (ownership guard) while `get_machine_slug()` includes it (non-empty filename).
- [ ] Docstrings on all three public functions.

## Success Criteria

- [ ] `config/machine.py` exists with `get_machine_name()`, `get_machine_slug()`, `get_machine_project_keys(machine=None)`.
- [ ] All five modules import from `config.machine`; no `def _get_machine_name` remains anywhere in the tree; `scutil --get ComputerName` appears only in `config/machine.py`.
- [ ] `ui/data/machine.py` retains only `get_machine_projects`, which imports `get_machine_name` from `config.machine` (no re-export shim of `get_machine_name`/`get_machine_project_keys`).
- [ ] `ui/app.py` and `ui/data/memories.py` import `get_machine_name`/`get_machine_project_keys` from `config.machine`.
- [ ] `tools/google_workspace/auth.py` token filename behavior unchanged (via `get_machine_slug`); now-unused `subprocess`/`platform` imports removed.
- [ ] The #1834 empty-machine fail-to-development guard is preserved (covered by both a `config` test and the retained sentry `_owned_project_key` test).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `grep -rn 'scutil --get ComputerName' --include=*.py .` returns exactly one file (`config/machine.py`).

## Team Orchestration

### Team Members

- **Builder (config-machine)**
  - Name: machine-builder
  - Role: Create `config/machine.py` and cut over all five modules + their importers; update the two affected test files; write `test_config_machine.py`.
  - Agent Type: builder
  - Resume: true

- **Validator (cutover)**
  - Name: cutover-validator
  - Role: Verify no leftover copies, behavior-preservation, and Verification table passes.
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Create config/machine.py
- **Task ID**: build-config-machine
- **Depends On**: none
- **Validates**: tests/unit/test_config_machine.py (create)
- **Assigned To**: machine-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `config/machine.py` with `get_machine_name()`, `get_machine_slug()`, `get_machine_project_keys(machine=None)` per the Technical Approach (stdlib only; `scutil` `timeout=5`; `""`/`[]` fail-soft; empty-machine guard; `platform.node()` fallback only in `get_machine_slug`).
- Import `VALOR_DIR` from `config.paths` for the `projects.json` path.
- Write module + function docstrings documenting the fail-soft contracts and the deliberate no-fallback decision.

### 2. Cut over the five modules + importers
- **Task ID**: build-cutover
- **Depends On**: build-config-machine
- **Assigned To**: machine-builder
- **Agent Type**: builder
- **Parallel**: false
- `ui/data/machine.py`: delete `get_machine_name` + `get_machine_project_keys`; keep `get_machine_projects`, import `get_machine_name` from `config.machine`.
- `ui/app.py` (L146/356/397/769) + `ui/data/memories.py` (L48): import `get_machine_name`/`get_machine_project_keys` from `config.machine`.
- `bridge/update.py`: delete `_get_machine_name`; `from config.machine import get_machine_name`; update L169/345/459.
- `tools/google_workspace/auth.py`: delete `_get_machine_name`; `from config.machine import get_machine_slug`; use it in `_get_token_path`; remove now-unused `subprocess`/`platform` imports.
- `scripts/update/readme_check.py`: delete `_get_machine_name`; `from config.machine import get_machine_name`.
- `monitoring/sentry_config.py`: delete `_get_machine_name`; import `get_machine_name` + `get_machine_project_keys`; reduce `_owned_project_key(machine)` to a one-line adapter over `get_machine_project_keys(machine)`.

### 3. Update + create tests
- **Task ID**: build-tests
- **Depends On**: build-cutover
- **Assigned To**: machine-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_config_machine.py` (name success/failure, slug transform + fallback, ownership match/empty-guard/read-failure).
- Update `tests/unit/test_bridge_update.py` patch targets to `bridge.update.get_machine_name`.
- Update `tests/unit/test_worker_sentry_init.py` patch targets to `monitoring.sentry_config.get_machine_name`; confirm `_owned_project_key` tests stay green.

### 4. Docs
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: machine-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add the "Machine identity resolution" note to `docs/features/single-machine-ownership.md` pointing at `config/machine.py`.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: cutover-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm all success criteria, including the single-`scutil`-file and no-`_get_machine_name` anti-criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_config_machine.py tests/unit/test_bridge_update.py tests/unit/test_worker_sentry_init.py tests/unit/test_ui_data_memories.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| scutil call centralized | `grep -rln 'scutil.*ComputerName' --include=*.py .` | output contains config/machine.py |
| No leftover scutil copies | `grep -rln 'scutil.*ComputerName' --include=*.py . \| grep -v 'config/machine.py'` | exit code 1 |
| No private machine-name helper survives | `grep -rn 'def _get_machine_name' --include=*.py .` | exit code 1 |
| No ui re-export shim | `grep -rn 'def get_machine_name\|def get_machine_project_keys' ui/data/machine.py` | exit code 1 |
| config.machine importable | `python -c "from config.machine import get_machine_name, get_machine_slug, get_machine_project_keys"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **`get_machine_slug()` as a third function** — the issue named only `get_machine_name()` and `get_machine_project_keys()`. I added `get_machine_slug()` so `google_workspace/auth.py`'s filesystem-safe token-filename need is fully centralized (rather than leaving a `.lower().replace(" ","-")` transform + `platform.node()` fallback at its call site). Confirm this is acceptable, or prefer keeping that one-line transform local to `auth.py` and exposing only the two named functions.
2. **`bridge/update.py` display fallback** — canonical `get_machine_name()` returns `""` on `scutil` failure (no `platform.node()` fallback), so bridge `/update` lines show an empty prefix on a broken-`scutil` host (never a real bridge machine). Accept the clean contract, or preserve bridge's fallback with `get_machine_name() or platform.node().split(".")[0]` at its call sites?
