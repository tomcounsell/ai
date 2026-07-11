# SDLC Issue-Keyed Stage Ledger

Durable, issue-keyed storage for the SDLC pipeline's stage/verdict/PR-number
bookkeeping (issue #2012), replacing the prior session-keyed
`AgentSession.stage_states` as the write target for every `sdlc-tool` state
mutation.

## Problem

Before this feature, the pipeline's durable ledger — stage statuses,
recorded verdicts, dispatch history, and the resolved PR number — lived on
`AgentSession.stage_states`: a JSON blob keyed by the **executor** doing the
work. The executor is ephemeral. It crashes, completes, gets killed, gets
superseded, or gets taken over by a different session. Every one of those
lifecycle events was a potential state-loss event, because the ledger lived
on the thing most likely to disappear.

This wasn't hypothetical. On PR #2008 (issue #1997), the original driver
session (`sdlc-local-1997`) went terminal, and a takeover session with a
foreign slug (`dev-7bd4cf82`) picked up the work and finished it. Every
stage/verdict marker the takeover wrote — keyed on `--issue-number 1997` —
resolved no live issue-owner session (`find_session_by_issue()` fails all
three of its passes for a foreign-slug session that never claimed
ownership of the issue via `issue_url`, a deterministic id, or
`message_text`), so `stage-marker`'s `PRESENT_NO_SESSION` degradation
quietly no-op'd every one of those writes — exit 0, no persisted state.
`sdlc-tool stage-query --issue-number 1997` returned an empty stage store,
the `/do-merge` gate read no REVIEW verdict and no `pr_number`, and the
pipeline deadlocked at MERGE — not on any code defect, but on missing
bookkeeping.

## Solution: move the ledger to the entity it's about

`PipelineLedger` moves the ledger off the executor entirely and onto the
`(target_repo, issue_number)` pair — the entity the pipeline is actually
*about*. A driver session and a takeover session working the same issue
read and write the exact same ledger record. The ledger never moves,
because it never lived on either session in the first place. Write
*authority* over that ledger is a separate concern, enforced by the
existing per-issue run_id lease — takeover is simply acquiring that lease.

## The `PipelineLedger` model

`agent/pipeline_ledger.py` — a Popoto model, no TTL (it must survive
indefinitely, unlike short-lived records such as `DedupRecord`'s 2-hour
TTL; a ledger persists even after its PR merges and every `AgentSession`
that ever worked it is deleted).

| Field | Type | Purpose |
|---|---|---|
| `ledger_key` | `KeyField` | Composite string key, `f"{target_repo}:{issue_number}"` |
| `target_repo` | `Field(null=True)` | The `owner/name` GitHub slug, stored redundantly (also embedded in `ledger_key`) so inspection/migration tooling can filter without parsing the composite key |
| `issue_number` | `IntField(null=True)` | The GitHub issue number, stored redundantly for the same reason |
| `stage_states_json` | `Field(default="{}")` | JSON blob holding the stage-status map plus all underscore-prefixed metadata (`_verdicts`, `_sdlc_dispatches`, `_patch_cycle_count`, `_critique_cycle_count`) — the same wire format `AgentSession.stage_states` already used |
| `pr_number` | `IntField(null=True)` | Field-backed, single-writer (`sdlc-tool meta-set --key pr_number`) — mirrors how `AgentSession.pr_number` was already a field, not a key inside the JSON blob |

`PipelineLedger.get_or_create(target_repo, issue_number)` returns the
existing record or creates an empty-but-valid one. An absent ledger is not
an error — it's what lets a fresh issue's first marker write construct a
state machine with nothing recorded yet, exactly like a fresh session with
no prior `stage_states` did before this feature.

The model itself does not resolve or validate `target_repo` — a `None` or
empty value reaching `PipelineLedger.get_or_create()` would mint a phantom
`None:{issue}` key. That responsibility is pushed to call sites (see
"Writer hard-fail semantics" and "Reader guard semantics" below), which is
exactly where the design keeps it: this model is pure storage and does not
itself gate writes.

## `PipelineStateMachine.for_issue()`

`agent/pipeline_state.py::PipelineStateMachine` now supports two backing
stores, selected at construction time:

- **`__init__(session)`** (original) — session-keyed, reads/writes
  `AgentSession.stage_states`. Still used by the in-session skill hooks
  (`pre_tool_use.py`/`post_tool_use.py`, unaffected by this feature) and
  retained as the reader's cold-path fallback for pre-cutover records.
- **`for_issue(target_repo, issue_number)`** (new, classmethod) —
  issue-keyed, loads (creating if absent) the `PipelineLedger` record for
  `(target_repo, issue_number)`.

Both constructors populate identical instance state
(`states`/`patch_cycle_count`/`critique_cycle_count`) through the same
`_load_state()` method, and every stage-transition method
(`start_stage`, `complete_stage`, `fail_stage`, `get_display_progress`) is
identical across both paths — only the load/store primitives
(`_read_raw`/`_write_raw`/`_load_preserved_metadata`) branch on which
backing store (`self.session` or `self._ledger`) is active. Exactly one of
the two is set on any given instance; the other is `None`.

**Merge-on-save is shared verbatim.** `_save()` reloads the live backing
store's raw blob before writing, so any underscore-prefixed metadata key
another writer added between this instance's construction and its save
(`_verdicts` from `sdlc_verdict.py`, `_sdlc_dispatches` from
`sdlc_dispatch.py`) is preserved rather than clobbered. This protocol
existed for the session-keyed path already (regression #1040); issue #2012
extends it verbatim to the ledger path, so two `for_issue()` instances for
the same issue merge exactly like two session-keyed instances for the same
session always have.

**One extra step the ledger path needs that the session path doesn't:**
`_refresh_ledger()` reloads `self._ledger` from Redis immediately before
every read and every write. `AgentSession.stage_states` is a computed
property that re-reads its backing session_events on every access;
a Popoto `Field()` value is not — it's cached on the in-memory instance
once loaded. Without this refresh, a `PipelineLedger` instance held across
the construct-then-later-save gap would read and re-persist a stale
snapshot, silently reproducing the staleness bug the session path never
had. `for_issue()` construction, and every subsequent read/write through
that instance, calls `_refresh_ledger()` first.

## The write-lease model

A caller may write the ledger only while it holds the per-issue run_id
lease (`models/session_lifecycle.py::touch_issue_lock` — see
[`docs/features/sdlc-issue-ownership-lock.md`](sdlc-issue-ownership-lock.md)
for the full lock mechanics). This unifies ownership and write authority:
the lock that used to exist purely to prevent two entry points from racing
now also gates every ledger mutation.

**`target_repo` is pinned once, never re-resolved per call.** Re-pointing
each writer to call `_resolve_target_repo()` (which shells `gh repo view`,
a 10-second-timeout, network/auth-dependent subprocess) on every single
write would be both slow and fragile — and a `None` result would mint a
`None:{issue}` phantom key, silently reproducing #2012 with a new cause.
Instead, `tools/sdlc_session_ensure.py::_acquire_run_lock_and_bind()`
resolves `target_repo` exactly ONCE per `ensure_session()` call — the one
place the process env (`GH_REPO`/`SDLC_TARGET_REPO`) is authoritative
regardless of a takeover session's foreign slug or cwd — and passes it
into every `touch_issue_lock()` call, where it's persisted on the lock
payload.

From there:

- **Writers** (`tools/sdlc_stage_marker.py`, `tools/sdlc_verdict.py`,
  `tools/sdlc_meta_set.py`, `tools/sdlc_dispatch.py`) call
  `tools/_sdlc_utils.py::resolve_ledger_lease(issue_number, run_id)` to peek
  the lock, confirm `run_id` is the live owner, and read the pinned
  `target_repo`. Immediately before the actual mutation, they call
  `revalidate_ledger_lease(issue_number, run_id, target_repo)` — a
  non-peek re-validate-and-renew that closes the peek-to-write TOCTOU
  window (a foreign run could have taken the lease in the gap between the
  peek and the write).
- **Readers** (`tools/sdlc_stage_query.py`, the dashboard) call
  `tools/_sdlc_utils.py::resolve_target_repo_for_read(issue_number)`, which
  peeks the lock with `run_id=None` (any live lease's pinned value is
  visible regardless of who holds it) and falls back to
  `_resolve_target_repo()`'s env-first ladder only when no live lease
  exists at all.

No writer or reader ever calls `_resolve_target_repo()` (the `gh repo view`
resolver) directly per-write/per-read anymore — grep confirms zero matches
in the four writer files.

## Writer hard-fail semantics (the fix for #2012's root cause)

Before this feature, a write that couldn't resolve its owning session
degraded *quietly*: `PRESENT_NO_SESSION`, exit 0, no persisted state — the
exact failure mode that caused the #1997/#2008 deadlock. There is no
session to fail to resolve anymore, so every one of the following is now
an **observable** failure: a clear stderr diagnostic and a non-zero exit.

Applies to the four writer CLIs:

- `sdlc-tool stage-marker` (`tools/sdlc_stage_marker.py`)
- `sdlc-tool verdict record` (`tools/sdlc_verdict.py`)
- `sdlc-tool meta-set` (`tools/sdlc_meta_set.py`)
- `sdlc-tool dispatch record` (`tools/sdlc_dispatch.py`)

Each refuses the write and exits non-zero (or, for `dispatch record`, whose
underlying `bool` return-type contract is preserved for existing callers,
returns `{"ok": false, ...}` with the `reason` merged in) when:

- **`LEASE_ABSENT`** — no live lease exists for this `run_id` + issue at
  all (the lock is unheld). The diagnostic points at re-running
  `sdlc-tool session-ensure`.
- **`ISSUE_LOCKED`** — the lease is held by a *foreign* `run_id`. The
  diagnostic surfaces `owner_run_id`/`owner_session_id`/`orphaned_lock`.
- **`TARGET_REPO_MISSING`** — the lease is confirmed held by this
  `run_id`, but its payload carries no pinned `target_repo` (a
  pre-#2012 payload that hasn't self-healed via a renewal yet, or a
  resolver that returned `None` at acquire time). The writer refuses
  rather than assembling a `None:{issue}` key.
- **`lease_lost`** — the TOCTOU re-validation immediately before the
  mutation found a foreign run had taken the lease since the initial
  resolve.

The one case that stays **quiet** (exit 0, a `{"status": "degraded", ...}`
payload) is Redis itself being unreachable — a genuine infra outage, not
an owner/lease problem, and the pre-existing degradation contract for that
case (issue #2004) is unchanged.

## Reader guard semantics

Readers resolve `target_repo` lease-first, env-fallback only on a cold read
(no live lease at all). The critical invariant, symmetric with the writer
side: **a `target_repo` resolution of `None` never reads
`PipelineLedger[(None, issue)]`.** `tools/sdlc_stage_query.py::_resolve_issue_record()`
returns `None` immediately in that case — the defined empty-ledger outcome
(an empty stages dict, default `_meta`), never a lookup against a phantom
key. Without this guard, a reader's cold-path resolution failure would
silently reproduce #2012 on the *read* side: `/do-merge` would see an empty
ledger and refuse a genuinely mergeable PR, for a reason indistinguishable
from the issue simply never having been worked.

## The retained session fallback (not the takeover path)

When `target_repo` *does* resolve but the resulting `PipelineLedger` is
empty (`stage_states_json == "{}"`), `_resolve_issue_record()` falls back to
the pre-cutover session-keyed lookup, `_find_session_by_issue()` (a thin
wrapper around `tools/_sdlc_utils.py::find_session_by_issue()`). This belt
exists for issues whose work started *before* this migration and whose
`AgentSession` still carries the old data, or a session created in the
window between a migration backfill run and this deploy.

This is explicitly **not** the takeover mechanism — the ledger itself is
what makes a driver→takeover handoff survive; this fallback only covers a
narrow transition-period gap for pre-cutover issues. The dashboard
(`ui/data/sdlc.py::_resolve_display_stages()`) applies the identical
resolution order for the same reason.

## `find_session_by_issue()`'s demoted scope

`tools/_sdlc_utils.py::find_session_by_issue()` is no longer part of any
writer's state-integrity path. Its retained callers, after this feature:

- **Routing/ownership**: `tools/sdlc_session_ensure.py` (resolving which
  session to bind a fresh/reused run_id to), `tools/sdlc_next_skill.py`
  (peek pre-check identity source), and the routing bits of
  `tools/sdlc_dispatch.py` (disambiguating lock contention from other
  write failures).
- **The reader's cold-path session fallback**: `tools/sdlc_stage_query.py::_find_session_by_issue()`
  — the belt described above, reached only when the ledger resolves but is
  empty.

It is explicitly **not** a dashboard caller (`ui/data/sdlc.py` reads
`session.stage_states`/`PipelineStateMachine(session)` directly, or the
issue-keyed ledger via its own `_resolve_issue_ledger()` helper — never
`find_session_by_issue`) and it is **not** part of any of the four writer
CLIs' write path anymore.

## The migration

`scripts/update/migrations.py::_migrate_backfill_pipeline_ledger()`,
registered in `MIGRATIONS`, runs during `/update`. It backfills
non-terminal (in-flight) `AgentSession.stage_states` blobs into the
issue-keyed ledger so a takeover after cutover reads the same progress the
old session-keyed path would have shown.

- **Scope is deliberately narrow**: only non-terminal sessions carrying a
  non-empty `stage_states` blob are considered. This is a live-issue lift,
  not a historical sweep — terminal sessions' state, if ever needed, is
  reconstructible from durable signals (verdicts, PR state), not from this
  migration.
- **Keying**: `target_repo` is taken from the session's live issue lock
  (a peek of `session:issuelock:{issue}`, the same lease-pinned value the
  rest of the pipeline trusts) first, falling back to the env-based
  `_resolve_target_repo()` ladder. A session for which **both** resolution
  paths fail is skipped with a logged WARNING — the migration never
  assembles a `None:{issue}` key.
- **Idempotent, write-if-empty**: a target ledger is backfilled only when
  it is completely empty (`stage_states_json == "{}"` and `pr_number is
  None`). Any ledger that already carries content — from an earlier run of
  this same migration, or from a live writer that got there first — is
  left untouched. Re-running the migration, or running it concurrently
  with a live writer, is a safe no-op for every session it has already
  touched.
- **ORM only**: `AgentSession.query.all()`, `PipelineLedger.get_or_create()`,
  `.save()` — no raw Redis operations.

## Recovery runbook: unblocking a PR stuck like #2008/#1997

Use this procedure when a PR is functionally complete and `MERGEABLE`/
`CLEAN`, but the pipeline won't advance it to MERGE because the ledger
appears empty — the exact symptom this feature fixes going forward, and
the immediate, independent recovery for any case discovered before this
fix was deployed (or any future case where the ledger is unexpectedly
empty for a genuinely-mergeable PR).

1. **Confirm the PR is actually mergeable** — don't trust the pipeline's
   stall as evidence it isn't:
   ```bash
   gh pr view <N> --json mergeable,mergeStateStatus
   ```
   Expect `"mergeable": "MERGEABLE"` and `"mergeStateStatus": "CLEAN"`. If
   either is not true, this is a different problem — fix the PR first.

2. **Re-run `/do-pr-review` as the live owner** to record a fresh REVIEW
   verdict into the (now issue-keyed) ledger. Acquire the issue's run_id
   lease first (`sdlc-tool session-ensure --issue-number <N> --issue-url ...`)
   so the review's verdict-record call has a valid lease to write through.
   This is the normal, non-break-glass path — it produces a real,
   fresh-against-head-commit REVIEW verdict, satisfying the merge gate's
   REVIEW-freshness check the same way any ordinary review would.

3. **Clear any stale "Do not merge yet" bot comment** left on the PR by an
   earlier stalled attempt, so a human skimming the PR doesn't get a false
   signal.

4. **Dispatch `/do-merge`.** With a fresh REVIEW verdict and a resolvable
   `pr_number` now in the ledger, the merge gate's predicate should pass
   normally.

**Break-glass alternative**: when re-running review isn't practical (e.g.
the substrate that would host a fresh review run is itself unavailable),
use the human-authorized override documented in `docs/sdlc/do-merge.md`:
write `data/merge_authorized_{PR}` containing a non-empty `override: <reason>`
line, then `gh pr merge`. This bypasses the merge gate's automated checks
entirely — an explicit human decision, not a substitute for the ledger fix.
**Delete the override file immediately after use** — a stale override file
left in place would silently authorize a *future* merge of the same PR
number that the operator never reviewed.

## Source Files

| File | Role |
|---|---|
| `agent/pipeline_ledger.py` | `PipelineLedger` model, `_build_key()` |
| `agent/pipeline_state.py` | `PipelineStateMachine.for_issue()`, shared `_save()`/`_load_preserved_metadata()`/`_refresh_ledger()` |
| `models/session_lifecycle.py` | `touch_issue_lock(target_repo=...)`, `IssueLockResult.target_repo`, self-healing renewal branch |
| `tools/sdlc_session_ensure.py` | `_acquire_run_lock_and_bind()` — the one place `target_repo` is resolved and pinned |
| `tools/_sdlc_utils.py` | `resolve_ledger_lease()`, `revalidate_ledger_lease()`, `resolve_target_repo_for_read()`, `is_pipeline_ledger()`, demoted `find_session_by_issue()` |
| `tools/sdlc_stage_marker.py`, `tools/sdlc_verdict.py`, `tools/sdlc_meta_set.py`, `tools/sdlc_dispatch.py` | The four writer CLIs, re-pointed at the ledger with hard-fail semantics |
| `tools/sdlc_stage_query.py` | `_resolve_issue_record()` — the reader resolution path |
| `ui/data/sdlc.py` | Dashboard re-point (`_resolve_issue_ledger()`, `_resolve_display_stages()`, `_session_has_stage_data()`) |
| `scripts/update/migrations.py` | `_migrate_backfill_pipeline_ledger()` |
| `tests/unit/test_sdlc_takeover_regression.py` | Driver→takeover regression + empty-ledger merge-gate behavior tests |

## See Also

- [`docs/features/sdlc-stage-tracking.md`](sdlc-stage-tracking.md) — how stage markers get written day to day
- [`docs/features/sdlc-issue-ownership-lock.md`](sdlc-issue-ownership-lock.md) — the run_id lease this feature reuses as its write authority
- `docs/plans/sdlc-issue-keyed-stage-ledger.md` — the originating plan, with full risk/race-condition analysis
