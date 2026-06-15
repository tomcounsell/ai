---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-15
tracking: https://github.com/tomcounsell/ai/issues/1653
last_comment_id:
---

# Patch popoto DatetimeField auto_now/auto_now_add to stamp datetime.now(UTC)

## Problem

popoto's `DatetimeField` implements Django-style `auto_now` / `auto_now_add` by
returning bare `datetime.now()` — naive **local** wall-clock time, no tzinfo. The
datetime encoder then strips any tzinfo via `strftime`, so the persisted
wall-clock value is whatever `datetime.now()` returned: local time, not UTC. On
any non-UTC host, every `auto_now` / `auto_now_add` timestamp is skewed by the
host's UTC offset (e.g. +7h on a UTC+7 machine), and downstream "age since last
update" math goes wrong (negative ages, masked staleness).

This is the **defense-in-depth follow-up to #1645**. #1645 already fixed the only
current consumer in the `ai` repo (`AgentSession.updated_at`) locally by dropping
`auto_now=True` and stamping `utc_now()` explicitly (merged in PR #1655). This
issue hardens popoto itself so a **future** `auto_now` field on **any** popoto
model is correct-by-default and the bug cannot recur.

**Current behavior:**
`DatetimeField.format_value_pre_save` returns `datetime.now()` (naive local) for
both the `auto_now_add` and `auto_now` branches. The serialized wall-clock value
is local time, mislabeled as UTC by every consumer that re-attaches `tz=UTC` on
read.

**Desired outcome:**
`format_value_pre_save` stamps UTC wall-clock time, so any popoto model using
`auto_now` / `auto_now_add` records correct timestamps regardless of host
timezone. A new popoto version carrying this fix is published to PyPI, pinned in
`ai`, and propagated to every machine.

## Freshness Check

**Baseline commit:** `777570c5` (ai repo, main)
**Issue filed at:** 2026-06-12T16:22:56Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `~/src/popoto/src/popoto/fields/datetime_field.py:116` — `auto_now_add` branch returns bare `datetime.now()` — still holds (v1.7.0).
- `~/src/popoto/src/popoto/fields/datetime_field.py:118` — `auto_now` branch returns bare `datetime.now()` — still holds.
- `~/src/popoto/src/popoto/fields/datetime_field.py:25` — module imports only `from datetime import datetime`; no `UTC`/`timezone` in scope — still holds (the fix must add the import).
- `~/src/popoto/src/popoto/models/encoding.py:91` — datetime encoder strips tzinfo via `strftime("%Y%m%dT%H:%M:%S.%f")` — still holds; this is *why* minting UTC wall-clock at the producer is the correct fix.
- Installed `ai/.venv/.../popoto/fields/datetime_field.py:116,118` — same defect, PyPI `1.6.1` — confirmed.

**Cited sibling issues/PRs re-checked:**
- #1645 — closed 2026-06-12T17:35:42Z; local producer fix landed via PR #1655 (merged 2026-06-12T17:35:41Z). The consumer the issue used as motivation no longer uses `auto_now`, so this fix is purely forward-looking hardening.

**Commits on main since issue was filed (touching `pyproject.toml` / `uv.lock`):**
- `64dcf016`, `8e1fbf93` — claude-agent-sdk dep bumps — irrelevant; popoto pin (`>=1.6.1`, locked `1.6.1`) unchanged.

**Active plans in `docs/plans/` overlapping this area:** `fix-response-delivered-at-datetimefield.md` is the #1645 plan (the local fix, already merged). No overlap with the upstream popoto patch — that plan is complete and touches only `ai` code.

**Notes:** No drift. The issue's "build + publish to PyPI" step is implemented by popoto's existing tag-triggered trusted-publisher pipeline (see Research); the human gate is the version-tag push + `release` environment approval, not a manual `twine upload`.

## Prior Art

- **PR #1655** (merged 2026-06-12): `fix(#1645): AgentSession.updated_at stamped in UTC (remove auto_now, explicit utc_now() in save())` — fixed the only current `ai` consumer locally. This plan does NOT touch `ai` model code; it fixes the popoto producer so the workaround in #1655 wouldn't be needed for future fields.
- **popoto #380 / ai #1099 + #1172** — prior popoto bump (`>=1.6.1`) for the lazy-load descriptor leak; precedent for the bump-pin-and-relock workflow this plan repeats.
- No prior attempt has patched `DatetimeField`'s timezone handling. This is the first fix at the producer layer.

## Research

This work centers on a library we control (`~/src/popoto`) plus its existing PyPI
release tooling. Ground truth came from reading the repo, not the web.

**Key findings:**
- popoto ships a **tag-triggered trusted-publisher release pipeline**:
  `~/src/popoto/.github/workflows/release.yml` fires on `v*` / `popoto-v*` tags,
  builds with `python -m build`, and publishes via
  `pypa/gh-action-pypi-publish@release/v1` using OIDC (`environment: release`,
  `id-token: write`). No API token in CI; publish authority is the GitHub
  `release` environment + the tag push. **The human-gated step is the version-tag
  push (and any required `release`-environment approval), not a manual upload.**
- Current popoto version is **`1.7.0`** (`~/src/popoto/pyproject.toml`), latest
  git tag `v1.7.0`. The CHANGELOG has an `## [Unreleased]` section with pending
  features (ContextAssembler retrieval modes). The fix should go out as the next
  patch/minor release with its own tag.
- popoto's encoder is the reason the fix belongs at the producer: it persists
  wall-clock via `strftime` (no tz), and the `ai` consumer
  (`models/agent_session.py:781`) re-attaches `tz=UTC` on read. Minting
  `datetime.now(UTC)` makes the stored wall-clock UTC, which round-trips correctly.

No external library research needed — the fix is a two-line change plus a release.

## Data Flow

1. **Producer (popoto, the fix target):** model `.save()` → `DatetimeField.format_value_pre_save` → currently returns naive `datetime.now()`. **After fix:** returns `datetime.now(UTC)` (tz-aware UTC).
2. **Encoder (popoto):** `encoding.py:91` serializes the datetime via `strftime("%Y%m%dT%H:%M:%S.%f")`, stripping tzinfo → stores UTC wall-clock string in Redis.
3. **Decoder (popoto):** `encoding.py:93` rebuilds a naive datetime via `strptime`.
4. **Consumer (ai):** e.g. `models/agent_session.py:781` re-attaches `tz=UTC` → correct UTC instant. (No `ai` consumer change needed; #1645 already made the one live consumer explicit.)

## Architectural Impact

- **New dependencies:** none. The popoto fix adds only `UTC` (or `timezone`) to an existing `from datetime import ...` line.
- **Interface changes:** none. `format_value_pre_save` signature unchanged; only the returned value's tz-awareness changes.
- **Coupling:** decreases reliance on host timezone — popoto becomes timezone-correct by default.
- **Data ownership:** unchanged.
- **Reversibility:** fully reversible — revert the popoto patch + republish, or pin `ai` back to `1.6.1`. The change is additive-correct; existing UTC-host data is unaffected (UTC host already stamped UTC).

## Appetite

**Size:** Small

**Team:** Solo dev, + human for the PyPI tag push (gate)

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (popoto PR review before tag)

The coding is two lines plus a popoto-side test. The real cost is the
cross-repo, human-gated release-and-propagate cycle, not implementation time.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto repo present | `test -f ~/src/popoto/src/popoto/fields/datetime_field.py` | The fix target is a sibling repo |
| popoto release workflow present | `test -f ~/src/popoto/.github/workflows/release.yml` | Tag-triggered PyPI publish exists |
| `uv` available | `uv --version` | Refresh `ai/uv.lock` after the pin bump |

Run all checks: `python scripts/check_prerequisites.py docs/plans/popoto_datetimefield_utc_stamp.md`

## Solution

### Key Elements

- **popoto `DatetimeField` patch**: `format_value_pre_save` stamps `datetime.now(UTC)` instead of `datetime.now()` for both the `auto_now_add` and `auto_now` branches. Add `UTC` to the `from datetime import ...` line.
- **popoto test**: add a test asserting that an `auto_now` / `auto_now_add` field stamps UTC wall-clock (assert the stored/round-tripped value matches `datetime.now(UTC)` within tolerance, NOT naive local), in `~/src/popoto/tests/test_field_types.py`.
- **popoto version bump + CHANGELOG**: bump `~/src/popoto/pyproject.toml` version (next patch, e.g. `1.7.1`), add a CHANGELOG entry under a new released section.
- **PyPI release (HUMAN-GATED)**: push the `v{new}` tag → `release.yml` builds and publishes via OIDC. Human pushes the tag (and approves the `release` environment if configured).
- **ai pin bump + relock**: update `ai/pyproject.toml` popoto specifier to `>={new}` and run `uv lock` to refresh `ai/uv.lock` to the new hash-pinned version.
- **Propagation**: every machine picks up the new pin on its next `/update` (which runs `uv sync`). No update-script change required.

### Flow

popoto repo → edit `DatetimeField` + test + version + CHANGELOG → open popoto PR → review/merge → **[HUMAN] push `v1.7.1` tag** → `release.yml` publishes to PyPI → ai repo: bump pin + `uv lock` → open ai PR → merge → `/update` propagates to all machines.

### Technical Approach

- **The fix** (popoto `datetime_field.py`):
  - Line 25: `from datetime import datetime` → `from datetime import datetime, UTC`
  - Line 116 (`auto_now_add` branch): `return datetime.now()` → `return datetime.now(UTC)`
  - Line 118 (`auto_now` branch): `return datetime.now()` → `return datetime.now(UTC)`
  - (`UTC` is available from `datetime` on Python 3.11+; popoto's release CI uses 3.12 and `ai` runs 3.14, so `datetime.UTC` is safe. If popoto still supports <3.11, use `from datetime import datetime, timezone` and `datetime.now(timezone.utc)`. Verify popoto's `requires-python` during build.)
- **Version choice**: bug-fix patch release. Current is `1.7.0` with an unreleased feature block in CHANGELOG; coordinate the version number so the release carries this fix cleanly (likely `1.7.1`, or fold into whatever the next release is — decide at build time, see Open Questions).
- **Pin bump**: `ai/pyproject.toml:17` `"popoto>=1.6.1"` → `"popoto>={new}"`; then `uv lock` rewrites the `uv.lock` popoto entry (version + hashes).
- **Integration points**: none in `ai` beyond the pin — the producer fix is transparent to all `ai` consumers (they already treat decoded datetimes as UTC).

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. `format_value_pre_save` is a pure value transform with no try/except. The `ai`-side change is a dependency-pin edit only.

### Empty/Invalid Input Handling
- `format_value_pre_save` already guards on `field_value` truthiness for the `auto_now_add` branch (`if self.auto_now_add and not field_value`). The popoto test should cover: (a) `auto_now_add` with no prior value stamps UTC; (b) `auto_now` overwrites on every save with UTC; (c) `skip_auto_now=True` preserves the existing value (regression guard — must not change).

### Error State Rendering
- No user-visible output path. The only observable surface is the stored timestamp value, asserted directly in the popoto test.

## Test Impact

- [ ] `~/src/popoto/tests/test_field_types.py` — ADD: a UTC-stamping assertion for `auto_now`/`auto_now_add` (popoto-side test; not in the `ai` suite). This is the load-bearing regression guard for the fix.
- [ ] `~/src/popoto/tests/test_field_types.py` — VERIFY/UPDATE: any existing `DatetimeField` test that asserts naive-local behavior must be updated to expect UTC. Audit during the popoto build step.

No existing **ai-repo** tests are affected — the `ai` change is a pin bump only, and #1645's tests (PR #1655) already assert `AgentSession.updated_at` is explicit-UTC via `utc_now()`, independent of popoto's `auto_now` behavior. The `ai` suite continues to pass unchanged against the new popoto version because no `ai` model uses `auto_now` anymore (grep-confirmed in #1645 recon).

## Rabbit Holes

- **Migrating popoto to fully tz-aware datetimes end-to-end** (encoder/decoder carrying tzinfo through serialization). Tempting, but the encoder deliberately stores wall-clock strings; making it tz-aware is a much larger, riskier change with backward-compat concerns for existing stored data. Out of scope — minting UTC at the producer is sufficient and correct given the current encoder.
- **Re-healing already-stored `ai` data.** #1645 already healed `AgentSession` future-stamped records. No popoto model other than the (now-fixed) `AgentSession.updated_at` ever used `auto_now`, so there is no stale data to heal here.
- **Folding unrelated popoto `[Unreleased]` CHANGELOG features into this release decision.** Don't gate this bugfix on the ContextAssembler feature work; pick a version that ships the fix cleanly and move on.

## Risks

### Risk 1: popoto `requires-python` is older than 3.11 (no `datetime.UTC`)
**Impact:** `from datetime import UTC` fails to import on the build/CI runner, breaking the release.
**Mitigation:** Check `~/src/popoto/pyproject.toml` `requires-python` during build. If `<3.11`, use `from datetime import timezone` + `datetime.now(timezone.utc)` (works on all supported versions, identical result).

### Risk 2: PyPI publish is human-gated and may stall
**Impact:** The `ai` pin bump cannot land until the new popoto version is on PyPI; an un-pushed tag blocks the whole chain.
**Mitigation:** Sequence explicitly via No-Gos `[ORDERED]`. The popoto PR can be reviewed/merged independently; the `ai` PR is opened only after the version is confirmed live on PyPI (`pip index versions popoto` or PyPI JSON API shows the new version).

### Risk 3: New popoto version carries unintended changes (the `[Unreleased]` block)
**Impact:** Publishing from `main` may bundle in-flight features, widening the blast radius of an `ai` bump beyond this fix.
**Mitigation:** Confirm what's on popoto `main` at tag time. If unrelated features are present and not ready, branch the fix or coordinate the version so `ai` only adopts a stable release. Surface in Open Questions.

## Race Conditions

No race conditions identified. The fix is a synchronous, single-threaded pure
value transform (`datetime.now(UTC)` instead of `datetime.now()`). The release
and pin-bump steps are sequential, human-gated, and idempotent.

## No-Gos (Out of Scope)

- [EXTERNAL] Pushing the popoto `v{new}` version tag and approving the `release` GitHub environment — requires a maintainer with publish authority on the popoto PyPI project; the agent cannot push tags that trigger a third-party publish nor approve a protected environment.
- [ORDERED] Bumping `ai/pyproject.toml` + `uv lock` to the new popoto version — must wait until the new version is confirmed live on PyPI (gated on the EXTERNAL tag push above).
- [ORDERED] Running `/update` to propagate the new pin across machines — must wait until the `ai` pin-bump PR merges.
- No end-to-end tz-aware serialization rewrite of popoto's encoder/decoder (see Rabbit Holes).

## Update System

The `/update` skill runs `uv sync` (or equivalent dependency sync) on each
machine, so once the `ai/uv.lock` pin is bumped and merged, the new popoto
version propagates automatically on the next `/update`. **No change to the update
script or update skill is required** — this rides the existing dependency-sync
path. The only operational note: after this `ai` PR merges, every machine must
run `/update` to pull the fixed popoto; until then, machines keep the old
`1.6.1` (which is harmless because no `ai` model still uses `auto_now`).

## Agent Integration

No agent integration required. This change is an upstream library fix plus a
dependency-pin bump. It exposes no new CLI entry point, no new MCP tool, and the
bridge does not import popoto's `DatetimeField` directly. The agent's behavior is
unchanged; only the correctness of any *future* `auto_now` field improves. No
`pyproject.toml [project.scripts]` entry and no `bridge/telegram_bridge.py`
import are needed.

## Documentation

### Feature Documentation
- [ ] No `ai`-side feature doc — this is a dependency correctness fix, not a user-facing feature. Add a one-line note to the popoto `CHANGELOG.md` (the canonical changelog for the library fix) under the released version section.

### External Documentation Site
- [ ] popoto's own docs (Read the Docs, `deploy-docs.yml`) need no change — `auto_now`/`auto_now_add` are already documented as "current datetime"; the fix makes the implementation match the (already-UTC-implying) contract. Optionally add a sentence clarifying timestamps are UTC.

### Inline Documentation
- [ ] Update the `format_value_pre_save` docstring in `~/src/popoto/src/popoto/fields/datetime_field.py` to state it stamps UTC (the docstring currently says "current time" / "datetime.now()" — make it say UTC).

(`ai`-repo documentation: none needed. The only `ai` change is a pin bump in `pyproject.toml` + `uv.lock`, with the rationale captured in the existing pin comment on line 17 — update that comment to reference #1653.)

## Success Criteria

- [ ] `~/src/popoto/src/popoto/fields/datetime_field.py` `format_value_pre_save` returns `datetime.now(UTC)` (or `datetime.now(timezone.utc)`) for both branches; `UTC`/`timezone` imported.
- [ ] popoto test asserts `auto_now`/`auto_now_add` stamps UTC wall-clock (passes in the popoto suite).
- [ ] popoto version bumped in `pyproject.toml` and CHANGELOG entry added.
- [ ] New popoto version published to PyPI (verified via `pip index versions popoto` or PyPI JSON API). **[HUMAN-GATED]**
- [ ] `ai/pyproject.toml` popoto specifier bumped to `>={new}`; pin comment references #1653.
- [ ] `ai/uv.lock` refreshed via `uv lock` to the new version + hashes.
- [ ] `ai` test suite passes against the new popoto version (`pytest tests/unit/`).
- [ ] Propagation path confirmed: new pin rides the existing `/update` `uv sync` (machines adopt it on their next routine update; tracked under No-Gos [ORDERED]).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When executed, the lead coordinates; the popoto fix and the ai-pin bump are two
sequential phases separated by the human PyPI gate.

### Team Members

- **Builder (popoto-fix)**
  - Name: `popoto-builder`
  - Role: Patch `DatetimeField.format_value_pre_save` to UTC, add test, bump version + CHANGELOG, update docstring — all in `~/src/popoto`.
  - Agent Type: builder
  - Resume: true

- **Validator (popoto-fix)**
  - Name: `popoto-validator`
  - Role: Verify the popoto patch, run popoto's test suite, confirm version bump and CHANGELOG.
  - Agent Type: validator
  - Resume: true

- **Builder (ai-pin)**
  - Name: `ai-pin-builder`
  - Role: After the new popoto version is live on PyPI, bump `ai/pyproject.toml` specifier, run `uv lock`, run `ai` unit tests, update the pin comment.
  - Agent Type: builder
  - Resume: true

- **Validator (ai-pin)**
  - Name: `ai-pin-validator`
  - Role: Verify `uv.lock` carries the new version + hashes and the `ai` suite passes.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

(see template — Tier 1 `builder` / `validator` suffice for this Small work.)

## Step by Step Tasks

### 1. Patch popoto DatetimeField + test + version
- **Task ID**: build-popoto-fix
- **Depends On**: none
- **Validates**: `~/src/popoto/tests/test_field_types.py` (popoto suite, run in the popoto repo)
- **Assigned To**: popoto-builder
- **Agent Type**: builder
- **Parallel**: false
- In `~/src/popoto/src/popoto/fields/datetime_field.py`: import `UTC` (or `timezone`, gated on `requires-python`), change both `datetime.now()` returns at lines 116/118 to UTC, update the docstring to say UTC.
- Add a popoto test asserting `auto_now`/`auto_now_add` stamps UTC wall-clock and that `skip_auto_now=True` preserves the existing value.
- Bump `~/src/popoto/pyproject.toml` version (e.g. `1.7.1`) and add a CHANGELOG entry under a released section.
- Run popoto's own test suite; confirm green.

### 2. Validate popoto fix
- **Task ID**: validate-popoto-fix
- **Depends On**: build-popoto-fix
- **Assigned To**: popoto-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm both branches return UTC; confirm import is correct for popoto's `requires-python`.
- Run the popoto test suite; verify the new UTC test passes and no existing `DatetimeField` test regressed.
- Confirm version + CHANGELOG are consistent. Report pass/fail.

### 3. [HUMAN GATE] Publish popoto to PyPI
- **Task ID**: publish-popoto (human-gated; see No-Gos [EXTERNAL])
- **Depends On**: validate-popoto-fix
- Maintainer merges the popoto PR, pushes the `v{new}` tag, approves the `release` environment if configured. `release.yml` builds + publishes via OIDC.
- Verify the new version is live: `pip index versions popoto` (or PyPI JSON API).

### 4. Bump ai pin + relock
- **Task ID**: build-ai-pin
- **Depends On**: publish-popoto
- **Validates**: `pytest tests/unit/` (ai suite)
- **Assigned To**: ai-pin-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `ai/pyproject.toml:17` specifier to `>={new}`; update the pin comment to reference #1653.
- Run `uv lock`; confirm `ai/uv.lock` popoto entry shows the new version + refreshed hashes.
- Run `pytest tests/unit/`; confirm green against the new popoto.

### 5. Validate ai pin
- **Task ID**: validate-ai-pin
- **Depends On**: build-ai-pin
- **Assigned To**: ai-pin-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `uv.lock` carries the new version + hashes; confirm `ai` unit suite passes. Report pass/fail.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-ai-pin
- **Assigned To**: popoto-builder (docstring/CHANGELOG already done in task 1) + ai-pin-builder (pin comment in task 4)
- **Agent Type**: documentarian
- **Parallel**: false
- Verify popoto docstring + CHANGELOG say UTC; verify `ai` pin comment references #1653. No new `docs/features/` page (dependency fix).

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-ai-pin, document-feature
- **Assigned To**: ai-pin-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm every Success Criterion met (popoto fix live, pin bumped, lock refreshed, suite green). Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| popoto fix present | `grep -c 'datetime.now(UTC)\|datetime.now(timezone.utc)' ~/src/popoto/src/popoto/fields/datetime_field.py` | output > 1 |
| popoto no naive auto-now | `grep -n 'return datetime.now()' ~/src/popoto/src/popoto/fields/datetime_field.py` | exit code 1 |
| popoto version live on PyPI | `pip index versions popoto 2>/dev/null \| grep -F "$(grep '^version' ~/src/popoto/pyproject.toml \| head -1 \| sed 's/.*"\(.*\)".*/\1/')"` | output contains version (human-gated) |
| ai lock updated | `grep -A1 'name = "popoto"' uv.lock \| grep version` | output contains new version |
| ai tests pass | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Version number / release coordination.** Current popoto is `1.7.0` with an unreleased CHANGELOG block (ContextAssembler features). Should this fix ship as a standalone `1.7.1` patch (cherry-picked / branch from the `v1.7.0` tag to exclude in-flight features), or fold into the next planned popoto release that also carries the `[Unreleased]` work? This determines the tag and what `ai` adopts.
2. **Who pushes the tag.** The PyPI publish is human-gated via the `release` GitHub environment. Confirm which maintainer/machine pushes the `v{new}` tag so the EXTERNAL no-go has an owner.
3. **`requires-python` floor.** If popoto must support Python <3.11, use `timezone.utc` instead of `datetime.UTC`. Confirm popoto's `requires-python` at build time so the builder picks the right import (functionally identical either way).
