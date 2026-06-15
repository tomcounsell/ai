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
- **Reversibility:** rollback is the **ai-side pin pin-back** (`>=1.7.1` → `==1.6.1`, `uv lock`, merge, `/update`), not a popoto re-release — PyPI versions are immutable, so "revert + republish" means a new forward-fix version, never the same tag. The change is additive-correct; existing UTC-host data is unaffected (UTC host already stamped UTC).
- **Write-only, non-migrating (mixed-epoch window):** the fix changes only the wall-clock value of **new** writes. On a non-UTC host, pre-fix rows hold naive-**local** strings and post-fix rows hold naive-**UTC** strings, with no offset marker to distinguish them — so cross-boundary ordering/age math is briefly wrong. This is accepted, not migrated: #1645 already healed the only live `auto_now` consumer (`AgentSession.updated_at`, short-lived records), and no other popoto model uses `auto_now`. See Rabbit Holes.

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

- **popoto `DatetimeField` patch**: `format_value_pre_save` stamps `datetime.now(timezone.utc)` instead of `datetime.now()` for both the `auto_now_add` and `auto_now` branches. Add `timezone` to the `from datetime import ...` line. (`datetime.UTC` is forbidden — 3.11+, breaks popoto's `>=3.10` floor.)
- **popoto test**: add a test asserting that an `auto_now` / `auto_now_add` field stamps UTC wall-clock via naive-to-naive comparison (the encoder strips tzinfo, so compare against `datetime.now(timezone.utc).replace(tzinfo=None)`, NOT an aware value), in `~/src/popoto/tests/test_auto_timestamps.py`.
- **popoto version bump + CHANGELOG**: bump `~/src/popoto/pyproject.toml` version (next patch, e.g. `1.7.1`), add a CHANGELOG entry under a new released section.
- **PyPI release (HUMAN-GATED)**: push the `v{new}` tag → `release.yml` builds and publishes via OIDC. Human pushes the tag (and approves the `release` environment if configured).
- **ai pin bump + relock**: update `ai/pyproject.toml` popoto specifier to `>={new}` and run `uv lock` to refresh `ai/uv.lock` to the new hash-pinned version.
- **Propagation**: every machine picks up the new pin on its next `/update` (which runs `uv sync`). No update-script change required.

### Flow

popoto repo → edit `DatetimeField` + test + version + CHANGELOG → open popoto PR → review/merge to main → **[HUMAN] cut `release/1.7.1` from tag `v1.7.0`, cherry-pick only the fix, push `v1.7.1` tag from that branch** → `release.yml` publishes to PyPI (excludes the `[Unreleased]` block) → **[HUMAN] confirm live via `pip index versions popoto`** → ai repo: bump pin to `>=1.7.1` + `uv lock` → open ai PR → merge → `/update` propagates to all machines.

### Technical Approach

- **The fix** (popoto `datetime_field.py`) — use `timezone.utc` **unconditionally** (mandated by critique; `datetime.UTC` is 3.11+ and popoto's `requires-python = ">=3.10"`):
  - Line 25: `from datetime import datetime` → `from datetime import datetime, timezone`
  - Line 116 (`auto_now_add` branch): `return datetime.now()` → `return datetime.now(timezone.utc)`
  - Line 118 (`auto_now` branch): `return datetime.now()` → `return datetime.now(timezone.utc)`
  - `timezone.utc` is valid Python 3.2+ and functionally identical to `datetime.UTC`. Do **not** use `datetime.UTC` — it would build green on the 3.12 release CI but `ImportError` on any 3.10 install of popoto. No version gate, no build-time check needed.
- **Version choice**: ship as standalone patch `1.7.1` cut from tag `v1.7.0` (see Flow), so the `[Unreleased]` CHANGELOG block (MemoryLifecycle, ContextAssembler) does **not** ride along. Branch `release/1.7.1` off `v1.7.0`, cherry-pick only the DatetimeField fix commit, tag from that branch.
- **Pin bump**: `ai/pyproject.toml:17` `"popoto>=1.6.1"` → `"popoto>=1.7.1"` (the fix floor — **not** a loose carryover, so no machine can silently resolve still-buggy 1.7.0); then `uv lock` rewrites the `uv.lock` popoto entry (version + hashes).
- **Integration points**: none in `ai` beyond the pin — the producer fix is transparent to all `ai` consumers (they already treat decoded datetimes as UTC).

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. `format_value_pre_save` is a pure value transform with no try/except. The `ai`-side change is a dependency-pin edit only.

### Empty/Invalid Input Handling
- `format_value_pre_save` already guards on `field_value` truthiness for the `auto_now_add` branch (`if self.auto_now_add and not field_value`). The popoto test should cover: (a) `auto_now_add` with no prior value stamps UTC; (b) `auto_now` overwrites on every save with UTC; (c) `skip_auto_now=True` preserves the existing value (regression guard — must not change).
- **Comparison must be naive-to-naive.** The encoder (`encoding.py:91`) strips tzinfo via `strftime`, so the decoded round-trip value is **naive**. Comparing it against an aware `datetime.now(timezone.utc)` raises `TypeError`. Capture `before = datetime.now(timezone.utc).replace(tzinfo=None)` and `after` the same way, then assert `before <= decoded <= after` (a tolerance window, never exact equality). The real signal: on a non-UTC host the decoded wall-clock now equals UTC-now, not local-now — run the test under `TZ=America/New_York` so a naive-`datetime.now()` regression visibly diverges.

### Error State Rendering
- No user-visible output path. The only observable surface is the stored timestamp value, asserted directly in the popoto test.

## Test Impact

- [ ] `~/src/popoto/tests/test_auto_timestamps.py` — ADD: a UTC-stamping assertion for `DatetimeField` `auto_now`/`auto_now_add` (popoto-side test; not in the `ai` suite). This is the authoritative home for auto-now tests — pattern it after the existing `SortedField` round-trip cases, using `datetime.now(timezone.utc)` naive bounds instead of `time.time()`. This is the load-bearing regression guard for the fix.
- [ ] `~/src/popoto/tests/test_field_types.py` — NO CHANGE: confirmed (critique) to have zero `auto_now` assertions; its single `DatetimeField` reference is non-auto_now and needs no update. The "audit existing naive-local tests" obligation resolves to a no-op.

No existing **ai-repo** tests are affected — the `ai` change is a pin bump only, and #1645's tests (PR #1655) already assert `AgentSession.updated_at` is explicit-UTC via `utc_now()`, independent of popoto's `auto_now` behavior. The `ai` suite continues to pass unchanged against the new popoto version because no `ai` model uses `auto_now` anymore (grep-confirmed in #1645 recon).

## Rabbit Holes

- **Migrating popoto to fully tz-aware datetimes end-to-end** (encoder/decoder carrying tzinfo through serialization). Tempting, but the encoder deliberately stores wall-clock strings; making it tz-aware is a much larger, riskier change with backward-compat concerns for existing stored data. Out of scope — minting UTC at the producer is sufficient and correct given the current encoder.
- **Re-healing already-stored `ai` data.** #1645 already healed `AgentSession` future-stamped records. No popoto model other than the (now-fixed) `AgentSession.updated_at` ever used `auto_now`, so there is no stale data to heal here.
- **Migrating the mixed-epoch transition window.** Re-stamping pre-fix naive-local rows to UTC is tempting but out of scope: the only live `auto_now` consumer is already healed (#1645) and its records are short-lived. Accept the transient window (Architectural Impact) rather than ship a one-time migration.
- **Folding unrelated popoto `[Unreleased]` CHANGELOG features into this release decision.** Resolved by the release-branch strategy (cut `release/1.7.1` from tag `v1.7.0`, cherry-pick only the fix), so the `[Unreleased]` MemoryLifecycle/ContextAssembler work never enters the 1.7.1 artifact. Don't fold it in; don't gate this bugfix on it.

## Risks

### Risk 1: ~~popoto `requires-python` is older than 3.11 (no `datetime.UTC`)~~ — RESOLVED
**Closed by critique.** popoto's `requires-python = ">=3.10"` is confirmed and `datetime.UTC` is 3.11+, so the plan mandates `timezone.utc` unconditionally (Technical Approach). There is no remaining version-gate decision — `datetime.UTC` is forbidden by the plan, not merely discouraged.

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

- [ ] `~/src/popoto/src/popoto/fields/datetime_field.py` `format_value_pre_save` returns `datetime.now(timezone.utc)` for both branches; `timezone` imported. (`datetime.UTC` is forbidden — see Technical Approach.)
- [ ] popoto test in `test_auto_timestamps.py` asserts `auto_now`/`auto_now_add` stamps UTC wall-clock via naive-to-naive comparison (passes in the popoto suite).
- [ ] popoto version bumped to `1.7.1` in `pyproject.toml` and CHANGELOG entry added under a released section.
- [ ] New popoto version published to PyPI from `release/1.7.1` (verified via `pip index versions popoto`). **[HUMAN-GATED]**
- [ ] `ai/pyproject.toml` popoto specifier bumped to `>=1.7.1`; pin comment references #1653.
- [ ] `ai/uv.lock` refreshed via `uv lock` to the new version + hashes.
- [ ] `ai` test suite passes against the new popoto version (`pytest tests/unit/`).
- [ ] Propagation path confirmed: new pin rides the existing `/update` `uv sync` (machines adopt it on their next routine update; tracked under No-Gos [ORDERED]).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When executed, the lead coordinates; the popoto fix and the ai-pin bump are two
sequential phases separated by the human PyPI gate.

### Team Members

Collapsed per critique (Simplifier, x3): two builders separated only by the unavoidable human PyPI gate. Validation is each builder's own done-check plus the standard SDLC test/review stages — no dedicated validator agents, no standalone validate-all, no separate documentarian.

- **Builder (popoto-fix)**
  - Name: `popoto-builder`
  - Role: Patch `DatetimeField.format_value_pre_save` to `timezone.utc`, add the `test_auto_timestamps.py` test, bump version to `1.7.1` + CHANGELOG, update docstring — all in `~/src/popoto`. Self-verifies by running popoto's suite green before handing off.
  - Agent Type: builder
  - Resume: true

- **Builder (ai-pin)**
  - Name: `ai-pin-builder`
  - Role: After the new popoto version is live on PyPI, bump `ai/pyproject.toml` specifier to `>=1.7.1`, run `uv lock`, run `ai` unit tests green, update the pin comment to reference #1653, and flip the stale `models/agent_session.py` "do not re-add auto_now" tombstone comment to point at the fixed version (comment only — do NOT remove the manual `save()` UTC override). Self-verifies the lock + suite before handing off.
  - Agent Type: builder
  - Resume: true

### Available Agent Types

(see template — Tier 1 `builder` / `validator` suffice for this Small work.)

## Step by Step Tasks

### 1. Patch popoto DatetimeField + test + version
- **Task ID**: build-popoto-fix
- **Depends On**: none
- **Validates**: `~/src/popoto/tests/test_auto_timestamps.py` (popoto suite, run in the popoto repo)
- **Assigned To**: popoto-builder
- **Agent Type**: builder
- **Parallel**: false
- In `~/src/popoto/src/popoto/fields/datetime_field.py`: add `timezone` to the `from datetime import ...` line (line 25), change both `datetime.now()` returns at lines 116/118 to `datetime.now(timezone.utc)`, update the docstring to say UTC. **Do not use `datetime.UTC`.**
- Add a test in `test_auto_timestamps.py` (pattern after the existing `SortedField` round-trip cases) asserting `auto_now`/`auto_now_add` stamps UTC wall-clock via **naive-to-naive** comparison (`before = datetime.now(timezone.utc).replace(tzinfo=None)`; `before <= decoded <= after`), and that `skip_auto_now=True` preserves the existing value. Run under `TZ=America/New_York` so a naive regression diverges.
- Bump `~/src/popoto/pyproject.toml` version to `1.7.1` and add a CHANGELOG entry under a released section.
- Self-check: run popoto's own test suite; confirm green. Open the popoto PR.

### 2. [HUMAN GATE] Publish popoto to PyPI
- **Task ID**: publish-popoto (human-gated; see No-Gos [EXTERNAL])
- **Depends On**: build-popoto-fix
- Maintainer (**Valor**) reviews/merges the popoto PR to main, then cuts `release/1.7.1` from tag `v1.7.0`, cherry-picks the fix commit, and pushes the `v1.7.1` tag from that branch (excluding the `[Unreleased]` block). Approves the `release` environment if configured. `release.yml` builds + publishes via OIDC.
- **Resume Signal**: the pipeline pauses here. Resume Step 3 only after `pip index versions popoto` shows `1.7.1` live on PyPI. (Before tagging, confirm reviewers: `gh api repos/tomcounsell/popoto/environments/release --jq '.protection_rules'`.)

### 3. Bump ai pin + relock + flip stale comment
- **Task ID**: build-ai-pin
- **Depends On**: publish-popoto
- **Validates**: `pytest tests/unit/` (ai suite)
- **Assigned To**: ai-pin-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `ai/pyproject.toml:17` specifier to `>=1.7.1`; update the pin comment to reference #1653.
- Flip the stale tombstone comment in `models/agent_session.py` ("do not re-add auto_now") to point at the fixed popoto version. Comment only — do NOT remove the manual `save()` UTC override.
- Run `uv lock`; confirm `ai/uv.lock` popoto entry shows `1.7.1` + refreshed hashes.
- Self-check: run `pytest tests/unit/`; confirm green against the new popoto. Open the ai PR. (Final validation against Success Criteria is the standard SDLC review/test stage — no separate validate-all task.)

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| popoto fix present | `grep -c 'datetime.now(timezone.utc)' ~/src/popoto/src/popoto/fields/datetime_field.py` | output >= 2 |
| popoto does NOT use 3.11-only UTC alias | `grep -c 'datetime.now(UTC)' ~/src/popoto/src/popoto/fields/datetime_field.py` | output 0 |
| popoto no naive auto-now | `grep -n 'return datetime.now()' ~/src/popoto/src/popoto/fields/datetime_field.py` | exit code 1 |
| popoto version live on PyPI | `pip index versions popoto 2>/dev/null \| grep -F "$(grep '^version' ~/src/popoto/pyproject.toml \| head -1 \| sed 's/.*"\(.*\)".*/\1/')"` | output contains version (human-gated) |
| ai lock updated | `grep -A1 'name = "popoto"' uv.lock \| grep version` | output contains new version |
| ai tests pass | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

War room run 2026-06-15 (6 personas x 3 rounds). Verdict: **REVISE -> resolved**. All BLOCKERs absorbed into the plan below.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic / Adversary / User / Operator / Consistency / Simplifier (unanimous) | `datetime.UTC` is 3.11+, but popoto `requires-python = ">=3.10"`; release CI builds only on 3.12, so it ships green yet `ImportError`s on any 3.10 install. | Technical Approach now mandates `timezone.utc` unconditionally; Risk 1 closed; Open Q3 resolved. | `from datetime import datetime, timezone` + `datetime.now(timezone.utc)`. Valid 3.2+. Verification grep narrowed to the `timezone.utc` token only. |
| BLOCKER | Adversary / Skeptic / User | Proposed test compares decoded (naive, encoder strips tzinfo) vs `datetime.now(UTC)` (aware) -> `TypeError`. | Test Impact + Task 1 rewritten for naive-to-naive comparison with tolerance. | Capture `before = datetime.now(timezone.utc).replace(tzinfo=None)`; assert `before <= decoded <= after`. Never compare aware-to-naive. |
| BLOCKER | Archaeologist / Skeptic (x2) | New auto-now test targeted `test_field_types.py`, which has zero auto_now coverage; the real harness is `test_auto_timestamps.py` (SortedField round-trip pattern). | Test Impact + Task 1 redirect the test to `test_auto_timestamps.py`. | Pattern after the SortedField cases; use `datetime.now(timezone.utc)` bounds instead of `time.time()`. Run under `TZ=America/New_York` so a naive regression visibly diverges. |
| BLOCKER | Operator (x2) | Tagging from popoto `main` ships the `[Unreleased]` block (MemoryLifecycle recipe + ContextAssembler retrieval_mode) bundled into a "2-line patch", widening blast radius and breaking clean revert. | Solution Flow + Open Q1 resolved: cut `release/1.7.1` from tag `v1.7.0`, cherry-pick only the fix. | `git checkout -b release/1.7.1 v1.7.0 && git cherry-pick <fix>` then tag from that branch; `[Unreleased]` stays on main. |
| CONCERN | Operator (x2) | ai pin `>=1.6.1` lets any machine silently resolve still-buggy 1.7.0 after publish; revert isn't clean. | Pin bumped to `>=1.7.1` (the fix floor), not a loose carryover. | Optional pre-tighten to `==1.6.1` closes the skew window before publish; treated as a nicety, not required for Small appetite. |
| CONCERN | Archaeologist | `models/agent_session.py` tombstone comment ("auto_now removed deliberately, do not re-add") becomes stale-but-sticky once popoto is correct. | Documentation task: flip the comment to point at the fixed version. | Do NOT auto-rip the manual `save()` UTC override in this Small plan -- gate removal behind verifying `auto_now` fires on every ai save path. Comment flip only. |
| CONCERN | Adversary (x2) | Non-UTC hosts get a mixed-epoch field: pre-fix naive-local strings coexist with post-fix naive-UTC strings, no offset marker; cross-boundary ordering silently wrong. | Architectural Impact + Rabbit Holes scope note: fix is write-only / non-migrating. | AgentSession lifetimes are short (already healed in #1645); no other popoto model uses auto_now. Accept the transient window explicitly rather than migrate. |
| CONCERN | User | PyPI publish is `[HUMAN-GATED]`; pipeline has no defined resume trigger, so the SDLC stalls ambiguously at Step 2. | Step 2 gains an explicit Resume Signal. | Resume when `pip index versions popoto` shows the new version live. |
| CONCERN | Simplifier (x3) | 4 named agents + 7 tasks (two builder/validator pairs + standalone validate-all + documentarian) is ceremony disproportionate to a 2-line fix. | Orchestration collapsed to 2 builders + the human gate; validation folded into each builder's done-check and the standard SDLC review/test stages. | popoto-builder (patch+test+version+CHANGELOG+docstring), human gate, ai-pin-builder (bump+lock+comment). |
| NIT | Operator / User | No fleet-version-skew check and no operator-visible (dashboard age) smoke test after the bump. | Noted as optional follow-up; not added to this plan's scope. | `importlib.metadata.version("popoto")` in `tools/doctor.py` is the natural home if pursued later. |

---

## Open Questions

_All three resolved during critique (2026-06-15); retained for traceability._

1. ~~**Version number / release coordination.**~~ **RESOLVED:** standalone `1.7.1` cut from tag `v1.7.0`, cherry-picking only the fix, so the `[Unreleased]` block is excluded. (Operator critic, BLOCKER.)
2. **Who pushes the tag.** Owner: **Valor** (holds publish authority on the popoto PyPI project / `release` environment). Before the gate, confirm the environment's required reviewers: `gh api repos/tomcounsell/popoto/environments/release --jq '.protection_rules'`. This is the one remaining genuinely-external dependency.
3. ~~**`requires-python` floor.**~~ **RESOLVED:** popoto's floor is `>=3.10`; the plan mandates `timezone.utc` unconditionally. `datetime.UTC` is forbidden. (Unanimous, BLOCKER.)
