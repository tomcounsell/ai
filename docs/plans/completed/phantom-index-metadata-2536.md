---
status: In Progress
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-06
tracking: https://github.com/tomcounsell/ai/issues/2536
last_comment_id: 5200457495
revision_applied: true
revision_applied_at: 2026-08-06T05:36:37Z
---

# Popoto version-floor guard: stop `rebuild_indexes()` from destroying the AgentSession index under a stale interpreter

## Problem

`AgentSession.rebuild_indexes()` fails with `ExtraData: unpack(b) received extra data.` Issue #2536 filed this as suspected phantom index-bookkeeping corruption (the #2207 class). It is not. The investigation (2026-08-06, read-only, evidence in the issue's Recon Summary) found the keyspace is completely healthy and the failure is an **interpreter/environment split**.

Two Python environments exist on this machine:

| Interpreter | popoto | AgentSession eager-decode result |
|---|---|---|
| `.venv/bin/python` 3.14.3 — what every launchd plist hardcodes | 1.8.0 | 0 / 4005 records fail |
| ambient `python3` 3.12.13 (homebrew + user site-packages) | 1.7.1 | 4005 / 4005 records fail |

`pyproject.toml:18` pins `popoto>=1.8.0`. The ambient interpreter carries a stale 1.7.1 that violates the floor, and `uv sync` only ever writes `.venv`, so nothing corrects it.

Popoto 1.8.0's `INDEX_SWAP_LUA` stores a server-authoritative pointer field `{field}\x00idxset` inside each model hash for every `IndexedField` (on `AgentSession`: `status`, `task_type`, `claude_session_uuid`). Those pointers are raw Redis Set-key strings, deliberately not msgpack-encoded. popoto 1.8.x's `decode_popoto_model_hashmap` skips them (`popoto/models/encoding.py:324, 338, 422, 427` — `if b"\x00" not in key_b  # skip internal pointer fields`). **popoto 1.7.1 has no such skip** (`popoto/models/encoding.py:327-331`) and runs `msgpack.unpackb()` over every field, raising `ExtraData`.

**Current behavior:**

Under the ambient 1.7.1 interpreter the system looks perfectly healthy right up to the moment it destroys itself:

- `AgentSession.query.all()` returns 4006 records with **no exception** — verified. The query path uses popoto's lazy decoder (`_create_lazy_model`), which defers `unpackb` to attribute access and therefore never touches the `\x00idxset` pointer fields.
- Per-field access (`status`, `exec_pid`, `updated_at`) succeeds for the same reason — only the accessed field is unpacked.
- So there is **no warning signal at all**. Every observability surface reports green.
- Then `rebuild_indexes()` runs. `popoto/models/base.py:2742-2777` **deletes `$Class:AgentSession` and every secondary index key in Step 1**, before the Step 2 scan at `:2785-2805` that eagerly decodes and raises on record #1. The result is a destroyed AgentSession index with nothing rebuilt: hashes intact, `query.all()` returns 0, every surface reports "zero sessions."

That is verbatim the 2026-07-14 incident recorded in the module docstring of `agent/index_drift.py:3-9` ("an eng session crashed with a msgpack decode failure. Afterward `AgentSession.query.all()` returned `0` with **no exception**, while 11 `AgentSession` hashes still existed"). **The founding incident of the index-drift guard is this bug.** That guard detects the aftermath. Nothing prevents the cause.

**Status of the exposure (corrected 2026-08-06 after round-2 critique).** On *this* machine the bug is now **latent, not reproducible**: the operator removed the ambient popoto mid-investigation, so `python3 -c "import popoto"` raises `ModuleNotFoundError` rather than importing 1.7.1. That does not weaken the case for the fix, and the case should not be overstated to strengthen it. What remains true: the vector is real and was exercised (`scripts/migrate_strip_pid_fields.py` under ambient `python3` is exactly how this surfaced during the #2516 cutover); every repo script with a `#!/usr/bin/env python3` shebang still resolves to whatever ambient interpreter a machine happens to have; nothing prevents the stale install from returning here or existing on any other fleet machine; and nothing in the repo would detect it. launchd services were never at risk — their plists hardcode `/Users/valorengels/src/ai/.venv/bin/python`. The guard exists so the *next* below-floor interpreter refuses instead of detonating.

**Desired outcome:**

Any process about to run popoto's destructive index rebuild against production Redis under a below-floor popoto **fails closed with an actionable message, before deleting anything**. `python -m tools.doctor` reports the violation so a machine in this state is visible without waiting for someone to trip the mine.

## Freshness Check

**Baseline commit:** `c1d137f3b0d579c7e0ae68e23bb42567e5fb74a9`
**Issue filed at:** 2026-08-05T02:36:01Z
**Disposition:** Major drift (on root cause) — the issue's premise is disproven and the plan is scoped to the real defect. Re-scoped in-place per the investigation mandate rather than closing; the underlying `rebuild_indexes()` failure is real, only its cause was misattributed.

**File:line references re-verified:**
- `popoto/models/base.py:2805` — `decode_popoto_model_hashmap` call inside `rebuild_indexes()` Step 2 — **still holds** (verified in installed 1.7.1 and downloaded 1.8.1 dists).
- `popoto/models/base.py:2742-2777` — index teardown precedes the Step 2 scan — **still holds**. This is the line range that makes the failure destructive rather than benign.
- `popoto/models/encoding.py:327-331` (1.7.1) — unconditional `msgpack.unpackb` over every hash field — **still holds**.
- `popoto/models/encoding.py:324, 338, 422, 427` (1.8.1) — `\x00` pointer-field skip — **confirmed present** in a wheel downloaded to scratch, not installed.
- `pyproject.toml:18` — `popoto>=1.8.0` — **still holds**.
- `agent/index_drift.py:3-9` — 2026-07-14 incident narrative — **still holds**, and is now explained by this root cause.
- `models/session_lifecycle.py:475` — the repo's only in-tree reference to `\x00idxset` — **still holds**.
- `tools/doctor.py:95-105` — `_check_venv` — **still holds**, and is the gap: it asserts `.venv/bin/python` *exists*, never that the running interpreter *is* it.

**Cited sibling issues/PRs re-checked:**
- **#2086** — "Eng session crashed with 'unpack(b) received extra data' and all AgentSession records became invisible to queries" — CLOSED 2026-07-15. **This is the same bug.** It was root-caused as a "mixed-version deploy artifact" and closed on the belief that deploy hygiene had closed the window. It had not; the window merely moved to the ambient interpreter, where nothing enforces the floor. Recorded in `docs/features/popoto-descriptor-pollution-ledger.md:16`.
- **#2088** — worker-loop crash on a fully-corrupted AgentSession — CLOSED 2026-07-15. Sibling of #2086, different failure surface, not re-opened by this work.
- **#2083** — popoto 1.8.0 descriptor-pollution scar-tissue audit — CLOSED 2026-07-17. Produced `docs/features/popoto-descriptor-pollution-ledger.md`, which documents the `\x00idxset` pointer semantics this plan depends on. Its KEEP verdicts are unaffected.
- **#2207** — 6.2M phantom AgentSession index-bookkeeping hashes — CLOSED. **Unrelated to this issue** despite #2536's framing; no phantom of this shape exists.
- **#1038** — binary-field decode hazard — honored throughout: the investigation logged key names, field names, and exception types only, never a decoded value.

**Commits on main since issue was filed (touching referenced files):**
- `877720530` "Durability M1 fence: close nine unfenced consumers + permanent regression tests (#2518) (#2538)" — touched `models/agent_session.py`. **Irrelevant** to this root cause (execution-fence fields, not index bookkeeping).
- `f59d3df5f` "Bump deps: claude-agent-sdk 0.2.130->0.2.131" — touched `pyproject.toml`. **Irrelevant**; did not move the popoto pin.

**Active plans in `docs/plans/` overlapping this area:** **one — `docs/plans/generalize-migration-guards-2524.md` (issue #2524).** Its *plan document* landed on main after this plan's; its *implementation* is still in flight (critique returned NEEDS REVISION, mid-revision). Nothing of #2524's code has merged. It swaps `rebuild_indexes()` → `clean_indexes()` in the strip-family migrations and cites this issue as its rationale (`:49`, `:280`). Two call sites appear in both plans: `scripts/migrate_strip_pty_fields.py:161` and `scripts/migrate_schema_diet_fields.py:230`. **Serialization ruling: #2524 lands first and owns those two files.** Its plan is now status Ready (critique resolved at `413efa7cf`) with code under review in PR #2545; nothing of it has merged yet, so this plan cites the plan document and the open PR, not a merged SHA. Serialization ruling unchanged: #2524 lands first. This lane must not edit those two files; the seam-level guard in this plan covers whatever call sites remain at build time, so no coordination is required beyond leaving those files alone. `docs/plans/durability-m1-fence-canary.md` touches `models/agent_session.py` but not any index-bookkeeping path — no overlap.

**Notes:** Two premise corrections that matter for the build.

1. #2536's claim that the error is "non-fatal everywhere it currently fires" is **false for `rebuild_indexes()` itself**, because teardown precedes the scan. The guard must run *before* popoto's Step 1, never as a `try/except` around the rebuild — by the time popoto raises, the indexes are already gone.
2. **Correction to this plan's own first draft (commit `95295c4cf`).** That draft asserted `AgentSession.repair_indexes()` was the only in-repo caller of `rebuild_indexes()`. That was wrong: the grep behind it used an unquoted `--include=*.py`, which zsh rejects outright, so it returned no matches and the absence was misread as evidence. Re-run correctly there are **ten** real call sites (full inventory in Risk 3). Guarding only `repair_indexes()` would leave the actual detonation path — ad-hoc `python scripts/migrate_*.py` under the ambient interpreter, which is exactly how #2516 surfaced this — completely open. The Solution below is re-scoped to a single seam-level interlock as a result.

## Prior Art

- **#2086**: "Eng session crashed with 'unpack(b) received extra data'..." — Closed 2026-07-15 with root cause correctly identified as a popoto 1.8.0-writer / 1.7.1-reader mixed-version artifact. **No code shipped.** It was closed on the assertion that deploy hygiene closed the window. This plan is the enforcement that closure assumed but never built.
- **#2083 / PR for `docs/features/popoto-descriptor-pollution-ledger.md`**: Audited popoto 1.8.0's `INDEX_SWAP_LUA` and documented the `{field}\x00idxset` pointer contract (ledger `:59`, `:114`, `:277`). Succeeded. This plan consumes that documentation and must not contradict its KEEP verdicts.
- **#2101 / #2207 (`repair_indexes` A1 guard)**: Added the identity-less-record shim inside `AgentSession.repair_indexes()` so popoto's rebuild loop cannot re-inflate phantom index members. Succeeded, and is the precedent for this plan's approach: `repair_indexes()` is already the sanctioned place to wrap popoto's rebuild with repo-side safety. The new guard sits alongside it.
- **#1720**: Documented that `rebuild_indexes()` transiently empties `$Class:AgentSession`, and added bounded read-path retries in `tools/valor_session.py` and `tools/sdlc_stage_query.py`. Succeeded for the *transient* window. It does not help when the rebuild never completes — the permanent-empty case this plan prevents.
- **#2524 (`docs/plans/generalize-migration-guards-2524.md`, in flight)**: Generalizes the strip-family migration guards and swaps `rebuild_indexes()` → `clean_indexes()` in `scripts/migrate_strip_pty_fields.py:161` and `scripts/migrate_schema_diet_fields.py:230`, citing this issue as its rationale. **Serialized ahead of this plan and owns those two files.** Its plan is now status Ready (critique resolved at `413efa7cf`) with code under review in PR #2545; nothing has merged. Serialization ruling unchanged: #2524 lands first. Complementary, not competing: it removes two call sites, this plan guards the seam every remaining site traverses.
- **#1459**: Redis orphan index cleanup / Sentry noise. Related to `clean_indexes()`, not the rebuild path. No overlap.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #2086 (closed, no code) | Root-caused the `ExtraData` crash as a popoto mixed-version artifact and closed the issue. | Concluded "the mixed-version window is closed by deploy hygiene." Deploy hygiene governs `.venv` only. The ambient interpreter was never in scope for `uv sync`, so the window stayed open on exactly the path (`#!/usr/bin/env python3` scripts, ad-hoc agent shell commands) that produced both the original incident and #2536. A root cause was identified but nothing was built to enforce it. |
| #1720 (shipped) | Bounded retries on the read path to survive the class-set-empty window during a rebuild. | Correct fix for a *transient* window. Assumes the rebuild finishes. Under a below-floor popoto the rebuild never finishes, so the window is permanent and the retries simply exhaust. |
| `agent/index_drift.py` (#2101 lineage, shipped) | Loud ERROR + Sentry when raw hash count exceeds queryable count. | Detect-only by explicit design (module docstring `:27-33`). It fires *after* the index is destroyed. Valuable, but it is an alarm, not an interlock. |

**Root cause pattern:** every prior response treated the *aftermath* of a below-floor decode (retry it, detect it, document it). None of them checked the precondition. The missing piece is a fail-closed interlock on the one code path where a below-floor popoto is silently destructive.

## Research

Purely internal — the relevant "external" artifact is the popoto package itself, which was inspected directly rather than searched. No WebSearch performed.

**Verified by direct inspection instead:**
- popoto 1.8.1 wheel downloaded to scratch (`pip download popoto==1.8.1 --no-deps`, never installed) and unpacked to confirm the `\x00` pointer-field skip at `popoto/models/encoding.py:324, 338, 422, 427`. This confirms the skip is present in the whole 1.8.x line, not a 1.8.0-only accident — so a `>=1.8.0` floor is the correct predicate.
- `pip index versions popoto` → available 1.8.1, 1.8.0, 1.7.1, ...; ambient INSTALLED 1.7.1, LATEST 1.8.1.
- `tomllib` (stdlib, 3.11+) and `packaging` 26.0 are both available in `.venv` — the two dependencies the guard needs, neither of them new.

## Spike Results

### spike-1: Does wrapping `popoto.models.base.Model.rebuild_indexes` intercept every subclass call?
- **Assumption**: "A single wrapper on popoto's `Model.rebuild_indexes` classmethod intercepts all ten repo call sites, including the aliased-import and generic `model_class` forms, with `cls` correctly bound to the concrete subclass."
- **Method**: prototype (in-process, `.venv/bin/python`, no Redis writes — the stub raised before reaching popoto)
- **Finding**: **Confirmed.** Replacing `Model.rebuild_indexes` with a `classmethod` wrapper intercepted `AgentSession.rebuild_indexes()`, `TelegramMessage.rebuild_indexes()`, and the generic `model_class.rebuild_indexes()` form. `cls` bound to the concrete subclass in all three cases (`calls: ['AgentSession', 'TelegramMessage', 'AgentSession']`). This is what makes the seam approach viable and is the reason the plan does not edit ten call sites.
- **Confidence**: high
- **Impact on plan**: Determined the entire Solution. Without subclass interception the plan would have needed ten per-caller edits plus a permanent lint rule.

### spike-2: Does the install point actually execute for every caller?
- **Assumption**: "Installing from `models/__init__.py` runs before any of the ten call sites can reach a model class."
- **Method**: code-read of every caller's import statement
- **Finding**: **Confirmed.** Nine callers use `from models.X import Y` (`migrate_strip_pty_fields.py:99`, `migrate_schema_diet_fields.py:168`, `migrate_parent_session_field.py:159`, `migrate_session_type_pm_to_eng.py:319` aliased, `merge_dev_chat_into_eng.py:54`, and the in-package `models/agent_session.py`); one uses `import models as models_pkg` (`popoto_index_cleanup.py:76`). Both forms execute the `models` package `__init__` first, so no caller can reach a model class before the interlock is installed.
- **Confidence**: high
- **Impact on plan**: Fixed the install location. A `models/base.py` shared-base-class alternative was rejected here — all 19 popoto models subclass `Model` directly, so that route needs 19 edits and still misses nothing the seam doesn't already cover.

### spike-3: Are `tomllib` and `packaging` available without adding a dependency?
- **Assumption**: "The floor resolver needs no new dependency."
- **Method**: prototype
- **Finding**: **Confirmed.** `tomllib` is stdlib (3.11+) and `packaging` 26.0 is already installed. Parsing `pyproject.toml` yields `['popoto>=1.8.0']` from `[project].dependencies`.
- **Confidence**: high
- **Impact on plan**: No dependency addition, so no `uv.lock` churn and no `/update` propagation concern.

## Data Flow

Tracing the destructive path this plan interrupts:

1. **Entry point**: an operator, agent, or `#!/usr/bin/env python3` repo script invokes something that calls `AgentSession.repair_indexes()` — e.g. `scripts/migrate_strip_pid_fields.py`, the worker startup guard, or the hourly `agent-session-cleanup` reflection.
2. **`models/agent_session.py::repair_indexes`**: scans and clears `$IndexF:AgentSession:*` keys, installs the per-`IndexedField` A1 shims, then delegates to popoto.
3. **`popoto/models/base.py:2742-2777` (rebuild Step 1)**: **deletes** `$Class:AgentSession`, every sorted-field index, every key-field index set, every geo index, every composite index. *This is the point of no return.*
4. **`popoto/models/base.py:2785-2805` (rebuild Step 2)**: `scan_iter` over `AgentSession:*` → `hgetall` → `decode_popoto_model_hashmap`. Under popoto < 1.8.0 this raises `ExtraData` on the first record.
5. **Output**: exception propagates; the `finally` restores the shims but **cannot restore the deleted indexes**. Redis is left with 4005 intact hashes and no index. `query.all()` → 0. The dashboard, `valor-session list`, and the worker all report zero sessions.

The guard inserts at step 2, before step 3 — the only position where failing is free.

## Architectural Impact

- **New dependencies**: none new to the project. `tomllib` is stdlib; `packaging` is already installed (26.0) as a transitive dependency.
- **Interface changes**: one new module (`config/popoto_floor.py`) exposing `popoto_floor_satisfied()` and `assert_popoto_floor()`; one new private doctor check. `repair_indexes()` keeps its `(stale_count, rebuilt_count)` return arity — it gains a raise path, not a signature change.
- **Coupling**: adds a small, deliberate coupling from `models/__init__.py` (the seam install) and `models/agent_session.py` (the entry guard) to the declared dependency floor. This is the point: the destructive path should know its own precondition. Coupling to popoto internals IS increased, deliberately: the seam wraps `popoto.models.base.Model.rebuild_indexes` and the oracle reads `popoto.__version__`. Both are public surface, but this is a monkeypatch on a third-party class and carries a re-verify-on-upgrade obligation.
- **Data ownership**: unchanged. The guard performs no Redis operations of any kind.
- **Reversibility**: trivial. Deleting the `assert_popoto_floor()` call and the doctor check restores current behavior exactly.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (root cause is settled and evidenced; no scope ambiguity remains)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Guard tests seed and delete `test-`-scoped AgentSessions |
| `packaging` importable | `.venv/bin/python -c "import packaging.version"` | Version comparison in the floor check |
| `tomllib` importable | `.venv/bin/python -c "import tomllib"` | Parsing the floor out of `pyproject.toml` |

## Solution

### Key Elements

- **`config/popoto_floor.py`** — resolves the declared popoto floor from `pyproject.toml` (single source of truth, no duplicated version literal), reads the **running interpreter's** installed popoto version, and compares them. Exposes a non-raising predicate for reporting and a raising assertion for interlocking.
- **A single seam-level interlock on `popoto.models.base.Model.rebuild_indexes`**, installed once from `models/__init__.py`. Every one of the ten call sites traverses this classmethod, so one install covers them all with zero per-caller edits — including future call sites nobody has written yet.
- **`tools/doctor.py` Environment check** — surfaces a below-floor running interpreter as a FAIL with a concrete fix line, so a machine in this state is visible from `python -m tools.doctor` rather than only at detonation time.

### Flow

**Someone runs `python scripts/migrate_*.py` under ambient `python3`** → the script's `from models.agent_session import AgentSession` executes `models/__init__.py`, installing the interlock → script calls `AgentSession.rebuild_indexes()` → **guard fires before popoto's Step 1** → `RuntimeError` naming the running interpreter, its popoto version, the required floor, and the fix → **Redis untouched, indexes intact** → operator re-runs under `.venv/bin/python` → rebuild proceeds normally.

In parallel: **`python -m tools.doctor`** → Environment section → `popoto_floor` check → FAIL with the same fix line, without anyone having to trip the guard first.

### Technical Approach

- **Guard at the seam, not per caller.** All ten call sites (Risk 3) funnel through popoto's `Model.rebuild_indexes` classmethod. Wrapping that one method covers every site, and — more importantly — covers the next migration script someone writes. Ten separate `assert_popoto_floor()` calls would be ten things to forget.
- **Install from `models/__init__.py`, as its first statement, before the model imports.** Verified: every caller reaches its model class through `from models.X import Y` (`migrate_strip_pty_fields.py:99`, `migrate_schema_diet_fields.py:168`, `migrate_parent_session_field.py:159`, `migrate_session_type_pm_to_eng.py:319`, `merge_dev_chat_into_eng.py:54`) or `import models as models_pkg` (`popoto_index_cleanup.py:76`). Both forms execute the package `__init__`, so the interlock is installed before any model class is reachable. Installing before the model imports also keeps it free of circular-import risk — it patches a library class and needs no repo model.
- **The install must be idempotent.** Re-importing `models` must not double-wrap (which would nest the guard and corrupt the `__wrapped__` chain). Mark the patched function with a sentinel attribute and return early if already installed.
- **Preserve `classmethod` binding.** Spike-1 confirmed `cls` binds to the concrete subclass through the wrapper, which the generic `model_class.rebuild_indexes()` form at `popoto_index_cleanup.py:262` depends on.
- **Resolve the floor from `pyproject.toml`, never hardcode it.** Parse `[project].dependencies` with `tomllib`, find the `popoto` requirement, and read its `>=` lower bound via `packaging.requirements.Requirement`. A hardcoded `"1.8.0"` would silently rot the day the pin moves; the whole bug being fixed is a version predicate drifting out of sync with reality.
- **Locate `pyproject.toml` relative to the module**, mirroring the existing `PROJECT_DIR = Path(__file__).resolve().parent.parent` idiom in `tools/doctor.py:32`. If it is unreadable (installed-package layout, missing file), the resolver returns `None` and the guard **fails open, loudly** (`logger.error` + Sentry capture) rather than blocking legitimate work — an unresolvable floor is an unknown, not a violation.
- **Read the installed version from `popoto.__version__`, NOT `importlib.metadata`.** Round-2 critique caught this and it is empirically confirmed on this machine: after the operator removed the ambient popoto, `python3 -c "import popoto"` raises `ModuleNotFoundError` while `importlib.metadata.version("popoto")` still cheerfully returns `1.7.1` — a stale editable-install `.pth` record pointing at a deleted worktree. Metadata is a static string over mutable source, so it can be wrong in either direction: a false positive blocks index repair fleet-wide (Risk 1 realized), a false negative lets the index be destroyed anyway. `popoto.__version__` is the version of the module actually imported, it is confirmed present (`1.8.0` in `.venv`), and since the interlock wraps popoto's own class popoto is imported by definition at that point. If `__version__` is absent, treat it as unresolvable — do not silently fall back to metadata. Do not shell out to `pip`.
- **Compare with `packaging.version.Version`**, not string comparison (`"1.10.0" < "1.8.0"` is true as strings and false as versions).
- **Raise, do not log-and-continue.** `repair_indexes()` currently returns `(0, 0)` when it loses its re-entrancy lock. A floor violation is categorically different — a silent no-op return would let a caller believe a repair happened. Fail closed and loud.
- **`AgentSession.repair_indexes()` needs its own guard at entry — it is the one exception to "no per-caller edits", and it is not redundant.** Round-2 critique caught this. `repair_indexes()` performs its OWN destructive teardown *before* it ever reaches popoto: it deletes every `$IndexF:AgentSession:*` key at `models/agent_session.py:2425`, which is upstream of the lock (`:2429`), the shims (`:2487`), and `cls.rebuild_indexes()` (`:2488`). The seam interlock fires inside `rebuild_indexes()` and therefore cannot protect that teardown. Exactly one call site is edited — `repair_indexes()` entry, before the `$IndexF` scan — and it is guarded because it deletes indexes the seam never sees. Every other caller delegates its teardown entirely to popoto and needs no edit.
- **Do not add an import-time raise on `models/agent_session.py`.** Verified: under 1.7.1 `query.all()` returns 4006 records without raising (lazy decode never touches the pointer fields), so reads are genuinely functional. An import-time raise would break working read paths to prevent a bug that exists only on the rebuild path.
- **This is a monkeypatch on a third-party class and must be labelled as one.** There is no existing monkeypatch precedent in this repo to lean on — `models/session_lifecycle.py:502-504` is a *comment* convention for popoto coupling points, not a patch. This is a new pattern and should be reviewed as one. Borrow only the re-verify-on-upgrade note from that convention. The patch module carries the same re-verify-on-upgrade note naming `popoto/models/base.py:2707`.
- The doctor check reuses the same `popoto_floor_satisfied()` predicate — one implementation, two consumers, no drifting duplicate.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `config/popoto_floor.py` will contain exactly one broad handler — around `pyproject.toml` read/parse — and it must emit `logger.error` + a Sentry capture (never `pass`, and never a mere WARNING: this branch means the interlock is disabled). A test asserts the ERROR is emitted and the predicate returns the "unresolvable" sentinel when `pyproject.toml` is missing or malformed.
- [ ] The Sentry capture in the unresolvable branch is itself wrapped so a Sentry failure cannot crash the caller; a test asserts the resolver still returns its sentinel when `capture_message` raises.
- [ ] `AgentSession.repair_indexes()`'s existing `finally` shim-restore block is unchanged by this work; the new guard raises *before* any shim is installed, so no handler interaction exists. A test asserts no shim is left installed after a guard-triggered raise.
- [ ] The doctor check must not raise — `run_checks()` wraps each check, but a check that throws degrades the report. A test asserts it returns a `CheckResult` even when the underlying resolver raises.

### Empty/Invalid Input Handling
- [ ] `pyproject.toml` present but with no `popoto` requirement → floor unresolvable → fail open with `logger.error` + Sentry capture. Tested.
- [ ] `popoto` requirement present but with no `>=` lower bound (e.g. a bare `popoto`) → floor unresolvable → fail open with `logger.error` + Sentry capture. Tested.
- [ ] `popoto` importable but exposing no `__version__` → unresolvable, fail open with `logger.error` + Sentry capture. Tested. (popoto genuinely absent is a different, self-announcing failure — the import fails first.)
- [ ] Malformed installed version string (unparseable by `packaging.version.Version`) → unresolvable, fail open with `logger.error` + Sentry capture. Tested.

### Error State Rendering
- [ ] The `RuntimeError` message is the entire user-facing surface of this feature. A test asserts it contains all four load-bearing facts: `sys.executable`, the installed version, the required floor, and the `.venv/bin/python` remedy. A guard that fires with a vague message is barely better than the crash it replaces.
- [ ] The doctor `CheckResult.fix` string is asserted non-empty and to name `.venv/bin/python` on failure.

## Test Impact

- [ ] `tests/unit/test_agentsession_pending_index_leak.py` — **UPDATE**: eight call sites invoke `AgentSession.repair_indexes()` (`:88, :99, :152, :157, :186, :189, :212, :246, :259`). These run under `.venv` (popoto 1.8.0), so the guard passes and they are unaffected in practice. Verify with a focused run; no code change expected. If any test monkeypatches popoto internals in a way that perturbs `popoto.__version__` or the seam install, adjust that test only.
- [ ] `tests/unit/test_session_health_phantom_guard.py` — **UPDATE**: spies on and monkeypatches `AgentSession.repair_indexes` (`:224, :230, :253, :259`). The guard runs inside the real method, which these tests replace with a spy, so they are structurally unaffected. Verify with a focused run; no code change expected.
- [ ] `tests/unit/test_doctor.py` — **UPDATE**: add coverage for the new `popoto_floor` check and confirm the existing check-count/registry assertions (`:564` references `repair_indexes` in a fix string) still hold with one additional Environment check registered.
- [ ] `tests/unit/test_popoto_floor.py` — **CREATE**: includes a test asserting the interlock is actually installed on `popoto.models.base.Model.rebuild_indexes` after `import models`, and that a second import does not double-wrap — a silent no-op install must fail CI, not production.
- [ ] `tests/unit/test_popoto_cleanup_reflection.py` — **UPDATE**: patches `scripts.popoto_index_cleanup._get_all_models` and exercises the generic `model_class.rebuild_indexes()` path (`:93, :96`). Runs under `.venv` so the guard passes; verify with a focused run, no code change expected.

## Rabbit Holes

- **Auditing every `#!/usr/bin/env python3` shebang in the repo and rewriting them to point at `.venv/bin/python`.** Tempting and superficially thorough, but it is a large mechanical diff across dozens of scripts, it breaks the scripts on machines whose venv lives elsewhere, and it still would not cover the actual dominant vector — agents typing `python ...` in a shell. The interlock covers every vector at one site.
- **Building a general-purpose "all pyproject pins satisfied in the running interpreter" validator.** Genuinely interesting and genuinely out of scope. One dependency has a demonstrated destructive failure mode; generalizing to all of them multiplies false positives (extras, markers, optional groups) for no evidenced benefit.
- **Uninstalling or repairing the ambient popoto from inside the code.** Mutating a shared environment programmatically is an operator decision, and doing it from a library import path would be genuinely dangerous.
- **Re-litigating whether popoto's `rebuild_indexes()` should delete-then-rebuild at all.** It should not — teardown before a fallible scan is an upstream design flaw. That is an upstream patch or an issue against popoto, not this plan.
- **Adding retry logic around the rebuild.** #1720 already covers the transient window. Retrying a version mismatch just fails N times.

## Risks

### Risk 1: The guard fires on a machine that is actually fine, blocking legitimate repair
**Impact:** `repair_indexes()` runs on the worker startup path and the hourly `agent-session-cleanup` reflection. A false positive there would block index repair fleet-wide and could itself cause an incident.
**Mitigation:** The guard fails **open** on every uncertainty — unresolvable floor, unreadable `pyproject.toml`, missing package metadata, unparseable version — and raises only on the single unambiguous condition `installed < floor`, with both values successfully parsed. Every fail-open branch is loud (`logger.error` + Sentry) and has a dedicated test. The worker and the reflection both run under `.venv/bin/python` (verified: launchd plists hardcode it), so on the intended path the guard never fires at all.

### Risk 2: The floor resolver breaks when `pyproject.toml` is not adjacent to the module
**Impact:** If the repo is ever installed as a package rather than run from a checkout, `Path(__file__).parent.parent / "pyproject.toml"` misses and the floor is unresolvable.
**Mitigation:** That is the fail-open path by design — the guard degrades to current behavior, but loudly (`logger.error` + Sentry capture, and a doctor FAIL). It is also the existing, accepted idiom in `tools/doctor.py:32`. Explicitly tested via a monkeypatched path.

### Risk 3: Call sites bypass the guard
**Impact:** Any `rebuild_indexes()` path that skips the interlock is an open detonation route — it deletes the index, then dies.

**This risk was mis-assessed in the first draft of this plan and is the reason for the re-scope.** That draft claimed exactly one caller, based on a grep whose unquoted `--include=*.py` zsh rejected outright; it returned nothing and the absence was misread as evidence. The verified inventory on `95295c4cf` is **ten** real call sites:

| Call site | Form | Ownership |
|---|---|---|
| `models/agent_session.py:2488` | `cls.rebuild_indexes()` inside `repair_indexes()` | this plan |
| `scripts/migrate_agent_session_keyfield_rename.py:180` | `AgentSession.rebuild_indexes()` | this plan |
| `scripts/migrate_parent_session_field.py:161` | `AgentSession.rebuild_indexes()` | this plan |
| `scripts/migrate_unify_parent_session_field.py:110` | `AgentSession.rebuild_indexes()` | this plan |
| `scripts/migrate_session_type_pm_to_eng.py:321` | `_AgentSession.rebuild_indexes()` (aliased import) | this plan |
| `scripts/migrate_session_type_chat_to_pm.py:155` | `AgentSession.rebuild_indexes()` | this plan |
| `scripts/merge_dev_chat_into_eng.py:371` | `telegram_message_cls.rebuild_indexes()` (**not** AgentSession) | this plan |
| `scripts/popoto_index_cleanup.py:262` | `model_class.rebuild_indexes()` (generic, live reflection) | this plan |
| `scripts/migrate_strip_pty_fields.py:161` | `AgentSession.rebuild_indexes()` | **#2524** |
| `scripts/migrate_schema_diet_fields.py:230` | `AgentSession.rebuild_indexes()` | **#2524** |

Two of these are not AgentSession at all (`merge_dev_chat_into_eng.py` uses `TelegramMessage`; `popoto_index_cleanup.py` is generic across every model in `models.__all__`), which is decisive: a guard scoped to `AgentSession` could never have covered them.

**Mitigation:** the seam-level interlock on `popoto.models.base.Model.rebuild_indexes` covers all ten by construction, plus any future call site, with zero per-caller edits. Spike-1 empirically confirmed interception for named subclasses, a second model class, and the generic `model_class` form. A `## Verification` anti-criterion (red-state proven, see below) asserts no file calls `rebuild_indexes()` without the interlock being installed.

**Note on `/update`:** the automated path was never at risk — `scripts/update/migrations.py` invokes every migration script with an explicit `.venv/bin/python` (`:63, :110, :137, :168, :202`). The exposure is ad-hoc invocation, which is precisely how #2516 surfaced this.

### Risk 4: Monkeypatching a third-party classmethod breaks on a popoto upgrade
**Impact:** If a future popoto renames, relocates, or changes the signature of `Model.rebuild_indexes`, the install could silently no-op (leaving everything unguarded) or raise at import of `models`, which would break the entire application.
**Mitigation:** The installer resolves the original via `Model.__dict__["rebuild_indexes"]`. If the attribute is absent it emits `logger.error` + a Sentry capture and leaves the seam uninstalled — it **must not raise**. Round-2 critique was right that raising here is unacceptable: `models/__init__.py` is on the import path for the bridge, the worker, and every repo script, so a future popoto rename would convert a maintenance-path bug into a total outage. Loud-but-non-fatal is the correct severity; the seam being missing is caught by the doctor check and by CI (below), not by taking the system down. The patch module carries the repo's standard re-verify-on-upgrade note (mirroring `models/session_lifecycle.py:502-504`) naming `popoto/models/base.py:2707`. A test asserts the sentinel is present after importing `models`, and the doctor check asserts it at runtime, so a silently-missing seam fails CI and shows up in `doctor` rather than passing unnoticed.

### Risk 5: The stale ambient popoto persists after this ships
**Impact:** The guard converts silent index destruction into a loud refusal, which is the goal — but the machine stays mis-provisioned and the ambient interpreter remains unsuitable for repo work.
**Mitigation:** Out of scope by rail (environment mutation is an operator decision) and tracked as an `[EXTERNAL]` No-Go with the exact command. The doctor check makes the condition continuously visible so it cannot be forgotten. This is deliberate: the code fix must not depend on the environment being repaired, or it would silently regress the next time any machine drifts.

## Race Conditions

No new race conditions identified. The guard is a pure, synchronous, read-only computation over process-local state (a module attribute read, a file read) executed before any Redis operation. It performs no I/O against Redis and holds no lock.

One pre-existing interaction is worth recording because the guard's placement sits next to it:

### Race 1 (pre-existing, unchanged): `repair_indexes()` re-entrancy
**Location:** `models/agent_session.py:2427-2438`
**Trigger:** Two callers (worker startup and the hourly reflection) invoke `repair_indexes()` concurrently; concurrent shim installs on the same `IndexedField` would clobber each other's captured original `on_save`.
**Data prerequisite:** none.
**State prerequisite:** the per-class `threading.Lock` must be acquired before any shim install.
**Mitigation:** Already handled by the existing non-blocking lock (loser returns `(0, 0)`). The new guard is placed **before** the lock acquisition and mutates nothing, so it cannot affect this ordering. Two concurrent callers under a below-floor popoto both raise identically and neither touches Redis — a strictly safer outcome than today.

## No-Gos (Out of Scope)

- [EXTERNAL] **Ambient-interpreter remediation on this and other machines.** The parent session removed the ambient popoto here mid-investigation (verified: `import popoto` now raises `ModuleNotFoundError`). A residual artifact remains that this lane deliberately does not touch — `importlib.metadata` still reports `1.7.1` from a stale editable-install `.pth` pointing at a deleted worktree, which is precisely why the guard reads `popoto.__version__` instead. Cleaning that record, and auditing other fleet machines, are operator actions on environments this lane cannot reach. The doctor check is the durable mechanism that surfaces the condition wherever `/update` runs.
- [EXTERNAL] **Verifying and remediating the popoto version on other fleet machines.** Requires access to hosts this lane cannot reach. The doctor check is the durable mechanism that surfaces it wherever `/update` runs.
- [SEPARATE-SLUG #2524] **Editing `scripts/migrate_strip_pty_fields.py` or `scripts/migrate_schema_diet_fields.py`.** Both are owned by #2524, which swaps their `rebuild_indexes()` calls for `clean_indexes()` and is serialized ahead of this lane. Touching them here would conflict with a branch already in flight. The seam-level interlock covers them regardless of merge order, so there is nothing to coordinate beyond leaving them alone. Asserted by a `## Verification` anti-criterion.
- [SEPARATE-SLUG #2207] **Phantom AgentSession index-bookkeeping hashes and the `[repair_indexes] quarantined 513 identity-less hash re-add(s)` / `phantoms_filtered=171` signal from today's `/update`.** Genuinely separate from this root cause — those counters come from the A1 shim running correctly under `.venv` (popoto 1.8.0), where zero decode failures occur. Not conflated with the `ExtraData` defect.
- [DESTRUCTIVE] **Any purge, `hdel`, or ORM-scoped delete of `\x00idxset` pointer fields or index metadata.** There is nothing phantom to reclaim, and deleting the pointers would break `INDEX_SWAP_LUA`'s atomic old-set `SREM` (`docs/features/popoto-descriptor-pollution-ledger.md:59`), regressing #2083's KEEP verdicts. Review-before-execute is the only safe posture and the correct answer is "never." Asserted absent by a `## Verification` anti-criterion.
- [DESTRUCTIVE] **Calling `AgentSession.rebuild_indexes()` or `repair_indexes()` against production Redis to demonstrate the fix.** The demonstration is the thing that destroys the index. All guard tests monkeypatch the reported version so the raise happens before any Redis call. Asserted absent by a `## Verification` anti-criterion.

## Update System

No `/update` script changes required. `scripts/update/deps.py::sync_with_uv` already installs into `.venv` from `uv.lock`, which correctly holds popoto 1.8.0 — the venv was never the problem.

Two notes for the build:
- The new doctor check runs automatically wherever `python -m tools.doctor` already runs; no registration outside `tools/doctor.py::get_checks` is needed.
- Do **not** add an ambient-interpreter repair step to `/update`. `/update` must not `pip install` into a user's system Python — that is the environment mutation the `[EXTERNAL]` No-Go reserves for an operator.

## Agent Integration

No agent integration required. This is an internal safety interlock on a maintenance code path plus one additional check in an existing CLI (`python -m tools.doctor`), which the agent can already invoke via Bash. No new `[project.scripts]` entry point, no MCP surface, and no bridge import.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/popoto-version-floor-guard.md` — the interlock's contract, why it wraps popoto's `Model.rebuild_indexes` seam rather than individual callers, why it must precede popoto's teardown-before-scan, the deliberate runtime-fails-open / observability-fails-loud split, and the operator remedy for a below-floor machine.
- [ ] Add a row to the `docs/features/README.md` index table.

### Existing Docs to Correct
- [ ] `docs/features/popoto-descriptor-pollution-ledger.md:16` — amend the claim that #2086 was "closed by deploy hygiene." Record that the window stayed open on the ambient interpreter until this guard shipped, and link this feature doc. This is the single most load-bearing doc correction in the plan: the stale claim is what let the bug sit for three weeks.
- [ ] `agent/index_drift.py:3-33` module docstring — note that the 2026-07-14 incident's root cause is now identified (below-floor popoto during an eager rebuild) and cross-reference the guard, so the next reader knows the module is the alarm and the guard is the interlock.

### Inline Documentation
- [ ] Comment at the `assert_popoto_floor()` call site in `repair_indexes()` stating the constraint the code cannot show: this must precede popoto's Step 1 teardown (`popoto/models/base.py:2742-2777`) because a check after that point runs on an already-destroyed index.
- [ ] Docstrings on the public `config/popoto_floor.py` functions covering the fail-open contract.

## Success Criteria

- [x] `AgentSession.repair_indexes()` raises `RuntimeError` before any Redis operation when the running interpreter's popoto is below the `pyproject.toml` floor, and the message names `sys.executable`, the installed version, the required floor, and the `.venv/bin/python` remedy.
- [x] Both guard points are live: the seam sentinel `__popoto_floor_guarded__` is present on `popoto.models.base.Model.rebuild_indexes` after `import models`, AND `AgentSession.repair_indexes()` raises at entry before its own `$IndexF` teardown at `models/agent_session.py:2425`.
- [x] A regression test proves **no index key is deleted** on the guard path — `$Class:AgentSession` cardinality and the `$IndexF:AgentSession:*` key set are byte-identical before and after a guard-triggered `repair_indexes()` raise. (Achievable only because of the entry guard; the seam alone fires after the `$IndexF` delete.)
- [x] The floor is read from `pyproject.toml` at runtime; `grep -rn '"1\.8\.0"' config/ models/` finds no hardcoded version literal.
- [x] The installed-version oracle is `popoto.__version__` (the module actually imported), never `importlib.metadata`. `grep -n "importlib.metadata" config/popoto_floor.py` finds no version lookup.
- [x] Every uncertainty branch (missing/malformed `pyproject.toml`, absent `popoto` requirement, no `>=` bound, absent `popoto.__version__`, unparseable version) fails **open at runtime** but emits `logger.error` + a Sentry capture, and renders as **FAIL** in `python -m tools.doctor`. Each branch covered by a test.
- [x] `python -m tools.doctor` reports a `popoto_floor` check in the Environment category; it PASSES under `.venv/bin/python` on this machine and FAILS with a non-empty `fix` under a monkeypatched below-floor version.
- [x] No NEW uninterlocked `rebuild_indexes()` caller is introduced beyond the ten-site baseline recorded in Risk 3 (checked by `scripts/checks/no_new_rebuild_callers.sh`, which pins the baseline as an explicit file list — never a bare zero-match grep, and never an unquoted `--include` glob).
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`), including the `popoto-descriptor-pollution-ledger.md:16` correction.

## Team Orchestration

### Team Members

- **Builder (floor-guard)**
  - Name: `floor-guard-builder`
  - Role: Implement `config/popoto_floor.py`, the `repair_indexes()` interlock, and the doctor check.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Test engineer (floor-guard)**
  - Name: `floor-guard-tester`
  - Role: Unit coverage for the resolver, predicate, assertion, every fail-open branch, and the no-Redis-mutation regression test.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (floor-guard)**
  - Name: `floor-guard-validator`
  - Role: Verify success criteria and the anti-criteria; confirm guard placement precedes all Redis work.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `floor-guard-docs`
  - Role: Feature doc, README index row, and the two existing-doc corrections.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Per the template's roster. Domain framing for the builder: Redis/Popoto data — never raw Redis on Popoto-managed keys; all test fixtures use a `test-` project-key prefix and are deleted via the ORM.

## Step by Step Tasks

### 1. Implement the floor resolver
- **Task ID**: build-floor-module
- **Depends On**: none
- **Validates**: tests/unit/test_popoto_floor.py (create)
- **Assigned To**: floor-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `config/popoto_floor.py`.
- Resolve `pyproject.toml` via `Path(__file__).resolve().parent.parent / "pyproject.toml"`; parse with `tomllib`; find the `popoto` entry in `[project].dependencies`; extract the `>=` lower bound with `packaging.requirements.Requirement`.
- Read the running interpreter's version from `popoto.__version__` — the module actually imported. **Never `importlib.metadata`**, and never a metadata fallback when `__version__` is absent: metadata is a static string over mutable source and reported a version for a popoto that raised `ModuleNotFoundError` on this very machine (stale editable-install `.pth`). An absent `__version__` is *unresolvable*, not an excuse to consult a less trustworthy oracle.
- Compare with `packaging.version.Version`. Never compare version strings lexically.
- Expose `popoto_floor_satisfied()` returning a structured result (satisfied / installed / floor / reason) that distinguishes "satisfied", "violated", and "unresolvable", plus `assert_popoto_floor()` raising `RuntimeError` only on "violated".
- Fail open on every unresolvable branch, but **loudly**: emit a `logger.error` and a `sentry_sdk.capture_message` at `error` level from inside the resolver itself, mirroring `agent/index_drift.py::_report_loud` (`:202-222`) so the signal never depends on a caller's handling. An unresolvable floor means the interlock is disabled; that must not be a WARNING nobody reads. The Sentry capture is itself exception-isolated — it must never crash the caller. No bare `except: pass`.
- The `RuntimeError` message must name `sys.executable`, the installed version, the required floor, and the `.venv/bin/python` remedy.

### 2. Install the seam interlock
- **Task ID**: build-seam-interlock
- **Depends On**: build-floor-module
- **Validates**: tests/unit/test_popoto_floor.py, tests/unit/test_agentsession_pending_index_leak.py
- **Informed By**: spike-1 (subclass interception confirmed, `cls` binds correctly), spike-2 (`models/__init__.py` runs before every caller)
- **Assigned To**: floor-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add an `install_rebuild_interlock()` function that wraps `popoto.models.base.Model.rebuild_indexes` so it calls `assert_popoto_floor()` before delegating to the original.
- Resolve the original via `Model.__dict__["rebuild_indexes"]`. If absent, emit `logger.error` + a Sentry capture and leave the seam uninstalled — **never raise**: `models/__init__.py` imports on the bridge/worker/every-script path, so raising here would turn a popoto rename into a full outage.
- Preserve `classmethod` binding so `cls` is the concrete subclass (spike-1 depends on this; `popoto_index_cleanup.py:262` calls the generic form).
- Make the install idempotent via a sentinel attribute — re-importing `models` must not double-wrap.
- Call it as the **first statement** of `models/__init__.py`, before the model imports.
- Add the repo-standard re-verify-on-upgrade note naming `popoto/models/base.py:2707`, mirroring `models/session_lifecycle.py:502-504`.
- **Edit exactly one call site: `AgentSession.repair_indexes()` entry** (before the `$IndexF` scan at `models/agent_session.py:2416`), because that method deletes indexes at `:2425` before the seam is ever reached. Add the inline comment naming that ordering constraint. Do not guard any other caller; do not touch `scripts/migrate_strip_pty_fields.py` or `scripts/migrate_schema_diet_fields.py` (owned by #2524); do not add an import-time raise to `models/agent_session.py`.

### 3. Add the doctor check
- **Task ID**: build-doctor-check
- **Depends On**: build-floor-module
- **Validates**: tests/unit/test_doctor.py
- **Assigned To**: floor-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_check_popoto_floor()` to `tools/doctor.py` in the Environment category, consuming `popoto_floor_satisfied()` (no duplicate logic).
- Register it in `get_checks()` next to `_check_venv`.
- PASS on satisfied; **FAIL on unresolvable** with a message saying the interlock is disabled; FAIL on violated with a `fix` naming `.venv/bin/python`. Both failure modes carry a non-empty `fix`.
- **Runtime and observability deliberately diverge here** (critique CONCERN). The runtime interlock fails *open* on an unresolvable floor, because a false positive would block index repair fleet-wide on the worker startup path. The doctor check fails *loud* on the same condition, because doctor is a diagnostic that gates nothing — and "the interlock is silently disabled" is exactly the state a health check exists to surface. `CheckResult` has no third degraded state (`tools/doctor.py:53-75`), so a PASS-with-note would collapse to `passed=True` in any boolean summary; FAIL is the only rendering that is actually visible.
- The check must never raise — return a `CheckResult` even when metadata lookup throws.

### 3b. Add the baseline-diff caller check
- **Task ID**: build-baseline-check
- **Depends On**: build-seam-interlock
- **Validates**: tests/unit/test_popoto_floor.py
- **Assigned To**: floor-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/checks/no_new_rebuild_callers.sh`: assert that the set of files invoking `rebuild_indexes()` is a subset of an explicit, in-script baseline file list (the Risk 3 inventory). Exit 0 when no NEW caller appears; exit non-zero naming any newcomer.
- **Pin the baseline as a literal file list, never a bare zero-match grep** — round 1's implementation note, restated by round 2. A file dropping off the list (e.g. #2524 removing two) must NOT fail the check; only additions fail.
- Quote every `--include="*.py"`. An unquoted glob makes zsh abort the command and return exit 1, which reads as a pass — that is the exact defect that produced this plan's first-draft error, and it must not be reproduced in the check that guards against it.
- Verify red-state first: temporarily add a call site, confirm the script fails and names it, then remove it and paste that output into the PR body.

### 4. Test the guard
- **Task ID**: test-floor-guard
- **Depends On**: build-seam-interlock, build-doctor-check
- **Validates**: tests/unit/test_popoto_floor.py, tests/unit/test_doctor.py
- **Assigned To**: floor-guard-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_popoto_floor.py`: floor parsed from a fixture `pyproject.toml`; satisfied / violated / unresolvable classification; every fail-open branch (missing file, malformed TOML, no `popoto` requirement, no `>=` bound, absent `popoto.__version__`, unparseable installed version) asserting a `logger.error` is emitted — **ERROR, not WARNING**: this branch means the interlock is disabled. There is no `PackageNotFoundError` branch; that failure mode disappeared with the metadata oracle.
- Assert the `RuntimeError` message contains all four load-bearing facts.
- **Regression test (the load-bearing one):** monkeypatch the reported installed version to `1.7.1`, snapshot `$Class:AgentSession` cardinality and the `$IndexF:AgentSession:*` key set via the ORM/sanctioned read paths, call `repair_indexes()`, assert it raises, then assert both snapshots are unchanged. The monkeypatch guarantees the raise happens before any Redis call, so this test never risks a real rebuild.
- Assert no `IndexedField` shim is left installed after a guard-triggered raise.
- Any AgentSession fixture uses a `test-` project-key prefix and is deleted via the ORM in teardown. Never a bulk unscoped operation.
- Add doctor coverage for PASS / FAIL / unresolvable and the non-empty `fix` string.

### 5. Validate
- **Task ID**: validate-floor-guard
- **Depends On**: test-floor-guard
- **Assigned To**: floor-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm every Success Criteria row.
- Re-check the Risk 3 ten-site inventory against the live tree and confirm every surviving site is covered — via the `__popoto_floor_guarded__` sentinel on `popoto.models.base.Model.rebuild_indexes`, not a caller-count grep. If #2524 has merged, two sites will have disappeared; that is expected and reduces the inventory rather than invalidating it.
- Run `scripts/checks/no_new_rebuild_callers.sh` and confirm it passes against the recorded baseline.
- Confirm the guard call precedes every Redis operation by reading the method top-down.
- Run each `## Verification` command and record actual output.

### 6. Documentation
- **Task ID**: document-floor-guard
- **Depends On**: validate-floor-guard
- **Assigned To**: floor-guard-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/popoto-version-floor-guard.md` and add the `docs/features/README.md` index row.
- Correct `docs/features/popoto-descriptor-pollution-ledger.md:16` (the "closed by deploy hygiene" claim).
- Update the `agent/index_drift.py` module docstring to cross-reference the now-identified root cause of the 2026-07-14 incident.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-floor-guard
- **Assigned To**: floor-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run all verification commands including the anti-criteria.
- Confirm documentation tasks landed.
- Generate the final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Focused tests pass | `scripts/pytest-clean.sh tests/unit/test_popoto_floor.py tests/unit/test_doctor.py tests/unit/test_agentsession_pending_index_leak.py -q` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check .` | exit code 0 |
| Format clean | `.venv/bin/python -m ruff format --check .` | exit code 0 |
| Guard module exists | `.venv/bin/python -c "from config.popoto_floor import assert_popoto_floor, popoto_floor_satisfied"` | exit code 0 |
| Floor read from pyproject, not hardcoded | `grep -nE '"[0-9]+\.[0-9]+\.[0-9]+"' config/popoto_floor.py` | match count == 0 |
| Floor is resolved at runtime from pyproject | `grep -c 'declared_floor' config/popoto_floor.py` | output > 0 |
| Interlock installed on import of `models` | `.venv/bin/python -c "import models, popoto.models.base as b; assert getattr(b.Model.rebuild_indexes, '__popoto_floor_guarded__', False); print('installed')"` | output contains installed |
| Interlock install is idempotent | `.venv/bin/python -c "import models, importlib, popoto.models.base as b; f=b.Model.rebuild_indexes; importlib.reload(models); print(b.Model.rebuild_indexes is f)"` | output contains True |
| Interlock intercepts a real subclass call | `scripts/pytest-clean.sh tests/unit/test_popoto_floor.py -q -k interception` | exit code 0 |
| Doctor check registered | `.venv/bin/python -m tools.doctor --json \| .venv/bin/python -c "import json,sys; print([c['name'] for c in json.load(sys.stdin)['checks']].count('popoto_floor'))"` | output contains 1 |
| Doctor passes on this machine | `.venv/bin/python -m tools.doctor --json \| .venv/bin/python -c "import json,sys; print([c['passed'] for c in json.load(sys.stdin)['checks'] if c['name']=='popoto_floor'])"` | output contains True |
| Anti-criterion: no NEW uninterlocked caller beyond the recorded baseline | `bash scripts/checks/no_new_rebuild_callers.sh` | exit code 0 |
| Anti-criterion: no pointer-field deletion | `grep -rniE "hdel|idxset" --include="*.py" config/popoto_floor.py tools/doctor.py` | match count == 0 |
| Anti-criterion: #2524-owned files untouched | `git diff --name-only main...HEAD -- scripts/migrate_strip_pty_fields.py scripts/migrate_schema_diet_fields.py` | match count == 0 |
| Anti-criterion: guard not scattered across migration scripts | `grep -rln "assert_popoto_floor" scripts/ --include="*.py"` | exit code 1 |
| Anti-criterion: no raw Redis writes added | `grep -rnE "POPOTO_REDIS_DB\.(delete\|srem\|sadd\|zrem\|hdel)" config/popoto_floor.py tools/doctor.py` | match count == 0 |

## Critique Results

Round 3 re-critique of the revised plan (commit `db9a11caf`) — 2026-08-06. FULL depth, 3 critics (Risk & Robustness, Scope & Value, History & Consistency), roster gate 3/3 complete and grounded. Verdict: **NEEDS REVISION** — 2 blockers, 2 concerns.

Round 2's blockers 1 and 3, and concerns 2, 3, 4, 5, 6 were genuinely applied and verified against the source: the `repair_indexes()` entry guard is mandated above the `$IndexF` scan (Technical Approach `:195`, Task 2 `:401`, Success Criteria `:327-328`); the anti-criterion is a baseline-diff check (Task 3b, `scripts/checks/no_new_rebuild_callers.sh`) with every `--include` glob quoted; install-time seam failure logs + Sentry instead of raising (Task 2 `:396`, Risk 4). **The oracle fix and the severity fix were applied to the prose but NOT to the Step by Step Tasks** — the same partial-application pattern round 2 caught in round 1. A builder executes the task list, so those two remain live blockers.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency, structural | Round-2 fix #2 (oracle = `popoto.__version__`) is not applied to the builder instructions. Task 1 `:381` still reads "Read the running interpreter's version with `importlib.metadata.version(\"popoto\")`" — the exact oracle round 2's CONCERN row `:505` rejected, and the exact oracle Success Criterion `:330` greps to prove absent from `config/popoto_floor.py`. A builder executing Task 1 verbatim writes code that fails the plan's own Success Criterion. | **ADDRESSED (round 3)** — Task 1 :381 now mandates `popoto.__version__` and explicitly forbids a metadata fallback when `__version__` is absent. Stale framing purged from Architectural Impact, Failure Path, and Test Impact. NOTE: the SHIPPED code (PR #2546) never used `importlib.metadata` — `grep importlib.metadata config/popoto_floor.py` returns nothing — so this was a plan-document lag, not a code defect. | Replace Task 1 `:381` with: "Read the running interpreter's version from `popoto.__version__` (`getattr(popoto, \"__version__\", None)`); if the attribute is absent, classify the result as **unresolvable** — do NOT fall back to `importlib.metadata`, per Technical Approach `:192`." Note that one critic suggested a metadata fallback; that suggestion is **rejected** — `:192` establishes the no-fallback rule because the stale editable-install `.pth` on this machine is exactly the false signal a fallback would reintroduce. |
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency, structural | Round-2 fix #5 (uniform `logger.error` + Sentry) is not applied to Task 4. `:435` still instructs the tester to assert "the WARNING is emitted" for every fail-open branch, contradicting Task 1 `:384`, Task 3 `:412-413`, and Success Criterion `:331`. It additionally enumerates `PackageNotFoundError` as a fail-open branch, an exception that cannot fire once `importlib.metadata` is gone. Tests written from Task 4 will assert the wrong severity and cover a dead branch. | **ADDRESSED (round 3)** — Task 4 :435 now asserts `logger.error` (explicitly 'ERROR, not WARNING') and the dead `PackageNotFoundError` branch is replaced with 'absent `popoto.__version__`'. The shipped tests already assert ERROR-level; see `test_unresolvable_branches_fail_open_but_loud`. | Replace Task 4 `:435`'s branch list with: missing `pyproject.toml`, malformed TOML, no `popoto` requirement, no `>=` bound, **absent `popoto.__version__` attribute**, unparseable installed version — and change "asserting the WARNING is emitted" to "asserting a `logger.error` AND a `sentry_sdk.capture_message(level=\"error\")` are emitted, and that no WARNING-only path exists." |
| CONCERN | Risk & Robustness, Scope & Value, History & Consistency | Stale `importlib.metadata` framing survives in four more sections beyond Task 1: Architectural Impact `:148` ("the guard reads `importlib.metadata`, not popoto's private API"), Failure Path Test Strategy `:206` and `:211` (tests specced around `importlib.metadata.version` raising `PackageNotFoundError`), Test Impact `:220`, and Race Conditions `:278`. These are the sections a reviewer reads to sanity-check the design, so the plan still reads as if the rejected oracle is the design. | **ADDRESSED (round 3)** — All four sections corrected. Architectural Impact now states coupling to popoto IS deliberately increased (a monkeypatch with a re-verify-on-upgrade obligation) rather than the false claim that it is not. The only surviving mentions are explicit prohibitions plus the No-Go describing the real residual artifact. | Scrub all five: `:148` → "the guard reads `popoto.__version__`, not popoto's private API"; `:206` → "even when the `popoto.__version__` read raises or the attribute is absent"; `:211` → "absent `popoto.__version__` attribute → treated as unresolvable, fail open"; `:220` → "perturbs `popoto.__version__`"; `:278` → "(`popoto.__version__`, a file read)". Do this in the same pass as the Task 1 fix so the two cannot drift again. |
| CONCERN | History & Consistency | The #2524 serialization narrative is stale as of this very revision commit. Freshness Check `:74` and Prior Art `:87` both say #2524's "critique returned NEEDS REVISION and it is mid-revision", but `docs/plans/generalize-migration-guards-2524.md` landed on main at `413efa7cf` — BEFORE `db9a11caf` — reading "NEEDS REVISION -> RESOLVED" with `status: Ready`, and #2524 now has an open PR (#2545) carrying real code. The serialization ruling itself is unaffected (the seam interlock is merge-order agnostic), but a reader acting on `:74` misjudges how close the two files are to changing under them. | **ADDRESSED (round 3)** — Narrative updated: #2524's plan is status Ready (critique resolved at `413efa7cf`) with code in review as PR #2545; nothing merged. Serialization ruling unchanged — #2524 lands first, and this lane's diff is verified not to touch its two files. | Reword `:74` and `:87` to: "#2524's plan document is on main and its critique is RESOLVED (`status: Ready`); its code is unmerged on `session/generalize-migration-guards-2524` and under review in PR #2545." Keep the serialization ruling and the No-Go carve-out for the two migration scripts exactly as written. |

### Round 2 (2026-08-06, commit `7090419b0`)

Round 2 re-critique of the revised plan (commit `7090419b0`) — 2026-08-06. FULL depth, 3 critics, roster gate 3/3 complete and grounded. Verdict: **NEEDS REVISION** — 3 blockers, 6 concerns, 4 nits.

Round 1's two blockers were correctly re-scoped in the plan body (seam-level interlock; #2524 overlap recorded), but round 1's blocker-1 Implementation Note — "the anti-criterion must become a diff-based 'no NEW uninterlocked caller beyond this baseline list' check, not a bare zero-match grep" — was **not applied**; the Verification row is unchanged. Round 1 table retained in commit `7090419b0`.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, structural | The seam-only interlock fires too late to satisfy the plan's own headline criteria. `repair_indexes()` deletes every `$IndexF:AgentSession:*` key at `models/agent_session.py:2425` — before the re-entrancy lock (`:2429`), before the shim install (`~:2475`), and before `cls.rebuild_indexes()` (`:2488`). Success Criteria `:327` ("raises `RuntimeError` before any Redis operation") and `:328` ("`$IndexF:AgentSession:*` key set byte-identical before and after") are therefore unachievable, and the Task 4 regression test will fail. Data Flow `:142` ("the guard inserts at step 2, before step 3") contradicts Data Flow `:137`, which places the `$IndexF` clear inside step 2. | **ADDRESSED (round 2)** — Technical Approach and Task 2 now mandate exactly one call-site edit: `assert_popoto_floor()` at `repair_indexes()` entry, above the `$IndexF` scan, in addition to the seam. Success Criteria updated to assert both guard points. | Add exactly one `assert_popoto_floor()` call as the first statement of `repair_indexes()`, above `prefix = f"$IndexF:{cls.__name__}:"` (`models/agent_session.py:2413`) and above the `from popoto.models.query import POPOTO_REDIS_DB` import at `:2404`. Keep the seam interlock for the other 8 sites. This requires relaxing Technical Approach `:195` ("Do not also add a per-call-site guard in `repair_indexes()`") and Task 2 `:400` ("Edit exactly zero call sites") to "exactly one: `repair_indexes()` entry". The redundancy is not drift — the seam and the entry guard protect two structurally different teardowns. |
| BLOCKER | Scope & Value, structural | Success Criterion `:332` ("`AgentSession.repair_indexes()` remains the only in-repo caller of popoto's `rebuild_indexes()`") and Task 5 `:436` ("Re-run the sole-caller grep") are draft-1 leftovers that directly contradict Risk 3's ten-call-site inventory (`:250-261`) — the very finding that motivated the re-scope, and the same false claim round 1 flagged as blocker 1. A correctly-quoted grep finds 9 live call sites on main. Validating against `:332` would re-assert the retracted claim. | **ADDRESSED (round 2)** — the sole-caller Success Criterion is deleted and replaced with a sentinel-coverage criterion plus a baseline-diff check; Task 5 now re-checks the Risk 3 inventory against the sentinel instead of counting callers. | Delete criterion `:332`; replace with "Every call site in the Risk 3 inventory is covered by the interlock, verified via the `__popoto_floor_guarded__` sentinel on `popoto.models.base.Model.rebuild_indexes`." Replace Task 5 `:436` with a re-check of the Risk 3 inventory against that sentinel rather than a caller-count grep. |
| BLOCKER | structural | Round 1's prescribed fix for the anti-criterion was not applied, and the row is additionally broken at the shell level. `:474` and `:475` use **unquoted** `--include=*.py`, which zsh rejects outright ("no matches found", exit 1) — the exact defect the plan documents at `:79` and `:248` as the cause of its first draft's miss. Both rows therefore pass **vacuously**: `:474` expects "exit code 1" and gets it from the glob failure, never from grep. Correctly quoted, `:474` finds the 8 legitimate `scripts/` call sites this plan deliberately does not edit, so its stated expectation is also semantically wrong. | **ADDRESSED (round 2)** — Both anti-criteria rows replaced. The uninterlocked-caller row is now a baseline-diff check (`scripts/checks/no_new_rebuild_callers.sh`, added as Task 3b) per round 1's implementation note, and every remaining `--include` glob is quoted. Red-state proven: the correctly-quoted grep returns 8 `scripts/` sites today. | Quote the pattern in every grep row: `grep -rn --include='*.py' ...`. Then implement round 1's instruction: `:474` must become a diff against a checked-in baseline list of known call sites, asserting no NEW uninterlocked caller — not a bare zero-match grep. Run every Verification row in zsh before landing; a row that exits 1 on a glob error is indistinguishable from a passing row. |
| CONCERN | Risk & Robustness, structural | `importlib.metadata.version("popoto")` is not a sound oracle for "which popoto will this process run", so Technical Approach `:192` ("reports what *this interpreter* actually imported") is factually wrong. Verified on this machine: ambient `python3` reports metadata `1.7.1` while `import popoto` raises `ModuleNotFoundError` — the 1.7.1 install is an **editable** install (`/opt/homebrew/lib/python3.12/site-packages/__editable__.popoto-1.7.1.pth`) pointing at a deleted git worktree. Under an editable install the metadata string is static while the source is mutable, so the oracle can be wrong in **either** direction: false positive blocks index repair fleet-wide (Risk 1 realized), false negative lets the index be destroyed anyway. | **ADDRESSED (round 2)** — Oracle changed to `popoto.__version__`; `importlib.metadata` is no longer used for the version lookup, and a Success Criterion greps to enforce that. Confirmed empirically on this machine: ambient `import popoto` raises `ModuleNotFoundError` while metadata still reports 1.7.1 from a stale editable `.pth`. | The guard wraps `popoto.models.base.Model.rebuild_indexes`, so popoto is already imported when it fires. Read `popoto.__version__` as the primary oracle (confirmed present: `1.8.0` in `.venv`), falling back to `importlib.metadata.version("popoto")` only when the attribute is absent. Include `sys.modules["popoto"].__file__` in the `RuntimeError` message — under an editable install the path is the only way an operator can tell which checkout is live. |
| CONCERN | Risk & Robustness | Risk 5's "fail loudly at install time" (`:271`, Task 2 `:395`) means raising inside `models/__init__.py`, which is on the import path for the bridge, the worker, and every repo script. A future popoto that renames or relocates `rebuild_indexes` would take down the entire fleet rather than just the guarded maintenance operation — blast radius disproportionate to the fail-open posture the plan adopts everywhere else. | **ADDRESSED (round 2)** — Install-time failure no longer raises. It emits `logger.error` + a Sentry capture and leaves the seam uninstalled; the sentinel is asserted by a test and by the doctor check. Raising on the bridge/worker import path was the right thing to reject. | Do not raise from `install_rebuild_interlock()`. On a missing `Model.__dict__["rebuild_indexes"]`, emit `logger.error` + `sentry_sdk.capture_message(level="error")` and set a module-level `INTERLOCK_INSTALLED = False`; have the doctor check FAIL on that flag. The "silent no-op fails CI" requirement is preserved by the Task 4 test asserting the sentinel after `import models`. |
| CONCERN | Scope & Value, structural | The fail-open branch is specified at two different severities. `:191`, `:209-212`, `:239`, and Success Criterion `:330` say "fail open with a WARNING"; `:203` and Task 1 `:383` say `logger.error` plus a Sentry capture at error level. Criterion `:330` asserts the WARNING form, so the test written against it will contradict the code written against Task 1. | **ADDRESSED (round 2)** — Normalized throughout to `logger.error` + Sentry capture at runtime, plus a doctor **FAIL**. No WARNING-severity wording remains for the unresolvable branch. | Task 1 `:383` is authoritative (an unresolvable floor means the interlock is disabled — not a WARNING anyone reads). Scrub "WARNING" from `:191`, `:209-212`, `:239`, and `:330`, replacing with "fails open, logging ERROR + Sentry capture". |
| CONCERN | Scope & Value | Documentation task `:322` instructs a comment "at the `assert_popoto_floor()` call site in `repair_indexes()`", but Task 2 `:400` and Technical Approach `:195` forbid any call site there. Under the seam-only design no such site exists, so the doc task is unexecutable. | **ADDRESSED (round 2)** — Resolved by the blocker-1 fix: `repair_indexes()` now legitimately has exactly one `assert_popoto_floor()` call site, so the documentation task is consistent with Task 2. | Resolves automatically if blocker 1 is fixed by adding the `repair_indexes()` entry guard — `:322` then names a real site. If blocker 1 is instead resolved by rewriting the criteria, retarget `:322` to the `assert_popoto_floor()` call inside the `install_rebuild_interlock()` wrapper. Do not leave both wordings. |
| CONCERN | structural | The `[EXTERNAL]` No-Go `:292` gives a remedy that does not match this machine: it cites `~/Library/Python/3.12/lib/python/site-packages/popoto`, which does not exist. The real artifact is an editable install at `/opt/homebrew/lib/python3.12/site-packages/` (`__editable__.popoto-1.7.1.pth` + `popoto-1.7.1.dist-info`). Relatedly, the premise table `:24` and the claim at `:41` that "the exposure is live and ongoing" are stale — `import popoto` under ambient `python3` now raises `ModuleNotFoundError` because the editable target worktree was deleted. | **ADDRESSED (round 2)** — No-Go rewritten. It now records that the parent session already removed the ambient install, names the real residual artifact (stale editable-install `.pth` metadata), and ties it to why the guard reads `popoto.__version__`. | Correct `:292` to `python3 -m pip uninstall -y popoto` plus removal of `/opt/homebrew/lib/python3.12/site-packages/__editable__.popoto-1.7.1.pth`. Re-word `:41` to state the exposure is latent rather than currently reproducible, and name the editable-install mechanism — an editable target is restored by any `git worktree add` in the popoto repo, which is exactly why the guard is still warranted. Make the evidence honest without weakening the justification. |
| CONCERN | History & Consistency | Freshness Check `:74` claims #2524 "landed on main after this plan" while `:87` says it is "in flight" with a NEEDS REVISION critique. `git log main` confirms only #2524's plan-document commits are on main; its code (`925502c4f`) sits solely on the unmerged branch `session/generalize-migration-guards-2524`. | **ADDRESSED (round 2)** — Wording corrected to distinguish #2524's plan document (merged) from its implementation (in flight, NEEDS REVISION, nothing merged). | Reword `:74` to "#2524's plan document is on main; its code is unmerged on `session/generalize-migration-guards-2524`." The serialization ruling is unaffected — the seam interlock is merge-order agnostic — but a reader acting on `:74` would wrongly assume the two migration scripts already use `clean_indexes()`. |
| NIT | History & Consistency | `## Test Impact` lists `tests/unit/test_popoto_floor.py` — CREATE twice, at `:223` and `:225`. | **ADDRESSED (round 2)** — Duplicate row removed. | Merge into one row. |
| NIT | structural | Risk sections are out of order: Risk 5 appears at `:269`, before Risk 4 at `:273`. | **ADDRESSED (round 2)** — Renumbered — the popoto-upgrade risk is now Risk 4, the stale-ambient-install risk is Risk 5. | Reorder or renumber. |
| NIT | structural | The Verification row at `:471` embeds a literal `\n` inside a `python -c` string, so the command is not runnable as written. | **ADDRESSED (round 2)** — Row replaced with a focused pytest invocation (`-k interception`) instead of an inline `python -c`. | Move the snippet to a heredoc or a `tests/` helper rather than inlining multi-line Python in a table cell. |
| NIT | History & Consistency | Technical Approach `:197` frames the monkeypatch as following "the existing repo convention for popoto coupling points", citing `models/session_lifecycle.py:502-504`. That precedent is a comment convention over direct Popoto-internals *reads*, not a monkeypatch; a grep across production code found zero other third-party class monkeypatches. The technique is novel here. | **ADDRESSED (round 2)** — Reframed honestly: there is no monkeypatch precedent in this repo; `session_lifecycle.py:502-504` is a comment convention, not a patch. The plan now states this is a new pattern that should be reviewed as one. | Reword `:197` so the *comment* convention is what is borrowed and the monkeypatch is acknowledged as novel, so a future reader does not assume precedent that does not exist. |

### Round 1 (2026-08-06, commit `7090419b0`)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency, structural check | Risk 3's "Verified by grep at plan time: `Model.rebuild_indexes()` has exactly one in-repo caller, `AgentSession.repair_indexes()`" is FALSE on main today. Nine live (non-docstring) call sites invoke `.rebuild_indexes()` directly, bypassing `repair_indexes()` and therefore the new guard entirely: `scripts/migrate_agent_session_keyfield_rename.py:180`, `scripts/migrate_strip_pty_fields.py:161`, `scripts/migrate_parent_session_field.py:161`, `scripts/migrate_session_type_pm_to_eng.py:321`, `scripts/popoto_index_cleanup.py:262`, `scripts/migrate_unify_parent_session_field.py:110`, `scripts/merge_dev_chat_into_eng.py:371`, `scripts/migrate_schema_diet_fields.py:230`, `scripts/migrate_session_type_chat_to_pm.py:155`. All the `migrate_*` ones carry `#!/usr/bin/env python3` shebangs — i.e. they ARE the ambient-interpreter vector the Problem section names as dominant. The guard sits on a path the dominant vector does not traverse, so the Rabbit Holes claim "the interlock covers every vector at one site" does not hold. | **ADDRESSED** — Solution re-scoped from a per-caller guard in `repair_indexes()` to a single seam-level interlock on `popoto.models.base.Model.rebuild_indexes`, installed from `models/__init__.py`. Spike-1 empirically confirms it intercepts all ten sites (the critic found nine; `models/agent_session.py:2488` is the tenth) including the aliased-import and generic `model_class` forms. Risk 3 now carries the full verified inventory. | The plan's own Verification anti-criterion `grep -rn "\.rebuild_indexes(" --include=*.py models/ tools/ agent/ bridge/ worker/ scripts/ \| grep -v "models/agent_session.py"` (Expected: exit code 1) returns 16 matching lines on unmodified main — it fails at baseline, before any build work. Resolve by either (a) calling `assert_popoto_floor()` at the top of each direct call site (or routing them through `repair_indexes()`), or (b) explicitly narrowing Risk 3 / the Success Criterion to "the `repair_indexes()` path only" and carving the 9 script call sites into a named follow-up. Either way the anti-criterion must become a diff-based "no NEW uninterlocked caller beyond this baseline list" check, not a bare zero-match grep. |
| BLOCKER | History & Consistency, Scope & Value | The Freshness Check's "Active plans in `docs/plans/` overlapping this area: none." is false. `docs/plans/generalize-migration-guards-2524.md` (issue #2524, status Ready, already built on branch `session/generalize-migration-guards-2524`) cites #2536 directly and rewrites two of the same call sites — `scripts/migrate_strip_pty_fields.py:161` and `scripts/migrate_schema_diet_fields.py:230` — swapping `rebuild_indexes()` for `clean_indexes()` specifically to route around this bug. Neither plan cross-references the other. | **ADDRESSED** — Freshness Check overlap entry added; #2524 added to Prior Art; the two shared call sites are marked #2524-owned in Risk 3's table and carved out as a No-Go this lane must not edit. Serialization ruling recorded: #2524 lands first. Because the guard is now seam-level rather than per-caller, the merge order no longer changes this plan's correctness — whichever call sites survive are covered automatically. | If #2524 merges first, the 9-call-site baseline this plan's Risk 3 evidence depends on drops to 7, so Task 5 (`validate-floor-guard`) must re-run the sole-caller grep against the post-merge tree rather than the count recorded at plan time. Add #2524 to Prior Art and to the Freshness Check overlap list, and record the merge-order dependency explicitly. |
| CONCERN | Risk & Robustness | The fail-open "unresolvable floor" branch (missing/malformed `pyproject.toml`, absent `popoto` requirement, no `>=` bound, `PackageNotFoundError`, unparseable version) surfaces only as a `logger.warning` plus a doctor "PASS-with-note". That is precisely the state in which the interlock is silently disabled, yet `python -m tools.doctor` renders it identically to a clean PASS. Contrast `agent/index_drift.py`, which reports unconditionally to Sentry so the signal never depends on a caller swallowing it. | **ADDRESSED (round 2)** — Addressed in round 2 as described in the Technical Approach and Task 1/3: `logger.error` + Sentry from inside the resolver, and doctor renders unresolvable as FAIL (no PASS-with-note). | `tools/doctor.py`'s `CheckResult` models only `passed`/`message`/`fix` (see `_check_venv` at `tools/doctor.py:95`) — there is no third degraded state, so "PASS-with-note" collapses to `passed=True` in any summary that counts booleans. Task 3 must either add an explicit degraded/WARN rendering or emit an ERROR-level log (or Sentry capture) on the unresolvable branch so a silently-disabled interlock is visible without tailing a WARNING log. |

---

## Open Questions

None blocking. The root cause is settled with reproducible read-only evidence under both interpreters, the fix scope is one module plus two call sites, and the one judgment call (operator-gated removal of the stale ambient popoto) is explicitly deferred as an `[EXTERNAL]` No-Go rather than left ambiguous.

The one judgment call a reviewer should look at directly: **the runtime interlock fails open on an unresolvable floor while the doctor check fails loud on the same condition.** That asymmetry is deliberate. Blocking index repair fleet-wide on an unknown would be a worse incident than the one being prevented, but a silently-disabled interlock must still be visible — so the runtime degrades and the diagnostic shouts. If a reviewer prefers fail-closed at runtime too, that is a one-line change with a materially different risk profile.
