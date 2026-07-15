---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-15
tracking: https://github.com/tomcounsell/ai/issues/2086
last_comment_id: 4966570427
---

# AgentSession Index-Drift Loud Surfacing

## Problem

On 2026-07-14 an eng session crashed with `unpack(b) received extra data` (a
msgpack decode failure). Afterward `AgentSession.query.all()` returned **0 with
no exception**, `valor_session list` said "No sessions found", and the dashboard
showed an empty queue — while **11 AgentSession hashes still existed in Redis**
(`repair_indexes` reported `sessions_rebuilt=11, cleaned=0`). The queue was
effectively blind: every observability surface reported zero sessions while the
data was intact but unreachable through the index.

**Current behavior:** When the status index / class set desyncs from the actual
hashes (index empty or unreadable, hashes present), `query.all()` legitimately
returns `[]` — `get_many_objects` finds no db_keys and returns an empty list
with no error (`popoto/models/query.py:2688-2694`). There is **no signal**
anywhere that distinguishes "genuinely zero sessions" from "N orphaned hashes
the index can no longer see." Corruption masquerades as emptiness, silently.

**Desired outcome:** A divergence between the raw hash count and the queryable
count surfaces **loudly** — a logged ERROR plus a Sentry capture carrying both
counts — at worker startup (after the existing index-repair steps) and via a
`python -m tools.doctor` health check. An empty queue that is actually corruption
can never again pass unnoticed. The guard reuses the existing bounded-SCAN
primitive and does not touch the popoto-1.8.0 descriptor-pollution code owned by
#2083.

## Freshness Check

**Baseline commit:** `4a1bd83cb9c87031f818c4d5fbf0f17a2d9c8f49`
**Issue filed at:** 2026-07-14T06:39:02Z
**Disposition:** Major drift (on root cause) — plan re-scoped to the durable code slice.

**File:line references re-verified (against current main):**
- `popoto/models/encoding.py:320,333,422` — the installed 1.8.0 decoder skips `\x00`
  internal pointer fields (`{field}\x00idxset`). Confirmed present. This is exactly
  the skip a pre-1.8.0 reader lacked, which is why a 1.7.1 reader ran `unpackb` on a
  raw Set-key pointer value and hit `ExtraData`. The crash mechanism is version-gated.
- `popoto.__version__ == "1.8.0"`; `pyproject.toml:18` pins `popoto>=1.8.0`. The whole
  fleet reads and writes 1.8.0 now — the mixed-version window that produced the crash is
  closed by deploy hygiene.
- `popoto/models/encoding.py:328-334` (`decode_popoto_model_hashmap`) calls
  `msgpack.unpackb` with **no** try/except — so a genuinely corrupt value RAISES and
  propagates; it is never silently swallowed. Confirms the original suspicion ("does
  `query.all()` swallow per-record unpack exceptions?") is **false**. The masking is at
  the index layer, not the decode layer.
- `models/agent_session.py:2100-2143` `repair_indexes` — present; counts stale index
  members, deletes `$IndexF:*` keys, calls `rebuild_indexes()`. Reported `rebuilt=11`
  yet queryable count stayed 0 — membership and hash existence diverged silently.
- `agent/session_archive.py:349-368` `_redis_has_agentsession_keys()` — bounded SCAN
  `match="AgentSession*"`, `_SCAN_MAX_ITERATIONS` / `_SCAN_COUNT_HINT`. Present. This is
  the anchor primitive for a count-based reconciliation.
- `worker/__main__.py:687-742` — startup Step 1 (`popoto_index_cleanup.run_cleanup`),
  Step 2 (`cleanup_corrupted_agent_sessions`), Step 2b (`clean_indexes` class-set orphans).
  Present; the reconciliation guard slots in after 2b.
- `tools/doctor.py:878-920` `get_checks` — `_check_session_archive_freshness` already sits
  in the Services group; a sibling `_check_agentsession_index_drift` registers alongside it.

**Cited sibling issues/PRs re-checked:**
- #2088 / PR #2099 — **MERGED 2026-07-15**. Makes `_worker_loop` survive a `ModelException`
  when popping a corrupted record (the "amplifier" the root-cause comment named). Already
  shipped; this plan does not re-address the worker-loop-survival path.
- #2083 (`docs/plans/popoto-descriptor-pollution-audit.md`, branch
  `session/popoto-descriptor-pollution-audit`, status Ready) — audits the descriptor /
  index-race defensive code in `models/agent_session.py` and `models/session_lifecycle.py`.
  **Coordinate, do not collide:** this plan adds a NEW reconciliation surface and MUST NOT
  edit that file's `__getattribute__`/`__setattr__`, coercion sets, or the `srem`-loop.
- Root-cause comment (id 4966570427) — mixed-version deploy artifact, ~95% confidence,
  reproduced from pure msgpack. Not filable as a straight popoto bug.

**Commits on main since issue filed touching referenced files:**
- `4a5a72ff` "Worker loop survives ModelException when popping a corrupted AgentSession
  (#2088)" — the amplifier fix. Partially addresses the umbrella but NOT the silent-empty
  observability gap this plan targets.
- Plan-migration commits only for `docs/plans/` — no other touches to `worker/__main__.py`,
  `models/agent_session.py`, `agent/session_archive.py`, or `tools/doctor.py`.

**Active plans overlapping this area:** `docs/plans/popoto-descriptor-pollution-audit.md`
(#2083, same file `models/agent_session.py`) and
`docs/plans/session-recovery-observation-audit.md` (candidate 13 — make index repair atomic,
remove duplicate daily cleanup registration). Overlap is a **coordination signal, not a
blocker**: #2083 removes/keeps existing defenses; candidate 13 makes repair atomic; THIS plan
adds a read-only divergence *detector*. The three are orthogonal (detect vs. repair-atomically
vs. audit-defenses). No file-level write collision as long as the detector lives in its own
method and a new doctor check.

**Notes:** The root cause (mixed-version popoto) is resolved by the fleet running `>=1.8.0`;
that half is deploy hygiene, not code, and is explicitly out of scope. What remains — and what
the supervisor mandated — is the code-level defense that makes the *next* index/hash divergence
(from any cause) impossible to miss.

## Prior Art

- **#2088 / PR #2099** — "Worker loop survives ModelException when popping a corrupted
  AgentSession" (merged 2026-07-15). Made the pop path survive a corrupt record instead of
  killing the worker loop. Related amplifier; does not add drift detection.
- **#1803** — earlier instance of the same worker-loop-crash class (`StatusConflictError`
  race). Established the "loop body must not let unhandled exceptions escape" pattern.
- **#1720** — the class-set delete→re-add window inside `rebuild_indexes()`; the reason
  `query.filter(session_id=...)` can transiently see nothing. Documents the exact desync
  mechanism this plan detects.
- **#1459** — `clean_indexes()` class-set orphan cleanup at worker startup (Step 2b). The
  inverse orphan (index member → missing hash); this plan detects the OTHER direction
  (hash present → not queryable).
- **#1271 / #1835** — orphan-index reap cadence and Sentry noise-filtering. Establishes that
  benign transient index noise is filtered (`drop_orphan_noise`); the drift ERROR here must be
  distinguishable from that benign noise (see Risk 2).

## Data Flow

1. **Write:** a live process saves an `AgentSession` (popoto 1.8.0) → hash `AgentSession:<key>`
   plus atomic Lua index maintenance for the `status` IndexedField and the class set.
2. **Desync event:** an index/class-set operation (a non-atomic repair window, a crash mid-write,
   or historically a cross-version reader) leaves the hash present but the index empty or unreadable.
3. **Read (broken):** `AgentSession.query.all()` → `_execute_filter` (no filter) → db_keys from the
   class set/index → empty → `get_many_objects` returns `[]` (`query.py:2688-2694`). No error.
4. **Observability surfaces** (`ui/app.py`, `ui/data/sdlc.py`, `tools/valor_session.py`,
   `agent/session_health.py`) all read through `query.all()` → all report zero sessions.
5. **Guard (new, detect-only):** raw bounded SCAN counts `AgentSession:*` hashes → compare to
   `len(query.all())`. Divergence → log ERROR + Sentry capture (both counts) as the loud signal.
   The guard does NOT call `repair_indexes()` — repair is owned by candidate 13
   (`session-recovery-observation-audit`, atomic index repair). If `query.all()` itself raises on a
   genuinely corrupt hash, reconcile catches it internally and routes it to the same loud ERROR +
   Sentry path (never a swallowed empty).

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm scope boundary against #2083 / candidate 13)
- Review rounds: 1 (Redis/Popoto correctness of the SCAN-vs-query reconciliation)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Guard reads Redis directly |
| popoto >= 1.8.0 | `python -c "import popoto; assert tuple(map(int, popoto.__version__.split('.'))) >= (1,8,0)"` | Decoder skips `\x00` internal fields; no cross-version crash |

## Solution

### Key Elements

- **Reconciliation function** — a single source of truth that returns
  `(hash_count, queryable_count, drifted: bool, truncated: bool)` for AgentSession, using a bounded
  SCAN for the raw hash count and `len(AgentSession.query.all())` for the queryable count. Lives in a
  new dedicated `agent/index_drift.py` (resolved: keeps `session_archive.py` cohesive; imports the SCAN
  cap constants from `agent/session_archive.py`).
- **`query.all()`-raises is surfaced inside reconcile.** Reconcile wraps `len(AgentSession.query.all())`
  in its own try/except: if it raises (genuinely corrupt hash), reconcile logs the loud ERROR + Sentry
  itself and returns a drifted result — so surfacing NEVER depends on any outer caller's try/except
  swallowing it into a silent warning.
- **Loud surfacing (detect-only)** — when drifted, `logger.error(...)` with both counts and a stable
  message prefix, plus `sentry_sdk.capture_message(...)` at `error` level. The message prefix is distinct
  from the benign orphan-noise that `drop_orphan_noise` filters, so it is NOT suppressed. The guard does
  NOT attempt `repair_indexes()` — repair (and its #1720 non-atomic window) is owned by candidate 13.
- **Truncated-scan guard** — if the bounded SCAN exits with `cursor != 0` (keyspace exceeded
  `_SCAN_MAX_ITERATIONS`), reconcile returns `truncated=True`, emits a distinct "scan incomplete" WARNING,
  and does NOT compute drift from the partial count (a partial undercount must never be reported as
  "no drift" or misclassified as a stale-index anomaly).
- **Worker startup guard** — invoked after `worker/__main__.py` Step 2b, non-fatal (wrapped in
  try/except like its neighbors) as a last-resort net for detector bugs only; the corruption path is
  already surfaced loudly inside reconcile, so this outer guard can never downgrade real drift to a
  silent warning.
- **Doctor health check** — `_check_agentsession_index_drift` registered in `get_checks` Services
  group, returning a failing `CheckResult` (with both counts and a fix hint) when drift is present.

### Flow

Worker boot → Step 1/2/2b index repair → **reconcile(AgentSession)** → counts match? →
if yes: continue silently → if no: ERROR + Sentry (both counts) → worker continues serving
(non-fatal; no self-heal — repair is candidate 13's job).

`python -m tools.doctor` → Services checks → **_check_agentsession_index_drift** →
PASS (counts equal) or FAIL ("N hashes, M queryable — index desync; run repair_indexes").

### Technical Approach

- **Raw hash count must exclude non-hash companion keys.** `match="AgentSession*"` also matches
  capped-list keys (`AgentSession:<key>::<field>`) and any `AgentSession`-prefixed non-hash keys.
  Count only keys of Redis type `hash` whose name has the base-key shape (no `::`), OR filter by
  `TYPE == hash`. Reuse the bounded-iteration cap (`_SCAN_MAX_ITERATIONS`) so a corrupt keyspace
  cannot hang the guard. This is the one correctness-sensitive detail — get it exactly right so the
  count is apples-to-apples with `query.all()`.
- **Divergence tolerance defaults to `0`** (any `hash_count > queryable_count` is drift). Per the
  magic-numbers convention it is a named env-overridable constant (`AGENTSESSION_INDEX_DRIFT_TOLERANCE`)
  with a grain-of-salt comment — BUT the feature doc must warn that any nonzero value suppresses the exact
  silent-empty incident class this guard exists to catch (it is a footgun on a should-always-be-0
  invariant; only widen it if a specific environment is proven noisy). `hash_count < queryable_count` is a
  different anomaly (stale index members → already handled by `clean_indexes`); the guard reports it too
  but tags it distinctly.
- **Truncated SCAN is never compared.** The SCAN loop is capped at `_SCAN_MAX_ITERATIONS`; track whether it
  exited on `cursor == 0` (exhaustive) vs hit the cap. Only compute drift when exhaustive; if truncated,
  return `truncated=True`, WARN "scan incomplete", and skip the drift determination — a partial count must
  never masquerade as "no drift."
- **Single source of truth:** worker startup and doctor both import the same reconcile function from
  `agent/index_drift.py`; no copy-paste of the SCAN logic. Because reconcile self-surfaces the
  `query.all()`-raises case, both callers get the loud signal for free.
- **Sentry level `error`**, message prefixed e.g. `[index-drift] AgentSession` so it is greppable and
  not caught by `drop_orphan_noise` (verify against `monitoring/sentry_config.py`).
- **Do NOT** modify decode-level behavior, `__getattribute__`/`__setattr__`, the coercion sets, or
  the `srem`-loop in `models/agent_session.py` / `models/session_lifecycle.py` — that surface is
  owned by #2083.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `query.all()` raising inside reconcile → reconcile itself logs the loud ERROR + Sentry and returns a
  drifted result; add a test asserting the ERROR fires and is NOT downgraded to a silent warning by any
  outer try/except.
- [ ] The worker-startup guard's outer try/except is a last-resort net for *detector bugs* only — add a test
  that an unexpected exception in the detector (not the `query.all()` path) is logged as a warning and does
  NOT crash startup.
- [ ] The doctor check is wrapped by `run_checks`' per-check try/except — add a test that a reconcile
  exception yields a failing `CheckResult` rather than aborting the whole doctor run.
- [ ] No `except Exception: pass` introduced — every handler logs.

### Empty/Invalid Input Handling
- [ ] Empty keyspace (0 hashes, 0 queryable) → `drifted=False`, no ERROR, no Sentry.
- [ ] Corrupt/huge keyspace → bounded SCAN terminates at `_SCAN_MAX_ITERATIONS` with `cursor != 0` →
  `truncated=True`, WARN "scan incomplete", NO drift ERROR fired from the partial count; test asserts the
  guard returns rather than hanging and does not misclassify.
- [ ] `query.all()` raising (genuinely corrupt hash) → reconcile catches it internally and surfaces the
  raise as loud ERROR + Sentry, not a swallowed empty (asserted independently of any outer try/except).

### Error State Rendering
- [ ] The doctor FAIL `CheckResult` renders both counts and an actionable fix string.
- [ ] The Sentry capture payload includes `hash_count`, `queryable_count`, and `truncated`.

## Test Impact

- [ ] `tests/unit/test_worker_entry.py` — UPDATE: add a case asserting the new startup reconcile
  step runs after Step 2b and is non-fatal on a detector-bug exception (existing startup-sequence tests must still pass).
- [ ] `tests/unit/test_doctor.py` — UPDATE: add a case for `_check_agentsession_index_drift`
  (PASS when counts equal, FAIL with both counts when drifted); confirm it is registered in `get_checks`.
- [ ] `tests/unit/test_index_drift.py` — CREATE: unit coverage for the reconcile function in the new
  `agent/index_drift.py` (SCAN-vs-query count, capped-list exclusion, truncated-scan handling,
  `query.all()`-raises loud path).
- [ ] No existing test asserts the current silent-empty behavior, so nothing needs DELETE — the change
  is additive detection (detect-only; no self-heal).

## Rabbit Holes

- **Rewriting popoto's index maintenance to be globally atomic.** That is candidate 13's job
  (session-recovery-observation-audit) — do not open `INDEX_SWAP_LUA` or `rebuild_indexes` internals here.
- **Decode-level quarantine of corrupt hash values.** Tempting ("make decode not crash"), but the
  1.8.0 decoder already skips `\x00` fields and the worker loop already survives the pop (#2099). Adding
  a quarantine layer is a separate concern (#2088's theme) and risks masking real corruption.
- **Auditing / removing the descriptor-pollution defenses.** Owned by #2083. Reading them for context is
  fine; editing them here collides with that branch.
- **Generalizing the drift check to every Popoto model.** Start with AgentSession (the incident model);
  a generic sweep is a follow-up, not this plan.
- **Auto-restoring from the SQLite archive on drift.** `restore_if_empty` already owns cold-boot restore;
  the guard's job is to SURFACE, not to trigger archive restore.

## Risks

### Risk 1: Raw SCAN count and query count are not apples-to-apples
**Impact:** Companion keys (`::field` capped lists) or type-mismatched keys inflate the hash count,
producing false-positive drift ERRORs on every boot.
**Mitigation:** Count only Redis `hash`-type keys of the base-key shape (no `::`); add a unit test with a
capped-list key present asserting it is excluded. Default tolerance `0` but env-overridable to absorb any
residual off-by-one while the count logic is hardened.

### Risk 2: Drift ERROR is mistaken for benign orphan-index noise and filtered/ignored
**Impact:** The loud signal gets swallowed by `drop_orphan_noise` or dismissed as known noise, defeating
the purpose.
**Mitigation:** Use a distinct, greppable message prefix (`[index-drift] AgentSession`); verify against
`monitoring/sentry_config.py::drop_orphan_noise` that it is NOT filtered; assert non-suppression in a test.

### Risk 3: Self-heal would open the very non-atomic window candidate 13 flags — so this plan does NOT self-heal
**Impact (avoided):** Calling `repair_indexes()` on drift briefly empties the class set (issue #1720),
transiently worsening the desync for concurrent readers — and the doctor path could fire it any time
`python -m tools.doctor` runs while the worker/dashboard are serving, reproducing the exact silent-empty
incident this plan exists to detect.
**Resolution (this plan is DETECT-ONLY):** The guard never calls `repair_indexes()`. Repair (and its #1720
window) is owned exclusively by candidate 13 (`session-recovery-observation-audit`, atomic index repair).
This guard's sole job is to SURFACE drift loudly; no writer path, no class-set mutation, safe to run from the
worker startup and the read-only doctor check alike.

## Race Conditions

### Race 1: Drift check races a concurrent save/index update
**Location:** `agent/session_archive.py` (or `agent/index_drift.py`) reconcile function; `worker/__main__.py` startup.
**Trigger:** A session is saved (hash written, index updated via Lua) between the SCAN and the `query.all()`.
**Data prerequisite:** SCAN snapshot and query snapshot are taken at slightly different instants.
**State prerequisite:** For a FALSE drift to fire, a hash must exist in the SCAN window but its index write not
yet be visible to `query.all()`.
**Mitigation:** popoto 1.8.0 index maintenance is atomic per save (Lua), so a committed hash has a committed
index entry — the window is sub-millisecond. Run the startup guard AFTER Step 2b (quiescent, pre-serve). For the
doctor check, a one-shot transient is acceptable (it is a diagnostic, re-runnable). Because the guard is
detect-only, re-running it (or the next boot) collapses a true transient without any mutation. Tolerance is
env-tunable if a specific environment proves noisy (but see the footgun warning in Technical Approach).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2083] Auditing or removing the popoto-1.8.0 descriptor-pollution / index-race defensive
  code in `models/agent_session.py` and `models/session_lifecycle.py` — owned by the descriptor-pollution audit.
- [SEPARATE-SLUG session-recovery-observation-audit] Making popoto index repair globally atomic (self-heal on
  drift) and removing the duplicate daily cleanup registration — this is candidate 13, tracked in
  `docs/plans/session-recovery-observation-audit.md` (that plan carries no GitHub tracking issue; reference it by
  file path). This plan is DETECT-ONLY and defers ALL repair to that slug. (#2086 is THIS plan's own tracking
  issue, not the sibling's.)
- [EXTERNAL] Ensuring every reader process across the fleet runs popoto `>=1.8.0` (the mixed-version root cause
  of the original crash) — deploy hygiene handled by `/update`, not code in this plan.

## Update System

No update system changes required — this feature is purely internal (worker startup guard + doctor check +
one reconciliation function). No new dependencies, no config files to propagate, no migration. The popoto
`>=1.8.0` pin already exists in `pyproject.toml` and is deployed fleet-wide by `/update`.

## Agent Integration

No agent integration required — this is a bridge/worker-internal reliability change plus a `python -m tools.doctor`
check. `tools.doctor` is already an agent-reachable CLI entry point; the new check surfaces automatically in its
existing output. No new MCP surface, no `.mcp.json` change, no bridge import.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/agentsession-index-drift-detection.md` describing the silent-empty failure mode,
  the reconcile function (detect-only), the worker-startup guard, the doctor check, and the env-tunable
  tolerance constant — WITH an explicit warning that any nonzero `AGENTSESSION_INDEX_DRIFT_TOLERANCE`
  suppresses the exact silent-empty incident class the guard exists to catch.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Cross-link from `docs/features/session-lifecycle.md` (the class-set-empty window / #1720 note) to the new detector.

### Inline Documentation
- [ ] Docstring on the reconcile function stating what "drift" means and why it is loud.
- [ ] Grain-of-salt comment on the tolerance constant marking it provisional/tunable.

## Success Criteria

- [ ] A reconcile function in `agent/index_drift.py` returns `(hash_count, queryable_count, drifted, truncated)`
  for AgentSession using a bounded SCAN; it does NOT call `repair_indexes()` (detect-only).
- [ ] Worker startup runs the guard after Step 2b; on drift it logs ERROR + Sentry (both counts), non-fatally.
- [ ] `query.all()` raising is caught inside reconcile and surfaced as loud ERROR + Sentry (asserted independently
  of any outer try/except).
- [ ] A truncated SCAN (`cursor != 0` at the cap) yields `truncated=True` + a "scan incomplete" WARNING and does
  NOT fire a drift ERROR from the partial count (asserted in a test).
- [ ] `python -m tools.doctor` includes an AgentSession index-drift check that FAILs (with both counts + fix hint)
  when hashes exist that `query.all()` cannot see.
- [ ] The drift message prefix is NOT suppressed by `drop_orphan_noise` (asserted in a test).
- [ ] A capped-list companion key does not inflate the hash count (asserted in a test).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `worker/__main__.py` references the reconcile function and `tools/doctor.py` registers the check.

## Team Orchestration

### Team Members

- **Builder (drift-detector)**
  - Name: drift-builder
  - Role: reconcile function + worker-startup guard + doctor check + tests
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Reviewer (redis-correctness)**
  - Name: redis-reviewer
  - Role: verify SCAN-vs-query count is apples-to-apples, self-heal boundary is safe, no collision with #2083 files
  - Agent Type: code-reviewer
  - Resume: true

- **Validator**
  - Name: drift-validator
  - Role: verify success criteria, run checks
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Uses Tier 1 `builder` / `code-reviewer` / `validator`. For the builder, paste the Redis/Popoto rules from
`DOMAIN_FRAMING.md` into the task (never raw Redis writes on Popoto keys; reads via bounded SCAN are fine for
diagnostics but must not mutate).

## Step by Step Tasks

### 1. Reconcile function
- **Task ID**: build-reconcile
- **Depends On**: none
- **Validates**: tests/unit/test_index_drift.py (create)
- **Informed By**: Freshness Check (anchor primitive `_redis_has_agentsession_keys`)
- **Assigned To**: drift-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/index_drift.py`; add reconcile function returning `(hash_count, queryable_count, drifted, truncated)`
  using bounded SCAN counting only `hash`-type base keys; exclude `::` companion keys. Import SCAN cap constants
  from `agent/session_archive.py`.
- Wrap `len(AgentSession.query.all())` in reconcile's own try/except → on raise, log loud ERROR + Sentry and return
  a drifted result (surfacing must not depend on any outer caller).
- Track exhaustive vs capped SCAN exit → on `cursor != 0` return `truncated=True` + "scan incomplete" WARNING and
  skip the drift comparison.
- Add named env-overridable tolerance constant (default `0`) with grain-of-salt comment.
- Unit tests: equal counts (no drift), hash>query (drift), capped-list key excluded, bounded-SCAN truncation
  (`truncated=True`, no false drift), `query.all()` raising surfaces loudly.

### 2. Worker startup guard
- **Task ID**: build-worker-guard
- **Depends On**: build-reconcile
- **Validates**: tests/unit/test_worker_entry.py
- **Assigned To**: drift-builder
- **Agent Type**: builder
- **Parallel**: false
- Invoke reconcile after Step 2b, wrapped in try/except (non-fatal, last-resort net for detector bugs only); on
  drift ERROR + Sentry (both counts). NO `repair_indexes()` call (detect-only).
- Test: drift at boot logs ERROR and does not crash startup; a detector-bug exception logs warning only; the
  `query.all()`-raises path still fires the loud ERROR from inside reconcile (not swallowed by this try/except).

### 3. Doctor check
- **Task ID**: build-doctor-check
- **Depends On**: build-reconcile
- **Validates**: tests/unit/test_doctor.py
- **Assigned To**: drift-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_check_agentsession_index_drift`, register in `get_checks` Services group; FAIL renders both counts + fix hint.
- Test: PASS on equal counts, FAIL on drift, exception yields failing CheckResult not a crashed run.

### 4. Review
- **Task ID**: review-redis
- **Depends On**: build-worker-guard, build-doctor-check
- **Assigned To**: redis-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify count is apples-to-apples, guard is detect-only (NO `repair_indexes()` call anywhere), the
  `query.all()`-raises path surfaces from inside reconcile, truncated SCAN is not compared, message prefix not
  suppressed by `drop_orphan_noise`, and NO edits to #2083-owned files.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: review-redis
- **Assigned To**: drift-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agentsession-index-drift-detection.md`, add README index entry, cross-link session-lifecycle.md.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: drift-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all checks; verify success criteria incl. docs; final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_doctor.py tests/unit/test_worker_entry.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Doctor check registered | `grep -c "_check_agentsession_index_drift" tools/doctor.py` | output > 1 |
| Worker guard wired | `grep -c "reconcile" worker/__main__.py` | output > 0 |
| No #2083 file collision | `git diff --name-only main | grep -c "session_lifecycle.py"` | match count == 0 |

## Critique Results

War room run 2026-07-15 (Risk & Robustness, Scope & Value, History & Consistency, FULL depth). All findings
resolved in-plan; verdict flipped NEEDS REVISION → READY TO BUILD.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER (3-critic converge) | Risk/History/Scope | Automatic `repair_indexes()` self-heal reopens the #1720 window (doctor path could fire it while serving) and is scope creep on a surface-only plan | Plan is now DETECT-ONLY: removed self-heal + `healed` field from Solution, Data Flow, Success Criteria, tasks; repair deferred to candidate 13 | Resolves Open Question #1 → detect-only |
| BLOCKER | Risk/Operator | Worker-guard `try/except` swallowed the `query.all()`-raises case into a silent WARNING | Reconcile now catches `query.all()` raises internally and fires the loud ERROR + Sentry itself; outer worker try/except is a last-resort net for detector bugs only | Surfacing never depends on outer caller |
| CONCERN | Risk/Adversary | Bounded SCAN can truncate → partial `hash_count` misclassified vs unbounded `query.all()` | Added `truncated` return state: on `cursor != 0` at the cap, WARN "scan incomplete" and skip drift comparison | Third state; no false "no drift" |
| CONCERN | History/Consistency | No-Go entry mislabeled #2086 (this plan's own tracking issue) as the sibling's | Rewrote the No-Go to reference `docs/plans/session-recovery-observation-audit.md` by file path (it has no GitHub issue); noted #2086 is THIS plan's issue | Labeling fix |
| NIT | Scope/Simplifier | Env-tunable tolerance is a footgun on a should-always-be-0 invariant | Kept named env-overridable constant (magic-numbers convention) default `0`, but feature doc must warn any nonzero value suppresses the incident class | Resolves Open Question #3 → default 0 |
| RESOLVED | — | Open Question #2: reconcile module home | New dedicated `agent/index_drift.py` (cohesion; imports SCAN caps from `session_archive.py`) | Resolves Open Question #2 |

---

## Open Questions

All three resolved in critique (2026-07-15) so the plan can proceed to build:

1. **Self-heal on drift, or detect-only?** → **RESOLVED: DETECT-ONLY.** The guard never calls
   `repair_indexes()`; automatic self-heal would reopen the #1720 class-set-empty window (and the doctor path
   could fire it while the worker/dashboard are serving, reproducing the very incident this detects). All repair
   is owned by candidate 13 (`session-recovery-observation-audit`, atomic index repair).
2. **Home for the reconcile function** → **RESOLVED: new dedicated `agent/index_drift.py`** for cohesion; it
   imports the SCAN cap constants from `agent/session_archive.py` (no copy-paste, `session_archive.py` stays cohesive).
3. **Tolerance default** → **RESOLVED: `0`**, exposed as the named env-overridable constant
   `AGENTSESSION_INDEX_DRIFT_TOLERANCE` (magic-numbers convention) with a feature-doc warning that any nonzero
   value suppresses the exact silent-empty incident class this guard catches. Capped-list exclusion test is the guardrail.
