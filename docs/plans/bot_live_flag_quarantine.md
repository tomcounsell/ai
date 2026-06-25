---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-25
tracking: https://github.com/tomcounsell/ai/issues/1777
last_comment_id:
---

# Bot Live-Flag Quarantine (stop suppressing a mis-registered human)

## Problem

A registered Telegram bot peer is matched by the deterministic loop-guard and its
inbound messages are recorded to history but never spawn a session. If a token is
swapped or a bot id is typo'd in `projects.json` so that the registered id actually
points at a **human** account, that human is permanently silenced: every message
they send is treated as bot chatter and dropped.

PR #1771 (issue #1574, acceptance criterion 4) added `validate_bot_live_flags()` to
probe each registered id against the live Telegram `User.bot` flag and surface
mismatches. But the wiring is **warn-not-crash**: `main()` logs an ERROR and keeps
serving, and the offending id is **never removed** from `BOT_ID_TO_PROJECT`. So the
exact harm the criterion exists to prevent still happens — now merely accompanied by
a log line nobody is guaranteed to read.

**Current behavior:**
On a live-flag mismatch, the bridge logs `REGISTERED BOT MISCONFIGURATION (#1574): ...`
at ERROR and continues. The mis-registered human id stays in `BOT_ID_TO_PROJECT`,
`find_project_for_bot(human_id)` keeps returning a hit, and the loop-guard
(`telegram_bridge.py:1247` NewMessage handler, `routing.py:1126` `should_respond_sync`)
keeps suppressing that human's messages for the entire life of the bridge.

**Desired outcome:**
On a live-flag mismatch, the offending id is **quarantined** — removed from
`BOT_ID_TO_PROJECT` (and therefore from the routing copy, which is the same dict
object). `find_project_for_bot(human_id)` then returns `None`, the loop-guard no
longer suppresses that account, and the message is allowed through (fail SAFE toward
"treat as human"). The loud ERROR log is retained.

## Freshness Check

**Baseline commit:** `5328e1b5` (HEAD of `main` at plan time)
**Issue filed at:** 2026-06-24T06:14:51Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/config_validation.py:308` — `validate_bot_live_flags(config, resolver)` raises a
  `ConfigValidationError` listing mismatch *strings* (lines 337-361); it does NOT expose
  *which* ids mismatched. — still holds.
- `bridge/telegram_bridge.py:2785-2805` — `main()` catches `ConfigValidationError`, logs ERROR,
  keeps serving; no quarantine. — still holds (cited as `main()` wiring in issue).
- `bridge/telegram_bridge.py:634-642` — `BOT_ID_TO_PROJECT` built once at module load. — still holds.
- `bridge/telegram_bridge.py:652` — `_routing_module.BOT_ID_TO_PROJECT = BOT_ID_TO_PROJECT`
  rebinds the routing-module name to the **same dict object**. — still holds (load-bearing, see Data Flow).
- `bridge/routing.py:290-308` — `find_project_for_bot` reads `BOT_ID_TO_PROJECT.get(sender_id)`. — still holds.
- `bridge/telegram_bridge.py:1247` / `bridge/routing.py:1126` — loop-guard reads. — still holds.

**Cited sibling issues/PRs re-checked:**
- #1574 — closed; the parent acceptance criteria. Criterion 4 ("validate against live `User.bot`
  flag and surfaces mismatches") was satisfied literally by PR #1771 but the harm-prevention intent
  was not.
- PR #1771 (commit `ba5ecb62`, merged 2026-06-24) — added the validator + resolver + 5 unit tests +
  loop-guard. Those stay; this issue only adds the quarantine step.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=2026-06-24T06:14:51Z` over `bridge/config_validation.py`,
  `bridge/routing.py`, `bridge/telegram_bridge.py` returned zero commits.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** No drift. All line references match current code exactly.

## Prior Art

- **Issue #1574**: "Bot end-to-end testing via valor-telegram: synchronous `--await-reply` +
  deterministic bridge loop-guard + bot registry" — closed. Established the bot registry,
  `BOT_ID_TO_PROJECT`, the loop-guard, and acceptance criterion 4 (live-flag validation). This
  plan completes criterion 4's *intent*.
- **PR #1771**: "feat(#1574): bot E2E testing via `valor-telegram send --await-reply` +
  deterministic loop-guard" — merged 2026-06-24. Added `validate_bot_live_flags`, the injectable
  resolver, the warn-not-crash wiring, and 5 live-flag unit tests. The structural validation,
  resolver, and existing tests are correct and stay. Identified during SDLC re-review of commit
  `d676519c` that the patch surfaces mismatches but does not quarantine them.

## Research

No relevant external findings — this is purely a bridge-internal change (no external libraries,
APIs, or ecosystem patterns involved). Proceeding with codebase context.

## Data Flow

The load-bearing fact for this fix is dict aliasing:

1. **Module load** (`telegram_bridge.py:634-642`): the bridge builds
   `BOT_ID_TO_PROJECT: dict[int, dict]` from `projects.<key>.telegram.bots[]`.
2. **Aliasing** (`telegram_bridge.py:652`): `_routing_module.BOT_ID_TO_PROJECT = BOT_ID_TO_PROJECT`
   rebinds the routing module's name to point at **the same dict object** the bridge built
   (`routing.py:33` declares its own `{}` but is overwritten by this assignment at startup).
3. **Live probe** (`telegram_bridge.py:2799` in `main()`, after connect): `validate_bot_live_flags`
   probes each id via `client.get_entity`. On mismatch it currently raises; `main()` logs and continues.
4. **Loop-guard reads** (`telegram_bridge.py:1247`, `routing.py:1126`): both gate on
   `find_project_for_bot(sender_id)`, which reads `BOT_ID_TO_PROJECT.get(...)` — the aliased object.

**Consequence:** A single `BOT_ID_TO_PROJECT.pop(bot_id, None)` on the bridge's dict removes the id
from **both** the bridge map and the routing copy, because they are one object after step 2. After
the pop, `find_project_for_bot(human_id)` returns `None` and the loop-guard falls through to normal
human handling.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1771 | Added `validate_bot_live_flags` + warn-not-crash wiring + 5 unit tests | Satisfied "surfaces mismatches" *literally* (an ERROR log) but never *acted* on the mismatch. The id stays in `BOT_ID_TO_PROJECT`, so the loop-guard keeps suppressing the human. A log is not protection. |

**Root cause pattern:** The validator was designed as a pure assertion (raise-or-pass) rather than a
remediation step. To quarantine, the validator must *report which ids* mismatched so the caller can
remove them — raising an opaque error string is insufficient.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `validate_bot_live_flags` changes from returning `None` (raise-on-mismatch)
  to **returning the set of offending bot ids** (`set[int]`) and no longer raising. The caller
  (`main()`) takes responsibility for logging + quarantining. This is the minimal interface that lets
  the caller act on individual ids. The function name stays; only the return contract changes.
- **Coupling:** unchanged. The validator stays decoupled from a live client via the injectable resolver.
- **Data ownership:** `main()` remains the owner of `BOT_ID_TO_PROJECT` mutation.
- **Reversibility:** trivially reversible — the quarantine is an in-memory `pop`; a bridge restart with
  a corrected config rebuilds the full map.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Tests use a fake resolver and direct
manipulation of `routing.BOT_ID_TO_PROJECT`, so no live Telegram session is required.

## Solution

### Key Elements

- **`validate_bot_live_flags` returns offending ids**: change the contract from "raise on any
  mismatch" to "return `set[int]` of bot ids that failed the live-flag probe (non-bot, unresolvable,
  or resolver error)". The human-readable mismatch detail is still produced for logging.
- **`main()` quarantines + logs**: after the probe, if the returned set is non-empty, log the loud
  ERROR (retained) and `pop` each offending id from `BOT_ID_TO_PROJECT`. Because of dict aliasing
  (Data Flow step 2), this clears the routing copy in the same operation.
- **Fail-safe direction**: a quarantined id is *removed*, so the loop-guard treats that sender as a
  human and the message is allowed. Never the inverse (we never *add* suppression on uncertainty).

### Flow

Bridge startup → `main()` connects to Telegram → `validate_bot_live_flags(CONFIG, resolver)` probes
each registered id → returns `{offending_ids}` → if non-empty: log ERROR (with detail) AND
`BOT_ID_TO_PROJECT.pop(id)` for each → `find_project_for_bot(human_id)` now returns `None` →
human's next message is no longer suppressed by the loop-guard.

### Technical Approach

- **Return contract**: `validate_bot_live_flags` returns a tuple of `(offending_ids: set[int],
  error_detail: str | None)` — the set drives quarantine, the detail string drives the log. (Keeping
  both avoids re-deriving the human-readable message in the caller.) The function no longer raises
  `ConfigValidationError`; mismatch is a normal return value because the bridge must not crash.
- **Caller wiring** (`telegram_bridge.py:2792-2805`): replace the `try/except ConfigValidationError`
  with: call the function, if `offending_ids` is non-empty `logger.error("REGISTERED BOT
  MISCONFIGURATION (#1574): %s", detail)` then loop `BOT_ID_TO_PROJECT.pop(bot_id, None)`. Keep the
  existing success-path `logger.info` for the no-mismatch case.
- **Aliasing is the mechanism**: the fix relies on `telegram_bridge.py:652` already pointing the
  routing module at the same dict. A defensive belt-and-suspenders `_routing_module.BOT_ID_TO_PROJECT.pop(...)`
  is redundant (same object) but harmless; the plan prefers a single pop on the bridge's name plus a
  comment documenting the aliasing invariant so a future refactor that breaks the alias is caught.
- **No change to `validate_projects_config`**: that aggregator (`config_validation.py:364`) calls only
  the *structural* validators, not the live-flag probe. The live probe is wired separately in `main()`.
  The return-contract change is isolated to `validate_bot_live_flags` and its sole caller.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The current wiring's `except ConfigValidationError` block is being *replaced* by a non-raising
  return contract. After the change, `validate_bot_live_flags` swallows resolver exceptions internally
  (already does at `config_validation.py:345`) and reports them as offending ids — assert via test that
  an unresolvable id appears in the returned offending set.
- [ ] No remaining `except Exception: pass` blocks introduced. The `main()` quarantine loop has no
  exception handler (a `pop` cannot raise); state this explicitly.

### Empty/Invalid Input Handling
- [ ] No registered bots → function returns an empty offending set and makes no resolver calls
  (existing `test_live_flag_no_bots_makes_no_calls` is updated to assert the empty-set return).
- [ ] All ids resolve to real bots → empty offending set, no quarantine, success log fires.

### Error State Rendering
- [ ] The loud ERROR log is asserted to still fire on a mismatch (caplog assertion) — surfacing
  remains valuable even after quarantine.

## Test Impact

- [ ] `tests/unit/test_dm_whitelist_validation.py::test_live_flag_human_account_surfaces_mismatch` —
  UPDATE: assert the human id is in the returned offending set (and detail string contains "NON-bot")
  instead of `pytest.raises(ConfigValidationError)`.
- [ ] `tests/unit/test_dm_whitelist_validation.py::test_live_flag_unresolvable_id_surfaces_error` —
  UPDATE: assert the unresolvable id is in the returned offending set (detail contains "failed to
  resolve") instead of `pytest.raises`.
- [ ] `tests/unit/test_dm_whitelist_validation.py::test_live_flag_bot_true_passes` — UPDATE: assert the
  return is an empty offending set instead of "must not raise".
- [ ] `tests/unit/test_dm_whitelist_validation.py::test_live_flag_no_bots_makes_no_calls` — UPDATE:
  assert empty-set return in addition to the existing "no resolver calls" assertion.
- [ ] `tests/unit/test_dm_whitelist_validation.py::test_live_flag_deduplicates_repeated_ids` — UPDATE:
  assert empty-set return (probe still dedupes; the contract change is the return value).
- [ ] `tests/integration/test_bot_loop_guard.py` — UPDATE (add a new test, see below): add
  `test_quarantined_id_is_not_suppressed` proving that after popping a mis-registered human id from
  `routing.BOT_ID_TO_PROJECT`, `find_project_for_bot(human_id)` returns `None` and
  `should_respond_sync(..., sender_id=human_id)` returns `True`. The existing 3 tests in this file are
  unaffected (they don't touch the live-flag probe).

No other tests reference `validate_bot_live_flags` or its raise behavior (grep-confirmed: only the 5
unit tests above import it).

## Rabbit Holes

- **Re-validating on a schedule / live re-probe loop**: tempting to periodically re-probe ids in case
  a token is fixed at runtime. Out of scope — a config fix requires a bridge restart anyway, which
  rebuilds the full map. Don't build a background re-validation loop.
- **Surfacing the quarantine to a human via Telegram/email alert**: the loud ERROR log is the agreed
  surface. Wiring a push notification is a separate concern, not this bug.
- **Refactoring the module-load-time dict aliasing** (`telegram_bridge.py:652`): the aliasing is the
  mechanism this fix relies on. Don't "clean it up" into per-module copies — that would break the
  single-pop quarantine. Document the invariant instead.
- **Touching the structural `validate_bot_live_flags` callers in `validate_projects_config`**: the
  live probe is NOT in that aggregator. Don't change `validate_projects_config`.

## Risks

### Risk 1: The dict-aliasing invariant (`telegram_bridge.py:652`) is broken by a future refactor
**Impact:** A pop on the bridge's `BOT_ID_TO_PROJECT` would no longer clear the routing copy, so
`find_project_for_bot` would keep returning a hit and the human would stay suppressed — silently
reintroducing this exact bug.
**Mitigation:** Add a code comment at the quarantine site documenting the aliasing invariant, and add
the integration test (`test_quarantined_id_is_not_suppressed`) that asserts `find_project_for_bot`
returns `None` after a pop — that test fails loudly if the alias is ever broken.

### Risk 2: Changing the return contract breaks an unseen caller
**Impact:** A caller expecting `validate_bot_live_flags` to raise would silently stop getting the
exception.
**Mitigation:** Grep-confirmed the only caller is `main()` (`telegram_bridge.py:2799`) and the only
test importers are the 5 unit tests, all listed in Test Impact. All are updated in this plan.

## Race Conditions

No race conditions identified. The live-flag probe and quarantine run sequentially in `main()` at
bridge startup, *before* the NewMessage handler is registered and serving — so no inbound message can
read `BOT_ID_TO_PROJECT` concurrently with the quarantine pop. The pop completes before any handler
reads the map. All operations are single-threaded within the bridge startup coroutine.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1574] Re-probing bot ids on a runtime schedule (live re-validation loop) — the
  parent issue #1574 established that config changes require a restart; runtime re-probe is a distinct
  enhancement, not part of this harm-prevention fix.
- Nothing else deferred — the quarantine, the retained ERROR log, and the regression tests are all in
  scope for this plan.

## Update System

No update system changes required — this feature is purely internal to the bridge. No new
dependencies, config files, or migration steps. Existing `projects.json` schema is unchanged (the fix
acts on the *runtime* map, not the config shape).

## Agent Integration

No agent integration required — this is a bridge-internal change. The fix modifies how
`bridge/telegram_bridge.py main()` reacts to a live-flag mismatch at startup; it exposes no new CLI
entry point and requires no new `pyproject.toml [project.scripts]` entry or MCP tool. The bridge
already imports `validate_bot_live_flags` directly (`telegram_bridge.py:2793`); only its call-site
handling changes. Coverage is via unit + integration tests (no agent-invocation path).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bot-e2e-testing.md` — add a subsection documenting the quarantine
  behavior: on a live-flag mismatch the offending id is removed from the runtime bot registry (fail-safe
  toward "treat as human, allow the message"), with the loud ERROR log retained. Note that a config fix
  requires a bridge restart to re-register.

### External Documentation Site
- [ ] No external docs site changes — this repo's feature docs live under `docs/features/`.

### Inline Documentation
- [ ] Update the `validate_bot_live_flags` docstring (`config_validation.py:308`) to reflect the new
  return contract (returns offending ids instead of raising).
- [ ] Add a comment at the `main()` quarantine site (`telegram_bridge.py:2792-2805`) documenting the
  dict-aliasing invariant (line 652) that makes a single pop clear both maps.

## Success Criteria

- [ ] `validate_bot_live_flags` returns the set of offending bot ids (non-bot, unresolvable, or
  resolver-error) and no longer raises `ConfigValidationError`.
- [ ] On a live-flag mismatch, `main()` logs the loud `REGISTERED BOT MISCONFIGURATION (#1574)` ERROR
  AND pops each offending id from `BOT_ID_TO_PROJECT`.
- [ ] Regression test: after a mismatch is quarantined, `find_project_for_bot(human_id)` returns
  `None` and `should_respond_sync(..., sender_id=human_id)` returns `True` (message NOT suppressed).
- [ ] The 5 existing live-flag unit tests are updated to the new return contract and pass.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (quarantine)**
  - Name: quarantine-builder
  - Role: Change the `validate_bot_live_flags` return contract and wire the quarantine + retained ERROR log in `main()`; update the 5 unit tests and add the integration regression test.
  - Agent Type: builder
  - Resume: true

- **Validator (quarantine)**
  - Name: quarantine-validator
  - Role: Verify quarantine behavior, return-contract correctness, retained ERROR log, and the regression assertions.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: bot-docs
  - Role: Update `docs/features/bot-e2e-testing.md` and the inline docstring/comment.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard roster — see template. This plan uses builder, validator, documentarian.)

## Step by Step Tasks

### 1. Change return contract + wire quarantine
- **Task ID**: build-quarantine
- **Depends On**: none
- **Validates**: tests/unit/test_dm_whitelist_validation.py, tests/integration/test_bot_loop_guard.py
- **Informed By**: Data Flow (dict aliasing at telegram_bridge.py:652 means one pop clears both maps)
- **Assigned To**: quarantine-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/config_validation.py:308`, change `validate_bot_live_flags` to collect offending bot ids and return `(offending_ids: set[int], detail: str | None)` instead of raising. Keep the human-readable detail string for logging. Update the docstring.
- In `bridge/telegram_bridge.py:2792-2805`, replace the `try/except ConfigValidationError` with: call the function; on non-empty offending set, `logger.error("REGISTERED BOT MISCONFIGURATION (#1574): %s", detail)` then `for bot_id in offending: BOT_ID_TO_PROJECT.pop(bot_id, None)`. Keep the success-path `logger.info`. Add a comment documenting the line-652 aliasing invariant.
- Remove the now-unused `ConfigValidationError` import in that block if nothing else needs it (verify first).

### 2. Update existing live-flag unit tests
- **Task ID**: build-unit-tests
- **Depends On**: build-quarantine
- **Assigned To**: quarantine-builder
- **Agent Type**: builder
- **Parallel**: false
- Update the 5 tests in `tests/unit/test_dm_whitelist_validation.py` (lines 481-549) to assert the new return contract: empty set for valid bots; offending id present for human/unresolvable; detail string substring checks preserved.

### 3. Add quarantine regression integration test
- **Task ID**: build-regression
- **Depends On**: build-quarantine
- **Assigned To**: quarantine-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `test_quarantined_id_is_not_suppressed` to `tests/integration/test_bot_loop_guard.py`: register a bot id in `routing.BOT_ID_TO_PROJECT` (via the existing fixture pattern), simulate quarantine by popping it, then assert `routing.find_project_for_bot(id) is None` and `routing.should_respond_sync(text=..., is_dm=True, project=..., sender_id=id) is True`.

### 4. Validate
- **Task ID**: validate-quarantine
- **Depends On**: build-quarantine, build-unit-tests, build-regression
- **Assigned To**: quarantine-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_dm_whitelist_validation.py tests/integration/test_bot_loop_guard.py -q`.
- Confirm the ERROR log is retained and the quarantine pop clears `find_project_for_bot`.
- Report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-quarantine
- **Assigned To**: bot-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bot-e2e-testing.md` with the quarantine behavior subsection.
- Confirm inline docstring (config_validation.py) and aliasing comment (telegram_bridge.py) are present.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: quarantine-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table commands.
- Confirm all success criteria met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Live-flag + loop-guard tests pass | `pytest tests/unit/test_dm_whitelist_validation.py tests/integration/test_bot_loop_guard.py -q` | exit code 0 |
| Quarantine regression test exists | `grep -rn "test_quarantined_id_is_not_suppressed" tests/integration/test_bot_loop_guard.py` | output contains test_quarantined_id_is_not_suppressed |
| Quarantine pop wired in main() | `grep -n "BOT_ID_TO_PROJECT.pop" bridge/telegram_bridge.py` | output contains BOT_ID_TO_PROJECT.pop |
| Loud ERROR log retained | `grep -n "REGISTERED BOT MISCONFIGURATION" bridge/telegram_bridge.py` | output contains REGISTERED BOT MISCONFIGURATION |
| Validator no longer raises on mismatch | `grep -c "raise ConfigValidationError" bridge/config_validation.py` | output > 0 |
| Lint clean | `python -m ruff check bridge/ tests/` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/ tests/` | exit code 0 |

<!-- Note: "Validator no longer raises on mismatch" expects output > 0 because the OTHER structural
     validators in config_validation.py still raise ConfigValidationError; only validate_bot_live_flags
     stops raising. The behavioral assertion that the live-flag probe no longer raises is covered by the
     updated unit tests, not by a grep. -->

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Return shape**: the plan proposes `validate_bot_live_flags` returns `(set[int], str | None)`.
   An alternative is to keep it raising but attach the offending ids to the exception
   (`ConfigValidationError.offending_ids`). The tuple-return is cleaner because mismatch is a normal
   (non-crashing) outcome for the bridge — is that the preferred shape, or do you want to preserve the
   raise-with-attribute form for symmetry with the other structural validators?
