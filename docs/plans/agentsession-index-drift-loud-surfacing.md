---
status: Planning
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
5. **Guard (new):** raw bounded SCAN counts `AgentSession:*` hashes → compare to `len(query.all())`.
   Divergence → log ERROR + Sentry capture (both counts) → attempt `repair_indexes()` self-heal →
   re-count → if still divergent, ERROR persists as the loud signal.

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
  `(hash_count, queryable_count, drifted: bool)` for AgentSession, using a bounded SCAN for the
  raw hash count and `len(AgentSession.query.all())` for the queryable count. Lives beside the
  existing `_redis_has_agentsession_keys` scan primitive in `agent/session_archive.py` (or a small
  new `agent/index_drift.py` if that keeps `session_archive.py` cohesive — dev's call at build).
- **Loud surfacing** — when drifted, `logger.error(...)` with both counts and a stable message
  prefix, plus `sentry_sdk.capture_message(...)` at `error` level. The message prefix is distinct
  from the benign orphan-noise that `drop_orphan_noise` filters, so it is NOT suppressed.
- **Self-heal attempt** — on drift, call `AgentSession.repair_indexes()` once, then re-count; the
  ERROR/Sentry fires with a `healed=True|False` field so a transient desync self-resolves quietly-ish
  (still logged) while a persistent one stays loud.
- **Worker startup guard** — invoked after `worker/__main__.py` Step 2b, non-fatal (wrapped in
  try/except like its neighbors), so a divergence at boot is reported but never crashes the worker.
- **Doctor health check** — `_check_agentsession_index_drift` registered in `get_checks` Services
  group, returning a failing `CheckResult` (with both counts and a fix hint) when drift is present.

### Flow

Worker boot → Step 1/2/2b index repair → **reconcile(AgentSession)** → counts match? →
if yes: continue silently → if no: ERROR + Sentry + one `repair_indexes()` → re-count →
still diverged? ERROR stays → worker continues serving (non-fatal).

`python -m tools.doctor` → Services checks → **_check_agentsession_index_drift** →
PASS (counts equal) or FAIL ("N hashes, M queryable — index desync; run repair_indexes").

### Technical Approach

- **Raw hash count must exclude non-hash companion keys.** `match="AgentSession*"` also matches
  capped-list keys (`AgentSession:<key>::<field>`) and any `AgentSession`-prefixed non-hash keys.
  Count only keys of Redis type `hash` whose name has the base-key shape (no `::`), OR filter by
  `TYPE == hash`. Reuse the bounded-iteration cap (`_SCAN_MAX_ITERATIONS`) so a corrupt keyspace
  cannot hang the guard. This is the one correctness-sensitive detail — get it exactly right so the
  count is apples-to-apples with `query.all()`.
- **Thresholds are named, env-overridable constants** with a grain-of-salt comment marking them
  provisional (per the magic-numbers convention). Divergence tolerance defaults to `0` (any
  hash_count > queryable_count is drift), but expose `AGENTSESSION_INDEX_DRIFT_TOLERANCE` so a noisy
  environment can widen it without a code change. `hash_count < queryable_count` is a different
  anomaly (stale index members → already handled by `clean_indexes`); the guard reports it too but
  tags it distinctly.
- **Single source of truth:** worker startup and doctor both import the same reconcile function; no
  copy-paste of the SCAN logic.
- **Sentry level `error`**, message prefixed e.g. `[index-drift] AgentSession` so it is greppable and
  not caught by `drop_orphan_noise` (verify against `monitoring/sentry_config.py`).
- **Do NOT** modify decode-level behavior, `__getattribute__`/`__setattr__`, the coercion sets, or
  the `srem`-loop in `models/agent_session.py` / `models/session_lifecycle.py` — that surface is
  owned by #2083.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The worker-startup guard is wrapped in try/except (like its Step 2/2b neighbors) — add a test
  asserting a raised exception inside reconcile is logged as a warning and does NOT crash startup.
- [ ] The doctor check is wrapped by `run_checks`' per-check try/except — add a test that a reconcile
  exception yields a failing `CheckResult` rather than aborting the whole doctor run.
- [ ] No `except Exception: pass` introduced — every handler logs.

### Empty/Invalid Input Handling
- [ ] Empty keyspace (0 hashes, 0 queryable) → `drifted=False`, no ERROR, no Sentry.
- [ ] Corrupt/huge keyspace → bounded SCAN terminates at `_SCAN_MAX_ITERATIONS`; test asserts the
  guard returns rather than hanging.
- [ ] `query.all()` raising (genuinely corrupt hash) → reconcile surfaces the raise as loud ERROR,
  not a swallowed empty.

### Error State Rendering
- [ ] The doctor FAIL `CheckResult` renders both counts and an actionable fix string.
- [ ] The Sentry capture payload includes `hash_count`, `queryable_count`, `healed`.

## Test Impact

- [ ] `tests/unit/test_worker_entry.py` — UPDATE: add a case asserting the new startup reconcile
  step runs after Step 2b and is non-fatal on exception (existing startup-sequence tests must still pass).
- [ ] `tests/unit/test_doctor.py` — UPDATE: add a case for `_check_agentsession_index_drift`
  (PASS when counts equal, FAIL with both counts when drifted); confirm it is registered in `get_checks`.
- [ ] `tests/unit/test_session_archive.py` — UPDATE only if the reconcile function lands in
  `agent/session_archive.py` (add unit coverage for the SCAN-vs-query count); if it lands in a new
  `agent/index_drift.py`, add `tests/unit/test_index_drift.py` (REPLACE this row with a create).
- [ ] No existing test asserts the current silent-empty behavior, so nothing needs DELETE — the change
  is additive detection plus a self-heal attempt.

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

### Risk 3: Self-heal `repair_indexes()` opens the very non-atomic window candidate 13 flags
**Impact:** Calling `repair_indexes()` on drift briefly empties the class set (issue #1720), transiently
worsening the desync for concurrent readers.
**Mitigation:** Only attempt self-heal at worker startup (before sessions are actively served) and in the
doctor check (read-heavy, low-concurrency). Do NOT wire self-heal into the hot 300s reflection path. Document
this boundary; the detector alone (no heal) is the safe default if concurrency is a concern.

## Race Conditions

### Race 1: Drift check races a concurrent save/index update
**Location:** `agent/session_archive.py` (or `agent/index_drift.py`) reconcile function; `worker/__main__.py` startup.
**Trigger:** A session is saved (hash written, index updated via Lua) between the SCAN and the `query.all()`.
**Data prerequisite:** SCAN snapshot and query snapshot are taken at slightly different instants.
**State prerequisite:** For a FALSE drift to fire, a hash must exist in the SCAN window but its index write not
yet be visible to `query.all()`.
**Mitigation:** popoto 1.8.0 index maintenance is atomic per save (Lua), so a committed hash has a committed
index entry — the window is sub-millisecond. Run the startup guard AFTER Step 2b (quiescent, pre-serve). For the
doctor check, a one-shot transient is acceptable (it is a diagnostic, re-runnable); the self-heal + re-count
collapses a true transient. Tolerance is env-tunable if a specific environment proves noisy.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2083] Auditing or removing the popoto-1.8.0 descriptor-pollution / index-race defensive
  code in `models/agent_session.py` and `models/session_lifecycle.py` — owned by the descriptor-pollution audit.
- [SEPARATE-SLUG #2086] Making popoto index repair globally atomic and removing the duplicate daily cleanup
  registration — this is candidate 13 of the session-recovery-observation-audit; the umbrella issue is #2086's
  sibling work, tracked in `docs/plans/session-recovery-observation-audit.md`, not this slug.
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
  the reconcile function, the worker-startup guard, the doctor check, and the env-tunable tolerance constant.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Cross-link from `docs/features/session-lifecycle.md` (the class-set-empty window / #1720 note) to the new detector.

### Inline Documentation
- [ ] Docstring on the reconcile function stating what "drift" means and why it is loud.
- [ ] Grain-of-salt comment on the tolerance constant marking it provisional/tunable.

## Success Criteria

- [ ] A reconcile function returns `(hash_count, queryable_count, drifted)` for AgentSession using a bounded SCAN.
- [ ] Worker startup runs the guard after Step 2b; on drift it logs ERROR + Sentry (both counts) and attempts one
  `repair_indexes()` self-heal, non-fatally.
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
- **Validates**: tests/unit/test_index_drift.py (create) or tests/unit/test_session_archive.py
- **Informed By**: Freshness Check (anchor primitive `_redis_has_agentsession_keys`)
- **Assigned To**: drift-builder
- **Agent Type**: builder
- **Parallel**: false
- Add reconcile function returning `(hash_count, queryable_count, drifted)` using bounded SCAN counting only
  `hash`-type base keys; exclude `::` companion keys.
- Add named env-overridable tolerance constant with grain-of-salt comment.
- Unit tests: equal counts (no drift), hash>query (drift), capped-list key excluded, bounded-SCAN termination,
  `query.all()` raising surfaces loudly.

### 2. Worker startup guard
- **Task ID**: build-worker-guard
- **Depends On**: build-reconcile
- **Validates**: tests/unit/test_worker_entry.py
- **Assigned To**: drift-builder
- **Agent Type**: builder
- **Parallel**: false
- Invoke reconcile after Step 2b, wrapped in try/except (non-fatal); on drift ERROR + Sentry + one repair_indexes()
  self-heal + re-count with `healed` flag.
- Test: drift at boot logs ERROR and does not crash startup; reconcile exception logs warning only.

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
- Verify count is apples-to-apples, self-heal confined to startup/doctor, message prefix not suppressed by
  `drop_orphan_noise`, and NO edits to #2083-owned files.

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

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Self-heal on drift, or detect-only?** The plan attempts one `repair_indexes()` at startup/doctor
   (safe, low-concurrency) but explicitly keeps it out of the hot 300s reflection path to avoid re-opening
   the #1720 class-set-empty window. Confirm detect-plus-startup-heal is the right boundary, or prefer
   detect-only and let candidate 13 own all repair.
2. **Home for the reconcile function** — extend `agent/session_archive.py` (next to the existing SCAN
   primitive) or a new `agent/index_drift.py`? Leaning toward a small dedicated module for cohesion; confirm.
3. **Tolerance default** — ship with `0` (any hash>query is drift) and env-tunable, or start permissive to
   avoid boot-time noise while the count logic is proven? Leaning `0` with the capped-list exclusion test as
   the guardrail.
