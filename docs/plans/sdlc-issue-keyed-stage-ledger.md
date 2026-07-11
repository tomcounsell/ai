---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2012
last_comment_id: 4940858561
revision_applied: true
---

# SDLC Issue-Keyed Stage Ledger

## Problem

The SDLC pipeline stores its durable ledger — stage states, verdicts, dispatch
history, and the resolved PR number — as a JSON blob on the **executor**
(`AgentSession.stage_states`). The AgentSession is ephemeral: it crashes,
completes, gets killed, gets superseded, or gets taken over. The ledger is
durable: it must outlive every one of those events. Storing the ledger on the
executor makes every executor lifecycle event a potential state-loss event.

**Current behavior:**
PR #2008 (issue #1997) is functionally complete and `MERGEABLE`/`CLEAN`, yet the
pipeline cannot advance it to MERGE. The original driver session
(`sdlc-local-1997`) went terminal; a takeover session with a foreign slug
(`dev-7bd4cf82`) completed the rework. Every stage/verdict marker the takeover
wrote keyed on `--issue-number 1997` resolved no live issue-owner session
(`find_session_by_issue()` fails all three passes for a foreign-slug session),
so `stage-marker`'s `PRESENT_NO_SESSION` degradation quietly no-op'd them
(exit 0, no write). Result: `sdlc-tool stage-query --issue-number 1997` returns
an empty stage store, the `/do-merge` gate reads no REVIEW verdict and no
`pr_number`, and the pipeline deadlocks on missing bookkeeping — not on any code
defect.

**Desired outcome:**
Pipeline state survives a driver→takeover handoff because it never lived on the
executor in the first place. State is keyed by `(target_repo, issue_number)` —
the entity the pipeline is *about*. A session holds the #2003 run_id **lease** to
*write* that issue-keyed ledger; takeover is simply acquiring the lease, and the
ledger never moves because it never lived on the session. `/do-merge` reads the
issue-keyed REVIEW verdict and `pr_number` directly and makes a correct
decision. PR #2008 / issue #1997 is unblocked and merged (immediate runbook,
independent of the systemic fix).

## Freshness Check

**Baseline commit:** `6ddc8bcb` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-10T16:12:34Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/pipeline_state.py:324-383` — ledger persisted as JSON blob on the
  session (`_save()` → `self.session.stage_states = json.dumps(data); self.session.save()`).
  Still holds; the write is at ~line 376, `_save` at ~324.
- `tools/_sdlc_utils.py:99` — `find_session_by_issue()` three-pass scan
  (issue_url suffix → `sdlc-local-{N}` → `message_text` regex). Still holds; the
  three-pass docstring is at lines 104-137, terminal-exclusion at 137+.
- `tools/sdlc_stage_marker.py:40-47` — tri-state degradation; `PRESENT_NO_SESSION`
  is QUIET by contract (degraded marker, exit 0). Still holds.
- `agent/pipeline_state.py:854` — `derive_from_durable_signals()` is slug-keyed
  (bails when `session.slug` is absent, ~line 907). Still holds; confirms the
  cold-Redis fallback breaks on the same takeover scenario.

**Cited sibling issues/PRs re-checked:**
- #2003 (PR #2010, "run_id ownership + merge-predicate enforcement") — CLOSED/merged.
  Its cold-state gate is one of the two combining causes of #2012.
- #2004 (PR #2011, "resilience hygiene sweep, loud degradation") — CLOSED/merged.
  Landed the tri-state degradation contract in `stage-marker`.
- #1954 (issue-ownership lock) — CLOSED. The lock hands off ownership but not the ledger.
- #1671, #1735, #1916, #1558 — all CLOSED; each patched the resolution/enforcement
  layer while leaving session-keyed storage untouched (see Prior Art).

**Commits on main since issue was filed (touching referenced files):**
- `2f324bff` (#2003 / PR #2010) — changed the run_id lease + cold-state gate; **changed root cause**
  (the cold-state gate now refuses takeover writes rather than re-homing them).
- `ffed9ba0` (#2004 / PR #2011) — landed the loud/quiet degradation contract in `stage-marker`;
  partially relevant (defines the quiet `PRESENT_NO_SESSION` path).

Both are already accounted for in the owner's root-cause review; the defect
reproduces on current main (issue #1997 stage-query still empty, PR #2008 still open).

**Active plans in `docs/plans/` overlapping this area:** none blocking.
`resilience-simplification-three-tier.md` (draft, no tracking issue) and
`agent-session-outcome-verification.md` (issue #1267) mention `stage_states` in
passing but neither touches ledger keying. Coordinate lightly, no dependency.

**Notes:** Line numbers drifted slightly under #2003/#2004; claims hold verbatim.
The corrected anchors above are the ones to use in Technical Approach.

## Prior Art

Every prior fix patched the resolution/enforcement layer while leaving the
session-keyed storage untouched. This is the pattern the issue-keyed ledger breaks.

- **#1558**: sdlc-tool state subcommands silently no-op'd outside `/sdlc` — added
  deterministic `sdlc-local-{N}` ids. Closed. Made resolution more deterministic
  but kept state on the session.
- **#1671 / #1731**: forked stage skills wrote SDLC state to the wrong session —
  read/write convergence. Closed. Same keying, better routing.
- **#1735**: loud-fail guard when no owning session resolves. Closed. Later softened
  to the quiet `PRESENT_NO_SESSION` case by #2004.
- **#1916**: predecessor backfill for a fresh pipeline's first marker. Closed.
  Operates on the session-keyed store.
- **#1915 / #1954**: terminal-session exclusion + issue-ownership lock. Closed.
  The lock hands off ownership; nothing hands off the ledger — a direct cause of #2012.
- **#2003 (PR #2010)**: run_id lease + cold-state gate. Closed/merged. The gate
  refuses takeover writes rather than re-homing them — the second combining cause.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1558 | Deterministic `sdlc-local-{N}` ids | Reduced ambiguity but state still lives on the session; a foreign-slug takeover matches no id |
| #1671/#1731 | Route writes to the owning session | Correct target *when a session resolves*; a terminal driver resolves to nothing |
| #1735 | Loud-fail on no-session | Correct signal, but later reasoned away by the non-`ai`-repo case (#2004) |
| #1915/#1954 | Terminal exclusion + ownership lock | Makes the state store unreachable-by-design the instant the driver goes terminal, while state still sits on the dead record |
| #2003 | run_id lease + cold-state gate | Ensures takeover writes are *refused* rather than re-homed — quiet exit 0 |

**Root cause pattern:** The durable ledger lives on an ephemeral, session-keyed
record, and work identity (issue number) is *inferred* by a heuristic resolver
rather than *declared*. Every fix improved the inference; none moved the ledger
to the entity it belongs to (the issue). #2012 is one instance of that class,
not the class itself.

## Architectural Impact

- **New dependencies**: A new durable Popoto model (working name `PipelineLedger`)
  keyed by `(target_repo, issue_number)`. No external services.
- **Lease payload change**: `touch_issue_lock`'s JSON payload gains a `target_repo`
  field, resolved at lease-acquire in `_acquire_run_lock_and_bind`. This is the
  single authoritative source of the ledger key's repo component; writers read it from
  the lease and never re-resolve. **Self-healing renewal (BLOCKER round-2 fix):** the
  same-owner renewal branch (`models/session_lifecycle.py:995-996`) today is a bare
  `_R.expire(key, ttl)` that never rewrites the payload — so a lock acquired before this
  deploy that merely keeps renewing would *never* gain `target_repo`, and under the
  hard-fail design every write would fail until the TTL lapsed and a fresh re-acquire ran
  (re-deadlocking in-flight issues across cutover). The fix rewrites the payload on
  renewal: same-owner renewal re-`SET`s the payload with the pinned `target_repo`
  (re-resolved from the same authoritative env the caller uses at acquire), so any legacy
  or pre-deploy payload self-heals on its next renewal tick — no separate lock backfill
  needed. A payload still lacking `target_repo` before its first post-deploy renewal is
  handled by the writer's observable degradation path (see Risk 5).
- **Interface changes**: `PipelineStateMachine` gains an issue-keyed construction
  path (e.g. `PipelineStateMachine.for_issue(target_repo, issue_number)`) alongside
  or replacing the session-keyed `__init__(session)`. All `sdlc-tool` writers
  (`stage-marker`, `verdict`, `meta-set`, `dispatch`) and readers (`stage-query`,
  `verdict get`, `next-skill`) re-point at the issue-keyed record.
- **Coupling**: *Decreases* coupling between the pipeline ledger and the executor
  lifecycle. `find_session_by_issue()` demotes from state-integrity infrastructure to a
  routing/ownership concern (`sdlc_session_ensure.py`, `sdlc_next_skill.py`,
  `sdlc_dispatch.py`) plus the reader's cold-path session fallback in
  `sdlc_stage_query.py`. It is NOT a dashboard caller (`grep find_session_by_issue ui/`
  is empty).
- **Data ownership**: The ledger's owner becomes the issue (per repo). The run_id
  lease (#1954/#2003) becomes the *write authority* over that ledger — unifying
  ownership and write authority, which answers open-question Q4.
- **Reversibility (honest, round-2 correction):** One-directional. The migration
  backfills live in-flight `stage_states` into the ledger but writes nothing back; after
  cutover, every stage/verdict write lands on the ledger ONLY and the session
  `stage_states` is frozen at migration time. Consequences of `git revert`:
  - **Reverted before any post-cutover ledger write** (immediate rollback, low-activity
    window): clean. Readers fall back to the still-current `stage_states`; nothing was
    lost because nothing new was written to the ledger.
  - **Reverted after post-cutover ledger writes**: **lossy.** Progress written to the
    ledger after cutover is invisible to the reverted session-keyed readers, because
    `stage_states` is stale. Recovering that progress requires a reverse-backfill
    (ledger → `stage_states`) — which this plan does NOT build (dropping the mirror is
    exactly why post-cutover state lives only on the ledger; see Risk 2). Rollback after
    real pipeline activity is therefore a manual, lossy operation, not a clean `git
    revert`. Own this: the safe rollback window is "before the first post-cutover write."
    Mitigate operationally by cutting over during a quiet window and validating fast, not
    by pretending the revert is symmetric.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (storage-model choice, migration strategy, mirror-retention decision)
- Review rounds: 2+ (this touches the SDLC substrate; adversarial critique + code review)

This is a substrate refactor with a migration and many call-site re-points. The
bottleneck is correctness of the migration and the write-lease semantics, not
coding volume.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "import redis,os; redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379')).ping()"` | Popoto model + lease live here |
| Popoto available | `python -c "import popoto"` | New ledger model uses the ORM |
| sdlc-tool on PATH | `sdlc-tool stage-query --issue-number 2012 >/dev/null` | Writers/readers being re-pointed |

## Spike Results

Spikes were resolved by code-read during recon (no prototypes dispatched — the
facts are directly observable in the source).

### spike-1: Is the ledger truly session-keyed with no issue-keyed store today?
- **Assumption**: "All pipeline state persists on `AgentSession.stage_states`; there is no issue-keyed record."
- **Method**: code-read
- **Finding**: Confirmed for the storage half; **corrected** for the keying half.
  `PipelineStateMachine._save()` writes only `self.session.stage_states` (session-keyed) —
  confirmed. But the claim that `_meta._resolved_target_repo` is "already computed at
  every write site" is **false**. `_resolve_target_repo()` is called in exactly ONE
  place: the READER, `tools/sdlc_stage_query.py:412` (inside `_compute_meta`). The four
  writers — `tools/sdlc_stage_marker.py`, `tools/sdlc_verdict.py`, `tools/sdlc_meta_set.py`,
  `tools/sdlc_dispatch.py` — reference no repo resolver at all. A `(target_repo, issue)`
  composite key is therefore NOT derivable at write sites for free; naively re-pointing each
  writer to call `_resolve_target_repo()` would shell out to `gh repo view` (10s timeout,
  network/auth-dependent) per write, and a `None` result would mint a malformed `None:{issue}`
  key routing writes to a phantom record — silently reproducing #2012 with a new cause.
- **Confidence**: high
- **Impact on plan**: The key must NOT be re-resolved per write. Resolve `target_repo`
  ONCE at lease-acquire time (in `session-ensure`, where the env is authoritative — see
  Technical Approach → Keying), pin it onto the run_id lease payload, and have writers
  read it from the lease. The fix introduces a new record; a migration is required to
  lift live state.

### spike-2: Does the run_id lease already gate by issue number?
- **Assumption**: "The #2003 run_id lock is keyed by issue and can serve as the write lease."
- **Method**: code-read (`tools/sdlc_session_ensure.py`, `touch_issue_lock`)
- **Finding**: Confirmed. `session-ensure` contests a per-issue lock (`SET NX EX`)
  carrying the run_id and mirrors the winner to `AgentSession.active_run_id`. The
  lock's JSON payload (`models/session_lifecycle.py:967`, `touch_issue_lock`) today
  holds `{run_id, session_id, pid, hostname}` at key `session:issuelock:{issue_number}`.
  The lock is already issue-keyed; only the *ledger* it guards is session-keyed.
- **Confidence**: high
- **Impact on plan**: No new lock is needed. Make the existing lease the write
  authority over the issue-keyed ledger (Q4 unification is a rewire, not a rebuild).
  The lock payload is also the natural home for the pinned `target_repo` (BLOCKER fix):
  `_acquire_run_lock_and_bind()` resolves `target_repo` once when it wins the lock and
  passes it into `touch_issue_lock`, which persists it in the payload for every
  subsequent writer to read — no per-write resolution, no phantom `None:{issue}` key.

### spike-3: Does the cold-Redis fallback survive a takeover?
- **Assumption**: "`derive_from_durable_signals()` can reconstruct state for a foreign-slug takeover."
- **Method**: code-read (`agent/pipeline_state.py:854-1030`)
- **Finding**: It cannot. It is slug-keyed and bails when `session.slug` is
  absent/foreign. It breaks on the exact #2012 scenario.
- **Confidence**: high
- **Impact on plan**: Keep `derive_from_durable_signals()` only as the
  empty-Redis cold-start fallback; it is not the fix. Do not rely on it for takeover.

## Data Flow

1. **Entry point**: A stage skill (e.g. `/do-pr-review`) calls
   `sdlc-tool verdict record --stage REVIEW --issue-number N --run-id R`.
2. **Lease check + repo pin**: The writer confirms run_id `R` holds the write lease
   for issue `N` (live lock owner match, or free lock + `active_run_id` mirror match)
   AND reads the lease-pinned `target_repo` from the lock payload. The composite ledger
   key `(target_repo, N)` is assembled from that pinned value — never re-resolved via
   `gh repo view`. If the lease is held but carries no `target_repo` (legacy/expired),
   the writer degrades *observably* (stderr diagnostic + non-zero exit) rather than
   minting a `None:{N}` phantom key.
3. **Ledger write (new)**: On a valid lease with a pinned `target_repo`, the verdict +
   `pr_number` are written to the issue-keyed `PipelineLedger[(target_repo, N)]` record
   — no session resolution required. On an invalid/absent lease, degrade *observably*
   (the write-drop condition that previously hid state loss no longer exists, because
   there is no session to fail to resolve).
4. **Ledger read (guarded)**: `/do-merge` gate calls `sdlc-tool stage-query
   --issue-number N`, which reads `PipelineLedger[(target_repo, N)]` directly — resolving
   `target_repo` from the live lease (`touch_issue_lock` peek) when present, else from
   env-first `_resolve_target_repo()` (GH_REPO rung-0, authoritative in the gate's
   context) on a cold read. If that resolution returns `None`, the reader does NOT key
   `(None, N)`; it takes the defined empty-ledger path (reconstruct-or-refuse with an
   actionable reason, per AC #2). On a successful resolution, REVIEW verdict + `pr_number`
   are present regardless of which session (driver or takeover) wrote them.
5. **Output**: MERGE gate's shared predicate evaluates REVIEW freshness against PR
   head and merges, or refuses with an actionable `GATES_FAILED` reason.

## Solution

### Key Elements

- **`PipelineLedger` (new durable record)**: Popoto model keyed by
  `(target_repo, issue_number)`. Holds what `stage_states` holds today: stage
  statuses, `_verdicts`, `_sdlc_dispatches`, `pr_number`, cycle counters.
- **Issue-keyed `PipelineStateMachine`**: A construction path that reads/writes the
  ledger by `(repo, issue)` instead of by session. Same validation
  (`StageStates` Pydantic model), same cross-process merge-on-save protocol.
- **Write lease = run_id lock, with a pinned `target_repo`**: The #1954/#2003 per-issue
  lock becomes the write authority over the ledger. `target_repo` is resolved ONCE when
  the lock is won and pinned into the lock payload; every writer reads it from the lease
  to assemble the `(target_repo, issue)` key — no per-write `gh repo view`. A caller with
  a valid run_id lease may write; takeover = acquiring the lease (and inheriting/refreshing
  the same pinned repo).
- **Readers re-pointed**: `stage-query`, `verdict get`, `next-skill`, and the
  `/do-merge` gate read the issue-keyed ledger directly, keying on the lease-pinned
  `target_repo` when a lease is live, else env-first `_resolve_target_repo()`.
- **Atomic cutover, no mirror**: writers and readers move to the ledger in ONE PR.
  There is no session-side `stage_states` mirror (dropped — it re-manufactures the
  Risk 2 split-brain). The dashboard/steering re-point to the ledger in the same change.
- **Migration**: Backfill live, non-terminal in-flight issues' `stage_states` into
  the new ledger (idempotent; recorded in `data/migrations_completed.json`), keying
  each via the session's lease-pinned `target_repo` and skipping observably when it
  cannot be authoritatively determined.
- **`derive_from_durable_signals` demoted**: retained only as the empty-Redis
  cold-start fallback, not the takeover path.
- **PR #2008 runbook**: an immediate, documented recovery to unblock #1997 now,
  independent of the refactor landing.

### Flow

Stage skill emits `sdlc-tool ... --issue-number N --run-id R` → lease verified for
issue N and its pinned `target_repo` read from the lock payload → ledger record
`PipelineLedger[(target_repo, N)]` written → `/do-merge` reads the same record →
REVIEW verdict + pr_number present → merge decision made.

### Technical Approach

- **Storage**: Introduce `PipelineLedger` as a Popoto model with a composite
  string key `{target_repo}:{issue_number}`.
- **Keying (BLOCKER fix — resolve once, pin on the lease)**: `target_repo` is resolved
  exactly ONCE, at lease-acquire, inside `_acquire_run_lock_and_bind()`
  (`tools/sdlc_session_ensure.py`) — the one place where the process env
  (`GH_REPO`/`SDLC_TARGET_REPO`, set authoritatively by `sdk_client.py`) is trustworthy,
  regardless of a takeover session's foreign slug or cwd. The resolved slug is passed to
  `touch_issue_lock` and persisted in the lock payload
  (`session:issuelock:{issue_number}`, `models/session_lifecycle.py:967`). Writers read
  `target_repo` FROM the lease (a peek of the lock payload) and never call
  `_resolve_target_repo()` themselves — no per-write `gh repo view` (10s timeout), no
  `None:{issue}` phantom key. **Renewal must self-heal, not bare-`EXPIRE` (BLOCKER
  round-2 fix):** the same-owner renewal branch in `touch_issue_lock`
  (`models/session_lifecycle.py:995-996`) currently calls only `_R.expire(key, ttl)`,
  which never rewrites the payload — so a lock acquired before this change that keeps
  renewing would never acquire `target_repo`, and under the hard-fail write design every
  stage write would fail until the TTL lapsed and a fresh re-acquire ran. That would
  re-deadlock in-flight issues across cutover. The renewal branch is changed to re-`SET`
  the payload (preserving `run_id`/`session_id`, re-pinning `target_repo` resolved from
  the same authoritative env the caller uses at acquire) so any legacy/pre-deploy payload
  self-heals on its next renewal tick. A re-acquire after TTL lapse re-resolves under the
  same authoritative env, so the value is stable across the issue's lifetime. This single
  pin also dissolves the writer-vs-writer key divergence (all writers read the one pinned
  value) and the migration mis-key concern (the backfill keys off the same pinned value).
- **State machine**: Add `PipelineStateMachine.for_issue(target_repo, issue_number)`
  that loads/saves the ledger record. Preserve the existing `StageStates` Pydantic
  validation and the `_load_preserved_metadata()` merge-on-save protocol verbatim
  so concurrent writers (different stages, same issue) don't clobber each other.
- **Writers**: `tools/sdlc_stage_marker.py`, `tools/sdlc_verdict.py`,
  `tools/sdlc_meta_set.py`, `tools/sdlc_dispatch.py` switch from
  `find_session_by_issue()` → build/load the ledger by (target_repo, issue), reading
  `target_repo` from the lease payload, and gate the write on the run_id lease. The
  `PRESENT_NO_SESSION` quiet no-op is removed for the issue-keyed path — there is no
  session to fail to resolve. A lease held but missing a pinned `target_repo`
  (legacy/expired payload) is an observable hard-fail (stderr + non-zero exit), never a
  `None:{issue}` write (Risk 5).
- **Readers (with a guarded cold path — BLOCKER round-2 fix)**:
  `tools/sdlc_stage_query.py`, `tools/sdlc_next_skill.py`, and the `/do-merge` gate
  (`docs/sdlc/do-merge.md` predicate) read the ledger directly. The reader resolves the
  key's repo component with the SAME precedence as the writers: **lease-pinned
  `target_repo` first** (a `touch_issue_lock(..., peek=True)` peek — the peek mode already
  exists), falling back to env-first `_resolve_target_repo()` (GH_REPO rung-0) ONLY when
  no live lease exists (cold read after TTL lapse). The round-1 fix pinned the writers but
  left `_compute_meta` (`tools/sdlc_stage_query.py:412`) calling `_resolve_target_repo()`
  unconditionally — the exact env-first heuristic this plan names as the bug. That path
  can return `None`, which would key `PipelineLedger[(None, issue)]`, read an empty ledger,
  and make `/do-merge` refuse a genuinely mergeable PR — #2012 resurrected on the READ
  side. Guard it symmetrically with the writers: **a reader that resolves `target_repo`
  to `None` does NOT read `(None, issue)`** — it yields the defined empty-ledger outcome
  (AC #2: reconstruct via cold-start fallback or refuse with an actionable
  `GATES_FAILED` reason, logged), never a silent stall against a phantom key. When a
  lease is live, reader and writer key identically because both read the one pinned value;
  on the cold path both derive from the same `GH_REPO`, so they agree. This reader guard
  is covered by ACs #5c/#6 and a dedicated test (below), not by AC #5's writer-only grep.
- **`find_session_by_issue()` demotion (round-2 correction)**: its retained callers are
  the routing/ownership path (`tools/sdlc_session_ensure.py`, `tools/sdlc_next_skill.py`,
  `tools/sdlc_dispatch.py`) and the reader's cold-path *session* fallback
  (`tools/sdlc_stage_query.py::_find_session_by_issue`) — NOT the dashboard. The earlier
  claim that it was "retained for a dashboard caller (`ui/data/sdlc.py`)" is false:
  `grep find_session_by_issue ui/` returns zero matches. The dashboard reads
  `session.stage_states` and `PipelineStateMachine(session)` *directly by session*, so it
  is re-pointed to the ledger (below) but never touches `find_session_by_issue`. The
  demotion removes `find_session_by_issue` from state-integrity write paths; it stays for
  routing/ownership and the reader's session fallback.
- **No session-side mirror**: cut over writers, readers, dashboard, and steering to
  the ledger atomically in one PR. The dashboard re-point is real and required — it reads
  the session-keyed `stage_states`/`PipelineStateMachine(session)` today
  (`ui/data/sdlc.py:786-795`, `1146`), which goes stale the moment writes move to the
  ledger. Dropping the mirror avoids re-manufacturing the Risk 2 split-brain (a mirror is
  a second source of truth that can disagree with the ledger).
- **Migration** (`scripts/update/migrations.py`, registered in `MIGRATIONS`):
  for each non-terminal AgentSession carrying a non-empty `stage_states` with a
  resolvable issue number AND an authoritatively-determined `target_repo` (from the
  session's lease-pinned value), write the blob into
  `PipelineLedger[(target_repo, issue)]` if the ledger is empty (idempotent; never
  overwrite a newer ledger). Sessions whose `target_repo` cannot be determined are
  skipped with a logged WARNING — never keyed under `None`. Use ORM methods only —
  no raw Redis ops.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `except Exception` blocks in `tools/sdlc_stage_marker.py`,
  `tools/sdlc_verdict.py`, `agent/pipeline_state.py`, and the new ledger module —
  each must assert observable behavior (logger.warning + degraded marker, or
  non-zero exit for `PRESENT_WRITE_FAILED`). No silent `pass`.
- [ ] Test that a ledger write with an **invalid/absent run_id lease** degrades
  observably (stderr diagnostic + non-zero exit for the wiring-bug case), not the
  old quiet exit-0.
- [ ] Test that a ledger write when the lease carries **no pinned `target_repo`**
  (legacy/expired payload, or resolver → `None`) hard-fails observably and NEVER
  assembles a `None:{issue}` key — assert no `PipelineLedger` record with a `None`
  repo component is ever created (Risk 5, writer side).
- [ ] Test that a **READER** cold path resolving `target_repo` → `None` does NOT read
  `PipelineLedger[(None, issue)]` — it takes the defined empty-ledger outcome
  (reconstruct-or-refuse with an actionable reason), never a silent stall against a
  phantom key (Risk 5, reader side — the BLOCKER round-2 gap AC #5's writer grep misses).
- [ ] Test that `_acquire_run_lock_and_bind` pins `target_repo` into the lock payload
  on acquisition.
- [ ] Test that a **same-owner renewal self-heals** a legacy payload: given a lock whose
  payload lacks `target_repo`, the next renewal re-`SET`s the payload WITH the pinned
  `target_repo` (not a bare `EXPIRE`) — assert the payload gains the field after renewal
  (BLOCKER round-2 fix; `models/session_lifecycle.py:995-996`).

### Empty/Invalid Input Handling
- [ ] Test `for_issue()` with a `(repo, issue)` that has no ledger yet → returns an
  empty-but-valid state machine, first write persists (predecessor backfill intact).
- [ ] Test `stage-query`/`verdict get` on a non-existent ledger → empty `{}` result
  (unchanged CLI contract), never a crash.
- [ ] Malformed ledger JSON → treated as empty with a WARNING, mirroring current
  `stage_states` behavior.

### Error State Rendering
- [ ] `/do-merge` gate with an **empty** ledger but a genuinely reviewed+mergeable
  PR → refuse with an actionable `GATES_FAILED` reason (or reconstruct via the
  cold-start fallback), never a silent stall.
- [ ] Verify the takeover regression test asserts the REVIEW verdict + `pr_number`
  are readable by `/do-merge` after a driver→takeover handoff.

## Test Impact

- [ ] `tests/unit/test_pipeline_state.py` (and any `test_pipeline*`) — UPDATE:
  cover the new `for_issue()` path alongside the session path; assert ledger
  persistence survives session deletion.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: the `PRESENT_NO_SESSION`
  quiet-no-op assertions change for the issue-keyed path; assert observable
  degradation on invalid lease instead.
- [ ] `tests/unit/test_do_merge_docs_gate.py` — UPDATE: gate reads REVIEW verdict +
  `pr_number` from the ledger; keep the docs-gate semantics.
- [ ] Tests asserting `stage-query` / `verdict get` read from the session record —
  UPDATE: re-point expectations at the ledger.
- [ ] `tests/unit/test_session_lifecycle.py` / `tests/unit/test_sdlc_session_ensure.py`
  (whichever covers `touch_issue_lock`/`_acquire_run_lock_and_bind`) — UPDATE: assert the
  lock payload now carries a pinned `target_repo` and that renewal preserves it.
- [ ] Add NEW: `tests/unit/test_pipeline_ledger.py` and a takeover regression test
  (terminal driver + foreign-slug takeover completes → ledger intact, merge-gate reads it),
  plus a Risk 5 test (no-pinned-repo write hard-fails, never mints `None:{issue}`).

A precise per-file audit runs at build start (the exact test module names for the
substrate move once the ledger module path is fixed). The dispositions above are
the known-affected set from the current call-site map.

## Rabbit Holes

- **Rewriting `find_session_by_issue()` from scratch.** It demotes to a
  routing/ownership helper (session-ensure, next-skill, dispatch) plus the reader's
  cold-path session fallback — leave its three passes intact; do not redesign session
  resolution as part of this fix. (It is not a dashboard caller.)
- **Unifying all five identities** (issue_number, session_id, run_id, slug, GitHub
  artifacts) into one scheme. Tempting, but out of scope — only the ledger key
  moves to the issue. The others stay as-is.
- **Building a general state-migration framework** for rehoming state between
  sessions (open-question option (a)). The whole point of issue-keying is that the
  ledger never moves, so no migration-between-sessions machinery is needed — only a
  one-time backfill.
- **Reworking `derive_from_durable_signals()` to be issue-keyed.** Keep it as the
  narrow empty-Redis cold-start fallback; do not expand it.
- **A `--strict` flag on `stage-marker`** (open-question Q3). With issue-keyed
  state, "no session resolves" stops being a write-drop condition, so the flag is
  unnecessary for this failure class. Do not add it.

## Risks

### Risk 1: Migration mis-keys or clobbers live in-flight ledgers
**Impact:** An in-flight issue loses stage progress or gets a stale blob written over a newer one, or gets keyed under the wrong/`None` repo.
**Mitigation:** Idempotent backfill that writes only when the target ledger is empty; never overwrite. Key each entry off the session's lease-pinned `target_repo` (the same single source of truth writers use), skipping with a WARNING any session whose repo cannot be authoritatively determined. Dry-run first, log every (target_repo, issue) it touches, record completion in `data/migrations_completed.json`.

### Risk 2: Split-brain during transition (some writers on session, some on ledger)
**Impact:** A stage marker written to the old session store is invisible to a reader on the new ledger, re-creating the exact deadlock mid-migration.
**Mitigation:** Land writers and readers (plus dashboard and steering) atomically in one PR — they share the CLI entry points. **No session-side mirror** is retained: a mirror is a second writable source of truth that can silently diverge from the ledger, which IS the split-brain this risk names. The cutover is all-or-nothing; full-suite gate before merge.

### Risk 3: Concurrent same-issue writers race on the ledger
**Impact:** Two stages (or a builder + a marker) writing the same ledger concurrently clobber each other's `_verdicts`/`_sdlc_dispatches`.
**Mitigation:** Reuse the existing `update_stage_states` / `_load_preserved_metadata` merge-on-save protocol verbatim against the ledger record; the run_id write-lease already serializes legitimate writers per issue.

### Risk 4: The write-lease gate rejects legitimate writes and re-deadlocks
**Impact:** Over-strict lease checking refuses a valid takeover's writes, reproducing #2012 with a different cause.
**Mitigation:** The lease semantics are already proven by #2003 (`session-ensure` claim-echo-with-proof). Takeover acquires the lease via the ordinary contest; the ledger write authority follows the same lock. Regression test the takeover path end-to-end.

### Risk 5: A missing/unresolvable `target_repo` mints a `None:{issue}` phantom key (writer AND reader)
**Impact:** A writer OR reader that cannot obtain a pinned `target_repo` (legacy lock payload, expired lease, or a resolver returning `None`) keys the ledger under `None:{issue}`, silently routing writes to a phantom record or reading an empty one — reproducing #2012 with a new cause. The round-1 fix guarded only the writers; the reader cold path (`_compute_meta` → `_resolve_target_repo()`) was left unguarded.
**Mitigation:** `target_repo` is resolved at lease-acquire under authoritative env and pinned on the lock payload. Both sides read it from the lease first:
- **Writers** treat an absent pinned `target_repo` as an observable hard-fail (stderr diagnostic + non-zero exit), never assembling a key with a `None` component.
- **Readers** resolve lease-first, else env `_resolve_target_repo()`; a `None` result yields the defined empty-ledger outcome (AC #2 reconstruct-or-refuse), never a read against `(None, issue)`.
- **Self-healing renewal** closes the legacy-payload gap: same-owner renewal re-`SET`s the lock payload with the re-pinned `target_repo` instead of a bare `EXPIRE` (`models/session_lifecycle.py:995-996`), so a lock that predates this change gains the field on its next renewal tick rather than never — without which a continuously-renewing in-flight lease would hard-fail every write across cutover.
- Dedicated tests assert (a) the writer `None:{issue}` write is impossible (exits non-zero), (b) the reader never reads `(None, issue)`, and (c) renewal self-heals a legacy payload.

## Race Conditions

### Race 1: Concurrent stage writes to one issue ledger
**Location:** the new ledger `_save()` path (mirrors `agent/pipeline_state.py:324-383`)
**Trigger:** Two `sdlc-tool` invocations for the same issue write near-simultaneously (e.g. a `stage-marker completed` and a `verdict record`).
**Data prerequisite:** The ledger record must exist (or be created idempotently) before the second writer merges into it.
**State prerequisite:** Both writers hold the same run_id write-lease (only one live lease per issue exists).
**Mitigation:** Optimistic merge-on-save (`_load_preserved_metadata` reload+retry) plus the single-live-lease invariant from #1954/#2003. No two *different* run_ids can hold the lease simultaneously.

### Race 2: Migration runs while a live session writes the old store
**Location:** `scripts/update/migrations.py` backfill vs. a live pipeline write
**Trigger:** The migration reads `stage_states` off a session that a live stage skill is concurrently updating.
**Data prerequisite:** The ledger must reflect the *latest* session blob, not a stale read.
**State prerequisite:** The migration only backfills when the ledger is empty; a live writer that has already populated the ledger wins.
**Mitigation:** Write-if-empty semantics; after cutover, live writers target the ledger directly, so the migration is a one-time lift with no ongoing contention. Run the migration during `/update` when no pipeline is mid-write on this machine.

## No-Gos (Out of Scope)

- [Q3 SPLIT] The immediate unblock of PR #2008 / issue #1997 is performed as an
  operator-recovery runbook (task 5) that runs FIRST and INDEPENDENT of the Large
  refactor — it works on unmodified main because PR #2008 is already
  `MERGEABLE`/`CLEAN` and the block is missing bookkeeping, not code. The refactor
  prevents recurrence; it is not a prerequisite for recovering this one PR. The
  runbook stays in this plan's scope (not deferred to another issue) but is not
  gated on tasks 1-4.
- [ORDERED] Actually merging PR #2008 waits on the human go-ahead — the runbook
  records/repairs state (or writes the human-authorized merge token) first; the
  merge action is sequenced after, never before.
- Full unification of the five work identities (issue_number/session_id/run_id/
  slug/GitHub) into a single scheme — this plan moves only the ledger key.

## Update System

- **Migration required.** Add an idempotent backfill to
  `scripts/update/migrations.py` and register it in `MIGRATIONS` (per the repo's
  Popoto Schema Migration Requirement). It lifts live in-flight sessions'
  `stage_states` into `PipelineLedger[(repo, issue)]`, write-if-empty, recorded in
  `data/migrations_completed.json`.
- **No new dependencies** to propagate — the ledger uses the existing Popoto/Redis
  substrate already present on every machine.
- `run_pending_migrations()` runs on `/update`; the migration is safe to run on the
  skills/tools-only machine (Redis-local, idempotent).

## Agent Integration

- **No new MCP/`.mcp.json` surface.** The agent already reaches this functionality
  through the existing `sdlc-tool` CLI entry points (`stage-marker`, `verdict`,
  `stage-query`, `meta-set`, `dispatch`, `next-skill`) declared in
  `pyproject.toml [project.scripts]`. The fix re-points those existing commands at
  the issue-keyed ledger; their CLI contracts (flags, JSON output shapes) are
  preserved.
- **No bridge import changes.** `bridge/telegram_bridge.py` does not call the
  pipeline ledger directly.
- Integration coverage: a test that drives `sdlc-tool verdict record` then
  `sdlc-tool stage-query` across a simulated driver→takeover handoff and asserts the
  ledger round-trips (the agent-visible contract).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-stage-tracking.md` — the ledger is now issue-keyed;
  document `(target_repo, issue_number)` as the key and the run_id lease as the
  write authority.
- [ ] Update `docs/features/sdlc-issue-ownership-lock.md` — the lock now doubles as
  the ledger write-lease (Q4 unification).
- [ ] Create `docs/features/sdlc-issue-keyed-stage-ledger.md` describing the
  `PipelineLedger` model, migration, and the takeover-handoff guarantee; add it to
  `docs/features/README.md` index.
- [ ] Add a recovery runbook (in the feature doc) for the immediate-unblock case:
  re-run `/do-pr-review` as the live owner, clear stale comments, dispatch `/do-merge`.

### Inline Documentation
- [ ] Docstrings on `PipelineLedger`, `PipelineStateMachine.for_issue()`, and the
  migration function explaining the keying and lease semantics.
- [ ] Comment the `find_session_by_issue()` demotion (now routing/ownership + reader
  cold-path session fallback only; not a state-write or dashboard caller).

## Success Criteria

- [ ] A driver→takeover handoff (original terminal, foreign-slug takeover completes)
  leaves a populated issue-keyed ledger — REVIEW verdict and `pr_number` readable by
  `/do-merge`. (AC #1)
- [ ] `/do-merge` has defined, tested behavior when the ledger is empty but the PR is
  genuinely reviewed+mergeable: reconstruct via cold-start fallback or refuse with an
  actionable reason — never a silent stall. (AC #2)
- [ ] PR #2008 / issue #1997 is unblocked and merged via the runbook. (AC #3)
- [ ] Regression test covering the terminal-driver + takeover-completes scenario. (AC #4)
- [ ] `target_repo` is resolved at lease-acquire and pinned on the lock payload; no
  **writer** calls `_resolve_target_repo()` and no writer shells `gh repo view` per write
  (grep confirms the four writer files reference no repo resolver). (AC #5 — writers only)
- [ ] Same-owner lock renewal **self-heals** a legacy payload: renewal re-`SET`s the
  payload with the pinned `target_repo` rather than a bare `EXPIRE`, so a lock predating
  this change gains the field on its next renewal (tested). (AC #5b)
- [ ] The **reader** resolves `target_repo` lease-first, falls back to env only on a cold
  read, and NEVER reads `PipelineLedger[(None, issue)]`: a `None` resolution yields the
  defined empty-ledger outcome (reconstruct-or-refuse), verified by test (AC #5's writer
  grep does not cover this path). (AC #5c)
- [ ] A writer with no pinned `target_repo` hard-fails observably; no `PipelineLedger`
  record is ever keyed under a `None` repo component (writer or reader). (AC #6)
- [ ] Migration backfills live in-flight ledgers idempotently; re-running is a no-op.
- [ ] `stage-marker`/`verdict`/`stage-query` CLI contracts unchanged (JSON shapes stable).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `find_session_by_issue` no longer referenced by any state-write path (grep confirms
  only routing/ownership callers — `sdlc_session_ensure.py`, `sdlc_next_skill.py`,
  `sdlc_dispatch.py` — and the reader's cold-path session fallback in `sdlc_stage_query.py`
  remain; zero matches in `ui/`).

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (ledger-storage)**
  - Name: `ledger-builder`
  - Role: `PipelineLedger` model + `PipelineStateMachine.for_issue()` + merge-on-save protocol + pin `target_repo` on the lease (`touch_issue_lock` payload + `_acquire_run_lock_and_bind`)
  - Agent Type: builder
  - Domain: async/redis-popoto
  - Resume: true

- **Builder (cli-repoint)**
  - Name: `cli-builder`
  - Role: re-point `sdlc-tool` writers/readers and the `/do-merge` gate at the ledger; demote `find_session_by_issue`
  - Agent Type: builder
  - Resume: true

- **Builder (migration)**
  - Name: `migration-builder`
  - Role: idempotent backfill in `scripts/update/migrations.py` + registration
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Test engineer (takeover-regression)**
  - Name: `takeover-tester`
  - Role: driver→takeover regression test + merge-gate empty-ledger behavior test
  - Agent Type: test-engineer
  - Resume: true

- **Validator (ledger-integrity)**
  - Name: `ledger-validator`
  - Role: verify state survives session deletion; verify CLI contracts unchanged; verify migration idempotency
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Build the issue-keyed ledger + pin target_repo on the lease
- **Task ID**: build-ledger
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_ledger.py (create), tests/unit/test_pipeline_state.py, tests/unit/test_session_lifecycle.py
- **Informed By**: spike-1 (session-keyed today; only the reader resolves target_repo), spike-2 (lease already issue-keyed; lock payload is the pin site)
- **Assigned To**: ledger-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `PipelineLedger` Popoto model keyed by `{target_repo}:{issue_number}`.
- Add `PipelineStateMachine.for_issue(target_repo, issue_number)` reusing `StageStates` validation and the `_load_preserved_metadata` merge-on-save protocol.
- Resolve `target_repo` in `_acquire_run_lock_and_bind` and pin it into the `touch_issue_lock` payload. **Make renewal self-healing (BLOCKER round-2):** change the same-owner renewal branch (`models/session_lifecycle.py:995-996`) from a bare `_R.expire(key, ttl)` to a payload re-`SET` that re-pins `target_repo`, so legacy/pre-deploy payloads gain the field on their next renewal instead of never. Extend/reuse the existing `touch_issue_lock(..., peek=True)` lease-peek to return the pinned `target_repo` for writers AND readers to consume.

### 2. Re-point CLI writers/readers and the merge gate
- **Task ID**: build-cli-repoint
- **Depends On**: build-ledger
- **Validates**: tests/unit/test_sdlc_stage_marker.py, tests/unit/test_do_merge_docs_gate.py
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: false
- Switch `stage-marker`, `verdict`, `meta-set`, `dispatch`, `stage-query`, `next-skill` to the ledger, gated on the run_id write-lease. Writers read `target_repo` from the lease (never call `_resolve_target_repo()`); a missing pinned repo is an observable hard-fail, never a `None:{issue}` write.
- **Guard the reader cold path (BLOCKER round-2):** `stage-query`/`_compute_meta` and the `/do-merge` predicate resolve `target_repo` lease-first (via the peek), fall back to env `_resolve_target_repo()` only on a cold read, and on a `None` result take the defined empty-ledger outcome (reconstruct-or-refuse) — NEVER a read against `PipelineLedger[(None, issue)]`.
- Re-point the `/do-merge` predicate; remove the `PRESENT_NO_SESSION` quiet no-op from the issue-keyed path; demote `find_session_by_issue` to routing/ownership + reader cold-path fallback (it is NOT a dashboard caller). Re-point the dashboard (`ui/data/sdlc.py`, which reads `session.stage_states`/`PipelineStateMachine(session)` directly) and steering readers to the ledger in this SAME PR (atomic cutover, no mirror).

### 3. Migration backfill
- **Task ID**: build-migration
- **Depends On**: build-ledger
- **Validates**: tests/unit/test_migrations.py (or equivalent)
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: true
- Idempotent write-if-empty backfill of live in-flight `stage_states` into the ledger; register in `MIGRATIONS`.

### 4. Takeover regression + merge-gate behavior tests
- **Task ID**: build-takeover-tests
- **Depends On**: build-cli-repoint
- **Assigned To**: takeover-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Terminal-driver + foreign-slug-takeover regression: ledger intact, merge-gate reads REVIEW verdict + pr_number.
- Empty-ledger merge-gate: reconstruct-or-refuse, never silent stall.

### 5. Immediate unblock runbook for PR #2008 (SPLIT — lands FIRST, on unmodified main)
- **Task ID**: runbook-2008
- **Depends On**: none
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- **Q3 decision: split.** The unblock does NOT wait on the Large refactor. It runs first, using a mechanism that works on current main (the ledger fix is not required to recover one PR). PR #2008 is already `MERGEABLE`/`CLEAN`; the block is missing bookkeeping, not a code defect.
- Document + execute the operator recovery: acquire the #1997 issue lease with a live session, then either (a) revive the `sdlc-local-1997` driver session (slug that resolves) to record a fresh REVIEW verdict + `pr_number` and dispatch `/do-merge`, or (b) if reviving is infeasible, use the human-authorized merge path (`data/merge_authorized_2008` written in a separate call, then `gh pr merge`) since the PR is already clean. Clear the stale "Do not merge yet" comment either way. Human go-ahead gates the actual merge.
- This step and the refactor (tasks 1-4, 6-7) are independent tracks; #1997 is not held hostage to the refactor landing.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-cli-repoint, build-migration, build-takeover-tests
- **Assigned To**: documentarian (assign a documentarian)
- **Agent Type**: documentarian
- **Parallel**: false
- Update stage-tracking + ownership-lock docs; create the ledger feature doc + runbook; index it.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: all previous
- **Assigned To**: ledger-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify state survives session deletion, CLI contracts unchanged, migration idempotent, all success criteria met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Ledger model exists | `python -c "from agent.pipeline_ledger import PipelineLedger"` | exit code 0 |
| Migration registered | `grep -c "PipelineLedger\|pipeline_ledger" scripts/update/migrations.py` | output > 0 |
| No state-write path uses find_session_by_issue | `grep -rn "find_session_by_issue" tools/sdlc_stage_marker.py tools/sdlc_verdict.py tools/sdlc_meta_set.py` | match count == 0 |
| Writers never resolve repo per write | `grep -rn "_resolve_target_repo\|gh repo view" tools/sdlc_stage_marker.py tools/sdlc_verdict.py tools/sdlc_meta_set.py tools/sdlc_dispatch.py` | match count == 0 |
| Lease payload pins target_repo | `grep -c "target_repo" models/session_lifecycle.py tools/sdlc_session_ensure.py` | output > 0 |
| Renewal self-heals (not bare EXPIRE) | `pytest tests/unit -k "renewal and self_heal or renewal and target_repo" -q` | exit code 0 |
| Reader guards None repo (no None-keyed read) | `pytest tests/unit -k "reader and (none_repo or cold_path)" -q` | exit code 0 |
| find_session_by_issue not in ui/ | `grep -rc "find_session_by_issue" ui/` | match count == 0 |
| Takeover regression present | `grep -rln "takeover" tests/ \| head -1` | exit code 0 |
| Stage-query CLI contract stable | `sdlc-tool stage-query --issue-number 2012` | output contains stages |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | 2 critics (cross-validated) | Key `{target_repo}:{issue}` re-introduces fragile per-write `target_repo` inference; only the reader (`sdlc_stage_query.py:412`) resolves it today, so re-pointed writers would newly shell `gh repo view` (10s timeout) and a `None` result mints a phantom `None:{issue}` key — reproducing #2012 | Technical Approach → Keying; Data Flow 2-4; Risk 5; spike-1 correction; tasks 1-2 | Resolve `target_repo` ONCE at lease-acquire in `_acquire_run_lock_and_bind`, pin on the `touch_issue_lock` payload; writers read from the lease, never re-resolve. Same pin dissolves writer-vs-writer divergence + migration mis-key. |
| CONCERN | cross-validated | Optional session-side mirror re-manufactures the Risk 2 split-brain | Solution → Atomic cutover; Technical Approach → No session-side mirror; Risk 2; Reversibility | Mirror dropped; writers/readers/dashboard/steering cut over atomically in one PR. |
| SCOPE | supervisor | Close Q2 (mirror) and Q3 (hotfix split) before build | Resolved-in-this-revision block; No-Gos; task 5 | Q2 → drop mirror. Q3 → split: unblock #2008 first, independent of the refactor, on unmodified main. |
| BLOCKER (round 2) | re-critique | Legacy lock payloads never regain `target_repo` under EXPIRE-only renewal (`session_lifecycle.py:995-996`) — a continuously-renewing pre-deploy lease would hard-fail every write across cutover | Arch Impact → lease payload; Technical Approach → Keying; Risk 5; task 1; AC #5b; Failure Path tests | Self-healing renewal: same-owner renewal re-`SET`s the payload with the re-pinned `target_repo` instead of bare `EXPIRE`. |
| BLOCKER (round 2) | re-critique | Reader cold path (`_compute_meta` → `_resolve_target_repo()` at `sdlc_stage_query.py:412`) is unguarded/untested; a `None` result keys `(None, issue)` → empty read → /do-merge refuses a mergeable PR (#2012 on the read side). AC #5 grep covered writers only | Technical Approach → Readers; Data Flow 4; Risk 5 (reader); AC #5c/#6; Failure Path reader test | Reader resolves lease-first, guards `None` into the defined empty-ledger outcome, never reads `(None, issue)`; ACs/tests extended to the reader. |
| CONCERN (round 2) | re-critique | Phantom caller: plan claimed `find_session_by_issue` retained for a dashboard caller, but `ui/data/sdlc.py` has zero references | Arch Impact → Coupling; Technical Approach → demotion; Rabbit Holes; AC + Verification `find_session_by_issue not in ui/` | Corrected: retained callers are routing/ownership + reader cold-path fallback; dashboard reads `session.stage_states` directly and is re-pointed, but never calls `find_session_by_issue`. |
| CONCERN (round 2) | re-critique | Rollback overclaimed — one-directional migration with no mirror loses post-cutover progress on `git revert` | Arch Impact → Reversibility | Stated honestly: clean revert only before the first post-cutover ledger write; after that, revert is manual + lossy (needs a reverse-backfill this plan does not build). |

---

## Resolved in round-2 revision (re-critique NEEDS REVISION)

- **BLOCKER — legacy lock payloads never regain `target_repo` under EXPIRE-only renewal.**
  Resolved. Same-owner renewal is changed from a bare `_R.expire(key, ttl)`
  (`models/session_lifecycle.py:995-996`) to a payload re-`SET` that re-pins `target_repo`,
  so a lock predating this deploy self-heals on its next renewal tick rather than never —
  no continuously-renewing in-flight lease hard-fails every write across cutover. See
  Architectural Impact → lease payload, Technical Approach → Keying, Risk 5, task 1,
  AC #5b.
- **BLOCKER — reader cold-path `target_repo` inference unguarded/untested.** Resolved. The
  reader (`_compute_meta`/`stage-query` and the `/do-merge` predicate) resolves
  `target_repo` lease-first (peek), env-fallback only on a cold read, and a `None` result
  yields the defined empty-ledger outcome — it NEVER reads `PipelineLedger[(None, issue)]`.
  ACs/tests extended beyond AC #5's writer-only grep to cover the reader (AC #5c/#6,
  Failure Path reader test). See Technical Approach → Readers, Data Flow 4, Risk 5.
- **CONCERN — phantom dashboard caller of `find_session_by_issue`.** Corrected.
  `ui/data/sdlc.py` has zero references; the retained callers are routing/ownership
  (`sdlc_session_ensure.py`, `sdlc_next_skill.py`, `sdlc_dispatch.py`) plus the reader's
  cold-path session fallback. The dashboard reads `session.stage_states` directly and is
  re-pointed to the ledger, but never touches `find_session_by_issue`. AC grep + a new
  `find_session_by_issue not in ui/` verification row corrected.
- **CONCERN — rollback overclaimed.** Corrected. Reversibility is now stated honestly:
  the migration is one-directional; `git revert` is clean only before the first
  post-cutover ledger write. After real pipeline activity, revert is a manual, lossy
  operation (post-cutover progress lives only on the ledger; no reverse-backfill is built).

## Resolved in round-1 revision (critique NEEDS REVISION)

- **BLOCKER — fragile per-write `target_repo` inference.** Resolved. `target_repo` is
  resolved ONCE at lease-acquire and pinned on the run_id lock payload; writers read it
  from the lease and never re-resolve (no `gh repo view` per write, no `None:{issue}`
  phantom key). See Technical Approach → Keying, Data Flow steps 2-4, Risk 5. This single
  pin also dissolves the writer-vs-writer key divergence and migration mis-key concerns.
- **CONCERN — session-side mirror re-manufactures Risk 2 split-brain.** Resolved. The
  mirror is **dropped**. Writers, readers, dashboard, and steering cut over to the ledger
  atomically in one PR (former Q2).
- **Q3 — scope of the PR #2008 unblock.** Decided: **split**. The unblock runs first and
  independent of the refactor, on unmodified main (task 5, No-Gos). #1997 is not held
  hostage to the Large refactor.

## Open Questions

1. **Storage mechanism for `PipelineLedger`.** A dedicated Popoto model keyed by
   `{repo}:{issue}` (proposed) vs. a plain Redis hash namespace managed through the
   ORM. Any preference, or constraints from the dashboard/analytics that read
   `stage_states` today? (Not scope-determining — either mechanism honors the pinned
   key and atomic cutover above.)
2. **Migration blast radius.** Backfill only currently-non-terminal in-flight
   issues, or also sweep recently-terminal sessions (last N days) so historical
   ledgers are queryable? The former is minimal-risk; the latter preserves history.
